"""
notify.py - Confirmacion al paciente tras agendar (Ortodoncia Richard)

Prioridad:
  1. Email via SMTP (Gmail App Password) -> HTML + .ics adjunto
  2. Fallback: WhatsApp Cloud API oficial (plantilla 'confirmacion_hora'; ver wa_cloud.py)

El .ics adjunto es reconocido automaticamente por Gmail, iOS Mail, Outlook y
la mayoria de apps de calendario: aparece como boton "Agregar al calendario".
El fallback de WhatsApp es solo texto (las plantillas de Meta no llevan adjuntos).

Variables de entorno requeridas (configurar en Render):
  SMTP_USER  — email que envia (ej: recepcion@ortodonciarichard.cl)
  SMTP_PASS  — App Password de Gmail (16 caracteres, sin espacios)
  WA_ENABLED, WA_TOKEN, WA_PHONE_NUMBER_ID — ver wa_cloud.py
"""

import os
import ssl
import smtplib
import logging
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase

log = logging.getLogger(__name__)

from scheduling import generar_ics, load_config
import wa_cloud
import pacientes
import nps
import psq


# ── Email (primario) ─────────────────────────────────────────────────────────

# ── Layout de los emails al paciente ─────────────────────────────────────────
#
# Los 5 correos que le llegan al paciente (confirmacion de hora, consentimiento
# para firmar, copia del consentimiento firmado, formulario de seguro y
# recordatorio de control dental) comparten el mismo sobre: fondo #f0f5fb, tarjeta
# blanca de 560px, cabecera navy con la marca en dorado, y pie navy.
#
# Estaba copiado entero en las 5 funciones. Cada retoque de marca (un color, la
# direccion, el telefono) habia que hacerlo 5 veces, y bastaba olvidar una para
# que un paciente recibiera un correo con la direccion vieja.
#
# ⚠️ Los estilos van EN LINEA a proposito: los clientes de correo (Gmail, Outlook)
# descartan las hojas de estilo y buena parte de los selectores CSS. Y la
# maquetacion con <table> anidadas tampoco es un descuido: es lo unico que Outlook
# renderiza igual que el resto.

CLINICA_DIRECCION = 'Paul Harris 10.349, of. 305, Las Condes, Santiago'
CLINICA_TELEFONO = '+56 2 2217 3499'
CLINICA_TEL_LINK = 'tel:+56222173499'
CLINICA_WHATSAPP = 'https://wa.me/56933558189'
CLINICA_WEB = 'https://www.ortodonciarichard.cl'

# Las 3 variantes de pie que existen hoy. Se mantienen tal cual estaban: cambiar
# cual usa cada correo es una decision de contenido, no de refactor.
PIE_SOLO_DIRECCION = 'solo_direccion'
PIE_CON_WEB = 'con_web'
PIE_COMPLETO = 'completo'          # direccion + telefono + web


def _pie(variante):
    if variante == PIE_SOLO_DIRECCION:
        return f'Ortodoncia Richard · {CLINICA_DIRECCION}'
    if variante == PIE_CON_WEB:
        return (f'Ortodoncia Richard · {CLINICA_DIRECCION}<br>'
                f'<a href="{CLINICA_WEB}" style="color:#C9A84C;text-decoration:none;">'
                f'www.ortodonciarichard.cl</a>')
    return (f'Ortodoncia Richard · {CLINICA_DIRECCION}<br>'
            f'📞 <a href="{CLINICA_TEL_LINK}" style="color:#C9A84C;text-decoration:none;">'
            f'{CLINICA_TELEFONO}</a> &nbsp;|&nbsp; '
            f'<a href="{CLINICA_WEB}" style="color:#C9A84C;text-decoration:none;">'
            f'www.ortodonciarichard.cl</a>')


def contacto_inline(prefijo='Si tienes dudas, contáctanos:'):
    """La linea de contacto que va dentro del cuerpo de varios correos."""
    return (f'{prefijo} 📞 <a href="{CLINICA_TEL_LINK}" style="color:#1A2E4A;">'
            f'{CLINICA_TELEFONO}</a> &nbsp;|&nbsp; '
            f'<a href="{CLINICA_WHATSAPP}" style="color:#1A2E4A;">WhatsApp</a>')


def _email_layout(titulo, cuerpo, pie=PIE_SOLO_DIRECCION, title_tag=None):
    """El sobre comun. `cuerpo` es el HTML que va dentro de la tarjeta blanca;
    `titulo` es el encabezado sobre fondo navy."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_tag or titulo}</title></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr>
    <td style="background:#1A2E4A;padding:28px 32px;text-align:center;">
      <p style="margin:0;color:#C9A84C;font-size:13px;letter-spacing:2px;text-transform:uppercase;">Ortodoncia Richard</p>
      <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">{titulo}</h1>
    </td>
  </tr>
  <tr>
    <td style="padding:32px 32px 24px;">
{cuerpo}
    </td>
  </tr>
  <tr>
    <td style="background:#1A2E4A;padding:20px 32px;text-align:center;">
      <p style="margin:0;color:#8fa8c8;font-size:12px;">{_pie(pie)}</p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _html_confirmacion(cita, reagenda=False):
    """HTML del cuerpo del email de confirmacion. reagenda=True cambia el titulo
    y la bajada para un reagendamiento ('tu hora fue reagendada con exito')."""
    titulo = '¡Tu hora fue reagendada con éxito!' if reagenda else '¡Tu hora quedó agendada!'
    bajada = ('Hola <strong>' + cita['nombre'] + '</strong>, tu hora anterior quedó anulada '
              'y tu nueva cita quedó agendada con los siguientes datos:') if reagenda else \
             ('Hola <strong>' + cita['nombre'] + '</strong>, confirmamos tu cita con los siguientes datos:')
    cuerpo = f"""      <p style="margin:0 0 24px;color:#4A5568;font-size:15px;">{bajada}</p>

      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <tr style="background:#f8fafc;">
          <td style="padding:12px 16px;color:#1A2E4A;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;width:40%;">📅 Fecha</td>
          <td style="padding:12px 16px;color:#1A2535;font-size:15px;font-weight:600;">{cita['fecha_legible']}</td>
        </tr>
        <tr>
          <td style="padding:12px 16px;color:#1A2E4A;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;border-top:1px solid #e2e8f0;">🕐 Hora</td>
          <td style="padding:12px 16px;color:#1A2535;font-size:15px;border-top:1px solid #e2e8f0;">{cita['hora']} hrs</td>
        </tr>
        <tr style="background:#f8fafc;">
          <td style="padding:12px 16px;color:#1A2E4A;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;border-top:1px solid #e2e8f0;">👨‍⚕️ Doctor</td>
          <td style="padding:12px 16px;color:#1A2535;font-size:15px;border-top:1px solid #e2e8f0;">{cita['doctor_nombre']}</td>
        </tr>
        <tr>
          <td style="padding:12px 16px;color:#1A2E4A;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;border-top:1px solid #e2e8f0;">📋 Motivo</td>
          <td style="padding:12px 16px;color:#1A2535;font-size:15px;border-top:1px solid #e2e8f0;">{cita['motivo_label']}</td>
        </tr>
        <tr style="background:#f8fafc;">
          <td style="padding:12px 16px;color:#1A2E4A;font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;border-top:1px solid #e2e8f0;">📍 Dirección</td>
          <td style="padding:12px 16px;color:#1A2535;font-size:15px;border-top:1px solid #e2e8f0;">{cita['direccion']}</td>
        </tr>
      </table>

      <!-- CTA calendario -->
      <div style="margin:24px 0;padding:16px;background:#f0f5fb;border-radius:8px;border-left:4px solid #C9A84C;">
        <p style="margin:0;color:#1A2E4A;font-size:14px;">
          <strong>📆 Agregar al calendario</strong><br>
          <span style="color:#4A5568;">Adjuntamos un archivo <code>.ics</code> — ábrelo para agregar esta cita directamente a tu calendario (Google Calendar, iPhone, Outlook, etc.).</span>
        </p>
      </div>

      <p style="margin:0;color:#4A5568;font-size:14px;">
        Si necesitas reagendar o cancelar, comunícate con nosotros:<br>
        📞 <a href="{CLINICA_TEL_LINK}" style="color:#1A2E4A;">{CLINICA_TELEFONO}</a> &nbsp;|&nbsp;
        💬 <a href="{CLINICA_WHATSAPP}" style="color:#1A2E4A;">WhatsApp</a>
      </p>"""
    return _email_layout(titulo, cuerpo, pie=PIE_CON_WEB,
                         title_tag='Confirmación de hora')


def _enviar_email_smtp(cita, ics, reagenda=False):
    """Email principal: smtplib con .ics adjunto (Gmail App Password)."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (cita.get('email') or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return False

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    asunto = 'Hora reagendada' if reagenda else 'Confirmación de hora'
    msg['Subject'] = f"{asunto} — {cita['fecha_legible']} {cita['hora']} hrs"
    msg['Reply-To'] = smtp_user

    msg.attach(MIMEText(_html_confirmacion(cita, reagenda=reagenda), 'html', 'utf-8'))

    # .ics como adjunto — Gmail/iOS/Outlook muestran "Agregar al calendario"
    ics_part = MIMEBase('text', 'calendar', method='PUBLISH', charset='utf-8')
    ics_part.set_payload(ics.encode('utf-8'))
    ics_part['Content-Disposition'] = 'attachment; filename="cita-ortodoncia-richard.ics"'
    msg.attach(ics_part)

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo()
            s.starttls(context=ctx)
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [dest], msg.as_bytes())
        log.info('Email enviado a %s', dest)
        return True
    except Exception as e:
        log.error('SMTP error: %s', e)
        return False


# ── WhatsApp (fallback, Cloud API oficial) ───────────────────────────────────

def _enviar_whatsapp(cita, ics, reagenda=False, primera=False):
    """Fallback: WhatsApp Cloud API con la plantilla 'confirmacion_hora'
    (o 'reagenda_confirmada' si reagenda=True). Sin adjunto (las plantillas
    de Meta no llevan .ics); solo texto. Devuelve (ok: bool, error: str|None)
    — el error se propaga hasta F2 para que la secretaria vea la causa real
    (token vencido, sin telefono, etc.) en vez de un mensaje generico.

    primera=True: primero intenta la plantilla especial de primera consulta
    (con video de bienvenida); si falla, cae a la confirmacion normal."""
    if not cita.get('telefono'):
        return False, 'La cita no tiene teléfono registrado'

    # Primera consulta: plantilla propia con video de bienvenida. Si falla (p.ej.
    # video no disponible), cae a la confirmacion normal -- el paciente nunca
    # queda sin aviso por WhatsApp.
    if primera:
        try:
            fecha_iso = cita['fecha'].isoformat() if hasattr(cita.get('fecha'), 'isoformat') else ''
            resultado = wa_cloud.enviar_primera_consulta(
                telefono=cita['telefono'], nombre=cita['nombre'],
                doctor_nombre=cita['doctor_nombre'],
                fecha_legible=cita['fecha_legible'], hora=cita['hora'],
                id_agenda=str(cita.get('id_agenda') or ''),
                fecha_iso=fecha_iso,
            )
            if resultado.get('ok'):
                return True, None
            log.warning('primera_consulta no confirmo el envio; uso confirmacion_hora')
        except wa_cloud.WhatsAppCloudError as e:
            log.warning('primera_consulta fallo (%s); uso confirmacion_hora', e)

    envio = wa_cloud.enviar_reagenda_confirmada if reagenda else wa_cloud.enviar_confirmacion_hora
    try:
        resultado = envio(
            telefono=cita['telefono'],
            nombre=cita['nombre'],
            doctor_nombre=cita['doctor_nombre'],
            fecha_legible=cita['fecha_legible'],
            hora=cita['hora'],
        )
        if resultado.get('ok'):
            return True, None
        return False, 'WhatsApp no confirmó el envío'
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error: %s', e)
        return False, str(e)


# ── Recordatorios / inasistencia (scheduler de server.py) ───────────────────
# A diferencia de _enviar_whatsapp() (fallback de la confirmacion), estas 3
# son el UNICO canal para estos avisos -- no hay version por email.

def enviar_recordatorio_semana(cita):
    """cita: nombre, telefono, doctor_nombre, fecha_legible, hora, id_agenda, fecha."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_recordatorio_semana(
            telefono=cita['telefono'], nombre=cita['nombre'],
            doctor_nombre=cita['doctor_nombre'],
            fecha_legible=cita['fecha_legible'], hora=cita['hora'],
            id_agenda=cita['id_agenda'], fecha_iso=cita.get('fecha', ''),
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (recordatorio_semana): %s', e)
        return {'ok': False, 'error': str(e)}


def enviar_recordatorio_dia(cita):
    """cita: nombre, telefono, doctor_nombre, fecha_legible, hora, id_agenda,
    fecha, rut (opcional, para el saludo por genero)."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        # La plantilla dice "Estimad{{1}}," -> {{1}} lleva el sufijo de genero
        # PEGADO al nombre: "a Maria" / "o Juan" / "o/a Sofia". pacientes.saludo
        # NUNCA adivina por el nombre: si la ficha no trae genero da 'o/a'.
        saludo_nombre = f"{pacientes.saludo(cita.get('rut') or '')} {cita['nombre']}"
        r = wa_cloud.enviar_recordatorio_dia(
            telefono=cita['telefono'], nombre=saludo_nombre,
            doctor_nombre=cita['doctor_nombre'],
            fecha_legible=cita['fecha_legible'], hora=cita['hora'],
            id_agenda=cita['id_agenda'], fecha_iso=cita.get('fecha', ''),
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (recordatorio_dia): %s', e)
        return {'ok': False, 'error': str(e)}


def enviar_recordatorio_control(cita):
    """Recordatorio de control (recaptacion de pacientes que dejaron de venir),
    disparado a mano desde el asistente F2. cita: nombre, telefono,
    doctor_nombre, fecha_legible, fecha, id_agenda (de la ULTIMA cita del
    paciente, que es de donde salen todos los datos).

    Igual que _enviar_whatsapp() (fallback de la confirmacion): si la
    plantilla dedicada 'recordatorio_control_dr_vial' todavia no existe o no
    esta aprobada en Meta, cae de vuelta a 'conversacion_general' con un
    motivo libre -- mismo patron que _enviar_whatsapp_consentimiento(), para
    no dejar de enviar mientras se aprueba."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_recordatorio_control(
            telefono=cita['telefono'], nombre=cita['nombre'],
            doctor=cita['doctor_nombre'], fecha_legible=cita['fecha_legible'],
            id_agenda=cita['id_agenda'], fecha_iso=cita.get('fecha', ''),
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.warning('recordatorio_control_dr_vial no disponible (%s); uso conversacion_general', e)
        try:
            motivo = (f"le corresponde su control con {cita['doctor_nombre']}. "
                      "Puede agendar hora directamente respondiendo por este medio.")
            r2 = wa_cloud.enviar_conversacion_general(cita['telefono'], cita['nombre'], motivo)
            return {'ok': bool(r2.get('ok'))}
        except wa_cloud.WhatsAppCloudError as e2:
            log.error('WhatsApp Cloud API error (recordatorio_control, fallback): %s', e2)
            return {'ok': False, 'error': str(e2)}


def avisar_recepcion_interes_control(nombre, telefono):
    """El paciente toco 'Agendar por WhatsApp' desde el recordatorio de
    control -- avisar de inmediato para que recepcion le conteste y coordine
    la hora (mismo patron que avisar_recepcion_anulacion)."""
    filas = _fila('Paciente', nombre) + _fila('Teléfono', telefono)
    html = _aviso_recepcion_html('Un paciente quiere agendar su control por WhatsApp', filas)
    return _enviar_email_recepcion(f'Interés en agendar control — {nombre or telefono}', html)


def enviar_inasistencia(cita):
    """cita: nombre, telefono, fecha_legible, id_agenda, fecha."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_inasistencia_reagendar(
            telefono=cita['telefono'], nombre=cita['nombre'],
            fecha_legible=cita['fecha_legible'], id_agenda=cita['id_agenda'],
            fecha_iso=cita.get('fecha', ''),
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (inasistencia_reagendar): %s', e)
        return {'ok': False, 'error': str(e)}


# ── Mensaje libre + avisos a recepcion (webhook: Confirmo/Anular/Reagendar) ──

def enviar_texto_libre(telefono, texto):
    """Respuesta libre al paciente tras tocar un boton (dentro de la ventana
    de 24h que ese mismo toque abre). No lanza si falla -- el webhook no debe
    caerse porque el mensaje de cortesia no salio; solo se loguea."""
    if not telefono:
        return {'ok': False, 'error': 'Sin telefono'}
    try:
        r = wa_cloud.enviar_texto_libre(telefono, texto)
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (texto libre): %s', e)
        return {'ok': False, 'error': str(e)}


def _aviso_recepcion_html(titulo, filas_html, etiqueta='WhatsApp — Aviso',
                          pie='Ortodoncia Richard · Recordatorios automáticos por WhatsApp'):
    """Sobre comun de los avisos internos a recepcion. `etiqueta`/`pie` traen
    por defecto el texto de WhatsApp porque casi todos estos avisos nacen de
    ahi; los que no (ej. consentimientos pendientes) pasan el suyo."""
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr><td style="background:#1A2E4A;padding:24px 32px;">
    <p style="margin:0;color:#C9A84C;font-size:12px;letter-spacing:2px;text-transform:uppercase">{etiqueta}</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:20px">{titulo}</h1>
  </td></tr>
  <tr><td style="padding:24px 32px;">
    <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
      <tbody>{filas_html}</tbody>
    </table>
  </td></tr>
  <tr><td style="background:#1A2E4A;padding:16px 32px;text-align:center;">
    <p style="margin:0;color:#8fa8c8;font-size:12px">{pie}</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def _fila(label, valor):
    return f"""<tr>
      <td style="padding:10px 14px;font-weight:700;color:#1A2E4A;white-space:nowrap;border-top:1px solid #e2e8f0">{label}</td>
      <td style="padding:10px 14px;color:#1A2535;border-top:1px solid #e2e8f0">{valor or '—'}</td>
    </tr>"""


def _enviar_email_recepcion(asunto, html):
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    if not smtp_user or not smtp_pass:
        return False
    msg = MIMEMultipart('mixed')
    msg['From'] = f'WhatsApp Ortodoncia Richard <{smtp_user}>'
    msg['To'] = smtp_user
    msg['Subject'] = asunto
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [smtp_user], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP aviso WhatsApp error: %s', e)
        return False


def enviar_reporte_evoluciones(asunto, html):
    """Reporte diario de revision de evoluciones (sistema local de las 6:15).
    Destinatario fijo por env var (no es un relay abierto): el endpoint que llama
    esto no acepta 'para' del cliente."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    destino = os.getenv('REPORTE_EVOLUCIONES_EMAIL', 'alberto@delreal.cl').strip()
    if not smtp_user or not smtp_pass:
        return False
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Revisión de Evoluciones <{smtp_user}>'
    msg['To'] = destino
    msg['Subject'] = asunto
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [destino], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP reporte evoluciones error: %s', e)
        return False


def enviar_reporte_semanal(asunto, html):
    """Reporte semanal de KPIs de negocio. Destinatario fijo por env var
    (REPORTE_SEMANAL_EMAIL, default alberto@delreal.cl) -- no es un relay abierto,
    el endpoint que lo llama no acepta 'para' del cliente. Mismo molde que
    enviar_reporte_evoluciones."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    destino = os.getenv('REPORTE_SEMANAL_EMAIL',
                        os.getenv('REPORTE_EVOLUCIONES_EMAIL', 'alberto@delreal.cl')).strip()
    if not smtp_user or not smtp_pass:
        return False
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Reporte Semanal Ortodoncia <{smtp_user}>'
    msg['To'] = destino
    msg['Subject'] = asunto
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [destino], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP reporte semanal error: %s', e)
        return False


def enviar_reporte_evoluciones_rodrigo(asunto, html):
    """Reporte diario de fichas SIN evolucion escrita del Dr. Rodrigo Oyonarte
    (mismo sistema que enviar_reporte_evoluciones, sin la seccion de
    oportunidades). Destinatario fijo por env var (no es un relay abierto):
    el endpoint que llama esto no acepta 'para' del cliente."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    destino = os.getenv('REPORTE_EVOLUCIONES_RODRIGO_EMAIL', 'royonarte@miuandes.cl').strip()
    if not smtp_user or not smtp_pass:
        return False
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Revisión de Evoluciones <{smtp_user}>'
    msg['To'] = destino
    msg['Subject'] = asunto
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [destino], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP reporte evoluciones Rodrigo error: %s', e)
        return False


def enviar_reporte_alineadores(asunto, html):
    """Reporte de pacientes con alineadores (Digitrack/Invisalign) con 9+ meses
    de tratamiento agendados para el dia siguiente (aviso anticipado de la
    politica de cuota mensual tras 12 meses). Destinatario fijo por env var (no
    es un relay abierto): el endpoint que llama esto no acepta 'para' del cliente."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    destino = os.getenv('REPORTE_ALINEADORES_EMAIL', 'recepcion@ortodonciarichard.cl').strip()
    if not smtp_user or not smtp_pass:
        return False
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Aviso Alineadores <{smtp_user}>'
    msg['To'] = destino
    msg['Subject'] = asunto
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [destino], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP reporte alineadores error: %s', e)
        return False


def avisar_recepcion_anulacion(id_agenda, telefono, nombre=''):
    """El paciente anulo su hora tocando el boton -- avisar de inmediato para
    que recepcion lo vea (DentiDesk ya quedo actualizado por separado)."""
    filas = _fila('Cita', id_agenda) + _fila('Paciente', nombre) + _fila('Teléfono', telefono)
    html = _aviso_recepcion_html('Un paciente anuló su hora por WhatsApp', filas)
    return _enviar_email_recepcion(f'Anulación por WhatsApp — cita {id_agenda}', html)


def avisar_recepcion_quiere_reagendar(id_agenda, telefono, nombre='', fecha=''):
    """El paciente toco el boton 'Reagendar' en WhatsApp -- la cita quedo
    marcada en DentiDesk con el estado 'Pidio cambiar su hora' y SIGUE
    VIGENTE: recepcion no debe anularla, solo pasa a 'Re-agendado' cuando el
    paciente concreta la hora nueva por el link."""
    filas = (_fila('Cita', id_agenda) + _fila('Paciente', nombre)
             + _fila('Teléfono', telefono) + _fila('Fecha de la hora actual', fecha)
             + _fila('Estado', 'Pidió cambiar su hora'))
    html = _aviso_recepcion_html(
        'Un paciente pidió cambiar su hora por WhatsApp — la hora SIGUE agendada, no anular',
        filas)
    return _enviar_email_recepcion(f'Pidió cambiar su hora — cita {id_agenda}', html)


# ── NPS / encuesta de satisfaccion ───────────────────────────────────────────

def enviar_nps(cita):
    """Encuesta de satisfaccion (NPS) tras la atencion. cita: nombre, telefono,
    doctor_nombre, id_agenda, fecha, cuando ('hoy'/'ayer'). Igual que
    enviar_recordatorio_control: si
    la plantilla dedicada de NPS todavia no existe o no esta aprobada en Meta,
    cae de vuelta a 'conversacion_general' con un motivo libre, para no dejar
    de enviar mientras se aprueba."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_nps(
            telefono=cita['telefono'], nombre=cita['nombre'],
            cuando=cita.get('cuando', 'hoy'), doctor=cita['doctor_nombre'],
            id_agenda=cita['id_agenda'], fecha_iso=cita.get('fecha', ''),
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.warning('plantilla NPS no disponible (%s); uso conversacion_general', e)
        try:
            bare = wa_cloud.nombre_doctor_sin_titulo(cita.get('doctor_nombre', ''))
            con_doctor = f" con el Dr. {bare}" if bare else ""
            motivo = (f"gracias por su visita{con_doctor}. Nos encantaría saber "
                      "cómo estuvo su experiencia; puede contarnos respondiendo "
                      "por este medio.")
            r2 = wa_cloud.enviar_conversacion_general(cita['telefono'], cita['nombre'], motivo)
            return {'ok': bool(r2.get('ok'))}
        except wa_cloud.WhatsAppCloudError as e2:
            log.error('WhatsApp Cloud API error (nps, fallback): %s', e2)
            return {'ok': False, 'error': str(e2)}


def avisar_recepcion_detractor(nombre, telefono, doctor='', id_agenda='', fecha=''):
    """El paciente califico su atencion como 'Puede mejorar' (detractor NPS) --
    avisar de inmediato para que recepcion haga seguimiento privado (mismo
    patron que avisar_recepcion_anulacion)."""
    filas = (_fila('Paciente', nombre) + _fila('Doctor', doctor)
             + _fila('Teléfono', telefono) + _fila('Fecha atención', fecha))
    html = _aviso_recepcion_html(
        "Un paciente calificó su atención como 'Puede mejorar'", filas)
    return _enviar_email_recepcion(f'Paciente insatisfecho — {nombre or telefono}', html)


def responder_nps_promotor(telefono, nombre, doctor, review_url):
    """Agradece al paciente promotor y le pide una reseña de Google, con una
    frase sugerida lista para copiar que menciona al doctor. No lanza si falla
    (usa enviar_texto_libre, que solo loguea)."""
    # {{2}} viene sin titulo (DentiDesk lo da asi y wa_cloud lo normaliza); en
    # la resena SI queremos el "Dr." explicito, porque mencionar al doctor por
    # su titulo+nombre es justo lo que mejora su posicionamiento en Google.
    bare = wa_cloud.nombre_doctor_sin_titulo(doctor)
    mencion = f"al Dr. {bare}" if bare else "a su especialista"
    frase = (f'"Excelente atención del Dr. {bare}, muy recomendable."'
             if bare else '"Excelente atención, muy recomendable."')
    texto = (
        f"¡Muchas gracias, {nombre}! 😊 Nos alegra que haya tenido una buena experiencia.\n\n"
        f"Si nos regala un momento, nos ayudaría muchísimo que compartiera su experiencia "
        f"en Google y mencione {mencion} 🙏:\n{review_url}\n\n"
        f"Puede copiar y pegar algo así:\n{frase}"
    )
    return enviar_texto_libre(telefono, texto)


def responder_nps_pasivo(telefono, nombre):
    """Agradecimiento simple al paciente pasivo (NPS), sin pedir reseña
    publica. No lanza si falla (usa enviar_texto_libre)."""
    texto = (f"¡Gracias por su tiempo, {nombre}! Seguiremos trabajando para que "
             "su próxima visita sea aún mejor. 🦷")
    return enviar_texto_libre(telefono, texto)


def responder_nps_detractor(telefono, nombre):
    """Respuesta empatica al paciente detractor (NPS), sin pedir reseña
    publica. No lanza si falla (usa enviar_texto_libre)."""
    texto = (f"Gracias por contarnos, {nombre}. Lamentamos que su experiencia no "
             "haya sido la mejor. Una persona de nuestro equipo se pondrá en "
             "contacto con usted para ayudar en lo que necesite. 🙏")
    return enviar_texto_libre(telefono, texto)


# ── Solicitud de cambio de datos ─────────────────────────────────────────────

def enviar_solicitud_cambio_datos(datos, cfg=None):
    """
    Notifica a recepcion que el paciente agendó pero quiere actualizar sus datos.
    datos: nombre, rut_fmt, email_antiguo, email_nuevo, telefono_antiguo,
           telefono_nuevo, fecha_legible, hora, doctor_nombre.
    """
    cfg = cfg or load_config()
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    if not smtp_user or not smtp_pass:
        return False

    nombre   = datos.get('nombre', 'Paciente')
    rut      = datos.get('rut_fmt', '')

    # Solo incluir los campos que realmente cambian
    cambios = []
    if datos.get('email_nuevo') and datos.get('email_nuevo') != datos.get('email_antiguo'):
        cambios.append(('Email', datos.get('email_antiguo') or '(sin registro)', datos['email_nuevo']))
    if datos.get('telefono_nuevo') and datos.get('telefono_nuevo') != datos.get('telefono_antiguo'):
        cambios.append(('Teléfono', datos.get('telefono_antiguo') or '(sin registro)', datos['telefono_nuevo']))
    if not cambios:
        return True  # sin cambios reales, no enviar

    cambios_html = ''.join(f"""
      <tr>
        <td style="padding:10px 12px;font-weight:700;color:#1A2E4A;white-space:nowrap">{campo}</td>
        <td style="padding:10px 12px;color:#718096;text-decoration:line-through">{antiguo}</td>
        <td style="padding:10px 12px;color:#2D3748">→</td>
        <td style="padding:10px 12px;color:#1A2E4A;font-weight:600">{nuevo}</td>
      </tr>""" for campo, antiguo, nuevo in cambios)

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr><td style="background:#1A2E4A;padding:24px 32px;">
    <p style="margin:0;color:#C9A84C;font-size:12px;letter-spacing:2px;text-transform:uppercase">Agenda Online — Aviso</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:20px">Solicitud de actualización de datos</h1>
  </td></tr>
  <tr><td style="padding:28px 32px;">
    <p style="margin:0 0 16px;color:#4A5568;font-size:15px">
      El paciente <strong>{nombre}</strong> (RUT {rut}) agendó hora el
      <strong>{datos.get('fecha_legible','')} a las {datos.get('hora','')} hrs</strong>
      con <strong>{datos.get('doctor_nombre','')}</strong> y está solicitando actualizar sus datos de contacto:
    </p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
      <thead><tr style="background:#f8fafc">
        <th style="padding:10px 12px;text-align:left;color:#4A5568;font-size:12px;text-transform:uppercase">Campo</th>
        <th style="padding:10px 12px;text-align:left;color:#4A5568;font-size:12px;text-transform:uppercase">Dato actual</th>
        <th></th>
        <th style="padding:10px 12px;text-align:left;color:#4A5568;font-size:12px;text-transform:uppercase">Dato solicitado</th>
      </tr></thead>
      <tbody>{cambios_html}</tbody>
    </table>
    <p style="margin:20px 0 0;color:#718096;font-size:13px">
      Si los datos son correctos, puedes actualizar la ficha del paciente en DentiDesk.
    </p>
  </td></tr>
  <tr><td style="background:#1A2E4A;padding:16px 32px;text-align:center;">
    <p style="margin:0;color:#8fa8c8;font-size:12px">Ortodoncia Richard · Sistema de agendamiento online</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Agenda Ortodoncia Richard <{smtp_user}>'
    msg['To']   = smtp_user
    msg['Subject'] = f'Paciente solicita cambio de datos — {nombre} (RUT {rut})'
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [smtp_user], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP cambio_datos error: %s', e)
        return False


# ── Aviso a recepción de un nuevo agendamiento ───────────────────────────────

def enviar_aviso_agendamiento(datos, cfg=None):
    """
    Avisa a recepción que un paciente agendó (se activa por motivo desde el panel).
    datos: nombre, rut_fmt, email, telefono, fecha_legible, hora, doctor_nombre,
           motivo_label.
    """
    cfg = cfg or load_config()
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    if not smtp_user or not smtp_pass:
        return False

    def fila(label, valor):
        return f"""
      <tr>
        <td style="padding:10px 14px;font-weight:700;color:#1A2E4A;white-space:nowrap;border-top:1px solid #e2e8f0">{label}</td>
        <td style="padding:10px 14px;color:#1A2535;border-top:1px solid #e2e8f0">{valor or '—'}</td>
      </tr>"""

    filas = (
        fila('Paciente', datos.get('nombre', '')) +
        fila('RUT', datos.get('rut_fmt', '')) +
        fila('Motivo', datos.get('motivo_label', '')) +
        fila('Doctor', datos.get('doctor_nombre', '')) +
        fila('Fecha', datos.get('fecha_legible', '')) +
        fila('Hora', f"{datos.get('hora','')} hrs") +
        fila('Email', datos.get('email', '')) +
        fila('Teléfono', datos.get('telefono', ''))
    )

    html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr><td style="background:#1A2E4A;padding:24px 32px;">
    <p style="margin:0;color:#C9A84C;font-size:12px;letter-spacing:2px;text-transform:uppercase">Agenda Online — Aviso</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:20px">Nuevo agendamiento</h1>
  </td></tr>
  <tr><td style="padding:24px 32px;">
    <p style="margin:0 0 16px;color:#4A5568;font-size:15px">Se agendó una nueva hora a través del sitio web:</p>
    <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
      <tbody>{filas}</tbody>
    </table>
  </td></tr>
  <tr><td style="background:#1A2E4A;padding:16px 32px;text-align:center;">
    <p style="margin:0;color:#8fa8c8;font-size:12px">Ortodoncia Richard · Sistema de agendamiento online</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Agenda Ortodoncia Richard <{smtp_user}>'
    msg['To']   = smtp_user
    msg['Subject'] = (f"Nuevo agendamiento — {datos.get('nombre','')} · "
                      f"{datos.get('motivo_label','')} · {datos.get('fecha_legible','')} {datos.get('hora','')}")
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [smtp_user], msg.as_bytes())
        return True
    except Exception as e:
        log.error('SMTP aviso_agendamiento error: %s', e)
        return False


# ── Consentimiento informado (link de firma) ─────────────────────────────────

def _html_consentimiento(nombre, link, tipo_label):
    cuerpo = f"""      <p style="margin:0 0 20px;color:#4A5568;font-size:15px;">Hola <strong>{nombre}</strong>, antes de tu próximo tratamiento necesitamos que firmes tu <strong>{tipo_label}</strong>.</p>
      <p style="margin:0 0 24px;color:#4A5568;font-size:15px;">Puedes leerlo con calma y firmarlo directamente desde tu celular:</p>
      <div style="text-align:center;margin:0 0 24px;">
        <a href="{link}" style="display:inline-block;background:#C9A84C;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:8px;font-weight:700;font-size:15px;">Firmar consentimiento</a>
      </div>
      <p style="margin:0;color:#718096;font-size:13px;">Si tienes dudas, contáctanos: 📞 <a href="{CLINICA_TEL_LINK}" style="color:#1A2E4A;">{CLINICA_TELEFONO}</a> &nbsp;|&nbsp; 💬 <a href="{CLINICA_WHATSAPP}" style="color:#1A2E4A;">WhatsApp</a></p>"""
    return _email_layout('Consentimiento informado', cuerpo, pie=PIE_SOLO_DIRECCION)


def _enviar_email_consentimiento(nombre, email, link, tipo_label):
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (email or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return False

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    msg['Subject'] = f'Firma tu {tipo_label} — Ortodoncia Richard'
    msg['Reply-To'] = smtp_user
    msg.attach(MIMEText(_html_consentimiento(nombre, link, tipo_label), 'html', 'utf-8'))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [dest], msg.as_bytes())
        log.info('Link de consentimiento enviado a %s', dest)
        return True
    except Exception as e:
        log.error('SMTP consentimiento error: %s', e)
        return False


def _enviar_whatsapp_consentimiento(nombre, telefono, link, tipo_label):
    """Plantilla dedicada 'consentimiento_informado'. Mientras Meta la aprueba
    (o si por algun motivo falla), cae de vuelta a 'conversacion_general' con
    el link en el motivo — el mismo mensaje que se usaba antes de tener
    plantilla propia, para no dejar de enviar durante la transicion."""
    if not telefono:
        return False
    try:
        resultado = wa_cloud.enviar_consentimiento(telefono, nombre, tipo_label, link)
        return bool(resultado.get('ok'))
    except wa_cloud.WhatsAppCloudError as e:
        log.warning('consentimiento_informado no disponible (%s); uso conversacion_general', e)
        try:
            motivo = f"necesita firmar su {tipo_label}. Puede hacerlo directamente desde su celular aquí: {link}"
            resultado = wa_cloud.enviar_conversacion_general(telefono, nombre, motivo)
            return bool(resultado.get('ok'))
        except wa_cloud.WhatsAppCloudError as e2:
            log.error('WhatsApp Cloud API error (consentimiento): %s', e2)
            return False


def _html_copia_consentimiento(nombre, tipo_label):
    cuerpo = f"""      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Hola <strong>{nombre}</strong>, gracias por firmar tu <strong>{tipo_label}</strong>.</p>
      <p style="margin:0 0 8px;color:#4A5568;font-size:15px;">Adjuntamos una copia en PDF del documento firmado para tus registros.</p>
      <p style="margin:20px 0 0;color:#718096;font-size:13px;">Si tienes dudas, contáctanos: 📞 <a href="{CLINICA_TEL_LINK}" style="color:#1A2E4A;">{CLINICA_TELEFONO}</a> &nbsp;|&nbsp; 💬 <a href="{CLINICA_WHATSAPP}" style="color:#1A2E4A;">WhatsApp</a></p>"""
    return _email_layout('Tu consentimiento firmado', cuerpo, pie=PIE_SOLO_DIRECCION,
                         title_tag='Copia de tu consentimiento')


def enviar_copia_consentimiento(paciente, pdf_path, tipo_label='consentimiento informado'):
    """Envía al paciente una copia en PDF de su consentimiento firmado (adjunto).
    paciente: dict con nombres, apellidos, email. Devuelve dict {ok, canal}."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (paciente.get('email') or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return {'ok': False, 'error': 'sin SMTP o email'}

    nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip() or 'Paciente'
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    # Copia a recepcion (SMTP_USER = recepcion@ortodonciarichard.cl) para que la
    # clinica reciba tambien el PDF firmado, sin depender de revisar la bandeja.
    cc_recepcion = smtp_user if smtp_user.lower() != dest.lower() else ''
    if cc_recepcion:
        msg['Cc'] = cc_recepcion
    msg['Subject'] = f'Copia de tu {tipo_label} — Ortodoncia Richard'
    msg['Reply-To'] = smtp_user
    msg.attach(MIMEText(_html_copia_consentimiento(nombre, tipo_label), 'html', 'utf-8'))

    try:
        pdf_bytes = Path(pdf_path).read_bytes()
        adj = MIMEBase('application', 'pdf')
        adj.set_payload(pdf_bytes)
        from email.encoders import encode_base64
        encode_base64(adj)
        adj['Content-Disposition'] = 'attachment; filename="consentimiento-firmado.pdf"'
        msg.attach(adj)
    except Exception as e:
        log.error('No se pudo adjuntar el PDF de consentimiento: %s', e)
        return {'ok': False, 'error': 'no se pudo adjuntar el PDF'}

    try:
        ctx = ssl.create_default_context()
        destinatarios = [dest] + ([cc_recepcion] if cc_recepcion else [])
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, destinatarios, msg.as_bytes())
        log.info('Copia de consentimiento enviada a %s (cc %s)', dest, cc_recepcion or '—')
        return {'ok': True, 'canal': 'email'}
    except Exception as e:
        log.error('SMTP copia consentimiento error: %s', e)
        return {'ok': False, 'error': str(e)}


def _html_formulario_seguro(nombre, aseguradora_nombre, link_cambio=''):
    # Bloque opcional al pie: si el paciente cambió de compañía, actualiza su
    # aseguradora por un link con token y el sistema le reenvía el formulario
    # correcto. Estilos en LÍNEA y <table> (regla 6): Gmail/Outlook lo rompen si no.
    bloque_cambio = ''
    if link_cambio:
        bloque_cambio = f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0 0;">
        <tr><td style="border-top:1px solid #E2E8F0;padding-top:16px;">
          <p style="margin:0 0 10px;color:#4A5568;font-size:14px;">¿Cambió su compañía de seguro complementario? Indíquenos la nueva y le enviaremos el formulario correcto.</p>
          <a href="{link_cambio}" style="display:inline-block;background:#1A2E4A;color:#ffffff;text-decoration:none;font-size:14px;padding:10px 18px;border-radius:6px;">Actualizar mi aseguradora</a>
        </td></tr>
      </table>"""
    cuerpo = f"""      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Hola <strong>{nombre}</strong>,</p>
      <p style="margin:0 0 8px;color:#4A5568;font-size:15px;">Adjuntamos el formulario de reembolso de <strong>{aseguradora_nombre}</strong> con el detalle de tus prestaciones, listo para que lo presentes a tu seguro complementario.</p>
      <p style="margin:20px 0 0;color:#718096;font-size:13px;">Si tienes dudas, contáctanos: 📞 <a href="{CLINICA_TEL_LINK}" style="color:#1A2E4A;">{CLINICA_TELEFONO}</a> &nbsp;|&nbsp; 💬 <a href="{CLINICA_WHATSAPP}" style="color:#1A2E4A;">WhatsApp</a></p>{bloque_cambio}"""
    return _email_layout('Formulario de tu seguro complementario', cuerpo, pie=PIE_SOLO_DIRECCION)


def avisar_recepcion_seguro_no_enviado(motivo, rut, glosa, folio='', paciente=''):
    """Aviso a la clínica cuando el auto-envío NO pudo mandar el formulario de
    seguro (queda pendiente para hacerlo a mano desde el F2)."""
    razones = {
        'sin_aseguradora': 'El paciente no tiene una aseguradora asignada.',
        'glosa': 'No se reconoció ninguna prestación en la glosa de la boleta.',
        'glosa_nueva': ('La boleta trae una prestación nueva que aún no está '
                        'configurada. Ya quedó agregada en el panel de Seguros → '
                        'Prestaciones; revísala y, si corresponde, envía el '
                        'formulario a mano desde el F2.'),
        'sin_email': 'El paciente no tiene un email registrado.',
        'sin_doctor': ('No hay un "doctor por defecto" con firma configurado para el '
                       'auto-envío, así que el formulario habría salido sin el '
                       'odontólogo ni la firma. Configúralo en el panel (pestaña '
                       'Seguros) y emite este formulario a mano desde el F2.'),
        'error_pdf': 'Hubo un error al generar el PDF del formulario.',
        'error_envio': 'Hubo un error al enviar el correo.',
    }
    detalle = razones.get(motivo, motivo)
    filas = (_fila('Paciente', paciente or '—') + _fila('RUT', rut)
             + _fila('Boleta (folio)', folio or '—')
             + _fila('Glosa', glosa or '—') + _fila('Motivo', detalle))
    html = _aviso_recepcion_html('No se pudo enviar el formulario de seguro', filas)
    return _enviar_email_recepcion(
        f'Seguro complementario pendiente — {paciente or rut}', html)


def enviar_formulario_seguro(paciente, pdf_path, aseguradora_nombre, link_cambio=''):
    """Envía al paciente el formulario de reembolso del seguro complementario
    (PDF adjunto, Cc a recepción). Mismo patrón que enviar_copia_consentimiento.
    paciente: dict con nombres, apellidos, email. `link_cambio` (opcional) agrega al
    pie el enlace "Actualizar mi aseguradora". Devuelve {ok, canal|error}."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (paciente.get('email') or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return {'ok': False, 'error': 'sin SMTP o email'}

    nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip() or 'Paciente'
    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    cc_recepcion = smtp_user if smtp_user.lower() != dest.lower() else ''
    if cc_recepcion:
        msg['Cc'] = cc_recepcion
    msg['Subject'] = f'Formulario seguro complementario {aseguradora_nombre} — Ortodoncia Richard'
    msg['Reply-To'] = smtp_user
    msg.attach(MIMEText(_html_formulario_seguro(nombre, aseguradora_nombre, link_cambio), 'html', 'utf-8'))

    try:
        pdf_bytes = Path(pdf_path).read_bytes()
        adj = MIMEBase('application', 'pdf')
        adj.set_payload(pdf_bytes)
        from email.encoders import encode_base64
        encode_base64(adj)
        adj['Content-Disposition'] = 'attachment; filename="formulario-seguro.pdf"'
        msg.attach(adj)
    except Exception as e:
        log.error('No se pudo adjuntar el PDF del seguro: %s', e)
        return {'ok': False, 'error': 'no se pudo adjuntar el PDF'}

    try:
        ctx = ssl.create_default_context()
        destinatarios = [dest] + ([cc_recepcion] if cc_recepcion else [])
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, destinatarios, msg.as_bytes())
        log.info('Formulario de seguro enviado a %s (cc %s)', dest, cc_recepcion or '—')
        return {'ok': True, 'canal': 'email'}
    except Exception as e:
        log.error('SMTP formulario seguro error: %s', e)
        return {'ok': False, 'error': str(e)}


def _frecuencia_label(frecuencia_meses):
    """Convierte la frecuencia en meses a texto legible ('6 meses', '3 meses',
    'un año'). No se hardcodea el '6 meses' del texto -- sale de la
    frecuencia real del paciente (config o override por F2)."""
    n = frecuencia_meses or 6
    if n == 12:
        return 'un año'
    if n == 1:
        return 'un mes'
    return f'{n} meses'


def _html_control_dental(nombre, saludo_sufijo, frecuencia_meses=6):
    """HTML del recordatorio de control dental (limpieza y revision de
    caries con el dentista general del paciente, mientras dura el
    tratamiento de ortodoncia). Molde: _html_formulario_seguro. saludo_sufijo
    es 'o' | 'a' | 'o/a' (pacientes.saludo). La ultima linea (nota de
    escape) va atenuada -- es un pie de pagina, no un parrafo mas."""
    frecuencia_txt = _frecuencia_label(frecuencia_meses)
    cuerpo = f"""      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Estimad{saludo_sufijo} <strong>{nombre}</strong>,</p>
      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Han pasado {frecuencia_txt} desde nuestro último recordatorio. Durante el tratamiento de ortodoncia te recomendamos agendar un control con tu dentista para limpieza y revisión de caries.</p>
      <p style="margin:0 0 20px;color:#4A5568;font-size:15px;">Mantener una buena higiene permite que el tratamiento de ortodoncia avance en los tiempos esperados, además de poder terminarlo de la mejor manera.</p>
      <p style="margin:0;color:#a0aec0;font-size:12px;">Si ya fuiste recientemente a tu control dental, por favor no consideres este correo.</p>"""
    return _email_layout('Recordatorio: control con tu dentista', cuerpo, pie=PIE_COMPLETO,
                         title_tag='Recordatorio de control dental')


def enviar_recordatorio_control_dental(paciente, cfg=None):
    """Envia el recordatorio de control dental (limpieza/revision de caries
    con el dentista general) a un paciente inscrito del modulo
    control_dental. Mismo patron que enviar_formulario_seguro pero SIN
    adjunto. paciente: dict con al menos rut, nombre, email (y opcionalmente
    frecuencia_meses). Devuelve {ok, canal|error}."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (paciente.get('email') or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return {'ok': False, 'error': 'sin SMTP o email'}

    nombre = paciente.get('nombre', 'Paciente')
    saludo_sufijo = pacientes.saludo(paciente.get('rut', ''))
    frecuencia_meses = paciente.get('frecuencia_meses', 6)

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    msg['Subject'] = 'Recordatorio: control con tu dentista'
    msg['Reply-To'] = smtp_user
    msg.attach(MIMEText(_html_control_dental(nombre, saludo_sufijo, frecuencia_meses), 'html', 'utf-8'))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [dest], msg.as_bytes())
        log.info('Recordatorio de control dental enviado a %s', dest)
        return {'ok': True, 'canal': 'email'}
    except Exception as e:
        log.error('SMTP recordatorio control dental error: %s', e)
        return {'ok': False, 'error': str(e)}


def avisar_recepcion_control_dental_sin_email(lista):
    """Aviso agrupado a recepcion con los pacientes inscritos en control
    dental que no tienen email registrado, para que se los pidan en la
    proxima cita. UN solo correo con la lista completa (no uno por
    paciente). lista: [{nombre, rut, ...}]. Si viene vacia, no manda nada."""
    if not lista:
        return False
    filas = ''.join(_fila('Paciente', p.get('nombre', '')) + _fila('RUT', p.get('rut', ''))
                     for p in lista)
    html = _aviso_recepcion_html(
        f'Pacientes sin email para control dental ({len(lista)})', filas)
    return _enviar_email_recepcion(
        f'Control dental — {len(lista)} paciente(s) sin email', html)


def _fila_seccion(texto):
    """Fila de encabezado a todo el ancho, para separar bloques dentro de la
    tabla de un aviso a recepcion."""
    return (f'<tr><td colspan="2" style="padding:10px 14px;background:#1A2E4A;'
            f'color:#ffffff;font-size:13px;font-weight:700;letter-spacing:.5px">'
            f'{texto}</td></tr>')


def _filas_consentimientos(lista):
    """Una fila por paciente: nombre a la izquierda, y a la derecha el RUT, el
    documento y la hora con su doctor."""
    filas = []
    for p in lista:
        hora = p.get('hora_cita') or ''
        doctor = (p.get('doctor_cita') or '').strip()
        cita = f'{hora} con {doctor}'.strip() if doctor else (hora or 'sin hora')
        filas.append(_fila(
            p.get('nombre', ''),
            f"{p.get('rut', '')} · {p.get('tipo', '')}<br>"
            f"<span style=\"color:#718096;font-size:13px\">{cita}</span>"))
    return ''.join(filas)


def avisar_recepcion_consentimientos_pendientes(hoy, manana=None):
    """Aviso agrupado a recepcion con los pacientes que tienen hora y AUN NO
    firman su consentimiento. UN solo correo con las dos listas (nunca uno por
    paciente); si ambas vienen vacias no manda nada.

    `hoy` y `manana` son listas de {nombre, rut, tipo, hora_cita, doctor_cita,
    ...} tal como las devuelve consentimientos.pendientes_con_cita_en().

    Van en dos bloques a proposito: a los de HOY hay que pasarles la tablet de
    recepcion cuando lleguen; a los de MAÑANA todavia se les alcanza a reenviar
    el link para que lleguen firmando."""
    manana = manana or []
    if not hoy and not manana:
        return False

    filas = ''
    if hoy:
        filas += _fila_seccion(f'⚠️ Vienen HOY sin firmar ({len(hoy)})')
        filas += _filas_consentimientos(hoy)
    if manana:
        filas += _fila_seccion(f'📅 Vienen MAÑANA sin firmar ({len(manana)})')
        filas += _filas_consentimientos(manana)

    partes = ([f'{len(hoy)} hoy'] if hoy else []) + ([f'{len(manana)} mañana'] if manana else [])
    html = _aviso_recepcion_html(
        'Consentimientos pendientes de firma', filas,
        etiqueta='Consentimientos — Aviso diario',
        pie='Ortodoncia Richard · Aviso automático de consentimientos sin firmar')
    return _enviar_email_recepcion(
        f"Consentimientos pendientes de firma — {', '.join(partes)}", html)


def enviar_link_consentimiento(paciente, link, canal, tipo_label='consentimiento informado'):
    """
    Envía el link de firma de consentimiento por el canal elegido explícitamente
    por la secretaria desde F2 (a diferencia de enviar_confirmacion(), que hace
    fallback automático entre canales).

    paciente: dict con nombres, apellidos, email, telefono (formato de
              pacientes.lookup()). canal: 'mail' | 'whatsapp'.
    """
    nombre = f"{paciente.get('nombres', '')} {paciente.get('apellidos', '')}".strip()
    if canal == 'mail':
        email = (paciente.get('email') or '').strip()
        if '@' not in email:
            return {'ok': False, 'canal': 'email',
                    'error': 'El paciente no tiene un email registrado en la base. '
                             'Si lo acabas de registrar, la ficha puede no haberse '
                             'sincronizado todavía (se actualiza 2 veces al día).'}
        ok = _enviar_email_consentimiento(nombre, email, link, tipo_label)
        return {'ok': ok, 'canal': 'email',
                'error': None if ok else 'No se pudo enviar el email (revisa la configuración SMTP).'}
    if canal == 'whatsapp':
        telefono = (paciente.get('telefono') or '').strip()
        if not telefono:
            return {'ok': False, 'canal': 'whatsapp',
                    'error': 'El paciente no tiene un teléfono registrado en la base. '
                             'Si lo acabas de registrar, la ficha puede no haberse '
                             'sincronizado todavía (se actualiza 2 veces al día).'}
        ok = _enviar_whatsapp_consentimiento(nombre, telefono, link, tipo_label)
        return {'ok': ok, 'canal': 'whatsapp',
                'error': None if ok else 'WhatsApp no confirmó el envío (revisa el estado en el panel).'}
    return {'ok': False, 'error': f'Canal no soportado: {canal}'}


# ── Punto de entrada ─────────────────────────────────────────────────────────

def enviar_confirmacion(cita, cfg=None, canal=None, reagenda=False, primera=False):
    """
    Envía confirmacion de cita al paciente.
    cita: dict con nombre, telefono, email, fecha (date), fecha_legible, hora,
          doctor_nombre, motivo_label, dur_min.
    canal: None (default) = automatico: email primero, WhatsApp de respaldo
           (usado por el agendamiento online y el barrido de confirmaciones).
           'email' | 'whatsapp' = forzado explicitamente (lo usa el asistente
           F2, donde la secretaria elige el canal a mano).
           'ambos' = email Y WhatsApp (lo usa el reagendamiento: el paciente vino
           desde WhatsApp, asi que se le avisa por ambos canales).
    reagenda: True cuando la cita nueva viene de un reagendamiento -- usa el
           texto/plantilla 'reagenda_confirmada' en vez de 'confirmacion_hora'.
    primera: True cuando la cita es una primera consulta -- el WhatsApp usa la
           plantilla especial 'primera_consulta' (video de bienvenida) en vez
           de 'confirmacion_hora', con fallback si esa plantilla falla.
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

    if canal == 'ambos':
        # Reagendamiento: mandar por los dos canales (viene desde WhatsApp).
        # Se considera OK si al menos uno salio; se reporta cada uno.
        email_ok = _enviar_email_smtp(cita, ics, reagenda=reagenda)
        wa_ok, wa_err = _enviar_whatsapp(cita, ics, reagenda=reagenda, primera=primera)
        canales = [c for c, ok in (('email', email_ok), ('whatsapp', wa_ok)) if ok]
        return {'ok': bool(canales), 'canal': '+'.join(canales) or None,
                'email_ok': email_ok, 'whatsapp_ok': wa_ok,
                'error': None if canales else (wa_err or 'No se pudo enviar por ningún canal')}

    if canal == 'whatsapp':
        ok, err = _enviar_whatsapp(cita, ics, reagenda=reagenda, primera=primera)
        if ok:
            return {'ok': True, 'canal': 'whatsapp'}
        return {'ok': False, 'canal': None, 'error': err or 'No se pudo enviar por WhatsApp'}

    if canal == 'email':
        if _enviar_email_smtp(cita, ics, reagenda=reagenda):
            return {'ok': True, 'canal': 'email'}
        return {'ok': False, 'canal': None, 'error': 'No se pudo enviar el email'}

    # Automatico (online / barrido): email primero, WhatsApp de respaldo
    if _enviar_email_smtp(cita, ics, reagenda=reagenda):
        return {'ok': True, 'canal': 'email'}
    ok, _err = _enviar_whatsapp(cita, ics, reagenda=reagenda, primera=primera)
    if ok:
        return {'ok': True, 'canal': 'whatsapp'}

    return {'ok': False, 'canal': None, 'error': 'No se pudo enviar confirmacion'}


# ── PSQ (cuestionario de sueño pediátrico) — resultado al doctor ────────────

_PSQ_MOTIVO_NOTA = {
    'sin_doctor': ('No se pudo determinar qué doctor atendió a este paciente en '
                   'DentiDesk, así que este correo se envió a recepción.'),
    'sin_email':  ('Se identificó al doctor tratante, pero no tiene un email '
                   'configurado para este aviso (EMAIL_&lt;doctor&gt;), así '
                   'que este correo se envió a recepción.'),
}


def _html_resultado_psq(paciente, doctor_label, resultado, motivo_envio):
    import html as _html
    nombre = _html.escape(paciente.get('nombre') or 'Paciente')
    rut_fmt = _html.escape(paciente.get('rut_fmt') or '')
    fecha_legible = _html.escape(paciente.get('fecha_legible') or '')

    alto = resultado['riesgo'] == 'alto'
    color = '#e53e3e' if alto else '#38a169'
    etiqueta = 'RIESGO ALTO de trastorno respiratorio del sueño' if alto else 'Riesgo bajo'
    pct = round(resultado['puntaje'] * 100, 1)

    saludo = f'Hola Dr(a). {_html.escape(doctor_label)},' if doctor_label else 'Hola,'
    nota = ''
    if motivo_envio in _PSQ_MOTIVO_NOTA:
        nota = (f'<p style="margin:0 0 16px;padding:10px 14px;background:#fff8e1;'
                f'border-radius:6px;color:#8a6d1f;font-size:13px;">'
                f'⚠️ {_PSQ_MOTIVO_NOTA[motivo_envio]}</p>')

    filas_paciente = (_fila('Paciente', nombre) + _fila('RUT', rut_fmt)
                      + _fila('Fecha de respuesta', fecha_legible))

    filas_detalle = ''
    seccion_actual = None
    for item in resultado['detalle']:
        if item['seccion'] != seccion_actual:
            seccion_actual = item['seccion']
            titulo = _html.escape(psq.SECCIONES.get(seccion_actual, seccion_actual))
            filas_detalle += (f'<tr><td colspan="2" style="padding:10px 14px 4px;'
                              f'font-weight:700;color:#1A2E4A;font-size:12px;'
                              f'text-transform:uppercase;letter-spacing:.4px;'
                              f'border-top:1px solid #e2e8f0;">{titulo}</td></tr>')
        etiqueta_resp = {
            'si': 'Sí', 'no': 'No', 'no_se': 'No sé',
            'nunca': 'Nunca', 'algunas_veces': 'Algunas veces',
            'muchas_veces': 'Muchas veces', 'casi_siempre': 'Casi siempre',
        }.get(item['respuesta'], item['respuesta'] or '—')
        if item['positiva'] is True:
            marca_color = '#e53e3e'
        elif item['positiva'] is False:
            marca_color = '#4A5568'
        else:
            marca_color = '#a0aec0'
        filas_detalle += (
            f'<tr><td style="padding:6px 14px;color:#1A2535;font-size:13px;">'
            f'{_html.escape(item["texto"])}</td>'
            f'<td style="padding:6px 14px;color:{marca_color};font-size:13px;'
            f'font-weight:600;white-space:nowrap;text-align:right;">{etiqueta_resp}</td></tr>'
        )

    cuerpo = f"""      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">{saludo}</p>
      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Un paciente respondió el cuestionario de sueño pediátrico (PSQ) desde el sitio. Este es el resultado:</p>
      {nota}
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;margin-bottom:18px;">
        <tbody>{filas_paciente}</tbody>
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{color};border-radius:8px;margin-bottom:18px;">
        <tr><td style="padding:16px 20px;text-align:center;">
          <p style="margin:0;color:#fff;font-size:15px;font-weight:700;">{etiqueta}</p>
          <p style="margin:4px 0 0;color:#fff;font-size:13px;opacity:.9;">Puntaje {resultado['puntaje']:.3f} ({pct}%) · {resultado['positivas']}/{resultado['contestadas']} respuestas positivas · corte de referencia {resultado['corte']:.3f}</p>
        </td></tr>
      </table>
      <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e8f0;border-radius:8px;overflow:hidden;">
        <tbody>{filas_detalle}</tbody>
      </table>
      <p style="margin:16px 0 0;color:#a0aec0;font-size:12px;">Este cuestionario es una herramienta de screening (PSQ-CL, versión chilena validada, Andes Pediátrica 2024) y no reemplaza una evaluación clínica. Un puntaje sobre el corte sugiere evaluar derivación a especialista en sueño.</p>"""
    return _email_layout('Resultado cuestionario de sueño (PSQ)', cuerpo, pie=PIE_SOLO_DIRECCION,
                         title_tag='Resultado PSQ')


def enviar_resultado_psq(destinatario_email, doctor_label, paciente, resultado, motivo_envio):
    """Envía el resultado del PSQ a `destinatario_email` (doctor tratante o
    recepción, ver psq.resolver_destinatario). `paciente`: {nombre, rut_fmt,
    fecha_legible}. Devuelve {ok, error}."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    if not smtp_user or not smtp_pass or '@' not in (destinatario_email or ''):
        return {'ok': False, 'error': 'sin SMTP o email de destino'}

    msg = MIMEMultipart('alternative')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = destinatario_email
    nombre_paciente = paciente.get('nombre') or 'un paciente'
    msg['Subject'] = f'Resultado PSQ (cuestionario de sueño) — {nombre_paciente}'
    msg['Reply-To'] = smtp_user
    msg.attach(MIMEText(_html_resultado_psq(paciente, doctor_label, resultado, motivo_envio),
                        'html', 'utf-8'))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [destinatario_email], msg.as_bytes())
        log.info('Resultado PSQ enviado a %s (paciente %s)', destinatario_email, nombre_paciente)
        return {'ok': True}
    except Exception as e:
        log.error('SMTP resultado PSQ error: %s', e)
        return {'ok': False, 'error': str(e)}
