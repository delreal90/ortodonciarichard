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


if __name__ == '__main__':
    unittest.main(verbosity=2)
