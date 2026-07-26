"""
recordatorios_wa.py - Recordatorios automaticos por WhatsApp (Ortodoncia Richard)

Tres avisos, cada uno con su propio toggle activo/inactivo y hora de envio
(configurables desde el panel admin, pestania "WhatsApp"):
  - recordatorio_semana:    cita en 4 dias habiles (lunes-viernes; ignora feriados)
  - recordatorio_dia:       cita en el proximo dia habil (salta fin de semana;
                             ignora feriados)
  - inasistencia_reagendar: citas marcadas "no llega" en DentiDesk (ayer/hoy)

Los recordatorios semana/dia solo se envian en dias habiles (lunes-viernes):
si el loop cae en fin de semana, no mandan nada (la clinica no atiende y el
registro anti-duplicados evitaria reenvios de todos modos).

Corre desde _loop_recordatorios() en server.py, a la hora que cada tipo tenga
configurada. Registro anti-duplicados propio (no reusa el de confirmaciones.py,
son avisos distintos). Config + registro viven en el disco persistente de
Render (misma base que confirmaciones_enviadas.json / patient_index.json,
via PATIENT_INDEX_PATH) para sobrevivir a los redeploys sin pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime, timedelta

import dentidesk
import notify
import scheduling
import wa_cloud
import fechas   # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('WA_RECORDATORIOS_CONFIG_PATH', _BASE_DIR / 'wa_recordatorios_config.json'))
ENVIADOS_PATH = Path(os.environ.get('WA_RECORDATORIOS_ENVIADOS_PATH', _BASE_DIR / 'wa_recordatorios_enviados.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    # Los 3 arrancan APAGADOS a proposito: el primer deploy no debe mandar
    # nada solo. Se activan a mano desde el panel (pestania WhatsApp) cuando
    # el usuario este listo.
    'recordatorio_semana':    {'activo': False, 'hora': '09:00'},
    'recordatorio_dia':       {'activo': False, 'hora': '09:00'},
    'inasistencia_reagendar': {'activo': False, 'hora': '12:00'},
}

_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def _fecha_legible(d):
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}'


# ── Config (activo/hora por tipo) ───────────────────────────────────────────

def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # copia profunda
    for k in ('recordatorio_semana', 'recordatorio_dia', 'inasistencia_reagendar'):
        if isinstance(data.get(k), dict):
            if 'activo' in data[k]:
                cfg[k]['activo'] = bool(data[k]['activo'])
            hora = str(data[k].get('hora', '')).strip()
            if len(hora) == 5 and hora[2] == ':':
                cfg[k]['hora'] = hora
    return cfg


def save_config(updates):
    """Actualiza solo los campos recibidos (activo/hora por tipo); preserva el
    resto -- mismo criterio que server.set_scheduling_config()."""
    with _LOCK:
        cfg = load_config()
        for k in ('recordatorio_semana', 'recordatorio_dia', 'inasistencia_reagendar'):
            cambios = updates.get(k)
            if not isinstance(cambios, dict):
                continue
            if 'activo' in cambios:
                cfg[k]['activo'] = bool(cambios['activo'])
            if 'hora' in cambios:
                hora = str(cambios['hora']).strip()
                if len(hora) == 5 and hora[2] == ':':
                    cfg[k]['hora'] = hora
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro anti-duplicados ─────────────────────────────────────────────────

def _load_registro():
    if ENVIADOS_PATH.exists():
        try:
            reg = json.loads(ENVIADOS_PATH.read_text(encoding='utf-8'))
            if isinstance(reg, dict):
                return reg
        except (ValueError, OSError):
            pass
    return {'semana': {}, 'dia': {}, 'inasistencia': {}}


def _save_registro(reg):
    ENVIADOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENVIADOS_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(reg, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, ENVIADOS_PATH)


# Igual que confirmaciones.py: una entrada {IdAgenda: ts} por cada recordatorio
# enviado, para siempre, releida en cada barrido. 180 dias es holgado (la agenda
# no acepta citas a mas de 60), y una cita de hace medio año ya no necesita
# recordatorio. Mismo criterio que control_dental / nps.
_DIAS_RETENCION = 180


def _podar(reg):
    """Saca de cada tipo las entradas mas viejas que _DIAS_RETENCION."""
    limite = (fechas.ahora_chile() - timedelta(days=_DIAS_RETENCION)).isoformat()
    quitadas = 0
    for tipo in ('semana', 'dia', 'inasistencia'):
        d = reg.get(tipo)
        if not isinstance(d, dict):
            continue
        viejas = [k for k, v in d.items() if isinstance(v, str) and v < limite]
        for k in viejas:
            del d[k]
        quitadas += len(viejas)
    return quitadas


def _marcar(tipo, id_agenda):
    with _LOCK:
        reg = _load_registro()
        reg.setdefault(tipo, {})[str(id_agenda)] = fechas.ahora_chile().isoformat(timespec='seconds')
        podadas = _podar(reg)
        _save_registro(reg)
    if podadas:
        print(f'[recordatorios] podadas {podadas} entradas de mas de '
              f'{_DIAS_RETENCION} dias')


def ultimo_envio(tipo):
    ts = list((_load_registro().get(tipo) or {}).values())
    return max(ts) if ts else None


# ── Escaneo y envio ──────────────────────────────────────────────────────────

def _procesar_dia(cfg, target_date, tipo, fn_envio, incluir_doctor):
    """Escanea las citas de un dia puntual y envia fn_envio() a las que
    correspondan (tienen telefono, no estan canceladas/atendidas, no se les
    envio antes este mismo tipo de aviso)."""
    try:
        citas = dentidesk._get_agenda_day(cfg, target_date)
    except Exception as e:
        return {'ok': False, 'error': str(e)}

    reg = _load_registro().get(tipo, {})
    import pacientes as _pac
    enviadas = 0
    for c in citas:
        ida = str(c.get('IdAgenda') or '')
        if not ida or ida in reg:
            continue
        estado_txt = (c.get('Status') or '').lower()
        if any(s in estado_txt for s in dentidesk._ESTADOS_INACTIVOS):
            continue
        telefono = (c.get('Phone') or '').strip()
        if not telefono:
            continue
        nombres, _ = _pac._split_nombre(c.get('PatientName', ''))
        cita = {
            'nombre': nombres or 'paciente',
            'telefono': telefono,
            'fecha_legible': _fecha_legible(target_date),
            'fecha': target_date.isoformat(),
            'hora': (c.get('time') or '')[:5],
            'id_agenda': ida,
        }
        if incluir_doctor:
            cita['doctor_nombre'] = (c.get('ProfessionalName') or '').strip()
        r = fn_envio(cita)
        if r.get('ok'):
            _marcar(tipo, ida)
            enviadas += 1
    return {'ok': True, 'enviadas': enviadas, 'citas': len(citas)}


def enviar_recordatorios_semana(cfg, hoy=None):
    """Cita en 4 dias habiles (lunes-viernes, ignora feriados) -> recordatorio_semana.
    Solo envia si HOY es dia habil (si cae fin de semana, no manda)."""
    hoy = hoy or fechas.hoy_chile()
    if hoy.isoweekday() >= 6:
        return {'ok': True, 'enviadas': 0, 'citas': 0, 'omitido': 'fin de semana'}
    target = scheduling.sumar_dias_habiles(hoy, 4)
    return _procesar_dia(cfg, target, 'semana', notify.enviar_recordatorio_semana, incluir_doctor=True)


def enviar_recordatorios_dia(cfg, hoy=None):
    """Proximo dia habil (salta fin de semana; ignora feriados) -> recordatorio_dia.
    Solo envia si HOY es dia habil (si cae fin de semana, no manda)."""
    hoy = hoy or fechas.hoy_chile()
    if hoy.isoweekday() >= 6:
        return {'ok': True, 'enviadas': 0, 'citas': 0, 'omitido': 'fin de semana'}
    target = scheduling.siguiente_dia_habil(hoy + timedelta(days=1))
    return _procesar_dia(cfg, target, 'dia', notify.enviar_recordatorio_dia, incluir_doctor=True)


def enviar_inasistencias(cfg, hoy=None):
    """Barre ayer y hoy buscando citas marcadas 'no llega' -> inasistencia_reagendar."""
    hoy = hoy or fechas.hoy_chile()
    enviadas = revisadas = 0
    reg = _load_registro().get('inasistencia', {})
    import pacientes as _pac
    for target in (hoy - timedelta(days=1), hoy):
        try:
            citas = dentidesk._get_agenda_day(cfg, target)
        except Exception as e:
            print(f'[recordatorios] no se pudo leer la agenda del {target}: {e!r}')
            continue
        for c in citas:
            ida = str(c.get('IdAgenda') or '')
            if not ida or ida in reg:
                continue
            estado_txt = (c.get('Status') or '').lower()
            if 'no llega' not in estado_txt:
                continue
            revisadas += 1
            telefono = (c.get('Phone') or '').strip()
            if not telefono:
                continue
            nombres, _ = _pac._split_nombre(c.get('PatientName', ''))
            r = notify.enviar_inasistencia({
                'nombre': nombres or 'paciente', 'telefono': telefono,
                'fecha_legible': _fecha_legible(target), 'fecha': target.isoformat(),
                'id_agenda': ida,
            })
            if r.get('ok'):
                _marcar('inasistencia', ida)
                enviadas += 1
    return {'ok': True, 'enviadas': enviadas, 'revisadas': revisadas}


def estado():
    """Para el indicador del panel: chequeo en vivo contra Meta + ultimos envios."""
    est = wa_cloud.verificar_estado()
    est['ultimo_envio_semana'] = ultimo_envio('semana')
    est['ultimo_envio_dia'] = ultimo_envio('dia')
    est['ultimo_envio_inasistencia'] = ultimo_envio('inasistencia')
    return est
