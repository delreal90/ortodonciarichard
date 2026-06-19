"""
confirmaciones.py - Confirmacion por email para citas creadas en DentiDesk
directamente (agendadas presencial o por telefono).

Barre getAgendaDay buscando citas con email y les envia el mismo correo de
confirmacion + .ics que reciben los pacientes que agendan online.

SEGURIDAD ANTI-SPAM: la PRIMERA corrida solo REGISTRA las citas existentes como
"ya avisadas" SIN enviar nada. Si no, al activar esto le llegaria un correo a los
cientos de pacientes que ya tienen hora. De ahi en adelante, solo se envia a las
citas NUEVAS (IdAgenda que no estaba registrado).

Las reservas ONLINE se registran al crearse (marcar_enviada) para no duplicar.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
except ImportError:
    requests = None

import dentidesk
import notify

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
ENVIADAS_PATH = Path(os.environ.get('CONFIRMACIONES_PATH',
                                    _BASE_DIR / 'confirmaciones_enviadas.json'))

_LOCK = threading.Lock()
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _fecha_legible(d):
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}'


def _load():
    """Devuelve el dict {IdAgenda: ts}, o None si NUNCA se ha corrido (1a vez)."""
    if ENVIADAS_PATH.exists():
        try:
            return json.loads(ENVIADAS_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return {}
    return None


def _save(idx):
    ENVIADAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENVIADAS_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, ENVIADAS_PATH)


def marcar_enviada(id_agenda):
    """Registra una cita como ya confirmada (p.ej. una reserva online recien
    creada) para que el barrido no le reenvie el correo."""
    if not id_agenda:
        return
    with _LOCK:
        idx = _load() or {}
        idx[str(id_agenda)] = datetime.now().isoformat(timespec='seconds')
        _save(idx)


def barrer_y_confirmar(cfg=None, dias_adelante=90, max_workers=10):
    """Escanea las citas de los proximos dias y envia confirmacion a las nuevas
    con email. La primera corrida solo siembra (no envia). Idempotente."""
    cfg = cfg or dentidesk.load_config()
    if not cfg['dentidesk']['enabled'] or requests is None:
        return {'ok': False, 'motivo': 'demo'}

    with _LOCK:
        idx = _load()
        primera_vez = idx is None
        idx = idx or {}

    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    hoy = date.today()
    dias = [hoy + timedelta(days=k) for k in range(0, dias_adelante + 1)
            if (hoy + timedelta(days=k)).weekday() < 5]

    def scan(d):
        try:
            token = dentidesk._auth_token(cfg)
            r = requests.post(url, json={'IdLocation': dd['id_location'],
                                         'Date': d.isoformat(), 'Token': token}, timeout=20)
            return (r.json() or {}).get('data', []) if r.status_code == 200 else []
        except Exception:
            return []

    citas = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for res in pool.map(scan, dias):
            citas.extend(res)

    import pacientes
    enviadas = 0
    nuevos = {}
    for c in citas:
        ida = str(c.get('IdAgenda') or '')
        if not ida or ida in idx or ida in nuevos:
            continue
        estado = (c.get('Status') or '').lower()
        if any(s in estado for s in dentidesk._ESTADOS_INACTIVOS):
            continue
        email = (c.get('PatientEmail') or '').strip()
        if '@' not in email:
            continue
        if primera_vez:
            nuevos[ida] = 'seed'          # solo registrar, NO enviar
            continue
        try:
            nombres, _ = pacientes._split_nombre(c.get('PatientName', ''))
            fch = datetime.strptime(c.get('Date', ''), '%Y-%m-%d').date()
            r = notify.enviar_confirmacion({
                'nombre': nombres or 'paciente',
                'telefono': (c.get('Phone') or '').strip(),
                'email': email, 'fecha': fch,
                'fecha_legible': _fecha_legible(fch),
                'hora': (c.get('time') or '')[:5],
                'doctor_nombre': (c.get('ProfessionalName') or '').strip(),
                'motivo_label': (c.get('Reason') or 'Cita').strip(),
                'dur_min': int(c.get('duration') or 30),
            }, cfg)
            if r.get('ok'):
                nuevos[ida] = datetime.now().isoformat(timespec='seconds')
                enviadas += 1
        except Exception:
            pass

    # Merge atomico: recargar lo ultimo (marcar_enviada pudo correr en paralelo).
    with _LOCK:
        actual = _load() or {}
        actual.update(nuevos)
        _save(actual)

    return {'ok': True, 'primera_vez': primera_vez, 'citas': len(citas),
            'enviadas': enviadas, 'registradas': len(actual)}
