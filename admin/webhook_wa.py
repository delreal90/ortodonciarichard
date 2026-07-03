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
  - Reagendar  -> SOLO acuse de recibo al paciente + aviso a recepcion para
                  gestion manual. Nada de DentiDesk ni logica de horarios
                  todavia (queda para una fase futura).

Ignora cualquier evento que no sea un toque de boton (mensajes de texto libre,
recibos de entrega/lectura, etc.) -- esos se ven manualmente en la bandeja de
Meta Business Suite, no los procesa este bot.
"""

import logging

import dentidesk
import notify

log = logging.getLogger(__name__)

ACCION_CONFIRMAR = 'Confirmo'
ACCION_ANULAR = 'Anular'
ACCION_REAGENDAR = 'Reagendar'


def procesar_evento(payload, cfg):
    """Punto de entrada: recorre el payload completo del webhook (puede traer
    varios mensajes en un mismo POST) y despacha cada toque de boton."""
    procesados = 0
    for entry in payload.get('entry', []) or []:
        for change in entry.get('changes', []) or []:
            valor = change.get('value', {}) or {}
            for msg in valor.get('messages', []) or []:
                try:
                    if _procesar_mensaje(msg, cfg):
                        procesados += 1
                except Exception as e:
                    log.error('Error procesando mensaje de webhook: %s', e)
    return {'ok': True, 'procesados': procesados}


def _procesar_mensaje(msg, cfg):
    if msg.get('type') != 'button':
        return False  # texto libre / estados de entrega: no los maneja el bot

    boton = msg.get('button') or {}
    texto = (boton.get('text') or '').strip()
    crudo = (boton.get('payload') or '').strip()
    telefono = msg.get('from', '')

    tipo, _, id_agenda = crudo.partition(':')
    if not id_agenda:
        log.warning('Boton sin id_agenda en el payload: %r', crudo)
        return False

    if texto == ACCION_CONFIRMAR:
        _confirmar(id_agenda, tipo, telefono, cfg)
    elif texto == ACCION_ANULAR:
        _anular(id_agenda, telefono, cfg)
    elif texto == ACCION_REAGENDAR:
        _reagendar_placeholder(id_agenda, telefono, cfg)
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


def _anular(id_agenda, telefono, cfg):
    id_status = cfg['dentidesk'].get('id_status_cancelado')
    if id_status:
        _actualizar_dentidesk(id_agenda, id_status, cfg, 'anular')
    else:
        log.warning('id_status_cancelado no configurado -- no se actualiza DentiDesk (cita %s)', id_agenda)
    notify.enviar_texto_libre(telefono, 'Su hora quedó anulada. Si desea reagendar, puede escribirnos por este mismo medio.')
    notify.avisar_recepcion_anulacion(id_agenda, telefono)


def _reagendar_placeholder(id_agenda, telefono, cfg):
    """Fase futura: por ahora no toca DentiDesk ni ofrece horarios -- solo
    acusa recibo y deja que recepcion lo gestione a mano."""
    notify.enviar_texto_libre(
        telefono,
        'Recibimos su solicitud para reagendar. Nos pondremos en contacto a la brevedad para coordinar un nuevo horario.'
    )
    notify.avisar_recepcion_reagendar(id_agenda, telefono)
