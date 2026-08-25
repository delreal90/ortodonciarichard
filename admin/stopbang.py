"""
stopbang.py - Cuestionario STOP-BANG (tamizaje de apnea obstructiva del sueno
en ADULTOS). Es el instrumento que la AAO recomienda para adultos, y el
equivalente adulto del PSQ-CL pediatrico que ya vive en psq.py.

Referencia del uso odontologico: Validity of the STOP-Bang Questionnaire in
Identifying OSA in a Dental Patient Cohort (n=1.000). En cohortes dentales, el
48,2% puntuo 3 o mas, y de los que siguieron estudio, el 79,1% tenia AOS
(IAH >= 5). El dentista funciona como centinela epidemiologico: pesquisa y
deriva, no diagnostica.

ES UN TAMIZAJE, NO UN DIAGNOSTICO. El diagnostico se hace con polisomnografia y
lo hace un medico.

LOS OCHO ITEMS (uno por letra)
------------------------------
  S - Snoring       ronca fuerte
  T - Tired         cansancio o somnolencia diurna
  O - Observed      alguien le vio pausas respiratorias al dormir
  P - Pressure      hipertension arterial (en tratamiento o diagnosticada)
  B - BMI           indice de masa corporal > 35
  A - Age           mas de 50 anios
  N - Neck          circunferencia de cuello > 40 cm
  G - Gender        sexo masculino

TRES DE LOS OCHO NO SON PREGUNTAS, SON MEDICIONES
--------------------------------------------------
IMC, cuello y edad no se contestan de memoria. La edad sale sola de la ficha; el
IMC se calcula si el paciente sabe su peso y talla; y el CUELLO se mide con
huincha (son cinco segundos y lo puede hacer la asistente en el mismo momento
del escaneo) o, si el paciente contesta desde su telefono, se deduce de su talla
de camisa -- ver cuello_desde_camisa() y su advertencia.
Un item que no se midio NO se cuenta como negativo: se
informa el puntaje con su denominador real ("3 de 6 items contestados") para que
nadie lea un 2/8 incompleto como riesgo bajo.

CEREBRO SIN RED: solo recibe respuestas y calcula.
"""

IMC_UMBRAL = 35
EDAD_UMBRAL = 50
CUELLO_UMBRAL_CM = 40

# ── El cuello por talla de camisa ────────────────────────────────────────
#
# La circunferencia de cuello es el unico item del STOP-BANG que el paciente no
# puede contestar de memoria: nadie sabe cuanto mide su cuello en centimetros.
# Pero SI sabe que camisa usa, y la talla de camisa ES la medida del cuello en
# pulgadas -- por eso se pregunta asi cuando contesta desde su telefono.
#
# ⚠️ Es un DATO REFERIDO, no una medicion. El cuello del STOP-BANG publicado se
# toma con huincha, y un cuello de camisa se corta con holgura sobre el cuello
# real. Por eso:
#   - Se guarda de donde vino (cuello_origen) y la hoja lo declara.
#   - Una medicion con huincha hecha en la clinica SIEMPRE le gana.
#   - NO se le aplica ningun factor de correccion: inventar un descuento en
#     milimetros para "compensar la holgura" seria un ajuste sin fuente, y en un
#     item que se decide por un umbral de 40 cm eso cambia el resultado.
PULGADA_CM = 2.54

# Rango util en adultos. Los medios puntos son los que existen de verdad en la
# etiqueta de una camisa; se escriben como los lee el paciente.
TALLAS_CAMISA = (
    ('14', 14.0), ('14 1/2', 14.5), ('15', 15.0), ('15 1/2', 15.5),
    ('16', 16.0), ('16 1/2', 16.5), ('17', 17.0), ('17 1/2', 17.5),
    ('18', 18.0), ('18 1/2', 18.5), ('19', 19.0), ('20', 20.0),
)


def cuello_desde_camisa(talla):
    """Centimetros de cuello a partir de la talla de camisa, o None.

    None cuando no se sabe (el paciente eligio "No sé" o dejo el campo vacio).
    Ese item queda SIN REGISTRAR, que no es lo mismo que negativo: el puntaje
    sale incompleto y la hoja lo dice.
    """
    if talla in (None, '', 'no_se'):
        return None
    try:
        pulgadas = float(dict(TALLAS_CAMISA).get(str(talla).strip(), talla))
    except (TypeError, ValueError):
        return None
    if not 10 <= pulgadas <= 25:
        return None
    return round(pulgadas * PULGADA_CM, 1)

ITEMS = (
    ('ronquido',  'S', 'Ronquido fuerte',
     'Ronca fuerte (se oye a través de una puerta cerrada).'),
    ('cansancio', 'T', 'Cansancio diurno',
     'Se siente cansado o con sueño durante el día.'),
    ('apneas',    'O', 'Pausas respiratorias observadas',
     'Alguien le ha visto dejar de respirar mientras duerme.'),
    ('presion',   'P', 'Presión arterial alta',
     'Tiene hipertensión arterial o está en tratamiento por ella.'),
    ('imc',       'B', 'Índice de masa corporal',
     'IMC mayor a %d.' % IMC_UMBRAL),
    ('edad',      'A', 'Edad',
     'Más de %d años.' % EDAD_UMBRAL),
    ('cuello',    'N', 'Circunferencia de cuello',
     'Mayor a %d cm.' % CUELLO_UMBRAL_CM),
    ('sexo',      'G', 'Sexo',
     'Sexo masculino.'),
)

TEXTO_LEGAL = ('Este resultado corresponde a un tamizaje, no a un diagnóstico. La apnea '
               'obstructiva del sueño se diagnostica con polisomnografía y el diagnóstico '
               'lo realiza un médico.')

# Bandas clasicas del instrumento.
BANDAS = ((2, 'bajo'), (4, 'intermedio'), (8, 'alto'))
PUNTAJE_DERIVA = 3


def _si(v):
    return v is True or v == 'si' or v == 1


def imc(peso_kg, talla_cm):
    """IMC, o None si falta un dato o viene absurdo. No se adivina."""
    try:
        peso, talla = float(peso_kg), float(talla_cm) / 100.0
    except (TypeError, ValueError):
        return None
    if peso <= 0 or talla <= 0 or talla > 2.5 or peso > 400:
        return None
    return round(peso / (talla * talla), 1)


def _positivo(clave, r):
    v = r.get(clave)
    if v is None or v == '':
        return None
    if clave == 'imc':
        try:
            return float(v) > IMC_UMBRAL
        except (TypeError, ValueError):
            return None
    if clave == 'edad':
        try:
            return float(v) > EDAD_UMBRAL
        except (TypeError, ValueError):
            return None
    if clave == 'cuello':
        try:
            return float(v) > CUELLO_UMBRAL_CM
        except (TypeError, ValueError):
            return None
    if clave == 'sexo':
        return str(v).upper().startswith('M')
    return _si(v)


def banda(puntaje):
    for tope, nombre in BANDAS:
        if puntaje <= tope:
            return nombre
    return 'alto'


def evaluar(respuestas):
    """Calcula el puntaje.

    respuestas: dict con las claves de ITEMS. 'imc' acepta el IMC ya calculado
    (usar imc() para obtenerlo de peso y talla), 'edad' en anios, 'cuello' en cm,
    'sexo' 'M'/'F'; el resto si/no.

    Devuelve {'puntaje', 'contestados', 'banda', 'items', 'sin_registrar',
    'incompleto', 'texto_legal'}.

    'incompleto' es True si falto algun item: en ese caso el puntaje es un PISO,
    no el resultado. La hoja tiene que decirlo -- un 2/8 con tres items sin medir
    puede ser en realidad un 5.
    """
    r = dict(respuestas or {})
    filas = []
    for clave, letra, etiqueta, texto in ITEMS:
        pos = _positivo(clave, r)
        filas.append({'clave': clave, 'letra': letra, 'etiqueta': etiqueta, 'texto': texto,
                      'positivo': pos, 'registrado': pos is not None,
                      'valor': r.get(clave)})

    puntaje = sum(1 for f in filas if f['positivo'])
    contestados = sum(1 for f in filas if f['registrado'])
    sin_registrar = [f['clave'] for f in filas if not f['registrado']]

    return {'instrumento': 'STOP-BANG', 'items': filas, 'puntaje': puntaje,
            'contestados': contestados, 'total_items': len(ITEMS),
            'banda': banda(puntaje), 'sin_registrar': sin_registrar,
            'incompleto': bool(sin_registrar), 'texto_legal': TEXTO_LEGAL}


def sugiere_derivacion(resultado):
    """True si corresponde sugerir evaluacion medica. Devuelve (bool, motivo).

    Con items sin medir, el puntaje es un piso: por eso un incompleto que ya
    llego a 2 se trata como si pudiera ser 3. Es tamizaje -- se prefiere una
    consulta de mas a una apnea que nadie vio.
    """
    p = resultado.get('puntaje', 0)
    if p >= PUNTAJE_DERIVA:
        return True, ('STOP-BANG %d de 8: riesgo %s.' % (p, resultado['banda']))
    if resultado.get('incompleto') and p >= PUNTAJE_DERIVA - 1:
        return True, ('STOP-BANG %d de 8 con %d items sin medir: el puntaje real puede ser mayor.'
                      % (p, len(resultado['sin_registrar'])))
    return False, 'STOP-BANG %d de 8: riesgo %s.' % (p, resultado['banda'])
