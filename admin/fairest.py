"""
fairest.py - FAIREST-6 (pediatrico) y FAIREST 6+4 (adultos): banderas rojas de
trastorno respiratorio del sueno que se pesquisan en el examen clinico.

Referencia: Oh JS, Zaghi S, Peterson C, Law CS, Yoon AJ. Determinants of
Sleep-Disordered Breathing During the Mixed Dentition: Development of a
Functional Airway Evaluation Screening Tool (FAIREST-6). Pediatr Dent
2021;43(4):262-272. Laminas oficiales de Fairest.org & The Breathe Institute
(FAIREST 6, abril 2023; FAIREST 6+4 adultos, diciembre 2023).

ES UN TAMIZAJE, NO UN DIAGNOSTICO. El puntaje sugiere derivar a un medico; el
diagnostico se hace con polisomnografia y lo hace un medico. Ver TEXTO_LEGAL y
FRASES_PROHIBIDAS mas abajo: esas constantes son las que impiden que la hoja
impresa diga algo que la AAO lista explicitamente entre lo que un ortodoncista
NO debe hacer (en particular, atribuirle a la expansion palatina un efecto sobre
la apnea).

CEREBRO SIN RED: recibe lo observado y devuelve el puntaje. Lo unico que importa
de afuera es transversal.py, para el item 6.

EL ITEM 6 SE PUNTUA DISTINTO A LA LAMINA -- A PROPOSITO
--------------------------------------------------------
La lamina sugiere decidir "paladar estrecho" con el intermolar maxilar medido en
la cuspide MESIOLINGUAL (adultos: <32 severo ... 36-38 promedio; ninos: edad +
24 mm). Por decision del usuario (2026-08-20) NO se usa esa guia: el item se
puntua POSITIVO bajo el PERCENTIL 15 de la normativa de Bishara, que es la
evaluacion transversal que la clinica ya venia haciendo (cuspide MESIOVESTIBULAR).

Es defendible -- el item, tal como esta impreso, es un si/no clinico ("signos de
apinamiento, boveda alta y/o paladar estrecho") y la tabla de milimetros es solo
una ayuda sugerida; reemplazarla por un criterio objetivo con normativa por edad
y sexo es mas reproducible que el ojo del examinador. Pero es una
operacionalizacion NUESTRA: las caracteristicas operativas publicadas del
instrumento se midieron con el criterio original. Por eso evaluar() devuelve
'item6_criterio' y la hoja impresa lo declara.

⚠️ NO mezclar las dos mediciones. Entre la cuspide mesiovestibular y la
mesiolingual hay ~15 mm (el ancho de la corona): usar una con los cortes de la
otra da un resultado que se ve razonable y esta completamente equivocado.

DOS UMBRALES QUE LA LAMINA NO IMPRIME
--------------------------------------
Los items 9 (Friedman) y 10 (aleteo palatino) vienen sin criterio de positividad
escrito en la lamina. Se aplicaron las convenciones de la literatura -- FTP III-IV
como posicion de riesgo, y el aleteo POSITIVO (no se produce ronquido con la
lengua en succion palatina) como bandera -- y quedan como constantes con nombre,
FTP_POSITIVO_DESDE y ALETEO_POSITIVO_ES_BANDERA, para que el usuario las
confirme antes de imprimir el primer informe. Estan marcadas con PENDIENTE.
"""

import transversal

# ── Los umbrales, todos con nombre ───────────────────────────────────────

COBERTURA_AMIGDALINA_POSITIVA = 50      # % de ocupacion orofaringea (lamina: >50%)
ANQUILOGLOSIA_GRADO_POSITIVO = 3        # TRMR-TIP grado 3-4 (<50%)
FTP_POSITIVO_DESDE = 3                  # PENDIENTE de confirmacion del usuario
ALETEO_POSITIVO_ES_BANDERA = True       # PENDIENTE de confirmacion del usuario

# El umbral del item 6 vive en transversal.py (es el duenio de los percentiles).
PERCENTIL_PALADAR_ESTRECHO = transversal.PERCENTIL_PALADAR_ESTRECHO

# Tabla de riesgo de la lamina. Solo esta definida para el FAIREST-6: la lamina
# de adultos NO publica bandas para el total de 10, asi que no se inventan.
BANDAS_6 = ((1, 'normal'), (3, 'leve'), (5, 'moderado'), (6, 'severo'))

TEXTO_LEGAL = ('Este resultado corresponde a un tamizaje clínico, no a un diagnóstico. '
               'El diagnóstico de un trastorno respiratorio del sueño lo realiza un médico '
               'mediante polisomnografía.')

# Se revisa contra el texto generado antes de imprimir (test_fairest / test_informe_pc).
# La AAO lista "recomendar expansion palatina por apnea" entre lo que el
# ortodoncista NO debe hacer: evidencia insuficiente.
FRASES_PROHIBIDAS = (
    'trata la apnea', 'cura la apnea', 'tiene apnea', 'padece apnea',
    'la expansion resuelve', 'la expansion cura', 'expansion para la apnea',
    'diagnostico de apnea', 'diagnostica apnea',
)


def _sin_tildes(t):
    import unicodedata
    t = unicodedata.normalize('NFKD', (t or '').lower())
    return ''.join(c for c in t if not unicodedata.combining(c))


def frases_prohibidas_en(texto):
    """Las frases prohibidas que aparecen en un texto, comparando SIN tildes.

    Se compara sin tildes a proposito: el detector no puede fallar solo porque
    el texto diga 'expansion' o 'expansión'. Lo usan las pruebas sobre todo lo
    que se imprime."""
    plano = _sin_tildes(texto)
    return [f for f in FRASES_PROHIBIDAS if _sin_tildes(f) in plano]

_ITEMS_6 = (
    ('respiracion_bucal',   'Respiración bucal',
     'Dificultad para respirar solo por nariz durante 3 o más minutos.'),
    ('tension_mentoniano',  'Tensión del mentoniano',
     'Se ve esfuerzo perioral al mantener los labios cerrados.'),
    ('amigdalas',           'Hipertrofia amigdalina',
     'Las amígdalas ocupan más del 50% del espacio orofaríngeo.'),
    ('anquiloglosia',       'Anquiloglosia',
     'Movilidad lingual restringida (TRMR-TIP grado 3 o 4).'),
    ('desgaste_dentario',   'Desgaste dentario',
     'Signos visibles de desgaste en los dientes.'),
    ('paladar_estrecho',    'Paladar estrecho',
     'Ancho transversal bajo el percentil 15 para la edad y el sexo.'),
)

_ITEMS_4 = (
    ('festoneado_lingual',  'Festoneado lingual',
     'Marcas de los dientes en los bordes de la lengua.'),
    ('desborde_lingual',    'Desborde lingual',
     'La lengua desborda el espacio disponible en succión palatina.'),
    ('friedman',            'Posición lingual de Friedman',
     'Posición lingual que reduce la visibilidad de la orofaringe (FTP 3 o 4).'),
    ('aleteo_palatino',     'Aleteo palatino',
     'No se produce sonido de ronquido con la lengua en succión palatina.'),
)

ITEMS = {k: (etiqueta, texto) for k, etiqueta, texto in _ITEMS_6 + _ITEMS_4}


def _si(v):
    return v is True or v == 'si' or v == 1


def _positivo(clave, obs):
    """Traduce lo observado a bandera si/no, segun el criterio de cada item.
    Devuelve None si el item no se registro (no es lo mismo que negativo)."""
    v = obs.get(clave)
    if v is None or v == '':
        return None

    if clave == 'amigdalas':
        # Acepta el % directo o el tramo de la lamina ('0-25','25-50','51-75','76-100').
        if isinstance(v, str) and '-' in v:
            try:
                v = float(v.split('-')[-1])
            except ValueError:
                return None
        try:
            return float(v) > COBERTURA_AMIGDALINA_POSITIVA
        except (TypeError, ValueError):
            return None
    if clave == 'anquiloglosia':
        try:
            return int(v) >= ANQUILOGLOSIA_GRADO_POSITIVO
        except (TypeError, ValueError):
            return None
    if clave == 'friedman':
        try:
            return int(v) >= FTP_POSITIVO_DESDE
        except (TypeError, ValueError):
            return None
    if clave == 'aleteo_palatino':
        # 'positivo' en la lamina = NO se produce sonido de ronquido.
        pos = (v == 'positivo') or _si(v)
        return pos if ALETEO_POSITIVO_ES_BANDERA else (not pos)
    return _si(v)


def banda_riesgo(puntaje_6):
    """Banda de la lamina para el FAIREST-6 (0-6)."""
    for tope, nombre in BANDAS_6:
        if puntaje_6 <= tope:
            return nombre
    return 'severo'


def paladar_estrecho(arcada_maxilar_intermolar_mm=None, intercanino_mm=None,
                     sexo=None, edad=None, tramo_intermolar=None):
    """Puntua el item 6 con la evaluacion transversal de Bishara.

    Precedencia: se prefiere el INTERMOLAR maxilar, que es la medida transversal
    directa del paladar. Si no hay referencia para esa edad -- el caso real es el
    nino de 6 o 7 anios, que cae en el hueco entre molar temporal y permanente --
    se cae al INTERCANINO maxilar, que Bishara mide de forma continua de los 3 a
    los 45 anios. Si tampoco hay, devuelve None: el item queda SIN REGISTRAR, no
    negativo. Un item que no se pudo medir no es un item ausente.

    Devuelve {'positivo': bool|None, 'origen', 'percentil', 'detalle', ...}.
    """
    intentos = []
    if arcada_maxilar_intermolar_mm not in (None, ''):
        intentos.append(('intermolar', arcada_maxilar_intermolar_mm, tramo_intermolar))
    if intercanino_mm not in (None, ''):
        intentos.append(('intercanino', intercanino_mm, None))

    ultimo = None
    for medida, mm, tramo in intentos:
        r = transversal.percentil(medida, 'maxilar', sexo, edad, mm, tramo)
        ultimo = r
        if r.get('ok'):
            return {'positivo': bool(r['bajo_p15']), 'origen': medida,
                    'percentil': r['percentil'], 'mm': r['mm'],
                    'media': r['media'], 'de': r['de'],
                    'sospechoso': r.get('sospechoso', False),
                    'detalle': 'Ancho %s maxilar %g mm: percentil %.1f.'
                               % (medida, r['mm'], r['percentil'])}
    return {'positivo': None, 'origen': None, 'percentil': None,
            'detalle': (ultimo or {}).get('detalle', 'Sin medición transversal registrada.'),
            'motivo': (ultimo or {}).get('motivo', 'sin_medicion')}


def evaluar(observaciones, adulto=False, transversal_datos=None):
    """Puntua el instrumento.

    observaciones: dict con las claves de ITEMS. Los items no registrados NO
    cuentan como negativos: se informan aparte en 'sin_registrar', para que la
    hoja pueda decir "5 de 6 items evaluados" en vez de dar por sano lo que
    nadie miro.
    transversal_datos: dict con {intermolar_mm, intercanino_mm, sexo, edad,
    tramo_intermolar} para resolver el item 6. Si viene, manda sobre lo que
    traiga observaciones['paladar_estrecho'].

    Devuelve un dict con puntaje_6, banda, items_extra, total_adulto,
    item6_criterio y la lista detallada.
    """
    obs = dict(observaciones or {})

    item6 = None
    if transversal_datos:
        item6 = paladar_estrecho(
            arcada_maxilar_intermolar_mm=transversal_datos.get('intermolar_mm'),
            intercanino_mm=transversal_datos.get('intercanino_mm'),
            sexo=transversal_datos.get('sexo'),
            edad=transversal_datos.get('edad'),
            tramo_intermolar=transversal_datos.get('tramo_intermolar'))
        obs['paladar_estrecho'] = item6['positivo']

    def _fila(clave, etiqueta, texto):
        pos = _positivo(clave, obs)
        fila = {'clave': clave, 'etiqueta': etiqueta, 'texto': texto, 'positivo': pos,
                'registrado': pos is not None}
        if clave == 'paladar_estrecho' and item6:
            fila['detalle'] = item6['detalle']
            fila['percentil'] = item6['percentil']
            fila['origen'] = item6['origen']
            fila['sospechoso'] = item6.get('sospechoso', False)
        return fila

    base = [_fila(k, e, t) for k, e, t in _ITEMS_6]
    extra = [_fila(k, e, t) for k, e, t in _ITEMS_4] if adulto else []

    puntaje_6 = sum(1 for f in base if f['positivo'])
    puntaje_4 = sum(1 for f in extra if f['positivo'])
    sin_registrar = [f['clave'] for f in base + extra if not f['registrado']]

    return {
        'instrumento': 'FAIREST 6+4' if adulto else 'FAIREST-6',
        'items': base + extra,
        'puntaje_6': puntaje_6,
        'banda': banda_riesgo(puntaje_6),
        'puntaje_extra_4': puntaje_4 if adulto else None,
        'total_adulto': (puntaje_6 + puntaje_4) if adulto else None,
        # La lamina de adultos no publica bandas para el total de 10: la banda
        # SIEMPRE sale del FAIREST-6, y el total de 10 se informa como conteo.
        'banda_es_del_6': True,
        'sin_registrar': sin_registrar,
        'item6_criterio': ('Ítem 6 (paladar estrecho) puntuado con criterio objetivo: '
                           'percentil < %d de la referencia transversal de Bishara, '
                           'en vez de la estimación visual de la lámina.'
                           % PERCENTIL_PALADAR_ESTRECHO),
        'texto_legal': TEXTO_LEGAL,
    }


def sugiere_derivacion(resultado, puntaje_cuestionario_alto=False):
    """Conducta a partir del tamizaje. Deliberadamente conservadora: basta
    UNA de las dos senales (el instrumento clinico o el cuestionario) para
    sugerir evaluacion medica. En tamizaje, el costo de derivar de mas es una
    consulta; el de derivar de menos es un nino que no duerme por anios.

    Devuelve (bool, motivo_legible).
    """
    if puntaje_cuestionario_alto:
        return True, 'El cuestionario de sueño resultó sobre el punto de corte.'
    if resultado.get('puntaje_6', 0) >= 2:
        return True, ('El examen clínico muestra %d de 6 señales (%s).'
                      % (resultado['puntaje_6'], resultado['banda']))
    return False, 'El tamizaje no muestra señales que justifiquen evaluación médica hoy.'
