"""
test_rendimiento_agenda.py - Arreglo de rendimiento de la agenda online (2026-10).

Problema medido en produccion: el aviso "ya tienes una hora" (citas-futuras) hacia
~66 conexiones HTTPS nuevas en rafaga y congelaba TODO el servidor ~11 s. Lo que se
prueba aca:
  - citas_futuras_paciente lee la agenda por _get_agenda_day (cache) con max_age;
  - max_age sirve lo cacheado sin cambiar el TTL de nadie mas;
  - la poda no borra dias futuros recientes, si los pasados;
  - el endpoint cachea 60 s por RUT;
  - /api/salud (publico) y el registro de solicitudes lentas, sin query string;
  - las marcas diarias de los loops de KPIs sobreviven a un reinicio.

Cero red: DentiDesk deshabilitado y todo lo de red mockeado; archivos en tempdir.

    cd admin && python test_rendimiento_agenda.py
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='rendimiento_agenda_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['LINKS_AGENDA_PATH'] = str(_TMP / 'links_agenda.json')
os.environ['LOOPS_ESTADO_PATH'] = str(_TMP / 'loops_estado.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de DentiDesk.
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)   # que no arranquen los schedulers
sys.path.insert(0, str(Path(__file__).parent))

import server              # noqa: E402
import dentidesk           # noqa: E402
import fechas              # noqa: E402

if getattr(server, 'limiter', None):
    server.limiter.enabled = False   # esta suite hace >60 llamadas seguidas; no prueba el limite

# RUTs sinteticos, DV valido (ningun RUT real en este repo publico).
RUT_1 = '17.406.985-9'
RUT_2 = '12.345.678-5'

CFG_DD = {'dentidesk': {'enabled': True, 'base_url': 'https://ejemplo.invalido',
                        'id_location': 1}}


def _primer_dia_habil():
    d = fechas.hoy_chile()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _cita(rut, hora, estado='Confirmado', id_agenda='1', fecha=None):
    c = {'PatientDocument': rut, 'time': hora + ':00', 'Status': estado,
         'ProfessionalName': ' Alberto Del Real ', 'Reason': 'Control', 'IdAgenda': id_agenda}
    if fecha:
        c['Date'] = fecha
    return c


class TestCitasFuturasUsaCache(unittest.TestCase):

    def test_usa_get_agenda_day_con_max_age_y_misma_salida(self):
        dia = _primer_dia_habil()
        llamadas = []

        def falso(cfg, d, force=False, max_age=None):
            llamadas.append((d, force, max_age))
            if d != dia:
                return []
            return [
                _cita(RUT_1, '10:30', id_agenda='11'),
                _cita(RUT_1, '09:00', id_agenda='12', fecha=dia.isoformat()),
                _cita(RUT_1, '11:00', estado='Cancelado', id_agenda='13'),   # inactiva
                _cita(RUT_1, '12:00', estado='Atendido', id_agenda='14'),    # inactiva
                _cita(RUT_2, '10:00', id_agenda='15'),                       # otro RUT
            ]

        with mock.patch.object(dentidesk, '_get_agenda_day', side_effect=falso):
            citas = dentidesk.citas_futuras_paciente(RUT_1, CFG_DD)

        self.assertTrue(llamadas)
        self.assertTrue(all(m == dentidesk._CITAS_FUTURAS_MAX_AGE and not f
                            for _, f, m in llamadas))
        self.assertEqual(dentidesk._CITAS_FUTURAS_MAX_AGE, 1800)
        self.assertEqual([c['id_agenda'] for c in citas], ['12', '11'])   # ordenadas por hora
        self.assertEqual(citas[0], {
            'id_agenda': '12', 'fecha': dia.isoformat(), 'hora': '09:00',
            'profesional': 'Alberto Del Real', 'motivo': 'Control', 'estado': 'Confirmado'})
        # Sin 'Date' en la cita cae a la fecha del dia barrido.
        self.assertEqual(citas[1]['fecha'], dia.isoformat())

    def test_solo_dias_habiles_y_desde_hoy_de_chile(self):
        vistos = []
        with mock.patch.object(dentidesk, '_get_agenda_day',
                               side_effect=lambda c, d, **k: vistos.append(d) or []):
            dentidesk.citas_futuras_paciente(RUT_1, CFG_DD)
        self.assertTrue(vistos)
        self.assertTrue(all(d.weekday() < 5 for d in vistos))
        self.assertGreaterEqual(min(vistos), fechas.hoy_chile())

    def test_dia_que_falla_se_loguea_y_no_rompe(self):
        def falso(cfg, d, **k):
            raise RuntimeError('red caida')
        with mock.patch.object(dentidesk, '_get_agenda_day', side_effect=falso):
            with self.assertLogs(dentidesk.log, level='WARNING') as cm:
                self.assertEqual(dentidesk.citas_futuras_paciente(RUT_1, CFG_DD), [])
        self.assertTrue(any('citas_futuras_paciente' in m for m in cm.output))

    def test_dentidesk_apagado_no_consulta(self):
        cfg = {'dentidesk': {'enabled': False}}
        with mock.patch.object(dentidesk, '_get_agenda_day') as m:
            self.assertEqual(dentidesk.citas_futuras_paciente(RUT_1, cfg), [])
        m.assert_not_called()


class TestMaxAgeYPoda(unittest.TestCase):

    def setUp(self):
        dentidesk._AGENDA_DIA_CACHE.clear()
        self.addCleanup(dentidesk._AGENDA_DIA_CACHE.clear)
        self.hoy = fechas.hoy_chile()

    def _http_ok(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {'data': [{'IdAgenda': 'nuevo'}]}
        return resp

    def test_max_age_sirve_15_min_pero_el_ttl_normal_no(self):
        dia = self.hoy + timedelta(days=3)
        dentidesk._AGENDA_DIA_CACHE[dia.isoformat()] = (time.time() - 900, [{'IdAgenda': 'viejo'}])
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk._HTTP, 'post', return_value=self._http_ok()) as post:
            # Con max_age=1800 una entrada de 15 min sirve: cero llamadas a la red.
            self.assertEqual(dentidesk._get_agenda_day(CFG_DD, dia, max_age=1800),
                             [{'IdAgenda': 'viejo'}])
            post.assert_not_called()
            # Con el TTL normal (600 s) esa misma entrada ya vencio: va a la red.
            self.assertEqual(dentidesk._get_agenda_day(CFG_DD, dia),
                             [{'IdAgenda': 'nuevo'}])
            self.assertEqual(post.call_count, 1)

    def test_poda_conserva_futuro_reciente_y_borra_pasado(self):
        futuro = self.hoy + timedelta(days=5)
        pasado = self.hoy - timedelta(days=5)
        muy_viejo = self.hoy + timedelta(days=6)
        ahora = time.time()
        dentidesk._AGENDA_DIA_CACHE[futuro.isoformat()] = (ahora - 900, [])
        dentidesk._AGENDA_DIA_CACHE[pasado.isoformat()] = (ahora - 900, [])
        dentidesk._AGENDA_DIA_CACHE[muy_viejo.isoformat()] = (
            ahora - dentidesk._AGENDA_DIA_RETENCION - 5, [])
        otro = self.hoy + timedelta(days=9)
        with mock.patch.object(dentidesk, '_auth_token', return_value='tok'), \
             mock.patch.object(dentidesk._HTTP, 'post', return_value=self._http_ok()):
            dentidesk._get_agenda_day(CFG_DD, otro)
        cache = dentidesk._AGENDA_DIA_CACHE
        self.assertIn(futuro.isoformat(), cache, 'un dia futuro de 15 min no se poda')
        self.assertNotIn(pasado.isoformat(), cache, 'un dia pasado de 15 min si')
        self.assertNotIn(muy_viejo.isoformat(), cache, 'pasada la retencion, se va')
        self.assertIn(otro.isoformat(), cache)

    def test_sesion_http_compartida(self):
        import requests
        self.assertIsInstance(dentidesk._HTTP, requests.Session)
        self.assertEqual(dentidesk._AGENDA_DIA_RETENCION, 1800)


class TestEndpointCitasFuturas(unittest.TestCase):

    def setUp(self):
        self.client = server.app.test_client()
        with server._CITAS_FUT_LOCK:
            server._CITAS_FUT_CACHE.clear()
        self.addCleanup(server._CITAS_FUT_CACHE.clear)

    def test_segunda_llamada_sale_del_cache(self):
        citas = [{'id_agenda': '1', 'fecha': '2026-10-08', 'hora': '10:00',
                  'profesional': 'X', 'motivo': 'Control', 'estado': 'Confirmado'}]
        with mock.patch.object(server.dentidesk, 'citas_futuras_paciente',
                               return_value=citas) as m:
            r1 = self.client.get('/api/agenda/citas-futuras?rut=' + RUT_1)
            r2 = self.client.get('/api/agenda/citas-futuras?rut=' + RUT_1)
            # Mismo RUT escrito distinto = misma clave.
            r3 = self.client.get('/api/agenda/citas-futuras?rut=' + RUT_1.replace('.', ''))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.get_json(), {'ok': True, 'citas': citas})
        self.assertEqual(r2.get_json(), r1.get_json())
        self.assertEqual(r3.get_json(), r1.get_json())
        self.assertEqual(m.call_count, 1)

    def test_cache_vence_a_los_60_s(self):
        with mock.patch.object(server.dentidesk, 'citas_futuras_paciente',
                               return_value=[]) as m:
            self.client.get('/api/agenda/citas-futuras?rut=' + RUT_2)
            k = server.scheduling.limpiar_rut(RUT_2)
            t, c = server._CITAS_FUT_CACHE[k]
            server._CITAS_FUT_CACHE[k] = (t - 61, c)
            self.client.get('/api/agenda/citas-futuras?rut=' + RUT_2)
        self.assertEqual(m.call_count, 2)

    def test_una_falla_no_se_cachea(self):
        with mock.patch.object(server.dentidesk, 'citas_futuras_paciente',
                               side_effect=RuntimeError('boom')):
            r = self.client.get('/api/agenda/citas-futuras?rut=' + RUT_1)
            self.assertEqual(r.get_json(), {'ok': True, 'citas': []})
        self.assertEqual(server._CITAS_FUT_CACHE, {})
        with mock.patch.object(server.dentidesk, 'citas_futuras_paciente',
                               return_value=[]) as m2:
            self.client.get('/api/agenda/citas-futuras?rut=' + RUT_1)
        self.assertEqual(m2.call_count, 1)

    def test_rut_invalido_sigue_dando_400(self):
        r = self.client.get('/api/agenda/citas-futuras?rut=1-1')
        self.assertEqual(r.status_code, 400)


class TestSaludYLentas(unittest.TestCase):

    def setUp(self):
        self.client = server.app.test_client()
        server._LENTAS.clear()
        self.addCleanup(server._LENTAS.clear)

    def test_salud_publica_sin_token(self):
        with mock.patch.dict(os.environ, {'ADMIN_TOKEN': 'token-de-prueba'}):
            r = self.client.get('/api/salud')
        self.assertEqual(r.status_code, 200)
        d = r.get_json()
        self.assertEqual(set(d), {'ok', 'inicio', 'uptime_s', 'version',
                                  'lentas_recientes', 'ultimas_lentas'})
        self.assertTrue(d['ok'])
        self.assertIsInstance(d['uptime_s'], int)
        datetime.fromisoformat(d['inicio'])
        self.assertEqual(d['lentas_recientes'], 0)
        self.assertEqual(d['ultimas_lentas'], [])

    def test_version_sale_de_render_git_commit(self):
        with mock.patch.dict(os.environ, {'RENDER_GIT_COMMIT': 'abcdef1234567890'}):
            self.assertEqual(self.client.get('/api/salud').get_json()['version'], 'abcdef1')

    def test_solicitud_lenta_queda_registrada_sin_query_string(self):
        with mock.patch.object(server, '_LENTA_UMBRAL_S', -1.0):
            with self.assertLogs(server.app.logger, level='WARNING') as cm:
                self.client.get('/api/salud?rut=' + RUT_1)
        self.assertEqual(len(server._LENTAS), 1)
        e = server._LENTAS[0]
        self.assertEqual(set(e), {'hora', 'metodo', 'path', 'status', 'seg'})
        self.assertEqual((e['metodo'], e['path'], e['status']), ('GET', '/api/salud', 200))
        datetime.fromisoformat(e['hora'])
        visto = json.dumps(list(server._LENTAS)) + ' '.join(cm.output)
        self.assertNotIn('rut=', visto)
        self.assertNotIn('17.406', visto)
        self.assertNotIn('?', visto)
        self.assertTrue(any('lento: GET /api/salud' in m for m in cm.output))

    def test_solicitud_rapida_no_se_registra(self):
        self.client.get('/api/salud')
        self.assertEqual(len(server._LENTAS), 0)

    def test_deque_guarda_solo_las_ultimas_30(self):
        with mock.patch.object(server, '_LENTA_UMBRAL_S', -1.0):
            with self.assertLogs(server.app.logger, level='WARNING'):
                for _ in range(35):
                    self.client.get('/api/salud')
        self.assertEqual(len(server._LENTAS), 30)
        d = self.client.get('/api/salud').get_json()
        self.assertEqual(d['lentas_recientes'], 30)
        self.assertEqual(len(d['ultimas_lentas']), 5)


class _FinDelLoop(Exception):
    pass


class TestMarcasDeLoops(unittest.TestCase):

    def setUp(self):
        self.ruta = Path(os.environ['LOOPS_ESTADO_PATH'])
        if self.ruta.exists():
            self.ruta.unlink()
        self.addCleanup(lambda: self.ruta.exists() and self.ruta.unlink())

    def test_marcas_se_persisten_y_se_releen(self):
        self.assertIsNone(server._loop_marca_leer('kpi_cosecha'))
        server._loop_marca_escribir('kpi_cosecha', '2026-10-07')
        server._loop_marca_escribir('clinico_proyeccion', '2026-10-06')
        self.assertEqual(server._loop_marca_leer('kpi_cosecha'), '2026-10-07')
        self.assertEqual(server._loop_marca_leer('clinico_proyeccion'), '2026-10-06')
        # Quedaron en disco, con la estructura pedida.
        self.assertEqual(json.loads(self.ruta.read_text(encoding='utf-8')),
                         {'kpi_cosecha': '2026-10-07', 'clinico_proyeccion': '2026-10-06'})

    def test_ruta_por_defecto_junto_a_patient_index(self):
        with mock.patch.dict(os.environ):
            os.environ.pop('LOOPS_ESTADO_PATH')
            ruta = server._loops_estado_store().path
        self.assertEqual(ruta.name, 'loops_estado.json')
        self.assertEqual(ruta.parent, Path(os.environ['PATIENT_INDEX_PATH']).parent)

    def _correr_una_vuelta(self):
        """Una vuelta del loop a las 10:00 con DentiDesk 'habilitado' y todo mockeado."""
        ahora = datetime(2026, 10, 7, 10, 0)
        cfg = {'dentidesk': {'enabled': True}}
        with mock.patch.object(server.fechas, 'ahora_chile_aware', return_value=ahora), \
             mock.patch.object(server.scheduling, 'load_config', return_value=cfg), \
             mock.patch.object(server.kpi, 'cosechar', return_value={}) as cos, \
             mock.patch.object(server.kpi, 'capturar_disponibilidad', return_value={}), \
             mock.patch.object(server.clinico, 'proyectar_todo', return_value={}) as proy, \
             mock.patch('perfil.guardar_snapshot', return_value={}) as snap, \
             mock.patch('time.sleep', side_effect=_FinDelLoop):
            with self.assertRaises(_FinDelLoop):
                server._loop_kpi_cosecha()
        return {'cosechar': cos, 'proyectar': proy, 'snapshot': snap}

    def test_loop_corre_una_vez_y_un_reinicio_no_lo_repite(self):
        m = self._correr_una_vuelta()                      # primer arranque del dia
        m['cosechar'].assert_called_once()
        m['proyectar'].assert_called_once()
        m['snapshot'].assert_called_once()
        self.assertEqual(server._loop_marca_leer('kpi_cosecha'), '2026-10-07')
        self.assertEqual(server._loop_marca_leer('clinico_proyeccion'), '2026-10-07')

        m = self._correr_una_vuelta()                      # "reinicio" el mismo dia
        m['cosechar'].assert_not_called()
        m['proyectar'].assert_not_called()
        m['snapshot'].assert_not_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
