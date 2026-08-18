"""
test_psq.py - Cuestionario de sueño pediátrico (PSQ-CL): puntaje, validacion
y resolucion del destinatario (doctor tratante / recepcion).

Cero red: DentiDesk queda deshabilitado y el registro escribe en un tempfile.

    cd admin && python test_psq.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='psq_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['PSQ_REGISTRO_PATH'] = str(_TMP / 'psq_registro.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import psq  # noqa: E402

RUT = '17.406.985-9'


def _respuestas(si_no='no', frecuencia='nunca', **overrides):
    """Arma un dict de 22 respuestas, todas iguales salvo los overrides."""
    r = {}
    for p in psq.PREGUNTAS:
        r[p['id']] = frecuencia if p['tipo'] == 'frecuencia' else si_no
    r.update(overrides)
    return r


class TestValidarRespuestas(unittest.TestCase):

    def test_completo_y_valido_pasa(self):
        self.assertEqual(psq.validar_respuestas(_respuestas()), '')

    def test_no_es_dict(self):
        self.assertTrue(psq.validar_respuestas(None))
        self.assertTrue(psq.validar_respuestas('nope'))

    def test_faltan_preguntas(self):
        r = _respuestas()
        del r['p5']
        del r['p22']
        err = psq.validar_respuestas(r)
        self.assertIn('2', err)

    def test_valor_invalido_si_no(self):
        r = _respuestas(p1='tal_vez')
        self.assertTrue(psq.validar_respuestas(r))

    def test_valor_invalido_frecuencia(self):
        r = _respuestas(p17='si')  # 'si' no es una opcion de frecuencia
        self.assertTrue(psq.validar_respuestas(r))

    def test_no_se_es_valido_en_si_no(self):
        r = _respuestas(p1='no_se')
        self.assertEqual(psq.validar_respuestas(r), '')

    def test_no_se_no_es_valido_en_frecuencia(self):
        # La seccion C (frecuencia) no tiene opcion 'no se' en el formulario.
        r = _respuestas(p17='no_se')
        self.assertTrue(psq.validar_respuestas(r))


class TestCalcularRiesgo(unittest.TestCase):

    def test_todo_negativo_puntaje_cero_riesgo_bajo(self):
        res = psq.calcular_riesgo(_respuestas(si_no='no', frecuencia='nunca'))
        self.assertEqual(res['puntaje'], 0.0)
        self.assertEqual(res['contestadas'], 22)
        self.assertEqual(res['positivas'], 0)
        self.assertEqual(res['riesgo'], 'bajo')

    def test_todo_positivo_puntaje_uno_riesgo_alto(self):
        res = psq.calcular_riesgo(_respuestas(si_no='si', frecuencia='casi_siempre'))
        self.assertEqual(res['puntaje'], 1.0)
        self.assertEqual(res['positivas'], 22)
        self.assertEqual(res['riesgo'], 'alto')

    def test_no_se_no_cuenta_en_el_denominador(self):
        # Todas 'no_se' en la seccion A/B (16 items) + 'nunca' en C (6 items):
        # 0 contestadas de A/B, 6 de C, 0 positivas -> puntaje 0, no division por cero.
        r = _respuestas(si_no='no_se', frecuencia='nunca')
        res = psq.calcular_riesgo(r)
        self.assertEqual(res['contestadas'], 6)
        self.assertEqual(res['positivas'], 0)
        self.assertEqual(res['puntaje'], 0.0)

    def test_frecuencia_muchas_veces_y_casi_siempre_cuentan_como_positivo(self):
        r = _respuestas(si_no='no', frecuencia='nunca',
                        p17='muchas_veces', p18='casi_siempre', p19='algunas_veces')
        res = psq.calcular_riesgo(r)
        # p17 y p18 positivos; p19 ('algunas_veces') NO cuenta como positivo.
        self.assertEqual(res['positivas'], 2)
        self.assertEqual(res['contestadas'], 22)

    def test_puntaje_sobre_el_corte_da_riesgo_alto(self):
        # Corte 0.227: con 6 de 22 positivas (0.2727...) debe quedar 'alto'.
        overrides = {f'p{i}': 'si' for i in range(1, 7)}
        r = _respuestas(si_no='no', frecuencia='nunca', **overrides)
        res = psq.calcular_riesgo(r)
        self.assertGreater(res['puntaje'], psq.PUNTAJE_CORTE)
        self.assertEqual(res['riesgo'], 'alto')

    def test_puntaje_bajo_el_corte_da_riesgo_bajo(self):
        overrides = {f'p{i}': 'si' for i in range(1, 5)}  # 4/22 = 0.1818
        r = _respuestas(si_no='no', frecuencia='nunca', **overrides)
        res = psq.calcular_riesgo(r)
        self.assertLess(res['puntaje'], psq.PUNTAJE_CORTE)
        self.assertEqual(res['riesgo'], 'bajo')

    def test_detalle_trae_las_22_preguntas_en_orden(self):
        res = psq.calcular_riesgo(_respuestas())
        ids = [d['id'] for d in res['detalle']]
        self.assertEqual(ids, [p['id'] for p in psq.PREGUNTAS])

    def test_detalle_marca_no_se_como_positiva_none(self):
        res = psq.calcular_riesgo(_respuestas(p1='no_se'))
        item = next(d for d in res['detalle'] if d['id'] == 'p1')
        self.assertIsNone(item['positiva'])


class TestResolverDestinatario(unittest.TestCase):

    def setUp(self):
        self._env_orig = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env_orig)

    def test_sin_doctor_encontrado_cae_a_recepcion(self):
        cfg = {'dentidesk': {'enabled': False}, 'doctores': {}}
        with mock.patch('dentidesk.doctor_de_paciente', return_value=''):
            email, doc_key, motivo = psq.resolver_destinatario(RUT, cfg)
        self.assertEqual(email, psq.EMAIL_RESPALDO)
        self.assertEqual(doc_key, '')
        self.assertEqual(motivo, 'sin_doctor')

    def test_doctor_encontrado_sin_email_configurado_cae_a_recepcion(self):
        os.environ.pop('EMAIL_ALBERTO', None)
        cfg = {'dentidesk': {'enabled': False}, 'doctores': {}}
        with mock.patch('dentidesk.doctor_de_paciente', return_value='alberto'):
            email, doc_key, motivo = psq.resolver_destinatario(RUT, cfg)
        self.assertEqual(email, psq.EMAIL_RESPALDO)
        self.assertEqual(doc_key, 'alberto')
        self.assertEqual(motivo, 'sin_email')

    def test_doctor_encontrado_con_email_configurado(self):
        os.environ['EMAIL_ALBERTO'] = 'alberto@example.com'
        cfg = {'dentidesk': {'enabled': False}, 'doctores': {}}
        with mock.patch('dentidesk.doctor_de_paciente', return_value='alberto'):
            email, doc_key, motivo = psq.resolver_destinatario(RUT, cfg)
        self.assertEqual(email, 'alberto@example.com')
        self.assertEqual(doc_key, 'alberto')
        self.assertEqual(motivo, 'doctor')

    def test_excepcion_al_resolver_doctor_no_revienta(self):
        cfg = {'dentidesk': {'enabled': False}, 'doctores': {}}
        with mock.patch('dentidesk.doctor_de_paciente', side_effect=RuntimeError('boom')):
            email, doc_key, motivo = psq.resolver_destinatario(RUT, cfg)
        self.assertEqual(email, psq.EMAIL_RESPALDO)
        self.assertEqual(motivo, 'sin_doctor')


class TestRegistro(unittest.TestCase):

    def setUp(self):
        psq._STORE.save({'envios': {}})

    def test_guardar_y_listar(self):
        psq.guardar_envio('id1', {'id': 'id1', 'fecha_iso': '2026-01-01T10:00:00'})
        psq.guardar_envio('id2', {'id': 'id2', 'fecha_iso': '2026-02-01T10:00:00'})
        items = psq.listar_envios()
        self.assertEqual([i['id'] for i in items], ['id2', 'id1'])  # mas reciente primero

    def test_actualizar_envio(self):
        psq.guardar_envio('id1', {'id': 'id1', 'fecha_iso': '2026-01-01', 'estado': 'pendiente'})
        psq.actualizar_envio('id1', estado='enviado', destinatario='x@y.cl')
        item = psq._STORE.load()['envios']['id1']
        self.assertEqual(item['estado'], 'enviado')
        self.assertEqual(item['destinatario'], 'x@y.cl')

    def test_actualizar_envio_inexistente_no_revienta(self):
        psq.actualizar_envio('no-existe', estado='enviado')  # no debe lanzar


if __name__ == '__main__':
    unittest.main(verbosity=2)
