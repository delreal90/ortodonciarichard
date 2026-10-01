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


class TestFormaPago(_Base):
    """Filtrar por tarjeta es para cuadrar contra la cartola: no puede perder ni
    colar compras de otra forma de pago."""

    def _gasto(self, pago, monto=1000, fecha='2026-09-10'):
        compras.crear_compra({'fecha': fecha, 'tipo_gasto': 'variable', 'moneda': 'CLP',
                              'forma_pago': pago, 'total': monto}, [])

    def test_filtra_por_una_tarjeta(self):
        self._gasto('tc_oficina_ods', 1000)
        self._gasto('tc_personal_ods', 2000)
        self._gasto('transferencia', 4000)
        r = compras.listar_compras(forma_pago='tc_oficina_ods')
        self.assertEqual([c['total'] for c in r], [1000])

    def test_todas_las_tarjetas_incluye_las_antiguas_sin_especificar(self):
        self._gasto('tc_oficina_row', 1000)
        self._gasto('credito', 2000)          # compra antigua: solo decía "crédito"
        self._gasto('debito', 4000)
        r = compras.listar_compras(forma_pago='tarjetas')
        self.assertEqual(sorted(c['total'] for c in r), [1000, 2000])

    def test_las_siete_tarjetas_existen(self):
        self.assertEqual(len(compras.TARJETAS_CREDITO), 7)
        for k in compras.TARJETAS_CREDITO:
            self.assertIn(k, compras.FORMAS_PAGO)

    def test_el_excel_muestra_el_nombre_de_la_tarjeta(self):
        p = compras.crear_producto('Guantes M')
        compras.crear_compra({'fecha': '2026-09-10', 'tipo_gasto': 'variable', 'moneda': 'CLP',
                              'forma_pago': 'tc_personal_adv'},
                             [{'producto_id': p, 'cantidad': 1, 'precio_unitario': 500}])
        self.assertEqual(compras.filas_export()[0]['forma_pago'], 'T. Crédito Personal ADV')

    def test_se_puede_cambiar_la_tarjeta_de_una_compra_antigua(self):
        self._gasto('credito', 3000)
        cid = compras.listar_compras()[0]['id']
        compras.actualizar_compra(cid, {'forma_pago': 'tc_oficina_adv'})
        self.assertEqual(compras.obtener_compra(cid)['forma_pago'], 'tc_oficina_adv')
        self.assertEqual(compras.obtener_compra(cid)['total'], 3000)



class _ConfBase(_Base):
    """Lo que el rol Inventario NO puede ver: sueldos, SII, Previred, honorarios de
    doctores y servicios externos privados. La categoría sola no alcanzaba: el Dr.
    Labraña es un servicio externo (va en «Servicios», operación) y su pago tampoco lo
    debe ver quien lleva el inventario (pedido del Dr. Alberto, 2026-09-30)."""

    def setUp(self):
        super().setUp()
        self.servicios = compras.crear_categoria('Servicios', 'operacion')
        self.sueldos = compras.crear_categoria('Sueldos', 'administracion')
        self.externo = compras.crear_proveedor('Dr. Externo')
        self.insumos_prov = compras.crear_proveedor('Dental Uno')
        compras.actualizar_proveedor(self.externo, confidencial=True)
        self.c_externo = self._compra(self.externo, self.servicios, foto='liq.jpg')
        self.c_insumo = self._compra(self.insumos_prov, self.servicios)

    def _compra(self, prov, cat, foto=None):
        cid = compras.crear_compra({'fecha': '2026-09-10', 'tipo_gasto': 'variable',
                                       'moneda': 'CLP', 'proveedor_id': prov,
                                       'categoria_id': cat, 'total': 500000,
                                       'foto_path': foto}, [])
        return cid


class TestConfidencial(_ConfBase):

    def test_el_pago_a_un_proveedor_confidencial_no_se_ve_aunque_sea_de_operacion(self):
        ids = {c['id'] for c in compras.listar_compras(solo_ambito='operacion')}
        self.assertNotIn(self.c_externo, ids)
        self.assertIn(self.c_insumo, ids)

    def test_el_admin_lo_sigue_viendo(self):
        self.assertIn(self.c_externo, {c['id'] for c in compras.listar_compras()})

    def test_tampoco_entrando_por_el_detalle(self):
        self.assertIsNone(compras.obtener_compra(self.c_externo, solo_ambito='operacion'))
        self.assertIsNotNone(compras.obtener_compra(self.c_externo))

    def test_ni_sin_categoria(self):
        """Un pago sensible anotado sin categoría caía en «operación» por defecto."""
        cid = self._compra(self.externo, None)
        self.assertNotIn(cid, {c['id'] for c in compras.listar_compras(solo_ambito='operacion')})

    def test_el_nombre_del_proveedor_tampoco_aparece(self):
        nombres = {p['nombre'] for p in compras.listar_proveedores(solo_ambito='operacion')}
        self.assertNotIn('Dr. Externo', nombres)
        self.assertIn('Dental Uno', nombres)
        self.assertIn('Dr. Externo', {p['nombre'] for p in compras.listar_proveedores()})

    def test_cargos_recurrentes_filtrados(self):
        compras.crear_suscripcion({'nombre': 'Honorarios', 'proveedor_id': self.externo,
                                   'categoria_id': self.servicios, 'monto': 1, 'dia_mes': 28,
                                   'fecha_inicio': '2099-01-01'})
        compras.crear_suscripcion({'nombre': 'Contador', 'proveedor_id': self.insumos_prov,
                                   'categoria_id': self.sueldos, 'monto': 1, 'dia_mes': 28,
                                   'fecha_inicio': '2099-01-01'})
        compras.crear_suscripcion({'nombre': 'Internet', 'proveedor_id': self.insumos_prov,
                                   'categoria_id': self.servicios, 'monto': 1, 'dia_mes': 28,
                                   'fecha_inicio': '2099-01-01'})
        ve = {x['nombre'] for x in compras.listar_suscripciones(solo_ambito='operacion')}
        self.assertEqual(ve, {'Internet'})
        self.assertEqual(len(compras.listar_suscripciones()), 3)

    def test_la_foto_se_asocia_a_su_compra(self):
        self.assertEqual(compras.compra_de_foto('liq.jpg'), self.c_externo)
        self.assertIsNone(compras.compra_de_foto('no-existe.jpg'))

    def test_desmarcar_lo_vuelve_visible(self):
        compras.actualizar_proveedor(self.externo, confidencial=False)
        self.assertIn(self.c_externo,
                      {c['id'] for c in compras.listar_compras(solo_ambito='operacion')})


class TestConfidencialRutas(_ConfBase):
    """Las mismas garantías llamando a las rutas, con la sesión de un usuario
    Inventario: editar, ver la foto y el proveedor no pueden abrir un costado."""

    @classmethod
    def setUpClass(cls):
        os.environ['DENTIDESK_ENABLED'] = 'false'
        os.environ.pop('RENDER', None)
        os.environ.pop('RUN_PATIENT_SYNC', None)
        import server
        cls.app = server.app.test_client()

    def setUp(self):
        super().setUp()
        inv = compras.crear_usuario('ana', 'Ana', 'clave-larga-123', rol='inventario')
        adm = compras.crear_usuario('jefe', 'Jefe', 'clave-larga-123', rol='admin')
        self.h_inv = {'X-Compras-Token': compras.crear_sesion(inv)}
        self.h_adm = {'X-Compras-Token': compras.crear_sesion(adm)}
        (Path(compras.FOTOS_DIR)).mkdir(parents=True, exist_ok=True)
        (Path(compras.FOTOS_DIR) / 'liq.jpg').write_bytes(b'x')

    def test_no_puede_editar_lo_que_no_ve(self):
        r = self.app.post('/api/compras/compras/actualizar', headers=self.h_inv,
                          json={'id': self.c_externo, 'notas': 'x'})
        self.assertEqual(r.status_code, 404)

    def test_no_puede_mover_una_compra_a_un_proveedor_confidencial(self):
        r = self.app.post('/api/compras/compras/actualizar', headers=self.h_inv,
                          json={'id': self.c_insumo, 'proveedor_id': self.externo})
        self.assertEqual(r.status_code, 400)

    def test_no_puede_ver_la_foto(self):
        r = self.app.get('/api/compras/foto/liq.jpg', headers=self.h_inv)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.app.get('/api/compras/foto/liq.jpg', headers=self.h_adm).status_code, 200)

    def test_no_puede_desmarcar_un_proveedor(self):
        r = self.app.post('/api/compras/proveedores/actualizar', headers=self.h_inv,
                          json={'id': self.insumos_prov, 'confidencial': True})
        self.assertEqual(r.status_code, 403)
        r = self.app.post('/api/compras/proveedores/actualizar', headers=self.h_inv,
                          json={'id': self.externo, 'nombre': 'otro'})
        self.assertEqual(r.status_code, 404)

    def test_la_lista_de_proveedores_de_la_ruta_lo_esconde(self):
        j = self.app.get('/api/compras/proveedores', headers=self.h_inv).get_json()
        self.assertNotIn('Dr. Externo', {p['nombre'] for p in j['proveedores']})

    def test_el_admin_si_lo_marca(self):
        r = self.app.post('/api/compras/proveedores/actualizar', headers=self.h_adm,
                          json={'id': self.insumos_prov, 'confidencial': True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(compras.proveedor_confidencial(self.insumos_prov))

class TestDetalleEnDosManos(_Base):
    """Factura en dos manos: una persona ingresa la factura (total, forma de pago) y
    otra los productos. El total de la factura manda; el stock se suma una sola vez."""

    def setUp(self):
        super().setUp()
        self.insumos = compras.crear_categoria('Insumos dos manos', 'operacion')
        self.sueldos = compras.crear_categoria('Sueldos dos manos', 'administracion')
        self.guantes = compras.crear_producto('Guantes M', unidad='caja')
        self.fresa = compras.crear_producto('Fresa', unidad='unidad')

    def _factura(self, total=119000, **extra):
        cab = {'fecha': '2026-09-10', 'tipo_gasto': 'variable', 'moneda': 'CLP',
               'forma_pago': 'tc_oficina_ods', 'categoria_id': self.insumos,
               'total': total, 'detalle_pendiente': True, 'detalle_nota': 'en el escritorio'}
        cab.update(extra)
        return compras.crear_compra(cab, [])

    def _stock(self, pid):
        return compras.obtener_producto(pid)['stock_actual']

    def test_la_factura_pendiente_cuenta_como_gasto_y_no_toca_stock(self):
        cid = self._factura()
        self.assertEqual(compras.obtener_compra(cid)['detalle_estado'], 'pendiente')
        self.assertEqual(compras.resumen_gastos()['total'], 119000)
        self.assertEqual(self._stock(self.guantes), 0)
        self.assertEqual(compras.contar_por_detallar(), 1)

    def test_detallar_suma_stock_y_alimenta_el_historial_de_precios(self):
        cid = self._factura()
        compras.detallar_compra(cid, [
            {'producto_id': self.guantes, 'cantidad': 4, 'precio_unitario': 20000},
            {'producto_id': self.fresa, 'cantidad': 10, 'precio_unitario': 2000}])
        self.assertEqual(self._stock(self.guantes), 4)
        self.assertEqual(self._stock(self.fresa), 10)
        self.assertEqual(compras.historial_precios(self.guantes)[0]['precio_unitario'], 20000)
        self.assertEqual(compras.obtener_compra(cid)['detalle_estado'], 'completo')
        self.assertEqual(compras.contar_por_detallar(), 0)

    def test_el_total_es_el_de_la_factura_aunque_los_productos_sumen_distinto(self):
        """Los productos suman 100.000 (sin IVA) y la factura dice 119.000."""
        cid = self._factura(119000)
        compras.detallar_compra(cid, [{'producto_id': self.guantes, 'cantidad': 5,
                                       'precio_unitario': 20000}])
        c = compras.obtener_compra(cid)
        self.assertEqual((c['total'], c['total_clp']), (119000, 119000))
        self.assertEqual(compras.resumen_gastos()['total'], 119000)

    def test_detallar_dos_veces_no_suma_el_stock_dos_veces(self):
        cid = self._factura()
        items = [{'producto_id': self.guantes, 'cantidad': 3, 'precio_unitario': 1000}]
        compras.detallar_compra(cid, items)
        with self.assertRaises(ValueError):
            compras.detallar_compra(cid, items)
        self.assertEqual(self._stock(self.guantes), 3)

    def test_no_se_detalla_una_compra_normal(self):
        cid = compras.crear_compra({'fecha': '2026-09-10', 'tipo_gasto': 'fijo',
                                    'moneda': 'CLP', 'total': 50000}, [])
        with self.assertRaises(ValueError):
            compras.detallar_compra(cid, [{'producto_id': self.guantes, 'cantidad': 1,
                                           'precio_unitario': 1}])
        self.assertEqual(self._stock(self.guantes), 0)

    def test_sin_productos_no_se_puede_guardar(self):
        cid = self._factura()
        with self.assertRaises(ValueError):
            compras.detallar_compra(cid, [])
        self.assertEqual(compras.obtener_compra(cid)['detalle_estado'], 'pendiente')

    def test_editar_la_forma_de_pago_no_pisa_el_total_de_la_factura(self):
        """actualizar_compra recalcula el total desde los ítems en una compra normal;
        en una de dos manos eso cambiaría 119.000 por la suma neta."""
        cid = self._factura(119000)
        compras.detallar_compra(cid, [{'producto_id': self.guantes, 'cantidad': 5,
                                       'precio_unitario': 20000}])
        compras.actualizar_compra(cid, {'forma_pago': 'tc_personal_row'})
        c = compras.obtener_compra(cid)
        self.assertEqual((c['forma_pago'], c['total']), ('tc_personal_row', 119000))

    def test_detallar_resuelve_la_solicitud_pendiente(self):
        compras.crear_solicitud([{'producto_id': self.guantes, 'cantidad': 2}])
        cid = self._factura()
        compras.detallar_compra(cid, [{'producto_id': self.guantes, 'cantidad': 2,
                                       'precio_unitario': 1000}])
        self.assertEqual([p for p in compras.listar_pendientes()
                          if p['producto_id'] == self.guantes], [])

    def test_no_se_deja_pendiente_algo_que_inventario_no_ve(self):
        with self.assertRaises(ValueError):
            self._factura(categoria_id=self.sueldos)
        prov = compras.crear_proveedor('Dr. Externo dos manos')
        compras.actualizar_proveedor(prov, confidencial=True)
        with self.assertRaises(ValueError):
            self._factura(proveedor_id=prov)
        self.assertEqual(compras.contar_por_detallar(), 0)

    def test_no_se_puede_mover_una_pendiente_a_administracion(self):
        cid = self._factura()
        with self.assertRaises(ValueError):
            compras.actualizar_compra(cid, {'categoria_id': self.sueldos})
        self.assertEqual(compras.obtener_compra(cid)['categoria_id'], self.insumos)

    def test_borrar_al_usuario_que_detallo_conserva_la_compra(self):
        u = compras.crear_usuario('ana2', 'Ana', 'clave-larga-123', rol='inventario')
        compras.crear_usuario('jefe2', 'Jefe', 'clave-larga-123', rol='admin')
        cid = self._factura()
        compras.detallar_compra(cid, [{'producto_id': self.guantes, 'cantidad': 1,
                                       'precio_unitario': 1000}], usuario_id=u)
        compras.eliminar_usuario(u)
        c = compras.obtener_compra(cid)
        self.assertEqual((c['detalle_estado'], c['detallado_por']), ('completo', None))


class TestDetalleRutas(_Base):
    """Las rutas con la sesión de Inventario (Ana María) y de un admin (Octavio)."""

    @classmethod
    def setUpClass(cls):
        os.environ['DENTIDESK_ENABLED'] = 'false'
        os.environ.pop('RENDER', None)
        os.environ.pop('RUN_PATIENT_SYNC', None)
        import server
        cls.app = server.app.test_client()

    def setUp(self):
        super().setUp()
        inv = compras.crear_usuario('ana', 'Ana', 'clave-larga-123', rol='inventario')
        adm = compras.crear_usuario('octavio', 'Octavio', 'clave-larga-123', rol='admin')
        self.h_inv = {'X-Compras-Token': compras.crear_sesion(inv)}
        self.h_adm = {'X-Compras-Token': compras.crear_sesion(adm)}
        self.insumos = compras.crear_categoria('Insumos rutas', 'operacion')
        self.guantes = compras.crear_producto('Guantes M', unidad='caja')

    def _crear(self, headers, **cab):
        base = {'fecha': '2026-09-10', 'tipo_gasto': 'variable', 'moneda': 'CLP',
                'categoria_id': self.insumos, 'total': 119000, 'detalle_pendiente': True}
        base.update(cab)
        return self.app.post('/api/compras/compras', headers=headers,
                             json={'cabecera': base, 'items': []})

    def test_octavio_ingresa_y_ana_detalla(self):
        cid = self._crear(self.h_adm).get_json()['id']
        me = self.app.get('/api/compras/me', headers=self.h_inv).get_json()
        self.assertEqual(me['usuario']['por_detallar'], 1)
        lista = self.app.get('/api/compras/por-detallar', headers=self.h_inv).get_json()
        self.assertEqual([c['id'] for c in lista['compras']], [cid])
        r = self.app.post('/api/compras/compras/detallar', headers=self.h_inv, json={
            'id': cid, 'items': [{'producto_id': self.guantes, 'cantidad': 2, 'precio_unitario': 50000}]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(compras.obtener_producto(self.guantes)['stock_actual'], 2)
        self.assertEqual(self.app.get('/api/compras/por-detallar', headers=self.h_inv)
                         .get_json()['compras'], [])

    def test_no_puede_detallar_lo_que_no_ve(self):
        """Una compra administrativa pedida por id: 404, como en editar."""
        sueldos = compras.crear_categoria('Sueldos rutas', 'administracion')
        cid = compras.crear_compra({'fecha': '2026-09-10', 'tipo_gasto': 'fijo', 'moneda': 'CLP',
                                    'categoria_id': sueldos, 'total': 900000}, [])
        r = self.app.post('/api/compras/compras/detallar', headers=self.h_inv, json={
            'id': cid, 'items': [{'producto_id': self.guantes, 'cantidad': 1, 'precio_unitario': 1}]})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(compras.obtener_producto(self.guantes)['stock_actual'], 0)

    def test_marcar_pendiente_algo_confidencial_da_un_error_claro(self):
        prov = compras.crear_proveedor('Dr. Externo rutas')
        compras.actualizar_proveedor(prov, confidencial=True)
        r = self._crear(self.h_adm, proveedor_id=prov)
        self.assertEqual(r.status_code, 400)
        self.assertIn('confidencial', r.get_json()['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
