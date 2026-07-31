"""
test_reporte_semanal.py - Reporte semanal de KPIs de negocio.

Cero red: mockea DentiDesk, stats, nps, compras, seguimiento_pc y reactivacion.
'hoy' se pasa explicito donde aplica.

    cd admin && python test_reporte_semanal.py
"""

import os
import sys
import tempfile
import unittest
import contextlib
from pathlib import Path
from datetime import date
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='reporte_semanal_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import reporte_semanal as rs  # noqa: E402


def _cita(rut, fecha, reason='Primera Consulta', status='Atendido',
          doctor='Alberto Del Real', nombre='Juan Perez', telefono='987654321',
          id_agenda=None):
    return {
        'IdAgenda': id_agenda or f'{rut}-{fecha}',
        'PatientDocument': rut,
        'PatientName': nombre,
        'ProfessionalName': doctor,
        'Reason': reason,
        'Status': status,
        'Date': fecha,
        'Phone': telefono,
    }


class TestVentanaSemanaAnterior(unittest.TestCase):
    def test_lunes_a_domingo_semana_anterior(self):
        # Lunes 2026-08-03 -> semana anterior: lunes 2026-07-27 a domingo 2026-08-02.
        hoy = date(2026, 8, 3)
        desde, hasta = rs.ventana_semana_anterior(hoy)
        self.assertEqual(desde, date(2026, 7, 27))
        self.assertEqual(hasta, date(2026, 8, 2))
        self.assertEqual(desde.weekday(), 0)   # lunes
        self.assertEqual(hasta.weekday(), 6)   # domingo

    def test_a_mitad_de_semana(self):
        # Jueves 2026-07-30 -> semana actual empieza el lunes 2026-07-27;
        # la ANTERIOR es 2026-07-20 a 2026-07-26.
        hoy = date(2026, 7, 30)
        desde, hasta = rs.ventana_semana_anterior(hoy)
        self.assertEqual(desde, date(2026, 7, 20))
        self.assertEqual(hasta, date(2026, 7, 26))

    def test_usa_hoy_chile_por_defecto(self):
        with mock.patch('reporte_semanal.fechas.hoy_chile', return_value=date(2026, 8, 3)):
            desde, hasta = rs.ventana_semana_anterior()
        self.assertEqual(desde, date(2026, 7, 27))
        self.assertEqual(hasta, date(2026, 8, 2))


class TestBarridoClinico(unittest.TestCase):
    def _citas_por_dia(self, d):
        d_iso = d.isoformat()
        mapa = {
            # Lunes: atendido normal + primera consulta atendida + no-show
            date(2026, 7, 27).isoformat(): [
                _cita('11111111-1', d_iso, reason='Control Fijo', status='Atendido'),
                _cita('22222222-2', d_iso, reason='Primera Consulta', status='Atendido'),
                _cita('33333333-3', d_iso, reason='Control Fijo', status='Paciente no llega'),
            ],
            # Martes: cancelacion + inicio de tratamiento + alta
            date(2026, 7, 28).isoformat(): [
                _cita('44444444-4', d_iso, reason='Montaje Total', status='Cancelado'),
                _cita('55555555-5', d_iso, reason='Montaje Total', status='Atendido'),
                _cita('66666666-6', d_iso, reason='Retiro Total', status='Atendido'),
            ],
        }
        return mapa.get(d.isoformat(), [])

    def test_cuenta_atendidos_no_shows_inicios_altas(self):
        with mock.patch('reporte_semanal.dentidesk._get_agenda_day',
                        side_effect=lambda scfg, d: self._citas_por_dia(d)):
            res = rs._barrido_clinico(date(2026, 7, 27), date(2026, 8, 2), scfg={})

        # Lunes: 2 atendidos (control fijo + primera consulta) + 1 no-show.
        # Martes: 1 atendido (montaje) + 1 atendido (retiro) + 1 cancelacion.
        self.assertEqual(res['atendidos'], 4)
        self.assertEqual(res['no_shows'], 1)
        self.assertEqual(res['cancelaciones'], 1)
        self.assertEqual(res['primeras_consultas'], 1)
        self.assertEqual(res['inicios'], 1)   # solo el montaje ATENDIDO (el cancelado no cuenta)
        self.assertEqual(res['altas'], 1)     # retiro total atendido
        self.assertEqual(res['dias_habiles'], 5)  # lu-vi de la semana 27jul-2ago

    def test_un_dia_que_falla_no_rompe_el_resto(self):
        def scan(scfg, d):
            if d == date(2026, 7, 27):
                raise RuntimeError('DentiDesk caido')
            return self._citas_por_dia(d)

        with mock.patch('reporte_semanal.dentidesk._get_agenda_day', side_effect=scan):
            res = rs._barrido_clinico(date(2026, 7, 27), date(2026, 8, 2), scfg={})

        # El martes (que si funciona) sigue contando: 2 atendidos, 1 cancelacion, 1 inicio, 1 alta.
        self.assertEqual(res['atendidos'], 2)
        self.assertEqual(res['cancelaciones'], 1)
        self.assertEqual(res['inicios'], 1)
        self.assertEqual(res['altas'], 1)
        self.assertEqual(res['dias_habiles'], 5)


class TestAgregar(unittest.TestCase):
    def _mocks_ok(self):
        return {
            'reporte_semanal.stats.resumen':
                mock.Mock(return_value={'total': 12, 'nuevos': 5, 'conocidos': 7,
                                        'por_doctor': [{'label': 'Alberto', 'total': 12}]}),
            'reporte_semanal.stats.resumen_funnel':
                mock.Mock(return_value={'total_sesiones': 40, 'reservaron': 12, 'conversion_pct': 30}),
            'reporte_semanal.seguimiento_pc.resumen':
                mock.Mock(return_value={'total': 10, 'pendientes': 4, 'convertidos': 5,
                                        'completados': 1, 'no_molestar': 0}),
            'reporte_semanal._barrido_clinico':
                mock.Mock(return_value={'atendidos': 20, 'no_shows': 2, 'cancelaciones': 1,
                                        'primeras_consultas': 3, 'inicios': 2, 'altas': 1,
                                        'dias_habiles': 5}),
            'reporte_semanal.nps.resumen':
                mock.Mock(return_value={'nps': 55, 'promotores': 10, 'pasivos': 3, 'detractores': 2,
                                        'tasa_respuesta': 0.5, 'resenas_mes': {'2026-07': {'resenas': 4, 'rating': 4.8}},
                                        'rating_reciente': 4.8}),
            'reporte_semanal._contar_seguros_enviados': mock.Mock(return_value=3),
            'reporte_semanal.compras.resumen_gastos':
                mock.Mock(return_value={'total': 150000, 'n_compras': 4, 'por_mes': [],
                                        'por_categoria': [], 'por_proveedor': []}),
            'reporte_semanal.reactivacion.resumen':
                mock.Mock(return_value={'total': 8, 'pendientes': 3, 'volvio': 4, 'completado': 1,
                                        'no_molestar': 0, 'ultimo_barrido': {}}),
        }

    def test_devuelve_las_4_areas(self):
        parches = self._mocks_ok()
        with contextlib.ExitStack() as stack:
            for target, m in parches.items():
                stack.enter_context(mock.patch(target, m))
            kpis = rs.agregar(date(2026, 7, 27), date(2026, 8, 2))

        self.assertEqual(kpis['desde'], '2026-07-27')
        self.assertEqual(kpis['hasta'], '2026-08-02')
        for area in ('comercial', 'clinico', 'reputacion', 'operacion', 'reactivacion'):
            self.assertIn(area, kpis)
            self.assertNotIn('error', kpis[area])
        self.assertEqual(kpis['comercial']['reservas_online'], 12)
        self.assertEqual(kpis['comercial']['fuga_primeras_consultas'], 4)
        self.assertEqual(kpis['clinico']['atendidos'], 20)
        self.assertEqual(kpis['reputacion']['nps'], 55)
        self.assertEqual(kpis['operacion']['seguros_enviados_periodo'], 3)
        self.assertEqual(kpis['operacion']['gastos']['total'], 150000)
        self.assertEqual(kpis['reactivacion']['pendientes'], 3)

    def test_una_fuente_que_falla_no_rompe_agregar(self):
        parches = self._mocks_ok()
        parches['reporte_semanal.nps.resumen'] = mock.Mock(side_effect=Exception('nps caido'))
        with contextlib.ExitStack() as stack:
            for target, m in parches.items():
                stack.enter_context(mock.patch(target, m))
            # No debe lanzar aunque nps.resumen() reviente.
            kpis = rs.agregar(date(2026, 7, 27), date(2026, 8, 2))

        self.assertTrue(kpis['reputacion'].get('error'))
        # El resto de las areas sigue sano.
        self.assertNotIn('error', kpis['comercial'])
        self.assertNotIn('error', kpis['clinico'])
        self.assertNotIn('error', kpis['operacion'])
        self.assertNotIn('error', kpis['reactivacion'])

    def test_todas_las_fuentes_fallando_no_rompe_agregar(self):
        with contextlib.ExitStack() as stack:
            for target in self._mocks_ok():
                stack.enter_context(mock.patch(target, mock.Mock(side_effect=Exception('boom'))))
            kpis = rs.agregar(date(2026, 7, 27), date(2026, 8, 2))

        self.assertEqual(kpis['desde'], '2026-07-27')
        for area in ('comercial', 'clinico', 'reputacion', 'reactivacion'):
            self.assertTrue(kpis[area].get('error'))
        # seguros_enviados_periodo cae a 0 (defensivo), gastos queda en error,
        # pero 'operacion' en si no se marca error (es un dict compuesto).
        self.assertEqual(kpis['operacion']['seguros_enviados_periodo'], 0)
        self.assertTrue(kpis['operacion']['gastos'].get('error'))


class TestRenderHtml(unittest.TestCase):
    def test_render_devuelve_html_no_vacio_con_numeros(self):
        kpis = {
            'desde': '2026-07-27', 'hasta': '2026-08-02',
            'comercial': {'reservas_online': 12, 'nuevos': 5, 'conocidos': 7,
                          'por_doctor': [{'label': 'Alberto', 'total': 12}],
                          'conversion': {'total_sesiones': 40, 'reservaron': 12, 'conversion_pct': 30},
                          'fuga_primeras_consultas': 4},
            'clinico': {'atendidos': 20, 'no_shows': 2, 'cancelaciones': 1,
                       'primeras_consultas': 3, 'inicios': 2, 'altas': 1, 'dias_habiles': 5},
            'reputacion': {'nps': 55, 'promotores': 10, 'pasivos': 3, 'detractores': 2,
                          'tasa_respuesta': 0.5, 'resenas_mes': {}, 'rating': 4.8},
            'operacion': {'seguros_enviados_periodo': 3,
                         'gastos': {'total': 150000, 'n_compras': 4}},
            'reactivacion': {'total': 8, 'pendientes': 3, 'volvio': 4, 'completado': 1},
        }
        html = rs.render_html(kpis)
        self.assertIsInstance(html, str)
        self.assertGreater(len(html), 0)
        self.assertIn('Reporte semanal', html)
        self.assertIn('20', html)          # atendidos
        self.assertIn('150.000', html)     # gasto formateado con separador de miles

    def test_render_no_revienta_con_bloques_en_error(self):
        kpis = {
            'desde': '2026-07-27', 'hasta': '2026-08-02',
            'comercial': {'error': True}, 'clinico': {'error': True},
            'reputacion': {'error': True},
            'operacion': {'seguros_enviados_periodo': 0, 'gastos': {'error': True}},
            'reactivacion': {'error': True},
        }
        html = rs.render_html(kpis)
        self.assertIn('Reporte semanal', html)

    def test_asunto_formatea_fechas_dd_mm(self):
        kpis = {'desde': '2026-07-27', 'hasta': '2026-08-02'}
        self.assertEqual(rs.asunto(kpis), 'Reporte semanal — 27-07 al 02-08')


if __name__ == '__main__':
    unittest.main(verbosity=2)
