"""
psq.py - Pediatric Sleep Questionnaire, version chilena validada (PSQ-CL).

El paciente (apoderado) responde el cuestionario de 22 items en /psq. El
backend calcula el puntaje, determina el doctor tratante (el ultimo que
atendio al paciente en DentiDesk) y le envia el resultado por correo. Si no
se puede determinar el doctor, el correo va a recepcion@ortodonciarichard.cl
(decision explicita del usuario -- no se adivina un doctor al azar).

Referencia clinica: Bertran K, Deck B, Vargas MP, et al. Validacion y
adaptacion transcultural de la Escala de Trastornos Respiratorios del Sueno
del Cuestionario de Sueno Pediatrico (PSQ-SRDB) a idioma espanol. Andes
pediatr. 2024;95(4):415-422 (version chilena, PSQ-CL).

  - 22 items en 3 secciones: A/B (conducta nocturna + diurna, 16 items,
    respuesta si/no/no_se) y C (conducta tipo hiperactividad, 6 items,
    respuesta nunca/algunas_veces/muchas_veces/casi_siempre).
  - Puntaje = positivas / contestadas (excluye 'no_se' del denominador; la
    seccion C no tiene opcion 'no se', siempre cuenta).
  - En la seccion C, "muchas veces" y "casi siempre" cuentan como POSITIVO
    (convencion del PSQ original de Chervin et al. 2000 para la subescala de
    hiperactividad).
  - Punto de corte: el estudio chileno (PSQ-CL) determino 0,227 mediante
    curva ROC (sensibilidad 73%, especificidad 78%), mas sensible que el
    corte 0,33 del instrumento original en ingles. Se usa el corte chileno
    por ser la version validada que este formulario implementa.

⚠️ Esto es un instrumento de SCREENING, no diagnostico. El correo al doctor
lo deja explicito: un puntaje alto sugiere evaluar derivacion a especialista
en sueno, no confirma un trastorno respiratorio del sueno.
"""

import os
import re
import logging
import threading
import unicodedata
from pathlib import Path

import jsonstore
import fechas
from scheduling import rut_valido, limpiar_rut, formatear_rut

log = logging.getLogger(__name__)

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                 Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('PSQ_REGISTRO_PATH', _BASE_DIR / 'psq_registro.json'))

_STORE = jsonstore.JsonStore(REGISTRO_PATH, default={'envios': {}}, indent=2,
                             claves={'envios': {}})

# Correo de respaldo cuando no se pudo determinar (o no hay email configurado
# para) el doctor tratante. Pedido explicito del usuario: nunca se adivina.
EMAIL_RESPALDO = 'recepcion@ortodonciarichard.cl'

# Cuantos dias hacia atras se busca al doctor que atendio por ultima vez al
# paciente (dentidesk.doctor_de_paciente barre dia por dia -- ver ahi). Un
# paciente de ortodoncia controla cada 4-8 semanas, asi que 4 meses cubre el
# caso normal sin arriesgar un escaneo demasiado largo en la request.
DIAS_ATRAS_DOCTOR = 120

_SI = 'si'; _NO = 'no'; _NO_SE = 'no_se'
_OPCIONES_SI_NO = (_SI, _NO, _NO_SE)

_NUNCA = 'nunca'; _ALGUNAS = 'algunas_veces'
_MUCHAS = 'muchas_veces'; _CASI_SIEMPRE = 'casi_siempre'
_OPCIONES_FRECUENCIA = (_NUNCA, _ALGUNAS, _MUCHAS, _CASI_SIEMPRE)
_FRECUENCIA_POSITIVA = (_MUCHAS, _CASI_SIEMPRE)

SECCION_NOCHE = 'noche'
SECCION_DIA = 'dia'
SECCION_CONDUCTA = 'conducta'

SECCIONES = {
    SECCION_NOCHE: 'Conducta durante la noche y mientras duerme',
    SECCION_DIA: 'Conducta durante el día y otros problemas posibles',
    SECCION_CONDUCTA: 'Conducta general (frecuencia)',
}

# Los 22 items, en el orden y con el texto exacto del formulario validado
# (PSQ-CL) que entrego la clinica. id estable p1..p22 -- no reordenar sin
# migrar registros ya guardados (el detalle del email se arma por este orden).
PREGUNTAS = [
    {'id': 'p1',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Ronca más de la mitad del tiempo?'},
    {'id': 'p2',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Ronca siempre?'},
    {'id': 'p3',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Ronca de forma ruidosa?'},
    {'id': 'p4',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Tiene una respiración ruidosa o "pesada"?'},
    {'id': 'p5',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Tiene problemas o dificultad para respirar?'},
    {'id': 'p6',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': 'Alguna vez, ¿ha visto a su hijo/a parar de respirar durante la noche?'},
    {'id': 'p7',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Su hijo/a tiene tendencia a respirar por la boca durante el día?'},
    {'id': 'p8',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Su hijo/a tiene la boca seca cuando se despierta por las mañanas?'},
    {'id': 'p9',  'seccion': SECCION_NOCHE, 'tipo': 'si_no',
     'texto': '¿Su hijo/a ocasionalmente se hace pipí (orina) en la cama?'},
    {'id': 'p10', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su hijo/a se despierta en la mañana sintiendo que no descansó?'},
    {'id': 'p11', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su hijo/a tiene problema de somnolencia (sueño excesivo) durante el día?'},
    {'id': 'p12', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su profesor o cualquier otro cuidador le ha comentado alguna vez que su '
              'hijo/a parece estar somnoliento (sueño excesivo) durante el día?'},
    {'id': 'p13', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Le cuesta despertar a su hijo/a por las mañanas?'},
    {'id': 'p14', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su hijo/a se despierta con dolor de cabeza por las mañanas?'},
    {'id': 'p15', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su hijo/a ha parado de crecer a un ritmo normal (según control pediátrico) '
              'en algún momento desde que nació?'},
    {'id': 'p16', 'seccion': SECCION_DIA, 'tipo': 'si_no',
     'texto': '¿Su hijo/a tiene sobrepeso (según control pediátrico) o pesa más de lo '
              'normal para su edad?'},
    {'id': 'p17', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'No parece escuchar cuando se le habla directamente.'},
    {'id': 'p18', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'Tiene dificultad para organizar tareas y actividades.'},
    {'id': 'p19', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'Se distrae fácilmente con estímulos externos.'},
    {'id': 'p20', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'Agita las manos, pies, o se mueve mientras está sentado.'},
    {'id': 'p21', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'Está permanentemente en marcha o a menudo moviéndose como si tuviera un motor.'},
    {'id': 'p22', 'seccion': SECCION_CONDUCTA, 'tipo': 'frecuencia',
     'texto': 'Se entromete o interrumpe a otros (ej. intercepta conversaciones o juegos).'},
]

_POR_ID = {p['id']: p for p in PREGUNTAS}

# Punto de corte validado para la poblacion pediatrica chilena (PSQ-CL, ver
# docstring del modulo). Una puntuacion MAYOR a este valor se asocia a TRS.
PUNTAJE_CORTE = 0.227


def opciones_validas(pregunta):
    return _OPCIONES_FRECUENCIA if pregunta['tipo'] == 'frecuencia' else _OPCIONES_SI_NO


def calcular_riesgo(respuestas):
    """respuestas: {id_pregunta: valor}. Devuelve:
      {puntaje, contestadas, positivas, riesgo ('alto'|'bajo'), corte, detalle}
    'detalle' es una lista en el orden de PREGUNTAS con {id, texto, seccion,
    respuesta, positiva (True/False/None -- None = 'no_se', no conto)}.

    No valida aca que 'respuestas' traiga las 22 claves ni que los valores
    sean validos -- eso lo hace el endpoint antes de llamar (para poder
    devolver un 400 claro en vez de que esto reviente o cuente cualquier
    cosa como negativo)."""
    contestadas = 0
    positivas = 0
    detalle = []
    for p in PREGUNTAS:
        valor = (respuestas or {}).get(p['id'], '')
        if p['tipo'] == 'frecuencia':
            positiva = valor in _FRECUENCIA_POSITIVA
            cuenta = valor in _OPCIONES_FRECUENCIA
        else:
            positiva = (valor == _SI)
            cuenta = valor in (_SI, _NO)   # 'no_se' no cuenta
        if cuenta:
            contestadas += 1
            if positiva:
                positivas += 1
        detalle.append({
            'id': p['id'], 'texto': p['texto'], 'seccion': p['seccion'],
            'respuesta': valor, 'positiva': (positiva if cuenta else None),
        })
    puntaje = round(positivas / contestadas, 3) if contestadas else 0.0
    riesgo = 'alto' if puntaje > PUNTAJE_CORTE else 'bajo'
    return {'puntaje': puntaje, 'contestadas': contestadas, 'positivas': positivas,
            'riesgo': riesgo, 'corte': PUNTAJE_CORTE, 'detalle': detalle}


def validar_respuestas(respuestas):
    """Verifica que 'respuestas' traiga las 22 claves con un valor valido
    para su tipo. Devuelve '' si esta OK, o un mensaje de error legible."""
    if not isinstance(respuestas, dict):
        return 'Faltan las respuestas del cuestionario'
    faltantes = [p['id'] for p in PREGUNTAS if p['id'] not in respuestas]
    if faltantes:
        return f'Faltan {len(faltantes)} respuesta(s) por contestar'
    invalidas = [p['id'] for p in PREGUNTAS
                 if respuestas.get(p['id']) not in opciones_validas(p)]
    if invalidas:
        return f'Respuesta invalida en {len(invalidas)} pregunta(s)'
    return ''


# ── Resolucion del doctor tratante -> email ───────────────────────────────

def _norm(txt):
    s = unicodedata.normalize('NFD', (txt or '').strip().lower())
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def email_doctor(doc_key):
    """Email del doctor, via variable de entorno EMAIL_<DOC_KEY> (ej.
    EMAIL_ALBERTO). Nombre generico a proposito -- no PSQ_EMAIL_* -- para que
    otras funciones futuras que necesiten el email de un doctor (no solo
    este aviso) reusen la misma variable en vez de duplicarla. Nunca se
    versiona un email real en este repo publico -- ver regla de privacidad
    del proyecto. Devuelve '' si no esta configurada."""
    if not doc_key:
        return ''
    return os.environ.get(f'EMAIL_{doc_key.upper()}', '').strip()


def resolver_destinatario(rut, cfg, hoy_iso=None):
    """(email, doc_key, motivo) para el envio del resultado.
    motivo: 'doctor' (se encontro y tiene email), 'sin_doctor' (no se pudo
    determinar quien atendio al paciente) o 'sin_email' (se determino el
    doctor pero no tiene email configurado). En los dos ultimos casos el
    email devuelto es EMAIL_RESPALDO -- nunca se adivina un doctor."""
    import dentidesk
    hoy_iso = hoy_iso or fechas.hoy_chile().isoformat()
    doc_key = ''
    try:
        doc_key = dentidesk.doctor_de_paciente(rut, hoy_iso, cfg, dias_atras=DIAS_ATRAS_DOCTOR)
    except Exception as e:
        log.warning('psq: fallo al resolver el doctor tratante de %s: %r', rut, e)
    if not doc_key:
        return EMAIL_RESPALDO, '', 'sin_doctor'
    email = email_doctor(doc_key)
    if not email:
        return EMAIL_RESPALDO, doc_key, 'sin_email'
    return email, doc_key, 'doctor'


def nombre_doctor(cfg, doc_key):
    if not doc_key:
        return ''
    doc_cfg = (cfg.get('doctores') or {}).get(doc_key) or {}
    return doc_cfg.get('professional_name', '') or doc_key.title()


# ── Registro ────────────────────────────────────────────────────────────

def guardar_envio(registro_id, datos):
    def _fn(reg):
        reg['envios'][registro_id] = datos
    _STORE.actualizar(_fn)


def actualizar_envio(registro_id, **campos):
    def _fn(reg):
        item = reg['envios'].get(registro_id)
        if item is not None:
            item.update(campos)
    _STORE.actualizar(_fn)


def ultimo_por_rut(rut):
    """El PSQ mas reciente de un RUT, o None si nunca respondio.

    Lo consume el Informe de Primera Consulta para mostrar el resultado que el
    apoderado ya contesto en /psq, en vez de volver a preguntar las 22 items en
    el box. Devuelve solo lo que la hoja necesita -- puntaje, riesgo y fecha --
    y NO el detalle respuesta por respuesta: es informacion clinica del menor y
    no tiene por que viajar al navegador para imprimir un resumen.

    Ojo: que devuelva None significa "no lo ha respondido", que NO es lo mismo
    que "sin riesgo". La hoja lo dice con esas palabras.
    """
    clave = limpiar_rut(rut)
    if not clave:
        return None
    candidatos = [e for e in _STORE.load().get('envios', {}).values()
                  if e.get('rut') == clave]
    if not candidatos:
        return None
    e = max(candidatos, key=lambda x: x.get('fecha_iso', ''))
    return {'puntaje': e.get('puntaje'), 'riesgo': e.get('riesgo'),
            'riesgo_alto': e.get('riesgo') == 'alto',
            'corte': PUNTAJE_CORTE,
            'fecha': (e.get('fecha_iso') or '')[:10]}


def listar_envios(limite=200):
    envios = _STORE.load().get('envios', {})
    items = sorted(envios.values(), key=lambda e: e.get('fecha_iso', ''), reverse=True)
    return items[:limite]
