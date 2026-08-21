"""
test_transversal.py - Evaluacion transversal de arcadas (Bishara 1997):
referencia por edad/sexo, percentil, y la curva SVG de la hoja impresa.

Cero red y cero disco: el modulo solo lee su propia tabla versionada
(transversal_normas.json) y calcula.

    cd admin && python test_transversal.py

Un fallo aca es un numero MAL en una hoja que el paciente se lleva firmada por
el doctor. Lo que se protege:

  - Que la tabla y lo que devuelve el modulo no se separen nunca (se recorren
    las 48 filas del JSON, no se hardcodea ninguna).
  - Que la interpolacion JAMAS cruce del molar temporal al permanente: entre
    los 5 y los 8 anios Bishara cambia el diente medido y el valor salta ~7 mm.
    Rellenar ese hueco seria inventar un dato con cara de dato.
  - Que la curva no dibuje un valle donde los datos solo crecen (por eso la
    interpolacion es monotona y no un spline cualquiera).
"""

import json
import sys
import re
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import transversal  # noqa: E402

_NORMAS = json.load(open(transversal.NORMAS_PATH, encoding='utf-8'))
REGISTROS = _NORMAS['registros']


class TestExactitudContraLaTabla(unittest.TestCase):
    """La tabla es la fuente. En la edad exacta de cada fila, el modulo tiene
    que devolver EXACTAMENTE la media y la DE publicadas: si la interpolacion
    se toca y deja de pasar por sus propios puntos, esto lo caza."""

    def test_las_48_filas_devuelven_su_media_y_su_de(self):
        self.assertEqual(len(REGISTROS), 48)   # si cambia la tabla, esto avisa
        for r in REGISTROS:
            with self.subTest(medida=r['medida'], arcada=r['arcada'],
                              sexo=r['sexo'], edad=r['edad']):
                ref = transversal.referencia(r['medida'], r['arcada'], r['sexo'],
                                             r['edad'], r['tramo'])
                self.assertTrue(ref['ok'], ref)
                self.assertEqual(ref['media'], r['media'])
                self.assertEqual(ref['de'], r['de'])
                self.assertEqual(ref['tramo'], r['tramo'])
                self.assertFalse(ref['interpolado'])

class TestCurvaContinua(unittest.TestCase):
    """La curva es UNA sola de los 3 a los 45 anios y atraviesa el recambio.

    Bishara mide sobre el diente que el paciente TIENE a cada edad (segundos
    molares temporales a los 3 y 5, primeros permanentes desde los 8; caninos
    temporales hasta los 8 y permanentes desde los 13) y sus figuras 4 y 5
    trazan una linea continua. Partirla dejaba sin referencia justo al nino de
    6-7 anios, que es el paciente pediatrico mas frecuente.
    """

    def test_el_nino_de_6_y_7_si_recibe_referencia_de_intermolar(self):
        for edad in (5.5, 6, 6.5, 7, 7.9):
            for arcada in transversal.ARCADAS:
                for sexo in transversal.SEXOS:
                    with self.subTest(edad=edad, arcada=arcada, sexo=sexo):
                        ref = transversal.referencia('intermolar', arcada, sexo, edad)
                        self.assertTrue(ref.get('ok'), (edad, arcada, sexo, ref))
                        self.assertTrue(ref['interpolado'])

    def test_el_valor_del_hueco_cae_entre_sus_vecinos_de_tabla(self):
        # Hombres: 43,5 mm a los 5 y 51,0 a los 8. La interpolacion tiene que
        # quedar adentro, no disparada.
        ref = transversal.referencia('intermolar', 'maxilar', 'M', 6.5)
        self.assertGreater(ref['media'], 43.5)
        self.assertLess(ref['media'], 51.0)

    def test_la_curva_nunca_retrocede_atravesando_el_recambio(self):
        # De 3 a 13 anios el ancho solo crece: si la interpolacion monotona
        # fallara, el salto del recambio podria dibujar una joroba.
        previo = -1
        for i in range(101):
            edad = 3 + (13 - 3) * i / 100
            m = transversal.referencia('intermolar', 'maxilar', 'M', edad)['media']
            self.assertGreaterEqual(m + 1e-9, previo, 'retrocede en %.2f anios' % edad)
            previo = m

    def test_dice_sobre_que_diente_esta_medida_la_referencia(self):
        self.assertIn('temporal',
                      transversal.referencia('intermolar', 'maxilar', 'M', 4)['diente'])
        self.assertIn('permanente',
                      transversal.referencia('intermolar', 'maxilar', 'M', 9)['diente'])
        self.assertIn('temporal',
                      transversal.referencia('intercanino', 'maxilar', 'M', 8)['diente'])
        self.assertIn('permanente',
                      transversal.referencia('intercanino', 'maxilar', 'M', 13)['diente'])

    def test_marca_las_edades_de_recambio(self):
        # Ahi el ascenso refleja el recambio ademas del crecimiento, y la hoja
        # lo dice. Pero la curva pasa igual.
        self.assertTrue(transversal.referencia('intermolar', 'maxilar', 'M', 6)['en_recambio'])
        self.assertFalse(transversal.referencia('intermolar', 'maxilar', 'M', 9)['en_recambio'])
        self.assertTrue(transversal.referencia('intercanino', 'maxilar', 'M', 11)['en_recambio'])
        self.assertFalse(transversal.referencia('intercanino', 'maxilar', 'M', 6)['en_recambio'])

    def test_el_tramo_declarado_no_cambia_el_resultado(self):
        # Se sigue aceptando por compatibilidad con lo que guarda el registro,
        # pero no puede alterar el numero.
        base = transversal.referencia('intermolar', 'maxilar', 'M', 6)
        for tramo in (None, 'molar_temporal', 'molar_permanente', 'cualquier_cosa'):
            with self.subTest(tramo=tramo):
                r = transversal.referencia('intermolar', 'maxilar', 'M', 6, tramo=tramo)
                self.assertEqual(r['media'], base['media'])
                self.assertEqual(r['de'], base['de'])

    def test_el_intercanino_tambien_es_continuo(self):
        ref = transversal.referencia('intercanino', 'maxilar', 'M', 6)
        self.assertTrue(ref['ok'])
        self.assertGreater(ref['media'], 30.3)
        self.assertLess(ref['media'], 32.5)



class TestBordesDeEdad(unittest.TestCase):

    def test_bajo_el_minimo_no_se_extrapola(self):
        ref = transversal.referencia('intercanino', 'maxilar', 'M', 2)
        self.assertFalse(ref['ok'])
        self.assertEqual(ref['motivo'], 'fuera_de_rango')

    def test_sobre_los_45_se_usa_el_borde_y_se_declara(self):
        # El ancho esta en meseta desde los 26: un paciente de 50 no es raro.
        ref = transversal.referencia('intercanino', 'maxilar', 'M', 50)
        self.assertTrue(ref['ok'])
        self.assertTrue(ref['en_borde'])
        self.assertEqual(ref['media'], 33.7)   # el valor de los 45

    def test_parametros_fuera_del_catalogo(self):
        self.assertEqual(transversal.referencia('intercuspideo', 'maxilar', 'M', 10)['motivo'],
                         'parametro_invalido')
        self.assertEqual(transversal.referencia('intercanino', 'palatino', 'M', 10)['motivo'],
                         'parametro_invalido')
        self.assertEqual(transversal.referencia('intercanino', 'maxilar', 'X', 10)['motivo'],
                         'parametro_invalido')
        self.assertEqual(transversal.referencia('intercanino', 'maxilar', 'M', 'diez')['motivo'],
                         'parametro_invalido')


class TestPercentil(unittest.TestCase):
    """percentil = Phi(z). Es un supuesto declarado (la fuente publica media y
    DE, no percentiles empiricos), pero tiene que estar bien calculado."""

    def test_medir_la_media_da_percentil_50(self):
        ref = transversal.referencia('intermolar', 'maxilar', 'M', 13)
        p = transversal.percentil('intermolar', 'maxilar', 'M', 13, ref['media'])
        self.assertTrue(p['ok'])
        self.assertEqual(p['percentil'], 50.0)
        self.assertEqual(p['z'], 0.0)

    def test_una_de_por_debajo_del_z_del_p15(self):
        ref = transversal.referencia('intermolar', 'maxilar', 'M', 13)
        mm = ref['media'] - 1.036 * ref['de']
        p = transversal.percentil('intermolar', 'maxilar', 'M', 13, mm)
        self.assertAlmostEqual(p['percentil'], 15.0, delta=0.1)

    def test_el_z_del_p97(self):
        ref = transversal.referencia('intercanino', 'mandibular', 'F', 13)
        mm = ref['media'] + 1.881 * ref['de']
        p = transversal.percentil('intercanino', 'mandibular', 'F', 13, mm)
        self.assertAlmostEqual(p['percentil'], 97.0, delta=0.1)

    def test_bajo_p15_es_true_bajo_el_umbral(self):
        # 13 anios, M, intermolar maxilar: media 53,4 / DE 2,9 -> P15 ~ 50,4 mm
        p = transversal.percentil('intermolar', 'maxilar', 'M', 13, 50.0)
        self.assertLess(p['percentil'], 15)
        self.assertTrue(p['bajo_p15'])

    def test_bajo_p15_es_false_sobre_el_umbral(self):
        p = transversal.percentil('intermolar', 'maxilar', 'M', 13, 51.0)
        self.assertGreater(p['percentil'], 15)
        self.assertFalse(p['bajo_p15'])

    def test_percentil_hereda_el_error_de_referencia(self):
        p = transversal.percentil('intermolar', 'maxilar', 'M', 2, 32.0)
        self.assertFalse(p['ok'])
        self.assertEqual(p['motivo'], 'fuera_de_rango')

    def test_medicion_no_numerica(self):
        p = transversal.percentil('intermolar', 'maxilar', 'M', 13, 'cincuenta')
        self.assertFalse(p['ok'])
        self.assertEqual(p['motivo'], 'parametro_invalido')

    def test_etiqueta_percentil_sin_referencia_no_dice_dentro_del_promedio(self):
        self.assertEqual(transversal.etiqueta_percentil(None), 'sin referencia')
        self.assertEqual(transversal.etiqueta_percentil(2), 'muy por debajo del promedio')
        self.assertEqual(transversal.etiqueta_percentil(10), 'bajo el promedio')
        self.assertEqual(transversal.etiqueta_percentil(50), 'dentro del promedio')
        self.assertEqual(transversal.etiqueta_percentil(90), 'sobre el promedio')
        self.assertEqual(transversal.etiqueta_percentil(99), 'muy por encima del promedio')


class TestMonotonia(unittest.TestCase):
    """La interpolacion es monotona (PCHIP) a proposito: un spline suave puede
    hacer overshoot y dibujar un valle donde los datos solo crecen. En un
    grafico que el paciente se lleva a la casa, ese valle es un error que nadie
    va a notar y que igual esta mal."""

    def test_intermolar_maxilar_masculino_entre_8_y_13_nunca_decrece(self):
        previo = None
        for i in range(51):
            edad = 8 + (13 - 8) * i / 50.0
            ref = transversal.referencia('intermolar', 'maxilar', 'M', edad)
            self.assertTrue(ref['ok'], (edad, ref))
            if previo is not None:
                self.assertGreaterEqual(ref['media'], previo - 1e-9,
                                        'la curva baja en la edad %.2f' % edad)
            previo = ref['media']

    def test_la_curva_pasa_por_los_dos_extremos_del_tramo(self):
        self.assertEqual(transversal.referencia('intermolar', 'maxilar', 'M', 8)['media'], 51.0)
        self.assertEqual(transversal.referencia('intermolar', 'maxilar', 'M', 13)['media'], 53.4)


class TestCeldaSospechosa(unittest.TestCase):
    """La Tabla II publica DE = 6,2 mm para el intermolar mandibular femenino a
    los 3 anios: tres veces la de sus vecinas. Se transcribe fiel a la fuente,
    pero el modulo TIENE que marcarla para que la hoja avise y para que nadie
    decida el item 6 del FAIREST con esa celda."""

    def test_la_celda_se_informa_como_sospechosa(self):
        ref = transversal.referencia('intermolar', 'mandibular', 'F', 3)
        self.assertTrue(ref['ok'])
        self.assertEqual(ref['de'], 6.2)
        self.assertTrue(ref['sospechoso'])

    def test_una_celda_vecina_normal_no_se_marca(self):
        ref = transversal.referencia('intermolar', 'mandibular', 'M', 3)
        self.assertTrue(ref['ok'])
        self.assertFalse(ref['sospechoso'])

    def test_el_percentil_arrastra_la_marca(self):
        p = transversal.percentil('intermolar', 'mandibular', 'F', 3, 30.0)
        self.assertTrue(p['sospechoso'])

    def test_la_marca_esta_declarada_en_el_json(self):
        fila = [r for r in REGISTROS
                if r['medida'] == 'intermolar' and r['arcada'] == 'mandibular'
                and r['sexo'] == 'F' and r['edad'] == 3][0]
        self.assertTrue(fila.get('sospechoso'))


class TestCurvaSvg(unittest.TestCase):

    def test_devuelve_svg_con_las_cinco_lineas(self):
        svg = transversal.curva_svg('intermolar', 'maxilar', 'M', 13, 53.4)
        self.assertTrue(svg.startswith('<svg'))
        for p in (3, 15, 50, 85, 97):
            self.assertIn('>P%d<' % p, svg)

    def test_sin_referencia_devuelve_none_en_vez_de_grafico_vacio(self):
        # El llamador imprime "sin referencia normativa"; un SVG en blanco
        # se leeria como un grafico donde el paciente no aparece.
        # Bajo los 3 anios no hay tabla: la referencia de Bishara parte ahi.
        self.assertIsNone(transversal.curva_svg('intermolar', 'maxilar', 'M', 2, 32.0))
        self.assertIsNone(transversal.curva_svg('intercanino', 'maxilar', 'M', 2, 28.0))

    def test_sin_medicion_igual_dibuja_la_referencia(self):
        svg = transversal.curva_svg('intercanino', 'maxilar', 'F', 10)
        self.assertTrue(svg.startswith('<svg'))
        self.assertNotIn('<circle', svg)   # sin punto del paciente

    def test_con_medicion_dibuja_el_punto(self):
        svg = transversal.curva_svg('intercanino', 'maxilar', 'F', 10, 31.5)
        self.assertIn('<circle', svg)
        self.assertIn('31.5 mm', svg)


class TestVentanaDelEje(unittest.TestCase):
    """Regla del usuario (2026-08-20): el eje SIEMPRE parte a los 3 anios.
    Pediatrico llega a 18; adulto a 45; y un paciente que empezo de nino pero
    ya tiene algun control pasados los 18 se grafica 3-45, para que toda su
    historia entre en el mismo grafico."""

    def test_pediatrico_va_de_3_a_18(self):
        self.assertEqual(transversal._ventana(10), (3, 18))
        self.assertEqual(transversal._ventana(17.9), (3, 18))

    def test_adulto_va_de_3_a_45(self):
        self.assertEqual(transversal._ventana(18), (3, 45))
        self.assertEqual(transversal._ventana(41), (3, 45))

    def test_un_control_pasados_los_18_estira_el_eje(self):
        # El paciente tenia 10 en su primer informe; hoy tiene 20. Todo su
        # seguimiento tiene que caber en el mismo grafico.
        self.assertEqual(transversal._ventana(10, [(8, 46.0), (20, 52.0)]), (3, 45))

    def test_el_eje_no_se_estira_por_historico_pediatrico(self):
        self.assertEqual(transversal._ventana(12, [(8, 46.0), (10, 47.0)]), (3, 18))

    def test_el_eje_arranca_a_los_3_aunque_la_curva_empiece_despues(self):
        # El intermolar permanente no tiene datos antes de los 8, pero el eje
        # igual parte en 3: el hueco se ve, no se disimula.
        svg = transversal.curva_svg('intermolar', 'maxilar', 'M', 13, mm=50.0,
                                    tramo=transversal.TRAMO_MOLAR_PERMANENTE)
        marcas = re.findall(r'text-anchor="middle">(\d+)<', svg)
        self.assertIn('3', marcas)
        self.assertIn('18', marcas)


class TestTrayectoriaDelPaciente(unittest.TestCase):
    """Las mediciones previas se dibujan y se unen con una linea DELGADA."""

    def _svg(self, historico):
        return transversal.curva_svg('intermolar', 'maxilar', 'M', 13, mm=49.0,
                                     tramo=transversal.TRAMO_MOLAR_PERMANENTE,
                                     historico=historico)

    def test_cada_medicion_previa_es_un_punto(self):
        svg = self._svg([(8, 46.0), (10, 47.2), (11.5, 48.1)])
        self.assertEqual(svg.count('fill="#fff" stroke="#1A2E4A"'), 3)

    def test_la_linea_que_une_es_mas_delgada_que_los_puntos(self):
        # 1 px de trazo contra un radio de 3,4-4,8: la vista sigue las
        # mediciones, no el trazo que las une.
        svg = self._svg([(8, 46.0), (10, 47.2)])
        self.assertIn('stroke-width="1" stroke-linejoin', svg)

    def test_sin_historico_no_hay_linea_de_trayectoria(self):
        svg = self._svg([])
        self.assertNotIn('stroke-width="1" stroke-linejoin', svg)

    def test_una_sola_medicion_no_dibuja_linea(self):
        # Con un punto no hay trayectoria que mostrar.
        svg = transversal.curva_svg('intermolar', 'maxilar', 'M', 13, mm=49.0,
                                    tramo=transversal.TRAMO_MOLAR_PERMANENTE)
        self.assertNotIn('stroke-width="1" stroke-linejoin', svg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
