"""
kpi.py — Datamart de KPIs de la clínica (Ortodoncia Richard)

El proyecto medía muchas cosas pero no guardaba ninguna serie: `reporte_semanal.py`
recalcula todo contra DentiDesk en cada corrida y no persiste nada, así que no se
podía graficar una tendencia ni comparar contra el año pasado. Este módulo es el
almacén que faltaba: una copia local de la agenda que se alimenta sola, más las
consultas que la convierten en indicadores.

POR QUÉ SQLite Y NO jsonstore.py (regla 2 del CLAUDE.md)
--------------------------------------------------------
Misma excepción que `compras.py`, y por la misma razón, a mayor escala: son ~90.000
filas de citas con GROUP BY por mes/doctor/motivo y joins contra ingresos. Leer un
JSON entero en memoria por cada consulta del panel no tiene sentido. Se sigue el
molde de compras.py: esquema + `_migrar()` idempotente, y los CREATE INDEX en un
executescript SEPARADO y POSTERIOR a las migraciones (ver el bug `ix_compras_sus`
documentado en CLAUDE.md: un índice sobre una columna que todavía no existe aborta
el script entero y deja la base a medio migrar — y NO se manifiesta en una base nueva).

HALLAZGOS SOBRE getAgendaDay (verificados en vivo el 2026-08-21)
----------------------------------------------------------------
Sondeando la API con datos reales aparecieron tres cosas que la documentación del
proyecto no registraba, y que cambian lo que se puede medir:

1. **`IdStatus` numérico SÍ viene.** El CLAUDE.md afirma que DentiDesk "solo devuelve
   el NOMBRE del estado (nunca el IdStatus)". Es cierto para el MOTIVO (`IdReason`
   no viene), pero NO para el estado: cada cita trae `IdStatus`. Por eso acá el
   estado se normaliza por NÚMERO (exacto) y el nombre queda solo de respaldo.
   ⚠️ Esto NO cambia a los otros cuatro módulos que deciden por subcadena
   (`server._ESTADOS_NO_REAGENDABLES`, `dentidesk._ESTADOS_INACTIVOS`,
   `control_dental._ESTADOS_NO_OCURRIO`, `consentimientos._ESTADOS_CITA_NO_CUENTA`):
   están probados en producción y cambiarlos no es parte de este trabajo.

2. **`BookedBy` = quién agendó la cita**, con el valor literal `'Agendado via web'`
   para las reservas del sitio. El origen online/mesón sale gratis, sin cruzar con
   `agendamientos.jsonl`.

3. **`CreateDate` está en el 100% de las citas** ('YYYY-MM-DD HH:MM:SS'), así que la
   anticipación con que se agenda es medible en toda la historia.

Y lo más importante para el alcance: **DentiDesk devuelve la agenda de hace 5 años**
(probado en 2021-03-10). El histórico se reconstruye por API, con estados incluidos —
algo que el export parquet de `ortodonciarichard-analytics/` no permite, porque es un
export de ATENDIDOS y no trae el estado.

PRIVACIDAD
----------
La base guarda RUT y montos → vive en el disco persistente (env `KPI_DB_PATH`, por
defecto junto a `patient_index.json`) y está en `.gitignore`. Este repo es PÚBLICO.
Las consultas de este módulo devuelven AGREGADOS; las únicas que devuelven RUT son
las listas accionables que el panel usa para contactar al paciente.
"""

import os
import logging
import sqlite3
from pathlib import Path
from datetime import date, timedelta

import fechas            # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import control_dental    # clasificar_motivo / _normalizar — no se duplica la tabla de motivos.

log = logging.getLogger(__name__)


def ahora_cl():
    """Hora actual en Chile (Render corre en UTC). Ver fechas.py."""
    return fechas.ahora_chile_aware()


_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
DB_PATH = Path(os.environ.get('KPI_DB_PATH', _BASE_DIR / 'kpi.db'))

_normalizar = control_dental._normalizar


# ── Estados: del IdStatus numérico (exacto) al estado normalizado ────────────
# Mapa verificado en vivo (940 citas de 4 años, 2026-08-21). Los tres últimos no
# aparecieron en la muestra pero están en el diccionario oficial de DentiDesk
# (scheduling_config.json -> dentidesk) y se agregan por completitud.
ESTADO_POR_ID = {
    '2120':  'no_confirmado',       # No confirmado (nace así una cita nueva)
    '2121':  'confirmada',          # Confirmado
    '2122':  'cancelada',           # Hora Cancelada
    '2123':  'confirmada',          # Confirmado por e-mail
    '2124':  'cancelada',           # Cancelado por e-mail
    '2125':  'atendido',            # Atendido
    '2132':  'reagendada',          # Re-agendado
    '21254': 'en_sillon',           # En sillón
    '25991': 'no_llega',            # Paciente no llega
    '27084': 'no_contesta',         # No Contesta el Teléfono
    '33579': 'quiere_reagendar',    # Pidió cambiar su hora — LA CITA SIGUE VIGENTE
    '32180': 'confirmada',          # Confirmado por WhatsApp
    '40968': 'confirmada',          # 1 SEMANA Confirmado por WhatsApp
    # Estos dos aparecieron en el backfill y no están en la doc del proyecto. Se leen
    # como que el paciente SÍ se sentó (se llenó su ficha de primera consulta). Es una
    # lectura INFERIDA del nombre: si resulta significar otra cosa, se corrige el mapa
    # y se corre reclasificar() — no hay que volver a pedirle nada a DentiDesk.
    '27086': 'atendido',            # Ficha Primera Consulta
    '27085': 'atendido',            # Primera Consulta Ingresada
}

# Respaldo por NOMBRE, para un IdStatus que no esté en el mapa (un estado nuevo creado
# en DentiDesk después de escribir esto). Se evalúa en orden sobre el nombre
# normalizado (sin tildes, minúsculas): el primero que calce gana, así que los
# fragmentos más específicos van primero.
_ESTADO_POR_NOMBRE = (
    ('no llega',       'no_llega'),
    ('no contesta',    'no_contesta'),
    ('no seguir',      'no_seguir'),
    ('pidio cambiar',  'quiere_reagendar'),
    ('re-agend',       'reagendada'),
    ('reagend',        'reagendada'),
    ('cancel',         'cancelada'),
    ('atendid',        'atendido'),
    ('en sillon',      'en_sillon'),
    ('sala de espera', 'en_sillon'),
    ('no confirmado',  'no_confirmado'),
    ('confirmad',      'confirmada'),
)

# La cita OCURRIÓ: el paciente se sentó. 'en_sillon' cuenta — en un día pasado significa
# que la atención pasó y nadie la marcó "Atendido" después.
ESTADOS_OCURRIO = ('atendido', 'en_sillon')

# La cita sigue VIVA (ocupa su bloque y el paciente tiene hora). 'quiere_reagendar' está
# acá a propósito: el paciente pidió cambiarla, pero hasta que concrete la nueva esta
# vale — mismo criterio que el resto del proyecto (ver CLAUDE.md, "Pedir reagendar deja
# rastro en DentiDesk": la cita NO se cancela).
ESTADOS_VIGENTE = ('no_confirmado', 'confirmada', 'quiere_reagendar',
                   'atendido', 'en_sillon', 'no_contesta')


def estado_norm(id_status, nombre=''):
    """Estado normalizado de una cita. Prioriza el IdStatus numérico (exacto); cae al
    nombre solo si el id es desconocido. Devuelve 'otro' si no reconoce ninguno."""
    clave = str(id_status or '').strip()
    if clave in ESTADO_POR_ID:
        return ESTADO_POR_ID[clave]
    n = _normalizar(nombre)
    for fragmento, estado in _ESTADO_POR_NOMBRE:
        if fragmento in n:
            return estado
    return 'otro'


# ── Doctores: del nombre del profesional a la key interna ───────────────────
# Hay que aceptar DOS vocabularios: la API devuelve 'Octavio Del Real' y el export
# histórico 'Dr. Octavio Del Real'. Se normaliza quitando el título.
_TITULOS = ('dr. ', 'dra. ', 'dr ', 'dra ')


def _sin_titulo(nombre):
    n = _normalizar(nombre)
    for t in _TITULOS:
        if n.startswith(t):
            return n[len(t):].strip()
    return n


def doc_key(professional_name, cfg=None):
    """'Dr. Alberto Del Real' / 'Alberto Del Real' -> 'alberto'. El auxiliar de
    radiología -> 'rx'. Un profesional que no está en el config -> '' (no se
    inventa: queda fuera de los cortes por doctor, pero la cita igual se guarda)."""
    n = _sin_titulo(professional_name)
    if not n:
        return ''
    if 'rayos' in n or 'intraoral' in n or 'radiolog' in n:
        return 'rx'
    cfg = cfg or {}
    for key, d in (cfg.get('doctores') or {}).items():
        if isinstance(d, dict) and _sin_titulo(d.get('professional_name', '')) == n:
            return key
    return ''


# ── Categorías de motivo ─────────────────────────────────────────────────────
# Se reutiliza control_dental.clasificar_motivo() (inicio_fijos / inicio_alineadores /
# control / fin_fase / fin_definitivo) y se agregan las categorías que el embudo
# comercial necesita y que ese módulo no distingue, porque no le hacían falta.

_PRIMERA_CONSULTA = {'primera consulta'}

# El ESTUDIO INTEGRAL: la cita de toma de registros y la de explicación del plan.
# ⚠️ Dos vocabularios. La API los llama 'Registros para el Estudio Integral de
# Ortodoncia' / 'Explicación del Diagnóstico y Plan de Tratamiento'; el export
# histórico los llama 'Inicio' / 'Inicia Tratamiento' / 'Explicación Plan Tratamiento'.
# La evidencia de que 'Inicio' es la toma de registros y NO el inicio del tratamiento
# está en analisis_conversion_pc.py: dura 30 min (no 120 como un montaje), ocurre ANTES
# de la explicación del plan en 457 de 469 pacientes, y antes del montaje en 220 de 221.
# La secuencia real es:
#     Primera Consulta -> Inicio (registros) -> Explicación Plan -> Montaje/Instalar
# Además 'Inicia Tratamiento' (2021-2023) e 'Inicio' (2022-2026) son EL MISMO motivo
# renombrado: compararlos por separado año a año es engañoso.
_ESTUDIO = {
    # Vocabulario de la API
    'registros para el estudio integral de ortodoncia',
    'explicacion del diagnostico y plan de tratamiento',
    'estudio integral de ortodoncia',
    # Vocabulario del export histórico y del catálogo de DentiDesk
    'inicio', 'inicia tratamiento', 'retiro total + inicio',
    'explicacion plan tratamiento', 'explicacion plan tratamiento online',
    'expl. plan trat. online', 'explicacion plan + disyuntor',
    'explicacion plan + separaciones', 'explicacion plan + instalacion digitrack',
    're-estudio', 'planificar tratamiento',
}

# Segunda consulta: es otra EVALUACIÓN, no un paso del estudio. Va aparte a propósito —
# analisis_conversion_pc.py la contaba como "avance", y eso mezcla al paciente que
# arrancó con el que volvió a que lo miraran de nuevo. Ver `destino_primeras_consultas`,
# que reporta las dos variantes para poder auditar la diferencia.
_SEGUNDA_CONSULTA = {'segunda consulta', 'consulta online'}

_URGENCIA = {
    'bracket suelto', 'fierro pincha', 'dolor / molestia', 'arco suelto / roto',
    'placa/essix roto / desajustado', 'retenedor fijo suelto / roto', 'banda suelta / rota',
    'boca herida', 'encias inflamadas', 'aparato herbst suelto / roto', 'disyuntor suelto',
    'forsus suelto', 'nance suelto / roto', 'pal bar suelta / rota', 'carriere suelto',
    'quad helix suelto / roto', 'microtornillo suelto', 'tornillo suelto con dolor',
    'tornillo inflamado', 'plano relajacion roto', 'essix / placa perdida',
    'mascara de laire perdida', 'cemento out', 'plano relajacion perdido',
}


def categoria_motivo(reason, cfg=None):
    """Categoría del motivo para los KPIs. Las categorías propias de este módulo se
    evalúan ANTES de delegar en control_dental, porque ese módulo devuelve None para
    'Primera Consulta' y para el estudio (no le hacían falta)."""
    clave = _normalizar(reason)
    if not clave:
        return ''
    if clave in _PRIMERA_CONSULTA:
        return 'primera_consulta'
    if clave in _SEGUNDA_CONSULTA:
        return 'segunda_consulta'
    if clave in _ESTUDIO:
        return 'estudio'
    if clave in _URGENCIA:
        return 'urgencia'
    return control_dental.clasificar_motivo(reason, cfg) or 'otro'


# Categorías que cuentan como "arrancó el tratamiento" en el embudo comercial.
CATEGORIAS_INICIO = ('estudio', 'inicio_fijos', 'inicio_alineadores')

# El valor literal de BookedBy cuando la reserva vino del sitio (verificado en vivo).
BOOKED_WEB = 'agendado via web'


# ── Conexión y esquema ───────────────────────────────────────────────────────

def _conn():
    """Conexión nueva por llamada (mismo criterio que compras.py). WAL permite
    lecturas concurrentes con una escritura; el timeout evita 'database is locked'
    entre el hilo de cosecha y los requests del panel."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con


def init_db():
    """Crea el esquema si no existe. Idempotente — se llama en cada arranque."""
    con = _conn()
    try:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS citas (
            id_agenda    TEXT PRIMARY KEY,   -- clave de dedup en todo el proyecto
            fecha        TEXT NOT NULL,      -- YYYY-MM-DD
            hora         TEXT,               -- HH:MM
            duracion     INTEGER,            -- minutos AGENDADOS (la API no da hora de término)
            doctor       TEXT,               -- key interna: alberto|rodrigo|octavio|patricio|rx|''
            profesional  TEXT,               -- ProfessionalName crudo
            motivo       TEXT,               -- Reason crudo (la API nunca da IdReason)
            categoria    TEXT,               -- derivada por categoria_motivo()
            id_status    TEXT,               -- IdStatus numérico (SÍ viene, ver cabecera)
            estado       TEXT,               -- Status crudo
            estado_norm  TEXT,               -- derivada por estado_norm()
            rut          TEXT,               -- limpio: solo dígitos y K
            creada       TEXT,               -- CreateDate -> anticipación de la reserva
            agendada_por TEXT,               -- BookedBy ('Agendado via web' = sitio)
            fuente       TEXT,               -- api | historico
            visto        TEXT                -- ts de la última cosecha que la tocó
        );

        CREATE TABLE IF NOT EXISTS disponibilidad (
            fecha        TEXT NOT NULL,
            doctor       TEXT NOT NULL,
            min_libres   INTEGER NOT NULL DEFAULT 0,
            min_ocupados INTEGER NOT NULL DEFAULT 0,
            visto        TEXT,
            PRIMARY KEY (fecha, doctor)
        );

        CREATE TABLE IF NOT EXISTS ingresos (
            folio        TEXT PRIMARY KEY,   -- SII_FOLIO
            fecha        TEXT NOT NULL,
            rut          TEXT,
            monto        INTEGER NOT NULL DEFAULT 0,   -- negativo en notas de crédito
            tipo_doc     TEXT,
            descripcion  TEXT,
            doctor       TEXT,
            creado       TEXT
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            fecha   TEXT NOT NULL,
            clave   TEXT NOT NULL,
            valor   REAL,
            detalle TEXT,
            PRIMARY KEY (fecha, clave)
        );
        """)
        # Las migraciones van ANTES de los índices: en una base creada antes de una
        # columna nueva, un CREATE INDEX sobre ella aborta el executescript entero y
        # _migrar() nunca llega a correr. No se manifiesta en una base nueva.
        _migrar(con)
        con.executescript("""
        CREATE INDEX IF NOT EXISTS ix_citas_fecha     ON citas(fecha);
        CREATE INDEX IF NOT EXISTS ix_citas_rut       ON citas(rut);
        CREATE INDEX IF NOT EXISTS ix_citas_doctor    ON citas(doctor);
        CREATE INDEX IF NOT EXISTS ix_citas_fecha_doc ON citas(fecha, doctor);
        CREATE INDEX IF NOT EXISTS ix_citas_categoria ON citas(categoria);
        CREATE INDEX IF NOT EXISTS ix_citas_estado    ON citas(estado_norm);
        CREATE INDEX IF NOT EXISTS ix_ingresos_fecha  ON ingresos(fecha);
        CREATE INDEX IF NOT EXISTS ix_ingresos_rut    ON ingresos(rut);
        """)
        con.commit()
    finally:
        con.close()


def _migrar(con):
    """Migraciones idempotentes para bases ya creadas (CREATE TABLE IF NOT EXISTS no
    agrega columnas a una tabla que ya existe). Vacío por ahora — el esquema es nuevo.
    Las columnas futuras van acá, y sus índices en el bloque POSTERIOR de init_db().

        if 'columna_nueva' not in _cols('citas'):
            con.execute('ALTER TABLE citas ADD COLUMN columna_nueva TEXT')
    """
    def _cols(tabla):
        return {r['name'] for r in con.execute(f'PRAGMA table_info({tabla})')}
    return _cols


# ═══════════════════════════════════════════════════════════════════════════
# INGESTA — de la agenda de DentiDesk a la base local
# ═══════════════════════════════════════════════════════════════════════════

_CAMPOS = ('id_agenda', 'fecha', 'hora', 'duracion', 'doctor', 'profesional',
           'motivo', 'categoria', 'id_status', 'estado', 'estado_norm', 'rut',
           'creada', 'agendada_por', 'fuente', 'visto')

# Upsert por IdAgenda. La cláusula WHERE del DO UPDATE es la que impide que el import
# del export histórico PISE una fila que ya trajo la API: la API es estrictamente
# mejor (trae estado, IdStatus, BookedBy y CreateDate; el export no trae ninguno).
_SQL_UPSERT = """
INSERT INTO citas ({campos})
VALUES ({marcas})
ON CONFLICT(id_agenda) DO UPDATE SET
    fecha=excluded.fecha, hora=excluded.hora, duracion=excluded.duracion,
    doctor=excluded.doctor, profesional=excluded.profesional,
    motivo=excluded.motivo, categoria=excluded.categoria,
    id_status=excluded.id_status, estado=excluded.estado,
    estado_norm=excluded.estado_norm, rut=excluded.rut,
    creada=excluded.creada, agendada_por=excluded.agendada_por,
    fuente=excluded.fuente, visto=excluded.visto
WHERE excluded.fuente = 'api' OR citas.fuente = 'historico'
""".format(campos=','.join(_CAMPOS), marcas=','.join('?' * len(_CAMPOS)))


def _fila(c, cfg, fuente, visto):
    """Una cita cruda de getAgendaDay -> tupla lista para el upsert."""
    from scheduling import limpiar_rut
    motivo = (c.get('Reason') or '').strip()
    estado_txt = (c.get('Status') or '').strip()
    prof = (c.get('ProfessionalName') or '').strip()
    try:
        dur = int(c.get('duration') or 0)
    except (TypeError, ValueError):
        dur = 0
    return (
        str(c.get('IdAgenda') or ''),
        (c.get('Date') or '')[:10],
        (c.get('time') or '')[:5],
        dur,
        doc_key(prof, cfg),
        prof,
        motivo,
        categoria_motivo(motivo, cfg),
        str(c.get('IdStatus') or ''),
        estado_txt,
        estado_norm(c.get('IdStatus'), estado_txt),
        limpiar_rut(str(c.get('PatientDocument') or '')),
        (c.get('CreateDate') or '')[:19],
        (c.get('BookedBy') or '').strip(),
        fuente,
        visto,
    )


def guardar_citas(citas, cfg=None, fuente='api'):
    """Upsert de una lista de citas crudas. Devuelve cuántas se escribieron.
    Las filas sin IdAgenda o sin fecha se descartan: no hay con qué deduplicarlas."""
    cfg = cfg or {}
    visto = ahora_cl().isoformat(timespec='seconds')
    filas = [f for f in (_fila(c, cfg, fuente, visto) for c in citas) if f[0] and f[1]]
    if not filas:
        return 0
    con = _conn()
    try:
        con.executemany(_SQL_UPSERT, filas)
        con.commit()
    finally:
        con.close()
    return len(filas)


def _dias_habiles(desde, hasta):
    """Días L-V en [desde, hasta]. La clínica atiende L-V (scheduling_config) y todos
    los barridos del proyecto usan el mismo criterio."""
    out, d = [], desde
    while d <= hasta:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _barrer_dias(dias, cfg, max_workers=6, pausa=0.0):
    """Trae la agenda de varios días en paralelo. Devuelve (citas, dias_ok, dias_error).
    Un día que falla NO aborta el barrido: se cuenta aparte y se sigue (mismo criterio
    que control_dental.barrer y reporte_semanal._barrido_clinico). Se devuelve el
    conteo de errores en vez de tragarlos, para que el que llama sepa si la pasada
    quedó incompleta."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    import dentidesk

    citas, ok, err = [], 0, 0

    def scan(d):
        try:
            return dentidesk._get_agenda_day(cfg, d, force=True) or []
        except Exception as e:
            log.warning('kpi: fallo al leer la agenda del %s: %r', d, e)
            return None

    # En lotes, para poder pausar entre ellos sin serializar el barrido completo.
    lote = max(1, max_workers * 4)
    for i in range(0, len(dias), lote):
        trozo = dias[i:i + lote]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for res in pool.map(scan, trozo):
                if res is None:
                    err += 1
                else:
                    ok += 1
                    citas.extend(res)
        if pausa and i + lote < len(dias):
            time.sleep(pausa)
    return citas, ok, err


def cosechar(cfg=None, dias_atras=30, dias_adelante=45, max_workers=6):
    """Refresca la ventana móvil de la agenda. Los `dias_atras` NO son redundancia: el
    estado de una cita cambia DESPUÉS de la visita (la clínica marca 'Atendido' más
    tarde — la misma trampa que documenta control_dental). Los `dias_adelante` traen
    la agenda ya comprometida, que es lo que permite ver la ocupación futura."""
    cfg = cfg or _scheduling_cfg()
    if not cfg['dentidesk']['enabled']:
        return {'ok': False, 'motivo': 'dentidesk deshabilitado'}
    hoy = fechas.hoy_chile()
    dias = _dias_habiles(hoy - timedelta(days=dias_atras), hoy + timedelta(days=dias_adelante))
    citas, ok, err = _barrer_dias(dias, cfg, max_workers=max_workers)
    n = guardar_citas(citas, cfg, fuente='api')
    _snapshot('kpi_cosecha', n, f'{ok} dias ok, {err} con error')
    return {'ok': True, 'dias': len(dias), 'dias_ok': ok, 'dias_error': err,
            'citas': len(citas), 'guardadas': n}


def backfill(desde, hasta, cfg=None, max_workers=4, pausa=0.5, progreso=None):
    """Reconstruye la historia barriendo getAgendaDay hacia atrás. Se corre UNA vez.

    Es REANUDABLE: guarda el avance en `snapshots` después de cada mes, así un
    redeploy de Render (o un corte) no obliga a empezar de cero. Va paceado a
    propósito (`pausa` entre lotes) — son miles de requests contra la app de la
    clínica y no hay ninguna prisa: se corre una vez, fuera de horario de atención.
    """
    cfg = cfg or _scheduling_cfg()
    if not cfg['dentidesk']['enabled']:
        return {'ok': False, 'motivo': 'dentidesk deshabilitado'}

    total_dias = total_citas = total_err = 0
    # Mes a mes, para poder guardar el avance y poder reanudar.
    cursor = date(desde.year, desde.month, 1)
    while cursor <= hasta:
        fin_mes = _fin_de_mes(cursor)
        dias = _dias_habiles(max(cursor, desde), min(fin_mes, hasta))
        if dias:
            citas, ok, err = _barrer_dias(dias, cfg, max_workers=max_workers, pausa=pausa)
            guardar_citas(citas, cfg, fuente='api')
            total_dias += ok
            total_citas += len(citas)
            total_err += err
            _snapshot('kpi_backfill_avance', total_citas,
                      f'hasta {min(fin_mes, hasta).isoformat()}: '
                      f'{total_dias} dias, {total_err} errores')
            if progreso:
                progreso(min(fin_mes, hasta), len(citas), err)
            log.warning('kpi.backfill %s: %s citas (%s dias, %s errores)',
                        cursor.strftime('%Y-%m'), len(citas), ok, err)
        cursor = fin_mes + timedelta(days=1)

    _snapshot('kpi_backfill_fin', total_citas,
              f'{desde.isoformat()} a {hasta.isoformat()}: '
              f'{total_dias} dias, {total_err} errores')
    return {'ok': True, 'dias': total_dias, 'citas': total_citas, 'dias_error': total_err,
            'desde': desde.isoformat(), 'hasta': hasta.isoformat()}


def _fin_de_mes(d):
    import calendar
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def capturar_disponibilidad(cfg=None, dias=15, max_workers=4):
    """Guarda los minutos libres y ocupados de cada doctor para los próximos días
    hábiles.

    ⚠️ Esto HAY que capturarlo día a día: es el único denominador real para un % de
    ocupación, y `getAvailableHours` solo responde por días FUTUROS. Lo que no se
    guarde hoy no se puede reconstruir mañana — por eso va en el barrido diario y no
    se calcula al momento de consultar el panel.

    Reutiliza `dentidesk.bloques_libres_15` / `bloques_ocupados`, que ya descuentan
    bloqueos, feriados, vacaciones y el horario real del doctor."""
    from concurrent.futures import ThreadPoolExecutor
    import dentidesk

    cfg = cfg or _scheduling_cfg()
    if not cfg['dentidesk']['enabled']:
        return {'ok': False, 'motivo': 'dentidesk deshabilitado'}

    hoy = fechas.hoy_chile()
    fechas_obj = _dias_habiles(hoy, hoy + timedelta(days=dias))
    doctores = [k for k, d in (cfg.get('doctores') or {}).items()
                if isinstance(d, dict) and d.get('atiende')]
    pares = [(d, f) for d in doctores for f in fechas_obj]
    visto = ahora_cl().isoformat(timespec='seconds')

    def uno(par):
        doc, f = par
        try:
            libres = dentidesk.bloques_libres_15(cfg, doc, f)
            ocupados = dentidesk.bloques_ocupados(cfg, doc, f)
            return (f.isoformat(), doc, len(libres) * 15, len(ocupados) * 15, visto)
        except Exception as e:
            log.warning('kpi.capturar_disponibilidad %s %s: %r', doc, f, e)
            return None

    filas = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for r in pool.map(uno, pares):
            if r:
                filas.append(r)

    if filas:
        con = _conn()
        try:
            con.executemany(
                'INSERT INTO disponibilidad (fecha, doctor, min_libres, min_ocupados, visto) '
                'VALUES (?,?,?,?,?) ON CONFLICT(fecha, doctor) DO UPDATE SET '
                'min_libres=excluded.min_libres, min_ocupados=excluded.min_ocupados, '
                'visto=excluded.visto', filas)
            con.commit()
        finally:
            con.close()
    return {'ok': True, 'pares': len(pares), 'guardados': len(filas)}


# ── Snapshots: valores que no se pueden recalcular hacia atrás ───────────────

def _snapshot(clave, valor, detalle='', fecha=None):
    """Deja un valor fechado. Sirve para el avance del backfill, la calidad de datos y
    las métricas no reconstruibles (un conteo de 'hoy' no se puede volver a calcular
    mañana)."""
    con = _conn()
    try:
        con.execute('INSERT INTO snapshots (fecha, clave, valor, detalle) VALUES (?,?,?,?) '
                    'ON CONFLICT(fecha, clave) DO UPDATE SET valor=excluded.valor, '
                    'detalle=excluded.detalle',
                    ((fecha or fechas.hoy_chile()).isoformat(), clave, valor, detalle))
        con.commit()
    finally:
        con.close()


def snapshot(clave, limite=60):
    """Serie histórica de un snapshot, del más reciente al más antiguo."""
    con = _conn()
    try:
        return [dict(r) for r in con.execute(
            'SELECT fecha, valor, detalle FROM snapshots WHERE clave=? '
            'ORDER BY fecha DESC LIMIT ?', (clave, limite))]
    finally:
        con.close()


def _scheduling_cfg():
    """Import perezoso para evitar ciclos (mismo patrón que control_dental y
    seguimiento_pc)."""
    import scheduling
    return scheduling.load_config()


# ── Reclasificación y calidad de datos ───────────────────────────────────────

def reclasificar(cfg=None):
    """Recalcula `categoria`, `estado_norm` y `doctor` desde los campos CRUDOS que ya
    están guardados (motivo, id_status, estado, profesional). Cero red.

    Esta función es la razón por la que la base guarda el dato crudo además del
    derivado: los mapas de este módulo van a quedar cortos (DentiDesk tiene estados y
    motivos que no aparecieron en el sondeo, y la clínica puede crear más). Cuando eso
    pase, se corrige la constante y se corre esto — NO hay que volver a barrer 5 años
    de agenda. Sin esta salida, un mapa incompleto quedaría fosilizado en la base."""
    cfg = cfg or {}
    con = _conn()
    try:
        filas = con.execute(
            'SELECT id_agenda, motivo, id_status, estado, profesional FROM citas').fetchall()
        cambios = [
            (categoria_motivo(r['motivo'], cfg),
             estado_norm(r['id_status'], r['estado']),
             doc_key(r['profesional'], cfg),
             r['id_agenda'])
            for r in filas
        ]
        con.executemany(
            'UPDATE citas SET categoria=?, estado_norm=?, doctor=? WHERE id_agenda=?',
            cambios)
        con.commit()
    finally:
        con.close()
    return {'ok': True, 'revisadas': len(cambios)}


# ── Helpers de consulta ──────────────────────────────────────────────────────

def _iso(d):
    """date | 'YYYY-MM-DD' -> 'YYYY-MM-DD'. Acepta ambos para que los endpoints
    puedan pasar el query param tal cual."""
    return d.isoformat() if hasattr(d, 'isoformat') else str(d or '')[:10]


def _rango(desde=None, hasta=None, doctor=None):
    """(where_sql, params) para filtrar `citas`. Siempre arranca con 'WHERE 1=1' para
    que el que llama pueda concatenar ' AND ...' sin ramas condicionales."""
    cond, p = ['1=1'], []
    if desde:
        cond.append('fecha >= ?')
        p.append(_iso(desde))
    if hasta:
        cond.append('fecha <= ?')
        p.append(_iso(hasta))
    if doctor:
        cond.append('doctor = ?')
        p.append(doctor)
    return 'WHERE ' + ' AND '.join(cond), p


def _pct(n, total, dec=1):
    """Porcentaje, o None si no hay base. None y 0 son cosas distintas: '0% de
    inasistencia' es un dato, 'no hubo citas' no lo es, y el panel los pinta
    distinto."""
    return round(n / total * 100, dec) if total else None


def _lista(con, sql, p=()):
    return [dict(r) for r in con.execute(sql, p)]


def calidad_datos(desde=None, hasta=None):
    """Lo que el panel tiene que declarar para que sus números se lean bien.

    `sin_motivo` es el número importante: DentiDesk permite agendar sin elegir motivo,
    y en el sondeo eso fue el 19% de las citas (coincide con el ~17% del export
    histórico). Toda métrica que dependa del motivo —la conversión, sobre todo— es un
    PISO, no un valor exacto, y esa cota se calcula acá en vez de asumirse.

    Los `*_sin_clasificar` son la lista de trabajo: cada motivo o estado que aparezca
    ahí se agrega a las constantes del módulo y se corre `reclasificar()`."""
    w, p = _rango(desde, hasta)
    con = _conn()
    try:
        def cuenta(extra):
            return con.execute(f'SELECT COUNT(*) FROM citas {w} AND {extra}', p).fetchone()[0]
        total = con.execute(f'SELECT COUNT(*) FROM citas {w}', p).fetchone()[0]
        sin_motivo = cuenta("TRIM(COALESCE(motivo,''))=''")
        sin_rut = cuenta("TRIM(COALESCE(rut,''))=''")
        sin_doctor = cuenta("TRIM(COALESCE(doctor,''))=''")
        motivos_otro = _lista(con, f"SELECT motivo, COUNT(*) n FROM citas {w} "
                                   f"AND categoria='otro' GROUP BY 1 ORDER BY n DESC LIMIT 40", p)
        estados_otro = _lista(con, f"SELECT id_status, estado, COUNT(*) n FROM citas {w} "
                                   f"AND estado_norm='otro' GROUP BY 1,2 ORDER BY n DESC LIMIT 40", p)
        rango = con.execute('SELECT MIN(fecha), MAX(fecha) FROM citas').fetchone()
    finally:
        con.close()
    return {
        'total': total,
        'sin_motivo': sin_motivo,
        'sin_motivo_pct': _pct(sin_motivo, total),
        'sin_rut': sin_rut,
        'sin_doctor': sin_doctor,
        'motivos_sin_clasificar': motivos_otro,
        'estados_sin_clasificar': estados_otro,
        'rango': {'desde': rango[0], 'hasta': rango[1]},
    }


# ═══════════════════════════════════════════════════════════════════════════
# CONSULTAS — los KPIs. Cero red: todo sale de la base local.
# ═══════════════════════════════════════════════════════════════════════════

# SQL reutilizable: las citas que "cuentan" (ocupan un bloque y el paciente tiene hora).
# Deja fuera canceladas, no-shows, reagendadas y 'no seguir'.
_SQL_CUENTA = "estado_norm IN ({})".format(','.join(f"'{e}'" for e in ESTADOS_VIGENTE))
_SQL_OCURRIO = "estado_norm IN ({})".format(','.join(f"'{e}'" for e in ESTADOS_OCURRIO))


def pacientes_nuevos(desde=None, hasta=None, doctor=None):
    """Pacientes cuya PRIMERA cita de toda la base cae en el rango.

    ⚠️ TRUNCAMIENTO A LA IZQUIERDA: la base empieza donde llegó el backfill. Un
    paciente que ya estaba en tratamiento antes aparece con una "primera visita"
    artificial en el primer mes disponible. Por eso `serie_pacientes_nuevos` descarta
    los primeros meses, y por eso este número no se debe leer para el arranque de la
    serie. Mismo criterio que los scripts de ortodonciarichard-analytics."""
    cond, p = ['1=1'], []
    if desde:
        cond.append('pr.f0 >= ?')
        p.append(_iso(desde))
    if hasta:
        cond.append('pr.f0 <= ?')
        p.append(_iso(hasta))
    if doctor:
        # El doctor de la PRIMERA cita: a quién entró ese paciente.
        cond.append('pr.doctor = ?')
        p.append(doctor)
    con = _conn()
    try:
        # La primera cita de cada RUT se calcula sobre TODA la base (sin filtro de
        # rango) y recién después se pregunta si esa fecha cae dentro. Filtrar antes
        # daría "nuevo" a cualquiera que simplemente no vino el período anterior.
        # MIN(fecha) y el doctor de ese día se resuelven en una pasada con la forma
        # `MIN(fecha), doctor` de SQLite, que devuelve el doctor de la fila mínima.
        return con.execute(f"""
            WITH pr AS (
                SELECT rut, MIN(fecha) f0, doctor FROM citas
                WHERE rut <> '' AND {_SQL_CUENTA}
                GROUP BY rut
            )
            SELECT COUNT(*) FROM pr WHERE {' AND '.join(cond)}
        """, p).fetchone()[0]
    finally:
        con.close()


def destino_primeras_consultas(desde=None, hasta=None, doctor=None, ventana_dias=90):
    """★ Qué pasó con cada Primera Consulta. Reparto en 3 destinos + los indeterminados.

    Esto NO es una tasa de conversión binaria, y esa es justamente la corrección que
    pidió el usuario (2026-08-21):

        "hay pacientes que tienen primera consulta, pero uno no indica el estudio, sino
         que puede indicar controlar u otra cosa, y ese no es un paciente perdido. Pero
         el que tuvo primera consulta y nunca más vino, ese sí es un paciente perdido."

    Ninguna métrica del proyecto medía eso:
      · analisis_conversion_pc.py mide binario (convirtió a estudio/inicio: 39,2%), así
        que su "no convirtió" mezcla al paciente en observación con el perdido.
      · seguimiento_pc.es_avance() hace lo contrario: cuenta CUALQUIER control como
        avance, así que su "pendiente" tampoco aísla al perdido.

    Los cuatro destinos:
      inicio      — llegó al estudio o a instalación de aparatos. Es la definición del
                    script histórico, mantenida a propósito para que el 39,2% siga
                    siendo comparable.
      siguio      — volvió, pero a otra cosa (control de evolución, urgencia, segunda
                    consulta). NO es fuga: el doctor indicó observar, no tratar.
      perdido     — CERO citas posteriores. El número que faltaba.
      en_ventana  — la consulta es demasiado reciente para juzgarla. Se informa aparte y
                    NO se cuenta como perdido: llamar perdido a alguien que consultó
                    hace dos semanas infla la fuga y hace perder credibilidad al panel.

    ⚠️ 'perdido' mira TODA la historia posterior, no la ventana de 90 días: "nunca más
    vino" no tiene ventana. La ventana solo decide si un regreso cuenta como `inicio`
    para efectos de la tasa comparable con la línea base.
    """
    hoy = fechas.hoy_chile()
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        pcs = con.execute(
            f"SELECT id_agenda, rut, fecha, doctor FROM citas {w} "
            f"AND categoria='primera_consulta' AND {_SQL_OCURRIO} AND rut<>'' "
            f"ORDER BY fecha", p).fetchall()
        # Todas las citas que cuentan, agrupadas por RUT. Se cargan en memoria a
        # propósito: son decenas de miles de filas y el cruce en Python es mucho más
        # legible que una subconsulta correlacionada por cada primera consulta.
        porrut = {}
        for r in con.execute(
                f"SELECT rut, fecha, categoria, id_agenda FROM citas "
                f"WHERE rut<>'' AND {_SQL_CUENTA}"):
            porrut.setdefault(r['rut'], []).append((r['fecha'], r['categoria'], r['id_agenda']))
    finally:
        con.close()

    destinos = {'inicio': 0, 'siguio': 0, 'perdido': 0, 'en_ventana': 0}
    conv90 = conv90_base = 0
    dias_hasta = []
    por_mes, por_doc = {}, {}
    perdidos = []
    vistas = set()

    for pc in pcs:
        # Un mismo paciente el mismo día no cuenta dos veces (doble agenda).
        if (pc['rut'], pc['fecha']) in vistas:
            continue
        vistas.add((pc['rut'], pc['fecha']))

        f0 = pc['fecha']
        limite = (date.fromisoformat(f0) + timedelta(days=ventana_dias)).isoformat()
        posteriores = [x for x in porrut.get(pc['rut'], [])
                       if x[0] >= f0 and x[2] != pc['id_agenda']]

        inicio_alguna = [x for x in posteriores if x[1] in CATEGORIAS_INICIO]
        inicio_en_ventana = [x for x in inicio_alguna if x[0] <= limite]
        reciente = (date.fromisoformat(f0) + timedelta(days=ventana_dias)) > hoy

        if inicio_alguna:
            destino = 'inicio'
            d = (date.fromisoformat(min(x[0] for x in inicio_alguna))
                 - date.fromisoformat(f0)).days
            dias_hasta.append(d)
        elif reciente:
            destino = 'en_ventana'
        elif posteriores:
            destino = 'siguio'
        else:
            destino = 'perdido'
            perdidos.append({'rut': pc['rut'], 'fecha': f0, 'doctor': pc['doctor']})

        destinos[destino] += 1

        # Tasa comparable con la línea base histórica (39,2%): ventana estricta de 90
        # días y solo las consultas que ya completaron la ventana.
        if not reciente:
            conv90_base += 1
            if inicio_en_ventana:
                conv90 += 1

        mes = f0[:7]
        por_mes.setdefault(mes, {'mes': mes, 'total': 0, 'inicio': 0, 'siguio': 0,
                                 'perdido': 0, 'en_ventana': 0})
        por_mes[mes]['total'] += 1
        por_mes[mes][destino] += 1

        dk = pc['doctor'] or '—'
        por_doc.setdefault(dk, {'doctor': dk, 'total': 0, 'inicio': 0, 'siguio': 0,
                                'perdido': 0, 'en_ventana': 0})
        por_doc[dk]['total'] += 1
        por_doc[dk][destino] += 1

    total = sum(destinos.values())
    # El denominador de los porcentajes EXCLUYE los indeterminados: mezclarlos hace
    # que la fuga parezca bajar solo porque hubo consultas recientes.
    base = total - destinos['en_ventana']
    for fila in list(por_mes.values()) + list(por_doc.values()):
        b = fila['total'] - fila['en_ventana']
        fila['pct_inicio'] = _pct(fila['inicio'], b)
        fila['pct_perdido'] = _pct(fila['perdido'], b)

    dias_hasta.sort()
    return {
        'total': total,
        'base_clasificada': base,
        'destinos': destinos,
        'pct': {k: _pct(v, base) for k, v in destinos.items() if k != 'en_ventana'},
        'conversion_90d': _pct(conv90, conv90_base),
        'conversion_90d_base': conv90_base,
        'ventana_dias': ventana_dias,
        'dias_hasta_inicio': _percentiles(dias_hasta),
        'serie': sorted(por_mes.values(), key=lambda x: x['mes']),
        'por_doctor': sorted(por_doc.values(), key=lambda x: -x['total']),
        'perdidos': perdidos,
    }


def _percentiles(xs):
    """mediana / p25 / p75 sin numpy (no es dependencia del backend)."""
    if not xs:
        return {'n': 0, 'mediana': None, 'p25': None, 'p75': None}
    def q(f):
        return xs[min(len(xs) - 1, int(len(xs) * f))]
    return {'n': len(xs), 'mediana': q(.5), 'p25': q(.25), 'p75': q(.75)}


# ── Ocupación y capacidad ────────────────────────────────────────────────────

def ocupacion(desde=None, hasta=None, doctor=None):
    """Capacidad usada, por doctor, sobre citas YA OCURRIDAS.

    ⚠️ Devuelve HORAS ABSOLUTAS por día trabajado, NO un porcentaje, y es una decisión
    deliberada. Un % necesita un denominador de "horas disponibles" y para un día que
    ya pasó ese dato no existe: `getAvailableHours` solo responde por días futuros, y
    la jornada del config (09:00-19:30 L-V) no es la real — Octavio trabaja ~140 días
    al año, no 250. Inventar el denominador daría un porcentaje que se ve preciso y
    está mal. Las horas por día trabajado se comparan entre doctores y contra el
    propio historial, que es lo que se necesita para decidir.
    El porcentaje REAL existe, pero solo hacia adelante: ver `ocupacion_futura()`.

    'Día trabajado' = (doctor, fecha) con al menos una cita que ocurrió.
    Los minutos son los AGENDADOS: la API no entrega hora de término."""
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        filas = _lista(con, f"""
            SELECT doctor,
                   COUNT(DISTINCT fecha)        dias_trabajados,
                   COUNT(*)                     atenciones,
                   SUM(COALESCE(duracion,0))    minutos
            FROM citas {w} AND {_SQL_OCURRIO} AND doctor <> ''
            GROUP BY doctor ORDER BY minutos DESC
        """, p)
    finally:
        con.close()
    for f in filas:
        d = f['dias_trabajados'] or 0
        f['horas'] = round((f['minutos'] or 0) / 60, 1)
        f['horas_por_dia'] = round((f['minutos'] or 0) / 60 / d, 2) if d else None
        f['atenciones_por_dia'] = round(f['atenciones'] / d, 1) if d else None
    return {'por_doctor': filas, 'nota': 'horas de sillón AGENDADAS por día trabajado; '
                                         'no es un % de ocupación (ver ocupacion_futura)'}


def ocupacion_futura(dias=15, doctor=None):
    """% de ocupación REAL de los próximos días, desde la tabla `disponibilidad`.

    Acá el porcentaje sí es honesto: el denominador lo da DentiDesk vía
    `bloques_libres_15`, que ya descuenta bloqueos, feriados, vacaciones y el horario
    real de cada doctor. Es además el único bloque directamente accionable del panel:
    dice cuántas horas hay que llenar esta semana."""
    hoy = fechas.hoy_chile()
    hasta = (hoy + timedelta(days=dias)).isoformat()
    cond, p = ['fecha >= ?', 'fecha <= ?'], [hoy.isoformat(), hasta]
    if doctor:
        cond.append('doctor = ?')
        p.append(doctor)
    con = _conn()
    try:
        filas = _lista(con, f"""
            SELECT doctor, SUM(min_libres) libres, SUM(min_ocupados) ocupados
            FROM disponibilidad WHERE {' AND '.join(cond)}
            GROUP BY doctor ORDER BY libres DESC
        """, p)
    finally:
        con.close()
    for f in filas:
        cap = (f['libres'] or 0) + (f['ocupados'] or 0)
        f['horas_libres'] = round((f['libres'] or 0) / 60, 1)
        f['horas_ocupadas'] = round((f['ocupados'] or 0) / 60, 1)
        f['pct_ocupacion'] = _pct(f['ocupados'] or 0, cap)
    return {'desde': hoy.isoformat(), 'hasta': hasta, 'por_doctor': filas}


def heatmap(desde=None, hasta=None, doctor=None):
    """Minutos vendidos por día de la semana × hora. Es el mapa de los huecos: el
    informe de julio ya identificó 8-9h (solo Vial), 13-14h (almuerzo), 19h (casi solo
    Rodrigo) y el lunes flojo. Esto lo mantiene vivo en vez de congelado."""
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        filas = _lista(con, f"""
            SELECT CAST(strftime('%w', fecha) AS INTEGER) dow,
                   CAST(substr(hora,1,2) AS INTEGER)      hora,
                   COUNT(*) atenciones, SUM(COALESCE(duracion,0)) minutos
            FROM citas {w} AND {_SQL_OCURRIO} AND hora <> ''
            GROUP BY 1,2 ORDER BY 1,2
        """, p)
    finally:
        con.close()
    # strftime('%w'): 0=domingo. Se reindexa a 0=lunes, que es como se lee una agenda.
    for f in filas:
        f['dow'] = (f['dow'] - 1) % 7
    return {'celdas': filas,
            'dias': ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom']}


# ── Fugas de agenda ──────────────────────────────────────────────────────────

def fugas(desde=None, hasta=None, doctor=None):
    """Inasistencia, cancelación y reagenda. Solo sobre citas cuya fecha YA PASÓ: una
    cita de mañana todavía no puede ser un no-show.

    ⚠️⚠️ EL DATO QUE HAY QUE MIRAR ES `tasa_no_ocurrio`, NO `tasa_inasistencia`.
    En el primer semestre de 2023 la clínica CAMBIÓ cómo etiqueta una cita que no se
    cumple. Medido sobre los 5 años del backfill:

        semestre    no llega   reagendada   cancelada     suma    % del total
        2022-S1        141         538         442        1121       21,0%
        2023-S1         14        1050          94        1158       20,1%

    La inasistencia "cayó" de 2,9% a 0,2% y las cancelaciones se desplomaron, pero la
    suma se quedó clavada en ~21%: lo que pasó es que ahora casi todo se marca
    'Re-agendado'. Leer la caída de la inasistencia como una mejora sería un error
    grave —y muy fácil de cometer, porque además coincide con la época en que se
    encendieron los recordatorios de WhatsApp. `tasa_no_ocurrio` suma las tres y es
    robusta al cambio de criterio, así que es la única comparable a través de 2023.

    ⚠️ LA TASA DE CONFIRMACIÓN NO SE PUEDE MEDIR HACIA ATRÁS, y conviene entender por
    qué antes de pedirla: DentiDesk guarda UN solo campo de estado, así que cuando la
    cita se marca 'Atendido' se PISA el 'Confirmado por WhatsApp' que tenía antes. En
    una cita pasada, `estado_norm='confirmada'` no significa "confirmó": significa
    "confirmó y nadie la marcó atendida después". Lo medible es
    `tasa_confirmacion_vigente()`, sobre las citas que aún no ocurren.
    """
    hoy = fechas.hoy_chile().isoformat()
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        r = dict(con.execute(f"""
            SELECT
              COUNT(*)                                                          agendadas,
              SUM(CASE WHEN {_SQL_OCURRIO} THEN 1 ELSE 0 END)                   ocurrieron,
              SUM(CASE WHEN estado_norm='no_llega'  THEN 1 ELSE 0 END)          no_llega,
              SUM(CASE WHEN estado_norm='cancelada' THEN 1 ELSE 0 END)          canceladas,
              SUM(CASE WHEN estado_norm='reagendada' THEN 1 ELSE 0 END)         reagendadas,
              SUM(CASE WHEN estado_norm='quiere_reagendar' THEN 1 ELSE 0 END)   quiere_reagendar,
              SUM(CASE WHEN estado_norm='no_llega' THEN COALESCE(duracion,0) ELSE 0 END) min_no_llega,
              SUM(CASE WHEN estado_norm='cancelada' THEN COALESCE(duracion,0) ELSE 0 END) min_cancel
            FROM citas {w} AND fecha < ?
        """, p + [hoy]).fetchone())
    finally:
        con.close()
    esperadas = (r['ocurrieron'] or 0) + (r['no_llega'] or 0)
    r['tasa_inasistencia'] = _pct(r['no_llega'] or 0, esperadas)
    r['tasa_cancelacion'] = _pct(r['canceladas'] or 0, r['agendadas'] or 0)
    r['tasa_reagenda'] = _pct(r['reagendadas'] or 0, r['agendadas'] or 0)
    r['horas_perdidas_no_llega'] = round((r['min_no_llega'] or 0) / 60, 1)
    r['horas_perdidas_cancelacion'] = round((r['min_cancel'] or 0) / 60, 1)
    r['base_inasistencia'] = esperadas
    # La métrica robusta: no depende de CÓMO se etiquetó la cita que no se cumplió.
    # Es la única comparable a través del cambio de criterio de 2023 (ver el docstring).
    r['no_ocurrieron'] = ((r['no_llega'] or 0) + (r['canceladas'] or 0)
                          + (r['reagendadas'] or 0))
    r['tasa_no_ocurrio'] = _pct(r['no_ocurrieron'], r['agendadas'] or 0)
    return r


def tasa_confirmacion_vigente(doctor=None):
    """% de citas AÚN NO OCURRIDAS que ya están confirmadas. Es la única forma honesta
    de medir confirmación (ver la nota en `fugas`). Se lee como una foto de hoy, no
    como una serie — por eso el barrido la guarda además como snapshot diario."""
    hoy = fechas.hoy_chile().isoformat()
    cond, p = ['fecha >= ?'], [hoy]
    if doctor:
        cond.append('doctor = ?')
        p.append(doctor)
    con = _conn()
    try:
        r = con.execute(f"""
            SELECT COUNT(*) total,
                   SUM(CASE WHEN estado_norm='confirmada' THEN 1 ELSE 0 END) confirmadas
            FROM citas WHERE {' AND '.join(cond)} AND {_SQL_CUENTA}
        """, p).fetchone()
    finally:
        con.close()
    return {'proximas': r['total'], 'confirmadas': r['confirmadas'] or 0,
            'pct': _pct(r['confirmadas'] or 0, r['total'])}


# ── Cartera ──────────────────────────────────────────────────────────────────

def cartera(desde=None, hasta=None, doctor=None, dias_activo=90):
    """Inicios vs altas (el flujo neto) y tamaño de la cartera activa.

    El flujo neto es el indicador que el informe de julio no tenía y que más
    directamente explica hacia dónde va la clínica: si las altas superan a los inicios,
    la cartera se está vaciando aunque el volumen de atenciones se vea estable —
    exactamente el cuadro de "clínica de terminaciones y contenciones" que describía
    ese informe."""
    w, p = _rango(desde, hasta, doctor)
    hoy = fechas.hoy_chile()
    corte = (hoy - timedelta(days=dias_activo)).isoformat()
    con = _conn()
    try:
        serie = _lista(con, f"""
            SELECT substr(fecha,1,7) mes,
              SUM(CASE WHEN categoria IN ('inicio_fijos','inicio_alineadores') THEN 1 ELSE 0 END) inicios,
              SUM(CASE WHEN categoria='fin_definitivo' THEN 1 ELSE 0 END) altas
            FROM citas {w} AND {_SQL_OCURRIO}
            GROUP BY 1 ORDER BY 1
        """, p)
        cond_act, pa = ['fecha >= ?', 'fecha <= ?', "rut <> ''"], [corte, hoy.isoformat()]
        if doctor:
            cond_act.append('doctor = ?')
            pa.append(doctor)
        activos = con.execute(
            f"SELECT COUNT(DISTINCT rut) FROM citas WHERE {' AND '.join(cond_act)} "
            f"AND {_SQL_OCURRIO}", pa).fetchone()[0]
    finally:
        con.close()
    for m in serie:
        m['neto'] = (m['inicios'] or 0) - (m['altas'] or 0)
    tot_i = sum(m['inicios'] or 0 for m in serie)
    tot_a = sum(m['altas'] or 0 for m in serie)
    return {'inicios': tot_i, 'altas': tot_a, 'neto': tot_i - tot_a,
            'activos': activos, 'dias_activo': dias_activo, 'serie': serie}


# ── Serie mensual: todos los conteos en una sola pasada ──────────────────────

def serie_mensual(desde=None, hasta=None, doctor=None):
    """Una fila por mes con todo lo que el panel puede graficar. Una sola consulta en
    vez de una por métrica: el panel elige qué pintar."""
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        serie = _lista(con, f"""
            SELECT substr(fecha,1,7) mes,
              COUNT(*)                                                        agendadas,
              SUM(CASE WHEN {_SQL_OCURRIO} THEN 1 ELSE 0 END)                 atendidos,
              SUM(CASE WHEN estado_norm='no_llega'   THEN 1 ELSE 0 END)       no_llega,
              SUM(CASE WHEN estado_norm='cancelada'  THEN 1 ELSE 0 END)       canceladas,
              SUM(CASE WHEN estado_norm='reagendada' THEN 1 ELSE 0 END)       reagendadas,
              SUM(CASE WHEN categoria='primera_consulta' AND {_SQL_OCURRIO}
                       THEN 1 ELSE 0 END)                                     primeras_consultas,
              SUM(CASE WHEN categoria IN ('inicio_fijos','inicio_alineadores')
                       AND {_SQL_OCURRIO} THEN 1 ELSE 0 END)                  inicios,
              SUM(CASE WHEN categoria='fin_definitivo' AND {_SQL_OCURRIO}
                       THEN 1 ELSE 0 END)                                     altas,
              SUM(CASE WHEN {_SQL_OCURRIO} THEN COALESCE(duracion,0) ELSE 0 END) minutos,
              COUNT(DISTINCT CASE WHEN {_SQL_OCURRIO} THEN fecha END)         dias
            FROM citas {w}
            GROUP BY 1 ORDER BY 1
        """, p)
    finally:
        con.close()
    for m in serie:
        esperadas = (m['atendidos'] or 0) + (m['no_llega'] or 0)
        m['tasa_inasistencia'] = _pct(m['no_llega'] or 0, esperadas)
        # La serie comparable a través del cambio de etiquetado de 2023 (ver `fugas`).
        m['no_ocurrieron'] = ((m['no_llega'] or 0) + (m['canceladas'] or 0)
                              + (m['reagendadas'] or 0))
        m['tasa_no_ocurrio'] = _pct(m['no_ocurrieron'], m['agendadas'] or 0)
        m['horas'] = round((m['minutos'] or 0) / 60, 1)
        m['neto_cartera'] = (m['inicios'] or 0) - (m['altas'] or 0)
    return serie


# ── Origen de la reserva ─────────────────────────────────────────────────────

def origen_reservas(desde=None, hasta=None, doctor=None):
    """Reservas hechas por el sitio vs por mesón/teléfono.

    Sale del campo `BookedBy` de DentiDesk, que trae el literal 'Agendado via web' —
    hallazgo del sondeo del 2026-08-21. No hace falta cruzar con `agendamientos.jsonl`
    (que además solo existe desde julio-2026); esto cubre toda la historia."""
    w, p = _rango(desde, hasta, doctor)
    con = _conn()
    try:
        total = con.execute(f'SELECT COUNT(*) FROM citas {w}', p).fetchone()[0]
        web = con.execute(f'SELECT COUNT(*) FROM citas {w} AND LOWER(agendada_por)=?',
                          p + [BOOKED_WEB]).fetchone()[0]
        serie = _lista(con, f"""
            SELECT substr(fecha,1,7) mes, COUNT(*) total,
                   SUM(CASE WHEN LOWER(agendada_por)=? THEN 1 ELSE 0 END) web
            FROM citas {w} GROUP BY 1 ORDER BY 1
        """, [BOOKED_WEB] + p)
    finally:
        con.close()
    for m in serie:
        m['pct_web'] = _pct(m['web'] or 0, m['total'])
    return {'total': total, 'web': web, 'pct_web': _pct(web, total), 'serie': serie}


# ── Resumen: los tiles de la portada, con comparación ────────────────────────

def _un_ano_antes(d):
    """Mismo día del año anterior. El 29-feb cae al 28 (no existe todos los años)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


def _tiles(desde, hasta, doctor):
    """Los números de un período. Se llama dos veces (período y año anterior) para
    poder comparar: un KPI sin referencia no dice nada."""
    dpc = destino_primeras_consultas(desde, hasta, doctor)
    fg = fugas(desde, hasta, doctor)
    oc = ocupacion(desde, hasta, doctor)
    ca = cartera(desde, hasta, doctor)
    horas = sum(d['minutos'] or 0 for d in oc['por_doctor']) / 60
    dias = sum(d['dias_trabajados'] or 0 for d in oc['por_doctor'])
    return {
        'pacientes_nuevos': pacientes_nuevos(desde, hasta, doctor),
        'primeras_consultas': dpc['total'],
        'pct_inicio': (dpc['pct'] or {}).get('inicio'),
        'pct_perdido': (dpc['pct'] or {}).get('perdido'),
        'conversion_90d': dpc['conversion_90d'],
        'tasa_inasistencia': fg['tasa_inasistencia'],
        'tasa_no_ocurrio': fg['tasa_no_ocurrio'],
        'tasa_cancelacion': fg['tasa_cancelacion'],
        'horas_sillon': round(horas, 1),
        'horas_por_dia': round(horas / dias, 2) if dias else None,
        'atendidos': fg['ocurrieron'] or 0,
        'inicios': ca['inicios'],
        'altas': ca['altas'],
        'neto_cartera': ca['neto'],
        'activos': ca['activos'],
    }


# ── Ingresos (boletas DTE de DentiDesk) ──────────────────────────────────────
# La API de DentiDesk NO expone boletas. Se leen con la SESIÓN del navegador desde la
# extensión F2 (`POST /ajax/ajaxConfigIntegracionSii.php`), que ya lo hace para Seguros,
# y se empujan acá. El login tiene reCAPTCHA, así que no hay forma de hacerlo
# server-side — por eso esto depende de que el PC de recepción esté encendido con
# DentiDesk abierto, igual que el auto-envío de seguros.

def _monto(v):
    """'$ 45.000' / '45000' / 45000.0 -> 45000. Devuelve 0 si no se puede leer, en vez
    de reventar: una fila rara no puede tumbar la carga del mes entero."""
    if isinstance(v, (int, float)):
        return int(v)
    digitos = ''.join(c for c in str(v or '') if c.isdigit())
    return int(digitos) if digitos else 0


def registrar_ingresos(dtes, cfg=None):
    """Guarda boletas/facturas emitidas. Upsert por folio (idempotente: la extensión
    puede reenviar el mismo mes las veces que quiera).

    Las NOTAS DE CRÉDITO se guardan en NEGATIVO para que la suma del período sea el
    ingreso neto sin tener que acordarse de restarlas en cada consulta.

    El doctor se atribuye por (RUT, fecha) contra las citas ATENDIDAS de ese día. Si el
    paciente no tiene atención ese día, el doctor queda VACÍO en vez de adivinarse con
    una ventana de días: una boleta mal atribuida ensucia el ingreso por doctor y nadie
    lo notaría. `sin_doctor` en el resultado dice cuántas quedaron así."""
    from scheduling import limpiar_rut
    if not dtes:
        return {'ok': True, 'recibidos': 0, 'guardados': 0, 'sin_doctor': 0}

    creado = ahora_cl().isoformat(timespec='seconds')
    con = _conn()
    try:
        filas, sin_doctor = [], 0
        for d in dtes:
            folio = str(d.get('SII_FOLIO') or d.get('folio') or '').strip()
            fecha = str(d.get('FECHA_EMISION') or d.get('fecha') or '')[:10]
            if not folio or not fecha:
                continue
            rut = limpiar_rut(str(d.get('RUT') or d.get('rut') or ''))
            tipo = str(d.get('TIPO_DOCUMENTO') or d.get('tipo') or '').strip()
            monto = _monto(d.get('MONTO') if 'MONTO' in d else d.get('monto'))
            if 'nota' in _normalizar(tipo):
                monto = -abs(monto)
            doctor = ''
            if rut:
                r = con.execute(
                    f"SELECT doctor FROM citas WHERE rut=? AND fecha=? AND {_SQL_OCURRIO} "
                    f"AND doctor NOT IN ('', 'rx') ORDER BY duracion DESC LIMIT 1",
                    (rut, fecha)).fetchone()
                doctor = r['doctor'] if r else ''
            if not doctor:
                sin_doctor += 1
            filas.append((folio, fecha, rut, monto, tipo,
                          str(d.get('DESCRIPCION') or d.get('descripcion') or '')[:300],
                          doctor, creado))
        if filas:
            con.executemany(
                'INSERT INTO ingresos (folio, fecha, rut, monto, tipo_doc, descripcion, '
                'doctor, creado) VALUES (?,?,?,?,?,?,?,?) '
                'ON CONFLICT(folio) DO UPDATE SET fecha=excluded.fecha, rut=excluded.rut, '
                'monto=excluded.monto, tipo_doc=excluded.tipo_doc, '
                'descripcion=excluded.descripcion, doctor=excluded.doctor', filas)
            con.commit()
    finally:
        con.close()
    return {'ok': True, 'recibidos': len(dtes), 'guardados': len(filas),
            'sin_doctor': sin_doctor}


def plata(desde=None, hasta=None, doctor=None):
    """Ingresos, gastos y margen. El ingreso por hora de sillón es el indicador que
    junta operación y precio en un número.

    ⚠️ Los ingresos existen solo desde que la extensión empezó a empujar boletas; los
    gastos vienen de `compras.py`, que tiene datos desde 2022. Comparar un margen de un
    período sin boletas cargadas daría un número catastrófico y falso, por eso se
    devuelve `meses_con_ingresos` para que el panel no dibuje lo que no tiene."""
    cond, p = ['1=1'], []
    if desde:
        cond.append('fecha >= ?')
        p.append(_iso(desde))
    if hasta:
        cond.append('fecha <= ?')
        p.append(_iso(hasta))
    if doctor:
        cond.append('doctor = ?')
        p.append(doctor)
    w = 'WHERE ' + ' AND '.join(cond)
    con = _conn()
    try:
        tot = con.execute(f'SELECT COALESCE(SUM(monto),0) t, COUNT(*) n FROM ingresos {w}',
                          p).fetchone()
        serie = _lista(con, f"SELECT substr(fecha,1,7) mes, SUM(monto) monto, COUNT(*) n "
                            f"FROM ingresos {w} GROUP BY 1 ORDER BY 1", p)
        por_doc = _lista(con, f"SELECT doctor, SUM(monto) monto, COUNT(*) n "
                              f"FROM ingresos {w} GROUP BY 1 ORDER BY monto DESC", p)
    finally:
        con.close()

    ingresos = tot['t'] or 0
    # Gastos: los calcula compras.py, que ya suma CLP + dólares convertidos.
    gastos = None
    try:
        import compras
        gastos = (compras.resumen_gastos(desde=_iso(desde) if desde else None,
                                         hasta=_iso(hasta) if hasta else None) or {}).get('total')
    except Exception as e:
        log.warning('kpi.plata: no se pudieron leer los gastos: %r', e)

    oc = ocupacion(desde, hasta, doctor)
    minutos = sum(d['minutos'] or 0 for d in oc['por_doctor'])
    horas = minutos / 60

    return {
        'ingresos': ingresos,
        'boletas': tot['n'],
        'meses_con_ingresos': len(serie),
        'gastos': gastos,
        'margen': (ingresos - gastos) if gastos is not None else None,
        'horas_sillon': round(horas, 1),
        'ingreso_por_hora': round(ingresos / horas) if horas else None,
        'serie': serie,
        'por_doctor': por_doc,
    }


def resumen_semanal(desde, hasta):
    """Los conteos del periodo con las MISMAS claves que espera
    `reporte_semanal._barrido_clinico`, para que ese modulo lea del datamart en vez
    de barrer DentiDesk dia por dia en cada corrida.

    Devuelve `citas` para que el que llama pueda distinguir "el periodo dio cero" de
    "el datamart todavia no tiene este periodo" — en el segundo caso hay que caer al
    barrido directo, y confundirlos mandaria un reporte en blanco."""
    w, p = _rango(desde, hasta)
    con = _conn()
    try:
        r = con.execute(f"""
            SELECT COUNT(*) citas,
              SUM(CASE WHEN {_SQL_OCURRIO} THEN 1 ELSE 0 END)              atendidos,
              SUM(CASE WHEN estado_norm='no_llega'  THEN 1 ELSE 0 END)     no_shows,
              SUM(CASE WHEN estado_norm='cancelada' THEN 1 ELSE 0 END)     cancelaciones,
              SUM(CASE WHEN categoria='primera_consulta' AND {_SQL_OCURRIO}
                       THEN 1 ELSE 0 END)                                  primeras_consultas,
              SUM(CASE WHEN categoria IN ('inicio_fijos','inicio_alineadores')
                       AND {_SQL_OCURRIO} THEN 1 ELSE 0 END)               inicios,
              SUM(CASE WHEN categoria='fin_definitivo' AND {_SQL_OCURRIO}
                       THEN 1 ELSE 0 END)                                  altas,
              COUNT(DISTINCT fecha)                                        dias_habiles
            FROM citas {w}
        """, p).fetchone()
    finally:
        con.close()
    d = {k: (r[k] or 0) for k in r.keys()}
    d['fuente'] = 'datamart'
    return d


def resumen(desde=None, hasta=None, doctor=None):
    """La portada del panel: los indicadores del período, los del mismo período del
    año anterior, y la variación. Comparar contra el año anterior y no contra el mes
    previo es deliberado: esta clínica tiene una estacionalidad fuerte (febrero es un
    desierto, ~247 atenciones vs ~700 el resto), así que un mes contra el anterior
    mide la estación, no la gestión."""
    hoy = fechas.hoy_chile()
    if not hasta:
        hasta = hoy
    if not desde:
        desde = hoy.replace(day=1)
    desde = desde if hasattr(desde, 'isoformat') else date.fromisoformat(_iso(desde))
    hasta = hasta if hasattr(hasta, 'isoformat') else date.fromisoformat(_iso(hasta))

    actual = _tiles(desde, hasta, doctor)

    # Un rango de más de 18 meses SE SOLAPA con su propio año anterior, así que la
    # comparación deja de significar nada (comparar 2021-2026 contra 2020-2025 es
    # comparar el período consigo mismo corrido un año). En ese caso no se compara:
    # es mejor no mostrar flecha que mostrar una que no quiere decir nada.
    meses = (hasta.year - desde.year) * 12 + (hasta.month - desde.month)
    comparable = meses <= 18
    previo = _tiles(_un_ano_antes(desde), _un_ano_antes(hasta), doctor) if comparable else {}

    variacion, delta = {}, {}
    for k, v in actual.items():
        p = previo.get(k)
        if not (isinstance(v, (int, float)) and isinstance(p, (int, float))):
            variacion[k], delta[k] = None, None
            continue
        delta[k] = round(v - p, 2)
        # Un porcentaje sobre valores que pueden ser NEGATIVOS invierte el signo: el
        # flujo neto de cartera pasando de -317 a -477 daba "+50,5%" y se pintaba de
        # verde, cuando en realidad empeoró. En esos casos se informa solo la
        # diferencia absoluta y el panel no dibuja porcentaje.
        variacion[k] = round((v - p) / p * 100, 1) if p > 0 and v >= 0 else None

    return {
        'desde': desde.isoformat(), 'hasta': hasta.isoformat(), 'doctor': doctor or '',
        'meses': meses, 'comparable': comparable,
        'actual': actual,
        'ano_anterior': previo,
        'variacion_pct': variacion,
        'delta': delta,
        'confirmacion_vigente': tasa_confirmacion_vigente(doctor),
        'ocupacion_futura': ocupacion_futura(doctor=doctor),
        'calidad': calidad_datos(desde, hasta),
    }
