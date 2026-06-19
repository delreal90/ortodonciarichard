"""
dentidesk.py - Cliente de la API de DentiDesk (Ortodoncia Richard)

Unico modulo que habla por red con DentiDesk. El resto del sistema lo usa
a traves de funciones limpias y NO ve los detalles de auth/JWT.

JWT de un solo uso: se autentica ANTES de cada request (el token expira al
primer uso). Las credenciales viven en scheduling_config.json (backend),
nunca en el frontend.

MODO MOCK: si config['dentidesk']['enabled'] es false, devuelve datos
simulados deterministas. Asi el bosquejo corre completo sin credenciales.
Cuando llegue el token: poner enabled=true y rellenar email/password.

Docs API: https://documentation-api-dd-...run.app/documentacion_api_dd_chile.php
  POST /api/users/authentication.php         -> token (un solo uso)
  POST /api/profesional/getAvailableHours.php
  POST /api/agenda/createAgenda.php
"""

import hashlib
from datetime import date, datetime, time, timedelta

try:
    import requests
except ImportError:  # el bosquejo no rompe si requests no esta instalado
    requests = None

from scheduling import load_config, generar_grilla, _hash01, limpiar_rut, grilla_horario_doctor


class DentiDeskError(Exception):
    pass


# ── Auth ─────────────────────────────────────────────────────────────────────

def _auth_token(cfg):
    """Obtiene un JWT de un solo uso. Se llama justo antes de cada request real."""
    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/users/authentication.php"
    resp = requests.post(url, json={'email': dd['email'], 'password': dd['password']}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    token = data.get('token') or data.get('Token')
    if not token:
        raise DentiDeskError(f'Auth sin token: {data}')
    return token


def _basic_auth(cfg):
    dd = cfg['dentidesk']
    if dd.get('basic_auth_user'):
        return (dd['basic_auth_user'], dd['basic_auth_pass'])
    return None


# ── Horas ocupadas reales ────────────────────────────────────────────────────

def horas_disponibles_dentidesk(cfg, doc_id, target_date, motivo):
    """
    Llama a getAvailableHours y devuelve el set de horas 'HH:MM' REALMENTE
    disponibles para (doctor, fecha, motivo) segun DentiDesk.

    Formato real de respuesta:
      200 -> {"message":"OK.","data":{"2026-06-23":["10:00","11:30",...]}}
      401 -> {"message":"Access denied.","description":"[API_DD] No existen
              horarios disponibles para este profesional en este dia."}
              => ese dia el profesional no tiene horas (NO es error de auth).
    """
    if requests is None:
        raise DentiDeskError("Falta 'requests' (pip install requests)")
    dd = cfg['dentidesk']
    doc_cfg = cfg['doctores'][doc_id]
    token = _auth_token(cfg)
    url = f"{dd['base_url'].rstrip('/')}/api/profesional/getAvailableHours.php"
    payload = {
        'IdLocation': dd['id_location'],
        'IdReason': motivo['id_reason'],
        'Professional': doc_cfg['professional_id'],
        'Date': target_date.isoformat(),
        'Token': token,
    }
    resp = requests.post(url, json=payload, auth=_basic_auth(cfg), timeout=20)

    if resp.status_code == 401:
        # Distinguir "sin horas ese dia" (normal) de un fallo real de credenciales
        try:
            desc = (resp.json() or {}).get('description', '')
        except ValueError:
            desc = resp.text
        if 'No existen horarios' in desc or 'horarios disponibles' in desc:
            return set()
        raise DentiDeskError(f'Auth/permiso rechazado por DentiDesk: {desc[:200]}')

    resp.raise_for_status()
    data = resp.json() or {}
    horas = set()
    for lista in (data.get('data') or {}).values():
        for h in lista:
            horas.add(h if isinstance(h, str) else h.get('Hour', ''))
    return horas


# Cache de getAgendaDay por fecha (una sola llamada cubre a todos los doctores).
_AGENDA_DIA_CACHE = {}
_AGENDA_DIA_TTL = 90  # segundos


def _get_agenda_day(cfg, target_date):
    """Lista de citas del dia (todos los profesionales). Cacheada."""
    import time as _t
    key = target_date.isoformat()
    hit = _AGENDA_DIA_CACHE.get(key)
    if hit and (_t.time() - hit[0]) < _AGENDA_DIA_TTL:
        return hit[1]
    dd = cfg['dentidesk']
    token = _auth_token(cfg)
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    resp = requests.post(url, json={'IdLocation': dd['id_location'],
                                    'Date': target_date.isoformat(), 'Token': token}, timeout=25)
    data = (resp.json() or {}).get('data', []) if resp.status_code == 200 else []
    _AGENDA_DIA_CACHE[key] = (_t.time(), data)
    return data


def _expandir_bloques(hhmmss, dur_min, paso=15):
    """'10:00:00' dur 30 -> ['10:00','10:15'] (bloques de 15 min)."""
    h, m = int(hhmmss[:2]), int(hhmmss[3:5])
    base = datetime.combine(date.today(), time(h, m))
    n = max(1, (int(dur_min) + paso - 1) // paso)
    return [(base + timedelta(minutes=paso * k)).strftime('%H:%M') for k in range(n)]


def bloques_ocupados(cfg, doc_id, target_date):
    """Bloques de 15 min realmente ocupados (citas existentes) del doctor ese dia."""
    nombre = cfg['doctores'][doc_id].get('professional_name', '')
    ocupados = set()
    for c in _get_agenda_day(cfg, target_date):
        if (c.get('ProfessionalName') or '').strip() != nombre:
            continue
        t = (c.get('time') or '')[:8]
        if len(t) < 5:
            continue
        ocupados.update(_expandir_bloques(t, c.get('duration') or 15))
    return ocupados


def disponibilidad_real(doc_id, target_date, motivo_key, cfg=None):
    """
    Devuelve (libres, ocupados) en bloques 'HH:MM' para (doctor, fecha):
      - libres   = getAvailableHours (horas que el paciente puede tomar)
      - ocupados = citas ya agendadas del doctor (getAgendaDay)
    La UNION libres+ocupados = capacidad REAL del doctor ese dia (su jornada real),
    que es el denominador correcto para la ocupacion aparente.
    """
    cfg = cfg or load_config()
    motivo = cfg['motivos'][motivo_key]

    if not cfg['dentidesk']['enabled']:
        # MOCK: jornada tipica (9-13 y 15-19) con ~25% ocupado determinista.
        manana = [f'{h:02d}:{m:02d}' for h in range(9, 13) for m in (0, 15, 30, 45)]
        tarde  = [f'{h:02d}:{m:02d}' for h in range(15, 19) for m in (0, 15, 30, 45)]
        worked = manana + tarde
        ocupados = {h for h in worked if _hash01(doc_id, target_date.isoformat(), h, 'real') < 0.25}
        libres = [h for h in worked if h not in ocupados]
        return set(libres), ocupados

    # REAL
    libres = horas_disponibles_dentidesk(cfg, doc_id, target_date, motivo)

    # Denominador de la ocupacion aparente: si el doctor tiene horario configurado
    # en el panel, lo usamos (grilla = jornada del dia) y NO consultamos getAgendaDay
    # (menos carga a DentiDesk, mas rapido). Si no hay horario configurado, caemos al
    # comportamiento anterior (getAgendaDay con las citas reales).
    grid = grilla_horario_doctor(cfg['doctores'].get(doc_id, {}), target_date, cfg)
    if grid is not None:
        ocupados = set(grid) - set(libres)
    else:
        ocupados = bloques_ocupados(cfg, doc_id, target_date)
    return set(libres), ocupados


# ── Buscar paciente por RUT ──────────────────────────────────────────────────

def buscar_paciente(rut, cfg=None):
    """
    Cruza el RUT con DentiDesk para saber si el paciente ya existe.

    Devuelve:
      {'existe': True,  'datos': {nombres, apellidos, email, fecha_nacimiento, telefono_movil}}
      {'existe': False, 'datos': {}}

    IMPORTANTE: la API de DentiDesk (diccionario 375) NO expone un endpoint de
    busqueda de paciente por RUT. Los unicos endpoints son: authentication,
    getAgendaDay, updateAgenda, getAgendaStatus, createAgenda, getAvailableHours.
    Por eso, en modo REAL siempre devolvemos existe=False (el paciente ingresa sus
    datos). El RUT igual se valida y se envia en createAgenda (RutPatient).

    Si la clinica habilita mas adelante un endpoint de pacientes, se ajusta SOLO
    la rama REAL de abajo; el contrato no cambia y el frontend/WhatsApp no se tocan.
    """
    cfg = cfg or load_config()
    limpio = limpiar_rut(rut)

    if not cfg['dentidesk']['enabled']:
        # MOCK: ~50% de los RUT "existen", determinista por RUT. Devuelve la misma
        # forma ENMASCARADA que el modo real (para probar la UI de reconocido).
        import pacientes
        existe = _hash01(limpio, 'paciente') < 0.5
        if not existe:
            return {'existe': False, 'datos': {}}
        nombres = ['Maria Jose', 'Juan Pablo', 'Camila', 'Ignacio', 'Valentina'][int(_hash01(limpio,'n')*5)]
        apellidos = ['Gonzalez Soto', 'Perez Rojas', 'Munoz Diaz', 'Vergara Lillo'][int(_hash01(limpio,'a')*4)]
        movil = '+569' + str(10000000 + int(_hash01(limpio, 'tel') * 89999999))
        rec = {'nombres': nombres, 'apellidos': apellidos,
               'email': f'{limpio}@correo.cl', 'telefono': movil}
        return {'existe': True, 'datos': pacientes.display(rec)}

    # REAL — DentiDesk no tiene endpoint de pacientes. Buscamos en nuestra base
    # local (construida por barrido de getAgendaDay). Devolvemos datos ENMASCARADOS
    # (el email/telefono reales no salen del backend).
    import pacientes
    rec = pacientes.lookup(limpio)
    if not rec:
        return {'existe': False, 'datos': {}}
    return {'existe': True, 'datos': pacientes.display(rec)}


# ── Citas futuras del paciente (para avisar de doble agendamiento) ────────────

# Estados que indican que la cita NO esta activa (no hay que avisar de ellas).
_ESTADOS_INACTIVOS = ('cancel', 'no llega', 'no seguir', 'reagend', 're-agend', 'atendid')


def citas_futuras_paciente(rut, cfg=None, dias_adelante=60, max_workers=12):
    """Busca las citas ACTIVAS futuras del paciente (por RUT) escaneando getAgendaDay
    en una ventana de dias. Devuelve lista [{fecha, hora, profesional, motivo, estado}].
    DentiDesk no tiene busqueda por paciente, por eso se barre dia a dia (en paralelo)."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg or load_config()
    if not cfg['dentidesk']['enabled']:
        return []
    objetivo = limpiar_rut(rut)
    if not objetivo:
        return []

    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    hoy = date.today()
    dias = [hoy + timedelta(days=k) for k in range(0, dias_adelante + 1)
            if (hoy + timedelta(days=k)).weekday() < 5]

    def scan(d):
        try:
            token = _auth_token(cfg)
            r = requests.post(url, json={'IdLocation': dd['id_location'],
                                         'Date': d.isoformat(), 'Token': token}, timeout=20)
            if r.status_code != 200:
                return []
            out = []
            for c in (r.json() or {}).get('data', []):
                if limpiar_rut(str(c.get('PatientDocument', ''))) != objetivo:
                    continue
                estado = (c.get('Status') or '').lower()
                if any(s in estado for s in _ESTADOS_INACTIVOS):
                    continue
                out.append({
                    'fecha':       c.get('Date', d.isoformat()),
                    'hora':        (c.get('time') or '')[:5],
                    'profesional': (c.get('ProfessionalName') or '').strip(),
                    'motivo':      (c.get('Reason') or '').strip(),
                    'estado':      (c.get('Status') or '').strip(),
                })
            return out
        except Exception:
            return []

    citas = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for res in pool.map(scan, dias):
            citas.extend(res)
    citas.sort(key=lambda c: (c['fecha'], c['hora']))
    return citas


# ── Crear cita ───────────────────────────────────────────────────────────────

def crear_cita(*, doc_id, motivo_key, target_date, hora,
               nombre, apellido, email, telefono, rut='', cfg=None):
    """Crea la cita en DentiDesk. Devuelve dict con resultado."""
    cfg = cfg or load_config()
    motivo = cfg['motivos'][motivo_key]
    doc_cfg = cfg['doctores'][doc_id]

    if not cfg['dentidesk']['enabled']:
        # MOCK: simula creacion exitosa con un id ficticio determinista
        fake_id = hashlib.sha256(
            f'{doc_id}{target_date}{hora}{telefono}'.encode()
        ).hexdigest()[:10]
        return {'ok': True, 'mock': True, 'id_cita': fake_id}

    if requests is None:
        raise DentiDeskError("Falta 'requests' (pip install requests)")
    dd = cfg['dentidesk']
    token = _auth_token(cfg)
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/createAgenda.php"
    payload = {
        'IdLocation': dd['id_location'],
        'IdStatus': dd['id_status_nueva_cita'],
        'IdReason': motivo['id_reason'],
        'Professional': doc_cfg['professional_id'],
        'NamePatient': nombre,
        'LastnamePatient': apellido,
        'EmailPatient': email,
        'RutPatient': rut,
        'PhonePatient': telefono,
        'Date': target_date.isoformat(),
        'Hour': hora,
        'Token': token,
    }
    resp = requests.post(url, json=payload, auth=_basic_auth(cfg), timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return {'ok': True, 'mock': False, 'raw': data,
            'id_cita': data.get('IdAgenda') or data.get('id')}
