"""
test_reagenda_pendientes.py - El aviso a recepcion de "quiere reagendar" espera
unos minutos antes de salir, y NO sale si el paciente ya resolvio solo.

El correo se mandaba en el mismo instante en que el paciente tocaba el boton,
pero la mayoria elige su hora nueva en el minuto siguiente con el link que
recibe: ese aviso llegaba igual y llenaba la bandeja de recepcion. Estas
pruebas fijan las dos mitades del arreglo: la espera (modulo) y la decision
final (barrido en server.py).

Cero red: DentiDesk deshabilitado y stores en tempfiles.

    cd admin && python test_reagenda_pendientes.py
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='reagenda_pend_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['REAGENDA_PENDIENTES_PATH'] = str(_TMP / 'reagenda_pendientes.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import reagenda_pendientes as rp   # noqa: E402
import server                      # noqa: E402

RUT = '17.406.985-9'   # sintetico, DV valido modulo 11
CFG = {'dentidesk': {'enabled': True}}


class _Base(unittest.TestCase):
    def setUp(self):
        rp._save({'pendientes': {}})


class TestEspera(_Base):

    def test_recien_anotado_no_esta_vencido(self):
        rp.registrar('100', '56900000000', 'Ana Prueba', '2026-08-06', RUT)
        self.assertEqual(rp.vencidos(), [], 'no puede avisarse antes de la espera')

    def test_vence_pasados_los_minutos(self):
        rp.registrar('100', '56900000000', 'Ana Prueba', '2026-08-06', RUT)
        futuro = datetime.now() + timedelta(minutes=rp.MINUTOS_ESPERA + 1)
        self.assertEqual([ida for ida, _ in rp.vencidos(ahora=futuro)], ['100'])

    def test_tocar_dos_veces_no_duplica_el_aviso(self):
        rp.registrar('100', '56900000000', 'Ana', '2026-08-06', RUT)
        rp.registrar('100', '56900000000', 'Ana', '2026-08-06', RUT)
        self.assertEqual(len(rp.listar()), 1)

    def test_sin_marca_de_tiempo_se_trata_como_vencido(self):
        """Mejor un aviso de mas que perder al paciente por un dato corrupto."""
        rp._save({'pendientes': {'100': {'telefono': '569', 'pedido': ''}}})
        self.assertEqual([ida for ida, _ in rp.vencidos()], ['100'])

    def test_resolver_lo_saca_de_la_lista(self):
        rp.registrar('100', '56900000000', 'Ana', '2026-08-06', RUT)
        rp.resolver('100', 'ya_agendo')
        self.assertEqual(rp.listar(), {})

    def test_sobrevive_al_reinicio(self):
        """El pendiente vive en disco, no en memoria: si Render reinicia justo
        despues del toque, recepcion igual se entera."""
        rp.registrar('100', '56900000000', 'Ana', '2026-08-06', RUT)
        rp._STORE._cache = None   # simula proceso nuevo leyendo del archivo
        self.assertIn('100', rp.listar())

    def test_poda_descarta_los_muy_viejos(self):
        rp.registrar('100', '56900000000', 'Ana', '2026-08-06', RUT)
        rp.podar(ahora=datetime.now() + timedelta(days=rp._DIAS_RETENCION + 1))
        self.assertEqual(rp.listar(), {})


class TestDecision(_Base):
    """_procesar_reagenda_pendientes: a quien se le avisa y a quien no."""

    def setUp(self):
        super().setUp()
        rp.registrar('100', '56900000000', 'Ana Prueba', '2026-08-06', RUT)
        self.ahora = datetime.now() + timedelta(minutes=rp.MINUTOS_ESPERA + 1)
        self.avisar = mock.patch.object(server.notify, 'avisar_recepcion_quiere_reagendar').start()
        self.citas = mock.patch.object(server.dentidesk, 'citas_futuras_paciente').start()
        self.citas.return_value = []
        self.addCleanup(mock.patch.stopall)

    def test_si_no_agendo_sale_el_correo(self):
        r = server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.avisar.assert_called_once()
        self.assertEqual(r['avisados'], 1)
        self.assertEqual(rp.listar(), {}, 'el pendiente se cierra tras avisar')

    def test_si_ya_agendo_NO_sale_el_correo(self):
        """El caso que motivo todo: el paciente eligio su hora con el link."""
        self.citas.return_value = [{'id_agenda': '999', 'fecha': '2026-08-20'}]
        r = server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.avisar.assert_not_called()
        self.assertEqual(r['ya_agendaron'], 1)
        self.assertEqual(rp.listar(), {})

    def test_la_cita_original_no_cuenta_como_hora_nueva(self):
        """La cita vieja sigue VIGENTE en 'Pidio cambiar su hora', asi que si es
        futura aparece en la busqueda. Confundirla con una hora nueva dejaria a
        recepcion sin avisar justo cuando hay que avisar."""
        self.citas.return_value = [{'id_agenda': '100', 'fecha': '2026-08-06'}]
        r = server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.avisar.assert_called_once()
        self.assertEqual(r['avisados'], 1)

    def test_sin_rut_se_avisa_igual(self):
        """Sin RUT no hay como comprobar si agendo: se avisa (no se asume)."""
        rp._save({'pendientes': {}})
        rp.registrar('101', '56900000000', 'Sin Rut', '2026-08-06', '')
        server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.avisar.assert_called_once()

    def test_no_toca_los_que_aun_no_vencen(self):
        r = server._procesar_reagenda_pendientes(CFG)   # ahora = de verdad
        self.avisar.assert_not_called()
        self.assertEqual(r['vencidos'], 0)
        self.assertIn('100', rp.listar(), 'sigue esperando su turno')

    def test_si_falla_la_consulta_el_pendiente_no_se_pierde(self):
        self.citas.side_effect = RuntimeError('DentiDesk caido')
        r = server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.assertEqual(r['errores'], 1)
        self.avisar.assert_not_called()
        self.assertIn('100', rp.listar(), 'se reintenta en el proximo ciclo')

    def test_si_falla_el_correo_el_pendiente_no_se_pierde(self):
        self.avisar.side_effect = RuntimeError('SMTP caido')
        r = server._procesar_reagenda_pendientes(CFG, ahora=self.ahora)
        self.assertEqual(r['errores'], 1)
        self.assertIn('100', rp.listar())


class TestEndpoints(unittest.TestCase):

    def setUp(self):
        rp._save({'pendientes': {}})
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        self.headers = {'X-Admin-Token': 'token-de-prueba'}

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def test_listar_exige_token(self):
        self.assertEqual(self.client.get('/api/reagenda-pendientes').status_code, 403)

    def test_run_exige_token(self):
        self.assertEqual(self.client.post('/api/reagenda-pendientes/run').status_code, 403)

    def test_listar_no_expone_telefono_ni_rut(self):
        rp.registrar('100', '56988887777', 'Ana Prueba', '2026-08-06', RUT)
        crudo = self.client.get('/api/reagenda-pendientes', headers=self.headers).get_data(as_text=True)
        self.assertIn('Ana Prueba', crudo)
        for sensible in ('56988887777', RUT, '17406985'):
            self.assertNotIn(sensible, crudo)


if __name__ == '__main__':
    unittest.main(verbosity=2)
