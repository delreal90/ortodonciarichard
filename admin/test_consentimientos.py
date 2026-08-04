"""
test_consentimientos.py - Que no se dupliquen los consentimientos, y que el
aviso diario a recepcion liste SOLO a los pacientes del dia.

Cero red, cero correo: DentiDesk interceptado y el registro en un archivo
temporal.

    cd admin && python test_consentimientos.py

Los dos problemas reales que motivaron estas pruebas (produccion, jul-2026):

  1. Un paciente al que se le mandaba el link 2-3 veces terminaba con 2-3
     registros. Firmaba UNO y los otros quedaban en 'enviado' para siempre:
     8 de los 12 pendientes eran de este tipo. Cubierto por
     obtener_o_crear_registro() (no duplicar) + el cierre de hermanos de
     marcar_firmado() + limpiar_huerfanos() (los ya existentes).

  2. El aviso preguntaba por las citas de los proximos 45 dias, asi que el
     mismo paciente salia en el correo TODOS los dias hasta firmar. Ahora
     pendientes_con_cita_en() mira UN dia.

Un fallo aca no es cosmetico: es recepcion recibiendo todos los dias una lista
con pacientes que ya firmaron, hasta que deja de leer el correo.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='consent_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['CONSENTIMIENTOS_REGISTRO_PATH'] = str(_TMP / 'consentimientos_registro.json')
os.environ['CONSENTIMIENTOS_COLA_PATH'] = str(_TMP / 'cola_tablet.json')
os.environ['CONSENTIMIENTOS_PDF_DIR'] = str(_TMP / 'firmados')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import consentimientos    # noqa: E402

RUT = '22222222-9'          # ficticio (el repo es publico: nunca un RUT real)
OTRO_RUT = '17406985-9'
TIPO = 'ortodoncia'


def _limpiar():
    consentimientos._save_registro({})


def _poner(consent_id, rut, tipo, estado, creado, firmado=None, canal='whatsapp'):
    """Siembra un registro con fechas controladas (las funciones normales
    estampan ahora_chile(), y aca hacen falta fechas del pasado)."""
    idx = consentimientos._load_registro()
    idx[consent_id] = {'rut': consentimientos._limpiar_rut(rut), 'tipo': tipo,
                       'canal': canal, 'estado': estado, 'creado': creado,
                       'firmado': firmado, 'pdf_path': None,
                       'subido_dentidesk': False, 'respaldo_drive': None}
    consentimientos._save_registro(idx)


def _hace(dias):
    return (consentimientos.ahora_chile() - timedelta(days=dias)).isoformat(timespec='seconds')


# ── 1. No duplicar al enviar ────────────────────────────────────────────────

class TestObtenerOCrear(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_el_primer_envio_crea_uno_nuevo(self):
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        self.assertFalse(reutilizado)
        self.assertEqual(consentimientos.obtener_registro(cid)['estado'], 'enviado')

    def test_el_segundo_envio_reutiliza_el_mismo_registro(self):
        """El caso que generaba los huerfanos: reenviar el link no debe crear
        otro registro."""
        cid1, _ = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cid2, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'whatsapp')
        self.assertEqual(cid1, cid2)
        self.assertTrue(reutilizado)
        self.assertEqual(len(consentimientos.listar()), 1)

    def test_al_reutilizar_actualiza_el_canal_y_deja_rastro(self):
        cid, _ = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'whatsapp')
        item = consentimientos.obtener_registro(cid)
        self.assertEqual(item['canal'], 'whatsapp')
        self.assertEqual(len(item['reenvios']), 1)
        self.assertEqual(item['reenvios'][0]['canal'], 'whatsapp')

    def test_uno_mas_viejo_que_la_ventana_no_se_reutiliza(self):
        _poner('viejo', RUT, TIPO, 'enviado', _hace(30 * 7))   # 7 meses
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        self.assertFalse(reutilizado)
        self.assertNotEqual(cid, 'viejo')

    def test_una_firma_reciente_no_bloquea_un_envio_nuevo(self):
        """Si la secretaria manda de nuevo es porque quiere una firma nueva
        (otra fase del tratamiento): eso SI crea un registro aparte."""
        _poner('firmado1', RUT, TIPO, 'firmado', _hace(10), firmado=_hace(10))
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        self.assertFalse(reutilizado)
        self.assertNotEqual(cid, 'firmado1')

    def test_un_reemplazado_tampoco_se_reutiliza(self):
        _poner('r1', RUT, TIPO, 'reemplazado', _hace(5))
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        self.assertFalse(reutilizado)

    def test_otro_tipo_no_se_reutiliza(self):
        cid1, _ = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cid2, reutilizado = consentimientos.obtener_o_crear_registro(RUT, 'rehabilitacion', 'mail')
        self.assertFalse(reutilizado)
        self.assertNotEqual(cid1, cid2)

    def test_el_rut_se_normaliza(self):
        """Con puntos y guion, o sin nada, tiene que dar con el mismo registro."""
        cid1, _ = consentimientos.obtener_o_crear_registro('22.222.222-9', TIPO, 'mail')
        cid2, reutilizado = consentimientos.obtener_o_crear_registro('222222229', TIPO, 'mail')
        self.assertEqual(cid1, cid2)
        self.assertTrue(reutilizado)

    def test_crear_registro_sigue_creando_siempre(self):
        """El flujo walk-up de la tablet lo necesita: nace y se firma en el
        mismo request."""
        a = consentimientos.crear_registro(RUT, TIPO, 'tablet')
        b = consentimientos.crear_registro(RUT, TIPO, 'tablet')
        self.assertNotEqual(a, b)


# ── 2. Cerrar hermanos al firmar ────────────────────────────────────────────

class TestCierreDeHermanos(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_los_enviados_previos_quedan_reemplazados(self):
        _poner('a', RUT, TIPO, 'enviado', _hace(8))
        _poner('b', RUT, TIPO, 'enviado', _hace(7))
        _poner('c', RUT, TIPO, 'enviado', _hace(1))
        consentimientos.marcar_firmado('c', 'x.pdf')
        self.assertEqual(consentimientos.obtener_registro('a')['estado'], 'reemplazado')
        self.assertEqual(consentimientos.obtener_registro('b')['estado'], 'reemplazado')
        self.assertEqual(consentimientos.obtener_registro('a')['reemplazado_por'], 'c')
        self.assertEqual(consentimientos.obtener_registro('c')['estado'], 'firmado')

    def test_no_toca_otro_tipo(self):
        _poner('otro_tipo', RUT, 'rehabilitacion', 'enviado', _hace(8))
        _poner('c', RUT, TIPO, 'enviado', _hace(1))
        consentimientos.marcar_firmado('c', 'x.pdf')
        self.assertEqual(consentimientos.obtener_registro('otro_tipo')['estado'], 'enviado')

    def test_no_toca_otro_paciente(self):
        _poner('otro_rut', OTRO_RUT, TIPO, 'enviado', _hace(8))
        _poner('c', RUT, TIPO, 'enviado', _hace(1))
        consentimientos.marcar_firmado('c', 'x.pdf')
        self.assertEqual(consentimientos.obtener_registro('otro_rut')['estado'], 'enviado')

    def test_no_toca_uno_enviado_despues_de_la_firma(self):
        """Un consentimiento posterior a una firma es una peticion nueva y
        legitima: no es un huerfano."""
        _poner('c', RUT, TIPO, 'enviado', _hace(5))
        consentimientos.marcar_firmado('c', 'x.pdf')
        _poner('posterior', RUT, TIPO, 'enviado',
               (consentimientos.ahora_chile() + timedelta(seconds=5)).isoformat(timespec='seconds'))
        consentimientos.marcar_firmado('c', 'x.pdf')   # re-firmar no debe arrastrarlo
        self.assertEqual(consentimientos.obtener_registro('posterior')['estado'], 'enviado')

    def test_un_reemplazado_no_se_puede_borrar(self):
        _poner('a', RUT, TIPO, 'enviado', _hace(8))
        _poner('c', RUT, TIPO, 'enviado', _hace(1))
        consentimientos.marcar_firmado('c', 'x.pdf')
        ok, error = consentimientos.borrar_registro('a')
        self.assertFalse(ok)
        # server.py responde 409 cuando el mensaje menciona "firmado".
        self.assertIn('firmado', error)

    def test_un_enviado_normal_si_se_puede_borrar(self):
        cid, _ = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        ok, error = consentimientos.borrar_registro(cid)
        self.assertTrue(ok)


# ── 3. Limpieza retroactiva ─────────────────────────────────────────────────

class TestLimpiarHuerfanos(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_el_caso_real_reportado(self):
        """Reproduce el caso reportado: 2 enviados el 22-jul, firmo el 29-jul."""
        _poner('a', RUT, TIPO, 'enviado', '2026-07-22T16:21:00-04:00')
        _poner('b', RUT, TIPO, 'enviado', '2026-07-22T16:53:00-04:00')
        _poner('c', RUT, TIPO, 'firmado', '2026-07-29T12:20:00-04:00',
               firmado='2026-07-29T12:23:00-04:00')
        r = consentimientos.limpiar_huerfanos()
        self.assertEqual(r['cerrados'], 2)
        self.assertEqual(consentimientos.obtener_registro('a')['estado'], 'reemplazado')
        self.assertEqual(consentimientos.obtener_registro('b')['estado'], 'reemplazado')

    def test_con_dos_firmas_cada_huerfano_lo_cierra_la_suya(self):
        """Caso real de un paciente con 2 firmas y envios intercalados: la
        atribucion tiene que ser cronologica, no 'la ultima firma se lleva
        todo'."""
        _poner('e1', OTRO_RUT, TIPO, 'enviado', '2026-07-07T12:20:00-04:00')
        _poner('f1', OTRO_RUT, TIPO, 'firmado', '2026-07-07T12:00:00-04:00',
               firmado='2026-07-07T12:25:00-04:00')
        _poner('e2', OTRO_RUT, TIPO, 'enviado', '2026-07-08T09:00:00-04:00')
        _poner('f2', OTRO_RUT, TIPO, 'firmado', '2026-07-09T08:00:00-04:00',
               firmado='2026-07-09T08:53:00-04:00')
        consentimientos.limpiar_huerfanos()
        self.assertEqual(consentimientos.obtener_registro('e1')['reemplazado_por'], 'f1')
        self.assertEqual(consentimientos.obtener_registro('e2')['reemplazado_por'], 'f2')

    def test_no_toca_un_enviado_posterior_a_todas_las_firmas(self):
        _poner('f', RUT, TIPO, 'firmado', '2026-07-03T09:00:00-04:00',
               firmado='2026-07-03T10:21:00-04:00')
        _poner('posterior', RUT, TIPO, 'enviado', '2026-07-07T12:27:00-04:00')
        consentimientos.limpiar_huerfanos()
        self.assertEqual(consentimientos.obtener_registro('posterior')['estado'], 'enviado')

    def test_es_idempotente(self):
        _poner('a', RUT, TIPO, 'enviado', '2026-07-22T16:21:00-04:00')
        _poner('c', RUT, TIPO, 'firmado', '2026-07-29T12:20:00-04:00',
               firmado='2026-07-29T12:23:00-04:00')
        self.assertEqual(consentimientos.limpiar_huerfanos()['cerrados'], 1)
        self.assertEqual(consentimientos.limpiar_huerfanos()['cerrados'], 0)

    def test_no_borra_nada(self):
        _poner('a', RUT, TIPO, 'enviado', '2026-07-22T16:21:00-04:00')
        _poner('c', RUT, TIPO, 'firmado', '2026-07-29T12:20:00-04:00',
               firmado='2026-07-29T12:23:00-04:00')
        consentimientos.limpiar_huerfanos()
        self.assertEqual(len(consentimientos.listar()), 2)

    def test_un_subido_sin_fecha_de_firma_no_revienta(self):
        """Registros viejos pueden no traer 'firmado'; se cae a 'creado'."""
        _poner('a', RUT, TIPO, 'enviado', '2026-07-01T10:00:00-04:00')
        _poner('s', RUT, TIPO, 'subido', '2026-07-05T10:00:00-04:00', firmado=None)
        consentimientos.limpiar_huerfanos()
        self.assertEqual(consentimientos.obtener_registro('a')['estado'], 'reemplazado')


# ── 4. El aviso mira UN dia ─────────────────────────────────────────────────

HOY = date(2026, 8, 5)


def _cita(rut, hora='10:30', estado='Confirmado', doctor='Alberto Del Real'):
    return {'PatientDocument': rut, 'time': f'{hora}:00', 'Status': estado,
            'ProfessionalName': doctor, 'Reason': 'Control',
            'Date': HOY.isoformat(), 'IdAgenda': '1'}


def _con_dentidesk(citas):
    """Habilita DentiDesk y devuelve las citas dadas. Se parchea por ruta
    punteada porque consentimientos.py importa dentidesk/scheduling DENTRO de
    la funcion, no a nivel de modulo."""
    return (mock.patch('scheduling.load_config',
                       return_value={'dentidesk': {'enabled': True}}),
            mock.patch('dentidesk._get_agenda_day', return_value=citas))


class TestPendientesConCitaEn(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_sin_pendientes_no_llama_a_la_api(self):
        cfg, agenda = _con_dentidesk([_cita('22222222')])
        with cfg, agenda as m:
            self.assertEqual(consentimientos.pendientes_con_cita_en(HOY), [])
            m.assert_not_called()

    def test_con_dentidesk_apagado_no_llama_a_la_api(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        with mock.patch('scheduling.load_config',
                        return_value={'dentidesk': {'enabled': False}}), \
             mock.patch('dentidesk._get_agenda_day') as m:
            self.assertEqual(consentimientos.pendientes_con_cita_en(HOY), [])
            m.assert_not_called()

    def test_el_paciente_con_cita_ese_dia_aparece(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('22222222-9', hora='11:15')])
        with cfg, agenda:
            r = consentimientos.pendientes_con_cita_en(HOY)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['hora_cita'], '11:15')
        self.assertEqual(r[0]['doctor_cita'], 'Alberto Del Real')
        self.assertEqual(r[0]['rut'], '22.222.222-9')

    def test_el_paciente_sin_cita_ese_dia_no_aparece(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('11111111-1')])
        with cfg, agenda:
            self.assertEqual(consentimientos.pendientes_con_cita_en(HOY), [])

    def test_las_citas_que_no_ocurren_se_excluyen(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        for estado in ('Hora Cancelada', 'Paciente no llega', 'Re-agendado',
                       'No seguir (conversado con tratante)'):
            cfg, agenda = _con_dentidesk([_cita('22222222-9', estado=estado)])
            with cfg, agenda:
                self.assertEqual(consentimientos.pendientes_con_cita_en(HOY), [],
                                 f'no deberia listar una cita "{estado}"')

    def test_una_cita_ya_atendida_SI_aparece(self):
        """Decision de diseño: si al paciente ya lo atendieron sin firmar, ese
        es justamente el caso que hay que avisar. Por eso la tupla propia y no
        dentidesk._ESTADOS_INACTIVOS, que excluye 'atendido'."""
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('22222222-9', estado='Atendido')])
        with cfg, agenda:
            self.assertEqual(len(consentimientos.pendientes_con_cita_en(HOY)), 1)

    def test_los_firmados_y_reemplazados_no_aparecen(self):
        _poner('firmado1', RUT, TIPO, 'firmado', _hace(2), firmado=_hace(2))
        _poner('reemp', OTRO_RUT, TIPO, 'reemplazado', _hace(2))
        cfg, agenda = _con_dentidesk([_cita('22222222-9'), _cita('17406985-9')])
        with cfg, agenda:
            self.assertEqual(consentimientos.pendientes_con_cita_en(HOY), [])

    def test_una_sola_llamada_a_la_agenda_con_varios_pendientes(self):
        """La razon de ser del rediseño: antes era una llamada por pendiente,
        cada una barriendo 45 dias."""
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        consentimientos.obtener_o_crear_registro(OTRO_RUT, TIPO, 'mail')
        consentimientos.obtener_o_crear_registro('11111111-1', TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('22222222-9'), _cita('17406985-9')])
        with cfg, agenda as m:
            r = consentimientos.pendientes_con_cita_en(HOY)
        self.assertEqual(len(r), 2)
        m.assert_called_once()

    def test_acepta_la_fecha_como_texto(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('22222222-9')])
        with cfg, agenda as m:
            r = consentimientos.pendientes_con_cita_en('2026-08-05')
        self.assertEqual(len(r), 1)
        self.assertEqual(m.call_args[0][1], HOY)

    def test_dos_pendientes_del_mismo_tipo_salen_una_sola_vez(self):
        """Los duplicados que quedaron de antes del dedup no deben mostrar al
        mismo paciente dos veces en el correo (caso real: un RUT con dos
        'enviado' sin firmar, que no tienen firma que los cierre)."""
        _poner('v1', RUT, TIPO, 'enviado', _hace(10))
        _poner('v2', RUT, TIPO, 'enviado', _hace(2))
        cfg, agenda = _con_dentidesk([_cita('22222222-9')])
        with cfg, agenda:
            r = consentimientos.pendientes_con_cita_en(HOY)
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['consent_id'], 'v2')   # el mas reciente

    def test_dos_tipos_distintos_si_salen_los_dos(self):
        _poner('orto', RUT, TIPO, 'enviado', _hace(3))
        _poner('rehab', RUT, 'rehabilitacion', 'enviado', _hace(3))
        cfg, agenda = _con_dentidesk([_cita('22222222-9')])
        with cfg, agenda:
            r = consentimientos.pendientes_con_cita_en(HOY)
        self.assertEqual(len(r), 2)

    def test_sale_ordenado_por_hora(self):
        consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        consentimientos.obtener_o_crear_registro(OTRO_RUT, TIPO, 'mail')
        cfg, agenda = _con_dentidesk([_cita('22222222-9', hora='16:00'),
                                      _cita('17406985-9', hora='09:00')])
        with cfg, agenda:
            r = consentimientos.pendientes_con_cita_en(HOY)
        self.assertEqual([i['hora_cita'] for i in r], ['09:00', '16:00'])


# ── 5. Compatibilidad con registros viejos ──────────────────────────────────

class TestRegistrosViejos(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_uno_sin_las_claves_nuevas_no_rompe_nada(self):
        idx = consentimientos._load_registro()
        idx['viejo'] = {'rut': consentimientos._limpiar_rut(RUT), 'tipo': TIPO,
                        'canal': 'mail', 'estado': 'enviado',
                        'creado': _hace(3), 'firmado': None}
        consentimientos._save_registro(idx)
        self.assertEqual(len(consentimientos.listar()), 1)
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'whatsapp')
        self.assertEqual(cid, 'viejo')
        self.assertTrue(reutilizado)
        self.assertEqual(len(consentimientos.obtener_registro('viejo')['reenvios']), 1)

    def test_uno_con_creado_corrupto_se_ignora_sin_reventar(self):
        _poner('malo', RUT, TIPO, 'enviado', 'no-es-una-fecha')
        cid, reutilizado = consentimientos.obtener_o_crear_registro(RUT, TIPO, 'mail')
        self.assertFalse(reutilizado)
        self.assertNotEqual(cid, 'malo')


if __name__ == '__main__':
    unittest.main(verbosity=2)
