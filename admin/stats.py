"""
stats.py - Registro y agregacion de agendamientos (Ortodoncia Richard)

Cada reserva exitosa se guarda como una linea JSON (JSONL) en un archivo en el
disco persistente. Sin datos personales sensibles: solo lo necesario para las
estadisticas (fecha/hora de la cita, doctor, motivo, especialidad, si el paciente
ya estaba en la base, y cuando se hizo la reserva).

Ruta configurable por env STATS_PATH (en Render apunta al disco persistente,
p.ej. /var/data/agendamientos.jsonl).
"""

import os
import json
from pathlib import Path
from datetime import datetime, date, timedelta

try:
    from zoneinfo import ZoneInfo
    _TZ_CL = ZoneInfo('America/Santiago')
except Exception:
    _TZ_CL = None


def _ahora_cl():
    """Hora actual en Chile (America/Santiago). En Render el servidor corre en
    UTC, asi que datetime.now() daria una hora adelantada -- por eso se fija la
    zona explicitamente para que el 'ts' de las reservas sea hora local chilena."""
    return datetime.now(_TZ_CL) if _TZ_CL else datetime.now()

# Por defecto, junto a la base de pacientes (mismo disco persistente).
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
STATS_PATH = Path(os.environ.get('STATS_PATH', _BASE_DIR / 'agendamientos.jsonl'))
EVENTOS_PATH = Path(os.environ.get('EVENTOS_PATH', _BASE_DIR / 'eventos.jsonl'))

_DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

# Embudo de agendamiento: pasos en orden. Un paciente "avanza" por estos pasos;
# medir cuántos llegan a cada uno revela dónde abandonan.
_PASOS = ['abrir', 'especialidad', 'rut', 'datos', 'profesional', 'motivo', 'horas', 'reservado']
_PASOS_LABEL = {
    'abrir':        'Abrió la agenda',
    'especialidad': 'Eligió especialidad',
    'rut':          'Ingresó su RUT',
    'datos':        'Completó sus datos',
    'profesional':  'Eligió profesional',
    'motivo':       'Eligió motivo',
    'horas':        'Vio horas disponibles',
    'reservado':    'Confirmó la reserva',
}


def registrar(evento):
    """Agrega un agendamiento al log. 'evento' es un dict; se completa con ts."""
    try:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        evento = {**evento, 'ts': _ahora_cl().isoformat(timespec='seconds')}
        with open(STATS_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(evento, ensure_ascii=False) + '\n')
        return True
    except Exception:
        return False


def _leer(path=None):
    path = path or STATS_PATH
    if not path.exists():
        return []
    out = []
    try:
        with open(path, encoding='utf-8') as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    out.append(json.loads(linea))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _hora_franja(hhmm):
    """'14:30' -> '14:00' (agrupa por hora)."""
    try:
        return f"{int(hhmm.split(':')[0]):02d}:00"
    except (ValueError, AttributeError, IndexError):
        return '—'


def resumen(desde=None, hasta=None):
    """
    Devuelve un dict con agregaciones. Filtra por fecha de la CITA (campo 'fecha')
    si se pasan desde/hasta (date). Sin filtro = todo el historico.
    """
    eventos = _leer()

    def en_rango(e):
        if not (desde or hasta):
            return True
        try:
            f = datetime.strptime(e.get('fecha', ''), '%Y-%m-%d').date()
        except ValueError:
            return True
        if desde and f < desde:
            return False
        if hasta and f > hasta:
            return False
        return True

    eventos = [e for e in eventos if en_rango(e)]

    por_motivo, por_doctor, por_esp = {}, {}, {}
    por_dia_semana = {d: 0 for d in _DIAS}
    por_hora, por_fecha_reserva = {}, {}
    nuevos = conocidos = 0

    for e in eventos:
        por_motivo[e.get('motivo_label', '—')] = por_motivo.get(e.get('motivo_label', '—'), 0) + 1
        por_doctor[e.get('doctor_nombre', '—')] = por_doctor.get(e.get('doctor_nombre', '—'), 0) + 1
        por_esp[e.get('especialidad', '—')] = por_esp.get(e.get('especialidad', '—'), 0) + 1
        # Dia de semana y hora EN QUE SE HIZO EL AGENDAMIENTO (momento de la reserva,
        # del campo ts), no de la cita. Asi se ve cuando suelen agendar los pacientes.
        ts = (e.get('ts') or '')
        try:
            dt = datetime.fromisoformat(ts)
            por_dia_semana[_DIAS[dt.weekday()]] += 1
            por_hora[f'{dt.hour:02d}:00'] = por_hora.get(f'{dt.hour:02d}:00', 0) + 1
        except (ValueError, IndexError):
            pass
        # cuando se hizo la reserva (fecha del ts) — para el timeline
        if ts[:10]:
            por_fecha_reserva[ts[:10]] = por_fecha_reserva.get(ts[:10], 0) + 1
        if e.get('paciente_conocido'):
            conocidos += 1
        else:
            nuevos += 1

    # timeline ultimos 30 dias (por fecha de reserva)
    hoy = date.today()
    timeline = []
    for k in range(29, -1, -1):
        d = (hoy - timedelta(days=k)).isoformat()
        timeline.append({'fecha': d, 'total': por_fecha_reserva.get(d, 0)})

    def ordenar(d):
        return [{'label': k, 'total': v} for k, v in sorted(d.items(), key=lambda x: -x[1])]

    return {
        'total': len(eventos),
        'nuevos': nuevos,
        'conocidos': conocidos,
        'por_motivo': ordenar(por_motivo),
        'por_doctor': ordenar(por_doctor),
        'por_especialidad': ordenar(por_esp),
        'por_dia_semana': [{'label': d, 'total': por_dia_semana[d]} for d in _DIAS],
        'por_hora': [{'label': h, 'total': por_hora[h]} for h in sorted(por_hora)],
        'timeline_30d': timeline,
    }


def ultimos(n=20):
    """Las N reservas mas recientes (por 'ts'), para revisar/depurar el registro
    desde el panel. 'ts' se usa como identificador para eliminar()."""
    eventos = _leer()
    eventos.sort(key=lambda e: e.get('ts', ''), reverse=True)
    return eventos[:n]


def eliminar(ts):
    """Elimina del log todas las entradas cuyo 'ts' coincida exactamente (sirve
    para sacar una reserva de prueba que distorsiona las estadisticas). Devuelve
    cuantas se eliminaron."""
    eventos = _leer()
    restantes = [e for e in eventos if e.get('ts') != ts]
    eliminados = len(eventos) - len(restantes)
    if eliminados:
        with open(STATS_PATH, 'w', encoding='utf-8') as f:
            for e in restantes:
                f.write(json.dumps(e, ensure_ascii=False) + '\n')
    return eliminados


# ── Embudo de agendamiento (dónde abandonan los pacientes) ────────────────────

def registrar_evento(sesion, paso, latency_ms=None):
    """Registra un paso del flujo de agendamiento (telemetria, sin datos personales).
    'sesion' identifica una visita (anonima); 'paso' debe estar en _PASOS."""
    if paso not in _PASOS:
        return False
    try:
        ev = {'s': str(sesion)[:40], 'p': paso,
              'ts': datetime.now().isoformat(timespec='seconds')}
        if latency_ms is not None:
            ev['ms'] = max(0, int(latency_ms))
        EVENTOS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(EVENTOS_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        return True
    except Exception:
        return False


def resumen_funnel(desde=None, hasta=None):
    """Agrega los eventos en un embudo: cuantas sesiones llegan a cada paso, donde
    abandonan, y la latencia promedio de carga de horas. Filtra por fecha del evento."""
    eventos = _leer(EVENTOS_PATH)

    def en_rango(e):
        if not (desde or hasta):
            return True
        f = (e.get('ts') or '')[:10]
        if desde and f < desde.isoformat():
            return False
        if hasta and f > hasta.isoformat():
            return False
        return True

    eventos = [e for e in eventos if en_rango(e)]
    idx = {p: i for i, p in enumerate(_PASOS)}

    # Por sesion: el paso mas avanzado alcanzado + latencias de "horas".
    max_paso = {}
    latencias = []
    for e in eventos:
        s, p = e.get('s'), e.get('p')
        if not s or p not in idx:
            continue
        max_paso[s] = max(max_paso.get(s, -1), idx[p])
        if p == 'horas' and isinstance(e.get('ms'), int):
            latencias.append(e['ms'])

    total_sesiones = len(max_paso)
    # Embudo monotono: llegaron al paso i = sesiones cuyo max_paso >= i
    funnel = []
    base = None
    prev = None
    for i, p in enumerate(_PASOS):
        n = sum(1 for v in max_paso.values() if v >= i)
        if base is None:
            base = n or 1
        funnel.append({
            'paso': p, 'label': _PASOS_LABEL[p], 'sesiones': n,
            'pct_inicio': round(n / base * 100),
            'pct_anterior': round(n / prev * 100) if prev else 100,
        })
        prev = n if n else prev

    # Donde abandonan: ultimo paso alcanzado por sesion (los que NO reservaron)
    abandono = {}
    for v in max_paso.values():
        if v < idx['reservado']:
            p = _PASOS[v]
            abandono[_PASOS_LABEL[p]] = abandono.get(_PASOS_LABEL[p], 0) + 1
    abandono_list = sorted(({'label': k, 'total': v} for k, v in abandono.items()),
                           key=lambda x: -x['total'])

    latencias.sort()
    lat_prom = round(sum(latencias) / len(latencias)) if latencias else 0
    lat_med = latencias[len(latencias) // 2] if latencias else 0
    reservaron = sum(1 for v in max_paso.values() if v >= idx['reservado'])

    return {
        'total_sesiones': total_sesiones,
        'reservaron': reservaron,
        'conversion_pct': round(reservaron / total_sesiones * 100) if total_sesiones else 0,
        'funnel': funnel,
        'abandono': abandono_list,
        'latencia_horas_ms_prom': lat_prom,
        'latencia_horas_ms_mediana': lat_med,
        'latencia_muestras': len(latencias),
    }
