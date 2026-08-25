"""
test_stopbang.py - STOP-BANG: tamizaje de apnea obstructiva del sueno en
adultos (el equivalente adulto del PSQ-CL pediatrico de psq.py).

Cero red y cero disco: el modulo solo recibe respuestas y calcula.

    cd admin && python test_stopbang.py

Lo que se protege:

  - Los cuatro umbrales medidos (IMC > 35, edad > 50, cuello > 40 cm, sexo M).
    Se prueban por ambos lados: un ">" que se vuelve ">=" cambia el puntaje de
    pacientes reales sin que nadie lo note.
  - Que un item SIN MEDIR no se cuente como negativo. Tres de los ocho items no
    son preguntas sino mediciones (IMC, cuello, edad) y el cuello hay que sacarlo
    con huincha: leer un 2/8 incompleto como "riesgo bajo" es justamente el error
    que este modulo existe para no cometer.
  - Que imc() no adivine con datos ausentes o absurdos.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import stopbang  # noqa: E402


def _completo(**overrides):
    """Las 8 respuestas, todas negativas salvo los overrides. Sirve para
    aislar un item a la vez y para tener un caso COMPLETO (sin sin_registrar)."""
    r = {'ronquido': 'no', 'cansancio': 'no', 'apneas': 'no', 'presion': 'no',
         'imc': 22, 'edad': 30, 'cuello': 35, 'sexo': 'F'}
    r.update(overrides)
    return r


class TestPuntajeYBandas(unittest.TestCase):

    def test_todo_negativo_es_cero_y_riesgo_bajo(self):
        res = stopbang.evaluar(_completo())
        self.assertEqual(res['puntaje'], 0)
        self.assertEqual(res['contestados'], 8)
        self.assertEqual(res['banda'], 'bajo')
        self.assertFalse(res['incompleto'])

    def test_todo_positivo_es_ocho_y_riesgo_alto(self):
        res = stopbang.evaluar(_completo(ronquido='si', cansancio='si', apneas='si',
                                         presion='si', imc=40, edad=60, cuello=44, sexo='M'))
        self.assertEqual(res['puntaje'], 8)
        self.assertEqual(res['banda'], 'alto')

    def test_bandas_0_a_2_bajo(self):
        for p in (0, 1, 2):
            self.assertEqual(stopbang.banda(p), 'bajo')

    def test_bandas_3_y_4_intermedio(self):
        for p in (3, 4):
            self.assertEqual(stopbang.banda(p), 'intermedio')

    def test_bandas_5_o_mas_alto(self):
        for p in (5, 6, 7, 8):
            self.assertEqual(stopbang.banda(p), 'alto')

    def test_la_banda_del_resultado_sigue_al_puntaje(self):
        res = stopbang.evaluar(_completo(ronquido='si', cansancio='si', apneas='si'))
        self.assertEqual(res['puntaje'], 3)
        self.assertEqual(res['banda'], 'intermedio')


class TestUmbrales(unittest.TestCase):
    """Los cuatro items que no son si/no. Cada uno se prueba justo por encima y
    justo por debajo de su corte."""

    def _puntaje(self, **ov):
        return stopbang.evaluar(_completo(**ov))['puntaje']

    def test_imc_umbral_35(self):
        self.assertEqual(stopbang.IMC_UMBRAL, 35)
        self.assertEqual(self._puntaje(imc=35.1), 1)
        self.assertEqual(self._puntaje(imc=35), 0)     # el corte es ">", no ">="
        self.assertEqual(self._puntaje(imc=34.9), 0)

    def test_edad_umbral_50(self):
        self.assertEqual(stopbang.EDAD_UMBRAL, 50)
        self.assertEqual(self._puntaje(edad=51), 1)
        self.assertEqual(self._puntaje(edad=50), 0)
        self.assertEqual(self._puntaje(edad=49), 0)

    def test_cuello_umbral_40_cm(self):
        self.assertEqual(stopbang.CUELLO_UMBRAL_CM, 40)
        self.assertEqual(self._puntaje(cuello=40.5), 1)
        self.assertEqual(self._puntaje(cuello=40), 0)
        self.assertEqual(self._puntaje(cuello=39.5), 0)

    def test_sexo_masculino_suma(self):
        self.assertEqual(self._puntaje(sexo='M'), 1)
        self.assertEqual(self._puntaje(sexo='m'), 1)
        self.assertEqual(self._puntaje(sexo='F'), 0)

    def test_valores_no_numericos_no_cuentan_como_negativos(self):
        res = stopbang.evaluar(_completo(cuello='no me lo midieron'))
        self.assertEqual(res['puntaje'], 0)
        self.assertIn('cuello', res['sin_registrar'])
        self.assertTrue(res['incompleto'])


class TestImc(unittest.TestCase):

    def test_calculo_normal(self):
        self.assertEqual(stopbang.imc(70, 170), 24.2)

    def test_datos_ausentes_devuelven_none(self):
        self.assertIsNone(stopbang.imc(None, 170))
        self.assertIsNone(stopbang.imc(70, None))
        self.assertIsNone(stopbang.imc('', ''))
        self.assertIsNone(stopbang.imc('setenta', 170))

    def test_datos_absurdos_devuelven_none(self):
        self.assertIsNone(stopbang.imc(70, 0))       # talla 0
        self.assertIsNone(stopbang.imc(900, 170))    # peso 900 kg
        self.assertIsNone(stopbang.imc(70, 300))     # 3 metros de talla
        self.assertIsNone(stopbang.imc(0, 170))


class TestItemsSinRegistrar(unittest.TestCase):
    """Un item que nadie midio NO es un item negativo."""

    def test_item_ausente_aparece_en_sin_registrar(self):
        r = _completo()
        del r['cuello']
        res = stopbang.evaluar(r)
        self.assertIn('cuello', res['sin_registrar'])
        self.assertTrue(res['incompleto'])
        self.assertEqual(res['contestados'], 7)

    def test_item_vacio_tambien_cuenta_como_no_registrado(self):
        res = stopbang.evaluar(_completo(cuello=''))
        self.assertIn('cuello', res['sin_registrar'])

    def test_el_item_sin_registrar_no_suma_ni_resta_puntaje(self):
        r = _completo(ronquido='si')
        del r['cuello']
        res = stopbang.evaluar(r)
        self.assertEqual(res['puntaje'], 1)
        self.assertIsNone(next(f for f in res['items'] if f['clave'] == 'cuello')['positivo'])

    def test_cuestionario_vacio_es_incompleto_con_denominador_cero(self):
        res = stopbang.evaluar({})
        self.assertEqual(res['puntaje'], 0)
        self.assertEqual(res['contestados'], 0)
        self.assertEqual(len(res['sin_registrar']), 8)
        self.assertTrue(res['incompleto'])

    def test_los_ocho_items_salen_en_orden_con_su_letra(self):
        res = stopbang.evaluar(_completo())
        self.assertEqual([f['letra'] for f in res['items']],
                         list('STOPBANG'))


class TestSugiereDerivacion(unittest.TestCase):
    """El puntaje de un cuestionario incompleto es un PISO, no el resultado."""

    def test_tres_o_mas_deriva(self):
        res = stopbang.evaluar(_completo(ronquido='si', cansancio='si', apneas='si'))
        deriva, motivo = stopbang.sugiere_derivacion(res)
        self.assertTrue(deriva)
        self.assertIn('3 de 8', motivo)

    def test_dos_incompleto_deriva_porque_el_puntaje_es_un_piso(self):
        r = _completo(ronquido='si', cansancio='si')
        del r['cuello']
        res = stopbang.evaluar(r)
        self.assertEqual(res['puntaje'], 2)
        self.assertTrue(res['incompleto'])
        deriva, motivo = stopbang.sugiere_derivacion(res)
        self.assertTrue(deriva)
        self.assertIn('sin medir', motivo)

    def test_dos_completo_no_deriva(self):
        res = stopbang.evaluar(_completo(ronquido='si', cansancio='si'))
        self.assertEqual(res['puntaje'], 2)
        self.assertFalse(res['incompleto'])
        deriva, _ = stopbang.sugiere_derivacion(res)
        self.assertFalse(deriva)

    def test_uno_incompleto_no_deriva(self):
        r = _completo(ronquido='si')
        del r['cuello']
        res = stopbang.evaluar(r)
        deriva, _ = stopbang.sugiere_derivacion(res)
        self.assertFalse(deriva)

    def test_el_texto_legal_dice_que_no_es_diagnostico(self):
        res = stopbang.evaluar(_completo())
        self.assertIn('no a un diagnóstico', res['texto_legal'])
        self.assertIn('polisomnografía', res['texto_legal'])



class TestCuelloPorTallaDeCamisa(unittest.TestCase):
    """La circunferencia de cuello es el unico item que el paciente no puede
    contestar de memoria. Su talla de camisa SI la sabe, y es esa misma medida
    en pulgadas."""

    def test_convierte_pulgadas_a_centimetros(self):
        self.assertAlmostEqual(stopbang.cuello_desde_camisa('16'), 40.6, places=1)
        self.assertAlmostEqual(stopbang.cuello_desde_camisa('15 1/2'), 39.4, places=1)

    def test_el_umbral_cae_entre_la_15_y_medio_y_la_16(self):
        """Es el corte que decide el item, asi que se fija: 15 1/2 no lo pasa y
        16 si. Si alguien toca la conversion, esto tiene que romperse."""
        bajo = stopbang.cuello_desde_camisa('15 1/2')
        alto = stopbang.cuello_desde_camisa('16')
        self.assertLessEqual(bajo, stopbang.CUELLO_UMBRAL_CM)
        self.assertGreater(alto, stopbang.CUELLO_UMBRAL_CM)

    def test_no_se_deja_el_item_sin_registrar(self):
        """Sin registrar NO es negativo: el puntaje sale incompleto y la hoja lo
        dice. Convertir un "no se" en un 0 seria inventar un dato tranquilizador."""
        for v in (None, '', 'no_se'):
            self.assertIsNone(stopbang.cuello_desde_camisa(v))

    def test_basura_no_inventa_un_numero(self):
        for v in ('grande', 'XL', '99', '3'):
            self.assertIsNone(stopbang.cuello_desde_camisa(v))

    def test_acepta_pulgadas_directas(self):
        """Por si alguna vez llega el numero en vez de la etiqueta."""
        self.assertAlmostEqual(stopbang.cuello_desde_camisa(16), 40.6, places=1)

    def test_no_se_le_aplica_correccion_por_holgura(self):
        """Un cuello de camisa se corta con holgura, pero descontarle milimetros
        inventados seria un ajuste sin fuente en un item que se juega en un
        umbral. Se convierte y punto; la hoja declara de donde vino."""
        for etiqueta, pulgadas in stopbang.TALLAS_CAMISA:
            self.assertAlmostEqual(stopbang.cuello_desde_camisa(etiqueta),
                                   round(pulgadas * stopbang.PULGADA_CM, 1), places=1)

if __name__ == '__main__':
    unittest.main(verbosity=2)
