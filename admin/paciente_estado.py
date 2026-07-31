"""
paciente_estado.py - En que estado clinico esta un paciente (nuevo,
primera_consulta, fijo, alineadores, removible, pasivo, desconocido), para que
el agendamiento online le ofrezca SOLO los motivos que le corresponden (menu
filtrado) en vez del menu completo de siempre. El caso real: un paciente con
aparatos fijos hoy puede agendar "Primera Consulta" o "Estudio Integral" desde
el sitio -- motivos que ya no le sirven, y que confunden mas de lo que ayudan.

DE DONDE SALE EL ESTADO
-----------------------
Un barrido diario de la agenda (barrer(), molde de control_dental.barrer())
recorre los dias PASADOS y HABILES y clasifica el ultimo motivo atendido de
cada paciente. Tambien se actualiza al vuelo cuando el paciente reserva por
el sitio (registrar_reserva_online) o cuando la asistente fuerza un estado a
mano desde el F2/panel (set_manual). clasificar() -- lo que consulta el
agendamiento online para armar el menu -- es 100% local (lee el registro
propio), nunca llama a DentiDesk, igual que control_dental.evaluar().

CONTINGENCIA 3 -- LA DIRECCION DEL IMPORT
------------------------------------------
Este modulo PUEDE importar control_dental (reutiliza sus constantes de
clasificacion de motivos -- _INICIO_FIJOS, _INICIO_ALINEADORES,
_FIN_DEFINITIVO, _ESTADOS_NO_OCURRIO -- y _normalizar/sumar_meses; no tiene
sentido tener una segunda copia de la misma tabla IdReason que puede
divergir). control_dental JAMAS puede importar este modulo. Si scheduling.py
llega a necesitar clasificar() para armar el menu de agendamiento (el caso
real: scheduling_config.json ya documenta 'control_pasivo'/'control_evolucion'
como 'solo_filtrado', pensados para este modulo), ese import tiene que ser
perezoso (dentro de la funcion) -- este modulo ya importa 'scheduling' de
forma perezosa en _scheduling_cfg(), asi ningun ciclo se materializa al
cargar los modulos.

POR QUE UN RUT AUSENTE DEL STORE DEVUELVE MENU COMPLETO
--------------------------------------------------------
Un RUT que el barrido nunca vio puede ser un paciente de verdad nuevo, o uno
que el barrido aun no alcanzo a procesar (el store recien se esta poblando).
No hay forma de distinguir ambos casos, y esconderle motivos a alguien de
quien no sabemos nada seria peor que mostrarle de mas -- por eso clasificar()
de un RUT ausente devuelve estado='nuevo' con motivos_permitidos=None (nunca
se filtra por falta de datos, solo cuando SI sabemos algo del paciente).

CONTINGENCIA 10 -- KILL-SWITCH SIN DEPLOY
------------------------------------------
La env var MENU_FILTRADO=off (case-insensitive) apaga el filtrado por
completo: clasificar() siempre devuelve motivos_permitidos=None (menu
completo), sin tocar el 'estado'/'fuente' calculados. Sirve para apagar el
filtrado desde el panel de Render sin esperar un deploy si la tabla de
clasificacion tuviera un error que le esconde a alguien un motivo valido.

CONTINGENCIA 6 -- motivos no reconocidos
------------------------------------------
El procesador del barrido acumula en 'motivos_desconocidos' (tope 200
entradas, SIN rut asociado -- este repo es PUBLICO) los Reason que no
calzaron con ninguna categoria. La correccion se hace sin deploy agregando
una entrada a cfg['estado_motivos_extra'] (vive en scheduling_config.json,
NO se le agregan claves nuevas -- se lee con .get() y default), que
estado_por_motivo() consulta ANTES que las constantes del modulo.
"""

import os
import threading
from pathlib import Path
from datetime import date, timedelta

import dentidesk
import fechas          # hoy_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore       # guardado atomico con lock. Ver jsonstore.py.
import avisos          # rut_key compartido con control_dental/recaptacion/nps.
import control_dental  # SOLO lectura: constantes de motivos, _normalizar, sumar_meses,
                        # _ESTADOS_NO_OCURRIO. Nunca al reves (ver CONTINGENCIA 3 arriba).

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
ESTADO_PATH = Path(os.environ.get('PACIENTE_ESTADO_PATH', _BASE_DIR / 'paciente_estado.json'))

_LOCK = threading.Lock()

# Tope de motivos_desconocidos (contingencia 6): el repo es PUBLICO, asi que
# esta lista NUNCA guarda el RUT del paciente, solo el texto del motivo y un
# contador -- y se corta en 200 entradas NUEVAS para que el JSON no crezca sin
# techo (un motivo ya presente puede seguir sumando su contador igual).
_MAX_MOTIVOS_DESCONOCIDOS = 200

_normalizar = control_dental._normalizar


# ── Clasificacion de motivos (Reason -> estado clinico) ─────────────────────
#
# El match es POR NOMBRE normalizado: getAgendaDay solo devuelve el texto
# 'Reason', nunca el IdReason numerico (mismo hecho documentado en
# control_dental.py). Los IdReason de abajo son solo trazabilidad hacia
# scheduling_config.json -> motivos_id_reason_extra, no se usan en el codigo.
#
# Fijo y alineadores reutilizan las constantes de INICIO de control_dental
# (mismos IdReason -- una instalacion es una instalacion) y SUMAN los
# controles periodicos, que alli solo cuentan como señal de vida pero aca SI
# clasifican estado (si el paciente sigue yendo a control de fijo, sigue en
# fijo). Los refinamientos de alineadores (que control_dental excluye a
# proposito de sus INICIOS, porque no son una instalacion nueva) SI cuentan
# aca: confirman que el paciente continua con alineadores.

_FIJO_CONTROL = {
    'control fijo': 16073,
    'control lingual total': 20121,
    'control lingual parcial': 20120,
    'control disyuntor': 33821,
    'control barra / nance': 33779,
    'control microtornillos': 20173,
    'control digitrack + fijo': 27675,
}

_ALINEADORES_CONTROL = {
    'control digitrack': 20071,
    'control invisalign': 20070,
    'control clear correct': 27649,
    'control digitrack + lingual': 27676,
    'control invisalign + fijo': 20188,
    # Refinamientos a mitad de tratamiento -- no instalan, pero confirman
    # continuidad con alineadores (a diferencia de control_dental, donde no
    # importa: alli solo interesa CUANDO arranca el tratamiento).
    'instalar refinamiento digitrack': 25091,
    'instalar refinamiento invisalign': 25092,
    'instalar refinamiento clear correct': 27672,
    'impresion p / refinamiento invisalign': 28495,
    'impresion p / refinamiento clear correct': 31965,
    'scanner refinamiento digitrack': 24412,
}

_REMOVIBLE = {
    'control removible': 20013,
    'control plano relajacion': 20115,
    'placa': 18166,
    'plano relajacion': 1655,
    'silensor': 20103,
    'instalar twin block': 26611,
    'quad helix': 20046,
    'nance': 20051,
    'barra lingual': 20053,
    'barra palatina / hg': 18170,
    'aligner / essix': 20187,
}

# Pasivo reutiliza el FIN_DEFINITIVO de control_dental (fin de tratamiento
# activo -> el paciente pasa a contencion) y suma 'Control Pasivo', que alli
# es solo señal de vida (_CONTROL) pero aca es justo el estado 'pasivo'.
_PASIVO_EXTRA = {
    'control pasivo': 18162,
}

_PRIMERA_CONSULTA = {
    'primera consulta': 18152,
    'segunda consulta': 18165,
    'inicio': 23935,  # los registros del Estudio Integral (motivo oculto 'estudio_registros')
    'explicacion plan tratamiento': 18167,
    'explicacion plan tratamiento online': 34354,
    'explicacion plan + disyuntor': 36043,
    'explicacion plan + separaciones': 36295,
    'toma rxs': 23934,
    # OJO: 'Explicación Plan + Instalación Digitrack' (35053) NO va aca -- ya
    # es una instalacion de alineadores (ver control_dental._INICIO_ALINEADORES,
    # que se reutiliza en estado_por_motivo() mas abajo).
}


def estado_por_motivo(reason, cfg=None, extra=None):
    """Devuelve 'fijo'|'alineadores'|'removible'|'pasivo'|'primera_consulta'|
    None segun el Reason (tal como lo devuelve getAgendaDay). Urgencias y
    cualquier motivo que no calce con nada devuelven None -- no cambian el
    estado del paciente.

    Precedencia de los overrides, de mayor a menor:
      1. 'extra' -- lo que la clinica clasifico desde el panel. Vive en el
         propio store (disco persistente), NO en scheduling_config.json: ese
         archivo esta versionado y Render lo reescribe con cada deploy, asi
         que lo que guardara el panel ahi se perderia al siguiente push.
         Si no se pasa, se lee del store.
      2. cfg['estado_motivos_extra'] -- overrides puestos a mano en la config
         versionada (siguen funcionando, para poder fijar un mapeo "de
         fabrica" en el repo).
      3. Las constantes del modulo.
    Valor vacio en un override = None (permite "apagar" un motivo que quedo
    mal clasificado, sin borrar la entrada)."""
    clave = _normalizar(reason)
    if not clave:
        return None

    if extra is None:
        extra = _load_estado().get('motivos_extra') or {}
    if clave in extra:
        return extra[clave] or None

    cfg = cfg or {}
    extra_cfg = cfg.get('estado_motivos_extra') or {}
    if clave in extra_cfg:
        return extra_cfg[clave] or None

    if clave in control_dental._INICIO_FIJOS or clave in _FIJO_CONTROL:
        return 'fijo'
    if clave in control_dental._INICIO_ALINEADORES or clave in _ALINEADORES_CONTROL:
        return 'alineadores'
    if clave in _REMOVIBLE:
        return 'removible'
    if clave in control_dental._FIN_DEFINITIVO or clave in _PASIVO_EXTRA:
        return 'pasivo'
    if clave in _PRIMERA_CONSULTA:
        return 'primera_consulta'
    return None


# ── Registro ─────────────────────────────────────────────────────────────

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(
    ESTADO_PATH, indent=2,
    default={'ultimo_barrido': '', 'pacientes': {}, 'motivos_desconocidos': {}, 'motivos_extra': {}},
    claves={'ultimo_barrido': '', 'pacientes': {}, 'motivos_desconocidos': {}, 'motivos_extra': {}})


def _load_estado():
    return _STORE.load()


def _save_estado(reg):
    _STORE.save(reg)


def _scheduling_cfg():
    """scheduling.load_config() es el 'cfg' que usan estado_por_motivo() y
    clasificar(): ahi viven 'motivos' (para no ofrecer un motivo que no esta
    configurado), 'estado_motivos_extra' y 'meses_vigencia_estado' (leidas con
    .get() y default -- NO se agregan claves nuevas a scheduling_config.json).
    Import perezoso: evita el ciclo si scheduling.py llega a importar este
    modulo (ver CONTINGENCIA 3 en el docstring del modulo)."""
    import scheduling
    return scheduling.load_config()


# ── El estado por RUT: consulta y overrides ─────────────────────────────────

def get(rut):
    """El registro crudo del paciente ({'estado','fuente','ultima_cita',
    'ultimo_motivo','actualizado','bloqueo_manual'}), o None si el barrido
    nunca lo vio."""
    clave = avisos.rut_key(rut)
    return (_load_estado().get('pacientes') or {}).get(clave)


def motivos_desconocidos():
    """Los motivos de DentiDesk que el barrido vio y no supo clasificar, con
    cuantas veces aparecieron y cuantos pacientes quedaron 'colgados' de cada
    uno (los que ese motivo dejo en 'desconocido' = viendo el menu completo).
    Es la lista de trabajo del panel: se ordena por impacto real, no por
    frecuencia, porque un motivo muy repetido puede no dejar a nadie colgado
    si esos pacientes tienen otra cita mas nueva que si clasifico."""
    reg = _load_estado()
    desc = reg.get('motivos_desconocidos') or {}
    colgados = {}
    for p in (reg.get('pacientes') or {}).values():
        if (p or {}).get('estado') == 'desconocido':
            m = (p or {}).get('ultimo_motivo') or ''
            if m:
                colgados[m] = colgados.get(m, 0) + 1
    out = []
    for reason, info in desc.items():
        info = info if isinstance(info, dict) else {'n': info, 'ultima': ''}
        out.append({
            'reason': reason,
            'veces': info.get('n', 0),
            'ultima': info.get('ultima', ''),
            'pacientes_colgados': colgados.get(reason, 0),
        })
    out.sort(key=lambda d: (-d['pacientes_colgados'], -d['veces']))
    return out


def clasificar_motivo(reason, categoria):
    """El panel resuelve un motivo: lo guarda en el store (disco persistente,
    sobrevive a los deploys), lo saca de la lista de pendientes y RECLASIFICA
    al tiro a los pacientes cuya ultima cita fue justamente ese motivo.

    Ese reproceso es la parte que importa: sin el, clasificar un motivo no
    haria nada visible hasta que esos pacientes volvieran a tener una cita
    (el barrido solo pisa con citas MAS NUEVAS), o sea meses. Solo se tocan
    los que estan en 'desconocido' y sin correccion manual -- lo que la
    clinica ajusto a mano nunca se pisa.

    categoria='' borra el override (el motivo vuelve a no clasificar).
    Devuelve cuantos pacientes se reclasificaron."""
    if categoria and categoria not in _ESTADOS_VALIDOS:
        raise ValueError(f'estado desconocido: {categoria!r}')
    clave = _normalizar(reason)
    if not clave:
        raise ValueError('motivo vacio')
    with _LOCK:
        reg = _load_estado()
        extra = dict(reg.get('motivos_extra') or {})
        if categoria:
            extra[clave] = categoria
        else:
            extra.pop(clave, None)
        reg['motivos_extra'] = extra
        (reg.get('motivos_desconocidos') or {}).pop(reason, None)

        reclasificados = 0
        if categoria:
            for p in (reg.get('pacientes') or {}).values():
                if not p or p.get('bloqueo_manual') or p.get('estado') != 'desconocido':
                    continue
                if _normalizar(p.get('ultimo_motivo') or '') == clave:
                    p['estado'] = categoria
                    p['fuente'] = 'barrido'
                    reclasificados += 1
        _save_estado(reg)
        return reclasificados


def resumen():
    """Conteos agregados de la cartera, para saber si el barrido de verdad
    poblo la base y como quedo repartida. SIN datos personales: solo numeros
    por estado + los motivos que no se supieron clasificar (nombres de motivo
    de DentiDesk, nunca RUT). Es lo que mira una persona despues del backfill
    para decidir si enciende o no el menu filtrado."""
    reg = _load_estado()
    pacientes_reg = reg.get('pacientes') or {}
    por_estado = {}
    manuales = 0
    for p in pacientes_reg.values():
        est = (p or {}).get('estado') or 'desconocido'
        por_estado[est] = por_estado.get(est, 0) + 1
        if (p or {}).get('bloqueo_manual'):
            manuales += 1
    return {
        'total': len(pacientes_reg),
        'por_estado': dict(sorted(por_estado.items(), key=lambda kv: -kv[1])),
        'con_correccion_manual': manuales,
        'ultimo_barrido': reg.get('ultimo_barrido') or '',
        'motivos_desconocidos': reg.get('motivos_desconocidos') or {},
    }


# Menu de motivos agendables por estado. None = menu completo (no se filtra).
# 'urgencia' siempre esta disponible: un paciente en cualquier estado puede
# tener una urgencia. 'estudio_integral' y 'control_evolucion' son
# 'solo_filtrado' en scheduling_config.json -- solo aparecen para quien
# clasificar() se los ofrece (o por un link precargado del F2).
MENU_POR_ESTADO = {
    'nuevo':            ['primera_consulta', 'urgencia'],
    'primera_consulta': ['estudio_integral', 'control_evolucion', 'urgencia'],
    'fijo':             ['control_fijo', 'urgencia'],
    'alineadores':      ['control_alineadores', 'urgencia'],
    'removible':        ['control_removible', 'urgencia'],
    'pasivo':           ['control_pasivo', 'urgencia'],
    'desconocido':      None,   # None = menu completo
}

_ESTADOS_VALIDOS = frozenset(MENU_POR_ESTADO)


def clasificar(rut, cfg=None):
    """Devuelve {'estado', 'motivos_permitidos', 'fuente'} para armar el menu
    de agendamiento online de este paciente. 100% local (no llama a
    DentiDesk): lee el registro del barrido/reserva/override manual. Si no se
    pasa 'cfg', se carga scheduling.load_config() (ahi viven 'motivos',
    'estado_motivos_extra' y 'meses_vigencia_estado').

    - RUT ausente del store -> estado='nuevo', motivos_permitidos=None (menu
      completo). Ver la nota grande en el docstring del modulo: nunca se
      esconden motivos por falta de datos.
    - Vigencia: si 'ultima_cita' es mas vieja que cfg['meses_vigencia_estado']
      (default 14) meses, el estado se degrada a 'desconocido' (menu
      completo) -- un paciente que no se ve hace mas de un año no deberia
      seguir "atrapado" en el menu reducido de un estado que quiza ya no es
      el suyo.
    - El menu se filtra contra cfg['motivos'] (los que existen de verdad en
      scheduling_config.json), para no ofrecer un motivo sin configurar.
    - CONTINGENCIA 8: si el menu incluiria 'estudio_integral' pero el RUT no
      esta en la base local de pacientes (patient_index.json), se saca --
      el endpoint real (solo_pacientes_existentes) lo rechazaria con 403
      despues, asi que ofrecerlo seria prometer algo que se le va a negar.
      Tolerante a fallos: si pacientes.lookup revienta, no bloquea el resto.
    - CONTINGENCIA 10: la env var MENU_FILTRADO=off fuerza SIEMPRE
      motivos_permitidos=None, sin tocar el estado calculado -- kill-switch
      sin deploy."""
    cfg = cfg if cfg is not None else _scheduling_cfg()
    clave = avisos.rut_key(rut)
    reg = _load_estado()
    p = (reg.get('pacientes') or {}).get(clave)

    if not p:
        return {'estado': 'nuevo', 'motivos_permitidos': None, 'fuente': ''}

    estado = p.get('estado') or 'desconocido'
    fuente = p.get('fuente', '')

    if estado not in ('nuevo', 'desconocido'):
        meses_vigencia = cfg.get('meses_vigencia_estado', 14)
        ultima = p.get('ultima_cita') or ''
        try:
            f_ultima = date.fromisoformat(ultima[:10])
            limite = control_dental.sumar_meses(f_ultima, meses_vigencia)
            if fechas.hoy_chile() > limite:
                estado = 'desconocido'
        except (ValueError, TypeError):
            pass  # sin ultima_cita valida no se puede evaluar vigencia -- se deja como esta

    menu = MENU_POR_ESTADO.get(estado)
    if menu is not None:
        motivos_cfg = cfg.get('motivos') or {}
        menu = [m for m in menu if m in motivos_cfg]

        if 'estudio_integral' in menu:
            try:
                import pacientes  # import perezoso: evita ciclo y solo hace falta aca
                if not pacientes.lookup(rut):
                    menu = [m for m in menu if m != 'estudio_integral']
            except Exception:
                pass  # tolerante a fallos: no bloquea el resto del menu

    if (os.environ.get('MENU_FILTRADO') or '').strip().lower() == 'off':
        menu = None

    return {'estado': estado, 'motivos_permitidos': menu, 'fuente': fuente}


def _aplicar_estado(reg, clave, fecha_cita, reason, categoria):
    """Logica compartida SIN lock (barrer/backfill ya sostienen el lock
    alrededor de todo el barrido; registrar_cita_atendida lo toma el solo).
    Solo pisa si fecha_cita es MAS NUEVA que la ultima_cita guardada -- una
    cita mas vieja que la ya registrada no pisa nada (misma fecha SI
    actualiza, para no perder una segunda cita del mismo dia). A proposito NO
    se chequea bloqueo_manual aca: un override manual pierde ante una cita
    real posterior, la realidad manda (ver registrar_cita_atendida). Si
    'categoria' es None, solo actualiza ultima_cita/ultimo_motivo (señal de
    vida), sin tocar el estado."""
    pacientes_reg = reg.setdefault('pacientes', {})
    p = pacientes_reg.get(clave)
    anterior = (p or {}).get('ultima_cita') or ''
    if p and anterior > fecha_cita:
        return p

    nuevo = dict(p or {})
    nuevo['ultima_cita'] = fecha_cita
    nuevo['ultimo_motivo'] = reason or ''
    nuevo['actualizado'] = fechas.hoy_chile().isoformat()
    if categoria:
        nuevo['estado'] = categoria
        nuevo['fuente'] = 'barrido'
    else:
        nuevo.setdefault('estado', 'desconocido')
        nuevo.setdefault('fuente', 'barrido')
    nuevo.setdefault('bloqueo_manual', False)
    pacientes_reg[clave] = nuevo
    return nuevo


def registrar_cita_atendida(rut, fecha_iso, reason, cfg=None):
    """Lo llama el barrido (o quien quiera anotar una cita puntual) por cada
    cita PASADA que de verdad ocurrio. Solo pisa el estado si fecha_iso es MAS
    NUEVA que la ultima_cita guardada -- un bloqueo_manual pierde ante esto,
    porque una cita real posterior es la realidad, no una suposicion."""
    cfg = cfg if cfg is not None else _scheduling_cfg()
    clave = avisos.rut_key(rut)
    categoria = estado_por_motivo(reason, cfg)
    with _LOCK:
        reg = _load_estado()
        resultado = _aplicar_estado(reg, clave, fecha_iso, reason, categoria)
        _save_estado(reg)
        return resultado


# Mapeo motivo_key (de scheduling, lo que el paciente elige online) -> estado.
# 'primera_consulta' y 'urgencia' NO cambian el estado -- no aportan
# informacion clinica nueva (una urgencia no dice en que fase esta el
# tratamiento; primera_consulta ya es el estado inicial de todos modos).
_MOTIVO_KEY_A_ESTADO = {
    'control_fijo': 'fijo',
    'control_alineadores': 'alineadores',
    'control_removible': 'removible',
    'control_pasivo': 'pasivo',
    'control_evolucion': 'primera_consulta',
}


def registrar_reserva_online(rut, motivo_key, cfg=None):
    """Cuando el paciente agenda por el sitio, el motivo_key elegido (no el
    Reason de DentiDesk) ya nos dice a que estado corresponde -- se anticipa
    sin esperar el barrido de mañana. Nunca pisa un override manual
    (bloqueo_manual=True): la asistente ya toco a este paciente a mano.

    'cfg' se acepta por firma uniforme con el resto del modulo (hoy el mapeo
    motivo_key->estado es fijo y no lo necesita; queda listo por si el
    mapeo se vuelve configurable a futuro)."""
    nuevo_estado = _MOTIVO_KEY_A_ESTADO.get(motivo_key)
    if not nuevo_estado:
        return get(rut)

    clave = avisos.rut_key(rut)
    with _LOCK:
        reg = _load_estado()
        pacientes_reg = reg.setdefault('pacientes', {})
        p = pacientes_reg.get(clave)
        if p and p.get('bloqueo_manual'):
            return p

        nuevo = dict(p or {})
        nuevo['estado'] = nuevo_estado
        nuevo['fuente'] = 'reserva_online'
        nuevo['actualizado'] = fechas.hoy_chile().isoformat()
        nuevo.setdefault('ultima_cita', nuevo.get('ultima_cita', ''))
        nuevo.setdefault('ultimo_motivo', nuevo.get('ultimo_motivo', ''))
        nuevo.setdefault('bloqueo_manual', False)
        pacientes_reg[clave] = nuevo
        _save_estado(reg)
        return nuevo


def set_manual(rut, estado):
    """Override del F2/panel: marca bloqueo_manual=True y fuente='manual'.
    estado='' (o None) LIMPIA el override -- vuelve a que el barrido/reserva
    online decidan (bloqueo_manual=False), SIN borrar el historico
    (ultima_cita/ultimo_motivo) del paciente: son datos reales de la agenda,
    no algo que la asistente haya inventado."""
    clave = avisos.rut_key(rut)
    with _LOCK:
        reg = _load_estado()
        pacientes_reg = reg.setdefault('pacientes', {})
        p = dict(pacientes_reg.get(clave) or {})
        if not estado:
            p['bloqueo_manual'] = False
            # Bajar el flag no basta: el estado que escribio la asistente sigue
            # ahi, y el barrido solo pisa cuando aparece una cita MAS NUEVA --
            # un paciente sin citas nuevas quedaria con el override "quitado"
            # pero aplicandose igual. Se recalcula al tiro desde su ultima cita
            # real; si ese motivo no clasifica, queda 'desconocido' (menu
            # completo) hasta que el barrido lo vea de nuevo.
            recalculado = estado_por_motivo(p.get('ultimo_motivo') or '')
            p['estado'] = recalculado or 'desconocido'
            p['fuente'] = 'barrido'
        else:
            if estado not in _ESTADOS_VALIDOS:
                raise ValueError(f'estado desconocido: {estado!r}')
            p['estado'] = estado
            p['fuente'] = 'manual'
            p['bloqueo_manual'] = True
        p.setdefault('ultima_cita', '')
        p.setdefault('ultimo_motivo', '')
        p['actualizado'] = fechas.hoy_chile().isoformat()
        pacientes_reg[clave] = p
        _save_estado(reg)
        return p


# ── El barrido: dias pasados y habiles resuelven el estado de la cartera ────

def _procesar_cita(reg, cfg, c):
    """Aplica UNA cita (ya filtrada: pasada, habil, activa -- no
    _ESTADOS_NO_OCURRIO) sobre 'reg', mutandolo in-place. Compartido entre
    barrer() y backfill(), igual que control_dental._procesar_citas_dia."""
    rut = dentidesk.limpiar_rut(str(c.get('PatientDocument', '')))
    if not rut:
        return
    reason = (c.get('Reason') or '').strip()
    fecha_cita = c.get('Date') or fechas.hoy_chile().isoformat()
    clave = avisos.rut_key(rut)

    # Los overrides salen del 'reg' que el barrido ya tiene en memoria: releer
    # el store por cada cita serian miles de lecturas de disco por barrido.
    categoria = estado_por_motivo(reason, cfg, extra=reg.get('motivos_extra') or {})
    _aplicar_estado(reg, clave, fecha_cita, reason, categoria)

    if reason and categoria is None:
        desc = reg.setdefault('motivos_desconocidos', {})
        # Tope de 200 (contingencia 6): un motivo YA presente puede seguir
        # actualizando su contador/fecha aunque se haya llegado al tope; lo
        # que no crece mas alla del tope son motivos NUEVOS.
        if reason in desc or len(desc) < _MAX_MOTIVOS_DESCONOCIDOS:
            info = desc.get(reason, {'n': 0, 'ultima': ''})
            info['n'] = info.get('n', 0) + 1
            info['ultima'] = fecha_cita
            desc[reason] = info


def _procesar_resultados(reg, cfg, resultados):
    """resultados: lista de (fecha, citas, ok). Descarta _ESTADOS_NO_OCURRIO
    (reutilizado de control_dental: en el pasado 'Atendido' es la señal que
    se busca, no un estado a ignorar -- ver la nota grande en
    control_dental.py sobre por que _ESTADOS_INACTIVOS de dentidesk NO sirve
    aca) y aplica cada cita. Devuelve cuantas citas se procesaron."""
    procesadas = 0
    for _fecha, citas, _ok in resultados:
        for c in citas:
            estado_cita = (c.get('Status') or '').lower()
            if any(s in estado_cita for s in control_dental._ESTADOS_NO_OCURRIO):
                continue
            _procesar_cita(reg, cfg, c)
            procesadas += 1
    return procesadas


def barrer(cfg=None, dias_atras=7, max_workers=6):
    """Recorre getAgendaDay desde ayer hacia atras 'dias_atras' dias HABILES
    (L-V) y resuelve el estado de toda la cartera. NO incluye hoy: una cita de
    hoy puede no haber pasado todavia, asi que se deja para el barrido de
    mañana (que la vuelve a mirar dentro de la misma ventana rodante de
    'dias_atras') -- mismo criterio que control_dental usa para separar
    dias pasados de dias futuros. Los -7 dias (en vez de solo ayer) hacen el
    barrido idempotente y auto-reparable si Render se reinicia a medio
    proceso -- reprocesar una cita ya vista no cambia nada (la comparacion de
    fechas en _aplicar_estado es el mecanismo de idempotencia, no hace falta
    un set de 'vistos' aparte).

    CONTINGENCIA 5: cada dia se escanea en su propio try/except (dentro de
    'scan'); un dia que revienta queda en 'dias_fallidos' del resultado y NO
    frena a los demas. Re-correr barrer() es la recuperacion (idempotente)."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg if cfg is not None else _scheduling_cfg()
    scfg = _scheduling_cfg()
    hoy = fechas.hoy_chile()

    dias = [hoy - timedelta(days=k) for k in range(1, dias_atras + 1)
            if (hoy - timedelta(days=k)).weekday() < 5]

    def scan(d):
        try:
            return (d, dentidesk._get_agenda_day(scfg, d), True)
        except Exception:
            return (d, [], False)

    resultados = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for r in pool.map(scan, dias):
            resultados.append(r)
    resultados.sort(key=lambda r: r[0])
    dias_fallidos = [d.isoformat() for d, _citas, ok in resultados if not ok]

    with _LOCK:
        reg = _load_estado()
        procesadas = _procesar_resultados(reg, cfg, resultados)
        reg['ultimo_barrido'] = hoy.isoformat()
        _save_estado(reg)

    return {'dias_procesados': len(dias), 'dias_fallidos': dias_fallidos,
            'citas_procesadas': procesadas}


def backfill(cfg=None, meses=6, max_workers=6):
    """Barrido one-off hacia atras 'meses' meses (a diferencia de barrer(),
    que es el mantenimiento diario de -7 dias): sirve para poblar el store la
    primera vez, con la cartera ya en tratamiento. No incluye hoy (mismo
    criterio que barrer()) ni toca 'ultimo_barrido' (eso es del barrido
    diario). Mismas garantias de tolerancia a fallos (contingencia 5) e
    idempotencia que barrer()."""
    from concurrent.futures import ThreadPoolExecutor
    cfg = cfg if cfg is not None else _scheduling_cfg()
    scfg = _scheduling_cfg()
    hoy = fechas.hoy_chile()
    desde = control_dental.sumar_meses(hoy, -meses)
    dias = [d for d in (desde + timedelta(days=k) for k in range((hoy - desde).days))
            if d.weekday() < 5]

    def scan(d):
        try:
            return (d, dentidesk._get_agenda_day(scfg, d), True)
        except Exception:
            return (d, [], False)

    resultados = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for r in pool.map(scan, dias):
            resultados.append(r)
    resultados.sort(key=lambda r: r[0])
    dias_fallidos = [d.isoformat() for d, _citas, ok in resultados if not ok]

    with _LOCK:
        reg = _load_estado()
        procesadas = _procesar_resultados(reg, cfg, resultados)
        _save_estado(reg)

    return {'dias_procesados': len(dias), 'dias_fallidos': dias_fallidos,
            'citas_procesadas': procesadas}
