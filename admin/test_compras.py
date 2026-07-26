"""
test_compras.py - El unico modulo del proyecto que mueve dinero y stock.

Cero red. Base SQLite temporal, se crea y destruye en cada corrida.

    cd admin && python test_compras.py

Cubre lo que duele si falla:
  - Cargos recurrentes: que NO se cobre dos veces el mismo mes, que el dia 31
    caiga bien en febrero, y que cortar una suscripcion la detenga de verdad.
  - Stock: que borrar una compra devuelva el stock que habia sumado.
  - Migraciones: que init_db sea idempotente sobre una base ya creada (el bug
    del indice sobre una columna inexistente que dejaba la base a medio migrar).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='compras_test_'))
os.environ['COMPRAS_DB_PATH'] = str(_TMP / 'compras.db')
os.environ['COMPRAS_FOTOS_DIR'] = str(_TMP / 'fotos')
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
sys.path.insert(0, str(Path(__file__).parent))

import compras   # noqa: E402


class _Base(unittest.TestCase):

    def setUp(self):
        if compras.DB_PATH.exists():
            compras.DB_PATH.unlink()
        for suf in ('-wal', '-shm'):
            p = Path(str(compras.DB_PATH) + suf)
            if p.exists():
                p.unlink()
        compras.init_db()


class TestDiaAjustado(_Base):
    """Una suscripcion del dia 31 tiene que cobrar igual en los meses cortos."""

    def test_dia_31_en_meses_de_30(self):
        self.assertEqual(compras._dia_ajustado(2026, 4, 31), 30)
        self.assertEqual(compras._dia_ajustado(2026, 6, 31), 30)

    def test_dia_31_en_febrero(self):
        self.assertEqual(compras._dia_ajustado(2026, 2, 31), 28)

    def test_febrero_bisiesto(self):
        self.assertEqual(compras._dia_ajustado(2028, 2, 31), 29)

    def test_dia_normal_no_se_toca(self):
        self.assertEqual(compras._dia_ajustado(2026, 7, 15), 15)


class TestRecurrentes(_Base):

    def _crear(self, dia_mes=1, fecha_inicio=None, fecha_fin=None, monto=30000):
        return compras.crear_suscripcion({
            'nombre': 'Google Workspace', 'monto': monto, 'moneda': 'CLP',
            'forma_pago': 'tarjeta', 'dia_mes': dia_mes,
            'fecha_inicio': fecha_inicio or date(2026, 1, 1).isoformat(),
            'fecha_fin': fecha_fin, 'notas': '',
        })

    def _n_compras(self):
        con = compras._conn()
        try:
            return con.execute('SELECT COUNT(*) FROM compras').fetchone()[0]
        finally:
            con.close()

    def test_no_cobra_dos_veces_el_mismo_mes(self):
        """Anti-duplicado: el barrido corre todos los dias; solo el primero genera."""
        self._crear(dia_mes=1)
        antes = self._n_compras()
        for _ in range(5):
            compras.generar_recurrentes_pendientes()
        self.assertEqual(self._n_compras(), antes,
                         'ningun barrido extra debe generar otra compra este mes')

    def test_al_pasar_de_mes_vuelve_a_generar(self):
        self._crear(dia_mes=1)
        antes = self._n_compras()
        mes_que_viene = compras.ahora_cl() + timedelta(days=32)
        with mock.patch.object(compras, 'ahora_cl', return_value=mes_que_viene):
            compras.generar_recurrentes_pendientes()
        self.assertEqual(self._n_compras(), antes + 1)

    def test_cortar_detiene_la_generacion(self):
        sub_id, _ = self._crear(dia_mes=1)
        compras.cortar_suscripcion(sub_id)
        antes = self._n_compras()
        mes_que_viene = compras.ahora_cl() + timedelta(days=32)
        with mock.patch.object(compras, 'ahora_cl', return_value=mes_que_viene):
            compras.generar_recurrentes_pendientes()
        self.assertEqual(self._n_compras(), antes,
                         'una suscripcion cortada no puede seguir cobrando')

    def test_no_se_puede_editar_una_cortada(self):
        """Evita reabrir por accidente algo que se corto a proposito."""
        sub_id, _ = self._crear(dia_mes=1)
        compras.cortar_suscripcion(sub_id)
        with self.assertRaises(Exception):
            compras.actualizar_suscripcion(sub_id, {'monto': 99999})

    def test_fecha_fin_pasada_se_autodesactiva(self):
        sub_id, _ = self._crear(dia_mes=1,
                                fecha_fin=(date.today() - timedelta(days=1)).isoformat())
        antes = self._n_compras()
        compras.generar_recurrentes_pendientes()
        self.assertEqual(self._n_compras(), antes)


class TestStock(_Base):

    def _producto(self, minimo=0, inicial=0):
        return compras.crear_producto('Guantes M', unidad='caja',
                                      stock_minimo=minimo, stock_inicial=inicial)

    def _stock(self, pid):
        return (compras.obtener_producto(pid) or {}).get('stock_actual', 0)

    def test_una_compra_suma_stock(self):
        pid = self._producto()
        compras.crear_compra(
            {'fecha': '2026-07-01', 'tipo_gasto': 'variable', 'moneda': 'CLP'},
            [{'producto_id': pid, 'cantidad': 10, 'precio_unitario': 5000}])
        self.assertEqual(self._stock(pid), 10)

    def test_borrar_una_compra_devuelve_el_stock(self):
        """Si no revierte, el stock queda inflado para siempre y las sugerencias
        de compra salen mal."""
        pid = self._producto()
        cid = compras.crear_compra(
            {'fecha': '2026-07-01', 'tipo_gasto': 'variable', 'moneda': 'CLP'},
            [{'producto_id': pid, 'cantidad': 10, 'precio_unitario': 5000}])
        cid = cid['id'] if isinstance(cid, dict) else cid
        self.assertEqual(self._stock(pid), 10)
        compras.eliminar_compra(cid)
        self.assertEqual(self._stock(pid), 0)

    def test_borrar_deja_rastro_del_ajuste(self):
        """La reversion se registra como movimiento, no se borra el historial."""
        pid = self._producto()
        cid = compras.crear_compra(
            {'fecha': '2026-07-01', 'tipo_gasto': 'variable', 'moneda': 'CLP'},
            [{'producto_id': pid, 'cantidad': 7, 'precio_unitario': 100}])
        cid = cid['id'] if isinstance(cid, dict) else cid
        compras.eliminar_compra(cid)
        con = compras._conn()
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM movimientos_stock WHERE tipo='ajuste' AND producto_id=?",
                (pid,)).fetchone()[0]
        finally:
            con.close()
        self.assertGreaterEqual(n, 1)

    def test_gasto_sin_productos_no_toca_stock(self):
        """Arriendo, luz, servicios: monto directo, sin items."""
        cid = compras.crear_compra(
            {'fecha': '2026-07-01', 'tipo_gasto': 'fijo', 'moneda': 'CLP',
             'total': 450000}, [])
        self.assertTrue(cid)


class TestMoneda(_Base):

    def test_compra_en_dolares_guarda_total_en_pesos(self):
        """Los reportes suman total_clp para poder mezclar CLP y USD."""
        pid = compras.crear_producto('Bracket', unidad='unidad')
        cid = compras.crear_compra(
            {'fecha': '2026-07-01', 'tipo_gasto': 'variable', 'moneda': 'USD',
             'tipo_cambio': 950, 'costo_importacion': 20000},
            [{'producto_id': pid, 'cantidad': 2, 'precio_unitario': 100}])
        cid = cid['id'] if isinstance(cid, dict) else cid
        con = compras._conn()
        try:
            row = con.execute('SELECT total,total_clp FROM compras WHERE id=?',
                              (cid,)).fetchone()
        finally:
            con.close()
        self.assertEqual(row['total'], 200)                     # en USD
        self.assertEqual(row['total_clp'], 200 * 950 + 20000)   # en CLP


class TestMigraciones(_Base):

    def test_init_db_es_idempotente(self):
        """Se llama en cada arranque: no puede reventar sobre una base existente.
        El bug historico: un CREATE INDEX sobre una columna que aun no existia
        abortaba el script entero y dejaba la base a medio migrar — y NO se
        manifestaba en una base nueva, solo en las preexistentes."""
        pid = compras.crear_producto('Guantes M', stock_inicial=5)
        for _ in range(3):
            compras.init_db()
        self.assertEqual((compras.obtener_producto(pid) or {}).get('stock_actual'), 5)

    def test_las_columnas_migradas_existen(self):
        con = compras._conn()
        try:
            cols_compras = {r[1] for r in con.execute('PRAGMA table_info(compras)')}
            cols_items = {r[1] for r in con.execute('PRAGMA table_info(compra_items)')}
        finally:
            con.close()
        for c in ('moneda', 'tipo_cambio', 'costo_despacho', 'costo_importacion',
                  'total_clp', 'suscripcion_id'):
            self.assertIn(c, cols_compras, f'falta la columna {c} en compras')
        self.assertIn('marca', cols_items)


class TestCapacidades(_Base):
    """Los roles no son una escala lineal: se modelan por capacidades."""

    def test_admin_lo_puede_todo(self):
        for cap in ('escanear', 'stock', 'compras_ver', 'reportes', 'solicitar',
                    'registrar', 'admin'):
            self.assertIn(cap, compras.CAPS['admin'], f'admin deberia tener {cap}')

    def test_escaner_solo_escanea(self):
        self.assertEqual(set(compras.CAPS['escaner']), {'escanear'})

    def test_lectura_no_registra_ni_administra(self):
        self.assertNotIn('registrar', compras.CAPS['lectura'])
        self.assertNotIn('admin', compras.CAPS['lectura'])

    def test_solicitante_pide_pero_no_registra(self):
        self.assertIn('solicitar', compras.CAPS['solicitante'])
        self.assertNotIn('registrar', compras.CAPS['solicitante'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
