"""
recaptacion.py - Recordatorio de control (recaptacion de pacientes que dejaron
de venir), disparado a mano desde el asistente F2 (Ortodoncia Richard).

A diferencia de recordatorios_wa.py (que escanea la agenda sola, por horario),
aca no hay escaneo: la secretaria abre en DentiDesk la ULTIMA cita del
paciente, aprieta F2 y decide mandar el WhatsApp. El backend solo evalua si
corresponde (no tiene ya una hora agendada, no se le mando hace poco, no esta
en la lista de "no molestar") y lleva el registro anti-duplicados.

Config + registro propios (no reusan los de recordatorios_wa.py, son avisos
distintos), en el mismo disco persistente de Render (misma base que
patient_index.json / confirmaciones_enviadas.json, via PATIENT_INDEX_PATH)
para sobrevivir a los redeploys sin pasar por git.
"""

import os
import json
import threading
from pathlib import Path
from datetime import date, datetime, timedelta

import dentidesk
import fechas      # ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
CONFIG_PATH = Path(os.environ.get('RECAPTACION_CONFIG_PATH', _BASE_DIR / 'recaptacion_config.json'))
REGISTRO_PATH = Path(os.environ.get('RECAPTACION_REGISTRO_PATH', _BASE_DIR / 'recaptacion_registro.json'))

_LOCK = threading.Lock()

_DEFAULT_CONFIG = {
    'dias_minimos_reenvio': 90,
    # Hora (Chile) a la que el scheduler procesa los recordatorios PROGRAMADOS
    # (ver _loop_recaptacion_programados en server.py). Mismo formato/criterio
    # de validacion que recordatorios_wa (HH:MM).
    'hora_envio_programados': '10:00',
}

# Copiadas de recordatorios_wa.py (NO importar de alla): fecha_legible_larga
# necesita el anio (recordatorios_wa._fecha_legible no lo lleva, es para
# citas de la semana/dia siguiente donde el anio es obvio; aca el control
# puede recaer meses o anios despues, asi que el anio es necesario).
_DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']
_MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


def fecha_legible_larga(d):
    """'martes 1 de abril del 2025' -- version CON anio de _fecha_legible."""
    return f'{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]} del {d.year}'


# ── Config ────────────────────────────────────────────────────────────────

def load_config():
    data = {}
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            data = {}
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # copia profunda
    if isinstance(data, dict) and 'dias_minimos_reenvio' in data:
        try:
            dias = int(data['dias_minimos_reenvio'])
            if dias > 0:
                cfg['dias_minimos_reenvio'] = dias
        except (TypeError, ValueError):
            pass
    if isinstance(data, dict) and 'hora_envio_programados' in data:
        hora = str(data['hora_envio_programados']).strip()
        if len(hora) == 5 and hora[2] == ':':
            cfg['hora_envio_programados'] = hora
    return cfg


def save_config(updates):
    """Actualiza solo los campos recibidos; preserva el resto -- mismo
    criterio que recordatorios_wa.save_config()."""
    with _LOCK:
        cfg = load_config()
        if isinstance(updates, dict) and 'dias_minimos_reenvio' in updates:
            try:
                dias = int(updates['dias_minimos_reenvio'])
                if dias > 0:
                    cfg['dias_minimos_reenvio'] = dias
            except (TypeError, ValueError):
                pass
        if isinstance(updates, dict) and 'hora_envio_programados' in updates:
            hora = str(updates['hora_envio_programados']).strip()
            if len(hora) == 5 and hora[2] == ':':
                cfg['hora_envio_programados'] = hora
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, CONFIG_PATH)
        return cfg


# ── Registro (envios + no_molestar) ──────────────────────────────────────────

# 'programados' (recordatorios de control a futuro, agregado 2026-07-21) vive en
# el MISMO registro, no en un archivo aparte: son datos chicos y ya comparten
# disco y lock con envios/no_molestar.
# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(
    REGISTRO_PATH, indent=2,
    default={'envios': {}, 'no_molestar': [], 'programados': []},
    claves={'envios': {}, 'no_molestar': [], 'programados': []})


def _load_registro():
    return _STORE.load()


def _save_registro(reg):
    _STORE.save(reg)


def _rut_key(rut):
    """Normaliza para usar como clave del dict de envios -- distintos formatos
    del mismo RUT (con/sin puntos, con/sin guion) deben caer en la misma
    entrada. Usa el limpiador de dentidesk (mismo criterio que
    citas_futuras_paciente)."""
    return dentidesk.limpiar_rut(rut) or (rut or '').strip()


# ── Evaluacion (las 3 guardas, en orden) ─────────────────────────────────────

def evaluar(rut, cfg=None):
    """Devuelve None si se puede enviar, o un dict {'motivo','detalle',
    'puede_forzar'} si hay que bloquear. Orden de las guardas:
      1. no_molestar -- nunca se salta (puede_forzar=False).
      2. ya_tiene_hora -- cita activa futura (puede_forzar=True).
      3. enviado_reciente -- ya se le mando dentro de dias_minimos_reenvio
         (puede_forzar=True)."""
    cfg = cfg or load_config()
    scfg = _scheduling_cfg()
    clave = _rut_key(rut)

    reg = _load_registro()
    if clave in (reg.get('no_molestar') or []):
        return {
            'motivo': 'no_molestar',
            'detalle': 'Este paciente está marcado como "no molestar": no se le envían recordatorios de control.',
            'puede_forzar': False,
        }

    citas = dentidesk.citas_futuras_paciente(rut, scfg)
    if citas:
        c = citas[0]
        # Fecha en texto legible: este detalle lo lee la asistente en el panel
        # F2, un ISO suelto (2026-08-08) se entiende peor de un vistazo.
        try:
            f_leg = fecha_legible_larga(date.fromisoformat(c['fecha'][:10]))
        except (KeyError, ValueError):
            f_leg = c.get('fecha', '')
        return {
            'motivo': 'ya_tiene_hora',
            'detalle': f"El paciente ya tiene hora agendada el {f_leg} con {c['profesional'] or 'su doctor'}.",
            'puede_forzar': True,
        }

    envios = (reg.get('envios') or {}).get(clave) or []
    if envios:
        ultimo = max(envios, key=lambda e: e.get('fecha_envio', ''))
        try:
            f_envio = datetime.fromisoformat(ultimo['fecha_envio'])
        except (KeyError, ValueError):
            f_envio = None
        if f_envio is not None:
            dias_transcurridos = (fechas.ahora_chile() - f_envio).days
            dias_minimos = cfg.get('dias_minimos_reenvio', 90)
            if dias_transcurridos < dias_minimos:
                return {
                    'motivo': 'enviado_reciente',
                    'detalle': f'Ya se le envió un recordatorio de control hace {dias_transcurridos} días (el mínimo configurado son {dias_minimos}).',
                    'puede_forzar': True,
                }

    return None


def _scheduling_cfg():
    """citas_futuras_paciente() necesita el config de scheduling (credenciales
    DentiDesk), no el de recaptacion -- import perezoso para evitar ciclos."""
    import scheduling
    return scheduling.load_config()


# ── Registro de envios ───────────────────────────────────────────────────────

# Dos años. Mas generoso que el de confirmaciones/recordatorios porque este
# registro ES el historial que muestra el panel, no solo un anti-duplicado.
_DIAS_RETENCION_ENVIOS = 730


def _podar(reg):
    """Poda el historial viejo. Dos reglas de seguridad:

    1. De cada RUT se conserva SIEMPRE el envio mas reciente, por viejo que sea:
       la guarda `enviado_reciente` de evaluar() se calcula sobre el, y borrarlo
       equivaldria a habilitar un reenvio que no corresponde.
    2. Los programados solo se podan si ya estan CERRADOS (enviado/anulado/
       omitido). Un 'pendiente', por atrasado que este, nunca se toca: sigue en
       cola y `pendientes_vencidos` lo tiene que ver."""
    limite = (fechas.ahora_chile() - timedelta(days=_DIAS_RETENCION_ENVIOS)).isoformat()
    quitados = 0

    envios = reg.get('envios')
    if isinstance(envios, dict):
        for clave, lista in list(envios.items()):
            if not isinstance(lista, list) or len(lista) <= 1:
                continue
            lista.sort(key=lambda e: e.get('fecha_envio', ''))
            ultimo = lista[-1]
            conservados = [e for e in lista[:-1]
                           if (e.get('fecha_envio') or '') >= limite] + [ultimo]
            quitados += len(lista) - len(conservados)
            envios[clave] = conservados

    programados = reg.get('programados')
    if isinstance(programados, list):
        cerrados = ('enviado', 'anulado', 'omitido')
        conservados = [p for p in programados
                       if p.get('estado') not in cerrados
                       or (p.get('creado') or p.get('fecha_programada') or '') >= limite]
        quitados += len(programados) - len(conservados)
        reg['programados'] = conservados

    return quitados


def marcar_enviado(rut, id_agenda, doctor, nombre):
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        reg.setdefault('envios', {}).setdefault(clave, []).append({
            'fecha_envio': fechas.ahora_chile().isoformat(timespec='seconds'),
            'id_agenda': str(id_agenda or ''),
            'doctor': doctor or '',
            'nombre': nombre or '',
            'respondio': False,
        })
        podados = _podar(reg)
        _save_registro(reg)
    if podados:
        print(f'[recaptacion] podadas {podados} entradas de mas de '
              f'{_DIAS_RETENCION_ENVIOS} dias')


def marcar_respondio(rut):
    """Marca el envio MAS RECIENTE de ese RUT como respondido (el paciente toco
    'Agendar por WhatsApp'). Si el RUT no tiene envios registrados, no hace
    nada -- no revienta (puede pasar si el paciente responde a un envio muy
    viejo cuyo registro se perdio, o a un toque fuera de flujo)."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        envios = (reg.get('envios') or {}).get(clave)
        if not envios:
            return False
        ultimo = max(envios, key=lambda e: e.get('fecha_envio', ''))
        ultimo['respondio'] = True
        _save_registro(reg)
        return True


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
    """RUT marcados como 'no molestar'. Lo consume la pestania del panel, que
    los muestra con un boton para sacarlos de la lista."""
    return list(_load_registro().get('no_molestar') or [])


def historial(limite=100):
    """Envios aplanados (RUT + datos del envio), ordenados del mas reciente al
    mas antiguo. Para la pestania del panel."""
    reg = _load_registro()
    plano = []
    for rut, envios in (reg.get('envios') or {}).items():
        for e in envios:
            plano.append({**e, 'rut': rut})
    plano.sort(key=lambda e: e.get('fecha_envio', ''), reverse=True)
    return plano[:limite]


# ── Recordatorios PROGRAMADOS (fecha futura, se envian solos) ───────────────
#
# La asistente F2 puede elegir una fecha futura en vez de mandar el WhatsApp
# al instante. El envio real lo hace el scheduler de server.py
# (_loop_recaptacion_programados) el dia elegido, a la hora
# 'hora_envio_programados' de arriba.
#
# fecha_cita (de la cita de ORIGEN, la ultima atencion) se guarda junto al
# programado porque DentiDesk no tiene "buscar cita por id" -- para releer el
# telefono FRESCO el dia del envio hay que saber en que dia buscarla
# (dentidesk.info_cita exige fecha + id_agenda). Mismo problema que ya
# resuelve webhook_wa.py al reaccionar a un boton de una plantilla vieja.


def _siguiente_id_programado(reg, clave, fecha_programada):
    """Id corto y legible: '{rut}-{fecha_programada}', con sufijo numerico si
    ya existe (puede pasar si se anulo uno y se programa otro para el mismo
    RUT+fecha, o si el reemplazo de un pendiente deja el id 'libre' pero el
    viejo registro sigue en la lista con ese mismo id)."""
    base = f'{clave}-{fecha_programada}'
    existentes = {p.get('id') for p in reg.get('programados', [])}
    if base not in existentes:
        return base
    n = 2
    while f'{base}-{n}' in existentes:
        n += 1
    return f'{base}-{n}'


def programar(rut, id_agenda, fecha_cita, doctor, nombre, fecha_programada):
    """Crea un recordatorio de control PROGRAMADO para 'fecha_programada'
    (YYYY-MM-DD). Si el RUT ya tiene un 'pendiente', lo REEMPLAZA -- pasa el
    viejo a 'anulado' (motivo_omision explica el porque, queda en el
    historial) y crea el nuevo. Asi nunca hay dos programados pendientes para
    el mismo paciente (evita mandarle el WhatsApp dos veces si la secretaria
    reprograma la fecha).

    Devuelve el dict creado."""
    clave = _rut_key(rut)
    with _LOCK:
        reg = _load_registro()
        for p in reg.get('programados', []):
            if p.get('rut') == clave and p.get('estado') == 'pendiente':
                p['estado'] = 'anulado'
                p['motivo_omision'] = 'reemplazado por una nueva programacion'
        nuevo = {
            'id': _siguiente_id_programado(reg, clave, fecha_programada),
            'rut': clave,
            'id_agenda': str(id_agenda or ''),
            'fecha_cita': fecha_cita,
            'doctor': doctor or '',
            'nombre': nombre or '',
            'fecha_programada': fecha_programada,
            'creado': fechas.ahora_chile().isoformat(timespec='seconds'),
            'estado': 'pendiente',
            'motivo_omision': '',
        }
        reg.setdefault('programados', []).append(nuevo)
        _save_registro(reg)
        return nuevo


def listar_programados(incluir_cerrados=True):
    """Todos los programados ordenados por fecha_programada (ascendente). Si
    incluir_cerrados=False, solo devuelve los 'pendiente' (para el scheduler;
    el panel en cambio quiere ver todo, incluido lo ya enviado/anulado/omitido)."""
    programados = list(_load_registro().get('programados') or [])
    if not incluir_cerrados:
        programados = [p for p in programados if p.get('estado') == 'pendiente']
    programados.sort(key=lambda p: p.get('fecha_programada', ''))
    return programados


def anular_programado(id_):
    """Pasa un programado 'pendiente' a 'anulado'. Devuelve True si lo
    encontro y estaba pendiente, False si no existe o ya estaba cerrado (no
    tiene sentido 'anular' algo que ya se envio u omitio)."""
    with _LOCK:
        reg = _load_registro()
        for p in reg.get('programados', []):
            if p.get('id') == id_ and p.get('estado') == 'pendiente':
                p['estado'] = 'anulado'
                _save_registro(reg)
                return True
        return False


def pendientes_vencidos(hoy):
    """Programados 'pendiente' cuya fecha_programada ya llego (<=hoy). 'hoy'
    es un date (o string ISO YYYY-MM-DD, comparacion lexicografica funciona
    igual para ese formato)."""
    hoy_iso = hoy.isoformat() if hasattr(hoy, 'isoformat') else str(hoy)
    return [
        p for p in _load_registro().get('programados', [])
        if p.get('estado') == 'pendiente' and p.get('fecha_programada', '') <= hoy_iso
    ]


def marcar_programado(id_, estado, motivo_omision=''):
    """Cambia el estado de un programado (usado por el scheduler: 'enviado' u
    'omitido'). No valida transiciones -- el scheduler es el unico llamador
    y sabe lo que hace; anular_programado() de arriba si tiene la guarda de
    'solo si esta pendiente' porque ese si lo llama el panel a mano."""
    with _LOCK:
        reg = _load_registro()
        for p in reg.get('programados', []):
            if p.get('id') == id_:
                p['estado'] = estado
                p['motivo_omision'] = motivo_omision
                _save_registro(reg)
                return True
        return False
