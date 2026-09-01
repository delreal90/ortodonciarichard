"""
genero.py - Regla de decision NOMBRE -> SEXO, aprendida de la base propia.

POR QUE EXISTE
--------------
El sexo del paciente tiene UNA sola fuente: el export Excel del panel de
DentiDesk. El barrido de la agenda no lo trae (getAgendaDay solo devuelve
nombre, RUT, correo y telefono) y la ficha de primera consulta no lo pregunta.
Resultado: todo paciente que llega despues del ultimo export no lo tiene, o sea
justamente los de primera consulta, que es cuando el informe lo necesita para
calcular el percentil de sus anchos de arcada.

Este modulo cubre ese hueco proponiendo un sexo a partir del nombre. NO lo
inventa de una lista generica de nombres: lo aprende de los miles de pacientes
de ESTA clinica que si tienen el dato declarado. Es la misma poblacion, asi que
la evidencia es la que corresponde.

⚠️ ES UNA SUGERENCIA, NUNCA UN DATO DECLARADO
---------------------------------------------
Lo que sale de aca no se guarda en el campo 'genero' de la base ni pisa nada:
se calcula al vuelo para PRELLENAR un selector que el doctor ve y puede
cambiar en el acto. La diferencia importa porque el costo de equivocarse no es
el mismo en todas partes:

  - En el informe, un sexo equivocado se ve en pantalla y se corrige en un clic.
  - En un WhatsApp o un correo que sale automatico, tratar de "Estimado" a una
    paciente es una falta que el paciente lee y nadie alcanza a corregir.

Por eso pacientes.saludo() sigue SIN usar esto y sigue cayendo a "Estimado/a"
cuando no sabe. Esa decision no cambia; ver su docstring.

LOS NOMBRES COMPUESTOS
----------------------
"Maria Jose" es mujer y "Jose Maria" es hombre. Los mismos dos tokens, distinto
orden, distinto sexo. Por eso:

  1. Se busca primero el COMPUESTO de los dos primeros nombres ("maria jose").
     Es la evidencia mas precisa y resuelve el caso de frente.
  2. Si no hay datos suficientes, se cae al PRIMER nombre. En castellano el
     primer nombre manda, y por si solo ya separa Maria Jose de Jose Maria.
  3. ⚠️ NUNCA se cae al SEGUNDO nombre suelto. Seria exactamente la trampa:
     "Jose Maria" -> "Maria" -> mujer, que es lo contrario de lo correcto.

Y por sobre todo: si la evidencia no alcanza el umbral, se devuelve vacio. Un
"no se" honesto le cuesta al doctor un clic; una sugerencia equivocada con
apariencia de dato le cuesta un percentil calculado contra la tabla del sexo
que no era.

CEREBRO SIN RED: recibe la base ya cargada y calcula.
"""

import unicodedata

# Cuantos pacientes con sexo declarado tiene que haber para que un nombre
# cuente. Con menos, una coincidencia de dos o tres personas se convierte en
# una regla, y en una base de miles eso pasa seguido.
MIN_CASOS = 5

# Que tan de acuerdo tienen que estar. 0.90 deja pasar el ruido normal (un
# tipeo, un genero mal cargado en una ficha) sin dejar pasar los nombres
# genuinamente ambiguos, que es lo que hay que devolver vacio.
UMBRAL = 0.90

# Particulas que no son nombres y no aportan evidencia.
_RELLENO = {'de', 'del', 'la', 'las', 'los', 'y', 'san', 'santa'}


def _norm(texto):
    """Minusculas, sin tildes y solo letras. 'MARÍA JOSÉ' -> 'maria jose'."""
    s = unicodedata.normalize('NFD', (texto or '').strip().lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    s = ''.join(c if (c.isalpha() or c.isspace()) else ' ' for c in s)
    return ' '.join(s.split())


def tokens(nombres):
    """Los nombres de pila, sin particulas. 'Maria de los Angeles' ->
    ['maria', 'angeles']."""
    return [t for t in _norm(nombres).split() if t not in _RELLENO and len(t) > 1]


def construir_tabla(registros):
    """{clave -> {'M': n, 'F': n}} a partir de los pacientes con sexo DECLARADO.

    'registros' es un iterable de dicts con 'nombres' y 'genero'. Se indexan dos
    claves por paciente: su primer nombre y el compuesto de los dos primeros.

    Los que no tienen sexo declarado no entran: son justamente los que este
    modulo tiene que resolver, y aprender de una respuesta que no existe seria
    aprender de la nada.
    """
    tabla = {}

    def _sumar(clave, sexo):
        celda = tabla.setdefault(clave, {'M': 0, 'F': 0})
        celda[sexo] += 1

    for rec in registros:
        sexo = (rec or {}).get('genero') or ''
        if sexo not in ('M', 'F'):
            continue
        tks = tokens(rec.get('nombres'))
        if not tks:
            continue
        _sumar(tks[0], sexo)
        if len(tks) >= 2:
            _sumar('%s %s' % (tks[0], tks[1]), sexo)
    return tabla


def _resolver(clave, tabla):
    """('M'|'F', confianza, casos) o None si la evidencia no alcanza."""
    celda = tabla.get(clave)
    if not celda:
        return None
    total = celda['M'] + celda['F']
    if total < MIN_CASOS:
        return None
    sexo = 'M' if celda['M'] >= celda['F'] else 'F'
    confianza = celda[sexo] / total
    if confianza < UMBRAL:
        return None
    return sexo, round(confianza, 3), total


def inferir(nombres, tabla):
    """Sugiere el sexo a partir del nombre de pila.

    Devuelve siempre un dict; 'sexo' vacio significa que no se sabe, que NO es
    lo mismo que un sexo por defecto. 'via' dice de donde salio, para poder
    explicarlo y para poder auditarlo despues.
    """
    vacio = {'sexo': '', 'confianza': 0.0, 'casos': 0, 'clave': '', 'via': ''}
    tks = tokens(nombres)
    if not tks:
        return vacio

    # 1. El compuesto de los dos primeros: la evidencia mas precisa, y la unica
    #    que distingue de frente "Maria Jose" de "Jose Maria".
    if len(tks) >= 2:
        clave = '%s %s' % (tks[0], tks[1])
        r = _resolver(clave, tabla)
        if r:
            return {'sexo': r[0], 'confianza': r[1], 'casos': r[2],
                    'clave': clave, 'via': 'compuesto'}

    # 2. El primer nombre. En castellano manda, y ya separa los dos casos de
    #    arriba por si solo.
    r = _resolver(tks[0], tabla)
    if r:
        return {'sexo': r[0], 'confianza': r[1], 'casos': r[2],
                'clave': tks[0], 'via': 'primer_nombre'}

    # 3. No hay paso 3. Caer al segundo nombre suelto convertiria a
    #    "Jose Maria" en mujer.
    return vacio


def evaluar(registros):
    """Que tan bien funcionaria la regla, medida contra los que SI tienen el
    sexo declarado.

    Cada paciente se evalua con una tabla que NO lo incluye (se le resta su
    propio voto), porque si no la regla se estaria calificando con la respuesta
    a la vista y cualquier nombre unico daria 100%.

    Devuelve conteos agregados y la lista de nombres que quedaron ambiguos.
    Nunca devuelve pacientes: es para medir la regla, no para mirar fichas.
    """
    registros = [r for r in registros if (r or {}).get('genero') in ('M', 'F')]
    tabla = construir_tabla(registros)

    ok = malos = sin_respuesta = 0
    errores = {}
    for rec in registros:
        tks = tokens(rec.get('nombres'))
        if not tks:
            sin_respuesta += 1
            continue
        # Tabla sin el voto de este paciente.
        propia = {}
        claves = [tks[0]] + (['%s %s' % (tks[0], tks[1])] if len(tks) >= 2 else [])
        for c in claves:
            celda = dict(tabla.get(c) or {'M': 0, 'F': 0})
            celda[rec['genero']] -= 1
            propia[c] = celda
        mezcla = dict(tabla)
        mezcla.update(propia)

        r = inferir(rec.get('nombres'), mezcla)
        if not r['sexo']:
            sin_respuesta += 1
        elif r['sexo'] == rec['genero']:
            ok += 1
        else:
            malos += 1
            errores[r['clave']] = errores.get(r['clave'], 0) + 1

    con_respuesta = ok + malos
    return {
        'evaluados': len(registros),
        'aciertos': ok,
        'errores': malos,
        'sin_respuesta': sin_respuesta,
        'cobertura': round(con_respuesta / len(registros), 4) if registros else 0.0,
        'precision': round(ok / con_respuesta, 4) if con_respuesta else 0.0,
        'nombres_con_error': sorted(errores.items(), key=lambda kv: -kv[1])[:40],
        'nombres_en_tabla': len(tabla),
    }


def ambiguos(registros, minimo=MIN_CASOS):
    """Nombres que la base ve repartidos entre hombres y mujeres.

    Son los que la regla devuelve vacios a proposito. Sirve para revisarlos a
    ojo y confirmar que el vacio esta bien puesto.
    """
    tabla = construir_tabla(registros)
    out = []
    for clave, celda in tabla.items():
        total = celda['M'] + celda['F']
        if total < minimo:
            continue
        mayor = max(celda['M'], celda['F']) / total
        if mayor < UMBRAL:
            out.append({'nombre': clave, 'M': celda['M'], 'F': celda['F'],
                        'mayoria': round(mayor, 3)})
    return sorted(out, key=lambda d: -(d['M'] + d['F']))


def discordantes(registros, minimo=20, dominancia=0.95):
    """Registros cuyo sexo declarado contradice a un nombre que la base ve
    abrumadoramente de un solo sexo.

    NO es una medida de la regla: es una medida de la CALIDAD DEL DATO. Si 50
    Catalinas estan declaradas mujer y 6 hombre, lo mas probable no es que
    existan seis hombres llamados Catalina.

    Importa porque el sexo declarado se usa en cosas que el paciente ve, y
    porque es la respuesta contra la que se mide esta regla: si la respuesta
    tiene ruido, la precision medida sale mas baja que la real.
    """
    tabla = construir_tabla(registros)
    casos = []
    for clave, celda in tabla.items():
        if ' ' in clave:           # solo primeros nombres, no compuestos
            continue
        total = celda['M'] + celda['F']
        if total < minimo:
            continue
        mayor = 'M' if celda['M'] >= celda['F'] else 'F'
        if celda[mayor] / total < dominancia:
            continue
        contra = celda['F'] if mayor == 'M' else celda['M']
        if contra:
            casos.append({'nombre': clave, 'mayoria': mayor,
                          'concuerdan': celda[mayor], 'discordan': contra})
    casos.sort(key=lambda c: -c['discordan'])
    return {'nombres': casos[:30],
            'total_discordan': sum(c['discordan'] for c in casos),
            'total_en_esos_nombres': sum(c['discordan'] + c['concuerdan'] for c in casos)}
