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

# Por defecto, junto a la base de pacientes (mismo disco persistente).
_DEFAULT = Path(os.environ.get('PATIENT_INDEX_PATH',
                               Path(__file__).parent / 'patient_index.json')).parent / 'agendamientos.jsonl'
STATS_PATH = Path(os.environ.get('STATS_PATH', _DEFAULT))

_DIAS = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']


def registrar(evento):
    """Agrega un agendamiento al log. 'evento' es un dict; se completa con ts."""
    try:
        STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        evento = {**evento, 'ts': datetime.now().isoformat(timespec='seconds')}
        with open(STATS_PATH, 'a', encoding='utf-8') as f:
            f.write(json.dumps(evento, ensure_ascii=False) + '\n')
        return True
    except Exception:
        return False


def _leer():
    if not STATS_PATH.exists():
        return []
    out = []
    try:
        with open(STATS_PATH, encoding='utf-8') as f:
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
        por_hora[_hora_franja(e.get('hora', ''))] = por_hora.get(_hora_franja(e.get('hora', '')), 0) + 1
        # dia de semana de la cita
        try:
            f = datetime.strptime(e.get('fecha', ''), '%Y-%m-%d').date()
            por_dia_semana[_DIAS[f.weekday()]] += 1
        except (ValueError, IndexError):
            pass
        # cuando se hizo la reserva (fecha del ts)
        ts = (e.get('ts') or '')[:10]
        if ts:
            por_fecha_reserva[ts] = por_fecha_reserva.get(ts, 0) + 1
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
