"""
aceptaciones.py - Evidencia de que el paciente acepto el tratamiento de sus datos.

Auditoria 2026-11-10 (Ley 21.719): la casilla "autorizo el tratamiento de mis
datos personales y de salud" del agendamiento online solo se comprobaba en el
navegador. Si alguien preguntara "¿cuando y que acepto este paciente?", no habia
nada guardado. Aca queda: quien (RUT), cuando, donde (origen), que version del
texto y a que cita corresponde.

No decide nada: solo anota. La ruta que reserva NO rechaza una reserva sin
aceptacion (un navegador con la pagina vieja en cache no la manda, y dejar a un
paciente sin hora por eso seria peor); la falta queda a la vista en el registro.

Cero red. Estado en `aceptaciones.json` (disco persistente, gitignored: RUT).
"""

import os
import re
from pathlib import Path

import fechas
import jsonstore

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('ACEPTACIONES_PATH', _BASE_DIR / 'aceptaciones.json'))

_STORE = jsonstore.JsonStore(REGISTRO_PATH, default={'items': []}, indent=None,
                             claves={'items': []})

# Tope de filas: son ~3 reservas online al dia; 20.000 son mas de 15 años.
MAX_ITEMS = 20000


def _clave(rut):
    return ''.join(ch for ch in str(rut or '') if ch.isalnum()).upper()


def registrar(rut, origen, version, ref='', aceptado=True):
    """Anota una aceptacion (o su ausencia, aceptado=False). Nunca lanza: es un
    registro, no puede botar la reserva que lo origina."""
    try:
        fila = {
            'rut': _clave(rut),
            'fecha_iso': fechas.ahora_chile().isoformat(timespec='seconds'),
            'origen': str(origen or '')[:30],
            'version': re.sub(r'[^\w.\-]', '', str(version or ''))[:40],
            'ref': str(ref or '')[:40],
            'aceptado': bool(aceptado),
        }

        def _fn(reg):
            reg['items'].append(fila)
            if len(reg['items']) > MAX_ITEMS:
                del reg['items'][:len(reg['items']) - MAX_ITEMS]
        _STORE.actualizar(_fn)
        return True
    except Exception:
        return False


def de_rut(rut):
    clave = _clave(rut)
    return [it for it in _STORE.load().get('items', []) if it.get('rut') == clave]


def listar(limite=500):
    return list(reversed(_STORE.load().get('items', [])[-limite:]))
