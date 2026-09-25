"""
seguimiento_pc.py - Seguimiento de PRIMERAS CONSULTAS que no avanzaron a
tratamiento (Ortodoncia Richard).

Idea: recuperar al paciente que vino a su Primera Consulta, fue atendido, pero
no siguió -- no tiene agendado el estudio, ni la instalación de aparatos, ni
ninguna hora futura. Es la fuga de plata más grande en ortodoncia.

A diferencia de control_dental.py / recaptacion.py / nps.py, este módulo **NO le
escribe al paciente** ni manda correo: solo detecta a los candidatos y los
expone para que aparezcan en el CORREO DIARIO del Dr. Alberto (runbook
revision-evoluciones), con un botón de WhatsApp con texto pre-cargado que él
mismo dispara desde su celular. Por eso no hereda de avisos.py salvo rut_key +
la lista de no-molestar (opt-out manual, por si Alberto quiere excluir a alguien);
no hay plantillas de Meta ni ventana de 24h que respetar.

"Dos toques": el candidato aparece una vez pasada ~1 semana de su consulta y, si
no agendó, otra vez cerca del mes. El AVANCE de cada toque lo confirma el runbook
llamando a marcar_mostrados() cuando el correo de verdad se envió -- no se avanza
al solo leerlo (una corrida con la sesión cerrada NO manda correo, y marcarlo ahí
gastaría un toque que Alberto nunca vio).

CEREBRO SIN RED: solo config, registro en JSON y lógica de decisión. La única
lectura de DentiDesk es el barrido de getAgendaDay (igual que control_dental.barrer),
que corre en el scheduler de server.py, no acá dentro de evaluar/pendientes.

Config + registro propios en el mismo disco persistente de Render (misma base que
patient_index.json, vía PATIENT_INDEX_PATH) para sobrevivir a los redeploys sin
pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime, timedelta

import dentidesk
import control_dental   # clasificar_motivo, _normalizar, sumar_meses, _ESTADOS_NO_OCURRIO
import fechas           # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore        # guardado atomico con lock. Ver jsonstore.py.
import avisos           # rut_key + lista de no molestar, compartidos. Ver avisos.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('SEGUIMIENTO_PC_CONFIG_PATH', _BASE_DIR / 'seguimiento_pc_config.json'))
REGISTRO_PATH = Path(os.environ.get('SEGUIMIENTO_PC_REGISTRO_PATH', _BASE_DIR / 'seguimiento_pc_registro.json'))

_LOCK = threading.Lock()

# Match exacto de "Primera Consulta" (normalizado). NO 'contiene': existen
# "Segunda Consulta" y "Consulta Online" que NO son primera consulta -- mismo
# criterio que scheduling_config.json -> motivos_primera_consulta.
_PRIMERA_CONSULTA = {'primera consulta'}

# Motivos que prueban que el paciente SÍ avanzó después de la primera consulta
# (además de cualquier motivo ya clasificado por control_dental: montaje,
# instalaciones, controles, retiros). El estudio y la segunda consulta son los
# pasos naturales entre "vino a evaluarse" y "partió tratamiento".
# Textos TAL CUAL los devuelve DentiDesk (normalizados sin tildes), tomados de
# scheduling_config.json: estudio_registros / estudio_explicacion + Re-estudio y
# Segunda Consulta del catalogo de motivos.
_AVANCE_EXTRA = {
    'registros para el estudio integral de ortodoncia',
    'explicacion del diagnostico y plan de tratamiento',
    'estudio integral de ortodoncia',
    're-estudio',
    'segunda consulta',
}

_DEFAULT_CONFIG = {
    'activo': True,               # solo alimenta el reporte de Alberto (no contacta
                                   # pacientes), así que es seguro partir encendido.
    'dias_toque_1': 7,            # primer toque: ~1 semana después de la consulta.
    'dias_toque_2': 30,           # segundo toque: cerca del mes.
    'min_gap_dias': 14,           # separación mínima entre toque 1 y toque 2 (por si
                                   # el toque 1 salió tarde y +30 ya pasó).
    'max_por_reporte': 15,        # tope de candidatos por correo (anti-oleada del 1er
                                   # barrido); el resto sale al día siguiente.
    'dias_atras': 40,             # ventana del barrido hacia atrás (cubre el toque 2).
    'dias_adelante': 30,          # hacia adelante: para ver si ya tiene hora futura.
    'hora_barrido': '01:00',      # el barrido corre una vez al día (ventana hasta 17:00),
                                   # antes del primer intento del reporte (02:00).
    'mensaje_toque_1': ('Hola {nombre} 😊 Soy el Dr. Alberto Del Real. Quería saber cómo '
                        'quedaste después de tu consulta y si te surgió alguna duda sobre '
                        'el tratamiento o el presupuesto. Quedo atento.'),
    'mensaje_toque_2': ('Hola {nombre}, le escribo del equipo del Dr. Alberto Del Real. Si '
                        'quiere retomar su evaluación cuando guste, con gusto le coordinamos '
                        'una hora. ¡Saludos!'),
}

_rut_key = avisos.rut_key
_normalizar = control_dental._normalizar

_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def fecha_legible(d):
    """'martes 1 de abril' (sin año -- la primera consulta es reciente)."""
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}'


def es_primera_consulta(reason):
    return _normalizar(reason) in _PRIMERA_CONSULTA


def es_avance(reason, cfg=None):
    """True si el motivo indica que el paciente avanzó (dejó de ser un candidato
    a reencantar): estudio, segunda consulta, o cualquier motivo ya clasificado
    por control_dental (montaje, instalación, control, retiro)."""
    clave = _normalizar(reason)
    if clave in _AVANCE_EXTRA:
        return True
    return control_dental.clasificar_motivo(reason, cfg) is not None


def normalizar_wa(telefono):
    """Número para el link wa.me: solo dígitos; si son 9 y parten con 9 ->
    anteponer 56; si ya parte con 56 -> tal cual. Otro formato -> '' (no
    inventar). Mismo criterio que el runbook del reporte de evoluciones."""
    digitos = ''.join(c for c in (telefono or '') if c.isdigit())
    if len(digitos) == 9 and digitos.startswith('9'):
        return '56' + digitos
    if digitos.startswith('56') and len(digitos) >= 11:
        return digitos
    return ''


# ── Config ───────────────────────────────────────────────────────────────

def _validar_config(cfg, data):
    """Aplica sobre 'cfg' (dict ya inicializado con defaults) los campos válidos
    de 'data'. Compartido por load_config y save_config."""
    if not isinstance(data, dict):
        return cfg
    if 'activo' in data:
        cfg['activo'] = bool(data['activo'])
    for k in ('dias_toque_1', 'dias_toque_2', 'min_gap_dias', 'max_por_reporte',
              'dias_atras', 'dias_adelante'):
        if k in data:
            try:
                n = int(data[k])
                if n > 0:
                    cfg[k] = n
            except (TypeError, ValueError):
                pass
    if 'hora_barrido' in data:
        hora = str(data['hora_barrido']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['hora_barrido'] = hora
    for k in ('mensaje_toque_1', 'mensaje_toque_2'):
        if k in data and str(data[k]).strip():
            cfg[k] = str(data[k])
    return cfg


def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # copia profunda
    return _validar_config(cfg, data)


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo criterio
    que control_dental.save_config()."""
    with _LOCK:
        cfg = _validar_config(load_config(), updates if isinstance(updates, dict) else {})
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro ─────────────────────────────────────────────────────────────

_ESTRUCTURA = {'candidatos': {}, 'vistos': {}, 'no_molestar': [],
               # 'descartados' es el nombre viejo (solo 'no inicia'); se conserva
               # para no perder lo ya marcado en produccion. Ver destinos_manuales().
               'descartados': {}, 'destinos': {}}

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2,
                             default=_ESTRUCTURA, claves=_ESTRUCTURA)


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


# El opt-out manual. Compartido con recaptacion/control_dental/nps: ver avisos.py.
_NO_MOLESTAR = avisos.ListaNoMolestar(_load_registro, _save_registro, _LOCK)


def agregar_no_molestar(rut):
    return _NO_MOLESTAR.agregar(rut)


def quitar_no_molestar(rut):
    return _NO_MOLESTAR.quitar(rut)


def lista_no_molestar():
    return _NO_MOLESTAR.listar()


def en_no_molestar(rut):
    return _NO_MOLESTAR.contiene(rut)


# ── El destino de una primera consulta, dicho por el doctor ─────────────────
# Hay desenlaces que el doctor SABE y el sistema no puede deducir de la agenda,
# porque viven en la evolución (texto libre en DentiDesk, fuera de la API):
#
#   · "avisó que no inicia"      — decisión tomada, no es una fuga sin explicación
#   · "en tratamiento"           — tiene un aparato puesto; la agenda a veces lo
#                                  dice con un motivo que el clasificador no reconoce
#   · "control programado"       — "vuelve en 6 meses": no es que se haya perdido,
#                                  es que su próxima cita es a propósito lejana
#   · "no requiere seguimiento"  — se evaluó y no hay nada que tratar
#
# Sin esto, los cuatro se veían igual que el paciente que simplemente desapareció, y
# además el sistema les seguía escribiendo para invitarlos a retomar.
#
# ⚠️ Va en un diccionario APARTE de `candidatos`, no como un estado más de esos. El
# doctor puede marcarlo antes de que el barrido lo detecte (el toque 1 recién va a
# los 7 días), y un estado dentro de `candidatos` obligaría a inventarle una ficha
# de candidato para poder marcarlo. Así se puede anotar desde el minuto uno.

DESTINOS = {
    'no_inicia':          'Avisó que no inicia tratamiento',
    'en_tratamiento':     'En tratamiento (aparato instalado)',
    'control_programado': 'Control programado a futuro',
    'no_requiere':        'No requiere seguimiento',
}


def marcar_destino(rut, destino, motivo='', fecha_pc=''):
    """El doctor fija el destino de una primera consulta. Devuelve False si el RUT o
    el destino no son válidos — nunca inventa un destino que no está en DESTINOS,
    porque el panel de KPIs cuenta por esa clave y una desconocida se perdería."""
    clave = _rut_key(rut)
    if not clave or destino not in DESTINOS:
        return False
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('destinos', {})[clave] = {
            'rut': clave,
            'destino': destino,
            'motivo': (motivo or '').strip()[:200],
            'fecha_pc': fecha_pc or '',
            'creado': fechas.ahora_chile().isoformat(timespec='seconds'),
        }
        # Cualquiera de los cuatro destinos significa que ya se sabe qué pasó, así
        # que deja de tener sentido ofrecerlo para contactar en el reporte diario.
        cand = (reg.get('candidatos') or {}).get(clave)
        if cand and cand.get('estado') == 'pendiente':
            cand['estado'] = 'resuelto'
        _save_registro(reg)
    return True


def quitar_destino(rut):
    """Se marcó por error, o el paciente cambió de opinión."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        quitado = (reg.get('destinos') or {}).pop(clave, None) is not None
        # `descartados` es el nombre viejo (ver la migración más abajo): se limpia
        # también, si no el paciente reaparecería marcado al próximo arranque.
        quitado = (reg.get('descartados') or {}).pop(clave, None) is not None or quitado
        cand = (reg.get('candidatos') or {}).get(clave)
        if cand and cand.get('estado') in ('resuelto', 'descartado'):
            cand['estado'] = 'pendiente'
        if quitado:
            _save_registro(reg)
    return quitado


def destino_de(rut):
    """El destino que fijó el doctor, o '' si no hay ninguno."""
    d = destinos_manuales().get(_rut_key(rut)) or {}
    return d.get('destino', '')


def destinos_manuales():
    """{rut_key: {destino, motivo, fecha_pc, creado}}. Lo lee kpi.py.

    ⚠️ MIGRA `descartados` al vuelo. Ese era el nombre cuando el único destino
    posible era "no inicia" (2026-09-25); generalizarlo a cuatro destinos dejaría
    afuera a los pacientes ya marcados en producción, y con ellos el trabajo que el
    doctor ya hizo. La migración es de LECTURA (no reescribe el archivo) para que
    sea idempotente y no dependa de que alguien corra un script."""
    reg = _load_registro()
    out = {}
    for clave, d in (reg.get('descartados') or {}).items():
        out[clave] = dict(d, destino='no_inicia')
    # Lo nuevo manda: si un RUT está en los dos, gana el destino explícito.
    out.update(reg.get('destinos') or {})
    return out


# ── Compatibilidad: el nombre viejo, cuando "no inicia" era el único destino ──

def descartar(rut, motivo='', fecha_pc=''):
    return marcar_destino(rut, 'no_inicia', motivo=motivo, fecha_pc=fecha_pc)


def deshacer_descarte(rut):
    return quitar_destino(rut)


def esta_descartado(rut):
    return destino_de(rut) == 'no_inicia'


def descartados():
    return {k: v for k, v in destinos_manuales().items()
            if v.get('destino') == 'no_inicia'}


# ── El barrido: detecta candidatos y quién ya avanzó ────────────────────────

def _telefono_de_cita(c, rut):
    """El teléfono viene en la cita (getAgendaDay trae 'Phone', igual que lo usan
    recaptacion/nps en server.py); si no, se cae a la base local (pacientes.lookup),
    que el barrido 2x/día va completando."""
    tel = (c.get('Phone') or '').strip()
    if tel:
        return tel
    try:
        import pacientes
        rec = pacientes.lookup(rut)
        return (rec or {}).get('telefono', '') or ''
    except Exception:
        return ''


def _aplicar_barrido(reg, cfg, resultados, hoy):
    """Aplica sobre 'reg' (in-place) el resultado del barrido. 'resultados' es una
    lista de (date, citas). Separado de barrer() para poder probarlo sin red
    (los tests pasan citas ya armadas)."""
    hoy_iso = hoy.isoformat()

    # 1) Agregar por RUT toda la actividad de la ventana: la primera consulta más
    #    reciente (atendida, no cancelada) y las señales de avance / hora futura.
    por_rut = {}
    for d, citas in resultados:
        d_iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10]
        es_pasado = d_iso < hoy_iso
        for c in citas:
            rut = dentidesk.limpiar_rut(str(c.get('PatientDocument', '')))
            if not rut:
                continue
            estado = (c.get('Status') or '').lower()
            inactivo = any(s in estado for s in
                           (control_dental._ESTADOS_NO_OCURRIO if es_pasado
                            else dentidesk._ESTADOS_INACTIVOS))
            if inactivo:
                continue
            reason = (c.get('Reason') or '').strip()
            fecha_cita = (c.get('Date') or d_iso)[:10]
            agg = por_rut.setdefault(rut, {
                'pc': None, 'tiene_futura': False, 'avance_post_pc': [],
            })
            # Primera consulta atendida en el pasado -> candidato. Guardamos la
            # MÁS RECIENTE (si vino a evaluarse dos veces, la última manda).
            if es_pasado and es_primera_consulta(reason) and 'atendid' in estado:
                if not agg['pc'] or fecha_cita > agg['pc']['fecha']:
                    agg['pc'] = {
                        'fecha': fecha_cita,
                        'id_agenda': str(c.get('IdAgenda') or ''),
                        'nombre': (c.get('PatientName') or '').strip(),
                        'doctor': (c.get('ProfessionalName') or '').strip(),
                        'telefono': _telefono_de_cita(c, rut),
                    }
            # Hora futura activa (cualquier motivo): ya está enganchado.
            if fecha_cita > hoy_iso:
                agg['tiene_futura'] = True
            # Avance: estudio / segunda consulta / montaje / control / retiro.
            if es_avance(reason, cfg):
                agg['avance_post_pc'].append(fecha_cita)

    # 2) Volcar a 'candidatos', respetando el estado de toques ya guardado.
    candidatos = reg.setdefault('candidatos', {})
    vistos = reg.setdefault('vistos', {})
    for rut, agg in por_rut.items():
        pc = agg['pc']
        if not pc:
            continue
        # ¿Avanzó? Tiene hora futura, o hizo algún paso de avance en o después de
        # la primera consulta (no cuenta un avance ANTERIOR a esta PC: sería de
        # otro tratamiento ya cerrado).
        avanzo = agg['tiene_futura'] or any(f >= pc['fecha'] for f in agg['avance_post_pc'])

        existente = candidatos.get(rut)
        if existente:
            # Datos de contacto siempre al día.
            existente['nombre'] = pc['nombre'] or existente.get('nombre', '')
            existente['telefono'] = pc['telefono'] or existente.get('telefono', '')
            existente['doctor'] = pc['doctor'] or existente.get('doctor', '')
            existente['fecha_pc'] = pc['fecha'] or existente.get('fecha_pc', '')
            if avanzo and existente.get('estado') == 'pendiente':
                existente['estado'] = 'convertido'
            # Un 'completado' o 'convertido' NO se reactiva porque la PC siga en
            # la ventana; solo se resucita si aparece una PC MÁS NUEVA (otra
            # evaluación posterior a la conversión anterior).
            elif existente.get('estado') in ('completado', 'convertido') and not avanzo \
                    and pc['fecha'] > (existente.get('fecha_pc_procesada') or ''):
                existente['estado'] = 'pendiente'
                existente['proximo_toque'] = 1
                existente['proxima_fecha'] = _sumar_dias(pc['fecha'], cfg.get('dias_toque_1', 7))
                existente['toques'] = []
            existente['fecha_pc_procesada'] = pc['fecha']
            continue

        # Nuevo candidato.
        estado = 'convertido' if avanzo else 'pendiente'
        candidatos[rut] = {
            'rut': rut,
            'nombre': pc['nombre'],
            'telefono': pc['telefono'],
            'doctor': pc['doctor'],
            'id_agenda_pc': pc['id_agenda'],
            'fecha_pc': pc['fecha'],
            'fecha_pc_procesada': pc['fecha'],
            'estado': estado,
            'proximo_toque': 1,
            'proxima_fecha': _sumar_dias(pc['fecha'], cfg.get('dias_toque_1', 7)),
            'toques': [],
            'creado': fechas.ahora_chile().isoformat(timespec='seconds'),
        }
        if pc['id_agenda']:
            vistos[pc['id_agenda']] = hoy_iso

    return reg


def _sumar_dias(fecha_iso, n):
    try:
        return (date.fromisoformat(fecha_iso[:10]) + timedelta(days=n)).isoformat()
    except (TypeError, ValueError):
        return fecha_iso


def barrer(cfg=None, max_workers=6):
    """Barrido diario: recorre getAgendaDay de -dias_atras a +dias_adelante (solo
    días hábiles) y actualiza los candidatos. Los días pasados aportan la primera
    consulta y el avance; los futuros, si ya tiene hora agendada. Molde exacto de
    control_dental.barrer()."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg or load_config()
    scfg = _scheduling_cfg()
    hoy = fechas.hoy_chile()
    dias_atras = cfg.get('dias_atras', 40)
    dias_adelante = cfg.get('dias_adelante', 30)

    dias = [hoy + timedelta(days=k)
            for k in range(-dias_atras, dias_adelante + 1)
            if (hoy + timedelta(days=k)).weekday() < 5]

    def scan(d):
        try:
            return (d, dentidesk._get_agenda_day(scfg, d))
        except Exception:
            return (d, [])

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        resultados = list(pool.map(scan, dias))
    resultados.sort(key=lambda r: r[0])

    with _LOCK:
        reg = _load_registro()
        _aplicar_barrido(reg, cfg, resultados, hoy)
        # Poda de 'vistos' viejos (mismo criterio que control_dental).
        limite = (hoy - timedelta(days=90)).isoformat()
        reg['vistos'] = {k: v for k, v in reg.get('vistos', {}).items() if v >= limite}
        _save_registro(reg)
    n_pend = sum(1 for c in reg.get('candidatos', {}).values() if c.get('estado') == 'pendiente')
    return {'dias_procesados': len(dias), 'candidatos': len(reg.get('candidatos', {})),
            'pendientes': n_pend, 'hoy': hoy.isoformat()}


def _scheduling_cfg():
    """dentidesk._get_agenda_day() necesita el config de scheduling (credenciales
    DentiDesk) -- import perezoso para evitar ciclos (patrón de control_dental)."""
    import scheduling
    return scheduling.load_config()


# ── Consulta para el reporte diario ─────────────────────────────────────────

def pendientes(fecha=None, doctor=None, cfg=None):
    """Candidatos a los que les toca un toque en/antes de 'fecha' (proxima_fecha
    <= fecha; default hoy), opcionalmente filtrados por 'doctor' (subcadena del
    profesional de la primera consulta). Excluye 'no molestar'. Ordena por la
    primera consulta MÁS ANTIGUA primero y corta en max_por_reporte. Devuelve
    cada uno con el mensaje ya armado (nombre reemplazado) y el número wa listo.

    Lo consume el runbook revision-evoluciones para la sección
    'Primeras consultas para reencantar' del correo del Dr. Alberto."""
    cfg = cfg or load_config()
    hoy_iso = (fecha.isoformat() if hasattr(fecha, 'isoformat') else str(fecha)[:10]) \
        if fecha else fechas.hoy_chile().isoformat()
    doc_norm = _normalizar(doctor) if doctor else ''
    reg = _load_registro()
    no_molestar = set(reg.get('no_molestar') or [])
    # El paciente con destino fijado por el doctor se filtra ACÁ y no solo por su
    # estado de candidato: el barrido vuelve a pasar cada día y podría recrearlo como
    # 'pendiente'. Con la guarda puesta en el estado nada más, el paciente terminaría
    # recibiendo la invitación a retomar una evaluación que ya está resuelta — le
    # escribiríamos al que avisó que no inicia, al que tiene control programado a 6
    # meses y al que ya anda con un aparato puesto.
    resueltos = set(destinos_manuales())

    out = []
    for rut, c in (reg.get('candidatos') or {}).items():
        if c.get('estado') != 'pendiente':
            continue
        if rut in no_molestar or rut in resueltos:
            continue
        if c.get('proxima_fecha', '') > hoy_iso:
            continue
        if doc_norm and doc_norm not in _normalizar(c.get('doctor', '')):
            continue
        toque = c.get('proximo_toque', 1)
        plantilla = cfg.get('mensaje_toque_1' if toque == 1 else 'mensaje_toque_2', '')
        nombre = c.get('nombre', '') or ''
        # Nombre de pila para el saludo (el registro guarda nombre completo).
        primer_nombre = nombre.split()[0] if nombre else ''
        try:
            f_pc = date.fromisoformat((c.get('fecha_pc') or '')[:10])
            f_pc_leg = fecha_legible(f_pc)
        except (TypeError, ValueError):
            f_pc_leg = c.get('fecha_pc', '')
        out.append({
            'rut': rut,
            'nombre': nombre,
            'telefono': c.get('telefono', ''),
            'wa_numero': normalizar_wa(c.get('telefono', '')),
            'toque': toque,
            'fecha_pc': c.get('fecha_pc', ''),
            'fecha_pc_legible': f_pc_leg,
            'mensaje': plantilla.format(nombre=primer_nombre or 'hola'),
        })
    out.sort(key=lambda x: x.get('fecha_pc', ''))
    tope = cfg.get('max_por_reporte', 15)
    return out[:tope]


def marcar_mostrados(ruts, cfg=None):
    """Avanza el toque de cada RUT que el reporte YA mostró (lo llama el runbook
    tras enviar el correo con éxito). Toque 1 -> programa el 2 en
    max(fecha_pc + dias_toque_2, hoy + min_gap). Toque 2 -> 'completado'.
    Idempotente dentro del mismo día por la fecha de 'mostrado'. Devuelve cuántos
    avanzó."""
    cfg = cfg or load_config()
    hoy = fechas.hoy_chile()
    hoy_iso = hoy.isoformat()
    avanzados = 0
    with _LOCK:
        reg = _load_registro()
        candidatos = reg.get('candidatos', {})
        for rut in (ruts or []):
            clave = _rut_key(rut)
            c = candidatos.get(clave) or candidatos.get(rut)
            if not c or c.get('estado') != 'pendiente':
                continue
            # Anti-doble en el mismo día: si ya se marcó hoy, no re-avanzar (el
            # reporte solo se manda una vez al día, pero curarse en salud).
            toques = c.setdefault('toques', [])
            if toques and toques[-1].get('fecha') == hoy_iso:
                continue
            toque = c.get('proximo_toque', 1)
            toques.append({'n': toque, 'fecha': hoy_iso})
            if toque >= 2:
                c['estado'] = 'completado'
                c['proximo_toque'] = 3
            else:
                c['proximo_toque'] = 2
                base = _sumar_dias(c.get('fecha_pc', hoy_iso), cfg.get('dias_toque_2', 30))
                minimo = (hoy + timedelta(days=cfg.get('min_gap_dias', 14))).isoformat()
                c['proxima_fecha'] = max(base, minimo)
            avanzados += 1
        _save_registro(reg)
    return avanzados


def resumen():
    """Conteos para el panel/diagnóstico."""
    reg = _load_registro()
    cand = reg.get('candidatos', {})
    def n(estado):
        return sum(1 for c in cand.values() if c.get('estado') == estado)
    return {
        'total': len(cand),
        'pendientes': n('pendiente'),
        'convertidos': n('convertido'),
        'completados': n('completado'),
        'no_molestar': len(reg.get('no_molestar') or []),
    }


def listar(estado=None):
    """Candidatos, opcionalmente filtrados por estado. Para panel/diagnóstico."""
    reg = _load_registro()
    items = [dict(c) for c in (reg.get('candidatos') or {}).values()]
    if estado:
        items = [i for i in items if i.get('estado') == estado]
    items.sort(key=lambda i: i.get('fecha_pc', ''), reverse=True)
    return items
