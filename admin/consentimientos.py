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
import hashlib
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
    # Secreto DEDICADO para firmar los links de consentimiento (CONSENT_SECRET).
    # NO se reutiliza ADMIN_TOKEN: si ese token se filtrara (viaja en la extension
    # F2), un atacante podria forjar links validos de cualquier RUT y cosechar
    # nombres de pacientes o firmar consentimientos falsos. En dev local sin
    # CONSENT_SECRET usa un valor fijo NO apto para produccion.
    return os.environ.get('CONSENT_SECRET') or 'dev-secret-cambiar-en-produccion'


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
# Texto TEXTUAL del "CONSENTIMIENTO INFORMADO PARA TRATAMIENTO DE ORTODONCIA"
# v2 (Word) — debe ser una copia EXACTA (no resumen) de lo que el paciente lee
# en consentimiento.html antes de firmar; ambos deben mantenerse sincronizados
# palabra por palabra. Estructura: (título de sección, [(subtítulo|None, texto)]).

SECCIONES = [
    ("Sección I. Introducción y Objetivos del Tratamiento", [
        (None,
         "El presente documento tiene como finalidad informar al paciente acerca "
         "del tratamiento de ortodoncia propuesto, sus beneficios, las posibles "
         "complicaciones y las responsabilidades compartidas durante el proceso. "
         "Este tratamiento busca, además de lograr una sonrisa estéticamente "
         "agradable, mejorar la función masticatoria, la higiene bucal y, en "
         "consecuencia, la salud general del aparato estomatognático (dientes, "
         "encías y articulaciones temporomandibulares – ATM)."),
        (None,
         "Al firmar este consentimiento, el paciente declara que ha recibido y "
         "comprendido toda la información pertinente y que acepta participar de "
         "forma activa y colaborativa en su tratamiento."),
    ]),
    ("Sección II. Datos del Tratamiento y Requisitos para su Éxito", [
        (None,
         "Esta sección detalla aspectos clave del tratamiento y el rol "
         "fundamental que desempeña el paciente para lograr los mejores "
         "resultados:"),
        ("Elección del Enfoque Terapéutico",
         "Mi ortodoncista me ha explicado de manera detallada, considerando mis "
         "características y necesidades, cuál es el tratamiento ideal para mi "
         "caso. Tras haber aclarado todas mis alternativas, resuelto mis dudas y "
         "evaluado los beneficios y riesgos de cada opción, he decidido que el "
         "tratamiento a realizar será: {tratamiento}."),
        ("Duración del Tratamiento",
         "Sé cual es el tiempo estimado de tratamiento que me ha indicado mi "
         "doctor. Reconozco que este plazo es solo una estimación y puede variar "
         "en función de mi crecimiento facial, la respuesta biológica, mi "
         "asistencia a los controles, mi higiene y el cuidado personal de los "
         "aparatos."),
        ("Higiene Oral y Asistencia a Controles",
         "Sé que durante el tratamiento de ortodoncia mantener una higiene oral "
         "óptima es más desafiante, lo que puede aumentar el riesgo de caries, "
         "gingivitis y manchas blancas. Me comprometo a asistir a controles "
         "periódicos con mi dentista general, al menos cada 6 meses o según lo "
         "indique mi ortodoncista."),
        ("Información sobre el Dentista Actual",
         "Confirmo que mi dentista actual (no el ortodoncista, sino quien ve "
         "limpieza, caries, etc.) se llama: {dentista_actual} y que mi último "
         "control se realizó hace menos de 6 meses o se realizará antes de "
         "iniciar el tratamiento."),
        ("Cooperación y Cumplimiento",
         "Entiendo que mi asistencia regular a los controles de ortodoncia y el "
         "estricto seguimiento de las indicaciones del profesional son "
         "esenciales para el éxito del tratamiento. La falta de cooperación "
         "puede prolongar o complicar el proceso."),
        ("Resultados del Tratamiento",
         "Entiendo que, aunque mi tratamiento de ortodoncia se orienta a obtener "
         "el resultado estético y funcional más óptimo, la naturaleza misma de "
         "los procedimientos médicos implica que no es posible garantizar "
         "resultados absolutamente perfectos o definitivos. Reconozco que "
         "existen factores individuales e impredecibles —como las respuestas "
         "biológicas únicas, el crecimiento residual y otras variables— que "
         "pueden influir en el resultado final. Mi ortodoncista podrá "
         "explicarme cuándo se habrá alcanzado el mejor resultado posible "
         "según mi caso particular y, en algunas circunstancias, recomendar "
         "concluir el tratamiento en ese punto, ya que limitarlo a lo logrado "
         "puede ser la opción más segura y beneficiosa para mi salud general a "
         "largo plazo."),
    ]),
    ("Sección III. Riesgos y Efectos Potenciales del Tratamiento", [
        (None,
         "Es fundamental conocer los posibles riesgos y efectos secundarios "
         "asociados al tratamiento:"),
        ("Riesgos Relacionados con la Higiene Oral y la Salud Dental",
         "Entiendo que el uso de aparatos ortodóncicos puede aumentar el riesgo "
         "de desarrollar caries, gingivitis y manchas blancas, especialmente si "
         "no se mantiene una higiene oral adecuada. Entiendo que, en caso de "
         "mantenerse una higiene no adecuada, mi ortodoncista podría indicar el "
         "término anticipado del tratamiento, buscando mi mejor cuidado y "
         "evitando lesiones como caries o enfermedad a las encías."),
        ("Factores Individuales y Genéticos",
         "Acepto que existen factores individuales —como la forma de las "
         "raíces, la densidad ósea o predisposiciones genéticas— que pueden "
         "influir en la respuesta al tratamiento y en la duración o resultados "
         "finales."),
        ("Cambios Posteriores al Tratamiento Activo",
         "Soy consciente de que, una vez finalizado el periodo activo del "
         "tratamiento de ortodoncia, es posible que los dientes tiendan a "
         "moverse con el tiempo. El uso adecuado y continuo de retenedores es "
         "esencial para mantener los resultados obtenidos."),
        ("Síntomas de Trastornos Temporomandibulares (TTM)",
         "Entiendo que, si bien el tratamiento de ortodoncia en sí no causa "
         "disfunción temporomandibular, en algunos casos puede ocurrir que "
         "justo coincida el desarrollo de síntomas como dolor o alteración "
         "funcional en la ATM y músculos de la masticación durante el "
         "tratamiento de ortodoncia. Estos síntomas, de manifestarse, serán "
         "evaluados por el especialista."),
        ("Cirugía Bucal y Maxilofacial",
         "Estoy informado de que, en situaciones específicas, podría ser "
         "necesaria la realización de procedimientos quirúrgicos "
         "complementarios, tales como cirugía bucal o maxilofacial (incluyendo "
         "extracciones dentarias u otros procedimientos invasivos). Reconozco "
         "que los riesgos quirúrgicos y, eventualmente, el de anestesia local o "
         "general, deben ser discutidos con su dentista y/o cirujano "
         "maxilofacial, con anticipación al procedimiento quirúrgico."),
        ("Caninos Impactados",
         "Conozco que, en tratamientos orientados a solucionar problemas de "
         "caninos impactados o incluidos, el resultado puede no ser predecible "
         "en su totalidad. En algunos casos, podría ser necesaria la extracción "
         "del canino, lo que demandaría procedimientos adicionales y podría "
         "implicar costos extras."),
        ("Hueso Atrofiado o Insuficiencia Ósea",
         "Entiendo que en casos en los que exista un hueso atrofiado o "
         "insuficiencia de hueso alveolar, podría requerirse la realización de "
         "procedimientos adicionales (por ejemplo, una corticotomía) para "
         "permitir el adecuado movimiento de los dientes. Estos procedimientos "
         "conllevan riesgos adicionales y costos que serán de mi "
         "responsabilidad."),
        ("Casos con Extracciones Dentarias",
         "Acepto que, en función de discrepancias en el tamaño de los dientes o "
         "la necesidad de alinear la mordida, puede ser necesario extraer uno o "
         "más dientes. Estos procedimientos, que son complementarios al "
         "tratamiento de ortodoncia, tienen riesgos propios y no están "
         "incluidos en el costo base del tratamiento."),
        ("Uso de microtornillos/miniplacas",
         "Comprendo que para optimizar ciertos movimientos dentales pueden "
         "utilizarse microtornillos o miniplacas. Reconozco que aproximadamente "
         "en un 20% de los casos estos dispositivos pueden presentar "
         "complicaciones leves, que podrían requerir su retirada o "
         "reinstalación, con costos y riesgos adicionales."),
        ("Acortamiento de Raíces",
         "Estoy informado que es común que durante el tratamiento se puede "
         "producir un remodelado de las raíces (acortamiento o redondeamiento), "
         "lo cual, en la mayoría de los casos es leve y sin mayor relevancia. En "
         "situaciones excepcionales, este acortamiento puede resultar "
         "significativo, y su magnitud dependerá de factores individuales y "
         "genéticos, y será objeto de monitoreo durante el proceso "
         "terapéutico."),
    ]),
    ("Sección IV. Procedimientos Complementarios y Costos Adicionales", [
        (None,
         "Algunos casos pueden requerir procedimientos extras que no están "
         "incluidos en el costo base del tratamiento:"),
        (None,
         "Entiendo que en determinadas situaciones podría requerirse la "
         "realización de procedimientos complementarios, como extracciones, la "
         "instalación de minitornillos/miniplacas o rehabilitaciones con "
         "prótesis dentales. Estos procedimientos, de considerarse necesarios, "
         "tendrán un costo adicional que correrá por mi cuenta."),
    ]),
    ("Sección V. Uso de Biomateriales e Instrumental Clínico", [
        (None,
         "Durante el tratamiento se utilizarán diversos biomateriales y equipos "
         "especializados:"),
        (None,
         "Comprendo que se emplean biomateriales e instrumental clínico durante "
         "el tratamiento. Aunque estos productos y dispositivos son generalmente "
         "seguros, en raras ocasiones pueden provocar reacciones alérgicas, "
         "irritaciones o leves lesiones en las mucosas o la piel de la región "
         "bucal."),
    ]),
    ("Sección VI. Registro de Condiciones Médicas y Tratamientos Actuales", [
        (None,
         "La conocida seguridad y la personalización del tratamiento requieren "
         "conocer el estado de salud del paciente:"),
        (None,
         "Declaro haber informado de manera completa sobre mis condiciones "
         "médicas, alergias, tratamientos actuales o medicamentos que consumo "
         "(por ejemplo: utilización de bisfosfonatos, tratamientos hormonales, "
         "etc.), sabiendo que estos datos pueden influir en la evolución y "
         "resultado del tratamiento de ortodoncia."),
    ]),
    ("Sección VII. Confirmación de Entendimiento y Autorización", [
        (None,
         "Este es el compromiso final en el que el paciente confirma que ha "
         "entendido y acepta las condiciones expuestas:"),
        (None,
         "Confirmo que he leído y comprendido detalladamente el contenido de "
         "este consentimiento informado, que todas mis dudas han sido "
         "aclaradas y que autorizo de manera voluntaria el inicio del "
         "tratamiento de ortodoncia según lo explicado por mi especialista."),
    ]),
]


def _hash_firma(datos):
    """SHA-256 sobre los datos firmados (integridad — cambia si algo se altera)."""
    canon = '|'.join(str(datos.get(k, '')) for k in (
        'rut_fmt', 'nombre', 'tipo', 'tratamiento', 'dentista_actual',
        'quien_firma', 'apoderado_nombre', 'apoderado_rut', 'fecha', 'firma_png',
    ))
    return hashlib.sha256(canon.encode('utf-8')).hexdigest()


def generar_pdf(datos):
    """
    datos: dict con nombre, rut_fmt, tipo, tratamiento, dentista_actual,
           quien_firma, apoderado_nombre, apoderado_rut, fecha, firma_png
           (data URL 'data:image/png;base64,...' del canvas de firma), y
           opcionalmente consent_id, ip (para el sello de firma electrónica).
    Devuelve la ruta (Path) del PDF generado.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage, Table, TableStyle
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib import colors
    import io

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    rut_archivo = _limpiar_rut(datos.get('rut_fmt', ''))
    tipo = datos.get('tipo', 'ortodoncia')
    marca_tiempo = datetime.now()
    ruta = PDF_DIR / f"{rut_archivo}_{tipo}_{marca_tiempo.strftime('%Y%m%d-%H%M%S')}.pdf"

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle('titulo', parent=styles['Title'], fontSize=14)
    seccion = ParagraphStyle('seccion', parent=styles['Heading2'], fontSize=11, spaceBefore=10)
    subtitulo = ParagraphStyle('subtitulo', parent=styles['Heading4'], fontSize=9.5, spaceBefore=6, spaceAfter=2)
    cuerpo = ParagraphStyle('cuerpo', parent=styles['BodyText'], fontSize=9.5,
                            alignment=TA_JUSTIFY, leading=13)
    sello_txt = ParagraphStyle('sello', parent=styles['BodyText'], fontSize=8, leading=11, textColor=colors.HexColor('#1A2E4A'))

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    story = [
        Paragraph('Clínica de Ortodoncia C. Richard', titulo),
        Paragraph(TIPOS_DOCUMENTO.get(tipo, 'Consentimiento Informado'), styles['Heading3']),
        Spacer(1, 10),
    ]
    fmt_kwargs = {
        'tratamiento': datos.get('tratamiento') or '(no especificado)',
        'dentista_actual': datos.get('dentista_actual') or '(no especificado)',
    }
    for titulo_sec, bloques in SECCIONES:
        story.append(Paragraph(titulo_sec, seccion))
        for sub, texto in bloques:
            if sub:
                story.append(Paragraph(sub, subtitulo))
            story.append(Paragraph(texto.format(**fmt_kwargs), cuerpo))

    story.append(Spacer(1, 14))
    story.append(Paragraph('Sección Final: Firma y Datos de Confirmación', seccion))
    story.append(Paragraph(f"Nombre del Paciente: {datos.get('nombre', '')}", cuerpo))
    story.append(Paragraph(f"RUT del Paciente: {datos.get('rut_fmt', '')}", cuerpo))
    if datos.get('quien_firma') == 'apoderado':
        story.append(Paragraph(
            f"Nombre del Apoderado que firma: {datos.get('apoderado_nombre', '')} "
            f"(RUT {datos.get('apoderado_rut', '')}) — solo para pacientes menores de 18 años", cuerpo))
    story.append(Paragraph(f"Fecha: {datos.get('fecha', '')}", cuerpo))

    firma_png = datos.get('firma_png', '') or ''
    if firma_png.startswith('data:image'):
        img_bytes = base64.b64decode(firma_png.split(',', 1)[1])
        story.append(Spacer(1, 8))
        story.append(Paragraph('Firma del Paciente/Apoderado:', cuerpo))
        story.append(RLImage(io.BytesIO(img_bytes), width=6 * cm, height=2.5 * cm))

    # ── Sello de firma electrónica (trazabilidad e integridad) ───────────────
    consent_id = datos.get('consent_id') or '(sin id)'
    hash_doc = _hash_firma(datos)[:32]
    ip = datos.get('ip') or '(no registrada)'
    sello_html = (
        '<b>FIRMA ELECTRÓNICA</b><br/>'
        f'Este documento fue firmado electrónicamente el '
        f'{marca_tiempo.strftime("%d-%m-%Y")} a las {marca_tiempo.strftime("%H:%M:%S")} '
        f'(hora de Chile).<br/>'
        f'ID de verificación: {consent_id}<br/>'
        f'Hash de integridad (SHA-256): {hash_doc}<br/>'
        f'Dirección IP de origen: {ip}'
    )
    sello = Table([[Paragraph(sello_html, sello_txt)]], colWidths=[16.5 * cm])
    sello.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#C9A84C')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F0F5FB')),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 14))
    story.append(sello)

    doc.build(story)
    return ruta
