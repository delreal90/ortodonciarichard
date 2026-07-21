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

# Ruta de la base. Configurable por env para producción (p.ej. un disco
# persistente en Render): PATIENT_INDEX_PATH=/var/data/patient_index.json
INDEX_PATH = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json'))


# ── Almacen ──────────────────────────────────────────────────────────────────

def _load_index():
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return {}
    return {}


def _save_index(idx):
    # Escritura atomica: escribe a un temporal y renombra. Asi, si el refresco
    # corre mientras alguien agenda, nadie lee un archivo a medio escribir.
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(idx, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, INDEX_PATH)


def _limpiar_rut(rut):
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def lookup(rut):
    """Devuelve el registro {nombres, apellidos, email, telefono, genero,
    direccion, comuna, prevision, convenio} o None. Los campos sembrados solo
    desde la agenda (nunca desde el Excel) pueden faltar -- usar .get()."""
    return _load_index().get(_limpiar_rut(rut))


def saludo(rut_o_rec):
    """'o' | 'a' | 'o/a' segun el genero de la ficha.

    Acepta un RUT (hace lookup) o un registro ya cargado (dict).

    El fallback 'o/a' cuando no se sabe el genero es DELIBERADO: tratarla de
    'Estimado' a una paciente es peor que el generico 'Estimado/a', asi que
    ante la duda NUNCA se adivina. En particular, no se infiere por el
    nombre -- 'Maria Jose' y 'Jose Maria' romperian cualquier heuristica
    basada en el primer o el ultimo token."""
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


# ── Construccion de la base desde la agenda (getAgendaDay) ────────────────────

def construir_desde_agenda(cfg, dias_atras=120, dias_adelante=120, max_workers=6):
    """Barre getAgendaDay en una ventana de dias y arma/actualiza la base.
    Solo guarda pacientes que tengan RUT y email (los unicos utiles para dedup)."""
    import requests
    import dentidesk
    from concurrent.futures import ThreadPoolExecutor

    dd = cfg['dentidesk']
    url = f"{dd['base_url'].rstrip('/')}/api/agenda/getAgendaDay.php"
    hoy = date.today()
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
