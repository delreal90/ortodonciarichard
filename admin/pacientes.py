"""
pacientes.py - Base local de pacientes (Ortodoncia Richard)

DentiDesk NO tiene endpoint para buscar pacientes por RUT, pero su regla de
deduplicacion es: una cita se asocia a la ficha existente solo si coinciden
RUT + EMAIL. Si el email difiere, crea una ficha duplicada.

Estrategia (para evitar duplicados y precargar datos):
  1. Construimos una base local {RUT -> datos} barriendo getAgendaDay (que trae
     RUT, nombre, email, telefono por cada cita). Se refresca 2x/dia.
  2. (Opcional) Se siembra con un export completo de pacientes de la clinica.
  3. Al agendar: si el RUT esta en la base, usamos SU email registrado para que
     DentiDesk reconozca al paciente y NO duplique.

PRIVACIDAD: el email/telefono reales viven solo en el backend. Al frontend se
envia SIEMPRE enmascarado (ma***@gm***.cl). El archivo patient_index.json esta
en .gitignore (datos personales, nunca se versiona).

Modular: el bot de WhatsApp puede usar las mismas funciones (lookup, display).
"""

import os
import re
import json
import unicodedata
from pathlib import Path
from datetime import date, timedelta

import jsonstore

# Ruta de la base. Configurable por env para producción (p.ej. un disco
# persistente en Render): PATIENT_INDEX_PATH=/var/data/patient_index.json
INDEX_PATH = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json'))


# ── Almacen ──────────────────────────────────────────────────────────────────

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(INDEX_PATH, default={})


def _load_index():
    return _STORE.load()


def _save_index(idx):
    _STORE.save(idx)


def _limpiar_rut(rut):
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def lookup(rut):
    """Devuelve el registro {nombres, apellidos, email, telefono, genero,
    direccion, comuna, prevision, convenio, fecha_nacimiento, id_paciente}
    o None. Los campos sembrados solo desde la agenda (nunca desde el Excel)
    pueden faltar -- usar .get()."""
    return _load_index().get(_limpiar_rut(rut))


# La tabla nombre->sexo se arma recorriendo la base entera, asi que se cachea.
# Se invalida por el TAMANIO de la base: cuando entran pacientes nuevos --que es
# justo cuando la tabla se queda corta-- se rearma sola. No hace falta mas
# precision: si cambia el sexo de una ficha sin cambiar el total, la tabla vieja
# sigue siendo valida (es una sugerencia sobre miles de casos, no un dato).
_TABLA_GENERO = {'n': -1, 'tabla': {}}


def sugerir_genero(nombres):
    """Sexo probable a partir del nombre de pila, aprendido de esta misma base.

    ⚠️ Es una SUGERENCIA para prellenar un campo que una persona revisa, NO un
    dato declarado. No se guarda en la base y saludo() no la usa: ver el
    docstring de genero.py y el de saludo() aca abajo.
    """
    import genero
    idx = _load_index()
    if _TABLA_GENERO['n'] != len(idx):
        _TABLA_GENERO['tabla'] = genero.construir_tabla(idx.values())
        _TABLA_GENERO['n'] = len(idx)
    return genero.inferir(nombres, _TABLA_GENERO['tabla'])


def saludo(rut_o_rec):
    """'o' | 'a' | 'o/a' segun el genero de la ficha.

    Acepta un RUT (hace lookup) o un registro ya cargado (dict).

    El fallback 'o/a' cuando no se sabe el genero es DELIBERADO: tratarla de
    'Estimado' a una paciente es peor que el generico 'Estimado/a', asi que
    ante la duda NUNCA se adivina. En particular, no se infiere por el
    nombre. Existe sugerir_genero() --que si resuelve 'Maria Jose' vs
    'Jose Maria' con la evidencia de la propia base-- pero aca NO se usa a
    proposito: su sugerencia sirve para prellenar un campo que el doctor ve y
    corrige en un clic, no para encabezar un correo que sale solo y que el
    paciente lee antes de que nadie pueda arreglarlo."""
    rec = rut_o_rec if isinstance(rut_o_rec, dict) else lookup(rut_o_rec)
    genero = (rec or {}).get('genero', '')
    if genero == 'M':
        return 'o'
    if genero == 'F':
        return 'a'
    return 'o/a'


def total():
    return len(_load_index())


def vaciar():
    """Deja la base completamente vacia (para resembrar desde cero)."""
    _save_index({})
    return 0


# ── Limpieza de nombres ──────────────────────────────────────────────────────

def _split_nombre(full):
    """'Juan Jose Leiva Delaveau 3620D' -> ('Juan Jose', 'Leiva Delaveau').
    Quita sufijos de ficha (numero+letras al final) y separa nombres/apellidos
    con heuristica chilena (ultimos 2 tokens = apellidos)."""
    full = re.sub(r'\s+\d+[A-Za-z\-]*\s*$', '', (full or '')).strip()
    toks = full.split()
    if len(toks) <= 1:
        return (toks[0] if toks else ''), ''
    if len(toks) == 2:
        return toks[0], toks[1]
    return ' '.join(toks[:-2]), ' '.join(toks[-2:])


# ── Enmascarado (para mostrar al paciente sin filtrar datos) ──────────────────

def enmascarar_email(email):
    email = (email or '').strip()
    if '@' not in email:
        return ''
    local, dominio = email.split('@', 1)
    partes = dominio.rsplit('.', 1)
    base = partes[0]
    tld = ('.' + partes[1]) if len(partes) > 1 else ''
    def mask(s, keep=2):
        s = s or ''
        return (s[:keep] + '***') if len(s) > keep else (s + '***')
    return f'{mask(local)}@{mask(base)}{tld}'


def enmascarar_telefono(tel):
    digitos = re.sub(r'\D', '', tel or '')
    if not digitos:
        return ''
    ult = digitos[-4:]
    return '*' * max(0, len(digitos) - 4) + ult


def display(rec):
    """Version segura para el frontend: nombres/apellidos + contacto enmascarado.
    Incluye flags tiene_email/tiene_telefono para que el frontend sepa si debe
    pedirle al paciente esos datos (pacientes antiguos sin correo en ficha)."""
    if not rec:
        return {}
    email = rec.get('email', '')
    telefono = rec.get('telefono', '')
    return {
        'nombres':   rec.get('nombres', ''),
        'apellidos': rec.get('apellidos', ''),
        'email_masked':    enmascarar_email(email),
        'telefono_masked': enmascarar_telefono(telefono),
        'tiene_email':    bool(email and '@' in email),
        'tiene_telefono': bool(telefono),
    }


# ── Importar export completo de pacientes (Excel del panel DentiDesk) ─────────

def _normalizar_genero(texto):
    """'Femenino'/'Masculino' (o variantes con tilde/mayuscula/otro idioma)
    -> 'F'/'M'/''. Se guarda SIEMPRE normalizado (nunca el texto crudo del
    Excel): si el dia de manana el export cambia de idioma o capitalizacion,
    el resto del codigo (saludo(), etc.) sigue funcionando igual."""
    t = (texto or '').strip()
    if not t:
        return ''
    t = unicodedata.normalize('NFKD', t)
    t = ''.join(c for c in t if not unicodedata.combining(c))
    primera = t[0].lower()
    if primera == 'f':
        return 'F'
    if primera == 'm':
        return 'M'
    return ''


_DEV_CODE = re.compile(r'^-?[A-Z]{1,3}$')  # codigos de dispositivo: D, DD, DE, -D, -DE


def _es_codigo(tok):
    """True si el token es un codigo interno de la clinica (no parte del nombre):
    ficha numerica (5106A), letras de dispositivo (D/DD/DE/-D), 's/c', o simbolos."""
    t = (tok or '').strip()
    if not t:
        return True
    if t[0].isdigit():
        return True                                  # ficha: 5106A, 3189G-D
    if t.lower() == 's/c':
        return True
    if _DEV_CODE.match(t) and t.upper() == t:
        return True                                  # D, DD, DE, -DE (mayusculas)
    if all(not c.isalnum() for c in t):
        return True                                  # simbolos sueltos (▲, -)
    return False


def _split_nombre_export(full):
    """Formato export: 'Apellidos <codigos internos> Nombres'.
    Usa el bloque de codigos como separador (respeta apellidos compuestos y
    nombres de 2 palabras). Devuelve (nombres, apellidos), ambos SIN codigos.
    Ej: 'Abalos Lira 5106A D Jose Pedro'      -> ('Jose Pedro', 'Abalos Lira')
        'Abalos Perez de Arce Lucia'          -> ('Lucia', 'Abalos Perez de Arce')
        'Abarca Esparza-DD▲ Macarena'         -> ('Macarena', 'Abarca Esparza')"""
    # quitar simbolos y codigos pegados por guion: 'Esparza-DD' -> 'Esparza'
    s = (full or '').replace('▲', ' ')
    s = re.sub(r'-[A-Za-z]{1,3}\b', '', s)
    toks = s.split()

    apellidos, i = [], 0
    while i < len(toks) and not _es_codigo(toks[i]):
        apellidos.append(toks[i]); i += 1
    while i < len(toks) and _es_codigo(toks[i]):        # saltar el bloque de codigos
        i += 1
    nombres = [t for t in toks[i:] if not _es_codigo(t)]

    if not nombres and apellidos:
        # sin codigo separador: el ultimo token es el nombre
        nombres = [apellidos.pop()]
    return ' '.join(nombres), ' '.join(apellidos)


def importar_export_excel(path, reemplazar=False):
    """Siembra/actualiza la base desde el Excel 'Listado de Pacientes Totales'.
    Columnas reales del export: Nombre Paciente, RUT, Edad (se descarta, ver
    abajo), Genero, Telefono, Correo, Direccion, Comuna, Convenio, Prevision.
    reemplazar=True -> parte de cero (borra la base anterior antes de cargar)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h or '').strip().lower() for h in next(rows)]

    def col(*nombres):
        for n in nombres:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return None

    c_nom = col('nombre'); c_rut = col('rut')
    c_tel = col('tel');    c_mail = col('correo', 'email', 'mail')
    # 'Genero' y 'Prevision' traen tilde en el header del Excel ('Género',
    # 'Previsión'): se busca por un fragmento SIN tilde que igual matchea
    # (cae dentro del header con tilde igual, ya que la tilde no rompe la
    # subcadena buscada) en vez de normalizar todos los headers.
    c_gen = col('nero');   c_dir = col('direcc')
    c_com = col('comuna'); c_prev = col('visi')
    c_conv = col('convenio')
    # NO se guarda 'Edad': es un numero que envejece mal (queda desactualizado
    # apenas pasan los meses y nadie se acuerda de que esta podrido) -- mejor
    # ni tenerlo en la base que confiar en un dato que miente solo.

    # RUT-BASURERO: el export de DentiDesk usa un RUT falso compartido para
    # todo lo que no es un paciente real -- bloqueos de agenda, reuniones,
    # fichas de prueba ('BLOQUEO BLOQUEO', 'SORTCH REUNION', 'xxxxx') -- y
    # ademas algun paciente al que nunca le tomaron el RUT. Verificado en el
    # export del 21-07-2026: UN solo RUT (46266, invalido) agrupaba 186
    # nombres distintos. Sin filtrarlo, los 186 colapsan en un unico registro
    # (gana el ultimo) y queda una ficha Frankenstein en la base.
    #
    # El filtro es por CANTIDAD DE NOMBRES DISTINTOS, no por validez del RUT:
    # descartar todo RUT invalido tambien botaria a los extranjeros con
    # pasaporte, que son pacientes legitimos y para los que la base sirve
    # igual (los reconoce por su documento tal cual). Un documento repetido
    # con >3 nombres distintos, en cambio, no es un paciente: es un basurero.
    filas = list(rows)
    _nombres_por_rut = {}
    for r in filas:
        rut_k = _limpiar_rut(str(r[c_rut]) if c_rut is not None and r[c_rut] else '')
        if rut_k:
            nom = str(r[c_nom]).strip() if c_nom is not None and r[c_nom] else ''
            _nombres_por_rut.setdefault(rut_k, set()).add(nom)
    ruts_basurero = {k for k, v in _nombres_por_rut.items() if len(v) > 3}

    idx = {} if reemplazar else _load_index()
    agregados = 0
    descartados = 0
    for r in filas:
        rut = _limpiar_rut(str(r[c_rut]) if c_rut is not None and r[c_rut] else '')
        if not rut:
            continue
        if rut in ruts_basurero:
            descartados += 1
            continue
        # El email puede faltar (pacientes antiguos sin correo en ficha): igual los
        # guardamos para reconocerlos por RUT y precargar su nombre. Al agendar, si
        # no hay email registrado, el paciente lo ingresa.
        email = (str(r[c_mail]).strip() if c_mail is not None and r[c_mail] else '')
        if email and '@' not in email:
            email = ''
        nombres, apellidos = _split_nombre_export(str(r[c_nom]) if c_nom is not None and r[c_nom] else '')
        tel = str(r[c_tel]).strip() if c_tel is not None and r[c_tel] else ''
        genero = _normalizar_genero(str(r[c_gen]) if c_gen is not None and r[c_gen] else '')
        direccion = str(r[c_dir]).strip() if c_dir is not None and r[c_dir] else ''
        comuna = str(r[c_com]).strip() if c_com is not None and r[c_com] else ''
        prevision = str(r[c_prev]).strip() if c_prev is not None and r[c_prev] else ''
        convenio = str(r[c_conv]).strip() if c_conv is not None and r[c_conv] else ''
        nuevo = {'nombres': nombres, 'apellidos': apellidos, 'email': email, 'telefono': tel,
                 'genero': genero, 'direccion': direccion, 'comuna': comuna,
                 'prevision': prevision, 'convenio': convenio}
        if rut not in idx:
            agregados += 1
            idx[rut] = nuevo
        else:
            # Merge, no reemplazo: solo pisa los campos que vengan con valor,
            # para no borrar con vacio lo que ya estaba (p.ej. si esta fila del
            # Excel no trae comuna pero la ficha ya la tenia de una carga previa).
            existente = idx[rut]
            for k, v in nuevo.items():
                if v:
                    existente[k] = v
    _save_index(idx)
    # 'descartados' se devuelve para que el panel pueda mostrarlo: si un dia
    # ese numero se dispara, es senial de que el export cambio de forma (o de
    # que hay un RUT nuevo haciendo de basurero), no de que se perdieron
    # pacientes.
    return {'total': len(idx), 'nuevos': agregados, 'descartados': descartados}


# ── Fecha de nacimiento (export "Listado de Cumpleanos" de DentiDesk) ─────────

_RE_FECHA_DMY = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{4})$')
_RE_RUT_FMT = re.compile(r'^\d{1,3}(?:\.\d{3})*-[\dkK]$')
_RE_ID_FICHA = re.compile(r'id_paciente=(\d+)')

# Edad sobre la cual la fecha se considera error de tipeo y se descarta.
# No es "imposible" en abstracto, pero en una ficha de ortodoncia una fecha
# que implica 110+ anios es sistematicamente un digito mal tecleado en el anio.
_EDAD_MAX_PLAUSIBLE = 110


def _fecha_nac_a_iso(texto, hoy=None):
    """'dd/mm/yyyy' -> 'YYYY-MM-DD'. Devuelve '' si no parsea o si la fecha no
    es plausible (futura, o edad > _EDAD_MAX_PLAUSIBLE).

    El formato del export es dd/mm/yyyy (chileno) -- confirmado con el propio
    nombre del archivo ('24-07-2026') y con valores cuyo primer componente
    supera 12. Si el segundo componente fuera > 12 no seria un mes valido y la
    fila se descarta en vez de adivinar un orden distinto."""
    m = _RE_FECHA_DMY.match((texto or '').strip())
    if not m:
        return ''
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        f = date(y, mo, d)
    except ValueError:
        return ''
    hoy = hoy or date.today()
    if f > hoy:
        return ''
    if edad_a_fecha(f, hoy) > _EDAD_MAX_PLAUSIBLE:
        return ''
    return f.isoformat()


def edad_a_fecha(fecha_nac, referencia=None):
    """Edad cumplida (int) a la fecha de referencia (default hoy).
    Acepta date o 'YYYY-MM-DD'. Devuelve -1 si no se puede calcular."""
    if isinstance(fecha_nac, str):
        try:
            fecha_nac = date.fromisoformat(fecha_nac.strip())
        except ValueError:
            return -1
    if not isinstance(fecha_nac, date):
        return -1
    ref = referencia or date.today()
    return ref.year - fecha_nac.year - ((ref.month, ref.day) < (fecha_nac.month, fecha_nac.day))


def dias_objetivo_cumple(fecha):
    """Los (dia, mes) que se consideran cumpleanios en 'fecha'.

    Normalmente uno solo. La excepcion es el 29 de febrero: en anios NO
    bisiestos esos pacientes se saludan el 28, porque si no no apareceria
    nunca su cumpleanios en 3 de cada 4 anios."""
    objetivo = {(fecha.day, fecha.month)}
    if (fecha.day, fecha.month) == (28, 2):
        try:
            date(fecha.year, 2, 29)
        except ValueError:
            objetivo.add((29, 2))
    return objetivo


def cumplen_el(fecha):
    """Pacientes de la base que cumplen anios en 'fecha' (un date).

    Devuelve [{rut, nombres, apellidos, nombre, fecha_nacimiento, edad,
    id_paciente, email, telefono}] ordenado por nombre. 'edad' son los anios
    que CUMPLE en esa fecha (no la edad actual): por eso se calcula contra
    'fecha' y no contra hoy -- si no, un cumpleanios del 1 de enero visto
    desde el 31 de diciembre daria un anio menos."""
    objetivo = dias_objetivo_cumple(fecha)
    out = []
    for rut, rec in _load_index().items():
        iso = (rec.get('fecha_nacimiento') or '').strip()
        if not iso:
            continue
        try:
            f = date.fromisoformat(iso)
        except ValueError:
            continue
        if (f.day, f.month) not in objetivo:
            continue
        nombres = rec.get('nombres', '')
        apellidos = rec.get('apellidos', '')
        out.append({
            'rut': rut,
            'nombres': nombres,
            'apellidos': apellidos,
            'nombre': (nombres + ' ' + apellidos).strip(),
            'fecha_nacimiento': iso,
            'edad': fecha.year - f.year,
            'id_paciente': rec.get('id_paciente', ''),
            'email': rec.get('email', ''),
            'telefono': rec.get('telefono', ''),
        })
    out.sort(key=lambda p: p['nombre'].lower())
    return out


def cobertura_fecha_nacimiento():
    """{total, con_fecha, pct} -- para el reporte de calidad de datos."""
    idx = _load_index()
    con = sum(1 for r in idx.values() if (r.get('fecha_nacimiento') or '').strip())
    tot = len(idx)
    return {'total': tot, 'con_fecha': con,
            'pct': round(100.0 * con / tot, 1) if tot else 0.0}


def importar_cumpleanos(path, crear_nuevos=True):
    """Importa fechas de nacimiento desde el export 'Listado de Cumpleanos'
    del panel de DentiDesk.

    ⚠️ Pese a la extension .xls, el archivo NO es Excel: es una TABLA HTML
    (empieza con '<table id="tabla_pacientes">'). Por eso se parsea con
    BeautifulSoup y NO con openpyxl (que fallaria con un error de formato).

    Aporta dos campos que la base no tenia:
      - fecha_nacimiento (ISO YYYY-MM-DD) -- lo que el modulo de seguros
        necesitaba y se tipeaba a mano, y lo que habilita grupos etarios.
      - id_paciente (ID interno de DentiDesk, viene en el link a la ficha) --
        permite armar links directos a historial.php sin scrapear.

    Las celdas se identifican POR PATRON (RUT, fecha, link de ficha), no por
    posicion, para que un reordenamiento de columnas en el export no rompa la
    importacion.

    Merge conservador: solo escribe los dos campos nuevos y, si el paciente no
    estaba en la base y crear_nuevos=True, lo crea con su nombre. NUNCA pisa
    email/telefono/genero/direccion ya cargados desde el Excel principal."""
    from bs4 import BeautifulSoup

    html = Path(path).read_text(encoding='utf-8', errors='replace')
    soup = BeautifulSoup(html, 'html.parser')

    idx = _load_index()
    hoy = date.today()
    actualizados = nuevos = sin_rut = fecha_invalida = 0
    sospechosas = []          # >= 100 anios: se guardan, pero se reportan
    # RUTs que vienen MAS DE UNA VEZ en el propio export: son fichas duplicadas
    # en DentiDesk (el problema de dedup RUT+EMAIL que documenta el proyecto).
    # Se cuentan aparte de 'actualizados' porque no son una actualizacion de la
    # base: gana la ultima fila del archivo, y el id_paciente que queda es el de
    # esa ficha. Si este numero crece, hay fichas que fusionar en DentiDesk.
    vistos_en_archivo = set()
    duplicados_archivo = 0

    for tr in soup.find_all('tr'):
        celdas = [c.get_text(strip=True) for c in tr.find_all(['td', 'th'])]
        if not celdas:
            continue

        rut_txt = next((c for c in celdas if _RE_RUT_FMT.match(c)), '')
        rut = _limpiar_rut(rut_txt)
        if not rut:
            # cabecera, fila vacia, o paciente sin RUT registrado
            if any(_RE_FECHA_DMY.match(c) for c in celdas):
                sin_rut += 1
            continue

        fecha_txt = next((c for c in celdas if _RE_FECHA_DMY.match(c)), '')
        iso = _fecha_nac_a_iso(fecha_txt, hoy)
        if not iso:
            if fecha_txt:
                fecha_invalida += 1
            continue

        enlace = tr.find('a', href=_RE_ID_FICHA)
        id_pac = ''
        nombre_txt = ''
        if enlace:
            m = _RE_ID_FICHA.search(enlace.get('href', ''))
            id_pac = m.group(1) if m else ''
            nombre_txt = enlace.get_text(strip=True)

        primera_vez = rut not in vistos_en_archivo
        if not primera_vez:
            duplicados_archivo += 1
        vistos_en_archivo.add(rut)

        rec = idx.get(rut)
        if rec is None:
            if not crear_nuevos:
                continue
            nombres, apellidos = _split_nombre_export(nombre_txt)
            rec = {'nombres': nombres, 'apellidos': apellidos, 'email': '', 'telefono': ''}
            idx[rut] = rec
            nuevos += 1
        elif primera_vez:
            # ya existia en la base (siembra del Excel principal o import previo).
            # Solo se cuenta la 1a fila del RUT: las repetidas ya van en
            # duplicados_archivo y contarlas aca inflaria el numero.
            actualizados += 1

        rec['fecha_nacimiento'] = iso
        if id_pac:
            rec['id_paciente'] = id_pac

        if edad_a_fecha(iso, hoy) >= 100:
            sospechosas.append(rut)

    _save_index(idx)
    return {'total': len(idx), 'actualizados': actualizados, 'nuevos': nuevos,
            'sin_rut': sin_rut, 'fecha_invalida': fecha_invalida,
            'duplicados_archivo': duplicados_archivo,
            'sospechosas': len(sospechosas),
            'cobertura': cobertura_fecha_nacimiento()}


# ── Mezcla desde la Ficha de Primera Consulta (Google Form) ──────────────────

# Campos de contacto/demograficos que la ficha puede aportar. La parte CLINICA
# del formulario NO entra a la base (es de DentiDesk).
_CAMPOS_FICHA = ('nombres', 'apellidos', 'fecha_nacimiento', 'telefono',
                 'direccion', 'comuna')


def merge_fichas(fichas, crear_nuevos=True):
    """Suma a la base los datos de contacto de la Ficha de Primera Consulta.

    RELLENO CONSERVADOR: cada campo se escribe SOLO si en la base esta vacio.
    Nunca pisa un dato ya cargado (DentiDesk es la fuente autoritaria del
    nombre, y el correo NO se puede pisar por la dedup RUT+EMAIL: en un menor el
    correo del formulario es el del apoderado). El email se trata igual que el
    resto -- fill-empty -- pero se cuenta aparte por ser el mas sensible.

    `fichas` es la lista que arma fichas.interpretar(). Devuelve un resumen."""
    idx = _load_index()
    nuevos = actualizados = sin_rut_valido = emails_rellenados = 0

    for f in fichas:
        import scheduling
        if not scheduling.rut_valido(f.get('rut', '')):
            sin_rut_valido += 1                 # RUT basura (ej. "5-5"): se descarta
            continue
        rut = _limpiar_rut(f['rut'])

        rec = idx.get(rut)
        es_nuevo = rec is None
        if es_nuevo:
            if not crear_nuevos:
                continue
            rec = {'nombres': '', 'apellidos': '', 'email': '', 'telefono': ''}

        cambio = False
        for campo in _CAMPOS_FICHA:
            valor = (f.get(campo) or '').strip()
            if valor and not (rec.get(campo) or '').strip():
                rec[campo] = valor
                cambio = True

        # Email: fill-empty, NUNCA pisa (ver docstring).
        email = (f.get('email') or '').strip()
        if email and '@' in email and not (rec.get('email') or '').strip():
            rec['email'] = email
            emails_rellenados += 1
            cambio = True

        if es_nuevo:
            idx[rut] = rec
            nuevos += 1
        elif cambio:
            actualizados += 1

    _save_index(idx)
    return {'total': len(idx), 'nuevos': nuevos, 'actualizados': actualizados,
            'emails_rellenados': emails_rellenados, 'sin_rut_valido': sin_rut_valido,
            'cobertura': cobertura_fecha_nacimiento()}


# ── Construccion de la base desde la agenda (getAgendaDay) ────────────────────

def construir_desde_agenda(cfg, dias_atras=120, dias_adelante=120, max_workers=6, hoy=None):
    """Barre getAgendaDay en una ventana de dias y arma/actualiza la base.
    Solo guarda pacientes que tengan RUT y email (los unicos utiles para dedup).

    `hoy` es inyectable para poder probar sin depender del dia en que se corra el
    test: la ventana descarta sabados y domingos, asi que con dias_atras=0 y
    dias_adelante=0 un fin de semana no barre NADA."""
    import requests
    import dentidesk
    import fechas
    from concurrent.futures import ThreadPoolExecutor

    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    hoy = hoy or fechas.hoy_chile()   # date.today() es UTC: en Render, de noche
                                      # barria una ventana corrida un dia
    dias = [hoy + timedelta(days=k)
            for k in range(-dias_atras, dias_adelante + 1)
            if (hoy + timedelta(days=k)).weekday() < 5]

    def scan(d):
        try:
            token = dentidesk._auth_token(cfg)
            r = requests.post(url, json={'IdLocation': dd['id_location'],
                                         'Date': d.isoformat(), 'Token': token}, timeout=25)
            if r.status_code != 200:
                return []
            return (r.json() or {}).get('data', [])
        except Exception:
            return []

    # Recolectar primero (el escaneo tarda minutos), SIN tocar el archivo.
    recolectado = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for citas in pool.map(scan, dias):
            for c in citas:
                rut = _limpiar_rut(str(c.get('PatientDocument', '')))
                email = (c.get('PatientEmail') or '').strip()
                if not rut or not email or '@' not in email:
                    continue
                nombres, apellidos = _split_nombre(c.get('PatientName', ''))
                recolectado[rut] = {
                    'nombres': nombres,
                    'apellidos': apellidos,
                    'email': email,
                    'telefono': (c.get('Phone') or '').strip(),
                }
    # Merge atomico al FINAL: recargar lo ultimo (por si entro una importacion u
    # otra reserva durante el escaneo) y combinar SIN pisar. Nunca borra la semilla.
    idx = _load_index()
    agregados = sum(1 for r in recolectado if r not in idx)
    # Merge POR REGISTRO (no idx.update(recolectado) plano): recolectado[rut] solo
    # trae 4 campos (nombres/apellidos/email/telefono, lo unico que expone
    # getAgendaDay). Un update() de diccionario reemplaza la ficha ENTERA, asi que
    # borraria genero/direccion/comuna/prevision/convenio sembrados desde el Excel.
    # Este barrido corre 2x al dia, asi que sin este merge la siembra se perderia
    # a las pocas horas de haberla cargado.
    for rut, nuevo in recolectado.items():
        existente = idx.get(rut, {})
        existente.update({k: v for k, v in nuevo.items() if v})
        idx[rut] = existente
    _save_index(idx)
    return {'total': len(idx), 'nuevos': agregados, 'dias': len(dias)}
