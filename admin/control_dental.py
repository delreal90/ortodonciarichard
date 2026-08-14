"""
control_dental.py - Recordatorio de Control Dental para pacientes con
ortodoncia (aparatos fijos o alineadores).

A diferencia de recaptacion.py (que se dispara A MANO desde el F2, la
secretaria decide por cada paciente), aca la inscripcion es AUTOMATICA: un
barrido diario de la agenda de DentiDesk detecta solo cuando a un paciente
le instalan aparatos/alineadores y lo inscribe; detecta tambien cuando se
los retiran (o pasa a contencion) y lo da de baja. El canal es email (no
WhatsApp -- no requiere plantilla de Meta ni tiene tope de frecuencia de Meta).

La idea central (ver plan): UNA sola pasada por la agenda (-7 a +45 dias)
resuelve todo para TODA la cartera -- instalaciones, retiros, señal de vida
(ultima_cita/tiene_cita_futura) y motivos sin clasificar. Barrer por
paciente (dentidesk.citas_futuras_paciente) seria ~12s por paciente; aca es
una sola pasada compartida.

Config + registro propios (no reusan los de recaptacion.py, son avisos
distintos), en el mismo disco persistente de Render (misma base que
patient_index.json / confirmaciones_enviadas.json, via PATIENT_INDEX_PATH)
para sobrevivir a los redeploys sin pasar por git.
"""

import os
import json
import unicodedata
import threading
import calendar
from pathlib import Path
from datetime import date, datetime, timedelta

import dentidesk
import fechas      # hoy_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.
import avisos      # rut_key + lista de no molestar, compartidos. Ver avisos.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('CONTROL_DENTAL_CONFIG_PATH', _BASE_DIR / 'control_dental_config.json'))
REGISTRO_PATH = Path(os.environ.get('CONTROL_DENTAL_REGISTRO_PATH', _BASE_DIR / 'control_dental_registro.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    'activo': False,               # OFF por defecto (igual que recordatorios_wa) -- se
                                    # enciende solo cuando la clinica reviso la cartera inscrita.
    'frecuencia_meses': 6,
    'hora_envio': '11:00',
    'max_envios_por_dia': 30,      # anti-oleada del backfill inicial, ver barrer()/backfill()
    'meses_sin_actividad_pausa': 9,
    'motivos_extra': {},           # override desde el panel: {'motivo normalizado': 'categoria'}
                                    # -- se consulta ANTES que las constantes de abajo, asi se
                                    # puede clasificar un motivo nuevo sin deploy.
}

# Cuantos dias mantener una entrada en 'vistos' (dedup del barrido). Sin poda el
# JSON crece para siempre -- el barrido solo necesita mirar atras dias_atras (7)
# mas margen, no toda la historia.
_DIAS_RETENCION_VISTOS = 90


# ── Clasificacion de motivos (Reason -> categoria) ──────────────────────────
#
# El match es POR NOMBRE (Reason), que es lo que devuelve getAgendaDay --
# getAgendaDay NUNCA trae el IdReason numerico (confirmado en dentidesk.py y
# en la migracion de reagendar-info). Los IdReason de aca quedan solo como
# documentacion/trazabilidad hacia motivos_consulta.txt, no se usan en el
# codigo. Los nombres estan copiados TAL CUAL de motivos_consulta.txt (no del
# texto mas corto que aparece en el plan) porque ese .txt es lo unico
# verificado contra lo que la clinica realmente escribe en DentiDesk -- ej.
# "Instalar 2 x 4" (con espacios) y "Cementar Marpe (Expansor)", no las
# variantes abreviadas.

_INICIO_FIJOS = {
    'montaje total': 18163,
    'montaje parcial': 18164,
    'montaje lingual parcial': 20122,
    'montaje lingual total': 20123,
    'instalar 2 x 4': 20012,
    'instalar apto. herbst': 20180,
    'instalar forsus': 20185,
    'instalar hyrax': 25146,
    'instalar distal jet': 32291,
    'instalar carriere': 33311,
    'instalar pendulo': 20186,
    'cementar marpe (expansor)': 28070,
    'montaje vestibular + digitrack': 27673,
    'montaje lingual + digitrack': 27674,
}

# Los *refinamientos* (25091/25092/27672) NO inscriben a proposito: son un
# ajuste a mitad de tratamiento, el paciente ya estaba inscrito desde su
# instalacion original. Por eso NO aparecen en esta lista.
_INICIO_ALINEADORES = {
    'instalar digitrack': 20113,
    'instalar invisalign': 20114,
    'instalar clear correct': 26972,
    'explicacion plan + instalacion digitrack': 35053,
}

# Baja DEFINITIVA -- nunca se reactiva sola (una vez que el barrido la
# aplica, gana incluso sobre un paciente con bloqueo_manual: ver evaluar()
# y la nota grande mas abajo).
_FIN_DEFINITIVO = {
    'retiro total': 18171,
    'retiro digitrack': 21795,
    'retiro invisalign': 26032,
    'retiro clear correct': 31966,
    'retiro total + inicio': 33599,
    'retenedor fijo': 20048,
    'control contencion': 27245,
    'retiro retenedores fijos': 28432,
    'retiro aptos. por alergia': 20133,
}

# Baja REACTIVABLE (retiros de fase). Clinicamente el paciente que se retira
# el disyuntor o hace un retiro parcial SUELE seguir en tratamiento -- el
# usuario eligio a proposito tratarlo como baja igual (para que deje de
# recibir el correo mientras tanto), pero el panel la muestra aparte
# ("Bajas por retiro de fase -- revisar") con boton de reactivar. Una baja
# fin_fase NUNCA se re-aplica sobre un paciente con bloqueo_manual (a
# diferencia de fin_definitivo, que si "gana" siempre -- la realidad manda).
_FIN_FASE = {
    'retiro parcial': 18172,
    'retiro 2 x 4': 20191,
    'retiro disyuntor': 20052,
    'retiro forsus': 20125,
    'retiro pendulo': 20126,
    'retiro mascara de laire': 23801,
    'retiro barra palatina': 20175,
}

# NUNCA cuentan como fin de tratamiento (documentacion -- no hace falta que
# esten en una lista para que clasificar_motivo() los ignore, ya que si no
# estan en ninguna lista de arriba devuelve None; se dejan aca explicitos
# para que quede registrado por que NO se agregaron, y como defensa si
# alguien los agrega sin leer este comentario).
# - Retiro Aptos. para Resonancia Magnetica (20132): es temporal, no un fin.
# - Retiro Microtornillo (20075): un microtornillo no es el tratamiento.
# - Retiro Topes (26435): idem, un aditamento auxiliar.
_NUNCA_FIN = {
    'retiro aptos. para resonancia magnetica': 20132,
    'retiro microtornillo': 20075,
    'retiro topes': 26435,
}

# Solo senial de vida (actualiza ultima_cita), no inscribe ni da de baja.
# Se excluye a proposito "Control / Evaluacion PV" (24798): es del Dr. Vial
# (rehabilitacion), no de ortodoncia -- no debe contar como actividad de
# control dental de ortodoncia.
_CONTROL = {
    'control fijo': 16073,
    'control digitrack': 20071,
    'control invisalign': 20070,
    'control lingual total': 20121,
    'control lingual parcial': 20120,
    'control clear correct': 27649,
    'control disyuntor': 33821,
    'control pasivo': 18162,
    'control digitrack + fijo': 27675,
    'control digitrack + lingual': 27676,
    'control invisalign + fijo': 20188,
    'control barra / nance': 33779,
    'control higiene': 19964,
    'control higiene + bandas': 36181,
    'control microtornillos': 20173,
    'control plano relajacion': 20115,
    'control removible': 20013,
}


# Estados que significan que la cita NO ocurrio -- los unicos que hay que
# descartar al mirar dias PASADOS. Es deliberadamente distinto de
# dentidesk._ESTADOS_INACTIVOS: ese descarta ademas 'atendid', que para una
# cita futura tiene sentido (ya no es una hora proxima) pero en el pasado es
# exactamente la señal que buscamos (la instalacion si se hizo).
_ESTADOS_NO_OCURRIO = ('cancel', 'no llega', 'no seguir', 'reagend', 're-agend')


def _normalizar(texto):
    """Sin tildes, minusculas, espacios colapsados -- mismo truco que
    pacientes._normalizar_genero (NFKD + descartar los combining marks) y
    mismo criterio que seguros._norm_glosa. NO se tocan otros signos
    (., /, +) porque varios nombres de motivo los usan para distinguirse
    ('Retiro Total' vs 'Retiro Total + Inicio')."""
    if not texto:
        return ''
    sin_tildes = unicodedata.normalize('NFKD', texto)
    sin_tildes = ''.join(c for c in sin_tildes if not unicodedata.combining(c))
    return ' '.join(sin_tildes.lower().split())


def clasificar_motivo(reason, cfg=None):
    """Devuelve 'inicio_fijos'|'inicio_alineadores'|'fin_definitivo'|
    'fin_fase'|'control'|None segun el Reason (tal como lo devuelve
    getAgendaDay). cfg['motivos_extra'] (si viene) se consulta ANTES que
    las constantes del modulo -- asi el panel puede resolver un motivo nuevo
    o ambiguo (ej. 'Aligner/Essix', 'Placa') sin esperar un deploy."""
    clave = _normalizar(reason)
    if not clave:
        return None

    cfg = cfg or {}
    extra = cfg.get('motivos_extra') or {}
    if clave in extra:
        return extra[clave] or None

    if clave in _INICIO_FIJOS:
        return 'inicio_fijos'
    if clave in _INICIO_ALINEADORES:
        return 'inicio_alineadores'
    if clave in _FIN_DEFINITIVO:
        return 'fin_definitivo'
    if clave in _FIN_FASE:
        return 'fin_fase'
    if clave in _CONTROL:
        return 'control'
    return None


# ── Aritmetica de meses (no existe en el proyecto, no se agrega python-dateutil) ──

def sumar_meses(d, n):
    """d + n meses, con el mismo criterio que compras._dia_ajustado para los
    meses cortos: si el dia de 'd' no existe en el mes de destino (31 de
    agosto + 6 -> febrero no tiene 31), cae al ultimo dia real de ese mes
    (usa calendar.monthrange, igual que compras.py -- sin depender de
    python-dateutil, que no es dependencia del proyecto)."""
    total_meses = d.month - 1 + n
    anio = d.year + total_meses // 12
    mes = total_meses % 12 + 1
    dia = min(d.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)


# ── Config ───────────────────────────────────────────────────────────────

def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # copia profunda
    if not isinstance(data, dict):
        return cfg
    if 'activo' in data:
        cfg['activo'] = bool(data['activo'])
    if 'frecuencia_meses' in data:
        try:
            n = int(data['frecuencia_meses'])
            if n > 0:
                cfg['frecuencia_meses'] = n
        except (TypeError, ValueError):
            pass
    if 'hora_envio' in data:
        hora = str(data['hora_envio']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['hora_envio'] = hora
    if 'max_envios_por_dia' in data:
        try:
            n = int(data['max_envios_por_dia'])
            if n > 0:
                cfg['max_envios_por_dia'] = n
        except (TypeError, ValueError):
            pass
    if 'meses_sin_actividad_pausa' in data:
        try:
            n = int(data['meses_sin_actividad_pausa'])
            if n > 0:
                cfg['meses_sin_actividad_pausa'] = n
        except (TypeError, ValueError):
            pass
    if isinstance(data.get('motivos_extra'), dict):
        cfg['motivos_extra'] = dict(data['motivos_extra'])
    return cfg


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo
    criterio que recaptacion.save_config()."""
    with _LOCK:
        cfg = load_config()
        if not isinstance(updates, dict):
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix('.json.tmp')
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, CONFIG_PATH)
            return cfg
        if 'activo' in updates:
            cfg['activo'] = bool(updates['activo'])
        if 'frecuencia_meses' in updates:
            try:
                n = int(updates['frecuencia_meses'])
                if n > 0:
                    cfg['frecuencia_meses'] = n
            except (TypeError, ValueError):
                pass
        if 'hora_envio' in updates:
            hora = str(updates['hora_envio']).strip()
            if len(hora) == 5 and hora[2] == ':':
                cfg['hora_envio'] = hora
        if 'max_envios_por_dia' in updates:
            try:
                n = int(updates['max_envios_por_dia'])
                if n > 0:
                    cfg['max_envios_por_dia'] = n
            except (TypeError, ValueError):
                pass
        if 'meses_sin_actividad_pausa' in updates:
            try:
                n = int(updates['meses_sin_actividad_pausa'])
                if n > 0:
                    cfg['meses_sin_actividad_pausa'] = n
            except (TypeError, ValueError):
                pass
        if isinstance(updates.get('motivos_extra'), dict):
            cfg['motivos_extra'] = dict(updates['motivos_extra'])
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro ─────────────────────────────────────────────────────────────

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(
    REGISTRO_PATH, indent=2,
    default={'inscritos': {}, 'no_molestar': [], 'vistos': {}, 'motivos_desconocidos': {}},
    claves={'inscritos': {}, 'no_molestar': [], 'vistos': {}, 'motivos_desconocidos': {}})


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


# Clave canonica del paciente, compartida con recaptacion y nps: ver avisos.py.
_rut_key = avisos.rut_key


# ── Evaluacion (las 4 guardas, en orden) ────────────────────────────────────

def evaluar(rut, cfg=None):
    """Devuelve None si se puede enviar, o un dict {'motivo','detalle',
    'puede_forzar'} si hay que bloquear. Orden de las guardas (mismo
    contrato que recaptacion.evaluar):
      1. no_molestar -- nunca se salta.
      2. estado != 'activo' -- baja / desactivado a mano / sin email.
      3. señal de vida -- sin cita (pasada) en los ultimos
         meses_sin_actividad_pausa y sin cita futura -> pausado_inactivo.
         Es la guarda que atrapa al paciente que dejo de venir sin pasar por
         una cita de retiro -- sin ella el sistema le manda correos para
         siempre.
      4. email invalido -> sin_email."""
    cfg = cfg or load_config()
    clave = _rut_key(rut)
    reg = _load_registro()

    if clave in (reg.get('no_molestar') or []):
        return {
            'motivo': 'no_molestar',
            'detalle': 'Este paciente está marcado como "no molestar": no se le envían recordatorios de control dental.',
            'puede_forzar': False,
        }

    p = (reg.get('inscritos') or {}).get(clave)
    if not p:
        return {
            'motivo': 'no_inscrito',
            'detalle': 'Este paciente no está inscrito en control dental.',
            'puede_forzar': False,
        }

    if p.get('estado') != 'activo':
        return {
            'motivo': p.get('estado') or 'inactivo',
            'detalle': f"El paciente no está activo en control dental (estado: {p.get('estado') or 'desconocido'}).",
            'puede_forzar': True,
        }

    meses_pausa = cfg.get('meses_sin_actividad_pausa', 9)
    if not p.get('tiene_cita_futura'):
        ultima = p.get('ultima_cita') or p.get('fecha_inicio') or ''
        try:
            f_ultima = date.fromisoformat(ultima[:10])
            limite = sumar_meses(f_ultima, meses_pausa)
            if fechas.hoy_chile() > limite:
                return {
                    'motivo': 'pausado_inactivo',
                    'detalle': f'El paciente no tiene citas hace más de {meses_pausa} meses y no tiene hora futura -- se pausó automáticamente.',
                    'puede_forzar': True,
                }
        except (ValueError, TypeError):
            pass

    email = (p.get('email') or '').strip()
    if not email or '@' not in email:
        return {
            'motivo': 'sin_email',
            'detalle': 'El paciente no tiene un email válido registrado.',
            'puede_forzar': False,
        }

    return None


# ── Inscripcion / bajas / control manual (F2 y panel) ───────────────────────

def inscribir(rut, nombre, email, tipo, fecha_inicio, id_agenda_inicio, motivo_inicio,
              doctor, cfg=None, manual=False):
    """Inscribe (o re-inscribe) a un paciente. 'tipo' es 'fijos'|'alineadores'|
    'ambos'|'manual'. fecha_base arranca en fecha_inicio; frecuencia_meses
    hereda la global salvo que ya tuviera un override guardado. manual=True
    (desde el F2) marca bloqueo_manual -- el barrido ya no lo tocara salvo
    la excepcion de fin_definitivo (ver evaluar/barrer)."""
    cfg = cfg or load_config()
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        existente = reg.get('inscritos', {}).get(clave, {})
        frecuencia = existente.get('frecuencia_meses', cfg.get('frecuencia_meses', 6))
        fecha_base = existente.get('fecha_base', fecha_inicio)
        registro = {
            'nombre': nombre or existente.get('nombre', ''),
            'email': email or existente.get('email', ''),
            'tipo': tipo or existente.get('tipo', 'manual'),
            'fecha_inicio': fecha_inicio or existente.get('fecha_inicio', ''),
            'id_agenda_inicio': str(id_agenda_inicio or existente.get('id_agenda_inicio', '')),
            'motivo_inicio': motivo_inicio or existente.get('motivo_inicio', ''),
            'doctor': doctor or existente.get('doctor', ''),
            'estado': 'activo',
            'motivo_baja': '',
            'fecha_baja': '',
            'frecuencia_meses': frecuencia,
            'fecha_base': fecha_base,
            'proximo_envio': existente.get('proximo_envio') or sumar_meses(
                date.fromisoformat(fecha_base), frecuencia).isoformat(),
            'ultima_cita': existente.get('ultima_cita', fecha_inicio or ''),
            'tiene_cita_futura': existente.get('tiene_cita_futura', False),
            'bloqueo_manual': bool(manual) or existente.get('bloqueo_manual', False),
            'envios': existente.get('envios', []),
        }
        reg.setdefault('inscritos', {})[clave] = registro
        _save_registro(reg)
        return registro


def dar_de_baja(rut, motivo_baja, fecha_baja=None, forzar=False):
    """Da de baja a un paciente (motivo_baja: 'fin_definitivo'|'fin_fase' o
    texto libre si es manual). Respeta la regla de precedencia: si esta
    bloqueado a mano (bloqueo_manual=True) y la baja NO es fin_definitivo,
    NO se aplica -- salvo forzar=True (lo usa el F2, que si actua a mano
    puede dar de baja aunque el paciente estuviera bloqueado). Devuelve el
    registro actualizado, o el registro sin tocar si la baja se rechazo, o
    None si el RUT no esta inscrito."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        p = reg.get('inscritos', {}).get(clave)
        if not p:
            return None
        if p.get('bloqueo_manual') and motivo_baja != 'fin_definitivo' and not forzar:
            # Una baja fin_fase NUNCA se re-aplica sobre un paciente que la
            # asistente ya toco a mano -- solo fin_definitivo "gana siempre"
            # porque es la realidad (si el paciente de verdad termino el
            # tratamiento, no importa que la secretaria lo hubiera reactivado).
            return p
        p['estado'] = 'dado_de_baja'
        p['motivo_baja'] = motivo_baja or ''
        p['fecha_baja'] = fecha_baja or fechas.hoy_chile().isoformat()
        _save_registro(reg)
        return p


def reactivar(rut, manual=True):
    """Reactiva a un paciente dado de baja (tipico: baja fin_fase que la
    asistente revisa y decide que el paciente sigue en tratamiento). Vuelve
    a 'activo' y, si manual=True (F2/panel), marca bloqueo_manual para que
    el barrido no lo vuelva a dar de baja por el mismo motivo de fase."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        p = reg.get('inscritos', {}).get(clave)
        if not p:
            return None
        p['estado'] = 'activo'
        p['motivo_baja'] = ''
        p['fecha_baja'] = ''
        if manual:
            p['bloqueo_manual'] = True
        _save_registro(reg)
        return p


def set_manual(rut, activo=None, frecuencia_meses=None, fecha_base=None):
    """Ajustes del F2/panel sobre un inscrito: activar/desactivar,
    frecuencia propia, o correr la fecha base (ej: 'el paciente fue al
    dentista en abril' -> fecha_base=2026-04-15, recalcula proximo_envio).
    Cualquier llamada aca marca bloqueo_manual=True -- es, por definicion,
    la asistente tocando al paciente a mano."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        p = reg.get('inscritos', {}).get(clave)
        if not p:
            return None
        p['bloqueo_manual'] = True
        if activo is not None:
            p['estado'] = 'activo' if activo else 'desactivado_manual'
            if activo:
                p['motivo_baja'] = ''
                p['fecha_baja'] = ''
        if frecuencia_meses:
            try:
                p['frecuencia_meses'] = int(frecuencia_meses)
            except (TypeError, ValueError):
                pass
        if fecha_base:
            p['fecha_base'] = fecha_base
            try:
                p['proximo_envio'] = sumar_meses(
                    date.fromisoformat(fecha_base), p.get('frecuencia_meses', 6)).isoformat()
            except (TypeError, ValueError):
                pass
        _save_registro(reg)
        return p


# El opt-out del paciente. Compartido con recaptacion y nps: ver avisos.py.
_NO_MOLESTAR = avisos.ListaNoMolestar(_load_registro, _save_registro, _LOCK)


def agregar_no_molestar(rut):
    return _NO_MOLESTAR.agregar(rut)


def quitar_no_molestar(rut):
    return _NO_MOLESTAR.quitar(rut)


def lista_no_molestar():
    """Faltaba en este modulo (recaptacion y nps si la tenian), asi que server.py
    leia el registro a mano para armar la respuesta del panel."""
    return _NO_MOLESTAR.listar()


def en_no_molestar(rut):
    return _NO_MOLESTAR.contiene(rut)


# ── Envios / consultas para el panel ─────────────────────────────────────

def pendientes_hoy(hoy=None):
    """Inscritos activos cuyo proximo_envio ya llego (<=hoy), ordenados por
    proximo_envio ASCENDENTE (los mas vencidos primero) -- asi
    max_envios_por_dia (ver server.py) reparte la cola por antiguedad, no al
    azar del orden del dict."""
    hoy_iso = (hoy.isoformat() if hasattr(hoy, 'isoformat') else str(hoy)) if hoy else fechas.hoy_chile().isoformat()
    reg = _load_registro()
    pendientes = [
        {'rut': rut, **p} for rut, p in (reg.get('inscritos') or {}).items()
        if p.get('estado') == 'activo' and p.get('proximo_envio', '') <= hoy_iso
    ]
    pendientes.sort(key=lambda p: p.get('proximo_envio', ''))
    return pendientes


def proximos_envios(fecha=None, doctor=None, cfg=None):
    """Inscritos a los que se les ENVIARIA el recordatorio en/antes de 'fecha'
    (proximo_envio <= fecha), opcionalmente filtrado por 'doctor' (subcadena,
    case-insensitive, contra el campo 'doctor' = profesional que instalo los
    aparatos). Corre evaluar() y devuelve SOLO los que de verdad saldrian (las
    guardas no los bloquean) -- asi la lista es honesta con "se les enviara",
    no un simple filtro de fecha. evaluar() es 100% local (no toca DentiDesk),
    asi que esto es barato.

    Lo consume el reporte de evoluciones del Dr. Alberto (runbook
    revision-evoluciones) para avisarle a que pacientes suyos les llega el
    recordatorio dental al dia siguiente. Ordenado por proximo_envio."""
    hoy_iso = (fecha.isoformat() if hasattr(fecha, 'isoformat') else str(fecha)) \
        if fecha else fechas.hoy_chile().isoformat()
    doc_norm = _normalizar(doctor) if doctor else ''
    cfg = cfg or load_config()
    reg = _load_registro()

    out = []
    for rut, p in (reg.get('inscritos') or {}).items():
        if p.get('estado') != 'activo' or p.get('proximo_envio', '') > hoy_iso:
            continue
        if doc_norm and doc_norm not in _normalizar(p.get('doctor', '')):
            continue
        if evaluar(rut, cfg) is not None:  # alguna guarda lo bloquea -> no sale
            continue
        out.append({'rut': rut, **p})
    out.sort(key=lambda x: x.get('proximo_envio', ''))
    return out


def marcar_enviado(rut):
    """Registra el envio y recalcula proximo_envio = fecha_base +
    frecuencia_meses, adelantando fecha_base al dia del envio (asi cada
    envio ancla el siguiente ciclo en la fecha real de envio, no acumula
    corrimiento respecto de la fecha de instalacion original)."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        p = reg.get('inscritos', {}).get(clave)
        if not p:
            return None
        hoy = fechas.hoy_chile()
        p.setdefault('envios', []).append({
            'fecha': hoy.isoformat(),
            'email': p.get('email', ''),
        })
        p['fecha_base'] = hoy.isoformat()
        p['proximo_envio'] = sumar_meses(hoy, p.get('frecuencia_meses', 6)).isoformat()
        _save_registro(reg)
        return p


def historial(limite=100):
    """Envios aplanados (RUT + datos del envio), del mas reciente al mas
    antiguo. Para la pestania del panel."""
    reg = _load_registro()
    plano = []
    for rut, p in (reg.get('inscritos') or {}).items():
        for e in p.get('envios', []):
            plano.append({**e, 'rut': rut, 'nombre': p.get('nombre', '')})
    plano.sort(key=lambda e: e.get('fecha', ''), reverse=True)
    return plano[:limite]


def listar(filtro=None):
    """Inscritos, opcionalmente filtrados por 'estado'. Para la tabla del
    panel."""
    reg = _load_registro()
    items = [{'rut': rut, **p} for rut, p in (reg.get('inscritos') or {}).items()]
    if filtro:
        items = [i for i in items if i.get('estado') == filtro]
    items.sort(key=lambda i: i.get('nombre', ''))
    return items


def motivos_desconocidos():
    """Motivos vistos en la agenda que no calzaron con ninguna categoria --
    para que el panel los muestre y la clinica los clasifique (o los agregue
    a motivos_extra) sin esperar un deploy."""
    return dict(_load_registro().get('motivos_desconocidos') or {})


def clasificar_motivo_desconocido(reason, categoria):
    """El panel resuelve un motivo desconocido: lo guarda en
    cfg['motivos_extra'] (para que clasificar_motivo lo reconozca desde
    ahora) y lo saca de la lista de pendientes."""
    clave = _normalizar(reason)
    cfg = load_config()
    extra = dict(cfg.get('motivos_extra') or {})
    extra[clave] = categoria
    save_config({'motivos_extra': extra})
    with _LOCK:
        reg = _load_registro()
        reg.get('motivos_desconocidos', {}).pop(reason, None)
        _save_registro(reg)


# ── El barrido: una sola pasada por la agenda resuelve todo ─────────────────

def _email_de_cita(c, rut):
    """El email del paciente puede venir directo en la cita (getAgendaDay SI
    trae 'PatientEmail' en la practica -- lo usan confirmaciones.py,
    server.py y pacientes.construir_desde_agenda, verificado leyendo esos
    modulos), asi que se prueba primero ahi porque no cuesta una llamada
    extra. Si no viene (ficha antigua sin correo en esa cita puntual), se
    cae a la base local (pacientes.lookup), que es donde el barrido
    2x/dia de pacientes.py la va completando."""
    email = (c.get('PatientEmail') or '').strip()
    if email and '@' in email:
        return email
    import pacientes
    rec = pacientes.lookup(rut)
    return (rec or {}).get('email', '') or ''


def _procesar_citas_dia(reg, cfg, citas, hoy_iso, es_pasado):
    """Aplica UNA lista de citas (de un dia) sobre 'reg', mutandolo in-place.
    Separado de barrer() para que backfill() (que barre un rango mas largo,
    hacia atras solamente) reutilice exactamente la misma logica de
    inscripcion/baja/señal de vida, sin duplicar el criterio."""
    for c in citas:
        id_agenda = str(c.get('IdAgenda') or '')
        if not id_agenda:
            continue
        estado = (c.get('Status') or '').lower()
        # OJO: aca NO sirve dentidesk._ESTADOS_INACTIVOS. Esa tupla incluye
        # 'atendid', porque esta escrita para citas FUTURAS (una cita ya
        # atendida no es una "hora proxima" que avisarle al paciente). Pero
        # este barrido mira dias PASADOS, y ahi "Atendido" es justo la prueba
        # de que la instalacion de verdad ocurrio -- la clinica marca las
        # citas como atendidas despues de la visita, asi que filtrarlas
        # dejaria al sistema sin ver casi ninguna instalacion real.
        # Lo unico que hay que descartar en el pasado son los estados que
        # significan que la cita NO paso.
        inactivo = any(s in estado for s in
                       (_ESTADOS_NO_OCURRIO if es_pasado else dentidesk._ESTADOS_INACTIVOS))

        # Dedup por IdAgenda: si ya se proceso esta cita en un barrido
        # anterior, no se vuelve a inscribir/dar de baja (idempotencia) --
        # pero SI se sigue usando como señal de vida mas abajo (eso no
        # duplica nada, solo actualiza una fecha).
        ya_visto = id_agenda in reg.get('vistos', {})

        rut = dentidesk.limpiar_rut(str(c.get('PatientDocument', '')))
        if not rut:
            continue
        nombre = (c.get('PatientName') or '').strip()
        doctor = (c.get('ProfessionalName') or '').strip()
        reason = (c.get('Reason') or '').strip()
        fecha_cita = c.get('Date') or hoy_iso

        categoria = clasificar_motivo(reason, cfg)

        if not inactivo and not ya_visto and es_pasado:
            if categoria in ('inicio_fijos', 'inicio_alineadores'):
                tipo = 'fijos' if categoria == 'inicio_fijos' else 'alineadores'
                existente = reg.get('inscritos', {}).get(rut)
                # Si ya estaba inscrito con OTRO tipo (instalacion mixta,
                # ej. fijos superior + alineador inferior en tratamientos
                # raros), se marca 'ambos' en vez de pisar el tipo.
                if existente and existente.get('estado') == 'activo' and existente.get('tipo') not in (tipo, 'manual'):
                    tipo = 'ambos'
                frecuencia = cfg.get('frecuencia_meses', 6)
                fecha_base = (existente or {}).get('fecha_base') if existente else fecha_cita
                fecha_base = fecha_base or fecha_cita
                email = (existente or {}).get('email', '') or _email_de_cita(c, rut)
                nuevo = {
                    'nombre': nombre or (existente or {}).get('nombre', ''),
                    'email': email,
                    'tipo': tipo,
                    'fecha_inicio': (existente or {}).get('fecha_inicio') or fecha_cita,
                    'id_agenda_inicio': id_agenda,
                    'motivo_inicio': reason,
                    'doctor': doctor or (existente or {}).get('doctor', ''),
                    'estado': 'activo',
                    'motivo_baja': '',
                    'fecha_baja': '',
                    'frecuencia_meses': (existente or {}).get('frecuencia_meses', frecuencia),
                    'fecha_base': fecha_base,
                    'proximo_envio': (existente or {}).get('proximo_envio') or sumar_meses(
                        date.fromisoformat(fecha_base), frecuencia).isoformat(),
                    'ultima_cita': fecha_cita,
                    'tiene_cita_futura': (existente or {}).get('tiene_cita_futura', False),
                    'bloqueo_manual': (existente or {}).get('bloqueo_manual', False),
                    'envios': (existente or {}).get('envios', []),
                }
                # Un paciente con bloqueo_manual (la asistente lo desactivo,
                # o le cambio frecuencia/fecha a mano) NO se re-inscribe solo
                # porque el barrido volvio a ver la misma cita de instalacion
                # -- eso pisaria la decision humana.
                if not (existente or {}).get('bloqueo_manual'):
                    reg.setdefault('inscritos', {})[rut] = nuevo
            elif categoria in ('fin_definitivo', 'fin_fase'):
                existente = reg.get('inscritos', {}).get(rut)
                if existente and existente.get('estado') == 'activo':
                    bloqueado = existente.get('bloqueo_manual')
                    # Regla de precedencia (la parte delicada del plan): si
                    # esta bloqueado a mano, el barrido NO puede reactivar
                    # ni desactivar -- SALVO que sea una baja fin_definitivo,
                    # que siempre gana (es la realidad: el paciente de
                    # verdad termino). fin_fase jamas se re-aplica sobre un
                    # bloqueo_manual.
                    if not bloqueado or categoria == 'fin_definitivo':
                        existente['estado'] = 'dado_de_baja'
                        existente['motivo_baja'] = categoria
                        existente['fecha_baja'] = fecha_cita
                        if categoria == 'fin_definitivo':
                            existente['bloqueo_manual'] = False
            elif categoria is None:
                desc = reg.setdefault('motivos_desconocidos', {})
                info = desc.get(reason, {'n': 0, 'ultima': ''})
                info['n'] = info.get('n', 0) + 1
                info['ultima'] = fecha_cita
                desc[reason] = info

        # Señal de vida: cualquier cita del inscrito, pasada o futura,
        # cuenta -- independiente de si ya se habia 'visto' antes (no hay
        # nada que duplicar, solo se actualiza una fecha/flag).
        inscrito = reg.get('inscritos', {}).get(rut)
        if inscrito and not inactivo:
            if es_pasado:
                if fecha_cita > (inscrito.get('ultima_cita') or ''):
                    inscrito['ultima_cita'] = fecha_cita
            else:
                inscrito['tiene_cita_futura'] = True

        if id_agenda:
            reg.setdefault('vistos', {})[id_agenda] = hoy_iso


def barrer(cfg=None, dias_atras=7, dias_adelante=45, max_workers=6):
    """La pasada unica: recorre getAgendaDay de -dias_atras a +dias_adelante
    y actualiza inscripcion/bajas/señal de vida para TODA la cartera. Los
    dias pasados (con estado real) deciden inscripcion/baja/motivos
    desconocidos; los dias futuros solo aportan 'tiene_cita_futura'.
    Idempotente por dedup en reg['vistos'] (clave IdAgenda) y poda las
    entradas de 'vistos' mas viejas que _DIAS_RETENCION_VISTOS para que el
    JSON no crezca sin techo."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg or load_config()
    scfg = _scheduling_cfg()
    hoy = fechas.hoy_chile()
    hoy_iso = hoy.isoformat()

    # Solo dias habiles: la clinica atiende L-V, asi que pedir sabados y
    # domingos son ~28% de llamadas a DentiDesk tiradas a la basura. Mismo
    # filtro que ya usan dentidesk.citas_futuras_paciente y
    # pacientes.construir_desde_agenda.
    dias = [(hoy + timedelta(days=k), k < 0)
            for k in range(-dias_atras, dias_adelante + 1)
            if (hoy + timedelta(days=k)).weekday() < 5]

    def scan(par):
        d, es_pasado = par
        try:
            return (d, es_pasado, dentidesk._get_agenda_day(scfg, d))
        except Exception:
            return (d, es_pasado, [])

    resultados = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for r in pool.map(scan, dias):
            resultados.append(r)
    # Orden cronologico antes de procesar: si dos citas del mismo dia tocan
    # al mismo paciente (instalacion y luego control), procesar en orden no
    # cambia el resultado aca (cada dia es independiente), pero mantiene el
    # comportamiento predecible si en el futuro se agrega logica que si
    # dependa del orden.
    resultados.sort(key=lambda r: r[0])

    with _LOCK:
        reg = _load_registro()
        # tiene_cita_futura se RECALCULA en cada barrido, no se acumula:
        # _procesar_citas_dia solo lo sabe poner en True (ve una cita futura
        # y la marca), asi que sin este reset el flag quedaria pegado en True
        # para siempre en cuanto el paciente agenda una hora una sola vez --
        # y la guarda 'pausado_inactivo' de evaluar(), que corta apenas
        # tiene_cita_futura es True, nunca volveria a dispararse. Se hace
        # solo aca y NO en backfill(), que barre unicamente hacia atras y por
        # lo tanto no tiene con que volver a poblarlo.
        for p in reg.get('inscritos', {}).values():
            p['tiene_cita_futura'] = False
        for d, es_pasado, citas in resultados:
            _procesar_citas_dia(reg, cfg, citas, d.isoformat(), es_pasado)

        limite_poda = (hoy - timedelta(days=_DIAS_RETENCION_VISTOS)).isoformat()
        vistos = reg.get('vistos', {})
        reg['vistos'] = {k: v for k, v in vistos.items() if v >= limite_poda}

        _save_registro(reg)
    return {'dias_procesados': len(dias), 'hoy': hoy_iso}


def backfill(cfg=None, meses=6, max_workers=6):
    """Inscribe la cartera actual: barre hacia atras 'meses' meses SOLAMENTE
    (sin dias_adelante -- a diferencia de barrer(), esto es un barrido de
    UNA VEZ para poblar el registro, no el barrido diario de mantenimiento).
    Molde exacto de pacientes.construir_desde_agenda (mismo
    ThreadPoolExecutor, mismo patron recolectar-primero-mergear-despues).

    Anti-oleada: nunca fija un proximo_envio anterior a hoy+2 dias. Sin esto,
    un paciente cuya instalacion fue hace 5-6 meses saldria con proximo_envio
    en el pasado, y docenas de correos saldrian de golpe en la primera
    corrida del scheduler (mismo problema ya resuelto en confirmaciones.py
    con 'la primera corrida solo siembra')."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg or load_config()
    scfg = _scheduling_cfg()
    hoy = fechas.hoy_chile()
    # sumar_meses(-meses) en vez de meses*30: 6*30 son 180 dias, casi seis
    # dias menos que seis meses reales, y el borde es justo donde estan los
    # pacientes que se quieren pescar. Solo dias habiles (la clinica atiende
    # L-V), igual que barrer().
    desde = sumar_meses(hoy, -meses)
    dias = [d for d in (desde + timedelta(days=k) for k in range((hoy - desde).days + 1))
            if d.weekday() < 5]
    minimo_envio = hoy + timedelta(days=2)

    def scan(d):
        try:
            return dentidesk._get_agenda_day(scfg, d)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        listas = list(pool.map(scan, dias))

    with _LOCK:
        reg = _load_registro()
        for d, citas in zip(dias, listas):
            _procesar_citas_dia(reg, cfg, citas, d.isoformat(), True)

        # Anti-oleada: recorrer los inscritos recien creados/actualizados por
        # este backfill y correr el proximo_envio que haya quedado en el
        # pasado o muy pronto hacia hoy+2 dias, repartiendo por fecha_base
        # para no perder el orden relativo (el mas antiguo sigue siendo el
        # primero en salir, solo que todos arrancan un poco mas adelante).
        pendientes_atrasados = [
            p for p in reg.get('inscritos', {}).values()
            if p.get('estado') == 'activo' and p.get('proximo_envio', '') < minimo_envio.isoformat()
        ]
        pendientes_atrasados.sort(key=lambda p: p.get('proximo_envio', ''))
        for offset, p in enumerate(pendientes_atrasados):
            p['proximo_envio'] = (minimo_envio + timedelta(days=offset)).isoformat()

        _save_registro(reg)
    return {'dias_procesados': len(dias), 'inscritos_total': len(reg.get('inscritos', {}))}


def _scheduling_cfg():
    """dentidesk._get_agenda_day() necesita el config de scheduling
    (credenciales DentiDesk), no el de control_dental -- import perezoso
    para evitar ciclos (mismo patron que recaptacion._scheduling_cfg)."""
    import scheduling
    return scheduling.load_config()
