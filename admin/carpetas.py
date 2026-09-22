r"""
carpetas.py - Encontrar la carpeta de fotos de un paciente en DIGITAL1.

POR QUE EXISTE
--------------
Los registros de cada paciente viven en `\\DIGITAL1\Registros Pacientes`, una
carpeta por paciente, 7.745 en total. Con la cita abierta en DentiDesk hay que
salir al Explorador y buscarla a mano. Este modulo resuelve el nombre del
paciente -> la carpeta; quien la abre es `carpeta_agent.py`.

CEREBRO SIN RED NI DISCO: recibe una LISTA de nombres de carpeta y devuelve
candidatas ordenadas. Toda la I/O (y la red, que es lenta) vive en el agente,
para que las pruebas corran sin tocar el servidor.

LO QUE SE MIDIO SOBRE EL SERVIDOR REAL (2026-09-15)
---------------------------------------------------
El archivado es `letra <inicial del APELLIDO PATERNO>`, con cuatro grupos que no
son una sola letra: `CH`, `N - Ñ`, `Q - R`, `X - Y`. Dentro, el nombre sigue el
patron `Apellido1 Apellido2(o inicial) Nombre(s) [ficha] [codigo]`:

    Carvallo Mendoza Santiago    Azocar R Alfonso        Solis Maximiliano
    Ferrada S. Daniela           Bahamondes Kevin 4110I  Miranda A Isidora 3858 F
    Pinto De Lama P Ignacio      Larenas Bravo Ma Angelica

844 carpetas (10,9%) traen el numero de ficha en el nombre y 387 traen una
abreviatura con punto. Con solo apellido paterno + un nombre, el 94,1% de las
carpetas queda identificado univocamente.

⚠️ Las MEDICIONES son reales (se corrieron contra el servidor); los NOMBRES de
los ejemplos son inventados y solo copian la forma. Este repo es publico y que
alguien sea paciente de esta clinica es un dato personal de salud.

⚠️ EL RESTO NO ES UN DEFECTO DEL ALGORITMO: buena parte de los empates son
CARPETAS DUPLICADAS DEL MISMO PACIENTE -- `Arrieta R. Ignacia` y `Arrieta Rosales
Ignacia` son la misma persona, igual que `Alvear C Vicente` y `Alvear Cuevas
Vicente`. Eso no lo puede resolver ningun puntaje. Por eso este modulo DEVUELVE
UNA LISTA y marca `confiable`, en vez de elegir: la eleccion es de una persona.
"""

import re

import texto        # sin_tildes(): hay carpetas con tilde ("Alcántara", "Peñaloza").


# Las carpetas `letra *` reales del servidor. Cuatro no son una sola letra.
_GRUPOS = {
    'CH': 'letra CH',
    'N': 'letra N - Ñ',
    'Q': 'letra Q - R', 'R': 'letra Q - R',
    'X': 'letra X - Y', 'Y': 'letra X - Y',
}

MAX_CANDIDATAS = 6

# Una candidata por debajo de esto es ruido: comparte el apellido paterno pero
# casi nada mas.
_RATIO_MINIMO = 0.40

# Puntaje desde el cual un token se considera calzado ENTERO (no una inicial).
# Ver la guarda del nombre de pila en rankear().
_CALCE_ENTERO = 1.5

# Margen para declarar `confiable`. 2.0 es un token exacto COMPLETO de ventaja.
#
# ⚠️ No bajarlo a 1.5: esa es exactamente la distancia entre `Arrieta Rosales
# Ignacia` y `Arrieta R. Ignacia`, que son la MISMA paciente en dos carpetas. Con
# 1.5 el sistema abriria una de las dos sin avisar que existe la otra.
_MARGEN_CONFIABLE = 2.0


def tokens(cadena):
    """Tokens normalizados: sin tildes, en minusculas, sin puntuacion.

    `texto.sin_tildes` colapsa la enie ('Acuña' -> 'acuna'), que es justo lo que
    se quiere para comparar contra un nombre tecleado en DentiDesk.
    """
    return [t for t in re.split(r'[^a-z0-9]+', texto.sin_tildes(cadena)) if t]


def tokens_carpeta(nombre):
    """Tokens utiles del nombre de una carpeta: se descarta lo que no es nombre.

    Dos cosas se van:

    1. El numero de ficha, venga suelto o pegado al codigo: `3618`, `4110I`,
       `5108a`. Basta con que el token tenga un digito.
    2. El codigo de dispositivo del final -- la `F` de `Miranda A Isidora 3858 F`.

    ⚠️ El punto 2 solo aplica DESPUES de haber visto la ficha. Una letra suelta
    al principio es la inicial del apellido materno (`Azocar R Alfonso`) y es
    informacion: descartarla dejaria a esas carpetas sin con que desempatar.
    """
    salida = []
    hubo_ficha = False
    for tok in tokens(nombre):
        if any(c.isdigit() for c in tok):
            hubo_ficha = True
            continue
        if hubo_ficha and len(tok) <= 2:
            continue
        salida.append(tok)
    return salida


def carpetas_a_mirar(apellido):
    """Las carpetas `letra *` donde puede estar archivado ese apellido.

    Devuelve lista porque `C` y `CH` se miran siempre juntas: 'Chadwick' esta en
    `letra CH`, pero nada garantiza que alguien no lo haya archivado en `letra
    C`. Son 45 carpetas mas -- cuesta ~10 ms y evita un "no encontrada" tonto.
    """
    t = tokens(apellido)
    if not t:
        return []
    paterno = t[0]
    if paterno.startswith('ch'):
        return ['letra CH', 'letra C']
    inicial = paterno[0].upper()
    principal = _GRUPOS.get(inicial, 'letra ' + inicial)
    if inicial == 'C':
        return [principal, 'letra CH']
    return [principal]


def _calza_paterno(consulta, carpeta):
    """El apellido paterno es la llave del archivado: se exige calce de verdad.

    ⚠️ Esta guarda es la que impide el desastre medido: con un prefijo
    bidireccional flojo, la inicial suelta de `Abarza A Andrea` actuaba de
    COMODIN y esa carpeta salia como candidata de `Abundio Aniceto Alexsandra`
    (12 candidatas para un paciente). Exigiendo >=3 caracteres por lado, no hay
    token corto que calce con cualquier cosa.
    """
    if consulta == carpeta:
        return True
    if len(consulta) < 3 or len(carpeta) < 3:
        return False
    return carpeta.startswith(consulta) or consulta.startswith(carpeta)


def _puntaje_token(consulta, carpeta):
    """Cuanto vale calzar un token de la consulta contra uno de la carpeta."""
    if consulta == carpeta:
        return 2.0
    # Abreviatura en la carpeta: la 'R' de `Azocar R Alfonso`, el 'Ma' de
    # `Larenas Bravo Ma Angelica`. Vale poco: informa, pero no distingue.
    if len(carpeta) <= 2 and consulta.startswith(carpeta):
        return 0.5
    if len(consulta) <= 2 and carpeta.startswith(consulta):
        return 0.5
    if len(consulta) >= 3 and len(carpeta) >= 3 and (
            carpeta.startswith(consulta) or consulta.startswith(carpeta)):
        return 1.5
    return 0.0


def _asignar(consulta, disponibles):
    """Reparte los tokens de la consulta entre los de la carpeta, sin repetir.

    Goloso por puntaje: primero los calces exactos, que asi se quedan con su
    token antes de que una abreviatura se lo lleve.

    ⚠️ Que cada token de carpeta se consuma UNA SOLA VEZ es la otra mitad de la
    defensa contra el comodin: sin eso, una inicial 'A' absorbe el apellido
    materno Y el nombre de pila de la consulta.
    """
    pares = []
    for i, q in enumerate(consulta):
        for j, f in enumerate(disponibles):
            p = _puntaje_token(q, f)
            if p > 0:
                pares.append((p, i, j))
    pares.sort(key=lambda x: (-x[0], x[1], x[2]))

    usados_q, usados_f = set(), set()
    total = 0.0
    calzados = {}
    for p, i, j in pares:
        if i in usados_q or j in usados_f:
            continue
        usados_q.add(i)
        usados_f.add(j)
        calzados[i] = p
        total += p
    return total, calzados


def rankear(nombre, apellido, nombres_carpetas):
    """Candidatas ordenadas para un paciente.

    `nombres_carpetas` es la lista de nombres tal como estan en el disco. Se
    devuelve `indice` para que quien llamo sepa a que ruta corresponde cada una
    sin depender de que los nombres sean unicos entre carpetas `letra *`.

    Devuelve `{'candidatas': [...], 'confiable': bool}`. `confiable` significa
    "se puede abrir sin preguntar", no "es correcta".
    """
    ap = tokens(apellido)
    nom = tokens(nombre)
    if not ap or not nom:
        return {'candidatas': [], 'confiable': False}

    consulta = ap + nom
    # Los nombres de pila ocupan el tramo final de `consulta`.
    desde_nombres = len(ap)
    maximo = 2.0 * len(consulta)

    candidatas = []
    for indice, carp in enumerate(nombres_carpetas):
        ft = tokens_carpeta(carp)
        if not ft:
            continue
        if not _calza_paterno(ap[0], ft[0]):
            continue

        puntaje, calzados = _asignar(consulta, ft)

        # ⚠️ Tiene que calzar al menos un NOMBRE DE PILA, y calzarlo ENTERO.
        #
        # Lo primero, porque el apellido solo no identifica a nadie en una
        # familia: `Alvear C Vicente` y `Alvear Cuevas Sofia` empatarian para
        # cualquier Alvear.
        #
        # Lo segundo, porque una inicial CORROBORA pero no IDENTIFICA. El 'Ma'
        # de `Miranda C.Ma Veronica` (por Maria) calza con cualquier nombre que
        # empiece en "ma": buscando a "Matias Miranda Araya" salian cinco
        # Veronicas y Teresas de acompañantes. Medido en vivo.
        if not any(p >= _CALCE_ENTERO for i, p in calzados.items()
                   if i >= desde_nombres):
            continue

        ratio = puntaje / maximo if maximo else 0.0
        if ratio < _RATIO_MINIMO:
            continue
        candidatas.append({
            'carpeta': carp,
            'indice': indice,
            'puntaje': round(puntaje, 2),
            'ratio': round(ratio, 3),
        })

    candidatas.sort(key=lambda c: (-c['puntaje'], c['carpeta']))
    candidatas = candidatas[:MAX_CANDIDATAS]

    confiable = False
    if len(candidatas) == 1:
        confiable = True
    elif len(candidatas) >= 2:
        confiable = (candidatas[0]['puntaje'] - candidatas[1]['puntaje']) >= _MARGEN_CONFIABLE

    return {'candidatas': candidatas, 'confiable': confiable}
