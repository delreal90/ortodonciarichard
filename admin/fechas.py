"""
fechas.py — La hora de Chile, en un solo lugar.

POR QUE EXISTE
--------------
Render corre en **UTC**, 3-4 horas ADELANTE de Chile. Un `datetime.now()` o un
`date.today()` pelado en este proyecto no es "ahora": es el futuro.

Media docena de modulos ya lo habian resuelto, cada uno con su propia copia del
mismo bloque `ZoneInfo` (consentimientos, seguros, stats, cumpleanos). Pero a
`scheduling.py` — el modulo que decide que horas se le ofrecen al paciente en la
agenda online — se le habia olvidado, y ahi el error costaba citas:

    ahora  = datetime.now()                       # UTC  (ej. 23:00)
    inicio = datetime.combine(fecha, hora_cita)   # Chile (ej. 19:00 del dia sig.)
    inicio - ahora >= 12h ?

Como `ahora` va 4 h adelantado, el margen calculado sale 4 h MAS CHICO que el
real: el sistema rechazaba horas que si cumplian la anticipacion minima. Fallaba
del lado seguro (nunca agendaba demasiado justo), pero le borraba al paciente una
franja de horas ofrecibles, todos los dias.

REGLA
-----
En este proyecto **no se usa `datetime.now()` ni `date.today()`**. Se usa
`ahora_chile()` / `hoy_chile()`. Si necesitas la fecha de negocio (que dia es
"hoy" para la clinica), es siempre `hoy_chile()`.

Las funciones aceptan/devuelven datetimes **naive** en hora de Chile a proposito:
todo el resto del proyecto (DentiDesk, los JSON de registro, los `.isoformat()`
guardados) trabaja con horas de pared de Chile sin offset, y mezclar aware/naive
en comparaciones lanza TypeError. `ahora_chile_aware()` esta para cuando si se
necesita el offset explicito.
"""

from datetime import datetime, date

try:
    from zoneinfo import ZoneInfo
    TZ_CHILE = ZoneInfo('America/Santiago')
except Exception:      # pragma: no cover — falta el paquete tzdata
    TZ_CHILE = None


def ahora_chile_aware():
    """datetime actual CON zona horaria (America/Santiago)."""
    return datetime.now(TZ_CHILE) if TZ_CHILE else datetime.now()


def ahora_chile():
    """datetime actual en hora de Chile, SIN tzinfo.

    Naive a proposito: es la hora de pared de la clinica, comparable directamente
    con las horas que devuelve DentiDesk y con las que se guardan en los registros.
    """
    return ahora_chile_aware().replace(tzinfo=None)


def hoy_chile():
    """La fecha de HOY en Chile. Reemplaza a date.today(), que en Render devuelve
    el dia siguiente entre las ~20:00 y medianoche hora chilena."""
    return ahora_chile_aware().date()


def es_hoy_chile(d):
    """True si `d` (date o ISO 'YYYY-MM-DD') es hoy en Chile."""
    if isinstance(d, str):
        try:
            d = datetime.strptime(d[:10], '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return False
    return d == hoy_chile()
