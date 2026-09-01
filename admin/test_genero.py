"""
test_genero.py - La regla nombre -> sexo, aprendida de la base de la clinica.

Cero red y cero disco: el modulo recibe los registros ya cargados.

    cd admin && python test_genero.py

Lo que se protege:

  - "Maria Jose" es mujer y "Jose Maria" es hombre. Los mismos dos tokens en
    distinto orden, distinto sexo: es el caso que motivo todo el diseño.
  - Que NUNCA se caiga al segundo nombre suelto, que convertiria a "Jose Maria"
    en mujer.
  - Que un nombre genuinamente repartido devuelva VACIO. Un "no se" honesto le
    cuesta al doctor un clic; una sugerencia equivocada con apariencia de dato
    le cuesta el percentil calculado contra la tabla del sexo que no era.
  - Que la sugerencia NO llegue a pacientes.saludo(): ahi el costo de
    equivocarse lo paga el paciente leyendo "Estimado" en su correo.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import genero   # noqa: E402


def _base():
    """Una base chica pero con la forma de la real."""
    return (
        [{'nombres': 'Maria Jose', 'genero': 'F'}] * 8 +
        [{'nombres': 'Jose Maria', 'genero': 'M'}] * 7 +
        [{'nombres': 'Jose Luis', 'genero': 'M'}] * 9 +
        [{'nombres': 'Maria Fernanda', 'genero': 'F'}] * 6 +
        [{'nombres': 'Juan Pablo', 'genero': 'M'}] * 12 +
        [{'nombres': 'Andrea', 'genero': 'F'}] * 5 +
        [{'nombres': 'Andrea', 'genero': 'M'}] * 4 +
        [{'nombres': 'Ignacio', 'genero': 'M'}] * 10 +
        [{'nombres': 'Rara Vez', 'genero': 'F'}] * 2
    )


class TestNombresCompuestos(unittest.TestCase):
    def setUp(self):
        self.t = genero.construir_tabla(_base())

    def test_maria_jose_y_jose_maria(self):
        """El caso que motivo todo: mismos tokens, distinto orden."""
        self.assertEqual(genero.inferir('María José', self.t)['sexo'], 'F')
        self.assertEqual(genero.inferir('José María', self.t)['sexo'], 'M')

    def test_resuelve_por_el_compuesto_cuando_puede(self):
        r = genero.inferir('Maria Jose Perez', self.t)
        self.assertEqual(r['via'], 'compuesto')
        self.assertEqual(r['clave'], 'maria jose')

    def test_cae_al_primer_nombre_si_el_compuesto_no_tiene_datos(self):
        r = genero.inferir('Jose Alberto', self.t)
        self.assertEqual(r['via'], 'primer_nombre')
        self.assertEqual(r['sexo'], 'M')

    def test_nunca_cae_al_segundo_nombre_suelto(self):
        """Seria exactamente la trampa: un primer nombre desconocido seguido de
        'Maria' NO puede resolverse como mujer."""
        r = genero.inferir('Xyzabc Maria', self.t)
        self.assertEqual(r['sexo'], '')

    def test_las_particulas_no_cuentan(self):
        r = genero.inferir('Maria de los Angeles', self.t)
        self.assertEqual(genero.tokens('Maria de los Angeles'), ['maria', 'angeles'])
        self.assertEqual(r['sexo'], 'F')


class TestCuandoNoSeSabe(unittest.TestCase):
    def setUp(self):
        self.t = genero.construir_tabla(_base())

    def test_nombre_repartido_devuelve_vacio(self):
        """'Andrea' esta 5 a 4 en la base: eso no es una respuesta."""
        self.assertEqual(genero.inferir('Andrea', self.t)['sexo'], '')

    def test_nombre_desconocido_devuelve_vacio(self):
        self.assertEqual(genero.inferir('Nombre Que No Existe', self.t)['sexo'], '')

    def test_pocos_casos_no_alcanzan(self):
        """Dos personas no son una regla. 'Rara' aparece 2 veces."""
        self.assertEqual(genero.inferir('Rara Vez', self.t)['sexo'], '')

    def test_vacio_y_basura(self):
        for malo in ('', None, '   ', '123', 'de la'):
            self.assertEqual(genero.inferir(malo, self.t)['sexo'], '')

    def test_el_vacio_no_es_un_sexo_por_defecto(self):
        """Devolver siempre 'M' pasaria varias pruebas de arriba; esta no."""
        r = genero.inferir('Andrea', self.t)
        self.assertEqual(r['sexo'], '')
        self.assertEqual(r['via'], '')


class TestTabla(unittest.TestCase):
    def test_solo_aprende_de_los_declarados(self):
        base = _base() + [{'nombres': 'Ignacio', 'genero': ''}] * 50
        t = genero.construir_tabla(base)
        self.assertEqual(t['ignacio'], {'M': 10, 'F': 0})

    def test_ignora_registros_rotos(self):
        base = _base() + [{}, {'genero': 'M'}, {'nombres': 'Solo', 'genero': 'X'}]
        t = genero.construir_tabla(base)
        self.assertNotIn('solo', t)

    def test_ambiguos_lista_los_repartidos(self):
        amb = {a['nombre'] for a in genero.ambiguos(_base())}
        self.assertIn('andrea', amb)
        self.assertNotIn('ignacio', amb)


class TestEvaluacion(unittest.TestCase):
    def test_no_se_califica_con_la_respuesta_a_la_vista(self):
        """Cada paciente se evalua con una tabla sin su propio voto. Sin eso, un
        nombre que aparece una sola vez daria 100% de acierto por construccion.
        """
        base = [{'nombres': 'Unico', 'genero': 'F'}]
        r = genero.evaluar(base)
        self.assertEqual(r['aciertos'], 0)
        self.assertEqual(r['sin_respuesta'], 1)

    def test_mide_lo_que_dice_medir(self):
        r = genero.evaluar(_base())
        self.assertEqual(r['evaluados'], len(_base()))
        self.assertEqual(r['aciertos'] + r['errores'] + r['sin_respuesta'], r['evaluados'])
        self.assertGreater(r['precision'], 0.9)


class TestNoContaminaElSaludo(unittest.TestCase):
    """El saludo de los correos y WhatsApp cae a 'Estimado/a' cuando no sabe, y
    eso NO cambia: ahi el costo de equivocarse lo paga el paciente leyendo su
    correo, no el doctor mirando un selector."""

    def test_saludo_sigue_siendo_neutro_sin_genero_declarado(self):
        import pacientes
        self.assertEqual(pacientes.saludo({'nombres': 'Maria Jose'}), 'o/a')
        self.assertEqual(pacientes.saludo({'nombres': 'Jose Luis'}), 'o/a')

    def test_saludo_si_usa_el_declarado(self):
        import pacientes
        self.assertEqual(pacientes.saludo({'genero': 'F'}), 'a')
        self.assertEqual(pacientes.saludo({'genero': 'M'}), 'o')


if __name__ == '__main__':
    unittest.main(verbosity=2)
