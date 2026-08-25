"""
tamizaje_link.py - El link (y su QR) para que el paciente conteste el
cuestionario de sueno desde su propio telefono, en la sala de espera.

POR QUE EXISTE
--------------
El tamizaje del informe se apoya en un cuestionario: el PSQ-CL si el paciente
es menor, el STOP-BANG si es adulto. Si nadie lo contesto, la hoja lo dice --
no asume "sin riesgo" a partir de un formulario en blanco-- y el informe sale
incompleto en la parte que mas puede cambiarle la vida al paciente.

En la practica el apoderado esta ahi, con su telefono, esperando. Este modulo
convierte eso en un QR: el Dr. lo muestra en la pantalla del box, el apoderado
lo escanea, contesta, y el formulario del box se entera solo.

EL TOKEN
--------
Firmado con itsdangerous y con vencimiento corto (mismo patron que los links de
consentimiento). Lleva el RUT, el nombre y la edad para no volver a pedirselos
al paciente, y el id del informe para saber a cual pertenece la respuesta.

⚠️ El token NO es una credencial: da acceso a contestar UN cuestionario de UN
paciente y vence. Aun asi lleva vencimiento corto (2 horas por defecto) porque
un QR proyectado en una pantalla lo puede fotografiar cualquiera que pase.

CEREBRO SIN RED: solo firma, verifica y arma el diccionario de preguntas.
"""

import os

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import psq
import stopbang

# Dos horas: lo que dura una visita larga. Un QR que sigue sirviendo mañana es
# un QR que alguien puede haber fotografiado de la pantalla del box.
VIGENCIA_SEGUNDOS = 2 * 60 * 60

EDAD_ADULTO = 18


def _secret():
    # Se reusa el secreto de los consentimientos: es el mismo tipo de link
    # (firmado, corto, para el paciente) y tener dos secretos que rotar por
    # separado seria una fuente de errores sin ninguna ganancia.
    return (os.environ.get('TAMIZAJE_SECRET') or os.environ.get('CONSENT_SECRET')
            or 'dev-secret-cambiar-en-produccion')


def _serializer():
    return URLSafeTimedSerializer(_secret(), salt='tamizaje-sueno')


def crear_token(informe_id, rut, nombre, edad, sexo=''):
    return _serializer().dumps({'id': informe_id, 'rut': rut, 'nombre': nombre,
                                'edad': edad, 'sexo': sexo})


def leer_token(token):
    """Devuelve {'ok': True, **datos} o {'ok': False, 'motivo': ...}.

    Contrato con 'ok' (como link_agenda.resolver) porque "vencido" y "adulterado"
    tienen que poder distinguirse: al paciente se le dice que pida uno nuevo, no
    que hizo algo malo.
    """
    try:
        datos = _serializer().loads(token or '', max_age=VIGENCIA_SEGUNDOS)
    except SignatureExpired:
        return {'ok': False, 'motivo': 'expirado'}
    except (BadSignature, Exception):
        return {'ok': False, 'motivo': 'invalido'}
    return dict(datos, ok=True)


def tipo_para(edad):
    """'stopbang' | 'psq'. El instrumento pediatrico es el PSQ; no existe un
    STOP-BANG pediatrico validado."""
    try:
        return 'stopbang' if float(edad) >= EDAD_ADULTO else 'psq'
    except (TypeError, ValueError):
        return 'psq'


# Los textos de stopbang.ITEMS estan escritos como afirmaciones clinicas
# ("Ronca fuerte (se oye a traves de una puerta cerrada)"), que es lo correcto
# en la hoja que firma el doctor pero se lee raro con dos botones Si/No abajo.
# Aca se reformulan como preguntas dirigidas al paciente. Lo que NO cambia es la
# clave del item: el instrumento y su puntaje siguen viviendo en stopbang.py,
# esto es solo como se le pregunta. Si algun dia se agrega un item, su texto
# clinico sirve de respaldo (ver formulario()) y hay una prueba que lo vigila.
TEXTO_PACIENTE = {
    'ronquido': '¿Ronca fuerte, tanto que se oye desde otra pieza?',
    'cansancio': '¿Se siente cansado o con sueño durante el día?',
    'apneas': '¿Alguien le ha visto dejar de respirar mientras duerme?',
    'presion': '¿Tiene la presión alta o toma remedios para la presión?',
}


def formulario(edad):
    """Las preguntas que le tocan a ese paciente, listas para pintar.

    Se arman desde psq.py y stopbang.py, que son los duenios de cada
    instrumento: el cuestionario que ve el paciente y el que puntua el informe
    tienen que ser el mismo, y con una sola definicion no pueden separarse.
    """
    tipo = tipo_para(edad)
    if tipo == 'stopbang':
        preguntas = [{'id': clave, 'texto': TEXTO_PACIENTE.get(clave, texto), 'tipo': 'si_no'}
                     for clave, _letra, _etq, texto in stopbang.ITEMS
                     if clave in ('ronquido', 'cansancio', 'apneas', 'presion')]
        preguntas += [
            {'id': 'peso', 'texto': '¿Cuánto pesa, aproximadamente? (kg)', 'tipo': 'numero'},
            {'id': 'talla', 'texto': '¿Cuánto mide? (cm)', 'tipo': 'numero'},
        ]
        return {'tipo': 'stopbang', 'titulo': 'Cuestionario de sueño',
                'preguntas': preguntas, 'texto_legal': stopbang.TEXTO_LEGAL}

    return {'tipo': 'psq', 'titulo': 'Cuestionario de sueño de su hijo o hija',
            'preguntas': [{'id': p['id'], 'texto': p['texto'], 'seccion': p['seccion'],
                           'tipo': p['tipo'],
                           'opciones': list(psq.opciones_validas(p))}
                          for p in psq.PREGUNTAS],
            'texto_legal': ('Este cuestionario es un tamizaje: orienta al profesional, '
                            'no es un diagnóstico.')}
