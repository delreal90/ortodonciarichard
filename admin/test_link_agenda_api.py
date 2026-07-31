"""
test_link_agenda_api.py - Enganche de link_agenda.py al backend (tarea B2):
los 2 endpoints nuevos (crear el link desde el F2, resolverlo en la pagina de
agenda) y el soporte de 'link_token' en /api/agenda/reservar y
/api/agenda/citas-futuras.

Cero red: DentiDesk deshabilitado (DENTIDESK_ENABLED=false) y los stores en
tempfiles, mismo patron que test_seguridad.py / test_paciente_estado_api.py.

    cd admin && python test_link_agenda_api.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='link_agenda_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['LINKS_AGENDA_PATH'] = str(_TMP / 'links_agenda.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)   # que no arranquen los schedulers
sys.path.insert(0, str(Path(__file__).parent))

import server              # noqa: E402
import link_agenda         # noqa: E402
import pacientes            # noqa: E402
import scheduling           # noqa: E402

# RUTs sinteticos, DV valido modulo 11 (mismos que usa test_link_agenda.py /
# test_avisos.py -- ningun RUT real en este repo publico).
RUT_1 = '17.406.985-9'
RUT_2 = '12.345.678-5'


def _limpiar_links():
    link_agenda._save({'links': {}})


class TestDocKeyFlexible(unittest.TestCase):
    """La funcion mas fragil del cambio: el modal de F2 trae el doctor con
    titulo e inicial ("Dr. Alberto Del Real V."), pero professional_name en la
    config es solo "Alberto Del Real"."""

    def setUp(self):
        self.cfg = scheduling.load_config()

    def test_titulo_e_inicial_sueltos_matchean_un_solo_doctor(self):
        self.assertEqual(server._doc_key_flexible(self.cfg, 'Dr. Alberto Del Real V.'),
                         ['alberto'])

    def test_nombre_exacto_matchea_directo(self):
        self.assertEqual(server._doc_key_flexible(self.cfg, 'Alberto Del Real'), ['alberto'])

    def test_texto_vacio_no_matchea_nada(self):
        self.assertEqual(server._doc_key_flexible(self.cfg, ''), [])

    def test_apellido_compartido_matchea_mas_de_uno(self):
        """Octavio y Alberto comparten apellido 'Del Real' -- sin nombre de
        pila, el texto es ambiguo a proposito (verifica que la lista SI puede
        traer >1, no solo 0/1)."""
        encontrados = server._doc_key_flexible(self.cfg, 'Dr. Del Real')
        self.assertGreaterEqual(len(encontrados), 2)
        self.assertIn('octavio', encontrados)
        self.assertIn('alberto', encontrados)


class TestAsistenteLinkAgenda(unittest.TestCase):
    """POST /api/asistente/link-agenda -- lo llama el F2, protegido por
    ADMIN_TOKEN."""

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        _limpiar_links()
        # El paciente del link tiene que tener email en su ficha: por link nunca
        # se le pide (el wizard salta ese paso) y DentiDesk lo exige para crear
        # la cita. Sin ficha con email, generar el link se rechaza (ver
        # test_sin_email_en_la_ficha_no_deja_generar_el_link).
        pacientes._save_index({pacientes._limpiar_rut(RUT_1):
                               {'nombres': 'Ana', 'apellidos': 'Soto',
                                'email': 'ana.soto@example.com', 'telefono': '911111111'}})

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def _post(self, body, headers=True):
        h = {'X-Admin-Token': 'token-de-prueba'} if headers else {}
        return self.client.post('/api/asistente/link-agenda', json=body, headers=h)

    def test_exige_admin_token(self):
        r = self._post({'rut': RUT_1, 'doctor_texto': 'Alberto Del Real', 'motivo': 'control_fijo'},
                       headers=False)
        self.assertEqual(r.status_code, 403)

    def test_doctor_no_encontrado_da_422_con_lista(self):
        r = self._post({'rut': RUT_1, 'doctor_texto': 'Doctor Que No Existe',
                        'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 422)
        body = r.get_json()
        self.assertFalse(body['ok'])
        self.assertIn('doctores', body)
        self.assertTrue(len(body['doctores']) >= 1)
        self.assertIn('key', body['doctores'][0])
        self.assertIn('nombre', body['doctores'][0])

    def test_doctor_ambiguo_da_422_con_lista_de_mas_de_uno(self):
        r = self._post({'rut': RUT_1, 'doctor_texto': 'Dr. Del Real', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 422)
        body = r.get_json()
        self.assertFalse(body['ok'])
        self.assertGreaterEqual(len(body['doctores']), 2)

    def test_doctor_key_explicito_tiene_precedencia_sobre_doctor_texto(self):
        """doctor_texto matchea 'octavio' (o nada), pero doctor_key='alberto'
        manda -- se usa directo, sin matchear texto."""
        r = self._post({'rut': RUT_1, 'doctor_texto': 'un texto que no matchea a nadie',
                        'doctor_key': 'alberto', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['ok'])
        # Verificamos contra el registro real que el doctor guardado es 'alberto'.
        token = body['url'].rsplit('=', 1)[-1]
        resuelto = link_agenda.resolver(token)
        self.assertEqual(resuelto['doctor'], 'alberto')

    def test_motivo_compuesto_estudio_integral_rechazado(self):
        r = self._post({'rut': RUT_1, 'doctor_key': 'alberto', 'motivo': 'estudio_integral'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])

    def test_especialidad_de_motivo_distinta_a_la_del_doctor_rechazada(self):
        """'alberto' es ortodoncista; 'rehab_urgencia' es de rehabilitacion."""
        r = self._post({'rut': RUT_1, 'doctor_key': 'alberto', 'motivo': 'rehab_urgencia'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()['ok'])

    def test_control_pasivo_es_reservable_via_link(self):
        """control_pasivo es 'solo_filtrado' (no aparece en el menu online
        normal), pero por link SI se permite -- el link-agenda no aplica esa
        restriccion, a proposito."""
        r = self._post({'rut': RUT_1, 'doctor_key': 'alberto', 'motivo': 'control_pasivo'})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['ok'])
        token = body['url'].rsplit('=', 1)[-1]
        resuelto = link_agenda.resolver(token)
        self.assertTrue(resuelto['ok'])
        self.assertEqual(resuelto['motivo'], 'control_pasivo')

    def test_rut_invalido_da_400(self):
        r = self._post({'rut': 'no-es-un-rut', 'doctor_key': 'alberto', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 400)

    def test_ok_devuelve_nombre_del_paciente_si_esta_en_la_base(self):
        pacientes._save_index({pacientes._limpiar_rut(RUT_2):
                               {'nombres': 'Maria', 'apellidos': 'Perez',
                                'email': 'maria.perez@example.com', 'telefono': '987654321'}})
        r = self._post({'rut': RUT_2, 'doctor_key': 'alberto', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()['nombre'], 'Maria Perez')

    def test_sin_email_en_la_ficha_no_deja_generar_el_link(self):
        """El paciente que entra por link NUNCA escribe su email (el wizard se
        salta ese paso) y DentiDesk lo exige para crear la cita. Se corta al
        GENERAR -- la secretaria tiene la ficha abierta y puede arreglarlo;
        dejarlo pasar le daria al paciente un error sin salida al final."""
        pacientes._save_index({pacientes._limpiar_rut(RUT_2):
                               {'nombres': 'Sin', 'apellidos': 'Email', 'email': '', 'telefono': '912345678'}})
        r = self._post({'rut': RUT_2, 'doctor_key': 'alberto', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 409)
        self.assertFalse(r.get_json()['ok'])
        self.assertIn('email', r.get_json()['error'].lower())

    def test_rut_que_no_esta_en_la_base_tampoco_deja_generar_el_link(self):
        pacientes._save_index({})
        r = self._post({'rut': RUT_1, 'doctor_key': 'alberto', 'motivo': 'control_fijo'})
        self.assertEqual(r.status_code, 409)


class TestAgendaLinkInfo(unittest.TestCase):
    """GET /api/agenda/link-info?token= -- PUBLICA, nunca debe exponer datos
    crudos del paciente."""

    def setUp(self):
        self.client = server.app.test_client()
        _limpiar_links()
        pacientes._save_index({})

    def test_nunca_expone_rut_ni_email_completos(self):
        rut_limpio = pacientes._limpiar_rut(RUT_1)
        pacientes._save_index({rut_limpio: {
            'nombres': 'Juan', 'apellidos': 'Soto',
            'email': 'juan.soto.secreto@example.com', 'telefono': '912345678',
        }})
        creado = link_agenda.crear(RUT_1, 'alberto', 'control_fijo')
        r = self.client.get(f'/api/agenda/link-info?token={creado["token"]}')
        self.assertEqual(r.status_code, 200)
        crudo = r.get_data(as_text=True)
        self.assertNotIn(rut_limpio, crudo)
        self.assertNotIn('juan.soto.secreto@example.com', crudo)
        body = r.get_json()
        self.assertEqual(body['paciente']['nombres'], 'Juan')
        self.assertIn('email_masked', body['paciente'])
        self.assertNotEqual(body['paciente']['email_masked'], '')

    def test_token_basura_da_error_json_no_500_no_html(self):
        r = self.client.get('/api/agenda/link-info?token=esto-no-existe-nunca')
        self.assertIn(r.status_code, (404, 410))
        body = r.get_json()
        self.assertIsNotNone(body, 'la respuesta debe ser JSON, no una pagina de error')
        self.assertFalse(body['ok'])
        self.assertIn('motivo', body)

    def test_token_vacio_da_error_json(self):
        r = self.client.get('/api/agenda/link-info?token=')
        self.assertIn(r.status_code, (404, 410))
        self.assertFalse(r.get_json()['ok'])

    def test_link_valido_trae_doctor_y_motivo(self):
        creado = link_agenda.crear(RUT_1, 'alberto', 'control_fijo')
        r = self.client.get(f'/api/agenda/link-info?token={creado["token"]}')
        body = r.get_json()
        self.assertEqual(body['doctor']['key'], 'alberto')
        self.assertEqual(body['motivo']['key'], 'control_fijo')


class TestReservarConLinkToken(unittest.TestCase):
    """El soporte de 'link_token' en /api/agenda/reservar."""

    def setUp(self):
        self.client = server.app.test_client()
        _limpiar_links()

    def test_token_ya_usado_da_409(self):
        creado = link_agenda.crear(RUT_1, 'alberto', 'control_fijo')
        marcado = link_agenda.marcar_usado(creado['token'])
        self.assertIsNotNone(marcado)
        r = self.client.post('/api/agenda/reservar', json={'link_token': creado['token']})
        self.assertEqual(r.status_code, 409)
        body = r.get_json()
        self.assertFalse(body['ok'])
        self.assertEqual(body.get('motivo'), 'usado')

    def test_token_vencido_da_409(self):
        creado = link_agenda.crear(RUT_1, 'alberto', 'control_fijo', dias_expira=-1)
        r = self.client.post('/api/agenda/reservar', json={'link_token': creado['token']})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json().get('motivo'), 'expirado')

    def test_token_inexistente_da_409(self):
        r = self.client.post('/api/agenda/reservar', json={'link_token': 'no-existe-jamas'})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json().get('motivo'), 'no_existe')


class TestCitasFuturasConLinkToken(unittest.TestCase):
    """/api/agenda/citas-futuras acepta 'link_token' como alternativa a 'rut'."""

    def setUp(self):
        self.client = server.app.test_client()
        _limpiar_links()

    def test_sin_rut_ni_link_token_da_400(self):
        r = self.client.get('/api/agenda/citas-futuras')
        self.assertEqual(r.status_code, 400)

    def test_con_link_token_valido_resuelve_y_responde_200(self):
        creado = link_agenda.crear(RUT_1, 'alberto', 'control_fijo')
        r = self.client.get(f'/api/agenda/citas-futuras?link_token={creado["token"]}')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['ok'])
        self.assertIn('citas', body)

    def test_con_link_token_invalido_y_sin_rut_sigue_dando_400(self):
        r = self.client.get('/api/agenda/citas-futuras?link_token=basura-no-existe')
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
