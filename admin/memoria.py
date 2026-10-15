"""
memoria.py - Mantener la memoria del proceso bajo el limite de Render (512 MB).

El grafico de memoria de Render muestra una ESCALERA: cada tarea pesada (la cosecha
de KPIs, la proyeccion clinica, el respaldo, una pestana grande del panel) sube el
consumo un escalon y despues NO baja, aunque la tarea ya termino. No es una fuga de
Python: es el asignador de memoria de Linux (glibc), que se queda con lo que el
proceso libero para reusarlo despues en vez de devolverlo al sistema. Con muchos
hilos (este servidor tiene ~20 loops + 8 hilos de gunicorn) cada hilo puede tener
su propia "arena" y el sobrante se multiplica. Al cabo de unos dias de escalones el
proceso cruza el limite y Render lo reinicia (2026-09-25 al 2026-10-15: seis veces).

Tres piezas, todas no-op fuera de Linux (en Windows no hacen nada):
  - configurar(): limita las arenas a 2. Hay que llamarla ANTES de lanzar los hilos.
  - liberar(etiqueta): gc + malloc_trim(0) — devuelve al sistema lo libre. Se llama
    al final de cada tarea pesada y periodicamente.
  - rss_mb() + subidas: para saber QUE hizo subir la memoria (lo muestra /api/salud).
    Sin esto, una caida de las 15:00 no tiene a quien atribuirsela.
"""

import gc
import os
import sys
import ctypes
import logging
import threading
from collections import deque
from contextlib import contextmanager

import fechas

log = logging.getLogger(__name__)

_M_ARENA_MAX = -8          # constante de mallopt() en glibc
_ARENAS = 2

_libc = None
if sys.platform.startswith('linux'):
    try:
        _libc = ctypes.CDLL('libc.so.6')
    except OSError:
        _libc = None

_LOCK = threading.Lock()
_PICO = {'mb': 0.0, 'hora': ''}
# Tareas y solicitudes que subieron la memoria de forma notoria (sin datos personales:
# solo el nombre de la tarea o el path de la ruta, nunca la query string).
SUBIDAS = deque(maxlen=30)
UMBRAL_SUBIDA_MB = 25.0


def configurar():
    """Limita las arenas de malloc. Devuelve True si se aplico."""
    if _libc is None:
        return False
    try:
        return bool(_libc.mallopt(_M_ARENA_MAX, _ARENAS))
    except Exception as e:      # pragma: no cover - depende de la libc
        log.warning('memoria: mallopt fallo: %s', e)
        return False


def rss_mb():
    """Memoria residente del proceso en MB, o None si no se puede medir."""
    try:
        with open('/proc/self/statm') as f:
            residente = int(f.read().split()[1])
        mb = residente * os.sysconf('SC_PAGE_SIZE') / 1e6
    except (OSError, ValueError, IndexError, AttributeError):
        try:
            import psutil
            mb = psutil.Process().memory_info().rss / 1e6
        except Exception:
            return None
    with _LOCK:
        if mb > _PICO['mb']:
            _PICO['mb'] = mb
            _PICO['hora'] = fechas.ahora_chile().isoformat(timespec='seconds')
    return mb


def pico():
    with _LOCK:
        return {'mb': round(_PICO['mb']), 'hora': _PICO['hora']}


def anotar_subida(origen, antes, despues):
    """Registra que `origen` hizo subir la memoria de `antes` a `despues` (MB)."""
    if antes is None or despues is None or despues - antes < UMBRAL_SUBIDA_MB:
        return False
    SUBIDAS.append({'hora': fechas.ahora_chile().isoformat(timespec='seconds'),
                    'origen': origen, 'antes_mb': round(antes), 'despues_mb': round(despues)})
    log.warning('memoria: %s subio de %.0f a %.0f MB', origen, antes, despues)
    return True


def liberar(etiqueta=''):
    """gc + malloc_trim(0). Devuelve {'antes','despues'} en MB (None si no se mide).
    Nunca lanza: es higiene, no puede tumbar la tarea que la llama."""
    antes = rss_mb()
    try:
        gc.collect()
        if _libc is not None:
            _libc.malloc_trim(0)
    except Exception as e:      # pragma: no cover
        log.warning('memoria: liberar fallo: %s', e)
    despues = rss_mb()
    if etiqueta and antes is not None and despues is not None:
        print(f'[memoria] {etiqueta}: {antes:.0f} -> {despues:.0f} MB')
    return {'antes': antes, 'despues': despues}


@contextmanager
def tarea(etiqueta):
    """Envuelve una tarea pesada: anota si subio la memoria y al terminar la
    devuelve al sistema. Las excepciones de la tarea pasan intactas."""
    antes = rss_mb()
    try:
        yield
    finally:
        try:
            anotar_subida(etiqueta, antes, rss_mb())
            liberar(etiqueta)
        except Exception:   # pragma: no cover
            pass
