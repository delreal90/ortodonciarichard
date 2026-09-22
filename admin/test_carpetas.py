"""
test_carpetas.py - Encontrar la carpeta de fotos del paciente en DIGITAL1.

Cero red y cero disco: al modulo se le pasa una LISTA de nombres de carpeta.

⚠️ Los nombres de aca son INVENTADOS, pero copian la FORMA exacta de los reales
(apellido materno abreviado, con punto o ausente; numero de ficha suelto o pegado
al codigo; apellido paterno de varias palabras; tildes; doble espacio). Este repo
es PUBLICO y que alguien sea paciente de esta clinica es un dato personal de
salud. Lo que las pruebas ejercitan es la forma; la identidad no aporta nada.

    cd admin && python test_carpetas.py

Lo que se protege:

  - La trampa del COMODIN. Con un prefijo bidireccional flojo, la inicial suelta
    de `Abarza A Andrea` calzaba con cualquier consulta: `Abundio Aniceto
    Alexsandra` devolvia 12 candidatas. Es el unico bug que la medicion sobre
    los datos reales destapo y que leyendo el codigo no se ve.

  - Que las CARPETAS DUPLICADAS del mismo paciente salgan LAS DOS y con
    `confiable=False`. `Arrieta R. Ignacia` y `Arrieta Rosales Ignacia` son la
    misma persona; abrir una sin avisar de la otra esconde media ficha.

  - Que el numero de ficha y el codigo de dispositivo del final no ensucien el
    nombre, pero que la inicial del apellido materno del principio SI cuente:
    es lo unico que separa a dos hermanos en varias carpetas.

  - Que el apellido solo NUNCA alcance. En una familia, el apellido no
    identifica a nadie.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import carpetas         # noqa: E402
import carpeta_agent as agente   # noqa: E402  (solo funciones puras; no toca red)


# Una `letra A` inventada, con la forma exacta de la real y los casos que importan.
# (Nombres inventados a proposito: ver el aviso de la cabecera. Verificado contra el
# servidor: ninguno de estos nombres corresponde a un paciente de la clinica.)
LETRA_A = [
    'Arancibia Pérez de Lira Lucia',
    'Arancibia Pérez de Lira Patricio',
    'Abarza A Andrea',
    'Abundio Aniceto Alexsandra',
    'Azocar R Alfonso',
    'Arrieta R. Ignacia',
    'Arrieta Rosales Ignacia',
    'Alvear C Vicente',
    'Alvear Marincovic Esteban',
    'Alcaino Jordan Cristian',
    'Alcaino Vergara Cristian',
    'Alcántara Reyes Javier',
    'Almonacid Tapia Juan Ignacio',
    'Arenas G Juan Pablo 5089 A',
]


def _nombres(resultado):
    return [c['carpeta'] for c in resultado['candidatas']]


class TestElComodin(unittest.TestCase):
    """El bug que solo se ve midiendo: una inicial suelta calzaba con todo."""

    def test_una_inicial_no_calza_con_cualquier_apellido(self):
        r = carpetas.rankear('Alexsandra', 'Abundio Aniceto', LETRA_A)
        self.assertEqual(_nombres(r), ['Abundio Aniceto Alexsandra'])
        self.assertTrue(r['confiable'])

    def test_el_apellido_paterno_tiene_que_calzar_de_verdad(self):
        """'abarza' y 'abundio' comparten la 'ab' y nada mas."""
        self.assertFalse(carpetas._calza_paterno('abundio', 'abarza'))
        self.assertFalse(carpetas._calza_paterno('a', 'abarza'))
        self.assertTrue(carpetas._calza_paterno('arancibia', 'arancibia'))

    def test_un_token_de_carpeta_se_consume_una_sola_vez(self):
        """La otra mitad de la defensa: la 'A' no puede valer por dos."""
        total, calzados = carpetas._asignar(['abarza', 'aaa', 'andrea'],
                                            ['abarza', 'a', 'andrea'])
        self.assertEqual(len(calzados), 3)
        # 'aaa' solo pudo tomar la 'a' suelta (0.5), no robarle nada a los otros.
        self.assertEqual(total, 2.0 + 0.5 + 2.0)


class TestDuplicados(unittest.TestCase):
    """Dos carpetas del mismo paciente: hay que mostrar las dos."""

    def test_muestra_las_dos_y_no_se_declara_confiable(self):
        r = carpetas.rankear('Ignacia', 'Arrieta Rosales', LETRA_A)
        self.assertEqual(sorted(_nombres(r)),
                         ['Arrieta R. Ignacia', 'Arrieta Rosales Ignacia'])
        self.assertFalse(r['confiable'])

    def test_la_completa_igual_va_primera(self):
        r = carpetas.rankear('Ignacia', 'Arrieta Rosales', LETRA_A)
        self.assertEqual(_nombres(r)[0], 'Arrieta Rosales Ignacia')

    def test_la_carpeta_abreviada_se_encuentra_con_el_apellido_completo(self):
        """DentiDesk trae 'Alvear Cuevas'; la carpeta dice 'Alvear C'."""
        r = carpetas.rankear('Vicente', 'Alvear Cuevas', LETRA_A)
        self.assertEqual(_nombres(r), ['Alvear C Vicente'])


class TestHermanos(unittest.TestCase):
    def test_el_nombre_de_pila_separa_a_los_hermanos(self):
        r = carpetas.rankear('Lucia', 'Arancibia Perez de Lira', LETRA_A)
        self.assertEqual(_nombres(r), ['Arancibia Pérez de Lira Lucia'])
        self.assertTrue(r['confiable'])

    def test_mismo_nombre_distinto_apellido_materno(self):
        r = carpetas.rankear('Cristian', 'Alcaino Jordan', LETRA_A)
        self.assertEqual(_nombres(r)[0], 'Alcaino Jordan Cristian')
        self.assertTrue(r['confiable'])

    def test_el_apellido_solo_no_identifica_a_nadie(self):
        """⚠️ Regla que no se negocia: tiene que calzar un NOMBRE DE PILA."""
        r = carpetas.rankear('Sofia', 'Alvear Cuevas', LETRA_A)
        self.assertEqual(_nombres(r), [])


class TestNombresSucios(unittest.TestCase):
    """Ficha, codigo de dispositivo, puntos, tildes y espacios de mas."""

    def test_ficha_pegada_al_codigo(self):
        r = carpetas.rankear('Kevin', 'Bahamondes', ['Bahamondes Kevin 4110I'])
        self.assertEqual(_nombres(r), ['Bahamondes Kevin 4110I'])

    def test_doble_espacio_y_ficha_con_letra(self):
        c = ['Lagos Brunner  Francisca 5108a']
        r = carpetas.rankear('Francisca', 'Lagos Brunner', c)
        self.assertEqual(_nombres(r), c)

    def test_el_codigo_del_final_se_descarta(self):
        """La 'F' de 'Miranda A Isidora 3858 F' es codigo, no un nombre."""
        self.assertEqual(carpetas.tokens_carpeta('Miranda A Isidora 3858 F'),
                         ['miranda', 'a', 'isidora'])

    def test_la_inicial_del_principio_NO_se_descarta(self):
        """⚠️ Es lo unico que desempata entre hermanos. Ver tokens_carpeta()."""
        self.assertEqual(carpetas.tokens_carpeta('Azocar R Alfonso'),
                         ['azocar', 'r', 'alfonso'])

    def test_abreviatura_con_punto(self):
        r = carpetas.rankear('Daniela', 'Ferrada Soto', ['Ferrada S. Daniela'])
        self.assertEqual(_nombres(r), ['Ferrada S. Daniela'])

    def test_tildes(self):
        """Nadie teclea tildes, y media carpeta las tiene."""
        r = carpetas.rankear('Javier', 'Alcantara Reyes', LETRA_A)
        self.assertEqual(_nombres(r), ['Alcántara Reyes Javier'])

    def test_apellido_paterno_de_varias_palabras(self):
        c = ['Pinto De Lama P Ignacio']
        r = carpetas.rankear('Ignacio', 'Pinto De Lama', c)
        self.assertEqual(_nombres(r), c)

    def test_carpeta_sin_apellido_materno(self):
        c = ['Solis Maximiliano']
        r = carpetas.rankear('Maximiliano', 'Solis Soto', c)
        self.assertEqual(_nombres(r), c)

    def test_nombre_abreviado_en_la_carpeta(self):
        """'Ma' por 'Maria', que es de lo mas comun en estas carpetas."""
        c = ['Larenas Bravo Ma Angelica']
        r = carpetas.rankear('Maria Angelica', 'Larenas Bravo', c)
        self.assertEqual(_nombres(r), c)

    def test_nombre_compuesto(self):
        r = carpetas.rankear('Juan Pablo', 'Arenas G', LETRA_A)
        self.assertEqual(_nombres(r), ['Arenas G Juan Pablo 5089 A'])


class TestLetra(unittest.TestCase):
    """Los cuatro grupos que no son una sola letra."""

    def test_ch_mira_las_dos(self):
        self.assertEqual(carpetas.carpetas_a_mirar('Chadwick Perez'),
                         ['letra CH', 'letra C'])

    def test_c_tambien_mira_ch(self):
        self.assertEqual(carpetas.carpetas_a_mirar('Cortes Rojas'),
                         ['letra C', 'letra CH'])

    def test_enie_y_ene_caen_en_el_mismo_grupo(self):
        self.assertEqual(carpetas.carpetas_a_mirar('Núñez'), ['letra N - Ñ'])
        self.assertEqual(carpetas.carpetas_a_mirar('Nunez'), ['letra N - Ñ'])

    def test_grupos_q_r_y_x_y(self):
        self.assertEqual(carpetas.carpetas_a_mirar('Rojas'), ['letra Q - R'])
        self.assertEqual(carpetas.carpetas_a_mirar('Quiroga'), ['letra Q - R'])
        self.assertEqual(carpetas.carpetas_a_mirar('Yanez'), ['letra X - Y'])

    def test_letra_normal(self):
        self.assertEqual(carpetas.carpetas_a_mirar('Miranda Araya'), ['letra M'])

    def test_sin_apellido_no_hay_donde_mirar(self):
        self.assertEqual(carpetas.carpetas_a_mirar(''), [])
        self.assertEqual(carpetas.carpetas_a_mirar('   '), [])


class TestEntradasPobres(unittest.TestCase):
    def test_sin_nombre_o_sin_apellido_no_adivina(self):
        self.assertEqual(carpetas.rankear('', 'Miranda', LETRA_A)['candidatas'], [])
        self.assertEqual(carpetas.rankear('Matias', '', LETRA_A)['candidatas'], [])

    def test_las_carpetas_que_no_son_de_pacientes_no_calzan(self):
        """La raiz tiene 'z- Varios', 'Carpeta 1', 'DR AD'..."""
        basura = ['z- Varios', 'Carpeta 1', 'DR AD', '1 RX DR VIAL', 'Modelos 3D']
        r = carpetas.rankear('Matias', 'Miranda Araya', basura)
        self.assertEqual(r['candidatas'], [])

    def test_paciente_suelto_en_la_raiz_si_se_encuentra(self):
        r = carpetas.rankear('Martina', 'Peñaloza Manzano',
                             ['z- Varios', 'Peñaloza Manzano Martina'])
        self.assertEqual(_nombres(r), ['Peñaloza Manzano Martina'])


class TestSincronizarExtension(unittest.TestCase):
    """El ayudante se trae el F2 nuevo desde la carpeta compartida.

    Solo se prueba `_config_personalizado`, que es donde esta el peligro: es lo
    que decide si la sincronizacion ACTUALIZA el F2 o lo ROMPE. La copia de
    archivos no se prueba acá (toca red y disco).
    """

    CFG = ("window.DDASIS_CONFIG = {\n"
           "  apiBase: 'https://ortodonciarichard.onrender.com',\n"
           "  adminToken: 'eltokendelbackend',\n"
           "  carpetaUrl: 'http://127.0.0.1:8777',\n"
           "  carpetaToken: 'LA-LLAVE-DE-OTRO-PC',\n"
           "  extVersion: ''\n"
           "};\n")

    def test_la_llave_de_este_pc_pisa_la_del_origen(self):
        """⚠️ Lo que impide que sincronizar rompa el F2 de todos los PC.

        El config.js compartido trae la llave del PC donde se edita. Si viajara
        tal cual, cada PC quedaria con una llave que su ayudante no reconoce.
        """
        r = agente._config_personalizado(self.CFG, 'abcdef1234', 'mi-llave-local')
        self.assertIn("carpetaToken: 'mi-llave-local'", r)
        self.assertNotIn('LA-LLAVE-DE-OTRO-PC', r)

    def test_lo_demas_del_origen_si_viaja(self):
        """El sentido de todo esto: lo central se actualiza desde un solo lugar."""
        r = agente._config_personalizado(self.CFG, 'abcdef1234', 'mi-llave-local')
        self.assertIn("adminToken: 'eltokendelbackend'", r)
        self.assertIn("apiBase: 'https://ortodonciarichard.onrender.com'", r)

    def test_la_version_es_la_huella_de_lo_instalado(self):
        """La pone el ayudante, no una persona: por eso no se puede olvidar."""
        r = agente._config_personalizado(self.CFG, 'abcdef1234567890', 'k')
        self.assertIn("extVersion: 'abcdef12'", r)

    def test_un_config_sin_llave_se_rechaza(self):
        """⚠️ Se aborta ANTES de tocar la instalacion que hoy funciona.

        Copiar primero y fallar despues dejaria el PC sin F2.
        """
        with self.assertRaises(ValueError):
            agente._config_personalizado('window.DDASIS_CONFIG = {};', 'abc', 'k')

    def test_no_se_inventa_el_campo_de_version(self):
        """Un config.js viejo sin `extVersion` se sincroniza igual, sin avisos."""
        viejo = "window.DDASIS_CONFIG = {\n  carpetaToken: 'x'\n};\n"
        r = agente._config_personalizado(viejo, 'abcdef12', 'k')
        self.assertIn("carpetaToken: 'k'", r)
        self.assertNotIn('extVersion', r)


if __name__ == '__main__':
    unittest.main(verbosity=2)
