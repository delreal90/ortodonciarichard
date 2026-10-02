"""
referidos.py - "¿Quien le recomendo venir con nosotros?": de texto libre a
CANAL y a PERSONA.

QUE PROBLEMA RESUELVE
---------------------
La ficha de primera consulta (Google Form) pregunta quien recomendo la clinica,
en texto libre. Medido el 2026-10-02 sobre 663 respuestas: 450 textos
distintos. Hay nombres de dentistas ("Dra. Ulloa", "Maria Teresa Ulloa"), de
pacientes, parentescos ("mi mama"), "amiga", "Google", "nadie", "yo" y
puntos sueltos. Sin ordenar eso, la pregunta mas valiosa para saber como
llegan los pacientes no contesta nada.

CANALES (`CANALES`)
-------------------
dentista       un odontologo externo que derivo (la red que hay que cultivar)
doctor_clinica uno de los cuatro especialistas de la clinica
medico         pediatra, otorrino, fonoaudiologo... (otra puerta de entrada)
paciente       una persona que esta en la base de pacientes
ya_paciente    "ya era paciente", "atenciones previas" (vuelve; no lo trajo nadie)
familia        un pariente (con o sin nombre)
amigo          amiga, conocido, colega (sin nombre identificable)
internet       Google, Instagram, la web
propio         "yo", "nadie", "por mi cuenta"
colegio        el colegio o una charla
persona        un nombre que no se pudo identificar -> por confirmar
sin_dato       vacio, ".", "-", "no"
otro           texto que no calza con nada -> por confirmar

COMO SE DECIDE QUE ALGUIEN ES DENTISTA
--------------------------------------
Sin lista que mantener: la misma ficha pregunta "¿Quien es su dentista
habitual?". Un nombre que aparece ahi en 2 o mas fichas, o que alguien
escribio con "Dr."/"Dra." delante, es un dentista. Mas los que se agreguen a
mano desde el panel. Los cuatro de la clinica se reconocen antes.

⚠️ NO SE ADIVINA A UNA PERSONA
------------------------------
Regla de carpetas.py: si un nombre calza con dos pacientes, o con ninguno, no
se elige uno -- queda `por_confirmar` y el panel lo muestra. Un ranking de
recomendadores con un nombre equivocado le agradece a quien no era.

APRENDE CON EL TIEMPO
---------------------
Cada confirmacion del panel se guarda POR TEXTO NORMALIZADO
(`referidos_config.json`), asi el mismo texto escrito por otro paciente queda
resuelto para siempre. Los alias ("Dra. Ulloa" = "Maria Teresa Ulloa") se
resuelven solos cuando son inequivocos y se pueden fijar a mano cuando no.
Todo se re-clasifica en cada proyeccion nocturna: corregir una regla corrige
la historia entera, sin re-importar nada.

CEREBRO SIN RED.
"""

import difflib
import os
import re
from pathlib import Path

import jsonstore
import texto

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
_CFG = jsonstore.JsonStore(
    Path(os.environ.get('REFERIDOS_CONFIG_PATH', _BASE_DIR / 'referidos_config.json')),
    default={'confirmados': {}, 'alias': {}, 'dentistas': [], 'no_dentistas': []})

CANALES = ('dentista', 'doctor_clinica', 'medico', 'paciente', 'ya_paciente', 'familia',
           'amigo', 'internet', 'propio', 'colegio', 'persona', 'sin_dato', 'otro')

ETIQUETAS = {
    'dentista': 'Dentista externo', 'doctor_clinica': 'Doctor de la clínica',
    'medico': 'Otro profesional de salud', 'ya_paciente': 'Ya se atendía acá',
    'paciente': 'Paciente de la clínica', 'familia': 'Familiar',
    'amigo': 'Amigo/conocido', 'internet': 'Internet / redes',
    'propio': 'Por su cuenta', 'colegio': 'Colegio',
    'persona': 'Persona sin identificar', 'sin_dato': 'Sin respuesta', 'otro': 'Otro',
}

# Un dentista: aparece como "dentista habitual" en al menos este numero de fichas.
MIN_FICHAS_DENTISTA = 2

# Los cuatro especialistas (datos publicos: index.html).
DOCTORES_CLINICA = {
    'octavio del real': 'Dr. Octavio Del Real',
    'rodrigo oyonarte': 'Dr. Rodrigo Oyonarte',
    'alberto del real': 'Dr. Alberto Del Real',
    'patricio vial': 'Dr. Patricio Vial',
}

_TITULOS = {'dr', 'dra', 'doctor', 'doctora', 'doc', 'odontologo', 'odontologa',
            'dentista', 'ortodoncista', 'sr', 'sra', 'srta', 'don', 'dona'}
# Palabras que acompanan un nombre y no son parte de el.
_RELLENO = {'mi', 'su', 'un', 'una', 'el', 'la', 'los', 'las', 'me', 'nos', 'lo',
            'recomendo', 'recomendaron', 'recomendacion', 'por', 'de', 'del', 'y',
            'derivacion', 'derivado', 'derivada', 'derivo', 'conozco', 'a'}
# Particulas que SI pueden ser parte de un apellido ("de la Jara", "del Real").
_PARTICULAS = {'de', 'del', 'la', 'las', 'los', 'y'}

# Por orden de prioridad: el primer canal cuya palabra aparezca, gana.
_PALABRAS = (
    ('sin_dato', ('no', 'x', 'na', 'n a', 'no aplica', 'no se', 'no recuerdo', 'ns',
                  'no me acuerdo', 'no lo recuerdo', 'no tengo', 'no tiene')),
    ('internet', ('google', 'internet', 'instagram', 'insta', 'facebook', 'web',
                  'pagina', 'redes', 'red social', 'redes sociales', 'busqueda',
                  'busque', 'tiktok', 'online', 'doctoralia', 'mapa', 'maps',
                  'chatgpt', 'chat gpt')),
    ('familia', ('mama', 'papa', 'madre', 'padre', 'hermano', 'hermana', 'hermanos',
                 'hermanas', 'tio', 'tia', 'tios', 'abuelo', 'abuela', 'abuelos',
                 'primo', 'prima', 'primos', 'hijo', 'hija', 'hijos', 'hijas',
                 'esposo', 'esposa', 'marido', 'pareja', 'familia', 'familiar',
                 'familiares', 'suegra', 'suegro', 'cunado', 'cunada', 'sobrino',
                 'sobrina', 'nieto', 'nieta', 'padres', 'polola', 'pololo', 'novia',
                 'novio', 'mami', 'papi', 'senora', 'mujer')),
    ('ya_paciente', ('paciente', 'pacientes', 'ya era paciente', 'soy paciente',
                     'paciente antiguo', 'ex paciente', 'atenciones previas',
                     'tratamiento anterior', 'ya me atendia', 'me atendi')),
    ('propio', ('yo', 'nadie', 'ninguno', 'ninguna', 'solo', 'sola', 'por mi cuenta',
                'iniciativa propia', 'mi decision', 'decision propia', 'propia',
                'propio', 'nadie me recomendo', 'mismo', 'misma')),
    ('medico', ('pediatra', 'otorrino', 'otorrinolaringologo', 'fonoaudiologa',
                'fonoaudiologo', 'fono', 'kinesiologo', 'kinesiologa', 'medico',
                'medica', 'neurologo', 'broncopulmonar', 'maxilofacial', 'cirujano')),
    ('amigo', ('amigo', 'amiga', 'amigos', 'amigas', 'conocido', 'conocida',
               'conocidos', 'colega', 'colegas', 'companero', 'companera', 'vecino',
               'vecina', 'vecinos', 'apoderado', 'apoderada', 'apoderados', 'trabajo',
               'jefe', 'jefa', 'recomendacion')),
    ('colegio', ('colegio', 'charla', 'jardin', 'escuela')),
)


def norm(txt):
    """Minusculas, sin tildes, sin puntuacion, espacios simples."""
    s = texto.sin_tildes(txt)
    s = re.sub(r'[^a-z0-9 ]+', ' ', s)
    return ' '.join(s.split())


def _tokens(txt):
    return norm(txt).split()


def clave_persona(txt):
    """'Dra. María Teresa Ulloa' -> 'maria teresa ulloa'. Sin titulos ni relleno
    del borde (las particulas del medio se quedan: 'de la jara')."""
    toks = [t for t in _tokens(txt) if t not in _TITULOS]
    while toks and toks[0] in _RELLENO:
        toks.pop(0)
    while toks and toks[-1] in _RELLENO:
        toks.pop()
    return ' '.join(toks)


def _nucleo(clave):
    """Tokens significativos de una clave (sin particulas) para comparar."""
    return frozenset(t for t in clave.split() if t not in _PARTICULAS)


def _compatible(a, b):
    """True si `a` es una forma abreviada de `b`: cada palabra de `a` es el
    comienzo de una palabra de `b` ('m teresa' ~ 'maria teresa'), y al menos un
    apellido de 4+ letras coincide ENTERO (si no, 'ana' calzaria con media base).
    """
    na, nb = _nucleo(a), _nucleo(b)
    if not na or not nb or na == nb:
        return False
    if not any(len(t) >= 4 and t in nb for t in na):
        return False
    return all(any(u.startswith(t) for u in nb) for t in na)


# Un nombre "completo" tiene a lo mas tantas palabras. "Andrés Pinto Carla
# Rojas" son DOS dentistas escritos juntos: no puede ser el nombre de nadie.
_MAX_PALABRAS_NOMBRE = 3


def _maximales(clave, directorio):
    """Las formas mas completas de `clave` dentro del directorio."""
    sup = [d for d in directorio if len(_nucleo(d)) <= _MAX_PALABRAS_NOMBRE
           and len(_nucleo(d)) >= len(_nucleo(clave)) and _compatible(clave, d)]
    return sorted(d for d in sup if not any(_compatible(d, e) for e in sup))


def _titulo_medico(txt):
    return any(t in ('dr', 'dra', 'doctor', 'doctora', 'odontologo', 'odontologa',
                     'dentista') for t in _tokens(txt))


def _parece_nombre(clave):
    toks = clave.split()
    nucleo = [t for t in toks if t not in _PARTICULAS]
    return (1 <= len(nucleo) <= 5 and all(t.isalpha() for t in toks)
            and any(len(t) >= 3 for t in nucleo))


def _canal_por_palabras(n):
    """Canal por palabras clave sobre el texto normalizado, o ''."""
    toks = set(n.split())
    for canal, palabras in _PALABRAS:
        for p in palabras:
            if ' ' in p:
                if re.search(r'\b%s\b' % re.escape(p), n):
                    return canal
            elif p in toks:
                if canal == 'sin_dato' and len(toks) > 2:
                    continue          # "no se acuerda, creo que fue Google"
                return canal
    return ''


# ── Contexto: lo que se arma UNA vez por corrida ─────────────────────────────

class Contexto:
    """Directorio de dentistas, doctores, pacientes y lo aprendido.

    Se arma una vez por proyeccion (no por texto): indexar 4.000 pacientes 450
    veces no tiene sentido.
    """

    def __init__(self, perfiles, indice_pacientes=None, cfg=None):
        self.cfg = cfg if cfg is not None else config()
        self.confirmados = self.cfg.get('confirmados') or {}
        self.alias = self.cfg.get('alias') or {}
        no_dent = {clave_persona(x) for x in self.cfg.get('no_dentistas') or []}

        # Dentistas: "dentista habitual" en >= MIN fichas, o escrito con Dr./Dra.
        conteo = {}
        con_titulo = set()
        for p in perfiles.values():
            k = clave_persona(p.get('dentista') or '')
            if k and _parece_nombre(k):
                conteo[k] = conteo.get(k, 0) + 1
                if _titulo_medico(p.get('dentista')):
                    con_titulo.add(k)
            r = p.get('recomendo') or ''
            if _titulo_medico(r):
                k2 = clave_persona(r)
                if k2 and _parece_nombre(k2):
                    con_titulo.add(k2)
        dentistas = {k for k, n in conteo.items() if n >= MIN_FICHAS_DENTISTA}
        dentistas |= con_titulo
        # "no tengo", "mama fue paciente": la pregunta del dentista habitual tambien
        # trae frases, y una frase no es un dentista.
        dentistas = {k for k in dentistas
                     if not _canal_por_palabras(k) and len(_nucleo(k)) <= 4}
        dentistas |= {clave_persona(x) for x in self.cfg.get('dentistas') or []}
        dentistas -= no_dent
        self.doctores = dict(DOCTORES_CLINICA)
        dentistas = {d for d in dentistas if not self._es_doctor(d)}
        self.dentistas = dentistas
        self.conteo_dentista_habitual = conteo

        # Pacientes: nucleo de tokens -> [rut]
        self.pacientes = {}
        self.nombres = {}
        for rut, rec in (indice_pacientes or {}).items():
            nombre = ('%s %s' % (rec.get('nombres', ''), rec.get('apellidos', ''))).strip()
            k = clave_persona(nombre)
            if not k:
                continue
            self.nombres[rut] = nombre
            self.pacientes.setdefault(_nucleo(k), []).append(rut)
        self._lista_pacientes = list(self.pacientes.items())

    def _es_doctor(self, clave):
        nk = _nucleo(clave)
        if not nk:
            return ''
        # El texto ES el doctor ("dr oyonarte") o lo CONTIENE entero
        # ("conozco a rodrigo oyonarte").
        hits = [d for d in self.doctores if nk <= _nucleo(d) or _nucleo(d) <= nk]
        if len(hits) == 1:
            return hits[0]
        if hits:
            # "del real" calza con Octavio y Alberto: es la clinica, persona ambigua.
            return 'del real' if all('del real' in h for h in hits) else hits[0]
        return ''

    def canonica(self, clave):
        """Resuelve un alias. Manual primero; si no, el nombre mas completo del
        mismo dentista cuando es INEQUIVOCO ('ulloa', 'tere ulloa' y 'm teresa
        ulloa' -> 'maria teresa ulloa'). Con dos posibles no se elige."""
        if clave in self.alias:
            return self.alias[clave]
        sup = _maximales(clave, self.dentistas)
        if len(sup) == 1:
            return sup[0]
        if sup:
            return clave
        if clave in self.dentistas:
            return clave
        # Un error de tipeo ('beatriz montalba'): mismas palabras y casi igual a
        # UN solo dentista. Exigente a proposito.
        parecidos = [d for d in self.dentistas
                     if len(d.split()) == len(clave.split()) and len(clave) >= 8
                     and difflib.SequenceMatcher(None, clave, d).ratio() >= 0.9]
        return self.canonica(parecidos[0]) if len(parecidos) == 1 else clave

    def buscar_paciente(self, clave):
        """[ruts] cuyo nombre contiene TODOS los tokens de la clave (enteros).
        Exige 2 tokens: un nombre de pila suelto calza con media base."""
        nk = _nucleo(clave)
        if len(nk) < 2:
            return []
        out = []
        for nucleo, ruts in self._lista_pacientes:
            if nk <= nucleo:
                out.extend(ruts)
        return out


def _resultado(canal, estado, clave='', nombre='', rut='', candidatos=0):
    return {'canal': canal, 'estado': estado, 'clave': clave, 'nombre': nombre,
            'rut': rut, 'candidatos': candidatos}


def clasificar(txt, ctx, rut_paciente=''):
    """Clasifica UNA respuesta. Devuelve {canal, estado, clave, nombre, rut,
    candidatos}. estado: 'confirmado' (lo fijo una persona), 'auto' o
    'por_confirmar'."""
    n = norm(txt)
    if not n or len(n) <= 1:
        return _resultado('sin_dato', 'auto')

    conf = ctx.confirmados.get(n)
    if conf:
        return _resultado(conf.get('canal', 'otro'), 'confirmado',
                          conf.get('clave', ''), conf.get('nombre', ''),
                          conf.get('rut', ''))

    clave = clave_persona(txt)
    titulo = _titulo_medico(txt)

    # 1. Doctores de la clinica (antes que las palabras: "conozco a Rodrigo Oyonarte").
    doc = ctx._es_doctor(clave) if clave else ''
    if doc:
        return _resultado('doctor_clinica', 'auto', doc,
                          DOCTORES_CLINICA.get(doc, 'Dr. Del Real'))

    # 2. Palabras clave (familia, amigo, internet...). ANTES que el directorio de
    #    dentistas, salvo que el texto traiga "Dr./Dra." delante de un nombre:
    #    "mama fue paciente" no es un dentista aunque se haya escrito dos veces.
    canal = _canal_por_palabras(n)
    if canal and not (titulo and clave and _parece_nombre(clave)):
        return _resultado(canal, 'auto')
    if titulo and not clave:
        return _resultado('dentista', 'auto')      # "mi dentista", sin nombre

    # 3. El directorio de dentistas (incluye alias: "Dra. Ulloa").
    canon = ctx.canonica(clave) if clave else ''
    if canon and canon in ctx.dentistas:
        return _resultado('dentista', 'auto', canon, _titulo_nombre(canon))

    # 4. Un nombre: dentista con titulo que no estaba en el directorio, o paciente.
    if clave and _parece_nombre(clave):
        if titulo:
            return _resultado('dentista', 'auto', canon, _titulo_nombre(canon))
        ruts = [r for r in ctx.buscar_paciente(clave) if r != rut_paciente]
        if len(ruts) == 1:
            return _resultado('paciente', 'auto', clave,
                              ctx.nombres.get(ruts[0], clave.title()), ruts[0])
        return _resultado('persona', 'por_confirmar', canon, canon.title(),
                          candidatos=len(ruts))
    return _resultado('otro', 'por_confirmar')


def _titulo_nombre(clave):
    return ('Dr(a). ' + clave.title()) if clave else ''


def clasificar_todas(perfiles, indice_pacientes=None, cfg=None):
    """{rut: resultado} para todas las fichas, con un solo Contexto."""
    ctx = Contexto(perfiles, indice_pacientes, cfg)
    return {rut: clasificar(p.get('recomendo') or '', ctx, rut)
            for rut, p in perfiles.items()}, ctx


def por_confirmar(perfiles, clasificados):
    """Textos pendientes agrupados (el mismo texto se confirma una vez)."""
    grupos = {}
    for rut, res in clasificados.items():
        if res['estado'] != 'por_confirmar':
            continue
        txt = (perfiles.get(rut) or {}).get('recomendo') or ''
        k = norm(txt)
        g = grupos.setdefault(k, {'texto': txt.strip(), 'norm': k, 'veces': 0,
                                  'canal': res['canal'], 'candidatos': res['candidatos']})
        g['veces'] += 1
    return sorted(grupos.values(), key=lambda g: (-g['veces'], g['norm']))


# ── Configuracion (lo que el panel ensena) ───────────────────────────────────

def config():
    c = _CFG.load() or {}
    for k, v in (('confirmados', {}), ('alias', {}), ('dentistas', []),
                 ('no_dentistas', [])):
        c.setdefault(k, v)
    return c


def confirmar(txt, canal, nombre='', rut=''):
    """Fija para siempre como se lee un texto. Devuelve la clave normalizada."""
    if canal not in CANALES:
        raise ValueError('canal desconocido: %s' % canal)
    n = norm(txt)
    if not n:
        raise ValueError('texto vacio')
    clave = clave_persona(nombre) if nombre else ''

    def fn(c):
        c = dict(c or {})
        conf = dict(c.get('confirmados') or {})
        conf[n] = {'canal': canal, 'clave': clave, 'nombre': (nombre or '').strip(),
                   'rut': (rut or '').strip()}
        c['confirmados'] = conf
        if canal == 'dentista' and clave:
            dent = list(c.get('dentistas') or [])
            if clave not in dent:
                dent.append(clave)
            c['dentistas'] = dent
        return c
    _CFG.actualizar(fn)
    return n


def olvidar(txt):
    n = norm(txt)
    _CFG.actualizar(lambda c: {**(c or {}), 'confirmados': {
        k: v for k, v in ((c or {}).get('confirmados') or {}).items() if k != n}})


def fijar_alias(origen, destino):
    """'dra ulloa' -> 'maria teresa ulloa'. Destino vacio = borrar el alias."""
    o, d = clave_persona(origen), clave_persona(destino)
    if not o:
        raise ValueError('origen vacio')

    def fn(c):
        c = dict(c or {})
        al = dict(c.get('alias') or {})
        if d and d != o:
            al[o] = d
        else:
            al.pop(o, None)
        c['alias'] = al
        return c
    _CFG.actualizar(fn)


def marcar_no_dentista(nombre, quitar=False):
    k = clave_persona(nombre)

    def fn(c):
        c = dict(c or {})
        lst = [x for x in (c.get('no_dentistas') or []) if x != k]
        if not quitar:
            lst.append(k)
        c['no_dentistas'] = lst
        return c
    _CFG.actualizar(fn)
