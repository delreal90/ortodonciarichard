"""
fichas.py - Alimenta la base de pacientes desde el Google Sheet de la
"Ficha Unica de Primera Consulta".

QUE ES
------
Un Google Form que el paciente (o su apoderado) llena ANTES de la primera
consulta. Sus respuestas caen en un Google Sheet. Este modulo lo lee con la
MISMA cuenta de servicio de Google que ya usa drive_backup.py (privado: el Sheet
se comparte solo con esa cuenta, no se publica), toma los datos de CONTACTO y
DEMOGRAFICOS, y los suma a la base local de pacientes (patient_index.json).

QUE NO HACE
-----------
- No toca la parte CLINICA del formulario (antecedentes medicos, apnea, etc.):
  eso es de la ficha de DentiDesk, no de esta base.
- No PISA datos ya cargados. Rellena solo lo que falte. En particular NUNCA
  sobrescribe un correo: en un paciente menor el correo del formulario es el del
  APODERADO, y pisar el correo que DentiDesk tiene romperia su deduplicacion
  RUT+EMAIL (crearia fichas duplicadas). Ver pacientes.merge_fichas().

LAS DOS RAMAS DEL FORMULARIO
----------------------------
El form pregunta distinto segun si el paciente es adulto o menor, asi que el
mismo dato aparece en columnas distintas:
- Nombre: "Nombres"+"Apellidos" (adulto) o "Nombre y Apellidos del paciente" (menor).
- Fecha de nacimiento: hay DOS columnas con ese titulo (una por rama).
Por eso el mapeo resuelve cada campo a una LISTA de columnas y toma la primera
con dato. Se identifica por el TITULO de la columna (no por su posicion), asi un
reordenamiento del formulario no lo rompe; si un titulo cambia, el campo queda
vacio y se loguea, nunca revienta.

CONFIG
------
FICHA_SHEET_ID  - id del Google Sheet. NO se hardcodea (el repo es publico y el
                  id apunta a datos de pacientes): va como env var en Render y en
                  el secreto local. Sin el, el modulo queda apagado.
Credenciales: las mismas de drive_backup (GOOGLE_SERVICE_ACCOUNT_JSON o el
              archivo local), con scope de solo-lectura de Sheets.
"""

import os
import json
import unicodedata
from pathlib import Path

import jsonstore
import fechas

SHEET_ID = os.environ.get('FICHA_SHEET_ID', '').strip()
_SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly']

# Estado de la ultima sincronizacion (para el panel). En el disco persistente.
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
_ESTADO = jsonstore.JsonStore(_BASE_DIR / 'fichas_estado.json', default={})


# ── Mapeo de columnas (por TITULO, tolerante a reordenar) ────────────────────
# Cada campo logico -> lista de titulos posibles, EN ORDEN DE PRIORIDAD. Se
# toma la primera columna con dato. Los titulos van normalizados (sin tildes,
# minusculas). Cuando un titulo aparece repetido en el Sheet (las dos ramas),
# se recogen TODAS sus columnas y se prueba en orden de aparicion.
_CAMPOS = {
    'rut':          ['rut del paciente'],
    'nombres':      ['nombres'],
    'apellidos':    ['apellidos'],
    'nombre_junto': ['nombre y apellidos del paciente'],   # rama menor
    'fecha_nac':    ['fecha de nacimiento'],                # aparece 2 veces
    'email':        ['direccion de correo electronico', 'email de contacto',
                     'email del paciente'],
    'telefono':     ['celular de contacto', 'celular del paciente'],
    'direccion':    ['direccion'],                          # OJO: exacto, no "direccion de correo"
    'comuna':       ['comuna, ciudad', 'comuna'],
}


def _norm(s):
    s = unicodedata.normalize('NFKD', (s or '').strip())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ' '.join(s.lower().split())


def _norm_letras(s):
    """Normaliza a solo-letras minusculas (sin tildes/espacios/guiones/simbolos).
    Se usa para mapear la opcion elegida en el desplegable de aseguradora contra
    las keys del catalogo de seguros.py, que ya vienen en ese mismo formato
    (ej. 'BICE VIDA' -> 'bicevida', 'VIDA CAMARA' -> 'vidacamara')."""
    s = unicodedata.normalize('NFKD', (s or '').strip())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return ''.join(c for c in s.lower() if c.isalpha())


# Titulo de la columna de seguro: NO va en _CAMPOS/_indices porque su
# puntuacion exacta ("¿Tiene Seguro Complementario? ¿Cuál?") es incierta y el
# usuario va a rediseñar el formulario -- se busca por SUBSTRING sobre el
# titulo normalizado, no por match exacto.
_TITULO_SEGURO = 'seguro complementario'


def _indice_seguro(headers):
    """Indices de columna cuyo titulo normalizado CONTIENE 'seguro complementario'."""
    return [i for i, h in enumerate(headers) if _TITULO_SEGURO in _norm(h)]


def _mapear_aseguradora(texto):
    """Texto de la opcion elegida en el desplegable -> key de seguros.py.
    '-- No tengo --' -> seguros.SIN_SEGURO; '-- OTRA --' o vacio -> ''
    (no se asigna); una opcion que calce (normalizada a solo-letras) con una
    key del catalogo -> esa key; si no calza con nada -> ''."""
    import seguros
    norm = _norm_letras(texto)
    if not norm or norm == 'otra':
        return ''
    if norm == 'notengo':
        return seguros.SIN_SEGURO
    claves = {_norm_letras(a['key']): a['key']
              for a in seguros.listar_aseguradoras(solo_activas=False)}
    return claves.get(norm, '')


def _mapa(headers):
    """titulo-normalizado -> lista de indices de columna (en orden)."""
    m = {}
    for i, h in enumerate(headers):
        m.setdefault(_norm(h), []).append(i)
    return m


def _indices(mapa, campo):
    """Indices de columna para un campo logico, en orden de prioridad."""
    out = []
    for titulo in _CAMPOS[campo]:
        out.extend(mapa.get(titulo, []))
    return out


def _valor(row, indices):
    for i in indices:
        if i < len(row):
            v = (row[i] or '').strip()
            if v:
                return v
    return ''


# ── Lectura del Sheet ────────────────────────────────────────────────────────

def _credenciales():
    """Misma cuenta de servicio que drive_backup, con scope de Sheets."""
    from google.oauth2 import service_account
    raw = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if raw:
        return service_account.Credentials.from_service_account_info(
            json.loads(raw), scopes=_SCOPES)
    path = Path(os.environ.get('GOOGLE_SERVICE_ACCOUNT_PATH',
                               Path(__file__).parent / 'drive_service_account.json'))
    if not path.exists():
        return None
    return service_account.Credentials.from_service_account_file(str(path), scopes=_SCOPES)


def habilitado():
    """True si hay id de Sheet configurado y credenciales disponibles."""
    return bool(SHEET_ID) and _credenciales() is not None


def leer_filas(sheet_id=None):
    """Devuelve (headers, filas) del Sheet. Lanza si no hay credenciales/acceso."""
    from googleapiclient.discovery import build
    sid = sheet_id or SHEET_ID
    if not sid:
        raise RuntimeError('FICHA_SHEET_ID no configurado')
    creds = _credenciales()
    if not creds:
        raise RuntimeError('Sin credenciales de Google (cuenta de servicio)')
    svc = build('sheets', 'v4', credentials=creds, cache_discovery=False)
    # A1:BZ cubre holgado las ~57 columnas del formulario.
    vals = svc.spreadsheets().values().get(
        spreadsheetId=sid, range='A1:BZ200000').execute().get('values', [])
    if not vals:
        return [], []
    return vals[0], vals[1:]


# ── Interpretacion ───────────────────────────────────────────────────────────

def interpretar(headers, filas):
    """Convierte las filas crudas en fichas {rut, nombres, apellidos,
    fecha_nacimiento, email, telefono, direccion, comuna}.

    Dedup: si un paciente lleno el formulario dos veces, gana la ULTIMA
    respuesta. Google Forms agrega las respuestas en orden cronologico, asi que
    basta con que las filas mas nuevas pisen a las viejas al indexar por RUT
    (no hace falta parsear la marca temporal)."""
    mapa = _mapa(headers)
    idx = {c: _indices(mapa, c) for c in _CAMPOS}
    idx_seguro = _indice_seguro(headers)
    if not idx_seguro:
        print('[fichas] columna de seguro complementario no encontrada en el Sheet; '
              'seguro_key quedara vacio en todas las fichas')
    import pacientes

    por_rut = {}
    sin_rut_valido = 0
    for row in filas:
        rut_txt = _valor(row, idx['rut'])
        if not rut_txt:
            continue

        nombres = _valor(row, idx['nombres'])
        apellidos = _valor(row, idx['apellidos'])
        if not (nombres or apellidos):
            # rama menor: nombre y apellidos juntos en una sola columna
            junto = _valor(row, idx['nombre_junto'])
            if junto:
                nombres, apellidos = pacientes._split_nombre(junto)

        ficha = {
            'rut':               rut_txt,
            'nombres':           nombres,
            'apellidos':         apellidos,
            'fecha_nacimiento':  pacientes._fecha_nac_a_iso(_valor(row, idx['fecha_nac'])),
            'email':             _valor(row, idx['email']),
            'telefono':          _valor(row, idx['telefono']),
            'direccion':         _valor(row, idx['direccion']),
            # "Comuna, Ciudad" puede venir junto -> nos quedamos con la comuna.
            'comuna':            _valor(row, idx['comuna']).split(',')[0].strip(),
            # NO whitelisteado por pacientes.merge_fichas (solo lee _CAMPOS_FICHA
            # + rut/email) -- lo consume aparte seguros.asignar_si_vacio() en
            # fichas.sincronizar(). Ver seguros.py.
            'seguro_key':        _mapear_aseguradora(_valor(row, idx_seguro)),
        }
        # Clave por RUT limpio; las respuestas mas nuevas pisan a las viejas.
        por_rut[pacientes._limpiar_rut(rut_txt)] = ficha

    return list(por_rut.values()), sin_rut_valido


# ── Sincronizacion (lo que llama el scheduler y el boton del panel) ──────────

def sincronizar(sheet_id=None):
    """Lee el Sheet, interpreta y mezcla en la base. Devuelve un resumen y lo
    guarda como 'ultima corrida' para el panel. Nunca lanza hacia afuera: si
    algo falla, devuelve {'ok': False, 'error': ...}."""
    import pacientes
    try:
        headers, filas = leer_filas(sheet_id)
        fichas, _ = interpretar(headers, filas)
        res = pacientes.merge_fichas(fichas)

        # Asignacion de aseguradora: best-effort, aparte del merge de pacientes
        # (que es lo importante) -- un fallo aca nunca debe tumbar la sincronizacion.
        seguros_asignados = 0
        try:
            import seguros
            for f in fichas:
                seguro_key = f.get('seguro_key')
                if not seguro_key:
                    continue
                rut = pacientes._limpiar_rut(f.get('rut', ''))
                if not rut:
                    continue
                if seguros.asignar_si_vacio(rut, seguro_key):
                    seguros_asignados += 1
        except Exception as e:
            print(f'[fichas] fallo asignando aseguradoras (no afecta el merge de pacientes): {e!r}')

        resumen = {'ok': True, 'respuestas': len(filas), 'fichas': len(fichas), **res,
                   'seguros_asignados': seguros_asignados,
                   'cuando': fechas.ahora_chile().isoformat(timespec='seconds')}
    except Exception as e:
        resumen = {'ok': False, 'error': str(e),
                   'cuando': fechas.ahora_chile().isoformat(timespec='seconds')}
        print(f'[fichas] fallo la sincronizacion: {e!r}')
    _ESTADO.save(resumen)
    return resumen


def estado():
    """Ultima corrida (para el panel), mas si el modulo esta habilitado."""
    return {'habilitado': habilitado(), 'sheet_configurado': bool(SHEET_ID),
            'ultima': _ESTADO.load()}
