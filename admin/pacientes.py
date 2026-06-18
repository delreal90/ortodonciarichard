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
    """Devuelve el registro {nombres, apellidos, email, telefono} o None."""
    return _load_index().get(_limpiar_rut(rut))


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
    """Version segura para el frontend: nombres/apellidos + contacto enmascarado."""
    if not rec:
        return {}
    return {
        'nombres':   rec.get('nombres', ''),
        'apellidos': rec.get('apellidos', ''),
        'email_masked':    enmascarar_email(rec.get('email', '')),
        'telefono_masked': enmascarar_telefono(rec.get('telefono', '')),
    }


# ── Importar export completo de pacientes (Excel del panel DentiDesk) ─────────

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
    Columnas esperadas: Nombre Paciente, RUT, Edad, Genero, Telefono, Correo, ...
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

    idx = {} if reemplazar else _load_index()
    agregados = 0
    for r in rows:
        rut = _limpiar_rut(str(r[c_rut]) if c_rut is not None and r[c_rut] else '')
        email = (str(r[c_mail]).strip() if c_mail is not None and r[c_mail] else '')
        if not rut or not email or '@' not in email:
            continue
        nombres, apellidos = _split_nombre_export(str(r[c_nom]) if c_nom is not None and r[c_nom] else '')
        tel = str(r[c_tel]).strip() if c_tel is not None and r[c_tel] else ''
        if rut not in idx:
            agregados += 1
        idx[rut] = {'nombres': nombres, 'apellidos': apellidos, 'email': email, 'telefono': tel}
    _save_index(idx)
    return {'total': len(idx), 'nuevos': agregados}


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
    idx.update(recolectado)
    _save_index(idx)
    return {'total': len(idx), 'nuevos': agregados, 'dias': len(dias)}
