"""
nps.py - Encuestas de satisfaccion / NPS por WhatsApp para la clinica de
ortodoncia. Este modulo es el CEREBRO SIN RED: solo config, registro en JSON
y logica de decision. El envio real (plantilla WhatsApp) y la recepcion de
la respuesta (webhook) viven en otros modulos -- aca no hay ninguna llamada
de red, mismo criterio que control_dental.py y recaptacion.py.

Reutiliza clasificar_motivo() y sumar_meses() de control_dental.py (no se
reimplementa la clasificacion de motivos de DentiDesk ni la aritmetica de
meses, que ya estan resueltas y probadas alla) y dentidesk.limpiar_rut()
para normalizar RUT (mismo criterio que control_dental._rut_key).

Config + registro propios (no reusan los de control_dental.py ni los de
recaptacion.py, son avisos distintos), en el mismo disco persistente de
Render (misma base que patient_index.json, via PATIENT_INDEX_PATH) para
sobrevivir a los redeploys sin pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime

import dentidesk
import control_dental
import fechas      # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('NPS_CONFIG_PATH', _BASE_DIR / 'nps_config.json'))
REGISTRO_PATH = Path(os.environ.get('NPS_REGISTRO_PATH', _BASE_DIR / 'nps_registro.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    'activo': False,               # OFF por defecto (mismo criterio que control_dental
                                    # y recordatorios_wa) -- se enciende solo cuando la
                                    # clinica revisa el flujo.
    'review_url': 'https://g.page/r/CfYPKRCc7nsxEBM/review',
    'horas_despues_atencion': 3,
    'ventana_inicio': '11:00',
    'ventana_fin': '19:00',
    'frecuencia_meses': 6,
    'cooldown_meses': 6,
    'silencio_promotor_meses': 12,
    'max_envios_por_dia': 30,
    'periodico_activo': True,
    'nps_buena_es': 'pasivo',      # 'pasivo' | 'detractor'
}

# Cuantos dias mantener una entrada en 'vistos' (dedup del barrido/servidor).
# Mismo criterio que control_dental._DIAS_RETENCION_VISTOS -- sin poda el
# JSON crece para siempre.
_DIAS_RETENCION_VISTOS = 90


def _rut_key(rut):
    """Normaliza para usar como clave de los dicts del registro -- mismo
    criterio que control_dental._rut_key (dentidesk.limpiar_rut, con
    fallback al string tal cual si viene vacio/no-RUT)."""
    return dentidesk.limpiar_rut(rut) or (rut or '').strip()


def _normalizar_hora_a_min(hhmm):
    """Convierte 'HH:MM' a minutos desde medianoche, para comparaciones
    numericas si algun llamador lo necesita. En la practica dentro_de_ventana()
    compara los strings directo (HH:MM ordena lexicograficamente igual que
    numericamente), esta funcion queda de apoyo/documentacion."""
    try:
        h, m = hhmm.split(':')
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return 0


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
    if 'review_url' in data:
        cfg['review_url'] = str(data['review_url']).strip()
    if 'horas_despues_atencion' in data:
        try:
            n = int(data['horas_despues_atencion'])
            if n > 0:
                cfg['horas_despues_atencion'] = n
        except (TypeError, ValueError):
            pass
    if 'ventana_inicio' in data:
        hora = str(data['ventana_inicio']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['ventana_inicio'] = hora
    if 'ventana_fin' in data:
        hora = str(data['ventana_fin']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['ventana_fin'] = hora
    if 'frecuencia_meses' in data:
        try:
            n = int(data['frecuencia_meses'])
            if n > 0:
                cfg['frecuencia_meses'] = n
        except (TypeError, ValueError):
            pass
    if 'cooldown_meses' in data:
        try:
            n = int(data['cooldown_meses'])
            if n > 0:
                cfg['cooldown_meses'] = n
        except (TypeError, ValueError):
            pass
    if 'silencio_promotor_meses' in data:
        try:
            n = int(data['silencio_promotor_meses'])
            if n > 0:
                cfg['silencio_promotor_meses'] = n
        except (TypeError, ValueError):
            pass
    if 'max_envios_por_dia' in data:
        try:
            n = int(data['max_envios_por_dia'])
            if n > 0:
                cfg['max_envios_por_dia'] = n
        except (TypeError, ValueError):
            pass
    if 'periodico_activo' in data:
        cfg['periodico_activo'] = bool(data['periodico_activo'])
    if 'nps_buena_es' in data:
        valor = str(data['nps_buena_es']).strip()
        if valor in ('pasivo', 'detractor'):
            cfg['nps_buena_es'] = valor
    return cfg


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo
    criterio que control_dental.save_config()."""
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
        if 'review_url' in updates:
            cfg['review_url'] = str(updates['review_url']).strip()
        if 'horas_despues_atencion' in updates:
            try:
                n = int(updates['horas_despues_atencion'])
                if n > 0:
                    cfg['horas_despues_atencion'] = n
            except (TypeError, ValueError):
                pass
        if 'ventana_inicio' in updates:
            hora = str(updates['ventana_inicio']).strip()
            if len(hora) == 5 and hora[2] == ':':
                cfg['ventana_inicio'] = hora
        if 'ventana_fin' in updates:
            hora = str(updates['ventana_fin']).strip()
            if len(hora) == 5 and hora[2] == ':':
                cfg['ventana_fin'] = hora
        if 'frecuencia_meses' in updates:
            try:
                n = int(updates['frecuencia_meses'])
                if n > 0:
                    cfg['frecuencia_meses'] = n
            except (TypeError, ValueError):
                pass
        if 'cooldown_meses' in updates:
            try:
                n = int(updates['cooldown_meses'])
                if n > 0:
                    cfg['cooldown_meses'] = n
            except (TypeError, ValueError):
                pass
        if 'silencio_promotor_meses' in updates:
            try:
                n = int(updates['silencio_promotor_meses'])
                if n > 0:
                    cfg['silencio_promotor_meses'] = n
            except (TypeError, ValueError):
                pass
        if 'max_envios_por_dia' in updates:
            try:
                n = int(updates['max_envios_por_dia'])
                if n > 0:
                    cfg['max_envios_por_dia'] = n
            except (TypeError, ValueError):
                pass
        if 'periodico_activo' in updates:
            cfg['periodico_activo'] = bool(updates['periodico_activo'])
        if 'nps_buena_es' in updates:
            valor = str(updates['nps_buena_es']).strip()
            if valor in ('pasivo', 'detractor'):
                cfg['nps_buena_es'] = valor
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro ─────────────────────────────────────────────────────────────

_ESTRUCTURA = {
    'envios': {},
    'respuestas': {},
    'no_molestar': [],
    'vistos': {},
    'sembrado': False,        # ⚠️ la 1a corrida solo SIEMBRA (no encuesta a media cartera)
    'metricas_google': {},
    'baseline': {},
    'fecha_inicio_automatizacion': '',
    'overrides': {},
}

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2,
                             default=_ESTRUCTURA, claves=_ESTRUCTURA)


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


# ── Clasificacion del disparo (reusa control_dental.clasificar_motivo) ────

def clasificar_disparo(reason):
    """Devuelve 'hito'|'periodico'|None segun el Reason de la cita (mismo
    Reason que usa control_dental, tal como lo devuelve getAgendaDay). Un
    'hito' (fin de tratamiento) dispara la encuesta con mas peso que un
    control 'periodico' de rutina -- ver evaluar()."""
    cat = control_dental.clasificar_motivo(reason, None)
    if cat in ('fin_definitivo', 'fin_fase'):
        return 'hito'
    if cat in ('control', 'inicio_fijos', 'inicio_alineadores'):
        return 'periodico'
    return None


def es_promotor_silenciado(rut, cfg, reg):
    """True si el RUT respondio 'promotor' hace menos de
    silencio_promotor_meses -- a un promotor reciente no se le vuelve a
    preguntar tan seguido (ya dijo que esta feliz, insistir cansa)."""
    clave = _rut_key(rut)
    respuesta = (reg.get('respuestas') or {}).get(clave)
    if not respuesta or respuesta.get('categoria') != 'promotor':
        return False
    try:
        f_resp = datetime.fromisoformat(respuesta.get('fecha', '')).date()
    except (ValueError, TypeError):
        return False
    limite = control_dental.sumar_meses(f_resp, cfg.get('silencio_promotor_meses', 12))
    return fechas.hoy_chile() <= limite


# ── Evaluacion (las guardas, en orden) ──────────────────────────────────

def evaluar(rut, es_hito, cfg=None):
    """Devuelve None si se puede enviar, o un dict {'motivo','detalle',
    'puede_forzar'} si hay que bloquear. Orden de las guardas (mismo
    contrato que recaptacion.evaluar/control_dental.evaluar):
      1. no_molestar -- nunca se salta.
      2. promotor_reciente -- ya dijo que esta feliz hace poco.
      3. enviado_reciente -- cooldown global desde el ultimo envio.
      4. frecuencia_periodica -- solo para disparos NO hito: exige que
         hayan pasado frecuencia_meses desde el ultimo envio (en la
         practica el cooldown de arriba ya cubre esto la mayoria de las
         veces, pero se deja la guarda explicita por si se configuran
         valores distintos)."""
    cfg = cfg or load_config()
    clave = _rut_key(rut)
    reg = _load_registro()

    if clave in (reg.get('no_molestar') or []):
        return {
            'motivo': 'no_molestar',
            'detalle': 'Este paciente esta marcado como "no molestar": no se le envian encuestas de satisfaccion.',
            'puede_forzar': False,
        }

    if es_promotor_silenciado(rut, cfg, reg):
        return {
            'motivo': 'promotor_reciente',
            'detalle': f"El paciente respondio como promotor hace menos de {cfg.get('silencio_promotor_meses', 12)} meses.",
            'puede_forzar': True,
        }

    envios = (reg.get('envios') or {}).get(clave) or []
    if envios:
        ultimo = max(envios, key=lambda e: e.get('fecha', ''))
        try:
            f_envio = datetime.fromisoformat(ultimo['fecha']).date()
        except (KeyError, ValueError, TypeError):
            f_envio = None
        if f_envio is not None:
            cooldown = cfg.get('cooldown_meses', 6)
            limite = control_dental.sumar_meses(f_envio, cooldown)
            if fechas.hoy_chile() <= limite:
                return {
                    'motivo': 'enviado_reciente',
                    'detalle': f'Ya se le envio una encuesta hace menos de {cooldown} meses.',
                    'puede_forzar': True,
                }

        if not es_hito:
            frecuencia = cfg.get('frecuencia_meses', 6)
            limite_frec = control_dental.sumar_meses(f_envio, frecuencia) if f_envio else None
            if limite_frec is not None and fechas.hoy_chile() <= limite_frec:
                return {
                    'motivo': 'frecuencia_periodica',
                    'detalle': f'El disparo es periodico y todavia no pasan los {frecuencia} meses desde el ultimo envio.',
                    'puede_forzar': True,
                }

    return None


def dentro_de_ventana(cfg, ahora_hhmm):
    """True si 'ahora_hhmm' (formato HH:MM) cae dentro de la ventana horaria
    configurada -- comparacion de strings, que ordena igual que comparar
    los minutos (mismo truco que hora_envio en control_dental/recaptacion)."""
    return cfg.get('ventana_inicio', '11:00') <= ahora_hhmm <= cfg.get('ventana_fin', '19:00')


# ── Registro de envios / vistos / respuestas ────────────────────────────

def registrar_envio(rut, id_agenda, doctor):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('envios', {}).setdefault(clave, []).append({
            'fecha': fechas.ahora_chile().isoformat(timespec='seconds'),
            'id_agenda': str(id_agenda or ''),
            'doctor': doctor or '',
            'estado': 'enviado',
        })
        _save_registro(reg)


def marcar_visto(id_agenda, fecha_iso=None):
    """Marca una cita como ya procesada por el barrido (dedup por IdAgenda,
    mismo patron que control_dental.vistos). Poda las entradas mas viejas
    que _DIAS_RETENCION_VISTOS de una vez, asi el JSON no crece sin techo."""
    id_agenda = str(id_agenda or '')
    if not id_agenda:
        return
    fecha_iso = fecha_iso or fechas.hoy_chile().isoformat()
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('vistos', {})[id_agenda] = fecha_iso
        podar_vistos(reg)
        _save_registro(reg)


def ya_visto(id_agenda):
    reg = _load_registro()
    return str(id_agenda or '') in (reg.get('vistos') or {})


def podar_vistos(reg=None):
    """Elimina de reg['vistos'] las entradas mas viejas que
    _DIAS_RETENCION_VISTOS. Si no se pasa 'reg', carga/guarda su propia
    copia (para que el server pueda llamarla suelta, ej. desde un barrido
    periodico); si se pasa 'reg', muta in-place y NO guarda (lo hace el
    llamador, mismo patron que control_dental.barrer())."""
    standalone = reg is None
    if standalone:
        with _LOCK:
            reg = _load_registro()
            _podar_vistos_in_place(reg)
            _save_registro(reg)
            return reg
    _podar_vistos_in_place(reg)
    return reg


def _podar_vistos_in_place(reg):
    from datetime import timedelta
    limite_poda = (fechas.hoy_chile() - timedelta(days=_DIAS_RETENCION_VISTOS)).isoformat()
    vistos = reg.get('vistos', {})
    reg['vistos'] = {k: v for k, v in vistos.items() if v >= limite_poda}


def registrar_respuesta(rut, categoria, doctor=''):
    """Guarda la respuesta del paciente (categoria: 'promotor'|'pasivo'|
    'detractor'). Si ya habia una respuesta anterior para este RUT, la
    sobreescribe con la mas reciente -- solo interesa el ultimo sentir del
    paciente, no un historial de respuestas."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('respuestas', {})[clave] = {
            'categoria': categoria,
            'fecha': fechas.ahora_chile().isoformat(timespec='seconds'),
            'doctor': doctor or '',
        }
        _save_registro(reg)


def esta_sembrado():
    return bool(_load_registro().get('sembrado'))


def marcar_sembrado():
    with _LOCK:
        reg = _load_registro()
        reg['sembrado'] = True
        _save_registro(reg)


# ── No molestar ──────────────────────────────────────────────────────────

def agregar_no_molestar(rut):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        lista = reg.setdefault('no_molestar', [])
        if clave not in lista:
            lista.append(clave)
        _save_registro(reg)
        return lista


def quitar_no_molestar(rut):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        lista = reg.setdefault('no_molestar', [])
        if clave in lista:
            lista.remove(clave)
        _save_registro(reg)
        return lista


def lista_no_molestar():
    return list(_load_registro().get('no_molestar') or [])


def en_no_molestar(rut):
    """True si el RUT esta en la lista de 'no molestar'. Publico para que el
    server lo consulte al procesar un override 'enviar' (que salta el resto
    de las guardas de evaluar() pero NUNCA el opt-out del paciente)."""
    return _rut_key(rut) in (_load_registro().get('no_molestar') or [])


# ── Overrides manuales por cita (F2: Enviar / No Enviar) ────────────────────
#
# La asistente, con la cita abierta en DentiDesk, decide por esa cita puntual:
#   'no_enviar' -> el barrido nunca le manda la encuesta a esta cita.
#   'enviar'    -> se FUERZA el envio tras el tiempo planificado (horas_despues
#                  + ventana horaria), aunque el automatico no la habria tomado
#                  (motivo no-hito, cooldown, etc.). Respeta 'no molestar' (el
#                  opt-out del paciente manda), pero salta la elegibilidad por
#                  tipo de visita y el cooldown.
# La clave es el IdAgenda. Se guardan telefono/nombre/doctor/hora/duracion
# resueltos al momento del click porque DentiDesk no tiene 'buscar por id' y la
# cita puede caer fuera de la ventana ayer/hoy del barrido para cuando llegue
# la hora real de enviar (el server procesa estos overrides directo del
# registro, sin depender del scan de la agenda).

def registrar_override(id_agenda, accion, rut='', telefono='', nombre='',
                        doctor='', fecha_cita='', hora_cita='', duracion=0):
    """Crea/actualiza el override de una cita. accion: 'enviar'|'no_enviar'.
    Devuelve el dict guardado, o None si los datos minimos no estan."""
    id_agenda = str(id_agenda or '')
    if not id_agenda or accion not in ('enviar', 'no_enviar'):
        return None
    try:
        duracion = int(duracion or 0)
    except (TypeError, ValueError):
        duracion = 0
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('overrides', {})[id_agenda] = {
            'accion': accion,
            'rut': _rut_key(rut) if rut else '',
            'telefono': telefono or '',
            'nombre': nombre or '',
            'doctor': doctor or '',
            'fecha_cita': fecha_cita or '',
            'hora_cita': hora_cita or '',
            'duracion': duracion,
            'creado': fechas.ahora_chile().isoformat(timespec='seconds'),
            # 'estado' solo aplica al override 'enviar' (pendiente|enviado|
            # omitido); 'no_enviar' no lo usa (es un bloqueo permanente).
            'estado': 'pendiente',
        }
        _save_registro(reg)
        return reg['overrides'][id_agenda]


def get_override(id_agenda):
    """El override de esa cita, o None. Lo consulta el barrido (fase 2) para
    saltar las citas 'no_enviar' y no re-enviar las 'enviar' (que procesa la
    fase 1)."""
    return (_load_registro().get('overrides') or {}).get(str(id_agenda or ''))


def overrides_enviar_pendientes():
    """Overrides 'enviar' aun no procesados. El server los envia cuando pasa
    el tiempo planificado, sin depender del scan de la agenda (que solo mira
    ayer/hoy)."""
    reg = _load_registro()
    return [
        {'id_agenda': ida, **o}
        for ida, o in (reg.get('overrides') or {}).items()
        if o.get('accion') == 'enviar' and o.get('estado') == 'pendiente'
    ]


def marcar_override(id_agenda, estado):
    """Cambia el estado de un override 'enviar' ('enviado'|'omitido'). Devuelve
    True si existia."""
    id_agenda = str(id_agenda or '')
    with _LOCK:
        reg = _load_registro()
        o = (reg.get('overrides') or {}).get(id_agenda)
        if not o:
            return False
        o['estado'] = estado
        _save_registro(reg)
        return True


# ── Metricas manuales (Google reviews) ──────────────────────────────────

def set_metrica_mensual(mes, resenas, rating):
    """mes en formato 'YYYY-MM'. Se cargan a mano (no hay API de Google
    Reviews en este proyecto) para poder comparar contra el baseline."""
    with _LOCK:
        reg = _load_registro()
        try:
            resenas = int(resenas)
        except (TypeError, ValueError):
            resenas = 0
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = 0.0
        reg.setdefault('metricas_google', {})[str(mes)] = {
            'resenas': resenas,
            'rating': rating,
        }
        _save_registro(reg)


def set_baseline(resenas_mensuales_prom, rating, meses=None):
    """Guarda el promedio historico (antes de automatizar) para poder medir
    el impacto real del sistema mas adelante."""
    with _LOCK:
        reg = _load_registro()
        try:
            prom = float(resenas_mensuales_prom)
        except (TypeError, ValueError):
            prom = 0.0
        try:
            rating = float(rating)
        except (TypeError, ValueError):
            rating = 0.0
        reg['baseline'] = {
            'resenas_mensuales_prom': prom,
            'rating': rating,
            'meses': list(meses) if meses else [],
        }
        _save_registro(reg)


def set_fecha_inicio_automatizacion(fecha_iso):
    with _LOCK:
        reg = _load_registro()
        reg['fecha_inicio_automatizacion'] = fecha_iso or ''
        _save_registro(reg)


# ── Reportes para el panel ──────────────────────────────────────────────

def _mediana(valores):
    """Mediana a mano (sin numpy, no es dependencia del proyecto): ordena y
    toma el del medio, o el promedio de los dos centrales si son pares."""
    if not valores:
        return None
    ordenados = sorted(valores)
    n = len(ordenados)
    mitad = n // 2
    if n % 2 == 1:
        return ordenados[mitad]
    return (ordenados[mitad - 1] + ordenados[mitad]) / 2


def resumen(cfg=None):
    """Metricas para el panel: volumen, tasa de respuesta, NPS, resenas por
    mes vs baseline, y tiempo mediano entre envio y respuesta."""
    cfg = cfg or load_config()
    reg = _load_registro()
    envios = reg.get('envios') or {}
    respuestas = reg.get('respuestas') or {}

    enviadas = sum(len(lista) for lista in envios.values())
    respondidas = len(respuestas)
    tasa_respuesta = round(respondidas / enviadas, 4) if enviadas else 0

    promotores = sum(1 for r in respuestas.values() if r.get('categoria') == 'promotor')
    pasivos = sum(1 for r in respuestas.values() if r.get('categoria') == 'pasivo')
    detractores = sum(1 for r in respuestas.values() if r.get('categoria') == 'detractor')

    base = promotores + pasivos + detractores
    detractores_efectivos = detractores + (pasivos if cfg.get('nps_buena_es') == 'detractor' else 0)
    nps = round(100 * (promotores - detractores_efectivos) / base) if base else None

    metricas = dict(reg.get('metricas_google') or {})
    mes_actual = fechas.hoy_chile().strftime('%Y-%m')
    resenas_mes = dict(metricas)

    rating_reciente = None
    if metricas:
        ultimo_mes = sorted(metricas.keys())[-1]
        rating_reciente = metricas[ultimo_mes].get('rating')

    # Mediana de dias entre envio y respuesta: para cada RUT con respuesta,
    # busca su envio mas reciente que sea <= la fecha de respuesta.
    dias_respuesta = []
    for clave, resp in respuestas.items():
        try:
            f_resp = datetime.fromisoformat(resp.get('fecha', ''))
        except (ValueError, TypeError):
            continue
        envios_rut = envios.get(clave) or []
        candidatos = []
        for e in envios_rut:
            try:
                f_env = datetime.fromisoformat(e.get('fecha', ''))
            except (ValueError, TypeError):
                continue
            if f_env <= f_resp:
                candidatos.append(f_env)
        if not candidatos:
            continue
        f_envio_mas_reciente = max(candidatos)
        dias_respuesta.append((f_resp - f_envio_mas_reciente).days)

    return {
        'enviadas': enviadas,
        'respondidas': respondidas,
        'tasa_respuesta': tasa_respuesta,
        'promotores': promotores,
        'pasivos': pasivos,
        'detractores': detractores,
        'nps': nps,
        'resenas_mes': resenas_mes,
        'mes_actual': mes_actual,
        'baseline': reg.get('baseline') or {},
        'rating_reciente': rating_reciente,
        'mediana_atencion_respuesta_dias': _mediana(dias_respuesta),
        'config': cfg,
    }


def lista_por_categoria(categoria):
    """Respuestas filtradas por categoria ('promotor'|'pasivo'|'detractor'),
    ordenadas por fecha descendente. Para la pestania del panel."""
    reg = _load_registro()
    items = [
        {'rut': rut, **r} for rut, r in (reg.get('respuestas') or {}).items()
        if r.get('categoria') == categoria
    ]
    items.sort(key=lambda i: i.get('fecha', ''), reverse=True)
    return items


def historial(limite=100):
    """Envios aplanados (RUT + datos del envio + categoria de respuesta si
    existe), del mas reciente al mas antiguo. Molde control_dental.historial."""
    reg = _load_registro()
    respuestas = reg.get('respuestas') or {}
    plano = []
    for rut, lista in (reg.get('envios') or {}).items():
        respuesta = respuestas.get(rut)
        for e in lista:
            item = {**e, 'rut': rut}
            if respuesta:
                item['categoria'] = respuesta.get('categoria')
                item['fecha_respuesta'] = respuesta.get('fecha')
            plano.append(item)
    plano.sort(key=lambda e: e.get('fecha', ''), reverse=True)
    return plano[:limite]
