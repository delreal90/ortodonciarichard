"""
test_paciente_estado.py - Clasificacion de estado clinico y el menu filtrado
que ve el paciente en la agenda online.

Cero red: DentiDesk deshabilitado (DENTIDESK_ENABLED=false) y el barrido se
prueba monkeypatcheando paciente_estado.dentidesk._get_agenda_day (mismo
patron que test_seguimiento_pc.py / test_reporte_semanal.py).

    cd admin && python test_paciente_estado.py

Un fallo aca es un paciente al que se le esconde un motivo que si le
corresponde (o al reves: se le ofrece un Estudio Integral que el backend le
va a rechazar con 403).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='pestado_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fechas                       # noqa: E402
import control_dental               # noqa: E402
import paciente_estado as pe        # noqa: E402

RUT_1 = '12.345.678-5'   # sintetico, DV valido modulo 11
RUT_2 = '17.406.985-9'   # sintetico, DV valido modulo 11

# cfg minimo de scheduling que necesitan clasificar()/estado_por_motivo(): los
# motivos que existen de verdad y (cuando el test lo pida) overrides.
CFG_BASE = {
    'motivos': {
        'primera_consulta': {}, 'urgencia': {}, 'estudio_integral': {},
        'control_evolucion': {}, 'control_fijo': {}, 'control_alineadores': {},
        'control_removible': {}, 'control_pasivo': {},
    },
    'meses_vigencia_estado': 14,
    'estado_motivos_extra': {},
}


def _cita(rut, fecha, reason='Montaje Total', status='Atendido',
          doctor='Alberto Del Real', nombre='Juan Perez', id_agenda=None):
    return {
        'IdAgenda': id_agenda or f'{rut}-{fecha}-{reason}',
        'PatientDocument': rut,
        'PatientName': nombre,
        'ProfessionalName': doctor,
        'Reason': reason,
        'Status': status,
        'Date': fecha,
    }


def _rut_plano(rut):
    return rut.replace('.', '').replace('-', '')


def _limpiar():
    pe._save_estado({'ultimo_barrido': '', 'pacientes': {}, 'motivos_desconocidos': {}})


class TestEstadoPorMotivo(unittest.TestCase):
    """Una categoria por cada estado + un motivo que no cambia nada."""

    def test_montaje_total_es_fijo(self):
        self.assertEqual(pe.estado_por_motivo('Montaje Total'), 'fijo')

    def test_control_fijo_tambien_es_fijo(self):
        """A diferencia de control_dental (donde Control Fijo solo es señal de
        vida), aca SI confirma el estado -- el paciente sigue en fijo."""
        self.assertEqual(pe.estado_por_motivo('Control Fijo'), 'fijo')

    def test_instalar_digitrack_es_alineadores(self):
        self.assertEqual(pe.estado_por_motivo('Instalar Digitrack'), 'alineadores')

    def test_refinamiento_tambien_es_alineadores(self):
        """El refinamiento NO inscribe en control_dental, pero aca SI confirma
        que el paciente sigue con alineadores."""
        self.assertEqual(pe.estado_por_motivo('Instalar Refinamiento Invisalign'),
                         'alineadores')

    def test_control_removible_es_removible(self):
        self.assertEqual(pe.estado_por_motivo('Control Removible'), 'removible')

    def test_placa_es_removible(self):
        self.assertEqual(pe.estado_por_motivo('Placa'), 'removible')

    def test_retiro_total_es_pasivo(self):
        self.assertEqual(pe.estado_por_motivo('Retiro Total'), 'pasivo')

    def test_control_pasivo_es_pasivo(self):
        self.assertEqual(pe.estado_por_motivo('Control Pasivo'), 'pasivo')

    def test_primera_consulta_es_primera_consulta(self):
        self.assertEqual(pe.estado_por_motivo('Primera Consulta'), 'primera_consulta')

    def test_inicio_es_primera_consulta(self):
        self.assertEqual(pe.estado_por_motivo('Inicio'), 'primera_consulta')

    def test_explicacion_plan_instalacion_digitrack_es_alineadores_no_primera_consulta(self):
        """OJO documentado en el modulo: ya es una instalacion, no una primera
        consulta, aunque el nombre empiece igual que 'Explicación Plan...'."""
        self.assertEqual(
            pe.estado_por_motivo('Explicación Plan + Instalación Digitrack'),
            'alineadores')

    def test_urgencia_no_clasifica(self):
        self.assertIsNone(pe.estado_por_motivo('Urgencia de Ortodoncia'))

    def test_motivo_vacio_o_none_no_clasifica(self):
        self.assertIsNone(pe.estado_por_motivo(''))
        self.assertIsNone(pe.estado_por_motivo(None))

    def test_sin_tildes_y_sin_mayusculas(self):
        for txt in ('montaje total', 'MONTAJE TOTAL', '  Montaje   Total  '):
            with self.subTest(txt=txt):
                self.assertEqual(pe.estado_por_motivo(txt), 'fijo')

    def test_estado_motivos_extra_clasifica_un_motivo_inventado(self):
        """El panel resuelve un motivo nuevo/ambiguo sin deploy."""
        cfg = {'estado_motivos_extra': {'cita rarisima nueva': 'removible'}}
        self.assertEqual(pe.estado_por_motivo('Cita Rarisima Nueva', cfg), 'removible')

    def test_estado_motivos_extra_manda_sobre_las_constantes(self):
        cfg = {'estado_motivos_extra': {'montaje total': 'alineadores'}}
        self.assertEqual(pe.estado_por_motivo('Montaje Total', cfg), 'alineadores')


class TestRegistrarCitaAtendida(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_primera_cita_registra_estado(self):
        pe.registrar_cita_atendida(RUT_1, '2026-03-01', 'Montaje Total', cfg={})
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'fijo')
        self.assertEqual(r['fuente'], 'barrido')
        self.assertEqual(r['ultima_cita'], '2026-03-01')
        self.assertEqual(r['ultimo_motivo'], 'Montaje Total')
        self.assertFalse(r['bloqueo_manual'])

    def test_cita_mas_vieja_no_pisa_la_mas_nueva(self):
        """Precedencia fecha-mas-nueva: la ultima cita registrada manda."""
        pe.registrar_cita_atendida(RUT_1, '2026-05-01', 'Retiro Total', cfg={})
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'pasivo', 'la cita vieja no debe pisar la nueva')
        self.assertEqual(r['ultima_cita'], '2026-05-01')

    def test_cita_mas_nueva_si_pisa(self):
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        pe.registrar_cita_atendida(RUT_1, '2026-05-01', 'Retiro Total', cfg={})
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'pasivo')
        self.assertEqual(r['ultima_cita'], '2026-05-01')

    def test_motivo_que_no_clasifica_solo_actualiza_senal_de_vida(self):
        """Una Urgencia no debe borrar el estado 'fijo' ya conocido."""
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        pe.registrar_cita_atendida(RUT_1, '2026-02-01', 'Urgencia de Ortodoncia', cfg={})
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'fijo', 'la urgencia no debe tocar el estado')
        self.assertEqual(r['ultima_cita'], '2026-02-01')
        self.assertEqual(r['ultimo_motivo'], 'Urgencia de Ortodoncia')


class TestPrecedenciaManualVsBarrido(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_override_manual_pierde_ante_cita_posterior(self):
        """La regla central: bloqueo_manual pierde ante una cita real mas
        nueva -- la realidad manda."""
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        pe.set_manual(RUT_1, 'pasivo')
        self.assertEqual(pe.get(RUT_1)['estado'], 'pasivo')
        self.assertTrue(pe.get(RUT_1)['bloqueo_manual'])

        pe.registrar_cita_atendida(RUT_1, '2026-06-01', 'Instalar Digitrack', cfg={})
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'alineadores', 'la cita posterior debe ganar')

    def test_registrar_reserva_online_respeta_bloqueo_manual(self):
        """A diferencia del barrido, la reserva online SI respeta el override
        manual -- no lo pisa."""
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        pe.set_manual(RUT_1, 'pasivo')
        pe.registrar_reserva_online(RUT_1, 'control_fijo')
        self.assertEqual(pe.get(RUT_1)['estado'], 'pasivo',
                          'la reserva online no debe pisar un bloqueo_manual')

    def test_registrar_reserva_online_sin_bloqueo_si_actualiza(self):
        pe.registrar_reserva_online(RUT_1, 'control_alineadores')
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'alineadores')
        self.assertEqual(r['fuente'], 'reserva_online')

    def test_reserva_de_primera_consulta_o_urgencia_no_cambia_estado(self):
        self.assertIsNone(pe.registrar_reserva_online(RUT_1, 'primera_consulta'))
        self.assertIsNone(pe.get(RUT_1))
        self.assertIsNone(pe.registrar_reserva_online(RUT_1, 'urgencia'))
        self.assertIsNone(pe.get(RUT_1))

    def test_set_manual_limpia_el_override_sin_borrar_el_historico(self):
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        pe.set_manual(RUT_1, 'pasivo')
        pe.set_manual(RUT_1, '')
        r = pe.get(RUT_1)
        self.assertFalse(r['bloqueo_manual'])
        self.assertEqual(r['ultima_cita'], '2026-01-01', 'el historico no se borra')

    def test_quitar_el_override_recalcula_desde_la_ultima_cita(self):
        # Bajar bloqueo_manual no basta: si el estado que escribio la asistente
        # se queda pegado, un paciente sin citas nuevas sigue viendo el menu
        # equivocado para siempre (el barrido solo pisa con una cita MAS NUEVA).
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Montaje Total', cfg={})
        self.assertEqual(pe.get(RUT_1)['estado'], 'fijo')
        pe.set_manual(RUT_1, 'pasivo')
        self.assertEqual(pe.get(RUT_1)['estado'], 'pasivo')
        pe.set_manual(RUT_1, '')
        r = pe.get(RUT_1)
        self.assertEqual(r['estado'], 'fijo', 'vuelve a lo que dice su ultima cita real')
        self.assertEqual(r['fuente'], 'barrido')

    def test_quitar_el_override_sin_motivo_clasificable_deja_menu_completo(self):
        pe.registrar_cita_atendida(RUT_1, '2026-01-01', 'Motivo Que No Existe', cfg={})
        pe.set_manual(RUT_1, 'alineadores')
        pe.set_manual(RUT_1, '')
        self.assertEqual(pe.get(RUT_1)['estado'], 'desconocido')
        self.assertIsNone(pe.clasificar(RUT_1)['motivos_permitidos'])

    def test_set_manual_valida_estados_conocidos(self):
        with self.assertRaises(ValueError):
            pe.set_manual(RUT_1, 'estado_que_no_existe')


class TestClasificar(unittest.TestCase):

    def setUp(self):
        _limpiar()
        os.environ.pop('MENU_FILTRADO', None)

    def test_rut_ausente_es_nuevo_con_menu_completo(self):
        r = pe.clasificar('11.111.111-1', CFG_BASE)
        self.assertEqual(r['estado'], 'nuevo')
        self.assertIsNone(r['motivos_permitidos'],
                           'nunca se esconden motivos por falta de datos')

    def test_paciente_fijo_ve_el_menu_de_fijo(self):
        pe.registrar_cita_atendida(RUT_1, fechas.hoy_chile().isoformat(),
                                    'Montaje Total', cfg={})
        r = pe.clasificar(RUT_1, CFG_BASE)
        self.assertEqual(r['estado'], 'fijo')
        self.assertEqual(set(r['motivos_permitidos']), {'control_fijo', 'urgencia'})

    def test_vigencia_15_meses_cae_a_desconocido(self):
        vieja = control_dental.sumar_meses(fechas.hoy_chile(), -15).isoformat()
        pe.registrar_cita_atendida(RUT_1, vieja, 'Montaje Total', cfg={})
        r = pe.clasificar(RUT_1, CFG_BASE)
        self.assertEqual(r['estado'], 'desconocido')
        self.assertIsNone(r['motivos_permitidos'])

    def test_dentro_de_vigencia_no_cae(self):
        reciente = control_dental.sumar_meses(fechas.hoy_chile(), -6).isoformat()
        pe.registrar_cita_atendida(RUT_1, reciente, 'Montaje Total', cfg={})
        r = pe.clasificar(RUT_1, CFG_BASE)
        self.assertEqual(r['estado'], 'fijo')

    def test_menu_omite_motivos_ausentes_de_cfg_motivos(self):
        pe.registrar_cita_atendida(RUT_1, fechas.hoy_chile().isoformat(),
                                    'Montaje Total', cfg={})
        cfg_sin_urgencia = {
            'motivos': {'control_fijo': {}},  # 'urgencia' NO esta configurado
            'meses_vigencia_estado': 14,
        }
        r = pe.clasificar(RUT_1, cfg_sin_urgencia)
        self.assertEqual(r['motivos_permitidos'], ['control_fijo'])

    def test_rut_get_desconocido_devuelve_none(self):
        self.assertIsNone(pe.get('99.999.999-9'))

    def test_kill_switch_menu_filtrado_off(self):
        pe.registrar_cita_atendida(RUT_1, fechas.hoy_chile().isoformat(),
                                    'Montaje Total', cfg={})
        os.environ['MENU_FILTRADO'] = 'OFF'
        try:
            r = pe.clasificar(RUT_1, CFG_BASE)
        finally:
            os.environ.pop('MENU_FILTRADO', None)
        self.assertEqual(r['estado'], 'fijo', 'el kill-switch no toca el estado')
        self.assertIsNone(r['motivos_permitidos'], 'solo el menu se vuelve completo')

    def test_contingencia_8_estudio_integral_se_saca_si_rut_no_esta_en_indice(self):
        pe.registrar_cita_atendida(RUT_2, fechas.hoy_chile().isoformat(),
                                    'Primera Consulta', cfg={})
        import pacientes
        with mock.patch.object(pacientes, 'lookup', return_value=None):
            r = pe.clasificar(RUT_2, CFG_BASE)
        self.assertEqual(r['estado'], 'primera_consulta')
        self.assertNotIn('estudio_integral', r['motivos_permitidos'])
        self.assertIn('control_evolucion', r['motivos_permitidos'])

    def test_contingencia_8_estudio_integral_se_ofrece_si_rut_si_esta_en_indice(self):
        pe.registrar_cita_atendida(RUT_2, fechas.hoy_chile().isoformat(),
                                    'Primera Consulta', cfg={})
        import pacientes
        with mock.patch.object(pacientes, 'lookup', return_value={'email': 'a@b.cl'}):
            r = pe.clasificar(RUT_2, CFG_BASE)
        self.assertIn('estudio_integral', r['motivos_permitidos'])


class TestBarrer(unittest.TestCase):

    HOY = date(2026, 7, 29)  # miercoles

    def setUp(self):
        _limpiar()

    def test_barrer_tolera_un_dia_que_revienta(self):
        ayer = self.HOY - timedelta(days=1)      # martes 28
        antier = self.HOY - timedelta(days=2)    # lunes 27

        def fake_get_agenda_day(scfg, d):
            if d == ayer:
                raise RuntimeError('DentiDesk no respondio')
            return [_cita(_rut_plano(RUT_1), d.isoformat())]

        with mock.patch.object(fechas, 'hoy_chile', return_value=self.HOY), \
             mock.patch.object(pe.dentidesk, '_get_agenda_day', side_effect=fake_get_agenda_day):
            resultado = pe.barrer(cfg={}, dias_atras=2)

        self.assertEqual(resultado['dias_fallidos'], [ayer.isoformat()],
                          'el dia que revienta debe quedar registrado, sin frenar el resto')
        self.assertEqual(resultado['citas_procesadas'], 1, 'solo el dia lunes proceso su cita')
        r = pe.get(RUT_1)
        self.assertIsNotNone(r, 'el dia que si funciono debe haberse procesado')
        self.assertEqual(r['ultima_cita'], antier.isoformat())

    def test_barrer_no_incluye_hoy(self):
        """Una cita de HOY puede no haber pasado todavia -- se deja para el
        barrido de mañana (mismo criterio que control_dental)."""
        vistos = []

        def fake_get_agenda_day(scfg, d):
            vistos.append(d)
            return []

        with mock.patch.object(fechas, 'hoy_chile', return_value=self.HOY), \
             mock.patch.object(pe.dentidesk, '_get_agenda_day', side_effect=fake_get_agenda_day):
            pe.barrer(cfg={}, dias_atras=3)

        self.assertNotIn(self.HOY, vistos)

    def test_barrer_descarta_estados_no_ocurrio(self):
        def fake_get_agenda_day(scfg, d):
            return [_cita(_rut_plano(RUT_1), d.isoformat(), status='Hora Cancelada')]

        with mock.patch.object(fechas, 'hoy_chile', return_value=self.HOY), \
             mock.patch.object(pe.dentidesk, '_get_agenda_day', side_effect=fake_get_agenda_day):
            resultado = pe.barrer(cfg={}, dias_atras=1)

        self.assertEqual(resultado['citas_procesadas'], 0)
        self.assertIsNone(pe.get(RUT_1))

    def test_barrer_acumula_motivo_desconocido_sin_rut(self):
        def fake_get_agenda_day(scfg, d):
            return [_cita(_rut_plano(RUT_1), d.isoformat(),
                           reason='Cita Rarisima Sin Clasificar')]

        with mock.patch.object(fechas, 'hoy_chile', return_value=self.HOY), \
             mock.patch.object(pe.dentidesk, '_get_agenda_day', side_effect=fake_get_agenda_day):
            pe.barrer(cfg={}, dias_atras=1)

        desc = pe._load_estado()['motivos_desconocidos']
        self.assertIn('Cita Rarisima Sin Clasificar', desc)
        entrada = desc['Cita Rarisima Sin Clasificar']
        self.assertNotIn('rut', entrada)
        self.assertGreaterEqual(entrada['n'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
