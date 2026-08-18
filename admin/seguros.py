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
import re
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
            # ACTUALIZACIÓN VERSIONADA: cuando el seed sube su 'seed_rev', se
            # RE-APLICAN los campos de mapeo aunque ya existan en disco. El self-heal
            # de abajo solo rellena lo VACÍO, así que una corrección visual de
            # coordenadas (ej. Bice Vida/Consorcio) nunca llegaría a producción, donde
            # la aseguradora ya existe con el mapeo viejo. Esto solo afecta a las
            # aseguradoras cuyo seed_rev subió; no toca nombre/activa ni a las demás.
            if sv.get('seed_rev', 0) > cur.get('seed_rev', 0):
                for campo in ('mapeo_campos', 'tipo_plantilla', 'plantilla_pdf',
                              'max_prestaciones_por_form', 'tapar'):
                    if campo in sv:
                        cur[campo] = sv[campo]
                cur['seed_rev'] = sv['seed_rev']
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


# ── Catálogo de prestaciones (auto-descubiertas desde la glosa de la boleta) ─
# {prest_id: {nombre, glosa_original, glosas_boleta:[patrones opcionales],
#             activa, origen}}  — SIN precio (lo pone la boleta).

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


# ── Interpretación de la glosa de boleta ────────────────────────────────────
# Modelo NUEVO: la glosa de la boleta se COPIA tal cual como prestación (el valor
# lo pone la boleta). 'glosas_boleta' quedó como PATRONES OPCIONALES para agrupar
# variantes (ej. un patrón "CONTROL MENSUAL DE ORTODONCIA" captura "…AGOSTO",
# "…JULIO"). Ver prestacion_por_glosa / filas_desde_items más abajo.

def _normalizar_texto(s):
    import unicodedata
    s = unicodedata.normalize('NFD', s or '')
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return s.upper()


def _norm_glosa(s):
    """Clave normalizada para comparar glosas: sin tildes, MAYÚSCULAS, espacios
    colapsados y sin el sufijo ' PIEZA XXX' que a veces trae la glosa del DTE."""
    t = _normalizar_texto(s)
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\s*PIEZA\s+\S+\s*$', '', t).strip()
    return t


def _sin_pieza_boca(s):
    """Quita el sufijo ' pieza Boca' que DentiDesk agrega a la glosa cuando NO se
    asignó un diente específico (normal en ortodoncia: el tratamiento es de toda la
    boca). Es ruido en el formulario del seguro. NO toca un diente real como
    'pieza 11' — solo el marcador genérico 'Boca'."""
    return re.sub(r'(?i)\s*pieza\s+boca\s*$', '', s or '').strip()


def prestacion_por_glosa(glosa):
    """Prestación que corresponde a una glosa. (1) por PATRÓN (glosas_boleta —
    agrupa variantes tipo 'CONTROL MENSUAL … AGOSTO/JULIO'); (2) por glosa_original
    exacta (normalizada). Devuelve la prestación o None."""
    key = _norm_glosa(glosa)
    if not key:
        return None
    prestaciones = listar_prestaciones()
    for p in prestaciones:                       # (1) patrón
        for pat in (p.get('glosas_boleta') or []):
            k = _norm_glosa(pat)
            if k and k in key:
                return p
    for p in prestaciones:                       # (2) glosa_original exacta
        if _norm_glosa(p.get('glosa_original', '')) == key:
            return p
    return None


def obtener_o_crear_prestacion_glosa(glosa):
    """Devuelve la prestación de esta glosa; si no existe, la CREA
    (auto-descubrimiento): guarda glosa_original y nombre = la glosa tal cual, SIN
    precio (el precio lo pone la boleta). Así aparece sola en el panel."""
    p = prestacion_por_glosa(glosa)
    if p:
        return p
    limpia = _sin_pieza_boca(re.sub(r'\s+', ' ', (glosa or '').strip()))
    pid = guardar_prestacion(None, {'nombre': limpia, 'glosa_original': limpia,
                                    'origen': 'boleta'})
    return {'id': pid, 'nombre': limpia, 'glosa_original': limpia}


def _monto_int(v):
    """Monto a entero, tolerando los dos formatos que llegan de DentiDesk:
    '124.000' (punto = separador de MILES, como se ve en pantalla) y '124000.000'
    (punto = DECIMAL, como lo devuelve el detalle de prestaciones del abono).
    Distinguirlos importa: quitar el punto a ciegas convertía 124.000 en 124 millones."""
    s = str(v if v is not None else 0).replace('$', '').replace(' ', '').strip()
    if not s:
        return 0
    # Miles solo si el patrón es 1-3 dígitos + grupos EXACTOS de 3 ('1.234.567').
    if re.fullmatch(r'-?\d{1,3}(\.\d{3})+', s):
        s = s.replace('.', '')
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _aseguradora_sin_formulario(aseguradora_key):
    """True si la aseguradora NO tiene formulario oficial mapeado → el PDF será el
    'informe genérico' (generar_pdf_generico). Ej.: EUROAMERICA, que no tiene
    formulario y pide un informe timbrado con la prestación valorizada y la PIEZA."""
    aseg = obtener_aseguradora(aseguradora_key) or {}
    return not aseg.get('plantilla_pdf')


def filas_desde_items(items, aseguradora_key, fecha=''):
    """Modelo nuevo: convierte los ÍTEMS de la boleta/presupuesto en filas del
    formulario. items = [{'descripcion','valor'}, ...]. Por cada ítem: encuentra-o-
    crea la prestación (por glosa), usa el NOMBRE por aseguradora si hay override
    (mapeo_prestaciones), y el VALOR del ítem tal cual. NUNCA falla: copia lo que
    venga (el estudio llega desglosado en sus ítems, el control como uno solo).

    Para aseguradoras SIN formulario (informe genérico, ej. EUROAMERICA) se usa la
    glosa CRUDA de la boleta como descripción — así conserva la PIEZA tratada, que
    esas aseguradoras exigen y que el nombre agrupado de la prestación puede perder."""
    mapeo = mapeo_prestaciones()
    es_informe = _aseguradora_sin_formulario(aseguradora_key)
    filas = []
    for it in (items or []):
        glosa = (it.get('descripcion') or '').strip()
        if not glosa:
            continue
        valor = _monto_int(it.get('valor'))
        p = obtener_o_crear_prestacion_glosa(glosa)
        overrides = (mapeo.get(p['id']) or {}).get(aseguradora_key) or []
        if overrides:
            # renombre/código por aseguradora; si mapea a varios, el valor va en el 1º
            for j, ov in enumerate(overrides):
                filas.append({'id': p['id'], 'codigo': ov.get('codigo', ''),
                              'descripcion': _sin_pieza_boca(ov.get('descripcion') or p.get('nombre') or glosa),
                              'valor': valor if j == 0 else 0, 'fecha': fecha})
        else:
            desc = _sin_pieza_boca(glosa if es_informe else (p.get('nombre') or glosa))
            filas.append({'id': p['id'], 'codigo': '',
                          'descripcion': desc,
                          'valor': valor, 'fecha': fecha})
    return filas


def clasificar_items(items):
    """Separa las glosas de la boleta en CONOCIDAS (ya resuelven a una prestación
    existente) y NUEVAS (ninguna prestación las cubre todavía). NO crea nada — es
    consulta pura, para que el auto-envío decida si mandar el formulario solo.
    Devuelve {'conocidos': [glosa,...], 'nuevos': [glosa,...]}."""
    conocidos, nuevos = [], []
    for it in (items or []):
        glosa = (it.get('descripcion') or '').strip()
        if not glosa:
            continue
        (conocidos if prestacion_por_glosa(glosa) else nuevos).append(glosa)
    return {'conocidos': conocidos, 'nuevos': nuevos}


def registrar_glosas(items):
    """Crea en el catálogo (para que aparezcan en el panel) las prestaciones de las
    glosas que aún no existan, SIN precio. Se usa cuando el auto-envío detecta glosas
    nuevas: no manda el formulario pero deja las prestaciones listas para configurar."""
    for it in (items or []):
        glosa = (it.get('descripcion') or '').strip()
        if glosa:
            obtener_o_crear_prestacion_glosa(glosa)


def filas_desde_boleta(glosa, monto_total, aseguradora_key, fecha=''):
    """Compat (fallback): cuando SOLO se tiene la glosa + total del DTE (sin el
    detalle del presupuesto), se trata como un ÚNICO ítem. Devuelve
    (filas, no_reconocido=False): con el modelo nuevo NUNCA es 'no reconocido'."""
    filas = filas_desde_items([{'descripcion': glosa, 'valor': monto_total}],
                              aseguradora_key, fecha=fecha)
    return filas, False


def items_de_boleta(items, glosa, monto):
    """Devuelve los ítems que corresponden a ESTA boleta, garantizando que cuadren
    con ella. El detalle que lee la extensión viene de `presupuesto_edit.php`, que
    trae TODO el plan de tratamiento del paciente — NO solo lo cobrado en la boleta.
    Por eso se valida contra el MONTO del DTE: si el detalle suma el total de la
    boleta, se usa desglosado (ej. el estudio, o una boleta que cobra todo el
    presupuesto); si NO cuadra (el presupuesto trae más prestaciones que las
    cobradas), se cae a UNA sola línea = glosa + total del DTE. Así la cantidad y el
    monto del formulario SIEMPRE coinciden con la boleta.

    Si no viene MONTO no se puede validar → se confía en el detalle (mejor esfuerzo)."""
    monto_i = _monto_int(monto)
    if items:
        suma = sum(_monto_int(it.get('valor')) for it in items)
        if not monto_i or suma == monto_i:
            return list(items)
    return [{'descripcion': glosa, 'valor': monto}] if glosa else []


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


# ── Preferencia y datos extra por paciente (RUT) ─────────────────────────────
# {rut_limpio: {ultima_aseguradora, datos_extra:{fecha_nacimiento,direccion,...},
#               actualizado}}
#
# ultima_aseguradora puede ser:
#   - una key real ('zurich', 'metlife', …)  → estado 'asignada'
#   - SIN_SEGURO ('sin_seguro')              → el paciente declaró NO tener seguro
#   - ausente/'' (sin registro)              → 'sin_asignar' (nadie la definió aún)
# Ni 'sin_seguro' ni 'sin_asignar' disparan avisos a recepción en el auto-envío:
# el paciente sin seguro no genera formulario, y "sin asignar" es simplemente que
# todavía nadie eligió su aseguradora (todos los pacientes viejos arrancan así).

SIN_SEGURO = 'sin_seguro'


def paciente_seguro(rut):
    return _load(PACIENTES_PATH).get(_limpiar_rut(rut))


def estado_aseguradora(rut):
    """'asignada' | 'sin_seguro' | 'sin_asignar' para el RUT dado."""
    aseg = (paciente_seguro(rut) or {}).get('ultima_aseguradora')
    if not aseg:
        return 'sin_asignar'
    if aseg == SIN_SEGURO:
        return 'sin_seguro'
    return 'asignada'


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


def asignar_si_vacio(rut, aseguradora):
    """Asigna la aseguradora SOLO si el paciente hoy está 'sin_asignar' (regla de
    oro, igual que pacientes.merge_fichas): nunca pisa una aseguradora ya elegida a
    mano ni un 'sin_seguro'. Lo usa la sincronización del formulario de primera
    consulta. Devuelve True si escribió, False si no tocó nada."""
    if not aseguradora or estado_aseguradora(rut) != 'sin_asignar':
        return False
    guardar_paciente_seguro(rut, aseguradora=aseguradora)
    return True


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


# ── Capacidad del formulario y red de seguridad ──────────────────────────────
# El formulario oficial de cada aseguradora trae POCAS filas de prestación (3 en
# BCI, 5 en la mayoría). Hasta el 2026-08-18 las filas que sobraban se descartaban
# EN SILENCIO — pero el total sí las sumaba, así que el formulario salía
# descuadrado (caso real Zurich: 7 prestaciones, se imprimieron 5 y el total decía
# la suma de las 7). Estas funciones garantizan que eso no vuelva a pasar.

def capacidad_formulario(aseguradora_key):
    """Cuántas prestaciones puede mostrar el formulario de esa aseguradora.
    0 = sin límite (las que no tienen plantilla usan el informe propio)."""
    aseg = obtener_aseguradora(aseguradora_key) or {}
    tabla = aseg.get('tabla_prestaciones') or {}
    if tabla.get('capacidad'):
        return int(tabla['capacidad'])
    if aseg.get('plantilla_pdf'):
        return int(aseg.get('max_prestaciones_por_form') or 6)
    return 0


def preparar_filas_para_formulario(filas, capacidad):
    """Recorta las filas a lo que el formulario puede mostrar SIN perder plata: las
    primeras `capacidad-1` van tal cual y la última resume el sobrante ("Otras
    prestaciones — ver detalle adjunto") con su suma. Así el total impreso siempre
    cuadra con lo que se ve. Devuelve (visibles, resumidas); `resumidas` son las que
    se fueron al resumen, para listarlas en la hoja anexa."""
    filas = list(filas or [])
    if capacidad < 1 or len(filas) <= capacidad:
        return filas, []
    visibles = filas[:capacidad - 1]
    resto = filas[capacidad - 1:]
    total = sum(_monto_int(f.get('valor')) for f in resto)
    fmt = f'{total:,}'.replace(',', '.') if total else ''
    visibles.append({
        'codigo': '',
        'descripcion': f'Otras prestaciones ({len(resto)}) — ver detalle adjunto',
        'fecha': resto[0].get('fecha', ''),
        'valor': fmt, 'valor_total': fmt, 'cantidad': '1',
    })
    return visibles, resto


def _filas_desde_valores(valores):
    """Reconstruye la lista de filas desde los prestacion_N_* que armó
    armar_valores(). Se hace acá (y no cambiando la firma de rellenar_pdf) para no
    tocar los 4 puntos del server que ya lo llaman."""
    filas = []
    n = 1
    while f'prestacion_{n}_descripcion' in valores:
        filas.append({
            'codigo':      valores.get(f'prestacion_{n}_codigo', ''),
            'descripcion': valores.get(f'prestacion_{n}_descripcion', ''),
            'fecha':       valores.get(f'prestacion_{n}_fecha', ''),
            'valor':       valores.get(f'prestacion_{n}_valor', ''),
            'valor_total': valores.get(f'prestacion_{n}_valor_total', ''),
            'cantidad':    valores.get(f'prestacion_{n}_cantidad', ''),
        })
        n += 1
    return filas


_SUFIJOS_PRESTACION = ('codigo', 'descripcion', 'fecha', 'valor', 'valor_total',
                       'cantidad', 'fecha_dia', 'fecha_mes', 'fecha_anio', 'fecha_aa')


def _aplicar_capacidad(valores, capacidad):
    """Aplica preparar_filas_para_formulario y REESCRIBE los prestacion_N_* de
    `valores` (así los formularios de filas fijas, los que aún no tienen tabla
    dinámica, también quedan cuadrados). Devuelve (visibles, resumidas)."""
    filas = _filas_desde_valores(valores)
    if not filas:
        return [], []
    visibles, resto = preparar_filas_para_formulario(filas, capacidad)
    if not resto:
        return visibles, []
    for i, f in enumerate(visibles, start=1):
        for suf in ('codigo', 'descripcion', 'fecha'):
            valores[f'prestacion_{i}_{suf}'] = f.get(suf, '')
        valores[f'prestacion_{i}_valor'] = f.get('valor', '')
        valores[f'prestacion_{i}_valor_total'] = f.get('valor', '')
    n = len(visibles) + 1                      # limpiar las que ya no se imprimen
    while f'prestacion_{n}_descripcion' in valores:
        for suf in _SUFIJOS_PRESTACION:
            valores.pop(f'prestacion_{n}_{suf}', None)
        n += 1
    return visibles, resto


def _dibujar_tabla_prestaciones(page, spec, filas):
    """Redibuja la tabla de prestaciones con TANTAS filas como haga falta.

    Tapa el cuerpo de la tabla original (mismo mecanismo que 'tapar') y dibuja una
    grilla nueva de n filas más delgadas, ajustando el tamaño de letra. El
    encabezado, la fila del Total y el resto de la hoja quedan INTACTOS.

    spec: {pagina, y0, y1, columnas:[{campo,x0,x1,align}], filas_min, fontsize_max,
    fontsize_min}. Coordenadas fitz (origen ARRIBA-izquierda), igual que 'tapar'.
    Si el rango incluye una banda que se quiere absorber (ej. "Detalle y Costo
    Laboratorio" en MAPFRE/BUPA/Cruz Blanca), el tapado borra sus etiquetas y esas
    filas quedan disponibles para prestaciones."""
    import fitz
    y0, y1 = float(spec['y0']), float(spec['y1'])
    cols = spec.get('columnas') or []
    if not cols or y1 <= y0:
        return 0
    n = max(len(filas), int(spec.get('filas_min') or 1))
    alto = (y1 - y0) / n
    fs = max(float(spec.get('fontsize_min', 6.0)),
             min(float(spec.get('fontsize_max', 9.0)), alto * 0.68))
    x_ini = min(c['x0'] for c in cols)
    x_fin = max(c['x1'] for c in cols)
    gris = (0.45, 0.45, 0.45)

    page.draw_rect(fitz.Rect(x_ini, y0, x_fin, y1), color=None, fill=(1, 1, 1),
                   fill_opacity=1)
    for i in range(1, n):                                   # separadores de fila
        yy = y0 + alto * i
        page.draw_line(fitz.Point(x_ini, yy), fitz.Point(x_fin, yy),
                       color=gris, width=0.4)
    for c in cols[1:]:                                      # separadores de columna
        page.draw_line(fitz.Point(c['x0'], y0), fitz.Point(c['x0'], y1),
                       color=gris, width=0.4)
    page.draw_rect(fitz.Rect(x_ini, y0, x_fin, y1), color=gris, width=0.5)

    # Por cada columna se busca la letra MÁS GRANDE con la que el texto más largo
    # entra COMPLETO, partiéndolo en las líneas que quepan en la fila (una fila alta
    # como la de CHUBB admite 2-3 líneas). Recortar es el último recurso y es
    # peligroso: "…ARCADA SUPERIOR" y "…ARCADA INFERIOR" cortadas quedarían como dos
    # filas IDÉNTICAS en el formulario. El tamaño es uniforme dentro de la columna
    # (ajustarlo celda a celda se ve descuidado).
    # fontsize_min es el tamaño PREFERIDO mínimo; si con él el texto no entra, se
    # baja hasta _FS_ABS_MIN antes de recortar (perder "ARCADA SUPERIOR/INFERIOR" es
    # peor que un par de puntos menos de letra).
    fs_min = float(spec.get('fontsize_min', 6.0))
    _FS_ABS_MIN = 5.0
    fs_col, lineas_col = {}, {}
    for ci, c in enumerate(cols):
        campo = c.get('campo')
        if not campo:
            continue
        ancho = c['x1'] - c['x0'] - 4
        textos = [t for t in (str(f_.get(campo) or '').strip() for f_ in filas[:n]) if t]
        f = fs
        elegido = None
        while f >= _FS_ABS_MIN - 0.01:
            k = max(1, int(alto / (f * 1.15)))
            if all(_lineas_necesarias(t, f, ancho) <= k for t in textos):
                elegido = (f, k)
                break
            f -= 0.25
        if elegido is None:                 # ni al mínimo entra: se recorta al dibujar
            elegido = (_FS_ABS_MIN, max(1, int(alto / (_FS_ABS_MIN * 1.15))))
        fs_col[ci], lineas_col[ci] = elegido

    for i, fila in enumerate(filas[:n]):
        for ci, c in enumerate(cols):
            campo = c.get('campo')
            if not campo:
                continue                    # columna de la aseguradora (ej. U.C.O.)
            txt = str(fila.get(campo) or '').strip()
            if not txt:
                continue
            f = fs_col[ci]
            ancho = c['x1'] - c['x0'] - 4
            lineas = _envolver_celda(txt, f, ancho, lineas_col[ci])
            alto_bloque = f * 1.15 * len(lineas)
            base0 = y0 + alto * i + (alto - alto_bloque) / 2 + f
            align = c.get('align', 'left')
            for j, ln in enumerate(lineas):
                while len(ln) > 1 and fitz.get_text_length(ln, fontsize=f) > ancho:
                    ln = ln[:-1]            # último recurso: nunca invadir la vecina
                w = fitz.get_text_length(ln, fontsize=f)
                if align == 'right':
                    x = c['x1'] - 2 - w
                elif align == 'center':
                    x = (c['x0'] + c['x1']) / 2 - w / 2
                else:
                    x = c['x0'] + 2
                page.insert_text(fitz.Point(x, base0 + f * 1.15 * j), ln, fontsize=f)
    return n


def _lineas_necesarias(txt, fs, ancho):
    """Cuántas líneas ocupa `txt` a ese tamaño dentro de `ancho` (corte por palabra)."""
    import fitz
    lineas, actual = 1, ''
    for p in txt.split():
        cand = f'{actual} {p}'.strip()
        if actual and fitz.get_text_length(cand, fontsize=fs) > ancho:
            lineas += 1
            actual = p
        else:
            actual = cand
    return lineas


def _envolver_celda(txt, fs, ancho, max_lineas):
    """Parte el texto en hasta `max_lineas` que quepan en `ancho` (por palabras)."""
    import fitz
    if max_lineas <= 1 or fitz.get_text_length(txt, fontsize=fs) <= ancho:
        return [txt]
    lineas, actual = [], ''
    for p in txt.split():
        cand = f'{actual} {p}'.strip()
        if actual and fitz.get_text_length(cand, fontsize=fs) > ancho:
            lineas.append(actual)
            actual = p
            if len(lineas) >= max_lineas:
                return lineas
        else:
            actual = cand
    if actual:
        lineas.append(actual)
    return lineas[:max_lineas] or [txt]


def _anexar_detalle(ruta_pdf, aseguradora_key, valores_completos, firma_doctor_key):
    """Pega al final del formulario una hoja con el detalle COMPLETO (todas las
    prestaciones), reusando el informe propio. Se usa cuando hubo que resumir."""
    import fitz
    try:
        anexo = generar_pdf_generico(aseguradora_key, valores_completos,
                                     firma_doctor_key)
        doc = fitz.open(str(ruta_pdf))
        doc.insert_pdf(fitz.open(str(anexo)))
        tmp = Path(str(ruta_pdf) + '.tmp')
        doc.save(str(tmp), garbage=3, deflate=True)
        doc.close()
        os.replace(str(tmp), str(ruta_pdf))
    except Exception as e:
        print(f'[seguros] no se pudo anexar el detalle: {e!r}')
    return ruta_pdf


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

    # Capacidad: nunca se descarta una prestación en silencio. Si sobran, la última
    # fila las resume y al final se anexa el detalle completo.
    valores_completos = dict(valores)          # copia SIN recortar, para el anexo
    filas_tabla, filas_resumidas = _aplicar_capacidad(
        valores, capacidad_formulario(aseguradora_key))
    # Tabla dinámica: si la aseguradora la declara, se redibuja con más filas y sus
    # mapeos prestacion_N_* quedan sin efecto (los reemplaza el motor).
    tabla_spec = aseg.get('tabla_prestaciones') or None

    # 1) Separar: campos AcroForm vs posiciones overlay vs imagen de firma.
    # Las coordenadas (x, y) del overlay/firma van en el sistema del PDF (origen
    # ABAJO-izquierda), igual que reportlab.
    campos_acro = {}     # nombre_campo_pdf -> valor
    campos_acro_fs = {}  # nombre_campo_pdf -> fontsize fijo (si el spec lo pide)
    textos = {}          # pagina(1-based) -> [(x, y, texto, fontsize)]
    imagenes = {}        # pagina(1-based) -> [(x, y, w, h, ruta)]
    campos_tabla = set()  # widgets de las filas viejas, a borrar si hay tabla dinámica
    for campo_logico, spec in mapeo.items():
        # Con tabla dinámica, las filas las dibuja el motor: los mapeos de fila
        # quedan sin efecto (y sus widgets AcroForm se eliminan, para que no queden
        # cajas editables flotando sobre la tabla nueva).
        if tabla_spec and campo_logico.startswith('prestacion_'):
            for s in (spec if isinstance(spec, list) else [spec]):
                if isinstance(s, dict) and 'campo' in s:
                    campos_tabla.add(s['campo'])
            continue
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
    # El sufijo aleatorio evita que dos PDF generados en el MISMO segundo para el
    # mismo paciente+aseguradora se pisen entre sí (el registro guarda la ruta).
    ruta_out = GENERADOS_DIR / (
        f"{rut}_{aseguradora_key}_{ahora_chile().strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:4]}.pdf")

    # 2) Formularios AcroForm (campos rellenables): PyMuPDF. Setea cada campo con
    # tamaño de fuente AUTO (0) para que el texto largo se encoja y quepa en la
    # casilla, y HORNEA la apariencia en el PDF (no depende del visor del
    # paciente, a diferencia de NeedAppearances). La firma y cualquier texto por
    # coordenadas se dibujan encima (convertimos y: PDF abajo-izq -> fitz arriba-izq).
    # Una aseguradora con tabla dinámica se renderiza SIEMPRE por acá (fitz dibuja
    # texto e imagen igual que el overlay, y las specs de coordenadas son las
    # mismas porque esta rama ya convierte y = H - y).
    if tipo == 'acroform' or campos_acro or tabla_spec:
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
                if w.field_name in campos_acro or w.field_name in campos_tabla:
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
        # TAPAR: rectangulos blancos que ocultan texto impreso de la plantilla
        # (ej. la etiqueta "Costo Laboratorio" de MetLife, para reusar esa fila
        # como una 8a prestacion). Coordenadas fitz (origen ARRIBA-izquierda, igual
        # que los rect de los widgets). Se dibujan ANTES del texto para que quede
        # encima. Config: aseg['tapar'] = [{'pagina','x0','y0','x1','y1'}].
        for cov in (aseg.get('tapar') or []):
            pnum = cov.get('pagina', 1)
            if 0 <= pnum - 1 < len(doc):
                try:
                    doc[pnum - 1].draw_rect(
                        fitz.Rect(cov['x0'], cov['y0'], cov['x1'], cov['y1']),
                        color=None, fill=(1, 1, 1), fill_opacity=1)
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
        # TABLA DINÁMICA de prestaciones (más filas que las que trae el formulario).
        if tabla_spec:
            pnum = int(tabla_spec.get('pagina', 1))
            if 0 <= pnum - 1 < len(doc):
                try:
                    _dibujar_tabla_prestaciones(doc[pnum - 1], tabla_spec, filas_tabla)
                except Exception as e:
                    print(f'[seguros] fallo la tabla dinamica de {aseguradora_key}: {e!r}')
        doc.save(str(ruta_out), garbage=3, deflate=True)
        doc.close()
        if filas_resumidas:
            _anexar_detalle(ruta_out, aseguradora_key, valores_completos,
                            firma_doctor_key)
        return ruta_out

    # 3) Formularios PLANOS (sin campos): overlay reportlab + pypdf (Colmena, etc.)
    writer_final = _overlay_pdf(plantilla, textos, imagenes)
    with open(ruta_out, 'wb') as f:
        writer_final.write(f)
    if filas_resumidas:
        _anexar_detalle(ruta_out, aseguradora_key, valores_completos, firma_doctor_key)
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
        f"{rut}_{aseguradora_key or 'generico'}_{ahora_chile().strftime('%Y%m%d-%H%M%S')}"
        f"-{uuid.uuid4().hex[:4]}.pdf")

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


def partir_nombre_doctor(nombre):
    """Divide el nombre del doctor en (nombres, apellidos) para formularios que los
    piden en campos SEPARADOS (ej. MetLife: 'Apellidos' y 'Nombres'). Quita el
    titulo (Dr./Dra./Prof.) y toma la PRIMERA palabra como nombre de pila; el resto
    son apellidos. Los doctores de la clinica tienen un solo nombre de pila, asi que
    basta. Devuelve (nombres, apellidos)."""
    s = (nombre or '').strip()
    bajo = s.lower()
    for tit in ('dr. ', 'dra. ', 'prof. ', 'dr ', 'dra ', 'prof '):
        if bajo.startswith(tit):
            s = s[len(tit):].strip()
            break
    partes = s.split()
    if len(partes) <= 1:
        return (s, '')
    return (partes[0], ' '.join(partes[1:]))


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
    # Fecha de atención (de la boleta / cita). El FORMULARIO se fecha con la MISMA
    # fecha de la atención (la boleta), no con "hoy": el reembolso corresponde a esa
    # atención. Si no viene fecha de atención, cae a hoy.
    _fa = _fecha_ddmmyyyy(datos.get('fecha_atencion', ''))
    _fa_p = _partes_fecha(_fa) if _fa else ('', '', '')
    _ff = _fa or ahora_chile().strftime('%d-%m-%Y')     # fecha del formulario
    _ff_p = _partes_fecha(_ff)
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
        # Fecha del formulario = fecha de la atención/boleta (no "hoy").
        'fecha_emision': _ff,
        'fecha_emision_dia': _ff_p[0],
        'fecha_emision_mes': _ff_p[1],
        'fecha_emision_anio': _ff_p[2],
        'fecha_atencion': _fa,
        # Partes de la fecha de atención (formularios con casillas dd/mm/aa)
        'fecha_atencion_dia': _fa_p[0],
        'fecha_atencion_mes': _fa_p[1],
        'fecha_atencion_aa': _fa_p[2][-2:],
        'doctor_nombre': datos.get('doctor_nombre', ''),
        # Nombre partido para formularios que piden apellidos/nombres por separado
        # (ej. MetLife). Si un endpoint sobreescribe doctor_nombre con nombre_visible,
        # debe recalcular estos dos (lo hace server.py tras el override).
        'doctor_nombres': partir_nombre_doctor(datos.get('doctor_nombre', ''))[0],
        'doctor_apellidos': partir_nombre_doctor(datos.get('doctor_nombre', ''))[1],
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
