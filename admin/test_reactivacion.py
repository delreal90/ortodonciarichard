"""
test_reactivacion.py - Reactivacion de pacientes inactivos (terminado / abandono).

Cero red: no se llama a DentiDesk. Los tests arman las citas a mano y las pasan
por reactivacion._aplicar_barrido (la misma logica que usa barrer(), pero sin la
parte que baja getAgendaDay). 'hoy' se pasa explicito.

    cd admin && python test_reactivacion.py

Un fallo aca es un paciente que se pierde (candidato que no aparece cuando
debia) o uno al que Alberto ve dos veces de mas (toque que no avanzo, o un
paciente con hora futura que igual sale en el reporte).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='reactivacion_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fechas                # noqa: E402
import control_dental        # noqa: E402
import reactivacion as rc    # noqa: E402

HOY = date(2026, 7, 29)


def _cita(rut, fecha, reason='Control Fijo', status='Atendido',
          doctor='Alberto Del Real', nombre='Juan Perez', telefono='987654321',
          id_agenda=None):
    return {
        'IdAgenda': id_agenda or f'{rut}-{fecha}-{reason}',
        'PatientDocument': rut,
        'PatientName': nombre,
        'ProfessionalName': doctor,
        'Reason': reason,
        'Status': status,
        'Date': fecha,
        'Phone': telefono,
    }


def _barrer(citas_por_dia, hoy=HOY, cfg=None):
    """citas_por_dia: dict {fecha_iso: [citas]} -> corre _aplicar_barrido sobre
    un registro limpio y lo devuelve."""
    rc._save_registro({})
    cfg = cfg or rc.load_config()
    reg = rc._load_registro()
    resultados = [(date.fromisoformat(f), cs) for f, cs in citas_por_dia.items()]
    rc._aplicar_barrido(reg, cfg, resultados, hoy)
    rc._save_registro(reg)
    return reg


def _meses_atras(n, hoy=HOY):
    return control_dental.sumar_meses(hoy, -n)


class TestNormalizarWa(unittest.TestCase):
    def test_nueve_digitos_partiendo_con_9(self):
        self.assertEqual(rc.normalizar_wa('987654321'), '56987654321')
        self.assertEqual(rc.normalizar_wa('+56 9 8765 4321'), '56987654321')

    def test_formato_invalido_no_inventa(self):
        self.assertEqual(rc.normalizar_wa('221734'), '')
        self.assertEqual(rc.normalizar_wa(''), '')


class TestClasificacionPoblaciones(unittest.TestCase):
    def test_retiro_total_hace_7_meses_es_terminado(self):
        f = _meses_atras(7).isoformat()
        reg = _barrer({f: [_cita('11111111', f, reason='Retiro Total')]})
        c = reg['candidatos']['11111111']
        self.assertEqual(c['poblacion'], 'terminado')
        self.assertEqual(c['estado'], 'pendiente')
        self.assertEqual(c['fecha_ref'], f)

    def test_montaje_y_control_sin_alta_hace_7_meses_es_abandono(self):
        f_montaje = _meses_atras(12).isoformat()
        f_control = _meses_atras(7).isoformat()
        reg = _barrer({
            f_montaje: [_cita('22222222', f_montaje, reason='Montaje Total')],
            f_control: [_cita('22222222', f_control, reason='Control Fijo')],
        })
        c = reg['candidatos']['22222222']
        self.assertEqual(c['poblacion'], 'abandono')
        self.assertEqual(c['estado'], 'pendiente')
        self.assertEqual(c['fecha_ref'], f_control)  # ultima_cita, no la de montaje

    def test_con_hora_futura_no_es_candidato(self):
        f = _meses_atras(7).isoformat()
        futuro = (HOY + timedelta(days=10)).isoformat()
        reg = _barrer({
            f: [_cita('33333333', f, reason='Retiro Total')],
            futuro: [_cita('33333333', futuro, reason='Control Fijo', status='No confirmado')],
        })
        self.assertNotIn('33333333', reg['candidatos'])

    def test_candidato_pendiente_que_agenda_pasa_a_volvio(self):
        # Primero califica como candidato pendiente (barrido 1)...
        f = _meses_atras(7).isoformat()
        rc._save_registro({})
        cfg = rc.load_config()
        reg = rc._load_registro()
        rc._aplicar_barrido(reg, cfg, [(date.fromisoformat(f),
                                        [_cita('44444444', f, reason='Retiro Total')])], HOY)
        rc._save_registro(reg)
        self.assertEqual(reg['candidatos']['44444444']['estado'], 'pendiente')

        # ...luego agenda una hora futura (barrido 2, mismo registro).
        futuro = (HOY + timedelta(days=5)).isoformat()
        reg = rc._load_registro()
        rc._aplicar_barrido(reg, cfg, [(date.fromisoformat(f),
                                        [_cita('44444444', f, reason='Retiro Total')]),
                                       (date.fromisoformat(futuro),
                                        [_cita('44444444', futuro, reason='Control Fijo',
                                               status='No confirmado')])], HOY)
        rc._save_registro(reg)
        self.assertEqual(reg['candidatos']['44444444']['estado'], 'volvio')

    def test_alta_reciente_no_es_candidato_todavia(self):
        f = _meses_atras(2).isoformat()  # aun no pasan los 6 meses de recall
        reg = _barrer({f: [_cita('55555555', f, reason='Retiro Total')]})
        self.assertNotIn('55555555', reg['candidatos'])

    def test_sin_tratamiento_previo_no_es_candidato(self):
        # Solo un motivo no clasificado (ni fin_definitivo ni avance) -> nunca
        # tuvo tratamiento real, no debe salir como 'abandono'.
        f = _meses_atras(7).isoformat()
        reg = _barrer({f: [_cita('66666666', f, reason='Radiografia Suelta')]})
        self.assertNotIn('66666666', reg['candidatos'])


class TestPendientes(unittest.TestCase):
    def test_filtra_por_doctor_y_arma_mensaje_por_poblacion(self):
        f_terminado = _meses_atras(7).isoformat()
        f_abandono_montaje = _meses_atras(12).isoformat()
        f_abandono_control = _meses_atras(7).isoformat()
        rc._save_registro({})
        cfg = rc.load_config()
        reg = rc._load_registro()
        rc._aplicar_barrido(reg, cfg, [
            (date.fromisoformat(f_terminado), [
                _cita('11111111', f_terminado, reason='Retiro Total',
                      doctor='Alberto Del Real', nombre='Maria Jose Soto'),
            ]),
            (date.fromisoformat(f_abandono_montaje), [
                _cita('99999999', f_abandono_montaje, reason='Montaje Total',
                      doctor='Rodrigo Oyonarte', nombre='Pedro Lagos'),
            ]),
            (date.fromisoformat(f_abandono_control), [
                _cita('99999999', f_abandono_control, reason='Control Fijo',
                      doctor='Rodrigo Oyonarte', nombre='Pedro Lagos'),
            ]),
        ], HOY)
        rc._save_registro(reg)

        items_alberto = rc.pendientes(fecha=HOY, doctor='Alberto Del Real')
        self.assertEqual([i['rut'] for i in items_alberto], ['11111111'])
        it = items_alberto[0]
        self.assertEqual(it['poblacion'], 'terminado')
        self.assertTrue(it['mensaje'].startswith('Hola Maria'))  # primer nombre
        self.assertNotIn('Soto', it['mensaje'])
        self.assertIn('retenedores', it['mensaje'])
        self.assertEqual(it['wa_numero'], '56987654321')
        self.assertEqual(it['toque'], 1)

        items_rodrigo = rc.pendientes(fecha=HOY, doctor='Rodrigo Oyonarte')
        self.assertEqual([i['rut'] for i in items_rodrigo], ['99999999'])
        it2 = items_rodrigo[0]
        self.assertEqual(it2['poblacion'], 'abandono')
        self.assertTrue(it2['mensaje'].startswith('Hola Pedro'))
        self.assertIn('retomarlo', it2['mensaje'])

    def test_no_molestar_lo_excluye(self):
        f = _meses_atras(7).isoformat()
        _barrer({f: [_cita('11111111', f, reason='Retiro Total')]})
        rc.agregar_no_molestar('11111111')
        self.assertEqual(rc.pendientes(fecha=HOY), [])

    def test_tope_max_por_reporte(self):
        f = _meses_atras(7).isoformat()
        cfg = rc.load_config()
        cfg['max_por_reporte'] = 2
        citas = [_cita(f'1000000{i}', f, reason='Retiro Total', nombre=f'Pac {i}')
                 for i in range(5)]
        rc._save_registro({})
        reg = rc._load_registro()
        rc._aplicar_barrido(reg, cfg, [(date.fromisoformat(f), citas)], HOY)
        rc._save_registro(reg)
        items = rc.pendientes(fecha=HOY, cfg=cfg)
        self.assertEqual(len(items), 2)


class TestDosToques(unittest.TestCase):
    def test_avanza_a_toque_2_y_luego_completado(self):
        f = _meses_atras(7).isoformat()
        _barrer({f: [_cita('11111111', f, reason='Retiro Total')]})

        with mock.patch.object(fechas, 'hoy_chile', return_value=HOY):
            self.assertEqual(rc.marcar_mostrados(['11111111']), 1)
        c = rc._load_registro()['candidatos']['11111111']
        self.assertEqual(c['proximo_toque'], 2)
        esperado = (HOY + timedelta(days=45)).isoformat()  # dias_entre_toques
        self.assertEqual(c['proxima_fecha'], esperado)

        # Ya no aparece hoy (toque 2 es a futuro).
        self.assertEqual(rc.pendientes(fecha=HOY), [])

        # Cuando llega el toque 2 y se muestra -> completado.
        futuro = date.fromisoformat(c['proxima_fecha'])
        items = rc.pendientes(fecha=futuro)
        self.assertEqual(items[0]['toque'], 2)
        with mock.patch.object(fechas, 'hoy_chile', return_value=futuro):
            rc.marcar_mostrados(['11111111'])
        c = rc._load_registro()['candidatos']['11111111']
        self.assertEqual(c['estado'], 'completado')
        self.assertEqual(rc.pendientes(fecha=futuro + timedelta(days=60)), [])

    def test_marcar_dos_veces_el_mismo_dia_no_avanza_de_mas(self):
        f = _meses_atras(7).isoformat()
        _barrer({f: [_cita('11111111', f, reason='Retiro Total')]})
        with mock.patch.object(fechas, 'hoy_chile', return_value=HOY):
            rc.marcar_mostrados(['11111111'])
            rc.marcar_mostrados(['11111111'])   # segundo click el mismo dia
        c = rc._load_registro()['candidatos']['11111111']
        self.assertEqual(c['proximo_toque'], 2)   # avanzo UNA vez, no dos


if __name__ == '__main__':
    unittest.main(verbosity=2)
