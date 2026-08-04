"""
compras.py — Registro de compras/gastos + control de stock (Ortodoncia Richard)

Módulo autocontenido (mismo patrón que stats.py / consentimientos.py). Guarda todo
en una base SQLite en el disco persistente de Render. A diferencia de los .json del
resto del proyecto, aquí hay relaciones reales (compras ↔ ítems ↔ productos ↔
proveedores ↔ movimientos de stock) que justifican una base de datos.

Ruta configurable por env COMPRAS_DB_PATH. Por defecto vive junto a la base de
pacientes (mismo disco persistente): .../compras.db

Roles de usuario:
  - admin    : todo (incluye categorías, proveedores, usuarios).
  - registro : ingresar compras, productos, proveedores, mover stock.
  - lectura  : solo ver (reportes, listados).

Fase 1: compras multi-ítem, proveedores, productos con buscador, categorías, usuarios.
Fase 2: movimientos de stock, escaneo (códigos), alertas de mínimo, cola de etiquetas,
        reportes + export Excel.
Fase 3 (futura): foto/PDF/XML → formulario (OCR enchufable), gastos fijos recurrentes.
"""

import os
import math
import json
import sqlite3
import secrets
import hashlib
from pathlib import Path
from datetime import datetime, date, timedelta

import fechas


def ahora_cl():
    """Hora actual en Chile (Render corre en UTC). Ver fechas.py."""
    return fechas.ahora_chile_aware()


def _hoy_cl():
    return ahora_cl().date().isoformat()


# La base vive junto al resto del estado persistente (patient_index.json, etc.).
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
DB_PATH = Path(os.environ.get('COMPRAS_DB_PATH', _BASE_DIR / 'compras.db'))

# Carpeta para las fotos de facturas/boletas (respaldo del documento).
FOTOS_DIR = Path(os.environ.get('COMPRAS_FOTOS_DIR', _BASE_DIR / 'compras_fotos'))

# Roles y sus capacidades. Modelo por CAPACIDADES (no una escala lineal), porque los
# roles nuevos no son subconjuntos limpios unos de otros (ej. 'escaner' escanea pero no
# ve; 'solicitante' ve y solicita pero no registra compras).
#   escanear     — registrar salidas de stock (escaneo).
#   stock        — ver productos, stock, alertas, resolver códigos.
#   compras_ver  — ver el historial de compras y su detalle.
#   reportes     — ver reportes de gasto y exportar.
#   solicitar    — crear solicitudes de compra y ver pendientes.
#   registrar    — ingresar compras/productos/proveedores/movimientos/códigos.
#   admin        — usuarios, categorías, borrar compras.
ROLES = ('admin', 'registro', 'solicitante', 'lectura', 'escaner')
CAPS = {
    'admin':       {'escanear', 'stock', 'compras_ver', 'reportes', 'solicitar', 'registrar', 'admin'},
    'registro':    {'escanear', 'stock', 'compras_ver', 'reportes', 'solicitar', 'registrar'},
    'solicitante': {'escanear', 'stock', 'compras_ver', 'solicitar'},
    'lectura':     {'stock', 'compras_ver', 'reportes'},
    'escaner':     {'escanear'},
}
# Etiquetas legibles para el panel de usuarios.
ROLES_LABEL = {
    'admin': 'Administrador (todo)',
    'registro': 'Registro (ingresa compras y stock)',
    'solicitante': 'Solicitante (ve, escanea, pide compras)',
    'lectura': 'Lectura (solo ver)',
    'escaner': 'Escáner (solo escanear salidas)',
}


def tiene_cap(rol, cap):
    return cap in CAPS.get(rol, set())


# fijo = mismo monto siempre (arriendo). variable = compras que cambian (insumos).
# recurrente = se repite mes a mes (suscripciones: Google Workspace, Render, etc.).
TIPOS_GASTO = ('fijo', 'variable', 'recurrente')
TIPOS_DOC = ('factura', 'boleta', 'otro')
FORMAS_PAGO = ('efectivo', 'transferencia', 'debito', 'credito', 'cheque', 'otro')
UNIDADES = ('unidad', 'caja', 'paquete', 'litro', 'kilo', 'metro', 'par', 'set')

SESION_DIAS = 30            # validez de una sesión de login
PBKDF2_ITER = 200_000      # iteraciones para el hash de contraseña


# ── Conexión ────────────────────────────────────────────────────────────────

def _conn():
    """Abre una conexión nueva por llamada (volumen bajo — es seguro y simple).
    WAL permite lecturas concurrentes con una escritura; timeout evita 'database
    is locked' bajo los workers de gunicorn."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA foreign_keys=ON')
    return con


def init_db():
    """Crea el esquema si no existe. Idempotente — se puede llamar en cada arranque."""
    con = _conn()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT UNIQUE NOT NULL,
            nombre        TEXT NOT NULL,
            rol           TEXT NOT NULL DEFAULT 'registro',
            password_hash TEXT NOT NULL,
            salt          TEXT NOT NULL,
            activo        INTEGER NOT NULL DEFAULT 1,
            creado        TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sesiones (
            token      TEXT PRIMARY KEY,
            usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
            expira     TEXT NOT NULL,
            creado     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categorias (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre    TEXT NOT NULL,
            archivada INTEGER NOT NULL DEFAULT 0,
            creado    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS proveedores (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre    TEXT NOT NULL,
            rut       TEXT,
            contacto  TEXT,
            notas     TEXT,
            archivado INTEGER NOT NULL DEFAULT 0,
            creado    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS productos (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre        TEXT NOT NULL,
            categoria_prod TEXT,
            unidad        TEXT NOT NULL DEFAULT 'unidad',
            marca         TEXT,                 -- última marca comprada (referencial)
            stock_actual  REAL NOT NULL DEFAULT 0,
            stock_minimo  REAL NOT NULL DEFAULT 0,
            notas         TEXT,
            archivado     INTEGER NOT NULL DEFAULT 0,
            creado        TEXT NOT NULL
        );

        -- Un producto puede tener varios códigos (barras del fabricante, QR del
        -- fabricante, y/o uno propio impreso). Cualquiera resuelve al mismo producto.
        CREATE TABLE IF NOT EXISTS codigos_producto (
            codigo      TEXT PRIMARY KEY,
            producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            origen      TEXT NOT NULL DEFAULT 'fabricante',  -- fabricante | propio
            creado      TEXT NOT NULL
        );

        -- Cargos recurrentes (suscripciones): la PLANTILLA de un gasto que se repite
        -- cada mes (Google Workspace, Render, arriendo). El barrido diario genera la
        -- 'compra' del mes cuando llega el día de cobro. 'cortar' = activa=0, deja de
        -- generarse pero el historial de compras ya generadas queda intacto.
        CREATE TABLE IF NOT EXISTS suscripciones (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            proveedor_id     INTEGER REFERENCES proveedores(id),
            categoria_id     INTEGER REFERENCES categorias(id),
            monto            REAL NOT NULL,
            moneda           TEXT NOT NULL DEFAULT 'CLP',
            tipo_cambio      REAL NOT NULL DEFAULT 1,
            forma_pago       TEXT,
            dia_mes          INTEGER NOT NULL,       -- 1-31 (se ajusta a meses cortos)
            fecha_inicio     TEXT NOT NULL,
            fecha_fin        TEXT,                   -- NULL = indefinido
            activa           INTEGER NOT NULL DEFAULT 1,
            notas            TEXT,
            ultima_generada  TEXT,                   -- 'YYYY-MM' del último mes ya cobrado
            usuario_id       INTEGER REFERENCES usuarios(id),
            creado           TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS compras (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha        TEXT NOT NULL,          -- fecha de compra (YYYY-MM-DD)
            proveedor_id INTEGER REFERENCES proveedores(id),
            tipo_doc     TEXT,                   -- factura | boleta | otro
            nro_doc      TEXT,
            forma_pago   TEXT,
            tipo_gasto   TEXT NOT NULL DEFAULT 'variable',  -- fijo | variable | recurrente
            categoria_id INTEGER REFERENCES categorias(id),
            moneda       TEXT NOT NULL DEFAULT 'CLP',        -- CLP | USD
            tipo_cambio  REAL NOT NULL DEFAULT 1,            -- CLP por 1 unidad de moneda
            costo_despacho    REAL NOT NULL DEFAULT 0,       -- flete/envío (en la moneda de la compra)
            costo_importacion REAL NOT NULL DEFAULT 0,       -- aduana/courier (SIEMPRE en CLP; llega después)
            total        REAL NOT NULL DEFAULT 0,            -- total en la moneda de la compra (ítems+despacho)
            total_clp    REAL NOT NULL DEFAULT 0,            -- total convertido a CLP (para reportes)
            foto_path    TEXT,
            notas        TEXT,
            suscripcion_id INTEGER REFERENCES suscripciones(id),  -- si nació de un cargo recurrente
            usuario_id   INTEGER REFERENCES usuarios(id),
            creado       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS compra_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            compra_id       INTEGER NOT NULL REFERENCES compras(id) ON DELETE CASCADE,
            producto_id     INTEGER REFERENCES productos(id),
            marca           TEXT,               -- marca comprada en ESTA compra
            cantidad        REAL NOT NULL,
            precio_unitario REAL NOT NULL,
            subtotal        REAL NOT NULL
        );

        -- Libro mayor de stock: cada entrada (compra) y salida (consumo) queda acá.
        CREATE TABLE IF NOT EXISTS movimientos_stock (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            tipo        TEXT NOT NULL,     -- entrada | salida | ajuste
            cantidad    REAL NOT NULL,     -- siempre positiva; el 'tipo' da el signo
            motivo      TEXT,
            compra_id   INTEGER REFERENCES compras(id),
            usuario_id  INTEGER REFERENCES usuarios(id),
            creado      TEXT NOT NULL
        );

        -- Cola de etiquetas para el agente de impresión (PC de la clínica).
        CREATE TABLE IF NOT EXISTS cola_impresion (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            codigo      TEXT NOT NULL,
            cantidad    INTEGER NOT NULL DEFAULT 1,
            estado      TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | impreso | error
            creado      TEXT NOT NULL,
            procesado   TEXT
        );

        -- Solicitudes de compra: lo que un encargado pide comprar. Cada fila = un
        -- producto pendiente. Al hacerse una compra de ese producto, se marca 'comprado'.
        CREATE TABLE IF NOT EXISTS pendientes_compra (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id       INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            cantidad_sugerida REAL NOT NULL DEFAULT 0,
            motivo            TEXT,          -- manual | stock_bajo | reposicion
            nota              TEXT,
            estado            TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | comprado | cancelado
            solicitado_por    INTEGER REFERENCES usuarios(id),
            creado            TEXT NOT NULL,
            resuelto          TEXT,
            compra_id         INTEGER REFERENCES compras(id)
        );

        """)
        # Las migraciones van ANTES de los indices: en una base creada antes de una
        # feature, las columnas nuevas todavia no existen (CREATE TABLE IF NOT EXISTS
        # no las agrega) y un CREATE INDEX sobre una columna faltante aborta el
        # executescript entero, dejando la base a medio migrar.
        _migrar(con)
        con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_pend_producto ON pendientes_compra(producto_id);
        CREATE INDEX IF NOT EXISTS ix_pend_estado   ON pendientes_compra(estado);
        CREATE INDEX IF NOT EXISTS ix_sus_activa    ON suscripciones(activa);
        CREATE INDEX IF NOT EXISTS ix_compras_sus   ON compras(suscripcion_id);
        CREATE INDEX IF NOT EXISTS ix_items_compra   ON compra_items(compra_id);
        CREATE INDEX IF NOT EXISTS ix_items_producto ON compra_items(producto_id);
        CREATE INDEX IF NOT EXISTS ix_mov_producto   ON movimientos_stock(producto_id);
        CREATE INDEX IF NOT EXISTS ix_compras_fecha  ON compras(fecha);
        CREATE INDEX IF NOT EXISTS ix_cod_producto   ON codigos_producto(producto_id);
        """)
        con.commit()
    finally:
        con.close()


def _migrar(con):
    """Migraciones idempotentes para bases ya creadas (CREATE TABLE IF NOT EXISTS no
    agrega columnas a tablas existentes). Agrega columnas nuevas si faltan."""
    def _cols(tabla):
        return {r['name'] for r in con.execute(f'PRAGMA table_info({tabla})')}
    if 'marca' not in _cols('productos'):
        con.execute('ALTER TABLE productos ADD COLUMN marca TEXT')
    if 'marca' not in _cols('compra_items'):
        con.execute('ALTER TABLE compra_items ADD COLUMN marca TEXT')
    ccol = _cols('compras')
    for col, ddl in (
        ('moneda', "ALTER TABLE compras ADD COLUMN moneda TEXT NOT NULL DEFAULT 'CLP'"),
        ('tipo_cambio', 'ALTER TABLE compras ADD COLUMN tipo_cambio REAL NOT NULL DEFAULT 1'),
        ('costo_despacho', 'ALTER TABLE compras ADD COLUMN costo_despacho REAL NOT NULL DEFAULT 0'),
        ('costo_importacion', 'ALTER TABLE compras ADD COLUMN costo_importacion REAL NOT NULL DEFAULT 0'),
        ('total_clp', 'ALTER TABLE compras ADD COLUMN total_clp REAL NOT NULL DEFAULT 0'),
    ):
        if col not in ccol:
            con.execute(ddl)
    # Backfill: para compras previas (CLP), el total_clp es igual al total.
    if 'total_clp' not in ccol:
        con.execute('UPDATE compras SET total_clp=total WHERE total_clp=0')
    if 'suscripcion_id' not in ccol:
        con.execute('ALTER TABLE compras ADD COLUMN suscripcion_id INTEGER REFERENCES suscripciones(id)')


# ── Utilidades ────────────────────────────────────────────────────────────────

def _row(r):
    return dict(r) if r is not None else None


def _rows(rs):
    return [dict(r) for r in rs]


def _norm(s):
    return (s or '').strip()


# ══════════════════════════════════════════════════════════════════════════════
# USUARIOS Y SESIONES
# ══════════════════════════════════════════════════════════════════════════════

def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                            salt.encode('utf-8'), PBKDF2_ITER)
    return h.hex(), salt


def contar_usuarios():
    con = _conn()
    try:
        return con.execute('SELECT COUNT(*) AS n FROM usuarios').fetchone()['n']
    finally:
        con.close()


def crear_usuario(email, nombre, password, rol='registro'):
    email = _norm(email).lower()
    nombre = _norm(nombre)
    if not email or '@' not in email:
        raise ValueError('Email inválido')
    if not nombre:
        raise ValueError('Falta el nombre')
    if not password or len(password) < 6:
        raise ValueError('La contraseña debe tener al menos 6 caracteres')
    if rol not in ROLES:
        raise ValueError('Rol inválido')
    ph, salt = _hash_password(password)
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO usuarios(email,nombre,rol,password_hash,salt,activo,creado) '
            'VALUES(?,?,?,?,?,1,?)',
            (email, nombre, rol, ph, salt, ahora_cl().isoformat(timespec='seconds')))
        con.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ValueError('Ya existe un usuario con ese email')
    finally:
        con.close()


def verificar_login(email, password):
    """Devuelve el dict del usuario si las credenciales son válidas, o None."""
    email = _norm(email).lower()
    con = _conn()
    try:
        u = con.execute('SELECT * FROM usuarios WHERE email=? AND activo=1',
                        (email,)).fetchone()
    finally:
        con.close()
    if not u:
        return None
    calc, _ = _hash_password(password, u['salt'])
    if not secrets.compare_digest(calc, u['password_hash']):
        return None
    return {'id': u['id'], 'email': u['email'], 'nombre': u['nombre'], 'rol': u['rol']}


def crear_sesion(usuario_id):
    token = secrets.token_urlsafe(32)
    ahora = ahora_cl()
    con = _conn()
    try:
        con.execute('INSERT INTO sesiones(token,usuario_id,expira,creado) VALUES(?,?,?,?)',
                    (token, usuario_id,
                     (ahora + timedelta(days=SESION_DIAS)).isoformat(timespec='seconds'),
                     ahora.isoformat(timespec='seconds')))
        con.commit()
    finally:
        con.close()
    return token


def usuario_por_sesion(token):
    """Devuelve {id,email,nombre,rol} del usuario dueño de la sesión, o None si el
    token no existe o venció."""
    if not token:
        return None
    con = _conn()
    try:
        r = con.execute(
            'SELECT u.id,u.email,u.nombre,u.rol,s.expira '
            'FROM sesiones s JOIN usuarios u ON u.id=s.usuario_id '
            'WHERE s.token=? AND u.activo=1', (token,)).fetchone()
    finally:
        con.close()
    if not r:
        return None
    if r['expira'] < ahora_cl().isoformat(timespec='seconds'):
        cerrar_sesion(token)
        return None
    return {'id': r['id'], 'email': r['email'], 'nombre': r['nombre'], 'rol': r['rol']}


def cerrar_sesion(token):
    con = _conn()
    try:
        con.execute('DELETE FROM sesiones WHERE token=?', (token,))
        con.commit()
    finally:
        con.close()


def listar_usuarios():
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT id,email,nombre,rol,activo,creado FROM usuarios ORDER BY nombre'))
    finally:
        con.close()


def actualizar_usuario(usuario_id, nombre=None, rol=None, activo=None, password=None):
    sets, vals = [], []
    if nombre is not None:
        sets.append('nombre=?'); vals.append(_norm(nombre))
    if rol is not None:
        if rol not in ROLES:
            raise ValueError('Rol inválido')
        sets.append('rol=?'); vals.append(rol)
    if activo is not None:
        sets.append('activo=?'); vals.append(1 if activo else 0)
    if password is not None:
        if len(password) < 6:
            raise ValueError('La contraseña debe tener al menos 6 caracteres')
        ph, salt = _hash_password(password)
        sets += ['password_hash=?', 'salt=?']; vals += [ph, salt]
    if not sets:
        return
    vals.append(usuario_id)
    con = _conn()
    try:
        con.execute(f'UPDATE usuarios SET {",".join(sets)} WHERE id=?', vals)
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# CATEGORÍAS
# ══════════════════════════════════════════════════════════════════════════════

def crear_categoria(nombre):
    nombre = _norm(nombre)
    if not nombre:
        raise ValueError('Falta el nombre de la categoría')
    con = _conn()
    try:
        cur = con.execute('INSERT INTO categorias(nombre,archivada,creado) VALUES(?,0,?)',
                          (nombre, _hoy_cl()))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def listar_categorias(incluir_archivadas=False):
    con = _conn()
    try:
        q = 'SELECT * FROM categorias'
        if not incluir_archivadas:
            q += ' WHERE archivada=0'
        q += ' ORDER BY nombre'
        return _rows(con.execute(q))
    finally:
        con.close()


def renombrar_categoria(cat_id, nombre):
    nombre = _norm(nombre)
    if not nombre:
        raise ValueError('Falta el nombre')
    con = _conn()
    try:
        con.execute('UPDATE categorias SET nombre=? WHERE id=?', (nombre, cat_id))
        con.commit()
    finally:
        con.close()


def archivar_categoria(cat_id, archivar=True):
    con = _conn()
    try:
        con.execute('UPDATE categorias SET archivada=? WHERE id=?',
                    (1 if archivar else 0, cat_id))
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# PROVEEDORES
# ══════════════════════════════════════════════════════════════════════════════

def crear_proveedor(nombre, rut='', contacto='', notas=''):
    nombre = _norm(nombre)
    if not nombre:
        raise ValueError('Falta el nombre del proveedor')
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO proveedores(nombre,rut,contacto,notas,archivado,creado) '
            'VALUES(?,?,?,?,0,?)',
            (nombre, _norm(rut), _norm(contacto), _norm(notas), _hoy_cl()))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def listar_proveedores(buscar='', incluir_archivados=False):
    con = _conn()
    try:
        q = 'SELECT * FROM proveedores'
        cond, vals = [], []
        if not incluir_archivados:
            cond.append('archivado=0')
        if _norm(buscar):
            cond.append('(nombre LIKE ? OR rut LIKE ?)')
            like = f'%{_norm(buscar)}%'
            vals += [like, like]
        if cond:
            q += ' WHERE ' + ' AND '.join(cond)
        q += ' ORDER BY nombre'
        return _rows(con.execute(q, vals))
    finally:
        con.close()


def actualizar_proveedor(prov_id, **campos):
    permit = {'nombre', 'rut', 'contacto', 'notas', 'archivado'}
    sets, vals = [], []
    for k, v in campos.items():
        if k in permit:
            sets.append(f'{k}=?')
            vals.append(1 if k == 'archivado' and v else 0 if k == 'archivado' else _norm(v))
    if not sets:
        return
    vals.append(prov_id)
    con = _conn()
    try:
        con.execute(f'UPDATE proveedores SET {",".join(sets)} WHERE id=?', vals)
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# PRODUCTOS + CÓDIGOS
# ══════════════════════════════════════════════════════════════════════════════

def crear_producto(nombre, categoria_prod='', unidad='unidad', stock_minimo=0,
                   notas='', stock_inicial=0):
    nombre = _norm(nombre)
    if not nombre:
        raise ValueError('Falta el nombre del producto')
    if unidad not in UNIDADES:
        unidad = 'unidad'
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO productos(nombre,categoria_prod,unidad,stock_actual,'
            'stock_minimo,notas,archivado,creado) VALUES(?,?,?,?,?,?,0,?)',
            (nombre, _norm(categoria_prod), unidad, float(stock_inicial or 0),
             float(stock_minimo or 0), _norm(notas), _hoy_cl()))
        prod_id = cur.lastrowid
        if float(stock_inicial or 0) > 0:
            con.execute(
                'INSERT INTO movimientos_stock(producto_id,tipo,cantidad,motivo,creado) '
                'VALUES(?,?,?,?,?)',
                (prod_id, 'entrada', float(stock_inicial), 'Stock inicial',
                 ahora_cl().isoformat(timespec='seconds')))
        con.commit()
        return prod_id
    finally:
        con.close()


def listar_productos(buscar='', incluir_archivados=False):
    con = _conn()
    try:
        q = 'SELECT * FROM productos'
        cond, vals = [], []
        if not incluir_archivados:
            cond.append('archivado=0')
        if _norm(buscar):
            cond.append('(nombre LIKE ? OR categoria_prod LIKE ?)')
            like = f'%{_norm(buscar)}%'
            vals += [like, like]
        if cond:
            q += ' WHERE ' + ' AND '.join(cond)
        q += ' ORDER BY nombre'
        return _rows(con.execute(q, vals))
    finally:
        con.close()


def obtener_producto(prod_id):
    con = _conn()
    try:
        p = _row(con.execute('SELECT * FROM productos WHERE id=?', (prod_id,)).fetchone())
        if p:
            p['codigos'] = _rows(con.execute(
                'SELECT codigo,origen FROM codigos_producto WHERE producto_id=?', (prod_id,)))
        return p
    finally:
        con.close()


def actualizar_producto(prod_id, **campos):
    permit = {'nombre', 'categoria_prod', 'unidad', 'stock_minimo', 'notas', 'archivado'}
    sets, vals = [], []
    for k, v in campos.items():
        if k not in permit:
            continue
        if k == 'nombre':
            v = _norm(v)
            if not v:
                raise ValueError('El nombre no puede quedar vacío')
            sets.append('nombre=?'); vals.append(v)
        elif k == 'stock_minimo':
            sets.append('stock_minimo=?'); vals.append(float(v or 0))
        elif k == 'archivado':
            sets.append('archivado=?'); vals.append(1 if v else 0)
        else:
            sets.append(f'{k}=?'); vals.append(_norm(v))
    if not sets:
        return
    vals.append(prod_id)
    con = _conn()
    try:
        con.execute(f'UPDATE productos SET {",".join(sets)} WHERE id=?', vals)
        con.commit()
    finally:
        con.close()


def eliminar_producto(prod_id):
    """Borra un producto DE VERDAD (no solo archivar). Solo se permite si nunca tuvo
    compras registradas — borrarlo con historial de compras dejaría ítems huérfanos y
    distorsionaría reportes pasados. Si tiene historial, hay que archivarlo en su lugar
    (actualizar_producto con archivado=True) o cancelar cualquier pendiente asociado."""
    con = _conn()
    try:
        n = con.execute('SELECT COUNT(*) AS n FROM compra_items WHERE producto_id=?',
                        (prod_id,)).fetchone()['n']
        if n > 0:
            raise ValueError('Este producto tiene compras registradas — no se puede '
                             'eliminar sin perder ese historial. Archívalo en su lugar.')
        # codigos_producto, movimientos_stock y pendientes_compra tienen ON DELETE
        # CASCADE hacia productos, así que se limpian solos.
        con.execute('DELETE FROM productos WHERE id=?', (prod_id,))
        con.commit()
    finally:
        con.close()


def producto_por_codigo(codigo):
    """Resuelve un código escaneado (barras/QR de cualquier origen) a su producto."""
    codigo = _norm(codigo)
    if not codigo:
        return None
    con = _conn()
    try:
        r = con.execute(
            'SELECT p.* FROM codigos_producto c JOIN productos p ON p.id=c.producto_id '
            'WHERE c.codigo=?', (codigo,)).fetchone()
        return _row(r)
    finally:
        con.close()


def agregar_codigo(prod_id, codigo, origen='fabricante'):
    """Asocia un código a un producto (mapeo-al-primer-escaneo). Si el código ya
    estaba en OTRO producto, lanza ValueError."""
    codigo = _norm(codigo)
    if not codigo:
        raise ValueError('Código vacío')
    con = _conn()
    try:
        ya = con.execute('SELECT producto_id FROM codigos_producto WHERE codigo=?',
                         (codigo,)).fetchone()
        if ya:
            if ya['producto_id'] == prod_id:
                return False  # ya estaba en este producto
            raise ValueError('Ese código ya está asignado a otro producto')
        con.execute('INSERT INTO codigos_producto(codigo,producto_id,origen,creado) '
                    'VALUES(?,?,?,?)',
                    (codigo, prod_id, origen if origen in ('fabricante', 'propio') else 'fabricante',
                     ahora_cl().isoformat(timespec='seconds')))
        con.commit()
        return True
    finally:
        con.close()


def generar_codigo_propio(prod_id):
    """Crea un código propio único para un producto sin código de fabricante, listo
    para imprimir en etiqueta. Formato: OR-<prod_id>-<aleatorio>."""
    codigo = f"OR-{prod_id}-{secrets.token_hex(3).upper()}"
    agregar_codigo(prod_id, codigo, origen='propio')
    return codigo


def ultima_compra_producto(prod_id):
    """Última vez que se compró: fecha, proveedor, precio unitario y cantidad."""
    con = _conn()
    try:
        r = con.execute(
            'SELECT c.fecha, c.id AS compra_id, pr.nombre AS proveedor, '
            '       i.precio_unitario, i.cantidad, i.marca '
            'FROM compra_items i JOIN compras c ON c.id=i.compra_id '
            'LEFT JOIN proveedores pr ON pr.id=c.proveedor_id '
            'WHERE i.producto_id=? ORDER BY c.fecha DESC, c.id DESC LIMIT 1',
            (prod_id,)).fetchone()
        return _row(r)
    finally:
        con.close()


def historial_precios(prod_id, limite=50):
    """Evolución del precio de un producto: cada compra con fecha, proveedor y precio."""
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT c.fecha, pr.nombre AS proveedor, i.precio_unitario, i.cantidad, i.marca '
            'FROM compra_items i JOIN compras c ON c.id=i.compra_id '
            'LEFT JOIN proveedores pr ON pr.id=c.proveedor_id '
            'WHERE i.producto_id=? ORDER BY c.fecha DESC, c.id DESC LIMIT ?',
            (prod_id, limite)))
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# COMPRAS (cabecera + ítems, transaccional; suma stock)
# ══════════════════════════════════════════════════════════════════════════════

def _moneda_de(cab):
    """Normaliza moneda/tipo de cambio de una cabecera.
    Devuelve (moneda, tipo_cambio). USD → tipo_cambio = CLP por 1 USD (obligatorio);
    CLP → tipo_cambio = 1 siempre."""
    moneda = cab.get('moneda') if cab.get('moneda') in ('CLP', 'USD') else 'CLP'
    if moneda == 'CLP':
        return 'CLP', 1.0
    tc = float(cab.get('tipo_cambio') or 0)
    if tc <= 0:
        raise ValueError('Falta el tipo de cambio (CLP por dólar) para una compra en USD')
    return 'USD', tc


def _total_clp(total_moneda, tipo_cambio, costo_importacion):
    """Total en CLP = (total en su moneda × tipo de cambio) + costo de importación
    (que SIEMPRE viene en CLP, es la boleta del courier/aduana)."""
    return round(float(total_moneda) * float(tipo_cambio) + float(costo_importacion or 0), 2)


def crear_compra(cab, items, usuario_id=None, suscripcion_id=None):
    """Registra una compra completa en UNA transacción.
    cab: dict con fecha, proveedor_id, tipo_doc, nro_doc, forma_pago, tipo_gasto,
         categoria_id, foto_path, notas, moneda, tipo_cambio, costo_despacho,
         costo_importacion, total (opcional; se recalcula de los ítems).
    items: lista de dicts {producto_id, marca, cantidad, precio_unitario}.
    suscripcion_id: si esta compra nace de un cargo recurrente (suscripciones), enlaza
    de vuelta para mostrar "generado automáticamente de X" en el detalle.
    Cada ítem suma stock del producto (movimiento 'entrada')."""
    fecha = _norm(cab.get('fecha')) or _hoy_cl()
    tipo_gasto = cab.get('tipo_gasto') if cab.get('tipo_gasto') in TIPOS_GASTO else 'variable'
    items = items or []
    moneda, tc = _moneda_de(cab)
    despacho = round(float(cab.get('costo_despacho') or 0), 2)
    importacion = round(float(cab.get('costo_importacion') or 0), 2)

    # Gasto SIN productos (arriendo, luz, servicios): se registra solo el monto,
    # no toca stock. Requiere un total > 0 en la cabecera.
    if not items:
        base = round(float(cab.get('total') or 0), 2)
        if base <= 0:
            raise ValueError('La compra no tiene productos ni monto')
        total = round(base + despacho, 2)
        total_clp = _total_clp(total, tc, importacion)
        ahora = ahora_cl().isoformat(timespec='seconds')
        con = _conn()
        try:
            cur = con.execute(
                'INSERT INTO compras(fecha,proveedor_id,tipo_doc,nro_doc,forma_pago,'
                'tipo_gasto,categoria_id,moneda,tipo_cambio,costo_despacho,costo_importacion,'
                'total,total_clp,foto_path,notas,suscripcion_id,usuario_id,creado) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (fecha, cab.get('proveedor_id'), cab.get('tipo_doc'), _norm(cab.get('nro_doc')),
                 cab.get('forma_pago'), tipo_gasto, cab.get('categoria_id'), moneda, tc,
                 despacho, importacion, total, total_clp,
                 cab.get('foto_path'), _norm(cab.get('notas')), suscripcion_id, usuario_id, ahora))
            con.commit()
            return cur.lastrowid
        finally:
            con.close()

    # Calcular subtotales y total desde los ítems (fuente de verdad).
    norm_items = []
    subtotal = 0.0
    for it in items:
        pid = it.get('producto_id')
        cant = float(it.get('cantidad') or 0)
        precio = float(it.get('precio_unitario') or 0)
        if not pid or cant <= 0:
            raise ValueError('Cada ítem necesita producto y cantidad > 0')
        sub = round(cant * precio, 2)
        subtotal += sub
        norm_items.append((int(pid), _norm(it.get('marca')), cant, precio, sub))
    total = round(subtotal + despacho, 2)          # ítems + despacho, en la moneda
    total_clp = _total_clp(total, tc, importacion)

    ahora = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO compras(fecha,proveedor_id,tipo_doc,nro_doc,forma_pago,'
            'tipo_gasto,categoria_id,moneda,tipo_cambio,costo_despacho,costo_importacion,'
            'total,total_clp,foto_path,notas,suscripcion_id,usuario_id,creado) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (fecha, cab.get('proveedor_id'), cab.get('tipo_doc'), _norm(cab.get('nro_doc')),
             cab.get('forma_pago'), tipo_gasto, cab.get('categoria_id'), moneda, tc,
             despacho, importacion, total, total_clp,
             cab.get('foto_path'), _norm(cab.get('notas')), suscripcion_id, usuario_id, ahora))
        compra_id = cur.lastrowid
        for pid, marca, cant, precio, sub in norm_items:
            con.execute(
                'INSERT INTO compra_items(compra_id,producto_id,marca,cantidad,precio_unitario,'
                'subtotal) VALUES(?,?,?,?,?,?)', (compra_id, pid, marca, cant, precio, sub))
            con.execute(
                'INSERT INTO movimientos_stock(producto_id,tipo,cantidad,motivo,compra_id,'
                'usuario_id,creado) VALUES(?,?,?,?,?,?,?)',
                (pid, 'entrada', cant, f'Compra #{compra_id}', compra_id, usuario_id, ahora))
            con.execute('UPDATE productos SET stock_actual=stock_actual+? WHERE id=?',
                        (cant, pid))
            # recordar la última marca comprada del producto (referencial, prellenado)
            if marca:
                con.execute('UPDATE productos SET marca=? WHERE id=?', (marca, pid))
        # Auto-resolver: si algún producto comprado estaba pendiente por comprar, se
        # marca 'comprado' y sale de la lista de pendientes.
        _resolver_pendientes(con, [n[0] for n in norm_items], compra_id, ahora)
        con.commit()
        return compra_id
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# CARGOS RECURRENTES (suscripciones): se cargan solos mes a mes hasta que se cortan
# ══════════════════════════════════════════════════════════════════════════════

def _dia_ajustado(anio, mes, dia):
    """Si el día pedido no existe en ese mes (31 en abril, etc.), usa el último día
    real del mes. Así una suscripción del día 31 cobra el 30 en los meses cortos."""
    import calendar
    return min(int(dia), calendar.monthrange(anio, mes)[1])


def _marcar_generada(sub_id, mes_iso):
    con = _conn()
    try:
        con.execute('UPDATE suscripciones SET ultima_generada=? WHERE id=?', (mes_iso, sub_id))
        con.commit()
    finally:
        con.close()


def _proxima_cobranza(sub, hoy):
    """Próxima fecha en que se generará el cargo (o None si ya no corresponde por
    fecha_fin). No mira si YA se generó este mes; el llamador decide qué mostrar."""
    dia_obj = _dia_ajustado(hoy.year, hoy.month, sub['dia_mes'])
    mes_actual = hoy.strftime('%Y-%m')
    if sub.get('ultima_generada') == mes_actual or hoy.day >= dia_obj:
        anio, mes = (hoy.year + 1, 1) if hoy.month == 12 else (hoy.year, hoy.month + 1)
        cand = date(anio, mes, _dia_ajustado(anio, mes, sub['dia_mes']))
    else:
        cand = date(hoy.year, hoy.month, dia_obj)
    if sub.get('fecha_fin') and cand.isoformat() > sub['fecha_fin']:
        return None
    return cand.isoformat()


def crear_suscripcion(datos, usuario_id=None):
    """Crea un cargo recurrente. Si hoy ya alcanzó (o pasó) el día de cobro del mes
    actual, genera de una vez la compra de ESTE mes (para no esperar al barrido de
    mañana); si no, el barrido diario la generará cuando llegue el día.
    datos: nombre, proveedor_id, categoria_id, monto, moneda, tipo_cambio, forma_pago,
           dia_mes (1-31), fecha_inicio, fecha_fin (None/'' = indefinido), notas.
    Devuelve (suscripcion_id, compra_id_generada_o_None)."""
    nombre = _norm(datos.get('nombre'))
    if not nombre:
        raise ValueError('Falta el nombre del cargo recurrente')
    dia_mes = int(datos.get('dia_mes') or 0)
    if not (1 <= dia_mes <= 31):
        raise ValueError('El día del mes debe estar entre 1 y 31')
    monto = round(float(datos.get('monto') or 0), 2)
    if monto <= 0:
        raise ValueError('Falta el monto del cargo')
    moneda, tc = _moneda_de(datos)
    fecha_inicio = _norm(datos.get('fecha_inicio')) or _hoy_cl()
    fecha_fin = _norm(datos.get('fecha_fin')) or None
    if fecha_fin and fecha_fin < fecha_inicio:
        raise ValueError('La fecha de término no puede ser anterior al inicio')
    ahora = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO suscripciones(nombre,proveedor_id,categoria_id,monto,moneda,'
            'tipo_cambio,forma_pago,dia_mes,fecha_inicio,fecha_fin,activa,notas,'
            'ultima_generada,usuario_id,creado) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,NULL,?,?)',
            (nombre, datos.get('proveedor_id'), datos.get('categoria_id'), monto, moneda, tc,
             datos.get('forma_pago'), dia_mes, fecha_inicio, fecha_fin,
             _norm(datos.get('notas')), usuario_id, ahora))
        sub_id = cur.lastrowid
        con.commit()
    finally:
        con.close()

    compra_id = None
    hoy = ahora_cl().date()
    dia_obj = _dia_ajustado(hoy.year, hoy.month, dia_mes)
    ya_llego = (hoy.day >= dia_obj and hoy.isoformat() >= fecha_inicio
                and (not fecha_fin or hoy.isoformat() <= fecha_fin))
    if ya_llego:
        compra_id = crear_compra({
            'fecha': hoy.isoformat(), 'proveedor_id': datos.get('proveedor_id'),
            'tipo_doc': 'otro', 'forma_pago': datos.get('forma_pago'),
            'tipo_gasto': 'recurrente', 'categoria_id': datos.get('categoria_id'),
            'moneda': moneda, 'tipo_cambio': tc, 'foto_path': datos.get('foto_path'),
            'notas': f'{nombre} (cargo recurrente)', 'total': monto,
        }, [], usuario_id, suscripcion_id=sub_id)
        _marcar_generada(sub_id, hoy.strftime('%Y-%m'))
    return sub_id, compra_id


def listar_suscripciones(solo_activas=False):
    con = _conn()
    try:
        q = ('SELECT s.*, pr.nombre AS proveedor_nombre, cat.nombre AS categoria_nombre '
             'FROM suscripciones s LEFT JOIN proveedores pr ON pr.id=s.proveedor_id '
             'LEFT JOIN categorias cat ON cat.id=s.categoria_id')
        if solo_activas:
            q += ' WHERE s.activa=1'
        q += ' ORDER BY s.activa DESC, s.nombre'
        rows = _rows(con.execute(q))
    finally:
        con.close()
    hoy = ahora_cl().date()
    for r in rows:
        r['proxima_cobranza'] = _proxima_cobranza(r, hoy) if r['activa'] else None
    return rows


def actualizar_suscripcion(sub_id, campos):
    """Edita una suscripción activa (monto, día de cobro, proveedor, categoría, forma
    de pago, notas, o acortar la fecha_fin). NO reabre una ya cortada — usar
    crear_suscripcion de nuevo para eso (evita reactivar algo que se cortó a propósito)."""
    permit = {'nombre', 'proveedor_id', 'categoria_id', 'monto', 'forma_pago', 'dia_mes',
              'fecha_fin', 'notas'}
    campos = {k: v for k, v in (campos or {}).items() if k in permit}
    if 'dia_mes' in campos:
        campos['dia_mes'] = int(campos['dia_mes'])
        if not (1 <= campos['dia_mes'] <= 31):
            raise ValueError('El día del mes debe estar entre 1 y 31')
    if 'monto' in campos:
        campos['monto'] = round(float(campos['monto']), 2)
        if campos['monto'] <= 0:
            raise ValueError('Monto inválido')
    if 'nombre' in campos:
        campos['nombre'] = _norm(campos['nombre'])
    if not campos:
        return
    con = _conn()
    try:
        actual = con.execute('SELECT activa FROM suscripciones WHERE id=?', (sub_id,)).fetchone()
        if not actual:
            raise ValueError('Cargo recurrente no encontrado')
        if not actual['activa']:
            raise ValueError('Este cargo ya está cortado; no se puede editar')
        sets = ','.join(f'{k}=?' for k in campos)
        con.execute(f'UPDATE suscripciones SET {sets} WHERE id=?', [*campos.values(), sub_id])
        con.commit()
    finally:
        con.close()


def cortar_suscripcion(sub_id, fecha_fin=None):
    """Corta la recurrencia: desde ahora el barrido diario ya no la genera más. El
    historial de compras ya generadas por este cargo se conserva intacto."""
    fin = _norm(fecha_fin) or _hoy_cl()
    con = _conn()
    try:
        con.execute('UPDATE suscripciones SET activa=0, fecha_fin=? WHERE id=?', (fin, sub_id))
        con.commit()
    finally:
        con.close()


def generar_recurrentes_pendientes(usuario_id=None):
    """Barrido diario: para cada suscripción activa que ya llegó a su día de cobro
    (ajustado a meses cortos) y no se ha generado este mes, crea la compra del mes.
    Si una suscripción ya pasó su fecha_fin, se auto-desactiva sin generar más.
    Devuelve la lista de lo generado (para logging)."""
    hoy = ahora_cl().date()
    mes_actual = hoy.strftime('%Y-%m')
    con = _conn()
    try:
        subs = _rows(con.execute('SELECT * FROM suscripciones WHERE activa=1'))
    finally:
        con.close()
    generadas = []
    for s in subs:
        if s.get('ultima_generada') == mes_actual:
            continue
        if s.get('fecha_fin') and hoy.isoformat() > s['fecha_fin']:
            con2 = _conn()
            try:
                con2.execute('UPDATE suscripciones SET activa=0 WHERE id=?', (s['id'],))
                con2.commit()
            finally:
                con2.close()
            continue
        if s.get('fecha_inicio') and hoy.isoformat() < s['fecha_inicio']:
            continue
        dia_obj = _dia_ajustado(hoy.year, hoy.month, s['dia_mes'])
        if hoy.day < dia_obj:
            continue
        compra_id = crear_compra({
            'fecha': hoy.isoformat(), 'proveedor_id': s['proveedor_id'], 'tipo_doc': 'otro',
            'forma_pago': s['forma_pago'], 'tipo_gasto': 'recurrente',
            'categoria_id': s['categoria_id'], 'moneda': s['moneda'], 'tipo_cambio': s['tipo_cambio'],
            'notas': f"{s['nombre']} (cargo recurrente automático)", 'total': s['monto'],
        }, [], usuario_id or s.get('usuario_id'), suscripcion_id=s['id'])
        _marcar_generada(s['id'], mes_actual)
        generadas.append({'suscripcion_id': s['id'], 'nombre': s['nombre'], 'compra_id': compra_id,
                          'monto': s['monto'], 'moneda': s['moneda']})
    return generadas


def actualizar_compra(compra_id, campos):
    """Edita la cabecera de una compra ya registrada (NO los ítems). El uso clave es
    agregar el COSTO DE IMPORTACIÓN que llega después (FedEx/DHL/aduana), o corregir
    despacho, tipo de cambio, moneda, proveedor, categoría, etc. Recalcula total y
    total_clp a partir de los ítems existentes + los costos nuevos."""
    permit = {'fecha', 'proveedor_id', 'tipo_doc', 'nro_doc', 'forma_pago', 'tipo_gasto',
              'categoria_id', 'notas', 'moneda', 'tipo_cambio', 'costo_despacho',
              'costo_importacion'}
    con = _conn()
    try:
        actual = _row(con.execute('SELECT * FROM compras WHERE id=?', (compra_id,)).fetchone())
        if not actual:
            raise ValueError('Compra no encontrada')
        nuevo = {**actual, **{k: v for k, v in (campos or {}).items() if k in permit}}
        if nuevo.get('tipo_gasto') not in TIPOS_GASTO:
            nuevo['tipo_gasto'] = 'variable'
        moneda, tc = _moneda_de(nuevo)
        despacho = round(float(nuevo.get('costo_despacho') or 0), 2)
        importacion = round(float(nuevo.get('costo_importacion') or 0), 2)
        # base = suma de ítems (en la moneda). Si no hay ítems (gasto directo), se
        # conserva el 'base' implícito del total anterior menos su despacho anterior.
        fila = con.execute('SELECT COALESCE(SUM(subtotal),0) AS s, COUNT(*) AS n '
                           'FROM compra_items WHERE compra_id=?', (compra_id,)).fetchone()
        if fila['n'] > 0:
            base = round(fila['s'], 2)
        else:
            base = round(float(actual['total']) - float(actual['costo_despacho'] or 0), 2)
        total = round(base + despacho, 2)
        total_clp = _total_clp(total, tc, importacion)
        con.execute(
            'UPDATE compras SET fecha=?,proveedor_id=?,tipo_doc=?,nro_doc=?,forma_pago=?,'
            'tipo_gasto=?,categoria_id=?,moneda=?,tipo_cambio=?,costo_despacho=?,'
            'costo_importacion=?,total=?,total_clp=?,notas=? WHERE id=?',
            (_norm(nuevo.get('fecha')) or actual['fecha'], nuevo.get('proveedor_id'),
             nuevo.get('tipo_doc'), _norm(nuevo.get('nro_doc')), nuevo.get('forma_pago'),
             nuevo['tipo_gasto'], nuevo.get('categoria_id'), moneda, tc, despacho,
             importacion, total, total_clp, _norm(nuevo.get('notas')), compra_id))
        con.commit()
        return {'total': total, 'total_clp': total_clp, 'moneda': moneda}
    finally:
        con.close()


def listar_compras(desde=None, hasta=None, proveedor_id=None, categoria_id=None,
                   tipo_gasto=None, limite=200):
    con = _conn()
    try:
        q = ('SELECT c.*, pr.nombre AS proveedor_nombre, cat.nombre AS categoria_nombre '
             'FROM compras c LEFT JOIN proveedores pr ON pr.id=c.proveedor_id '
             'LEFT JOIN categorias cat ON cat.id=c.categoria_id')
        cond, vals = [], []
        if desde:
            cond.append('c.fecha>=?'); vals.append(desde)
        if hasta:
            cond.append('c.fecha<=?'); vals.append(hasta)
        if proveedor_id:
            cond.append('c.proveedor_id=?'); vals.append(proveedor_id)
        if categoria_id:
            cond.append('c.categoria_id=?'); vals.append(categoria_id)
        if tipo_gasto in TIPOS_GASTO:
            cond.append('c.tipo_gasto=?'); vals.append(tipo_gasto)
        if cond:
            q += ' WHERE ' + ' AND '.join(cond)
        q += ' ORDER BY c.fecha DESC, c.id DESC LIMIT ?'
        vals.append(limite)
        return _rows(con.execute(q, vals))
    finally:
        con.close()


def obtener_compra(compra_id):
    con = _conn()
    try:
        c = _row(con.execute(
            'SELECT c.*, pr.nombre AS proveedor_nombre, cat.nombre AS categoria_nombre '
            'FROM compras c LEFT JOIN proveedores pr ON pr.id=c.proveedor_id '
            'LEFT JOIN categorias cat ON cat.id=c.categoria_id WHERE c.id=?',
            (compra_id,)).fetchone())
        if c:
            c['items'] = _rows(con.execute(
                'SELECT i.*, p.nombre AS producto_nombre, p.unidad '
                'FROM compra_items i LEFT JOIN productos p ON p.id=i.producto_id '
                'WHERE i.compra_id=?', (compra_id,)))
        return c
    finally:
        con.close()


def eliminar_compra(compra_id):
    """Borra una compra y REVIERTE el stock que había sumado (registra la reversión
    como movimiento de ajuste para dejar rastro)."""
    ahora = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    try:
        compra = con.execute('SELECT fecha,suscripcion_id FROM compras WHERE id=?',
                             (compra_id,)).fetchone()
        # Si nació de un cargo recurrente, liberar 'ultima_generada' de ESE mes para que
        # el barrido diario la vuelva a generar (si no, la suscripción cree que ya cobró).
        if compra and compra['suscripcion_id']:
            mes = (compra['fecha'] or '')[:7]
            con.execute("UPDATE suscripciones SET ultima_generada=NULL "
                       "WHERE id=? AND ultima_generada=?", (compra['suscripcion_id'], mes))
        items = con.execute('SELECT producto_id,cantidad FROM compra_items WHERE compra_id=?',
                            (compra_id,)).fetchall()
        for it in items:
            con.execute('UPDATE productos SET stock_actual=stock_actual-? WHERE id=?',
                        (it['cantidad'], it['producto_id']))
            con.execute(
                'INSERT INTO movimientos_stock(producto_id,tipo,cantidad,motivo,creado) '
                'VALUES(?,?,?,?,?)',
                (it['producto_id'], 'ajuste', it['cantidad'],
                 f'Reversión por borrado de compra #{compra_id}', ahora))
        # Desacoplar los movimientos de la compra (mantiene el historial del libro
        # mayor, pero libera la FK para poder borrar la compra).
        con.execute('UPDATE movimientos_stock SET compra_id=NULL WHERE compra_id=?', (compra_id,))
        # Si esta compra había resuelto una solicitud pendiente (auto-resolución al
        # comprar), al borrarla esa solicitud vuelve a quedar pendiente — ya no está
        # comprada de verdad. Si no, la FK de pendientes_compra.compra_id bloquea el DELETE.
        con.execute("UPDATE pendientes_compra SET estado='pendiente', compra_id=NULL, "
                    "resuelto=NULL WHERE compra_id=? AND estado='comprado'", (compra_id,))
        con.execute('DELETE FROM compra_items WHERE compra_id=?', (compra_id,))
        con.execute('DELETE FROM compras WHERE id=?', (compra_id,))
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# STOCK — movimientos, salidas por escaneo, alertas
# ══════════════════════════════════════════════════════════════════════════════

def registrar_movimiento(prod_id, tipo, cantidad, motivo='', usuario_id=None):
    """Registra una entrada/salida/ajuste y actualiza el stock_actual.
    tipo 'salida' resta; 'entrada' suma; 'ajuste' FIJA el stock al valor 'cantidad'."""
    cantidad = float(cantidad)
    ahora = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    try:
        p = con.execute('SELECT stock_actual FROM productos WHERE id=?', (prod_id,)).fetchone()
        if not p:
            raise ValueError('Producto no encontrado')
        if tipo == 'entrada':
            delta = cantidad
        elif tipo == 'salida':
            delta = -cantidad
        elif tipo == 'ajuste':
            # 'ajuste' deja el stock EXACTAMENTE en 'cantidad' (inventario físico).
            delta = cantidad - p['stock_actual']
        else:
            raise ValueError('Tipo de movimiento inválido')
        con.execute(
            'INSERT INTO movimientos_stock(producto_id,tipo,cantidad,motivo,usuario_id,creado) '
            'VALUES(?,?,?,?,?,?)',
            (prod_id, tipo, abs(cantidad) if tipo != 'ajuste' else cantidad,
             _norm(motivo), usuario_id, ahora))
        con.execute('UPDATE productos SET stock_actual=stock_actual+? WHERE id=?',
                    (delta, prod_id))
        con.commit()
        nuevo = con.execute('SELECT stock_actual FROM productos WHERE id=?',
                            (prod_id,)).fetchone()['stock_actual']
        return nuevo
    finally:
        con.close()


def salida_por_codigo(codigo, cantidad=1, motivo='Consumo', usuario_id=None):
    """Descuenta stock a partir de un código escaneado. Devuelve (producto, nuevo_stock)
    o (None, None) si el código no está mapeado a ningún producto."""
    prod = producto_por_codigo(codigo)
    if not prod:
        return None, None
    nuevo = registrar_movimiento(prod['id'], 'salida', cantidad, motivo, usuario_id)
    return prod, nuevo


def movimientos_producto(prod_id, limite=100):
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT m.*, u.nombre AS usuario_nombre FROM movimientos_stock m '
            'LEFT JOIN usuarios u ON u.id=m.usuario_id '
            'WHERE m.producto_id=? ORDER BY m.creado DESC LIMIT ?', (prod_id, limite)))
    finally:
        con.close()


def productos_bajo_minimo():
    """Productos cuyo stock cayó al mínimo o por debajo (mínimo > 0)."""
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT * FROM productos WHERE archivado=0 AND stock_minimo>0 '
            'AND stock_actual<=stock_minimo ORDER BY nombre'))
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# SOLICITUDES DE COMPRA (pendientes por comprar + sugerencias por consumo)
# ══════════════════════════════════════════════════════════════════════════════

def consumo_diario(prod_id, ventana_dias=90):
    """Estima el consumo diario de un producto a partir de sus SALIDAS de stock (que
    son el consumo real). rate = total salido / días transcurridos desde la 1ª salida
    (tope: ventana_dias). Devuelve dict con rate, total, días y nº de muestras."""
    con = _conn()
    try:
        desde = (ahora_cl().date() - timedelta(days=ventana_dias)).isoformat()
        rows = con.execute(
            "SELECT cantidad, creado FROM movimientos_stock "
            "WHERE producto_id=? AND tipo='salida' AND substr(creado,1,10)>=? "
            "ORDER BY creado", (prod_id, desde)).fetchall()
    finally:
        con.close()
    if not rows:
        return {'rate': 0.0, 'total': 0.0, 'dias': 0, 'muestras': 0}
    total = sum(r['cantidad'] for r in rows)
    try:
        prim = date.fromisoformat(rows[0]['creado'][:10])
    except ValueError:
        prim = ahora_cl().date()
    span = max(1, min((ahora_cl().date() - prim).days, ventana_dias))
    return {'rate': total / span, 'total': total, 'dias': span, 'muestras': len(rows)}


def sugerir_cantidad(prod_id, cobertura_dias=60):
    """Sugiere cuánto comprar de un producto para cubrir 'cobertura_dias' de consumo.
    Si hay historial de salidas: cantidad = consumo*cobertura − stock actual. Si no,
    cae al historial de compras (última cantidad) o al doble del mínimo."""
    prod = obtener_producto(prod_id) or {}
    stock = prod.get('stock_actual', 0) or 0
    minimo = prod.get('stock_minimo', 0) or 0
    cons = consumo_diario(prod_id)
    rate = cons['rate']
    if rate > 0:
        sugerida = math.ceil(max(0, rate * cobertura_dias - stock))
        base = f'consumo ~{round(rate * 30, 1)}/mes ({cons["muestras"]} salidas)'
        dias_rest = round(stock / rate) if rate > 0 else None
    else:
        ult = ultima_compra_producto(prod_id)
        if ult and ult.get('cantidad'):
            sugerida = math.ceil(ult['cantidad'])
            base = 'según última compra'
        elif minimo > 0:
            sugerida = math.ceil(max(minimo * 2 - stock, minimo))
            base = 'según stock mínimo'
        else:
            sugerida = 1
            base = 'sin historial'
        dias_rest = None
    return {'cantidad': sugerida, 'rate_mensual': round(rate * 30, 2), 'base': base,
            'dias_restantes': dias_rest, 'stock_actual': stock}


def productos_sugeridos(cobertura_dias=60):
    """Lista de productos que el sistema sugiere comprar, por stock bajo el mínimo o
    por proyección de quiebre (días de stock restantes < media cobertura). Excluye los
    que ya tienen una solicitud pendiente. Ordenado por urgencia."""
    pendientes_ids = {p['producto_id'] for p in listar_pendientes()}
    out = []
    for p in listar_productos():
        if p['id'] in pendientes_ids:
            continue
        stock = p['stock_actual'] or 0
        minimo = p['stock_minimo'] or 0
        cons = consumo_diario(p['id'])
        rate = cons['rate']
        dias_rest = (stock / rate) if rate > 0 else None
        razones, urgencia = [], 0.0
        if minimo > 0 and stock <= minimo:
            razones.append('stock bajo el mínimo'); urgencia += 100
        if dias_rest is not None and dias_rest < cobertura_dias / 2:
            razones.append(f'~{round(dias_rest)} días de stock')
            urgencia += (cobertura_dias / 2 - dias_rest)
        if not razones:
            continue
        sug = sugerir_cantidad(p['id'], cobertura_dias)
        out.append({
            'producto_id': p['id'], 'nombre': p['nombre'], 'unidad': p['unidad'],
            'stock_actual': stock, 'stock_minimo': minimo,
            'cantidad_sugerida': sug['cantidad'], 'rate_mensual': sug['rate_mensual'],
            'dias_restantes': (round(dias_rest) if dias_rest is not None else None),
            'razon': ' · '.join(razones), 'urgencia': round(urgencia, 1)})
    out.sort(key=lambda x: -x['urgencia'])
    return out


def crear_solicitud(items, usuario_id=None, nota=''):
    """Registra una solicitud de compra. items: [{producto_id, cantidad, motivo?, nota?}].
    Un producto pendiente = una fila. Si ya había un pendiente activo del mismo producto,
    se actualiza (upsert) en vez de duplicar. Devuelve cuántos productos quedaron pendientes."""
    items = items or []
    if not items:
        raise ValueError('La solicitud no tiene productos')
    ahora = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    n = 0
    try:
        for it in items:
            pid = it.get('producto_id')
            if not pid:
                continue
            cant = float(it.get('cantidad') or 0)
            motivo = it.get('motivo') if it.get('motivo') in ('manual', 'stock_bajo', 'reposicion') else 'manual'
            nota_it = _norm(it.get('nota')) or _norm(nota)
            ex = con.execute("SELECT id FROM pendientes_compra WHERE producto_id=? AND estado='pendiente'",
                             (pid,)).fetchone()
            if ex:
                con.execute('UPDATE pendientes_compra SET cantidad_sugerida=?,nota=?,motivo=?,'
                            'solicitado_por=?,creado=? WHERE id=?',
                            (cant, nota_it, motivo, usuario_id, ahora, ex['id']))
            else:
                con.execute('INSERT INTO pendientes_compra(producto_id,cantidad_sugerida,motivo,'
                            'nota,estado,solicitado_por,creado) VALUES(?,?,?,?,?,?,?)',
                            (pid, cant, motivo, nota_it, 'pendiente', usuario_id, ahora))
            n += 1
        con.commit()
    finally:
        con.close()
    return n


def listar_pendientes(estado='pendiente'):
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT pc.*, p.nombre AS producto_nombre, p.unidad, p.stock_actual, '
            'p.stock_minimo, u.nombre AS solicitante '
            'FROM pendientes_compra pc JOIN productos p ON p.id=pc.producto_id '
            'LEFT JOIN usuarios u ON u.id=pc.solicitado_por '
            'WHERE pc.estado=? ORDER BY pc.creado DESC', (estado,)))
    finally:
        con.close()


def contar_pendientes():
    con = _conn()
    try:
        return con.execute("SELECT COUNT(*) AS n FROM pendientes_compra "
                           "WHERE estado='pendiente'").fetchone()['n']
    finally:
        con.close()


def cancelar_pendiente(pend_id):
    con = _conn()
    try:
        con.execute("UPDATE pendientes_compra SET estado='cancelado', resuelto=? "
                    "WHERE id=? AND estado='pendiente'",
                    (ahora_cl().isoformat(timespec='seconds'), pend_id))
        con.commit()
    finally:
        con.close()


def _resolver_pendientes(con, producto_ids, compra_id, ahora):
    """Marca como 'comprado' los pendientes de los productos recién comprados (se llama
    DENTRO de la transacción de crear_compra). Devuelve los ids de productos resueltos."""
    resueltos = []
    for pid in set(producto_ids):
        cur = con.execute("UPDATE pendientes_compra SET estado='comprado', resuelto=?, "
                          "compra_id=? WHERE producto_id=? AND estado='pendiente'",
                          (ahora, compra_id, pid))
        if cur.rowcount:
            resueltos.append(pid)
    return resueltos


# ══════════════════════════════════════════════════════════════════════════════
# COLA DE IMPRESIÓN (etiquetas para el agente en el PC de la clínica)
# ══════════════════════════════════════════════════════════════════════════════

def encolar_impresion(prod_id, codigo, cantidad=1):
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO cola_impresion(producto_id,codigo,cantidad,estado,creado) '
            'VALUES(?,?,?,?,?)',
            (prod_id, _norm(codigo), int(cantidad or 1), 'pendiente',
             ahora_cl().isoformat(timespec='seconds')))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def cola_pendiente():
    """Trabajos de etiqueta pendientes, con nombre de producto (para el agente)."""
    con = _conn()
    try:
        return _rows(con.execute(
            'SELECT ci.*, p.nombre AS producto_nombre, p.unidad '
            'FROM cola_impresion ci JOIN productos p ON p.id=ci.producto_id '
            "WHERE ci.estado='pendiente' ORDER BY ci.creado"))
    finally:
        con.close()


def marcar_impresion(job_id, estado='impreso'):
    con = _conn()
    try:
        con.execute('UPDATE cola_impresion SET estado=?, procesado=? WHERE id=?',
                    (estado, ahora_cl().isoformat(timespec='seconds'), job_id))
        con.commit()
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# REPORTES
# ══════════════════════════════════════════════════════════════════════════════

def resumen_gastos(desde=None, hasta=None):
    """Agrega el gasto por mes, categoría, proveedor y tipo. TODO en CLP (usa total_clp)
    para que las compras en dólares e importaciones sumen correcto en un solo reporte."""
    con = _conn()
    try:
        cond, vals = [], []
        if desde:
            cond.append('c.fecha>=?'); vals.append(desde)
        if hasta:
            cond.append('c.fecha<=?'); vals.append(hasta)
        where = (' WHERE ' + ' AND '.join(cond)) if cond else ''
        # total_clp puede ser 0 en filas viejas antes de la migración → cae a total.
        M = 'CASE WHEN c.total_clp>0 THEN c.total_clp ELSE c.total END'

        total = con.execute(f'SELECT COALESCE(SUM({M}),0) AS t, COUNT(*) AS n '
                            f'FROM compras c{where}', vals).fetchone()

        por_mes = _rows(con.execute(
            f"SELECT substr(c.fecha,1,7) AS mes, SUM({M}) AS total, COUNT(*) AS n "
            f"FROM compras c{where} GROUP BY mes ORDER BY mes", vals))

        por_cat = _rows(con.execute(
            f"SELECT COALESCE(cat.nombre,'(sin categoría)') AS label, SUM({M}) AS total, "
            f"COUNT(*) AS n FROM compras c LEFT JOIN categorias cat ON cat.id=c.categoria_id"
            f"{where} GROUP BY label ORDER BY total DESC", vals))

        por_prov = _rows(con.execute(
            f"SELECT COALESCE(pr.nombre,'(sin proveedor)') AS label, SUM({M}) AS total, "
            f"COUNT(*) AS n FROM compras c LEFT JOIN proveedores pr ON pr.id=c.proveedor_id"
            f"{where} GROUP BY label ORDER BY total DESC", vals))

        por_tipo = _rows(con.execute(
            f"SELECT c.tipo_gasto AS label, SUM({M}) AS total, COUNT(*) AS n "
            f"FROM compras c{where} GROUP BY c.tipo_gasto", vals))

        return {
            'total': round(total['t'], 2),
            'n_compras': total['n'],
            'por_mes': por_mes,
            'por_categoria': por_cat,
            'por_proveedor': por_prov,
            'por_tipo': por_tipo,
        }
    finally:
        con.close()


def filas_export(desde=None, hasta=None):
    """Filas planas (una por ítem de compra) para exportar a Excel."""
    con = _conn()
    try:
        cond, vals = [], []
        if desde:
            cond.append('c.fecha>=?'); vals.append(desde)
        if hasta:
            cond.append('c.fecha<=?'); vals.append(hasta)
        where = (' WHERE ' + ' AND '.join(cond)) if cond else ''
        return _rows(con.execute(
            f"SELECT c.fecha, pr.nombre AS proveedor, c.tipo_doc, c.nro_doc, "
            f"c.forma_pago, c.tipo_gasto, cat.nombre AS categoria, "
            f"p.nombre AS producto, i.marca, i.cantidad, i.precio_unitario, i.subtotal, "
            f"c.moneda, c.tipo_cambio, c.costo_despacho, c.costo_importacion, c.total, c.total_clp "
            f"FROM compras c JOIN compra_items i ON i.compra_id=c.id "
            f"LEFT JOIN proveedores pr ON pr.id=c.proveedor_id "
            f"LEFT JOIN categorias cat ON cat.id=c.categoria_id "
            f"LEFT JOIN productos p ON p.id=i.producto_id{where} "
            f"ORDER BY c.fecha DESC, c.id DESC", vals))
    finally:
        con.close()
