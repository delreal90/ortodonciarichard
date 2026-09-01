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
from scheduling import formatear_rut

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
        # Peso y talla en vez del IMC, y talla de camisa en vez de centimetros de
        # cuello: en los tres casos se pregunta el dato que la persona SABE y el
        # calculo lo hace el servidor. El sexo NO se pregunta -- ya esta en la
        # ficha, y volver a pedirlo es una pregunta de mas que ademas se puede
        # contestar distinto.
        preguntas += [
            {'id': 'peso', 'texto': '¿Cuánto pesa, aproximadamente? (kg)', 'tipo': 'numero'},
            {'id': 'talla', 'texto': '¿Cuánto mide? (cm)', 'tipo': 'numero'},
            {'id': 'cuello_camisa',
             'texto': '¿Qué talla de camisa usa en el cuello?',
             'ayuda': 'Es el número de la etiqueta. Si no lo sabe, deje "No sé".',
             'tipo': 'lista',
             'opciones': [t for t, _p in stopbang.TALLAS_CAMISA] + ['no_se'],
             # Solo para ESTA pregunta: 'no_se' tambien lo usan las 22 del PSQ,
             # donde "no uso camisa" no tiene ningun sentido.
             'etiquetas': {'no_se': 'No sé / no uso camisa'}},
        ]
        return {'tipo': 'stopbang', 'titulo': 'Cuestionario de sueño',
                'preguntas': preguntas, 'texto_legal': stopbang.TEXTO_LEGAL}

    return {'tipo': 'psq', 'titulo': 'Cuestionario de sueño de su hijo o hija',
            # La seccion va con su titulo legible (psq.SECCIONES), no con la
            # clave: al paciente le salian encabezados "noche" / "dia" /
            # "conducta" en minuscula, que se leen como un error del sistema.
            'preguntas': [{'id': p['id'], 'texto': p['texto'],
                           'seccion': psq.SECCIONES.get(p['seccion'], p['seccion']),
                           'tipo': p['tipo'],
                           'opciones': list(psq.opciones_validas(p))}
                          for p in psq.PREGUNTAS],
            'texto_legal': ('Este cuestionario es un tamizaje: orienta al profesional, '
                            'no es un diagnóstico.')}


# ── El historial que se mira en el panel ─────────────────────────────────
#
# Los dos instrumentos viven en lugares distintos y por buenas razones: el PSQ
# tiene registro propio (lo puede contestar cualquiera desde /psq, exista o no
# un informe) y el STOP-BANG vive DENTRO del informe del paciente (nace de su
# QR y se imprime en su hoja). Para mirarlos hay que juntarlos, y eso se hace
# aca --sin red, sobre los dos registros-- en vez de que el panel pida dos
# listas y las cruce en el navegador.
def historial(limite=300):
    """Los tamizajes de sueno contestados, del mas nuevo al mas viejo.

    Cada fila: fecha, nombre, rut, instrumento, puntaje, lectura, si quedo
    sobre el corte, de donde vino y --si corresponde-- el informe al que
    pertenece, para poder abrirlo.
    """
    import informe_pc   # aca y no arriba: informe_pc es el consumidor natural
                        # de este modulo y el import al reves seria circular.
    filas = []

    for e in psq.listar_envios(limite=limite):
        filas.append({
            'fecha': e.get('fecha_iso') or '',
            'nombre': e.get('nombre') or '',
            'rut': formatear_rut(e.get('rut') or ''),
            'instrumento': 'PSQ-CL',
            'puntaje': e.get('puntaje'),
            'lectura': _lectura_psq(e),
            'alto': e.get('riesgo') == 'alto',
            'origen': 'informe' if e.get('estado') == 'desde_informe' else 'psq',
            'informe_id': '',
        })

    for item in informe_pc.todos():
        sb = (item.get('tamizaje') or {}).get('stopbang') or {}
        if not sb.get('respondido_por_el_paciente'):
            continue
        datos = dict(sb)
        if datos.get('imc') in (None, '') and datos.get('peso') and datos.get('talla'):
            datos['imc'] = stopbang.imc(datos['peso'], datos['talla'])
        res = stopbang.evaluar(datos)
        _deriva, lectura = stopbang.sugiere_derivacion(res)
        filas.append({
            'fecha': sb.get('respondido_por_el_paciente') or item.get('creado') or '',
            'nombre': item.get('nombre') or '',
            'rut': item.get('rut_fmt') or item.get('rut') or '',
            'instrumento': 'STOP-BANG',
            'puntaje': res.get('puntaje'),
            'lectura': lectura,
            'alto': bool(_deriva),
            'origen': 'informe',
            'informe_id': item.get('id') or '',
        })

    filas.sort(key=lambda f: f.get('fecha') or '', reverse=True)
    return filas[:limite]


def _lectura_psq(e):
    alto = e.get('riesgo') == 'alto'
    return ('Puntaje %s (corte %s): %s'
            % (e.get('puntaje'), str(psq.PUNTAJE_CORTE).replace('.', ','),
               'sobre el corte' if alto else 'bajo el corte'))
