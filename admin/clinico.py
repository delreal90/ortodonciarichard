"""
clinico.py — La capa clinica y de eventos de la base del proyecto (clinica.db).

QUE PROBLEMA RESUELVE
---------------------
El proyecto tiene mas de veinte almacenes de datos, cada uno nacido con el
sistema que lo necesitaba, y ninguna forma de cruzarlos. Hoy no se puede
contestar "el paciente con la arcada estrecha, inicia tratamiento mas seguido?"
aunque los dos datos existan: viven en archivos que nadie enlazo nunca.

Este modulo agrega, AL LADO de la agenda que ya vive en `clinica.db` (kpi.py),
el registro clinico y una capa de eventos con el resto de los sistemas. Con eso
un JOIN cruza un ancho de arcada con el destino de la primera consulta sin salir
de SQL.

⚠️ ES UNA PROYECCION, NO LA FUENTE DE VERDAD
--------------------------------------------
El JSON manda (regla 2 del CLAUDE.md: nadie escribe su propio guardado). Estas
tablas se reconstruyen ENTERAS con `proyectar_todo()` y son desechables. Eso no
es una limitacion: es la propiedad que permite recalcular cinco anos de
percentiles si algun dia cambia la tabla normativa de Bishara, sin reescribir un
solo dato a mano.

⚠️ La base COMPLETA, en cambio, NO es desechable: `disponibilidad` (de kpi.py) no
se puede reconstruir de ninguna parte. Ver basedatos.py y backup.py.

LO QUE ESTA BASE NUNCA VA A TENER
---------------------------------
La ficha clinica real vive en DentiDesk, que solo expone 6 endpoints de agenda
(sin API de pacientes ni de documentos, verificado). Aca hay agenda, informes de
evaluacion, tamizajes y el rastro de los sistemas de avisos. **No** el
odontograma, **no** las evoluciones, **no** las radiografias. Decirlo importa:
una consulta que asume un dato que no esta da un resultado sesgado sin avisar.

⚠️ EL INFORME CASI NUNCA SABE DE QUE CITA VINO
----------------------------------------------
`informes.id_agenda` viene vacio salvo que el informe se haya abierto desde el
F2 con la cita en pantalla (2 de los 16 informes reales). **El cruce informe <->
agenda va por RUT y fecha**, no por id_agenda. Asumir lo contrario devuelve casi
nada y hace parecer que la base esta vacia cuando no lo esta.
"""

import json
import logging

import basedatos
import fechas
import transversal
import informe_pc
from scheduling import limpiar_rut

log = logging.getLogger(__name__)

# Minimo de casos por celda (sexo x tramo de edad) para emitir media y DE. Una
# media de dos pacientes presentada igual que una de treinta es un numero que se
# ve solido y no lo es. Bishara publico con 15 por sexo.
MIN_CASOS_CELDA = 5

# Ancho de los tramos de edad del contador de muestra, en anos.
TRAMO_EDAD = 1


# ── Esquema ──────────────────────────────────────────────────────────────────
#
# Sin claves foraneas A PROPOSITO: `PRAGMA foreign_keys` es POR CONEXION y ni
# kpi.py ni este modulo lo activan, asi que declararlas se verian en el esquema
# y no se aplicarian — una garantia falsa es peor que ninguna. La integridad la
# da que esto se reconstruye entero dentro de una transaccion.

_TABLAS = """
CREATE TABLE IF NOT EXISTS pacientes (
    rut              TEXT PRIMARY KEY,
    nombres          TEXT,
    apellidos        TEXT,
    sexo             TEXT,
    fecha_nacimiento TEXT,
    comuna           TEXT,
    prevision        TEXT,
    convenio         TEXT,
    id_paciente      TEXT,
    en_indice        INTEGER NOT NULL DEFAULT 0,
    actualizado      TEXT
);

CREATE TABLE IF NOT EXISTS informes (
    id                     TEXT PRIMARY KEY,
    rut                    TEXT,
    fecha                  TEXT,
    edad                   REAL,
    sexo                   TEXT,
    sexo_origen            TEXT,
    doctor                 TEXT,
    medidor                TEXT,
    id_agenda              TEXT,
    conclusion             TEXT,
    meses_control          INTEGER,
    sin_tratamiento_previo INTEGER,
    evaluacion             TEXT,
    n_hallazgos            INTEGER NOT NULL DEFAULT 0,
    n_imagenes             INTEGER NOT NULL DEFAULT 0,
    impreso                TEXT,
    editado_tras_imprimir  TEXT,
    creado                 TEXT,
    actualizado            TEXT
);

CREATE TABLE IF NOT EXISTS mediciones (
    informe_id  TEXT NOT NULL,
    clave       TEXT NOT NULL,
    rut         TEXT,
    fecha       TEXT,
    edad        REAL,
    sexo        TEXT,
    medidor     TEXT,
    valor_mm    REAL,
    medida      TEXT,
    arcada      TEXT,
    percentil   REAL,
    z           REAL,
    en_recambio INTEGER,
    PRIMARY KEY (informe_id, clave)
);

CREATE TABLE IF NOT EXISTS oclusion (
    informe_id TEXT NOT NULL,
    sitio      TEXT NOT NULL,
    lado       TEXT NOT NULL,
    valor      TEXT,
    cuartos    INTEGER,
    clase      TEXT,
    rut        TEXT,
    fecha      TEXT,
    PRIMARY KEY (informe_id, sitio, lado)
);

CREATE TABLE IF NOT EXISTS hallazgos (
    informe_id TEXT NOT NULL,
    clave      TEXT NOT NULL,
    grupo      TEXT,
    rut        TEXT,
    fecha      TEXT,
    PRIMARY KEY (informe_id, clave)
);

CREATE TABLE IF NOT EXISTS ordenes (
    informe_id TEXT NOT NULL,
    clave      TEXT NOT NULL,
    detalle    TEXT,
    rut        TEXT,
    fecha      TEXT,
    PRIMARY KEY (informe_id, clave)
);

CREATE TABLE IF NOT EXISTS tamizajes (
    id          TEXT PRIMARY KEY,
    rut         TEXT,
    fecha       TEXT,
    instrumento TEXT,
    puntaje     REAL,
    alto        INTEGER,
    origen      TEXT,
    informe_id  TEXT
);

CREATE TABLE IF NOT EXISTS eventos (
    sistema TEXT NOT NULL,
    ref     TEXT NOT NULL,
    rut     TEXT,
    fecha   TEXT,
    tipo    TEXT,
    estado  TEXT,
    datos   TEXT,
    PRIMARY KEY (sistema, ref)
);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""

# ⚠️ En executescript SEPARADO y POSTERIOR a las migraciones: un CREATE INDEX
# sobre una columna que todavia no existe aborta el script entero y deja la base
# a medio migrar. NO se manifiesta en una base nueva (la columna nace en el
# CREATE TABLE), solo en las preexistentes. Paso de verdad con `ix_compras_sus`
# — ver CLAUDE.md.
_INDICES = """
CREATE INDEX IF NOT EXISTS ix_informes_rut   ON informes(rut);
CREATE INDEX IF NOT EXISTS ix_informes_fecha ON informes(fecha);
CREATE INDEX IF NOT EXISTS ix_med_rut        ON mediciones(rut);
CREATE INDEX IF NOT EXISTS ix_med_clave      ON mediciones(clave);
CREATE INDEX IF NOT EXISTS ix_ocl_rut        ON oclusion(rut);
CREATE INDEX IF NOT EXISTS ix_hall_clave     ON hallazgos(clave);
CREATE INDEX IF NOT EXISTS ix_hall_rut       ON hallazgos(rut);
CREATE INDEX IF NOT EXISTS ix_ord_clave      ON ordenes(clave);
CREATE INDEX IF NOT EXISTS ix_tam_rut        ON tamizajes(rut);
CREATE INDEX IF NOT EXISTS ix_ev_rut         ON eventos(rut);
CREATE INDEX IF NOT EXISTS ix_ev_sistema     ON eventos(sistema, tipo);
CREATE INDEX IF NOT EXISTS ix_ev_fecha       ON eventos(fecha);
"""

# Todo lo que este modulo proyecta y por lo tanto puede borrar y rehacer.
# ⚠️ NO incluye las tablas de kpi.py (citas, disponibilidad, ingresos,
# snapshots): `disponibilidad` no se puede reconstruir de ninguna parte.
TABLAS_PROYECTADAS = ('pacientes', 'informes', 'mediciones', 'oclusion',
                      'hallazgos', 'ordenes', 'tamizajes', 'eventos')


def _migrar(con):
    """Migraciones idempotentes para bases ya creadas.

    Hoy no hay ninguna. El patron, cuando haga falta (molde de compras.py):

        cols = {r['name'] for r in con.execute('PRAGMA table_info(informes)')}
        if 'columna_nueva' not in cols:
            con.execute('ALTER TABLE informes ADD COLUMN columna_nueva TEXT')
    """
    return con


def init_db():
    """Crea el esquema si no existe. Idempotente — se llama en cada arranque."""
    con = basedatos.conectar()
    try:
        con.executescript(_TABLAS)
        _migrar(con)
        con.executescript(_INDICES)
        con.commit()
    finally:
        con.close()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _num(v):
    """float o None. Nunca lanza: el formulario manda '' cuando el campo esta
    vacio, y '' no es 0."""
    if v in (None, ''):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _entero(v):
    n = _num(v)
    return None if n is None else int(n)


def _texto(v):
    return '' if v is None else str(v)


def _bool_o_none(v):
    """1 | 0 | None.

    ⚠️ La ausencia NO es False. `sin_tratamiento_previo` se agrego al formulario
    despues de que ya hubiera informes guardados: los viejos no lo traen, y
    proyectarlos como 0 afirmaria que esos pacientes SI tuvieron tratamiento
    previo — justo el criterio de inclusion de Bishara, invertido.
    """
    if v is None or v == '':
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ('si', 'sí', 'true', '1'):
            return 1
        if s in ('no', 'false', '0'):
            return 0
        return None
    return 1 if v else 0


# ── Proyeccion del registro clinico ─────────────────────────────────────────

def _sexo_de(item):
    """(sexo, origen). El origen importa: el formulario PRE-MARCA el sexo a
    partir del nombre (genero.py, 94,3% de precision) y el doctor puede no
    tocarlo. Como el percentil se calcula contra una tabla DISTINTA segun el
    sexo, un sexo errado da un numero que se ve razonable y esta mal.

    El formulario manda `sexo_origen`: 'declarado' (venia de la ficha del
    paciente), 'sugerido' (lo adivino genero.py por el nombre y nadie lo toco) o
    'confirmado' (una persona lo eligio a mano).

    ⚠️ Un informe viejo no trae el campo, y ahi la respuesta honesta es
    'desconocido' — NO 'confirmado'. Suponer que alguien lo revisó es
    exactamente el error que este campo existe para evitar.
    """
    s = (item.get('sexo') or '').strip().upper()[:1]
    if s not in ('M', 'F'):
        return '', ''
    origen = (item.get('sexo_origen') or '').strip().lower()
    return s, (origen if origen in ORIGENES_SEXO else 'desconocido')


def _filas_informe(item):
    """Todas las filas que produce UN informe, listas para insertar."""
    iid = item.get('id') or ''
    rut = limpiar_rut(item.get('rut') or '')
    fecha = item.get('fecha') or ''
    edad = informe_pc._edad_de(item)
    sexo, sexo_origen = _sexo_de(item)
    # El medidor es el doctor de la cita: decision del usuario ("el doctor que
    # lo mide es con quien tiene la cita el paciente"). Puede venir vacio si el
    # informe se abrio sin cita — se deja vacio, nunca se adivina.
    medidor = _texto(item.get('doctor_texto')).strip()
    med = item.get('mediciones') or {}

    # ⚠️ `evaluacion` ausente NO es lista vacia: informe_pc sustituye la
    # ausencia por EVALUACION_POR_DEFECTO al armar el documento, asi que
    # proyectar [] afirmaria que no se hizo nada.
    evaluacion = item.get('evaluacion')
    if evaluacion is None:
        evaluacion = list(informe_pc.EVALUACION_POR_DEFECTO)

    informe = (
        iid, rut, fecha, edad, sexo, sexo_origen,
        _texto(item.get('doctor_texto')), medidor, _texto(item.get('id_agenda')),
        _texto(item.get('conclusion')), _entero(item.get('meses_control')),
        _bool_o_none(item.get('sin_tratamiento_previo')),
        ','.join(str(e) for e in evaluacion),
        len(item.get('hallazgos') or []),
        len(item.get('imagenes') or []),
        item.get('impreso') or None,
        item.get('editado_tras_imprimir') or None,
        item.get('creado') or None, item.get('actualizado') or None,
    )

    mediciones = []
    # Anchos transversales: llevan percentil contra la tabla de Bishara.
    for clave, medida, arcada, _etiqueta in informe_pc.MEDICIONES_TRANSVERSALES:
        mm = _num(med.get(clave))
        if mm is None:
            continue
        pct = z = None
        recambio = None
        if edad is not None and sexo in ('M', 'F'):
            # ⚠️ Con la edad y el sexo DE ESTE informe, no con los de hoy: usar
            # la edad actual para un informe de hace dos anos da un percentil
            # que ese paciente nunca tuvo.
            r = transversal.percentil(medida, arcada, sexo, edad, mm)
            if r.get('ok'):
                pct, z = r.get('percentil'), r.get('z')
            recambio = 1 if transversal.en_recambio(medida, edad) else 0
        mediciones.append((iid, clave, rut, fecha, edad, sexo, medidor, mm,
                           medida, arcada, pct, z, recambio))
    # Resalte y sobremordida: no tienen tabla normativa, van sin percentil.
    for clave, _etiqueta, _unidad in informe_pc.MEDICIONES_SIMPLES:
        mm = _num(med.get(clave))
        if mm is not None:
            mediciones.append((iid, clave, rut, fecha, edad, sexo, medidor, mm,
                               None, None, None, None, None))

    # ⚠️ La clase se registra POR LADO: un caso puede ser Clase I a la derecha y
    # Clase II a la izquierda, y esa asimetria es clasificatoria (Angle la llama
    # "subdivision"), no un detalle.
    oclusion = []
    for sitio, _titulo in informe_pc.RELACIONES_OCLUSION:
        for lado, _nombre in informe_pc.LADOS:
            valor = med.get('%s_%s' % (sitio, lado))
            if not valor:
                continue
            par = informe_pc.RELACIONES_MAP.get(valor)
            if par is None:
                continue
            cuartos = par[1]
            # 'no_registrable' (pieza ausente) NO es Clase I: es la ausencia de
            # una relacion que registrar.
            clase = None if cuartos is None else informe_pc._clase_de(cuartos)
            oclusion.append((iid, sitio, lado, valor, cuartos, clase, rut, fecha))

    hallazgos = []
    for clave in (item.get('hallazgos') or []):
        ficha = informe_pc.HALLAZGOS.get(clave) or {}
        hallazgos.append((iid, clave, ficha.get('grupo', ''), rut, fecha))

    detalle = item.get('ordenes_detalle') or {}
    ordenes = [(iid, clave, _texto(detalle.get(clave)), rut, fecha)
               for clave in (item.get('ordenes') or [])]

    # ⚠️ El PSQ dentro del informe esta CONGELADO a proposito (server.py): es la
    # foto del resultado al momento de emitir la hoja, no un puntero al registro
    # vivo. Se proyecta tal cual.
    tamizajes = []
    tam = item.get('tamizaje') or {}
    psq = tam.get('psq') or {}
    if psq.get('puntaje') is not None:
        tamizajes.append(('informe:%s:psq' % iid, rut, psq.get('fecha') or fecha,
                          'PSQ-CL', _num(psq.get('puntaje')),
                          1 if psq.get('riesgo_alto') else 0, 'informe', iid))
    sb = tam.get('stopbang') or {}
    if sb.get('respondido_por_el_paciente'):
        r = _puntaje_stopbang(sb)
        if r is not None:
            tamizajes.append(('informe:%s:stopbang' % iid, rut, fecha,
                              'STOP-BANG', r[0], r[1], 'informe', iid))

    return informe, mediciones, oclusion, hallazgos, ordenes, tamizajes


def _puntaje_stopbang(sb):
    """(puntaje, alto) del STOP-BANG guardado, o None si no se pudo evaluar.

    ⚠️ Un item sin registrar NO es negativo: `stopbang.evaluar` los cuenta
    aparte y marca el puntaje como incompleto (un piso). Aca solo se traslada su
    resultado; no se reimplementa el umbral, que es como el panel y el papel
    firmado terminan mostrando numeros distintos.
    """
    try:
        import stopbang
        datos = dict(sb)
        if datos.get('imc') in (None, '') and datos.get('peso') and datos.get('talla'):
            datos['imc'] = stopbang.imc(datos['peso'], datos['talla'])
        r = stopbang.evaluar(datos)
        return float(r['puntaje']), (1 if r.get('banda') == 'alto' else 0)
    except Exception as e:
        log.warning('[clinico] stopbang no evaluable: %r', e)
        return None


# ── Adaptadores de la capa `eventos` ────────────────────────────────────────
#
# Cada sistema que le pasa algo al paciente aporta filas a UNA tabla, reusando
# la funcion de listado que ese modulo ya tiene. Asi "los que no firmaron su
# consentimiento, faltan mas a sus citas?" es un JOIN entre `eventos` y `citas`
# por RUT.
#
# ⚠️ `eventos` es deliberadamente laxa y tiene un limite honesto: contesta bien
# "le paso esto a este paciente, cuando?". NO es el lugar para analizar un
# sistema a fondo. Cuando uno lo amerite, se le hacen tablas tipadas, igual que
# las tiene la agenda. Escribirlo aca evita que alguien construya un analisis
# serio sobre json_extract().

_LIMITE_EVENTOS = 100000


def _ev_consentimientos():
    import consentimientos
    for it in consentimientos.listar():
        yield (it.get('id'), it.get('rut'), it.get('creado'),
               it.get('tipo') or 'consentimiento', it.get('estado'),
               {'canal': it.get('canal'), 'firmado': it.get('firmado'),
                'respaldo_drive': it.get('respaldo_drive')})


def _ev_nps():
    import nps
    for it in nps.historial(limite=_LIMITE_EVENTOS):
        yield (None, it.get('rut'), it.get('fecha'), 'encuesta',
               it.get('categoria') or it.get('estado'),
               {'doctor': it.get('doctor'), 'id_agenda': it.get('id_agenda'),
                'fecha_respuesta': it.get('fecha_respuesta')})


def _ev_control_dental():
    import control_dental
    for it in control_dental.historial(limite=_LIMITE_EVENTOS):
        yield (None, it.get('rut'), it.get('fecha'), 'recordatorio',
               it.get('estado') or 'enviado', {'canal': it.get('canal')})


def _ev_recaptacion():
    import recaptacion
    for it in recaptacion.historial(limite=_LIMITE_EVENTOS):
        yield (None, it.get('rut'), it.get('fecha_envio'), 'recordatorio',
               it.get('estado') or 'enviado',
               {'doctor': it.get('doctor'), 'respondio': it.get('respondio')})


def _ev_seguimiento_pc():
    import seguimiento_pc
    for it in seguimiento_pc.listar():
        yield (None, it.get('rut'), it.get('fecha_pc'), 'primera_consulta',
               it.get('estado'), {'doctor': it.get('doctor'),
                                  'toques': len(it.get('toques') or [])})


def _ev_fotos_finales():
    import fotos_finales
    for it in fotos_finales.historial(limite=_LIMITE_EVENTOS):
        yield (None, it.get('rut'), it.get('avisado') or it.get('fecha'),
               'collage', 'avisado', {'fecha_retiro': it.get('fecha_retiro')})


def _ev_seguros():
    import seguros
    for it in seguros.listar_registros():
        # `folio` es el de la boleta DTE, o sea la llave con la tabla `ingresos`:
        # permite preguntar cuanto se facturo de lo que se reembolso.
        yield (it.get('id'), it.get('rut'), it.get('creado'), 'formulario',
               it.get('estado'), {'aseguradora': it.get('aseguradora'),
                                  'origen': it.get('origen'),
                                  'doctor': it.get('doctor'),
                                  'folio': it.get('folio')})


def _ev_reactivacion():
    import reactivacion
    for it in reactivacion.listar():
        yield (None, it.get('rut'), it.get('proxima_fecha'), 'reactivacion',
               it.get('estado'), {'doctor': it.get('doctor')})


ADAPTADORES = (
    ('consentimiento', _ev_consentimientos),
    ('nps', _ev_nps),
    ('control_dental', _ev_control_dental),
    ('recaptacion', _ev_recaptacion),
    ('seguimiento_pc', _ev_seguimiento_pc),
    ('fotos_finales', _ev_fotos_finales),
    ('seguro', _ev_seguros),
    ('reactivacion', _ev_reactivacion),
)


def _filas_eventos():
    """Todas las filas de `eventos`, de todos los sistemas.

    ⚠️ Cada adaptador va en su propio try/except: un registro corrupto o un
    modulo que cambio de forma no puede dejar sin proyeccion a los otros siete.
    Se loguea y se sigue, igual que hace `reporte_semanal.agregar()`.
    """
    filas, errores = [], {}
    for sistema, adaptador in ADAPTADORES:
        try:
            n = 0
            for ref, rut, fecha, tipo, estado, datos in adaptador():
                n += 1
                # Ref estable: el id propio del sistema cuando lo tiene, y si no
                # un correlativo dentro de ese sistema. Sin esto, dos filas del
                # mismo paciente colisionarian en la PK y una se perderia.
                clave = str(ref) if ref else '%s:%d' % (limpiar_rut(rut or ''), n)
                filas.append((sistema, clave, limpiar_rut(rut or ''),
                              (fecha or '')[:10], tipo or '', estado or '',
                              json.dumps(datos or {}, ensure_ascii=False)))
        except Exception as e:
            errores[sistema] = repr(e)
            log.warning('[clinico] adaptador %s fallo: %r', sistema, e)
    return filas, errores


# ── Proyeccion ──────────────────────────────────────────────────────────────

def _filas_pacientes(ruts_vistos):
    """La espina: una fila por RUT visto en CUALQUIER fuente.

    ⚠️ Incluye los RUT que NO estan en patient_index.json (marcados
    `en_indice=0`). Si solo se proyectara el indice, cada cruce perderia
    silenciosamente a los pacientes que una fuente conoce y la otra no —
    justo los casos raros, que son los que se van a mirar.
    """
    try:
        import pacientes
        idx = pacientes._load_index()
    except Exception as e:
        log.warning('[clinico] no se pudo leer el indice de pacientes: %r', e)
        idx = {}

    ahora = fechas.ahora_chile().isoformat(timespec='seconds')
    filas = []
    for rut in sorted(set(ruts_vistos) | set(idx)):
        if not rut:
            continue
        r = idx.get(rut) or {}
        filas.append((rut, _texto(r.get('nombres')), _texto(r.get('apellidos')),
                      _texto(r.get('genero')), _texto(r.get('fecha_nacimiento')),
                      _texto(r.get('comuna')), _texto(r.get('prevision')),
                      _texto(r.get('convenio')), _texto(r.get('id_paciente')),
                      1 if rut in idx else 0, ahora))
    return filas


def proyectar_todo():
    """Reconstruye TODAS las tablas de este modulo desde los JSON. Cero red.

    Idempotente por construccion: borra y rehace. Es la unica forma de
    proyeccion que hay a proposito — la incremental obligaria a razonar sobre
    que cambio, y una proyeccion desechable no lo necesita.

    ⚠️ Todo dentro de UNA transaccion, para que nadie que consulte alcance a ver
    las tablas a medio llenar.
    """
    items = informe_pc.todos()
    informes, mediciones, oclusion, hallazgos, ordenes, tamizajes = [], [], [], [], [], []
    for item in items:
        if not (item.get('id') or ''):
            continue
        f = _filas_informe(item)
        informes.append(f[0])
        mediciones += f[1]
        oclusion += f[2]
        hallazgos += f[3]
        ordenes += f[4]
        tamizajes += f[5]

    # Los PSQ contestados en /psq (fuera de un informe) tambien son tamizajes.
    try:
        import psq
        for e in psq.listar_envios(limite=_LIMITE_EVENTOS):
            tamizajes.append(('psq:%s' % e.get('id'), limpiar_rut(e.get('rut') or ''),
                              (e.get('fecha_iso') or '')[:10], 'PSQ-CL',
                              _num(e.get('puntaje')),
                              1 if e.get('riesgo') == 'alto' else 0, 'psq', ''))
    except Exception as e:
        log.warning('[clinico] no se pudieron leer los envios de PSQ: %r', e)

    eventos, errores = _filas_eventos()

    ruts = ({i[1] for i in informes} | {t[1] for t in tamizajes}
            | {e[2] for e in eventos})
    pac = _filas_pacientes(ruts)

    ahora = fechas.ahora_chile().isoformat(timespec='seconds')
    con = basedatos.conectar()
    try:
        con.execute('BEGIN')
        for tabla in TABLAS_PROYECTADAS:
            con.execute('DELETE FROM %s' % tabla)
        con.executemany(_insert('pacientes'), pac)
        con.executemany(_insert('informes'), informes)
        con.executemany(_insert('mediciones'), mediciones)
        con.executemany(_insert('oclusion'), oclusion)
        con.executemany(_insert('hallazgos'), hallazgos)
        con.executemany(_insert('ordenes'), ordenes)
        con.executemany(_insert('tamizajes', reemplazar=True), tamizajes)
        con.executemany(_insert('eventos', reemplazar=True), eventos)
        con.executemany('INSERT OR REPLACE INTO meta VALUES (?,?)', [
            ('ultima_proyeccion', ahora),
            ('n_informes', str(len(informes))),
            ('sello_registro', _sello(items)),
            ('errores_adaptadores', json.dumps(errores, ensure_ascii=False)),
        ])
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

    return {'ok': True, 'informes': len(informes), 'mediciones': len(mediciones),
            'oclusion': len(oclusion), 'hallazgos': len(hallazgos),
            'ordenes': len(ordenes), 'tamizajes': len(tamizajes),
            'eventos': len(eventos), 'pacientes': len(pac),
            'errores': errores, 'fecha': ahora}


# Las columnas de cada tabla, EN EL ORDEN en que las arman las funciones de
# arriba.
#
# ⚠️ Los INSERT nombran sus columnas a proposito. Un `INSERT INTO t VALUES (?,?)`
# posicional se rompe el dia que `_migrar()` agregue una columna — y `_migrar()`
# existe justamente para eso. El error ("table has N columns but M values were
# supplied") aparece recien en produccion, sobre la base vieja, porque en una
# base nueva la columna nace en el CREATE TABLE y todo calza. Es el mismo tipo
# de trampa que el orden CREATE INDEX / migraciones.
_COLUMNAS = {
    'pacientes': ('rut', 'nombres', 'apellidos', 'sexo', 'fecha_nacimiento',
                  'comuna', 'prevision', 'convenio', 'id_paciente', 'en_indice',
                  'actualizado'),
    'informes': ('id', 'rut', 'fecha', 'edad', 'sexo', 'sexo_origen', 'doctor',
                 'medidor', 'id_agenda', 'conclusion', 'meses_control',
                 'sin_tratamiento_previo', 'evaluacion', 'n_hallazgos',
                 'n_imagenes', 'impreso', 'editado_tras_imprimir', 'creado',
                 'actualizado'),
    'mediciones': ('informe_id', 'clave', 'rut', 'fecha', 'edad', 'sexo',
                   'medidor', 'valor_mm', 'medida', 'arcada', 'percentil', 'z',
                   'en_recambio'),
    'oclusion': ('informe_id', 'sitio', 'lado', 'valor', 'cuartos', 'clase',
                 'rut', 'fecha'),
    'hallazgos': ('informe_id', 'clave', 'grupo', 'rut', 'fecha'),
    'ordenes': ('informe_id', 'clave', 'detalle', 'rut', 'fecha'),
    'tamizajes': ('id', 'rut', 'fecha', 'instrumento', 'puntaje', 'alto',
                  'origen', 'informe_id'),
    'eventos': ('sistema', 'ref', 'rut', 'fecha', 'tipo', 'estado', 'datos'),
}


def _insert(tabla, reemplazar=False):
    cols = _COLUMNAS[tabla]
    return 'INSERT %sINTO %s (%s) VALUES (%s)' % (
        'OR REPLACE ' if reemplazar else '', tabla,
        ','.join(cols), ','.join('?' * len(cols)))


def _sello(items):
    """Huella del registro de informes: cuantos hay y cual es el 'actualizado'
    mas nuevo. Sirve para saber si la proyeccion quedo atras sin rehacerla.

    ⚠️ No es gratis: quien la llama tuvo que leer y parsear el registro ENTERO.
    Medido el 2026-09-10: 5 ms con 1.000 informes, y la llaman /estado y
    /muestra en cada request del panel. A este tamano no molesta y es EXACTA,
    que es lo que importa. Se evaluo cambiarla por el mtime del archivo —O(1)—
    y se descarto: cambia una comparacion exacta por una heuristica que puede
    fallar en silencio, para ahorrar milisegundos que nadie nota.
    """
    if not items:
        return '0:'
    return '%d:%s' % (len(items), max((i.get('actualizado') or '') for i in items))


def esta_al_dia():
    """True si la proyeccion refleja el registro actual de informes."""
    try:
        sello = _sello(informe_pc.todos())
    except Exception:
        return True    # sin registro legible no hay nada que reproyectar
    con = basedatos.conectar()
    try:
        fila = con.execute("SELECT valor FROM meta WHERE clave='sello_registro'").fetchone()
    finally:
        con.close()
    return bool(fila) and fila['valor'] == sello


def proyectar_si_hace_falta():
    """Reproyecta solo si el registro cambio. Lo usan los endpoints de lectura,
    para que el contador incluya al paciente de hoy sin meter una escritura a
    SQLite dentro del guardado del informe."""
    if esta_al_dia():
        return None
    return proyectar_todo()


# ── El contador de muestra ──────────────────────────────────────────────────
#
# Cuanto hay acumulado para construir una normativa chilena propia de anchos de
# arcada. NO lleva el criterio de Bishara cableado: el usuario pidio "guarda
# todo, pero para despues se pueda elegir criterios de normalidad con filtros".

# De donde salio el sexo del informe. 'sugerido' = lo adivino genero.py por el
# nombre y nadie lo toco; 'desconocido' = informe viejo, sin el campo.
ORIGENES_SEXO = ('declarado', 'confirmado', 'sugerido')

CLASES = ('I', 'II', 'III')
TRAT_PREVIO = ('todos', 'sin', 'con', 'sin_dato')

# Nombre legible de cada medicion, para que la tabla del panel diga QUE se
# promedio. Sale de informe_pc, no se reescribe.
ETIQUETAS_MEDICION = dict(
    [(c, e) for c, _m, _a, e in informe_pc.MEDICIONES_TRANSVERSALES]
    + [(c, e) for c, e, _u in informe_pc.MEDICIONES_SIMPLES])


def _filtro_clase(sitio, clases, lados):
    """(sql, params) para exigir la clase de Angle en `sitio`.

    ⚠️ `lados='ambos'` exige que los DOS lados esten registrados y calcen. Eso
    es lo que significa "Clase I" en el criterio de Bishara: un caso Clase I a
    la derecha y Clase II a la izquierda no es Clase I, tiene nombre propio
    (subdivision). Con `lados='alguno'` basta uno.
    """
    marcas = ','.join('?' * len(clases))
    cuantos = '= 2' if lados == 'ambos' else '>= 1'
    sql = ('(SELECT COUNT(*) FROM oclusion o WHERE o.informe_id = i.id '
           'AND o.sitio = ? AND o.clase IN (%s)) %s' % (marcas, cuantos))
    return sql, [sitio] + list(clases)


def _where_muestra(f):
    """(where, params) comun a `muestra()` y `filas_export()`."""
    w, p = ['1=1'], []

    if f.get('medida'):
        w.append('m.medida = ?'); p.append(f['medida'])
    if f.get('arcada'):
        w.append('m.arcada = ?'); p.append(f['arcada'])
    if f.get('sexo'):
        w.append('i.sexo = ?'); p.append(f['sexo'])
    if f.get('edad_min') is not None:
        w.append('i.edad >= ?'); p.append(float(f['edad_min']))
    if f.get('edad_max') is not None:
        w.append('i.edad <= ?'); p.append(float(f['edad_max']))

    # ⚠️ Tres estados, no dos: la ausencia del dato no es "tuvo tratamiento".
    trat = f.get('tratamiento_previo') or 'todos'
    if trat == 'sin':
        w.append('i.sin_tratamiento_previo = 1')
    elif trat == 'con':
        w.append('i.sin_tratamiento_previo = 0')
    elif trat == 'sin_dato':
        w.append('i.sin_tratamiento_previo IS NULL')

    # ⚠️ "confirmado" significa que una PERSONA lo eligio o que venia declarado
    # en la ficha. NO basta con que el campo tenga algo: un sexo 'sugerido' esta
    # lleno y puede estar equivocado, que es justo contra lo que protege este
    # filtro. Hasta el 2026-09-10 esto comprobaba solo que no estuviera vacio, y
    # el rotulo del panel prometia una garantia que no daba.
    if f.get('solo_sexo_confirmado'):
        w.append("i.sexo IN ('M','F') AND i.sexo_origen IN ('declarado','confirmado')")
    if f.get('solo_con_medidor'):
        w.append("i.medidor <> ''")

    lados = f.get('lados') or 'ambos'
    for sitio, clave in (('clase_molar', 'clase_molar'),
                         ('clase_canina', 'clase_canina')):
        clases = [c for c in (f.get(clave) or []) if c in CLASES]
        if clases:
            sql, par = _filtro_clase(sitio, clases, lados)
            w.append(sql); p += par

    return ' AND '.join(w), p


def muestra(filtros=None):
    """Cuanto hay acumulado, con media y DE por sexo y tramo de edad.

    ⚠️ Devuelve DOS conteos, no uno: `mediciones` y `pacientes`. Un paciente con
    tres informes aporta tres mediciones de la MISMA boca. Bishara midio a los
    mismos 30 sujetos repetidamente, asi que repetir es legitimo — pero mostrar
    solo el total hace ver la muestra mas grande de lo que es.

    ⚠️ La media y la DE no se emiten bajo MIN_CASOS_CELDA. Una media de dos
    pacientes presentada igual que una de treinta es un numero que se ve solido
    y no lo es.
    """
    import statistics

    f = dict(filtros or {})
    where, params = _where_muestra(f)
    con = basedatos.conectar()
    try:
        filas = con.execute(
            'SELECT m.rut, m.clave, m.valor_mm, m.percentil, i.sexo, i.edad '
            'FROM mediciones m JOIN informes i ON i.id = m.informe_id '
            'WHERE %s AND m.valor_mm IS NOT NULL' % where, params).fetchall()
    finally:
        con.close()

    # ⚠️ La celda se agrupa por MEDICION ADEMAS de sexo y edad. Sin la clave en
    # la llave, un ancho intermolar (45 mm) y un resalte (3 mm) del mismo
    # paciente caen en la misma celda y su promedio da 24 mm: un numero que se
    # ve como un ancho de arcada plausible y no significa NADA. Paso de verdad
    # (revision del 2026-09-10). La tabla normativa de Bishara tambien esta
    # indexada por medida x arcada x sexo x edad, no solo por sexo y edad.
    celdas = {}
    for r in filas:
        if r['edad'] is None:
            continue
        desde = int(r['edad'] // TRAMO_EDAD) * TRAMO_EDAD
        celdas.setdefault((r['clave'], r['sexo'] or '', desde), []).append(
            (r['valor_mm'], r['rut'], r['percentil']))

    salida = []
    for (clave, sexo, desde), vals in sorted(celdas.items()):
        mm = [v[0] for v in vals]
        pacientes = len({v[1] for v in vals})
        fila = {'clave': clave, 'etiqueta': ETIQUETAS_MEDICION.get(clave, clave),
                'sexo': sexo, 'edad_desde': desde, 'edad_hasta': desde + TRAMO_EDAD,
                'mediciones': len(mm), 'pacientes': pacientes,
                'media': None, 'de': None, 'suficiente': pacientes >= MIN_CASOS_CELDA}
        if fila['suficiente']:
            fila['media'] = round(statistics.fmean(mm), 2)
            fila['de'] = round(statistics.stdev(mm), 2) if len(mm) > 1 else None
        salida.append(fila)

    return {
        'mediciones': len(filas),
        'pacientes': len({r['rut'] for r in filas if r['rut']}),
        'celdas': salida,
        'min_casos_celda': MIN_CASOS_CELDA,
        'filtros': f,
        'nota': ('La media y la DE se muestran solo desde %d pacientes por celda. '
                 'Un paciente con varios informes aporta varias mediciones de la '
                 'misma boca: por eso se informan los dos conteos.' % MIN_CASOS_CELDA),
    }


def filas_export(filtros=None):
    """Filas planas para analizar fuera (CSV). Una por medicion.

    ⚠️ Llevan RUT. Son datos de pacientes y se tratan como tales: no existe un
    modo "anonimo" aca a proposito (ver el endpoint en server.py).
    """
    f = dict(filtros or {})
    where, params = _where_muestra(f)
    sql = """
        SELECT m.rut, i.fecha, i.edad, i.sexo, i.sexo_origen, i.medidor,
               m.clave, m.medida, m.arcada, m.valor_mm, m.percentil, m.z,
               m.en_recambio, i.sin_tratamiento_previo, i.conclusion,
               i.id AS informe_id,
               (SELECT o.clase FROM oclusion o WHERE o.informe_id = i.id
                  AND o.sitio = 'clase_molar' AND o.lado = 'der')  AS clase_molar_der,
               (SELECT o.clase FROM oclusion o WHERE o.informe_id = i.id
                  AND o.sitio = 'clase_molar' AND o.lado = 'izq')  AS clase_molar_izq,
               (SELECT o.clase FROM oclusion o WHERE o.informe_id = i.id
                  AND o.sitio = 'clase_canina' AND o.lado = 'der') AS clase_canina_der,
               (SELECT o.clase FROM oclusion o WHERE o.informe_id = i.id
                  AND o.sitio = 'clase_canina' AND o.lado = 'izq') AS clase_canina_izq,
               p.comuna, p.prevision
        FROM mediciones m
        JOIN informes i ON i.id = m.informe_id
        LEFT JOIN pacientes p ON p.rut = m.rut
        WHERE %s AND m.valor_mm IS NOT NULL
        ORDER BY i.fecha, m.rut, m.clave""" % where
    con = basedatos.conectar()
    try:
        return [dict(r) for r in con.execute(sql, params)]
    finally:
        con.close()


def estado():
    """Que hay proyectado y que tan al dia esta. Para el panel."""
    con = basedatos.conectar()
    try:
        conteos = {t: con.execute('SELECT COUNT(*) n FROM %s' % t).fetchone()['n']
                   for t in TABLAS_PROYECTADAS}
        meta = {r['clave']: r['valor'] for r in con.execute('SELECT clave, valor FROM meta')}
        # Calidad: lo que limita lo que se puede preguntar, dicho de frente.
        sin_edad = con.execute(
            'SELECT COUNT(*) n FROM informes WHERE edad IS NULL').fetchone()['n']
        sin_sexo = con.execute(
            "SELECT COUNT(*) n FROM informes WHERE sexo NOT IN ('M','F')").fetchone()['n']
        sin_medidor = con.execute(
            "SELECT COUNT(*) n FROM informes WHERE medidor = ''").fetchone()['n']
        sin_trat = con.execute(
            'SELECT COUNT(*) n FROM informes '
            'WHERE sin_tratamiento_previo IS NULL').fetchone()['n']
        cruzan = con.execute(
            'SELECT COUNT(DISTINCT i.rut) n FROM informes i '
            'WHERE EXISTS (SELECT 1 FROM citas c WHERE c.rut = i.rut)').fetchone()['n']
        con_rut = con.execute(
            "SELECT COUNT(DISTINCT rut) n FROM informes WHERE rut <> ''").fetchone()['n']
    finally:
        con.close()
    errores = {}
    try:
        errores = json.loads(meta.get('errores_adaptadores') or '{}')
    except ValueError:
        pass
    return {
        'conteos': conteos,
        'ultima_proyeccion': meta.get('ultima_proyeccion', ''),
        'al_dia': esta_al_dia(),
        'errores_adaptadores': errores,
        'calidad': {
            'informes_sin_edad': sin_edad,
            'informes_sin_sexo': sin_sexo,
            'informes_sin_medidor': sin_medidor,
            'informes_sin_tratamiento_previo': sin_trat,
            'pacientes_con_informe': con_rut,
            'pacientes_que_cruzan_con_la_agenda': cruzan,
        },
    }
