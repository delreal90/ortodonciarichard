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

def _load_equipo():
    if EQUIPO_PATH.exists():
        try:
            data = json.loads(EQUIPO_PATH.read_text(encoding='utf-8'))
            return data if isinstance(data, list) else data.get('equipo', [])
        except (ValueError, OSError):
            return []
    return []


def _save_equipo(lista):
    EQUIPO_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EQUIPO_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, EQUIPO_PATH)


def _norm(s):
    s = unicodedata.normalize('NFKD', (s or '').strip())
    return ''.join(c for c in s if not unicodedata.combining(c)).lower()


_RE_DMY = re.compile(r'^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$')


def parsear_tabla_equipo(texto):
    """Parsea la tabla de 'cumpleanos doctores.txt'.

    Formato (tabla markdown):
        | Nombre           | Fecha de nacimiento |
        | ---------------- | ------------------- |
        | Rodrigo Oyonarte | 13/04/1973          |
        | Felipe Pozo      | PENDIENTE           |

    Devuelve [{nombre, fecha_nacimiento (ISO o ''), pendiente (bool)}].
    Las filas sin fecha valida (p.ej. 'PENDIENTE') NO se descartan: se guardan
    con pendiente=True para que se vea a quien le falta el dato, en vez de que
    la persona desaparezca en silencio."""
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
        iso, pendiente = '', True
        m = _RE_DMY.match(fecha_txt)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                iso = date(y, mo, d).isoformat()
                pendiente = False
            except ValueError:
                iso, pendiente = '', True
        out.append({'nombre': nombre, 'fecha_nacimiento': iso, 'pendiente': pendiente})
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
            'pendientes': [p['nombre'] for p in lista if p.get('pendiente')]}


def equipo():
    return _load_equipo()


# ── Consulta ─────────────────────────────────────────────────────────────────

def equipo_cumple_el(fecha):
    """Miembros del equipo que cumplen anios en 'fecha' (date).
    Devuelve [{nombre, fecha_nacimiento, edad}] ordenado por nombre."""
    import pacientes as _pac
    objetivo = _pac.dias_objetivo_cumple(fecha)
    out = []
    for p in _load_equipo():
        iso = (p.get('fecha_nacimiento') or '').strip()
        if not iso:
            continue
        try:
            f = date.fromisoformat(iso)
        except ValueError:
            continue
        if (f.day, f.month) not in objetivo:
            continue
        out.append({'nombre': p.get('nombre', ''),
                    'fecha_nacimiento': iso,
                    'edad': fecha.year - f.year})
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
