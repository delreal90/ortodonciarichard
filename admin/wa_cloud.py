"""
wa_cloud.py - Cliente de la WhatsApp Cloud API oficial (Meta) para Ortodoncia Richard

Unico modulo que habla por red con graph.facebook.com. El resto del sistema
(notify.py) lo usa a traves de funciones limpias por plantilla, sin ver
tokens ni el formato del payload de Meta.

Las 7 plantillas (creadas en el Administrador de WhatsApp, idioma es_CL /
"Spanish (CHL)"):
  conversacion_general      {{1}}=nombre {{2}}=motivo_libre         boton: Sí, díganme
  confirmacion_hora         {{1}}=nombre {{2}}=doctor {{3}}=fecha {{4}}=hora   (sin botones)
  recordatorio_semana       {{1}}=nombre {{2}}=doctor {{3}}=fecha {{4}}=hora   botones: Confirmo/Reagendar/Anular
  recordatorio_dia          {{1}}=nombre {{2}}=doctor {{3}}=fecha {{4}}=hora   botones: Confirmo/Reagendar/Anular
  inasistencia_reagendar    {{1}}=nombre {{2}}=fecha                          boton: Reagendar
  primera_consulta          {{1}}=nombre {{2}}=doctor {{3}}=fecha {{4}}=hora   header VIDEO, botones: Confirmo/Reagendar
  consentimiento_informado  {{1}}=nombre {{2}}=tipo_label {{3}}=link          (sin botones)
                            ⏳ en revision — notify.py cae de vuelta a
                            conversacion_general mientras Meta la aprueba.

Los botones de respuesta rapida (quick reply) llevan un PAYLOAD propio por boton,
independiente del texto aprobado: se les pone "{tipo}:{id_agenda}" (tipo = semana/dia/
inasistencia) para que el webhook (webhook_wa.py) sepa a que cita responde el toque y
de que recordatorio vino, sin depender del orden/indice de los botones (identifica la
ACCION por button.text, que Meta siempre manda igual al texto aprobado). Solo el header
de video (primera_consulta) necesita un link publico al archivo (Meta no acepta subir
el video en cada envio).

MODO MOCK: si WA_ENABLED no es 'true', no se llama a Meta; se devuelve un
resultado simulado para que el resto del sistema (server.py, confirmaciones.py)
corra completo sin credenciales todavia.

Variables de entorno (Render / local):
  WA_ENABLED           'true' para enviar de verdad; cualquier otro valor = mock
  WA_TOKEN             token de acceso (temporal 24h en pruebas; permanente en produccion)
  WA_PHONE_NUMBER_ID   Phone Number ID de la WABA (de prueba o el real)
  WA_API_VERSION       opcional, default 'v21.0'
"""

import os
import logging

log = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


class WhatsAppCloudError(Exception):
    pass


IDIOMA = 'es_CL'

# Posicion del boton quick-reply ("Agendar por WhatsApp") dentro de la
# plantilla 'recordatorio_control_dr_vial'. Meta SI acepto los 3 botones
# MEZCLADOS y el orden REAL con que quedo creada (verificado 2026-07-21) es:
#   0 = "Agendar Online"        (URL)
#   1 = "Llamar por telefono"   (telefono)
#   2 = "Agendar por WhatsApp"  (quick-reply)  <-- el unico que acepta payload
# Los botones URL/telefono NO llevan componente 'button' en el envio (Meta los
# resuelve solo con lo aprobado) y NO generan evento de webhook. Si algun dia
# se reordenan los botones al editar la plantilla, hay que ajustar este indice.
IDX_BOTON_AGENDAR_WA = 2


def _config():
    return {
        'enabled': os.getenv('WA_ENABLED', '').strip().lower() in ('1', 'true', 'yes', 'on'),
        'token': os.getenv('WA_TOKEN', '').strip(),
        'phone_number_id': os.getenv('WA_PHONE_NUMBER_ID', '').strip(),
        'api_version': os.getenv('WA_API_VERSION', 'v21.0').strip(),
    }


def _normalizar_telefono(tel):
    """Telefono chileno -> msisdn E.164 sin '+' (lo que espera la Cloud API)."""
    digits = ''.join(c for c in (tel or '') if c.isdigit())
    if digits.startswith('56'):
        return digits
    if digits.startswith('9') and len(digits) == 9:
        return '56' + digits
    if len(digits) == 8:
        return '569' + digits
    return digits


def _post(payload):
    """POST de bajo nivel a /messages. Modo mock si WA_ENABLED no esta activo."""
    cfg = _config()
    if not cfg['enabled']:
        log.info('wa_cloud MOCK -> %s', payload)
        return {'ok': True, 'mock': True, 'payload': payload}

    if requests is None:
        raise WhatsAppCloudError("Falta 'requests' (pip install requests)")
    if not cfg['token'] or not cfg['phone_number_id']:
        raise WhatsAppCloudError('Faltan WA_TOKEN / WA_PHONE_NUMBER_ID')

    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}/messages"
    headers = {
        'Authorization': f"Bearer {cfg['token']}",
        'Content-Type': 'application/json',
    }
    # Cualquier falla (red, timeout, JSON invalido) se envuelve SIEMPRE en
    # WhatsAppCloudError: es la unica excepcion que los llamadores (notify.py)
    # esperan capturar. Sin esto, un error de red se escapaba sin capturar y
    # tumbaba el request completo (Flask devolvia su pagina HTML de error en
    # vez de JSON, rompiendo al cliente que espera parsear la respuesta).
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=20)
    except requests.exceptions.RequestException as e:
        raise WhatsAppCloudError(f'Error de red al llamar a Meta: {e}')
    if resp.status_code >= 400:
        raise WhatsAppCloudError(f'Meta respondio {resp.status_code}: {resp.text[:300]}')
    try:
        data = resp.json()
    except ValueError:
        raise WhatsAppCloudError(f'Respuesta invalida de Meta (no es JSON): {resp.text[:300]}')
    return {'ok': True, 'mock': False, 'raw': data,
            'message_id': (data.get('messages') or [{}])[0].get('id')}


def _param(texto):
    return {'type': 'text', 'text': str(texto)}


def _enviar_plantilla(telefono, nombre_plantilla, parametros_body, header_video_url=None,
                       boton_payload=None, num_botones=0, boton_indices=None):
    """Arma y envia un mensaje de plantilla. parametros_body: lista ordenada
    de valores para {{1}}, {{2}}, ... del cuerpo. boton_payload/num_botones:
    si la plantilla tiene botones quick-reply, se les fija el MISMO payload
    (usado para identificar la cita al recibir el toque via webhook).

    boton_indices: lista opcional de indices CONCRETOS (0-based) a los que
    ponerle el payload -- para plantillas con botones MEZCLADOS (URL/telefono
    + quick-reply), donde NO todos los indices 0..num_botones-1 son
    quick-reply (los CTA de tipo URL/telefono no aceptan componente 'button'
    en el envio, Meta los resuelve solo con lo aprobado). Si no viene, se
    mantiene el comportamiento de siempre: range(num_botones) -- no rompe a
    los llamadores existentes (todos con botones 100% quick-reply)."""
    to = _normalizar_telefono(telefono)
    components = []
    if header_video_url:
        components.append({
            'type': 'header',
            'parameters': [{'type': 'video', 'video': {'link': header_video_url}}],
        })
    if parametros_body:
        components.append({
            'type': 'body',
            'parameters': [_param(v) for v in parametros_body],
        })
    if boton_payload and num_botones:
        indices = boton_indices if boton_indices is not None else range(num_botones)
        for i in indices:
            components.append({
                'type': 'button',
                'sub_type': 'quick_reply',
                'index': str(i),
                'parameters': [{'type': 'payload', 'payload': boton_payload}],
            })

    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'template',
        'template': {
            'name': nombre_plantilla,
            'language': {'code': IDIOMA},
        },
    }
    if components:
        payload['template']['components'] = components

    return _post(payload)


# ── Punto de entrada por plantilla ───────────────────────────────────────────

def enviar_conversacion_general(telefono, nombre, motivo_texto):
    return _enviar_plantilla(telefono, 'conversacion_general', [nombre, motivo_texto])


def enviar_consentimiento(telefono, nombre, tipo_label, link):
    return _enviar_plantilla(telefono, 'consentimiento_informado', [nombre, tipo_label, link])


def enviar_confirmacion_hora(telefono, nombre, doctor_nombre, fecha_legible, hora):
    return _enviar_plantilla(telefono, 'confirmacion_hora',
                              [nombre, doctor_nombre, fecha_legible, hora])


def enviar_reagenda_confirmada(telefono, nombre, doctor_nombre, fecha_legible, hora):
    """Aviso al paciente de que su hora fue reagendada con exito, tras completar
    la reserva nueva desde el link de reagendar. Plantilla `reagenda_confirmada`
    con {{1}}=nombre {{2}}=doctor {{3}}=fecha nueva {{4}}=hora nueva."""
    return _enviar_plantilla(telefono, 'reagenda_confirmada',
                              [nombre, doctor_nombre, fecha_legible, hora])


def enviar_recordatorio_semana(telefono, nombre, doctor_nombre, fecha_legible, hora, id_agenda, fecha_iso=''):
    """id_agenda: se codifica como payload 'semana:{id_agenda}:{fecha_iso}' en
    los 3 botones (Confirmo/Reagendar/Anular) para que el webhook sepa a que
    cita responde el toque, que vino del recordatorio de 1 semana (IdStatus
    40968 al confirmar), y en que dia esta (Reagendar: DentiDesk no tiene
    'buscar por id', el backend necesita el dia para encontrarla de nuevo)."""
    return _enviar_plantilla(telefono, 'recordatorio_semana',
                              [nombre, doctor_nombre, fecha_legible, hora],
                              boton_payload=f'semana:{id_agenda}:{fecha_iso}', num_botones=3)


def enviar_recordatorio_dia(telefono, nombre, doctor_nombre, fecha_legible, hora, id_agenda, fecha_iso=''):
    """id_agenda: payload 'dia:{id_agenda}:{fecha_iso}' -- confirmar desde aqui
    usa el IdStatus generico 32180 (no el de 'semana')."""
    return _enviar_plantilla(telefono, 'recordatorio_dia',
                              [nombre, doctor_nombre, fecha_legible, hora],
                              boton_payload=f'dia:{id_agenda}:{fecha_iso}', num_botones=3)


def enviar_inasistencia_reagendar(telefono, nombre, fecha_legible, id_agenda, fecha_iso=''):
    """id_agenda: payload 'inasistencia:{id_agenda}:{fecha_iso}' en el unico
    boton (Reagendar)."""
    return _enviar_plantilla(telefono, 'inasistencia_reagendar', [nombre, fecha_legible],
                              boton_payload=f'inasistencia:{id_agenda}:{fecha_iso}', num_botones=1)


def enviar_primera_consulta(telefono, nombre, doctor_nombre, fecha_legible, hora, video_url):
    """video_url: link publico y estable al video (Meta lo descarga en cada
    envio; no acepta subir el archivo por request). Ej: alojarlo en el propio
    sitio -> https://ortodonciarichard.cl/images/video-primera-consulta.mp4"""
    return _enviar_plantilla(telefono, 'primera_consulta',
                              [nombre, doctor_nombre, fecha_legible, hora],
                              header_video_url=video_url)


def enviar_recordatorio_control(telefono, nombre, doctor, fecha_legible, id_agenda, fecha_iso=''):
    """Recordatorio de control (recaptacion), disparado a mano desde el
    asistente F2. Plantilla 'recordatorio_control_dr_vial', {{1}}=nombre
    {{2}}=doctor {{3}}=fecha_legible. Botones MEZCLADOS (ver
    IDX_BOTON_AGENDAR_WA arriba): solo el quick-reply "Agendar por WhatsApp"
    lleva payload 'control:{id_agenda}:{fecha_iso}' (mismo formato
    tipo:id:fecha que recordatorio_semana/dia, para que webhook_wa.py lo
    parsee con el mismo split(':'))."""
    return _enviar_plantilla(telefono, 'recordatorio_control_dr_vial',
                              [nombre, doctor, fecha_legible],
                              boton_payload=f'control:{id_agenda}:{fecha_iso}',
                              num_botones=1, boton_indices=[IDX_BOTON_AGENDAR_WA])


def nombre_doctor_sin_titulo(doctor):
    """Quita un prefijo 'Dr.'/'Dra.' del nombre del profesional. La plantilla
    'encuesta_satisfaccion' ya dice 'con el Dr. {{2}}', asi que la variable
    debe ir SIN titulo para no duplicarlo ('el Dr. Dr. Octavio'). DentiDesk
    suele devolver el nombre sin titulo (ProfessionalName = 'Octavio Del
    Real'), pero esto lo hace robusto igual. ⚠️ La plantilla asume doctor
    HOMBRE (los 4 especialistas lo son); si algun dia atiende una profesional
    mujer, 'el Dr.' la trataria mal y habria que resolver el articulo/titulo
    aparte (el genero NO se infiere del nombre)."""
    d = (doctor or '').strip()
    low = d.lower()
    for pref in ('dra.', 'dra ', 'dr.', 'dr '):
        if low.startswith(pref):
            return d[len(pref):].strip()
    return d


def enviar_nps(telefono, nombre, cuando, doctor, id_agenda, fecha_iso=''):
    """Encuesta de satisfaccion (NPS) tras una atencion. Plantilla
    'encuesta_satisfaccion' (es_CL), {{1}}=nombre {{2}}=cuando ('hoy'/'ayer',
    lo calcula el server segun si el envio cae el mismo dia o al siguiente)
    {{3}}=doctor (SIN titulo: la plantilla ya pone 'el Dr.'). 3 botones
    quick-reply (Excelente/Buena/Puede mejorar) que llevan el payload
    'nps:{id_agenda}:{fecha_iso}' -- mismo formato tipo:id:fecha que
    recordatorio_semana/dia, para que webhook_wa.py lo parsee con el mismo
    split(':') y sepa a que atencion corresponde la respuesta. El pedido de
    resena (a los promotores) NO va aca: se manda como texto libre desde el
    webhook cuando el paciente toca 'Excelente' (dentro de la ventana de 24h
    que abre ese toque)."""
    return _enviar_plantilla(telefono, 'encuesta_satisfaccion',
                              [nombre, cuando or 'hoy', nombre_doctor_sin_titulo(doctor)],
                              boton_payload=f'nps:{id_agenda}:{fecha_iso}', num_botones=3)


# ── Mensaje libre (respuesta dentro de la ventana de 24h) ───────────────────

def enviar_texto_libre(telefono, texto):
    """Mensaje de texto SIN plantilla -- solo valido dentro de la ventana de
    24h que se abre cuando el paciente responde o toca un boton. Usado por
    el webhook para agradecer un Confirmo/Anular o acusar recibo de un
    Reagendar."""
    payload = {
        'messaging_product': 'whatsapp',
        'to': _normalizar_telefono(telefono),
        'type': 'text',
        'text': {'body': texto},
    }
    return _post(payload)


# ── Estado / salud ────────────────────────────────────────────────────────

def verificar_estado():
    """Chequeo liviano (sin enviar ningun mensaje): confirma que WA_TOKEN y
    WA_PHONE_NUMBER_ID son validos haciendo un GET al propio numero. Detecta
    tokens vencidos/invalidos (p.ej. el error 190 OAuthException) sin gastar
    cuota de mensajes ni molestar a ningun paciente."""
    cfg = _config()
    if not cfg['enabled']:
        return {'configurado': False, 'conectado': False, 'error': 'WA_ENABLED no esta activo'}
    if not cfg['token'] or not cfg['phone_number_id']:
        return {'configurado': False, 'conectado': False, 'error': 'Faltan WA_TOKEN / WA_PHONE_NUMBER_ID'}
    if requests is None:
        return {'configurado': True, 'conectado': False, 'error': "Falta 'requests' (pip install requests)"}

    url = f"https://graph.facebook.com/{cfg['api_version']}/{cfg['phone_number_id']}"
    params = {'fields': 'display_phone_number,quality_rating'}
    headers = {'Authorization': f"Bearer {cfg['token']}"}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except requests.exceptions.RequestException as e:
        return {'configurado': True, 'conectado': False, 'error': f'Error de red al llamar a Meta: {e}'}

    if resp.status_code >= 400:
        return {'configurado': True, 'conectado': False, 'error': f'Meta respondio {resp.status_code}: {resp.text[:300]}'}
    try:
        data = resp.json()
    except ValueError:
        return {'configurado': True, 'conectado': False, 'error': f'Respuesta invalida de Meta (no es JSON): {resp.text[:300]}'}

    return {
        'configurado': True, 'conectado': True,
        'numero': data.get('display_phone_number'),
        'calidad': data.get('quality_rating'),
    }
