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
    INDEX_PATH.write_text(json.dumps(idx, ensure_ascii=False), encoding='utf-8')


def _limpiar_rut(rut):
    return ''.join(c for c in (rut or '').upper() if c.isdigit() or c == 'K')


def lookup(rut):
    """Devuelve el registro {nombres, apellidos, email, telefono} o None."""
    return _load_index().get(_limpiar_rut(rut))


def total():
    return len(_load_index())


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

def _split_nombre_export(full):
    """Formato export: 'Apellido1 Apellido2 <ficha> Nombres'.
    Ej: 'Abalos Lira 5106A D Jose Pedro' -> ('Jose Pedro', 'Abalos Lira').
    Devuelve (nombres, apellidos)."""
    toks = (full or '').split()
    # ubicar el token de ficha (empieza con digito)
    idx = next((i for i, t in enumerate(toks) if t and t[0].isdigit()), None)
    if idx is None:
        # sin ficha: heuristica -> primeros 2 = apellidos
        if len(toks) <= 2:
            return (toks[-1] if toks else ''), (toks[0] if len(toks) > 1 else '')
        return ' '.join(toks[2:]), ' '.join(toks[:2])
    apellidos = toks[:idx]
    resto = toks[idx + 1:]
    # saltar sufijos de ficha tras el numero (cortos en mayuscula: D, DD, DE, -D)
    while resto and re.fullmatch(r'-?[A-Z]{1,3}', resto[0]):
        resto = resto[1:]
    return ' '.join(resto), ' '.join(apellidos)


def importar_export_excel(path):
    """Siembra/actualiza la base desde el Excel 'Listado de Pacientes Totales'.
    Columnas esperadas: Nombre Paciente, RUT, Edad, Genero, Telefono, Correo, ..."""
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

    idx = _load_index()
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

    idx = _load_index()
    agregados = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for citas in pool.map(scan, dias):
            for c in citas:
                rut = _limpiar_rut(str(c.get('PatientDocument', '')))
                email = (c.get('PatientEmail') or '').strip()
                if not rut or not email or '@' not in email:
                    continue
                nombres, apellidos = _split_nombre(c.get('PatientName', ''))
                if rut not in idx:
                    agregados += 1
                idx[rut] = {
                    'nombres': nombres,
                    'apellidos': apellidos,
                    'email': email,
                    'telefono': (c.get('Phone') or '').strip(),
                }
    _save_index(idx)
    return {'total': len(idx), 'nuevos': agregados, 'dias': len(dias)}
