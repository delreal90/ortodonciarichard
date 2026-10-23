"""
test_fotos_api.py - Las dos rutas que usa el observador de fotos de DIGITAL1.

Cero red y cero correo: DentiDesk y el SMTP quedan interceptados.

    cd admin && python test_fotos_api.py
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='fotos_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import server              # noqa: E402

TOKEN = 'token-de-prueba'
H = {'X-Admin-Token': TOKEN}


class TestAgendaDia(unittest.TestCase):
    def setUp(self):
        os.environ['ADMIN_TOKEN'] = TOKEN
        self.c = server.app.test_client()

    def test_sin_llave_403(self):
        self.assertEqual(self.c.get('/api/fotos/agenda-dia?fecha=2026-10-20').status_code, 403)

    def test_fecha_mala_o_futura_400(self):
        self.assertEqual(self.c.get('/api/fotos/agenda-dia?fecha=ayer', headers=H).status_code, 400)
        self.assertEqual(self.c.get('/api/fotos/agenda-dia?fecha=2999-01-01', headers=H).status_code, 400)

    def test_devuelve_citas_con_nombre_de_la_base(self):
        cfg = server.scheduling.load_config()
        cfg['dentidesk']['enabled'] = True
        cita = {'time': '10:15:00', 'duration': 30, 'ProfessionalName': 'Dr. Alberto Del Real',
                'Reason': 'Control', 'Status': 'Atendido', 'IdStatus': 2126,
                'PatientDocument': '11111111-1', 'PatientName': 'Perez Soto Juan'}
        with mock.patch.object(server.scheduling, 'load_config', return_value=cfg), \
             mock.patch.object(server.dentidesk, '_get_agenda_day', return_value=[cita]), \
             mock.patch('pacientes.lookup', return_value={'nombres': 'Juan', 'apellidos': 'Perez Soto'}):
            r = self.c.get('/api/fotos/agenda-dia?fecha=2026-10-20', headers=H)
        self.assertEqual(r.status_code, 200)
        c = r.get_json()['citas'][0]
        self.assertEqual((c['hora'], c['doctor'], c['nombres'], c['apellidos']),
                         ('10:15', 'Alberto Del Real', 'Juan', 'Perez Soto'))


class TestAviso(unittest.TestCase):
    def setUp(self):
        os.environ['ADMIN_TOKEN'] = TOKEN
        self.c = server.app.test_client()

    def test_sin_llave_403(self):
        self.assertEqual(self.c.post('/api/fotos/aviso', json={'tipo': 'listo'}).status_code, 403)

    def test_tipo_invalido_400(self):
        r = self.c.post('/api/fotos/aviso', json={'tipo': 'otro'}, headers=H)
        self.assertEqual(r.status_code, 400)

    def test_manda_correo_escapado(self):
        enviado = {}

        def falso(destino, asunto, html, remitente='x'):
            enviado.update(destino=destino, asunto=asunto, html=html)
            return {'ok': True}
        with mock.patch.dict(os.environ, {'EMAIL_ALBERTO': 'dr@ejemplo.cl'}), \
             mock.patch.object(server.notify, '_enviar_html', side_effect=falso):
            r = self.c.post('/api/fotos/aviso', headers=H, json={
                'tipo': 'listo', 'resumen': {'Aciertos': '96 %', '<b>x</b>': 1}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(enviado['destino'], 'dr@ejemplo.cl')
        self.assertIn('&lt;b&gt;x&lt;/b&gt;', enviado['html'])
        self.assertNotIn('<b>x</b>', enviado['html'])


if __name__ == '__main__':
    unittest.main()
