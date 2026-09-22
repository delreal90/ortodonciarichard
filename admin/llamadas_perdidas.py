"""
llamadas_perdidas.py - Llamadas de WhatsApp que nadie puede contestar
(Ortodoncia Richard).

QUE PROBLEMA RESUELVE
---------------------
El WhatsApp de la clinica (+56 9 3355 8189) vive en la Cloud API de Meta, no en
la app de WhatsApp Business. Eso es lo que permite automatizar confirmaciones,
recordatorios y NPS, pero tiene un costo: un numero en la Cloud API **no tiene
telefono donde suene la llamada**. Contestar exigiria montar una central por
software (SIP/WebRTC) y, sobre todo, alguien sentado esperando que suene.

Hasta ahora el paciente que tocaba el boton de llamar escuchaba tono, cortaba
con un "no contestaron", y **nadie en la clinica se enteraba de que habia
llamado**. Esa es la perdida real: no es que no le contesten, es que la llamada
no dejaba rastro en ninguna parte.

Este modulo cierra ese hueco con tres cosas, en este orden:

1. **Rechaza la llamada al tiro** (`wa_cloud.rechazar_llamada`). Cortar de
   inmediato es mejor trato que 30-60 segundos de tono para nada.
2. **Le contesta por escrito** diciendole que por aca no se reciben llamadas y
   dandole las dos salidas reales: escribir por el mismo WhatsApp, o llamar al
   fijo de la clinica.
3. **Le avisa a recepcion por correo**, que es lo que hace que una persona de
   verdad retome el contacto.

POR QUE NO PASA POR `avisos.py` (regla 3)
-----------------------------------------
La regla 3 pide que todo sistema que le escribe al paciente herede de
`avisos.py` y respete `no_molestar`. Ese contrato es para los sistemas que
**inician** el contacto (recordatorios, encuestas, recaptacion). Aca el
paciente acaba de llamar a la clinica: contestarle no es molestarlo, y quedarse
mudo con alguien que te esta buscando seria el peor resultado posible.

Es el mismo criterio con que ya operan las respuestas del webhook
(`_confirmar`, `_agendar_por_whatsapp`): responden libre, sin consultar avisos,
porque el paciente acaba de tocar un boton. Una llamada es esa misma categoria.

VENTANA ANTI-REPETICION
-----------------------
Un paciente que no entiende por que no le contestan llama 2 o 3 veces seguidas.
Se le responde UNA vez cada `VENTANA_MINUTOS`; las llamadas siguientes igual se
rechazan y se registran (recepcion ve que insistio, que es justamente la senial
de urgencia), pero no le llega el mismo texto tres veces.

Registro propio en el disco persistente de Render (misma base que
patient_index.json, via PATIENT_INDEX_PATH). Lleva RUT y telefono: va al
.gitignore, este repo es PUBLICO.
"""

import os
import logging
from pathlib import Path

import jsonstore
import fechas

log = logging.getLogger(__name__)

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('LLAMADAS_REGISTRO_PATH',
                                    _BASE_DIR / 'llamadas_registro.json'))

# Cuantos minutos deben pasar antes de volver a responderle al mismo numero.
VENTANA_MINUTOS = 30

# Ventana en que una segunda llamada del mismo numero se considera 'insistio'
# (solo cambia el texto del aviso a recepcion, no si se le responde al paciente).
HORAS_INSISTENCIA = 24

# Cuantas llamadas se guardan. Son pocas al mes; el tope es para que el archivo
# no crezca sin techo en anios.
MAX_REGISTRO = 2000

_STORE = jsonstore.JsonStore(REGISTRO_PATH, indent=2, default={'llamadas': []})


def _cargar():
    return _STORE.load()


def _clave_tel(tel):
    digits = ''.join(c for c in (tel or '') if c.isdigit())
    return digits[-8:] if len(digits) >= 8 else digits


def _minutos_desde(iso):
    """Minutos transcurridos desde un timestamp ISO nuestro, o None si no se
    puede leer. Cualquier basura en el registro se trata como 'hace mucho':
    ante la duda es mejor responderle de nuevo al paciente que dejarlo mudo."""
    if not iso:
        return None
    try:
        from datetime import datetime
        return (fechas.ahora_chile() - datetime.fromisoformat(iso)).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def debe_responder(telefono, registro=None):
    """True si a este numero NO se le respondio dentro de la ventana."""
    clave = _clave_tel(telefono)
    if not clave:
        return True
    reg = registro if registro is not None else _cargar()
    for it in reversed(reg.get('llamadas', []) or []):
        if _clave_tel(it.get('telefono')) != clave or not it.get('respondido'):
            continue
        mins = _minutos_desde(it.get('cuando'))
        if mins is None:
            return True
        return mins >= VENTANA_MINUTOS
    return True


def insistio(telefono, registro=None, horas=HORAS_INSISTENCIA):
    """True si este numero ya habia llamado en las ultimas `horas`.

    No es lo mismo que la ventana anti-repeticion: esa decide si le vuelvo a
    escribir al paciente; esta le dice a recepcion que el paciente va en su
    segunda o tercera llamada, que es la senial de que la cosa es urgente.
    """
    clave = _clave_tel(telefono)
    if not clave:
        return False
    reg = registro if registro is not None else _cargar()
    for it in reversed(reg.get('llamadas', []) or []):
        if _clave_tel(it.get('telefono')) != clave:
            continue
        mins = _minutos_desde(it.get('cuando'))
        if mins is not None and mins <= horas * 60:
            return True
    return False


def registrar(telefono, call_id='', nombre='', rut=''):
    """Deja la llamada en el registro y decide si toca responderle.

    Devuelve {'responder': bool, 'repeticion': bool}. La decision se toma
    DENTRO del actualizar() para que dos llamadas que entran al mismo tiempo no
    se lean ambas como 'primera' y le manden el texto duplicado.
    """
    decision = {}

    def _fn(reg):
        reg.setdefault('llamadas', [])
        responder = debe_responder(telefono, registro=reg)
        decision['responder'] = responder
        decision['repeticion'] = insistio(telefono, registro=reg)
        reg['llamadas'].append({
            'id': call_id,
            'telefono': telefono,
            'rut': rut,
            'nombre': nombre,
            'cuando': fechas.ahora_chile().isoformat(timespec='seconds'),
            'respondido': responder,
        })
        if len(reg['llamadas']) > MAX_REGISTRO:
            reg['llamadas'] = reg['llamadas'][-MAX_REGISTRO:]
        return reg

    _STORE.actualizar(_fn)
    return decision


def historial(limite=None):
    """Llamadas mas recientes primero. Lo usa el adaptador de clinico.py."""
    items = list(reversed(_cargar().get('llamadas', []) or []))
    return items[:limite] if limite else items


def total():
    return len(_cargar().get('llamadas', []) or [])


def vaciar():
    _STORE.save({'llamadas': []})
