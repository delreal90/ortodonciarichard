"""
link_agenda.py — Links de agenda pre-cargados (paciente + doctor + motivo).

POR QUE EXISTE
--------------
Mejora 2 del plan: la secretaria, con un paciente abierto en DentiDesk, aprieta
un boton en la extension F2 y el sistema genera un LINK corto. El paciente lo
abre y cae directo a elegir fecha y hora en la agenda online -- ya no tiene que
re-tipear su RUT ni elegir doctor/motivo, que la secretaria ya sabe de memoria
porque tiene la ficha abierta.

Este modulo es la mitad "backend puro": guarda el link y lo resuelve. Los
endpoints que lo consumen (uno para que F2 pida `crear()`, otro para que la
pagina de agenda resuelva el token de la URL) los escribe otra tarea aparte.

QUE GUARDA Y QUE NO
--------------------
El token es aleatorio (`secrets.token_urlsafe`) y no codifica nada -- no hay
forma de "leer" el RUT a partir del token. El registro SI guarda el RUT (es lo
que hace funcionar el link), pero este modulo nunca lo imprime ni lo manda a un
log: el repo es publico y esto toca datos de pacientes.

ESQUEMA
-------
{ "links": { "<token>": {
    "rut": "12345678", "doctor": "alberto", "motivo": "control_fijo",
    "creado": "2026-07-31T10:00:00", "expira": "2026-08-30",
    "usado": null, "id_agenda_origen": "..." } } }

`usado` es `None` mientras el link no se ha usado; al usarse pasa a ser el
timestamp ISO de `marcar_usado()`.

CONTRATO DE `resolver(token)`
------------------------------
Devuelve `{'ok': True, **datos}` si el link es valido (existe, no vencio, no se
uso), o `{'ok': False, 'motivo': 'no_existe'|'expirado'|'usado'}` si no. Se
elige un dict con 'ok' (en vez de None/dict como en avisos.evaluar) porque aca
el caso "no existe" TAMBIEN necesita poder distinguirse de los otros dos, y los
tres casos usan la misma forma -- asi el endpoint que lo consuma no tiene que
acordarse de un contrato distinto para cada tipo de falla, y darle al paciente
un mensaje util (por ejemplo "este link ya se uso" es un error mucho mas amable
que un 404 generico).
"""

import os
import secrets
import threading
from pathlib import Path
from datetime import timedelta

import avisos      # rut_key: clave canonica compartida del proyecto.
import fechas      # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore   # guardado atomico con lock. Ver jsonstore.py.

# Mismo calculo de base que control_dental.py: junto a patient_index.json, en
# el disco persistente de Render (sobrevive a los redeploys). El .gitignore ya
# cubre 'links_agenda.json' -- lo puso el coordinador, no se toca aca.
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
LINKS_PATH = Path(os.environ.get('LINKS_AGENDA_PATH', _BASE_DIR / 'links_agenda.json'))

_LOCK = threading.Lock()

# Escritura atomica + lock + respaldo si el archivo se corrompe: ver jsonstore.py.
_STORE = jsonstore.JsonStore(LINKS_PATH, indent=2,
                              default={'links': {}}, claves={'links': {}})

# Base por defecto de la URL del link. El sitio publico vive en este dominio;
# se puede sobreescribir por env (Render) o por cfg['clinica']['sitio_url']
# (config del proyecto) para no tener que tocar codigo si el dominio cambia o
# para apuntar a un entorno de prueba.
_SITIO_URL_BASE_DEFAULT = 'https://www.ortodonciarichard.cl'

# Margen de poda: cuanto tiempo despues de vencido se conserva un link antes de
# descartarlo del store. No es 0 (recien vencido) porque conviene poder
# explicarle a la secretaria "ese link se vencio el 12 de agosto" durante un
# tiempo razonable; tampoco es para siempre, porque el JSON no puede crecer sin
# limite. 90 dias es el mismo margen que usa control_dental.py para su propia
# poda de 'vistos'.
_DIAS_RETENCION_VENCIDOS = 90


def _load():
    return _STORE.load()


def _save(datos):
    _STORE.save(datos)


def _podar(datos):
    """Descarta del store los links vencidos hace mas de
    _DIAS_RETENCION_VENCIDOS. Se llama desde crear() (barato, y ya se esta
    escribiendo el store igual) -- NO desde resolver(), que es de solo lectura
    y se llama mucho mas seguido (cada vez que el paciente abre el link)."""
    limite = (fechas.hoy_chile() - timedelta(days=_DIAS_RETENCION_VENCIDOS)).isoformat()
    links = datos.get('links', {})
    a_borrar = [tok for tok, l in links.items() if l.get('expira', '') < limite]
    for tok in a_borrar:
        del links[tok]
    return len(a_borrar)


def _url_base(cfg=None):
    """Base de la URL del link, con la prioridad: env SITIO_URL_BASE > config
    de la clinica > fallback fijo. El fallback existe para que crear() nunca
    reviente por falta de config -- peor un link con el dominio "de siempre"
    que uno que no se genera."""
    env = os.environ.get('SITIO_URL_BASE')
    if env:
        return env.rstrip('/')
    if cfg:
        sitio = (cfg.get('clinica') or {}).get('sitio_url')
        if sitio:
            return sitio.rstrip('/')
    return _SITIO_URL_BASE_DEFAULT


def crear(rut, doctor_key, motivo_key, cfg=None, dias_expira=30, id_agenda_origen=''):
    """Crea un link de agenda para 'rut' con doctor y motivo ya elegidos por la
    secretaria. Devuelve {'token', 'url', 'expira'}.

    Token: secrets.token_urlsafe(9) son 72 bits de entropia -- combinado con el
    rate limit del backend (ver test_seguridad.py / los limitadores del
    proyecto), no es adivinable por fuerza bruta en un tiempo util. No hace
    falta mas: es un link de un solo uso con vencimiento corto, no una
    credencial de largo plazo.

    CONTINGENCIA 7 (colision de token): un token que ya existe en el store
    JAMAS se pisa -- se reintenta hasta 5 veces (en la practica, con 72 bits
    de espacio, la primera vez ya alcanza). Si los 5 intentos chocan (esto no
    deberia pasar nunca en producción real) se lanza una excepcion clara en
    vez de arriesgar sobreescribir el link de otro paciente."""
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
                'link_agenda.crear: 5 colisiones de token seguidas -- '
                'no se genero el link para no arriesgar pisar uno existente.')

        links[token] = {
            'rut': clave_rut,
            'doctor': doctor_key,
            'motivo': motivo_key,
            'creado': creado,
            'expira': expira,
            'usado': None,
            'id_agenda_origen': str(id_agenda_origen or ''),
        }

        # Poda barata: ya se esta escribiendo el store, y crear() es lo
        # bastante frecuente (cada vez que la secretaria genera un link) para
        # que el JSON no crezca sin que nadie lo pode.
        _podar(datos)

        _save(datos)

    base = _url_base(cfg)
    return {'token': token, 'url': f'{base}/#cita={token}', 'expira': expira}


def get(token):
    """Lectura cruda, sin validar vencimiento ni uso. Solo para diagnostico
    (panel, soporte) -- el endpoint que atiende al paciente debe usar
    resolver()."""
    if not token:
        return None
    return (_load().get('links') or {}).get(token)


def resolver(token):
    """Valida un token y devuelve el contrato documentado arriba del modulo:
    {'ok': True, **datos} si el link sirve, o
    {'ok': False, 'motivo': 'no_existe'|'expirado'|'usado'} si no."""
    registro = get(token)
    if not registro:
        return {'ok': False, 'motivo': 'no_existe'}
    if registro.get('usado'):
        return {'ok': False, 'motivo': 'usado'}
    if registro.get('expira', '') < fechas.hoy_chile().isoformat():
        return {'ok': False, 'motivo': 'expirado'}
    return {'ok': True, **registro}


def marcar_usado(token):
    """Sella el link como usado (timestamp de ahora_chile()). Read-modify-write
    bajo lock, como el resto de los registros JSON del proyecto. Devuelve el
    registro actualizado, o None si el token no existe."""
    with _LOCK:
        datos = _load()
        registro = (datos.get('links') or {}).get(token)
        if not registro:
            return None
        registro['usado'] = fechas.ahora_chile().isoformat()
        _save(datos)
        return registro


def listar(limite=100):
    """Links mas recientes primero, para diagnostico (panel). No filtra por
    validez a proposito -- el panel quiere ver tambien los vencidos/usados
    para poder explicarle a la secretaria que paso con un link puntual."""
    links = (_load().get('links') or {})
    items = [{'token': tok, **l} for tok, l in links.items()]
    items.sort(key=lambda l: l.get('creado', ''), reverse=True)
    return items[:limite]
