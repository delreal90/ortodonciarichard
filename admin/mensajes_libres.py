"""
mensajes_libres.py - Mensajes de texto libre que los pacientes le escriben al
WhatsApp de la clinica (Ortodoncia Richard).

QUE PROBLEMA RESUELVE
---------------------
El WhatsApp de la clinica vive en la Cloud API de Meta. Varios mensajes
automaticos terminan con "escribanos por aqui", pero el webhook solo procesaba
botones de plantilla y llamadas: un mensaje escrito a mano caia en la bandeja de
Meta Business Suite y **nadie se enteraba** hasta que alguien se acordaba de
abrirla. Recepcion SI contesta desde esa bandeja; lo que faltaba era AVISARLES.

QUE HACE (por cada mensaje que NO es boton ni llamada)
------------------------------------------------------
1. **Correo a recepcion SOLO si nadie le contesto en `ESPERA_RESPUESTA_MIN`
   (5) minutos** (pedido del usuario 2026-10-08: avisando al instante, el correo
   llegaba antes de que recepcion alcanzara a responder). Lleva nombre (si el
   telefono calza con un paciente), telefono, hora de Chile, el texto, y el link
   a la bandeja de Meta. "Le contestaron" = Meta avisa (webhook 'statuses',
   status 'sent') de un mensaje que salio del numero HACIA ese paciente y que NO
   mando este sistema (`wa_cloud.es_mensaje_propio`), o llega un eco del mensaje
   ('message_echoes' / 'smb_message_echoes'). Si Meta no avisa de las respuestas
   de la bandeja, el correo sale igual a los 5 minutos: nunca menos aviso que
   antes. `respuestas_vistas()` cuenta cuantas se detectaron (sale en
   /api/salud) para comprobarlo.
   Tipos que no son texto (audio, imagen, documento...) se anotan como
   "[envio un audio]" -- el contenido solo se ve en la bandeja.
2. **Anti-inundacion**: maximo UN correo por telefono cada `VENTANA_AVISO_MIN`
   (30) minutos. Los mensajes que llegan dentro de la ventana se ACUMULAN en el
   registro y salen juntos en el siguiente correo. Ese siguiente correo sale:
     cuando `enviar_vencidos()` lo barre (loop de 1 minuto del scheduler y al
     final de cada evento del webhook).
3. **Respuesta automatica SOLO fuera de horario de atencion**, una vez por
   telefono cada `HORAS_AUTORESPUESTA` (12) horas, con texto libre (el paciente
   acaba de escribir: estamos dentro de la ventana de 24 h de Meta). En horario
   de atencion NO se responde nada: solo el correo, una persona contesta.

HORARIO DE ATENCION
-------------------
Se reusa la definicion del config de la agenda (`scheduling_config.json` ->
"horario": dias_habiles, apertura, cierre; es la misma jornada que publica el
sitio: lunes a viernes 9:00 a 19:30). Si el config no la trae se usa
`HORARIO_POR_DEFECTO`. Los feriados NO se consideran (igual que
`scheduling.py`): un feriado es "en horario" y no sale respuesta automatica.

REGISTRO
--------
`mensajes_libres.json` en el disco persistente de Render (misma base que
patient_index.json, via PATIENT_INDEX_PATH). Lleva telefonos y texto de
pacientes: va al .gitignore (repo PUBLICO). Se poda lo de mas de 7 dias.

POR QUE NO PASA POR `avisos.py` (regla 3)
-----------------------------------------
Mismo criterio que llamadas_perdidas.py: el paciente es quien inicio el
contacto, contestarle no es molestarlo. Ademas el correo es interno.
"""

import os
import logging
from datetime import datetime, timedelta
from pathlib import Path

import fechas
import jsonstore
import notify
import pacientes
import wa_cloud

log = logging.getLogger(__name__)

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('MENSAJES_LIBRES_PATH',
                                    _BASE_DIR / 'mensajes_libres.json'))

# Cuanto se espera a que alguien le conteste al paciente antes de avisar.
ESPERA_RESPUESTA_MIN = 5
# Maximo un correo por telefono cada tantos minutos (lo demas se acumula).
VENTANA_AVISO_MIN = 30
# Una auto-respuesta por telefono cada tantas horas.
HORAS_AUTORESPUESTA = 12
# Registro: se descarta lo mas viejo que esto.
DIAS_RETENCION = 7
# Tope de mensajes acumulados por telefono y de largo por mensaje (un paciente
# que pega un texto enorme o spamea no debe inflar el registro ni el correo).
MAX_PENDIENTES = 20
MAX_TEXTO = 1000

URL_AGENDAR = 'https://www.ortodonciarichard.cl/#agendar'

# Solo si scheduling_config.json no trae "horario". Mismo valor que el sitio.
HORARIO_POR_DEFECTO = {'dias_habiles': [1, 2, 3, 4, 5],
                       'apertura': '09:00', 'cierre': '19:30'}

_DIAS = {1: 'lunes', 2: 'martes', 3: 'miércoles', 4: 'jueves',
         5: 'viernes', 6: 'sábado', 7: 'domingo'}

# Tipos de Meta que no traen texto -> como se anotan en el correo.
_SIN_TEXTO = {'image': 'una imagen', 'audio': 'un audio', 'document': 'un documento',
              'video': 'un video', 'sticker': 'un sticker', 'location': 'una ubicación',
              'contacts': 'un contacto'}

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2, default={'telefonos': {}},
                             claves={'telefonos': {}})


# -- Interpretacion del mensaje ------------------------------------------------

def describir(msg):
    """Texto a mostrarle a recepcion, o None si el mensaje no debe avisarse
    (boton, reaccion, evento del sistema: no son 'alguien nos escribio')."""
    tipo = (msg.get('type') or '').lower()
    if tipo == 'text':
        cuerpo = ((msg.get('text') or {}).get('body') or '').strip()
        return (cuerpo or '[envió un mensaje vacío]')[:MAX_TEXTO]
    if tipo == 'interactive':
        inter = msg.get('interactive') or {}
        resp = inter.get('button_reply') or inter.get('list_reply') or {}
        return (resp.get('title') or '[respondió un mensaje interactivo]')[:MAX_TEXTO]
    if tipo in _SIN_TEXTO:
        extra = msg.get(tipo) or {}
        detalle = ''
        if isinstance(extra, dict):
            detalle = (extra.get('caption') or extra.get('filename') or '').strip()
        texto = f'[envió {_SIN_TEXTO[tipo]}]'
        return (f'{texto} {detalle}' if detalle else texto)[:MAX_TEXTO]
    if tipo == 'unsupported':
        return '[envió algo que WhatsApp no deja ver aquí — revisar en la bandeja]'
    return None


def _clave_tel(tel):
    digits = ''.join(c for c in (tel or '') if c.isdigit())
    return digits[-8:] if len(digits) >= 8 else digits


def es_de_la_clinica(msg, valor, cfg):
    """True si el mensaje lo mando la propia clinica (eco) y no un paciente."""
    propios = {_clave_tel((valor.get('metadata') or {}).get('display_phone_number')),
               _clave_tel(((cfg or {}).get('clinica') or {}).get('whatsapp'))}
    propios.discard('')
    return _clave_tel(msg.get('from')) in propios


# -- Horario de atencion -------------------------------------------------------

def _horario(cfg):
    h = (cfg or {}).get('horario') or {}
    return {k: h.get(k) or HORARIO_POR_DEFECTO[k] for k in HORARIO_POR_DEFECTO}


def en_horario(cfg, ahora=None):
    """True si `ahora` (hora de Chile) cae dentro de la jornada de atencion."""
    ahora = ahora or fechas.ahora_chile()
    h = _horario(cfg)
    if ahora.isoweekday() not in h['dias_habiles']:
        return False
    return h['apertura'] <= ahora.strftime('%H:%M') < h['cierre']


def texto_horario(cfg):
    """'lunes a viernes de 9:00 a 19:30 hrs', armado desde el config."""
    h = _horario(cfg)
    dias = sorted(set(h['dias_habiles']))
    if len(dias) > 1 and dias == list(range(dias[0], dias[-1] + 1)):
        dias_txt = f'{_DIAS[dias[0]]} a {_DIAS[dias[-1]]}'
    else:
        dias_txt = ', '.join(_DIAS[d] for d in dias)
    return f"{dias_txt} de {h['apertura'].lstrip('0')} a {h['cierre'].lstrip('0')} hrs"


def texto_autorespuesta(cfg):
    return ('Hola, recibimos su mensaje. Recepción le responderá en nuestro '
            f'horario de atención ({texto_horario(cfg)}). Si desea agendar o '
            f'cambiar su hora puede hacerlo aquí: {URL_AGENDAR}')


# -- Registro (anti-inundacion) ------------------------------------------------

def _parse(iso):
    try:
        return datetime.fromisoformat(str(iso)[:19])
    except (ValueError, TypeError):
        return None


def _minutos_desde(iso, ahora):
    """Minutos desde `iso`, o None si no hay/ilegible. None se trata como 'hace
    mucho': ante la duda es mejor avisar de mas que dejar a un paciente mudo."""
    t = _parse(iso)
    return None if t is None else (ahora - t).total_seconds() / 60.0


def _ventana_vencida(iso, ahora):
    m = _minutos_desde(iso, ahora)
    return m is None or m >= VENTANA_AVISO_MIN


def _sin_respuesta_hace(iso, ahora):
    m = _minutos_desde(iso, ahora)
    return m is None or m >= ESPERA_RESPUESTA_MIN


def _podar(tels, ahora):
    limite = ahora - timedelta(days=DIAS_RETENCION)
    for clave in [c for c, e in tels.items()
                  if (_parse(e.get('ultimo_mensaje')) or ahora) <= limite]:
        tels.pop(clave, None)


def _hora_legible(iso):
    t = _parse(iso)
    return t.strftime('%d-%m %H:%M') if t else ''


def _registrar(telefono, texto, cfg, ahora, perfil=''):
    """Anota el mensaje como pendiente de respuesta y decide, DENTRO del
    actualizar() (dos mensajes simultaneos no se leen ambos como 'primero'), si
    toca la auto-respuesta de fuera de horario."""
    clave = _clave_tel(telefono)
    ahora_iso = ahora.isoformat(timespec='seconds')
    decision = {'avisar': None, 'previo_aviso': None,
                'autorresponder': False, 'previa_auto': None}

    def _fn(reg):
        tels = reg.setdefault('telefonos', {})
        _podar(tels, ahora)
        e = tels.setdefault(clave, {'pendientes': []})
        e['telefono'] = telefono
        e['ultimo_mensaje'] = ahora_iso
        pend = e.setdefault('pendientes', [])
        pend.append({'cuando': ahora_iso, 'texto': texto})
        del pend[:-MAX_PENDIENTES]
        if perfil:
            e['perfil'] = perfil
        # El correo ya no sale aca: lo decide enviar_vencidos() pasados
        # ESPERA_RESPUESTA_MIN sin respuesta.
        if not en_horario(cfg, ahora):
            m = _minutos_desde(e.get('ultima_autorespuesta'), ahora)
            if m is None or m >= HORAS_AUTORESPUESTA * 60:
                decision['autorresponder'] = True
                decision['previa_auto'] = e.get('ultima_autorespuesta')
                e['ultima_autorespuesta'] = ahora_iso
        return reg

    _STORE.actualizar(_fn)
    return clave, decision


def _deshacer(clave, mensajes=None, previo_aviso=None, restaurar_aviso=False,
              previa_auto=None, restaurar_auto=False):
    """Si el envio fallo, el registro vuelve a como estaba: el mensaje sigue
    pendiente y se reintenta (en vez de darlo por avisado)."""
    def _fn(reg):
        e = (reg.get('telefonos') or {}).get(clave)
        if e is None:
            return reg
        if mensajes:
            e['pendientes'] = (list(mensajes) + e.get('pendientes', []))[-MAX_PENDIENTES:]
        if restaurar_aviso:
            if previo_aviso is None:
                e.pop('ultimo_aviso', None)
            else:
                e['ultimo_aviso'] = previo_aviso
        if restaurar_auto:
            if previa_auto is None:
                e.pop('ultima_autorespuesta', None)
            else:
                e['ultima_autorespuesta'] = previa_auto
        return reg
    try:
        _STORE.actualizar(_fn)
    except Exception as ex:
        log.warning('No se pudo deshacer el registro de mensajes libres: %s', ex)


# -- Envio ---------------------------------------------------------------------

def _identificar(telefono, perfil=''):
    """(nombre, registrado). Nombre de la ficha por telefono; si no, el nombre
    de perfil de WhatsApp; registrado=False cuando el numero no es de un paciente."""
    try:
        pac = pacientes.buscar_por_telefono(telefono) or {}
    except Exception as e:
        log.warning('No se pudo buscar al paciente por telefono: %s', e)
        pac = {}
    if pac.get('nombre'):
        return pac['nombre'], True
    return (perfil or '').strip(), False


def _enviar_aviso(clave, telefono, mensajes, previo_aviso, perfil=''):
    nombre, registrado = _identificar(telefono, perfil)
    lista = [(_hora_legible(m.get('cuando')), m.get('texto', '')) for m in mensajes]
    try:
        ok = notify.avisar_recepcion_mensaje_libre(nombre, telefono, lista, registrado=registrado)
    except Exception as e:
        log.warning('Fallo el correo de mensaje libre (...%s): %s', telefono[-4:], e)
        ok = False
    if not ok:
        log.warning('No se pudo avisar a recepcion del mensaje de ...%s; queda pendiente',
                    telefono[-4:])
        # Los mensajes vuelven a pendientes pero la ventana NO se reabre: el
        # reintento espera a que se cumpla (no se martilla un SMTP caido en cada
        # evento del webhook). `previo_aviso` queda por si se quisiera rebobinar.
        _deshacer(clave, mensajes=mensajes)
    return bool(ok)


def procesar(msg, cfg, contactos=None, ahora=None):
    """Punto de entrada desde el webhook. NUNCA lanza: un fallo aqui no puede
    tumbar el webhook (Meta reintenta agresivamente si no ve 200). True si el
    mensaje era un mensaje libre (se haya podido avisar o no)."""
    try:
        texto = describir(msg)
        telefono = msg.get('from') or ''
        if texto is None or not _clave_tel(telefono):
            return False
        ahora = ahora or fechas.ahora_chile()
        perfil = (contactos or {}).get(telefono, '')
        clave, d = _registrar(telefono, texto, cfg, ahora, perfil)
    except Exception as e:
        log.warning('Mensaje libre: no se pudo registrar: %s', e)
        return False

    if d['autorresponder']:
        try:
            r = notify.enviar_texto_libre(telefono, texto_autorespuesta(cfg))
            if isinstance(r, dict) and not r.get('ok'):
                log.warning('No salio la auto-respuesta fuera de horario a ...%s', telefono[-4:])
                _deshacer(clave, previa_auto=d['previa_auto'], restaurar_auto=True)
        except Exception as e:
            log.warning('Mensaje libre: fallo la auto-respuesta: %s', e)
            _deshacer(clave, previa_auto=d['previa_auto'], restaurar_auto=True)
    return True


def enviar_vencidos(ahora=None):
    """Manda el correo de los mensajes que llevan ESPERA_RESPUESTA_MIN sin que
    nadie le conteste al paciente (y respetando la ventana anti-inundacion).
    Devuelve cuantos correos salieron. No lanza."""
    tomados = []
    try:
        ahora = ahora or fechas.ahora_chile()

        def _fn(reg):
            for clave, e in (reg.get('telefonos') or {}).items():
                pend = e.get('pendientes') or []
                if (pend and _sin_respuesta_hace(pend[0].get('cuando'), ahora)
                        and _ventana_vencida(e.get('ultimo_aviso'), ahora)):
                    tomados.append((clave, e.get('telefono', ''), list(pend),
                                    e.get('ultimo_aviso'), e.get('perfil', '')))
                    e['pendientes'] = []
                    e['ultimo_aviso'] = ahora.isoformat(timespec='seconds')
            return reg

        # Solo escribe si hay algo que tomar: el caso normal es no tener nada.
        if not any(e.get('pendientes') for e in (_STORE.load().get('telefonos') or {}).values()):
            return 0
        _STORE.actualizar(_fn)
    except Exception as e:
        log.warning('Mensajes libres: no se pudo barrer lo acumulado: %s', e)
        return 0
    enviados = 0
    for clave, telefono, mensajes, previo, perfil in tomados:
        try:
            if _enviar_aviso(clave, telefono, mensajes, previo, perfil):
                enviados += 1
        except Exception as e:
            log.warning('Mensajes libres: fallo el aviso acumulado: %s', e)
    return enviados


def vaciar():
    _STORE.save({'telefonos': {}})


# -- Respuestas de recepcion (la bandeja de Meta) ------------------------------

_RESPUESTAS_VISTAS = 0


def respuestas_vistas():
    """Cuantas respuestas a pacientes se detectaron desde el arranque (para
    /api/salud: si queda en 0 mientras recepcion contesta, Meta no nos avisa
    de los mensajes de la bandeja y el correo sale siempre a los 5 minutos)."""
    return _RESPUESTAS_VISTAS


def _desde_unix(ts):
    try:
        return datetime.fromtimestamp(int(ts), fechas.TZ_CHILE).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def registrar_respuesta(telefono, cuando=None):
    """Alguien le escribio al paciente desde el numero de la clinica: sus
    mensajes ANTERIORES a esa respuesta dejan de estar pendientes (los que
    escriba despues siguen esperando). True si habia algo que despejar."""
    global _RESPUESTAS_VISTAS
    clave = _clave_tel(telefono)
    if not clave:
        return False
    lim = (cuando or fechas.ahora_chile()).isoformat(timespec='seconds')
    despejados = []

    def _fn(reg):
        e = (reg.get('telefonos') or {}).get(clave)
        if not e:
            return reg
        pend = e.get('pendientes') or []
        quedan = [m for m in pend if str(m.get('cuando', '')) > lim]
        despejados.append(len(pend) - len(quedan))
        e['pendientes'] = quedan
        e['ultima_respuesta'] = lim
        return reg

    try:
        _STORE.actualizar(_fn)
    except Exception as ex:
        log.warning('Mensajes libres: no se pudo anotar la respuesta: %s', ex)
        return False
    _RESPUESTAS_VISTAS += 1
    return bool(despejados and despejados[0])


def procesar_salientes(valor, cfg=None):
    """Mira en un 'value' del webhook si salio algun mensaje de la clinica hacia
    un paciente que NO mando este sistema (= lo escribio una persona desde la
    bandeja). Nunca lanza."""
    try:
        for st in valor.get('statuses', []) or []:
            if (st.get('status') == 'sent' and st.get('recipient_id')
                    and not wa_cloud.es_mensaje_propio(st.get('id'))):
                registrar_respuesta(st['recipient_id'], _desde_unix(st.get('timestamp')))
        ecos = list(valor.get('message_echoes') or []) + list(valor.get('smb_message_echoes') or [])
        for m in ecos:
            if m.get('to') and not wa_cloud.es_mensaje_propio(m.get('id')):
                registrar_respuesta(m['to'], _desde_unix(m.get('timestamp')))
        for m in valor.get('messages', []) or []:
            if (m.get('to') and es_de_la_clinica(m, valor, cfg)
                    and not wa_cloud.es_mensaje_propio(m.get('id'))):
                registrar_respuesta(m['to'], _desde_unix(m.get('timestamp')))
    except Exception as e:
        log.warning('Mensajes libres: no se pudo leer los mensajes salientes: %s', e)
