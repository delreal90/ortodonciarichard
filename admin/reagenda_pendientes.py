"""
reagenda_pendientes.py - El aviso a recepcion de "un paciente quiere reagendar"
se ESPERA unos minutos antes de salir.

Por que existe: el correo se mandaba en el mismo instante en que el paciente
tocaba "Reagendar", pero la mayoria elige su hora nueva ahi mismo, en el
minuto siguiente, con el link que recibe. Ese correo llegaba igual y llenaba
la bandeja de recepcion con avisos que ya no habia que atender. El aviso vale
solo cuando el paciente NO resolvio solo.

Como funciona: al tocar el boton se anota un pendiente. Un barrido lo revisa
pasados `minutos_espera` (default 5) y recien ahi decide:
  - si el paciente YA tiene una hora nueva (online o agendada en el meson,
    da lo mismo: se mira DentiDesk) -> se descarta, sin correo.
  - si no -> sale el correo.

El estado vive en disco (jsonstore) y no en memoria a proposito: Render
reinicia y un pendiente en RAM se perderia, dejando a recepcion sin enterarse
nunca de ese paciente.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock

import fechas
import jsonstore

# Junto a patient_index.json (disco persistente en Render), igual que el resto
# de los registros del proyecto.
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
PENDIENTES_PATH = Path(os.environ.get('REAGENDA_PENDIENTES_PATH',
                                      _BASE_DIR / 'reagenda_pendientes.json'))

# Cuanto se espera antes de avisar. 5 minutos: suficiente para que el paciente
# que va a elegir hora al tiro lo haga, y poco para que recepcion se entere el
# mismo rato del que no.
MINUTOS_ESPERA = 5

# Un pendiente muy viejo (Render caido varias horas) igual se avisa: el correo
# lleva la fecha del pedido. Pasados estos dias ya no tiene sentido y se poda.
_DIAS_RETENCION = 7

_LOCK = RLock()
_STORE = jsonstore.JsonStore(PENDIENTES_PATH, indent=2,
                             default={'pendientes': {}},
                             claves={'pendientes': {}})


def _load():
    return _STORE.load()


def _save(reg):
    _STORE.save(reg)


def registrar(id_agenda, telefono, nombre='', fecha='', rut=''):
    """Anota que este paciente pidio reagendar. Idempotente por id_agenda: si
    toca el boton dos veces NO se duplica el aviso, pero SI se reinicia la
    espera (el ultimo toque es el que manda: sigue sin resolver)."""
    id_agenda = str(id_agenda or '').strip()
    if not id_agenda:
        return None
    with _LOCK:
        reg = _load()
        reg.setdefault('pendientes', {})[id_agenda] = {
            'telefono': telefono or '',
            'nombre': nombre or '',
            'fecha_cita': fecha or '',
            'rut': rut or '',
            'pedido': fechas.ahora_chile().isoformat(timespec='seconds'),
        }
        _save(reg)
        return reg['pendientes'][id_agenda]


def vencidos(minutos=None, ahora=None):
    """Los pendientes que ya cumplieron la espera. Devuelve
    [(id_agenda, datos)] ordenados del mas antiguo al mas nuevo."""
    minutos = MINUTOS_ESPERA if minutos is None else minutos
    ahora = ahora or fechas.ahora_chile()
    limite = ahora - timedelta(minutes=minutos)
    out = []
    for ida, p in (_load().get('pendientes') or {}).items():
        pedido = _parse(p.get('pedido'))
        # Sin marca de tiempo legible no se puede evaluar la espera: se trata
        # como vencido (mejor un aviso de mas que perder al paciente).
        if pedido is None or pedido <= limite:
            out.append((ida, p))
    out.sort(key=lambda kv: kv[1].get('pedido') or '')
    return out


def resolver(id_agenda, motivo=''):
    """Saca el pendiente de la lista (ya se aviso, o el paciente ya agendo).
    `motivo` es solo para el log de quien llama."""
    id_agenda = str(id_agenda or '').strip()
    with _LOCK:
        reg = _load()
        quitado = (reg.get('pendientes') or {}).pop(id_agenda, None)
        if quitado is not None:
            _save(reg)
        return quitado


def podar(dias=None, ahora=None):
    """Descarta pendientes demasiado viejos (ver _DIAS_RETENCION)."""
    dias = _DIAS_RETENCION if dias is None else dias
    ahora = ahora or fechas.ahora_chile()
    limite = ahora - timedelta(days=dias)
    with _LOCK:
        reg = _load()
        pend = reg.get('pendientes') or {}
        viejos = [ida for ida, p in pend.items()
                  if (_parse(p.get('pedido')) or ahora) <= limite]
        for ida in viejos:
            pend.pop(ida, None)
        if viejos:
            _save(reg)
        return len(viejos)


def _parse(iso):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(str(iso)[:19])
    except (ValueError, TypeError, AttributeError):
        return None


def listar():
    """Todos los pendientes (para el panel o diagnostico)."""
    return dict(_load().get('pendientes') or {})
