"""
test_paciente_estado_api.py - El enganche de paciente_estado.py al backend
(tarea B1): que /api/agenda/paciente devuelva el menu filtrado, que los 3
endpoints nuevos de asistente exijan ADMIN_TOKEN, y que el filtrado real
(un estado sembrado con set_manual) se refleje en la respuesta publica.

Cero red: DentiDesk deshabilitado (DENTIDESK_ENABLED=false) y patient_index
en un tempfile, mismo patron que test_seguridad.py.

    cd admin && python test_paciente_estado_api.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='pestado_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas.
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)   # que no arranquen los schedulers
sys.path.insert(0, str(Path(__file__).parent))

import server                     # noqa: E402
import paciente_estado as pe      # noqa: E402

RUT_1 = '12.345.678-5'   # sintetico, DV valido modulo 11
RUT_2 = '17.406.985-9'   # sintetico, DV valido modulo 11


class TestAgendaPacienteMenuFiltrado(unittest.TestCase):
    """El endpoint publico /api/agenda/paciente -- lo usa el paciente para
    entrar al wizard de agendamiento, nunca puede caerse por el filtrado."""

    def setUp(self):
        self.client = server.app.test_client()
        # Limpio el store entre pruebas (mismo tempfile para todo el archivo).
        pe._save_estado({'ultimo_barrido': '', 'pacientes': {}, 'motivos_desconocidos': {}})

    def test_rut_sin_estado_devuelve_menu_completo(self):
        r = self.client.get(f'/api/agenda/paciente?rut={RUT_1}')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertIn('motivos_permitidos', body)
        self.assertIn('estado_categoria', body)
        self.assertIsNone(body['motivos_permitidos'])
        self.assertEqual(body['estado_categoria'], 'nuevo')

    def test_estado_fijo_filtra_el_menu(self):
        """El filtrado real: un paciente con aparatos fijos hoy no deberia ver
        Primera Consulta ni Estudio Integral en el wizard."""
        pe.set_manual(RUT_1, 'fijo')
        r = self.client.get(f'/api/agenda/paciente?rut={RUT_1}')
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body['estado_categoria'], 'fijo')
        self.assertEqual(sorted(body['motivos_permitidos']), ['control_fijo', 'urgencia'])

    def test_rut_invalido_sigue_dando_400(self):
        """El filtrado no debe tapar la validacion de RUT que ya existia."""
        r = self.client.get('/api/agenda/paciente?rut=no-es-un-rut')
        self.assertEqual(r.status_code, 400)


class TestEndpointsAsistenteExigenToken(unittest.TestCase):
    """Los 3 endpoints nuevos son de administracion: ninguno es publico."""

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def test_get_paciente_estado_exige_token(self):
        r = self.client.get(f'/api/asistente/paciente-estado?rut={RUT_1}')
        self.assertEqual(r.status_code, 403)

    def test_post_paciente_estado_exige_token(self):
        r = self.client.post('/api/asistente/paciente-estado',
                             json={'rut': RUT_1, 'estado': 'fijo'})
        self.assertEqual(r.status_code, 403)

    def test_backfill_exige_token(self):
        r = self.client.post('/api/paciente-estado/backfill', json={'meses': 6})
        self.assertEqual(r.status_code, 403)


class TestAsistentePacienteEstadoConToken(unittest.TestCase):
    """Con token: guardar un estado a mano (F2/panel) y volver a leerlo."""

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        self.headers = {'X-Admin-Token': 'token-de-prueba'}
        pe._save_estado({'ultimo_barrido': '', 'pacientes': {}, 'motivos_desconocidos': {}})

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def test_guardar_y_leer_estado(self):
        r = self.client.post('/api/asistente/paciente-estado',
                             json={'rut': RUT_2, 'estado': 'alineadores'},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['estado'], 'alineadores')
        self.assertEqual(body['fuente'], 'manual')
        self.assertTrue(body['bloqueo_manual'])

        r2 = self.client.get(f'/api/asistente/paciente-estado?rut={RUT_2}',
                             headers=self.headers)
        self.assertEqual(r2.status_code, 200)
        body2 = r2.get_json()
        self.assertEqual(body2['estado'], 'alineadores')
        self.assertEqual(sorted(body2['motivos_permitidos']), ['control_alineadores', 'urgencia'])

    def test_limpiar_override_con_estado_vacio(self):
        pe.set_manual(RUT_2, 'removible')
        r = self.client.post('/api/asistente/paciente-estado',
                             json={'rut': RUT_2, 'estado': ''},
                             headers=self.headers)
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertFalse(body['bloqueo_manual'])

    def test_estado_invalido_da_400(self):
        r = self.client.post('/api/asistente/paciente-estado',
                             json={'rut': RUT_2, 'estado': 'inventado'},
                             headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_rut_invalido_da_400_en_get(self):
        r = self.client.get('/api/asistente/paciente-estado?rut=xxx', headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_rut_invalido_da_400_en_post(self):
        r = self.client.post('/api/asistente/paciente-estado',
                             json={'rut': 'xxx', 'estado': 'fijo'},
                             headers=self.headers)
        self.assertEqual(r.status_code, 400)

    def test_backfill_rechaza_modo_demo(self):
        """DENTIDESK_ENABLED=false en todo el archivo -- modo demo siempre."""
        r = self.client.post('/api/paciente-estado/backfill', json={'meses': 6},
                             headers=self.headers)
        self.assertEqual(r.status_code, 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
