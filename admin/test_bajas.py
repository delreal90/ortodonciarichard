"""
test_bajas.py - La baja general de avisos (derecho de oposicion, Ley 21.719).

Cero red / cero WhatsApp / cero correo. Todos los RUT y nombres son inventados.

    cd admin && python test_bajas.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='bajas_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import bajas            # noqa: E402
import avisos           # noqa: E402
import server           # noqa: E402
import webhook_wa       # noqa: E402

RUT = '12.345.678-5'
OTRO = '11.111.111-1'


def _limpiar():
    for it in bajas.listar():
        bajas.quitar(it['rut'])


class TestLista(unittest.TestCase):
    def setUp(self):
        _limpiar()

    def test_agregar_contiene_y_quitar(self):
        self.assertFalse(bajas.contiene(RUT))
        bajas.agregar(RUT, 'correo')
        self.assertTrue(bajas.contiene('12345678-5'))      # cualquier formato
        self.assertTrue(bajas.contiene('123456785'))
        self.assertFalse(bajas.contiene(OTRO))
        bajas.quitar(RUT)
        self.assertFalse(bajas.contiene(RUT))

    def test_la_primera_baja_conserva_su_fecha_y_origen(self):
        bajas.agregar(RUT, 'whatsapp')
        bajas.agregar(RUT, 'panel')
        self.assertEqual(bajas.listar()[0]['origen'], 'whatsapp')

    def test_rut_vacio_no_entra(self):
        self.assertFalse(bajas.agregar('', 'panel'))
        self.assertEqual(bajas.listar(), [])


class TestTodosLosSistemasLaRespetan(unittest.TestCase):
    """El punto entero: la baja general se mira DENTRO de ListaNoMolestar, asi
    que los cinco sistemas la respetan sin tocar su codigo."""

    def setUp(self):
        _limpiar()

    def test_lista_de_un_sistema_ve_la_baja_general(self):
        import threading
        reg = {}
        lista = avisos.ListaNoMolestar(lambda: reg, reg.update, threading.Lock())
        self.assertFalse(lista.contiene(RUT))
        bajas.agregar(RUT, 'correo')
        self.assertTrue(lista.contiene(RUT))
        bloqueo = lista.guarda('pidio no recibir avisos')(RUT)
        self.assertEqual(bloqueo['motivo'], 'no_molestar')
        self.assertFalse(bloqueo['puede_forzar'])           # el opt-out nunca se fuerza
        self.assertEqual(lista.listar(), [])                # la lista PROPIA no cambia

    def test_los_cinco_sistemas(self):
        import control_dental, nps, reactivacion, recaptacion, seguimiento_pc
        bajas.agregar(RUT, 'whatsapp')
        for mod in (control_dental, nps, reactivacion, recaptacion, seguimiento_pc):
            self.assertTrue(mod._NO_MOLESTAR.contiene(RUT), mod.__name__)
            self.assertFalse(mod._NO_MOLESTAR.contiene(OTRO), mod.__name__)


class TestEnlaceDelCorreo(unittest.TestCase):
    def setUp(self):
        _limpiar()
        self.client = server.app.test_client()
        if server.limiter:
            server.limiter.reset()

    def test_token_no_lleva_el_rut_a_la_vista_y_vuelve_al_mismo(self):
        tok = bajas.crear_token(RUT)
        self.assertEqual(bajas.rut_de_token(tok), '123456785')
        self.assertEqual(bajas.rut_de_token(tok + 'x'), '')
        self.assertEqual(bajas.rut_de_token(''), '')

    def test_abrir_la_pagina_no_da_de_baja(self):
        """Los correos pre-abren los enlaces: la baja es siempre un boton."""
        r = self.client.get('/baja?token=' + bajas.crear_token(RUT))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(bajas.contiene(RUT))

    def test_el_boton_da_de_baja(self):
        r = self.client.post('/api/baja', json={'token': bajas.crear_token(RUT)})
        self.assertTrue(r.get_json()['ok'])
        self.assertTrue(bajas.contiene(RUT))
        self.assertEqual(bajas.listar()[0]['origen'], 'correo')

    def test_token_inventado_no_da_de_baja_a_nadie(self):
        r = self.client.post('/api/baja', json={'token': 'basura'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(bajas.listar(), [])

    def test_el_correo_de_control_dental_trae_el_enlace(self):
        import notify
        html = notify._html_control_dental('Ana', 'a', 6, 'https://ejemplo/baja?token=abc')
        self.assertIn('Darme de baja', html)
        self.assertIn('https://ejemplo/baja?token=abc', html)
        self.assertNotIn('Darme de baja', notify._html_control_dental('Ana', 'a', 6))


class TestBajaPorWhatsApp(unittest.TestCase):
    def setUp(self):
        _limpiar()

    def test_solo_el_mensaje_entero_es_una_orden(self):
        for si in ('BAJA', 'baja.', ' Stop ', 'No más mensajes', 'no quiero mas mensajes'):
            self.assertTrue(bajas.es_pedido_de_baja(si), si)
        for no in ('', 'hola', 'me dieron de baja del seguro, ¿puedo ir igual?',
                   'no quiero perder mi hora', 'stop de bracket suelto'):
            self.assertFalse(bajas.es_pedido_de_baja(no), no)

    def _msg(self, texto):
        return {'type': 'text', 'from': '56911112222', 'text': {'body': texto}}

    def test_paciente_conocido_queda_de_baja_y_se_le_confirma(self):
        with mock.patch.object(webhook_wa.pacientes, 'buscar_por_telefono',
                               return_value={'rut': RUT, 'nombres': 'Ana'}), \
             mock.patch.object(webhook_wa.notify, 'enviar_texto_libre') as enviar:
            self.assertTrue(webhook_wa._baja_por_texto(self._msg('BAJA')))
        self.assertTrue(bajas.contiene(RUT))
        enviar.assert_called_once()

    def test_telefono_desconocido_no_adivina_y_lo_ve_recepcion(self):
        with mock.patch.object(webhook_wa.pacientes, 'buscar_por_telefono', return_value=None), \
             mock.patch.object(webhook_wa.notify, 'enviar_texto_libre') as enviar:
            self.assertFalse(webhook_wa._baja_por_texto(self._msg('BAJA')))
        self.assertEqual(bajas.listar(), [])
        enviar.assert_not_called()

    def test_un_mensaje_normal_no_se_toca(self):
        with mock.patch.object(webhook_wa.pacientes, 'buscar_por_telefono') as buscar:
            self.assertFalse(webhook_wa._baja_por_texto(self._msg('Hola, ¿tienen hora mañana?')))
        buscar.assert_not_called()


class TestEvidenciaDeAceptacion(unittest.TestCase):
    """La casilla de datos de la agenda online queda guardada en el servidor."""

    def test_registra_quien_cuando_y_que_version(self):
        import aceptaciones
        self.assertTrue(aceptaciones.registrar(RUT, 'agenda_online', 'agenda-2026-08', ref='999'))
        fila = aceptaciones.de_rut('12345678-5')[-1]
        self.assertEqual((fila['origen'], fila['version'], fila['ref'], fila['aceptado']),
                         ('agenda_online', 'agenda-2026-08', '999', True))
        self.assertTrue(fila['fecha_iso'])

    def test_una_reserva_sin_casilla_queda_a_la_vista_no_se_pierde(self):
        import aceptaciones
        server._anotar_aceptacion(OTRO, {}, '1000')
        self.assertFalse(aceptaciones.de_rut(OTRO)[-1]['aceptado'])


class TestPanel(unittest.TestCase):
    def setUp(self):
        _limpiar()
        self.client = server.app.test_client()
        self._tok = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'llave-de-prueba'

    def tearDown(self):
        if self._tok is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._tok

    def test_exige_llave(self):
        self.assertEqual(self.client.get('/api/bajas').status_code, 403)

    def test_agregar_y_deshacer(self):
        h = {'X-Admin-Token': 'llave-de-prueba'}
        r = self.client.post('/api/bajas', json={'rut': RUT}, headers=h)
        self.assertEqual(len(r.get_json()['bajas']), 1)
        r = self.client.post('/api/bajas', json={'rut': RUT, 'quitar': True}, headers=h)
        self.assertEqual(r.get_json()['bajas'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
