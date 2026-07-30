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


if __name__ == '__main__':
    unittest.main(verbosity=2)
