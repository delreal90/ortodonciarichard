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


# ── Email (primario) ─────────────────────────────────────────────────────────

def _html_confirmacion(cita):
    """HTML del cuerpo del email de confirmacion."""
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Confirmación de hora</title></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">

  <!-- Header -->
  <tr>
    <td style="background:#1A2E4A;padding:28px 32px;text-align:center;">
      <p style="margin:0;color:#C9A84C;font-size:13px;letter-spacing:2px;text-transform:uppercase;">Ortodoncia Richard</p>
      <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">¡Tu hora quedó agendada!</h1>
    </td>
  </tr>

  <!-- Detalles -->
  <tr>
    <td style="padding:32px 32px 24px;">
      <p style="margin:0 0 24px;color:#4A5568;font-size:15px;">Hola <strong>{cita['nombre']}</strong>, confirmamos tu cita con los siguientes datos:</p>

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
        📞 <a href="tel:+56222173499" style="color:#1A2E4A;">+56 2 2217 3499</a> &nbsp;|&nbsp;
        💬 <a href="https://wa.me/56933558189" style="color:#1A2E4A;">WhatsApp</a>
      </p>
    </td>
  </tr>

  <!-- Footer -->
  <tr>
    <td style="background:#1A2E4A;padding:20px 32px;text-align:center;">
      <p style="margin:0;color:#8fa8c8;font-size:12px;">
        Ortodoncia Richard · Paul Harris 10.349, of. 305, Las Condes, Santiago<br>
        <a href="https://www.ortodonciarichard.cl" style="color:#C9A84C;text-decoration:none;">www.ortodonciarichard.cl</a>
      </p>
    </td>
  </tr>

</table>
</td></tr>
</table>
</body>
</html>"""


def _enviar_email_smtp(cita, ics):
    """Email principal: smtplib con .ics adjunto (Gmail App Password)."""
    smtp_user = os.getenv('SMTP_USER', '').strip()
    smtp_pass = os.getenv('SMTP_PASS', '').strip()
    dest = (cita.get('email') or '').strip()
    if not smtp_user or not smtp_pass or '@' not in dest:
        return False

    msg = MIMEMultipart('mixed')
    msg['From'] = f'Ortodoncia Richard <{smtp_user}>'
    msg['To'] = dest
    msg['Subject'] = f"Confirmación de hora — {cita['fecha_legible']} {cita['hora']} hrs"
    msg['Reply-To'] = smtp_user

    msg.attach(MIMEText(_html_confirmacion(cita), 'html', 'utf-8'))

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

def _enviar_whatsapp(cita, ics):
    """Fallback: WhatsApp Cloud API con la plantilla 'confirmacion_hora'.
    Sin adjunto (las plantillas de Meta no llevan .ics); solo texto.
    Devuelve (ok: bool, error: str|None) — el error se propaga hasta F2 para
    que la secretaria vea la causa real (token vencido, sin telefono, etc.)
    en vez de un mensaje generico."""
    if not cita.get('telefono'):
        return False, 'La cita no tiene teléfono registrado'
    try:
        resultado = wa_cloud.enviar_confirmacion_hora(
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
    """cita: nombre, telefono, doctor_nombre, fecha_legible, hora, id_agenda."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_recordatorio_semana(
            telefono=cita['telefono'], nombre=cita['nombre'],
            doctor_nombre=cita['doctor_nombre'],
            fecha_legible=cita['fecha_legible'], hora=cita['hora'],
            id_agenda=cita['id_agenda'],
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (recordatorio_semana): %s', e)
        return {'ok': False, 'error': str(e)}


def enviar_recordatorio_dia(cita):
    """cita: nombre, telefono, doctor_nombre, fecha_legible, hora, id_agenda."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_recordatorio_dia(
            telefono=cita['telefono'], nombre=cita['nombre'],
            doctor_nombre=cita['doctor_nombre'],
            fecha_legible=cita['fecha_legible'], hora=cita['hora'],
            id_agenda=cita['id_agenda'],
        )
        return {'ok': bool(r.get('ok'))}
    except wa_cloud.WhatsAppCloudError as e:
        log.error('WhatsApp Cloud API error (recordatorio_dia): %s', e)
        return {'ok': False, 'error': str(e)}


def enviar_inasistencia(cita):
    """cita: nombre, telefono, fecha_legible, id_agenda."""
    if not cita.get('telefono'):
        return {'ok': False, 'error': 'La cita no tiene teléfono registrado'}
    try:
        r = wa_cloud.enviar_inasistencia_reagendar(
            telefono=cita['telefono'], nombre=cita['nombre'],
            fecha_legible=cita['fecha_legible'], id_agenda=cita['id_agenda'],
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


def _aviso_recepcion_html(titulo, filas_html):
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr><td style="background:#1A2E4A;padding:24px 32px;">
    <p style="margin:0;color:#C9A84C;font-size:12px;letter-spacing:2px;text-transform:uppercase">WhatsApp — Aviso</p>
    <h1 style="margin:6px 0 0;color:#fff;font-size:20px">{titulo}</h1>
  </td></tr>
  <tr><td style="padding:24px 32px;">
    <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:8px;overflow:hidden">
      <tbody>{filas_html}</tbody>
    </table>
  </td></tr>
  <tr><td style="background:#1A2E4A;padding:16px 32px;text-align:center;">
    <p style="margin:0;color:#8fa8c8;font-size:12px">Ortodoncia Richard · Recordatorios automáticos por WhatsApp</p>
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


def avisar_recepcion_anulacion(id_agenda, telefono, nombre=''):
    """El paciente anulo su hora tocando el boton -- avisar de inmediato para
    que recepcion lo vea (DentiDesk ya quedo actualizado por separado)."""
    filas = _fila('Cita', id_agenda) + _fila('Paciente', nombre) + _fila('Teléfono', telefono)
    html = _aviso_recepcion_html('Un paciente anuló su hora por WhatsApp', filas)
    return _enviar_email_recepcion(f'Anulación por WhatsApp — cita {id_agenda}', html)


def avisar_recepcion_reagendar(id_agenda, telefono, nombre=''):
    """El paciente pidio reagendar -- por ahora esto se gestiona a mano
    (la logica de horas disponibles queda para una fase futura)."""
    filas = _fila('Cita', id_agenda) + _fila('Paciente', nombre) + _fila('Teléfono', telefono)
    html = _aviso_recepcion_html('Un paciente pidió reagendar por WhatsApp', filas)
    return _enviar_email_recepcion(f'Solicitud de reagendar — cita {id_agenda}', html)


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
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Consentimiento informado</title></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr>
    <td style="background:#1A2E4A;padding:28px 32px;text-align:center;">
      <p style="margin:0;color:#C9A84C;font-size:13px;letter-spacing:2px;text-transform:uppercase;">Ortodoncia Richard</p>
      <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">Consentimiento informado</h1>
    </td>
  </tr>
  <tr>
    <td style="padding:32px 32px 24px;">
      <p style="margin:0 0 20px;color:#4A5568;font-size:15px;">Hola <strong>{nombre}</strong>, antes de tu próximo tratamiento necesitamos que firmes tu <strong>{tipo_label}</strong>.</p>
      <p style="margin:0 0 24px;color:#4A5568;font-size:15px;">Puedes leerlo con calma y firmarlo directamente desde tu celular:</p>
      <div style="text-align:center;margin:0 0 24px;">
        <a href="{link}" style="display:inline-block;background:#C9A84C;color:#ffffff;text-decoration:none;padding:14px 28px;border-radius:8px;font-weight:700;font-size:15px;">Firmar consentimiento</a>
      </div>
      <p style="margin:0;color:#718096;font-size:13px;">Si tienes dudas, contáctanos: 📞 <a href="tel:+56222173499" style="color:#1A2E4A;">+56 2 2217 3499</a> &nbsp;|&nbsp; 💬 <a href="https://wa.me/56933558189" style="color:#1A2E4A;">WhatsApp</a></p>
    </td>
  </tr>
  <tr>
    <td style="background:#1A2E4A;padding:20px 32px;text-align:center;">
      <p style="margin:0;color:#8fa8c8;font-size:12px;">Ortodoncia Richard · Paul Harris 10.349, of. 305, Las Condes, Santiago</p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


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
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Copia de tu consentimiento</title></head>
<body style="margin:0;padding:0;background:#f0f5fb;font-family:Arial,Helvetica,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f5fb;padding:32px 16px;">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(26,46,74,0.10);">
  <tr>
    <td style="background:#1A2E4A;padding:28px 32px;text-align:center;">
      <p style="margin:0;color:#C9A84C;font-size:13px;letter-spacing:2px;text-transform:uppercase;">Ortodoncia Richard</p>
      <h1 style="margin:8px 0 0;color:#ffffff;font-size:22px;font-weight:700;">Tu consentimiento firmado</h1>
    </td>
  </tr>
  <tr>
    <td style="padding:32px 32px 24px;">
      <p style="margin:0 0 16px;color:#4A5568;font-size:15px;">Hola <strong>{nombre}</strong>, gracias por firmar tu <strong>{tipo_label}</strong>.</p>
      <p style="margin:0 0 8px;color:#4A5568;font-size:15px;">Adjuntamos una copia en PDF del documento firmado para tus registros.</p>
      <p style="margin:20px 0 0;color:#718096;font-size:13px;">Si tienes dudas, contáctanos: 📞 <a href="tel:+56222173499" style="color:#1A2E4A;">+56 2 2217 3499</a> &nbsp;|&nbsp; 💬 <a href="https://wa.me/56933558189" style="color:#1A2E4A;">WhatsApp</a></p>
    </td>
  </tr>
  <tr>
    <td style="background:#1A2E4A;padding:20px 32px;text-align:center;">
      <p style="margin:0;color:#8fa8c8;font-size:12px;">Ortodoncia Richard · Paul Harris 10.349, of. 305, Las Condes, Santiago</p>
    </td>
  </tr>
</table>
</td></tr>
</table>
</body>
</html>"""


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
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as s:
            s.ehlo(); s.starttls(context=ctx); s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_user, [dest], msg.as_bytes())
        log.info('Copia de consentimiento enviada a %s', dest)
        return {'ok': True, 'canal': 'email'}
    except Exception as e:
        log.error('SMTP copia consentimiento error: %s', e)
        return {'ok': False, 'error': str(e)}


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
        ok = _enviar_email_consentimiento(nombre, paciente.get('email', ''), link, tipo_label)
        return {'ok': ok, 'canal': 'email'}
    if canal == 'whatsapp':
        ok = _enviar_whatsapp_consentimiento(nombre, paciente.get('telefono', ''), link, tipo_label)
        return {'ok': ok, 'canal': 'whatsapp'}
    return {'ok': False, 'error': f'Canal no soportado: {canal}'}


# ── Punto de entrada ─────────────────────────────────────────────────────────

def enviar_confirmacion(cita, cfg=None, canal=None):
    """
    Envía confirmacion de cita al paciente.
    cita: dict con nombre, telefono, email, fecha (date), fecha_legible, hora,
          doctor_nombre, motivo_label, dur_min.
    canal: None (default) = automatico: email primero, WhatsApp de respaldo
           (usado por el agendamiento online y el barrido de confirmaciones).
           'email' | 'whatsapp' = forzado explicitamente (lo usa el asistente
           F2, donde la secretaria elige el canal a mano).
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

    if canal == 'whatsapp':
        ok, err = _enviar_whatsapp(cita, ics)
        if ok:
            return {'ok': True, 'canal': 'whatsapp'}
        return {'ok': False, 'canal': None, 'error': err or 'No se pudo enviar por WhatsApp'}

    if canal == 'email':
        if _enviar_email_smtp(cita, ics):
            return {'ok': True, 'canal': 'email'}
        return {'ok': False, 'canal': None, 'error': 'No se pudo enviar el email'}

    # Automatico (online / barrido): email primero, WhatsApp de respaldo
    if _enviar_email_smtp(cita, ics):
        return {'ok': True, 'canal': 'email'}
    ok, _err = _enviar_whatsapp(cita, ics)
    if ok:
        return {'ok': True, 'canal': 'whatsapp'}

    return {'ok': False, 'canal': None, 'error': 'No se pudo enviar confirmacion'}
