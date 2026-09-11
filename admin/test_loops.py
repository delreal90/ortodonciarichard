"""
test_loops.py — Los hilos del scheduler no pueden tener una variable sin inicializar.

POR QUE EXISTE
--------------
El 2026-09-10, al enganchar la proyeccion clinica al barrido diario, la linea que
inicializaba su bandera (`ya_proyecto = None`) quedo por accidente en OTRA funcion.
El resultado:

    if _CLINICO_HORA <= slot < _KPI_LIMITE and ya_proyecto != ahora.date():
        ya_proyecto = ahora.date()

Python trata `ya_proyecto` como LOCAL (se le asigna dentro de la funcion), asi que
leerla antes lanza **UnboundLocalError**. Y como todos los loops envuelven su cuerpo
en un `except Exception` que solo imprime —correcto, para que un fallo no mate el
hilo— el error no rompia nada visible: **la proyeccion simplemente no corria nunca**,
y el log se llenaba de un error cada 40 segundos.

Esa es la forma mas cara de un bug: no revienta, no aparece en ninguna prueba, y el
sistema informa que todo esta bien. Ninguna de las 986 pruebas lo detecto porque
ninguna ejecuta los hilos del scheduler: necesitan red, reloj y esperas.

QUE HACE ESTA PRUEBA
--------------------
No ejecuta los loops: los LEE. Para cada funcion `_loop_*` de server.py busca
variables locales que se lean antes de asignarse por primera vez. Es un analisis
estatico barato que cubre justo la clase de error que se nos escapo, sin necesitar
red ni relojes.

⚠️ La heuristica es deliberadamente simple (primera lectura vs. primera escritura por
numero de linea). En un `while True` eso es exactamente lo que importa: en la primera
vuelta la variable todavia no existe.
"""

import os
import sys
import ast
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server.py')


def _locales_sin_inicializar(fn):
    """[(nombre, linea_lectura)] de los locales leidos antes de asignarse.

    Un nombre es LOCAL si se le asigna en algun punto de la funcion. Los globales
    y los importados dentro de la funcion no cuentan.
    """
    asignadas, importadas = set(), set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    asignadas.add(t.id)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            if isinstance(n.target, ast.Name):
                asignadas.add(n.target.id)
        elif isinstance(n, ast.For) and isinstance(n.target, ast.Name):
            asignadas.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                importadas.add((a.asname or a.name).split('.')[0])
        elif isinstance(n, ast.withitem) and isinstance(n.optional_vars, ast.Name):
            asignadas.add(n.optional_vars.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            asignadas.add(n.name)

    primera_escritura, primera_lectura = {}, {}
    for n in ast.walk(fn):
        if not isinstance(n, ast.Name):
            continue
        destino = primera_escritura if isinstance(n.ctx, ast.Store) else primera_lectura
        if n.id not in destino or n.lineno < destino[n.id]:
            destino[n.id] = n.lineno

    malas = []
    for nombre, linea in sorted(primera_lectura.items()):
        if nombre not in asignadas or nombre in importadas:
            continue
        escritura = primera_escritura.get(nombre)
        if escritura is not None and linea < escritura:
            malas.append((nombre, linea))
    return malas


def _funciones_loop():
    with open(SERVER, encoding='utf-8') as f:
        arbol = ast.parse(f.read())
    return [n for n in ast.walk(arbol)
            if isinstance(n, ast.FunctionDef) and n.name.startswith('_loop')]


class TestLoopsDelScheduler(unittest.TestCase):

    def test_hay_loops_que_revisar(self):
        """Si el patron de nombres cambia, esta suite dejaria de revisar nada y
        pasaria en verde sin mirar una sola linea."""
        self.assertGreaterEqual(len(_funciones_loop()), 10)

    def test_ninguna_variable_se_lee_antes_de_existir(self):
        """El bug de `ya_proyecto`: se leia en la linea de arriba de donde se
        asignaba, y el except del loop se lo tragaba en silencio."""
        fallas = []
        for fn in _funciones_loop():
            for nombre, linea in _locales_sin_inicializar(fn):
                fallas.append('%s: lee %r en la linea %d antes de asignarla'
                              % (fn.name, nombre, linea))
        self.assertEqual(fallas, [], 'UnboundLocalError garantizado en:\n  '
                                     + '\n  '.join(fallas))

    def test_la_heuristica_detecta_el_caso_real(self):
        """Sin esto, un cambio que rompa el detector dejaria la prueba anterior
        pasando en verde para siempre."""
        roto = ast.parse(
            'def _loop_demo():\n'
            '    ya_corrio = None\n'
            '    while True:\n'
            '        if ya_proyecto != 1:\n'
            '            ya_proyecto = 1\n').body[0]
        self.assertEqual([n for n, _ in _locales_sin_inicializar(roto)], ['ya_proyecto'])

        sano = ast.parse(
            'def _loop_demo():\n'
            '    ya_proyecto = None\n'
            '    while True:\n'
            '        if ya_proyecto != 1:\n'
            '            ya_proyecto = 1\n').body[0]
        self.assertEqual(_locales_sin_inicializar(sano), [])


def suite():
    return unittest.defaultTestLoader.loadTestsFromTestCase(TestLoopsDelScheduler)


if __name__ == '__main__':
    ok = unittest.TextTestRunner(verbosity=2).run(suite()).wasSuccessful()
    sys.exit(0 if ok else 1)
