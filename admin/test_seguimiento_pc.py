"""
test_seguimiento_pc.py - Seguimiento de primeras consultas que no avanzaron.

Cero red: no se llama a DentiDesk. Los tests arman las citas a mano y las pasan
por seguimiento_pc._aplicar_barrido (la misma logica que usa barrer(), pero sin
la parte que baja getAgendaDay). 'hoy' se pasa explicito.

    cd admin && python test_seguimiento_pc.py

Un fallo aca es un paciente que se pierde (candidato que no aparece cuando
debia) o uno al que Alberto ve dos veces de mas (toque que no avanzo).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='segpc_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fechas             # noqa: E402
import seguimiento_pc as sp  # noqa: E402

HOY = date(2026, 7, 29)


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


def _barrer(citas_por_dia, hoy=HOY, cfg=None):
    """citas_por_dia: dict {fecha_iso: [citas]} -> corre _aplicar_barrido sobre un
    registro limpio y lo devuelve."""
    sp._save_registro({})
    cfg = cfg or sp.load_config()
    reg = sp._load_registro()
    resultados = [(date.fromisoformat(f), cs) for f, cs in citas_por_dia.items()]
    sp._aplicar_barrido(reg, cfg, resultados, hoy)
    sp._save_registro(reg)
    return reg


class TestClasificacion(unittest.TestCase):
    def test_primera_consulta_exacta(self):
        self.assertTrue(sp.es_primera_consulta('Primera Consulta'))
        self.assertTrue(sp.es_primera_consulta('PRIMERA CONSULTA'))

    def test_no_confundir_con_otras_consultas(self):
        # El bug clasico: 'contiene' pescaria estas. Debe ser match exacto.
        self.assertFalse(sp.es_primera_consulta('Segunda Consulta'))
        self.assertFalse(sp.es_primera_consulta('Consulta Online'))

    def test_avance_reconoce_estudio_y_montaje(self):
        self.assertTrue(sp.es_avance('Registros para el Estudio Integral de Ortodoncia'))
        self.assertTrue(sp.es_avance('Explicación del Diagnóstico y Plan de Tratamiento'))
        self.assertTrue(sp.es_avance('Segunda Consulta'))
        self.assertTrue(sp.es_avance('Montaje Total'))     # via control_dental
        self.assertTrue(sp.es_avance('Control Fijo'))      # via control_dental

    def test_avance_no_marca_la_primera_consulta(self):
        self.assertFalse(sp.es_avance('Primera Consulta'))
        self.assertFalse(sp.es_avance(''))


class TestNormalizarWa(unittest.TestCase):
    def test_nueve_digitos_partiendo_con_9(self):
        self.assertEqual(sp.normalizar_wa('987654321'), '56987654321')
        self.assertEqual(sp.normalizar_wa('+56 9 8765 4321'), '56987654321')

    def test_formato_invalido_no_inventa(self):
        self.assertEqual(sp.normalizar_wa('221734'), '')
        self.assertEqual(sp.normalizar_wa(''), '')


class TestBarridoCandidatos(unittest.TestCase):
    def test_primera_consulta_sin_avance_queda_pendiente(self):
        f = (HOY - timedelta(days=10)).isoformat()
        reg = _barrer({f: [_cita('11111111', f)]})
        c = reg['candidatos']['11111111']
        self.assertEqual(c['estado'], 'pendiente')
        self.assertEqual(c['proximo_toque'], 1)
        # proxima_fecha = fecha_pc + dias_toque_1 (7)
        self.assertEqual(c['proxima_fecha'],
                         (date.fromisoformat(f) + timedelta(days=7)).isoformat())

    def test_con_hora_futura_se_convierte(self):
        f = (HOY - timedelta(days=10)).isoformat()
        futuro = (HOY + timedelta(days=5)).isoformat()
        reg = _barrer({
            f: [_cita('22222222', f)],
            futuro: [_cita('22222222', futuro, reason='Control Fijo', status='No confirmado')],
        })
        self.assertEqual(reg['candidatos']['22222222']['estado'], 'convertido')

    def test_con_montaje_posterior_se_convierte(self):
        f = (HOY - timedelta(days=20)).isoformat()
        montaje = (HOY - timedelta(days=5)).isoformat()
        reg = _barrer({
            f: [_cita('33333333', f)],
            montaje: [_cita('33333333', montaje, reason='Montaje Total')],
        })
        self.assertEqual(reg['candidatos']['33333333']['estado'], 'convertido')

    def test_primera_consulta_cancelada_no_es_candidato(self):
        f = (HOY - timedelta(days=10)).isoformat()
        reg = _barrer({f: [_cita('44444444', f, status='Hora Cancelada')]})
        self.assertNotIn('44444444', reg['candidatos'])

    def test_estudio_agendado_a_futuro_no_es_candidato(self):
        # Vino a PC y ya tiene el estudio agendado -> enganchado, no aparece.
        f = (HOY - timedelta(days=3)).isoformat()
        estudio = (HOY + timedelta(days=10)).isoformat()
        reg = _barrer({
            f: [_cita('55555555', f)],
            estudio: [_cita('55555555', estudio,
                            reason='Registros para el Estudio Integral de Ortodoncia',
                            status='No confirmado')],
        })
        self.assertEqual(reg['candidatos']['55555555']['estado'], 'convertido')


class TestPendientes(unittest.TestCase):
    def test_filtra_por_doctor_y_fecha_y_arma_mensaje(self):
        f = (HOY - timedelta(days=10)).isoformat()
        _barrer({f: [
            _cita('11111111', f, doctor='Alberto Del Real', nombre='Maria Jose Soto'),
            _cita('99999999', f, doctor='Rodrigo Oyonarte', nombre='Pedro Lagos'),
        ]})
        items = sp.pendientes(fecha=HOY, doctor='Alberto Del Real')
        self.assertEqual([i['rut'] for i in items], ['11111111'])
        it = items[0]
        self.assertTrue(it['mensaje'].startswith('Hola Maria'))  # saluda por el nombre de pila
        self.assertNotIn('Soto', it['mensaje'])                  # no vuelca el nombre completo
        self.assertEqual(it['wa_numero'], '56987654321')
        self.assertEqual(it['toque'], 1)

    def test_no_aparece_antes_de_que_toque(self):
        f = (HOY - timedelta(days=3)).isoformat()   # +7 aun no llega
        _barrer({f: [_cita('11111111', f)]})
        self.assertEqual(sp.pendientes(fecha=HOY), [])

    def test_no_molestar_lo_excluye(self):
        f = (HOY - timedelta(days=10)).isoformat()
        _barrer({f: [_cita('11111111', f)]})
        sp.agregar_no_molestar('11111111')
        self.assertEqual(sp.pendientes(fecha=HOY), [])

    def test_tope_max_por_reporte(self):
        f = (HOY - timedelta(days=10)).isoformat()
        cfg = sp.load_config()
        cfg['max_por_reporte'] = 2
        citas = [_cita(f'1000000{i}', f, nombre=f'Pac {i}') for i in range(5)]
        sp._save_registro({})
        reg = sp._load_registro()
        sp._aplicar_barrido(reg, cfg, [(date.fromisoformat(f), citas)], HOY)
        sp._save_registro(reg)
        items = sp.pendientes(fecha=HOY, cfg=cfg)
        self.assertEqual(len(items), 2)


class TestDosToques(unittest.TestCase):
    def test_avanza_a_toque_2_y_luego_completado(self):
        f = (HOY - timedelta(days=10)).isoformat()
        _barrer({f: [_cita('11111111', f)]})

        # Toque 1 mostrado hoy -> pasa a toque 2, programado ~+30 desde la PC.
        with mock.patch.object(fechas, 'hoy_chile', return_value=HOY), \
             mock.patch.object(fechas, 'ahora_chile', return_value=fechas.ahora_chile()):
            self.assertEqual(sp.marcar_mostrados(['11111111']), 1)
        c = sp._load_registro()['candidatos']['11111111']
        self.assertEqual(c['proximo_toque'], 2)
        esperado = (date.fromisoformat(f) + timedelta(days=30)).isoformat()
        gap = (HOY + timedelta(days=14)).isoformat()
        self.assertEqual(c['proxima_fecha'], max(esperado, gap))

        # Ya no aparece hoy (toque 2 es a futuro).
        self.assertEqual(sp.pendientes(fecha=HOY), [])

        # Cuando llega el toque 2 y se muestra -> completado.
        futuro = date.fromisoformat(c['proxima_fecha'])
        items = sp.pendientes(fecha=futuro)
        self.assertEqual(items[0]['toque'], 2)
        self.assertIn('equipo del Dr. Alberto', items[0]['mensaje'])
        with mock.patch.object(fechas, 'hoy_chile', return_value=futuro), \
             mock.patch.object(fechas, 'ahora_chile', return_value=fechas.ahora_chile()):
            sp.marcar_mostrados(['11111111'])
        c = sp._load_registro()['candidatos']['11111111']
        self.assertEqual(c['estado'], 'completado')
        self.assertEqual(sp.pendientes(fecha=futuro + timedelta(days=60)), [])

    def test_marcar_dos_veces_el_mismo_dia_no_avanza_de_mas(self):
        f = (HOY - timedelta(days=10)).isoformat()
        _barrer({f: [_cita('11111111', f)]})
        with mock.patch.object(fechas, 'hoy_chile', return_value=HOY):
            sp.marcar_mostrados(['11111111'])
            sp.marcar_mostrados(['11111111'])   # segundo click el mismo dia
        c = sp._load_registro()['candidatos']['11111111']
        self.assertEqual(c['proximo_toque'], 2)   # avanzo UNA vez, no dos


class TestDescartar(unittest.TestCase):
    """«El paciente avisó que no inicia»: un desenlace decidido, distinto de
    no-molestar (que solo silencia) y distinto de perderse (que es desaparecer)."""

    def setUp(self):
        sp._STORE.save(dict(sp._ESTRUCTURA))

    def test_descartar_y_deshacer(self):
        self.assertTrue(sp.descartar('11.111.111-1', motivo='no le calzó el precio'))
        self.assertTrue(sp.esta_descartado('111111111'))
        self.assertIn('111111111', sp.descartados())
        self.assertTrue(sp.deshacer_descarte('111111111'))
        self.assertFalse(sp.esta_descartado('111111111'))

    def test_se_puede_marcar_aunque_no_sea_candidato_todavia(self):
        """El paciente avisa cuando quiere; el barrido recién lo detecta a los 7 días.
        Si hubiera que ser candidato primero, el caso más común no se podría registrar."""
        self.assertTrue(sp.descartar('22.222.222-2'))
        self.assertTrue(sp.esta_descartado('222222222'))

    def test_rut_invalido_no_se_guarda(self):
        self.assertFalse(sp.descartar('   '))

    def test_deja_de_aparecer_para_contactar(self):
        """La razón práctica del botón: ya avisó que no, y el sistema le seguía
        escribiendo para invitarlo a retomar su evaluación."""
        reg = sp._load_registro()
        reg['candidatos']['333333333'] = {
            'rut': '333333333', 'nombre': 'Paciente Prueba', 'telefono': '912345678',
            'doctor': 'Alberto Del Real', 'fecha_pc': '2026-09-01', 'estado': 'pendiente',
            'proximo_toque': 1, 'proxima_fecha': '2026-09-08', 'toques': [],
        }
        sp._save_registro(reg)
        antes = [p['rut'] for p in sp.pendientes(fecha='2026-09-30')]
        self.assertIn('333333333', antes)

        sp.descartar('333333333')
        despues = [p['rut'] for p in sp.pendientes(fecha='2026-09-30')]
        self.assertNotIn('333333333', despues)

    def test_sigue_filtrado_aunque_el_barrido_lo_recree_como_pendiente(self):
        """El barrido pasa todos los días: si la guarda viviera solo en el estado del
        candidato, bastaría una pasada para que volviera a la lista."""
        sp.descartar('444444444')
        reg = sp._load_registro()
        reg['candidatos']['444444444'] = {
            'rut': '444444444', 'nombre': 'Otro', 'telefono': '', 'doctor': '',
            'fecha_pc': '2026-09-01', 'estado': 'pendiente',   # ← recreado por el barrido
            'proximo_toque': 1, 'proxima_fecha': '2026-09-08', 'toques': [],
        }
        sp._save_registro(reg)
        self.assertNotIn('444444444',
                         [p['rut'] for p in sp.pendientes(fecha='2026-09-30')])



class TestMesControl(unittest.TestCase):
    """«Control programado» con el MES en que el doctor le indicó volver: es lo que
    permite revisar después si el paciente de verdad vino."""

    def setUp(self):
        sp._STORE.save(dict(sp._ESTRUCTURA))

    def test_mes_valido_normaliza_y_rechaza(self):
        self.assertEqual(sp.mes_valido('2027-3'), '2027-03')
        self.assertEqual(sp.mes_valido('2027-03-15'), '2027-03')
        for malo in ('', 'marzo', '2027-13', '2027-00', '1999-05', '2072-03', None):
            self.assertEqual(sp.mes_valido(malo), '', malo)

    def test_se_guarda_con_el_control_programado(self):
        self.assertTrue(sp.marcar_destino('111111111', 'control_programado',
                                          fecha_pc='2026-08-25', mes_control='2027-02'))
        self.assertEqual(sp.destinos_manuales()['111111111']['mes_control'], '2027-02')

    def test_otros_destinos_no_guardan_mes(self):
        """Un mes colgado de 'en tratamiento' no significaría nada, y alguien podría
        leerlo como un control que hay que revisar."""
        sp.marcar_destino('222222222', 'en_tratamiento', mes_control='2027-02')
        self.assertEqual(sp.destinos_manuales()['222222222']['mes_control'], '')

    def test_sin_mes_igual_queda_marcado(self):
        """El doctor puede no saber el mes en el momento: la marca no se pierde."""
        self.assertTrue(sp.marcar_destino('333333333', 'control_programado'))
        self.assertEqual(sp.destino_de('333333333'), 'control_programado')
        self.assertEqual(sp.destinos_manuales()['333333333']['mes_control'], '')

    def test_cambiar_el_mes_conserva_la_nota(self):
        sp.marcar_destino('444444444', 'control_programado', motivo='ver erupción de caninos',
                          fecha_pc='2026-08-25', mes_control='2027-02')
        self.assertTrue(sp.fijar_mes_control('444444444', '2027-04'))
        d = sp.destinos_manuales()['444444444']
        self.assertEqual(d['mes_control'], '2027-04')
        self.assertEqual(d['motivo'], 'ver erupción de caninos')
        self.assertEqual(d['fecha_pc'], '2026-08-25')

    def test_no_se_fija_mes_a_quien_no_es_control_programado(self):
        sp.marcar_destino('555555555', 'no_inicia')
        self.assertFalse(sp.fijar_mes_control('555555555', '2027-04'))
        self.assertFalse(sp.fijar_mes_control('666666666', '2027-04'))   # sin marca
        sp.marcar_destino('777777777', 'control_programado', mes_control='2027-02')
        self.assertFalse(sp.fijar_mes_control('777777777', 'abril'))     # mes inválido
        self.assertEqual(sp.destinos_manuales()['777777777']['mes_control'], '2027-02')

if __name__ == '__main__':
    unittest.main(verbosity=2)
