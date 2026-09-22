"""
test_llamadas.py - Llamadas de WhatsApp que nadie puede contestar.

Cero red, cero WhatsApp, cero correo: todo interceptado.

    cd admin && python test_llamadas.py

Por que se prueba: el numero de la clinica vive en la Cloud API y NO tiene
telefono donde suene. Lo unico que evita que una llamada se pierda en el vacio
es esta cadena -- rechazar, contestar por escrito, avisarle a recepcion. Si se
rompe en silencio, el sintoma para el paciente es identico al problema que esto
vino a resolver: llama, no pasa nada, nadie se entera.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='llamadas_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['LLAMADAS_REGISTRO_PATH'] = str(_TMP / 'llamadas_registro.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import llamadas_perdidas   # noqa: E402
import pacientes           # noqa: E402
import wa_cloud            # noqa: E402
import webhook_wa          # noqa: E402

CFG = {'dentidesk': {}}

TEL = '56912345678'


def evento_llamada(event='connect', call_id='wacid.ABC', telefono=TEL,
                   incluir_from=True, perfil=None):
    """Arma el POST del webhook 'calls' tal como lo manda Meta."""
    contacts = [{'wa_id': telefono, 'profile': {'name': perfil}}] if perfil else []
    llamada = {'id': call_id, 'event': event, 'timestamp': '1671644824'}
    if incluir_from:
        llamada['from'] = telefono
    return {'entry': [{'changes': [{
        'field': 'calls',
        'value': {'contacts': contacts, 'calls': [llamada]},
    }]}]}


class _Base(unittest.TestCase):

    def setUp(self):
        llamadas_perdidas.vaciar()
        pacientes.vaciar()
        self.notify = mock.patch.object(webhook_wa, 'notify').start()
        self.wa = mock.patch.object(webhook_wa, 'wa_cloud').start()
        # El modulo mockeado tiene que exponer la excepcion REAL: webhook_wa la
        # usa en un `except`, y un Mock ahi revienta con TypeError.
        self.wa.WhatsAppCloudError = wa_cloud.WhatsAppCloudError
        self.addCleanup(mock.patch.stopall)

    def _sembrar_paciente(self, rut='11111111K', nombres='Maria',
                          apellidos='Perez', telefono='+56 9 1234 5678',
                          genero='F'):
        idx = pacientes._load_index()
        idx[rut] = {'nombres': nombres, 'apellidos': apellidos,
                    'telefono': telefono, 'genero': genero}
        pacientes._save_index(idx)


class TestCadenaCompleta(_Base):

    def test_connect_rechaza_responde_y_avisa(self):
        self._sembrar_paciente()
        r = webhook_wa.procesar_evento(evento_llamada(), CFG)

        self.assertEqual(r['procesados'], 1)
        self.wa.rechazar_llamada.assert_called_once_with('wacid.ABC')
        self.notify.responder_llamada_perdida.assert_called_once()
        self.notify.avisar_recepcion_llamada_perdida.assert_called_once()

    def test_el_paciente_se_identifica_por_telefono(self):
        """El RUT es lo que hace que la llamada llegue a clinica.db, y el
        nombre lo que permite saludarlo. Ambos salen SOLO del telefono."""
        self._sembrar_paciente(rut='11111111K', nombres='Maria')
        webhook_wa.procesar_evento(evento_llamada(), CFG)

        _, kwargs = self.notify.responder_llamada_perdida.call_args
        self.assertEqual(kwargs['nombre'], 'Maria')
        self.assertEqual(kwargs['rut'], '11111111K')

    def test_desconocido_igual_se_responde_y_se_avisa(self):
        """Quien llama puede no estar en la base (numero nuevo, familiar).
        Perder ese contacto seria peor que no saber su nombre."""
        r = webhook_wa.procesar_evento(evento_llamada(), CFG)

        self.assertEqual(r['procesados'], 1)
        self.notify.responder_llamada_perdida.assert_called_once()
        args, _ = self.notify.avisar_recepcion_llamada_perdida.call_args
        self.assertEqual(args[1], TEL)

    def test_si_falla_el_rechazo_igual_contesta(self):
        """Cortar es lo cosmetico; el texto y el aviso son lo que importa."""
        self.wa.rechazar_llamada.side_effect = wa_cloud.WhatsAppCloudError('token vencido')
        r = webhook_wa.procesar_evento(evento_llamada(), CFG)

        self.assertEqual(r['procesados'], 1)
        self.notify.responder_llamada_perdida.assert_called_once()
        self.notify.avisar_recepcion_llamada_perdida.assert_called_once()


class TestQueNoSeDuplica(_Base):

    def test_terminate_no_gatilla_nada(self):
        """El 'terminate' describe la MISMA llamada que el 'connect' y llega
        justo despues. Atenderlo mandaria el texto dos veces."""
        r = webhook_wa.procesar_evento(evento_llamada(event='terminate'), CFG)

        self.assertEqual(r['procesados'], 0)
        self.notify.responder_llamada_perdida.assert_not_called()
        self.wa.rechazar_llamada.assert_not_called()

    def test_insistir_no_manda_el_texto_de_nuevo(self):
        webhook_wa.procesar_evento(evento_llamada(call_id='a'), CFG)
        webhook_wa.procesar_evento(evento_llamada(call_id='b'), CFG)
        webhook_wa.procesar_evento(evento_llamada(call_id='c'), CFG)

        self.assertEqual(self.notify.responder_llamada_perdida.call_count, 1)
        self.assertEqual(self.notify.avisar_recepcion_llamada_perdida.call_count, 1)

    def test_pero_las_tres_quedan_registradas(self):
        """Cuantas veces insistio es la senial de urgencia: no se pierde."""
        for cid in ('a', 'b', 'c'):
            webhook_wa.procesar_evento(evento_llamada(call_id=cid), CFG)

        self.assertEqual(llamadas_perdidas.total(), 3)
        self.assertEqual(self.wa.rechazar_llamada.call_count, 3)

    def test_pasada_la_ventana_se_responde_de_nuevo(self):
        webhook_wa.procesar_evento(evento_llamada(call_id='a'), CFG)
        with mock.patch.object(llamadas_perdidas, 'VENTANA_MINUTOS', 0):
            webhook_wa.procesar_evento(evento_llamada(call_id='b'), CFG)

        self.assertEqual(self.notify.responder_llamada_perdida.call_count, 2)

    def test_el_segundo_aviso_dice_que_insistio(self):
        webhook_wa.procesar_evento(evento_llamada(call_id='a'), CFG)
        with mock.patch.object(llamadas_perdidas, 'VENTANA_MINUTOS', 0):
            webhook_wa.procesar_evento(evento_llamada(call_id='b'), CFG)

        _, kwargs = self.notify.avisar_recepcion_llamada_perdida.call_args
        self.assertTrue(kwargs['repeticion'])


class TestPayloadRaro(_Base):

    def test_telefono_sale_de_contacts_si_no_viene_from(self):
        """Meta documenta al que llama en contacts[].wa_id; `from` aparece en
        unas versiones del payload y en otras no."""
        r = webhook_wa.procesar_evento(
            evento_llamada(incluir_from=False, perfil='Juan'), CFG)

        self.assertEqual(r['procesados'], 1)
        args, _ = self.notify.avisar_recepcion_llamada_perdida.call_args
        self.assertEqual(args[1], TEL)

    def test_sin_telefono_no_revienta(self):
        r = webhook_wa.procesar_evento(
            evento_llamada(incluir_from=False), CFG)

        self.assertEqual(r['procesados'], 0)
        self.notify.responder_llamada_perdida.assert_not_called()

    def test_una_llamada_rota_no_tumba_el_resto_del_post(self):
        """Regla del webhook: un item malformado no puede dejar sin procesar
        lo que venia en el mismo POST."""
        payload = evento_llamada()
        payload['entry'][0]['changes'][0]['value']['calls'].insert(0, None)

        r = webhook_wa.procesar_evento(payload, CFG)
        self.assertEqual(r['procesados'], 1)

    def test_mensajes_y_llamadas_en_el_mismo_post(self):
        """Un POST puede traer las dos cosas: la llamada no puede dejar sin
        procesar la confirmacion de cita."""
        payload = evento_llamada()
        payload['entry'][0]['changes'][0]['value']['messages'] = [
            {'type': 'button', 'from': TEL,
             'button': {'text': 'Confirmo', 'payload': 'semana:123:2026-08-03'}}]

        with mock.patch.object(webhook_wa, 'dentidesk'):
            r = webhook_wa.procesar_evento(payload, {'dentidesk': {
                'id_status_confirmado_semana': 40968}})
        self.assertEqual(r['procesados'], 2)


class TestBusquedaPorTelefono(unittest.TestCase):
    """El unico dato que trae una llamada es el numero. Si no calza con la
    ficha, el paciente queda como 'No identificado' y el evento sin RUT."""

    def setUp(self):
        pacientes.vaciar()

    def _sembrar(self, rut, telefono):
        idx = pacientes._load_index()
        idx[rut] = {'nombres': 'Ana', 'apellidos': 'Soto', 'telefono': telefono}
        pacientes._save_index(idx)

    def test_calza_aunque_este_escrito_distinto(self):
        for escrito in ('+56 9 1234 5678', '912345678', '56912345678',
                        '9 1234 5678', '12345678'):
            with self.subTest(escrito=escrito):
                pacientes.vaciar()
                self._sembrar('11111111K', escrito)
                self.assertEqual(
                    (pacientes.buscar_por_telefono(TEL) or {}).get('rut'),
                    '11111111K')

    def test_telefono_ambiguo_devuelve_none(self):
        """Madre e hijo con el mismo numero: saludar con el nombre equivocado
        es peor que no saludar."""
        self._sembrar('11111111K', '+56 9 1234 5678')
        self._sembrar('22222222K', '912345678')
        self.assertIsNone(pacientes.buscar_por_telefono(TEL))

    def test_numero_corto_o_vacio_no_calza_con_cualquiera(self):
        self._sembrar('11111111K', '+56 9 1234 5678')
        for malo in ('', None, '123'):
            with self.subTest(malo=malo):
                self.assertIsNone(pacientes.buscar_por_telefono(malo))


class TestRegistroYBaseDeDatos(_Base):

    def test_el_evento_llega_a_la_base(self):
        """Regla 9: un sistema sin adaptador queda mudo para cualquier
        analisis futuro."""
        import clinico
        self._sembrar_paciente()
        webhook_wa.procesar_evento(evento_llamada(), CFG)

        filas = list(clinico._ev_llamadas_perdidas())
        self.assertEqual(len(filas), 1)
        ref, rut, fecha, tipo, estado, datos = filas[0]
        self.assertEqual(ref, 'wacid.ABC')
        self.assertEqual(rut, '11111111K')
        self.assertEqual(tipo, 'llamada_perdida')
        self.assertEqual(estado, 'respondido')
        self.assertEqual(datos['telefono'], TEL)

    def test_el_telefono_va_en_datos_aunque_no_haya_rut(self):
        """Sin RUT, el numero es el UNICO rastro de ese contacto."""
        import clinico
        webhook_wa.procesar_evento(evento_llamada(), CFG)

        _, rut, _, _, _, datos = list(clinico._ev_llamadas_perdidas())[0]
        self.assertEqual(rut, '')
        self.assertEqual(datos['telefono'], TEL)

    def test_el_registro_no_crece_sin_techo(self):
        with mock.patch.object(llamadas_perdidas, 'MAX_REGISTRO', 5):
            for i in range(12):
                llamadas_perdidas.registrar('5691111111%d' % (i % 10),
                                            call_id='c%d' % i)
        self.assertEqual(llamadas_perdidas.total(), 5)

    def test_registro_corrupto_no_deja_mudo_al_paciente(self):
        """Ante una fecha ilegible se responde igual: es mejor un mensaje de
        mas que dejar a alguien esperando."""
        llamadas_perdidas.registrar(TEL, call_id='a')
        reg = llamadas_perdidas._cargar()
        reg['llamadas'][0]['cuando'] = 'no-es-una-fecha'
        llamadas_perdidas._STORE.save(reg)

        self.assertTrue(llamadas_perdidas.debe_responder(TEL))


if __name__ == '__main__':
    unittest.main(verbosity=2)
