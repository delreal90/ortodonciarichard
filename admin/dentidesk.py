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
from datetime import date, datetime, timedelta

try:
    import requests
except ImportError:  # el bosquejo no rompe si requests no esta instalado
    requests = None

from scheduling import load_config, generar_grilla, _hash01, limpiar_rut


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


def horas_ocupadas(doc_id, target_date, motivo_key, cfg=None):
    """
    Devuelve el set de horas 'HH:MM' ocupadas para (doctor, fecha) respecto de la
    grilla teorica. ocupadas = grilla - disponibles_reales.

    Asi scheduling.py puede aplicar encima la ocupacion simulada (anti-agenda-vacia)
    usando una grilla estable como denominador.
    """
    cfg = cfg or load_config()
    motivo = cfg['motivos'][motivo_key]
    grilla = generar_grilla(cfg, motivo['duracion_min'])

    if not cfg['dentidesk']['enabled']:
        # MOCK: ~20-30% ocupacion real determinista por doctor+fecha+hora
        ocupadas = set()
        for h in grilla:
            if _hash01(doc_id, target_date.isoformat(), h, 'real') < 0.25:
                ocupadas.add(h)
        return ocupadas

    # REAL
    disponibles = horas_disponibles_dentidesk(cfg, doc_id, target_date, motivo)
    return set(grilla) - disponibles


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
        # MOCK: ~50% de los RUT "existen", determinista por RUT.
        existe = _hash01(limpio, 'paciente') < 0.5
        if not existe:
            return {'existe': False, 'datos': {}}
        nombres = ['Maria Jose', 'Juan Pablo', 'Camila', 'Ignacio', 'Valentina'][int(_hash01(limpio,'n')*5)]
        apellidos = ['Gonzalez Soto', 'Perez Rojas', 'Munoz Diaz', 'Vergara Lillo'][int(_hash01(limpio,'a')*4)]
        anio = 1965 + int(_hash01(limpio, 'y') * 45)
        mes = 1 + int(_hash01(limpio, 'm') * 12)
        dia = 1 + int(_hash01(limpio, 'd') * 27)
        movil = '+569' + str(10000000 + int(_hash01(limpio, 'tel') * 89999999))
        return {'existe': True, 'datos': {
            'nombres': nombres, 'apellidos': apellidos,
            'email': f'{limpio}@correo.cl',
            'fecha_nacimiento': f'{anio:04d}-{mes:02d}-{dia:02d}',
            'telefono_movil': movil,
        }}

    # REAL — DentiDesk no tiene endpoint de pacientes; el paciente ingresa sus datos.
    return {'existe': False, 'datos': {}}


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
