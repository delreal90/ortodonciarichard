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
             mock.patch.object(dentidesk.requests, 'post', return_value=respuesta_mala):
            self.assertEqual(dentidesk._get_agenda_day(self.cfg, _DIA), [])
        self.assertEqual(len(dentidesk._AGENDA_DIA_CACHE), 0,
                         'un fallo NO puede quedar cacheado')

        # La llamada siguiente, ya con DentiDesk sano, debe traer los datos.
        respuesta_ok = mock.Mock(status_code=200)
        respuesta_ok.json.return_value = {'data': [{'IdAgenda': '1'}]}
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk.requests, 'post', return_value=respuesta_ok):
            self.assertEqual(dentidesk._get_agenda_day(self.cfg, _DIA),
                             [{'IdAgenda': '1'}])

    def test_json_invalido_tampoco_se_cachea(self):
        respuesta = mock.Mock(status_code=200, text='<html>error</html>')
        respuesta.json.side_effect = ValueError('no es JSON')
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk.requests, 'post', return_value=respuesta):
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
                'PatientDocument': '17.406.985-9', 'Phone': '56900000000',
                'PatientEmail': 'paciente@example.cl', 'PatientName': 'Nombre Apellido'}
        with self._con_cita(cita):
            crudo = self._diag().get_data(as_text=True)
        for sensible in ('17.406.985-9', '17406985', '56900000000',
                         'paciente@example.cl', 'Nombre Apellido'):
            self.assertNotIn(sensible, crudo)


if __name__ == '__main__':
    unittest.main(verbosity=2)
