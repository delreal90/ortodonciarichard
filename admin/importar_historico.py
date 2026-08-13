# -*- coding: utf-8 -*-
"""
importar_historico.py — Carga masiva del catálogo e historial de compras (Ortodoncia Richard)

Importa el `seed.json` generado a partir de los Excel históricos del usuario
(INVENTARIO 2.0.xlsx 2023-2024 + Registro Google Forms 2025-2026) al sistema de
compras (compras.py / SQLite).

Reglas clave:
- NO toca el stock: inserta compras + compra_items directamente por SQL, SIN
  movimientos_stock (a diferencia de compras.crear_compra). El stock parte en 0
  y se ajusta después con inventario físico.
- Idempotente por REEMPLAZO: toda compra importada lleva el marcador '[hist]' al
  inicio de `notas`. Al re-importar, se borran primero las compras con ese
  marcador (y sus ítems) y se reinsertan — sin tocar jamás compras creadas a
  mano por los usuarios.
- Catálogo por FUSIÓN: productos/proveedores/categorías se dedupean por nombre
  (case-insensitive). Si ya existen, solo se completan campos vacíos (marca,
  categoría de producto); nunca se pisan datos que el usuario ya editó.

Esquema del seed:
{
  "categorias_gasto": ["Servicios", ...],
  "proveedores": [{"nombre", "rut"?, "notas"?}],
  "productos":   [{"nombre", "marca"?, "categoria_prod"?, "unidad"?}],
  "compras": [{
      "fecha" (YYYY-MM-DD), "proveedor"?, "tipo_doc"?, "nro_doc"?, "forma_pago"?,
      "tipo_gasto"?, "categoria"?, "total"?,   # total solo si NO hay items
      "notas"?, "items": [{"producto", "marca"?, "cantidad", "precio_unitario"}]
  }]
}
"""

import json

import compras

MARCADOR = '[hist]'


def _norm_key(s):
    return ' '.join((s or '').strip().upper().split())


def importar(seed, usuario_id=None):
    """Ejecuta la importación completa en UNA transacción. Devuelve resumen dict."""
    ahora = compras.ahora_cl().isoformat(timespec='seconds')
    con = compras._conn()
    resumen = {'categorias_nuevas': 0, 'proveedores_nuevos': 0, 'productos_nuevos': 0,
               'productos_actualizados': 0, 'compras_previas_reemplazadas': 0,
               'compras_importadas': 0, 'items_importados': 0, 'compras_saltadas': 0}
    try:
        # ── Categorías de gasto (fusión por nombre) ──────────────────────────
        cat_id = {}
        for r in con.execute('SELECT id, nombre FROM categorias'):
            cat_id[_norm_key(r['nombre'])] = r['id']
        for nombre in seed.get('categorias_gasto', []):
            k = _norm_key(nombre)
            if not k or k in cat_id:
                continue
            cur = con.execute(
                'INSERT INTO categorias(nombre,archivada,creado) VALUES(?,0,?)',
                (nombre.strip(), ahora[:10]))
            cat_id[k] = cur.lastrowid
            resumen['categorias_nuevas'] += 1

        # ── Proveedores (fusión por nombre) ──────────────────────────────────
        prov_id = {}
        for r in con.execute('SELECT id, nombre FROM proveedores'):
            prov_id[_norm_key(r['nombre'])] = r['id']
        for p in seed.get('proveedores', []):
            k = _norm_key(p.get('nombre'))
            if not k or k in prov_id:
                continue
            cur = con.execute(
                'INSERT INTO proveedores(nombre,rut,contacto,notas,archivado,creado) '
                'VALUES(?,?,?,?,0,?)',
                (p['nombre'].strip(), (p.get('rut') or '').strip(), '',
                 (p.get('notas') or '').strip(), ahora[:10]))
            prov_id[k] = cur.lastrowid
            resumen['proveedores_nuevos'] += 1

        # ── Productos (fusión; completa marca/categoría si faltan) ───────────
        prod_id = {}
        prod_row = {}
        for r in con.execute('SELECT id, nombre, marca, categoria_prod FROM productos'):
            k = _norm_key(r['nombre'])
            prod_id[k] = r['id']
            prod_row[k] = r
        for p in seed.get('productos', []):
            k = _norm_key(p.get('nombre'))
            if not k:
                continue
            if k in prod_id:
                ex = prod_row.get(k)
                sets, vals = [], []
                if ex is not None:
                    if p.get('marca') and not (ex['marca'] or '').strip():
                        sets.append('marca=?'); vals.append(p['marca'].strip())
                    if p.get('categoria_prod') and not (ex['categoria_prod'] or '').strip():
                        sets.append('categoria_prod=?'); vals.append(p['categoria_prod'].strip())
                if sets:
                    vals.append(prod_id[k])
                    con.execute(f'UPDATE productos SET {",".join(sets)} WHERE id=?', vals)
                    resumen['productos_actualizados'] += 1
                continue
            unidad = p.get('unidad') if p.get('unidad') in compras.UNIDADES else 'unidad'
            cur = con.execute(
                'INSERT INTO productos(nombre,categoria_prod,unidad,marca,stock_actual,'
                'stock_minimo,notas,archivado,creado) VALUES(?,?,?,?,0,0,?,0,?)',
                (p['nombre'].strip(), (p.get('categoria_prod') or '').strip(),
                 unidad, (p.get('marca') or '').strip() or None, '', ahora[:10]))
            prod_id[k] = cur.lastrowid
            resumen['productos_nuevos'] += 1

        # ── Compras: reemplazo de lo previamente importado ('[hist]') ────────
        previas = con.execute(
            "SELECT id FROM compras WHERE notas LIKE ?", (MARCADOR + '%',)).fetchall()
        if previas:
            ids = [r['id'] for r in previas]
            marcas = ','.join('?' * len(ids))
            con.execute(f'DELETE FROM compra_items WHERE compra_id IN ({marcas})', ids)
            con.execute(f'DELETE FROM compras WHERE id IN ({marcas})', ids)
            resumen['compras_previas_reemplazadas'] = len(ids)

        for c in seed.get('compras', []):
            fecha = (c.get('fecha') or '').strip()
            if not fecha:
                resumen['compras_saltadas'] += 1
                continue
            items = c.get('items') or []
            subtotal = round(sum(float(i['cantidad']) * float(i['precio_unitario'])
                                 for i in items), 2)
            total = subtotal if items else round(float(c.get('total') or 0), 2)
            if total <= 0:
                resumen['compras_saltadas'] += 1
                continue
            tipo_gasto = c.get('tipo_gasto') if c.get('tipo_gasto') in compras.TIPOS_GASTO else 'variable'
            notas = (MARCADOR + ' ' + (c.get('notas') or '').strip()).strip()
            pid = prov_id.get(_norm_key(c.get('proveedor')))
            cid = cat_id.get(_norm_key(c.get('categoria')))
            cur = con.execute(
                'INSERT INTO compras(fecha,proveedor_id,tipo_doc,nro_doc,forma_pago,'
                'tipo_gasto,categoria_id,moneda,tipo_cambio,costo_despacho,'
                'costo_importacion,total,total_clp,foto_path,notas,usuario_id,creado) '
                "VALUES(?,?,?,?,?,?,?,'CLP',1,0,0,?,?,NULL,?,?,?)",
                (fecha, pid, c.get('tipo_doc') or 'otro', (c.get('nro_doc') or '').strip(),
                 c.get('forma_pago') or 'otro', tipo_gasto, cid, total, total,
                 notas, usuario_id, ahora))
            compra_id = cur.lastrowid
            for i in items:
                ipid = prod_id.get(_norm_key(i.get('producto')))
                con.execute(
                    'INSERT INTO compra_items(compra_id,producto_id,marca,cantidad,'
                    'precio_unitario,subtotal) VALUES(?,?,?,?,?,?)',
                    (compra_id, ipid, (i.get('marca') or '').strip() or None,
                     float(i['cantidad']), float(i['precio_unitario']),
                     round(float(i['cantidad']) * float(i['precio_unitario']), 2)))
                resumen['items_importados'] += 1
            resumen['compras_importadas'] += 1

        con.commit()
        return resumen
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def importar_desde_archivo(ruta, usuario_id=None):
    with open(ruta, encoding='utf-8') as f:
        seed = json.load(f)
    return importar(seed, usuario_id)


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print('Uso: python importar_historico.py <seed.json>')
        sys.exit(1)
    print(json.dumps(importar_desde_archivo(sys.argv[1]), indent=2, ensure_ascii=False))
