"""
test_fairest.py - FAIREST-6 (pediatrico) y FAIREST 6+4 (adultos): banderas
rojas de trastorno respiratorio del sueno que se pesquisan en el examen.

Cero red y cero disco: fairest solo consulta transversal.py (que a su vez solo
lee su tabla versionada).

    cd admin && python test_fairest.py

Lo que se protege:

  - Los criterios de positividad de cada item (amigdalas, anquiloglosia,
    Friedman), probados por ambos lados del corte.
  - EL ITEM 6, que es donde este proyecto se aparta de la lamina a proposito:
    'paladar estrecho' se puntua con el percentil de Bishara (< P15) y no con la
    estimacion visual. Se fija el umbral, la PRECEDENCIA intermolar -> intercanino
    (el caso real es el nino de 6-7 anios, que no tiene referencia de intermolar)
    y que sin ninguna medicion el item queda SIN REGISTRAR y no negativo.
  - Que en adultos la BANDA siga saliendo del puntaje de 6 aunque el total sea
    de 10: la lamina de adultos no publica bandas para el total, y no se inventan.
  - Que el detector de frases prohibidas funcione con y sin tildes. Es lo que
    impide que la hoja impresa le atribuya a la expansion palatina un efecto
    sobre la apnea, que la AAO lista entre lo que un ortodoncista NO debe hacer.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import fairest      # noqa: E402
import transversal  # noqa: E402

# 13 anios, masculino, intermolar maxilar: media 53,4 / DE 2,9 -> el P15 cae en
# 50,4 mm. 50,0 queda justo bajo el umbral y 51,0 justo sobre el.
EDAD = 13
SEXO = 'M'
MM_BAJO_P15 = 50.0
MM_SOBRE_P15 = 51.0


def _obs(**ov):
    """Los 6 items pediatricos, todos negativos salvo los overrides."""
    r = {'respiracion_bucal': 'no', 'tension_mentoniano': 'no', 'amigdalas': '0-25',
         'anquiloglosia': 1, 'desgaste_dentario': 'no', 'paladar_estrecho': 'no'}
    r.update(ov)
    return r


class TestPuntajeYBanda(unittest.TestCase):

    def test_todo_negativo_es_cero_y_normal(self):
        res = fairest.evaluar(_obs())
        self.assertEqual(res['puntaje_6'], 0)
        self.assertEqual(res['banda'], 'normal')
        self.assertEqual(res['sin_registrar'], [])
        self.assertEqual(res['instrumento'], 'FAIREST-6')

    def test_todo_positivo_es_seis_y_severo(self):
        res = fairest.evaluar(_obs(respiracion_bucal='si', tension_mentoniano='si',
                                   amigdalas='76-100', anquiloglosia=4,
                                   desgaste_dentario='si', paladar_estrecho='si'))
        self.assertEqual(res['puntaje_6'], 6)
        self.assertEqual(res['banda'], 'severo')

    def test_la_tabla_de_bandas_de_la_lamina(self):
        self.assertEqual(fairest.banda_riesgo(0), 'normal')
        self.assertEqual(fairest.banda_riesgo(1), 'normal')
        self.assertEqual(fairest.banda_riesgo(2), 'leve')
        self.assertEqual(fairest.banda_riesgo(3), 'leve')
        self.assertEqual(fairest.banda_riesgo(4), 'moderado')
        self.assertEqual(fairest.banda_riesgo(5), 'moderado')
        self.assertEqual(fairest.banda_riesgo(6), 'severo')

    def test_item_no_registrado_no_cuenta_como_negativo(self):
        obs = _obs()
        del obs['desgaste_dentario']
        res = fairest.evaluar(obs)
        self.assertIn('desgaste_dentario', res['sin_registrar'])
        fila = next(f for f in res['items'] if f['clave'] == 'desgaste_dentario')
        self.assertIsNone(fila['positivo'])
        self.assertFalse(fila['registrado'])

    def test_sin_adulto_no_hay_items_extra(self):
        res = fairest.evaluar(_obs())
        self.assertEqual(len(res['items']), 6)
        self.assertIsNone(res['total_adulto'])
        self.assertIsNone(res['puntaje_extra_4'])


class TestCriteriosDeCadaItem(unittest.TestCase):

    def _positivo(self, clave, valor):
        res = fairest.evaluar(_obs(**{clave: valor}))
        return next(f for f in res['items'] if f['clave'] == clave)['positivo']

    def test_amigdalas_sobre_el_50_por_ciento_es_positivo(self):
        self.assertTrue(self._positivo('amigdalas', '51-75'))
        self.assertTrue(self._positivo('amigdalas', '76-100'))

    def test_amigdalas_hasta_el_50_por_ciento_es_negativo(self):
        self.assertFalse(self._positivo('amigdalas', '0-25'))
        self.assertFalse(self._positivo('amigdalas', '25-50'))

    def test_amigdalas_acepta_el_porcentaje_directo(self):
        self.assertTrue(self._positivo('amigdalas', 60))
        self.assertFalse(self._positivo('amigdalas', 50))   # el corte es ">", no ">="

    def test_anquiloglosia_grados_3_y_4_positivos(self):
        self.assertTrue(self._positivo('anquiloglosia', 3))
        self.assertTrue(self._positivo('anquiloglosia', 4))

    def test_anquiloglosia_grados_1_y_2_negativos(self):
        self.assertFalse(self._positivo('anquiloglosia', 1))
        self.assertFalse(self._positivo('anquiloglosia', 2))

    def test_friedman_grados_3_y_4_positivos(self):
        for g in (3, 4):
            res = fairest.evaluar(_obs(friedman=g), adulto=True)
            fila = next(f for f in res['items'] if f['clave'] == 'friedman')
            self.assertTrue(fila['positivo'], g)

    def test_friedman_grados_1_y_2_negativos(self):
        for g in (1, 2):
            res = fairest.evaluar(_obs(friedman=g), adulto=True)
            fila = next(f for f in res['items'] if f['clave'] == 'friedman')
            self.assertFalse(fila['positivo'], g)

    def test_aleteo_palatino_positivo_es_bandera(self):
        # 'positivo' en la lamina = NO se produce ronquido con la lengua en
        # succion palatina. Es una de las dos convenciones que la lamina no
        # imprime y que quedaron como constante con nombre.
        self.assertTrue(fairest.ALETEO_POSITIVO_ES_BANDERA)
        res = fairest.evaluar(_obs(aleteo_palatino='positivo'), adulto=True)
        fila = next(f for f in res['items'] if f['clave'] == 'aleteo_palatino')
        self.assertTrue(fila['positivo'])


class TestItem6PaladarEstrecho(unittest.TestCase):
    """El item que se puntua con criterio objetivo (percentil de Bishara)."""

    def test_el_umbral_es_el_percentil_15_y_vive_en_transversal(self):
        self.assertEqual(fairest.PERCENTIL_PALADAR_ESTRECHO, 15)
        self.assertIs(fairest.PERCENTIL_PALADAR_ESTRECHO,
                      transversal.PERCENTIL_PALADAR_ESTRECHO)

    def test_justo_bajo_p15_es_positivo(self):
        r = fairest.paladar_estrecho(MM_BAJO_P15, None, SEXO, EDAD)
        self.assertTrue(r['positivo'])
        self.assertLess(r['percentil'], 15)

    def test_justo_sobre_p15_es_negativo(self):
        r = fairest.paladar_estrecho(MM_SOBRE_P15, None, SEXO, EDAD)
        self.assertFalse(r['positivo'])
        self.assertGreater(r['percentil'], 15)

    def test_el_cambio_se_refleja_en_el_puntaje_y_en_la_banda(self):
        # Con una sola bandera clinica ademas del item 6, cruzar el P15 mueve
        # el puntaje de 1 a 2 y con eso la banda de 'normal' a 'leve'.
        datos = {'sexo': SEXO, 'edad': EDAD}
        bajo = fairest.evaluar({'respiracion_bucal': 'si'},
                               transversal_datos=dict(datos, intermolar_mm=MM_BAJO_P15))
        sobre = fairest.evaluar({'respiracion_bucal': 'si'},
                                transversal_datos=dict(datos, intermolar_mm=MM_SOBRE_P15))
        self.assertEqual(bajo['puntaje_6'], 2)
        self.assertEqual(bajo['banda'], 'leve')
        self.assertEqual(sobre['puntaje_6'], 1)
        self.assertEqual(sobre['banda'], 'normal')

    def test_la_medicion_manda_sobre_lo_que_venga_en_observaciones(self):
        # transversal_datos gana: si el doctor marco la casilla a ojo y la
        # medicion dice otra cosa, manda la medicion.
        res = fairest.evaluar(_obs(paladar_estrecho='si'),
                              transversal_datos={'sexo': SEXO, 'edad': EDAD,
                                                 'intermolar_mm': MM_SOBRE_P15})
        fila = next(f for f in res['items'] if f['clave'] == 'paladar_estrecho')
        self.assertFalse(fila['positivo'])
        self.assertEqual(res['puntaje_6'], 0)

    def test_el_criterio_usado_se_declara_en_el_resultado(self):
        res = fairest.evaluar(_obs())
        self.assertIn('criterio objetivo', res['item6_criterio'])
        self.assertIn('percentil < 15', res['item6_criterio'])


class TestItem6Precedencia(unittest.TestCase):
    """Se prefiere el intermolar (medida transversal directa del paladar); si
    no hay referencia para esa edad se cae al intercanino, que Bishara mide de
    forma continua. Sin ninguna medicion, el item queda SIN REGISTRAR."""

    def test_con_intermolar_disponible_usa_el_intermolar(self):
        r = fairest.paladar_estrecho(MM_BAJO_P15, 30.0, SEXO, EDAD)
        self.assertEqual(r['origen'], 'intermolar')

    def test_sin_intermolar_medido_usa_el_intercanino(self):
        # El respaldo sigue existiendo, pero ahora se activa cuando NO se midio
        # el intermolar, no por la edad: la curva ya cubre de 3 a 45 anios.
        r = fairest.paladar_estrecho(arcada_maxilar_intermolar_mm=None,
                                     intercanino_mm=29.0, sexo=SEXO, edad=6)
        self.assertEqual(r['origen'], 'intercanino')
        self.assertIsNotNone(r['percentil'])
        self.assertIn('intercanino', r['detalle'])

    def test_sin_ninguna_medicion_queda_sin_registrar_no_negativo(self):
        r = fairest.paladar_estrecho(None, None, SEXO, EDAD)
        self.assertIsNone(r['positivo'])
        self.assertIsNone(r['origen'])
        self.assertIsNone(r['percentil'])

    def test_sin_medicion_el_item_aparece_en_sin_registrar(self):
        res = fairest.evaluar(_obs(),
                              transversal_datos={'sexo': SEXO, 'edad': EDAD})
        self.assertIn('paladar_estrecho', res['sin_registrar'])
        fila = next(f for f in res['items'] if f['clave'] == 'paladar_estrecho')
        self.assertIsNone(fila['positivo'])
        self.assertEqual(res['puntaje_6'], 0)

    def test_el_nino_de_6_o_7_ya_tiene_referencia_de_intermolar(self):
        # Antes caia al intercanino porque la curva estaba partida; ahora la
        # curva de Bishara atraviesa el recambio y el intermolar sirve igual.
        r = fairest.paladar_estrecho(arcada_maxilar_intermolar_mm=46.0,
                                     intercanino_mm=29.0, sexo='M', edad=6.5)
        self.assertEqual(r['origen'], 'intermolar')
        self.assertIsNotNone(r['percentil'])

    def test_la_celda_sospechosa_se_arrastra_hasta_el_item(self):
        # Intermolar mandibular femenino a los 3 anios tiene DE 6,2 (probable
        # error de imprenta del paper). Aca se usa el MAXILAR, pero la marca de
        # sospechoso viaja igual cuando corresponde: se comprueba que la clave
        # existe para que la hoja pueda avisar.
        r = fairest.paladar_estrecho(40.0, None, 'F', 3)
        self.assertIn('sospechoso', r)


class TestAdultos(unittest.TestCase):

    def test_seis_mas_cuatro_suma_diez_pero_la_banda_sale_del_6(self):
        # Los 4 items de adultos positivos, los 6 pediatricos negativos.
        res = fairest.evaluar(_obs(festoneado_lingual='si', desborde_lingual='si',
                                   friedman=4, aleteo_palatino='positivo'),
                              adulto=True)
        self.assertEqual(res['instrumento'], 'FAIREST 6+4')
        self.assertEqual(len(res['items']), 10)
        self.assertEqual(res['puntaje_6'], 0)
        self.assertEqual(res['puntaje_extra_4'], 4)
        self.assertEqual(res['total_adulto'], 4)
        # La lamina de adultos NO publica bandas para el total de 10: la banda
        # sale SIEMPRE del FAIREST-6. Con 0 de 6, es 'normal' aunque el total sea 4.
        self.assertEqual(res['banda'], 'normal')
        self.assertTrue(res['banda_es_del_6'])

    def test_el_total_adulto_es_la_suma_de_los_dos_bloques(self):
        res = fairest.evaluar(_obs(respiracion_bucal='si', desgaste_dentario='si',
                                   festoneado_lingual='si', friedman=3),
                              adulto=True)
        self.assertEqual(res['puntaje_6'], 2)
        self.assertEqual(res['puntaje_extra_4'], 2)
        self.assertEqual(res['total_adulto'], 4)
        self.assertEqual(res['banda'], fairest.banda_riesgo(2))
        self.assertEqual(res['banda'], 'leve')   # NO 'moderado', que seria la del 4

    def test_los_items_extra_sin_registrar_tambien_se_informan(self):
        res = fairest.evaluar(_obs(), adulto=True)
        for clave in ('festoneado_lingual', 'desborde_lingual', 'friedman',
                      'aleteo_palatino'):
            self.assertIn(clave, res['sin_registrar'])


class TestFrasesProhibidas(unittest.TestCase):
    """El detector no puede fallar solo porque el texto lleve tildes."""

    def test_detecta_con_tilde(self):
        self.assertTrue(fairest.frases_prohibidas_en(
            'La expansión resuelve el problema respiratorio.'))

    def test_detecta_sin_tilde(self):
        self.assertTrue(fairest.frases_prohibidas_en(
            'La expansion resuelve el problema respiratorio.'))

    def test_las_dos_formas_dan_el_mismo_hallazgo(self):
        con = fairest.frases_prohibidas_en('la expansión cura la apnea')
        sin = fairest.frases_prohibidas_en('la expansion cura la apnea')
        self.assertEqual(con, sin)
        self.assertTrue(con)

    def test_es_insensible_a_mayusculas(self):
        self.assertTrue(fairest.frases_prohibidas_en('EXPANSIÓN PARA LA APNEA'))

    def test_un_texto_limpio_no_dispara(self):
        self.assertEqual(fairest.frases_prohibidas_en(fairest.TEXTO_LEGAL), [])
        self.assertEqual(fairest.frases_prohibidas_en(
            'El tamizaje sugiere evaluación médica del sueño.'), [])

    def test_los_textos_propios_del_modulo_estan_limpios(self):
        res = fairest.evaluar(_obs(), adulto=True)
        texto = ' '.join([res['item6_criterio'], res['texto_legal']] +
                         [f['etiqueta'] + ' ' + f['texto'] for f in res['items']])
        self.assertEqual(fairest.frases_prohibidas_en(texto), [])

    def test_el_texto_legal_dice_que_no_es_diagnostico(self):
        self.assertIn('no a un diagnóstico', fairest.TEXTO_LEGAL)


class TestSugiereDerivacion(unittest.TestCase):
    """Basta UNA de las dos senales. En tamizaje el costo de derivar de mas es
    una consulta; el de derivar de menos es un nino que no duerme por anios."""

    def test_cuestionario_alto_deriva_aunque_el_fairest_sea_cero(self):
        res = fairest.evaluar(_obs())
        self.assertEqual(res['puntaje_6'], 0)
        deriva, motivo = fairest.sugiere_derivacion(res, puntaje_cuestionario_alto=True)
        self.assertTrue(deriva)
        self.assertIn('cuestionario', motivo.lower())

    def test_fairest_2_deriva_aunque_el_cuestionario_este_bajo(self):
        res = fairest.evaluar(_obs(respiracion_bucal='si', desgaste_dentario='si'))
        self.assertEqual(res['puntaje_6'], 2)
        deriva, motivo = fairest.sugiere_derivacion(res, puntaje_cuestionario_alto=False)
        self.assertTrue(deriva)
        self.assertIn('2 de 6', motivo)

    def test_fairest_1_y_cuestionario_bajo_no_deriva(self):
        res = fairest.evaluar(_obs(respiracion_bucal='si'))
        deriva, _ = fairest.sugiere_derivacion(res, puntaje_cuestionario_alto=False)
        self.assertFalse(deriva)

    def test_el_motivo_nunca_trae_una_frase_prohibida(self):
        for alto in (True, False):
            for obs in (_obs(), _obs(respiracion_bucal='si', desgaste_dentario='si')):
                _, motivo = fairest.sugiere_derivacion(fairest.evaluar(obs), alto)
                self.assertEqual(fairest.frases_prohibidas_en(motivo), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
