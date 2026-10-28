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
        # Desde 2026-10-01 Inventario no edita compras (es una acción del Historial, que
        # ya no tiene): se le cierra antes, con 404.
        self.assertEqual(r.status_code, 404)
        self.assertNotEqual(compras.obtener_compra(self.c_insumo)['proveedor_id'], self.externo)

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


class TestInventarioNoVeNiAnota(_Base):
    """Auditoría del rol Inventario (2026-10-01): la ficha de un producto mostraba las
    compras de administración (un equipo de $4.000.000 anotado en «Otros»), y podía
    anotar pagos en categorías o a proveedores que después no ve."""

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
        self.insumos = compras.crear_categoria('Insumos aud', 'operacion')
        self.otros = compras.crear_categoria('Otros aud', 'administracion')
        self.conf = compras.crear_proveedor('Dr. Externo aud')
        compras.actualizar_proveedor(self.conf, confidencial=True)
        self.equipo = compras.crear_producto('Equipo dental aud')
        for fecha, cat, prov, precio in (('2026-01-10', self.insumos, None, 1000),
                                         ('2026-05-10', self.otros, None, 4000000),
                                         ('2026-06-10', self.insumos, self.conf, 777)):
            compras.crear_compra({'fecha': fecha, 'tipo_gasto': 'variable', 'moneda': 'CLP',
                                  'categoria_id': cat, 'proveedor_id': prov},
                                 [{'producto_id': self.equipo, 'cantidad': 1, 'precio_unitario': precio}])

    def _ficha(self, h):
        return self.app.get(f'/api/compras/productos/{self.equipo}', headers=h).get_json()['producto']

    def test_la_ficha_del_producto_no_muestra_compras_que_no_ve(self):
        p = self._ficha(self.h_inv)
        self.assertEqual([h['precio_unitario'] for h in p['historial_precios']], [1000])
        self.assertEqual(p['ultima_compra']['precio_unitario'], 1000)

    def test_la_lista_de_stock_tampoco(self):
        j = self.app.get('/api/compras/productos?detalle=1', headers=self.h_inv).get_json()
        p = next(x for x in j['productos'] if x['id'] == self.equipo)
        self.assertEqual(p['ultima_compra']['precio_unitario'], 1000)

    def test_el_admin_sigue_viendo_todo(self):
        p = self._ficha(self.h_adm)
        self.assertEqual(len(p['historial_precios']), 3)
        self.assertEqual(p['ultima_compra']['precio_unitario'], 777)

    def test_no_puede_anotar_en_una_categoria_que_no_ve(self):
        for cab in ({'categoria_id': self.otros}, {'proveedor_id': self.conf},
                    {'categoria_id': 'abc'}):
            base = {'fecha': '2026-10-01', 'tipo_gasto': 'variable', 'moneda': 'CLP', 'total': 1}
            base.update(cab)
            r = self.app.post('/api/compras/compras', headers=self.h_inv,
                              json={'cabecera': base, 'items': []})
            self.assertEqual(r.status_code, 400, cab)
        r = self.app.post('/api/compras/suscripciones', headers=self.h_inv, json={
            'nombre': 'x', 'monto': 1, 'dia_mes': 1, 'categoria_id': self.otros,
            'fecha_inicio': '2026-10-01'})
        # Recurrentes ya no es del rol Inventario (2026-10-01): 403 antes de mirar la categoría.
        self.assertEqual(r.status_code, 403)

    def test_si_puede_anotar_insumos_y_el_admin_todo(self):
        base = {'fecha': '2026-10-01', 'tipo_gasto': 'variable', 'moneda': 'CLP', 'total': 1}
        r = self.app.post('/api/compras/compras', headers=self.h_inv,
                          json={'cabecera': {**base, 'categoria_id': self.insumos}, 'items': []})
        self.assertEqual(r.status_code, 200)
        r = self.app.post('/api/compras/compras', headers=self.h_adm,
                          json={'cabecera': {**base, 'categoria_id': self.otros}, 'items': []})
        self.assertEqual(r.status_code, 200)


class TestInventarioSinHistorialNiRecurrentes(_Base):
    """Pedido del usuario (2026-10-01): el rol Inventario no ve las pestañas Historial
    ni Recurrentes. Se cierra en el SERVIDOR, no solo escondiendo el botón."""

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
        reg = compras.crear_usuario('reg', 'Reg', 'clave-larga-123', rol='registro')
        self.h_inv = {'X-Compras-Token': compras.crear_sesion(inv)}
        self.h_reg = {'X-Compras-Token': compras.crear_sesion(reg)}
        self.insumos = compras.crear_categoria('Insumos tabs', 'operacion')
        base = {'fecha': '2026-09-10', 'tipo_gasto': 'variable', 'moneda': 'CLP',
                'categoria_id': self.insumos, 'total': 1000}
        self.normal = compras.crear_compra(dict(base), [])
        self.pendiente = compras.crear_compra(dict(base, detalle_pendiente=True), [])
        self.sub, _ = compras.crear_suscripcion({'nombre': 'Internet', 'monto': 1000, 'dia_mes': 28,
                                                 'categoria_id': self.insumos,
                                                 'fecha_inicio': '2026-09-01'})

    def test_las_capacidades(self):
        self.assertNotIn('historial', compras.CAPS['inventario'])
        self.assertNotIn('recurrentes', compras.CAPS['inventario'])
        for rol in ('admin', 'registro'):
            self.assertTrue({'historial', 'recurrentes'} <= compras.CAPS[rol], rol)
        for rol in ('solicitante', 'lectura'):          # conservan lo que ya tenían
            self.assertIn('historial', compras.CAPS[rol])

    def test_no_ve_la_lista_de_compras(self):
        self.assertEqual(self.app.get('/api/compras/compras', headers=self.h_inv).status_code, 403)
        self.assertEqual(self.app.get('/api/compras/compras', headers=self.h_reg).status_code, 200)

    def test_no_abre_una_compra_por_su_numero_salvo_las_por_detallar(self):
        r = self.app.get(f'/api/compras/compras/{self.normal}', headers=self.h_inv)
        self.assertEqual(r.status_code, 404)
        r = self.app.get(f'/api/compras/compras/{self.pendiente}', headers=self.h_inv)
        self.assertEqual(r.status_code, 200)          # «Por detallar» sigue funcionando
        r = self.app.get(f'/api/compras/compras/{self.normal}', headers=self.h_reg)
        self.assertEqual(r.status_code, 200)

    def test_no_edita_compras(self):
        r = self.app.post('/api/compras/compras/actualizar', headers=self.h_inv,
                          json={'id': self.normal, 'notas': 'x'})
        self.assertEqual(r.status_code, 404)

    def test_la_foto_solo_de_las_por_detallar(self):
        Path(compras.FOTOS_DIR).mkdir(parents=True, exist_ok=True)
        for nombre, cid in (('normal.jpg', self.normal), ('pend.jpg', self.pendiente)):
            (Path(compras.FOTOS_DIR) / nombre).write_bytes(b'x')
            con = compras._conn()
            con.execute('UPDATE compras SET foto_path=? WHERE id=?', (nombre, cid))
            con.commit(); con.close()
        self.assertEqual(self.app.get('/api/compras/foto/normal.jpg', headers=self.h_inv).status_code, 404)
        self.assertEqual(self.app.get('/api/compras/foto/pend.jpg', headers=self.h_inv).status_code, 200)

    def test_sigue_detallando_y_registrando_compras(self):
        r = self.app.post('/api/compras/compras/detallar', headers=self.h_inv, json={
            'id': self.pendiente, 'items': [{'producto_id': compras.crear_producto('X tabs'),
                                             'cantidad': 1, 'precio_unitario': 1000}]})
        self.assertEqual(r.status_code, 200)
        r = self.app.post('/api/compras/compras', headers=self.h_inv, json={'cabecera': {
            'fecha': '2026-10-01', 'tipo_gasto': 'variable', 'moneda': 'CLP',
            'categoria_id': self.insumos, 'total': 500}, 'items': []})
        self.assertEqual(r.status_code, 200)

    def test_no_ve_ni_toca_recurrentes(self):
        for metodo, ruta, body in (
                ('get', '/api/compras/suscripciones', None),
                ('post', '/api/compras/suscripciones', {'nombre': 'x', 'monto': 1, 'dia_mes': 1,
                                                        'categoria_id': self.insumos,
                                                        'fecha_inicio': '2026-10-01'}),
                ('post', '/api/compras/suscripciones/actualizar', {'id': self.sub, 'monto': 5}),
                ('post', '/api/compras/suscripciones/cortar', {'id': self.sub})):
            r = getattr(self.app, metodo)(ruta, headers=self.h_inv, json=body)
            self.assertEqual(r.status_code, 403, ruta)
        self.assertEqual(self.app.get('/api/compras/suscripciones', headers=self.h_reg).status_code, 200)
        self.assertTrue(compras.listar_suscripciones()[0]['activa'])



class TestNaturaleza(_Base):
    """Lo que no es gasto del mes (inversión, ahorro, duplicado) se marca, no se borra,
    y queda fuera de los totales. Caso real: los dos Excel que se llevaron en paralelo
    dejaron compras anotadas dos veces, y un sillón dental hundía el margen de julio."""

    def setUp(self):
        super().setUp()
        self.cat = compras.crear_categoria('Insumos', 'operacion')
        self.a = compras.crear_compra({'fecha': '2026-07-10', 'categoria_id': self.cat, 'total': 100000}, [])
        self.b = compras.crear_compra({'fecha': '2026-07-11', 'categoria_id': self.cat, 'total': 100000}, [])
        self.silla = compras.crear_compra({'fecha': '2026-07-12', 'categoria_id': self.cat, 'total': 9000000}, [])

    def test_solo_los_gastos_suman(self):
        compras.marcar_naturaleza(self.b, 'duplicado', 'es la #a')
        compras.marcar_naturaleza(self.silla, 'inversion')
        r = compras.resumen_gastos('2026-07-01', '2026-07-31')
        self.assertEqual(r['total'], 100000)
        self.assertEqual(r['fuera_de_gastos']['inversion']['total'], 9000000)
        self.assertEqual(r['fuera_de_gastos']['duplicado']['n'], 1)
        self.assertEqual(sum(x['total'] for x in compras.gasto_por_mes_ambito()), 100000)

    def test_no_se_borra_y_se_puede_deshacer(self):
        compras.marcar_naturaleza(self.b, 'duplicado')
        self.assertIn(self.b, {c['id'] for c in compras.listar_compras()})
        compras.marcar_naturaleza(self.b, 'gasto')
        self.assertEqual(compras.resumen_gastos('2026-07-01', '2026-07-31')['total'], 9200000)

    def test_naturaleza_invalida(self):
        with self.assertRaises(ValueError):
            compras.marcar_naturaleza(self.a, 'cualquiera')


class TestCorregirItem(_Base):
    """El Excel de inventario anotó 100 cajas de láminas cuando era 1 caja de 100:
    $20.490.000 en vez de $204.900, cuatro veces."""

    def setUp(self):
        super().setUp()
        self.p = compras.crear_producto('Lámina Essix (100)', unidad='caja')

    def test_corrige_total_y_stock(self):
        cid = compras.crear_compra({'fecha': '2026-07-07', 'moneda': 'CLP'},
                                   [{'producto_id': self.p, 'cantidad': 100, 'precio_unitario': 204900}])
        item = compras.obtener_compra(cid)['items'][0]
        r = compras.corregir_item(item['id'], cantidad=1)
        self.assertEqual(r['total_clp'], 204900)
        prod = next(x for x in compras.listar_productos() if x['id'] == self.p)
        self.assertEqual(prod['stock_actual'], 1)          # sumó 100, ahora 1

    def test_una_compra_del_historico_no_toca_el_stock(self):
        """El histórico importado nunca sumó stock: corregirlo no puede restar."""
        cid = compras.crear_compra({'fecha': '2026-07-07', 'moneda': 'CLP'},
                                   [{'producto_id': self.p, 'cantidad': 100, 'precio_unitario': 204900}])
        con = compras._conn()
        con.execute('DELETE FROM movimientos_stock WHERE compra_id=?', (cid,))
        con.execute('UPDATE productos SET stock_actual=0 WHERE id=?', (self.p,))
        con.commit(); con.close()
        compras.corregir_item(compras.obtener_compra(cid)['items'][0]['id'], cantidad=1)
        prod = next(x for x in compras.listar_productos() if x['id'] == self.p)
        self.assertEqual(prod['stock_actual'], 0)

    def test_cantidad_invalida(self):
        cid = compras.crear_compra({'fecha': '2026-07-07'},
                                   [{'producto_id': self.p, 'cantidad': 2, 'precio_unitario': 10}])
        with self.assertRaises(ValueError):
            compras.corregir_item(compras.obtener_compra(cid)['items'][0]['id'], cantidad=0)


class TestGastosFijos(_Base):
    """Arriendo, sueldos, PreviRed, PPM, honorarios: salen todos los meses. Un mes sin
    el arriendo anotado se veía mucho más barato y el margen mentía."""

    def setUp(self):
        super().setUp()
        self.cat = compras.crear_categoria('Otros', 'administracion')
        self.duena = compras.crear_proveedor('Dueña del local')
        self.arr = compras.crear_obligatorio('Arriendo', self.duena, self.cat, 12000000, '2026-06')

    def _pago(self, fecha, total=12000000, **extra):
        return compras.crear_compra({'fecha': fecha, 'proveedor_id': self.duena,
                                     'categoria_id': self.cat, 'total': total, **extra}, [])

    def test_un_pago_del_proveedor_en_el_mes_lo_da_por_anotado(self):
        self._pago('2026-06-30')
        est = compras.estado_obligatorios('2026-06')[0]
        self.assertEqual((est['estado'], est['pagado']), ('registrado', 12000000))
        self.assertEqual(compras.estado_obligatorios('2026-07')[0]['estado'], 'pendiente')

    def test_atrasados_son_solo_meses_cerrados(self):
        self._pago('2026-06-30')
        from datetime import date as _d
        atr = compras.obligatorios_atrasados(hoy=_d(2026, 9, 15))
        self.assertEqual([a['periodo'] for a in atr], ['2026-07', '2026-08'])   # sep en curso

    def test_no_se_exige_antes_de_su_inicio(self):
        self.assertEqual(compras.estado_obligatorios('2026-05'), [])

    def test_anotar_desde_la_lista_cubre_el_mes_aunque_se_pague_despues(self):
        """El arriendo de julio pagado el 3 de agosto cubre julio, no agosto."""
        compras.registrar_obligatorio(self.arr, '2026-07', 12100000, fecha='2026-08-03')
        self.assertEqual(compras.estado_obligatorios('2026-07')[0]['estado'], 'registrado')
        self.assertEqual(compras.estado_obligatorios('2026-08')[0]['estado'], 'pendiente')

    def test_omitir_con_nota_y_deshacer(self):
        with self.assertRaises(ValueError):
            compras.omitir_obligatorio(self.arr, '2026-07', '')     # sin nota no
        compras.omitir_obligatorio(self.arr, '2026-07', 'pagado con agosto')
        self.assertEqual(compras.estado_obligatorios('2026-07')[0]['estado'], 'omitido')
        compras.omitir_obligatorio(self.arr, '2026-07', deshacer=True)
        self.assertEqual(compras.estado_obligatorios('2026-07')[0]['estado'], 'pendiente')

    def test_corregir_el_mes_que_cubre_un_pago(self):
        """Caso real (2026-10-18): los pagos de septiembre se anotaron con la pestaña en
        octubre. Corregir el mes los mueve sin borrar ni tocar el monto."""
        cid = compras.registrar_obligatorio(self.arr, '2026-10', 12100000, fecha='2026-09-30')
        self.assertEqual(compras.estado_obligatorios('2026-09')[0]['estado'], 'pendiente')
        compras.actualizar_compra(cid, {'periodo': '2026-09'})
        self.assertEqual(compras.estado_obligatorios('2026-09')[0]['estado'], 'registrado')
        self.assertEqual(compras.estado_obligatorios('2026-10')[0]['estado'], 'pendiente')
        self.assertEqual(compras.obtener_compra(cid)['total'], 12100000)

    def test_el_mes_mal_escrito_se_rechaza_y_vacio_lo_quita(self):
        cid = compras.registrar_obligatorio(self.arr, '2026-07', 12100000, fecha='2026-08-03')
        with self.assertRaises(ValueError):
            compras.actualizar_compra(cid, {'periodo': '2026-13'})
        self.assertEqual(compras.obtener_compra(cid)['periodo'], '2026-07')
        compras.actualizar_compra(cid, {'periodo': ''})       # sin mes: cuenta por su fecha
        self.assertIsNone(compras.obtener_compra(cid)['periodo'])
        self.assertEqual(compras.estado_obligatorios('2026-08')[0]['estado'], 'registrado')

    def test_editar_otra_cosa_no_toca_el_mes(self):
        cid = compras.registrar_obligatorio(self.arr, '2026-07', 12100000, fecha='2026-08-03')
        compras.actualizar_compra(cid, {'forma_pago': 'transferencia'})
        self.assertEqual(compras.obtener_compra(cid)['periodo'], '2026-07')

    def test_un_duplicado_no_cuenta_como_pago(self):
        cid = self._pago('2026-06-30')
        compras.marcar_naturaleza(cid, 'duplicado')
        self.assertEqual(compras.estado_obligatorios('2026-06')[0]['estado'], 'pendiente')

    def test_faltantes_por_mes_para_el_panel(self):
        self._pago('2026-06-30')
        self.assertEqual(compras.faltantes_por_mes(['2026-06', '2026-07']), {'2026-07': ['Arriendo']})

    def test_monto_sugerido_es_el_ultimo_pagado(self):
        self._pago('2026-06-30', total=12450000)
        self.assertEqual(compras.estado_obligatorios('2026-07')[0]['monto_sugerido'], 12450000)

    def test_inactivo_no_se_exige(self):
        compras.actualizar_obligatorio(self.arr, {'activo': False})
        self.assertEqual(compras.estado_obligatorios('2026-07'), [])



class TestCuotas(_Base):
    """Pago en 3 cuotas o 3 cheques (2026-10-28): el gasto se reparte en los meses en que
    de verdad se paga, en Reportes, KPIs e Historial."""

    def setUp(self):
        super().setUp()
        self.cat = compras.crear_categoria('Insumos', 'operacion')

    def _compra(self, total=300000, **kw):
        return compras.crear_compra(dict({'fecha': '2026-10-15', 'categoria_id': self.cat,
                                          'total': total}, **kw), [])

    def test_por_defecto_es_un_solo_pago(self):
        c = compras.obtener_compra(self._compra())
        self.assertEqual((c['cuotas'], c['cuota_inicio']), (1, None))
        self.assertEqual(compras.resumen_gastos('2026-10-01', '2026-10-31')['total'], 300000)

    def test_tres_cuotas_se_reparten_en_tres_meses(self):
        self._compra(cuotas=3)
        for mes in ('2026-10', '2026-11', '2026-12'):
            r = compras.resumen_gastos(mes + '-01', mes + '-31')
            self.assertAlmostEqual(r['total'], 100000, msg=mes)
        self.assertEqual(compras.resumen_gastos('2027-01-01', '2027-01-31')['total'], 0)
        meses = {x['mes']: x['total'] for x in compras.gasto_por_mes_ambito()}
        self.assertAlmostEqual(meses.get('2026-11', 0), 100000)
        self.assertAlmostEqual(sum(x['total'] for x in compras.gasto_por_mes_ambito()), 300000)

    def test_la_primera_cuota_puede_ser_otro_mes(self):
        cid = self._compra(cuotas=2, cuota_inicio='2026-11')
        self.assertEqual(compras.obtener_compra(cid)['cuota_inicio'], '2026-11')
        self.assertEqual(compras.resumen_gastos('2026-10-01', '2026-10-31')['total'], 0)
        self.assertAlmostEqual(compras.resumen_gastos('2026-12-01', '2026-12-31')['total'], 150000)

    def test_contar_compras_no_cuenta_cuotas(self):
        self._compra(cuotas=3)
        self.assertEqual(compras.resumen_gastos('2026-01-01', '2027-12-31')['n_compras'], 1)

    def test_cuotas_invalidas(self):
        for n in (0, 49, 'x'):
            with self.assertRaises(ValueError, msg=n):
                self._compra(cuotas=n)

    def test_historial_muestra_la_cuota_del_periodo(self):
        cid = self._compra(cuotas=3)
        lista = compras.listar_compras('2026-11-01', '2026-11-30')
        c = next(x for x in lista if x['id'] == cid)
        self.assertEqual(c['cuotas_periodo'], [2])
        self.assertAlmostEqual(c['monto_periodo'], 100000)
        self.assertNotIn(cid, {x['id'] for x in compras.listar_compras('2027-01-01', '2027-01-31')})
        c = next(x for x in compras.listar_compras('2026-10-01', '2026-12-31') if x['id'] == cid)
        self.assertEqual(c['cuotas_periodo'], [1, 2, 3])
        self.assertAlmostEqual(c['monto_periodo'], 300000)
        c = next(x for x in compras.listar_compras() if x['id'] == cid)   # sin período: todo
        self.assertAlmostEqual(c['monto_periodo'], 300000)

    def test_un_pago_unico_sigue_filtrando_por_su_fecha(self):
        cid = self._compra()
        self.assertIn(cid, {x['id'] for x in compras.listar_compras('2026-10-15', '2026-10-15')})
        self.assertNotIn(cid, {x['id'] for x in compras.listar_compras('2026-10-16', '2026-10-31')})


class TestEditarCompra(_Base):
    """Editar una compra desde el Historial como si fuera una compra nueva: todo se puede
    cambiar y el stock se ajusta solo por la diferencia."""

    def setUp(self):
        super().setUp()
        self.a = compras.crear_producto('Guantes M', unidad='caja')
        self.b = compras.crear_producto('Mascarillas', unidad='caja')
        self.cab = {'fecha': '2026-10-10', 'tipo_gasto': 'variable', 'moneda': 'CLP',
                    'forma_pago': 'transferencia'}

    def _stock(self, pid):
        return compras.obtener_producto(pid)['stock_actual']

    def test_cambia_productos_y_ajusta_el_stock_por_la_diferencia(self):
        cid = compras.crear_compra(dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 10, 'precio_unitario': 1000}])
        r = compras.editar_compra(cid, dict(self.cab, forma_pago='tc_oficina_ods'), [
            {'producto_id': self.a, 'cantidad': 4, 'precio_unitario': 1000},
            {'producto_id': self.b, 'cantidad': 3, 'precio_unitario': 500}])
        self.assertEqual(r['total_clp'], 5500)
        self.assertEqual((self._stock(self.a), self._stock(self.b)), (4, 3))
        c = compras.obtener_compra(cid)
        self.assertEqual(c['forma_pago'], 'tc_oficina_ods')
        self.assertEqual(len(c['items']), 2)
        # y si después se borra, se devuelve exactamente lo que quedó sumado
        compras.eliminar_compra(cid)
        self.assertEqual((self._stock(self.a), self._stock(self.b)), (0, 0))

    def test_quitar_un_producto_le_resta_el_stock(self):
        cid = compras.crear_compra(dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 2, 'precio_unitario': 10},
            {'producto_id': self.b, 'cantidad': 5, 'precio_unitario': 10}])
        compras.editar_compra(cid, dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 2, 'precio_unitario': 10}])
        self.assertEqual((self._stock(self.a), self._stock(self.b)), (2, 0))

    def test_una_del_historico_no_toca_el_stock(self):
        cid = compras.crear_compra(dict(self.cab, notas='[hist] excel'), [
            {'producto_id': self.a, 'cantidad': 100, 'precio_unitario': 10}])
        con = compras._conn()
        con.execute('DELETE FROM movimientos_stock WHERE compra_id=?', (cid,))
        con.execute('UPDATE productos SET stock_actual=0')
        con.commit(); con.close()
        compras.editar_compra(cid, dict(self.cab, notas='excel corregido'), [
            {'producto_id': self.a, 'cantidad': 1, 'precio_unitario': 10}])
        self.assertEqual(self._stock(self.a), 0)
        self.assertTrue(compras.obtener_compra(cid)['notas'].startswith('[hist]'))
        compras.eliminar_compra(cid)                       # tampoco resta al borrarla
        self.assertEqual(self._stock(self.a), 0)

    def test_gasto_sin_productos_al_que_se_le_agregan(self):
        cid = compras.crear_compra(dict(self.cab, total=5000), [])
        compras.editar_compra(cid, dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 3, 'precio_unitario': 1000}])
        self.assertEqual(self._stock(self.a), 3)

    def test_cuotas_y_periodo_se_editan(self):
        cid = compras.crear_compra(dict(self.cab, total=90000), [])
        compras.editar_compra(cid, dict(self.cab, total=90000, cuotas=3, cuota_inicio='2026-11',
                                        periodo='2026-09'), [])
        c = compras.obtener_compra(cid)
        self.assertEqual((c['cuotas'], c['cuota_inicio'], c['periodo']), (3, '2026-11', '2026-09'))
        self.assertAlmostEqual(compras.resumen_gastos('2027-01-01', '2027-01-31')['total'], 30000)
        compras.editar_compra(cid, dict(self.cab, total=90000, cuotas=1), [])
        self.assertIsNone(compras.obtener_compra(cid)['cuota_inicio'])

    def test_conserva_naturaleza(self):
        cid = compras.crear_compra(dict(self.cab, total=5000), [])
        compras.marcar_naturaleza(cid, 'duplicado')
        compras.editar_compra(cid, dict(self.cab, total=6000), [])
        self.assertEqual(compras.obtener_compra(cid)['naturaleza'], 'duplicado')

    def test_factura_pendiente_no_recibe_productos_por_aca(self):
        cid = compras.crear_compra(dict(self.cab, total=119000, detalle_pendiente=True), [])
        with self.assertRaises(ValueError):
            compras.editar_compra(cid, dict(self.cab, total=119000), [
                {'producto_id': self.a, 'cantidad': 1, 'precio_unitario': 1}])
        compras.editar_compra(cid, dict(self.cab, total=120000), [])   # el total sí
        c = compras.obtener_compra(cid)
        self.assertEqual((c['total'], c['detalle_estado']), (120000, 'pendiente'))

    def test_factura_detallada_conserva_el_total_de_la_factura(self):
        cid = compras.crear_compra(dict(self.cab, total=119000, detalle_pendiente=True), [])
        compras.detallar_compra(cid, [{'producto_id': self.a, 'cantidad': 10,
                                       'precio_unitario': 10000}])
        compras.editar_compra(cid, dict(self.cab, total=119000), [
            {'producto_id': self.a, 'cantidad': 8, 'precio_unitario': 10000}])
        c = compras.obtener_compra(cid)
        self.assertEqual(c['total'], 119000)
        self.assertEqual(self._stock(self.a), 8)

    def test_resuelve_solicitudes_de_productos_nuevos(self):
        cid = compras.crear_compra(dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 1, 'precio_unitario': 10}])
        compras.crear_solicitud([{'producto_id': self.b, 'cantidad': 2}])
        compras.editar_compra(cid, dict(self.cab), [
            {'producto_id': self.a, 'cantidad': 1, 'precio_unitario': 10},
            {'producto_id': self.b, 'cantidad': 1, 'precio_unitario': 10}])
        con = compras._conn()
        est = con.execute('SELECT estado FROM pendientes_compra WHERE producto_id=?',
                          (self.b,)).fetchone()[0]
        con.close()
        self.assertEqual(est, 'comprado')


class TestEditarCompraRutas(_Base):
    """La ruta de edición completa respeta lo mismo que editar la cabecera."""

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
        reg = compras.crear_usuario('reg', 'Reg', 'clave-larga-123', rol='registro')
        self.h_inv = {'X-Compras-Token': compras.crear_sesion(inv)}
        self.h_reg = {'X-Compras-Token': compras.crear_sesion(reg)}
        self.cid = compras.crear_compra({'fecha': '2026-10-10', 'total': 1000}, [])

    def test_registro_edita(self):
        r = self.app.post('/api/compras/compras/editar', headers=self.h_reg, json={
            'id': self.cid, 'cabecera': {'fecha': '2026-10-11', 'total': 3000, 'cuotas': 3},
            'items': []})
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertEqual(compras.obtener_compra(self.cid)['cuotas'], 3)

    def test_inventario_no_edita(self):
        r = self.app.post('/api/compras/compras/editar', headers=self.h_inv, json={
            'id': self.cid, 'cabecera': {'total': 5}, 'items': []})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(compras.obtener_compra(self.cid)['total'], 1000)

    def test_sin_sesion(self):
        r = self.app.post('/api/compras/compras/editar', json={'id': self.cid})
        self.assertIn(r.status_code, (401, 403))

if __name__ == '__main__':
    unittest.main(verbosity=2)
