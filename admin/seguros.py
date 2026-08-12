"""
seguros.py - Formularios de seguros complementarios (Ortodoncia Richard)

Flujo: la secretaria, con la cita abierta en DentiDesk, aprieta "Seguro
complementario" en el asistente F2 -> se abre seguros_secretaria.html con los
datos del paciente en query params -> elige aseguradora y prestaciones (los
nombres/codigos se traducen al vocabulario de cada aseguradora via mapeo) ->
el backend rellena el PDF OFICIAL de la aseguradora (AcroForm u overlay de
coordenadas) estampando la firma+timbre del doctor -> se envia por email al
paciente (WhatsApp queda para cuando exista plantilla Meta aprobada).

Piezas (mismo molde que consentimientos.py):
  - Persistencia en JSON (disco persistente de Render, rutas por env var):
    aseguradoras, catalogo de prestaciones internas (arancel propio), mapeo
    prestacion->aseguradora, mapeo motivo->prestaciones sugeridas, preferencia
    y datos extra por paciente (RUT), firmas de doctores y registro/historial.
  - Rellenado de PDF: pypdf (campos AcroForm) + overlay reportlab fusionado
    con pypdf (PDFs planos y SIEMPRE la imagen de firma+timbre).
  - Token firmado (itsdangerous) SOLO para servir el PDF de vista previa en
    un <iframe> (un iframe no puede mandar el header X-Admin-Token).

Reutiliza pacientes.py para prellenar nombre/email/telefono por RUT.
"""

import os
import io
import json
import uuid
import threading
from pathlib import Path
from datetime import datetime

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import pacientes

import fechas
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.


def ahora_chile():
    """datetime actual en hora de Chile (Render corre en UTC). Ver fechas.py."""
    return fechas.ahora_chile_aware()


_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent

ASEGURADORAS_PATH = Path(os.environ.get('SEGUROS_ASEGURADORAS_PATH',
                                        _BASE_DIR / 'seguros_aseguradoras.json'))
PRESTACIONES_PATH = Path(os.environ.get('SEGUROS_PRESTACIONES_PATH',
                                        _BASE_DIR / 'seguros_prestaciones.json'))
MAPEO_PREST_PATH = Path(os.environ.get('SEGUROS_MAPEO_PREST_PATH',
                                       _BASE_DIR / 'seguros_mapeo_prestaciones.json'))
MAPEO_MOTIVOS_PATH = Path(os.environ.get('SEGUROS_MAPEO_MOTIVOS_PATH',
                                         _BASE_DIR / 'seguros_mapeo_motivos.json'))
PACIENTES_PATH = Path(os.environ.get('SEGUROS_PACIENTES_PATH',
                                     _BASE_DIR / 'seguros_pacientes.json'))
FIRMAS_INDEX_PATH = Path(os.environ.get('SEGUROS_FIRMAS_INDEX_PATH',
                                        _BASE_DIR / 'seguros_firmas.json'))
REGISTRO_PATH = Path(os.environ.get('SEGUROS_REGISTRO_PATH',
                                    _BASE_DIR / 'seguros_registro.json'))

PLANTILLAS_DIR = Path(os.environ.get('SEGUROS_PLANTILLAS_DIR',
                                     _BASE_DIR / 'seguros_plantillas'))
FIRMAS_DIR = Path(os.environ.get('SEGUROS_FIRMAS_DIR',
                                 _BASE_DIR / 'seguros_firmas'))
GENERADOS_DIR = Path(os.environ.get('SEGUROS_GENERADOS_DIR',
                                    _BASE_DIR / 'seguros_generados'))

_LOCK = threading.Lock()

# Semilla versionada en el repo (admin/seguros_seed/): mapeos ya construidos +
# PDFs oficiales de las aseguradoras. En el primer arranque (disco persistente
# vacio) se copia al directorio de datos; despues manda lo del disco (editable
# desde el panel sin deploy).
_SEED_DIR = Path(__file__).parent / 'seguros_seed'


def _aplicar_seed():
    """Auto-reparable en cada arranque. Copia los PDFs de plantilla si faltan y,
    por cada aseguradora del seed, RELLENA su mapeo_campos/tipo_plantilla/
    plantilla_pdf SOLO si están vacíos en el disco — sin pisar lo que el usuario
    haya editado (nombre, activa) ni un mapeo no vacío. Antes solo sembraba si el
    archivo NO existía; eso hacía que, una vez creada cualquier aseguradora desde
    el panel, el mapeo correcto del seed nunca se re-aplicara en los deploys."""
    try:
        # 1) PDFs de plantilla al disco persistente (si faltan)
        if _SEED_DIR.exists():
            PLANTILLAS_DIR.mkdir(parents=True, exist_ok=True)
            for pdf in _SEED_DIR.glob('*.pdf'):
                destino = PLANTILLAS_DIR / pdf.name
                if not destino.exists():
                    destino.write_bytes(pdf.read_bytes())

        seed_aseg = _SEED_DIR / 'aseguradoras_seed.json'
        if not seed_aseg.exists():
            return
        seed = json.loads(seed_aseg.read_text(encoding='utf-8'))
        idx = _load(ASEGURADORAS_PATH)
        cambiado = False
        for key, sv in seed.items():
            cur = idx.get(key)
            if cur is None:
                idx[key] = sv                      # no existe → sembrar completa
                cambiado = True
                continue
            # existe → rellenar SOLO lo que falta (self-heal), sin tocar nombre/activa
            if not (cur.get('mapeo_campos') or {}) and (sv.get('mapeo_campos') or {}):
                cur['mapeo_campos'] = sv['mapeo_campos']; cambiado = True
            if not cur.get('tipo_plantilla') and sv.get('tipo_plantilla'):
                cur['tipo_plantilla'] = sv['tipo_plantilla']; cambiado = True
            if not cur.get('plantilla_pdf') and sv.get('plantilla_pdf'):
                cur['plantilla_pdf'] = sv['plantilla_pdf']; cambiado = True
        if cambiado:
            ASEGURADORAS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _save(ASEGURADORAS_PATH, idx)
    except Exception as e:
        # La semilla es best-effort: sin ella el panel permite configurar a mano.
        # Pero NO en silencio — este mismo `except` fue el que oculto el NameError
        # que dejo a las 7 aseguradoras sin mapeo de campos (ver nota abajo). Si
        # vuelve a fallar, que quede en el log de Render.
        print(f'[seguros] no se pudo aplicar la semilla de aseguradoras: {e!r}')


# NOTA: la llamada a _aplicar_seed() va al FINAL del módulo, después de que
# _load/_save estén definidas. Si se llama aquí (antes de definirlas), lanza
# NameError que el except se traga en silencio y el seed nunca se aplica — ése
# fue exactamente el bug que dejó a todas las aseguradoras sin mapeo de campos.

# Vida util del token que protege la URL del PDF de vista previa (el iframe
# no puede mandar headers). Corto a proposito: solo cubre la sesion de trabajo.
PDF_TOKEN_MAX_AGE_SEGUNDOS = 4 * 3600


def _secret():
    # Secreto dedicado (SEGUROS_SECRET); cae a CONSENT_SECRET si no esta para
    # no exigir otra env var en Render, y a un valor de dev en local.
    return (os.environ.get('SEGUROS_SECRET')
            or os.environ.get('CONSENT_SECRET')
            or 'dev-secret-cambiar-en-produccion')


def _serializer():
    return URLSafeTimedSerializer(_secret(), salt='seguro-pdf')


def _limpiar_rut(rut):
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def _formatear_rut(rut):
    import re
    limpio = _limpiar_rut(rut)
    if len(limpio) < 2:
        return limpio
    cuerpo, dv = limpio[:-1], limpio[-1]
    cuerpo_fmt = re.sub(r'(?<=\d)(?=(\d{3})+(?!\d))', '.', cuerpo)
    return f'{cuerpo_fmt}-{dv}'


# ── Persistencia JSON ────────────────────────────────────────────────────────
# A diferencia de los otros modulos, seguros maneja VARIOS archivos (aseguradoras,
# prestaciones, mapeos, pacientes, firmas, registro), asi que la ruta viaja como
# parametro. Un store por ruta, cacheado: cada archivo necesita su propio lock,
# y crear uno nuevo en cada llamada haria que dos escrituras al mismo archivo no
# se excluyeran entre si. Ver jsonstore.py.
_STORES = {}
_STORES_LOCK = threading.Lock()


def _store(path):
    clave = str(path)
    with _STORES_LOCK:
        s = _STORES.get(clave)
        if s is None:
            s = _STORES[clave] = jsonstore.JsonStore(path, default={}, indent=2)
        return s


def _load(path):
    return _store(path).load()


def _save(path, data):
    _store(path).save(data)


# ── Aseguradoras ─────────────────────────────────────────────────────────────
# {key: {nombre, activa, plantilla_pdf, tipo_plantilla: 'acroform'|'overlay',
#        mapeo_campos: {campo_logico: {'campo': nombreAcroForm} |
#                       {'pagina': 1, 'x': 100, 'y': 200, 'fontsize': 9} |
#                       (firma) {'pagina':1,'x':..,'y':..,'ancho':..,'alto':..}},
#        max_prestaciones_por_form}}

def listar_aseguradoras(solo_activas=True):
    idx = _load(ASEGURADORAS_PATH)
    items = [{'key': k, **v} for k, v in idx.items()]
    if solo_activas:
        items = [a for a in items if a.get('activa', True)]
    return sorted(items, key=lambda a: a.get('nombre', ''))


def obtener_aseguradora(key):
    return _load(ASEGURADORAS_PATH).get(key)


def guardar_aseguradora(key, datos):
    with _LOCK:
        idx = _load(ASEGURADORAS_PATH)
        actual = idx.get(key, {})
        actual.update(datos)
        actual.setdefault('activa', True)
        actual.setdefault('mapeo_campos', {})
        actual.setdefault('max_prestaciones_por_form', 6)
        idx[key] = actual
        _save(ASEGURADORAS_PATH, idx)
    return idx[key]


# ── Catalogo de prestaciones internas (arancel propio) ──────────────────────
# {prest_id: {nombre, precio_arancel, activa, origen, motivo_scheduling_key}}

def listar_prestaciones(solo_activas=True):
    idx = _load(PRESTACIONES_PATH)
    items = [{'id': k, **v} for k, v in idx.items()]
    if solo_activas:
        items = [p for p in items if p.get('activa', True)]
    return sorted(items, key=lambda p: p.get('nombre', ''))


def guardar_prestacion(prest_id, datos):
    with _LOCK:
        idx = _load(PRESTACIONES_PATH)
        if not prest_id:
            prest_id = 'prest_' + uuid.uuid4().hex[:8]
        actual = idx.get(prest_id, {})
        actual.update(datos)
        actual.setdefault('activa', True)
        actual.setdefault('origen', 'manual')
        idx[prest_id] = actual
        _save(PRESTACIONES_PATH, idx)
    return prest_id


def seed_desde_motivos(cfg):
    """Siembra el catalogo desde los motivos del agendamiento online
    (scheduling_config.json -> 'motivos'). Idempotente: no duplica los que ya
    tienen ese motivo_scheduling_key. Los ~186 motivos internos completos
    (motivos_id_reason_extra) NO se siembran automaticamente — serian ruido;
    se agregan a mano los que de verdad se cobran a seguros."""
    creados = 0
    with _LOCK:
        idx = _load(PRESTACIONES_PATH)
        existentes = {v.get('motivo_scheduling_key') for v in idx.values()}
        for key, m in (cfg.get('motivos') or {}).items():
            if not isinstance(m, dict):
                continue  # entradas tipo "_comment"
            if key in existentes or m.get('oculto'):
                continue
            idx['prest_' + uuid.uuid4().hex[:8]] = {
                'nombre': m.get('label', key),
                'precio_arancel': 0,
                'activa': True,
                'origen': 'motivo_scheduling',
                'motivo_scheduling_key': key,
            }
            creados += 1
        _save(PRESTACIONES_PATH, idx)
    return creados


# ── Interpretacion de la glosa de boleta ────────────────────────────────────
# Cada prestacion puede definir 'glosas_boleta': lista de alias (substrings) con
# que la clinica la escribe en la glosa del DTE (ej. "CONTROL MENSUAL",
# "RECEMENTACION"). 'absorbe_saldo': True marca la prestacion (tipicamente el
# control/mensualidad) cuyo valor se calcula como monto_boleta - suma del resto,
# segun el modelo de cobro de la clinica (precio de lista fijo por sesion que se
# desglosa entre el control y los extras sin cambiar el total).

def _normalizar_texto(s):
    import unicodedata
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.upper()


def interpretar_glosa(glosa):
    """Detecta prestaciones internas presentes en la glosa de una boleta.
    Devuelve lista de prestaciones (dicts del catalogo) sin duplicados, en el
    orden del catalogo. Matching por substring de cada alias, sin tildes y
    case-insensitive."""
    texto = _normalizar_texto(glosa)
    if not texto.strip():
        return []
    out = []
    for p in listar_prestaciones():
        aliases = p.get('glosas_boleta') or []
        for a in aliases:
            a_norm = _normalizar_texto(a).strip()
            if a_norm and a_norm in texto:
                out.append(p)
                break
    return out


def filas_desde_boleta(glosa, monto_total, aseguradora_key, fecha=''):
    """Convierte la glosa + monto de una boleta en filas de prestaciones para
    el formulario, traducidas a la aseguradora. Reparto de valores: cada
    prestacion parte con su precio_arancel interno; si UNA de las detectadas
    tiene absorbe_saldo, su valor = monto_total - suma de las demas (>= 0).
    Devuelve (filas, no_reconocido:bool)."""
    detectadas = interpretar_glosa(glosa)
    if not detectadas:
        return [], True
    mapeo = mapeo_prestaciones()
    try:
        monto_total = int(monto_total or 0)
    except (TypeError, ValueError):
        monto_total = 0

    # valores base
    base = []
    for p in detectadas:
        try:
            v = int(p.get('precio_arancel') or 0)
        except (TypeError, ValueError):
            v = 0
        base.append({'p': p, 'valor': v})

    absorbentes = [b for b in base if b['p'].get('absorbe_saldo')]
    if monto_total > 0 and len(absorbentes) == 1:
        resto = sum(b['valor'] for b in base if not b['p'].get('absorbe_saldo'))
        if monto_total - resto > 0:
            absorbentes[0]['valor'] = monto_total - resto
    elif monto_total > 0 and len(base) == 1:
        # una sola prestacion: vale el total de la boleta
        base[0]['valor'] = monto_total

    filas = []
    for b in base:
        p = b['p']
        items = (mapeo.get(p['id']) or {}).get(aseguradora_key) or []
        if not items:
            items = [{'codigo': '', 'descripcion': p.get('nombre', '')}]
        # si una prestacion mapea a varios items de la aseguradora, el valor va
        # en el primero (la secretaria puede ajustar en la pagina si hace falta)
        for j, it in enumerate(items):
            filas.append({
                'id': p['id'],
                'codigo': it.get('codigo', ''),
                'descripcion': it.get('descripcion', p.get('nombre', '')),
                'valor': b['valor'] if j == 0 else 0,
                'fecha': fecha,
            })
    return filas, False


# ── Mapeo prestacion interna -> items de cada aseguradora ────────────────────
# {prest_id: {aseguradora_key: [{codigo, descripcion}, ...]}}

def mapeo_prestaciones():
    return _load(MAPEO_PREST_PATH)


def guardar_mapeo_prestacion(prest_id, aseguradora_key, items):
    with _LOCK:
        idx = _load(MAPEO_PREST_PATH)
        idx.setdefault(prest_id, {})[aseguradora_key] = items
        _save(MAPEO_PREST_PATH, idx)


def prestaciones_para_aseguradora(aseguradora_key):
    """Catalogo interno con la traduccion de la aseguradora ya resuelta.
    items = [] cuando aun no hay mapeo (el frontend usa el nombre interno)."""
    mapeo = mapeo_prestaciones()
    out = []
    for p in listar_prestaciones():
        items = (mapeo.get(p['id']) or {}).get(aseguradora_key) or []
        out.append({**p, 'items': items})
    return out


# ── Mapeo motivo de consulta -> prestaciones sugeridas ──────────────────────
# {motivo_key_o_label: [prest_id, ...]}. Se busca por key del scheduling y
# tambien por label textual (el F2 manda el label del select de DentiDesk).

def mapeo_motivos():
    return _load(MAPEO_MOTIVOS_PATH)


def guardar_mapeo_motivo(motivo, prest_ids):
    with _LOCK:
        idx = _load(MAPEO_MOTIVOS_PATH)
        idx[motivo] = prest_ids
        _save(MAPEO_MOTIVOS_PATH, idx)


def sugerencias_por_motivo(motivo_label, cfg=None):
    """prest_ids sugeridos para un motivo (label textual del F2 o key).
    Fallback: si no hay mapeo explicito, sugiere la prestacion cuyo
    motivo_scheduling_key tenga ese label en scheduling_config."""
    if not motivo_label:
        return []
    idx = mapeo_motivos()
    if motivo_label in idx:
        return idx[motivo_label]
    # match case-insensitive por label
    for k, v in idx.items():
        if k.strip().lower() == motivo_label.strip().lower():
            return v
    # fallback: motivo del scheduling con ese label -> prestacion sembrada
    if cfg:
        for key, m in (cfg.get('motivos') or {}).items():
            if not isinstance(m, dict):
                continue
            if (m.get('label') or '').strip().lower() == motivo_label.strip().lower():
                return [p['id'] for p in listar_prestaciones()
                        if p.get('motivo_scheduling_key') == key]
    return []


# ── Preferencia y datos extra por paciente (RUT) ─────────────────────────────
# {rut_limpio: {ultima_aseguradora, datos_extra:{fecha_nacimiento,direccion,...},
#               actualizado}}

def paciente_seguro(rut):
    return _load(PACIENTES_PATH).get(_limpiar_rut(rut))


def guardar_paciente_seguro(rut, aseguradora=None, datos_extra=None):
    rut = _limpiar_rut(rut)
    if not rut:
        return
    with _LOCK:
        idx = _load(PACIENTES_PATH)
        rec = idx.get(rut, {})
        if aseguradora:
            rec['ultima_aseguradora'] = aseguradora
        if datos_extra is not None:
            extra = rec.get('datos_extra', {})
            extra.update({k: v for k, v in datos_extra.items()})
            rec['datos_extra'] = extra
        rec['actualizado'] = ahora_chile().isoformat(timespec='seconds')
        idx[rut] = rec
        _save(PACIENTES_PATH, idx)


# ── Firmas + timbre de doctores ──────────────────────────────────────────────
# {doctor_key: {nombre_visible, imagen (archivo en FIRMAS_DIR), rut, especialidad}}
# rut/especialidad del PROFESIONAL: varios formularios los piden (ej. Zurich);
# no viven en ningun otro lado del sistema, asi que se guardan aqui.

def listar_firmas():
    idx = _load(FIRMAS_INDEX_PATH)
    return [{'key': k, **v} for k, v in idx.items()]


def datos_doctor(doctor_key):
    return _load(FIRMAS_INDEX_PATH).get(doctor_key) or {}


def guardar_firma(doctor_key, nombre_visible, imagen_nombre=None, rut=None, especialidad=None):
    with _LOCK:
        idx = _load(FIRMAS_INDEX_PATH)
        rec = idx.get(doctor_key, {})
        rec['nombre_visible'] = nombre_visible
        if imagen_nombre is not None:
            rec['imagen'] = imagen_nombre
        if rut is not None:
            rec['rut'] = rut
        if especialidad is not None:
            rec['especialidad'] = especialidad
        idx[doctor_key] = rec
        _save(FIRMAS_INDEX_PATH, idx)


def firma_de_doctor(doctor_key):
    rec = _load(FIRMAS_INDEX_PATH).get(doctor_key)
    if not rec or not rec.get('imagen'):
        return None
    ruta = FIRMAS_DIR / rec['imagen']
    return ruta if ruta.exists() else None


# ── Registro / historial de formularios ─────────────────────────────────────

def crear_registro(datos):
    """datos: rut, aseguradora, doctor, prestaciones (filas), fecha_atencion,
    id_agenda, pdf_path, folio?, origen?. Estado inicial 'generado'."""
    form_id = uuid.uuid4().hex[:12]
    with _LOCK:
        idx = _load(REGISTRO_PATH)
        idx[form_id] = {
            'rut': _limpiar_rut(datos.get('rut', '')),
            'aseguradora': datos.get('aseguradora'),
            'doctor': datos.get('doctor'),
            'prestaciones': datos.get('prestaciones', []),
            'fecha_atencion': datos.get('fecha_atencion'),
            'id_agenda': datos.get('id_agenda'),
            'folio': str(datos.get('folio') or ''),
            'origen': datos.get('origen', 'manual'),  # 'manual' | 'boleta' | 'auto'
            'pdf_path': str(datos.get('pdf_path') or ''),
            'email': datos.get('email', ''),
            'estado': 'generado',
            'canal': None,
            'creado': ahora_chile().isoformat(timespec='seconds'),
            'enviado': None,
        }
        _save(REGISTRO_PATH, idx)
    return form_id


def folio_ya_enviado(folio):
    """True si ya existe un formulario ENVIADO para esa boleta (folio SII).
    Guarda contra reenviar el mismo DTE — el ancla anti-duplicado del auto-envío."""
    folio = str(folio or '').strip()
    if not folio:
        return False
    for v in _load(REGISTRO_PATH).values():
        if str(v.get('folio') or '') == folio and v.get('estado') == 'enviado':
            return True
    return False


# ── Configuración del auto-envío ─────────────────────────────────────────────
# {activo, doctor_default (key para la firma cuando la boleta no trae doctor)}

AUTO_CONFIG_PATH = Path(os.environ.get('SEGUROS_AUTO_CONFIG_PATH',
                                       _BASE_DIR / 'seguros_auto_config.json'))


def get_auto_config():
    cfg = _load(AUTO_CONFIG_PATH)
    return {'activo': bool(cfg.get('activo', False)),
            'doctor_default': cfg.get('doctor_default', '')}


def set_auto_config(activo=None, doctor_default=None):
    with _LOCK:
        cfg = _load(AUTO_CONFIG_PATH)
        if activo is not None:
            cfg['activo'] = bool(activo)
        if doctor_default is not None:
            cfg['doctor_default'] = doctor_default
        _save(AUTO_CONFIG_PATH, cfg)
    return get_auto_config()


def obtener_registro(form_id):
    return _load(REGISTRO_PATH).get(form_id)


def marcar_enviado(form_id, canal='email'):
    with _LOCK:
        idx = _load(REGISTRO_PATH)
        if form_id in idx:
            idx[form_id]['estado'] = 'enviado'
            idx[form_id]['canal'] = canal
            idx[form_id]['enviado'] = ahora_chile().isoformat(timespec='seconds')
            _save(REGISTRO_PATH, idx)


def listar_registros(estado=None, rut=None):
    idx = _load(REGISTRO_PATH)
    items = [{'id': k, **v} for k, v in idx.items()]
    if estado:
        items = [i for i in items if i.get('estado') == estado]
    if rut:
        rut = _limpiar_rut(rut)
        items = [i for i in items if i.get('rut') == rut]
    return sorted(items, key=lambda i: i.get('creado', ''), reverse=True)


# ── Token para servir el PDF de vista previa (iframe sin headers) ────────────

def generar_token_pdf(form_id):
    return _serializer().dumps({'form_id': form_id})


def validar_token_pdf(token, max_age=PDF_TOKEN_MAX_AGE_SEGUNDOS):
    try:
        return _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


# ── Rellenado del PDF ────────────────────────────────────────────────────────
# Campos logicos estandar que el mapeo de cada aseguradora puede posicionar:
#   paciente_nombre, paciente_apellido, paciente_nombre_completo, paciente_rut,
#   paciente_fecha_nacimiento, paciente_direccion, paciente_email,
#   paciente_telefono, fecha_emision, doctor_nombre, total,
#   prestacion_{N}_codigo / _descripcion / _valor / _fecha  (N desde 1)
#   firma_doctor  -> SIEMPRE {'pagina','x','y','ancho','alto'} (imagen overlay)

def _overlay_pdf(plantilla_path, textos_por_pagina, imagenes_por_pagina):
    """Genera la plantilla + overlay fusionado. textos_por_pagina:
    {n_pagina(1-based): [(x, y, texto, fontsize)]}; imagenes_por_pagina:
    {n_pagina: [(x, y, ancho, alto, ruta_imagen)]}. Devuelve PdfWriter."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas as rl_canvas

    # plantilla_path puede ser una ruta o un stream en memoria (BytesIO con el
    # resultado de la etapa AcroForm)
    reader = PdfReader(plantilla_path if isinstance(plantilla_path, io.BytesIO)
                       else str(plantilla_path))
    writer = PdfWriter()
    # append() (y no add_page en un loop) para PRESERVAR el diccionario
    # /AcroForm del documento — si se pierde, los visores dejan de mostrar el
    # texto de los campos rellenados en la etapa AcroForm.
    writer.append(reader)
    for i, page in enumerate(writer.pages):
        num = i + 1
        textos = textos_por_pagina.get(num, [])
        imagenes = imagenes_por_pagina.get(num, [])
        if not textos and not imagenes:
            continue
        buf = io.BytesIO()
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        for (x, y, texto, fontsize) in textos:
            c.setFont('Helvetica', fontsize or 9)
            c.drawString(x, y, str(texto))
        for (x, y, ancho, alto, ruta) in imagenes:
            try:
                c.drawImage(str(ruta), x, y, width=ancho, height=alto,
                            preserveAspectRatio=True, mask='auto')
            except Exception:
                pass  # una firma corrupta no debe botar el formulario
        c.save()
        buf.seek(0)
        overlay = PdfReader(buf).pages[0]
        page.merge_page(overlay)
    return writer


def _stampar_campo(doc, page, w, value, fixed_fs):
    """Dibuja el valor de un campo AcroForm como TEXTO ESTATICO en la pagina (en
    la posicion/alineacion del campo), para luego borrar el widget. Asi ese dato
    queda 'aplanado' (nitido, no borroso en Chrome) mientras que los campos que NO
    llenamos siguen siendo editables por el paciente."""
    import fitz
    value = str(value or '')
    if not value:
        return
    r = w.rect
    pad = 2.0
    avail = r.width - 2 * pad
    try:
        font = fitz.Font('helv')
    except Exception:
        font = None
    if fixed_fs:
        fs = float(fixed_fs)
    else:
        fs = min(10.5, max(4.0, r.height - 2))
        if font:
            while fs > 3.5 and font.text_length(value, fs) > avail:
                fs -= 0.25
    tw = font.text_length(value, fs) if font else 0
    # alineacion segun el quadding del campo (/Q): 0 izq, 1 centro, 2 derecha
    quad = 0
    try:
        k = doc.xref_get_key(w.xref, 'Q')
        if k and k[0] == 'int':
            quad = int(k[1])
    except Exception:
        pass
    if quad == 1:
        x = r.x0 + (r.width - tw) / 2
    elif quad == 2:
        x = r.x1 - pad - tw
    else:
        x = r.x0 + pad
    y = r.y0 + (r.height + fs * 0.72) / 2   # baseline aprox. centrado vertical
    try:
        page.insert_text((x, y), value, fontsize=fs, fontname='helv', color=(0, 0, 0))
    except Exception:
        pass


def rellenar_pdf(aseguradora_key, valores, firma_doctor_key=None):
    """Rellena el PDF oficial de la aseguradora con `valores`
    ({campo_logico: texto}). Devuelve la ruta del PDF generado.
    Si la aseguradora no tiene plantilla mapeada, cae al PDF generico propio
    (generar_pdf_generico) para que el flujo nunca quede bloqueado."""
    aseg = obtener_aseguradora(aseguradora_key)
    plantilla = None
    if aseg and aseg.get('plantilla_pdf'):
        plantilla = PLANTILLAS_DIR / aseg['plantilla_pdf']
    if not plantilla or not plantilla.exists() or not (aseg.get('mapeo_campos') or {}):
        return generar_pdf_generico(aseguradora_key, valores, firma_doctor_key)

    mapeo = aseg.get('mapeo_campos') or {}
    tipo = aseg.get('tipo_plantilla', 'overlay')

    # 1) Separar: campos AcroForm vs posiciones overlay vs imagen de firma.
    # Las coordenadas (x, y) del overlay/firma van en el sistema del PDF (origen
    # ABAJO-izquierda), igual que reportlab.
    campos_acro = {}     # nombre_campo_pdf -> valor
    campos_acro_fs = {}  # nombre_campo_pdf -> fontsize fijo (si el spec lo pide)
    textos = {}          # pagina(1-based) -> [(x, y, texto, fontsize)]
    imagenes = {}        # pagina(1-based) -> [(x, y, w, h, ruta)]
    for campo_logico, spec in mapeo.items():
        # Un campo logico puede ir a VARIOS lugares del PDF (ej. "Nombre del
        # paciente" aparece en la seccion medica Y en la declaracion) — el
        # mapeo acepta un spec suelto o una lista de specs.
        specs = spec if isinstance(spec, list) else [spec]
        if campo_logico == 'firma_doctor':
            ruta = firma_de_doctor(firma_doctor_key) if firma_doctor_key else None
            for s in specs:
                if ruta and all(k in s for k in ('pagina', 'x', 'y', 'ancho', 'alto')):
                    imagenes.setdefault(s['pagina'], []).append(
                        (s['x'], s['y'], s['ancho'], s['alto'], ruta))
            continue
        valor = valores.get(campo_logico)
        if valor in (None, ''):
            continue
        for s in specs:
            if 'campo' in s:
                campos_acro[s['campo']] = str(valor)
                # fontsize fijo opcional (ej. para uniformar una sección);
                # si no se especifica, queda 0 = auto-ajuste al ancho del campo.
                if s.get('fontsize'):
                    campos_acro_fs[s['campo']] = s['fontsize']
            elif 'casillas' in s and all(k in s for k in ('pagina', 'y')):
                # Casillero por dígito (RUT, fechas dd-mm-aa): un carácter por
                # casilla, centrado en cada x. align='right' llena desde la
                # derecha (útil para RUT: DV en la última, cuerpo pegado al guión).
                cas = s['casillas']; fs = s.get('fontsize', 10)
                val = str(valor)
                if s.get('align') == 'right':
                    val = val[-len(cas):]; ini = len(cas) - len(val)
                else:
                    val = val[:len(cas)]; ini = 0
                for i, ch in enumerate(val):
                    cx = cas[ini + i]
                    textos.setdefault(s['pagina'], []).append(
                        (cx - 0.278 * fs, s['y'], ch, fs))
            elif all(k in s for k in ('pagina', 'x', 'y')):
                textos.setdefault(s['pagina'], []).append(
                    (s['x'], s['y'], str(valor), s.get('fontsize', 9)))

    GENERADOS_DIR.mkdir(parents=True, exist_ok=True)
    rut = _limpiar_rut(valores.get('paciente_rut', '')) or 'sinrut'
    ruta_out = GENERADOS_DIR / (
        f"{rut}_{aseguradora_key}_{ahora_chile().strftime('%Y%m%d-%H%M%S')}.pdf")

    # 2) Formularios AcroForm (campos rellenables): PyMuPDF. Setea cada campo con
    # tamaño de fuente AUTO (0) para que el texto largo se encoja y quepa en la
    # casilla, y HORNEA la apariencia en el PDF (no depende del visor del
    # paciente, a diferencia de NeedAppearances). La firma y cualquier texto por
    # coordenadas se dibujan encima (convertimos y: PDF abajo-izq -> fitz arriba-izq).
    if tipo == 'acroform' or campos_acro:
        import fitz
        doc = fitz.open(str(plantilla))
        # APLANADO SELECTIVO: los campos que LLENAMOS se dibujan como texto estatico
        # (nitido, no borroso) y se borra su widget; los campos vacios que llena el
        # paciente (RUT titular, N poliza, firma asegurado, etc.) quedan editables.
        for page in doc:
            for w in (page.widgets() or []):
                if w.field_name in campos_acro:
                    _stampar_campo(doc, page, w, campos_acro[w.field_name],
                                   campos_acro_fs.get(w.field_name))
        for page in doc:
            for w in list(page.widgets() or []):
                if w.field_name in campos_acro:
                    try:
                        page.delete_widget(w)
                    except Exception:
                        pass
        for pnum, imgs in imagenes.items():
            if 0 <= pnum - 1 < len(doc):
                page = doc[pnum - 1]; H = page.rect.height
                for (x, y, anc, alt, ruta) in imgs:
                    try:
                        page.insert_image(fitz.Rect(x, H - (y + alt), x + anc, H - y),
                                          filename=str(ruta), keep_proportion=True)
                    except Exception:
                        pass
        for pnum, txts in textos.items():
            if 0 <= pnum - 1 < len(doc):
                page = doc[pnum - 1]; H = page.rect.height
                for (x, y, texto, fs) in txts:
                    try:
                        page.insert_text((x, H - y), texto, fontsize=fs or 9)
                    except Exception:
                        pass
        doc.save(str(ruta_out), garbage=3, deflate=True)
        doc.close()
        return ruta_out

    # 3) Formularios PLANOS (sin campos): overlay reportlab + pypdf (Colmena, etc.)
    writer_final = _overlay_pdf(plantilla, textos, imagenes)
    with open(ruta_out, 'wb') as f:
        writer_final.write(f)
    return ruta_out


def generar_pdf_generico(aseguradora_key, valores, firma_doctor_key=None):
    """PDF propio de respaldo (reportlab) cuando la aseguradora no tiene
    plantilla oficial mapeada todavia. Lista los datos del paciente y la tabla
    de prestaciones con valores, con firma del doctor si existe."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, Image as RLImage)
    from reportlab.lib import colors

    NAVY = colors.HexColor('#1A2E4A')
    GOLD = colors.HexColor('#C9A84C')

    aseg = obtener_aseguradora(aseguradora_key) or {}
    nombre_aseg = aseg.get('nombre', aseguradora_key or 'Aseguradora')

    GENERADOS_DIR.mkdir(parents=True, exist_ok=True)
    rut = _limpiar_rut(valores.get('paciente_rut', '')) or 'sinrut'
    ruta = GENERADOS_DIR / (
        f"{rut}_{aseguradora_key or 'generico'}_{ahora_chile().strftime('%Y%m%d-%H%M%S')}.pdf")

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle('t', parent=styles['Title'], fontSize=14, textColor=NAVY)
    sub = ParagraphStyle('s', parent=styles['Heading3'], textColor=NAVY)
    cuerpo = ParagraphStyle('c', parent=styles['BodyText'], fontSize=10)

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=1.6 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    story = [
        Paragraph('Clínica de Ortodoncia C. Richard', titulo),
        Paragraph(f'Detalle de prestaciones — Seguro complementario {nombre_aseg}', sub),
        Spacer(1, 10),
        Paragraph(f"Paciente: {valores.get('paciente_nombre_completo', '')}", cuerpo),
        Paragraph(f"RUT: {_formatear_rut(valores.get('paciente_rut', ''))}", cuerpo),
    ]
    if valores.get('paciente_fecha_nacimiento'):
        story.append(Paragraph(f"Fecha de nacimiento: {valores['paciente_fecha_nacimiento']}", cuerpo))
    if valores.get('paciente_direccion'):
        story.append(Paragraph(f"Dirección: {valores['paciente_direccion']}", cuerpo))
    story += [
        Paragraph(f"Profesional tratante: {valores.get('doctor_nombre', '')}", cuerpo),
        Paragraph(f"Fecha de emisión: {valores.get('fecha_emision', '')}", cuerpo),
        Spacer(1, 12),
    ]

    filas = [['Fecha', 'Código', 'Prestación', 'Valor']]
    n = 1
    while f'prestacion_{n}_descripcion' in valores or f'prestacion_{n}_valor' in valores:
        filas.append([
            valores.get(f'prestacion_{n}_fecha', ''),
            valores.get(f'prestacion_{n}_codigo', ''),
            valores.get(f'prestacion_{n}_descripcion', ''),
            valores.get(f'prestacion_{n}_valor', ''),
        ])
        n += 1
    if valores.get('total'):
        filas.append(['', '', 'TOTAL', valores['total']])
    tabla = Table(filas, colWidths=[2.6 * cm, 2.4 * cm, 8.5 * cm, 3 * cm])
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#c8d2e0')),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#F0F5FB')),
    ]))
    story.append(tabla)

    ruta_firma = firma_de_doctor(firma_doctor_key) if firma_doctor_key else None
    if ruta_firma:
        story += [Spacer(1, 24),
                  RLImage(str(ruta_firma), width=5 * cm, height=2.5 * cm, kind='proportional'),
                  Paragraph(f"{valores.get('doctor_nombre', '')}", cuerpo)]
    story += [Spacer(1, 16),
              Paragraph(f'<font size="8" color="#4A5568">Documento generado el '
                        f'{ahora_chile().strftime("%d-%m-%Y %H:%M")} — Clínica de Ortodoncia '
                        f'C. Richard, Paul Harris 10.349, of. 305, Las Condes.</font>', cuerpo)]
    doc.build(story)
    return ruta


def campos_acroform(aseguradora_key):
    """Nombres de los campos AcroForm reales del PDF de la aseguradora, para
    que el panel los ofrezca en <select> al armar el mapeo."""
    aseg = obtener_aseguradora(aseguradora_key)
    if not aseg or not aseg.get('plantilla_pdf'):
        return None
    ruta = PLANTILLAS_DIR / aseg['plantilla_pdf']
    if not ruta.exists():
        return None
    from pypdf import PdfReader
    fields = PdfReader(str(ruta)).get_fields()
    if not fields:
        return []
    return [{'nombre': k, 'tipo': str(v.get('/FT', '')), 'valor': str(v.get('/V', '') or '')}
            for k, v in fields.items()]


def _fmt_monto(v):
    """'130000' o 130000 -> '130.000'. '' si no hay valor."""
    try:
        n = int(str(v).replace('.', '').replace('$', '').replace(' ', '').strip() or 0)
    except (TypeError, ValueError):
        return ''
    return f'{n:,}'.replace(',', '.') if n else ''


def _fecha_ddmmyyyy(fecha):
    """Normaliza a DD-MM-YYYY (los formularios chilenos lo piden así).
    Acepta YYYY-MM-DD o DD-MM-YYYY (o con /). Deja igual lo que no parsea."""
    d, m, y = _partes_fecha(fecha)
    return f'{d}-{m}-{y}' if d and m and y else (fecha or '')


def _partes_fecha(fecha):
    """'24-07-2026' o '2026-07-24' -> ('24','07','2026'). ('','','') si no parsea."""
    import re
    s = (fecha or '').strip()
    m = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', s)   # DD-MM-YYYY
    if m:
        return (m.group(1).zfill(2), m.group(2).zfill(2), m.group(3))
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', s)   # YYYY-MM-DD
    if m:
        return (m.group(3).zfill(2), m.group(2).zfill(2), m.group(1))
    return ('', '', '')


def _limpiar_nombre(full):
    """Quita los codigos internos de ficha de DentiDesk del nombre
    (ej. 'Alberto Jose Del Real Valdes 4269D-D' -> 'Alberto Jose Del Real
    Valdes'). Reutiliza pacientes._es_codigo para detectar los tokens-codigo."""
    import re
    s = (full or '').replace('▲', ' ')
    s = re.sub(r'-[A-Za-z]{1,3}\b', '', s)   # codigos pegados por guion (Esparza-DD)
    try:
        toks = [t for t in s.split() if not pacientes._es_codigo(t)]
    except Exception:
        toks = s.split()
    return ' '.join(toks).strip()


def _calcular_edad(fecha_nac):
    """Edad en años desde una fecha de nacimiento en varios formatos
    (YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY). '' si no se puede parsear."""
    s = (fecha_nac or '').strip()
    if not s:
        return ''
    import re
    m = re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$', s)      # YYYY-MM-DD
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.match(r'^(\d{1,2})[-/](\d{1,2})[-/](\d{4})$', s)  # DD-MM-YYYY
        if not m:
            return ''
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hoy = ahora_chile()
    edad = hoy.year - y - ((hoy.month, hoy.day) < (mo, d))
    return str(edad) if 0 <= edad < 130 else ''


def completar_datos_extra(rut, extra=None):
    """Rellena los huecos de datos_extra con lo que sepa la base local de
    pacientes (pacientes.py).

    Lo que la secretaria escribio A MANO en el modulo de seguros SIEMPRE manda
    -- la base local solo rellena lo que viene vacio (puede haberlo corregido a
    proposito). Campos que aporta hoy:
      - fecha_nacimiento: del export 'Listado de Cumpleanos' de DentiDesk.
        Antes habia que tipearla a mano en cada formulario.
      - direccion: de la siembra del Excel de pacientes (ya se hacia en el
        endpoint /api/seguro/precarga; aca queda centralizado para que tambien
        aplique a los flujos 1-clic desde la boleta, que no pasan por precarga).

    Se aplica dentro de armar_valores(), asi cubre TODOS los caminos que
    generan PDF (previsualizar, enviar, desde-boleta y auto-desde-boleta)."""
    out = dict(extra or {})
    if not rut:
        return out
    try:
        import pacientes as _pac
        rec = _pac.lookup(rut) or {}
    except Exception:
        return out                      # la base local nunca debe romper el envio
    for campo in ('fecha_nacimiento', 'direccion'):
        if not str(out.get(campo) or '').strip() and rec.get(campo):
            out[campo] = rec[campo]
    return out


def armar_valores(datos, filas):
    """Aplana el payload del frontend al dict de campos logicos que consume
    rellenar_pdf(). datos: rut,nombre,apellido,email,telefono,fecha_atencion,
    doctor_nombre,datos_extra. filas: [{codigo,descripcion,valor,fecha}]."""
    # Rellena con la base local (fecha de nacimiento, direccion) lo que no venga
    # escrito a mano. Va aca para cubrir TODOS los flujos que generan PDF.
    extra = completar_datos_extra(datos.get('rut', ''), datos.get('datos_extra'))
    # Nombre SIN codigos internos de DentiDesk (nunca deben ir al formulario)
    nombre = _limpiar_nombre((datos.get('nombre') or '').strip())
    apellido = _limpiar_nombre((datos.get('apellido') or '').strip())
    nombre_completo = _limpiar_nombre(f"{datos.get('nombre','')} {datos.get('apellido','')}")
    valores = {
        'paciente_nombre': nombre,
        'paciente_apellido': apellido,
        'paciente_nombre_completo': nombre_completo,
        'paciente_rut': datos.get('rut', ''),
        'paciente_rut_fmt': _formatear_rut(datos.get('rut', '')),
        # RUT partido para formularios con CASILLAS (un dígito por casilla, ej.
        # SURA): cuerpo (sin puntos ni guión) y dígito verificador por separado.
        'paciente_rut_cuerpo': _limpiar_rut(datos.get('rut', ''))[:-1],
        'paciente_rut_dv': _limpiar_rut(datos.get('rut', ''))[-1:],
        'paciente_email': datos.get('email', ''),
        'paciente_telefono': datos.get('telefono', ''),
        # Normalizada a DD-MM-YYYY: la base local la guarda en ISO y los
        # formularios chilenos la piden al reves. _fecha_ddmmyyyy deja igual lo
        # que no parsea, asi que lo tipeado a mano sigue funcionando.
        'paciente_fecha_nacimiento': _fecha_ddmmyyyy(extra.get('fecha_nacimiento', '')),
        'paciente_edad': _calcular_edad(extra.get('fecha_nacimiento', '')),
        'paciente_direccion': extra.get('direccion', ''),
        'fecha_emision': ahora_chile().strftime('%d-%m-%Y'),
        'fecha_emision_dia': ahora_chile().strftime('%d'),
        'fecha_emision_mes': ahora_chile().strftime('%m'),
        'fecha_emision_anio': ahora_chile().strftime('%Y'),
        'fecha_atencion': _fecha_ddmmyyyy(datos.get('fecha_atencion', '')),
        # Partes de la fecha de atención (formularios con casillas dd/mm/aa)
        'fecha_atencion_dia': _partes_fecha(_fecha_ddmmyyyy(datos.get('fecha_atencion', '')))[0],
        'fecha_atencion_mes': _partes_fecha(_fecha_ddmmyyyy(datos.get('fecha_atencion', '')))[1],
        'fecha_atencion_aa': _partes_fecha(_fecha_ddmmyyyy(datos.get('fecha_atencion', '')))[2][-2:],
        'doctor_nombre': datos.get('doctor_nombre', ''),
        # Naturaleza de la atencion (Zurich pide "lesion" / naturaleza)
        'lesion': 'Tratamiento de ortodoncia',
        # Datos de ortodoncia por paciente (guardados en datos_extra por RUT)
        'orto_tipo_aparatos': extra.get('tipo_aparatos', ''),
        'orto_fecha_instalacion': _fecha_ddmmyyyy(extra.get('fecha_instalacion', '')),
        'orto_fecha_primer_control': _fecha_ddmmyyyy(extra.get('fecha_primer_control', '')),
        'orto_duracion': extra.get('duracion_tratamiento', ''),
        'orto_valor_aparatos': _fmt_monto(extra.get('valor_aparatos', '')),
        'orto_valor_controles': _fmt_monto(extra.get('valor_controles', '')),
        # Datos fijos de la clinica (algunos formularios los piden)
        'clinica_nombre': 'Clínica de Ortodoncia C. Richard',
        'clinica_telefono': '+56 2 2217 3499',
        'clinica_email': 'recepcion@ortodonciarichard.cl',
        'clinica_direccion': 'Paul Harris 10.349, of. 305, Las Condes',
        'clinica_ciudad': 'Santiago',
        'clinica_dir_tel': 'Paul Harris 10.349, of. 305, Las Condes — Tel +56 2 2217 3499',
        'clinica_rut': '79.609.080-4',   # Clínica de Ortodoncia C. Richard Ltda.
        'clinica_razon_social': 'Clínica de Ortodoncia C. Richard Ltda.',
    }
    total = 0
    for i, fila in enumerate(filas, start=1):
        valores[f'prestacion_{i}_codigo'] = fila.get('codigo', '')
        valores[f'prestacion_{i}_descripcion'] = fila.get('descripcion', '')
        _fecha = _fecha_ddmmyyyy(fila.get('fecha', ''))
        valores[f'prestacion_{i}_fecha'] = _fecha
        # Partes de la fecha (formularios con casillas Día/Mes/Año, ej. Vida Cámara)
        _dp = _partes_fecha(_fecha)
        valores[f'prestacion_{i}_fecha_dia'] = _dp[0]
        valores[f'prestacion_{i}_fecha_mes'] = _dp[1]
        valores[f'prestacion_{i}_fecha_anio'] = _dp[2]
        valores[f'prestacion_{i}_fecha_aa'] = _dp[2][-2:]  # año 2 dígitos (casillas)
        try:
            v = int(fila.get('valor') or 0)
        except (TypeError, ValueError):
            v = 0
        total += v
        _vfmt = f'{v:,}'.replace(',', '.') if v else ''
        valores[f'prestacion_{i}_valor'] = _vfmt
        # cantidad 1 ⇒ valor total de la fila = valor unitario (formularios con
        # columnas separadas Valor Unitario / Valor Total, ej. SURA)
        valores[f'prestacion_{i}_valor_total'] = _vfmt
        valores[f'prestacion_{i}_cantidad'] = '1'
    valores['total'] = f'{total:,}'.replace(',', '.') if total else ''
    # "Tratamiento indicado" (formularios tipo Colmena): la primera prestacion
    if filas:
        valores['tratamiento_indicado'] = filas[0].get('descripcion', '')
    return valores


# ── Semilla al final: todas las funciones (_load/_save) ya están definidas ────
_aplicar_seed()
