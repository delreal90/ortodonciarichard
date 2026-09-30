"""
vigilante_respaldos.py - Avisa por correo si un respaldo lleva dias sin hacerse.

Hay DOS respaldos y hasta ahora ninguno avisaba cuando fallaba:
  - 'render'   : backup.py, 03:30 -> zip de datos a Drive (lo corre este mismo servidor).
  - 'digital1' : respaldo-digital1/, 21:00 -> copia de "Registros Pacientes" a Drive
                 (lo corre un PC de la clinica; deja `ultimo_respaldo.txt` en Drive).

Este modulo mira los dos una vez al dia y manda UN correo si alguno lleva
UMBRAL_DIAS o mas sin respaldo. No es un aviso diario a proposito: un fallo de un dia
suele ser un PC apagado; cuatro dias ya es un problema.

Anti-repeticion: avisa al cruzar 4 dias y de nuevo a los 8, 12... (cada multiplo).
Cuando el respaldo se recupera, el contador se reinicia.

⚠️ Si el respaldo de DIGITAL1 no se puede LEER (la carpeta no esta compartida con la
cuenta de servicio, credenciales caidas), eso cuenta como "sin verificar" y sigue el
mismo reloj de 4 dias: un vigilante que no ve nada no puede quedar en silencio.

Cero red en lo que es logica (parsear, contar dias, decidir); la red vive en
drive_backup.leer_texto_por_nombre y en notify.
"""

import os
import re
import threading
from datetime import date
from pathlib import Path

import fechas
import jsonstore
import backup
import drive_backup

UMBRAL_DIAS = 4
ARCHIVO_DIGITAL1 = 'ultimo_respaldo.txt'

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
ESTADO_PATH = Path(os.environ.get('VIGILANTE_RESPALDOS_PATH',
                                  _BASE_DIR / 'vigilante_respaldos.json'))

_LOCK = threading.Lock()
_STORE = jsonstore.JsonStore(ESTADO_PATH, indent=2, default={})

SISTEMAS = {
    'render': 'Respaldo de datos del sistema (Render)',
    'digital1': 'Respaldo de fotos y registros (DIGITAL1)',
}


def _a_fecha(texto):
    try:
        return date.fromisoformat((texto or '')[:10])
    except ValueError:
        return None


def dias_render(hoy):
    """(dias, detalle) desde el ultimo respaldo de Render que salio BIEN.
    Sin ningun respaldo ok, cuenta desde el primer intento registrado; sin
    historial no hay con que comparar -> (None, motivo)."""
    hist = backup._STORE.load().get('historial') or []
    oks = [_a_fecha(h.get('fecha')) for h in hist if h.get('ok')]
    oks = [f for f in oks if f]
    if oks:
        ultimo = max(oks)
        return (hoy - ultimo).days, f'ultimo respaldo correcto: {ultimo.isoformat()}'
    intentos = [f for f in (_a_fecha(h.get('fecha')) for h in hist) if f]
    if intentos:
        primero = min(intentos)
        return ((hoy - primero).days,
                f'ningun respaldo correcto desde el primer intento ({primero.isoformat()})')
    return None, 'sin historial de respaldos'


def parsear_corrida(texto):
    """Fecha de 'Ultima corrida: 2026-09-29 21:00 a 22:08' (la noche en que empezo)."""
    m = re.search(r'corrida:\s*(\d{4}-\d{2}-\d{2})', texto or '', re.IGNORECASE)
    return _a_fecha(m.group(1)) if m else None


def dias_digital1(hoy, leer=None):
    """(dias, detalle) desde la ultima corrida del respaldo de DIGITAL1, o
    (None, motivo) si no se pudo leer/entender el archivo de estado."""
    r = (leer or drive_backup.leer_texto_por_nombre)(ARCHIVO_DIGITAL1)
    if not r.get('ok'):
        return None, r.get('error') or 'no se pudo leer el estado'
    f = parsear_corrida(r.get('texto'))
    if not f:
        return None, 'el archivo de estado no tiene una fecha de corrida legible'
    return (hoy - f).days, f'ultima corrida: {f.isoformat()}'


def _bloque(dias):
    """Cuantos multiplos del umbral lleva: 0 (<4 dias), 1 (4-7), 2 (8-11)..."""
    return 0 if dias is None or dias < UMBRAL_DIAS else dias // UMBRAL_DIAS


def evaluar(hoy=None, leer=None):
    """Foto del estado de los dos respaldos + si toca avisar de cada uno. No envia
    nada ni guarda nada: por eso el endpoint de estado la puede usar sin efectos."""
    hoy = hoy or fechas.hoy_chile()
    est = _STORE.load()
    out = {}
    for clave in SISTEMAS:
        previo = est.get(clave) or {}
        if clave == 'render':
            dias, detalle = dias_render(hoy)
            sin_verificar = False
        else:
            dias, detalle = dias_digital1(hoy, leer=leer)
            sin_verificar = dias is None
            if sin_verificar:
                # El reloj corre desde el primer chequeo fallido consecutivo.
                desde = _a_fecha(previo.get('sin_verificar_desde')) or hoy
                dias = (hoy - desde).days
                detalle = f'no se pudo verificar: {detalle}'
        bloque = _bloque(dias)
        out[clave] = {
            'sistema': SISTEMAS[clave], 'dias': dias, 'detalle': detalle,
            'sin_verificar': sin_verificar,
            'atrasado': dias is not None and dias >= UMBRAL_DIAS,
            'avisar': bloque > int(previo.get('bloque', 0)),
            'bloque': bloque,
        }
    return out


def _destinatario():
    import psq
    return psq.email_doctor('alberto') or psq.EMAIL_RESPALDO


def revisar(enviar=True, hoy=None, leer=None):
    """Chequeo diario. Manda UN correo con lo que toque avisar. El contador solo se
    actualiza si el correo salio: una caida de SMTP se reintenta al dia siguiente."""
    import notify
    hoy = hoy or fechas.hoy_chile()
    with _LOCK:
        ev = evaluar(hoy, leer=leer)
        a_avisar = [dict(v, clave=k) for k, v in ev.items() if v['avisar']]
        correo = 'nada que avisar'
        if a_avisar and enviar:
            res = notify.avisar_respaldos_atrasados(_destinatario(), a_avisar, UMBRAL_DIAS)
            correo = 'enviado' if res.get('ok') else f"error: {res.get('error')}"
            if not res.get('ok'):
                a_avisar = []   # no marcar: se reintenta
        elif a_avisar:
            correo = 'pendiente (solo consulta)'
            a_avisar = []

        if enviar:
            est = _STORE.load()
            for clave, v in ev.items():
                e = est.setdefault(clave, {})
                if v['sin_verificar']:
                    e.setdefault('sin_verificar_desde', hoy.isoformat())
                else:
                    e.pop('sin_verificar_desde', None)
                if any(a['clave'] == clave for a in a_avisar) or v['bloque'] < int(e.get('bloque', 0)):
                    e['bloque'] = v['bloque']      # avisado, o recuperado (baja/reinicia)
                e['ultima_revision'] = hoy.isoformat()
            _STORE.save(est)
    return {'ok': True, 'umbral_dias': UMBRAL_DIAS, 'correo': correo, 'respaldos': ev}
