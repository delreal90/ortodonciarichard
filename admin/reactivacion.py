"""
reactivacion.py - Reactivacion de pacientes INACTIVOS del Dr. Alberto (Ortodoncia
Richard), para reencantarlos.

Dos poblaciones, con mensaje distinto:
  - 'terminado'  -- el paciente TERMINO su tratamiento (retiro total / retenedor
    fijo / alta) hace meses y le corresponde un control de retencion.
  - 'abandono'   -- el paciente tuvo tratamiento (montaje, controles) pero dejo
    de venir sin pasar por una cita de alta -- se le pregunta si quiere retomar.

Es HERMANO de seguimiento_pc.py (mismo molde: config, registro JsonStore, barrido
sobre getAgendaDay, pendientes/marcar_mostrados con "dos toques") pero NO hay que
confundirlo con recaptacion.py (ese es el envio MANUAL que dispara la asistente
desde el F2 sobre una cita puntual). Este modulo tampoco le escribe al paciente:
solo detecta y expone los candidatos para el CORREO DIARIO del Dr. Alberto (runbook
revision-evoluciones), con el mensaje ya armado y el link de WhatsApp listo -- el
envio real lo dispara Alberto a mano desde su celular.

CEREBRO SIN RED: solo config, registro en JSON y logica de deteccion. La unica
lectura de DentiDesk es el barrido de getAgendaDay (igual que
control_dental.barrer/seguimiento_pc.barrer), que corre en el scheduler de
server.py, no aca dentro de evaluar/pendientes.

Config + registro propios en el mismo disco persistente de Render (misma base que
patient_index.json, via PATIENT_INDEX_PATH) para sobrevivir a los redeploys sin
pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, timedelta

import dentidesk
import control_dental   # clasificar_motivo, _normalizar, sumar_meses, _ESTADOS_NO_OCURRIO
import fechas           # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore        # guardado atomico con lock. Ver jsonstore.py.
import avisos           # rut_key + lista de no molestar, compartidos. Ver avisos.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('REACTIVACION_CONFIG_PATH', _BASE_DIR / 'reactivacion_config.json'))
REGISTRO_PATH = Path(os.environ.get('REACTIVACION_REGISTRO_PATH', _BASE_DIR / 'reactivacion_registro.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    'activo': True,                  # solo alimenta el reporte de Alberto (no
                                      # contacta pacientes), asi que es seguro
                                      # partir encendido.
    'meses_recall_terminado': 6,     # meses tras el alta para recordar el
                                      # control de retencion.
    'meses_abandono': 6,             # meses sin actividad + sin hora futura
                                      # para considerar abandono.
    'dias_entre_toques': 45,         # gap entre el toque 1 y el toque 2.
    'max_por_reporte': 10,           # tope de candidatos por correo.
    # Ventana PROFUNDA: estos pacientes califican por el PASO DEL TIEMPO (cruzar
    # los meses_recall/abandono desde un evento viejo), asi que el barrido tiene
    # que mirar ~18 meses hacia atras -- una ventana corta no los ve nunca. Por
    # eso corre SEMANAL (no diario) y desde el loop del scheduler (un hilo
    # persistente; lanzarlo desde un request no sobrevive en Render/gunicorn).
    'dias_atras': 545,               # ~18 meses hacia atras.
    'dias_adelante': 30,             # hacia adelante (para ver hora futura -> 'volvio').
    'hora_barrido': '02:00',
    'mensaje_terminado': ('Hola {nombre}, ¿cómo estás? Soy el Dr. Alberto Del Real, tu '
                          'ortodoncista. Vi que ya ha pasado un tiempo desde tu último control, '
                          'así que quise escribirte para saber cómo has estado con la placa y '
                          'los retenedores. Conviene hacer un control de vez en cuando para '
                          'confirmar que todo siga estable. Si te parece, podemos coordinar una '
                          'hora cuando te acomode. ¡Quedo atento!'),
    'mensaje_abandono': ('Hola {nombre}, ¿cómo estás? Soy el Dr. Alberto Del Real, tu '
                         'ortodoncista. Vi que quedó pendiente tu tratamiento hace un tiempo y '
                         'quería saber cómo has estado y si te gustaría retomarlo. Si te parece, '
                         'podemos coordinar una hora cuando te acomode. ¡Quedo atento!'),
}

_rut_key = avisos.rut_key
_normalizar = control_dental._normalizar
sumar_meses = control_dental.sumar_meses

_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def fecha_legible(d):
    """'martes 1 de abril' (sin año -- mismo criterio que seguimiento_pc)."""
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}'


def normalizar_wa(telefono):
    """Numero para el link wa.me: solo digitos; si son 9 y parten con 9 ->
    anteponer 56; si ya parte con 56 -> tal cual. Otro formato -> '' (no
    inventar). Mismo criterio que seguimiento_pc.normalizar_wa."""
    digitos = ''.join(c for c in (telefono or '') if c.isdigit())
    if len(digitos) == 9 and digitos.startswith('9'):
        return '56' + digitos
    if digitos.startswith('56') and len(digitos) >= 11:
        return digitos
    return ''


def _sumar_dias(fecha_iso, n):
    try:
        return (date.fromisoformat(fecha_iso[:10]) + timedelta(days=n)).isoformat()
    except (TypeError, ValueError):
        return fecha_iso


# ── Config ───────────────────────────────────────────────────────────────

def _validar_config(cfg, data):
    """Aplica sobre 'cfg' (dict ya inicializado con defaults) los campos validos
    de 'data'. Compartido por load_config y save_config."""
    if not isinstance(data, dict):
        return cfg
    if 'activo' in data:
        cfg['activo'] = bool(data['activo'])
    for k in ('meses_recall_terminado', 'meses_abandono', 'dias_entre_toques',
              'max_por_reporte', 'dias_atras', 'dias_adelante'):
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
    for k in ('mensaje_terminado', 'mensaje_abandono'):
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
    que seguimiento_pc.save_config()."""
    with _LOCK:
        cfg = _validar_config(load_config(), updates if isinstance(updates, dict) else {})
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro ─────────────────────────────────────────────────────────────

_ESTRUCTURA = {'candidatos': {}, 'vistos': {}, 'no_molestar': []}

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2,
                             default=_ESTRUCTURA, claves=_ESTRUCTURA)


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


# El opt-out manual. Compartido con recaptacion/control_dental/nps/seguimiento_pc:
# ver avisos.py.
_NO_MOLESTAR = avisos.ListaNoMolestar(_load_registro, _save_registro, _LOCK)


def agregar_no_molestar(rut):
    return _NO_MOLESTAR.agregar(rut)


def quitar_no_molestar(rut):
    return _NO_MOLESTAR.quitar(rut)


def lista_no_molestar():
    return _NO_MOLESTAR.listar()


def en_no_molestar(rut):
    return _NO_MOLESTAR.contiene(rut)


# ── El barrido: detecta candidatos a reencantar ─────────────────────────────

def _telefono_de_cita(c, rut):
    """El telefono viene en la cita (getAgendaDay trae 'Phone'); si no, se cae
    a la base local (pacientes.lookup). Copia exacta de
    seguimiento_pc._telefono_de_cita."""
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
    """Aplica sobre 'reg' (in-place) el resultado del barrido. 'resultados' es
    una lista de (date, citas). Separado de barrer() para poder probarlo sin
    red (los tests pasan citas ya armadas). A diferencia de seguimiento_pc
    (que solo mira una PC puntual), aca se agrega sobre TODA la ventana:
    interesa la cita PASADA mas reciente y si hay hora futura, no un dia
    especifico."""
    hoy_iso = hoy.isoformat()

    # 1) Agregar por RUT toda la actividad de la ventana.
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
            nombre = (c.get('PatientName') or '').strip()
            doctor = (c.get('ProfessionalName') or '').strip()
            telefono = _telefono_de_cita(c, rut)
            id_agenda = str(c.get('IdAgenda') or '')

            agg = por_rut.setdefault(rut, {
                'tiene_futura': False,
                'ultima_cita': None,          # {'fecha','nombre','doctor','telefono','id_agenda'}
                'tuvo_tratamiento': False,
                'fecha_alta': None,           # idem
            })

            if fecha_cita > hoy_iso:
                agg['tiene_futura'] = True
                continue

            # Cita pasada, ocurrida. fecha_cita <= hoy_iso.
            if fecha_cita >= hoy_iso:
                # fecha == hoy: no cuenta como "pasada" para ultima_cita/alta
                # (es de hoy, no confirma que ya paso todo el dia) -- se
                # descarta a proposito para no confundir 'hoy' con historia.
                continue

            info = {'fecha': fecha_cita, 'nombre': nombre, 'doctor': doctor,
                     'telefono': telefono, 'id_agenda': id_agenda}
            if not agg['ultima_cita'] or fecha_cita > agg['ultima_cita']['fecha']:
                agg['ultima_cita'] = info

            categoria = control_dental.clasificar_motivo(reason, cfg)
            if categoria in ('inicio_fijos', 'inicio_alineadores', 'control', 'fin_fase'):
                agg['tuvo_tratamiento'] = True
            if categoria == 'fin_definitivo':
                if not agg['fecha_alta'] or fecha_cita > agg['fecha_alta']['fecha']:
                    agg['fecha_alta'] = info

    # 2) Clasificar cada RUT y volcar a 'candidatos'.
    candidatos = reg.setdefault('candidatos', {})
    vistos = reg.setdefault('vistos', {})
    for rut, agg in por_rut.items():
        tiene_futura = agg['tiene_futura']
        ultima = agg['ultima_cita']
        fecha_alta = agg['fecha_alta']
        tuvo_tratamiento = agg['tuvo_tratamiento']

        existente = candidatos.get(rut)

        # 1) Con hora futura -> no es candidato.
        if tiene_futura:
            if existente and existente.get('estado') == 'pendiente':
                existente['estado'] = 'volvio'
            continue

        poblacion = None
        fecha_ref = None
        if fecha_alta and hoy >= sumar_meses(date.fromisoformat(fecha_alta['fecha']),
                                              cfg.get('meses_recall_terminado', 6)):
            poblacion = 'terminado'
            fecha_ref = fecha_alta
        elif tuvo_tratamiento and not fecha_alta and ultima and \
                hoy >= sumar_meses(date.fromisoformat(ultima['fecha']), cfg.get('meses_abandono', 6)):
            poblacion = 'abandono'
            fecha_ref = ultima

        if not poblacion:
            # No califica todavia -- no se crea ni se toca un candidato nuevo.
            # Si ya existia y estaba 'volvio' (tuvo una hora futura que ya
            # paso sin nueva actividad calificante), lo dejamos como esta.
            continue

        nombre = fecha_ref.get('nombre') or (existente or {}).get('nombre', '')
        telefono = fecha_ref.get('telefono') or (existente or {}).get('telefono', '')
        doctor = fecha_ref.get('doctor') or (existente or {}).get('doctor', '')
        ultima_cita_iso = ultima['fecha'] if ultima else (existente or {}).get('ultima_cita', '')
        id_agenda = fecha_ref.get('id_agenda') or ''

        if existente:
            existente['nombre'] = nombre
            existente['telefono'] = telefono
            existente['doctor'] = doctor
            existente['ultima_cita'] = ultima_cita_iso
            existente['fecha_ref'] = fecha_ref['fecha']
            existente['poblacion'] = poblacion
            if existente.get('estado') == 'pendiente':
                pass  # sigue pendiente, solo se refrescaron los datos.
            elif existente.get('estado') in ('volvio', 'completado'):
                # Un 'completado'/'volvio' no se resucita salvo que vuelva a
                # calificar (ya estamos aca porque calificA de nuevo) -- se
                # reactiva a 'pendiente' toque 1, siguiendo el patron de
                # seguimiento_pc para 'convertido'/'completado'.
                existente['estado'] = 'pendiente'
                existente['proximo_toque'] = 1
                existente['proxima_fecha'] = sumar_meses(
                    date.fromisoformat(fecha_ref['fecha']),
                    cfg.get('meses_recall_terminado' if poblacion == 'terminado'
                            else 'meses_abandono', 6)).isoformat()
                existente['toques'] = []
            if id_agenda:
                vistos[id_agenda] = hoy_iso
            continue

        # Nuevo candidato.
        meses = cfg.get('meses_recall_terminado' if poblacion == 'terminado' else 'meses_abandono', 6)
        proxima_fecha = sumar_meses(date.fromisoformat(fecha_ref['fecha']), meses).isoformat()
        candidatos[rut] = {
            'rut': rut,
            'nombre': nombre,
            'telefono': telefono,
            'doctor': doctor,
            'poblacion': poblacion,
            'fecha_ref': fecha_ref['fecha'],
            'ultima_cita': ultima_cita_iso,
            'estado': 'pendiente',
            'proximo_toque': 1,
            'proxima_fecha': proxima_fecha,
            'toques': [],
            'creado': fechas.ahora_chile().isoformat(timespec='seconds'),
        }
        if id_agenda:
            vistos[id_agenda] = hoy_iso

    return reg


def estado_barrido():
    """Ultimo barrido: {tipo, inicio, dias_procesados, dias_con_citas, candidatos,
    errores_scan, ejemplo_error, error}. {} si nunca corrio -- lo usa el loop del
    scheduler para decidir la primera corrida y el panel para diagnostico."""
    return _load_registro().get('ultimo_barrido') or {}


def resetear_barrido():
    """Borra la marca de ultimo barrido -> el loop del scheduler lo re-ejecuta en
    su proxima vuelta. Es como pide el endpoint /run forzar un re-barrido sin
    correr los ~400 dias dentro del request (que no sobrevive en gunicorn)."""
    with _LOCK:
        reg = _load_registro()
        reg.pop('ultimo_barrido', None)
        _save_registro(reg)


def barrer(cfg=None, max_workers=6):
    """Barrido PROFUNDO: recorre getAgendaDay de -dias_atras (~18 meses) a
    +dias_adelante (solo dias habiles) y actualiza los candidatos. Registra su
    estado en reg['ultimo_barrido'] (corre en el loop del scheduler, un hilo
    persistente -- los errores no se verian de otra forma). Devuelve el estado.

    OJO: es una corrida larga (~400 dias). Va SEMANAL desde el loop, NUNCA desde
    un request (en Render/gunicorn un hilo lanzado por un request no sobrevive)."""
    import traceback
    from concurrent.futures import ThreadPoolExecutor
    estado = {'tipo': 'barrer', 'inicio': fechas.ahora_chile().isoformat(timespec='seconds'),
              'dias_procesados': 0, 'dias_con_citas': 0, 'candidatos': 0,
              'pendientes': 0, 'errores_scan': 0, 'ejemplo_error': '', 'error': ''}
    try:
        cfg = cfg or load_config()
        scfg = _scheduling_cfg()
        hoy = fechas.hoy_chile()
        dias_atras = cfg.get('dias_atras', 545)
        dias_adelante = cfg.get('dias_adelante', 30)
        dias = [hoy + timedelta(days=k)
                for k in range(-dias_atras, dias_adelante + 1)
                if (hoy + timedelta(days=k)).weekday() < 5]

        def scan(d):
            try:
                return (d, dentidesk._get_agenda_day(scfg, d))
            except Exception as e:
                return (d, {'__error__': str(e)})

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            crudos = list(pool.map(scan, dias))
        resultados, errores, con_citas = [], [], 0
        for d, res in crudos:
            if isinstance(res, dict) and '__error__' in res:
                errores.append(res['__error__'])
                resultados.append((d, []))
            else:
                resultados.append((d, res))
                if res:
                    con_citas += 1
        resultados.sort(key=lambda r: r[0])

        with _LOCK:
            reg = _load_registro()
            _aplicar_barrido(reg, cfg, resultados, hoy)
            limite = (hoy - timedelta(days=90)).isoformat()
            reg['vistos'] = {k: v for k, v in reg.get('vistos', {}).items() if v >= limite}
            n_pend = sum(1 for c in reg.get('candidatos', {}).values() if c.get('estado') == 'pendiente')
            estado.update({'dias_procesados': len(dias), 'dias_con_citas': con_citas,
                           'candidatos': len(reg.get('candidatos', {})), 'pendientes': n_pend,
                           'errores_scan': len(errores),
                           'ejemplo_error': (errores[0][:200] if errores else '')})
            reg['ultimo_barrido'] = estado
            _save_registro(reg)
        return estado
    except Exception:
        estado['error'] = traceback.format_exc()[-800:]
        try:
            with _LOCK:
                reg = _load_registro()
                reg['ultimo_barrido'] = estado
                _save_registro(reg)
        except Exception:
            pass
        return estado


def backfill(cfg=None, meses=18, max_workers=4):
    """Inscribe la cartera actual: barre hacia atras 'meses' meses SOLAMENTE
    (sin dias_adelante), para poblar el registro de una sola vez. Molde
    exacto de control_dental.backfill(). Registra su estado/errores en
    reg['ultimo_barrido'] porque suele correr en un hilo (los errores no se
    verian de otra forma). max_workers bajo (4) para no saturar el auth de
    DentiDesk (JWT de un solo uso) con cientos de dias de golpe."""
    import traceback
    from concurrent.futures import ThreadPoolExecutor
    estado = {'tipo': 'backfill', 'meses': meses, 'inicio': fechas.ahora_chile().isoformat(timespec='seconds'),
              'dias_procesados': 0, 'dias_con_citas': 0, 'candidatos': 0, 'error': ''}
    try:
        cfg = cfg or load_config()
        scfg = _scheduling_cfg()
        hoy = fechas.hoy_chile()
        desde = sumar_meses(hoy, -meses)
        dias = [d for d in (desde + timedelta(days=k) for k in range((hoy - desde).days + 1))
                if d.weekday() < 5]

        def scan(d):
            try:
                return (d, dentidesk._get_agenda_day(scfg, d))
            except Exception as e:
                return (d, {'__error__': str(e)})

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            crudos = list(pool.map(scan, dias))
        # Separa errores de dias reales para diagnostico.
        resultados, errores, con_citas = [], [], 0
        for d, res in crudos:
            if isinstance(res, dict) and '__error__' in res:
                errores.append(res['__error__'])
                resultados.append((d, []))
            else:
                resultados.append((d, res))
                if res:
                    con_citas += 1
        resultados.sort(key=lambda r: r[0])

        with _LOCK:
            reg = _load_registro()
            _aplicar_barrido(reg, cfg, resultados, hoy)
            estado.update({'dias_procesados': len(dias), 'dias_con_citas': con_citas,
                           'candidatos': len(reg.get('candidatos', {})),
                           'errores_scan': len(errores),
                           'ejemplo_error': (errores[0][:200] if errores else '')})
            reg['ultimo_barrido'] = estado
            _save_registro(reg)
        return {'dias_procesados': len(dias), 'dias_con_citas': con_citas,
                'candidatos': len(reg.get('candidatos', {})), 'errores_scan': len(errores)}
    except Exception:
        estado['error'] = traceback.format_exc()[-800:]
        try:
            with _LOCK:
                reg = _load_registro()
                reg['ultimo_barrido'] = estado
                _save_registro(reg)
        except Exception:
            pass
        return estado


def _scheduling_cfg():
    """dentidesk._get_agenda_day() necesita el config de scheduling
    (credenciales DentiDesk) -- import perezoso para evitar ciclos (patron de
    control_dental/seguimiento_pc)."""
    import scheduling
    return scheduling.load_config()


# ── Consulta para el reporte diario ─────────────────────────────────────────

def pendientes(fecha=None, doctor=None, cfg=None):
    """Candidatos a los que les toca un toque en/antes de 'fecha' (proxima_fecha
    <= fecha; default hoy), opcionalmente filtrados por 'doctor' (subcadena del
    profesional). Excluye 'no molestar'. Elige la plantilla segun 'poblacion'.
    Ordena por proxima_fecha asc y corta en max_por_reporte.

    Lo consume el runbook revision-evoluciones para la seccion de reactivacion
    del correo del Dr. Alberto."""
    cfg = cfg or load_config()
    hoy_iso = (fecha.isoformat() if hasattr(fecha, 'isoformat') else str(fecha)[:10]) \
        if fecha else fechas.hoy_chile().isoformat()
    doc_norm = _normalizar(doctor) if doctor else ''
    reg = _load_registro()
    no_molestar = set(reg.get('no_molestar') or [])

    out = []
    for rut, c in (reg.get('candidatos') or {}).items():
        if c.get('estado') != 'pendiente':
            continue
        if rut in no_molestar:
            continue
        if c.get('proxima_fecha', '') > hoy_iso:
            continue
        if doc_norm and doc_norm not in _normalizar(c.get('doctor', '')):
            continue
        toque = c.get('proximo_toque', 1)
        poblacion = c.get('poblacion', 'abandono')
        plantilla = cfg.get('mensaje_terminado' if poblacion == 'terminado' else 'mensaje_abandono', '')
        nombre = c.get('nombre', '') or ''
        primer_nombre = nombre.split()[0] if nombre else ''
        try:
            f_ref = date.fromisoformat((c.get('fecha_ref') or '')[:10])
            f_ref_leg = fecha_legible(f_ref)
        except (TypeError, ValueError):
            f_ref_leg = c.get('fecha_ref', '')
        out.append({
            'rut': rut,
            'nombre': nombre,
            'telefono': c.get('telefono', ''),
            'wa_numero': normalizar_wa(c.get('telefono', '')),
            'poblacion': poblacion,
            'toque': toque,
            'fecha_ref': c.get('fecha_ref', ''),
            'fecha_ref_legible': f_ref_leg,
            'ultima_cita': c.get('ultima_cita', ''),
            'mensaje': plantilla.format(nombre=primer_nombre or 'hola'),
            '_proxima_fecha': c.get('proxima_fecha', ''),
        })
    out.sort(key=lambda x: x.pop('_proxima_fecha'))
    tope = cfg.get('max_por_reporte', 10)
    return out[:tope]


def marcar_mostrados(ruts, cfg=None):
    """Avanza el toque de cada RUT que el reporte YA mostro. Toque 1 -> agenda
    el toque 2 a hoy + dias_entre_toques. Toque 2 -> 'completado'. Anti-doble
    el mismo dia. Devuelve cuantos avanzo. Molde de seguimiento_pc.marcar_mostrados,
    salvo que el toque 2 se agenda desde HOY (no desde fecha_ref)."""
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
                c['proxima_fecha'] = (hoy + timedelta(days=cfg.get('dias_entre_toques', 45))).isoformat()
            avanzados += 1
        _save_registro(reg)
    return avanzados


def resumen():
    """Conteos para el panel/diagnostico."""
    reg = _load_registro()
    cand = reg.get('candidatos', {})
    def n(estado):
        return sum(1 for c in cand.values() if c.get('estado') == estado)
    return {
        'total': len(cand),
        'pendientes': n('pendiente'),
        'volvio': n('volvio'),
        'completado': n('completado'),
        'no_molestar': len(reg.get('no_molestar') or []),
        'ultimo_barrido': reg.get('ultimo_barrido') or {},
    }


def listar(estado=None):
    """Candidatos, opcionalmente filtrados por estado. Para panel/diagnostico."""
    reg = _load_registro()
    items = [dict(c) for c in (reg.get('candidatos') or {}).values()]
    if estado:
        items = [i for i in items if i.get('estado') == estado]
    items.sort(key=lambda i: i.get('proxima_fecha', ''), reverse=True)
    return items
