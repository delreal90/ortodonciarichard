"""
webhook_wa.py - Procesa eventos entrantes del webhook de WhatsApp Cloud API.

Cuando el paciente toca un boton de una plantilla (Confirmo/Reagendar/Anular),
Meta manda un evento POST con type='button' y {payload, text}. El payload
trae "{tipo}:{id_agenda}" (tipo = semana/dia/inasistencia -- ver wa_cloud.py),
codificado al ENVIAR el recordatorio. El texto del boton (siempre igual al
aprobado en la plantilla) dice la ACCION: "Confirmo" / "Anular" / "Reagendar".

Reglas de negocio:
  - Confirmo   -> actualizar_estado_cita() con el IdStatus segun el origen
                  (40968 si vino del recordatorio de 1 semana, 32180 si no)
                  + mensaje de agradecimiento al paciente.
  - Anular     -> actualizar_estado_cita() con IdStatus 2122 (Hora Cancelada)
                  + mensaje al paciente + aviso INMEDIATO a recepcion por email.
  - Reagendar  -> manda al paciente el link de la agenda online con el id (y la
                  fecha) de su cita vieja codificados en el hash (#reagendar=
                  <id>&fecha=<fecha>). El frontend usa esos datos para precargar
                  doctor + motivo (/api/agenda/reagendar-info) y saltar directo
                  a elegir hora -- el paciente no puede cambiar de doctor ni de
                  motivo. Cuando complete la reserva nueva ahi, el backend marca
                  la cita vieja como "Re-agendado" (2132) y la mueve fuera de
                  horario (libera su bloque original). La cita vieja se mantiene
                  vigente hasta que confirme la nueva (asi no queda sin hora si
                  abandona el flujo).

  - Agendar por WhatsApp (recaptacion) -> viene del recordatorio de control
                  (ver recaptacion.py), payload "control:{id_agenda}:{fecha}".
                  Responde texto libre + link a la agenda online (cita
                  NUEVA, no precarga nada), marca el envio como respondido
                  (recaptacion.marcar_respondio) y avisa a recepcion. NO toca
                  DentiDesk -- no hay cita vigente que actualizar.

Ignora cualquier evento que no sea un toque de boton (mensajes de texto libre,
recibos de entrega/lectura, etc.) -- esos se ven manualmente en la bandeja de
Meta Business Suite, no los procesa este bot.
"""

import logging
from datetime import datetime

import dentidesk
import notify
import nps
import recaptacion

log = logging.getLogger(__name__)

ACCION_CONFIRMAR = 'Confirmo'
ACCION_ANULAR = 'Anular'
ACCION_REAGENDAR = 'Reagendar'
# Texto EXACTO del quick-reply de la plantilla 'recordatorio_control_dr_vial'
# (recaptacion) -- igual que las otras 3 acciones, Meta manda siempre el
# mismo texto aprobado, asi que se identifica por texto, no por indice.
ACCION_AGENDAR_WA = 'Agendar por WhatsApp'

# Texto EXACTO de los 3 quick-reply de la plantilla 'encuesta_satisfaccion' (NPS).
# Meta reenvia siempre el texto aprobado -- ademas estos botones traen tipo='nps'
# en el payload, asi que se despachan por tipo (ver _procesar_mensaje) y no por
# texto (ver _nps). SIN EMOJI: Meta NO permite emoji en los botones quick-reply
# de plantilla (a diferencia del cuerpo del mensaje). Si algun dia se editan las
# etiquetas en Meta, hay que actualizar estos 3 strings para que sigan calzando.
NPS_EXCELENTE = 'Excelente'
NPS_BUENA = 'Buena'
NPS_MEJORAR = 'Puede mejorar'

# Link a la agenda online con el id (y la fecha) de la cita vieja codificados
# en el hash. El frontend usa la fecha para pedirle a /api/agenda/reagendar-info
# los datos de la cita (DentiDesk no tiene 'buscar por id', hay que saber el
# dia). Al completar la reserva nueva, el backend marca esa cita vieja como
# "Re-agendado" y avisa al paciente (ver /api/agenda/reservar-reagenda).
URL_REAGENDA = 'https://www.ortodonciarichard.cl/#reagendar={id_agenda}&fecha={fecha}'


def procesar_evento(payload, cfg):
    """Punto de entrada: recorre el payload completo del webhook (puede traer
    varios mensajes en un mismo POST) y despacha cada toque de boton."""
    procesados = 0
    for entry in payload.get('entry', []) or []:
        for change in entry.get('changes', []) or []:
            valor = change.get('value', {}) or {}
            # Nombre de perfil de WhatsApp por wa_id -- fallback para el email de
            # anulacion cuando no se puede resolver el nombre desde DentiDesk.
            contactos = {}
            for contacto in valor.get('contacts', []) or []:
                wa_id = contacto.get('wa_id')
                nombre_perfil = (contacto.get('profile') or {}).get('name')
                if wa_id and nombre_perfil:
                    contactos[wa_id] = nombre_perfil
            for msg in valor.get('messages', []) or []:
                try:
                    if _procesar_mensaje(msg, cfg, contactos):
                        procesados += 1
                except Exception as e:
                    log.error('Error procesando mensaje de webhook: %s', e)
    return {'ok': True, 'procesados': procesados}


def _procesar_mensaje(msg, cfg, contactos=None):
    if msg.get('type') != 'button':
        return False  # texto libre / estados de entrega: no los maneja el bot

    boton = msg.get('button') or {}
    texto = (boton.get('text') or '').strip()
    crudo = (boton.get('payload') or '').strip()
    telefono = msg.get('from', '')

    # Formato 'tipo:id_agenda:fecha' (fecha agregada 2026-07-08; botones
    # enviados ANTES de ese cambio solo traen 'tipo:id_agenda' -- partes[2]
    # queda vacio y _reagendar cae de vuelta al link sin fecha).
    partes = crudo.split(':')
    tipo = partes[0] if partes else ''
    id_agenda = partes[1] if len(partes) > 1 else ''
    fecha = partes[2] if len(partes) > 2 else ''
    if not id_agenda:
        log.warning('Boton sin id_agenda en el payload: %r', crudo)
        return False

    perfil_nombre = (contactos or {}).get(telefono, '')

    if texto == ACCION_CONFIRMAR:
        _confirmar(id_agenda, tipo, telefono, cfg)
    elif texto == ACCION_ANULAR:
        _anular(id_agenda, telefono, cfg, fecha, perfil_nombre)
    elif texto == ACCION_REAGENDAR:
        _reagendar(id_agenda, telefono, cfg, fecha)
    elif texto == ACCION_AGENDAR_WA:
        _agendar_por_whatsapp(id_agenda, telefono, cfg, fecha, perfil_nombre)
    elif tipo == 'nps':
        _nps(texto, id_agenda, telefono, cfg, fecha, perfil_nombre)
    else:
        log.info('Boton no manejado: %r (cita %s)', texto, id_agenda)
        return False
    return True


def _actualizar_dentidesk(id_agenda, id_status, cfg, etiqueta):
    try:
        dentidesk.actualizar_estado_cita(id_agenda, id_status, cfg)
    except Exception as e:
        log.error('No se pudo %s la cita %s en DentiDesk: %s', etiqueta, id_agenda, e)


def _confirmar(id_agenda, tipo, telefono, cfg):
    dd = cfg['dentidesk']
    id_status = (dd.get('id_status_confirmado_semana') if tipo == 'semana'
                 else dd.get('id_status_confirmado_whatsapp'))
    if id_status:
        _actualizar_dentidesk(id_agenda, id_status, cfg, 'confirmar')
    else:
        log.warning('id_status_confirmado_* no configurado -- no se actualiza DentiDesk (cita %s)', id_agenda)
    notify.enviar_texto_libre(telefono, '¡Gracias! Su asistencia quedó confirmada. Le esperamos.')


def _anular(id_agenda, telefono, cfg, fecha='', perfil_nombre=''):
    id_status = cfg['dentidesk'].get('id_status_cancelado')
    if id_status:
        _actualizar_dentidesk(id_agenda, id_status, cfg, 'anular')
    else:
        log.warning('id_status_cancelado no configurado -- no se actualiza DentiDesk (cita %s)', id_agenda)
    # Nombre del paciente: DentiDesk (autoritativo, necesita fecha) y si no,
    # el nombre de perfil de WhatsApp del propio evento (fallback botones viejos).
    nombre = ''
    if fecha:
        try:
            f = datetime.strptime(fecha, '%Y-%m-%d').date()
            c = dentidesk.info_cita(cfg, id_agenda, f)
            if c:
                nombre = (c.get('PatientName') or '').strip()
        except Exception as e:
            log.warning('No se pudo obtener el nombre de la cita %s: %s', id_agenda, e)
    if not nombre:
        nombre = (perfil_nombre or '').strip()
    notify.enviar_texto_libre(telefono, 'Su hora quedó anulada. Si desea reagendar, puede escribirnos por este mismo medio.')
    notify.avisar_recepcion_anulacion(id_agenda, telefono, nombre)


def _reagendar(id_agenda, telefono, cfg, fecha=''):
    """Le manda al paciente el link de la agenda online con el id (y la fecha)
    de su cita vieja codificados. El frontend usa esos datos para precargar
    doctor + motivo (/api/agenda/reagendar-info) y saltar directo a elegir
    hora. Cuando complete la reserva nueva ahi, /api/agenda/reservar-reagenda
    marca esta cita vieja como 'Re-agendado' y le avisa (ver ese endpoint).
    No toca DentiDesk aca todavia -- la cita vieja sigue vigente hasta que el
    paciente concrete la nueva (asi no queda sin hora si abandona el flujo).

    fecha vacia (botones enviados antes de este cambio): el link igual abre
    con el id -- el frontend simplemente no puede precargar doctor/motivo y
    cae de vuelta al wizard completo (pidiendole todo al paciente)."""
    link = URL_REAGENDA.format(id_agenda=id_agenda, fecha=fecha)
    notify.enviar_texto_libre(
        telefono,
        'Para reagendar su hora tiene dos opciones:\n\n'
        '1️⃣ *Escríbanos por aquí mismo* y una persona de nuestro equipo lo '
        'coordina con usted. Le responderemos a la brevedad.\n\n'
        '2️⃣ *Elegir un nuevo horario usted mismo*, a cualquier hora, en este '
        'enlace:\n' + link + '\n\n'
        'Su hora actual se mantiene agendada hasta que confirme la nueva. 🦷'
    )


def _agendar_por_whatsapp(id_agenda, telefono, cfg, fecha='', perfil_nombre=''):
    """El paciente toco 'Agendar por WhatsApp' desde el recordatorio de
    control -- NO toca DentiDesk (no hay cita que actualizar, la de origen ya
    quedo atras). Responde texto libre avisando que el equipo lo contactara;
    NO le reofrece la agenda online (ver comentario abajo).

    id_agenda aca es el de la cita VIEJA (la que disparo el recordatorio),
    solo sirve para resolver nombre/RUT via info_cita -- no se actualiza."""
    rut = ''
    nombre = perfil_nombre or ''
    if fecha:
        try:
            f = datetime.strptime(fecha, '%Y-%m-%d').date()
            c = dentidesk.info_cita(cfg, id_agenda, f)
            if c:
                rut = (c.get('PatientDocument') or '').strip()
                nombre = (c.get('PatientName') or '').strip() or nombre
        except Exception as e:
            log.warning('No se pudo obtener rut/nombre de la cita %s: %s', id_agenda, e)

    # A proposito NO se ofrece de nuevo la agenda online: el paciente acaba de
    # elegir el canal humano teniendo el boton "Agendar Online" justo al lado.
    # Repetirle el link suena insistente y contradice lo que acaba de pedir
    # (a diferencia de _reagendar, donde el paciente NO eligio canal).
    notify.enviar_texto_libre(
        telefono,
        '¡Qué bueno! Una persona de nuestro equipo lo contactará a la brevedad '
        'para coordinar la hora de su control.\n\n'
        'Si prefiere, puede escribirnos por aquí mismo con los días y horarios '
        'que le acomoden. ¡Le esperamos! 🦷'
    )
    if rut:
        recaptacion.marcar_respondio(rut)
    notify.avisar_recepcion_interes_control(nombre, telefono)


def _nps(texto, id_agenda, telefono, cfg, fecha='', perfil_nombre=''):
    """El paciente toco uno de los 3 botones de la encuesta de satisfaccion
    (NPS) de la plantilla 'encuesta_satisfaccion'. El toque abre la ventana de
    24h igual que cualquier boton -- por eso las respuestas de aca en adelante
    van como texto libre (notify.enviar_texto_libre), no como plantilla nueva.

    id_agenda/fecha son de la cita que disparo la encuesta, solo sirven para
    resolver nombre/RUT/doctor via info_cita (mismo patron que _anular) -- no
    se actualiza DentiDesk, la cita ya fue atendida.

    El pedido de reseña (Google Business Profile) SOLO se le hace al promotor,
    y ahi si se incluye el nombre del doctor: un GBP tiene un solo link de
    reseña para toda la clinica, Google no separa reseñas por profesional, asi
    que pedirsela a un pasivo o detractor arriesga una reseña mala en el unico
    lugar publico -- al promotor en cambio conviene reforzarle CON quien lo
    atendio, para que la reseña lo mencione."""
    nombre = ''
    rut = ''
    doctor = ''
    if fecha:
        try:
            f = datetime.strptime(fecha, '%Y-%m-%d').date()
            c = dentidesk.info_cita(cfg, id_agenda, f)
            if c:
                nombre = (c.get('PatientName') or '').strip()
                rut = dentidesk.limpiar_rut(c.get('PatientDocument') or '')
                doctor = (c.get('ProfessionalName') or '').strip()
        except Exception as e:
            log.warning('No se pudo obtener datos de la cita %s para NPS: %s', id_agenda, e)
    nombre = nombre or perfil_nombre
    rut = rut or ''
    doctor = doctor or ''

    review_url = nps.load_config().get('review_url', '')

    if texto == NPS_EXCELENTE:
        nps.registrar_respuesta(rut, 'promotor', doctor)
        notify.responder_nps_promotor(telefono, nombre, doctor, review_url)
    elif texto == NPS_BUENA:
        nps.registrar_respuesta(rut, 'pasivo', doctor)
        notify.responder_nps_pasivo(telefono, nombre)
    elif texto == NPS_MEJORAR:
        nps.registrar_respuesta(rut, 'detractor', doctor)
        notify.responder_nps_detractor(telefono, nombre)
        notify.avisar_recepcion_detractor(nombre, telefono, doctor, id_agenda, fecha)
    else:
        log.info('Boton NPS no manejado: %r (cita %s)', texto, id_agenda)
