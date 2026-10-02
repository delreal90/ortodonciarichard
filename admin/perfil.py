"""
perfil.py - El perfil de los pacientes: quien llega, quien inicia, de donde
viene, quien lo trajo y cuanto se queda.

PARA QUE
--------
Pedido del Dr. Alberto (2026-10-02): saber "como llegar mejor a los
pacientes". Cruza en `clinica.db`:
  - la agenda de 5 anos (kpi.py) y el DESTINO de cada primera consulta
    (kpi.destino_primeras_consultas: una sola definicion de inicio/perdido);
  - la ficha de primera consulta (fichas.py): quien lo recomendo, su dentista
    habitual, colegio, profesion, hobbies, motivacion;
  - donde vive (geocodificar.py) contra el Censo 2024 de su comuna.

⚠️ TODO LO QUE SALE DE ACA ES AGREGADO
--------------------------------------
Ningun indicador identifica a una persona. Las celdas del mapa se redondean a
~100 m y se cuentan; una tasa no se informa con menos de MIN_N casos. La
excepcion deliberada es el ranking de RECOMENDADORES (dentistas y pacientes que
derivan): esta para agradecerles, solo lo ve el panel con ADMIN_TOKEN.

⚠️ INTERESES: SOLO LO QUE EL PACIENTE CONTO
------------------------------------------
Los intereses salen de lo que el paciente escribio en NUESTRA ficha (hobbies,
colegio, profesion). No se busca a nadie en redes sociales ni en ninguna otra
fuente: cruzar la ficha con perfiles de LinkedIn o Facebook seria tratar datos
de salud para marketing sin consentimiento (Ley 21.719), y se leeria como lo
que es. Decision explicita, 2026-10-02.

⚠️ TASAS SOLO SOBRE COHORTES CERRADAS
-------------------------------------
Una primera consulta de hace 3 semanas no ha decidido nada. Se usa el mismo
`en_ventana` de kpi.py (fuera del denominador) — es el arreglo del sesgo de
supervivencia del 2026-09-25, no se reimplementa.

⚠️ EL FORMULARIO EXISTE DESDE 2021 Y NO TODOS LO LLENAN
-------------------------------------------------------
~1/3 de las primeras consultas tiene ficha. Lo que sale de la ficha (canal,
intereses) describe a quien la lleno, que no es necesariamente igual al resto.
El panel lo dice en "Calidad del dato".

CERO RED.
"""

import json
import math
import re
from datetime import date, timedelta

import basedatos
import fechas
import texto

MIN_N = 5             # menos casos que esto: no se informa una tasa
MIN_N_HALLAZGO = 15   # para afirmar una diferencia en "Hallazgos"
MESES_ACTIVO = 36     # paciente "activo" para la penetracion por comuna
MIN_FALTAN = 10       # una comuna "con potencial" tiene que deber al menos esto

BANDAS_EDAD = (('<12', 0, 11), ('12-17', 12, 17), ('18-34', 18, 34), ('35+', 35, 200))
TRAMOS_KM = ((0, 3, '0-3 km'), (3, 6, '3-6 km'), (6, 10, '6-10 km'),
             (10, 20, '10-20 km'), (20, 9999, '20+ km'))

DIMENSIONES = {
    'canal': 'Quién lo recomendó',
    'banda_edad': 'Edad',
    'sexo': 'Sexo',
    'distancia': 'Distancia a la clínica',
    'comuna': 'Comuna',
    'prevision': 'Previsión',
    'doctor': 'Doctor de la primera consulta',
    'familia_previa': 'Familiar ya era paciente',
    'anio': 'Año',
}


# ═══════════════════════════════════════════════════════════════════════════
# Clasificadores de texto libre de la ficha (intereses, profesion, colegio)
# ═══════════════════════════════════════════════════════════════════════════

def _n(s):
    s = texto.sin_tildes(s)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return ' '.join(s.split())


# Grupo -> palabras (sobre texto normalizado). Un paciente puede tener varios.
INTERESES = {
    'Fútbol': ('futbol', 'football', 'soccer', 'baby futbol'),
    'Hockey': ('hockey',),
    'Tenis / pádel': ('tenis', 'padel', 'paddle', 'squash'),
    'Rugby': ('rugby',),
    'Equitación': ('equitacion', 'caballo', 'caballos', 'polo', 'salto'),
    'Natación / surf': ('natacion', 'nadar', 'surf', 'waterpolo', 'buceo'),
    'Atletismo / running': ('atletismo', 'correr', 'running', 'trote', 'maraton'),
    'Ciclismo': ('bicicleta', 'bici', 'ciclismo', 'mountain bike', 'mtb'),
    'Gimnasio / pilates / yoga': ('gimnasio', 'gym', 'pilates', 'yoga', 'crossfit',
                                   'entrenar', 'pesas', 'funcional'),
    'Danza / ballet': ('ballet', 'danza', 'baile', 'bailar', 'hip hop', 'jazz'),
    'Gimnasia': ('gimnasia', 'gimnasia artistica', 'gimnasia ritmica', 'acrobacia'),
    'Básquetbol / vóleibol': ('basquetbol', 'basketball', 'basket', 'voleibol',
                              'volleyball', 'volley', 'handball'),
    'Nieve / montaña': ('esqui', 'ski', 'snowboard', 'trekking', 'montana',
                        'escalada', 'andinismo', 'outdoor', 'senderismo'),
    'Artes marciales': ('karate', 'judo', 'taekwondo', 'jiu jitsu', 'box', 'boxeo',
                        'artes marciales', 'kung fu'),
    'Golf': ('golf',),
    'Música': ('musica', 'piano', 'guitarra', 'cantar', 'canto', 'violin', 'bateria',
               'instrumento', 'coro', 'flauta'),
    'Arte / manualidades': ('pintar', 'pintura', 'dibujar', 'dibujo', 'arte',
                            'manualidades', 'ceramica', 'teatro', 'fotografia'),
    'Lectura': ('leer', 'lectura', 'libros'),
    'Videojuegos / tecnología': ('videojuegos', 'video juegos', 'play', 'nintendo',
                                 'juegos', 'computador', 'programar', 'tecnologia',
                                 'gamer', 'roblox', 'minecraft'),
    'Scouts': ('scout', 'scouts', 'guias'),
    'Viajar': ('viajar', 'viajes'),
    'Cocina': ('cocinar', 'cocina', 'reposteria'),
}

PROFESIONES = {
    'Ingeniería': ('ingeniero', 'ingeniera', 'ingenieria', 'ing'),
    'Salud': ('medico', 'medica', 'dentista', 'odontologo', 'odontologa', 'enfermera',
              'kinesiologo', 'kinesiologa', 'psicologa', 'psicologo', 'nutricionista',
              'matrona', 'fonoaudiologa', 'tecnologo medico', 'veterinaria',
              'veterinario', 'quimico farmaceutico'),
    'Derecho': ('abogado', 'abogada', 'derecho'),
    'Educación': ('profesora', 'profesor', 'educadora', 'docente', 'parvularia'),
    'Diseño / arquitectura / arte': ('arquitecto', 'arquitecta', 'disenador',
                                     'disenadora', 'artista', 'publicista'),
    'Empresa / comercio': ('empresario', 'empresaria', 'comercial', 'administrador',
                           'administradora', 'contador', 'contadora', 'gerente',
                           'ventas', 'economista', 'comerciante', 'independiente'),
    'Comunicaciones': ('periodista', 'comunicador', 'comunicadora', 'periodismo'),
    'Dueña/o de casa': ('duena de casa', 'dueno de casa', 'casa'),
    'Estudiante': ('estudiante', 'universitario', 'universitaria', 'alumno', 'alumna'),
    'Jubilado/a': ('jubilado', 'jubilada', 'pensionado', 'pensionada'),
}

MOTIVACIONES = {
    'Estética / alinear dientes': ('chuecos', 'alinear', 'enderezar', 'estetica',
                                   'sonrisa', 'apinamiento', 'apinados', 'derechos',
                                   'separados', 'diastema', 'colmillo', 'frenillos',
                                   'brackets', 'invisalign', 'alineadores'),
    'Mordida / función': ('mordida', 'cruzada', 'invertida', 'abierta', 'mandibula',
                          'paladar', 'respirar', 'respiracion', 'masticar', 'atm',
                          'bruxismo', 'dolor'),
    'Control / evaluación': ('control', 'evaluacion', 'revision', 'diagnostico',
                             'chequeo', 'ver si necesita', 'consulta'),
    'Derivación de su dentista': ('derivacion', 'derivado', 'derivada', 'derivaron',
                                  'recomendacion del dentista', 'indicacion'),
    'Segunda opinión / retomar': ('segunda opinion', 'retomar', 'recidiva', 'retenedor',
                                  'se movieron', 'volvieron'),
}

_STOP_COLEGIO = {'colegio', 'the', 'school', 'liceo', 'instituto', 'escuela', 'c'}


def grupos(txt, mapa):
    """Grupos de `mapa` cuyas palabras aparecen en el texto (palabras enteras)."""
    n = ' %s ' % _n(txt)
    out = []
    for g, palabras in mapa.items():
        if any((' %s ' % p) in n for p in palabras):
            out.append(g)
    return out


def colegio_clave(txt):
    """'Colegio Cumbres' y 'cumbres' son el mismo colegio."""
    toks = [t for t in _n(txt).split() if t not in _STOP_COLEGIO]
    k = ' '.join(toks)
    return '' if k in ('', 'no', 'no aplica', 'na', 'ninguno', 'no tiene') else k


def _familiar_tratado(txt):
    """'Si, mi hermana' -> 1 ; 'no' / 'nadie' -> 0 ; vacio -> None."""
    n = _n(txt)
    if not n:
        return None
    if n in ('no', 'nadie', 'ninguno', 'ninguna', 'no se', 'no que sepa', 'n a', 'na',
             'no aun', 'todavia no', 'aun no', 'tampoco') or n.startswith('no '):
        return 0
    return 1


# ═══════════════════════════════════════════════════════════════════════════
# Proyeccion (la llama clinico.proyectar_todo). Lee JSON, no la base.
# ═══════════════════════════════════════════════════════════════════════════

def _indice_pacientes():
    try:
        import pacientes
        return pacientes._load_index()
    except Exception:
        return {}


def filas_fichas(perfiles=None, indice=None, cfg_referidos=None):
    """Una fila por ficha, en el orden de clinico._COLUMNAS['fichas_perfil']."""
    import referidos
    if perfiles is None:
        import fichas
        perfiles = fichas.perfiles()
    if not perfiles:
        return []
    indice = _indice_pacientes() if indice is None else indice
    clasif, ctx = referidos.clasificar_todas(perfiles, indice, cfg_referidos)
    filas = []
    for rut, p in perfiles.items():
        r = clasif[rut]
        dent = referidos.clave_persona(p.get('dentista') or '')
        dent = ctx.canonica(dent) if dent else ''
        dent = dent if dent in ctx.dentistas else ''
        hobbies = (p.get('hobbies') or '').strip()
        prof = (p.get('profesion') or '').strip()
        mot = (p.get('motivacion') or '').strip()
        gprof = grupos(prof, PROFESIONES)
        gmot = grupos(mot, MOTIVACIONES)
        filas.append((
            rut, p.get('fecha_form') or '', (p.get('recomendo') or '').strip(),
            r['canal'], r['clave'], r['nombre'], r['rut'], r['estado'],
            (p.get('dentista') or '').strip(), dent,
            _familiar_tratado(p.get('familiar')),
            (p.get('colegio') or '').strip(), colegio_clave(p.get('colegio')),
            prof, gprof[0] if gprof else ('Otra' if prof else ''),
            json.dumps(grupos(hobbies, INTERESES), ensure_ascii=False),
            mot, gmot[0] if gmot else ('Otra' if mot else ''),
        ))
    return filas


def filas_ubicaciones(indice=None, cache=None):
    """Una fila por paciente con comuna conocida, en el orden de
    clinico._COLUMNAS['ubicaciones']. Sin red: usa lo que ya esta geocodificado."""
    import geocodificar as geo
    indice = _indice_pacientes() if indice is None else indice
    cache = geo.cache() if cache is None else cache
    filas = []
    for rut, rec in indice.items():
        u = geo.ubicacion(rec.get('direccion'), rec.get('comuna'), cache)
        if not u:
            continue
        dist = geo.distancia_km(u['lat'], u['lon']) if u['lat'] is not None else None
        filas.append((rut, u['comuna'], u['lat'], u['lon'], u['precision'], dist))
    return filas


# ═══════════════════════════════════════════════════════════════════════════
# Analisis (lee clinica.db)
# ═══════════════════════════════════════════════════════════════════════════

def wilson(k, n, z=1.96):
    """Intervalo de confianza 95% de una proporcion (Wilson). En %, o (None, None)."""
    if not n:
        return None, None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round(100 * (c - m) / d, 1), round(100 * (c + m) / d, 1)


def _edad(nac, ref):
    try:
        n = date.fromisoformat(nac[:10])
        r = date.fromisoformat(ref[:10])
    except Exception:
        return None
    e = r.year - n.year - ((r.month, r.day) < (n.month, n.day))
    return e if 0 <= e <= 110 else None


def _banda(edad):
    if edad is None:
        return ''
    for nombre, a, b in BANDAS_EDAD:
        if a <= edad <= b:
            return nombre
    return ''


def _tramo(km):
    if km is None:
        return ''
    for a, b, nombre in TRAMOS_KM:
        if a <= km < b:
            return nombre
    return ''


_ISAPRES = ('isapre', 'banmedica', 'colmena', 'consalud', 'cruz blanca', 'vida tres',
            'masvida', 'mas vida', 'esencial', 'fundacion', 'nueva masvida', 'codelco')


def prevision_grupo(txt):
    n = _n(txt)
    if not n:
        return ''
    if 'fonasa' in n:
        return 'Fonasa'
    if any(i in n for i in _ISAPRES):
        return 'Isapre'
    if 'particular' in n or n in ('sin prevision', 'ninguna'):
        return 'Particular'
    return 'Otra'


def _familias(indice):
    """{rut: id_grupo} uniendo pacientes que comparten telefono o correo (el del
    apoderado suele ser el mismo para los hermanos)."""
    import pacientes
    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            padre[x] = padre.get(padre[x], padre[x])
            x = padre[x]
        return x

    por_clave = {}
    for rut, rec in indice.items():
        claves = []
        t = pacientes._tel_clave(rec.get('telefono'))
        if t:
            claves.append('t' + t)
        e = (rec.get('email') or '').strip().lower()
        if '@' in e:
            claves.append('e' + e)
        for c in claves:
            if c in por_clave:
                a, b = raiz(rut), raiz(por_clave[c])
                if a != b:
                    padre[a] = b
            else:
                por_clave[c] = rut
    return {rut: raiz(rut) for rut in indice}


def base(desde=None, hasta=None):
    """Una fila por primera consulta, con todo lo que se sabe del paciente."""
    import kpi
    d = kpi.destino_primeras_consultas(desde, hasta, incluir_todas=True)
    con = basedatos.conectar()
    try:
        pac = {r['rut']: dict(r) for r in con.execute(
            'SELECT rut, sexo, fecha_nacimiento, comuna, prevision FROM pacientes')}
        fic = {r['rut']: dict(r) for r in con.execute('SELECT * FROM fichas_perfil')}
        ubi = {r['rut']: dict(r) for r in con.execute('SELECT * FROM ubicaciones')}
        primera = {r['rut']: r['f0'] for r in con.execute(
            "SELECT rut, MIN(fecha) f0 FROM citas WHERE rut<>'' AND %s GROUP BY rut"
            % kpi._SQL_OCURRIO)}
    finally:
        con.close()

    fam = _familias(_indice_pacientes())
    # Por grupo familiar, la fecha de la primera cita de cada miembro.
    llegadas = {}
    for rut, g in fam.items():
        if rut in primera:
            llegadas.setdefault(g, []).append(primera[rut])

    filas = []
    for x in d.get('todas', []):
        rut = x['rut']
        p, f, u = pac.get(rut, {}), fic.get(rut), ubi.get(rut, {})
        edad = _edad(p.get('fecha_nacimiento') or '', x['fecha'])
        # ¿Alguien de su grupo familiar (mismo telefono o correo) ya se atendia
        # antes de esta primera consulta? Sin ficha en la base: no se sabe.
        g = fam.get(rut)
        previa = ''
        if g is not None:
            previa = 'Sí' if any(fe < x['fecha'] for fe in llegadas.get(g, [])) else 'No'
        filas.append({
            'rut': rut, 'fecha': x['fecha'], 'anio': x['fecha'][:4],
            'doctor': x.get('doctor') or '', 'destino': x['destino'],
            'cerrado': x['destino'] != 'en_ventana',
            'inicio': x['destino'] == 'inicio',
            'perdido': x['destino'] == 'perdido',
            'edad': edad, 'banda_edad': _banda(edad),
            'sexo': {'F': 'Mujer', 'M': 'Hombre'}.get(p.get('sexo') or '', ''),
            'comuna': u.get('comuna') or '',
            'prevision': prevision_grupo(p.get('prevision')),
            'distancia': _tramo(u.get('distancia_km')),
            'distancia_km': u.get('distancia_km'),
            'precision': u.get('precision') or '',
            'tiene_ficha': bool(f),
            'canal': (f or {}).get('canal') or '',
            'familia_previa': previa,
            'ficha': f,
        })
    return filas


def _contar(filas, dim, solo_cerradas=False):
    out = {}
    for f in filas:
        if solo_cerradas and not f['cerrado']:
            continue
        v = f.get(dim) or ''
        out.setdefault(v, []).append(f)
    return out


def _etiqueta(dim, v):
    if dim == 'canal':
        import referidos
        return referidos.ETIQUETAS.get(v, v) if v else 'Sin ficha'
    if dim == 'comuna':
        import geocodificar as geo
        c = geo.por_clave().get(v)
        return c['comuna'] if c else (v.title() if v else 'Sin dato')
    return v or 'Sin dato'


def distribucion(filas, dim, top=None):
    """Cuantos llegan por valor de la dimension."""
    total = len(filas)
    g = _contar(filas, dim)
    filas_out = [{'valor': v, 'label': _etiqueta(dim, v), 'n': len(xs),
                  'pct': round(100 * len(xs) / total, 1) if total else None}
                 for v, xs in g.items()]
    filas_out.sort(key=lambda r: (r['valor'] == '', -r['n']))
    if top and len(filas_out) > top:
        resto = filas_out[top:]
        filas_out = filas_out[:top] + [{
            'valor': '_otras', 'label': 'Otras', 'n': sum(r['n'] for r in resto),
            'pct': round(sum(r['pct'] or 0 for r in resto), 1)}]
    return filas_out


def conversion(filas, dim, top=None):
    """% de inicio por valor, SOLO cohortes cerradas, con IC de Wilson."""
    g = _contar(filas, dim, solo_cerradas=True)
    out = []
    for v, xs in g.items():
        n = len(xs)
        k = sum(1 for x in xs if x['inicio'])
        perd = sum(1 for x in xs if x['perdido'])
        bajo, alto = wilson(k, n)
        ok = n >= MIN_N
        out.append({'valor': v, 'label': _etiqueta(dim, v), 'n': n,
                    'inicio': k if ok else None,
                    'pct_inicio': round(100 * k / n, 1) if ok else None,
                    'ic': [bajo, alto] if ok else None,
                    'pct_perdido': round(100 * perd / n, 1) if ok else None})
    out.sort(key=lambda r: (r['valor'] == '', -r['n']))
    if top and len(out) > top:
        out = out[:top]
    return out


def por_anio(filas, dim):
    """Composicion por ano de la primera consulta (% de cada valor)."""
    anios = sorted({f['anio'] for f in filas})
    valores = [r['valor'] for r in distribucion(filas, dim)][:8]
    serie = []
    for a in anios:
        xs = [f for f in filas if f['anio'] == a]
        n = len(xs)
        fila = {'anio': a, 'n': n}
        for v in valores:
            fila[v] = round(100 * sum(1 for x in xs if (x.get(dim) or '') == v) / n, 1) \
                if n else None
        serie.append(fila)
    return {'valores': [{'valor': v, 'label': _etiqueta(dim, v)} for v in valores],
            'serie': serie}


def recomendadores(filas, top=30):
    """Quien trae pacientes (dentistas, doctores, medicos, pacientes)."""
    g = {}
    for f in filas:
        fi = f['ficha']
        if not fi or fi['canal'] not in ('dentista', 'doctor_clinica', 'medico',
                                         'paciente') or not fi['persona_clave']:
            continue
        k = (fi['canal'], fi['persona_clave'])
        r = g.setdefault(k, {'canal': fi['canal'], 'clave': fi['persona_clave'],
                             'nombre': fi['persona_nombre'] or fi['persona_clave'].title(),
                             'trajo': 0, 'cerradas': 0, 'iniciaron': 0, 'ultimo': ''})
        r['trajo'] += 1
        r['ultimo'] = max(r['ultimo'], f['fecha'])
        if f['cerrado']:
            r['cerradas'] += 1
            r['iniciaron'] += 1 if f['inicio'] else 0
    out = sorted(g.values(), key=lambda r: (-r['trajo'], -r['iniciaron'], r['nombre']))
    for r in out:
        r['pct_inicio'] = round(100 * r['iniciaron'] / r['cerradas'], 1) \
            if r['cerradas'] >= MIN_N else None
    return out[:top]


def dentistas_habituales(filas, top=25):
    """La red de dentistas de nuestros pacientes, hayan derivado o no."""
    g = {}
    for f in filas:
        fi = f['ficha']
        if not fi or not fi['dentista_clave']:
            continue
        r = g.setdefault(fi['dentista_clave'], {
            'nombre': fi['dentista_clave'].title(), 'pacientes': 0, 'lo_recomendo': 0,
            'cerradas': 0, 'iniciaron': 0})
        r['pacientes'] += 1
        if fi['canal'] == 'dentista' and fi['persona_clave'] == fi['dentista_clave']:
            r['lo_recomendo'] += 1
        if f['cerrado']:
            r['cerradas'] += 1
            r['iniciaron'] += 1 if f['inicio'] else 0
    return sorted(g.values(), key=lambda r: -r['pacientes'])[:top]


def intereses(filas):
    """Hobbies, colegios, profesiones y motivaciones de quienes llenaron la ficha."""
    con_ficha = [f for f in filas if f['ficha']]
    n = len(con_ficha)

    def conteo(clave_fn, top):
        g = {}
        for f in con_ficha:
            for v in clave_fn(f['ficha']):
                r = g.setdefault(v, {'label': v, 'n': 0, 'cerradas': 0, 'iniciaron': 0})
                r['n'] += 1
                if f['cerrado']:
                    r['cerradas'] += 1
                    r['iniciaron'] += 1 if f['inicio'] else 0
        out = sorted(g.values(), key=lambda r: -r['n'])[:top]
        for r in out:
            r['pct'] = round(100 * r['n'] / n, 1) if n else None
            r['pct_inicio'] = round(100 * r['iniciaron'] / r['cerradas'], 1) \
                if r['cerradas'] >= MIN_N else None
        return out

    nombres_colegio = {}
    for f in con_ficha:
        k = f['ficha']['colegio_clave']
        if k:
            nombres_colegio.setdefault(k, f['ficha']['colegio'].strip())
    colegios = conteo(lambda fi: [fi['colegio_clave']] if fi['colegio_clave'] else [], 25)
    for r in colegios:
        r['label'] = r['label'].title()
    return {
        'n_fichas': n,
        'hobbies': conteo(lambda fi: json.loads(fi['intereses'] or '[]'), 20),
        'colegios': colegios,
        'profesiones': conteo(lambda fi: [fi['profesion_grupo']]
                              if fi['profesion_grupo'] else [], 12),
        'motivaciones': conteo(lambda fi: [fi['motivacion_grupo']]
                               if fi['motivacion_grupo'] else [], 8),
    }


def valor(filas):
    """Cuanto se queda cada tipo de paciente: citas que ocurrieron despues de la
    primera consulta, meses de relacion e ingresos (estos solo desde que hay
    boletas cargadas, y se dice)."""
    import kpi
    con = basedatos.conectar()
    try:
        citas = {}
        for r in con.execute(
                "SELECT rut, fecha FROM citas WHERE rut<>'' AND %s" % kpi._SQL_OCURRIO):
            citas.setdefault(r['rut'], []).append(r['fecha'])
        ingresos = {r['rut']: r['m'] for r in con.execute(
            "SELECT rut, SUM(monto) m FROM ingresos WHERE rut<>'' GROUP BY rut")}
        desde_ing = con.execute('SELECT MIN(fecha) FROM ingresos').fetchone()[0]
    finally:
        con.close()

    def metricas(xs):
        cs, meses = [], []
        for f in xs:
            post = [c for c in citas.get(f['rut'], []) if c >= f['fecha']]
            cs.append(max(0, len(post) - 1))
            if post:
                meses.append((date.fromisoformat(max(post))
                              - date.fromisoformat(f['fecha'])).days / 30.4)
        cs.sort()
        meses.sort()
        return {'n': len(xs),
                'citas_mediana': cs[len(cs) // 2] if cs else None,
                'citas_promedio': round(sum(cs) / len(cs), 1) if cs else None,
                'meses_mediana': round(meses[len(meses) // 2], 1) if meses else None}

    cerradas = [f for f in filas if f['cerrado']]
    out = {'desde_ingresos': desde_ing}
    for dim in ('canal', 'banda_edad', 'distancia', 'familia_previa'):
        g = _contar(cerradas, dim)
        out[dim] = sorted(
            [dict(valor=v, label=_etiqueta(dim, v), **metricas(xs))
             for v, xs in g.items() if len(xs) >= MIN_N],
            key=lambda r: (r['valor'] == '', -r['n']))
    out['ingreso_promedio_iniciados'] = None
    if desde_ing:
        ini = [f for f in filas if f['inicio'] and f['fecha'] >= desde_ing]
        montos = [ingresos.get(f['rut'], 0) for f in ini]
        if len(montos) >= MIN_N:
            out['ingreso_promedio_iniciados'] = round(sum(montos) / len(montos))
    return out


# ── Geografia ───────────────────────────────────────────────────────────────

def _ols(xs, ys):
    """Minimos cuadrados con intercepto. xs: lista de vectores. Devuelve coef."""
    k = len(xs[0]) + 1
    X = [[1.0] + list(x) for x in xs]
    A = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    b = [sum(r[i] * y for r, y in zip(X, ys)) for i in range(k)]
    # Gauss-Jordan
    for i in range(k):
        piv = max(range(i, k), key=lambda r: abs(A[r][i]))
        if abs(A[piv][i]) < 1e-12:
            return None
        A[i], A[piv] = A[piv], A[i]
        b[i], b[piv] = b[piv], b[i]
        for r in range(k):
            if r != i:
                f = A[r][i] / A[i][i]
                A[r] = [a - f * c for a, c in zip(A[r], A[i])]
                b[r] -= f * b[i]
    return [b[i] / A[i][i] for i in range(k)]


def geografia():
    """Penetracion por comuna contra el Censo 2024, y donde hay POTENCIAL.

    Penetracion = pacientes activos (alguna cita que ocurrio en los ultimos 36
    meses) por cada 10.000 habitantes. 'Esperada' = lo que predice un modelo
    simple con la distancia a la clinica y el nivel educativo de la comuna
    (proxy socioeconomico publico). Una comuna cercana que esta muy por debajo
    de lo esperado es donde vale la pena aparecer.

    ⚠️ Es un modelo de 2 variables sobre ~50 comunas: sirve para ORDENAR donde
    mirar, no para pronosticar pacientes. El panel lo dice.
    """
    import geocodificar as geo
    import kpi
    corte = (fechas.hoy_chile() - timedelta(days=int(MESES_ACTIVO * 30.4))).isoformat()
    con = basedatos.conectar()
    try:
        activos = {r['rut'] for r in con.execute(
            "SELECT DISTINCT rut FROM citas WHERE rut<>'' AND fecha>=? AND %s"
            % kpi._SQL_OCURRIO, (corte,))}
        ubic = list(con.execute('SELECT rut, comuna, precision FROM ubicaciones'))
    finally:
        con.close()
    por_comuna = {}
    for r in ubic:
        if r['rut'] in activos and r['comuna']:
            por_comuna[r['comuna']] = por_comuna.get(r['comuna'], 0) + 1
    con_comuna = sum(por_comuna.values())

    filas = []
    for c in geo.comunas():
        d = geo.distancia_km(c['lat'], c['lon'])
        n = por_comuna.get(c['clave'], 0)
        filas.append({
            'clave': c['clave'], 'comuna': c['comuna'], 'pacientes': n,
            'poblacion': c['poblacion'], 'pob_5_19': c.get('pob_5_19'),
            'penetracion': round(10000 * n / c['poblacion'], 2) if c['poblacion'] else None,
            'distancia_km': round(d, 1),
            'escolaridad': c.get('escolaridad_adultos'),
            'pct_educ_superior': c.get('pct_educ_superior'),
        })

    # Modelo: log(penetracion + 0,1) ~ distancia + % educacion superior,
    # solo con comunas a <= 30 km (las rurales lejanas meten ruido).
    cerca = [f for f in filas if f['distancia_km'] <= 30 and f['pct_educ_superior']]
    coef = None
    if con_comuna >= 50 and len(cerca) >= 10:
        coef = _ols([(f['distancia_km'], f['pct_educ_superior']) for f in cerca],
                    [math.log(f['penetracion'] + 0.1) for f in cerca])
    for f in filas:
        f['esperada'] = f['indice'] = f['faltan'] = None
        f['potencial'] = False
        if coef and f in cerca:
            esp = math.exp(coef[0] + coef[1] * f['distancia_km']
                           + coef[2] * f['pct_educ_superior']) - 0.1
            f['esperada'] = round(max(esp, 0), 2)
            if esp > 0.2:
                f['indice'] = round(f['penetracion'] / esp, 2)
                # Cuantos pacientes FALTAN, en numero: un 0% de lo esperado en una
                # comuna donde se esperaban 2 pacientes es ruido, no una oportunidad.
                f['faltan'] = round(esp * f['poblacion'] / 10000 - f['pacientes'])
                f['potencial'] = (f['indice'] < 0.6 and f['distancia_km'] <= 15
                                  and (f['pob_5_19'] or 0) >= 10000
                                  and f['faltan'] >= MIN_FALTAN)
    filas.sort(key=lambda f: -f['pacientes'])
    return {'comunas': filas, 'pacientes_con_comuna': con_comuna,
            'modelo': {'coef': coef, 'n_comunas': len(cerca)} if coef else None,
            'fuentes': geo.fuentes()}


def mapa(filtro=None):
    """Celdas de ~100 m con cuantos pacientes viven ahi. SIN RUT.

    filtro: {'universo': 'todos'|'pc', 'destino': 'inicio'|'perdido'|'',
             'canal': ..., 'banda_edad': ...}
    Solo usa los puntos geocodificados a nivel de CALLE: los de precision
    'comuna' se apilarian en el centro de la comuna y dibujarian un barrio que
    no existe.
    """
    filtro = filtro or {}
    con = basedatos.conectar()
    try:
        ubi = {r['rut']: (r['lat'], r['lon']) for r in con.execute(
            "SELECT rut, lat, lon FROM ubicaciones WHERE precision='calle'")}
    finally:
        con.close()
    if filtro.get('universo') == 'pc' or any(filtro.get(k) for k in
                                             ('destino', 'canal', 'banda_edad')):
        ruts = set()
        for f in base():
            if filtro.get('destino') and f['destino'] != filtro['destino']:
                continue
            if filtro.get('canal') and f['canal'] != filtro['canal']:
                continue
            if filtro.get('banda_edad') and f['banda_edad'] != filtro['banda_edad']:
                continue
            ruts.add(f['rut'])
    else:
        ruts = set(ubi)
    celdas = {}
    for rut in ruts:
        if rut in ubi:
            k = (round(ubi[rut][0], 3), round(ubi[rut][1], 3))
            celdas[k] = celdas.get(k, 0) + 1
    import geocodificar as geo
    return {'celdas': [[la, lo, n] for (la, lo), n in celdas.items()],
            'n': sum(celdas.values()), 'clinica': list(geo.CLINICA)}


def comunas_geojson():
    """FeatureCollection de las comunas (datos publicos) para el coropletico."""
    import geocodificar as geo
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'properties': {'clave': c['clave'], 'comuna': c['comuna']},
         'geometry': c['geometria']} for c in geo.comunas() if c.get('geometria')]}


# ── Hallazgos ───────────────────────────────────────────────────────────────

def hallazgos(filas, geo_res=None):
    """Frases solo cuando la diferencia es estadisticamente clara: el IC 95% del
    grupo no se solapa con el del resto. Cada una con su n."""
    out = []
    cerradas = [f for f in filas if f['cerrado']]
    for dim in ('canal', 'banda_edad', 'distancia', 'prevision', 'sexo',
                'familia_previa'):
        g = _contar(cerradas, dim)
        for v, xs in g.items():
            if not v or len(xs) < MIN_N_HALLAZGO:
                continue
            resto = [f for f in cerradas if (f.get(dim) or '') != v and f.get(dim)]
            if len(resto) < MIN_N_HALLAZGO:
                continue
            k1, k2 = sum(x['inicio'] for x in xs), sum(x['inicio'] for x in resto)
            a, b = wilson(k1, len(xs)), wilson(k2, len(resto))
            p1, p2 = 100 * k1 / len(xs), 100 * k2 / len(resto)
            if a[0] > b[1] or a[1] < b[0]:
                out.append({
                    'tipo': 'conversion', 'dimension': dim,
                    'texto': '%s: %s inicia tratamiento %s que el resto (%.0f%% vs %.0f%%, '
                             'n=%d y %d).' % (
                                 DIMENSIONES[dim], _etiqueta(dim, v),
                                 'más' if p1 > p2 else 'menos', p1, p2,
                                 len(xs), len(resto)),
                    'diferencia': round(p1 - p2, 1)})
    out.sort(key=lambda h: -abs(h['diferencia']))
    if geo_res:
        pot = sorted([c for c in geo_res['comunas'] if c['potencial']],
                     key=lambda c: c['indice'])[:3]
        for c in pot:
            out.append({'tipo': 'geografia', 'texto':
                        '%s (a %.0f km, %s niños y jóvenes de 5 a 19 años) tiene %d%% de '
                        'los pacientes que se esperarían por su distancia y nivel '
                        'educativo: unos %d menos.' % (
                            c['comuna'], c['distancia_km'],
                            '{:,}'.format(c['pob_5_19']).replace(',', '.'),
                            round(100 * c['indice']), c['faltan'])})
    return out


# ── Calidad y resumen ────────────────────────────────────────────────────────

def calidad(filas):
    n = len(filas)
    con = basedatos.conectar()
    try:
        ub = con.execute("SELECT precision, COUNT(*) n FROM ubicaciones GROUP BY 1").fetchall()
        fp = con.execute("SELECT referido_estado, COUNT(*) n FROM fichas_perfil "
                         "GROUP BY 1").fetchall()
    finally:
        con.close()

    def pct(k):
        return round(100 * k / n, 1) if n else None
    return {
        'primeras_consultas': n,
        'con_ficha': pct(sum(1 for f in filas if f['tiene_ficha'])),
        'con_edad': pct(sum(1 for f in filas if f['edad'] is not None)),
        'con_comuna': pct(sum(1 for f in filas if f['comuna'])),
        'con_punto': pct(sum(1 for f in filas if f['precision'] == 'calle')),
        'ubicaciones': {r['precision'] or 'sin_punto': r['n'] for r in ub},
        'referidos': {r['referido_estado']: r['n'] for r in fp},
    }


def resumen(desde=None, hasta=None, dim_inicio='canal'):
    """Todo lo que pinta la pestaña del panel, en una llamada."""
    filas = base(desde, hasta)
    geo_res = geografia()
    con_ficha = [f for f in filas if f['tiene_ficha']]
    return {
        'calidad': calidad(filas),
        'dimensiones': DIMENSIONES,
        'llegan': {dim: distribucion(filas if dim != 'canal' else con_ficha, dim,
                                     top=12 if dim == 'comuna' else None)
                   for dim in ('banda_edad', 'sexo', 'distancia', 'comuna', 'prevision',
                               'canal', 'familia_previa')},
        'tendencia': {dim: por_anio(filas if dim != 'canal' else con_ficha, dim)
                      for dim in ('banda_edad', 'distancia', 'canal')},
        'inician': conversion(filas if dim_inicio != 'canal' else con_ficha, dim_inicio,
                              top=15),
        'dim_inicio': dim_inicio,
        'recomendadores': recomendadores(filas),
        'dentistas_habituales': dentistas_habituales(filas),
        'intereses': intereses(filas),
        'valor': valor(filas),
        'geografia': geo_res,
        'hallazgos': hallazgos(filas, geo_res),
    }


def guardar_snapshot():
    """Una foto mensual de la composicion y la conversion, para ver la tendencia
    con el tiempo (tabla `snapshots` de kpi.py, clave 'perfil')."""
    import kpi
    filas = base()
    hoy = fechas.hoy_chile()
    detalle = {dim: {r['valor']: r['pct_inicio'] for r in conversion(filas, dim)}
               for dim in ('canal', 'banda_edad', 'distancia')}
    cerradas = [f for f in filas if f['cerrado']]
    pct = round(100 * sum(f['inicio'] for f in cerradas) / len(cerradas), 1) \
        if cerradas else None
    kpi._snapshot('perfil', pct, json.dumps(detalle, ensure_ascii=False),
                  fecha=hoy.replace(day=1))
    return pct
