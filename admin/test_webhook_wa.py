"""
test_webhook_wa.py - El webhook que CANCELA CITAS REALES.

Cero red, cero WhatsApp, cero DentiDesk: todo interceptado.

    cd admin && python test_webhook_wa.py

Por que este modulo es el mas riesgoso del proyecto: procesa un POST que manda
Meta y, a partir de un string parseado con split(':'), llama a DentiDesk para
cambiar el estado de una cita. Un cambio de formato del payload, o un texto de
boton editado en Meta, lo rompe en silencio — y del otro lado hay pacientes que
se quedan sin hora o que creen haber anulado y no anularon.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='webhook_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import webhook_wa      # noqa: E402

CFG = {'dentidesk': {
    'id_status_confirmado_semana':   40968,
    'id_status_confirmado_whatsapp': 32180,
    'id_status_cancelado':           2122,
    'id_status_quiere_reagendar':    33579,
}}


def evento(payload, texto, telefono='56988887777', perfil=None, tipo_msg='button'):
    """Arma el POST tal como lo manda Meta."""
    contacts = [{'wa_id': telefono, 'profile': {'name': perfil}}] if perfil else []
    return {'entry': [{'changes': [{'value': {
        'contacts': contacts,
        'messages': [{'type': tipo_msg, 'from': telefono,
                      'button': {'text': texto, 'payload': payload}}],
    }}]}]}


class _Base(unittest.TestCase):
    """Intercepta TODO lo que sale hacia afuera y registra las llamadas."""

    def setUp(self):
        self.dentidesk = mock.patch.object(webhook_wa, 'dentidesk').start()
        self.notify = mock.patch.object(webhook_wa, 'notify').start()
        self.nps = mock.patch.object(webhook_wa, 'nps').start()
        self.recaptacion = mock.patch.object(webhook_wa, 'recaptacion').start()
        self.pendientes = mock.patch.object(webhook_wa, 'reagenda_pendientes').start()
        self.nps.load_config.return_value = {'review_url': 'https://g.page/x/review'}
        self.dentidesk.info_cita.return_value = None
        self.addCleanup(mock.patch.stopall)


class TestParseoDePayload(_Base):

    def test_formato_actual_tipo_id_fecha(self):
        r = webhook_wa.procesar_evento(
            evento('semana:13389698:2026-08-03', 'Confirmo'), CFG)
        self.assertEqual(r['procesados'], 1)
        self.dentidesk.actualizar_estado_cita.assert_called_once_with(
            '13389698', 40968, CFG)

    def test_boton_viejo_sin_fecha_sigue_funcionando(self):
        """Compatibilidad: botones enviados antes de 2026-07-08 traen solo
        'tipo:id_agenda'. Siguen vivos en los telefonos de los pacientes."""
        r = webhook_wa.procesar_evento(evento('dia:13389698', 'Confirmo'), CFG)
        self.assertEqual(r['procesados'], 1)
        self.dentidesk.actualizar_estado_cita.assert_called_once_with(
            '13389698', 32180, CFG)

    def test_payload_sin_id_no_hace_nada(self):
        for crudo in ('', 'semana', 'semana:', ':::'):
            with self.subTest(payload=crudo):
                self.dentidesk.reset_mock()
                r = webhook_wa.procesar_evento(evento(crudo, 'Anular'), CFG)
                self.assertEqual(r['procesados'], 0)
                self.dentidesk.actualizar_estado_cita.assert_not_called()

    def test_mensaje_que_no_es_boton_se_ignora(self):
        """Texto libre y recibos de entrega no los maneja el bot."""
        for t in ('text', 'image', 'status'):
            with self.subTest(tipo=t):
                r = webhook_wa.procesar_evento(
                    evento('dia:123:2026-08-03', 'Confirmo', tipo_msg=t), CFG)
                self.assertEqual(r['procesados'], 0)
        self.dentidesk.actualizar_estado_cita.assert_not_called()


class TestAcciones(_Base):

    def test_anular_usa_el_estado_cancelado(self):
        webhook_wa.procesar_evento(evento('dia:13389698:2026-08-03', 'Anular'), CFG)
        self.dentidesk.actualizar_estado_cita.assert_called_once_with(
            '13389698', 2122, CFG)
        self.notify.avisar_recepcion_anulacion.assert_called_once()
        self.notify.enviar_texto_libre.assert_called_once()

    def test_confirmo_desde_semana_vs_desde_dia(self):
        """El IdStatus depende de QUE recordatorio disparo el boton."""
        casos = [('semana:1:2026-08-03', 40968), ('dia:1:2026-08-03', 32180),
                 ('inasistencia:1:2026-08-03', 32180)]
        for payload, esperado in casos:
            with self.subTest(payload=payload):
                self.dentidesk.reset_mock()
                webhook_wa.procesar_evento(evento(payload, 'Confirmo'), CFG)
                self.dentidesk.actualizar_estado_cita.assert_called_once_with(
                    '1', esperado, CFG)

    def test_reagendar_marca_pidio_cambiar_hora(self):
        """Se marca 33579 ('Pidio cambiar su hora') para que recepcion vea la
        intencion en la agenda, PERO la cita sigue VIGENTE: solo
        /reservar-reagenda la pasa a 'Re-agendado' cuando el paciente concreta
        la hora nueva. Si se cancelara aca y abandona el flujo, queda sin hora."""
        webhook_wa.procesar_evento(evento('dia:13389698:2026-08-03', 'Reagendar'), CFG)
        self.dentidesk.actualizar_estado_cita.assert_called_once_with(
            '13389698', 33579, CFG)
        texto = self.notify.enviar_texto_libre.call_args[0][1]
        self.assertIn('#reagendar=13389698&fecha=2026-08-03', texto)

    def test_reagendar_no_avisa_a_recepcion_al_tiro_sino_que_anota(self):
        """El aviso espera unos minutos: la mayoria elige su hora nueva al tiro
        con el link, y ese correo llenaba la bandeja de recepcion sin necesidad.
        Quien decide es el barrido (_procesar_reagenda_pendientes)."""
        webhook_wa.procesar_evento(evento('dia:13389698:2026-08-03', 'Reagendar'), CFG)
        self.notify.avisar_recepcion_quiere_reagendar.assert_not_called()
        self.pendientes.registrar.assert_called_once()
        args = self.pendientes.registrar.call_args[0]
        self.assertEqual(args[0], '13389698')

    def test_si_no_se_puede_anotar_el_pendiente_se_avisa_al_tiro(self):
        """Mejor un correo de mas que dejar a recepcion sin enterarse."""
        self.pendientes.registrar.side_effect = RuntimeError('disco lleno')
        webhook_wa.procesar_evento(evento('dia:13389698:2026-08-03', 'Reagendar'), CFG)
        self.notify.avisar_recepcion_quiere_reagendar.assert_called_once()

    def test_reagendar_desde_los_tres_origenes(self):
        """El boton Reagendar existe en recordatorio_semana, recordatorio_dia e
        inasistencia_reagendar: los tres deben marcar igual."""
        for payload in ('semana:1:2026-08-03', 'dia:1:2026-08-03', 'inasistencia:1:2026-08-03'):
            with self.subTest(payload=payload):
                self.dentidesk.reset_mock()
                webhook_wa.procesar_evento(evento(payload, 'Reagendar'), CFG)
                self.dentidesk.actualizar_estado_cita.assert_called_once_with(
                    '1', 33579, CFG)

    def test_reagendar_sin_fecha_manda_link_igual(self):
        webhook_wa.procesar_evento(evento('dia:13389698', 'Reagendar'), CFG)
        texto = self.notify.enviar_texto_libre.call_args[0][1]
        self.assertIn('#reagendar=13389698', texto)
        self.dentidesk.actualizar_estado_cita.assert_called_once_with(
            '13389698', 33579, CFG)

    def test_reagendar_sin_id_status_configurado_no_inventa(self):
        """Config incompleta: NO se llama a DentiDesk con None, pero el paciente
        igual recibe su link (el mensaje nunca depende del marcado)."""
        webhook_wa.procesar_evento(evento('dia:1:2026-08-03', 'Reagendar'),
                                   {'dentidesk': {}})
        self.dentidesk.actualizar_estado_cita.assert_not_called()
        self.notify.enviar_texto_libre.assert_called_once()

    def test_reagendar_con_dentidesk_caido_igual_manda_link(self):
        """Regla del proyecto: el webhook nunca se cae porque un paso
        secundario falle -- el mensaje al paciente sale igual."""
        self.dentidesk.actualizar_estado_cita.side_effect = RuntimeError('502 Bad Gateway')
        r = webhook_wa.procesar_evento(evento('dia:1:2026-08-03', 'Reagendar'), CFG)
        self.assertEqual(r['procesados'], 1)
        self.notify.enviar_texto_libre.assert_called_once()
        self.pendientes.registrar.assert_called_once()

    def test_reagendar_guarda_nombre_y_rut_para_el_barrido(self):
        """El RUT es lo que despues permite preguntarle a DentiDesk si el
        paciente ya agendo: sin el, el aviso sale igual."""
        self.dentidesk.info_cita.return_value = {
            'PatientName': 'Juan Perez', 'PatientDocument': '11.111.111-1'}
        webhook_wa.procesar_evento(evento('dia:1:2026-08-03', 'Reagendar'), CFG)
        args = self.pendientes.registrar.call_args[0]
        self.assertIn('Juan Perez', args)
        self.assertIn('11.111.111-1', args)

    def test_agendar_por_whatsapp_no_toca_dentidesk_y_avisa(self):
        self.dentidesk.info_cita.return_value = {
            'PatientDocument': '11.111.111-1', 'PatientName': 'Juan Perez'}
        webhook_wa.procesar_evento(
            evento('control:999:2026-08-03', 'Agendar por WhatsApp'), CFG)
        self.dentidesk.actualizar_estado_cita.assert_not_called()
        self.recaptacion.marcar_respondio.assert_called_once_with('11.111.111-1')
        self.notify.avisar_recepcion_interes_control.assert_called_once()

    def test_boton_desconocido_no_hace_nada(self):
        """Si alguien edita el texto del boton en Meta, no debe adivinar."""
        r = webhook_wa.procesar_evento(
            evento('dia:13389698:2026-08-03', 'Confirmar asistencia'), CFG)
        self.assertEqual(r['procesados'], 0)
        self.dentidesk.actualizar_estado_cita.assert_not_called()
        self.notify.enviar_texto_libre.assert_not_called()

    def test_sin_id_status_configurado_no_inventa(self):
        """Config incompleta: avisa al paciente pero NO llama a DentiDesk con None."""
        webhook_wa.procesar_evento(evento('dia:1:2026-08-03', 'Anular'),
                                   {'dentidesk': {}})
        self.dentidesk.actualizar_estado_cita.assert_not_called()


class TestNPS(_Base):

    def setUp(self):
        super().setUp()
        self.dentidesk.info_cita.return_value = {
            'PatientName': 'Juan Perez', 'PatientDocument': '11.111.111-1',
            'ProfessionalName': 'Dr. Octavio Del Real'}
        self.dentidesk.limpiar_rut.side_effect = lambda r: (r or '').replace('.', '').replace('-', '')

    def test_promotor_recibe_link_de_resenia(self):
        webhook_wa.procesar_evento(evento('nps:555:2026-08-03', 'Excelente'), CFG)
        self.nps.registrar_respuesta.assert_called_once_with(
            '111111111', 'promotor', 'Dr. Octavio Del Real')
        self.notify.responder_nps_promotor.assert_called_once()

    def test_pasivo_no_recibe_link_de_resenia(self):
        """Un GBP tiene UN solo link publico: no se le pide resenia a un pasivo."""
        webhook_wa.procesar_evento(evento('nps:555:2026-08-03', 'Buena'), CFG)
        self.notify.responder_nps_pasivo.assert_called_once()
        self.notify.responder_nps_promotor.assert_not_called()

    def test_detractor_avisa_a_recepcion_y_no_pide_resenia(self):
        webhook_wa.procesar_evento(evento('nps:555:2026-08-03', 'Puede mejorar'), CFG)
        self.notify.avisar_recepcion_detractor.assert_called_once()
        self.notify.responder_nps_promotor.assert_not_called()

    def test_nps_nunca_toca_dentidesk(self):
        for t in ('Excelente', 'Buena', 'Puede mejorar'):
            with self.subTest(boton=t):
                webhook_wa.procesar_evento(evento('nps:555:2026-08-03', t), CFG)
        self.dentidesk.actualizar_estado_cita.assert_not_called()

    def test_boton_nps_desconocido_no_registra_respuesta(self):
        webhook_wa.procesar_evento(evento('nps:555:2026-08-03', 'Excelente!'), CFG)
        self.nps.registrar_respuesta.assert_not_called()


class TestRobustez(_Base):

    def test_un_mensaje_que_falla_no_mata_a_los_otros(self):
        """Meta puede mandar varios mensajes en un mismo POST. Si uno revienta,
        los demas TIENEN que procesarse igual."""
        payload = {'entry': [{'changes': [{'value': {'messages': [
            {'type': 'button', 'from': '5691', 'button': {'text': 'Anular', 'payload': 'dia:1:2026-08-03'}},
            {'type': 'button', 'from': '5692', 'button': {'text': 'Anular', 'payload': 'dia:2:2026-08-03'}},
        ]}}]}]}
        self.dentidesk.actualizar_estado_cita.side_effect = None
        self.notify.enviar_texto_libre.side_effect = [RuntimeError('WhatsApp caido'), None]
        r = webhook_wa.procesar_evento(payload, CFG)
        self.assertEqual(r['procesados'], 1, 'el segundo mensaje se procesa igual')

    def test_dentidesk_caido_no_rompe_el_webhook(self):
        """Si DentiDesk falla, igual hay que responderle al paciente — y sobre
        todo devolver 200 a Meta, que si no reintenta el evento."""
        self.dentidesk.actualizar_estado_cita.side_effect = RuntimeError('502')
        r = webhook_wa.procesar_evento(evento('dia:1:2026-08-03', 'Anular'), CFG)
        self.assertTrue(r['ok'])
        self.assertEqual(r['procesados'], 1)
        self.notify.enviar_texto_libre.assert_called_once()

    def test_payload_vacio_o_raro_no_revienta(self):
        for p in ({}, {'entry': None}, {'entry': [{}]},
                  {'entry': [{'changes': [{'value': {}}]}]},
                  {'entry': [{'changes': [{'value': {'messages': None}}]}]}):
            with self.subTest(payload=p):
                self.assertTrue(webhook_wa.procesar_evento(p, CFG)['ok'])

    def test_nombre_de_perfil_como_fallback(self):
        """Sin fecha no se puede consultar DentiDesk: se usa el nombre de perfil
        de WhatsApp para que el aviso a recepcion no salga anonimo."""
        webhook_wa.procesar_evento(
            evento('dia:1', 'Anular', perfil='Juanito Perez'), CFG)
        args = self.notify.avisar_recepcion_anulacion.call_args[0]
        self.assertIn('Juanito Perez', args)


if __name__ == '__main__':
    unittest.main(verbosity=2)
