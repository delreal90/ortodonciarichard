"""
test_mensajes_libres.py - Mensajes de texto libre escritos al WhatsApp de la clinica.

Cero red, cero WhatsApp, cero correo: notify y el reloj estan interceptados.

    cd admin && python test_mensajes_libres.py

Por que se prueba: el paciente escribe "me duele" y hasta ahora nadie en la
clinica se enteraba. Si esta cadena falla en silencio, el sintoma es identico
al problema original. Y el reves tambien duele: sin la ventana anti-inundacion,
un paciente que manda 15 mensajes seguidos llena el correo de recepcion.

Todos los telefonos, nombres y textos son INVENTADOS (repo publico).
"""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='mensajes_libres_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['MENSAJES_LIBRES_PATH'] = str(_TMP / 'mensajes_libres.json')
os.environ['LLAMADAS_REGISTRO_PATH'] = str(_TMP / 'llamadas_registro.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import mensajes_libres   # noqa: E402
import pacientes         # noqa: E402
import webhook_wa        # noqa: E402

CFG = {'dentidesk': {},
       'clinica': {'whatsapp': '+56900000001'},
       'horario': {'dias_habiles': [1, 2, 3, 4, 5], 'apertura': '09:00', 'cierre': '19:30'}}

TEL = '56912345678'
# 2026-10-07 es miercoles.
EN_HORARIO = datetime(2026, 10, 7, 11, 0)
FUERA_HORARIO = datetime(2026, 10, 7, 21, 15)
FIN_DE_SEMANA = datetime(2026, 10, 10, 12, 0)   # sabado


def evento(texto='Hola, tengo una duda', telefono=TEL, tipo='text', extra=None,
           perfil=None, metadata=None):
    msg = {'type': tipo, 'from': telefono, 'id': 'wamid.X'}
    if tipo == 'text':
        msg['text'] = {'body': texto}
    if extra:
        msg.update(extra)
    valor = {'contacts': [{'wa_id': telefono, 'profile': {'name': perfil}}] if perfil else [],
             'messages': [msg]}
    if metadata:
        valor['metadata'] = metadata
    return {'entry': [{'changes': [{'field': 'messages', 'value': valor}]}]}


class _Base(unittest.TestCase):

    def setUp(self):
        mensajes_libres.vaciar()
        pacientes.vaciar()
        self.reloj = [EN_HORARIO]
        mock.patch.object(mensajes_libres.fechas, 'ahora_chile',
                          side_effect=lambda: self.reloj[0]).start()
        self.notify = mock.patch.object(mensajes_libres, 'notify').start()
        self.notify.avisar_recepcion_mensaje_libre.return_value = True
        self.notify.enviar_texto_libre.return_value = {'ok': True}
        # Las clases de siempre prueban QUE dice el correo y la ventana de 30 min,
        # asi que corren sin la espera de respuesta (el correo sale en el mismo
        # evento). La espera de 5 min se prueba aparte, en TestEsperaRespuesta.
        mock.patch.object(mensajes_libres, 'ESPERA_RESPUESTA_MIN', 0).start()
        self.addCleanup(mock.patch.stopall)

    def enviar(self, *a, **kw):
        return webhook_wa.procesar_evento(evento(*a, **kw), CFG)

    def avanzar(self, **kw):
        self.reloj[0] = self.reloj[0] + timedelta(**kw)

    def _sembrar_paciente(self, rut='11111111K', nombres='Maria',
                          apellidos='Perez', telefono='+56 9 1234 5678'):
        idx = pacientes._load_index()
        idx[rut] = {'nombres': nombres, 'apellidos': apellidos,
                    'telefono': telefono, 'genero': 'F'}
        pacientes._save_index(idx)

    @property
    def correos(self):
        return self.notify.avisar_recepcion_mensaje_libre.call_args_list


class TestTextoAEmail(_Base):

    def test_texto_libre_manda_correo_con_los_datos(self):
        self._sembrar_paciente()
        r = self.enviar('Necesito cambiar mi hora')
        self.assertEqual(r['procesados'], 1)
        self.assertEqual(len(self.correos), 1)
        args, kwargs = self.correos[0]
        nombre, telefono, mensajes = args
        self.assertEqual(nombre, 'Maria Perez')
        self.assertEqual(telefono, TEL)
        self.assertTrue(kwargs['registrado'])
        self.assertEqual(len(mensajes), 1)
        hora, texto = mensajes[0]
        self.assertEqual(texto, 'Necesito cambiar mi hora')
        self.assertIn('07-10 11:00', hora)

    def test_numero_no_registrado_avisa_igual(self):
        self.enviar('Hola', perfil='Juan Inventado')
        args, kwargs = self.correos[0]
        self.assertEqual(args[0], 'Juan Inventado')   # nombre de perfil de WhatsApp
        self.assertFalse(kwargs['registrado'])

    def test_numero_sin_perfil_ni_ficha(self):
        self.enviar('Hola')
        args, kwargs = self.correos[0]
        self.assertEqual(args[0], '')
        self.assertFalse(kwargs['registrado'])

    def test_adjuntos_se_anotan_como_envio_un(self):
        casos = [('audio', '[envió un audio]'), ('image', '[envió una imagen]'),
                 ('document', '[envió un documento]'), ('video', '[envió un video]'),
                 ('sticker', '[envió un sticker]'), ('location', '[envió una ubicación]')]
        for i, (tipo, esperado) in enumerate(casos):
            with self.subTest(tipo=tipo):
                mensajes_libres.vaciar()
                self.notify.reset_mock()
                self.enviar(tipo=tipo, telefono=f'5691111000{i}')
                self.assertEqual(self.correos[0][0][2][0][1], esperado)

    def test_adjunto_con_pie_de_foto(self):
        self.enviar(tipo='image', extra={'image': {'caption': 'mi radiografia'}})
        self.assertEqual(self.correos[0][0][2][0][1], '[envió una imagen] mi radiografia')

    def test_texto_muy_largo_se_recorta(self):
        self.enviar('x' * 5000)
        self.assertEqual(len(self.correos[0][0][2][0][1]), mensajes_libres.MAX_TEXTO)

    def test_los_botones_no_son_mensajes_libres(self):
        """Un toque de boton lo maneja el bot: no debe generar aviso de texto."""
        payload = {'entry': [{'changes': [{'value': {'messages': [
            {'type': 'button', 'from': TEL,
             'button': {'text': 'Otro', 'payload': 'dia:1:2026-10-08'}}]}}]}]}
        with mock.patch.object(webhook_wa, 'notify'):
            webhook_wa.procesar_evento(payload, CFG)
        self.assertEqual(len(self.correos), 0)

    def test_reacciones_y_eventos_del_sistema_no_avisan(self):
        for tipo in ('reaction', 'system', 'order'):
            with self.subTest(tipo=tipo):
                self.enviar(tipo=tipo)
        self.assertEqual(len(self.correos), 0)


class TestAntiInundacion(_Base):

    def test_segundo_mensaje_dentro_de_30_min_no_manda_otro_correo(self):
        self.enviar('Primero')
        self.avanzar(minutes=5)
        self.enviar('Segundo')
        self.avanzar(minutes=20)
        self.enviar('Tercero')
        self.assertEqual(len(self.correos), 1)

    def test_pasada_la_ventana_sale_otro_correo_con_los_acumulados(self):
        self.enviar('Primero')
        self.avanzar(minutes=5)
        self.enviar('Segundo')
        self.avanzar(minutes=10)
        self.enviar('Tercero')
        self.avanzar(minutes=20)    # 35 min desde el primer correo
        self.enviar('Cuarto')
        self.assertEqual(len(self.correos), 2)
        textos = [t for _, t in self.correos[1][0][2]]
        self.assertEqual(textos, ['Segundo', 'Tercero', 'Cuarto'])

    def test_la_ventana_es_por_telefono(self):
        self.enviar('Hola', telefono='56911110001')
        self.enviar('Hola', telefono='56911110002')
        self.assertEqual(len(self.correos), 2)

    def test_el_telefono_se_reconoce_en_distintos_formatos(self):
        self.enviar('Uno', telefono='56912345678')
        self.enviar('Dos', telefono='+56 9 1234 5678')
        self.assertEqual(len(self.correos), 1)

    def test_lo_acumulado_sale_solo_aunque_el_paciente_no_vuelva(self):
        """Un mensaje importante dentro de la ventana no puede quedar sin avisar."""
        self.enviar('Hola')
        self.avanzar(minutes=2)
        self.enviar('Me duele mucho una muela')
        self.assertEqual(len(self.correos), 1)
        self.avanzar(minutes=10)
        self.assertEqual(mensajes_libres.enviar_vencidos(), 0)   # ventana aun abierta
        self.avanzar(minutes=25)
        self.assertEqual(mensajes_libres.enviar_vencidos(), 1)
        textos = [t for _, t in self.correos[1][0][2]]
        self.assertEqual(textos, ['Me duele mucho una muela'])
        # y no se manda dos veces
        self.assertEqual(mensajes_libres.enviar_vencidos(), 0)
        self.assertEqual(len(self.correos), 2)

    def test_el_barrido_corre_al_inicio_de_cada_evento_del_webhook(self):
        self.enviar('Hola', telefono='56911110001')
        self.avanzar(minutes=1)
        self.enviar('Urgente', telefono='56911110001')
        self.avanzar(minutes=40)
        # llega un evento de OTRO paciente: de paso se avisa lo acumulado del primero
        self.enviar('Hola', telefono='56911110002')
        telefonos = [c[0][1] for c in self.correos]
        self.assertEqual(sorted(telefonos), ['56911110001', '56911110001', '56911110002'])

    def test_registro_viejo_se_poda(self):
        self.enviar('Hola')
        self.avanzar(days=8)
        self.enviar('Hola', telefono='56911110002')
        claves = mensajes_libres._STORE.load()['telefonos']
        self.assertNotIn(mensajes_libres._clave_tel(TEL), claves)
        self.assertEqual(len(claves), 1)

    def test_tope_de_pendientes(self):
        self.enviar('Primero')
        for i in range(mensajes_libres.MAX_PENDIENTES + 10):
            self.enviar(f'spam {i}')
        e = mensajes_libres._STORE.load()['telefonos'][mensajes_libres._clave_tel(TEL)]
        self.assertEqual(len(e['pendientes']), mensajes_libres.MAX_PENDIENTES)


class TestAutoRespuesta(_Base):

    def test_fuera_de_horario_responde_una_vez(self):
        self.reloj[0] = FUERA_HORARIO
        self.enviar('Hola')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 1)
        tel, texto = self.notify.enviar_texto_libre.call_args[0]
        self.assertEqual(tel, TEL)
        self.assertIn('https://www.ortodonciarichard.cl/#agendar', texto)
        self.assertIn('lunes a viernes de 9:00 a 19:30 hrs', texto)

    def test_no_repite_dentro_de_12_horas(self):
        self.reloj[0] = FUERA_HORARIO
        self.enviar('Hola')
        self.avanzar(hours=1)
        self.enviar('Otra vez')
        self.avanzar(hours=10)
        self.enviar('Y otra')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 1)

    def test_pasadas_12_horas_vuelve_a_responder(self):
        self.reloj[0] = datetime(2026, 10, 7, 20, 0)
        self.enviar('Hola')
        self.avanzar(hours=11, minutes=50)      # 07:50 del jueves: aun sin abrir
        self.enviar('Hola?')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 1)
        self.avanzar(minutes=11)                # 08:01: 12 h y 1 min despues, aun fuera de horario
        self.enviar('Hola de nuevo')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 2)

    def test_en_horario_no_responde_nada(self):
        self.reloj[0] = EN_HORARIO
        self.enviar('Hola')
        self.notify.enviar_texto_libre.assert_not_called()
        self.assertEqual(len(self.correos), 1)    # pero el correo si sale

    def test_fin_de_semana_cuenta_como_fuera_de_horario(self):
        self.reloj[0] = FIN_DE_SEMANA
        self.enviar('Hola')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 1)

    def test_los_limites_del_horario(self):
        self.assertFalse(mensajes_libres.en_horario(CFG, datetime(2026, 10, 7, 8, 59)))
        self.assertTrue(mensajes_libres.en_horario(CFG, datetime(2026, 10, 7, 9, 0)))
        self.assertTrue(mensajes_libres.en_horario(CFG, datetime(2026, 10, 7, 19, 29)))
        self.assertFalse(mensajes_libres.en_horario(CFG, datetime(2026, 10, 7, 19, 30)))

    def test_sin_horario_en_el_config_usa_el_por_defecto(self):
        self.assertTrue(mensajes_libres.en_horario({}, EN_HORARIO))
        self.assertFalse(mensajes_libres.en_horario({}, FUERA_HORARIO))
        self.assertIn('lunes a viernes', mensajes_libres.texto_horario({}))

    def test_si_la_auto_respuesta_no_sale_se_reintenta_en_el_siguiente_mensaje(self):
        self.reloj[0] = FUERA_HORARIO
        self.notify.enviar_texto_libre.return_value = {'ok': False, 'error': 'x'}
        self.enviar('Hola')
        self.notify.enviar_texto_libre.return_value = {'ok': True}
        self.avanzar(minutes=1)
        self.enviar('Hola?')
        self.assertEqual(self.notify.enviar_texto_libre.call_count, 2)


class TestIgnorados(_Base):

    def test_eventos_de_status_se_ignoran(self):
        payload = {'entry': [{'changes': [{'field': 'messages', 'value': {
            'statuses': [{'id': 'wamid.X', 'status': 'delivered', 'recipient_id': TEL}]}}]}]}
        r = webhook_wa.procesar_evento(payload, CFG)
        self.assertEqual(r['procesados'], 0)
        self.assertEqual(len(self.correos), 0)
        self.notify.enviar_texto_libre.assert_not_called()

    def test_eco_de_la_propia_clinica_se_ignora(self):
        self.reloj[0] = FUERA_HORARIO
        # por el numero del config y por el del metadata del evento
        self.enviar('Hola', telefono='56900000001')
        self.enviar('Hola', telefono='56977770000',
                    metadata={'display_phone_number': '56977770000'})
        self.assertEqual(len(self.correos), 0)
        self.notify.enviar_texto_libre.assert_not_called()


class TestFallosNoRompenElWebhook(_Base):

    def test_falla_el_correo_con_excepcion(self):
        self.notify.avisar_recepcion_mensaje_libre.side_effect = RuntimeError('smtp caido')
        r = self.enviar('Hola')     # no lanza
        self.assertEqual(r['ok'], True)

    def test_falla_el_correo_devolviendo_false_y_se_reintenta_sin_perder_nada(self):
        self.notify.avisar_recepcion_mensaje_libre.return_value = False
        self.enviar('Primero')
        self.avanzar(minutes=1)
        self.notify.avisar_recepcion_mensaje_libre.return_value = True
        self.enviar('Segundo')                 # ventana abierta: no martilla el SMTP
        self.assertEqual(len(self.correos), 1)
        self.avanzar(minutes=31)
        self.assertEqual(mensajes_libres.enviar_vencidos(), 1)
        # el reintento lleva los DOS mensajes: nada se pierde
        textos = [t for _, t in self.correos[1][0][2]]
        self.assertEqual(textos, ['Primero', 'Segundo'])

    def test_falla_la_auto_respuesta_con_excepcion(self):
        self.reloj[0] = FUERA_HORARIO
        self.notify.enviar_texto_libre.side_effect = RuntimeError('meta caido')
        r = self.enviar('Hola')
        self.assertEqual(r['ok'], True)
        self.assertEqual(len(self.correos), 1)   # el correo igual salio

    def test_falla_la_busqueda_del_paciente(self):
        with mock.patch.object(mensajes_libres.pacientes, 'buscar_por_telefono',
                               side_effect=RuntimeError('indice roto')):
            self.enviar('Hola')
        self.assertEqual(len(self.correos), 1)

    def test_falla_el_registro_no_lanza(self):
        with mock.patch.object(mensajes_libres._STORE, 'actualizar',
                               side_effect=OSError('disco lleno')):
            r = self.enviar('Hola')
        self.assertEqual(r['ok'], True)
        self.assertEqual(len(self.correos), 0)

    def test_falla_el_barrido_no_lanza(self):
        with mock.patch.object(mensajes_libres._STORE, 'load',
                               side_effect=OSError('disco')):
            self.assertEqual(mensajes_libres.enviar_vencidos(), 0)


class TestCorreo(unittest.TestCase):
    """El HTML del correo: lo escrito por el paciente jamas se interpreta."""

    def test_el_texto_del_paciente_va_escapado_y_con_el_link(self):
        import notify
        capturado = {}
        with mock.patch.object(notify, '_enviar_email_recepcion',
                               side_effect=lambda a, h: capturado.update(asunto=a, html=h) or True):
            notify.avisar_recepcion_mensaje_libre(
                'Ana <b>X</b>\nBcc: x@y.z', '56912345678',
                [('07-10 11:00', '<script>alert(1)</script>\nsegunda linea')],
                registrado=False)
        self.assertNotIn('<script>', capturado['html'])
        self.assertIn('&lt;script&gt;', capturado['html'])
        self.assertIn('https://business.facebook.com/latest/inbox/whatsapp', capturado['html'])
        self.assertIn('número no registrado', capturado['html'])
        self.assertNotIn('\n', capturado['asunto'])        # sin inyeccion de cabeceras
        self.assertIn('responder en Meta Business Suite', capturado['asunto'])
        self.assertTrue(capturado['asunto'].startswith('WhatsApp: mensaje de '))


def estado_saliente(cuando, telefono=TEL, mid='wamid.RESP', status='sent'):
    """Lo que manda Meta cuando sale un mensaje del numero hacia el paciente."""
    ts = int(cuando.replace(tzinfo=mensajes_libres.fechas.TZ_CHILE).timestamp())
    return {'entry': [{'changes': [{'field': 'messages', 'value': {'statuses': [
        {'id': mid, 'status': status, 'timestamp': str(ts), 'recipient_id': telefono}]}}]}]}


class TestEsperaRespuesta(_Base):
    """Pedido del usuario: el correo llegaba antes de que recepcion alcanzara a
    contestar. Ahora sale solo si pasan 5 minutos sin que nadie le responda."""

    def setUp(self):
        super().setUp()
        mock.patch.object(mensajes_libres, 'ESPERA_RESPUESTA_MIN', 5).start()
        mensajes_libres.wa_cloud._IDS_PROPIOS.clear()

    def barrer(self):
        return mensajes_libres.enviar_vencidos()

    def test_sin_respuesta_el_correo_sale_a_los_5_minutos(self):
        self.enviar('Hola, tengo una duda')
        self.assertEqual(len(self.correos), 0)
        self.avanzar(minutes=4)
        self.barrer()
        self.assertEqual(len(self.correos), 0)
        self.avanzar(minutes=1)
        self.barrer()
        self.assertEqual(len(self.correos), 1)

    def test_si_recepcion_contesta_no_sale_correo(self):
        self.enviar('Hola, tengo una duda')
        self.avanzar(minutes=2)
        antes = mensajes_libres.respuestas_vistas()
        webhook_wa.procesar_evento(estado_saliente(self.reloj[0]), CFG)
        self.assertEqual(mensajes_libres.respuestas_vistas(), antes + 1)
        self.avanzar(minutes=10)
        self.barrer()
        self.assertEqual(len(self.correos), 0)

    def test_un_mensaje_automatico_nuestro_no_cuenta_como_respuesta(self):
        """La auto-respuesta, el 'gracias' de un boton, un recordatorio: los manda
        el sistema, no una persona. No pueden callar el aviso."""
        self.enviar('Hola, tengo una duda')
        mensajes_libres.wa_cloud._anotar_propio('wamid.BOT')
        self.avanzar(minutes=1)
        webhook_wa.procesar_evento(estado_saliente(self.reloj[0], mid='wamid.BOT'), CFG)
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 1)

    def test_lo_que_escribe_despues_de_la_respuesta_vuelve_a_esperar(self):
        self.enviar('Primera pregunta')
        self.avanzar(minutes=1)
        webhook_wa.procesar_evento(estado_saliente(self.reloj[0]), CFG)
        self.avanzar(minutes=1)
        self.enviar('Y otra cosa mas')
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 1)
        textos = [t for _, t in self.correos[0][0][2]]
        self.assertEqual(textos, ['Y otra cosa mas'])

    def test_un_aviso_atrasado_de_una_respuesta_vieja_no_despeja_lo_nuevo(self):
        """Meta puede entregar tarde el 'sent' de una respuesta anterior: manda la
        hora en que salio ese mensaje, no la hora en que llego el aviso."""
        hace_rato = self.reloj[0] - timedelta(minutes=20)
        self.enviar('Hola, tengo una duda')
        webhook_wa.procesar_evento(estado_saliente(hace_rato), CFG)
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 1)

    def test_solo_cuenta_el_sent(self):
        """'delivered' y 'read' de nuestros propios mensajes llegan despues y no
        son una respuesta nueva."""
        self.enviar('Hola, tengo una duda')
        self.avanzar(minutes=1)
        webhook_wa.procesar_evento(estado_saliente(self.reloj[0], status='read'), CFG)
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 1)

    def test_el_eco_de_la_app_tambien_cuenta_como_respuesta(self):
        self.enviar('Hola, tengo una duda')
        self.avanzar(minutes=1)
        ts = int(self.reloj[0].replace(tzinfo=mensajes_libres.fechas.TZ_CHILE).timestamp())
        webhook_wa.procesar_evento({'entry': [{'changes': [{'value': {'smb_message_echoes': [
            {'from': '56900000001', 'to': TEL, 'id': 'wamid.ECO', 'timestamp': str(ts),
             'type': 'text', 'text': {'body': 'Hola, le ayudo'}}]}}]}]}, CFG)
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 0)

    def test_la_respuesta_a_otro_paciente_no_despeja_este(self):
        self.enviar('Hola, tengo una duda')
        self.avanzar(minutes=1)
        webhook_wa.procesar_evento(estado_saliente(self.reloj[0], telefono='56977770000'), CFG)
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(len(self.correos), 1)

    def test_el_nombre_de_perfil_llega_al_correo_aunque_salga_despues(self):
        self.enviar('Hola', perfil='Juan Inventado')
        self.avanzar(minutes=5)
        self.barrer()
        self.assertEqual(self.correos[0][0][0], 'Juan Inventado')

    def test_lo_que_manda_wa_cloud_queda_anotado_como_propio(self):
        mensajes_libres.wa_cloud._anotar_propio('wamid.A')
        self.assertTrue(mensajes_libres.wa_cloud.es_mensaje_propio('wamid.A'))
        self.assertFalse(mensajes_libres.wa_cloud.es_mensaje_propio('wamid.B'))
        self.assertFalse(mensajes_libres.wa_cloud.es_mensaje_propio(None))


if __name__ == '__main__':
    unittest.main(verbosity=2)
