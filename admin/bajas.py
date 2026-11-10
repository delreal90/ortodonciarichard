"""
bajas.py - "No quiero recibir mas recordatorios": la lista GENERAL de bajas.

Nace de la auditoria del 2026-11-10 (Ley 21.719, derecho de oposicion). Cada
sistema que le escribe al paciente por iniciativa de la clinica (control dental,
recaptacion, encuestas, seguimiento, reactivacion) tiene su propia lista de "no
molestar", y esas listas siguen existiendo: "no me manden encuestas" no es "no
me avisen de mi control". Lo que faltaba eran dos cosas:

  1. Que el PACIENTE pudiera darse de baja solo. Las listas por sistema solo las
     llena el personal desde el panel o el F2.
  2. Una baja que valga para TODO. El que escribe "BAJA" por WhatsApp o aprieta
     el enlace de un correo no esta eligiendo un sistema: no quiere mas avisos.

Esta lista es ADICIONAL: `avisos.ListaNoMolestar.contiene()` la consulta ademas
de la propia, asi que los cinco sistemas la respetan sin tocar su codigo.

⚠️ NO aplica a lo asistencial: confirmacion de una hora, recordatorio de una cita
ya agendada, copia de un consentimiento, formulario de seguro que el paciente
pidio. Eso es parte de la atencion y no pasa por `avisos`.

El enlace del correo lleva un token FIRMADO (no el RUT) y sin vencimiento: un
correo de hace un año tiene que seguir sirviendo para darse de baja.
⚠️ Abrir el enlace NO da de baja: los correos pre-abren los links solos (misma
leccion que /tareas/propuesta). La baja es siempre un boton.

Cero red. Estado en `bajas_global.json` (disco persistente, gitignored: lleva RUT).
"""

import logging
import os
import re
import unicodedata
from pathlib import Path

from itsdangerous import BadSignature, URLSafeSerializer

import fechas
import jsonstore
import secretos

log = logging.getLogger(__name__)

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('BAJAS_PATH', _BASE_DIR / 'bajas_global.json'))

_STORE = jsonstore.JsonStore(REGISTRO_PATH, default={'bajas': {}}, indent=2,
                             claves={'bajas': {}})

ORIGENES = ('correo', 'whatsapp', 'panel')


def _clave(rut):
    """Misma forma canonica que avisos.rut_key (sin importarlo: avisos importa
    este modulo)."""
    return ''.join(ch for ch in str(rut or '') if ch.isalnum()).upper()


# ── La lista ──────────────────────────────────────────────────────────────

def agregar(rut, origen='panel', detalle=''):
    clave = _clave(rut)
    if not clave:
        return False

    def _fn(reg):
        reg['bajas'].setdefault(clave, {
            'fecha_iso': fechas.ahora_chile().isoformat(timespec='seconds'),
            'origen': origen if origen in ORIGENES else 'panel',
            'detalle': str(detalle or '')[:120],
        })
    _STORE.actualizar(_fn)
    return True


def quitar(rut):
    clave = _clave(rut)

    def _fn(reg):
        reg['bajas'].pop(clave, None)
    _STORE.actualizar(_fn)


def contiene(rut):
    clave = _clave(rut)
    if not clave:
        return False
    try:
        return clave in (_STORE.load().get('bajas') or {})
    except Exception as e:      # un archivo ilegible no puede botar los avisos
        log.warning('bajas: no se pudo leer la lista general: %r', e)
        return False


def listar():
    """[{rut, fecha_iso, origen, detalle}], la mas reciente primero."""
    items = [dict(v, rut=k) for k, v in (_STORE.load().get('bajas') or {}).items()]
    return sorted(items, key=lambda x: x.get('fecha_iso', ''), reverse=True)


# ── Enlace del correo ─────────────────────────────────────────────────────

def _serializer():
    return URLSafeSerializer(secretos.firma('CONSENT_SECRET'), salt='baja-avisos')


def crear_token(rut):
    return _serializer().dumps(_clave(rut))


def rut_de_token(token):
    """El RUT que firma ese token, o '' si no es valido."""
    try:
        return _clave(_serializer().loads(str(token or '')))
    except (BadSignature, Exception):
        return ''


def enlace(rut, base='https://ortodonciarichard.onrender.com'):
    return f'{base}/baja?token={crear_token(rut)}'


# ── WhatsApp: ¿el mensaje pide la baja? ───────────────────────────────────

# Solo mensajes que son, enteros, una orden de baja. "no quiero perder mi hora,
# ¿me pueden dar de baja el recordatorio del martes?" NO debe dar de baja sola:
# ese lo lee una persona. Por eso se compara el mensaje completo, no una palabra.
_FRASES_BAJA = {
    'baja', 'stop', 'alto', 'cancelar suscripcion', 'darme de baja', 'dar de baja',
    'no mas mensajes', 'no quiero mas mensajes', 'no enviar mas', 'no me escriban mas',
    'no me escriban', 'no molestar', 'unsubscribe', 'detener',
}


def es_pedido_de_baja(texto):
    s = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-z ]+', ' ', s.lower())
    s = re.sub(r'\s+', ' ', s).strip()
    return s in _FRASES_BAJA
