"""
tareas.py — Bolsa de tareas para los tiempos muertos del personal (2026-10-14).

Recepción, asistentes y aseo tienen ratos libres que hoy se pierden. Este módulo:

  1. Mantiene una BOLSA de tareas por puesto (secretaria · asistente_orto ·
     asistente_vial · aseo), alimentada por tres fuentes:
       - plantillas que se repiten (diaria / semanal / mensual),
       - encargos puntuales que anota un administrador,
       - GENERADORES: listas que el sistema ya calcula y nadie trabajaba
         (controles atrasados, consentimientos sin firmar, stock bajo mínimo…).
  2. Cada mañana ESTIMA cuánto tiempo libre tiene cada puesto (dotación del horario
     + agenda real del día en clinica.db) y arma una PROPUESTA de qué hacer hoy, que
     se le manda al Dr. Alberto para aprobar. Hasta que la aprueba (o hasta la hora
     de publicación automática) las tareas generadas no se ven en la bolsa.
  3. Registra quién tomó y terminó cada tarea, para el resumen semanal.

Datos en la MISMA base de compras (compras.db): las tareas se relacionan con los
usuarios de esa app (login, roles, el celular de cada persona). Misma excepción a la
regla 2 que compras: son relaciones reales.

⚠️ El tiempo libre es una ESTIMACIÓN y se dice así en todas partes. Sirve para no
proponer más trabajo del que cabe, no para medir a nadie.

⚠️ Las tareas que tocan pacientes NO envían nada solas: son para que una persona llame
o escriba. Cero red en este módulo salvo los generadores, que llaman funciones de
otros módulos (cada uno en su propio try/except).
"""

import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import compras
import fechas

log = logging.getLogger(__name__)

PUESTOS = compras.PUESTOS
ESTADOS = ('pendiente', 'tomada', 'hecha', 'vencida', 'descartada')
FRECUENCIAS = ('diaria', 'semanal', 'mensual')
PRIORIDADES = {1: 'Alta', 2: 'Normal', 3: 'Baja'}
DIAS_LABEL = {1: 'lunes', 2: 'martes', 3: 'miércoles', 4: 'jueves', 5: 'viernes',
              6: 'sábado', 7: 'domingo'}

# Dotación por día (isoweekday) y bloque, tomada del horario "desde ABRIL 2026 con
# 42 HORAS" (martes a viernes). El LUNES lo contó el Dr. Alberto (2026-10-14): en la
# mañana vienen solo tres personas y cada una ya tiene su labor (una asistente cubre
# recepción, la asistente del Dr. Vial ordena material y una persona de aseo); en la
# tarde están todos. La laboratorista no entra a las tareas por ahora.
_DOT_NORMAL = {'secretaria': 2, 'asistente_orto': 4, 'asistente_vial': 1, 'aseo': 2}
_DOT_LUNES_AM = {'secretaria': 1, 'asistente_orto': 0, 'asistente_vial': 1, 'aseo': 1}
DEFAULT_CONFIG = {
    'activo': False,                 # mandar la propuesta diaria al Dr.
    'hora_propuesta': '07:15',
    'publicar_sin_aprobar': True,    # si no aprueba a tiempo, publicar igual
    'hora_publicar': '10:00',
    'whatsapp': False,               # además del correo, aviso por WhatsApp
    'llenado_pct': 80,               # no proponer más que este % del libre estimado
    'bloques': {'AM': ['08:45', '13:30'], 'PM': ['14:30', '19:30']},
    'dotacion': {
        '1': {'AM': dict(_DOT_LUNES_AM), 'PM': dict(_DOT_NORMAL)},
        '2': {'AM': dict(_DOT_NORMAL), 'PM': dict(_DOT_NORMAL)},
        '3': {'AM': dict(_DOT_NORMAL), 'PM': dict(_DOT_NORMAL)},
        '4': {'AM': {'secretaria': 1, 'asistente_orto': 4, 'asistente_vial': 0, 'aseo': 1},
              'PM': dict(_DOT_NORMAL)},
        '5': {'AM': {'secretaria': 2, 'asistente_orto': 3, 'asistente_vial': 1, 'aseo': 2},
              'PM': dict(_DOT_NORMAL)},
    },
    'doctores_orto': ['alberto', 'rodrigo', 'octavio'],
    'doctor_vial': 'patricio',
    'hueco_min': 30,                 # un hueco de agenda menor a esto no se cuenta
    'secretaria_pct': {'baja': 35, 'normal': 15},
    'secretaria_umbral_citas_hora': 4,
    'aseo_min_por_persona': {'AM': 60, 'PM': 60},
    'vial_admin': {'dia': 2, 'bloque': 'AM'},    # martes AM: labores admin del Dr. Vial
    'preferido_stock': None,         # usuario que recibe las tareas de stock/etiquetas
    'resumen_semanal': True,
    'ultimo_resumen': '',
    'catalogo_cargado': False,
}


# ══════════════════════════════════════════════════════════════════════════════
# ESQUEMA
# ══════════════════════════════════════════════════════════════════════════════

def _conn():
    return compras._conn()


def init_db():
    """Idempotente. Va DESPUÉS de compras.init_db() (las FK apuntan a usuarios)."""
    con = _conn()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS tareas_plantillas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo        TEXT NOT NULL,
            detalle       TEXT,
            puesto        TEXT NOT NULL,
            minutos       INTEGER NOT NULL DEFAULT 15,
            prioridad     INTEGER NOT NULL DEFAULT 2,
            frecuencia    TEXT NOT NULL DEFAULT 'semanal',
            dias          TEXT NOT NULL DEFAULT '',     -- "2,4": días de la semana (diaria/semanal)
            dia_mes       INTEGER NOT NULL DEFAULT 1,   -- mensual
            admin_vial    INTEGER NOT NULL DEFAULT 0,   -- solo el bloque admin del Dr. Vial
            preferido_id  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            activa        INTEGER NOT NULL DEFAULT 1,
            creado        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tareas (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ref           TEXT UNIQUE,                  -- dedup de plantillas/generadores
            origen        TEXT NOT NULL,                -- plantilla | sistema | encargo
            plantilla_id  INTEGER REFERENCES tareas_plantillas(id) ON DELETE SET NULL,
            generador     TEXT,
            titulo        TEXT NOT NULL,
            detalle       TEXT,
            puesto        TEXT NOT NULL,
            minutos       INTEGER NOT NULL DEFAULT 15,
            prioridad     INTEGER NOT NULL DEFAULT 2,
            admin_vial    INTEGER NOT NULL DEFAULT 0,
            fecha         TEXT NOT NULL,                -- día en que nació
            vence         TEXT,
            publicada     INTEGER NOT NULL DEFAULT 0,   -- visible en la bolsa
            fecha_plan    TEXT,                         -- día en que se propuso hacerla
            asignada_a    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            estado        TEXT NOT NULL DEFAULT 'pendiente',
            tomada_por    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            tomada_en     TEXT,
            hecha_por     INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            hecha_en      TEXT,
            nota          TEXT,
            creada_por    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            creada        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tareas_propuestas (
            fecha         TEXT PRIMARY KEY,
            estado        TEXT NOT NULL,                -- armada | enviada | aprobada | auto
            libre         TEXT,                         -- JSON de la estimación
            ids           TEXT,                         -- JSON de las tareas propuestas
            creada        TEXT NOT NULL,
            enviada_en    TEXT,
            aprobada_en   TEXT
        );
        CREATE TABLE IF NOT EXISTS tareas_config (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            datos         TEXT NOT NULL
        );
        """)
        con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_tareas_estado ON tareas(estado, puesto);
        CREATE INDEX IF NOT EXISTS ix_tareas_hecha  ON tareas(hecha_en);
        """)
        con.commit()
    finally:
        con.close()


def _ahora():
    return fechas.ahora_chile().isoformat(timespec='seconds')


def _hoy():
    return fechas.hoy_chile()


def _d(x):
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x)[:10])


# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════════════

def load_config():
    con = _conn()
    try:
        r = con.execute('SELECT datos FROM tareas_config WHERE id=1').fetchone()
    finally:
        con.close()
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if r:
        try:
            cfg.update(json.loads(r['datos']))
        except ValueError:
            log.warning('[tareas] config ilegible, se usan los valores por defecto')
    return cfg


def save_config(campos):
    cfg = load_config()
    for k, v in (campos or {}).items():
        if k in DEFAULT_CONFIG:
            cfg[k] = v
    con = _conn()
    try:
        con.execute('INSERT INTO tareas_config(id,datos) VALUES(1,?) '
                    'ON CONFLICT(id) DO UPDATE SET datos=excluded.datos',
                    (json.dumps(cfg, ensure_ascii=False),))
        con.commit()
    finally:
        con.close()
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# PLANTILLAS
# ══════════════════════════════════════════════════════════════════════════════

def _validar_comun(d):
    titulo = (d.get('titulo') or '').strip()
    if not titulo:
        raise ValueError('Falta el título de la tarea')
    puesto = (d.get('puesto') or '').strip()
    if puesto not in PUESTOS:
        raise ValueError('Puesto inválido')
    minutos = int(d.get('minutos') or 15)
    if not 1 <= minutos <= 480:
        raise ValueError('Los minutos deben estar entre 1 y 480')
    prioridad = int(d.get('prioridad') or 2)
    if prioridad not in PRIORIDADES:
        raise ValueError('Prioridad inválida')
    return titulo, puesto, minutos, prioridad


def _dias_csv(dias):
    if isinstance(dias, str):
        dias = [x for x in dias.split(',') if x.strip()]
    out = sorted({int(x) for x in (dias or [])})
    if any(x < 1 or x > 7 for x in out):
        raise ValueError('Día de la semana inválido')
    return ','.join(str(x) for x in out)


def crear_plantilla(d):
    titulo, puesto, minutos, prioridad = _validar_comun(d)
    frecuencia = d.get('frecuencia') or 'semanal'
    if frecuencia not in FRECUENCIAS:
        raise ValueError('Frecuencia inválida')
    dia_mes = int(d.get('dia_mes') or 1)
    if not 1 <= dia_mes <= 28:
        raise ValueError('El día del mes debe estar entre 1 y 28')
    con = _conn()
    try:
        cur = con.execute(
            'INSERT INTO tareas_plantillas(titulo,detalle,puesto,minutos,prioridad,frecuencia,'
            'dias,dia_mes,admin_vial,preferido_id,activa,creado) VALUES(?,?,?,?,?,?,?,?,?,?,1,?)',
            (titulo, (d.get('detalle') or '').strip(), puesto, minutos, prioridad, frecuencia,
             _dias_csv(d.get('dias')), dia_mes, 1 if d.get('admin_vial') else 0,
             d.get('preferido_id') or None, _ahora()))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def actualizar_plantilla(pid, d):
    actual = obtener_plantilla(pid)
    if not actual:
        raise ValueError('Plantilla no encontrada')
    merged = {**actual, **{k: v for k, v in d.items() if v is not None}}
    titulo, puesto, minutos, prioridad = _validar_comun(merged)
    if merged['frecuencia'] not in FRECUENCIAS:
        raise ValueError('Frecuencia inválida')
    con = _conn()
    try:
        con.execute(
            'UPDATE tareas_plantillas SET titulo=?,detalle=?,puesto=?,minutos=?,prioridad=?,'
            'frecuencia=?,dias=?,dia_mes=?,admin_vial=?,preferido_id=?,activa=? WHERE id=?',
            (titulo, merged.get('detalle') or '', puesto, minutos, prioridad,
             merged['frecuencia'], _dias_csv(merged.get('dias')), int(merged.get('dia_mes') or 1),
             1 if merged.get('admin_vial') else 0, merged.get('preferido_id') or None,
             1 if merged.get('activa') else 0, int(pid)))
        con.commit()
    finally:
        con.close()


def obtener_plantilla(pid):
    con = _conn()
    try:
        return compras._row(con.execute('SELECT * FROM tareas_plantillas WHERE id=?',
                                        (int(pid),)).fetchone())
    finally:
        con.close()


def listar_plantillas(incluir_inactivas=True):
    con = _conn()
    try:
        q = 'SELECT * FROM tareas_plantillas'
        if not incluir_inactivas:
            q += ' WHERE activa=1'
        return compras._rows(con.execute(q + ' ORDER BY puesto, prioridad, titulo'))
    finally:
        con.close()


def _toca_hoy(p, d):
    """(ref, vence) si la plantilla genera una tarea el día `d`; None si no."""
    dias = [int(x) for x in (p.get('dias') or '').split(',') if x]
    wd = d.isoweekday()
    if p['frecuencia'] == 'diaria':
        if dias and wd not in dias:
            return None
        return f"pl:{p['id']}:{d.isoformat()}", d.isoformat()
    if p['frecuencia'] == 'semanal':
        # Nace el primer día de la semana que le corresponde (o el lunes) y queda
        # disponible hasta el sábado. Si ese día no se generó (servidor caído), se
        # genera el siguiente día hábil de esa misma semana: la ref es por semana.
        primero = dias[0] if dias else 1
        if wd < primero:
            return None
        anio, sem, _ = d.isocalendar()
        fin = d + timedelta(days=6 - wd)
        return f"pl:{p['id']}:{anio}-W{sem:02d}", fin.isoformat()
    if p['frecuencia'] == 'mensual':
        if d.day < int(p.get('dia_mes') or 1):
            return None
        sig = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return f"pl:{p['id']}:{d.strftime('%Y-%m')}", (sig - timedelta(days=1)).isoformat()
    return None


# ══════════════════════════════════════════════════════════════════════════════
# TAREAS
# ══════════════════════════════════════════════════════════════════════════════

def _insertar(con, t, d, publicada=0):
    """INSERT OR IGNORE por ref. Devuelve True si se creó."""
    cur = con.execute(
        'INSERT OR IGNORE INTO tareas(ref,origen,plantilla_id,generador,titulo,detalle,puesto,'
        'minutos,prioridad,admin_vial,fecha,vence,publicada,asignada_a,creada_por,creada) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (t.get('ref'), t['origen'], t.get('plantilla_id'), t.get('generador'), t['titulo'],
         t.get('detalle') or '', t['puesto'], int(t.get('minutos') or 15),
         int(t.get('prioridad') or 2), 1 if t.get('admin_vial') else 0, d.isoformat(),
         t.get('vence'), publicada, t.get('asignada_a'), t.get('creada_por'), _ahora()))
    return cur.rowcount > 0


def crear_encargo(d, creada_por=None):
    """Tarea suelta de un administrador. Se publica al tiro (no espera la propuesta)."""
    titulo, puesto, minutos, prioridad = _validar_comun(d)
    vence = (d.get('vence') or '').strip() or None
    if vence:
        _d(vence)   # valida el formato
    asignada = d.get('asignada_a') or None
    con = _conn()
    try:
        if asignada:
            u = con.execute('SELECT puesto FROM usuarios WHERE id=? AND activo=1',
                            (int(asignada),)).fetchone()
            if not u:
                raise ValueError('La persona asignada no existe')
        _insertar(con, {'origen': 'encargo', 'titulo': titulo, 'detalle': d.get('detalle'),
                        'puesto': puesto, 'minutos': minutos, 'prioridad': prioridad,
                        'vence': vence, 'asignada_a': asignada, 'creada_por': creada_por},
                  _hoy(), publicada=1)
        tid = con.execute('SELECT last_insert_rowid() AS i').fetchone()['i']
        con.commit()
        return tid
    finally:
        con.close()


def generar_dia(d=None, generadores=True):
    """Crea las tareas que tocan el día `d` (plantillas + generadores) y vence las
    pendientes cuyo plazo ya pasó. Idempotente: la `ref` impide duplicar."""
    d = _d(d or _hoy())
    creadas = {'plantillas': 0, 'sistema': 0, 'vencidas': 0, 'errores': {}}
    con = _conn()
    try:
        cur = con.execute("UPDATE tareas SET estado='vencida' WHERE estado='pendiente' "
                          "AND vence IS NOT NULL AND vence < ?", (d.isoformat(),))
        creadas['vencidas'] = cur.rowcount
        for p in listar_plantillas(incluir_inactivas=False):
            r = _toca_hoy(p, d)
            if not r:
                continue
            ref, vence = r
            if _insertar(con, {'ref': ref, 'origen': 'plantilla', 'plantilla_id': p['id'],
                               'titulo': p['titulo'], 'detalle': p['detalle'],
                               'puesto': p['puesto'], 'minutos': p['minutos'],
                               'prioridad': p['prioridad'], 'admin_vial': p['admin_vial'],
                               'vence': vence, 'asignada_a': p['preferido_id']}, d):
                creadas['plantillas'] += 1
        con.commit()
    finally:
        con.close()
    if generadores:
        r = correr_generadores(d)
        creadas['sistema'] = r['creadas']
        creadas['errores'] = r['errores']
    return creadas


# ── Bolsa del personal ─────────────────────────────────────────────────────────

_ORDEN_BOLSA = ('CASE WHEN t.asignada_a=:uid THEN 0 ELSE 1 END, '
                'CASE WHEN t.fecha_plan=:hoy THEN 0 ELSE 1 END, '
                't.prioridad, COALESCE(t.vence, "9999"), t.id')


def _es_admin(usuario):
    return (usuario or {}).get('rol_tareas') == 'admin'


def bolsa(usuario, hoy=None):
    """Lo que ve una persona: sus tareas tomadas + las pendientes publicadas de su
    puesto (las asignadas a otra persona no). Un admin sin puesto ve todo."""
    hoy = _d(hoy or _hoy()).isoformat()
    puesto = usuario.get('puesto') or ''
    con = _conn()
    try:
        base = ('SELECT t.*, ua.nombre AS asignada_nombre FROM tareas t '
                'LEFT JOIN usuarios ua ON ua.id=t.asignada_a ')
        mias = compras._rows(con.execute(
            base + "WHERE t.estado='tomada' AND t.tomada_por=:uid ORDER BY t.tomada_en",
            {'uid': usuario['id']}))
        if puesto:
            filtro = 't.puesto=:puesto AND (t.asignada_a IS NULL OR t.asignada_a=:uid)'
        elif _es_admin(usuario):
            filtro = '1=1'
        else:
            filtro = '0=1'
        libres = compras._rows(con.execute(
            base + f"WHERE t.estado='pendiente' AND t.publicada=1 AND {filtro} "
            f"ORDER BY {_ORDEN_BOLSA}", {'uid': usuario['id'], 'puesto': puesto, 'hoy': hoy}))
        hechas_hoy = con.execute(
            "SELECT COUNT(*) AS n FROM tareas WHERE hecha_por=? AND substr(hecha_en,1,10)=?",
            (usuario['id'], hoy)).fetchone()['n']
    finally:
        con.close()
    for t in libres:
        t['de_hoy'] = t.get('fecha_plan') == hoy
    return {'mias': mias, 'bolsa': libres, 'hechas_hoy': hechas_hoy}


def sugerir(usuario, minutos, hoy=None):
    """La tarea más importante de la bolsa que cabe en `minutos`."""
    minutos = int(minutos or 0)
    for t in bolsa(usuario, hoy)['bolsa']:
        if t['minutos'] <= minutos:
            return t
    return None


def _puede_ver(t, usuario):
    if _es_admin(usuario):
        return True
    if t['puesto'] != (usuario.get('puesto') or ''):
        return False
    return t['asignada_a'] in (None, usuario['id'])


def tomar(tid, usuario):
    """Pasa una tarea a 'tomada'. Comprobación y escritura en la misma transacción:
    dos personas apretando a la vez no pueden tomar la misma."""
    con = _conn()
    try:
        con.execute('BEGIN IMMEDIATE')
        t = con.execute('SELECT * FROM tareas WHERE id=?', (int(tid),)).fetchone()
        if not t or not _puede_ver(dict(t), usuario):
            raise ValueError('Tarea no encontrada')
        if t['estado'] != 'pendiente' or not t['publicada']:
            raise ValueError('Esa tarea ya la tomó otra persona o ya no está disponible')
        con.execute("UPDATE tareas SET estado='tomada', tomada_por=?, tomada_en=? WHERE id=?",
                    (usuario['id'], _ahora(), int(tid)))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def soltar(tid, usuario):
    con = _conn()
    try:
        con.execute('BEGIN IMMEDIATE')
        t = con.execute('SELECT * FROM tareas WHERE id=?', (int(tid),)).fetchone()
        if not t or t['estado'] != 'tomada' or (
                t['tomada_por'] != usuario['id']
                and not _es_admin(usuario)):
            raise ValueError('No tienes esa tarea tomada')
        con.execute("UPDATE tareas SET estado='pendiente', tomada_por=NULL, tomada_en=NULL "
                    "WHERE id=?", (int(tid),))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def terminar(tid, usuario, nota=''):
    """Marca hecha. Vale para una tarea tomada por esta persona o para una pendiente
    de su bolsa (se tomó y se hizo de una)."""
    con = _conn()
    try:
        con.execute('BEGIN IMMEDIATE')
        t = con.execute('SELECT * FROM tareas WHERE id=?', (int(tid),)).fetchone()
        if not t:
            raise ValueError('Tarea no encontrada')
        t = dict(t)
        if t['estado'] == 'tomada':
            if t['tomada_por'] != usuario['id'] and not _es_admin(usuario):
                raise ValueError('Esa tarea la tiene otra persona')
        elif t['estado'] == 'pendiente' and t['publicada'] and _puede_ver(t, usuario):
            pass
        else:
            raise ValueError('Esa tarea ya no está disponible')
        ahora = _ahora()
        con.execute("UPDATE tareas SET estado='hecha', hecha_por=?, hecha_en=?, nota=?, "
                    "tomada_por=COALESCE(tomada_por, ?), tomada_en=COALESCE(tomada_en, ?) "
                    "WHERE id=?", (usuario['id'], ahora, (nota or '').strip()[:500],
                                   usuario['id'], ahora, int(tid)))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def descartar(tid):
    con = _conn()
    try:
        cur = con.execute("UPDATE tareas SET estado='descartada' WHERE id=? "
                          "AND estado IN ('pendiente','tomada')", (int(tid),))
        con.commit()
        if not cur.rowcount:
            raise ValueError('Esa tarea ya está cerrada')
    finally:
        con.close()


def listar(estado=None, desde=None, hasta=None, limite=300):
    """Vista de administración (todas las personas)."""
    q = ('SELECT t.*, ua.nombre AS asignada_nombre, uh.nombre AS hecha_nombre, '
         'ut.nombre AS tomada_nombre FROM tareas t '
         'LEFT JOIN usuarios ua ON ua.id=t.asignada_a '
         'LEFT JOIN usuarios uh ON uh.id=t.hecha_por '
         'LEFT JOIN usuarios ut ON ut.id=t.tomada_por WHERE 1=1')
    args = []
    if estado:
        q += ' AND t.estado=?'; args.append(estado)
    if desde:
        q += ' AND t.fecha>=?'; args.append(str(desde))
    if hasta:
        q += ' AND t.fecha<=?'; args.append(str(hasta))
    q += ' ORDER BY t.id DESC LIMIT ?'; args.append(int(limite))
    con = _conn()
    try:
        return compras._rows(con.execute(q, args))
    finally:
        con.close()


# ══════════════════════════════════════════════════════════════════════════════
# ESTIMACIÓN DEL TIEMPO LIBRE
# ══════════════════════════════════════════════════════════════════════════════

def _min(hhmm):
    h, m = str(hhmm)[:5].split(':')
    return int(h) * 60 + int(m)


def citas_del_dia(d):
    """[{doctor, hora, duracion}] vigentes del día desde clinica.db (cosechada 03:00).
    Vacío si la base no está o no tiene ese día — el llamador lo trata como
    'sin agenda' y lo dice."""
    try:
        import kpi
        con = kpi._conn()
        try:
            filas = con.execute(
                f"SELECT doctor, hora, duracion FROM citas WHERE fecha=? AND {kpi._SQL_CUENTA}",
                (_d(d).isoformat(),)).fetchall()
        finally:
            con.close()
        return [dict(f) for f in filas if f['hora']]
    except Exception as e:
        log.warning('[tareas] no se pudo leer la agenda: %s', e)
        return []


def _ocupado(citas, ini, fin):
    """Minutos ocupados (unión de intervalos) dentro de [ini, fin)."""
    marcas = set()
    for c in citas:
        a = _min(c['hora'])
        b = a + int(c.get('duracion') or 15)
        for m in range(max(a, ini), min(b, fin)):
            marcas.add(m)
    return len(marcas)


def estimar_libre(d, cfg=None, citas=None):
    """Minutos libres estimados por puesto y bloque.

    Devuelve {'por_puesto': {puesto: minutos}, 'bloques': {bloque: {puesto: {...}}},
              'sin_agenda': bool, 'admin_vial': bool}

      asistente_orto — (asistentes − doctores de orto con agenda en el bloque) × bloque,
                       más los huecos ≥ hueco_min de los doctores que sí atienden.
      asistente_vial — el bloque entero si el Dr. Vial no atiende; si atiende, sus
                       huecos. El bloque admin (martes AM) se marca aparte.
      secretaria     — un % del bloque según la carga (citas por hora).
      aseo           — minutos fijos por persona y bloque.
    """
    cfg = cfg or load_config()
    d = _d(d)
    citas = citas_del_dia(d) if citas is None else citas
    sin_agenda = not citas
    dot_dia = cfg['dotacion'].get(str(d.isoweekday()), {})
    hueco = int(cfg.get('hueco_min', 30))
    va = cfg.get('vial_admin') or {}
    out = {'por_puesto': {p: 0 for p in PUESTOS}, 'bloques': {},
           'sin_agenda': sin_agenda, 'admin_vial': False}
    for bloque, (hi, hf) in cfg['bloques'].items():
        ini, fin = _min(hi), _min(hf)
        largo = fin - ini
        dot = dot_dia.get(bloque, {})
        en_bloque = [c for c in citas if ini <= _min(c['hora']) < fin]
        por_doc = {}
        for c in en_bloque:
            por_doc.setdefault(c['doctor'] or '', []).append(c)
        det = {}

        # Asistentes de ortodoncia
        n = int(dot.get('asistente_orto', 0))
        if sin_agenda:
            presentes = list(cfg['doctores_orto'])     # supuesto conservador: todos
        else:
            presentes = [k for k in cfg['doctores_orto'] if k in por_doc]
        cubiertos = min(n, len(presentes))
        libre = (n - cubiertos) * largo
        huecos = 0
        if not sin_agenda:
            for k in presentes[:cubiertos]:
                h = largo - _ocupado(por_doc[k], ini, fin)
                if h >= hueco:
                    huecos += h
        det['asistente_orto'] = {'personas': n, 'doctores': presentes,
                                 'sobran': n - cubiertos, 'minutos': libre + huecos}

        # Asistente Dr. Vial
        n = int(dot.get('asistente_vial', 0))
        es_admin = (d.isoweekday() == int(va.get('dia', 0)) and bloque == va.get('bloque'))
        vial = por_doc.get(cfg.get('doctor_vial', 'patricio'), [])
        if not n:
            m = 0
        elif vial:
            h = largo - _ocupado(vial, ini, fin)
            m = h if h >= hueco else 0
        elif sin_agenda:
            m = 0          # no sabemos si viene: no se inventa tiempo libre
        else:
            m = n * largo
        det['asistente_vial'] = {'personas': n, 'atiende_vial': bool(vial),
                                 'admin_vial': es_admin and n > 0, 'minutos': m}
        if es_admin and n:
            out['admin_vial'] = True

        # Recepción
        n = int(dot.get('secretaria', 0))
        por_hora = len(en_bloque) / (largo / 60) if largo else 0
        baja = not sin_agenda and por_hora < float(cfg.get('secretaria_umbral_citas_hora', 4))
        pct = cfg['secretaria_pct']['baja' if baja else 'normal']
        det['secretaria'] = {'personas': n, 'citas_hora': round(por_hora, 1),
                             'carga': 'baja' if baja else 'normal',
                             'minutos': int(n * largo * pct / 100)}

        # Aseo
        n = int(dot.get('aseo', 0))
        det['aseo'] = {'personas': n,
                       'minutos': n * int(cfg['aseo_min_por_persona'].get(bloque, 60))}

        out['bloques'][bloque] = det
        for p in PUESTOS:
            out['por_puesto'][p] += det[p]['minutos']
    return out


# ══════════════════════════════════════════════════════════════════════════════
# PROPUESTA DIARIA
# ══════════════════════════════════════════════════════════════════════════════

def armar_propuesta(d=None, cfg=None, citas=None):
    """Elige, por puesto y en orden de prioridad, las tareas pendientes que caben en
    el `llenado_pct` del tiempo libre estimado. Nunca propone más de lo que cabe.
    Idempotente: si ya se aprobó la de ese día, no la rehace."""
    cfg = cfg or load_config()
    d = _d(d or _hoy())
    previa = obtener_propuesta(d)
    if previa and previa['estado'] in ('aprobada', 'auto'):
        return previa
    libre = estimar_libre(d, cfg, citas)
    tope = {p: int(libre['por_puesto'][p] * int(cfg.get('llenado_pct', 80)) / 100)
            for p in PUESTOS}
    con = _conn()
    try:
        con.execute('UPDATE tareas SET fecha_plan=NULL WHERE fecha_plan=? AND estado=?',
                    (d.isoformat(), 'pendiente'))
        pend = compras._rows(con.execute(
            "SELECT * FROM tareas WHERE estado='pendiente' "
            "ORDER BY prioridad, COALESCE(vence,'9999'), id"))
        elegidas, usado = [], {p: 0 for p in PUESTOS}
        for t in pend:
            p = t['puesto']
            if p not in tope:
                continue
            if t['admin_vial'] and not libre['admin_vial']:
                continue          # lo de admin Dr. Vial solo el día que corresponde
            if usado[p] + t['minutos'] > tope[p]:
                continue
            usado[p] += t['minutos']
            elegidas.append(t['id'])
        if elegidas:
            con.execute(f"UPDATE tareas SET fecha_plan=? WHERE id IN "
                        f"({','.join('?' * len(elegidas))})", [d.isoformat(), *elegidas])
        con.execute(
            'INSERT INTO tareas_propuestas(fecha,estado,libre,ids,creada) VALUES(?,?,?,?,?) '
            'ON CONFLICT(fecha) DO UPDATE SET libre=excluded.libre, ids=excluded.ids',
            (d.isoformat(), 'armada', json.dumps(libre), json.dumps(elegidas), _ahora()))
        con.commit()
    finally:
        con.close()
    return obtener_propuesta(d)


def obtener_propuesta(d):
    d = _d(d)
    con = _conn()
    try:
        r = con.execute('SELECT * FROM tareas_propuestas WHERE fecha=?',
                        (d.isoformat(),)).fetchone()
        if not r:
            return None
        p = dict(r)
        p['libre'] = json.loads(p['libre'] or '{}')
        ids = json.loads(p['ids'] or '[]')
        p['ids'] = ids
        p['tareas'] = compras._rows(con.execute(
            'SELECT t.*, ua.nombre AS asignada_nombre FROM tareas t '
            'LEFT JOIN usuarios ua ON ua.id=t.asignada_a '
            f"WHERE t.id IN ({','.join('?' * len(ids)) or 'NULL'}) "
            'ORDER BY t.puesto, t.prioridad, t.id', ids)) if ids else []
        return p
    finally:
        con.close()


def marcar_enviada(d):
    con = _conn()
    try:
        con.execute("UPDATE tareas_propuestas SET estado='enviada', enviada_en=? "
                    "WHERE fecha=? AND estado='armada'", (_ahora(), _d(d).isoformat()))
        con.commit()
    finally:
        con.close()


def aprobar(d, cambios=None, auto=False):
    """Publica la propuesta. `cambios`: [{id, quitar?, asignada_a?}].

    Las propuestas para hoy quedan marcadas "de hoy"; además se publica el resto de
    lo pendiente, para que quien termine y siga con tiempo encuentre algo en la
    bolsa ("si sobra tiempo"). Una tarea QUITADA por el Dr. no se publica hoy y vuelve
    a considerarse mañana."""
    d = _d(d)
    prop = obtener_propuesta(d)
    if not prop:
        raise ValueError('No hay propuesta para ese día')
    if prop['estado'] in ('aprobada', 'auto'):
        return prop
    quitar = set()
    con = _conn()
    try:
        con.execute('BEGIN IMMEDIATE')
        for c in cambios or []:
            tid = int(c.get('id'))
            if c.get('quitar'):
                quitar.add(tid)
                con.execute('UPDATE tareas SET fecha_plan=NULL WHERE id=?', (tid,))
            elif 'asignada_a' in c:
                con.execute('UPDATE tareas SET asignada_a=? WHERE id=? AND estado=?',
                            (c['asignada_a'] or None, tid, 'pendiente'))
        ids = [i for i in prop['ids'] if i not in quitar]
        q = "UPDATE tareas SET publicada=1 WHERE estado='pendiente' AND publicada=0"
        args = []
        if quitar:
            q += f" AND id NOT IN ({','.join('?' * len(quitar))})"
            args = list(quitar)
        con.execute(q, args)
        con.execute('UPDATE tareas_propuestas SET estado=?, ids=?, aprobada_en=? WHERE fecha=?',
                    ('auto' if auto else 'aprobada', json.dumps(ids), _ahora(), d.isoformat()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return obtener_propuesta(d)


# ── Link firmado para aprobar desde el correo ──────────────────────────────────

VIGENCIA_TOKEN = 16 * 3600


def _serializer():
    secreto = (os.environ.get('TAREAS_SECRET') or os.environ.get('CONSENT_SECRET')
               or 'dev-secret-cambiar-en-produccion')
    return URLSafeTimedSerializer(secreto, salt='tareas-propuesta')


def crear_token(d):
    return _serializer().dumps({'fecha': _d(d).isoformat()})


def leer_token(token):
    try:
        datos = _serializer().loads(token or '', max_age=VIGENCIA_TOKEN)
    except SignatureExpired:
        return {'ok': False, 'motivo': 'vencido'}
    except BadSignature:
        return {'ok': False, 'motivo': 'invalido'}
    return {'ok': True, 'fecha': datos.get('fecha')}


# ══════════════════════════════════════════════════════════════════════════════
# GENERADORES — listas que el sistema ya calcula
# ══════════════════════════════════════════════════════════════════════════════
# Cada uno devuelve [{ref, titulo, detalle, puesto, minutos, prioridad, vence?}].
# UNA tarea agrupada por lista (no una por paciente): "Llamar 6 controles atrasados"
# con los nombres en el detalle. Ref por día → una lista nueva cada día y la de ayer
# vence sola. ⚠️ Nada de esto envía mensajes: es trabajo para una persona.

def _nombre_paciente(rut):
    try:
        import pacientes
        r = pacientes.lookup(rut) or {}
        n = f"{r.get('nombres', '')} {r.get('apellidos', '')}".strip()
        return n or rut
    except Exception:
        return rut


def _gen_controles(d):
    import kpi
    items = [x for x in kpi.controles_programados(hoy=d)['items'] if x['estado'] == 'pendiente']
    if not items:
        return []
    lineas = [f"• {_nombre_paciente(x['rut'])} — debía volver en {x['mes_control']}"
              for x in items[:25]]
    return [{'ref': f'sis:controles:{d.isoformat()}', 'puesto': 'secretaria', 'prioridad': 1,
             'titulo': f'Llamar {len(items)} control(es) programado(s) atrasado(s)',
             'detalle': 'Pacientes a los que se les indicó un control y no han vuelto ni '
                        'tienen hora. Ofrecer agendar.\n' + '\n'.join(lineas),
             'minutos': min(60, 5 * len(items)), 'vence': d.isoformat()}]


def _gen_consent_manana(d):
    import consentimientos
    import scheduling
    manana = scheduling.siguiente_dia_habil(d + timedelta(days=1))
    items = consentimientos.pendientes_con_cita_en(manana)
    if not items:
        return []
    lineas = [f"• {x.get('nombre') or x.get('rut')} ({x.get('hora_cita', '')})" for x in items[:25]]
    return [{'ref': f'sis:consent:{d.isoformat()}', 'puesto': 'secretaria', 'prioridad': 1,
             'titulo': f'Reenviar link de consentimiento a {len(items)} paciente(s) de mañana',
             'detalle': 'Tienen hora el próximo día hábil y no han firmado. Desde el F2: '
                        'Consentimiento → WhatsApp.\n' + '\n'.join(lineas),
             'minutos': min(30, 3 * len(items)), 'vence': d.isoformat()}]


def _gen_consent_subir(d):
    import consentimientos
    items = consentimientos.listar(estado='firmado')
    if not items:
        return []
    return [{'ref': f'sis:subir-consent:{d.isoformat()}', 'puesto': 'secretaria', 'prioridad': 2,
             'titulo': f'Subir {len(items)} consentimiento(s) firmado(s) a DentiDesk',
             'detalle': 'Panel → pestaña Consentimientos → "Abrir en DentiDesk", subir el PDF '
                        'y marcar "Ya lo subí".',
             'minutos': min(40, 4 * len(items)), 'vence': d.isoformat()}]


def _gen_stock(d, cfg):
    items = compras.productos_bajo_minimo()
    if not items:
        return []
    anio, sem, _ = d.isocalendar()
    lineas = [f"• {x['nombre']} (stock {compras._fmt_cant(x['stock_actual'])}, "
              f"mínimo {compras._fmt_cant(x['stock_minimo'])})" for x in items[:30]]
    fin = d + timedelta(days=6 - d.isoweekday())
    return [{'ref': f'sis:stock:{anio}-W{sem:02d}', 'puesto': 'asistente_orto', 'prioridad': 2,
             'titulo': f'Revisar {len(items)} producto(s) bajo el mínimo',
             'detalle': 'Contar lo que hay de verdad (Stock → ⚖️ Ajustar) y pedir lo que falte '
                        '(Solicitudes).\n' + '\n'.join(lineas),
             'minutos': min(45, 3 * len(items)), 'vence': fin.isoformat(),
             'asignada_a': cfg.get('preferido_stock')}]


def _gen_etiquetas(d, cfg):
    cola = compras.cola_pendiente()
    n = sum(int(j.get('cantidad') or 1) for j in cola)
    if not n:
        return []
    return [{'ref': f'sis:etiquetas:{d.isoformat()}', 'puesto': 'asistente_orto', 'prioridad': 3,
             'titulo': f'Imprimir y pegar {n} etiqueta(s) QR',
             'detalle': 'Compras → 🏷️ Etiquetas. Confirmar al final si salieron bien.',
             'minutos': 15, 'vence': d.isoformat(), 'asignada_a': cfg.get('preferido_stock')}]


def _gen_collages(d):
    import fotos_finales
    items = fotos_finales.pendientes()
    if not items:
        return []
    lineas = [f"• {x.get('nombre', '')} (control del {x.get('fecha_control', '')})" for x in items]
    return [{'ref': f'sis:collage:{d.isoformat()}', 'puesto': 'asistente_orto', 'prioridad': 3,
             'titulo': f'Armar {len(items)} collage(s) antes/después',
             'detalle': 'Casos terminados que ya tienen las fotos finales.\n' + '\n'.join(lineas),
             'minutos': min(90, 20 * len(items)), 'vence': d.isoformat()}]


GENERADORES = {
    'controles': lambda d, cfg: _gen_controles(d),
    'consent_manana': lambda d, cfg: _gen_consent_manana(d),
    'consent_subir': lambda d, cfg: _gen_consent_subir(d),
    'stock': _gen_stock,
    'etiquetas': _gen_etiquetas,
    'collages': lambda d, cfg: _gen_collages(d),
}


def correr_generadores(d=None, cfg=None):
    """Cada generador en su propio try/except: uno que falle no deja sin tareas a
    los demás. Los errores se devuelven (los muestra la administración)."""
    d = _d(d or _hoy())
    cfg = cfg or load_config()
    creadas, errores = 0, {}
    for nombre, fn in GENERADORES.items():
        try:
            items = fn(d, cfg) or []
        except Exception as e:
            log.warning('[tareas] generador %s falló: %s', nombre, e)
            errores[nombre] = str(e)
            continue
        con = _conn()
        try:
            for t in items:
                if _insertar(con, {**t, 'origen': 'sistema', 'generador': nombre}, d):
                    creadas += 1
            con.commit()
        finally:
            con.close()
    return {'creadas': creadas, 'errores': errores}


# ══════════════════════════════════════════════════════════════════════════════
# RESUMEN SEMANAL
# ══════════════════════════════════════════════════════════════════════════════

def resumen(desde, hasta):
    """Por persona: tareas hechas y minutos estimados. Por puesto: vencidas sin hacer
    y el tiempo libre estimado del período. `desde`/`hasta` inclusivos."""
    desde, hasta = _d(desde).isoformat(), _d(hasta).isoformat()
    con = _conn()
    try:
        personas = compras._rows(con.execute(
            "SELECT u.nombre, u.puesto, COUNT(t.id) AS hechas, COALESCE(SUM(t.minutos),0) AS minutos "
            "FROM usuarios u LEFT JOIN tareas t ON t.hecha_por=u.id "
            "AND substr(t.hecha_en,1,10) BETWEEN ? AND ? "
            "WHERE u.activo=1 AND u.puesto!='' GROUP BY u.id ORDER BY u.puesto, u.nombre",
            (desde, hasta)))
        vencidas = {r['puesto']: r['n'] for r in con.execute(
            "SELECT puesto, COUNT(*) AS n FROM tareas WHERE estado='vencida' "
            "AND vence BETWEEN ? AND ? GROUP BY puesto", (desde, hasta))}
        props = con.execute('SELECT libre FROM tareas_propuestas WHERE fecha BETWEEN ? AND ?',
                            (desde, hasta)).fetchall()
    finally:
        con.close()
    libre = {p: 0 for p in PUESTOS}
    for r in props:
        try:
            for p, m in json.loads(r['libre'] or '{}').get('por_puesto', {}).items():
                libre[p] = libre.get(p, 0) + int(m)
        except ValueError:
            pass
    usado = {p: 0 for p in PUESTOS}
    for x in personas:
        usado[x['puesto']] = usado.get(x['puesto'], 0) + x['minutos']
    return {'desde': desde, 'hasta': hasta, 'personas': personas, 'vencidas': vencidas,
            'libre_estimado': libre, 'minutos_hechos': usado}


# ══════════════════════════════════════════════════════════════════════════════
# CATÁLOGO SUGERIDO (para partir sin tipear 25 tareas)
# ══════════════════════════════════════════════════════════════════════════════
# Armado con listas de "downtime" de ACT Dental, Dental City, DentistryIQ y Curve
# Dental, más lo que el sistema sabe de la clínica. Lo que ya sale solo de los
# GENERADORES (controles atrasados, consentimientos, stock, etiquetas, collages) NO
# va acá. Se carga una vez desde la administración y después se edita a gusto.
# (titulo, puesto, minutos, prioridad, frecuencia, dias, dia_mes, admin_vial, detalle)
CATALOGO_SUGERIDO = [
    ('Confirmar por teléfono a quienes no respondieron el recordatorio de mañana',
     'secretaria', 15, 1, 'diaria', '', 1, 0, ''),
    ('Contestar mensajes de WhatsApp pendientes (bandeja de Meta)', 'secretaria', 10, 1, 'diaria', '', 1, 0, ''),
    ('Completar dirección y correo de los pacientes de hoy que no los tienen', 'secretaria', 10, 2,
     'diaria', '', 1, 0, 'Muchas fichas tienen "Las Condes" como dirección por defecto.'),
    ('Revisar boletas con seguro complementario sin enviar', 'secretaria', 10, 2, 'diaria', '', 1, 0, ''),
    ('Revisar fichas duplicadas en DentiDesk', 'secretaria', 20, 3, 'semanal', '4', 1, 0,
     'Mismo RUT en dos fichas: avisar al Dr. antes de unir.'),
    ('Pedir reseña en Google a pacientes que respondieron "Excelente"', 'secretaria', 15, 3,
     'semanal', '5', 1, 0, ''),
    ('Ordenar correo de recepción y archivo físico', 'secretaria', 20, 3, 'semanal', '3', 1, 0, ''),
    ('Preparar bandejas y kits de la agenda de mañana', 'asistente_orto', 30, 1, 'diaria', '', 1, 0,
     'Montajes, retiros, instalaciones. Revisar qué aparatos vuelven del laboratorio.'),
    ('Respaldar fotos y escaneos del día en la carpeta del paciente', 'asistente_orto', 10, 2,
     'diaria', '', 1, 0, ''),
    ('Revisar boxes: reponer cajones y ordenar', 'asistente_orto', 10, 2, 'diaria', '', 1, 0, ''),
    ('Armar y reponer kits (cementado, separadores, retiro, alineadores)', 'asistente_orto', 30, 2,
     'semanal', '4', 1, 0, ''),
    ('Esterilización: control biológico/químico y registro', 'asistente_orto', 20, 1, 'semanal', '2', 1, 0, ''),
    ('Limpieza profunda y revisión de equipos (succión, filtros, escáner, sillones)', 'asistente_orto',
     30, 2, 'semanal', '5', 1, 0, ''),
    ('Ordenar carpetas duplicadas en Registros Pacientes (DIGITAL1)', 'asistente_orto', 20, 3,
     'semanal', '4', 1, 0, ''),
    ('Fotos de casos para Instagram (solo con consentimiento)', 'asistente_orto', 30, 3, 'semanal', '5', 1, 0, ''),
    ('Capacitación corta: un video o protocolo', 'asistente_orto', 30, 3, 'semanal', '4', 1, 0, ''),
    ('Revisar vencimientos: materiales, cementos, anestesia, kit de emergencia', 'asistente_orto', 30, 1,
     'mensual', '', 1, 0, ''),
    ('Ordenar material', 'asistente_vial', 120, 1, 'semanal', '1', 1, 0,
     'Lunes en la mañana: es su labor fija de ese bloque.'),
    ('Labores administrativas del Dr. Vial (presupuestos, pacientes a recaptar, laboratorio)',
     'asistente_vial', 60, 1, 'semanal', '2', 1, 1, ''),
    ('Reposición de baños y dispensadores', 'aseo', 15, 1, 'diaria', '', 1, 0, ''),
    ('Boxes 1–4 a fondo: sillones, lámparas, muebles por dentro', 'aseo', 60, 2, 'semanal', '2', 1, 0, ''),
    ('Boxes 5–8 a fondo', 'aseo', 60, 2, 'semanal', '3', 1, 0, ''),
    ('Esterilización, laboratorio y sala de rayos a fondo', 'aseo', 60, 2, 'semanal', '4', 1, 0, ''),
    ('Recepción, sala de espera, baños y vidrios a fondo', 'aseo', 60, 2, 'semanal', '5', 1, 0, ''),
    ('Refrigerador, bodega, cortinas/tapiz y filtros de aire', 'aseo', 90, 3, 'mensual', '', 5, 0, ''),
]


def asegurar_catalogo_inicial():
    """Carga el catálogo sugerido UNA vez, la primera vez que arranca el sistema sin
    ninguna tarea fija. Si después se borran o pausan, no vuelve a cargarlas: la marca
    queda en la config."""
    cfg = load_config()
    if cfg.get('catalogo_cargado'):
        return 0
    n = cargar_catalogo() if not listar_plantillas() else 0
    save_config({'catalogo_cargado': True})
    return n


def cargar_catalogo():
    """Crea las plantillas del catálogo sugerido que aún no existen (por título).
    Idempotente: correrlo dos veces no duplica."""
    existentes = {p['titulo'].strip().lower() for p in listar_plantillas()}
    n = 0
    for (titulo, puesto, minutos, prio, frec, dias, dia_mes, av, det) in CATALOGO_SUGERIDO:
        if titulo.lower() in existentes:
            continue
        crear_plantilla({'titulo': titulo, 'puesto': puesto, 'minutos': minutos, 'prioridad': prio,
                         'frecuencia': frec, 'dias': dias, 'dia_mes': dia_mes,
                         'admin_vial': bool(av), 'detalle': det})
        n += 1
    return n
