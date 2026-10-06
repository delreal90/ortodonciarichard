"""
geocodificar.py - Donde viven los pacientes: comunas (datos publicos) y
direcciones convertidas en coordenadas con OpenStreetMap (Nominatim).

PARA QUE
--------
El perfil de pacientes (perfil.py) necesita saber en que comuna vive cada
paciente y, si se puede, en que punto, para el mapa de calor y para medir la
PENETRACION por comuna contra la poblacion del Censo 2024. Este modulo es el
dueno de las dos cosas:

1. Las COMUNAS de la Region Metropolitana (`datos_publicos/comunas_rm.json`,
   versionado: son datos publicados del INE y de OpenStreetMap, con su cita).
   `comuna_clave()` reconoce lo que el paciente escribio ("Lo Barneche",
   "Las Condes, Santiago", "Nunoa") y `comuna_de_punto()` dice en que comuna
   cae una coordenada.
2. La GEOCODIFICACION de direcciones contra Nominatim.

⚠️ QUE SE MANDA AFUERA Y QUE NO
-------------------------------
A Nominatim viaja SOLO "calle numero" + comuna + "Chile". Nunca un nombre, un
RUT, un telefono, ni el numero de departamento (`limpiar_direccion` lo saca:
no mejora la coordenada y si acerca la direccion a una persona). Tampoco se
loguea la direccion: el log de Render no es lugar para datos de pacientes.
El usuario aprobo usar OpenStreetMap el 2026-10-02; esta en la lista de
proveedores de privacidad.html.

⚠️ LA CACHE NO LLEVA RUT
------------------------
`geocache.json` va indexado por la direccion NORMALIZADA, no por paciente: cada
direccion se consulta una sola vez en la vida (dos hermanos en la misma casa,
una consulta). Quien cruza paciente -> coordenada es la proyeccion de la noche.

⚠️ UNA COORDENADA SE VALIDA ANTES DE CREERLA
--------------------------------------------
Nominatim encuentra "Los Almendros 123" en cinco comunas distintas. Si el punto
devuelto no cae en la comuna que el paciente declaro, NO se usa: se cae al
centro de la comuna declarada con precision='comuna', y el mapa lo distingue.
Mezclar en silencio un punto equivocado con uno bueno es peor que no tenerlo.
La unica excepcion es "Santiago": la mitad de quienes lo escriben quieren decir
la CIUDAD, asi que ahi manda la comuna donde cae el punto.

Politica de uso de Nominatim: maximo 1 consulta por segundo, User-Agent que
identifique la aplicacion con un correo de contacto, y cache de resultados.
Las tres se cumplen aca.
"""

import difflib
import json
import math
import os
import re
import threading
import time
from datetime import date
import urllib.parse
import urllib.request
from pathlib import Path

import fechas
import jsonstore
import texto

_DIR = Path(__file__).parent
_DATOS = _DIR / 'datos_publicos' / 'comunas_rm.json'
_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                _DIR / 'patient_index.json')).parent
_CACHE = jsonstore.JsonStore(
    Path(os.environ.get('GEOCACHE_PATH', _BASE_DIR / 'geocache.json')), default={})
# La ultima corrida (o la que va en curso), para el panel. Sin direcciones.
_ESTADO = jsonstore.JsonStore(_BASE_DIR / 'geocodificar_estado.json', default={})

NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
USER_AGENT = ('OrtodonciaRichard-perfil/1.0 (+https://www.ortodonciarichard.cl; '
              + os.environ.get('GEOCODER_EMAIL', 'recepcion@ortodonciarichard.cl') + ')')
PAUSA_S = 1.1                 # politica de Nominatim: <= 1 consulta por segundo
REINTENTAR_FALLO_DIAS = 90    # una direccion que no se encontro se vuelve a probar

# Paul Harris 10.349, Las Condes (la clinica). Para la distancia de cada paciente.
CLINICA = (-33.3859, -70.5308)

# Caja de la Region Metropolitana (oeste, norte, este, sur) para acotar Nominatim.
_VIEWBOX = '-71.75,-32.90,-69.75,-34.30'

# 'numero' = el punto de ESA casa (OpenStreetMap conoce el numero).
# 'calle'  = OpenStreetMap encontro la calle pero no el numero: devuelve UN punto
#            representativo de la calle entera, el mismo para cualquier numero.
#            Medido 2026-10-06: "Quebrada Honda 1200" y "2500" caen en la misma
#            esquina, y todos los pacientes de esa calle se apilaban ahi como si
#            vivieran juntos. Por eso el mapa de calor usa solo 'numero'.
# 'comuna' = sin direccion util: el centro de la comuna declarada.
PRECISIONES = ('numero', 'calle', 'comuna')

# Version de los registros de la cache. Los de antes de distinguir 'numero' de
# 'calle' (sin este campo) se vuelven a consultar: no se sabe cuales eran exactos.
VERSION_CACHE = 2


# ── Comunas (datos publicos) ─────────────────────────────────────────────────

_COMUNAS = None


def comunas():
    """Lista de comunas de la RM con poblacion, educacion, centro y geometria."""
    global _COMUNAS
    if _COMUNAS is None:
        with open(_DATOS, encoding='utf-8') as fh:
            doc = json.load(fh)
        for c in doc['comunas']:
            c['clave'] = _norm(c['comuna'])
            c['bbox'] = _bbox(c.get('geometria'))
        _COMUNAS = doc
    return _COMUNAS['comunas']


def fuentes():
    comunas()
    return _COMUNAS['fuentes']


def por_clave():
    return {c['clave']: c for c in comunas()}


def _norm(s):
    s = texto.sin_tildes(s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return ' '.join(s.split())


# Lo que la gente escribe y no es el nombre oficial.
_ALIAS_COMUNA = {
    'stgo': 'santiago', 'santiago centro': 'santiago', 'stgo centro': 'santiago',
    'barnechea': 'lo barnechea', 'la dehesa': 'lo barnechea',
    'chicureo': 'colina', 'piedra roja': 'colina',
    'nunoa': 'nunoa', 'penalolen': 'penalolen', 'maipu': 'maipu',
    'estacion central': 'estacion central', 'pac': 'pedro aguirre cerda',
    'til til': 'tiltil', 'san jose de maipo': 'san jose de maipo',
}

# "Santiago" casi siempre es la ciudad, no la comuna (ver el encabezado).
COMUNA_AMBIGUA = 'santiago'


def comuna_clave(escrito):
    """Clave de comuna de la RM a partir de lo que el paciente escribio, o ''.

    "Las Condes, Santiago" -> 'las condes' (la parte antes de la coma manda;
    si esa parte no es una comuna, se prueba el resto). Tolera errores de tipeo
    ("Lo Barneche") con un calce difuso exigente: confundir dos comunas
    reales es peor que dejarla sin comuna.
    """
    claves = por_clave()
    partes = [p for p in re.split(r'[,/;-]', escrito or '') if p.strip()] or ['']
    candidatos = [_norm(p) for p in partes] + [_norm(escrito)]
    for cand in candidatos:
        if not cand:
            continue
        cand = _ALIAS_COMUNA.get(cand, cand)
        if cand in claves:
            return cand
        # "las condes santiago" -> quitar la ciudad al final
        sin_ciudad = re.sub(r'\s+(santiago|chile|rm|region metropolitana)$', '', cand)
        sin_ciudad = _ALIAS_COMUNA.get(sin_ciudad, sin_ciudad)
        if sin_ciudad in claves:
            return sin_ciudad
        parecida = difflib.get_close_matches(sin_ciudad, list(claves), n=1, cutoff=0.86)
        if parecida:
            return parecida[0]
    return ''


def _bbox(geom):
    if not geom:
        return None
    xs, ys = [], []
    for anillo in _anillos(geom):
        for x, y in anillo:
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _anillos(geom):
    """Anillos exteriores de un Polygon o MultiPolygon GeoJSON."""
    if geom['type'] == 'Polygon':
        return [geom['coordinates'][0]]
    if geom['type'] == 'MultiPolygon':
        return [p[0] for p in geom['coordinates']]
    return []


def _dentro(lon, lat, anillo):
    """Ray casting clasico."""
    adentro = False
    j = len(anillo) - 1
    for i in range(len(anillo)):
        xi, yi = anillo[i][0], anillo[i][1]
        xj, yj = anillo[j][0], anillo[j][1]
        if (yi > lat) != (yj > lat) and \
                lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            adentro = not adentro
        j = i
    return adentro


def comuna_de_punto(lat, lon):
    """Clave de la comuna de la RM donde cae el punto, o ''."""
    for c in comunas():
        b = c.get('bbox')
        if not b or not (b[0] <= lon <= b[2] and b[1] <= lat <= b[3]):
            continue
        if any(_dentro(lon, lat, a) for a in _anillos(c['geometria'])):
            return c['clave']
    return ''


def distancia_km(lat, lon, a=CLINICA):
    """Distancia en linea recta (haversine). Es una aproximacion del viaje real,
    suficiente para agrupar en tramos; nunca se presenta como tiempo de viaje."""
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(lat)
    dp, dl = p2 - p1, math.radians(lon - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(h)), 2)


# ── Direcciones ──────────────────────────────────────────────────────────────

# Lo que viene despues de la calle y numero: depto, casa, oficina, block...
_RESTO = re.compile(
    r'(?:,|\s)\s*(?:depto|dpto|dto|departamento|depart|casa|of|oficina|block|torre|'
    r'parcela|lote|piso|#)\b.*$', re.IGNORECASE)


def limpiar_direccion(direccion):
    """'Av. Las Condes 12.345, depto 501' -> 'Av. Las Condes 12345'.

    Saca lo que identifica la VIVIENDA dentro del edificio (no mejora el punto y
    si acerca la direccion a una persona) y el punto de miles del numero, que
    Nominatim lee como decimal.
    """
    d = ' '.join((direccion or '').split())
    d = _RESTO.sub('', d)
    d = re.sub(r'(\d{1,3})\.(\d{3})\b', r'\1\2', d)
    return d.strip(' ,.-')


def tiene_numero(direccion):
    return bool(re.search(r'\d', direccion or ''))


def clave_cache(direccion, comuna):
    """La llave de la cache: direccion limpia + comuna, normalizadas. Sin RUT."""
    return '%s|%s' % (_norm(limpiar_direccion(direccion)), comuna or '')


def _consultar_nominatim(calle, comuna_nombre):
    """Una consulta real. Devuelve [(lat, lon, con_numero), ...] (hasta 3).
    `con_numero` = OpenStreetMap devolvio ESA casa (trae house_number), no un
    punto cualquiera de la calle."""
    q = urllib.parse.urlencode({
        'street': calle, 'city': comuna_nombre or 'Santiago', 'country': 'Chile',
        'countrycodes': 'cl', 'format': 'jsonv2', 'limit': 3,
        'viewbox': _VIEWBOX, 'bounded': 1, 'addressdetails': 1,
    })
    req = urllib.request.Request(NOMINATIM_URL + '?' + q,
                                 headers={'User-Agent': USER_AGENT,
                                          'Accept-Language': 'es'})
    with urllib.request.urlopen(req, timeout=30) as r:
        datos = json.loads(r.read().decode('utf-8'))
    return [(float(x['lat']), float(x['lon']),
             bool((x.get('address') or {}).get('house_number'))) for x in datos]


def resolver(direccion, comuna_escrita, consultar=None):
    """Geocodifica UNA direccion. Devuelve el registro que se guarda en la cache:
    {lat, lon, comuna, precision, ts} o {fallo: True, ts}.

    `consultar(calle, comuna_nombre)` se inyecta en las pruebas (cero red).
    """
    consultar = consultar or _consultar_nominatim
    ahora = fechas.ahora_chile().isoformat(timespec='seconds')
    declarada = comuna_clave(comuna_escrita)
    calle = limpiar_direccion(direccion)
    info = por_clave().get(declarada)
    nombre_comuna = info['comuna'] if info and declarada != COMUNA_AMBIGUA else ''

    candidatos = consultar(calle, nombre_comuna) if calle else []
    validos = []
    for cand in candidatos:
        lat, lon = cand[0], cand[1]
        # Las pruebas inyectan (lat, lon) a secas: eso es un punto exacto.
        con_numero = cand[2] if len(cand) > 2 else True
        donde = comuna_de_punto(lat, lon)
        if not donde:
            continue                            # fuera de la RM
        if declarada and declarada != COMUNA_AMBIGUA and donde != declarada:
            continue                            # misma calle, otra comuna
        validos.append((not con_numero, lat, lon, donde, con_numero))
    if validos:
        # Entre los candidatos validos, el que trae el numero de la casa gana.
        _, lat, lon, donde, con_numero = min(validos, key=lambda v: v[0])
        return {'lat': round(lat, 5), 'lon': round(lon, 5), 'comuna': donde,
                'precision': 'numero' if con_numero else 'calle',
                'v': VERSION_CACHE, 'ts': ahora}
    return {'fallo': True, 'comuna': declarada, 'v': VERSION_CACHE, 'ts': ahora}


def ubicacion(direccion, comuna_escrita, cache=None):
    """Donde situar a un paciente con lo que hay HOY, sin red.

    Devuelve {comuna, lat, lon, precision} o None si ni la comuna se conoce.
    Con la direccion geocodificada: el punto. Si no: el centro de la comuna
    declarada (precision='comuna'). "Santiago" sin punto no se ubica: seria
    poner en el centro a gente que vive en cualquier parte de la ciudad.
    """
    cache = _CACHE.load() if cache is None else cache
    import pacientes
    if pacientes.direccion_es_relleno(direccion, comuna_escrita):
        return None          # el valor por defecto de DentiDesk: no sabemos donde vive
    declarada = comuna_clave(comuna_escrita)
    if direccion and tiene_numero(direccion):
        reg = cache.get(clave_cache(direccion, declarada))
        if reg and not reg.get('fallo'):
            # Un registro viejo (sin version) no sabe si era exacto: 'calle'.
            prec = reg.get('precision') if reg.get('v') else 'calle'
            return {'comuna': reg['comuna'], 'lat': reg['lat'], 'lon': reg['lon'],
                    'precision': prec if prec in PRECISIONES else 'calle'}
    if not declarada or declarada == COMUNA_AMBIGUA:
        return {'comuna': declarada, 'lat': None, 'lon': None,
                'precision': ''} if declarada else None
    c = por_clave()[declarada]
    return {'comuna': declarada, 'lat': c['lat'], 'lon': c['lon'], 'precision': 'comuna'}


def pendientes(direcciones, cache=None):
    """De una lista de (direccion, comuna_escrita), las claves unicas que todavia
    no estan en la cache (o fallaron hace mas de REINTENTAR_FALLO_DIAS)."""
    cache = _CACHE.load() if cache is None else cache
    hoy = fechas.hoy_chile()
    vistos, out = set(), []
    for direccion, comuna_escrita in direcciones:
        if not direccion or not tiene_numero(direccion):
            continue
        declarada = comuna_clave(comuna_escrita)
        k = clave_cache(direccion, declarada)
        if k in vistos:
            continue
        vistos.add(k)
        reg = cache.get(k)
        if reg and reg.get('fallo'):
            try:
                dias = (hoy - date.fromisoformat(reg["ts"][:10])).days
            except Exception:
                dias = REINTENTAR_FALLO_DIAS
            if dias < REINTENTAR_FALLO_DIAS:
                continue
        elif reg and reg.get('v') == VERSION_CACHE:
            continue
        # (un registro de version vieja se vuelve a consultar)
        out.append((k, direccion, comuna_escrita))
    return out


def direcciones_de_pacientes():
    """(direccion, comuna) de cada paciente de la base local. Sin RUT."""
    import pacientes
    return [((r.get('direccion') or '').strip(), (r.get('comuna') or '').strip())
            for r in pacientes._load_index().values()]


# Cuantos errores SEGUIDOS cortan una corrida. Uno suelto (un timeout) no la
# corta: se espera y se sigue. Tres seguidos es que el servicio no esta
# respondiendo (o nos esta rechazando) y seguir golpeandolo empeora las cosas.
MAX_ERRORES_SEGUIDOS = 3

# Una sola corrida a la vez (el boton del panel y la de la noche no se cruzan:
# dos hilos a 1 consulta/segundo cada uno violan la politica de Nominatim).
_CORRIENDO = threading.Lock()


def _describir_error(e):
    """Que paso, SIN la direccion: tipo de error y, si lo hay, el codigo HTTP.
    Esto es lo que se muestra en el panel y se guarda en el estado."""
    import urllib.error
    if isinstance(e, urllib.error.HTTPError):
        return 'HTTP %s (%s)' % (e.code, {403: 'OpenStreetMap rechazo la consulta',
                                          429: 'demasiadas consultas',
                                          503: 'servicio no disponible'}.get(e.code, 'error'))
    if isinstance(e, urllib.error.URLError):
        return 'sin conexion (%s)' % type(getattr(e, 'reason', e)).__name__
    return type(e).__name__


def correr(maximo=800, consultar=None, pausa=PAUSA_S, direcciones=None):
    """Geocodifica hasta `maximo` direcciones nuevas. Lo llama el loop nocturno
    y el boton del panel. Guarda cada resultado al tiro: si Render se reinicia a
    mitad, lo hecho no se repite.

    ⚠️ Deja su avance y su resultado en `geocodificar_estado.json`. Corre en un
    hilo aparte, y sin esto un fallo solo quedaba en el log de Render: el panel
    se veia como si el boton no hiciera nada (paso el 2026-10-02).

    Nunca lanza hacia afuera: un error de red se reintenta, y tres seguidos
    cortan la corrida y se informan.
    """
    if not _CORRIENDO.acquire(blocking=False):
        return {'ok': False, 'error': 'ya hay una corrida en curso', 'consultadas': 0,
                'encontradas': 0, 'pendientes': None}
    try:
        return _correr(maximo, consultar, pausa, direcciones)
    finally:
        _CORRIENDO.release()


def en_curso():
    return _CORRIENDO.locked()


def _correr(maximo, consultar, pausa, direcciones):
    inicio = fechas.ahora_chile().isoformat(timespec='seconds')
    hechas = encontradas = seguidos = 0
    error = ''
    try:
        lista = pendientes(direcciones if direcciones is not None
                           else direcciones_de_pacientes())
    except Exception as e:
        lista = []
        error = 'no se pudo leer la base de pacientes: %s' % type(e).__name__
    total = min(len(lista), maximo)
    _ESTADO.save({'en_curso': True, 'inicio': inicio, 'total': total,
                  'consultadas': 0, 'encontradas': 0, 'error': ''})

    for k, direccion, comuna_escrita in lista[:maximo]:
        try:
            reg = resolver(direccion, comuna_escrita, consultar=consultar)
        except Exception as e:
            # ⚠️ Sin la direccion en el mensaje: el log no es lugar para ella.
            error = _describir_error(e)
            seguidos += 1
            if seguidos >= MAX_ERRORES_SEGUIDOS:
                break
            if pausa:
                time.sleep(pausa * 10)
            continue
        seguidos = 0
        error = ''
        _CACHE.actualizar(lambda c, k=k, reg=reg: {**c, k: reg})
        hechas += 1
        encontradas += 0 if reg.get('fallo') else 1
        if hechas % 20 == 0:
            _ESTADO.actualizar(lambda st, h=hechas, f=encontradas:
                               {**st, 'consultadas': h, 'encontradas': f})
        if pausa:
            time.sleep(pausa)
    res = {'ok': not error, 'error': error, 'consultadas': hechas,
           'encontradas': encontradas, 'pendientes': max(0, len(lista) - hechas)}
    _ESTADO.save({'en_curso': False, 'inicio': inicio, 'total': total,
                  'fin': fechas.ahora_chile().isoformat(timespec='seconds'), **res})
    return res


def estado():
    cache = _CACHE.load()
    ok = sum(1 for r in cache.values() if not r.get('fallo'))
    exactas = sum(1 for r in cache.values()
                  if r.get('v') and r.get('precision') == 'numero')
    solo_calle = sum(1 for r in cache.values()
                     if r.get('v') and r.get('precision') == 'calle')
    try:
        pend = len(pendientes(direcciones_de_pacientes(), cache))
    except Exception:
        pend = None
    return {'en_cache': len(cache), 'encontradas': ok, 'exactas': exactas,
            'solo_calle': solo_calle,
            'no_encontradas': len(cache) - ok, 'pendientes': pend,
            'ultima_corrida': _ESTADO.load() or None}


def cache():
    return _CACHE.load()
