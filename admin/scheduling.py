"""
scheduling.py - Logica central de agendamiento (Ortodoncia Richard)

DISENIO: este modulo NO sabe nada de Flask, de DentiDesk ni del frontend.
Solo recibe datos (slots reales disponibles desde la fuente que sea) y aplica:
  - reglas de negocio (anticipacion minima / urgencias)
  - simulacion de ocupacion minima aparente (anti-agenda-vacia), determinista
  - generacion de la grilla horaria por jornada (AM/PM)
  - generacion del archivo .ics

Asi el mismo cerebro se reutiliza desde:
  - el sitio web (via admin/server.py -> dentidesk.py)
  - el futuro bot de WhatsApp (via el mismo server.py o un worker)

Nada de I/O de red aqui. dentidesk.py es quien habla con la API.
"""

import os
import json
import hashlib
from pathlib import Path
from datetime import datetime, date, time, timedelta

CONFIG_PATH  = Path(__file__).parent / 'scheduling_config.json'
SECRETS_PATH = Path(__file__).parent / 'scheduling_secrets.json'  # gitignored, solo local


# ── Config ──────────────────────────────────────────────────────────────────

def load_config():
    """Carga el config público y le superpone las credenciales (que NUNCA se
    commitean). Prioridad: variables de entorno (Render) > archivo local
    scheduling_secrets.json (desarrollo) > config (vacío por defecto = mock)."""
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    dd = cfg['dentidesk']

    # 1) Archivo local de secretos (gitignored) — para probar en vivo desde el PC
    if SECRETS_PATH.exists():
        try:
            sec = json.loads(SECRETS_PATH.read_text(encoding='utf-8'))
            dd.update({k: v for k, v in sec.items() if v not in (None, '')})
        except (ValueError, OSError):
            pass

    # 2) Variables de entorno (producción en Render) — máxima prioridad
    env_map = {
        'email':           'DENTIDESK_EMAIL',
        'password':        'DENTIDESK_PASSWORD',
        'basic_auth_user': 'DENTIDESK_BASIC_USER',
        'basic_auth_pass': 'DENTIDESK_BASIC_PASS',
    }
    for key, env in env_map.items():
        if os.environ.get(env):
            dd[key] = os.environ[env]
    if os.environ.get('DENTIDESK_ENABLED'):
        dd['enabled'] = os.environ['DENTIDESK_ENABLED'].strip().lower() in ('1', 'true', 'yes', 'on')

    return cfg


def save_config(cfg):
    """Guarda el config público SIN credenciales (las credenciales viven solo en
    variables de entorno o en scheduling_secrets.json, nunca en el archivo
    versionado)."""
    import copy
    out = copy.deepcopy(cfg)
    dd = out.get('dentidesk', {})
    for secret in ('email', 'password', 'basic_auth_user', 'basic_auth_pass'):
        dd[secret] = ''
    # 'enabled' real puede venir de env; en el archivo lo dejamos en false por seguridad
    dd['enabled'] = False
    CONFIG_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')


# ── Grilla horaria ───────────────────────────────────────────────────────────

def _parse_hhmm(s):
    h, m = s.split(':')
    return time(int(h), int(m))


def generar_grilla(cfg, dur_min):
    """Lista de horas 'HH:MM' de la jornada completa, en pasos de slot_minutos,
    dejando espacio para la duracion de la cita antes del cierre."""
    h = cfg['horario']
    apertura = _parse_hhmm(h['apertura'])
    cierre = _parse_hhmm(h['cierre'])
    paso = timedelta(minutes=h['slot_minutos'])
    dur = timedelta(minutes=dur_min)

    slots = []
    cursor = datetime.combine(date.today(), apertura)
    fin = datetime.combine(date.today(), cierre)
    while cursor + dur <= fin:
        slots.append(cursor.strftime('%H:%M'))
        cursor += paso
    return slots


def jornada_de(hhmm, cfg):
    """'AM' o 'PM' segun corte_pm."""
    corte = _parse_hhmm(cfg['horario']['corte_pm'])
    return 'AM' if _parse_hhmm(hhmm) < corte else 'PM'


# ── Dias habiles / bandas temporales ─────────────────────────────────────────

def es_habil(d, cfg):
    # isoweekday(): lunes=1 ... domingo=7
    return d.isoweekday() in cfg['horario']['dias_habiles']


def dias_habiles_desde(d0, cantidad, cfg):
    """Lista de los proximos 'cantidad' dias habiles a partir de d0 (incluido si es habil)."""
    out = []
    d = d0
    while len(out) < cantidad:
        if es_habil(d, cfg):
            out.append(d)
        d += timedelta(days=1)
    return out


# Franjas temporales por dias de CALENDARIO desde hoy (inclusive).
_BANDAS = [
    ('dia_0_5',    0,  5),
    ('dia_6_10',   6, 10),
    ('dia_11_20', 11, 20),
    ('dia_21_30', 21, 30),
    ('dia_31_60', 31, 60),
]

def banda_temporal(target_date, hoy, cfg):
    """Clasifica una fecha en una franja segun cuantos dias de calendario la
    separan de hoy. Devuelve la key de config o None (fuera de las franjas)."""
    diff = (target_date - hoy).days
    for key, lo, hi in _BANDAS:
        if lo <= diff <= hi:
            return key
    return None


def dentro_de_ventana(target_date, cfg, hoy=None):
    """True si la fecha esta dentro de la ventana agendable (0..max dias)."""
    hoy = hoy or date.today()
    diff = (target_date - hoy).days
    maxd = cfg['reglas'].get('anticipacion_maxima_dias', 60)
    return 0 <= diff <= maxd


def dias_habiles_ventana(hoy, cfg):
    """Dias habiles dentro de la ventana de anticipacion maxima (calendario)."""
    maxd = cfg['reglas'].get('anticipacion_maxima_dias', 60)
    return [hoy + timedelta(days=k) for k in range(0, maxd + 1)
            if es_habil(hoy + timedelta(days=k), cfg)]


# ── Simulacion de ocupacion (determinista) ───────────────────────────────────

def _hash01(*parts):
    """Pseudo-aleatorio determinista en [0,1) a partir de strings.
    Mismo input -> mismo output siempre (consistencia entre dias/sesiones)."""
    raw = '|'.join(str(p) for p in parts).encode('utf-8')
    h = hashlib.sha256(raw).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF




def aplicar_ocupacion_simulada(doc_id, target_date, slots_grilla, ocupados_reales, cfg, hoy=None):
    """
    Decide que slots se muestran como DISPONIBLES al paciente.

    Entradas:
      - slots_grilla: todas las horas posibles de la jornada (['09:00', ...])
      - ocupados_reales: set de horas realmente ocupadas (desde DentiDesk)

    Logica por jornada (AM y PM por separado):
      1. ocupacion_real = ocupados_reales / total
      2. si ocupacion_real >= objetivo -> no se simula nada
      3. si esta mas vacia que el objetivo -> se marcan como "ocupado simulado"
         los slots libres con menor hash, hasta alcanzar el objetivo.

    Determinismo: el hash depende de (doctor, fecha, hora) -> un paciente que
    entra hoy y manana ve EXACTAMENTE los mismos bloques simulados.
    Un slot simulado-ocupado NUNCA se devuelve como disponible.
    """
    hoy = hoy or date.today()
    doc_cfg = cfg['doctores'].get(doc_id, {})
    banda_key = banda_temporal(target_date, hoy, cfg)

    disponibles = []
    # procesar AM y PM por separado (la regla de ocupacion es por jornada)
    for jornada in ('AM', 'PM'):
        de_jornada = [s for s in slots_grilla if jornada_de(s, cfg) == jornada]
        if not de_jornada:
            continue

        libres = [s for s in de_jornada if s not in ocupados_reales]
        ocupados_count = len(de_jornada) - len(libres)

        # objetivo solo aplica dentro de las franjas; fuera de eso no se simula.
        # Cada franja tiene UN porcentaje; el sistema busca la cantidad de horas
        # que mejor se acomode a ese porcentaje (redondeo al entero mas cercano).
        objetivo_frac = 0.0
        val = doc_cfg.get('ocupacion', {}).get(banda_key) if banda_key else None
        if isinstance(val, (int, float)):
            objetivo_frac = float(val) / 100.0

        objetivo_ocupados = int(round(objetivo_frac * len(de_jornada)))
        faltan = max(0, objetivo_ocupados - ocupados_count)

        # elegir cuales libres ocultar: los de menor hash (determinista)
        libres_ordenados = sorted(libres, key=lambda s: _hash01(doc_id, target_date.isoformat(), s))
        simulados = set(libres_ordenados[:faltan])

        for s in de_jornada:
            if s not in ocupados_reales and s not in simulados:
                disponibles.append(s)

    return sorted(disponibles)


# ── Reglas de negocio ────────────────────────────────────────────────────────

def cumple_anticipacion(target_date, hhmm, motivo_cfg, cfg, ahora=None):
    """True si la hora respeta la anticipacion minima (o es urgencia)."""
    if motivo_cfg.get('urgencia'):
        return True
    ahora = ahora or datetime.now()
    inicio = datetime.combine(target_date, _parse_hhmm(hhmm))
    min_horas = cfg['reglas']['anticipacion_minima_horas']
    return inicio - ahora >= timedelta(hours=min_horas)


def horas_disponibles(doc_id, target_date, motivo_key, libres, ocupados, cfg, ahora=None):
    """
    Punto de entrada principal: devuelve la lista final de horas que el paciente
    puede elegir para (doctor, fecha, motivo).

    'libres'   = horas realmente disponibles en DentiDesk.
    'ocupados' = bloques con citas ya agendadas del doctor.
    La UNION (libres+ocupados) = jornada REAL del doctor ese dia = denominador
    correcto para la ocupacion aparente (no una grilla fija).

    Flujo: capacidad real -> ocupacion simulada (anti-vacia) -> filtro anticipacion.
    """
    ahora = ahora or datetime.now()
    motivo_cfg = cfg['motivos'][motivo_key]
    worked = sorted(set(libres) | set(ocupados))   # capacidad real del dia

    disponibles = aplicar_ocupacion_simulada(
        doc_id, target_date, worked, set(ocupados), cfg, hoy=ahora.date()
    )
    return [h for h in disponibles
            if cumple_anticipacion(target_date, h, motivo_cfg, cfg, ahora)]


# ── RUT chileno ──────────────────────────────────────────────────────────────

def limpiar_rut(rut):
    """Deja solo digitos + DV (K). Ej: '12.345.678-5' -> '123456785'."""
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def dv_rut(cuerpo):
    """Calcula el digito verificador (modulo 11) de un cuerpo numerico."""
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    return {10: 'K', 11: '0'}.get(resto, str(resto))


def rut_valido(rut):
    """True si el RUT (con o sin formato) tiene DV correcto."""
    limpio = limpiar_rut(rut)
    if len(limpio) < 2:
        return False
    cuerpo, dv = limpio[:-1], limpio[-1]
    if not cuerpo.isdigit():
        return False
    return dv_rut(cuerpo) == dv


def formatear_rut(rut):
    """'123456785' -> '12.345.678-5'."""
    limpio = limpiar_rut(rut)
    if len(limpio) < 2:
        return rut
    cuerpo, dv = limpio[:-1], limpio[-1]
    miles = f'{int(cuerpo):,}'.replace(',', '.')
    return f'{miles}-{dv}'


# ── Generacion de .ics ───────────────────────────────────────────────────────

def generar_ics(*, titulo, fecha, hora, dur_min, doctor_nombre, direccion, descripcion=''):
    """Devuelve el contenido de un archivo .ics (string)."""
    inicio = datetime.combine(fecha, _parse_hhmm(hora))
    fin = inicio + timedelta(minutes=dur_min)
    fmt = '%Y%m%dT%H%M%S'
    uid = hashlib.sha256(f'{fecha}{hora}{doctor_nombre}'.encode()).hexdigest()[:16]
    stamp = datetime.now().strftime(fmt)

    def esc(s):
        return s.replace('\\', '\\\\').replace(',', '\\,').replace(';', '\\;').replace('\n', '\\n')

    return '\r\n'.join([
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Ortodoncia Richard//Agenda//ES',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        f'UID:{uid}@ortodonciarichard.cl',
        f'DTSTAMP:{stamp}',
        f'DTSTART:{inicio.strftime(fmt)}',
        f'DTEND:{fin.strftime(fmt)}',
        f'SUMMARY:{esc(titulo)}',
        f'DESCRIPTION:{esc(descripcion or f"Cita con {doctor_nombre}")}',
        f'LOCATION:{esc(direccion)}',
        'BEGIN:VALARM',
        'TRIGGER:-PT2H',
        'ACTION:DISPLAY',
        'DESCRIPTION:Recordatorio cita Ortodoncia Richard',
        'END:VALARM',
        'END:VEVENT',
        'END:VCALENDAR',
        '',
    ])
