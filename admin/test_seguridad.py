"""
test_seguridad.py - Pruebas de los cierres de seguridad del backend.

Cero red y cero correo: DentiDesk queda deshabilitado y todo escribe en temporales.

    cd admin && python test_seguridad.py

Cubre:
  - /api/upload: bloqueado en produccion, saneo del nombre, lista blanca de extensiones,
    y que NO se pueda escribir fuera de images/ (path traversal).
  - Que TODAS las rutas de administracion esten en RUTAS_SOLO_LOCAL (el descuido que
    dejo /api/upload abierto en Render).
  - /api/pacientes/estado exige token, igual que sus cuatro hermanos.
  - Los parametros numericos de la agenda publica devuelven JSON, no una pagina HTML de
    error 500, cuando llega basura.
"""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='seguridad_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de DentiDesk si
# scheduling_secrets.json las tiene activas (advertido en CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)   # que no arranquen los 8 schedulers
sys.path.insert(0, str(Path(__file__).parent))

import server              # noqa: E402


class TestUpload(unittest.TestCase):
    """El agujero real: /api/upload estaba sin token, sin bloqueo de produccion y
    concatenaba el nombre recibido del navegador directo sobre images/."""

    def setUp(self):
        self.client = server.app.test_client()
        self.images = Path(tempfile.mkdtemp(prefix='images_test_'))
        self._images_orig = server.IMAGES
        server.IMAGES = self.images

    def tearDown(self):
        server.IMAGES = self._images_orig
        server.EN_RENDER = False

    def _subir(self, target, contenido=b'x' * 10):
        return self.client.post('/api/upload', content_type='multipart/form-data', data={
            'file': (io.BytesIO(contenido), 'foto.jpg'),
            'target': target,
        })

    def test_bloqueado_en_produccion(self):
        server.EN_RENDER = True
        r = self._subir('foto.jpg')
        self.assertEqual(r.status_code, 403)

    def test_nunca_escribe_fuera_de_images(self):
        """La propiedad que importa: pase lo que pase, el archivo queda DENTRO de
        images/. Un target con '../' antes pisaba archivos del sitio (ej. index.html).

        Ojo: no todos estos devuelven 400. secure_filename aplana el nombre, asi que
        'sub/../../evil.jpg' se guarda como 'sub_.._.._evil.jpg' adentro de images/ —
        feo pero seguro. Lo que se verifica es que nada salga de la carpeta."""
        antes = set(self.images.parent.iterdir())
        for target in ('../../index.html', '..\\..\\index.html', '/etc/passwd',
                       'sub/../../evil.jpg', '....//....//evil.jpg'):
            with self.subTest(target=target):
                r = self._subir(target)
                if r.status_code == 200:
                    creado = Path(r.get_json()['path'])
                    self.assertEqual(creado.parent.name, 'images')
                    self.assertNotIn('..', creado.parts)
                else:
                    self.assertEqual(r.status_code, 400)
                    self.assertFalse(r.get_json()['ok'])
        # Nada nuevo fuera de images/ (ni index.html pisado, ni archivos sueltos).
        self.assertEqual(set(self.images.parent.iterdir()) - antes, set())

    def test_html_no_se_puede_subir(self):
        """'../../index.html' se aplana a 'index.html': aunque ya no escapa de la
        carpeta, un .html subible seria una pagina servida desde nuestro dominio."""
        r = self._subir('../../index.html')
        self.assertEqual(r.status_code, 400)

    def test_extension_no_permitida(self):
        for target in ('malicioso.exe', 'script.py', 'shell.php', 'sin_extension'):
            with self.subTest(target=target):
                r = self._subir(target)
                self.assertEqual(r.status_code, 400)

    def test_archivo_valido_se_guarda(self):
        """No romper el flujo real del panel: una foto normal sigue subiendo."""
        r = self._subir('dr-alberto-del-real.jpeg')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['ok'])
        self.assertTrue((self.images / 'dr-alberto-del-real.jpeg').exists())

    def test_faltan_datos(self):
        r = self.client.post('/api/upload', content_type='multipart/form-data', data={})
        self.assertFalse(r.get_json()['ok'])


class TestSinCarpetaEstatica(unittest.TestCase):
    """Con static_folder='.' Flask publicaba toda la carpeta admin/ en
    /./<archivo> (codigo, configuracion y cualquier archivo que cayera ahi)."""

    def test_la_carpeta_admin_no_se_publica(self):
        c = server.app.test_client()
        for f in ('server.py', 'scheduling_config.json', 'test_seguridad.py'):
            with self.subTest(archivo=f):
                self.assertEqual(c.get('/./' + f).status_code, 404)
        self.assertIsNone(server.app.static_folder)


class TestRutasSoloLocal(unittest.TestCase):
    """Guarda de regresion: toda ruta que edita el sitio o la config debe estar en el
    set. Si alguien agrega una ruta de administracion nueva y se olvida, esto falla."""

    RUTAS_ADMIN_ESPERADAS = {
        '/api/info', '/api/equipo', '/api/casos', '/api/faq', '/api/doctores',
        '/api/equipo/agregar', '/api/equipo/eliminar', '/api/publicar',
        '/api/scheduling-config', '/api/upload', '/api/galeria',
        '/api/galeria/agregar', '/api/galeria/eliminar', '/api/galeria/renombrar',
        '/api/galeria/reordenar',
    }

    def test_todas_las_rutas_admin_estan_en_el_set(self):
        faltantes = self.RUTAS_ADMIN_ESPERADAS - server.RUTAS_SOLO_LOCAL
        self.assertEqual(faltantes, set(), f'rutas sin bloqueo en produccion: {faltantes}')

    def test_bloqueadas_devuelven_403_en_render(self):
        client = server.app.test_client()
        server.EN_RENDER = True
        try:
            for ruta in sorted(server.RUTAS_SOLO_LOCAL):
                with self.subTest(ruta=ruta):
                    self.assertEqual(client.post(ruta).status_code, 403)
        finally:
            server.EN_RENDER = False


class TestAuthEndpoints(unittest.TestCase):

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def test_pacientes_estado_exige_token(self):
        """Era el unico de los cinco /api/pacientes/* sin token."""
        self.assertEqual(self.client.get('/api/pacientes/estado').status_code, 403)

    def test_pacientes_estado_con_token_responde(self):
        r = self.client.get('/api/pacientes/estado',
                            headers={'X-Admin-Token': 'token-de-prueba'})
        self.assertEqual(r.status_code, 200)


class TestCoberturaDeAuth(unittest.TestCase):
    """La guarda mas importante del archivo.

    server.py tiene 162 rutas y el control de acceso se escribe A MANO en cada
    handler: 121 copias de `if not _check_admin_token(): return 403`. Copiar dos
    lineas es facil de olvidar — asi fue como /api/upload quedo abierto en
    produccion durante meses.

    En vez de refactorizar 121 sitios (riesgoso: un decorador mal puesto deja un
    endpoint sin llave y nadie se entera), esta prueba RECORRE todas las rutas y
    exige que cada una tenga control de acceso o este en la lista de publicas de
    abajo. Agregar una ruta sin llave y sin declararla acá hace fallar el test.

    Si estas leyendo esto porque el test fallo: NO agregues tu ruta a la lista
    sin pensarlo. Preguntate si de verdad tiene que ser publica."""

    # Rutas deliberadamente SIN token de administrador, con su razon.
    PUBLICAS = {
        '/api/<path:_any>':                     'preflight CORS (OPTIONS)',
        # Agendamiento online: lo usa el paciente desde el sitio, no hay sesion.
        '/api/agenda/config':                   'catalogo de doctores y motivos',
        '/api/agenda/paciente':                 'existe el RUT (rate limit 10/min)',
        '/api/agenda/citas-futuras':            'aviso de doble hora (rate limit 10/min)',
        '/api/agenda/link-info':                'datos enmascarados del link pre-cargado (rate limit 10/min)',
        '/api/agenda/disponibilidad':           'horas libres',
        '/api/agenda/disponibilidad-reagendar': 'horas libres al reagendar',
        '/api/agenda/reagendar-info':           'datos de la cita a reagendar',
        '/api/agenda/reservar':                 'crear la cita',
        '/api/agenda/reservar-reagenda':        'crear la cita al reagendar',
        '/api/agenda/reservar-estudio':         'crear las 2 citas del estudio',
        '/api/agenda/evento':                   'telemetria anonima del embudo',
        '/api/salud':                           'monitoreo de salud: hora de arranque y solicitudes lentas, sin datos personales ni de pacientes',
        # Auth propia, no ADMIN_TOKEN.
        '/api/consentimiento/datos':            'token firmado itsdangerous en la URL',
        '/api/seguro/pdf':                      'token firmado propio (el iframe no manda headers)',
        '/api/seguro/link-info':                'token opaco del link "cambié mi aseguradora" (rate limit 10/min)',
        '/api/seguro/actualizar-aseguradora':   'el paciente actualiza su aseguradora con el token del correo (rate limit 10/min)',
        '/api/whatsapp/webhook':                'firma HMAC de Meta, fail-closed',
        '/api/psq/enviar':                      'cuestionario de sueño: el paciente lo envía sin sesión (rate limit 10/min)',
        '/api/tamizaje/datos':                  'cuestionario de sueño del informe: lo abre el paciente escaneando el QR, sin sesión. La llave es un token firmado que vence en 2 h (rate limit 30/min)',
        '/api/tamizaje/enviar':                 'el paciente responde ese cuestionario desde su teléfono; mismo token firmado y con vencimiento (rate limit 10/min)',
        '/api/compras/login':                   'la puerta de entrada de Compras',
        '/api/compras/logout':                  'cierra la sesion de Compras',
        '/api/compras/me':                      'lee el X-Compras-Token del header',
        '/api/compras/setup':                   'primer admin; solo con 0 usuarios',
        '/api/compras/qr/<path:codigo>.png':    'imagen de QR, sin datos',
    }

    def _rutas_api(self):
        return [r for r in server.app.url_map.iter_rules() if str(r).startswith('/api/')]

    def test_toda_ruta_tiene_llave_o_esta_declarada_publica(self):
        import inspect
        sin_llave = []
        for r in self._rutas_api():
            ruta = str(r)
            if ruta in self.PUBLICAS or ruta in server.RUTAS_SOLO_LOCAL:
                continue
            fn = server.app.view_functions.get(r.endpoint)
            try:
                cuerpo = inspect.getsource(fn)
            except (OSError, TypeError):
                cuerpo = ''
            if not any(g in cuerpo for g in ('_check_admin_token', '_check_kiosk_token',
                                             '_require_compras', '_print_autorizado')):
                sin_llave.append(ruta)
        self.assertEqual(sorted(set(sin_llave)), [],
                         'rutas sin control de acceso y sin declarar como publicas')

    def test_la_lista_de_publicas_no_tiene_rutas_fantasma(self):
        """Si una ruta publica se renombra o borra, sacarla de la lista — si no,
        la lista deja de proteger y nadie se entera."""
        reales = {str(r) for r in self._rutas_api()}
        fantasma = set(self.PUBLICAS) - reales
        self.assertEqual(fantasma, set(), 'rutas declaradas publicas que ya no existen')

    def test_una_muestra_de_rutas_protegidas_responde_403(self):
        """Que el control no solo este escrito, sino que efectivamente corte."""
        client = server.app.test_client()
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        try:
            for ruta, metodo in [('/api/pacientes/estado', 'GET'),
                                 ('/api/recaptacion/config', 'GET'),
                                 ('/api/control-dental/inscritos', 'GET'),
                                 ('/api/nps/resumen', 'GET'),
                                 ('/api/cumpleanos/proximos', 'GET'),
                                 ('/api/whatsapp/config', 'GET')]:
                with self.subTest(ruta=ruta):
                    r = client.open(ruta, method=metodo)
                    self.assertEqual(r.status_code, 403, f'{ruta} deberia exigir token')
        finally:
            os.environ.pop('ADMIN_TOKEN', None)


class TestParametrosAgenda(unittest.TestCase):
    """Rutas publicas del modal de agendar: un parametro con basura no puede tumbar el
    flujo con una pagina HTML de error 500 (el frontend espera JSON)."""

    def setUp(self):
        self.client = server.app.test_client()

    def test_offset_no_numerico_no_revienta(self):
        for url in ('/api/agenda/disponibilidad?doctor=octavio&motivo=control&offset=abc',
                    '/api/agenda/disponibilidad?doctor=octavio&motivo=control&min_dias=xyz',
                    '/api/agenda/disponibilidad-reagendar?doctor=octavio&duracion=30&offset=--'):
            with self.subTest(url=url):
                r = self.client.get(url)
                self.assertNotEqual(r.status_code, 500)
                self.assertEqual(r.mimetype, 'application/json')


def _rut_con_dv(cuerpo):
    """RUT valido (con digito verificador) a partir de un numero inventado."""
    s, m = 0, 2
    for d in reversed(str(cuerpo)):
        s += int(d) * m
        m = 2 if m == 7 else m + 1
    dv = 11 - (s % 11)
    dv = '0' if dv == 11 else 'K' if dv == 10 else str(dv)
    return f'{cuerpo}-{dv}'


class TestLimitesDeVelocidad(unittest.TestCase):
    """Los topes contra abusos. En 12 rutas de la agenda el @rate_limit estaba ARRIBA
    del @app.route: Flask registraba la funcion sin envolver y el tope no existia
    (80 de 80 consultas aceptadas en /api/agenda/paciente). Nadie lo noto porque
    ninguna prueba lo miraba."""

    def setUp(self):
        self.client = server.app.test_client()
        if server.limiter:
            server.limiter.reset()
        server._RUTS_POR_IP.clear()

    def test_rate_limit_siempre_debajo_de_app_route(self):
        """El decorador de mas abajo se aplica primero: @app.route tiene que ir ARRIBA
        para registrar la funcion ya envuelta por el limite."""
        lineas = (Path(__file__).parent / 'server.py').read_text(encoding='utf-8').splitlines()
        mal = [i + 1 for i in range(len(lineas) - 1)
               if lineas[i].startswith('@rate_limit(') and lineas[i + 1].startswith('@app.route(')]
        self.assertEqual(mal, [], f'@rate_limit sobre @app.route en las lineas {mal}')

    @unittest.skipUnless(server.limiter, 'flask-limiter no instalado')
    def test_buscar_rut_se_frena_a_los_10_por_minuto(self):
        rut = _rut_con_dv(11111111)
        codigos = [self.client.get(f'/api/agenda/paciente?rut={rut}').status_code
                   for _ in range(11)]
        self.assertNotIn(429, codigos[:10])
        self.assertEqual(codigos[10], 429)

    @unittest.skipUnless(server.limiter, 'flask-limiter no instalado')
    def test_detras_de_cloudflare_el_tope_usa_la_ip_real(self):
        """En Render cada request llega desde una IP de Cloudflare distinta: con esa
        de llave ningun tope funcionaba. La llave tiene que ser CF-Connecting-IP."""
        rut = _rut_con_dv(11111111)
        codigos = [self.client.get(f'/api/agenda/paciente?rut={rut}',
                                   headers={'CF-Connecting-IP': '203.0.113.7'},
                                   environ_base={'REMOTE_ADDR': f'172.70.0.{i}'}).status_code
                   for i in range(11)]
        self.assertNotIn(429, codigos[:10])
        self.assertEqual(codigos[10], 429)

    def test_salud_dice_que_ip_se_usa_sin_mostrarla(self):
        r = self.client.get('/api/salud', headers={'CF-Connecting-IP': '203.0.113.7'})
        d = r.get_json()
        self.assertEqual(d['ip_fuente'], 'cf-connecting-ip')
        self.assertNotIn('203.0.113.7', r.get_data(as_text=True))

    def _consultar(self, rut, ip='10.0.0.1'):
        with server.app.test_request_context(environ_base={'REMOTE_ADDR': ip}):
            return server._tope_ruts_excedido(rut)

    def test_tope_de_20_rut_distintos_por_dispositivo(self):
        ruts = [_rut_con_dv(10000000 + i) for i in range(21)]
        for rut in ruts[:20]:
            self.assertFalse(self._consultar(rut))
        self.assertTrue(self._consultar(ruts[20]), 'el RUT numero 21 deberia frenarse')

    def test_repetir_un_rut_ya_visto_no_cuenta(self):
        """Reintentos, volver atras, doble clic: el mismo RUT no gasta cupo."""
        ruts = [_rut_con_dv(10000000 + i) for i in range(20)]
        for rut in ruts:
            self._consultar(rut)
        for _ in range(5):
            self.assertFalse(self._consultar(ruts[0]))

    def test_el_tope_es_por_dispositivo(self):
        for i in range(20):
            self._consultar(_rut_con_dv(10000000 + i), ip='10.0.0.1')
        self.assertFalse(self._consultar(_rut_con_dv(20000000), ip='10.0.0.2'))

    def test_pasada_una_hora_se_libera(self):
        ruts = [_rut_con_dv(10000000 + i) for i in range(21)]
        for rut in ruts[:20]:
            self._consultar(rut)
        for vistos in server._RUTS_POR_IP.values():
            for k in vistos:
                vistos[k] -= 3601
        self.assertFalse(self._consultar(ruts[20]))

    def test_la_respuesta_lleva_codigo_para_que_el_sitio_no_reintente(self):
        """Con 'codigo' el frontend no reintenta el 429 y sigue como paciente nuevo:
        una persona real igual puede agendar escribiendo sus datos."""
        for i in range(20):
            self._consultar(_rut_con_dv(10000000 + i), ip='127.0.0.1')
        r = self.client.get(f'/api/agenda/paciente?rut={_rut_con_dv(30000000)}')
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.get_json().get('codigo'), 'tope_rut')

    def test_con_admin_token_no_hay_tope(self):
        os.environ['ADMIN_TOKEN'] = 'tok-prueba'
        try:
            for i in range(25):
                with server.app.test_request_context(headers={'X-Admin-Token': 'tok-prueba'}):
                    self.assertFalse(server._tope_ruts_excedido(_rut_con_dv(10000000 + i)))
        finally:
            os.environ.pop('ADMIN_TOKEN', None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
