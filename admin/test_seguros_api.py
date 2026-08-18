"""
test_seguros_api.py - El INTERRUPTOR CENTRAL del auto-envio de formularios de
seguro (panel -> Seguros -> Auto-envio).

Por que existe esta suite: el on/off vivia en el engranaje de CADA F2
(chrome.storage, por navegador), asi que apagarlo en un PC no apagaba los demas
y no habia forma de cortar todo de una. Ahora la extension siempre vigila y el
SERVIDOR decide. Si este gate se rompe, el auto-envio se reanudaria en SILENCIO
en toda la clinica -- por eso se fija con pruebas.

Cero red: DentiDesk deshabilitado y los stores en tempfiles (mismo patron que
test_link_agenda_api.py).

    cd admin && python test_seguros_api.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='seguros_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['SEGUROS_AUTO_CONFIG_PATH'] = str(_TMP / 'seguros_auto_config.json')
os.environ['SEGUROS_PACIENTES_PATH'] = str(_TMP / 'seguros_pacientes.json')
os.environ['SEGUROS_REGISTRO_PATH'] = str(_TMP / 'seguros_registro.json')
os.environ['SEGUROS_PRESTACIONES_PATH'] = str(_TMP / 'seguros_prestaciones.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import server    # noqa: E402
import seguros   # noqa: E402

RUT = '17.406.985-9'          # RUT sintetico, DV valido (repo publico)


class _Base(unittest.TestCase):
    """Cliente autenticado + los avisos a recepcion interceptados (cero correo)."""

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        self.avisos = []
        self._avisar_orig = server.notify.avisar_recepcion_seguro_no_enviado
        server.notify.avisar_recepcion_seguro_no_enviado = (
            lambda motivo, *a, **k: self.avisos.append(motivo))

    def tearDown(self):
        server.notify.avisar_recepcion_seguro_no_enviado = self._avisar_orig
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def _auto(self, **body):
        datos = {'rut': RUT, 'glosa': 'CONTROL MENSUAL DE ORTODONCIA',
                 'monto': 146000, 'folio': '999001'}
        datos.update(body)
        return self.client.post('/api/seguro/auto-desde-boleta', json=datos,
                                headers={'X-Admin-Token': 'token-de-prueba'})


class TestInterruptorCentral(_Base):

    def test_apagado_no_envia_y_no_avisa(self):
        seguros.set_auto_config(activo=False)
        r = self._auto()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get('auto_apagado'))
        # Apagado NO es un error: no se molesta a recepcion.
        self.assertEqual(self.avisos, [])

    def test_apagado_manda_sobre_cualquier_paciente(self):
        """Ni siquiera un paciente con aseguradora asignada se envia."""
        seguros.guardar_paciente_seguro(RUT, aseguradora='zurich')
        seguros.set_auto_config(activo=False)
        r = self._auto(folio='999002')
        self.assertTrue(r.get_json().get('auto_apagado'))
        self.assertEqual(self.avisos, [])

    def test_encendido_pasa_el_gate(self):
        """Encendido, sigue el flujo normal: este paciente esta 'sin asignar'
        (silencioso, sin aviso), lo que prueba que el gate lo dejo pasar."""
        seguros.guardar_paciente_seguro('11.111.111-1', aseguradora='')
        seguros.set_auto_config(activo=True)
        r = self._auto(rut='11.111.111-1', folio='999003')
        d = r.get_json()
        self.assertFalse(d.get('auto_apagado'))
        self.assertTrue(d.get('sin_asignar'))
        self.assertEqual(self.avisos, [])


class TestAutoConfigEndpoint(_Base):

    def _cfg(self, **body):
        h = {'X-Admin-Token': 'token-de-prueba'}
        if body:
            return self.client.post('/api/seguro/auto-config', json=body, headers=h)
        return self.client.get('/api/seguro/auto-config', headers=h)

    def test_guarda_y_devuelve_activo(self):
        self.assertTrue(self._cfg(activo=True).get_json()['activo'])
        self.assertTrue(self._cfg().get_json()['activo'])
        # y apagarlo de verdad apaga (False no debe tratarse como "no vino")
        self.assertFalse(self._cfg(activo=False).get_json()['activo'])
        self.assertFalse(self._cfg().get_json()['activo'])

    def test_exige_token(self):
        self.assertEqual(self.client.get('/api/seguro/auto-config').status_code, 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
