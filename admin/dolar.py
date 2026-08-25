# -*- coding: utf-8 -*-
"""
dolar.py — Dólar observado (Banco Central) para las compras en USD.

Cuando se registra una compra en dólares, el sistema propone automáticamente el
tipo de cambio del día de la compra en vez de que alguien lo busque a mano.

Fuente: https://mindicador.cl (API pública gratuita que republica las series del
Banco Central de Chile). Se pide la **serie anual completa** en UNA llamada
(`/api/dolar/<año>`, ~8 KB) en vez de día por día: la API responde de forma
irregular a las consultas por fecha puntual y así además se cachea todo de una.

Cache: tabla `dolar_dia` en la misma base de compras (los valores históricos nunca
cambian). El año en curso se refresca como máximo cada `TTL_HORAS`.

Fines de semana y feriados NO tienen valor publicado: se usa el del último día
hábil anterior (hasta `MAX_DIAS_ATRAS` días) y se informa qué fecha se usó.

Todo es best-effort: si no hay internet o la API falla, se devuelve None y el
usuario escribe el valor a mano (nunca bloquea el registro de la compra).
"""

import json
import time
import urllib.request
from datetime import date, datetime, timedelta

import compras

API = 'https://mindicador.cl/api/dolar/{anio}'
TIMEOUT = 12          # segundos
TTL_HORAS = 6         # refresco máximo del año en curso
MAX_DIAS_ATRAS = 10   # cuánto retroceder si el día no tiene valor (feriados largos)


def _ahora():
    return compras.ahora_cl()


def _init():
    """Crea la tabla de cache si no existe (idempotente)."""
    con = compras._conn()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS dolar_dia (
            fecha TEXT PRIMARY KEY,      -- YYYY-MM-DD (día publicado)
            valor REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dolar_refresco (
            anio TEXT PRIMARY KEY,
            ts   TEXT NOT NULL           -- último refresco de ese año
        );
        """)
        con.commit()
    finally:
        con.close()


def _leer_cache(fecha_iso):
    """Valor exacto de esa fecha en el cache, o None."""
    con = compras._conn()
    try:
        r = con.execute('SELECT valor FROM dolar_dia WHERE fecha=?', (fecha_iso,)).fetchone()
        return r['valor'] if r else None
    finally:
        con.close()


def _leer_cache_anterior(fecha_iso):
    """Último valor publicado en o antes de esa fecha (cubre fin de semana/feriado).
    Devuelve (valor, fecha_usada) o (None, None)."""
    limite = (date.fromisoformat(fecha_iso) - timedelta(days=MAX_DIAS_ATRAS)).isoformat()
    con = compras._conn()
    try:
        r = con.execute('SELECT fecha, valor FROM dolar_dia WHERE fecha<=? AND fecha>=? '
                        'ORDER BY fecha DESC LIMIT 1', (fecha_iso, limite)).fetchone()
        return (r['valor'], r['fecha']) if r else (None, None)
    finally:
        con.close()


def _refrescado_hace_poco(anio):
    con = compras._conn()
    try:
        r = con.execute('SELECT ts FROM dolar_refresco WHERE anio=?', (str(anio),)).fetchone()
    finally:
        con.close()
    if not r:
        return False
    try:
        ts = datetime.fromisoformat(r['ts'])
    except ValueError:
        return False
    ahora = _ahora()
    if ts.tzinfo and not ahora.tzinfo:
        ts = ts.replace(tzinfo=None)
    elif ahora.tzinfo and not ts.tzinfo:
        ts = ts.replace(tzinfo=ahora.tzinfo)
    return (ahora - ts) < timedelta(hours=TTL_HORAS)


def refrescar_anio(anio, intentos=2):
    """Baja la serie completa de un año y la guarda en el cache.
    Devuelve cuántos días se guardaron, o -1 si falló (sin red, API caída…).
    Reintenta: la API devuelve 500/timeouts esporádicos que se resuelven solos."""
    _init()   # se puede llamar directo (precarga) sin pasar antes por observado()
    data = None
    for intento in range(1, intentos + 1):
        try:
            req = urllib.request.Request(API.format(anio=anio),
                                         headers={'User-Agent': 'ortodonciarichard/compras'})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode('utf-8'))
            break
        except Exception as e:
            if intento >= intentos:
                print('[dolar] no se pudo obtener la serie', anio, '-', e)
                return -1
            time.sleep(1.5)
    if data is None:
        return -1

    serie = data.get('serie') or []
    if not serie:
        return 0
    filas = []
    for it in serie:
        f = (it.get('fecha') or '')[:10]
        v = it.get('valor')
        if len(f) == 10 and isinstance(v, (int, float)) and v > 0:
            filas.append((f, float(v)))
    if not filas:
        return 0
    con = compras._conn()
    try:
        con.executemany('INSERT OR REPLACE INTO dolar_dia(fecha,valor) VALUES(?,?)', filas)
        con.execute('INSERT OR REPLACE INTO dolar_refresco(anio,ts) VALUES(?,?)',
                    (str(anio), _ahora().isoformat(timespec='seconds')))
        con.commit()
    finally:
        con.close()
    return len(filas)


def observado(fecha_iso=None):
    """Dólar observado para una fecha (YYYY-MM-DD; por defecto hoy en Chile).

    Devuelve un dict:
      {'valor': 922.12, 'fecha': '2026-08-19', 'exacto': True|False, 'fuente': ...}
    o None si no se pudo determinar (sin red y sin cache).

    'exacto' = False significa que ese día no tenía valor publicado (fin de semana,
    feriado o aún no publicado) y se usó el del último día hábil anterior.
    """
    _init()
    try:
        f = date.fromisoformat(fecha_iso) if fecha_iso else _ahora().date()
    except (TypeError, ValueError):
        return None
    fecha_iso = f.isoformat()

    valor = _leer_cache(fecha_iso)
    if valor is not None:
        return {'valor': valor, 'fecha': fecha_iso, 'exacto': True, 'fuente': 'mindicador.cl'}

    # No está exacta: refrescar el año (salvo que ya se haya hecho hace poco) e insistir.
    if not _refrescado_hace_poco(f.year):
        refrescar_anio(f.year)
        valor = _leer_cache(fecha_iso)
        if valor is not None:
            return {'valor': valor, 'fecha': fecha_iso, 'exacto': True, 'fuente': 'mindicador.cl'}

    # Fin de semana / feriado / aún sin publicar → último día hábil anterior.
    valor, usada = _leer_cache_anterior(fecha_iso)
    if valor is not None:
        return {'valor': valor, 'fecha': usada, 'exacto': False, 'fuente': 'mindicador.cl'}

    # Puede que la fecha caiga al inicio del año y el valor esté en el año anterior.
    if f.month == 1 and not _refrescado_hace_poco(f.year - 1):
        refrescar_anio(f.year - 1)
        valor, usada = _leer_cache_anterior(fecha_iso)
        if valor is not None:
            return {'valor': valor, 'fecha': usada, 'exacto': False, 'fuente': 'mindicador.cl'}
    return None


if __name__ == '__main__':
    import sys
    print(observado(sys.argv[1] if len(sys.argv) > 1 else None))
