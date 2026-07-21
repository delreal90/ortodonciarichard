"""
recaptacion.py - Recordatorio de control (recaptacion de pacientes que dejaron
de venir), disparado a mano desde el asistente F2 (Ortodoncia Richard).

A diferencia de recordatorios_wa.py (que escanea la agenda sola, por horario),
aca no hay escaneo: la secretaria abre en DentiDesk la ULTIMA cita del
paciente, aprieta F2 y decide mandar el WhatsApp. El backend solo evalua si
corresponde (no tiene ya una hora agendada, no se le mando hace poco, no esta
en la lista de "no molestar") y lleva el registro anti-duplicados.

Config + registro propios (no reusan los de recordatorios_wa.py, son avisos
distintos), en el mismo disco persistente de Render (misma base que
patient_index.json / confirmaciones_enviadas.json, via PATIENT_INDEX_PATH)
para sobrevivir a los redeploys sin pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime, timedelta

import dentidesk

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('RECAPTACION_CONFIG_PATH', _BASE_DIR / 'recaptacion_config.json'))
REGISTRO_PATH = Path(os.environ.get('RECAPTACION_REGISTRO_PATH', _BASE_DIR / 'recaptacion_registro.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    'dias_minimos_reenvio': 90,
}

# Copiadas de recordatorios_wa.py (NO importar de alla): fecha_legible_larga
# necesita el anio (recordatorios_wa._fecha_legible no lo lleva, es para
# citas de la semana/dia siguiente donde el anio es obvio; aca el control
# puede recaer meses o anios despues, asi que el anio es necesario).
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def fecha_legible_larga(d):
    """'martes 1 de abril del 2025' -- version CON anio de _fecha_legible."""
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} del {d.year}'


# ── Config ────────────────────────────────────────────────────────────────

def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # copia profunda
    if isinstance(data, dict) and 'dias_minimos_reenvio' in data:
        try:
            dias = int(data['dias_minimos_reenvio'])
            if dias > 0:
                cfg['dias_minimos_reenvio'] = dias
        except (TypeError, ValueError):
            pass
    return cfg


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo
    criterio que recordatorios_wa.save_config()."""
    with _LOCK:
        cfg = load_config()
        if isinstance(updates, dict) and 'dias_minimos_reenvio' in updates:
            try:
                dias = int(updates['dias_minimos_reenvio'])
                if dias > 0:
                    cfg['dias_minimos_reenvio'] = dias
            except (TypeError, ValueError):
                pass
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro (envios + no_molestar) ──────────────────────────────────────────

def _load_registro():
    if REGISTRO_PATH.exists():
        try:
            reg = json.loads(REGISTRO_PATH.read_text(encoding='utf-8'))
            if isinstance(reg, dict):
                reg.setdefault('envios', {})
                reg.setdefault('no_molestar', [])
                return reg
        except (ValueError, OSError):
            pass
    return {'envios': {}, 'no_molestar': []}


def _save_registro(reg):
    REGISTRO_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRO_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, REGISTRO_PATH)


def _rut_key(rut):
    """Normaliza para usar como clave del dict de envios -- distintos formatos
    del mismo RUT (con/sin puntos, con/sin guion) deben caer en la misma
    entrada. Usa el limpiador de dentidesk (mismo criterio que
    citas_futuras_paciente)."""
    return dentidesk.limpiar_rut(rut) or (rut or '').strip()


# ── Evaluacion (las 3 guardas, en orden) ─────────────────────────────────────

def evaluar(rut, cfg=None):
    """Devuelve None si se puede enviar, o un dict {'motivo','detalle',
    'puede_forzar'} si hay que bloquear. Orden de las guardas:
      1. no_molestar -- nunca se salta (puede_forzar=False).
      2. ya_tiene_hora -- cita activa futura (puede_forzar=True).
      3. enviado_reciente -- ya se le mando dentro de dias_minimos_reenvio
         (puede_forzar=True)."""
    cfg = cfg or load_config()
    scfg = _scheduling_cfg()
    clave = _rut_key(rut)

    reg = _load_registro()
    if clave in (reg.get('no_molestar') or []):
        return {
            'motivo': 'no_molestar',
            'detalle': 'Este paciente está marcado como "no molestar": no se le envían recordatorios de control.',
            'puede_forzar': False,
        }

    citas = dentidesk.citas_futuras_paciente(rut, scfg)
    if citas:
        c = citas[0]
        # Fecha en texto legible: este detalle lo lee la asistente en el panel
        # F2, un ISO suelto (2026-08-08) se entiende peor de un vistazo.
        try:
            f_leg = fecha_legible_larga(date.fromisoformat(c['fecha'][:10]))
        except (KeyError, ValueError):
            f_leg = c.get('fecha', '')
        return {
            'motivo': 'ya_tiene_hora',
            'detalle': f"El paciente ya tiene hora agendada el {f_leg} con {c['profesional'] or 'su doctor'}.",
            'puede_forzar': True,
        }

    envios = (reg.get('envios') or {}).get(clave) or []
    if envios:
        ultimo = max(envios, key=lambda e: e.get('fecha_envio', ''))
        try:
            f_envio = datetime.fromisoformat(ultimo['fecha_envio'])
        except (KeyError, ValueError):
            f_envio = None
        if f_envio is not None:
            dias_transcurridos = (datetime.now() - f_envio).days
            dias_minimos = cfg.get('dias_minimos_reenvio', 90)
            if dias_transcurridos < dias_minimos:
                return {
                    'motivo': 'enviado_reciente',
                    'detalle': f'Ya se le envió un recordatorio de control hace {dias_transcurridos} días (el mínimo configurado son {dias_minimos}).',
                    'puede_forzar': True,
                }

    return None


def _scheduling_cfg():
    """citas_futuras_paciente() necesita el config de scheduling (credenciales
    DentiDesk), no el de recaptacion -- import perezoso para evitar ciclos."""
    import scheduling
    return scheduling.load_config()


# ── Registro de envios ───────────────────────────────────────────────────────

def marcar_enviado(rut, id_agenda, doctor, nombre):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('envios', {}).setdefault(clave, []).append({
            'fecha_envio': datetime.now().isoformat(timespec='seconds'),
            'id_agenda': str(id_agenda or ''),
            'doctor': doctor or '',
            'nombre': nombre or '',
            'respondio': False,
        })
        _save_registro(reg)


def marcar_respondio(rut):
    """Marca el envio MAS RECIENTE de ese RUT como respondido (el paciente toco
    'Agendar por WhatsApp'). Si el RUT no tiene envios registrados, no hace
    nada -- no revienta (puede pasar si el paciente responde a un envio muy
    viejo cuyo registro se perdio, o a un toque fuera de flujo)."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        envios = (reg.get('envios') or {}).get(clave)
        if not envios:
            return False
        ultimo = max(envios, key=lambda e: e.get('fecha_envio', ''))
        ultimo['respondio'] = True
        _save_registro(reg)
        return True


def agregar_no_molestar(rut):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        lista = reg.setdefault('no_molestar', [])
        if clave not in lista:
            lista.append(clave)
        _save_registro(reg)
        return lista


def quitar_no_molestar(rut):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        lista = reg.setdefault('no_molestar', [])
        if clave in lista:
            lista.remove(clave)
        _save_registro(reg)
        return lista


def lista_no_molestar():
    """RUT marcados como 'no molestar'. Lo consume la pestania del panel, que
    los muestra con un boton para sacarlos de la lista."""
    return list(_load_registro().get('no_molestar') or [])


def historial(limite=100):
    """Envios aplanados (RUT + datos del envio), ordenados del mas reciente al
    mas antiguo. Para la pestania del panel."""
    reg = _load_registro()
    plano = []
    for rut, envios in (reg.get('envios') or {}).items():
        for e in envios:
            plano.append({**e, 'rut': rut})
    plano.sort(key=lambda e: e.get('fecha_envio', ''), reverse=True)
    return plano[:limite]
