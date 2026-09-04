"""
fotos_finales.py - Aviso de "hay material para el collage" (Ortodoncia Richard).

QUE PROBLEMA RESUELVE
---------------------
Cuando el Dr. Alberto termina un tratamiento, el paciente vuelve unas semanas
despues -- ya con las encias sanas -- al control en que se le toman las fotos
post tratamiento. Ese es el momento en que existe el material para armar el
collage antes/despues, y hasta ahora nadie avisaba que habia llegado: dependia
de que el doctor se acordara caso a caso, asi que casos terminados quedaban sin
su collage.

Este modulo detecta ese control y deja al paciente listo para que server.py
mande UN correo agrupado al doctor sugiriendo armar el collage.

POR QUE SE DETECTA UN PATRON Y NO UN MOTIVO
-------------------------------------------
Lo primero que se intento fue buscar un motivo que dijera "fotos". No sirve:
medido sobre las 46.692 atenciones del historico (2021 - jul 2026), el Dr.
Alberto uso los motivos que nombran fotos 2 veces en cinco anios. Despues de un
retiro sus pacientes vuelven con motivos corrientes -- Control Contencion (69),
Control Removible (67), Impresion p/Essix (43), Aligner/Essix (37), Control
Digitrack (18).

Por eso el disparador es el PATRON: **la primera cita atendida despues de un
retiro de aparatos**. Ese mismo historico da los numeros con que estan
calibrados los defaults:

- mediana de 27 dias entre el retiro y esa cita (p25 19, p75 34) -> por eso
  `dias_minimos` 10 y `dias_maximos` 180.
- ~52 avisos al anio (~4,3 al mes) -> un correo diario agrupado casi siempre
  trae 0 o 1 paciente, no hace falta limitar mas.
- en 15 de 302 casos la primera cita posterior fue una urgencia (retenedor
  suelto/roto, essix perdido). Esas NO son el control de fotos -> `_URGENCIA`
  las salta y espera la siguiente.

CEREBRO SIN RED: config, registro en JSON y logica de decision. La unica lectura
de DentiDesk es el barrido de getAgendaDay (igual que control_dental.barrer y
seguimiento_pc.barrer), que corre en el scheduler de server.py -- no aca dentro.

Config + registro propios en el disco persistente de Render (misma base que
patient_index.json, via PATIENT_INDEX_PATH) para sobrevivir a los redeploys sin
pasar por git. Llevan RUT: van al .gitignore, este repo es PUBLICO.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, timedelta

import dentidesk
import control_dental   # _normalizar, _ESTADOS_NO_OCURRIO
import fechas           # hoy_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore        # guardado atomico con lock. Ver jsonstore.py.
import avisos           # rut_key compartido. Ver avisos.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('FOTOS_FINALES_CONFIG_PATH',
                                  _BASE_DIR / 'fotos_finales_config.json'))
REGISTRO_PATH = Path(os.environ.get('FOTOS_FINALES_REGISTRO_PATH',
                                    _BASE_DIR / 'fotos_finales_registro.json'))

_LOCK = threading.Lock()

# ── Motivos ─────────────────────────────────────────────────────────────────
#
# Lista PROPIA a proposito: NO se reusa control_dental._FIN_DEFINITIVO. Esa
# incluye 'control contencion', 'retenedor fijo' y 'retiro retenedores fijos',
# que aca son la cita de DESTINO (el control de las fotos) o no aplican. Si se
# reusara, el control post-retiro se leeria como un retiro nuevo y el aviso
# nunca saldria.
#
# Nombres TAL CUAL los devuelve getAgendaDay en el campo Reason, normalizados
# sin tildes. IdReason como documentacion (getAgendaDay nunca lo trae).
_RETIROS = {
    'retiro total': 18171,
    'retiro digitrack': 21795,
    'retiro invisalign': 26032,
    'retiro clear correct': 31966,
    'retiro alineadores': None,      # visto en el historico, sin IdReason en el .txt
    'retiro total + inicio': 33599,
}

# Motivos que NO son el control de fotos aunque caigan justo despues del retiro:
# el paciente vino por una urgencia o a reponer algo. Se saltan SIN consumir el
# aviso, asi el candidato sigue vivo para su cita siguiente (la de verdad).
# Se comparan como SUBCADENA del motivo normalizado: los nombres reales varian
# ('Retenedor Fijo Suelto / Roto', 'Essix / Placa Perdida',
# 'Placa/Essix Roto / Desajustado', 'Tornillo Suelto con Dolor').
_URGENCIA = ('suelto', 'roto', 'perdida', 'perdido', 'desajustado', 'urgencia', 'dolor')

_DEFAULT_CONFIG = {
    # Arranca APAGADO a proposito: primero se corre backfill(), se revisa la
    # lista con el doctor, y recien ahi se enciende (mismo criterio con que se
    # desplego control_dental).
    'activo': False,
    'doctor_key': 'alberto',          # para resolver EMAIL_<DOC_KEY>
    'doctor_nombre': 'Alberto Del Real',   # se compara contra ProfessionalName
    'dias_minimos': 10,               # antes de 10 dias la encia no esta sana
    'dias_maximos': 180,              # mas alla, la cita ya no es "el control post-retiro"
    'dias_atras': 45,                 # ventana del barrido hacia atras
    'hora_envio': '19:45',            # cierre de jornada, con el caso fresco
    'max_por_correo': 20,
    # nombre normalizado -> 'retiro' | 'urgencia' | 'ignorar'. Permite clasificar
    # un motivo nuevo o renombrado SIN deploy, igual que control_dental.
    'motivos_extra': {},
}

_rut_key = avisos.rut_key
_normalizar = control_dental._normalizar


def clasificar_motivo(reason, cfg=None):
    """'retiro' | 'urgencia' | None. cfg['motivos_extra'] manda sobre las
    constantes del modulo (asi el panel resuelve un motivo nuevo sin deploy);
    un valor 'ignorar' ahi hace que el motivo no sea ni una cosa ni la otra."""
    clave = _normalizar(reason)
    if not clave:
        return None
    extra = (cfg or {}).get('motivos_extra') or {}
    if clave in extra:
        valor = extra[clave]
        return valor if valor in ('retiro', 'urgencia') else None
    if clave in _RETIROS:
        return 'retiro'
    if any(p in clave for p in _URGENCIA):
        return 'urgencia'
    return None


def es_del_doctor(professional_name, cfg):
    """True si la cita es del doctor configurado. Se compara SIN titulo de los
    dos lados (dentidesk.sin_titulo_doctor): la API devuelve 'Alberto Del Real'
    y el modal del F2 muestra 'Dr. Alberto Del Real'."""
    objetivo = _normalizar(dentidesk.sin_titulo_doctor(cfg.get('doctor_nombre') or ''))
    actual = _normalizar(dentidesk.sin_titulo_doctor(professional_name or ''))
    return bool(objetivo) and objetivo == actual


# ── Config ──────────────────────────────────────────────────────────────────

def _validar_config(cfg, data):
    if not isinstance(data, dict):
        return cfg
    if 'activo' in data:
        cfg['activo'] = bool(data['activo'])
    for k in ('doctor_key', 'doctor_nombre'):
        if k in data and str(data[k]).strip():
            cfg[k] = str(data[k]).strip()
    for k in ('dias_minimos', 'dias_maximos', 'dias_atras', 'max_por_correo'):
        if k in data:
            try:
                n = int(data[k])
                if n > 0:
                    cfg[k] = n
            except (TypeError, ValueError):
                pass
    if 'hora_envio' in data:
        hora = str(data['hora_envio']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['hora_envio'] = hora
    if isinstance(data.get('motivos_extra'), dict):
        limpio = {}
        for k, v in data['motivos_extra'].items():
            clave = _normalizar(k)
            if clave and v in ('retiro', 'urgencia', 'ignorar'):
                limpio[clave] = v
        cfg['motivos_extra'] = limpio
    # dias_minimos por sobre dias_maximos dejaria el sistema mudo sin avisar.
    if cfg['dias_minimos'] >= cfg['dias_maximos']:
        cfg['dias_minimos'] = _DEFAULT_CONFIG['dias_minimos']
        cfg['dias_maximos'] = _DEFAULT_CONFIG['dias_maximos']
    return cfg


def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))   # copia profunda
    return _validar_config(cfg, data)


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo criterio
    que control_dental.save_config() / seguimiento_pc.save_config()."""
    with _LOCK:
        cfg = _validar_config(load_config(), updates if isinstance(updates, dict) else {})
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro ────────────────────────────────────────────────────────────────

_ESTRUCTURA = {
    'retiros': {},      # rut -> {fecha, motivo, doctor, nombre, id_agenda}
    'watchlist': {},    # rut -> {nombre, nota, agregado}
    'pendientes': {},   # rut -> candidato listo para el correo de hoy
    'avisados': {},     # rut -> fecha_iso del aviso (dedup por paciente)
    'vistos': {},       # id_agenda -> fecha_iso (dedup de citas, podado a 90d)
    'historial': [],    # avisos ya mandados, podado a 365 dias
    'motivos_desconocidos': {},
}

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2,
                             default=_ESTRUCTURA, claves=_ESTRUCTURA)


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


# ── Watchlist (inscripcion manual) ──────────────────────────────────────────

def agregar_watchlist(rut, nombre='', nota=''):
    """Inscribe un paciente para que su PROXIMA cita atendida dispare el aviso,
    sin exigir que se haya visto su retiro. Es lo que permite pedir "cuando
    venga tal paciente, recuerdamelo": su retiro puede ser anterior a
    que este sistema existiera."""
    clave = _rut_key(rut)
    if not clave:
        return None
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('watchlist', {})[clave] = {
            'nombre': (nombre or '').strip(),
            'nota': (nota or '').strip(),
            'agregado': fechas.hoy_chile().isoformat(),
        }
        _save_registro(reg)
        return reg['watchlist'][clave]


def quitar_watchlist(rut):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        existia = reg.get('watchlist', {}).pop(clave, None) is not None
        if existia:
            _save_registro(reg)
        return existia


def listar_watchlist():
    reg = _load_registro()
    return [dict(v, rut=k) for k, v in sorted(reg.get('watchlist', {}).items(),
                                              key=lambda kv: kv[1].get('agregado', ''))]


# ── El barrido ──────────────────────────────────────────────────────────────

def _citas_ordenadas(resultados, cfg):
    """Aplana [(date, [citas])] a una lista de citas del doctor configurado, ya
    atendidas, ORDENADA por fecha. El orden importa: el retiro tiene que
    registrarse antes de que se evalue la cita que viene despues."""
    salida = []
    for d, citas in resultados:
        d_iso = d.isoformat() if hasattr(d, 'isoformat') else str(d)[:10]
        for c in citas or []:
            estado = (c.get('Status') or '').lower()
            # Solo dias pasados y solo lo que de verdad ocurrio. Se usa
            # _ESTADOS_NO_OCURRIO (de control_dental) y NO
            # dentidesk._ESTADOS_INACTIVOS: esa ultima descarta 'atendid', que
            # para una cita futura tiene sentido pero aca es justo la señal que
            # buscamos.
            if any(s in estado for s in control_dental._ESTADOS_NO_OCURRIO):
                continue
            if 'atendid' not in estado:
                continue
            if not es_del_doctor(c.get('ProfessionalName'), cfg):
                continue
            rut = _rut_key(c.get('PatientDocument'))
            if not rut:
                continue
            salida.append({
                'fecha': (c.get('Date') or d_iso)[:10],
                'rut': rut,
                'nombre': (c.get('PatientName') or '').strip(),
                'motivo': (c.get('Reason') or '').strip(),
                'doctor': (c.get('ProfessionalName') or '').strip(),
                'id_agenda': str(c.get('IdAgenda') or ''),
            })
    salida.sort(key=lambda c: (c['fecha'], c['id_agenda']))
    return salida


def _dias_entre(a_iso, b_iso):
    return (date.fromisoformat(b_iso) - date.fromisoformat(a_iso)).days


def _aplicar_barrido(reg, cfg, resultados, hoy, solo_sembrar=False):
    """Aplica sobre 'reg' (in-place) el resultado del barrido. Separado de
    barrer() para poder probarlo sin red (los tests pasan citas ya armadas).

    solo_sembrar=True registra los retiros pero NO genera candidatos: es lo que
    usa backfill(). Sin eso, encender el sistema mandaria de golpe los controles
    de medio anio -- la misma oleada que ya enseño confirmaciones.py.
    """
    retiros = reg.setdefault('retiros', {})
    watchlist = reg.setdefault('watchlist', {})
    pendientes = reg.setdefault('pendientes', {})
    avisados = reg.setdefault('avisados', {})
    vistos = reg.setdefault('vistos', {})
    desconocidos = reg.setdefault('motivos_desconocidos', {})
    nuevos = 0

    for c in _citas_ordenadas(resultados, cfg):
        rut, fecha, motivo = c['rut'], c['fecha'], c['motivo']
        tipo = clasificar_motivo(motivo, cfg)

        if tipo == 'retiro':
            previo = retiros.get(rut)
            # Se queda con el retiro MAS RECIENTE: un paciente puede terminar
            # una fase, volver a tratamiento y retirarse de nuevo.
            if not previo or fecha >= previo.get('fecha', ''):
                retiros[rut] = {'fecha': fecha, 'motivo': motivo,
                                'doctor': c['doctor'], 'nombre': c['nombre'],
                                'id_agenda': c['id_agenda']}
            continue

        if solo_sembrar:
            continue

        # Dedup por cita: si esta cita ya se evaluo, no se vuelve a mirar.
        if c['id_agenda'] and c['id_agenda'] in vistos:
            continue

        # Una urgencia no es el control de fotos: se salta SIN marcarla vista,
        # para que el candidato siga vivo esperando su cita siguiente.
        if tipo == 'urgencia':
            continue

        en_watchlist = rut in watchlist
        retiro = retiros.get(rut)
        origen = ''

        if en_watchlist and fecha >= watchlist[rut].get('agregado', ''):
            origen = 'watchlist'
        elif retiro and fecha > retiro.get('fecha', ''):
            d = _dias_entre(retiro['fecha'], fecha)
            if cfg['dias_minimos'] <= d <= cfg['dias_maximos']:
                origen = 'retiro'
            else:
                # Fuera de ventana: se marca vista para no re-evaluarla cada dia.
                if c['id_agenda']:
                    vistos[c['id_agenda']] = fecha
                continue
        else:
            if tipo is None and motivo:
                desconocidos[_normalizar(motivo)] = motivo
            continue

        if rut in avisados:
            if c['id_agenda']:
                vistos[c['id_agenda']] = fecha
            continue

        if c['id_agenda']:
            vistos[c['id_agenda']] = fecha
        pendientes[rut] = {
            'rut': rut,
            'nombre': c['nombre'] or (watchlist.get(rut, {}).get('nombre') or ''),
            'fecha_control': fecha,
            'motivo_control': motivo,
            'doctor': c['doctor'],
            'id_agenda': c['id_agenda'],
            'origen': origen,
            'nota': watchlist.get(rut, {}).get('nota', '') if en_watchlist else '',
            'fecha_retiro': (retiro or {}).get('fecha', ''),
            'motivo_retiro': (retiro or {}).get('motivo', ''),
        }
        nuevos += 1

    # Poda: 'vistos' a 90 dias (igual que control_dental / seguimiento_pc).
    limite = (hoy - timedelta(days=90)).isoformat()
    reg['vistos'] = {k: v for k, v in vistos.items() if v >= limite}
    return nuevos


def _scheduling_cfg():
    """dentidesk._get_agenda_day() necesita el config de scheduling (credenciales
    DentiDesk) -- import perezoso para evitar ciclos (patron de control_dental)."""
    import scheduling
    return scheduling.load_config()


def _barrido(cfg, dias_atras, hoy, max_workers=6, solo_sembrar=False):
    from concurrent.futures import ThreadPoolExecutor
    scfg = _scheduling_cfg()
    dias = [hoy - timedelta(days=k)
            for k in range(0, dias_atras + 1)
            if (hoy - timedelta(days=k)).weekday() < 5]

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
        nuevos = _aplicar_barrido(reg, cfg, resultados, hoy, solo_sembrar=solo_sembrar)
        _save_registro(reg)
    return {'dias_procesados': len(dias), 'nuevos': nuevos,
            'pendientes': len(reg.get('pendientes', {})),
            'retiros_conocidos': len(reg.get('retiros', {})),
            'hoy': hoy.isoformat()}


def barrer(cfg=None, max_workers=6):
    """Barrido diario: recorre getAgendaDay de -dias_atras a hoy (solo dias
    habiles) y deja los candidatos en 'pendientes'.

    Los -45 dias NO son redundancia: la clinica marca "Atendido" DESPUES de la
    visita, asi que sin re-mirar hacia atras el barrido no veria nunca el estado
    final de una cita. Ademas hace el barrido idempotente y auto-reparable si
    Render se reinicia."""
    cfg = cfg or load_config()
    return _barrido(cfg, cfg['dias_atras'], fechas.hoy_chile(), max_workers)


def backfill(meses=6, cfg=None, max_workers=6):
    """One-off: siembra 'retiros' con los retiros anteriores al arranque, SIN
    generar un solo aviso. Se corre una vez, fuera de horario de atencion."""
    cfg = cfg or load_config()
    return _barrido(cfg, int(meses * 30), fechas.hoy_chile(), max_workers,
                    solo_sembrar=True)


# ── Consulta y cierre ───────────────────────────────────────────────────────

def pendientes(cfg=None):
    """Candidatos listos para el correo, el mas antiguo primero, cortado en
    max_por_correo."""
    cfg = cfg or load_config()
    lista = sorted(_load_registro().get('pendientes', {}).values(),
                   key=lambda p: (p.get('fecha_control', ''), p.get('nombre', '')))
    return lista[:cfg['max_por_correo']]


def marcar_avisados(ruts, hoy=None):
    """Cierra los candidatos que SI salieron en un correo enviado. Se llama
    despues del envio, nunca antes: si SMTP falla, el candidato sigue pendiente
    y se reintenta al dia siguiente en vez de perderse."""
    hoy_iso = (hoy or fechas.hoy_chile()).isoformat()
    with _LOCK:
        reg = _load_registro()
        historial = reg.setdefault('historial', [])
        for rut in ruts:
            cand = reg.get('pendientes', {}).pop(rut, None)
            reg.setdefault('avisados', {})[rut] = hoy_iso
            if cand:
                historial.append(dict(cand, avisado=hoy_iso))
            # Un paciente inscrito a mano ya cumplio su proposito: sale de la
            # watchlist para no volver a avisar en cada cita que tenga.
            reg.get('watchlist', {}).pop(rut, None)
        limite = (date.fromisoformat(hoy_iso) - timedelta(days=365)).isoformat()
        reg['historial'] = [h for h in historial if h.get('avisado', '') >= limite][-500:]
        _save_registro(reg)
        return len(ruts)


def descartar(rut):
    """Saca un candidato sin mandarlo (el doctor decide que ese caso no lleva
    collage). Queda en 'avisados' para que el barrido no lo vuelva a proponer."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        habia = reg.get('pendientes', {}).pop(clave, None) is not None
        reg.setdefault('avisados', {})[clave] = fechas.hoy_chile().isoformat()
        _save_registro(reg)
        return habia


def historial(limite=100):
    return list(reversed(_load_registro().get('historial', [])))[:limite]


def motivos_desconocidos():
    """Motivos vistos despues de un retiro que no calzan con ninguna lista. El
    panel los muestra para clasificarlos en motivos_extra sin deploy."""
    return _load_registro().get('motivos_desconocidos', {})


def resumen():
    reg = _load_registro()
    return {
        'pendientes': len(reg.get('pendientes', {})),
        'watchlist': len(reg.get('watchlist', {})),
        'retiros_conocidos': len(reg.get('retiros', {})),
        'avisados': len(reg.get('avisados', {})),
        'historial': len(reg.get('historial', [])),
        'motivos_desconocidos': len(reg.get('motivos_desconocidos', {})),
    }
