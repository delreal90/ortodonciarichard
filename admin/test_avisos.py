"""
test_avisos.py - Las guardas que deciden A QUIEN se le manda un aviso.

Cero red, cero WhatsApp, cero correo: DentiDesk interceptado y todos los
registros en archivos temporales.

    cd admin && python test_avisos.py

Cubre los tres "sistemas de avisos" que comparten el mismo contrato de
evaluar() -> None si se puede enviar, o {motivo, detalle, puede_forzar}:
  - recaptacion.py   (WhatsApp, lo dispara la asistente a mano)
  - control_dental.py (email cada 6 meses, inscripcion automatica por barrido)
  - nps.py           (encuesta de satisfaccion por WhatsApp)

Un fallo aca no es un error de pantalla: es un paciente que recibe un mensaje
que pidio no recibir, o una oleada de correos a media cartera.
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='avisos_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fechas             # noqa: E402
import control_dental     # noqa: E402
import recaptacion        # noqa: E402
import nps                # noqa: E402
import recordatorios_wa   # noqa: E402
import confirmaciones     # noqa: E402

RUT = '17.406.985-9'


def _limpiar(modulo):
    modulo._save_registro({})


# ═════════════════════════════════════════════════════════════════════════
# control_dental — aritmetica de meses y clasificacion de motivos
# ═════════════════════════════════════════════════════════════════════════

class TestSumarMeses(unittest.TestCase):
    """El ciclo de 6 meses se calcula con esto. Un error corre TODOS los envios."""

    def test_mes_corto_cae_al_ultimo_dia_real(self):
        # 31 de agosto + 6 meses -> febrero no tiene 31
        self.assertEqual(control_dental.sumar_meses(date(2025, 8, 31), 6),
                         date(2026, 2, 28))

    def test_ano_bisiesto(self):
        self.assertEqual(control_dental.sumar_meses(date(2027, 8, 31), 6),
                         date(2028, 2, 29))

    def test_cruza_el_ano(self):
        self.assertEqual(control_dental.sumar_meses(date(2026, 10, 15), 6),
                         date(2027, 4, 15))

    def test_diciembre_mas_uno(self):
        self.assertEqual(control_dental.sumar_meses(date(2026, 12, 1), 1),
                         date(2027, 1, 1))

    def test_cero_meses_no_mueve(self):
        self.assertEqual(control_dental.sumar_meses(date(2026, 3, 15), 0),
                         date(2026, 3, 15))


class TestClasificarMotivo(unittest.TestCase):
    """De esto depende a quien se INSCRIBE y a quien se da de baja."""

    def test_montaje_inscribe(self):
        self.assertEqual(control_dental.clasificar_motivo('Montaje Total'), 'inicio_fijos')

    def test_retiro_total_es_fin_definitivo(self):
        self.assertEqual(control_dental.clasificar_motivo('Retiro Total'), 'fin_definitivo')

    def test_retiro_parcial_es_fin_de_fase_no_definitivo(self):
        """Clinicamente estos pacientes SUELEN seguir en tratamiento: la baja es
        reactivable y el panel los muestra aparte."""
        self.assertEqual(control_dental.clasificar_motivo('Retiro Parcial'), 'fin_fase')

    def test_sin_tildes_y_sin_mayusculas(self):
        for txt in ('montaje total', 'MONTAJE TOTAL', '  Montaje   Total  '):
            with self.subTest(txt=txt):
                self.assertEqual(control_dental.clasificar_motivo(txt), 'inicio_fijos')

    def test_motivo_desconocido_no_se_adivina(self):
        self.assertIsNone(control_dental.clasificar_motivo('Cita rarisima nueva'))
        self.assertIsNone(control_dental.clasificar_motivo(''))
        self.assertIsNone(control_dental.clasificar_motivo(None))

    def test_el_panel_puede_resolver_un_motivo_sin_deploy(self):
        cfg = {'motivos_extra': {'placa': 'inicio_fijos'}}
        self.assertEqual(control_dental.clasificar_motivo('Placa', cfg), 'inicio_fijos')

    def test_motivos_extra_manda_sobre_las_constantes(self):
        cfg = {'motivos_extra': {'retiro parcial': 'fin_definitivo'}}
        self.assertEqual(control_dental.clasificar_motivo('Retiro Parcial', cfg),
                         'fin_definitivo')


class TestGuardasControlDental(unittest.TestCase):

    def setUp(self):
        _limpiar(control_dental)
        self.cfg = control_dental.load_config()

    def _inscribir(self, **campos):
        reg = control_dental._load_registro()
        base = {'estado': 'activo', 'email': 'paciente@test.cl',
                'tiene_cita_futura': True,
                'fecha_inicio': fechas.hoy_chile().isoformat(),
                'ultima_cita': fechas.hoy_chile().isoformat()}
        base.update(campos)
        reg.setdefault('inscritos', {})[control_dental._rut_key(RUT)] = base
        control_dental._save_registro(reg)

    def test_paciente_inscrito_y_activo_se_puede_enviar(self):
        self._inscribir()
        self.assertIsNone(control_dental.evaluar(RUT, self.cfg))

    def test_no_molestar_nunca_se_puede_forzar(self):
        self._inscribir()
        control_dental.agregar_no_molestar(RUT)
        r = control_dental.evaluar(RUT, self.cfg)
        self.assertEqual(r['motivo'], 'no_molestar')
        self.assertFalse(r['puede_forzar'], 'no_molestar JAMAS se salta')

    def test_no_inscrito_se_bloquea(self):
        self.assertEqual(control_dental.evaluar(RUT, self.cfg)['motivo'], 'no_inscrito')

    def test_email_invalido_se_bloquea(self):
        for email in ('', '   ', 'sin-arroba'):
            with self.subTest(email=email):
                self._inscribir(email=email)
                self.assertEqual(control_dental.evaluar(RUT, self.cfg)['motivo'],
                                 'sin_email')

    def test_paciente_que_dejo_de_venir_se_pausa(self):
        """La guarda de señal de vida: sin citas hace 9+ meses y sin hora futura.
        Sin esto, al paciente que se fue se le manda correo para siempre."""
        hace_mucho = control_dental.sumar_meses(fechas.hoy_chile(), -12).isoformat()
        self._inscribir(tiene_cita_futura=False, ultima_cita=hace_mucho)
        r = control_dental.evaluar(RUT, self.cfg)
        self.assertEqual(r['motivo'], 'pausado_inactivo')
        self.assertTrue(r['puede_forzar'])

    def test_sin_citas_recientes_pero_con_hora_futura_no_se_pausa(self):
        hace_mucho = control_dental.sumar_meses(fechas.hoy_chile(), -12).isoformat()
        self._inscribir(tiene_cita_futura=True, ultima_cita=hace_mucho)
        self.assertIsNone(control_dental.evaluar(RUT, self.cfg))

    def test_no_molestar_gana_sobre_todo_lo_demas(self):
        """El orden importa: no_molestar se evalua ANTES que cualquier otra cosa."""
        control_dental.agregar_no_molestar(RUT)   # ni siquiera esta inscrito
        self.assertEqual(control_dental.evaluar(RUT, self.cfg)['motivo'], 'no_molestar')

    def test_quitar_no_molestar_lo_devuelve_al_circuito(self):
        self._inscribir()
        control_dental.agregar_no_molestar(RUT)
        control_dental.quitar_no_molestar(RUT)
        self.assertIsNone(control_dental.evaluar(RUT, self.cfg))

    def test_rut_con_y_sin_puntos_son_el_mismo_paciente(self):
        self._inscribir()
        control_dental.agregar_no_molestar('17406985-9')
        self.assertEqual(control_dental.evaluar('17.406.985-9', self.cfg)['motivo'],
                         'no_molestar')


# ═════════════════════════════════════════════════════════════════════════
# recaptacion — las 3 guardas
# ═════════════════════════════════════════════════════════════════════════

class TestGuardasRecaptacion(unittest.TestCase):

    def setUp(self):
        _limpiar(recaptacion)
        self.cfg = recaptacion.load_config()
        self.dd = mock.patch.object(recaptacion, 'dentidesk').start()
        self.dd.citas_futuras_paciente.return_value = []
        self.dd.limpiar_rut.side_effect = lambda r: (r or '').replace('.', '').replace('-', '').upper()
        self.addCleanup(mock.patch.stopall)

    def test_sin_bloqueos_se_puede_enviar(self):
        self.assertIsNone(recaptacion.evaluar(RUT, self.cfg))

    def test_no_molestar_no_se_puede_forzar(self):
        recaptacion.agregar_no_molestar(RUT)
        r = recaptacion.evaluar(RUT, self.cfg)
        self.assertEqual(r['motivo'], 'no_molestar')
        self.assertFalse(r['puede_forzar'])

    def test_ya_tiene_hora_se_bloquea_pero_es_forzable(self):
        """No molestar a quien ya agendo. Forzable: la asistente puede saber algo
        que el sistema no."""
        self.dd.citas_futuras_paciente.return_value = [
            {'fecha': '2026-08-08', 'profesional': 'Dr. Vial'}]
        r = recaptacion.evaluar(RUT, self.cfg)
        self.assertEqual(r['motivo'], 'ya_tiene_hora')
        self.assertTrue(r['puede_forzar'])
        self.assertIn('Dr. Vial', r['detalle'])

    def test_enviado_reciente_se_bloquea(self):
        recaptacion.marcar_enviado(RUT, '123', 'Dr. Vial', 'Juan')
        r = recaptacion.evaluar(RUT, self.cfg)
        self.assertEqual(r['motivo'], 'enviado_reciente')
        self.assertTrue(r['puede_forzar'])

    def test_pasado_el_minimo_se_puede_reenviar(self):
        dias = self.cfg.get('dias_minimos_reenvio', 90)
        viejo = (fechas.ahora_chile() - timedelta(days=dias + 1)).isoformat(timespec='seconds')
        reg = recaptacion._load_registro()
        reg.setdefault('envios', {})[recaptacion._rut_key(RUT)] = [{'fecha_envio': viejo}]
        recaptacion._save_registro(reg)
        self.assertIsNone(recaptacion.evaluar(RUT, self.cfg))

    def test_orden_no_molestar_antes_que_ya_tiene_hora(self):
        recaptacion.agregar_no_molestar(RUT)
        self.dd.citas_futuras_paciente.return_value = [
            {'fecha': '2026-08-08', 'profesional': 'Dr. Vial'}]
        self.assertEqual(recaptacion.evaluar(RUT, self.cfg)['motivo'], 'no_molestar')


class TestProgramadosRecaptacion(unittest.TestCase):
    """Los recordatorios con fecha futura: la garantia central es que se
    re-evaluan al ENVIAR, no al programar."""

    def setUp(self):
        _limpiar(recaptacion)

    def test_un_solo_pendiente_por_paciente(self):
        """Reprogramar anula el anterior, no lo duplica."""
        manana = (fechas.hoy_chile() + timedelta(days=1)).isoformat()
        pasado = (fechas.hoy_chile() + timedelta(days=2)).isoformat()
        recaptacion.programar(RUT, '1', '2026-01-01', 'Dr. Vial', 'Juan', manana)
        recaptacion.programar(RUT, '1', '2026-01-01', 'Dr. Vial', 'Juan', pasado)
        progs = recaptacion.listar_programados()
        pendientes = [p for p in progs if p['estado'] == 'pendiente']
        anulados = [p for p in progs if p['estado'] == 'anulado']
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(len(anulados), 1, 'el anterior se anula, no se borra')
        self.assertEqual(pendientes[0]['fecha_programada'], pasado)

    def test_vencidos_incluye_los_de_hoy_y_los_atrasados(self):
        """Usa <=: si se pierde la ventana de un dia, sale al dia siguiente."""
        ayer = (fechas.hoy_chile() - timedelta(days=1)).isoformat()
        hoy = fechas.hoy_chile().isoformat()
        manana = (fechas.hoy_chile() + timedelta(days=1)).isoformat()
        recaptacion.programar('1-9', 'a', '2026-01-01', 'D', 'A', ayer)
        recaptacion.programar('2-7', 'b', '2026-01-01', 'D', 'B', hoy)
        recaptacion.programar('3-5', 'c', '2026-01-01', 'D', 'C', manana)
        vencidos = recaptacion.pendientes_vencidos(fechas.hoy_chile())
        self.assertEqual(len(vencidos), 2, 'el de manana todavia no')

    def test_anular_saca_de_la_cola(self):
        manana = (fechas.hoy_chile() + timedelta(days=1)).isoformat()
        recaptacion.programar(RUT, '1', '2026-01-01', 'D', 'Juan', manana)
        id_ = recaptacion.listar_programados()[0]['id']
        recaptacion.anular_programado(id_)
        self.assertEqual(recaptacion.pendientes_vencidos(
            fechas.hoy_chile() + timedelta(days=30)), [])


# ═════════════════════════════════════════════════════════════════════════
# nps — clasificacion del disparo y cooldowns
# ═════════════════════════════════════════════════════════════════════════

class TestNPS(unittest.TestCase):

    def setUp(self):
        _limpiar(nps)
        self.cfg = nps.load_config()

    def test_fin_de_tratamiento_es_hito(self):
        self.assertEqual(nps.clasificar_disparo('Retiro Total'), 'hito')
        self.assertEqual(nps.clasificar_disparo('Retiro Parcial'), 'hito')

    def test_control_es_periodico(self):
        self.assertEqual(nps.clasificar_disparo('Montaje Total'), 'periodico')

    def test_motivo_desconocido_no_dispara(self):
        self.assertIsNone(nps.clasificar_disparo('Cita rarisima'))

    def test_sin_historial_se_puede_enviar(self):
        self.assertIsNone(nps.evaluar(RUT, True, self.cfg))

    def test_no_molestar_no_se_puede_forzar(self):
        nps.agregar_no_molestar(RUT)
        r = nps.evaluar(RUT, True, self.cfg)
        self.assertEqual(r['motivo'], 'no_molestar')
        self.assertFalse(r['puede_forzar'])

    def test_promotor_reciente_no_recibe_otra_encuesta(self):
        """Ya dijo que esta feliz: insistir cansa y arriesga el numero."""
        nps.registrar_respuesta(RUT, 'promotor', 'Dr. Octavio')
        r = nps.evaluar(RUT, True, self.cfg)
        self.assertEqual(r['motivo'], 'promotor_reciente')

    def test_detractor_no_queda_silenciado_por_ser_promotor(self):
        """El silencio largo es SOLO para promotores; un detractor cae al
        cooldown normal, mas corto."""
        nps.registrar_respuesta(RUT, 'detractor', 'Dr. Octavio')
        reg = nps._load_registro()
        self.assertFalse(nps.es_promotor_silenciado(RUT, self.cfg, reg))

    def test_cooldown_bloquea_un_reenvio_inmediato(self):
        nps.registrar_envio(RUT, '123', 'Dr. Octavio')
        r = nps.evaluar(RUT, True, self.cfg)
        self.assertEqual(r['motivo'], 'enviado_reciente')

    def test_pasado_el_cooldown_un_hito_vuelve_a_disparar(self):
        cooldown = self.cfg.get('cooldown_meses', 6)
        viejo = control_dental.sumar_meses(fechas.hoy_chile(), -(cooldown + 1))
        reg = nps._load_registro()
        reg.setdefault('envios', {})[nps._rut_key(RUT)] = [
            {'fecha': datetime.combine(viejo, datetime.min.time()).isoformat(timespec='seconds')}]
        nps._save_registro(reg)
        self.assertIsNone(nps.evaluar(RUT, True, self.cfg))


# ═════════════════════════════════════════════════════════════════════════
# Poda de registros — no pueden crecer para siempre, pero tampoco borrar de mas
# ═════════════════════════════════════════════════════════════════════════

class TestPoda(unittest.TestCase):

    def setUp(self):
        _limpiar(recaptacion)
        recordatorios_wa._save_registro({'semana': {}, 'dia': {}, 'inasistencia': {}})
        confirmaciones._save({})

    def _viejo(self, dias):
        return (fechas.ahora_chile() - timedelta(days=dias)).isoformat(timespec='seconds')

    # ── confirmaciones ────────────────────────────────────────────────────
    def test_confirmaciones_poda_lo_viejo_y_deja_lo_nuevo(self):
        idx = {'1': self._viejo(400), '2': self._viejo(10),
               '_ultima_corrida': self._viejo(400)}
        quitadas = confirmaciones._podar(idx)
        self.assertEqual(quitadas, 1)
        self.assertNotIn('1', idx)
        self.assertIn('2', idx)

    def test_confirmaciones_no_toca_las_claves_de_control(self):
        """'_ultima_corrida' define desde cuando busca citas nuevas el barrido:
        si se poda, el barrido pierde su punto de partida."""
        idx = {'_ultima_corrida': self._viejo(400)}
        confirmaciones._podar(idx)
        self.assertIn('_ultima_corrida', idx)

    # ── recordatorios ─────────────────────────────────────────────────────
    def test_recordatorios_poda_los_tres_tipos(self):
        reg = {'semana': {'1': self._viejo(400), '2': self._viejo(5)},
               'dia': {'3': self._viejo(400)},
               'inasistencia': {'4': self._viejo(1)}}
        quitadas = recordatorios_wa._podar(reg)
        self.assertEqual(quitadas, 2)
        self.assertEqual(list(reg['semana']), ['2'])
        self.assertEqual(reg['dia'], {})
        self.assertEqual(list(reg['inasistencia']), ['4'])

    # ── recaptacion: las dos reglas de seguridad ──────────────────────────
    def test_recaptacion_conserva_SIEMPRE_el_ultimo_envio_por_rut(self):
        """La guarda enviado_reciente se calcula sobre el ultimo envio. Borrarlo
        habilitaria un reenvio que no corresponde — le llegaria un WhatsApp
        repetido a un paciente."""
        reg = {'envios': {'179999999': [
            {'fecha_envio': self._viejo(2000)},
            {'fecha_envio': self._viejo(1500)},
        ]}}
        recaptacion._podar(reg)
        quedan = reg['envios']['179999999']
        self.assertEqual(len(quedan), 1, 'queda exactamente el mas reciente')
        self.assertEqual(quedan[0]['fecha_envio'], self._viejo(1500))

    def test_recaptacion_poda_los_viejos_y_deja_los_de_dentro_del_plazo(self):
        """Retencion = 730 dias. Los de 2000 y 1000 se van; el de 800 tambien;
        el de 400 y el de 10 se quedan."""
        reg = {'envios': {'179999999': [
            {'fecha_envio': self._viejo(2000)},
            {'fecha_envio': self._viejo(1000)},
            {'fecha_envio': self._viejo(400)},
            {'fecha_envio': self._viejo(10)},
        ]}}
        quitados = recaptacion._podar(reg)
        self.assertEqual(quitados, 2)
        quedan = [e['fecha_envio'] for e in reg['envios']['179999999']]
        self.assertEqual(quedan, [self._viejo(400), self._viejo(10)])

    def test_recaptacion_nunca_poda_un_programado_pendiente(self):
        """Un pendiente atrasado sigue en cola: pendientes_vencidos usa <=, asi
        que un envio que perdio su ventana sale al dia siguiente. Podarlo seria
        perder el recordatorio en silencio."""
        reg = {'programados': [
            {'id': 1, 'estado': 'pendiente', 'creado': self._viejo(2000)},
            {'id': 2, 'estado': 'anulado',   'creado': self._viejo(2000)},
            {'id': 3, 'estado': 'enviado',   'creado': self._viejo(10)},
        ]}
        recaptacion._podar(reg)
        ids = [p['id'] for p in reg['programados']]
        self.assertIn(1, ids, 'un pendiente no se poda nunca')
        self.assertNotIn(2, ids, 'un anulado viejo si')
        self.assertIn(3, ids, 'un enviado reciente se conserva')

    def test_la_poda_real_no_rompe_la_guarda_de_reenvio(self):
        """De punta a punta: marcar_enviado poda, y evaluar sigue bloqueando."""
        with mock.patch.object(recaptacion, 'dentidesk') as dd:
            dd.citas_futuras_paciente.return_value = []
            dd.limpiar_rut.side_effect = lambda r: (r or '').replace('.', '').replace('-', '')
            recaptacion.marcar_enviado(RUT, '1', 'Dr. Vial', 'Juan')
            r = recaptacion.evaluar(RUT, recaptacion.load_config())
        self.assertEqual(r['motivo'], 'enviado_reciente')


if __name__ == '__main__':
    unittest.main(verbosity=2)
