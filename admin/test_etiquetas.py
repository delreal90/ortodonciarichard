"""
test_etiquetas.py - Hojas de stickers: imprimir la cola de etiquetas QR en una
impresora comun, recordando que stickers de la hoja ya se usaron.

Cero red. Base SQLite temporal.

    cd admin && python test_etiquetas.py

Cubre lo que duele si falla:
  - Que NUNCA se imprima sobre un sticker ya usado (la razon de ser del sistema).
  - Que si no alcanzan los libres, siga en hojas nuevas y lo diga.
  - Que los stickers se marquen usados SOLO al confirmar (si la impresora fallo,
    no se "gastan" stickers que siguen en blanco).
  - Que si alguien agrega etiquetas entre la vista previa y la confirmacion, se
    rechace (se marcarian posiciones equivocadas).
  - El formato Demarka 7001 que publica el fabricante: 6x11, 35x25 mm, margen
    superior 0 y lateral 2,95.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='etiquetas_test_'))
os.environ['COMPRAS_DB_PATH'] = str(_TMP / 'compras.db')
os.environ['COMPRAS_FOTOS_DIR'] = str(_TMP / 'fotos')
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
sys.path.insert(0, str(Path(__file__).parent))

import compras   # noqa: E402


class _Base(unittest.TestCase):

    def setUp(self):
        for suf in ('', '-wal', '-shm'):
            p = Path(str(compras.DB_PATH) + suf)
            if p.exists():
                p.unlink()
        compras.init_db()
        self.guantes = compras.crear_producto('Guantes Nitrilo M', 'Clínica', 'caja')
        self.fresa = compras.crear_producto('Fresa Composite', 'Clínica', 'unidad')

    def _encolar(self, prod, n, codigo=None):
        return compras.encolar_impresion(prod, codigo or f'OR-{prod}-X', n)

    def _posiciones(self, plan, pagina=0):
        return [e['pos'] for e in plan['paginas'][pagina]['etiquetas']]


class TestFormato(_Base):

    def test_formato_demarka_7001_del_fabricante(self):
        f = compras.formato_etiquetas()
        self.assertEqual((f['columnas'], f['filas'], f['total']), (6, 11, 66))
        self.assertEqual((f['ancho'], f['alto']), (35.0, 25.0))
        self.assertEqual((f['margen_sup'], f['margen_izq']), (0.0, 2.95))

    def test_la_grilla_cabe_en_la_hoja_carta(self):
        f = compras.formato_etiquetas()
        self.assertLessEqual(f['margen_izq'] + f['columnas'] * f['paso_x'], f['hoja_ancho'])
        self.assertLessEqual(f['margen_sup'] + f['filas'] * f['paso_y'], f['hoja_alto'])

    def test_guardar_ajuste_de_calibracion(self):
        compras.guardar_formato_etiquetas({'ajuste_x': 1.5, 'ajuste_y': -0.8})
        f = compras.formato_etiquetas()
        self.assertEqual((f['ajuste_x'], f['ajuste_y']), (1.5, -0.8))

    def test_valores_fuera_de_rango_se_rechazan(self):
        with self.assertRaises(ValueError):
            compras.guardar_formato_etiquetas({'ajuste_x': 50})
        with self.assertRaises(ValueError):
            compras.guardar_formato_etiquetas({'columnas': 0})
        with self.assertRaises(ValueError):
            compras.guardar_formato_etiquetas({'alto': 'abc'})

    def test_restaurar_vuelve_al_formato_del_fabricante(self):
        compras.guardar_formato_etiquetas({'columnas': 3, 'ajuste_x': 2})
        compras.guardar_formato_etiquetas({}, restaurar=True)
        f = compras.formato_etiquetas()
        self.assertEqual((f['columnas'], f['ajuste_x']), (6, 0.0))


class TestNoPisarUsados(_Base):
    """La razon de ser: en una hoja a medio usar, solo se imprime en los libres."""

    def test_rellena_solo_los_libres_en_orden(self):
        compras.marcar_posiciones([0, 1, 5], usada=True)
        self._encolar(self.guantes, 4)
        plan = compras.planificar_etiquetas()
        self.assertEqual(self._posiciones(plan), [2, 3, 4, 6])
        self.assertFalse(plan['paginas'][0]['nueva'])

    def test_cantidad_se_expande_en_varias_etiquetas(self):
        self._encolar(self.guantes, 3)
        self._encolar(self.fresa, 2)
        plan = compras.planificar_etiquetas()
        self.assertEqual(plan['total'], 5)
        nombres = [e['nombre'] for e in plan['paginas'][0]['etiquetas']]
        self.assertEqual(nombres, ['Guantes Nitrilo M'] * 3 + ['Fresa Composite'] * 2)

    def test_si_no_alcanzan_sigue_en_una_hoja_nueva(self):
        compras.marcar_posiciones(list(range(60)), usada=True)   # quedan 6 libres
        self._encolar(self.guantes, 10)
        plan = compras.planificar_etiquetas()
        self.assertEqual(len(plan['paginas']), 2)
        self.assertEqual(self._posiciones(plan, 0), [60, 61, 62, 63, 64, 65])
        self.assertTrue(plan['paginas'][1]['nueva'])
        self.assertEqual(self._posiciones(plan, 1), [0, 1, 2, 3])
        self.assertEqual(plan['hojas_nuevas'], 1)

    def test_hoja_llena_empieza_directo_en_una_nueva(self):
        compras.marcar_posiciones(list(range(66)), usada=True)
        self._encolar(self.guantes, 2)
        plan = compras.planificar_etiquetas()
        self.assertEqual(len(plan['paginas']), 1)
        self.assertTrue(plan['paginas'][0]['nueva'])
        self.assertEqual(self._posiciones(plan), [0, 1])

    def test_varias_hojas_completas(self):
        self._encolar(self.guantes, 140)          # 66 + 66 + 8
        plan = compras.planificar_etiquetas()
        self.assertEqual([len(p['etiquetas']) for p in plan['paginas']], [66, 66, 8])

    def test_la_vista_previa_no_marca_nada(self):
        self._encolar(self.guantes, 3)
        compras.planificar_etiquetas()
        self.assertEqual(compras.hoja_actual()['usadas'], [])
        self.assertEqual(len(compras.cola_pendiente()), 1)


class TestConfirmar(_Base):

    def test_confirmar_marca_usados_y_vacia_la_cola(self):
        compras.marcar_posiciones([0], usada=True)
        self._encolar(self.guantes, 3)
        plan = compras.planificar_etiquetas()
        r = compras.confirmar_etiquetas(plan['firma'])
        self.assertEqual(r['impresas'], 3)
        self.assertEqual(compras.hoja_actual()['usadas'], [0, 1, 2, 3])
        self.assertEqual(compras.cola_pendiente(), [])

    def test_la_siguiente_impresion_sigue_donde_quedo(self):
        self._encolar(self.guantes, 3)
        compras.confirmar_etiquetas(compras.planificar_etiquetas()['firma'])
        self._encolar(self.fresa, 2)
        self.assertEqual(self._posiciones(compras.planificar_etiquetas()), [3, 4])

    def test_al_llenarse_la_hoja_la_proxima_es_nueva(self):
        compras.marcar_posiciones(list(range(60)), usada=True)
        self._encolar(self.guantes, 10)
        compras.confirmar_etiquetas(compras.planificar_etiquetas()['firma'])
        hoja = compras.hoja_actual()          # la de la 2a pagina, con 4 usados
        self.assertEqual(hoja['usadas'], [0, 1, 2, 3])
        con = compras._conn()
        try:
            abiertas = con.execute('SELECT COUNT(*) n FROM hojas_etiquetas WHERE abierta=1').fetchone()['n']
        finally:
            con.close()
        self.assertEqual(abiertas, 1)

    def test_llenar_justo_la_hoja_la_cierra(self):
        compras.marcar_posiciones(list(range(64)), usada=True)
        self._encolar(self.guantes, 2)
        id_antes = compras.hoja_actual()['id']
        compras.confirmar_etiquetas(compras.planificar_etiquetas()['firma'])
        hoja = compras.hoja_actual()
        self.assertNotEqual(hoja['id'], id_antes)
        self.assertEqual(hoja['usadas'], [])

    def test_si_la_cola_cambio_se_rechaza_y_no_marca_nada(self):
        self._encolar(self.guantes, 3)
        firma_vieja = compras.planificar_etiquetas()['firma']
        self._encolar(self.fresa, 1)                   # alguien agrego en medio
        with self.assertRaises(ValueError):
            compras.confirmar_etiquetas(firma_vieja)
        self.assertEqual(compras.hoja_actual()['usadas'], [])
        self.assertEqual(len(compras.cola_pendiente()), 2)

    def test_si_marcaron_la_hoja_en_medio_se_rechaza(self):
        self._encolar(self.guantes, 3)
        firma_vieja = compras.planificar_etiquetas()['firma']
        compras.marcar_posiciones([0], usada=True)
        with self.assertRaises(ValueError):
            compras.confirmar_etiquetas(firma_vieja)

    def test_confirmar_sin_nada_en_cola(self):
        with self.assertRaises(ValueError):
            compras.confirmar_etiquetas(compras.planificar_etiquetas()['firma'])


class TestManejoManual(_Base):

    def test_marcar_y_desmarcar(self):
        compras.marcar_posiciones([3, 4], usada=True)
        compras.marcar_posiciones([3], usada=False)
        self.assertEqual(compras.hoja_actual()['usadas'], [4])

    def test_posicion_fuera_de_la_hoja(self):
        with self.assertRaises(ValueError):
            compras.marcar_posiciones([66], usada=True)
        with self.assertRaises(ValueError):
            compras.marcar_posiciones([-1], usada=True)

    def test_nueva_hoja_empieza_en_blanco(self):
        compras.marcar_posiciones([0, 1, 2], usada=True)
        compras.nueva_hoja()
        self.assertEqual(compras.hoja_actual()['usadas'], [])

    def test_cambiar_cantidad_y_quitar_de_la_cola(self):
        jid = self._encolar(self.guantes, 3)
        compras.cambiar_cantidad_impresion(jid, 7)
        self.assertEqual(compras.planificar_etiquetas()['total'], 7)
        compras.cambiar_cantidad_impresion(jid, 0)
        self.assertEqual(compras.cola_pendiente(), [])
        with self.assertRaises(ValueError):
            compras.cambiar_cantidad_impresion(jid, 900)

    def test_achicar_el_formato_ignora_posiciones_que_ya_no_existen(self):
        compras.marcar_posiciones([2, 65], usada=True)
        compras.guardar_formato_etiquetas({'filas': 5})       # 30 stickers
        hoja = compras.hoja_actual()
        self.assertEqual(hoja['usadas'], [2])
        self.assertEqual(hoja['libres'], 29)


if __name__ == '__main__':
    unittest.main(verbosity=2)
