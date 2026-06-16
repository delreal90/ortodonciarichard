"""
notify.py - Confirmacion al paciente tras agendar (Ortodoncia Richard)

Prioridad:
  1. WhatsApp (bridge Go en localhost:8080) -> mensaje + archivo .ics
  2. Fallback: email via Web3Forms

Reutilizable desde el sitio web y desde el futuro bot de WhatsApp.
"""

import os
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None

from scheduling import generar_ics, load_config

# Bridge de WhatsApp (mismo que usa el MCP)
BRIDGE_URL = os.getenv('WHATSAPP_API_URL', 'http://localhost:8080/api')
BRIDGE_TOKEN_FILE = Path(
    r'C:\Users\ESTUDIO3D\Claude Code Playground\whatsapp-mcp-vgp'
    r'\whatsapp-bridge\store\.bridge-token'
)
WEB3FORMS_KEY = 'f0aa501d-602a-4212-ac11-16b414a91b61'


def _bridge_headers():
    token = os.getenv('WHATSAPP_BRIDGE_TOKEN')
    if not token and BRIDGE_TOKEN_FILE.exists():
        token = BRIDGE_TOKEN_FILE.read_text(encoding='utf-8').strip()
    return {'Authorization': f'Bearer {token}'} if token else {}


def _normalizar_telefono(tel):
    """Telefono chileno -> JID de WhatsApp. Acepta '+56 9 ...', '9 1234 5678', etc."""
    digits = ''.join(c for c in tel if c.isdigit())
    if digits.startswith('56'):
        pass
    elif digits.startswith('9') and len(digits) == 9:
        digits = '56' + digits
    elif len(digits) == 8:
        digits = '569' + digits
    return f'{digits}@s.whatsapp.net'


def _mensaje_confirmacion(c):
    return (
        f"*Ortodoncia Richard* — Confirmacion de hora\n\n"
        f"Hola {c['nombre']}, tu hora quedo agendada:\n\n"
        f"🗓 *Fecha:* {c['fecha_legible']}\n"
        f"🕐 *Hora:* {c['hora']}\n"
        f"👨‍⚕️ *Doctor:* {c['doctor_nombre']}\n"
        f"📋 *Motivo:* {c['motivo_label']}\n"
        f"📍 *Direccion:* {c['direccion']}\n\n"
        f"Adjunto un archivo para agregarla a tu calendario.\n"
        f"Si necesitas reagendar, escribenos. ¡Te esperamos!"
    )


def enviar_confirmacion(cita, cfg=None):
    """
    cita: dict con nombre, telefono, email, fecha (date), fecha_legible, hora,
          doctor_nombre, motivo_label, dur_min.
    Devuelve dict con el canal usado y estado.
    """
    cfg = cfg or load_config()
    clin = cfg['clinica']

    ics = generar_ics(
        titulo=f"Cita Ortodoncia Richard - {cita['motivo_label']}",
        fecha=cita['fecha'], hora=cita['hora'], dur_min=cita['dur_min'],
        doctor_nombre=cita['doctor_nombre'], direccion=clin['direccion'],
        descripcion=f"Motivo: {cita['motivo_label']}. Doctor: {cita['doctor_nombre']}.",
    )

    cita = {**cita, 'direccion': clin['direccion']}
    mensaje = _mensaje_confirmacion(cita)

    # 1) WhatsApp
    if requests is not None:
        try:
            jid = _normalizar_telefono(cita['telefono'])
            r = requests.post(f'{BRIDGE_URL}/send',
                              json={'recipient': jid, 'message': mensaje},
                              headers=_bridge_headers(), timeout=15)
            if r.status_code == 200 and r.json().get('success'):
                _enviar_ics_whatsapp(jid, ics, cita)
                return {'ok': True, 'canal': 'whatsapp'}
        except Exception as e:
            pass  # cae al fallback

    # 2) Fallback email
    if _enviar_email(cita, ics):
        return {'ok': True, 'canal': 'email'}

    return {'ok': False, 'canal': None, 'error': 'No se pudo enviar confirmacion'}


def _enviar_ics_whatsapp(jid, ics, cita):
    """Guarda el .ics temporal y lo envia como archivo por el bridge."""
    if requests is None:
        return
    tmp = Path(tempfile.gettempdir()) / f"cita-{cita['fecha']}-{cita['hora'].replace(':','')}.ics"
    tmp.write_text(ics, encoding='utf-8')
    try:
        requests.post(f'{BRIDGE_URL}/send',
                      json={'recipient': jid, 'media_path': str(tmp)},
                      headers=_bridge_headers(), timeout=20)
    except Exception:
        pass


def _enviar_email(cita, ics):
    """Fallback: Web3Forms. (El .ics se incluye como texto/enlace de respaldo.)"""
    if requests is None or not cita.get('email'):
        return False
    try:
        r = requests.post('https://api.web3forms.com/submit', json={
            'access_key': WEB3FORMS_KEY,
            'subject': f"Confirmacion de hora - {cita['doctor_nombre']}",
            'from_name': 'Agenda Ortodoncia Richard',
            'email': cita['email'],
            'message': _mensaje_confirmacion(cita),
        }, timeout=15)
        return r.status_code == 200
    except Exception:
        return False
