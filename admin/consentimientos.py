"""
consentimientos.py - Consentimientos informados firmados digitalmente (Ortodoncia Richard)

Flujo: la secretaria dispara el envio desde el asistente F2 (mail / WhatsApp /
tablet) -> el paciente (o su apoderado) firma en consentimiento.html (celular o
tablet de recepcion) -> se genera el PDF firmado -> queda registrado localmente,
pendiente de subir a la ficha DentiDesk (pestaña "Informes") mediante un script
nocturno con Claude for Chrome (aun no implementado).

Piezas:
  - Token firmado y con expiracion (itsdangerous) para el link de celular: el
    paciente no necesita loguearse, el token ES la credencial.
  - Cola de UN item para la tablet de recepcion: F2 empuja {rut, tipo, id} y la
    tablet hace polling (ver /api/consentimiento/tablet/cola en server.py).
  - Registro de estado por consentimiento (enviado -> firmado -> subido), para
    la futura pestaña "Consentimientos" del panel admin.
  - Generacion del PDF final (reportlab) con el texto de cada seccion + los
    datos ingresados + la firma (imagen PNG en base64 desde el canvas).

Reutiliza pacientes.py (misma base local RUT -> nombre/email/telefono que usa
el agendamiento) para prellenar datos sin depender de un endpoint de busqueda
por RUT en DentiDesk (no existe).
"""

import os
import re
import json
import uuid
import base64
import threading
from pathlib import Path
from datetime import datetime, date

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import pacientes

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent

REGISTRO_PATH = Path(os.environ.get('CONSENTIMIENTOS_REGISTRO_PATH',
                                    _BASE_DIR / 'consentimientos_registro.json'))
COLA_TABLET_PATH = Path(os.environ.get('CONSENTIMIENTOS_COLA_PATH',
                                       _BASE_DIR / 'consentimientos_cola_tablet.json'))
PDF_DIR = Path(os.environ.get('CONSENTIMIENTOS_PDF_DIR',
                              _BASE_DIR / 'consentimientos_firmados'))

_LOCK = threading.Lock()

TIPOS_DOCUMENTO = {
    'ortodoncia': 'Consentimiento Informado — Tratamiento de Ortodoncia',
    # Futuro: 'rehabilitacion': 'Consentimiento Informado — Rehabilitación Oral e Implantología'
}

TOKEN_MAX_AGE_SEGUNDOS = 30 * 24 * 3600  # 30 dias


def _secret():
    # Reutiliza ADMIN_TOKEN como secreto de firma si no hay uno dedicado
    # (CONSENT_SECRET). En dev local sin ninguno de los dos, usa un valor fijo
    # NO apto para produccion (mismo criterio de _check_admin_token en server.py).
    return (os.environ.get('CONSENT_SECRET') or os.environ.get('ADMIN_TOKEN')
            or 'dev-secret-cambiar-en-produccion')


def _serializer():
    return URLSafeTimedSerializer(_secret(), salt='consentimiento')


def _limpiar_rut(rut):
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def _formatear_rut(rut):
    limpio = _limpiar_rut(rut)
    if len(limpio) < 2:
        return limpio
    cuerpo, dv = limpio[:-1], limpio[-1]
    cuerpo_fmt = re.sub(r'(?<=\d)(?=(\d{3})+(?!\d))', '.', cuerpo)
    return f'{cuerpo_fmt}-{dv}'


# ── Token de celular ─────────────────────────────────────────────────────────

def generar_token(rut, tipo, consent_id):
    return _serializer().dumps({'rut': _limpiar_rut(rut), 'tipo': tipo, 'id': consent_id})


def validar_token(token, max_age=TOKEN_MAX_AGE_SEGUNDOS):
    """Devuelve {'rut', 'tipo', 'id'} o None si el token es invalido/vencido."""
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ── Registro (estado de cada consentimiento) ────────────────────────────────

def _load_registro():
    if REGISTRO_PATH.exists():
        try:
            return json.loads(REGISTRO_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return {}
    return {}


def _save_registro(idx):
    REGISTRO_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRO_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, REGISTRO_PATH)


def crear_registro(rut, tipo, canal):
    """canal: 'mail' | 'whatsapp' | 'tablet'. Devuelve el id del registro."""
    consent_id = uuid.uuid4().hex[:12]
    with _LOCK:
        idx = _load_registro()
        idx[consent_id] = {
            'rut': _limpiar_rut(rut),
            'tipo': tipo,
            'canal': canal,
            'estado': 'enviado',
            'creado': datetime.now().isoformat(timespec='seconds'),
            'firmado': None,
            'pdf_path': None,
            'subido_dentidesk': False,
            'respaldo_drive': None,   # None = aún no se firma; True/False tras el intento
        }
        _save_registro(idx)
    return consent_id


def obtener_registro(consent_id):
    return _load_registro().get(consent_id)


def marcar_firmado(consent_id, pdf_path):
    with _LOCK:
        idx = _load_registro()
        if consent_id in idx:
            idx[consent_id]['estado'] = 'firmado'
            idx[consent_id]['firmado'] = datetime.now().isoformat(timespec='seconds')
            idx[consent_id]['pdf_path'] = str(pdf_path)
            _save_registro(idx)


def marcar_respaldo_drive(consent_id, ok, file_id=None):
    with _LOCK:
        idx = _load_registro()
        if consent_id in idx:
            idx[consent_id]['respaldo_drive'] = bool(ok)
            if file_id:
                idx[consent_id]['drive_file_id'] = file_id
            _save_registro(idx)


def marcar_subido_dentidesk(consent_id):
    with _LOCK:
        idx = _load_registro()
        if consent_id in idx:
            idx[consent_id]['estado'] = 'subido'
            idx[consent_id]['subido_dentidesk'] = True
            _save_registro(idx)


def listar(estado=None):
    idx = _load_registro()
    items = [{'id': k, **v} for k, v in idx.items()]
    if estado:
        items = [i for i in items if i['estado'] == estado]
    return sorted(items, key=lambda i: i['creado'], reverse=True)


# ── Cola de la tablet (kiosco) ───────────────────────────────────────────────
# La secretaria, desde F2, empuja {rut, tipo, id} a esta cola. La tablet hace
# polling (GET /api/consentimiento/tablet/cola) y, al detectar un item, salta
# directo a la pantalla de confirmacion de identidad con ese paciente.
# Cola de UN solo item: una tablet en recepcion, un consentimiento a la vez.

def poner_en_cola_tablet(rut, tipo, consent_id):
    with _LOCK:
        COLA_TABLET_PATH.parent.mkdir(parents=True, exist_ok=True)
        COLA_TABLET_PATH.write_text(json.dumps({
            'rut': _limpiar_rut(rut), 'tipo': tipo, 'id': consent_id,
            'ts': datetime.now().isoformat(timespec='seconds'),
        }), encoding='utf-8')


def obtener_cola_tablet():
    if not COLA_TABLET_PATH.exists():
        return None
    try:
        return json.loads(COLA_TABLET_PATH.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def limpiar_cola_tablet():
    with _LOCK:
        if COLA_TABLET_PATH.exists():
            COLA_TABLET_PATH.unlink()


# ── Datos del paciente (prellenado, version publica sin email/telefono) ──────

def datos_paciente(rut):
    """{'nombre', 'rut_fmt'} usando la base local (pacientes.py), o None si el
    RUT no esta en la base (paciente nuevo aun no sembrado). Version segura
    para exponer en endpoints publicos (celular/tablet) — sin email ni
    telefono. Para enviar el link (email/WhatsApp) usar pacientes.lookup()
    directamente, que si trae esos datos."""
    rec = pacientes.lookup(rut)
    if not rec:
        return None
    nombre = f"{rec.get('nombres', '')} {rec.get('apellidos', '')}".strip()
    return {'nombre': nombre, 'rut_fmt': _formatear_rut(rut)}


# ── Generacion del PDF firmado ───────────────────────────────────────────────
# El texto de cada seccion debe reflejar el mismo contenido mostrado en
# consentimiento.html (Sección I a VII, basadas en el consentimiento v2).

SECCIONES = [
    ("Sección I — Datos del tratamiento",
     "Mi ortodoncista me ha explicado de manera detallada, considerando mis "
     "características y necesidades, cuál es el tratamiento ideal para mi caso. "
     "Tras haber aclarado todas mis alternativas, resuelto mis dudas y evaluado "
     "los beneficios y riesgos de cada opción, he decidido que el tratamiento a "
     "realizar será: {tratamiento}. Sé cuál es el tiempo estimado de tratamiento "
     "que me ha indicado mi doctor y reconozco que es solo una estimación. "
     "Confirmo que mi dentista actual es: {dentista_actual}, y que mi último "
     "control se realizó hace menos de 6 meses o se realizará antes de iniciar "
     "el tratamiento."),
    ("Sección II — Cooperación y resultados",
     "Entiendo que mi asistencia regular a los controles de ortodoncia y el "
     "estricto seguimiento de las indicaciones del profesional son esenciales "
     "para el éxito del tratamiento. Entiendo que no es posible garantizar "
     "resultados absolutamente perfectos o definitivos."),
    ("Sección III — Riesgos y efectos potenciales",
     "Entiendo los riesgos asociados al tratamiento de ortodoncia: aumento de "
     "caries/gingivitis, factores individuales y genéticos, movimiento dentario "
     "posterior al tratamiento activo, síntomas de TTM, eventual cirugía bucal o "
     "maxilofacial, manejo de caninos impactados, hueso atrofiado, extracciones "
     "dentarias, uso de microtornillos/miniplacas y acortamiento de raíces."),
    ("Sección IV — Procedimientos complementarios y costos",
     "Entiendo que podrían requerirse procedimientos complementarios (extracciones, "
     "minitornillos/miniplacas, rehabilitaciones con prótesis) con costo adicional "
     "a mi cargo."),
    ("Sección V — Biomateriales e instrumental clínico",
     "Comprendo que se emplean biomateriales e instrumental clínico que, en raras "
     "ocasiones, pueden provocar reacciones alérgicas o leves lesiones."),
    ("Sección VI — Condiciones médicas",
     "Declaro haber informado de manera completa mis condiciones médicas, "
     "alergias, tratamientos actuales o medicamentos que consumo."),
    ("Sección VII — Confirmación de entendimiento",
     "Confirmo que he leído y comprendido detalladamente el contenido de este "
     "consentimiento informado, que todas mis dudas han sido aclaradas y que "
     "autorizo de manera voluntaria el inicio del tratamiento de ortodoncia."),
]


def generar_pdf(datos):
    """
    datos: dict con nombre, rut_fmt, tipo, tratamiento, dentista_actual,
           quien_firma, apoderado_nombre, apoderado_rut, fecha, firma_png
           (data URL 'data:image/png;base64,...' del canvas de firma).
    Devuelve la ruta (Path) del PDF generado.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
    from reportlab.lib.enums import TA_JUSTIFY
    import io

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    rut_archivo = _limpiar_rut(datos.get('rut_fmt', ''))
    tipo = datos.get('tipo', 'ortodoncia')
    marca_tiempo = datetime.now().strftime('%Y%m%d-%H%M%S')
    ruta = PDF_DIR / f"{rut_archivo}_{tipo}_{marca_tiempo}.pdf"

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle('titulo', parent=styles['Title'], fontSize=14)
    seccion = ParagraphStyle('seccion', parent=styles['Heading2'], fontSize=11, spaceBefore=10)
    cuerpo = ParagraphStyle('cuerpo', parent=styles['BodyText'], fontSize=9.5,
                            alignment=TA_JUSTIFY, leading=13)

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    story = [
        Paragraph('Clínica de Ortodoncia C. Richard', titulo),
        Paragraph(TIPOS_DOCUMENTO.get(tipo, 'Consentimiento Informado'), styles['Heading3']),
        Spacer(1, 10),
    ]
    for titulo_sec, texto in SECCIONES:
        texto_fmt = texto.format(
            tratamiento=datos.get('tratamiento') or '(no especificado)',
            dentista_actual=datos.get('dentista_actual') or '(no especificado)',
        )
        story.append(Paragraph(titulo_sec, seccion))
        story.append(Paragraph(texto_fmt, cuerpo))

    story.append(Spacer(1, 16))
    story.append(Paragraph('Datos del firmante', seccion))
    story.append(Paragraph(f"Paciente: {datos.get('nombre', '')} — RUT {datos.get('rut_fmt', '')}", cuerpo))
    if datos.get('quien_firma') == 'apoderado':
        story.append(Paragraph(
            f"Firma en calidad de apoderado/representante legal: "
            f"{datos.get('apoderado_nombre', '')} — RUT {datos.get('apoderado_rut', '')}", cuerpo))
    story.append(Paragraph(f"Fecha: {datos.get('fecha', '')}", cuerpo))

    firma_png = datos.get('firma_png', '') or ''
    if firma_png.startswith('data:image'):
        img_bytes = base64.b64decode(firma_png.split(',', 1)[1])
        story.append(Spacer(1, 10))
        story.append(Paragraph('Firma:', cuerpo))
        story.append(RLImage(io.BytesIO(img_bytes), width=6 * cm, height=2.5 * cm))

    doc.build(story)
    return ruta
