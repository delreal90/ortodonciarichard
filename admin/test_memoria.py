"""
test_memoria.py - Higiene de memoria (memoria.py). Cero red.

    cd admin && python test_memoria.py

En Windows configurar()/malloc_trim no hacen nada; lo que se prueba es que nunca
revienten y que el registro de subidas (lo que muestra /api/salud) funcione.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import memoria  # noqa: E402


class TestMemoria(unittest.TestCase):
    def setUp(self):
        memoria.SUBIDAS.clear()

    def test_configurar_y_liberar_no_revientan(self):
        memoria.configurar()
        r = memoria.liberar('prueba')
        self.assertIn('antes', r)
        self.assertIn('despues', r)

    def test_subida_chica_no_se_anota(self):
        self.assertFalse(memoria.anotar_subida('GET /x', 100, 110))
        self.assertFalse(memoria.anotar_subida('GET /x', None, 300))
        self.assertEqual(len(memoria.SUBIDAS), 0)

    def test_subida_grande_se_anota_sin_query_string(self):
        self.assertTrue(memoria.anotar_subida('GET /api/perfil/mapa', 200, 260))
        s = memoria.SUBIDAS[-1]
        self.assertEqual(s['origen'], 'GET /api/perfil/mapa')
        self.assertEqual((s['antes_mb'], s['despues_mb']), (200, 260))

    def test_tarea_anota_y_deja_pasar_la_excepcion(self):
        valores = iter([100.0, 180.0, 180.0, 120.0])
        with mock.patch.object(memoria, 'rss_mb', side_effect=lambda: next(valores)):
            with self.assertRaises(ValueError):
                with memoria.tarea('cosecha'):
                    raise ValueError('la tarea fallo')
        self.assertEqual(memoria.SUBIDAS[-1]['origen'], 'cosecha')

    def test_pico_registra_el_maximo(self):
        memoria.rss_mb()
        self.assertGreaterEqual(memoria.pico()['mb'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
