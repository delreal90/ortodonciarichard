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


class TestImportarHistorico(_Base):
    """Bug real (2026-09-25): la carga del historico creaba las categorias sin
    ambito -> todas 'operacion', y el rol inventario veia los pagos de "Otros"
    (Banco de Chile, honorarios, arriendo)."""

    def test_categorias_administrativas_entran_ocultas_para_inventario(self):
        import importar_historico
        importar_historico.importar({'categorias_gasto': [
            'Insumos Generales', 'Sueldos y Leyes Sociales', 'Impuestos y Tesorería',
            'Seguros', 'Gastos Comunes', 'Otros', 'Reparaciones']})
        amb = {c['nombre']: c['ambito'] for c in compras.listar_categorias()}
        for n in ('Sueldos y Leyes Sociales', 'Impuestos y Tesorería', 'Seguros',
                  'Gastos Comunes', 'Otros'):
            self.assertEqual(amb[n], 'administracion', n)
        for n in ('Insumos Generales', 'Reparaciones'):
            self.assertEqual(amb[n], 'operacion', n)


class TestFusionarProductos(_Base):
    """El catalogo quedo con el mismo insumo dos veces (dos Excel + altas a mano).
    Fusionar tiene que juntar TODO el rastro en uno, sin perder nada."""

    def _compra(self, pid, cant, precio, fecha='2026-07-01'):
        return compras.crear_compra(
            {'fecha': fecha, 'tipo_gasto': 'variable', 'moneda': 'CLP'},
            [{'producto_id': pid, 'cantidad': cant, 'precio_unitario': precio}])

    def _dos(self, unidad_b='unidad'):
        a = compras.crear_producto('Mini tornillo 2.0 x 14 (BSS)', unidad='unidad')
        b = compras.crear_producto('Mini tornillo 2.0 x 14 (BSS)', unidad=unidad_b)
        return a, b

    def test_el_stock_se_suma_y_el_duplicado_desaparece(self):
        a, b = self._dos()
        self._compra(a, 6, 1000)
        compras.registrar_movimiento(b, 'entrada', 2, 'conteo')
        compras.fusionar_productos(b, a)
        self.assertIsNone(compras.obtener_producto(b))
        self.assertEqual(compras.obtener_producto(a)['stock_actual'], 8)

    def test_el_historial_de_compras_pasa_al_que_queda(self):
        a, b = self._dos()
        self._compra(a, 1, 1000, '2026-01-10')
        self._compra(b, 1, 1200, '2026-06-10')
        compras.fusionar_productos(b, a)
        self.assertEqual(len(compras.historial_precios(a)), 2)
        self.assertEqual(compras.ultima_compra_producto(a)['fecha'], '2026-06-10')

    def test_los_codigos_siguen_resolviendo(self):
        """El QR pegado en la caja del duplicado no puede quedar huerfano."""
        a, b = self._dos()
        compras.agregar_codigo(b, 'OR-X-1')
        compras.fusionar_productos(b, a)
        self.assertEqual(compras.producto_por_codigo('OR-X-1')['id'], a)

    def test_el_consumo_del_duplicado_cuenta_para_las_sugerencias(self):
        a, b = self._dos()
        compras.registrar_movimiento(b, 'entrada', 10)
        compras.registrar_movimiento(b, 'salida', 4)
        compras.fusionar_productos(b, a)
        self.assertEqual(compras.consumo_diario(a)['total'], 4)

    def test_dos_solicitudes_pendientes_quedan_en_una(self):
        a, b = self._dos()
        compras.crear_solicitud([{'producto_id': a, 'cantidad': 2}])
        compras.crear_solicitud([{'producto_id': b, 'cantidad': 5}])
        compras.fusionar_productos(b, a)
        pend = [p for p in compras.listar_pendientes() if p['producto_id'] == a]
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]['cantidad_sugerida'], 5)

    def test_conserva_el_minimo_mayor_y_deja_el_nombre_viejo_en_notas(self):
        a = compras.crear_producto('Guantes M', unidad='caja', stock_minimo=2)
        b = compras.crear_producto('Guantes nitrilo talla M', unidad='caja', stock_minimo=5)
        compras.fusionar_productos(b, a)
        p = compras.obtener_producto(a)
        self.assertEqual(p['stock_minimo'], 5)
        self.assertIn('Guantes nitrilo talla M', p['notas'])

    def test_deja_rastro_en_los_movimientos(self):
        a, b = self._dos()
        compras.registrar_movimiento(b, 'entrada', 2)
        compras.fusionar_productos(b, a)
        movs = compras.movimientos_producto(a)
        self.assertTrue(any('Fusión' in (m['motivo'] or '') for m in movs))

    def test_unidades_distintas_se_rechazan(self):
        """3 cajas + 40 unidades no son 43 de nada."""
        a, b = self._dos(unidad_b='caja')
        with self.assertRaises(ValueError):
            compras.fusionar_productos(b, a)
        self.assertIsNotNone(compras.obtener_producto(b))
        compras.fusionar_productos(b, a, forzar_unidad=True)
        self.assertIsNone(compras.obtener_producto(b))

    def test_consigo_mismo_se_rechaza(self):
        a, _ = self._dos()
        with self.assertRaises(ValueError):
            compras.fusionar_productos(a, a)


class TestPosiblesDuplicados(_Base):

    def _ids(self, pares):
        return {(p['a']['id'], p['b']['id']) for p in pares} | {(p['b']['id'], p['a']['id']) for p in pares}

    def test_mismo_nombre_es_igual(self):
        a = compras.crear_producto('Mini tornillo 2.0 x 14 (BSS)')
        b = compras.crear_producto('mini  tornillo 2.0 x 14 (bss)')
        pares = compras.posibles_duplicados()
        self.assertIn((a, b), self._ids(pares))
        self.assertEqual(pares[0]['tipo'], 'igual')

    def test_tildes_y_orden_no_importan(self):
        a = compras.crear_producto('Guantes nitrilo M')
        b = compras.crear_producto('Guantes M nítrilo')
        self.assertIn((a, b), self._ids(compras.posibles_duplicados()))

    def test_medidas_distintas_no_son_duplicado(self):
        """2.0 x 14 y 2.0 x 12 se parecen mucho y son tornillos distintos."""
        compras.crear_producto('Mini tornillo 2.0 x 14 (BSS)')
        compras.crear_producto('Mini tornillo 2.0 x 12 (BSS)')
        self.assertEqual(compras.posibles_duplicados(), [])

    def test_cuadrantes_distintos_no_son_duplicado(self):
        """Notacion de Palmer: 6┘ y └6 son dientes distintos, no el mismo tubo."""
        compras.crear_producto('Tubo Cemen. Directo T.0,18 6┘')
        compras.crear_producto('Tubo Cemen. Directo T.0,18 └6')
        compras.crear_producto('Tubo Cemen. Directo T.0,18 [_6')
        self.assertEqual(compras.posibles_duplicados(), [])

    def test_variantes_de_la_misma_familia_no_son_duplicado(self):
        """Inf/Sup, tallas y Mini/Maxi son productos distintos."""
        compras.crear_producto('Arco Acero 17x25 Dorado Inf')
        compras.crear_producto('Arco Acero 17x25 Dorado Sup')
        compras.crear_producto('Guantes Nitrilo L')
        compras.crear_producto('Guantes Nitrilo M')
        compras.crear_producto('Tornillo Expansion Mini')
        compras.crear_producto('Tornillo Expansion Maxi')
        self.assertEqual(compras.posibles_duplicados(), [])

    def test_errores_de_tipeo_se_proponen_como_parecidos(self):
        a = compras.crear_producto('Alginato Hydrogum 5')
        b = compras.crear_producto('Alginato Hydrogun 5')
        pares = compras.posibles_duplicados()
        self.assertIn((a, b), self._ids(pares))
        self.assertEqual(pares[0]['tipo'], 'parecido')


if __name__ == '__main__':
    unittest.main(verbosity=2)
