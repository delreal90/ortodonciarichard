"""
cumpleanos.py - Cumpleanos del equipo y de los pacientes (Ortodoncia Richard)

Alimenta la seccion de cumpleanos del reporte diario que le llega a Alberto
(ver ../revision-evoluciones/INSTRUCCIONES.md, Paso 4.8).

Dos fuentes distintas:

  - EQUIPO: lista propia de la clinica (doctores + staff), importada desde la
    tabla de texto 'cumpleanos doctores.txt'. Vive en el disco persistente,
    NO en git.
  - PACIENTES: la base local `pacientes.py`, cuyo campo `fecha_nacimiento` se
    siembra desde el export 'Listado de Cumpleanos' de DentiDesk.

PRIVACIDAD: ⚠️ el repo del proyecto es PUBLICO (sirve el sitio por GitHub
Pages). Ninguna fecha de nacimiento puede quedar versionada -- por eso el
archivo del equipo se guarda junto al resto de los datos de runtime (disco
persistente de Render, derivado de PATIENT_INDEX_PATH) y esta en .gitignore.

Este modulo NO escribe a pacientes ni al equipo: solo informa. El saludo lo
decide y lo manda una persona.
"""

import os
import re
import json
import unicodedata
from pathlib import Path
from datetime import date, datetime, timedelta

import fechas
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

# Mismo idioma de rutas que el resto de los modulos: todo cuelga del directorio
# de PATIENT_INDEX_PATH (disco persistente en Render).
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
EQUIPO_PATH = Path(os.environ.get('CUMPLEANOS_EQUIPO_PATH',
                                  _BASE_DIR / 'cumpleanos_equipo.json'))

_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
          'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def ahora_chile():
    """Fecha/hora en America/Santiago (Render corre en UTC). Ver fechas.py."""
    return fechas.ahora_chile_aware()


def hoy_chile():
    return fechas.hoy_chile()


def fecha_legible(f):
    """date -> 'sábado 25 de julio de 2026'."""
    return f'{_DIAS[f.weekday()]} {f.day} de {_MESES[f.month - 1]} de {f.year}'


# ── Almacen del equipo ───────────────────────────────────────────────────────

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(EQUIPO_PATH, default=[], indent=1)


def _load_equipo():
    data = _STORE.load()
    # Formato historico: hubo una version que guardaba {'equipo': [...]}.
    lista = data if isinstance(data, list) else (data or {}).get('equipo', [])
    # Migracion en caliente: registros importados ANTES de que existiera
    # 'dia_mes' (solo tenian fecha_nacimiento completa) no lo traen guardado.
    # Derivarlo aca evita que el equipo entero desaparezca del correo hasta
    # el proximo re-import -- no se reescribe el archivo, solo se completa
    # en memoria para esta lectura.
    for p in lista:
        if not p.get('dia_mes') and p.get('fecha_nacimiento'):
            try:
                f = date.fromisoformat(p['fecha_nacimiento'])
                p['dia_mes'] = f'{f.day:02d}-{f.month:02d}'
            except ValueError:
                pass
    return lista


def _save_equipo(lista):
    _STORE.save(lista)


def _norm(s):
    s = unicodedata.normalize('NFKD', (s or '').strip())
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


_RE_DMY = re.compile(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$')
# Dia/mes SIN año conocido: '23/04' o '23/04/xxxx' (la convencion que usa la
# clinica en el .txt para marcar "se el dia, no el año").
_RE_DM_SIN_ANIO = re.compile(r'^(\d{1,2})[/-](\d{1,2})(?:[/-][xX]{2,4})?$')


def parsear_tabla_equipo(texto):
    """Parsea la tabla de 'cumpleanos doctores.txt'.

    Formato (tabla markdown):
        | Nombre           | Fecha de nacimiento |
        | ---------------- | ------------------- |
        | Rodrigo Oyonarte | 13/04/1973          |
        | Ana Maria        | 23/04/xxxx          |
        | Felipe Pozo      | PENDIENTE           |

    Devuelve [{nombre, fecha_nacimiento (ISO o ''), dia_mes ('DD-MM' o ''),
    pendiente (bool)}]. 'fecha_nacimiento' solo se llena si se conoce el año
    (hace falta para calcular la edad). 'dia_mes' se llena si se conoce el
    dia y el mes, CON o SIN año (acepta 'DD/MM/xxxx' o 'DD/MM' a secas) --
    es lo que usa la deteccion de cumpleanos cuando no hay año. 'pendiente'
    es SOLO cuando no hay ni dia/mes (p.ej. 'PENDIENTE'): alguien con dia/mes
    pero sin año YA sirve para saludar, no se descarta ni se marca pendiente
    -- solo no se le puede mostrar la edad."""
    out = []
    for linea in (texto or '').splitlines():
        linea = linea.strip()
        if not linea.startswith('|'):
            continue
        celdas = [c.strip() for c in linea.strip('|').split('|')]
        if len(celdas) < 2:
            continue
        nombre, fecha_txt = celdas[0], celdas[1]
        if not nombre or set(nombre) <= set('- '):
            continue                                   # separador |---|---|
        if _norm(nombre) in ('nombre', 'nombre completo'):
            continue                                   # cabecera
        iso, dia_mes = '', ''
        m = _RE_DMY.match(fecha_txt)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                iso = date(y, mo, d).isoformat()
                dia_mes = f'{d:02d}-{mo:02d}'
            except ValueError:
                iso = ''
        if not dia_mes:
            m2 = _RE_DM_SIN_ANIO.match(fecha_txt)
            if m2:
                d, mo = int(m2.group(1)), int(m2.group(2))
                try:
                    date(2000, mo, d)  # solo valida que el dia exista en ese mes (2000=bisiesto)
                    dia_mes = f'{d:02d}-{mo:02d}'
                except ValueError:
                    pass
        out.append({'nombre': nombre, 'fecha_nacimiento': iso, 'dia_mes': dia_mes,
                    'pendiente': not dia_mes})
    return out


def importar_equipo(texto, reemplazar=True):
    """Importa/actualiza la lista del equipo desde el texto de la tabla.

    reemplazar=True (default) deja EXACTAMENTE lo que trae el archivo: es una
    lista corta y curada a mano, asi que el archivo es la fuente de verdad y
    quien se va de la clinica debe desaparecer. Con reemplazar=False solo
    agrega/actualiza por nombre."""
    nuevos = parsear_tabla_equipo(texto)
    if reemplazar:
        _save_equipo(nuevos)
    else:
        actual = {_norm(p['nombre']): p for p in _load_equipo()}
        for p in nuevos:
            actual[_norm(p['nombre'])] = p
        _save_equipo(sorted(actual.values(), key=lambda p: _norm(p['nombre'])))
    lista = _load_equipo()
    return {'total': len(lista),
            'con_fecha': sum(1 for p in lista if not p.get('pendiente')),
            'con_anio': sum(1 for p in lista if p.get('fecha_nacimiento')),
            'pendientes': [p['nombre'] for p in lista if p.get('pendiente')]}


def equipo():
    return _load_equipo()


# ── Consulta ─────────────────────────────────────────────────────────────────

def equipo_cumple_el(fecha):
    """Miembros del equipo que cumplen anios (o cuyo dia es hoy, si no se
    conoce el año) en 'fecha' (date). Devuelve [{nombre, fecha_nacimiento,
    edad}] ordenado por nombre. 'edad' es None cuando no se conoce el año de
    nacimiento -- el saludo se muestra igual, solo sin la edad."""
    import pacientes as _pac
    objetivo = _pac.dias_objetivo_cumple(fecha)
    out = []
    for p in _load_equipo():
        dia_mes = (p.get('dia_mes') or '').strip()
        if not dia_mes:
            continue  # compat con registros viejos sin dia_mes: ver migracion en _load_equipo
        try:
            d, mo = (int(x) for x in dia_mes.split('-'))
        except ValueError:
            continue
        if (d, mo) not in objetivo:
            continue
        iso = (p.get('fecha_nacimiento') or '').strip()
        edad = None
        if iso:
            try:
                edad = fecha.year - date.fromisoformat(iso).year
            except ValueError:
                edad = None
        out.append({'nombre': p.get('nombre', ''), 'fecha_nacimiento': iso, 'edad': edad})
    out.sort(key=lambda p: _norm(p['nombre']))
    return out


def proximo_dia(desde=None):
    """El dia siguiente a 'desde' (default hoy en Chile).

    A diferencia de otros modulos del proyecto, aca NO se salta el fin de
    semana: un cumpleanios cae el dia que cae, y el reporte del viernes debe
    avisar el del sabado -- si saltara al lunes, ese saludo llegaria tarde."""
    return (desde or hoy_chile()) + timedelta(days=1)


def proximos(fecha=None):
    """Cumpleanos de una fecha (default: manana).

    Devuelve {fecha, fecha_legible, equipo:[...], pacientes:[...]} donde
    'edad' son los anios que la persona CUMPLE ese dia."""
    import pacientes as _pac
    f = fecha or proximo_dia()
    return {
        'fecha': f.isoformat(),
        'fecha_legible': fecha_legible(f),
        'equipo': equipo_cumple_el(f),
        'pacientes': _pac.cumplen_el(f),
    }
