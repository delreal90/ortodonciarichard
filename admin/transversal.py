"""
transversal.py - Evaluacion transversal de arcadas contra la normativa de
Bishara 1997: percentil por edad y sexo, y la curva de crecimiento estilo OMS
que se imprime en el Informe de Primera Consulta.

QUE HACE
--------
Dado un ancho medido en el escaneo (intercanino o intermolar, maxilar o
mandibular), la edad y el sexo del paciente, devuelve la media y la DE de
referencia, el z-score y el percentil; y sabe dibujar la curva en SVG con las
cinco lineas del estilo OMS (P3 y P97 rojas, P15 y P85 amarillas, P50 central).

CEREBRO SIN RED: solo lee su propia tabla (transversal_normas.json, versionada
junto a este archivo) y calcula. No toca disco persistente, no toca DentiDesk,
no guarda nada de pacientes.

LAS TRES COSAS QUE HAY QUE ENTENDER ANTES DE TOCAR ESTO
--------------------------------------------------------
1. LA CURVA ES UNA SOLA, Y ATRAVIESA EL RECAMBIO. Bishara mide sobre el diente
   que el paciente TIENE a cada edad: los molares de 3 y 5 anios son los segundos
   temporales y desde los 8 son los primeros permanentes; los caninos de 3, 5 y 8
   son temporales y desde los 13 permanentes. Las figuras 4 y 5 del paper trazan
   una sola linea de los 3 a los 45 anios atravesando ese cambio, y aca se hace
   igual. El ascenso entre los 5 y los 8 (43,5 -> 51,0 mm en hombres) refleja el
   recambio ademas del crecimiento: en_recambio() marca esas edades para poder
   decirlo al pie, pero NO parte la curva.

2. EL PUNTO QUE SE MIDE EN MEDIT TIENE QUE SER EL DE BISHARA. Intercanino =
   puntas de cuspide de los caninos. Intermolar = cuspide MESIOVESTIBULAR del
   primer molar permanente (o mesial del segundo temporal entre los 3 y 5). Si
   se mide otro punto -- por ejemplo la cuspide mesioLINGUAL, que es la que usa
   la lamina del FAIREST -- el numero sale igual de lindo y no significa nada:
   hay ~15 mm de diferencia, que es el ancho de las coronas.

3. EL PERCENTIL ASUME NORMALIDAD. La fuente publica media y DE, no percentiles
   empiricos: percentil = Phi(z). Es una decision explicita del usuario
   (2026-08-20) y la hoja impresa lo declara, igual que declara el n de la
   muestra (15 hombres y 15 mujeres de los 3 a los 45 anios).

QUIEN LO USA
------------
- informe_pc.py: la tabla de mediciones y la curva de la Hoja 1.
- fairest.py: el item 6 (paladar estrecho) se puntua positivo bajo el P15.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

NORMAS_PATH = Path(__file__).parent / 'transversal_normas.json'

# Cita corta para el pie de la hoja impresa. La larga vive en el JSON.
CITA = ('Bishara SE, Jakobsen JR, Treder J, Nowak A. Arch width changes from 6 weeks '
        'to 45 years of age. Am J Orthod Dentofacial Orthop. 1997;111(4):401-9.')
NOTA_DE_SUSTITUIDA = ('En esta edad la fuente publica una desviación estándar que triplica '
                      'la de todas sus edades vecinas, muy probablemente por un error de '
                      'imprenta. Para el gráfico se usó la del punto contiguo; el promedio '
                      'de referencia no se modificó.')

NOTA_RECAMBIO = ('En las edades marcadas, la referencia atraviesa el recambio de dientes '
                 'temporales a permanentes: ahí el ascenso de la curva refleja ese cambio '
                 'además del crecimiento.')

NOTA_MUESTRA = ('Referencia construida sobre 15 hombres y 15 mujeres del Iowa Facial Growth '
                'Study (Clase I, sin tratamiento previo). Percentiles calculados asumiendo '
                'distribución normal. Los valores entre las edades medidas son interpolados.')

MEDIDAS = ('intercanino', 'intermolar')
ARCADAS = ('maxilar', 'mandibular')
SEXOS = ('M', 'F')

TRAMO_CANINO = 'canino'
TRAMO_MOLAR_TEMPORAL = 'molar_temporal'
TRAMO_MOLAR_PERMANENTE = 'molar_permanente'


# Las cinco lineas del grafico, al estilo de las curvas de crecimiento de la OMS.
# z = Phi^-1(p). Rojo para los extremos, amarillo para P15/P85, negro el centro.
LINEAS_PERCENTIL = (
    (3,  -1.8808, '#cc2b2b'),
    (15, -1.0364, '#e0a500'),
    (50,  0.0,    '#1A2E4A'),
    (85,  1.0364, '#e0a500'),
    (97,  1.8808, '#cc2b2b'),
)

# Umbral del item 6 del FAIREST (paladar estrecho), por decision del usuario.
# Vive aca porque es el modulo duenio de los percentiles; fairest.py lo importa
# en vez de repetir el 15 suelto en su codigo.
PERCENTIL_PALADAR_ESTRECHO = 15

# Desde esta edad el grafico usa el eje largo (3-45). Vive aca porque el
# modulo del grafico es quien decide la ventana; informe_pc.py tiene su
# propia EDAD_ADULTO para elegir instrumento de tamizaje, que es otra cosa.
EDAD_ADULTO = 18


# ── Carga de la tabla ────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _normas():
    with open(NORMAS_PATH, encoding='utf-8') as fh:
        return json.load(fh)


@lru_cache(maxsize=64)
def _serie(medida, arcada, sexo):
    """Los puntos de la tabla para una combinacion, ordenados por edad.
    Devuelve una tupla de (edad, media, de, sospechoso).

    Es UNA sola serie de los 3 a los 45 anios, no dos. Ver la nota sobre el
    recambio en el encabezado del modulo.

    ⚠️ LA DE SOSPECHOSA SE SUSTITUYE PARA DIBUJAR (no la media)
    -----------------------------------------------------------
    El intermolar mandibular femenino a los 3 anios trae DE = 6,2 mm en la Tabla
    II, contra 1,9-2,4 en TODAS sus vecinas y 2,0 en la celda masculina de la
    misma edad. Con esa DE la banda P3-P97 mide 23,3 mm de ancho donde las demas
    miden 7, y la curva dibuja un embudo que no representa nada biologico: se ve
    como si la variabilidad se desplomara entre los 3 y los 5 anios.

    Es casi seguro un error de imprenta del paper. Se conserva el valor
    publicado en el JSON (la fuente no se altera) y la MEDIA se respeta tal
    cual; lo unico que se sustituye, y solo para calcular y dibujar, es la DE,
    por la del punto vecino de la misma serie. La hoja lo declara y el resultado
    viene marcado con 'de_sustituida'.
    """
    filas = [r for r in _normas()['registros']
             if r['medida'] == medida and r['arcada'] == arcada and r['sexo'] == sexo]
    filas.sort(key=lambda r: r['edad'])
    des = [r['de'] for r in filas]
    for i, r in enumerate(filas):
        if r.get('sospechoso'):
            vecina = des[i + 1] if i + 1 < len(des) else (des[i - 1] if i else None)
            if vecina:
                des[i] = vecina
    return tuple((r['edad'], r['media'], des[i], bool(r.get('sospechoso')))
                 for i, r in enumerate(filas))


def diente_de_referencia(medida, edad):
    """Sobre que diente esta medida la referencia a esa edad, en las palabras de
    Bishara. NO cambia el calculo -- la curva es continua -- pero se imprime al
    pie para que quien mida sepa que punto le corresponde a ese paciente."""
    try:
        edad = float(edad)
    except (TypeError, ValueError):
        return ''
    if medida == 'intermolar':
        return ('cúspide mesial del segundo molar temporal' if edad < 6.5
                else 'cúspide mesiovestibular del primer molar permanente')
    return ('punta de cúspide del canino temporal' if edad < 10.5
            else 'punta de cúspide del canino permanente')


def en_recambio(medida, edad):
    """True si la edad cae en el tramo donde la referencia cambia de diente
    temporal a permanente (molares entre 5 y 8; caninos entre 8 y 13).

    La curva pasa igual -- asi la publica el paper -- pero ahi el ascenso
    refleja el recambio ademas del crecimiento, y conviene decirlo."""
    try:
        edad = float(edad)
    except (TypeError, ValueError):
        return False
    return (5 < edad < 8) if medida == 'intermolar' else (8 < edad < 13)


# ── Interpolacion monotona (PCHIP / Fritsch-Carlson) ─────────────────────
#
# Se usa interpolacion monotona a proposito, NO un spline suave cualquiera:
# un Catmull-Rom o un cubico natural pueden hacer OVERSHOOT y dibujar un
# valle o una joroba donde los datos no la tienen. En un grafico clinico que
# el paciente se lleva a la casa, una curva que baja donde el ancho solo crece
# es un error que nadie va a notar y que igual esta mal.

def _pendientes_monotonas(xs, ys):
    n = len(xs)
    if n == 1:
        return [0.0]
    deltas = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = deltas[0], deltas[-1]
    for i in range(1, n - 1):
        if deltas[i - 1] * deltas[i] <= 0:
            m[i] = 0.0          # hay un cambio de direccion: pendiente cero, sin overshoot
        else:
            m[i] = (deltas[i - 1] + deltas[i]) / 2
    for i in range(n - 1):
        if deltas[i] == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / deltas[i], m[i + 1] / deltas[i]
        s = a * a + b * b
        if s > 9:
            t = 3.0 / math.sqrt(s)
            m[i], m[i + 1] = t * a * deltas[i], t * b * deltas[i]
    return m


def _interp(xs, ys, x):
    """Hermite cubico con pendientes monotonas. Fuera de rango: valor del borde."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    m = _pendientes_monotonas(xs, ys)
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            h = xs[i + 1] - xs[i]
            t = (x - xs[i]) / h
            t2, t3 = t * t, t * t * t
            return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h * m[i] +
                    (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h * m[i + 1])
    return ys[-1]


def _phi(z):
    """Funcion de distribucion acumulada normal estandar (stdlib, sin scipy)."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ── API principal ────────────────────────────────────────────────────────

def referencia(medida, arcada, sexo, edad, tramo=None):
    """Media y DE de referencia para una edad. Contrato con 'ok', igual que
    link_agenda.resolver: el caso 'no se puede' tiene que poder distinguirse
    del caso 'dio 0'.

    `tramo` se acepta por compatibilidad con lo que ya guarda el registro y con
    los llamadores, pero NO afecta el calculo: la curva es una sola. Se usa solo
    para saber que diente se midio.

    Devuelve {'ok': True, 'media', 'de', 'interpolado', 'en_borde',
    'sospechoso', 'diente', 'en_recambio'} o {'ok': False, 'motivo', 'detalle'}
    con motivo en: parametro_invalido | sin_datos | fuera_de_rango.
    """
    if medida not in MEDIDAS or arcada not in ARCADAS or sexo not in SEXOS:
        return {'ok': False, 'motivo': 'parametro_invalido',
                'detalle': 'medida/arcada/sexo fuera del catálogo'}
    try:
        edad = float(edad)
    except (TypeError, ValueError):
        return {'ok': False, 'motivo': 'parametro_invalido', 'detalle': 'edad no numérica'}

    serie = _serie(medida, arcada, sexo)
    if not serie:
        return {'ok': False, 'motivo': 'sin_datos', 'detalle': 'combinación sin filas en la tabla'}

    edades = [p[0] for p in serie]
    # Por debajo del primer punto NO se extrapola: seria inventar. Por encima
    # del ultimo (45 anios) si se usa el valor del borde, porque el ancho ya
    # esta en meseta desde los 26 y un paciente de 50 no es un caso raro.
    if edad < edades[0]:
        return {'ok': False, 'motivo': 'fuera_de_rango',
                'detalle': 'la referencia parte a los %g años' % edades[0]}

    medias = [p[1] for p in serie]
    des = [p[2] for p in serie]
    media = _interp(edades, medias, edad)
    de = _interp(edades, des, edad)
    sospechoso = any(p[3] for p in serie if abs(p[0] - edad) <= 2.5)

    return {'ok': True, 'media': round(media, 2), 'de': round(de, 2),
            'de_sustituida': sospechoso,
            'tramo': tramo or '', 'diente': diente_de_referencia(medida, edad),
            'interpolado': edad not in edades, 'en_borde': edad > edades[-1],
            'en_recambio': en_recambio(medida, edad),
            'sospechoso': sospechoso}


def percentil(medida, arcada, sexo, edad, mm, tramo=None):
    """Percentil de un ancho medido. Mismo contrato de 'ok' que referencia()."""
    ref = referencia(medida, arcada, sexo, edad, tramo)
    if not ref.get('ok'):
        return ref
    try:
        mm = float(mm)
    except (TypeError, ValueError):
        return {'ok': False, 'motivo': 'parametro_invalido', 'detalle': 'medición no numérica'}
    if ref['de'] <= 0:
        return {'ok': False, 'motivo': 'sin_datos', 'detalle': 'DE no positiva en la tabla'}

    z = (mm - ref['media']) / ref['de']
    p = _phi(z) * 100
    out = dict(ref)
    out.update({'mm': mm, 'z': round(z, 2), 'percentil': round(p, 1),
                'bajo_p15': p < PERCENTIL_PALADAR_ESTRECHO})
    return out


def etiqueta_percentil(p):
    """Texto corto para la hoja impresa. Deliberadamente descriptivo y sin
    juicio clinico: 'bajo lo esperado' no es un diagnostico, y quien lee esto
    es el paciente."""
    if p is None:
        return 'sin referencia'
    if p < 3:
        return 'muy por debajo del promedio'
    if p < 15:
        return 'bajo el promedio'
    if p <= 85:
        return 'dentro del promedio'
    if p <= 97:
        return 'sobre el promedio'
    return 'muy por encima del promedio'


# ── Curva SVG estilo OMS ─────────────────────────────────────────────────

def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _ventana(edad, historico=None):
    """Rango de edad del eje X.

    Regla del usuario (2026-08-20): el paciente PEDIATRICO se grafica siempre de
    los 3 a los 18 anios, y el ADULTO de los 3 a los 45. Un paciente que empezo
    de nino y ya tiene algun control pasados los 18 se grafica 3-45, para que
    toda su historia entre en el mismo grafico y las mediciones sean comparables
    entre si a lo largo del tratamiento.

    El eje SIEMPRE parte a los 3 aunque la curva de ese tramo empiece despues
    (el intermolar permanente arranca a los 8): asi todos los graficos del
    informe tienen el mismo eje y el hueco se ve, en vez de disimularse.
    """
    edades = [float(edad)] + [float(e) for e, _ in (historico or [])]
    return (3, 45) if max(edades) >= EDAD_ADULTO else (3, 18)


def curva_svg(medida, arcada, sexo, edad, mm=None, tramo=None, historico=None,
              ancho=520, alto=198):
    """SVG de la curva de crecimiento con el punto del paciente.

    historico: mediciones ANTERIORES del mismo paciente, como [(edad, mm), ...].
    Se dibujan como puntos huecos unidos por una linea DELGADA -- mas delgada
    que los propios puntos, para que la vista siga las mediciones y no el trazo
    que las une. Es lo que convierte el grafico en un seguimiento: en el segundo
    informe ya se ve si el ancho acompano el crecimiento o se quedo atras.

    ⚠️ El historico que llega aca tiene que ser del MISMO tramo: un ancho medido
    sobre molares temporales no se puede poner en la curva de los permanentes.
    De filtrarlo se encarga informe_pc.mediciones_previas().

    Devuelve el string SVG, o None si no hay referencia para ese caso (el
    llamador imprime 'sin referencia normativa' en vez de un grafico vacio).
    """
    ref = referencia(medida, arcada, sexo, edad, tramo)
    if not ref.get('ok'):
        return None
    serie = _serie(medida, arcada, sexo)
    edades = [p[0] for p in serie]
    medias = [p[1] for p in serie]
    des = [p[2] for p in serie]

    historico = sorted((float(e), float(v)) for e, v in (historico or []))
    x0, x1 = _ventana(edad, historico)

    # La curva solo existe dentro del tramo; el eje puede ser mas ancho.
    c0, c1 = max(x0, edades[0]), min(x1, edades[-1])
    pasos = 60
    xs = [c0 + (c1 - c0) * i / pasos for i in range(pasos + 1)] if c1 > c0 else [c0]
    curvas = []
    for p, z, color in LINEAS_PERCENTIL:
        pts = [(x, _interp(edades, medias, x) + z * _interp(edades, des, x)) for x in xs]
        curvas.append((p, color, pts))

    ys = [y for _, _, pts in curvas for _, y in pts]
    if mm is not None:
        ys.append(float(mm))
    ys.extend(v for _, v in historico)
    ymin, ymax = min(ys) - 1.5, max(ys) + 1.5

    ml, mr, mt, mb = 40, 34, 14, 28
    pw, ph = ancho - ml - mr, alto - mt - mb

    def px(x):
        return ml + (float(x) - x0) / (x1 - x0) * pw

    def py(y):
        return mt + (ymax - float(y)) / (ymax - ymin) * ph

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
           'width="100%%" style="max-width:%dpx;font-family:Inter,Arial,sans-serif">' % (ancho, alto, ancho)]
    out.append('<rect x="0" y="0" width="%d" height="%d" fill="#fff"/>' % (ancho, alto))

    # Rejilla: cada 2 mm en Y; en X cada anio si el eje es corto, cada 5 si no.
    paso_y = 2
    y = math.ceil(ymin / paso_y) * paso_y
    while y <= ymax:
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e6e6e6" stroke-width="1"/>'
                   % (ml, py(y), ml + pw, py(y)))
        out.append('<text x="%.1f" y="%.1f" font-size="9" fill="#666" text-anchor="end">%d</text>'
                   % (ml - 5, py(y) + 3, y))
        y += paso_y
    paso_x = 1 if (x1 - x0) <= 16 else 5
    # Con paso de 5 las marcas se alinean a multiplos de 5 (5, 10, ... 45) en vez
    # de arrastrar el 3 del inicio y terminar en 43 sin marcar el borde del eje.
    x = math.ceil(x0) if paso_x == 1 else paso_x * math.ceil(x0 / paso_x)
    while x <= x1:
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#e6e6e6" stroke-width="1"/>'
                   % (px(x), mt, px(x), mt + ph))
        out.append('<text x="%.1f" y="%.1f" font-size="9" fill="#666" text-anchor="middle">%d</text>'
                   % (px(x), mt + ph + 15, x))
        x += paso_x

    for p, color, pts in curvas:
        d = ' '.join('%s%.1f,%.1f' % ('M' if i == 0 else 'L', px(a), py(b))
                     for i, (a, b) in enumerate(pts))
        grosor = 1.8 if p == 50 else 1.2
        out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f" stroke-linejoin="round"/>'
                   % (d, color, grosor))
        ex, ey = pts[-1]
        out.append('<text x="%.1f" y="%.1f" font-size="9" fill="%s">P%d</text>'
                   % (px(ex) + 4, py(ey) + 3, color, p))

    out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#999" stroke-width="1"/>'
               % (ml, mt + ph, ml + pw, mt + ph))
    out.append('<text x="%.1f" y="%.1f" font-size="9" fill="#666">mm</text>' % (6, mt + 8))
    out.append('<text x="%.1f" y="%.1f" font-size="9" fill="#666" text-anchor="middle">edad (años)</text>'
               % (ml + pw / 2, alto - 3))

    # La trayectoria del paciente: linea DELGADA (1 px) uniendo sus mediciones,
    # con la actual al final. Los puntos pesan mas que la linea a proposito.
    trayecto = list(historico)
    if mm is not None:
        trayecto.append((float(edad), float(mm)))
    if len(trayecto) > 1:
        d = ' '.join('%s%.1f,%.1f' % ('M' if i == 0 else 'L', px(a), py(b))
                     for i, (a, b) in enumerate(trayecto))
        out.append('<path d="%s" fill="none" stroke="#1A2E4A" stroke-width="1" '
                   'stroke-linejoin="round" opacity="0.75"/>' % d)
    for a, b in historico:
        out.append('<circle cx="%.1f" cy="%.1f" r="3.4" fill="#fff" stroke="#1A2E4A" '
                   'stroke-width="1.6"/>' % (px(a), py(b)))

    if mm is not None:
        cx, cy = px(edad), py(mm)
        out.append('<circle cx="%.1f" cy="%.1f" r="4.8" fill="#1A2E4A" stroke="#fff" stroke-width="1.6"/>'
                   % (cx, cy))
        out.append('<text x="%.1f" y="%.1f" font-size="10" font-weight="600" fill="#1A2E4A">%s mm</text>'
                   % (cx + 8, cy - 6, _esc(('%g' % float(mm)))))

    out.append('</svg>')
    return ''.join(out)
