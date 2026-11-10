"""
test_reagenda_diagnostico.py - El link de reagendar: resolucion de motivo y
doctor, y el diagnostico de por que un link falla.

Nace de un caso real (2026-08-07): a un paciente que no vino se le mando el
WhatsApp de inasistencia y su link mostro "no disponible". No se pudo saber por
que, porque las ~6 ramas de fallo devolvian el MISMO mensaje generico y ninguna
quedaba registrada. Estas pruebas fijan las dos cosas que lo arreglan:

  1. La resolucion de motivo/doctor ahora NORMALIZA (tildes, mayusculas,
     espacios de mas). Antes era byte-a-byte: 'control fijo' no resolvia.
  2. Cada rama de fallo tiene su propio 'codigo' -- eso es lo que permite
     mostrarle al paciente un mensaje que corresponde y saber que paso.

Cero red: DentiDesk deshabilitado y stores en tempfiles (mismo patron que
test_link_agenda_api.py).

    cd admin && python test_reagenda_diagnostico.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

_DIA = date(2026, 8, 6)   # _get_agenda_day recibe un date, no un string

_TMP = Path(tempfile.mkdtemp(prefix='reagenda_diag_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)   # que no arranquen los schedulers
sys.path.insert(0, str(Path(__file__).parent))

import server              # noqa: E402
import dentidesk           # noqa: E402
import scheduling          # noqa: E402


def _cfg_real():
    """La config versionada de verdad -- estas pruebas valen justamente porque
    corren contra los 186 motivos reales de motivos_id_reason_extra."""
    return scheduling.load_config()


class TestResolucionDeMotivo(unittest.TestCase):
    """dentidesk.id_reason_por_label: el match es lo que decidia si el link
    servia o mostraba 'no disponible'."""

    def setUp(self):
        self.cfg = _cfg_real()

    def test_nombre_exacto_resuelve(self):
        self.assertEqual(
            str(dentidesk.id_reason_por_label(self.cfg, 'alberto', 'Control Fijo')), '16073')

    def test_mayusculas_espacios_y_tildes_no_rompen_el_match(self):
        # Estas tres FALLABAN antes del arreglo (comparacion byte-a-byte).
        for variante in ('control fijo', 'CONTROL FIJO', '  Control  Fijo '):
            with self.subTest(variante=variante):
                self.assertEqual(
                    str(dentidesk.id_reason_por_label(self.cfg, 'alberto', variante)), '16073')

    def test_sin_tilde_resuelve_igual(self):
        self.assertEqual(
            str(dentidesk.id_reason_por_label(self.cfg, 'alberto', 'Control Contencion')), '27245')

    def test_motivo_inexistente_no_inventa(self):
        self.assertIsNone(
            dentidesk.id_reason_por_label(self.cfg, 'alberto', 'Motivo Que No Existe'))

    def test_clave_de_comentario_no_resuelve(self):
        """motivos_id_reason_extra trae un '_comment' cuyo valor es un texto
        largo: no puede colarse como si fuera un IdReason."""
        self.assertIsNone(dentidesk.id_reason_por_label(self.cfg, 'alberto', '_comment'))

    def test_label_vacio_no_resuelve(self):
        for vacio in ('', '   ', None):
            with self.subTest(label=vacio):
                self.assertIsNone(dentidesk.id_reason_por_label(self.cfg, 'alberto', vacio))

    def test_ante_dos_motivos_iguales_no_adivina(self):
        """Normalizar podria hacer que dos nombres distintos colapsen en uno.
        Si eso pasa con IdReason DISTINTOS, se devuelve None: mejor derivar a
        WhatsApp que agendar el tipo de cita equivocado (paso de verdad, un
        'Imp essix' termino como 'Control Fijo')."""
        cfg = {'doctores': {'alberto': {'especialidad': 'ortodoncia'}},
               'motivos': {},
               'motivos_id_reason_extra': {'Control  Fijo': 111, 'control fijo': 222}}
        self.assertIsNone(dentidesk.id_reason_por_label(cfg, 'alberto', 'Control Fijo'))

    def test_la_config_real_no_tiene_motivos_ambiguos(self):
        """Guarda hacia el futuro: si la clinica agrega a la tabla un motivo que
        normaliza igual que otro con distinto IdReason, esta prueba lo caza
        ANTES del deploy (si no, ese motivo dejaria de resolver en silencio)."""
        vistos = {}
        for nombre, idr in (self.cfg.get('motivos_id_reason_extra') or {}).items():
            if nombre.startswith('_'):
                continue
            clave = dentidesk._norm_motivo(nombre)
            if clave in vistos and str(vistos[clave][1]) != str(idr):
                self.fail(f'Motivos ambiguos al normalizar: {vistos[clave][0]!r} '
                          f'({vistos[clave][1]}) vs {nombre!r} ({idr})')
            vistos[clave] = (nombre, idr)


class TestResolucionDeDoctor(unittest.TestCase):

    def setUp(self):
        self.cfg = _cfg_real()

    def test_nombre_exacto_y_variantes(self):
        for variante in ('Alberto Del Real', 'alberto del real', '  Alberto  Del Real '):
            with self.subTest(variante=variante):
                self.assertEqual(dentidesk.doc_key_por_nombre(self.cfg, variante), 'alberto')

    def test_desconocido_devuelve_vacio(self):
        self.assertEqual(dentidesk.doc_key_por_nombre(self.cfg, 'Nadie Que Exista'), '')

    def test_con_titulo_tambien_resuelve(self):
        """El config guarda 'Alberto Del Real' y la API de DentiDesk devuelve el
        ProfessionalName asi, pero el MODAL de la cita --de donde lee el F2--
        lo muestra 'Dr. Alberto Del Real'. Sin esto no calzaba, y el informe de
        evaluacion salia SIN FIRMA aunque el doctor la tuviera cargada."""
        for variante in ('Dr. Alberto Del Real', 'DR. ALBERTO DEL REAL',
                         'Dr Alberto Del Real', 'Dra. Alberto Del Real'):
            with self.subTest(variante=variante):
                self.assertEqual(dentidesk.doc_key_por_nombre(self.cfg, variante), 'alberto')

    def test_el_titulo_no_convierte_a_cualquiera_en_doctor(self):
        """Sacar el titulo hace el match mas permisivo; no puede hacerlo laxo."""
        self.assertEqual(dentidesk.doc_key_por_nombre(self.cfg, 'Dr. Nadie Que Exista'), '')
        self.assertEqual(dentidesk.doc_key_por_nombre(self.cfg, 'Dr.'), '')

    def test_sin_titulo_doctor(self):
        self.assertEqual(dentidesk.sin_titulo_doctor('Dra. Ana Perez'), 'Ana Perez')
        self.assertEqual(dentidesk.sin_titulo_doctor('Alberto Del Real'), 'Alberto Del Real')
        self.assertEqual(dentidesk.sin_titulo_doctor(''), '')
        # No es un prefijo: no se le puede comer el nombre.
        self.assertEqual(dentidesk.sin_titulo_doctor('Drago Milic'), 'Drago Milic')


class TestCacheNoGuardaFallos(unittest.TestCase):
    """_get_agenda_day cacheaba 10 minutos la lista vacia de una llamada
    FALLIDA: un blip de red se convertia en 10 min de 'no encontramos esa cita'
    para todo el sistema."""

    def setUp(self):
        dentidesk._AGENDA_DIA_CACHE.clear()
        self.cfg = _cfg_real()
        self.cfg['dentidesk'] = dict(self.cfg['dentidesk'], enabled=True,
                                     base_url='https://ejemplo.invalido')

    def tearDown(self):
        dentidesk._AGENDA_DIA_CACHE.clear()

    def test_respuesta_de_error_no_se_cachea(self):
        respuesta_mala = mock.Mock(status_code=500, text='Internal Server Error')
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk._HTTP, 'post', return_value=respuesta_mala):
            self.assertEqual(dentidesk._get_agenda_day(self.cfg, _DIA), [])
        self.assertEqual(len(dentidesk._AGENDA_DIA_CACHE), 0,
                         'un fallo NO puede quedar cacheado')

        # La llamada siguiente, ya con DentiDesk sano, debe traer los datos.
        respuesta_ok = mock.Mock(status_code=200)
        respuesta_ok.json.return_value = {'data': [{'IdAgenda': '1'}]}
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk._HTTP, 'post', return_value=respuesta_ok):
            self.assertEqual(dentidesk._get_agenda_day(self.cfg, _DIA),
                             [{'IdAgenda': '1'}])

    def test_json_invalido_tampoco_se_cachea(self):
        respuesta = mock.Mock(status_code=200, text='<html>error</html>')
        respuesta.json.side_effect = ValueError('no es JSON')
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk._HTTP, 'post', return_value=respuesta):
            self.assertEqual(dentidesk._get_agenda_day(self.cfg, _DIA), [])
        self.assertEqual(len(dentidesk._AGENDA_DIA_CACHE), 0)


class _EndpointBase(unittest.TestCase):
    """reagendar-info exige DentiDesk habilitado, asi que se fuerza en la config
    (sin red: info_cita va mockeado en cada prueba)."""

    def setUp(self):
        self.client = server.app.test_client()
        cfg = _cfg_real()
        cfg['dentidesk'] = dict(cfg['dentidesk'], enabled=True)
        self._patch_cfg = mock.patch.object(server.scheduling, 'load_config', return_value=cfg)
        self._patch_cfg.start()
        self.addCleanup(mock.patch.stopall)

    def _get(self, id_agenda='1', fecha='2026-08-06'):
        return self.client.get(
            f'/api/agenda/reagendar-info?id_agenda={id_agenda}&fecha={fecha}')


class TestReagendarInfoCodigos(_EndpointBase):
    """Cada falla con su codigo: es lo que permite mostrarle al paciente un
    mensaje que corresponde en vez de uno generico para todo."""

    def test_sin_parametros(self):
        r = self.client.get('/api/agenda/reagendar-info')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['codigo'], 'sin_parametros')

    def test_fecha_invalida(self):
        r = self._get(fecha='no-es-fecha')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()['codigo'], 'fecha_invalida')

    def test_cita_no_encontrada(self):
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=None):
            r = self._get()
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.get_json()['codigo'], 'cita_no_encontrada')

    def test_cita_cancelada_no_es_reagendable(self):
        cita = {'Status': 'Hora Cancelada', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo', 'duration': 15}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita):
            r = self._get()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['codigo'], 'cita_no_vigente')

    def test_paciente_no_llega_SI_puede_reagendar(self):
        """El caso reportado: el paciente que no vino es justamente a quien se
        le manda el link de inasistencia. Su estado NO puede bloquearlo."""
        cita = {'Status': 'Paciente no llega', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo', 'duration': 15, 'time': '10:00:00'}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita):
            r = self._get()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()['ok'])

    def test_pidio_cambiar_su_hora_SI_puede_reagendar(self):
        """El estado nuevo (33579) marca la INTENCION de cambiar la hora: seria
        absurdo que impidiera justamente reagendar. Por eso su nombre no puede
        contener 'reagend' (ver _ESTADOS_NO_REAGENDABLES)."""
        cita = {'Status': 'Pidió cambiar su hora', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo', 'duration': 15, 'time': '10:00:00'}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita):
            r = self._get()
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertTrue(r.get_json()['ok'])

    def test_doctor_no_resuelto(self):
        cita = {'Status': 'No confirmado', 'ProfessionalName': 'Doctor Fantasma',
                'Reason': 'Control Fijo', 'duration': 15}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita):
            r = self._get()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['codigo'], 'doctor_no_resuelto')

    def test_motivo_no_resuelto(self):
        cita = {'Status': 'No confirmado', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Motivo Inventado Que No Existe', 'duration': 15}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita):
            r = self._get()
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()['codigo'], 'motivo_no_resuelto')

    def test_excepcion_devuelve_502_json_y_no_html(self):
        """Un timeout de DentiDesk salia como pagina HTML 500 de Flask: el
        frontend ni siquiera podia leerla para explicar que paso."""
        with mock.patch.object(server.dentidesk, 'info_cita',
                               side_effect=RuntimeError('timeout')):
            r = self._get()
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.get_json()['codigo'], 'error_dentidesk')
        self.assertNotIn('<!doctype', r.get_data(as_text=True).lower())


class TestDiagnostico(_EndpointBase):
    """El endpoint que responde 'por que no le funciona el link a este
    paciente' sin tener que adivinar entre 6 causas."""

    def setUp(self):
        super().setUp()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        self.headers = {'X-Admin-Token': 'token-de-prueba'}

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def _diag(self, headers=True):
        return self.client.get(
            '/api/agenda/diagnostico-reagenda?id_agenda=1&fecha=2026-08-06',
            headers=self.headers if headers else {})

    def _con_cita(self, cita):
        """El diagnostico NO usa info_cita: relee la agenda del dia entera (asi
        puede reportar cuantas citas vio) y ubica la suya por IdAgenda."""
        cita = dict(cita, IdAgenda='1')
        return mock.patch.object(server.dentidesk, '_get_agenda_day', return_value=[cita])

    def test_exige_admin_token(self):
        self.assertEqual(self._diag(headers=False).status_code, 403)

    def test_motivo_desconocido_senala_el_paso_y_sugiere(self):
        cita = {'Status': 'Paciente no llega', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo Superior', 'duration': 15}
        with self._con_cita(cita):
            r = self._diag()
        self.assertEqual(r.status_code, 200)
        pasos = r.get_json()['pasos']
        self.assertTrue(pasos['cita']['ok'])
        self.assertTrue(pasos['doctor']['ok'])
        self.assertFalse(pasos['motivo']['ok'], 'el motivo es el que falla')
        self.assertTrue(pasos['motivo'].get('sugerencias'),
                        'debe sugerir el nombre parecido para poder corregirlo')

    def test_cita_sana_pasa_todos_los_pasos(self):
        cita = {'Status': 'No confirmado', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo', 'duration': 15, 'time': '10:00:00'}
        with self._con_cita(cita):
            r = self._diag()
        pasos = r.get_json()['pasos']
        for nombre in ('cita', 'estado', 'doctor', 'motivo', 'duracion'):
            self.assertTrue(pasos[nombre]['ok'], f'paso {nombre}: {pasos[nombre]}')

    def test_no_expone_datos_del_paciente(self):
        """El diagnostico se mira, se pega en un correo y se comenta: no puede
        llevar RUT, telefono ni email (repo publico, logs de Render)."""
        cita = {'Status': 'No confirmado', 'ProfessionalName': 'Alberto Del Real',
                'Reason': 'Control Fijo', 'duration': 15, 'time': '10:00:00',
                'PatientDocument': '14.141.411-9', 'Phone': '56900000000',
                'PatientEmail': 'paciente@example.cl', 'PatientName': 'Nombre Apellido'}
        with self._con_cita(cita):
            crudo = self._diag().get_data(as_text=True)
        for sensible in ('14.141.411-9', '14141411', '56900000000',
                         'paciente@example.cl', 'Nombre Apellido'):
            self.assertNotIn(sensible, crudo)


RUT_DUENO = '12.345.678-5'   # sinteticos, DV valido
RUT_OTRO = '11.111.111-1'


class TestReagendaSoloElDueno(_EndpointBase):
    """Auditoria 2026-11-10: el link de reagendar no es una llave (solo lleva
    id_agenda + fecha) y /citas-futuras entregaba ese id a quien escribiera un
    RUT. Con eso, un tercero podia marcar 'Re-agendado' la cita de otra persona.
    Estas pruebas fijan los dos cierres."""

    CITA = {'Status': 'No confirmado', 'ProfessionalName': 'Alberto Del Real',
            'Reason': 'Control Fijo', 'duration': 15, 'time': '10:00:00',
            'PatientDocument': RUT_DUENO}

    def _reagendar(self, rut, cita=None):
        cuerpo = {'id_agenda': '555', 'fecha_original': '2026-08-06', 'doctor': 'alberto',
                  'fecha': '2026-08-20', 'hora': '10:00', 'rut': rut,
                  'nombres': 'Ana', 'apellidos': 'Prueba', 'email': 'a@example.cl'}
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=cita or self.CITA), \
             mock.patch.object(server.dentidesk, 'crear_cita') as crear, \
             mock.patch.object(server.dentidesk, 'actualizar_estado_cita') as actualizar:
            r = self.client.post('/api/agenda/reservar-reagenda', json=cuerpo)
        return r, crear, actualizar

    def test_rut_ajeno_no_toca_la_cita(self):
        r, crear, actualizar = self._reagendar(RUT_OTRO)
        self.assertEqual(r.status_code, 403, r.get_data(as_text=True))
        self.assertEqual(r.get_json()['codigo'], 'rut_no_coincide')
        crear.assert_not_called()
        actualizar.assert_not_called()

    def test_cita_sin_rut_no_calza_con_nadie(self):
        cita = dict(self.CITA, PatientDocument='')
        r, crear, actualizar = self._reagendar(RUT_OTRO, cita)
        self.assertEqual(r.status_code, 403)
        crear.assert_not_called()
        actualizar.assert_not_called()

    def test_el_dueno_pasa_la_verificacion(self):
        """Con su propio RUT no se corta en 'rut_no_coincide' (lo que venga
        despues -- ventana, disponibilidad -- es otra prueba)."""
        r, _, _ = self._reagendar(RUT_DUENO.replace('.', ''))
        self.assertNotEqual((r.get_json() or {}).get('codigo'), 'rut_no_coincide')

    def test_respaldo_por_reservar_tambien_verifica(self):
        cfg = server.scheduling.load_config()
        with mock.patch.object(server.dentidesk, 'info_cita', return_value=self.CITA):
            self.assertTrue(server._reagenda_es_del_paciente(cfg, '555', '2026-08-06', RUT_DUENO))
            self.assertFalse(server._reagenda_es_del_paciente(cfg, '555', '2026-08-06', RUT_OTRO))
            # sin fecha no hay como releer la cita: no se da por verificada
            self.assertFalse(server._reagenda_es_del_paciente(cfg, '555', '', RUT_DUENO))
        with mock.patch.object(server.dentidesk, 'info_cita', side_effect=RuntimeError('caido')):
            self.assertFalse(server._reagenda_es_del_paciente(cfg, '555', '2026-08-06', RUT_DUENO))

    def test_citas_futuras_no_entrega_el_numero_de_cita(self):
        citas = [{'id_agenda': '555', 'fecha': '2026-08-06', 'hora': '10:00',
                  'profesional': 'Alberto Del Real', 'motivo': 'Control Fijo', 'estado': 'No confirmado'},
                 {'id_agenda': '777', 'fecha': '2026-08-13', 'hora': '11:00',
                  'profesional': 'Alberto Del Real', 'motivo': 'Control Fijo', 'estado': 'No confirmado'}]
        server._CITAS_FUT_CACHE.clear()
        with mock.patch.object(server.dentidesk, 'citas_futuras_paciente', return_value=citas):
            todas = self.client.get(f'/api/agenda/citas-futuras?rut={RUT_DUENO}').get_json()['citas']
            sin_la_propia = self.client.get(
                f'/api/agenda/citas-futuras?rut={RUT_DUENO}&excluir=555').get_json()['citas']
        self.assertEqual(len(todas), 2)
        self.assertTrue(all('id_agenda' not in c for c in todas))
        self.assertEqual([c['fecha'] for c in sin_la_propia], ['2026-08-13'])


class TestNombreAlPublico(unittest.TestCase):
    """/api/agenda/paciente es publica: RUT -> nombre completo + etapa de
    tratamiento era un dato de salud con nombre y apellido para cualquiera."""

    def test_solo_primer_nombre_e_inicial(self):
        self.assertEqual(server._nombre_publico('María José', 'Pérez Soto'), ('María', 'P.'))
        self.assertEqual(server._nombre_publico('', ''), ('', ''))

    def test_la_ruta_no_devuelve_apellido_ni_etapa(self):
        info = {'existe': True, 'datos': {'nombres': 'María José', 'apellidos': 'Pérez Soto',
                                          'email_masked': 'ma***@gm***.cl', 'tiene_email': True}}
        with mock.patch.object(server.dentidesk, 'buscar_paciente', return_value=info):
            cuerpo = server.app.test_client().get('/api/agenda/paciente?rut=' + RUT_OTRO).get_json()
        self.assertEqual(cuerpo['datos']['nombres'], 'María')
        self.assertEqual(cuerpo['datos']['apellidos'], 'P.')
        self.assertNotIn('estado_categoria', cuerpo)
        self.assertNotIn('Soto', str(cuerpo))

    def test_nombre_del_publico_se_sanea(self):
        self.assertEqual(server._limpiar_nombre('  <script>alert(1)</script> Ana\n'),
                         'script alert(1) /script Ana')
        self.assertLessEqual(len(server._limpiar_nombre('x' * 500)), 80)


if __name__ == '__main__':
    unittest.main(verbosity=2)
