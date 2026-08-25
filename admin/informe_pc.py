"""
informe_pc.py - Informe de Primera Consulta: el documento que el paciente se
lleva impreso antes de irse de la clinica.

POR QUE EXISTE
--------------
Hasta ahora el paciente pagaba su primera consulta y el unico papel que se
llevaba era el PRESUPUESTO. Todo el acto profesional -- el examen, el analisis
facial, el juicio del especialista -- se entregaba hablando, y de lo hablado se
retiene cerca de la mitad. La lectura que quedaba era "me cobraron para decirme
que hay que tomar mas examenes". Este modulo convierte esa conversacion en tres
hojas con la firma del doctor:

  Hoja 1  Informe de evaluacion   (motivo, lo que se hizo, mediciones, hallazgos,
                                   impresion diagnostica inicial, plan de accion)
  Hoja 2  Tamizaje de via aerea y sueno  (PSQ-CL o STOP-BANG + FAIREST)
  Hoja 3  Orden de examenes complementarios

El molde es el After-Visit Summary de la medicina general (estandar en atencion
primaria en EE.UU. desde Meaningful Use): mejora comprension, recuerdo y
satisfaccion, con UNA condicion dura -- que sea fiel a esa consulta y este
escrito sin jerga. Un formulario generico impreso se nota y resta. De ahi que el
motivo de consulta vaya en las palabras del paciente y las mediciones sean suyas.

CEREBRO SIN RED: catalogos, armado del documento y registro en JSON. No llama a
DentiDesk ni manda correos. Las mediciones las interpreta transversal.py y el
tamizaje lo puntuan fairest.py / stopbang.py / psq.py.

⚠️ TODO EL TEXTO DE ESTE ARCHIVO SE IMPRIME CON LA FIRMA DEL DOCTOR. No es copy
de marketing ni texto de relleno: cambiar una frase aca cambia lo que un
profesional afirma por escrito ante un paciente. Los catalogos son un BORRADOR
hasta que el Dr. Alberto los valide uno por uno (Fase 0 del plan).

REGLAS DE REDACCION QUE NO SE NEGOCIAN
---------------------------------------
- Nunca "diagnostico" a secas para lo del dia 1: es IMPRESION DIAGNOSTICA INICIAL.
- El bloque del Estudio Integral va SIEMPRE despues de los hallazgos y no pasa de
  cuatro lineas. Si el documento se siente comercial, fracaso completo.
- Sin montos. La plata vive en el presupuesto, que va aparte.
- El tamizaje de sueno no diagnostica nada y no se vincula con la expansion
  palatina (ver FRASES_PROHIBIDAS en fairest.py).
- El paciente al que se le dice "no requiere tratamiento" es el que hoy se va
  peor: pago por una buena noticia y se fue con las manos vacias. Para el, este
  documento ES el producto completo.
"""

import os
import secrets
from datetime import timedelta
from pathlib import Path

import fechas       # hoy_chile()/ahora_chile(): Render corre en UTC. Ver fechas.py.
import jsonstore    # guardado atomico con lock. Ver jsonstore.py.

_BASE_DIR = Path(os.environ.get('PATIENT_INDEX_PATH',
                                Path(__file__).parent / 'patient_index.json')).parent
REGISTRO_PATH = Path(os.environ.get('INFORME_PC_REGISTRO_PATH',
                                    _BASE_DIR / 'informe_pc_registro.json'))

# Datos clinicos con RUT: disco persistente, gitignored, nunca a git ni al log.
_STORE = jsonstore.JsonStore(REGISTRO_PATH, default={'informes': {}}, indent=2,
                             claves={'informes': {}})

# ── Textos fijos de la Hoja 1 ────────────────────────────────────────────

# Este bloque es el corazon del asunto: hace visible el trabajo que hoy no se ve.
# Cero clics para el doctor.
EVALUACION_REALIZADA = (
    'Escaneo digital 3D de sus dientes y arcadas, sin radiación.',
    'Examen clínico de dientes, mordida, encías y articulación.',
    'Análisis de su cara y de su sonrisa.',
    'Mediciones de sus arcadas comparadas con valores de referencia para su edad y sexo.',
    'Tamizaje de respiración y sueño.',
    'Revisión de sus antecedentes de salud y respuesta a sus consultas.',
)

QUE_APORTA_ESTUDIO = (
    'Lo que usted realizó hoy es la evaluación clínica y la opinión de un especialista. '
    'El Estudio Integral es un acto distinto: responde lo que no se puede ver mirando la '
    'boca -- la posición de las raíces, las piezas que aún no salen, los terceros molares y '
    'la relación entre los huesos de la cara. Comprende dos citas: una para tomar los '
    'registros y otra para explicarle el diagnóstico y el plan de tratamiento por escrito.'
)

DISCLAIMER = ('Este documento corresponde a una evaluación clínica inicial. No reemplaza el '
              'diagnóstico ni el plan de tratamiento, que se entregan una vez realizado el '
              'Estudio Integral de Ortodoncia.')

NOTA_MEDICIONES = ('Las mediciones se obtuvieron del escaneo digital realizado hoy. Los valores '
                   'de referencia corresponden a población sin tratamiento previo y son '
                   'orientativos: un valor fuera del promedio no es por sí solo una enfermedad.')


# ── Catalogo de hallazgos (BORRADOR - pendiente de validacion clinica) ───
#
# Cada hallazgo trae:
#   etiqueta   lo que el doctor ve en la casilla (corto, tecnico, para marcar rapido)
#   texto      lo que se imprime para el paciente (sin jerga)
#   relevancia por que importa; se imprime a continuacion del hallazgo
#
# La relevancia esta escrita para informar, no para asustar. Nada de "puede
# provocar" cuando lo honesto es "se asocia a".

CATALOGO_HALLAZGOS = (
    ('espacio', 'Espacio y alineación', (
        ('apinamiento', 'Apiñamiento',
         'Los dientes no tienen todo el espacio que necesitan y se ubican montados entre sí.',
         'El apiñamiento dificulta la higiene en las caras que quedan tapadas, que es donde '
         'suelen partir las caries y la inflamación de las encías.'),
        ('diastemas', 'Espacios / diastemas',
         'Hay espacios entre los dientes.',
         'Conviene precisar si sobra hueso, faltan piezas o los dientes son más pequeños de '
         'lo habitual, porque el tratamiento cambia en cada caso.'),
        ('pieza_no_erupcionada', 'Pieza sin erupcionar',
         'Hay una o más piezas que todavía no aparecen en boca.',
         'Saber donde estan y hacia donde vienen requiere imágenes: una pieza que se desvía '
         'puede dañar la raiz de la vecina sin dar ninguna molestia.'),
        ('ausencia_dentaria', 'Ausencia dentaria',
         'Falta una o más piezas dentarias.',
         'El espacio que queda se cierra o se conserva segun el plan, y esa decisión '
         'condiciona todo el tratamiento.'),
        ('erupcion_alterada', 'Erupción o recambio alterado',
         'El recambio de dientes de leche a definitivos no esta siguiendo el orden esperado.',
         'Detectarlo a tiempo permite guiar la erupción en vez de corregirla despues.'),
    )),
    ('relacion', 'Relación entre las arcadas', (
        ('resalte_aumentado', 'Resalte aumentado',
         'Los dientes de arriba quedan bastante por delante de los de abajo.',
         'Los incisivos muy expuestos se fracturan con más facilidad ante un golpe.'),
        ('mordida_invertida', 'Mordida invertida anterior',
         'Al morder, algunos dientes de abajo quedan por delante de los de arriba.',
         'Se asocia a desgaste y a sobrecarga de las piezas involucradas.'),
        ('mordida_abierta', 'Mordida abierta anterior',
         'Al morder, los dientes de adelante no llegan a tocarse.',
         'Obliga a los dientes de atrás a hacer todo el trabajo de masticar.'),
        ('mordida_profunda', 'Mordida profunda',
         'Los dientes de arriba cubren casi por completo a los de abajo al morder.',
         'Puede lesionar la encía del paladar y desgastar los bordes de los incisivos.'),
        ('clase_ii', 'Relación de Clase II',
         'La arcada de abajo queda por detras de la de arriba respecto de lo esperado.',
         'Distinguir si es por posición de los dientes o de los huesos requiere el estudio, '
         'y de eso depende el tipo de tratamiento.'),
        ('clase_iii', 'Relación de Clase III',
         'La arcada de abajo queda por delante de la de arriba respecto de lo esperado.',
         'Su manejo depende mucho de la edad y de cuanto crecimiento queda por delante.'),
        ('linea_media', 'Desviación de línea media',
         'El centro de la arcada de arriba y el de abajo no coinciden.',
         'Es lo que suele notarse en el espejo y en las fotos.'),
    )),
    ('transversal', 'Dimensión transversal', (
        ('mordida_cruzada', 'Mordida cruzada posterior',
         'Al morder, los dientes de atrás de un lado quedan por dentro de los del otro lado.',
         'Se asocia a masticar preferentemente de un lado y a desviaciones al cerrar.'),
        ('arcada_estrecha', 'Arcada superior estrecha',
         'El ancho de la arcada superior esta bajo el promedio para su edad y sexo.',
         'Es un hallazgo que se toma en cuenta al planificar, y tambien es una de las señales '
         'que se revisan en el tamizaje de respiración.'),
        ('mordida_en_tijera', 'Mordida en tijera',
         'Los dientes de atrás de arriba muerden completamente por fuera de los de abajo.',
         'Limita el contacto util para masticar en ese sector.'),
    )),
    ('funcion', 'Función y hábitos', (
        ('respiracion_bucal', 'Respiración bucal',
         'Se observa respiración preferentemente por la boca.',
         'Conviene establecer su causa; se evalua en el tamizaje de la segunda hoja.'),
        ('deglucion_atipica', 'Deglución atípica',
         'Al tragar, la lengua se apoya contra los dientes en vez de contra el paladar.',
         'Ese empuje repetido puede mantener abierta una mordida o devolverla despues del '
         'tratamiento, por eso a veces se indica terapia miofuncional.'),
        ('succion', 'Habito de succión',
         'Existe o existio hábito de succión (dedo, chupete u otro).',
         'Su efecto depende de cuanto tiempo se mantuvo y de la edad a la que cesó.'),
    )),
    ('asociados', 'Signos asociados', (
        ('desgaste', 'Desgaste dentario',
         'Se observan signos de desgaste en los dientes.',
         'El desgaste no se recupera solo; conviene identificar que lo esta produciendo.'),
        ('encias', 'Compromiso de encías',
         'Las encías muestran signos de inflamación o retracción.',
         'La ortodoncia requiere encías sanas: mover dientes sobre una encía inflamada empeora '
         'el cuadro.'),
        ('higiene', 'Higiene por reforzar',
         'La higiene puede mejorar antes de iniciar tratamiento.',
         'Con aparatos, el riesgo de caries y de manchas blancas sube; por eso se refuerza '
         'antes y no despues.'),
        ('caries_restauraciones', 'Caries o restauraciones por resolver',
         'Hay piezas que conviene tratar con su dentista antes de comenzar.',
         'Una lesión activa bajo un aparato es mucho más dificil y cara de resolver.'),
        ('atm', 'Signos en la articulación',
         'Se pesquisan ruidos o molestias en la articulación de la mandibula.',
         'Se registra y se sigue en el tiempo; no todo ruido articular requiere tratamiento.'),
        ('terceros_molares', 'Terceros molares por evaluar',
         'Corresponde evaluar la situación de las muelas del juicio.',
         'Su posición solo se establece con imágenes.'),
    )),
)

HALLAZGOS = {h[0]: {'grupo': g, 'grupo_label': gl, 'etiqueta': h[1], 'texto': h[2],
                    'relevancia': h[3]}
             for g, gl, items in CATALOGO_HALLAZGOS for h in items}

SIN_HALLAZGOS = ('sin_hallazgos', 'Sin hallazgos relevantes',
                 'El examen de hoy no muestra alteraciones que requieran tratamiento.')


# ── Impresion diagnostica inicial ────────────────────────────────────────
# Una sola, obligatoria. El texto lo lee el paciente.

CONCLUSIONES = (
    ('corresponde', 'Corresponde tratamiento de ortodoncia',
     'De acuerdo con lo evaluado hoy, corresponde realizar tratamiento de ortodoncia. '
     'El plan definitivo se establece con el Estudio Integral.'),
    ('control_evolucion', 'Aún no corresponde: control de evolución',
     'Hoy no corresponde iniciar tratamiento. Lo que se observa debe seguirse en el tiempo, '
     'porque el momento oportuno para intervenir depende del crecimiento y del recambio '
     'dentario. Se cita a control de evolución en {meses} meses.'),
    ('no_requiere', 'No requiere tratamiento',
     'De acuerdo con lo evaluado hoy, usted no requiere tratamiento de ortodoncia. '
     'Este documento deja constancia de la evaluación realizada y de sus mediciones.'),
    ('resolver_previo', 'Requiere resolver otra condición primero',
     'Antes de iniciar ortodoncia corresponde resolver lo indicado en el plan de acción. '
     'Una vez resuelto, se reevalúa.'),
    ('interdisciplinario', 'Requiere evaluación interdisciplinaria',
     'El caso requiere ser evaluado en conjunto con otra especialidad antes de definir un '
     'plan de tratamiento.'),
)

CONCLUSIONES_MAP = {c[0]: {'etiqueta': c[1], 'texto': c[2]} for c in CONCLUSIONES}


# ── Hoja 3: orden de examenes ────────────────────────────────────────────
#
# SOLO imagenes y examenes dentales. Las derivaciones medicas NO van aca: son
# otro acto, dirigido a un colega, y mezclarlas convertiria esta hoja en algo
# que no es. La sugerencia de evaluacion medica vive en la Hoja 2 y es una
# recomendacion, no una orden.

CATALOGO_ORDENES = (
    ('rx_panoramica', 'Radiografía panorámica', 'Ortopantomografía'),
    ('tele_perfil', 'Telerradiografía de perfil', 'Con análisis cefalométrico'),
    ('tele_frontal', 'Telerradiografía frontal', 'Postero-anterior'),
    ('cbct', 'Tomografía computarizada de haz cónico (CBCT)', ''),
    ('rx_periapical', 'Radiografías periapicales', ''),
    ('rx_carpal', 'Radiografía carpal', 'Evaluación de maduración ósea'),
    ('fotografias', 'Fotografías clínicas', 'Intraorales y extraorales'),
    ('escaneo', 'Escaneo intraoral / modelos de estudio', ''),
    ('analisis_modelos', 'Análisis de modelos', ''),
)

ORDENES = {o[0]: {'etiqueta': o[1], 'detalle': o[2]} for o in CATALOGO_ORDENES}

TEXTO_ORDEN = ('Se solicitan los exámenes marcados con el fin de completar el estudio '
               'diagnóstico del paciente.')


# ── Catalogo que consume el frontend ─────────────────────────────────────

def catalogo():
    """Todo lo que la pagina necesita para dibujar el formulario. Se sirve por
    endpoint para poder ajustar textos sin tocar el frontend."""
    return {
        'hallazgos': [{'grupo': g, 'label': gl,
                       'items': [{'clave': c, 'etiqueta': e, 'texto': t, 'relevancia': r}
                                 for c, e, t, r in items]}
                      for g, gl, items in CATALOGO_HALLAZGOS],
        'sin_hallazgos': {'clave': SIN_HALLAZGOS[0], 'etiqueta': SIN_HALLAZGOS[1],
                          'texto': SIN_HALLAZGOS[2]},
        'conclusiones': [{'clave': c, 'etiqueta': e, 'texto': t} for c, e, t in CONCLUSIONES],
        'relaciones': [{'valor': v, 'etiqueta': e} for v, e, _ in RELACIONES],
        'ordenes': [{'clave': c, 'etiqueta': e, 'detalle': d} for c, e, d in CATALOGO_ORDENES],
        'textos': {'evaluacion_realizada': list(EVALUACION_REALIZADA),
                   'que_aporta_estudio': QUE_APORTA_ESTUDIO,
                   'disclaimer': DISCLAIMER,
                   'nota_mediciones': NOTA_MEDICIONES,
                   'texto_orden': TEXTO_ORDEN},
    }


# ── Registro ─────────────────────────────────────────────────────────────

def _nuevo_id():
    return secrets.token_hex(8)


def guardar(datos):
    """Guarda un informe y devuelve su id. Si viene 'id', actualiza el existente
    conservando lo que ya tenia (asi reeditar no borra el sello de impresion)."""
    iid = (datos.get('id') or '').strip() or _nuevo_id()
    ahora = fechas.ahora_chile().isoformat(timespec='seconds')

    def _fn(reg):
        previo = reg['informes'].get(iid, {})
        item = dict(previo)
        item.update(datos)
        item['id'] = iid
        item['fecha'] = previo.get('fecha') or datos.get('fecha') or fechas.hoy_chile().isoformat()
        item['creado'] = previo.get('creado') or ahora
        item['actualizado'] = ahora
        # Si se edita algo que YA se imprimio, el papel que tiene el paciente
        # quedo desactualizado. Se marca para que recepcion lo vea en su lista y
        # sepa que hay que reimprimirlo. NO se borra la marca de impreso: que
        # paso por la impresora es un hecho, y borrarla seria falsear el
        # historial.
        item.setdefault('impreso', None)
        if previo.get('impreso'):
            item['editado_tras_imprimir'] = ahora
        # El formulario manda los TITULOS de las imagenes, no las imagenes: los
        # archivos ya viven en disco. Si la lista saliera del formulario, un
        # guardado normal borraria todas las fotos del informe.
        titulos = {t.get('archivo'): (t.get('titulo') or '').strip()[:80]
                   for t in (datos.get('titulos_imagenes') or []) if t.get('archivo')}
        item['imagenes'] = [dict(img, titulo=titulos.get(img.get('archivo'), img.get('titulo', '')))
                            for img in (previo.get('imagenes') or [])]
        item.pop('titulos_imagenes', None)
        reg['informes'][iid] = item
        return reg

    _STORE.actualizar(_fn)
    return iid


def obtener(iid):
    return _STORE.load().get('informes', {}).get(iid)


def listar(fecha=None, solo_pendientes=False):
    """Informes de una fecha (por defecto hoy). Ordenados del mas nuevo al mas
    viejo, que es como los quiere ver recepcion."""
    fecha = fecha or fechas.hoy_chile().isoformat()
    items = [i for i in _STORE.load().get('informes', {}).values() if i.get('fecha') == fecha]
    if solo_pendientes:
        items = [i for i in items if not i.get('impreso')]
    return sorted(items, key=lambda i: i.get('creado') or '', reverse=True)


def marcar_impreso(iid, quien=''):
    """Marca un informe como impreso. Devuelve True si existia."""
    encontrado = {'ok': False}

    def _fn(reg):
        item = reg['informes'].get(iid)
        if item:
            item['impreso'] = fechas.ahora_chile().isoformat(timespec='seconds')
            item['impreso_por'] = quien or item.get('impreso_por') or ''
            encontrado['ok'] = True
        return reg

    _STORE.actualizar(_fn)
    return encontrado['ok']


def podar(dias=None):
    """Saca del registro los informes mas viejos que N dias. Se llama desde el
    scheduler; el registro no tiene por que crecer para siempre."""
    dias = dias or 365
    limite = (fechas.hoy_chile() - timedelta(days=dias)).isoformat()
    borrados = {'n': 0}

    def _fn(reg):
        for iid in [k for k, v in reg['informes'].items() if (v.get('fecha') or '') < limite]:
            del reg['informes'][iid]
            borrados['n'] += 1
        return reg

    _STORE.actualizar(_fn)
    return borrados['n']



# ── Imagenes del informe ─────────────────────────────────────────────────
#
# Fotos clinicas, capturas del escaneo, lo que el Dr. quiera anexar. Se guardan
# como ARCHIVOS en el disco persistente y NO dentro del JSON del registro: un
# informe con cuatro fotos en base64 haria que cada lectura del registro
# arrastre megabytes, y ese registro se lee entero en cada guardado.
#
# El navegador manda DOS versiones ya reducidas: la de impresion y una
# miniatura. Se hace en el cliente porque en Render no hay Pillow (solo esta en
# el PC de la clinica, para la etiquetadora) y porque asi el request cabe en el
# MAX_CONTENT_LENGTH de 3 MB del servidor.

IMAGENES_DIR = Path(os.environ.get('INFORME_PC_IMAGENES_DIR',
                                   _BASE_DIR / 'informe_pc_imagenes'))

MAX_IMAGENES = 8
# Un dataURL de 2,2 MB deja margen bajo el limite de 3 MB del request contando
# el resto del cuerpo JSON.
MAX_BYTES_IMAGEN = 2_200_000

_EXT_POR_MIME = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp'}


def _decodificar(dataurl):
    """dataURL -> (bytes, extension). Devuelve (None, motivo) si no sirve.

    Solo se aceptan los tres formatos que produce un canvas de navegador: si
    llega otra cosa, es que no vino de donde creemos que vino.
    """
    import base64
    if not isinstance(dataurl, str) or not dataurl.startswith('data:'):
        return None, 'no es una imagen'
    try:
        cabecera, datos = dataurl.split(',', 1)
        mime = cabecera[5:].split(';')[0].strip().lower()
    except ValueError:
        return None, 'imagen mal formada'
    ext = _EXT_POR_MIME.get(mime)
    if not ext:
        return None, 'formato no aceptado (%s)' % mime
    try:
        crudo = base64.b64decode(datos, validate=True)
    except Exception:
        return None, 'imagen mal codificada'
    if not crudo:
        return None, 'imagen vacia'
    if len(crudo) > MAX_BYTES_IMAGEN:
        return None, 'la imagen pesa demasiado'
    return (crudo, ext), None


def agregar_imagen(informe_id, data, thumb, titulo=''):
    """Guarda una imagen del informe. Devuelve {'ok':True,'imagen':{...}} o
    {'ok':False,'error':...} -- mismo contrato con 'ok' que link_agenda."""
    item = obtener(informe_id)
    if not item:
        return {'ok': False, 'error': 'No se encontro ese informe'}
    if len(item.get('imagenes') or []) >= MAX_IMAGENES:
        return {'ok': False, 'error': 'Maximo %d imagenes por informe' % MAX_IMAGENES}

    grande, err = _decodificar(data)
    if err:
        return {'ok': False, 'error': err}
    chica, err = _decodificar(thumb or data)
    if err:
        return {'ok': False, 'error': err}

    IMAGENES_DIR.mkdir(parents=True, exist_ok=True)
    base = '%s_%s' % (informe_id, secrets.token_hex(4))
    archivo, archivo_t = base + grande[1], base + '_t' + chica[1]
    (IMAGENES_DIR / archivo).write_bytes(grande[0])
    (IMAGENES_DIR / archivo_t).write_bytes(chica[0])

    reg = {'archivo': archivo, 'thumb': archivo_t,
           'titulo': (titulo or '').strip()[:80],
           'agregada': fechas.ahora_chile().isoformat(timespec='seconds')}

    def _fn(datos):
        it = datos['informes'].get(informe_id)
        if it is not None:
            it.setdefault('imagenes', []).append(reg)
        return datos

    _STORE.actualizar(_fn)
    return {'ok': True, 'imagen': reg}


def borrar_imagen(informe_id, archivo):
    """Saca una imagen del informe y borra sus archivos."""
    quitada = {'ok': False}

    def _fn(datos):
        it = datos['informes'].get(informe_id)
        if not it:
            return datos
        quedan = []
        for img in (it.get('imagenes') or []):
            if img.get('archivo') == archivo:
                quitada['ok'] = True
                quitada['reg'] = img
            else:
                quedan.append(img)
        it['imagenes'] = quedan
        return datos

    _STORE.actualizar(_fn)
    if quitada['ok']:
        for clave in ('archivo', 'thumb'):
            nombre = (quitada.get('reg') or {}).get(clave)
            # Nunca se construye una ruta con lo que llego de afuera sin
            # comprobar que cae DENTRO del directorio de imagenes.
            if nombre and _dentro_de_imagenes(nombre):
                try:
                    (IMAGENES_DIR / nombre).unlink()
                except OSError:
                    pass
    return quitada['ok']


def _dentro_de_imagenes(nombre):
    """True si `nombre` es un archivo directo de IMAGENES_DIR (sin '..' ni rutas
    absolutas). Es la guarda contra un traversal por el nombre de archivo."""
    if not nombre or '/' in nombre or '\\' in nombre or nombre.startswith('.'):
        return False
    try:
        destino = (IMAGENES_DIR / nombre).resolve()
        return destino.parent == IMAGENES_DIR.resolve()
    except OSError:
        return False


def imagen_data_uri(nombre):
    """Una imagen guardada, como data URI. Devuelve '' si no existe.

    Se embeben en el documento en vez de servirse por URL porque un <img> no
    manda el header del token, y estas son fotos clinicas de un paciente: no
    pueden quedar en una ruta que baste adivinar.
    """
    import base64
    import mimetypes
    if not _dentro_de_imagenes(nombre):
        return ''
    ruta = IMAGENES_DIR / nombre
    if not ruta.exists():
        return ''
    mime = mimetypes.guess_type(str(ruta))[0] or 'image/jpeg'
    return 'data:%s;base64,%s' % (mime, base64.b64encode(ruta.read_bytes()).decode())


def imagenes_de(informe_id, thumbs=False):
    """Las imagenes de un informe, con su contenido embebido."""
    item = obtener(informe_id) or {}
    out = []
    for img in (item.get('imagenes') or []):
        nombre = img.get('thumb') if thumbs else img.get('archivo')
        out.append({'archivo': img.get('archivo'), 'titulo': img.get('titulo', ''),
                    'src': imagen_data_uri(nombre)})
    return out

# ── Armado del documento ─────────────────────────────────────────────────
#
# Toda la logica clinica vive aca y no en el HTML: la pagina solo pinta lo que
# este dict le entrega. Asi el mismo documento se puede reimprimir meses
# despues, o llevarlo a otro formato, sin reimplementar ninguna regla.

_MESES = ('enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio',
          'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre')

EDAD_ADULTO = 18   # desde aca se usa STOP-BANG y el FAIREST 6+4 en vez del PSQ y el 6

# Las cuatro mediciones transversales que van con curva y percentil. El orden es
# el de la hoja impresa: primero el maxilar, que es el que interesa mirar.
MEDICIONES_TRANSVERSALES = (
    ('intercanino_maxilar',    'intercanino', 'maxilar',    'Ancho intercanino superior'),
    ('intermolar_maxilar',     'intermolar',  'maxilar',    'Ancho intermolar superior'),
    ('intercanino_mandibular', 'intercanino', 'mandibular', 'Ancho intercanino inferior'),
    ('intermolar_mandibular',  'intermolar',  'mandibular', 'Ancho intermolar inferior'),
)

MEDICIONES_SIMPLES = (
    ('resalte', 'Resalte (overjet)', 'mm'),
    ('sobremordida', 'Sobremordida (overbite)', 'mm'),
)

# Clase molar y canina se registran POR LADO: un caso puede ser Clase I a la
# derecha y Clase II a la izquierda, y esa asimetria es justamente lo que
# cambia el plan. Guardar un solo valor obligaria a elegir cual mentir.
LADOS = (('der', 'derecha'), ('izq', 'izquierda'))

# ── Relacion molar y canina ──────────────────────────────────────────────
#
# La escala es la de Angle en CUSPIDES, que es el vocabulario con el que un
# ortodoncista formado en la tradicion americana lee esto sin traducir nada.
# Se guarda en CUARTOS de cuspide como entero: 0 = Clase I, negativo hacia
# Clase III, positivo hacia Clase II, +-4 = clase completa, +-5 = mas alla.
#
# Respaldo de la escala:
#  - El ABO Discrepancy Index puntua la relacion molar POR LADO, con la clase
#    de Angle: 0 pts Clase I, 2 pts cuspide a cuspide, 4 pts clase completa, y
#    1 pt por cada mm que exceda la clase completa.
#  - El PAR (componente antero-posterior) y el ICON usan el mismo escalon de
#    media cuspide ("half a unit, cusp to cusp").
#
# ⚠️ El escalon de 1/4 y 3/4 es vocabulario clinico legitimo y de uso corriente,
# pero NINGUN indice validado lo define y no hay datos publicados de
# reproducibilidad entre examinadores para el. La media cuspide es el unico
# escalon con evidencia. Por eso el informe imprime la frase canonica y la
# regla, y NO presenta el cuarto de cuspide como si fuera una medicion.
#
# 'cuspide a cuspide' va pegado al 1/2 a proposito: es el mismo escalon que
# otros llaman "end-on" o "end-to-end" segun con quien se formaron.

RELACIONES = (
    ('III-mas',      'Clase III más de cúspide completa',        -5),
    ('III-completa', 'Clase III completa',                       -4),
    ('III-3/4',      'Clase III ¾ cúspide',                      -3),
    ('III-1/2',      'Clase III ½ cúspide (cúspide a cúspide)',  -2),
    ('III-1/4',      'Clase III ¼ cúspide',                      -1),
    ('I',            'Clase I',                                   0),
    ('II-1/4',       'Clase II ¼ cúspide',                        1),
    ('II-1/2',       'Clase II ½ cúspide (cúspide a cúspide)',    2),
    ('II-3/4',       'Clase II ¾ cúspide',                        3),
    ('II-completa',  'Clase II completa',                         4),
    ('II-mas',       'Clase II más de cúspide completa',          5),
    # No es lo mismo que Clase I: si la pieza no esta, no hay relacion que
    # registrar. Se imprime como tal y no se dibuja marca en la regla.
    ('no_registrable', 'No registrable (pieza ausente o no erupcionada)', None),
)

RELACIONES_MAP = {v: (etiqueta, pos) for v, etiqueta, pos in RELACIONES}

RELACIONES_OCLUSION = (('clase_molar', 'Relación molar'),
                       ('clase_canina', 'Relación canina'))

CUARTOS_MAX = 5


def _clase_de(cuartos):
    """'I' | 'II' | 'III' a partir de los cuartos."""
    if cuartos == 0:
        return 'I'
    return 'II' if cuartos > 0 else 'III'


def frase_relacion(titulo, der, izq):
    """La frase canonica de Angle para lo registrado en los dos lados.

    Cuando un lado es Clase I y el otro no, la nomenclatura de Angle tiene un
    nombre propio para eso: SUBDIVISION, y nombra el lado que NO es Clase I
    ("Clase II subdivisión derecha"). Sale sola del registro por lado, que es
    justamente la prueba de que guardar cada lado por separado es lo correcto y
    no un capricho: la asimetria es clasificatoria, no un detalle.
    """
    lados = {}
    for nombre, v in (('derecha', der), ('izquierda', izq)):
        if v in RELACIONES_MAP:
            lados[nombre] = v
    if not lados:
        return ''

    def et(v):
        # El sinonimo entre parentesis ayuda a elegir en el selector, pero en la
        # frase impresa estorba: "Clase II 1/2 cuspide subdivision izquierda" ya
        # es larga.
        return RELACIONES_MAP[v][0].replace(' (cúspide a cúspide)', '')

    no_reg = [n for n, v in lados.items() if RELACIONES_MAP[v][1] is None]
    medibles = {n: RELACIONES_MAP[v][1] for n, v in lados.items()
                if RELACIONES_MAP[v][1] is not None}

    partes = []
    if len(medibles) == 2:
        (n1, c1), (n2, c2) = sorted(medibles.items())
        if c1 == c2:
            # "Clase I bilateral" no lo dice nadie: si los dos lados son Clase I,
            # es Clase I y punto.
            partes.append(et(lados[n1]) if c1 == 0 else '%s bilateral' % et(lados[n1]))
        elif 0 in (c1, c2):
            lado_no_i = n1 if c2 == 0 else n2
            partes.append('%s subdivisión %s' % (et(lados[lado_no_i]), lado_no_i))
        else:
            partes.append(' · '.join('%s: %s' % (n.capitalize(), et(lados[n]))
                                     for n in sorted(medibles)))
    elif medibles:
        n = list(medibles)[0]
        partes.append('%s: %s' % (n.capitalize(), et(lados[n])))
    for n in sorted(no_reg):
        partes.append('%s: no registrable' % n.capitalize())
    return ' · '.join(partes)


def regla_oclusion_svg(titulo, der, izq, ancho=470, alto=84):
    """Dibuja la relacion como una REGLA en vez de escribirla como texto.

    Una linea horizontal con Clase I al centro, Clase III a la izquierda y
    Clase II a la derecha, marcada cada cuarto de cuspide, y dos marcas: Der e
    Izq. Se lee de un vistazo cuanto y hacia donde, que es justo lo que una
    frase como "Clase II subdivisión derecha" obliga a reconstruir.

    Devuelve None si no hay ningun lado con una relacion medible.
    """
    lados = [(nombre, RELACIONES_MAP[v][1])
             for nombre, v in (('Der', der), ('Izq', izq))
             if v in RELACIONES_MAP and RELACIONES_MAP[v][1] is not None]
    if not lados:
        return None

    ml, mr = 26, 26
    pw = ancho - ml - mr
    eje_y = 54

    def px(c):
        return ml + (c + CUARTOS_MAX) / (2.0 * CUARTOS_MAX) * pw

    out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="100%%" '
           'style="max-width:%dpx;font-family:Inter,Arial,sans-serif">' % (ancho, alto, ancho)]
    out.append('<text x="%d" y="14" font-size="9" fill="#4A5568" font-weight="600">%s</text>'
               % (ml, _esc_svg(titulo)))
    out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#9aa7b8" stroke-width="1.2"/>'
               % (ml, eje_y, ml + pw, eje_y))

    for c in range(-CUARTOS_MAX, CUARTOS_MAX + 1):
        x = px(c)
        alto_marca = 7 if c == 0 else (5 if abs(c) in (2, 4) else 3.5)
        color = '#1A2E4A' if c == 0 else '#9aa7b8'
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="1.2"/>'
                   % (x, eje_y - alto_marca, x, eje_y + alto_marca, color))

    # Solo se rotulan los escalones con respaldo: Clase I, cuspide a cuspide y
    # clase completa. Los cuartos se ven como marca y no se nombran.
    for c, texto, peso in ((-4, 'III', '700'), (0, 'I', '700'), (4, 'II', '700')):
        out.append('<text x="%.1f" y="%d" font-size="9.5" font-weight="%s" fill="#1A2E4A" '
                   'text-anchor="middle">%s</text>' % (px(c), eje_y + 20, peso, texto))
    for c in (-2, 2):
        out.append('<text x="%.1f" y="%d" font-size="7.5" fill="#7b8794" '
                   'text-anchor="middle">cúsp. a cúsp.</text>' % (px(c), eje_y + 20))

    for i, (nombre, c) in enumerate(lados):
        x = px(c)
        # Si ambos lados caen en el mismo punto, el segundo sube para no taparse.
        y = eje_y - 17 - (17 if i and lados[0][1] == c else 0)
        out.append('<rect x="%.1f" y="%.1f" width="26" height="14" rx="7" fill="#1A2E4A"/>'
                   % (x - 13, y - 7))
        out.append('<text x="%.1f" y="%.1f" font-size="8.5" font-weight="700" fill="#fff" '
                   'text-anchor="middle">%s</text>' % (x, y + 3.2, nombre))
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%d" stroke="#1A2E4A" '
                   'stroke-width="0.8" opacity="0.5"/>' % (x, y + 8, x, eje_y - 7))

    out.append('</svg>')
    return ''.join(out)


def _esc_svg(t):
    return str(t).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def filas_oclusion(med):
    """La relacion molar y canina lista para imprimir: la frase canonica y la
    regla. La frase es corta y es la que el ortodoncista reconoce al instante;
    la regla es la que muestra la magnitud sin tener que leer."""
    out = []
    for base, titulo in RELACIONES_OCLUSION:
        der = med.get('%s_der' % base) or ''
        izq = med.get('%s_izq' % base) or ''
        texto = frase_relacion(titulo, der, izq)
        if not texto:
            continue
        out.append({'clave': base, 'titulo': titulo, 'texto': texto,
                    'svg': regla_oclusion_svg(titulo, der, izq)})
    return out


def fecha_legible(f):
    return '%d de %s de %d' % (f.day, _MESES[f.month - 1], f.year)


def _edad_de(item):
    """Edad del paciente: la que se escribio a mano manda; si no, se calcula de
    la fecha de nacimiento de la base local. Sin ninguna de las dos no hay
    percentil posible, y eso se informa en vez de asumir una edad."""
    if item.get('edad') not in (None, ''):
        try:
            return float(item['edad'])
        except (TypeError, ValueError):
            pass
    fnac = item.get('fecha_nacimiento')
    if fnac:
        import pacientes as _pac
        e = _pac.edad_a_fecha(fnac)
        if e >= 0:
            return float(e)
    return None


def _filas_simples(med):
    """Las mediciones que van en la TABLA del informe.

    Los anchos transversales NO entran aca: van solo como grafico (decision del
    usuario 2026-08-20). Repetir el numero arriba de la curva no agrega nada --
    el punto sobre la banda ya dice mas que el milimetraje suelto, y la tabla
    quedaba compitiendo con el grafico por la atencion.
    """
    filas = []
    for c, e, u in MEDICIONES_SIMPLES:
        if med.get(c) not in (None, ''):
            filas.append({'clave': c, 'etiqueta': e, 'valor': med.get(c), 'unidad': u})

    # La linea media sin el lado es un dato a medias: 2 mm a la derecha y 2 mm a
    # la izquierda son casos distintos.
    if med.get('linea_media') not in (None, ''):
        lado = dict(LADOS).get(med.get('linea_media_lado') or '', '')
        filas.append({'clave': 'linea_media', 'etiqueta': 'Desviación de línea media',
                      'valor': med['linea_media'],
                      'unidad': ('mm hacia la ' + lado) if lado else 'mm'})

    if med.get('mordida_cruzada'):
        filas.append({'clave': 'mordida_cruzada', 'etiqueta': 'Mordida cruzada posterior',
                      'valor': med['mordida_cruzada'], 'unidad': ''})
    return filas


def mediciones_previas(rut, clave, tramo=None, excluir_id=None):   # tramo: sin efecto, ver docstring
    """Las mediciones ANTERIORES de un ancho en este paciente, como
    [(edad, mm), ...] ordenadas por edad. Es lo que convierte el grafico en un
    seguimiento a partir del segundo informe.

    NO se filtra por el diente medido. La curva de Bishara atraviesa el recambio
    (mide el diente que el paciente tiene a cada edad), asi que la trayectoria
    del paciente tiene que atravesarlo igual: sus mediciones de molar temporal a
    los 5 y de permanente a los 8 son la misma linea de crecimiento, y cortarla
    escondia justo la parte que interesa mirar.

    Si descarta los informes sin edad registrada: un punto sin eje X no se puede
    dibujar, y suponerle una edad seria inventar.
    """
    clave_rut = (rut or '').replace('.', '').replace('-', '').strip().lower()
    if not clave_rut:
        return []
    puntos = []
    for item in _STORE.load().get('informes', {}).values():
        if item.get('id') == excluir_id:
            continue
        otro = (item.get('rut') or '').replace('.', '').replace('-', '').strip().lower()
        if otro != clave_rut:
            continue
        med = item.get('mediciones') or {}
        mm = med.get(clave)
        if mm in (None, ''):
            continue
        edad = _edad_de(item)
        if edad is None:
            continue
        try:
            puntos.append((float(edad), float(mm)))
        except (TypeError, ValueError):
            continue
    return sorted(puntos)


def puntuar_tamizaje(item):
    """Puntúa el tamizaje de un informe SIN guardarlo, para que el formulario
    muestre el resultado mientras se llena.

    Existe para que el puntaje viva en UN solo lugar. La alternativa era
    repetir los umbrales del FAIREST en el JavaScript, y ahí es donde las dos
    copias se separan sin que nadie lo note: el formulario mostraría un puntaje
    y el papel firmado otro.
    """
    doc = armar_documento(item)
    return doc['tamizaje']


def armar_documento(item, doctor=None, clinica=None):
    """Convierte un informe guardado en el documento listo para imprimir.

    doctor: {'nombre', 'registro', 'especialidad', 'firma_url'} lo resuelve el
    endpoint desde scheduling_config y seguros.firma_de_doctor.
    """
    import transversal
    import fairest
    import stopbang
    from datetime import date as _date

    item = dict(item or {})
    edad = _edad_de(item)
    sexo = (item.get('sexo') or '').upper()[:1] or None
    adulto = edad is not None and edad >= EDAD_ADULTO

    try:
        f = _date.fromisoformat(item.get('fecha') or fechas.hoy_chile().isoformat())
    except ValueError:
        f = fechas.hoy_chile()

    # ── Mediciones ──
    med = item.get('mediciones') or {}
    tramo = med.get('tramo_intermolar') or None
    transversales = []
    for clave, medida, arcada, etiqueta in MEDICIONES_TRANSVERSALES:
        mm = med.get(clave)
        if mm in (None, ''):
            continue
        fila = {'clave': clave, 'etiqueta': etiqueta, 'mm': mm, 'medida': medida,
                'arcada': arcada}
        tramo_de_esta = tramo if medida == 'intermolar' else None
        r = transversal.percentil(medida, arcada, sexo, edad, mm, tramo_de_esta)
        if r.get('ok'):
            previas = mediciones_previas(item.get('rut'), clave, tramo_de_esta,
                                         excluir_id=item.get('id'))
            fila.update({'percentil': r['percentil'], 'media': r['media'], 'de': r['de'],
                         'lectura': transversal.etiqueta_percentil(r['percentil']),
                         'interpolado': r['interpolado'], 'sospechoso': r.get('sospechoso'),
                         'mediciones_previas': len(previas),
                         'diente': r.get('diente', ''), 'en_recambio': r.get('en_recambio', False),
                         'svg': transversal.curva_svg(medida, arcada, sexo, edad, mm,
                                                      tramo_de_esta, historico=previas)})
        else:
            # Sin referencia NO es lo mismo que dentro del promedio. Se dice.
            fila.update({'percentil': None, 'lectura': 'sin referencia para esta edad',
                         'motivo_sin_referencia': r.get('detalle') or r.get('motivo')})
        transversales.append(fila)

    simples = _filas_simples(med)
    oclusion = filas_oclusion(med)

    # ── Hallazgos ──
    claves = list(item.get('hallazgos') or [])
    if item.get('sin_hallazgos') or not claves:
        hallazgos = []
        sin_hallazgos = True
    else:
        hallazgos = [dict(HALLAZGOS[c], clave=c) for c in claves if c in HALLAZGOS]
        sin_hallazgos = False

    # ── Impresion diagnostica inicial ──
    ck = item.get('conclusion') or ''
    conc = CONCLUSIONES_MAP.get(ck)
    conclusion = None
    if conc:
        texto = conc['texto']
        if '{meses}' in texto:
            texto = texto.replace('{meses}', str(item.get('meses_control') or 6))
        conclusion = {'clave': ck, 'etiqueta': conc['etiqueta'], 'texto': texto}

    # ── Tamizaje ──
    tam = item.get('tamizaje') or {}
    datos_transversal = {
        'intermolar_mm': med.get('intermolar_maxilar'),
        'intercanino_mm': med.get('intercanino_maxilar'),
        'sexo': sexo, 'edad': edad, 'tramo_intermolar': tramo,
    }
    res_fairest = fairest.evaluar(tam.get('fairest') or {}, adulto=adulto,
                                  transversal_datos=datos_transversal)

    cuestionario_alto = False
    if adulto:
        sb_datos = dict(tam.get('stopbang') or {})
        # El formulario del box pide peso y talla (que el paciente sabe) en vez
        # del IMC (que no). Se calcula aca y no en el navegador para que la
        # formula viva en un solo lugar, con sus guardas contra datos absurdos.
        if sb_datos.get('imc') in (None, '') and sb_datos.get('peso') and sb_datos.get('talla'):
            sb_datos['imc'] = stopbang.imc(sb_datos['peso'], sb_datos['talla'])
        res_sb = stopbang.evaluar(sb_datos)
        deriva_sb, motivo_sb = stopbang.sugiere_derivacion(res_sb)
        cuestionario_alto = deriva_sb
        cuestionario = {'tipo': 'STOP-BANG', 'resultado': res_sb, 'lectura': motivo_sb}
    else:
        # El PSQ-CL lo responde el apoderado en /psq; aca solo se muestra el
        # resultado que ya existe. Si nunca lo contesto, se dice: no se inventa
        # un "sin riesgo" a partir de un cuestionario en blanco.
        psq_prev = tam.get('psq')
        if psq_prev:
            cuestionario_alto = bool(psq_prev.get('riesgo_alto'))
            cuestionario = {'tipo': 'PSQ-CL', 'resultado': psq_prev,
                            'lectura': ('Puntaje %s (corte 0,227): %s'
                                        % (psq_prev.get('puntaje'),
                                           'sobre el corte' if cuestionario_alto
                                           else 'bajo el corte'))}
        else:
            cuestionario = {'tipo': 'PSQ-CL', 'resultado': None,
                            'lectura': 'El cuestionario de sueño no ha sido respondido.'}

    derivar, motivo_derivar = fairest.sugiere_derivacion(res_fairest, cuestionario_alto)

    tamizaje = {
        'adulto': adulto,
        'cuestionario': cuestionario,
        'fairest': res_fairest,
        'derivar': derivar,
        'motivo_derivar': motivo_derivar,
        'especialidades': (['Otorrinolaringologia', 'Medicina del sueño'] if adulto
                           else ['Otorrinolaringologia', 'Broncopulmonar pediatrico']),
        'texto_legal': fairest.TEXTO_LEGAL,
    }

    # ── Ordenes ──
    ordenes = [dict(ORDENES[c], clave=c) for c in (item.get('ordenes') or []) if c in ORDENES]
    plan = [p for p in (item.get('plan_accion') or []) if (p or {}).get('accion')]

    return {
        'id': item.get('id'),
        'fecha': item.get('fecha'),
        'fecha_legible': fecha_legible(f),
        'paciente': {'nombre': item.get('nombre') or '',
                     'rut': item.get('rut_fmt') or item.get('rut') or '',
                     'edad': edad, 'sexo': sexo},
        'doctor': doctor or {},
        'clinica': clinica or {},
        'motivo_consulta': item.get('motivo_consulta') or '',
        'evaluacion_realizada': list(EVALUACION_REALIZADA),
        'mediciones': {'transversales': transversales, 'simples': simples,
                       'oclusion': oclusion,
                       'nota': NOTA_MEDICIONES,
                       # La cita y la nota de la muestra viajan por si alguna
                       # version del documento las quiere, pero por decision del
                       # usuario (2026-08-20) NO se imprimen al pie.
                       'cita': transversal.CITA,
                       'nota_muestra': transversal.NOTA_MUESTRA,
                       # Solo se imprime si alguna medicion cae en el recambio.
                       'nota_recambio': (transversal.NOTA_RECAMBIO
                                         if any(t.get('en_recambio') for t in transversales)
                                         else '')},
        'hallazgos': hallazgos,
        'sin_hallazgos': sin_hallazgos,
        'sin_hallazgos_texto': SIN_HALLAZGOS[2],
        'conclusion': conclusion,
        'plan_accion': plan,
        # Al que no requiere tratamiento no se le ofrece el Estudio: seria
        # exactamente la venta que este documento existe para no parecer.
        'que_aporta_estudio': (QUE_APORTA_ESTUDIO if ck != 'no_requiere' else ''),
        'imagenes': imagenes_de(item.get('id')) if item.get('imagenes') else [],
        'tamizaje': tamizaje,
        'ordenes': ordenes,
        'texto_orden': TEXTO_ORDEN,
        'disclaimer': DISCLAIMER,
        'impreso': item.get('impreso'),
    }
