"""
link_aseguradora.py — Links para que el PACIENTE actualice su aseguradora.

POR QUE EXISTE
--------------
Al pie del correo con el formulario de seguro va un enlace "¿Cambió su compañía
aseguradora? Actualícela aquí". El paciente lo abre, elige su nueva aseguradora y
el sistema la guarda (y le reenvía el formulario corregido de su última boleta).

Molde exacto de link_agenda.py: token aleatorio opaco (`secrets.token_urlsafe`)
que NO codifica nada; el RUT vive server-side en el store y este módulo nunca lo
imprime ni lo loguea (el repo es público y esto toca datos de pacientes).

ESQUEMA
-------
{ "links": { "<token>": {
    "rut": "12345678", "creado": "...ISO...", "expira": "YYYY-MM-DD",
    "usado": null } } }

`usado` es `None` hasta que se usa; luego pasa a ser el timestamp de marcar_usado().
A diferencia del link de agenda, NO se invalida al primer uso desde resolver() — el
paciente podría equivocarse y volver a entrar con el mismo link mientras no venza; el
endpoint decide si sellarlo. resolver() sí respeta 'usado' si el endpoint lo selló.

CONTRATO DE resolver(token)
---------------------------
{'ok': True, **datos} si sirve, o
{'ok': False, 'motivo': 'no_existe'|'expirado'|'usado'} si no. Mismo criterio que
link_agenda.resolver: el caso "no existe" también se distingue para dar al paciente
un mensaje útil.
"""

import os
import secrets
import threading
from pathlib import Path
from datetime import timedelta

import avisos      # rut_key: clave canonica compartida del proyecto.
import fechas      # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
LINKS_PATH = Path(os.environ.get('LINKS_ASEGURADORA_PATH',
                                 _BASE_DIR / 'links_aseguradora.json'))

_LOCK = threading.Lock()
_STORE = jsonstore.JsonStore(LINKS_PATH, indent=2,
                             default={'links': {}}, claves={'links': {}})

# Vencimiento por defecto largo (60 días): el correo del formulario puede quedar sin
# abrir un buen rato, y el paciente cambia de seguro de tanto en tanto — no es un link
# de un solo uso inmediato como el de agenda.
_DIAS_EXPIRA_DEFAULT = 60
_DIAS_RETENCION_VENCIDOS = 90


def _load():
    return _STORE.load()


def _save(datos):
    _STORE.save(datos)


def _podar(datos):
    limite = (fechas.hoy_chile() - timedelta(days=_DIAS_RETENCION_VENCIDOS)).isoformat()
    links = datos.get('links', {})
    for tok in [t for t, l in links.items() if l.get('expira', '') < limite]:
        del links[tok]


def crear(rut, base_url, dias_expira=_DIAS_EXPIRA_DEFAULT):
    """Crea un link para que 'rut' actualice su aseguradora. `base_url` es el origen
    del backend que sirve la página (ej. request.url_root sin la barra final). Devuelve
    {'token', 'url', 'expira'} con url = {base}/actualizar-seguro?token=<token>.

    Igual que link_agenda.crear: un token existente JAMÁS se pisa (reintenta 5 veces;
    con 72 bits basta la primera). RuntimeError si las 5 chocan, para no arriesgar
    sobreescribir el link de otro paciente."""
    clave_rut = avisos.rut_key(rut)
    expira = (fechas.hoy_chile() + timedelta(days=dias_expira)).isoformat()
    creado = fechas.ahora_chile().isoformat()

    with _LOCK:
        datos = _load()
        links = datos.setdefault('links', {})
        token = None
        for _ in range(5):
            candidato = secrets.token_urlsafe(9)
            if candidato not in links:
                token = candidato
                break
        if token is None:
            raise RuntimeError(
                'link_aseguradora.crear: 5 colisiones de token seguidas — '
                'no se generó el link para no arriesgar pisar uno existente.')
        links[token] = {'rut': clave_rut, 'creado': creado,
                        'expira': expira, 'usado': None}
        _podar(datos)
        _save(datos)

    base = (base_url or '').rstrip('/')
    return {'token': token, 'url': f'{base}/actualizar-seguro?token={token}',
            'expira': expira}


def get(token):
    if not token:
        return None
    return (_load().get('links') or {}).get(token)


def resolver(token):
    registro = get(token)
    if not registro:
        return {'ok': False, 'motivo': 'no_existe'}
    if registro.get('usado'):
        return {'ok': False, 'motivo': 'usado'}
    if registro.get('expira', '') < fechas.hoy_chile().isoformat():
        return {'ok': False, 'motivo': 'expirado'}
    return {'ok': True, **registro}


def marcar_usado(token):
    with _LOCK:
        datos = _load()
        registro = (datos.get('links') or {}).get(token)
        if not registro:
            return None
        registro['usado'] = fechas.ahora_chile().isoformat()
        _save(datos)
        return registro
