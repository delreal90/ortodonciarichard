"""
secretos.py - Las llaves del backend, en un solo lugar.

Nace de la auditoria del 2026-11-10. Habia dos patrones repetidos a mano:

  1. Cuatro modulos (consentimientos, seguros, tamizaje_link, tareas) firmaban
     sus links con `os.environ.get('X_SECRET') or 'dev-secret-...'`. Ese valor de
     repuesto esta escrito en un repo PUBLICO: si la variable faltara en Render,
     cualquiera podria fabricar links validos.
  2. Tres comprobaciones de token (admin, kiosco, impresion) devolvian True si
     la variable no existia ("modo desarrollo"). En Render eso es dejar todo
     abierto por un descuido de configuracion.

La regla ahora es una sola: **en local se permite trabajar sin llaves; en Render,
una llave que falta CIERRA** (403 o error), nunca abre.

Se eligio cerrar por ruta y no "negarse a arrancar": un backend que no levanta
deja a los pacientes sin agenda por un problema que solo afecta al panel.
`/api/salud` informa `secretos_ok` para que la falta se vea al tiro.

Cero dependencias del resto del proyecto (lo importan modulos de datos).
"""

import hmac
import logging
import os

log = logging.getLogger(__name__)

# Solo para desarrollo local. Publico a proposito: NUNCA se usa en Render.
_DEV = 'dev-secret-cambiar-en-produccion'

# Sin estas, en Render hay funciones que quedan cerradas.
OBLIGATORIAS_EN_RENDER = ('ADMIN_TOKEN', 'KIOSK_TOKEN', 'CONSENT_SECRET')


def en_render():
    return bool(os.environ.get('RENDER'))


def faltantes():
    """Nombres de las llaves obligatorias que no estan definidas (o vacias)."""
    return [n for n in OBLIGATORIAS_EN_RENDER if not (os.environ.get(n) or '').strip()]


def firma(*nombres):
    """Secreto para firmar links (itsdangerous). Prueba las variables en orden
    -- p. ej. firma('SEGUROS_SECRET', 'CONSENT_SECRET') -- y devuelve la primera
    definida. Si no hay ninguna: en local usa el valor de desarrollo; en Render
    lanza RuntimeError (la ruta responde error en vez de firmar con una llave
    que cualquiera conoce)."""
    for n in nombres:
        v = (os.environ.get(n) or '').strip()
        if v:
            return v
    if en_render():
        log.error('secretos: falta %s en Render; no se firma con la llave de desarrollo',
                  ' / '.join(nombres))
        raise RuntimeError('Falta configurar ' + ' o '.join(nombres))
    return _DEV


def token_ok(nombre_env, provisto):
    """Compara el token que mando el cliente con el de la variable `nombre_env`.

    - Variable definida: comparacion en tiempo constante. Se compara en bytes:
      con str, un header con tildes hacia saltar un TypeError (500).
    - Variable ausente: en local se permite (desarrollo); en Render se RECHAZA.
    """
    esperado = (os.environ.get(nombre_env) or '').strip()
    if not esperado:
        if en_render():
            log.error('secretos: %s no esta definido en Render; acceso rechazado', nombre_env)
            return False
        return True
    return hmac.compare_digest(str(provisto or '').encode('utf-8'), esperado.encode('utf-8'))
