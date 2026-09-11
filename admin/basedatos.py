"""
basedatos.py — El archivo de base de datos del proyecto, en un solo lugar.

POR QUE EXISTE
--------------
Hasta ahora `kpi.py` tenia su propio `_conn()` privado apuntando a `kpi.db`, y
cualquier modulo nuevo que quisiera escribir en la MISMA base tendria que
escribir otro `_conn()` identico. Eso son dos modulos que *casualmente* abren el
mismo archivo. Con este modulo es **una base con varios modulos encima**, que es
lo que se quiere que sea: la gracia de tener una sola base es que un JOIN cruce
un ancho de arcada con el destino de la primera consulta sin salir de SQL.

EL NOMBRE: kpi.db -> clinica.db
--------------------------------
El archivo nacio guardando indicadores y se llamo `kpi.db`. Hoy guarda ademas el
registro clinico, las mediciones y el historial de cinco anos de agenda: es el
activo de datos del proyecto, no un cache. Un archivo llamado `kpi.db` se lee
como "metricas, derivado, desechable", y eso es una invitacion a borrarlo para
liberar disco.

Y NO es desechable: `citas` se puede volver a barrer de DentiDesk con
`kpi.backfill()`, pero **`disponibilidad` no se puede reconstruir de ninguna
parte** — `getAvailableHours` solo responde por dias FUTUROS, asi que los
minutos libres de un dia que ya paso no existen en ningun lado. Por eso
`backup.py` la respalda.

COMO SE RESUELVE LA RUTA
------------------------
    CLINICA_DB_PATH  ->  KPI_DB_PATH (compatibilidad)  ->  <disco>/clinica.db

Si alguna de las dos variables esta seteada, manda esa ruta y **no se renombra
nada**: quien la seteo sabe donde quiere el archivo. Eso mantiene andando tanto
un Render que ya tenga `KPI_DB_PATH` como `test_kpi.py`, que la fija antes de
importar.

Si no hay variable (el caso por defecto), al arrancar se renombra `kpi.db` a
`clinica.db` una sola vez.

⚠️ El checkpoint del WAL antes de renombrar NO es opcional: SQLite en modo WAL
guarda los cambios recientes en `kpi.db-wal`, y mover solo el `.db` dejaria esos
cambios atras. Se consolidan primero con `wal_checkpoint(TRUNCATE)`.

⚠️ Si el renombre falla, se SIGUE USANDO EL ARCHIVO VIEJO. La alternativa
—insistir con el nombre nuevo— crearia una base vacia al lado de los datos
reales, y el sistema arrancaria como si nunca hubiera habido nada. Que el
archivo conserve el nombre viejo es un problema cosmetico; perder de vista los
datos, no.
"""

import os
import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

NOMBRE = 'clinica.db'
NOMBRE_ANTERIOR = 'kpi.db'

# Mismo patron que el resto del proyecto: los datos viven junto a
# patient_index.json, en el disco persistente de Render.
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent


def _resolver():
    """(ruta, se_puede_renombrar). Una ruta dada por variable de entorno manda y
    nunca se toca."""
    env = os.environ.get('CLINICA_DB_PATH') or os.environ.get('KPI_DB_PATH')
    if env:
        return Path(env), False
    return _BASE_DIR / NOMBRE, True


DB_PATH, _RENOMBRABLE = _resolver()


def _checkpoint(path):
    """Consolida el WAL dentro del .db para poder moverlo entero."""
    con = sqlite3.connect(str(path), timeout=20)
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        con.commit()
    finally:
        con.close()


def migrar_nombre():
    """Renombra kpi.db -> clinica.db una sola vez. Idempotente y sin red.

    Devuelve True solo si movio el archivo en esta llamada.
    """
    global DB_PATH
    if not _RENOMBRABLE or DB_PATH.exists():
        return False
    viejo = _BASE_DIR / NOMBRE_ANTERIOR
    if not viejo.exists():
        return False
    try:
        _checkpoint(viejo)
        os.replace(str(viejo), str(DB_PATH))
    except Exception as e:
        # Ver el docstring del modulo: se sigue con el archivo que TENGA los
        # datos, antes que arrancar contra una base vacia.
        #
        # ⚠️ El orden importa. Si otro proceso gano la carrera y ya renombro,
        # `viejo` ya no existe: caer ahi crearia una base vacia al lado de los
        # datos reales. Hoy Render corre con --workers 1 y la carrera no puede
        # ocurrir, pero subir a 2 workers no deberia romper esto en silencio.
        if DB_PATH.exists():
            log.warning('[basedatos] el renombre fallo (%r) pero %s ya existe: '
                        'otro proceso lo hizo primero.', e, NOMBRE)
            return False
        log.warning('[basedatos] no se pudo renombrar %s -> %s (%r); se sigue '
                    'usando %s', NOMBRE_ANTERIOR, NOMBRE, e, NOMBRE_ANTERIOR)
        DB_PATH = viejo
        return False
    # El -wal quedo vacio por el checkpoint y el -shm se regenera solo.
    for sufijo in ('-wal', '-shm'):
        sobrante = Path(str(viejo) + sufijo)
        try:
            if sobrante.exists():
                sobrante.unlink()
        except OSError:
            pass
    log.warning('[basedatos] %s renombrado a %s', NOMBRE_ANTERIOR, NOMBRE)
    return True


# Se corre al importar, ANTES de que nadie abra una conexion: la primera
# conexion a la ruta nueva crearia el archivo y dejaria el renombre sin efecto,
# con los datos viejos huerfanos al lado.
try:
    migrar_nombre()
except Exception as _e:            # pragma: no cover — defensa de arranque
    log.warning('[basedatos] migrar_nombre fallo al importar: %r', _e)


def conectar():
    """Conexion nueva por llamada (mismo criterio que compras.py y kpi.py).

    WAL permite lecturas concurrentes con una escritura; el timeout evita
    'database is locked' entre el hilo de proyeccion y los requests del panel.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con
