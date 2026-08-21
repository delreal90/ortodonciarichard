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
import fechas      # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

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


# ⚠️ default_si_falta=None: la diferencia entre "archivo vacio" y "nunca se ha
# corrido" es informacion de negocio. Si el archivo NO existe, la primera corrida
# solo SIEMBRA (registra lo que ya hay sin enviar) — si no, le llegaria el correo
# de confirmacion a cientos de pacientes que ya tenian hora.
# Escritura atomica + lock + respaldo si se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(ENVIADAS_PATH, default={}, default_si_falta=None)


def _load():
    """Devuelve el dict {IdAgenda: ts}, o None si NUNCA se ha corrido (1a vez)."""
    return _STORE.load()


# El registro guarda una entrada {IdAgenda: ts} por CADA cita confirmada, desde
# que existe el sistema, y se relee entero en cada uno de los 4 barridos diarios.
# Sin poda crece para siempre. 180 dias es holgado: la agenda online no acepta
# citas a mas de 60 dias (anticipacion_maxima_dias), asi que una entrada de hace
# medio año ya no puede corresponder a una cita futura sin confirmar.
# Mismo criterio que control_dental._DIAS_RETENCION_VISTOS / nps.
_DIAS_RETENCION = 180


def _podar(idx):
    """Saca las entradas mas viejas que _DIAS_RETENCION. Devuelve cuantas saco."""
    limite = (fechas.ahora_chile() - timedelta(days=_DIAS_RETENCION)).isoformat()
    viejas = [k for k, v in idx.items()
              if not k.startswith('_') and isinstance(v, str) and v < limite]
    for k in viejas:
        del idx[k]
    return len(viejas)


def _save(idx):
    _STORE.save(idx)


def marcar_enviada(id_agenda):
    """Registra una cita como ya confirmada (p.ej. una reserva online recien
    creada) para que el barrido no le reenvie el correo."""
    if not id_agenda:
        return
    with _LOCK:
        idx = _load() or {}
        idx[str(id_agenda)] = fechas.ahora_chile().isoformat(timespec='seconds')
        _save(idx)


def barrer_y_confirmar(cfg=None, dias_adelante=90, max_workers=10):
    """Escanea las citas de los proximos dias y envia confirmacion a las nuevas
    con email. La primera corrida solo siembra (no envia). Idempotente."""
    cfg = cfg or dentidesk.load_config()
    if not cfg['dentidesk']['enabled'] or requests is None:
        return {'ok': False, 'motivo': 'demo'}

    ahora = fechas.ahora_chile()
    with _LOCK:
        idx = _load()
        primera_vez = idx is None
        idx = idx or {}

    # Solo se ENVIA a citas creadas recientemente (desde el ultimo barrido, con 1
    # dia de margen). Asi, cuando la ventana de 90 dias avanza y aparece un dia
    # "nuevo" con citas viejas, esas NO se mailean: se adoptan en silencio.
    ultima_raw = idx.get('_ultima_corrida')
    try:
        cutoff = datetime.fromisoformat(ultima_raw) - timedelta(days=1) if ultima_raw else None
    except (ValueError, TypeError):
        cutoff = None
    if primera_vez:
        cutoff = None   # 1a corrida: no enviar nada, solo sembrar

    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    hoy = fechas.hoy_chile()
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

    def _parse_create(s):
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
            try:
                return datetime.strptime((s or '').strip()[:19], fmt)
            except ValueError:
                continue
        return None

    import pacientes
    enviadas = adoptadas = 0
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
        # ¿Enviar? Solo si la cita fue CREADA recientemente (no en la 1a corrida).
        creada = _parse_create(c.get('CreateDate'))
        enviar = (cutoff is not None) and (creada is not None) and (creada >= cutoff)
        if not enviar:
            nuevos[ida] = 'adopt'          # vieja o 1a corrida: registrar sin enviar
            adoptadas += 1
            continue
        try:
            nombres, _ = pacientes._split_nombre(c.get('PatientName', ''))
            fch = datetime.strptime(c.get('Date', ''), '%Y-%m-%d').date()
            # Primera consulta (agendada por telefono/presencial): plantilla de
            # WhatsApp con video de bienvenida + email. canal 'ambos' porque con
            # el automatico (email primero) el WhatsApp nunca saldria.
            es_primera = dentidesk.es_primera_consulta(cfg, c.get('Reason'))
            r = notify.enviar_confirmacion({
                'nombre': nombres or 'paciente',
                'telefono': (c.get('Phone') or '').strip(),
                'email': email, 'fecha': fch,
                'fecha_legible': _fecha_legible(fch),
                'hora': (c.get('time') or '')[:5],
                'doctor_nombre': (c.get('ProfessionalName') or '').strip(),
                'motivo_label': (c.get('Reason') or 'Cita').strip(),
                'dur_min': int(c.get('duration') or 30),
                'id_agenda': ida,
                'rut': (c.get('PatientDocument') or '').strip(),
            }, cfg, canal=('ambos' if es_primera else None), primera=es_primera)
            if r.get('ok'):
                nuevos[ida] = fechas.ahora_chile().isoformat(timespec='seconds')
                enviadas += 1
            # si falla el envio, NO se registra -> se reintenta en el proximo barrido
        except Exception as e:
            # Loguear: sin esto, un envio que falla cada vez (mail invalido, SMTP
            # caido) es indistinguible de "no habia nada que confirmar" y se
            # reintenta en silencio 4 veces al dia para siempre.
            print(f'[confirmaciones] fallo el envio de la cita {ida}: {e!r}')

    # Merge atomico: recargar lo ultimo (marcar_enviada pudo correr en paralelo).
    with _LOCK:
        actual = _load() or {}
        actual.update(nuevos)
        actual['_ultima_corrida'] = ahora.isoformat(timespec='seconds')
        podadas = _podar(actual)
        _save(actual)
    if podadas:
        print(f'[confirmaciones] podadas {podadas} entradas de mas de '
              f'{_DIAS_RETENCION} dias')

    return {'ok': True, 'primera_vez': primera_vez, 'citas': len(citas),
            'enviadas': enviadas, 'adoptadas': adoptadas, 'podadas': podadas,
            'registradas': len([k for k in actual if not k.startswith('_')])}
