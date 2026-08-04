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
from datetime import datetime, date, timedelta

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import pacientes

import fechas
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.


def ahora_chile():
    """datetime actual en hora de Chile. El servidor en Render corre en UTC; sin
    esto, las horas registradas y el sello del PDF salen ~4h adelantadas.
    Ver fechas.py."""
    return fechas.ahora_chile_aware()

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

# Si al enviar ya existe un consentimiento 'enviado' del mismo rut+tipo creado
# hace menos que esto, se REUTILIZA en vez de crear otro (ver
# obtener_o_crear_registro). Sin esto, la secretaria que manda el link 2-3 veces
# (no llego el WhatsApp, cambio de canal) dejaba 2-3 registros y el paciente
# firmaba UNO: los otros quedaban colgados en 'enviado' para siempre y el aviso
# diario a recepcion los reportaba como pendientes. Constante de modulo (mismo
# criterio que TOKEN_MAX_AGE_SEGUNDOS): si algun dia hay que ajustarlo sin
# deploy, promoverlo a JsonStore con load_config()/save_config().
VENTANA_DEDUP_MESES = 6

# Estados de cita que significan que la atencion NO va a ocurrir (o no ocurrio).
# OJO, es DISTINTA a dentidesk._ESTADOS_INACTIVOS a proposito: esa incluye
# 'atendid' porque esta pensada para citas FUTURAS (una cita ya atendida no es
# una "hora proxima"). Aca la cita es de HOY, y si al paciente ya lo atendieron
# sin firmar ese es justamente el caso que hay que avisar -- falta el documento
# de una atencion que ya ocurrio. Mismo razonamiento que
# control_dental._ESTADOS_NO_OCURRIO (no se importa ese modulo: es pesado y no
# tiene relacion con consentimientos).
_ESTADOS_CITA_NO_CUENTA = ('cancel', 'no llega', 'no seguir', 'reagend', 're-agend')


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

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
# Este registro tiene consentimientos FIRMADOS (con su hash y su id de Drive):
# perderlo por un archivo corrupto seria perder la trazabilidad de documentos
# legales, por eso importa que el archivo malo se aparte en vez de pisarse.
_STORE = jsonstore.JsonStore(REGISTRO_PATH, default={}, indent=2)


def _load_registro():
    return _STORE.load()


def _save_registro(idx):
    _STORE.save(idx)


def _nuevo_item(rut, tipo, canal):
    """El dict de un registro recien creado. Extraido de crear_registro() para
    que obtener_o_crear_registro() lo pueda armar SIN volver a tomar _LOCK
    (es un threading.Lock() normal, no reentrante: tomarlo dos veces cuelga)."""
    return {
        'rut': _limpiar_rut(rut),
        'tipo': tipo,
        'canal': canal,
        'estado': 'enviado',
        'creado': ahora_chile().isoformat(timespec='seconds'),
        'firmado': None,
        'pdf_path': None,
        'subido_dentidesk': False,
        'respaldo_drive': None,   # None = aún no se firma; True/False tras el intento
    }


def crear_registro(rut, tipo, canal):
    """canal: 'mail' | 'whatsapp' | 'tablet'. Devuelve el id del registro.

    SIEMPRE crea uno nuevo. Lo usa el flujo walk-up de la tablet, donde el
    registro nace y se firma en el mismo request (no alcanza a quedar huerfano).
    Para el envio del link desde el F2 usar obtener_o_crear_registro()."""
    consent_id = uuid.uuid4().hex[:12]
    with _LOCK:
        idx = _load_registro()
        idx[consent_id] = _nuevo_item(rut, tipo, canal)
        _save_registro(idx)
    return consent_id


def obtener_o_crear_registro(rut, tipo, canal, meses=VENTANA_DEDUP_MESES):
    """Punto de entrada de POST /api/consentimiento/enviar.

    Si ya existe un registro 'enviado' del mismo rut+tipo creado hace menos de
    `meses`, lo REUTILIZA: mismo consent_id, se actualiza el canal y se deja
    rastro en 'reenvios'. Asi, reenviar el link no genera un duplicado que
    quedaria huerfano cuando el paciente firme cualquiera de ellos.

    Un registro 'firmado'/'subido'/'reemplazado' NO bloquea: si la secretaria
    manda el consentimiento de nuevo es porque quiere una firma nueva (otra
    fase del tratamiento), y eso debe crear un registro aparte.

    El token no se guarda, se genera en cada envio, asi que reutilizar un
    registro de hace meses igual manda un link fresco (30 dias de validez).

    Devuelve (consent_id, reutilizado: bool)."""
    rut_l = _limpiar_rut(rut)
    limite = ahora_chile() - timedelta(days=30 * meses)
    # Buscar y crear/actualizar bajo el MISMO lock: si se hicieran en dos
    # bloques, dos envios simultaneos del mismo paciente podrian no verse entre
    # si y crear igual el duplicado que esta funcion existe para evitar.
    with _LOCK:
        idx = _load_registro()
        vigentes = []
        for k, v in idx.items():
            if (v.get('rut') != rut_l or v.get('tipo') != tipo
                    or v.get('estado') != 'enviado'):
                continue
            try:
                creado_dt = datetime.fromisoformat(v['creado'])
            except (KeyError, TypeError, ValueError):
                continue
            if creado_dt >= limite:
                vigentes.append((creado_dt, k))

        if vigentes:
            # El mas reciente, por si quedaron varios de antes de este cambio.
            consent_id = max(vigentes)[1]
            item = idx[consent_id]
            item['canal'] = canal
            item.setdefault('reenvios', []).append({
                'ts': ahora_chile().isoformat(timespec='seconds'),
                'canal': canal,
            })
            _save_registro(idx)
            return consent_id, True

        consent_id = uuid.uuid4().hex[:12]
        idx[consent_id] = _nuevo_item(rut, tipo, canal)
        _save_registro(idx)
        return consent_id, False


def obtener_registro(consent_id):
    return _load_registro().get(consent_id)


def marcar_firmado(consent_id, pdf_path, pdf_sha256=None):
    with _LOCK:
        idx = _load_registro()
        item = idx.get(consent_id)
        if not item:
            return
        item['estado'] = 'firmado'
        item['firmado'] = ahora_chile().isoformat(timespec='seconds')
        item['pdf_path'] = str(pdf_path)
        # Hash SHA-256 de los bytes REALES del PDF final. Es el ancla de
        # integridad verificable: si el archivo se altera luego, al re-calcular
        # el hash ya no coincide con este valor guardado del lado del servidor.
        if pdf_sha256:
            item['pdf_sha256'] = pdf_sha256
        _cerrar_hermanos(idx, consent_id, item)
        _save_registro(idx)


def _cerrar_hermanos(idx, consent_id, firmado):
    """Marca 'reemplazado' los otros consentimientos del mismo rut+tipo que
    seguian en 'enviado' y son ANTERIORES a esta firma. Muta `idx` en memoria;
    quien llama guarda. Se asume el lock ya tomado (_LOCK no es reentrante).

    Por que existe: al paciente se le puede haber mandado el link 2-3 veces.
    Firma uno solo, y los demas quedaban en 'enviado' para siempre haciendo
    ruido en el aviso diario a recepcion (8 casos reales en produccion).

    La condicion "creados ANTES de la firma" es deliberada: un consentimiento
    enviado DESPUES de una firma es una peticion nueva y legitima (otra fase
    del tratamiento), y no se toca.

    Nunca se borra nada: son registros de un documento legal, solo cambian de
    estado y quedan con el rastro de que firma los reemplazo."""
    rut, tipo = firmado.get('rut'), firmado.get('tipo')
    firmado_ts = firmado.get('firmado') or ''
    for otro_id, otro in idx.items():
        if otro_id == consent_id:
            continue
        if (otro.get('rut') == rut and otro.get('tipo') == tipo
                and otro.get('estado') == 'enviado'
                and (otro.get('creado') or '') < firmado_ts):
            otro['estado'] = 'reemplazado'
            otro['reemplazado_por'] = consent_id
            otro['reemplazado_ts'] = firmado_ts


def limpiar_huerfanos():
    """Aplica _cerrar_hermanos() retroactivamente a TODO el historial, para los
    duplicados que quedaron colgados antes de que existiera ese cierre
    automatico. Se corre una vez tras desplegar, por endpoint.

    Recorre las firmas de cada (rut, tipo) en orden CRONOLOGICO: asi, si un
    paciente tiene varias firmas con envios intercalados, cada huerfano lo
    cierra la firma que le corresponde (y no la ultima de todas).

    Idempotente -- la segunda corrida devuelve cerrados=0 -- y sin red: es solo
    manipulacion del JSON. No borra nada. Devuelve {'cerrados': N, 'ids': [...]}."""
    with _LOCK:
        idx = _load_registro()
        grupos = {}
        for k, v in idx.items():
            grupos.setdefault((v.get('rut'), v.get('tipo')), []).append(k)

        cerrados = []
        for ids in grupos.values():
            firmas = sorted(
                (k for k in ids if idx[k].get('estado') in ('firmado', 'subido')),
                key=lambda k: idx[k].get('firmado') or idx[k].get('creado') or '')
            for fk in firmas:
                item = idx[fk]
                # Los 'subido' viejos podrian no tener 'firmado'; se cae a 'creado'
                # para no quedarse sin punto de corte temporal.
                referencia = dict(item)
                referencia['firmado'] = item.get('firmado') or item.get('creado') or ''
                antes = {k for k in ids if idx[k].get('estado') == 'enviado'}
                _cerrar_hermanos(idx, fk, referencia)
                cerrados.extend(k for k in antes if idx[k].get('estado') == 'reemplazado')

        _save_registro(idx)
        return {'cerrados': len(cerrados), 'ids': cerrados}


def hash_pdf(ruta):
    """SHA-256 de los bytes del archivo (para anclar/verificar integridad)."""
    h = hashlib.sha256()
    with open(ruta, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def borrar_registro(consent_id):
    """Borra un consentimiento SOLO si sigue en estado 'enviado'.
    Devuelve (ok, error). Un consentimiento firmado NUNCA se borra desde aquí
    (es un registro clínico/legal), y tampoco uno 'reemplazado': ese es el
    rastro de que hubo un envío duplicado y quedó cubierto por otra firma."""
    with _LOCK:
        idx = _load_registro()
        item = idx.get(consent_id)
        if not item:
            return False, 'Consentimiento no encontrado'
        if item.get('estado') != 'enviado':
            # Ojo: server.py responde 409 si el mensaje menciona "firmado".
            return False, ('No se puede borrar: este consentimiento ya fue firmado '
                           'o quedó reemplazado por otra firma')
        # Si estaba en la cola de la tablet, limpiarla también
        cola = obtener_cola_tablet()
        if cola and cola.get('id') == consent_id:
            _limpiar_cola_tablet_sin_lock()
        del idx[consent_id]
        _save_registro(idx)
        return True, None


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


# ── Alerta de pendientes con cita ese día ────────────────────────────────────
# Barrido diario (server.py -> _loop_alerta_consentimientos): cruza los
# consentimientos SIN FIRMAR (estado 'enviado') con la agenda de DentiDesk del
# día, para avisarle a recepción que ese paciente llega sin el documento
# firmado (mismo espíritu que el aviso de alineadores 9+ meses, pero sin
# scraping: acá el dato de "sin firmar" ya vive en este registro y la cita se
# resuelve por API).

def pendientes_con_cita_en(fecha):
    """Consentimientos sin firmar cuyo paciente tiene cita ESE día.

    `fecha` es un date o un ISO 'YYYY-MM-DD'. Devuelve una lista ordenada por
    hora ASCENDENTE: [{'consent_id','rut','nombre','tipo','canal','creado',
    'fecha_cita','hora_cita','doctor_cita'}]. Si DentiDesk está deshabilitado,
    o no hay consentimientos pendientes, devuelve [] sin tocar la API.

    Reemplaza a la versión anterior, que preguntaba por las citas de los
    próximos 45 días: eso hacía que un paciente con hora en tres semanas
    apareciera en el correo TODOS los días hasta firmar, y costaba una llamada
    por pendiente (cada una barriendo 45 días). Acá es UNA sola llamada a
    dentidesk._get_agenda_day(), que ya trae las citas de todos los
    profesionales de ese día, cruzada contra el set de RUTs pendientes."""
    import dentidesk
    import scheduling

    pendientes = listar(estado='enviado')
    if not pendientes:
        return []
    scfg = scheduling.load_config()
    if not scfg['dentidesk']['enabled']:
        return []

    if isinstance(fecha, str):
        objetivo = datetime.strptime(fecha[:10], '%Y-%m-%d').date()
    else:
        objetivo = fecha

    # Un pendiente por (rut, tipo): el mas reciente. De aqui en adelante no
    # deberian existir duplicados (obtener_o_crear_registro los evita), pero
    # los que quedaron de antes harian aparecer al mismo paciente dos veces en
    # el correo -- justo el ruido que este aviso trata de eliminar. Se muestra
    # el ultimo porque es el del link que se le mando por ultima vez.
    ultimo = {}
    for p in pendientes:
        clave = (p['rut'], p['tipo'])
        if p['creado'] > ultimo.get(clave, {}).get('creado', ''):
            ultimo[clave] = p
    por_rut = {}
    for p in ultimo.values():
        por_rut.setdefault(p['rut'], []).append(p)

    out = []
    for c in dentidesk._get_agenda_day(scfg, objetivo):
        rut = dentidesk.limpiar_rut(str(c.get('PatientDocument', '')))
        if rut not in por_rut:
            continue
        estado = (c.get('Status') or '').lower()
        if any(s in estado for s in _ESTADOS_CITA_NO_CUENTA):
            continue
        hora = (c.get('time') or '')[:5]
        doctor = (c.get('ProfessionalName') or '').strip()
        fecha_cita = c.get('Date') or objetivo.isoformat()
        rec = pacientes.lookup(rut) or {}
        nombre = f"{rec.get('nombres', '')} {rec.get('apellidos', '')}".strip()
        # Uno por tipo de documento: si al paciente le falta firmar dos
        # consentimientos DISTINTOS, ambos deben salir.
        for p in por_rut[rut]:
            out.append({
                'consent_id': p['id'],
                'rut': _formatear_rut(p['rut']),
                'nombre': nombre or _formatear_rut(p['rut']),
                'tipo': TIPOS_DOCUMENTO.get(p['tipo'], p['tipo']),
                'canal': p['canal'],
                'creado': p['creado'],
                'fecha_cita': fecha_cita,
                'hora_cita': hora,
                'doctor_cita': doctor,
            })
    out.sort(key=lambda i: i['hora_cita'])
    return out


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
            'ts': ahora_chile().isoformat(timespec='seconds'),
        }), encoding='utf-8')


def obtener_cola_tablet():
    if not COLA_TABLET_PATH.exists():
        return None
    try:
        return json.loads(COLA_TABLET_PATH.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None


def _limpiar_cola_tablet_sin_lock():
    if COLA_TABLET_PATH.exists():
        COLA_TABLET_PATH.unlink()


def limpiar_cola_tablet():
    with _LOCK:
        _limpiar_cola_tablet_sin_lock()


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
    marca_tiempo = ahora_chile()
    ruta = PDF_DIR / f"{rut_archivo}_{tipo}_{marca_tiempo.strftime('%Y%m%d-%H%M%S')}.pdf"

    styles = getSampleStyleSheet()
    titulo = ParagraphStyle('titulo', parent=styles['Title'], fontSize=14)
    tit_centrado = ParagraphStyle('titc', parent=titulo, alignment=1)  # centrado
    subt_centrado = ParagraphStyle('subc', parent=styles['Heading3'], alignment=1)
    seccion = ParagraphStyle('seccion', parent=styles['Heading2'], fontSize=11, spaceBefore=10)
    subtitulo = ParagraphStyle('subtitulo', parent=styles['Heading4'], fontSize=9.5, spaceBefore=6, spaceAfter=2)
    cuerpo = ParagraphStyle('cuerpo', parent=styles['BodyText'], fontSize=9.5,
                            alignment=TA_JUSTIFY, leading=13)
    sello_txt = ParagraphStyle('sello', parent=styles['BodyText'], fontSize=8, leading=11, textColor=colors.HexColor('#1A2E4A'))

    doc = SimpleDocTemplate(str(ruta), pagesize=letter,
                            topMargin=1.6 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    story = []
    # Logo de la clínica (encabezado), centrado. Si no está el archivo, se omite.
    _logo = Path(__file__).parent.parent / 'images' / 'logo.jpg'
    if _logo.exists():
        logo_w = 4.2 * cm
        logo_img = RLImage(str(_logo), width=logo_w, height=logo_w / 1.374)
        logo_img.hAlign = 'CENTER'
        story.append(logo_img)
        story.append(Spacer(1, 8))
    story += [
        Paragraph('Clínica de Ortodoncia C. Richard', tit_centrado),
        Paragraph(TIPOS_DOCUMENTO.get(tipo, 'Consentimiento Informado'), subt_centrado),
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

    # ── Sello de registro de firma (trazabilidad) ────────────────────────────
    # NOTA de honestidad técnica: esto NO es una firma electrónica avanzada con
    # PKI. Es un registro de trazabilidad. La integridad real se ancla FUERA del
    # PDF: al terminar de generarlo, el servidor calcula el SHA-256 de sus bytes
    # y lo guarda en su registro (consentimientos_registro.json). Para verificar
    # que un PDF no fue adulterado, se recalcula su hash y se compara con el
    # valor guardado del lado del servidor.
    consent_id = datos.get('consent_id') or '(sin id)'
    ip = datos.get('ip') or '(no registrada)'
    sello_html = (
        '<b>REGISTRO DE FIRMA</b><br/>'
        f'Firmado electrónicamente el '
        f'{marca_tiempo.strftime("%d-%m-%Y")} a las {marca_tiempo.strftime("%H:%M:%S")} '
        f'(hora de Chile).<br/>'
        f'ID de verificación: {consent_id}<br/>'
        f'Dirección IP de origen: {ip}<br/>'
        f'<font size="7">La integridad de este documento se verifica contra el registro '
        f'digital de la clínica (hash SHA-256 almacenado en el servidor al momento '
        f'de la firma).</font>'
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


def generar_pdf_blanco(tipo='ortodoncia'):
    """
    PDF "en blanco" (sin datos de paciente ni firma) del consentimiento, con
    estilo gráfico similar al formulario web — para que la clínica lo imprima
    y lo tenga disponible en recepción, por si un paciente prefiere leerlo en
    papel antes de firmar digitalmente. Tamaño carta.

    A diferencia de generar_pdf(), este NO se guarda en PDF_DIR (no es un
    documento firmado, no tiene datos personales) — se genera al vuelo y se
    devuelven los bytes.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Image as RLImage, Table, TableStyle, KeepTogether)
    from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
    from reportlab.lib import colors
    import io

    NAVY = colors.HexColor('#1A2E4A')
    GOLD = colors.HexColor('#C9A84C')
    BG = colors.HexColor('#F0F5FB')
    TEXT_MID = colors.HexColor('#4A5568')

    buf = io.BytesIO()
    styles = getSampleStyleSheet()
    tit_blanco = ParagraphStyle('titb', parent=styles['Title'], fontSize=16,
                                textColor=colors.white, alignment=TA_CENTER, spaceAfter=2)
    subt_blanco = ParagraphStyle('subb', parent=styles['Heading3'], fontSize=11,
                                 textColor=GOLD, alignment=TA_CENTER, fontName='Helvetica')
    seccion_txt = ParagraphStyle('secb', parent=styles['Heading2'], fontSize=11.5,
                                 textColor=colors.white, spaceAfter=0)
    # Los párrafos del cuerpo llevan fondo celeste + borde izquierdo dorado
    # (backColor/borderPadding en el propio ParagraphStyle) en vez de envolver
    # toda la sección en una Table — así cada párrafo puede partirse solo entre
    # páginas de forma segura (una Table con una celda gigante NO se puede
    # partir y revienta con LayoutError en secciones largas, ej. Sección III).
    subtitulo = ParagraphStyle('subtitulo', parent=styles['Heading4'], fontSize=9.5,
                               spaceBefore=8, spaceAfter=1, textColor=NAVY,
                               backColor=BG, borderPadding=8, leftIndent=0)
    cuerpo = ParagraphStyle('cuerpo', parent=styles['BodyText'], fontSize=9.5,
                            alignment=TA_JUSTIFY, leading=13,
                            backColor=BG, borderPadding=8, spaceAfter=0)
    label_txt = ParagraphStyle('label', parent=styles['BodyText'], fontSize=8.5,
                               textColor=TEXT_MID, spaceAfter=2)

    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=1.4 * cm, bottomMargin=1.8 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    story = []
    ANCHO = 16.5 * cm

    # ── Encabezado: logo + barra navy con el título (como el header web) ─────
    _logo = Path(__file__).parent.parent / 'images' / 'logo-png.png'
    if _logo.exists():
        logo_w = 3.6 * cm
        logo_img = RLImage(str(_logo), width=logo_w, height=logo_w / 1.374)
        logo_img.hAlign = 'CENTER'
        cabecera = Table(
            [[logo_img],
             [Paragraph('CLÍNICA DE ORTODONCIA C. RICHARD', subt_blanco)],
             [Paragraph(TIPOS_DOCUMENTO.get(tipo, 'Consentimiento Informado'), tit_blanco)]],
            colWidths=[ANCHO])
    else:
        cabecera = Table(
            [[Paragraph('CLÍNICA DE ORTODONCIA C. RICHARD', subt_blanco)],
             [Paragraph(TIPOS_DOCUMENTO.get(tipo, 'Consentimiento Informado'), tit_blanco)]],
            colWidths=[ANCHO])
    cabecera.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TOPPADDING', (0, 0), (-1, 0), 14),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 16),
        ('TOPPADDING', (0, -1), (-1, -1), 4),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(cabecera)
    story.append(Spacer(1, 2))
    story.append(Table([['']], colWidths=[ANCHO], rowHeights=[3],
                       style=TableStyle([('BACKGROUND', (0, 0), (-1, -1), GOLD)])))
    story.append(Spacer(1, 16))

    nota = Table([[Paragraph(
        '<b>Documento informativo — versión impresa en blanco.</b> Puedes leer con calma este '
        'consentimiento antes de firmar. La firma se realiza de forma digital, ya sea desde tu '
        'celular (link enviado por la clínica) o en la tablet de recepción.', label_txt)]],
        colWidths=[ANCHO])
    nota.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), BG),
        ('BOX', (0, 0), (-1, -1), 0.75, GOLD),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8), ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(nota)
    story.append(Spacer(1, 14))

    # ── Cada sección como una "tarjeta": barra navy con el título + cuerpo ────
    fmt_kwargs = {
        'tratamiento': '_' * 55,
        'dentista_actual': '_' * 45,
    }
    for titulo_sec, bloques in SECCIONES:
        barra = Table([[Paragraph(titulo_sec, seccion_txt)]], colWidths=[ANCHO])
        barra.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), NAVY),
            ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))

        # Párrafos sueltos (no Table) con fondo celeste propio — se parten
        # solos entre páginas sin riesgo de LayoutError en secciones largas.
        # La barra va en un KeepTogether con el primer párrafo para que el
        # título nunca quede solo al fondo de una página con su texto recién
        # en la siguiente.
        primer_bloque = [barra]
        resto = list(bloques)
        if resto:
            sub0, texto0 = resto.pop(0)
            if sub0:
                primer_bloque.append(Paragraph(sub0, subtitulo))
            primer_bloque.append(Paragraph(texto0.format(**fmt_kwargs), cuerpo))
        story.append(KeepTogether(primer_bloque))

        for sub, texto in resto:
            if sub:
                story.append(Paragraph(sub, subtitulo))
            story.append(Paragraph(texto.format(**fmt_kwargs), cuerpo))
        story.append(Spacer(1, 10))

    # ── Sección final: campos en blanco para completar a mano, si se imprime ─
    barra_final = Table([[Paragraph('Sección Final: Firma y Datos de Confirmación', seccion_txt)]], colWidths=[ANCHO])
    barra_final.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    campos_finales = [
        f"Nombre del Paciente: {'_' * 45}",
        f"RUT del Paciente: {'_' * 30}",
        f"Nombre del Apoderado que firma (solo menores de 18 años): {'_' * 30}",
        f"Fecha: {'_' * 20}",
        f"Firma del Paciente/Apoderado: {'_' * 30}",
    ]
    story.append(KeepTogether([barra_final, Paragraph(campos_finales[0], cuerpo)]))
    for c in campos_finales[1:]:
        story.append(Paragraph(c, cuerpo))

    doc.build(story)
    buf.seek(0)
    return buf
