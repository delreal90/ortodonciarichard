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
import logging
import unicodedata
from datetime import date, datetime, time, timedelta

try:
    import requests
except ImportError:  # el bosquejo no rompe si requests no esta instalado
    requests = None

from scheduling import load_config, generar_grilla, _hash01, limpiar_rut, grilla_horario_doctor

log = logging.getLogger(__name__)


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
    """DentiDesk exige BASIC AUTH ademas del Token JWT en updateAgenda (probado
    en vivo 2026-07-03: sin esto, 401 Unauthorized). Si no hay credenciales de
    Basic Auth separadas configuradas, usa las mismas email/password del login
    -- confirmado que funciona igual (son la misma cuenta)."""
    dd = cfg['dentidesk']
    if dd.get('basic_auth_user'):
        return (dd['basic_auth_user'], dd['basic_auth_pass'])
    return (dd['email'], dd['password'])


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
        # Distinguir "sin horas ese dia" (normal) de un fallo real de credenciales.
        # DentiDesk tambien responde 401 en feriados ("Fecha no disponible,
        # establecida como feriado") -- es un dia sin horas, no un error.
        try:
            desc = (resp.json() or {}).get('description', '')
        except ValueError:
            desc = resp.text
        if 'No existen horarios' in desc or 'horarios disponibles' in desc or 'feriado' in desc.lower():
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
_AGENDA_DIA_TTL = 600  # 10 min: la agenda del dia se comparte entre motivos del
# mismo doctor; con 90s expiraba mientras el paciente navegaba y el siguiente
# motivo volvia a frio. La reserva valida igual contra getAvailableHours en vivo.


def _get_agenda_day(cfg, target_date, force=False):
    """Lista de citas del dia (todos los profesionales). Cacheada.
    force=True ignora el cache y trae datos frescos de DentiDesk (lo usa el
    asistente F2: tras editar/guardar una cita el cache puede estar viejo)."""
    import time as _t
    key = target_date.isoformat()
    hit = _AGENDA_DIA_CACHE.get(key)
    if not force and hit and (_t.time() - hit[0]) < _AGENDA_DIA_TTL:
        return hit[1]
    dd = cfg['dentidesk']
    token = _auth_token(cfg)
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    resp = requests.post(url, json={'IdLocation': dd['id_location'],
                                    'Date': target_date.isoformat(), 'Token': token}, timeout=25)
    if resp.status_code != 200:
        log.warning('_get_agenda_day: DentiDesk respondio %s para %s (no se cachea)',
                    resp.status_code, key)
        return []
    try:
        data = (resp.json() or {}).get('data', [])
    except ValueError:
        log.warning('_get_agenda_day: respuesta 200 con JSON invalido para %s (no se cachea)', key)
        return []
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


# ── Sonda de slots libres por (doctor, dia) ─────────────────────────────────
# La navegacion de la agenda online usa UNA llamada getAvailableHours por
# doctor+dia (con el motivo mas corto de su especialidad como referencia) y
# deriva localmente que horas caben para cada motivo. La respuesta de DentiDesk
# ya descuenta citas, bloqueos, feriados, vacaciones y el horario real del
# doctor -- verificado en vivo el 2026-07-07: la derivacion local coincide
# exactamente con lo que getAvailableHours responde para motivos mas largos.
# (getAgendaDay NO sirve para esto: no trae bloqueos ni feriados.)

def _motivo_referencia(cfg, doc_id):
    """(key, duracion_min) del motivo mas corto de la especialidad del doctor."""
    esp = cfg['doctores'][doc_id].get('especialidad')
    cands = [(int(v.get('duracion_min', 15)), k) for k, v in cfg['motivos'].items()
             if not k.startswith('_') and isinstance(v, dict)
             and v.get('especialidad') == esp and v.get('id_reason')]
    if not cands:
        raise DentiDeskError(f'Sin motivo de referencia para el doctor {doc_id}')
    dur, key = min(cands)
    return key, dur


def _hhmm(minutos):
    return f'{minutos // 60:02d}:{minutos % 60:02d}'


def _a_min(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def bloques_libres_15(cfg, doc_id, target_date):
    """Set de bloques 'HH:MM' de 15 min realmente libres del doctor ese dia.
    Un inicio disponible para el motivo de referencia de duracion D implica
    que los D/15 bloques desde ahi estan libres."""
    key, dur = _motivo_referencia(cfg, doc_id)
    inicios = horas_disponibles_dentidesk(cfg, doc_id, target_date, cfg['motivos'][key])
    libres = set()
    for h in inicios:
        if len(h) < 5:
            continue
        m = _a_min(h)
        for t in range(m, m + dur, 15):
            libres.add(_hhmm(t))
    return libres


def horas_que_caben(libres15, duracion_min):
    """Horas de inicio donde cabe un motivo de 'duracion_min' minutos: todos
    sus bloques de 15 deben estar libres (regla validada contra DentiDesk)."""
    out = set()
    for h in libres15:
        m = _a_min(h)
        if all(_hhmm(t) in libres15 for t in range(m, m + int(duracion_min), 15)):
            out.add(h)
    return out


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

    # REAL. El denominador de la ocupacion aparente debe ser la JORNADA REAL del
    # doctor ese dia = horas libres + citas reales. getAgendaDay aporta las citas
    # reales; asi, en un dia sin reservas el denominador = horas libres reales (no
    # una grilla fija que puede ser mas ancha que su jornada), y el % se respeta
    # contra el dia real del doctor.
    libres = horas_disponibles_dentidesk(cfg, doc_id, target_date, motivo)
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


def citas_futuras_paciente(rut, cfg=None, dias_adelante=45, max_workers=6):
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
                    'id_agenda':   str(c.get('IdAgenda') or ''),
                    'fecha':       c.get('Date', d.isoformat()),
                    'hora':        (c.get('time') or '')[:5],
                    'profesional': (c.get('ProfessionalName') or '').strip(),
                    'motivo':      (c.get('Reason') or '').strip(),
                    'estado':      (c.get('Status') or '').strip(),
                })
            return out
        except Exception as e:
            # Loguear: un token vencido o un 500 de DentiDesk devolvia [] igual que
            # "ese dia no tiene citas". Las guardas que dependen de esto (ya_tiene_hora)
            # se caian del lado permisivo sin dejar rastro.
            log.warning('citas_futuras_paciente: fallo al leer la agenda del %s: %r', d, e)
            return []

    citas = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for res in pool.map(scan, dias):
            citas.extend(res)
    citas.sort(key=lambda c: (c['fecha'], c['hora']))
    return citas


# ── Crear cita ───────────────────────────────────────────────────────────────

def crear_cita(*, doc_id, motivo_key=None, id_reason=None, duracion_min=None,
               enviar_duracion=False, id_status=None, target_date, hora,
               nombre, apellido, email, telefono, rut='', cfg=None):
    """Crea la cita en DentiDesk. Devuelve dict con resultado.

    Dos formas de indicar el motivo:
      - motivo_key: uno de los motivos agendables online (cfg['motivos']) --
        uso normal del wizard de agendamiento.
      - id_reason: motivo RAW, sin pasar por la config -- lo usa el
        reagendamiento para preservar el motivo original de la cita vieja, que
        puede no estar en la lista de motivos online (ver id_reason_por_label).

    id_status (opcional): IdStatus con el que nace la cita. Default =
    id_status_nueva_cita (2120 'No confirmado'). El reagendamiento lo usa para
    crear la cita ya 'Confirmado por WhatsApp' (32180) cuando el paciente
    reagenda para el dia siguiente (viene interactuando por WhatsApp).

    enviar_duracion=True (solo reagendamiento): agrega el campo 'Duration' al
    payload para replicar una duracion ATIPICA de la cita original. El flujo
    normal lo deja en False -> payload identico al ya probado en vivo (no se
    arriesga a que un campo nuevo rompa createAgenda en el camino que funciona)."""
    cfg = cfg or load_config()
    doc_cfg = cfg['doctores'][doc_id]
    if motivo_key:
        motivo = cfg['motivos'][motivo_key]
        id_reason = id_reason or motivo['id_reason']
        duracion_min = duracion_min or motivo.get('duracion_min')
    if not id_reason:
        raise DentiDeskError('Falta id_reason (o motivo_key) para crear la cita')

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
        'IdStatus': id_status or dd['id_status_nueva_cita'],
        'IdReason': id_reason,
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
    # Duracion explicita (SOLO reagendamiento, enviar_duracion=True): el
    # paciente puede tener una cita con duracion ATIPICA (ej. "Instalar
    # Microtornillos" que normalmente es 30 min pero a este paciente se le
    # asigno 45). getAgendaDay la devuelve en 'duration'; para replicarla en la
    # cita nueva se manda aca. 'Duration' es el nombre de campo mas probable
    # (createAgenda usa PascalCase: Date, Hour, IdReason) pero NO esta
    # confirmado en vivo -- si DentiDesk lo ignora, la cita toma la duracion
    # standard de su IdReason (no rompe nada). PENDIENTE verificar contra una
    # cita de prueba real. El flujo normal NO manda este campo (enviar_duracion
    # default False) para no tocar el payload ya probado.
    if enviar_duracion and duracion_min:
        payload['Duration'] = int(duracion_min)
    resp = requests.post(url, json=payload, auth=_basic_auth(cfg), timeout=20)
    resp.raise_for_status()
    data = resp.json()
    return {'ok': True, 'mock': False, 'raw': data,
            'id_cita': data.get('IdAgenda') or data.get('id')}


# ── Actualizar estado de una cita existente ─────────────────────────────────

def actualizar_estado_cita(id_agenda, id_status, cfg=None):
    """Cambia el IdStatus de una cita existente (ej. 32180 'Confirmado por
    WhatsApp', 2122 'Hora Cancelada', 2132 'Re-agendado'). Usado por el webhook
    y el reagendamiento. Mismo patron que crear_cita(): auth de un solo uso +
    POST con Token.

    IMPORTANTE (verificado en vivo 2026-07-08): updateAgenda.php SOLO cambia el
    IdStatus. NO mueve la hora (campo Hour/Date ignorado) ni cambia la duracion
    (Duration/duration/Minutes ignorados) -- todos devuelven 200 OK pero solo
    el estado muta. Por eso no se puede 'mover' una cita a otro horario por la
    API; el unico lever es el estado."""
    cfg = cfg or load_config()
    if not cfg['dentidesk']['enabled']:
        return {'ok': True, 'mock': True}

    if requests is None:
        raise DentiDeskError("Falta 'requests' (pip install requests)")
    dd = cfg['dentidesk']
    token = _auth_token(cfg)
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/updateAgenda.php"
    payload = {
        'IdLocation': dd['id_location'],
        'IdAgenda': id_agenda,
        'IdStatus': id_status,
        'Token': token,
    }
    resp = requests.post(url, json=payload, auth=_basic_auth(cfg), timeout=20)
    resp.raise_for_status()
    return {'ok': True, 'mock': False, 'raw': resp.json()}


# ── Mapeo inverso nombre/label -> key interna ────────────────────────────────
# getAgendaDay devuelve el nombre del profesional (ProfessionalName) y el
# motivo (Reason) como TEXTO, no como las keys internas ('octavio',
# 'control_ortodoncico') que usa el frontend/scheduling_config.json. Estos
# helpers reconstruyen la key a partir del texto, para poder precargar
# doctor+motivo en el link de reagendar (ver server.py / recordatorios_wa.py).
# Si no hay match (nombre/label no coincide exacto) devuelven '' -- el
# frontend cae de vuelta a pedirselo al paciente, sin romper el flujo.

def sin_titulo_doctor(nombre):
    """Quita un prefijo 'Dr.'/'Dra.' del nombre de un profesional.

    Vive aca porque aca vive el ProfessionalName: la API de DentiDesk lo
    devuelve SIN titulo ('Alberto Del Real'), pero el modal de la cita --que es
    de donde lee el F2-- lo muestra CON titulo ('Dr. Alberto Del Real'). Los dos
    tienen que resolver al mismo doctor.
    """
    d = (nombre or '').strip()
    low = d.lower()
    for pref in ('dra.', 'dra ', 'dr.', 'dr '):
        if low.startswith(pref):
            return d[len(pref):].strip()
    return d


def doc_key_por_nombre(cfg, professional_name):
    professional_name = (professional_name or '').strip()
    if not professional_name:
        return ''
    # Se compara SIN titulo de los dos lados. El config guarda 'Alberto Del
    # Real' y el F2 manda 'Dr. Alberto Del Real': sin esto no calzaban, y el
    # informe salia sin firma aunque el doctor la tuviera cargada.
    objetivo = _norm_motivo(sin_titulo_doctor(professional_name))
    for k, v in cfg['doctores'].items():
        if (not k.startswith('_') and isinstance(v, dict)
                and _norm_motivo(sin_titulo_doctor(v.get('professional_name') or '')) == objetivo):
            return k
    log.warning('doc_key_por_nombre: sin match para professional_name=%r (normalizado=%r)',
                professional_name, objetivo)
    return ''


def doctor_de_paciente(rut, fecha_iso, cfg=None, dias_atras=30):
    """doctor_key del profesional que ATENDIÓ al paciente en `fecha_iso`; si ese día
    no tuvo cita, el del ÚLTIMO día con cita hacia atrás (hasta `dias_atras`). '' si
    no se encuentra o DentiDesk está deshabilitado.

    Lo usa el auto-envío de seguros: la boleta no dice el doctor, así que el
    formulario debe llevar al que atendió al paciente (o al último que lo vio si la
    boleta se emitió un día sin atención). Barre la agenda día por día DESDE la fecha
    hacia atrás y corta en el primer día con una cita del RUT — en el caso normal
    (boleta el mismo día de la atención) resuelve con UNA sola llamada."""
    cfg = cfg or load_config()
    if not cfg['dentidesk']['enabled']:
        return ''
    objetivo = limpiar_rut(rut)
    if not objetivo:
        return ''
    try:
        base = date.fromisoformat((fecha_iso or '')[:10])
    except ValueError:
        base = date.today()
    for k in range(0, dias_atras + 1):
        d = base - timedelta(days=k)
        if d.weekday() >= 5:                       # sáb/dom: la clínica no atiende
            continue
        try:
            citas = _get_agenda_day(cfg, d)
        except Exception as e:
            log.warning('doctor_de_paciente: fallo agenda del %s: %r', d, e)
            continue
        delp = [c for c in citas
                if limpiar_rut(str(c.get('PatientDocument', ''))) == objetivo]
        if not delp:
            continue
        # varias citas ese día → la más tardía (última atención del día)
        delp.sort(key=lambda c: (c.get('time') or ''), reverse=True)
        for c in delp:
            key = doc_key_por_nombre(cfg, c.get('ProfessionalName'))
            if key:
                return key
        # había cita(s) pero no se pudo resolver el doctor → seguir hacia atrás
    return ''


def _norm_motivo(texto):
    """Normaliza un nombre de motivo para comparar: minusculas, sin tildes,
    espacios colapsados."""
    s = unicodedata.normalize('NFD', (texto or '').strip().lower())
    s = ''.join(ch for ch in s if unicodedata.category(ch) != 'Mn')
    return ' '.join(s.split())


def es_primera_consulta(cfg, reason_label):
    """True si el motivo (texto 'Reason' que devuelve DentiDesk) corresponde a
    una PRIMERA CONSULTA -- esas reciben la plantilla de WhatsApp con video de
    bienvenida en vez de la confirmacion normal.

    Compara contra el label del motivo online 'primera_consulta' MAS las
    variantes que la clinica liste en cfg['motivos_primera_consulta'] (asi se
    pueden sumar nombres sin tocar codigo).

    El match es EXACTO (normalizado), NO 'contiene': en DentiDesk existen
    'Segunda Consulta' y 'Consulta Online', que NO son primera consulta."""
    objetivo = _norm_motivo(reason_label)
    if not objetivo:
        return False
    candidatos = set()
    m = (cfg.get('motivos') or {}).get('primera_consulta') or {}
    if m.get('label'):
        candidatos.add(_norm_motivo(m['label']))
    for extra in (cfg.get('motivos_primera_consulta') or []):
        candidatos.add(_norm_motivo(extra))
    return objetivo in candidatos


def id_reason_por_label(cfg, doc_key, label):
    """Resuelve el IdReason (numerico) del motivo ORIGINAL de una cita, a partir
    de su nombre tal como lo devuelve DentiDesk (campo 'Reason'). getAgendaDay
    NO trae el IdReason -- solo el texto -- asi que hay que reconstruirlo:

      1. Primero busca entre los motivos AGENDABLES online (cfg['motivos']),
         de la especialidad del doctor -- estos ya tienen id_reason confirmado.
      2. Si no hay match (motivo que la clinica escribio directo en DentiDesk,
         fuera del menu online, ej. "Instalacion de microtornillos"), busca en
         cfg['motivos_id_reason_extra'] -- tabla plana nombre->IdReason que la
         clinica entrega a mano (ver scheduling_config.json).

    Devuelve None si no hay match en ninguna -- quien llama debe manejarlo (no
    inventar un IdReason: se arriesga a agendar con el motivo equivocado)."""
    label = (label or '').strip()
    if not label:
        return None
    objetivo = _norm_motivo(label)

    esp = cfg['doctores'].get(doc_key, {}).get('especialidad')
    if esp:
        ids = set()
        for k, v in cfg['motivos'].items():
            if (not k.startswith('_') and isinstance(v, dict)
                    and v.get('especialidad') == esp
                    and v.get('id_reason')
                    and _norm_motivo(v.get('label') or '') == objetivo):
                ids.add(v['id_reason'])
        if ids:
            if len(ids) > 1:
                log.warning('id_reason_por_label: colision en cfg.motivos para label=%r '
                            '(normalizado=%r): IdReason distintos %s -- no se adivina',
                            label, objetivo, sorted(ids))
                return None
            return next(iter(ids))

    extra = cfg.get('motivos_id_reason_extra') or {}
    ids = set()
    for k, v in extra.items():
        if k.startswith('_'):
            continue  # ej. "_comment": no es un motivo
        if _norm_motivo(k) != objetivo:
            continue
        try:
            ids.add(int(v))
        except (TypeError, ValueError):
            log.warning('id_reason_por_label: motivos_id_reason_extra[%r]=%r no es un IdReason valido', k, v)
    if ids:
        if len(ids) > 1:
            log.warning('id_reason_por_label: colision en motivos_id_reason_extra para label=%r '
                        '(normalizado=%r): IdReason distintos %s -- no se adivina',
                        label, objetivo, sorted(ids))
            return None
        return next(iter(ids))

    log.warning('id_reason_por_label: sin match para label=%r (normalizado=%r) -- agregar '
                'este motivo a motivos_id_reason_extra en scheduling_config.json', label, objetivo)
    return None


def info_cita(cfg, id_agenda, fecha):
    """Busca una cita puntual por IdAgenda dentro de la agenda de UN dia
    (DentiDesk no tiene endpoint de 'buscar por id' -- solo getAgendaDay por
    fecha). El llamador debe conocer la fecha (la sabe desde que se armo el
    recordatorio de WhatsApp). Devuelve el registro crudo de DentiDesk o None
    si no esta ese dia (cita movida/eliminada por otra via)."""
    id_agenda = str(id_agenda)
    for c in _get_agenda_day(cfg, fecha, force=True):
        if str(c.get('IdAgenda') or '') == id_agenda:
            return c
    return None
