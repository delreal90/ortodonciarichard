# -*- coding: utf-8 -*-
"""
Genera las páginas estáticas que leen Google y los buscadores de IA
(ChatGPT, Gemini, Perplexity, Claude): una por doctor, una por cada pregunta
típica del paciente y la de tecnología. También regenera sitemap.xml y llms.txt.

    python tools/generar_paginas.py

Por qué existe (2026-10-07): el sitio era una sola página, la formación de los
doctores solo aparecía al hacer clic (la inyectaba JavaScript, que un crawler
no ejecuta) y las publicaciones no estaban enlazadas desde ninguna parte. Una
IA recomienda a quien puede CITAR: necesita texto plano, una URL por tema y
datos verificables (registro de la Superintendencia, DOI de cada artículo).

Reglas del contenido (es publicidad sanitaria y va con nombre de doctor):
- Nada de "el mejor", promesas de resultado ni cifras que no se puedan mostrar.
- Toda publicación lleva su DOI o PMID (verificadas en PubMed/Crossref).
- Los N° de registro salen de js/main.js (`doctorData[key].registro`), que es
  la fuente de verdad; test_informe_pc.py lo cruza con index.html.
- Editar el TEXTO acá y volver a correr el script: nunca los .html generados.
"""
import html
import io
import json
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITIO = 'https://www.ortodonciarichard.cl/'
HOY = '2026-10-07'
GOOGLE_PERFIL = 'https://g.page/r/CfYPKRCc7nsxEBM'
TEL, TEL_TXT = '+56222173499', '+56 2 2217 3499'
WA = 'https://wa.me/56933558189'


def registros_main_js():
    """N° de registro por doctor, leídos de js/main.js (fuente de verdad)."""
    txt = io.open(os.path.join(RAIZ, 'js', 'main.js'), encoding='utf-8').read()
    return dict(re.findall(r"(\w+): \{[^{}]*?registro:\s*'(\d+)'", txt, re.S))


# ═══════════════════════════════════════════════════════════════════════════
# PUBLICACIONES (verificadas 2026-10-07 en PubMed y Crossref)
# ═══════════════════════════════════════════════════════════════════════════
PUB = {
    'alineadores_2026': dict(
        autores='Oyonarte R, Lagos IM, Vidaurre LF, Parada BT, Del Real A, et al.',
        titulo='Mechanical properties of thermoformed and direct-printed aligner materials after immersion in 37 °C water: a 14-day in vitro study',
        revista='Scientific Reports', anio=2026, doi='10.1038/s41598-026-36723-8',
        resumen='Compara materiales de alineadores impresos directamente en 3D con los termoformados tradicionales.'),
    'ia_extracciones_2022': dict(
        autores='Del Real A, Del Real O, Sardina S, Oyonarte R',
        titulo='Use of automated artificial intelligence to predict the need for orthodontic extractions',
        revista='Korean Journal of Orthodontics', anio=2022, doi='10.4041/kjod.2022.52.2.102',
        resumen='Evalúa si un sistema de inteligencia artificial puede predecir cuándo un tratamiento requiere extracciones.'),
    'ia_molares_2026': dict(
        autores='Biskupovic F, Rosenberg F, Searle LM, et al. (con Oyonarte R)',
        titulo='Deep learning-based system for automated staging of lower molar maturation',
        revista='Journal of the World Federation of Orthodontists', anio=2026, doi='10.1016/j.ejwf.2025.08.004',
        resumen='Inteligencia artificial para estimar la maduración dentaria, que orienta el momento de tratar en pacientes en crecimiento.'),
    'clase_ii_2026': dict(
        autores='Oyonarte R, Castro MV, Sáez M, et al.',
        titulo='Influence of maxillomandibular ratio dynamics on post-pubertal sagittal classification: a retrospective historical cohort study',
        revista='International Orthodontics', anio=2026, doi='10.1016/j.ortho.2025.101109', resumen=''),
    'sutura_2024': dict(
        autores='Villarroel T, Yagnam S, Vicuña D, et al. (con Oyonarte R)',
        titulo='Midpalatal suture maturation in 15- to 35-year-olds: morphological assessment in the coronal plane using CBCT',
        revista='Odontology', anio=2024, doi='10.1007/s10266-023-00861-6',
        resumen='Maduración de la sutura del paladar evaluada con tomografía, clave para decidir una expansión en adolescentes y adultos.'),
    'endo_2024': dict(
        autores='Martínez I, Oyonarte R, Concha G, et al.',
        titulo='Orthodontic movement after regenerative endodontic procedure in mature permanent tooth associated with dens invaginatus: a case report with 4-year follow-up',
        revista='BMC Oral Health', anio=2024, doi='10.1186/s12903-024-04697-6', resumen=''),
    'brackets_2023': dict(
        autores='Ibarra RN, Sáez M, Rojas V, et al. (con Oyonarte R)',
        titulo='Comparison of the shear bond strength of new and recycled metallic brackets using different adhesive materials: an in vitro study',
        revista='European Oral Research', anio=2023, doi='10.26650/eor.20231163180', resumen=''),
    'premolares_2022': dict(
        autores='Janson G, Rizzo M, Valerio MV, et al. (con Oyonarte R)',
        titulo='Stability of first and second premolars extraction space closure',
        revista='American Journal of Orthodontics and Dentofacial Orthopedics', anio=2022,
        doi='10.1016/j.ajodo.2021.04.027', resumen=''),
    'hueso_2021': dict(
        autores='Janson D, Caldas W, Garib D, et al. (con Oyonarte R)',
        titulo='Cephalometric radiographic comparison of alveolar bone height changes between adolescent and adult patients treated with premolar extractions',
        revista='International Orthodontics', anio=2021, doi='10.1016/j.ortho.2021.08.004', resumen=''),
    'microtornillos_2021': dict(
        autores='Nenen F, Garnica N, Rojas V, et al. (con Oyonarte R)',
        titulo='Comparison of the primary stability of orthodontic miniscrews after repeated insertion cycles',
        revista='The Angle Orthodontist', anio=2021, doi='10.2319/050120-375.1', resumen=''),
    'molares_2020': dict(
        autores='Oyonarte R, Sánchez-Ugarte F, Montt J, et al.',
        titulo='Diagnostic assessment of tooth maturation of the mandibular second molars as a skeletal maturation indicator: a retrospective longitudinal study',
        revista='American Journal of Orthodontics and Dentofacial Orthopedics', anio=2020,
        doi='10.1016/j.ajodo.2019.09.012', resumen=''),
    'condilo_2013': dict(
        autores='Oyonarte R, Becerra D, Díaz-Zúñiga J, et al.',
        titulo='Morphological effects of mesenchymal stem cells and pulsed ultrasound on condylar growth in rats: a pilot study',
        revista='Australian Orthodontic Journal', anio=2013, pmid='23785932', resumen=''),
    'condilo_2009': dict(
        autores='Oyonarte R, Zárate M, Rodriguez F',
        titulo='Low-intensity pulsed ultrasound stimulation of condylar growth in rats',
        revista='The Angle Orthodontist', anio=2009, doi='10.2319/080708-414.1', resumen=''),
    'implantes_2006': dict(
        autores='Pilliar RM, Sagals G, Meguid SA, Oyonarte R, Deporter DA',
        titulo='Threaded versus porous-surfaced implants as anchorage units for orthodontic treatment: three-dimensional finite element analysis of peri-implant bone tissue stresses',
        revista='International Journal of Oral & Maxillofacial Implants', anio=2006, pmid='17190297', resumen=''),
    'implantes_2005b': dict(
        autores='Oyonarte R, Pilliar RM, Deporter D, Woodside DG',
        titulo='Peri-implant bone response to orthodontic loading: Part 2. Implant surface geometry and its effect on regional bone remodeling',
        revista='American Journal of Orthodontics and Dentofacial Orthopedics', anio=2005,
        doi='10.1016/j.ajodo.2004.02.024', resumen=''),
    'implantes_2005a': dict(
        autores='Oyonarte R, Pilliar RM, Deporter D, Woodside DG',
        titulo='Peri-implant bone response to orthodontic loading: Part 1. A histomorphometric study of the effects of implant surface design',
        revista='American Journal of Orthodontics and Dentofacial Orthopedics', anio=2005,
        doi='10.1016/j.ajodo.2004.02.023', resumen=''),
    # -- Complemento desde ResearchGate (2026-10-07), DOI verificado en Crossref --
    'modelos3d_2024': dict(
        autores='Salles P, Del Real A, Aguilera V, Oyonarte R',
        titulo='Análisis de la variación dimensional de modelos 3D obtenidos con dos impresoras de resina',
        revista='International Journal of Interdisciplinary Dentistry', anio=2024, doi='10.4067/s2452-55882024000300134',
        resumen='Compara la precisión de modelos dentales impresos en una impresora 3D de alto costo y otra de bajo costo.'),
    'cirugia_primero_2015': dict(
        autores='Del Real A, Oyonarte R, Solé P',
        titulo='Cirugía-Primero: pacientes y procedimientos. Una revisión narrativa',
        revista='Revisión narrativa', anio=2015, url='https://www.researchgate.net/profile/Alberto-Del-Real/research',
        resumen='Revisa qué pacientes son candidatos al protocolo Cirugía Primero y qué procedimientos incluye.'),
    'docencia_2017': dict(
        autores='Tricio J, Montt J, Ormeño A, Del Real A, Naranjo C',
        titulo="Impact of faculty development workshops in student-centered teaching methodologies on faculty members' teaching and their students' perceptions",
        revista='Journal of Dental Education', anio=2017, doi='10.21815/jde.017.014', resumen=''),
    'maduracion_hispanos_2017': dict(
        autores='Cisternas A, Morales R, Ramírez V, Del Real A, Oyonarte R',
        titulo='Diagnostic assessment of skeletal maturity through dental maturation in Hispanic growing individuals',
        revista='APOS Trends in Orthodontics', anio=2017, doi='10.4103/2321-1407.199181',
        resumen='Evalúa la maduración dentaria como indicador del momento de crecimiento en niños y adolescentes.'),
    'fisurados_2018': dict(
        autores='Del Real A, Ibarra N, Ledezma D, et al.',
        titulo='Comparación de los resultados clínicos de la distracción osteogénica versus cirugía ortognática en pacientes con labio fisurado. Revisión narrativa',
        revista='Revisión narrativa', anio=2018, url='https://www.researchgate.net/profile/Alberto-Del-Real/research', resumen=''),
    'molares_ijoid_2026': dict(
        autores='Biskupovic F, Larrañaga MJ, Rosenberg F, Searle LM, Maldonado S, Vairetti C, Oyonarte R',
        titulo='Sistema automatizado para determinar el estadio de maduración dental en segundos molares inferiores',
        revista='International Journal of Interdisciplinary Dentistry', anio=2026, doi='10.4067/s2452-55882026000100012', resumen=''),
    'buccal_shelf_2024': dict(
        autores='Wang L, Oyonarte R, Carmona R, Bidart C, Battaglia G',
        titulo='Characteristics of the buccal shelf for the installation of miniscrews in Chilean individuals aged 15-45 years: a descriptive study',
        revista='Journal of Oral Research', anio=2024, doi='10.17126/joralres.2024.020', resumen=''),
    'cefalo_chile_2023': dict(
        autores='Wang L, Quiroz C, Velasco A, Morales R, Vicuña D, Concha G, Oyonarte R',
        titulo='Cephalometric characteristics in Chilean Latino population with normal occlusion and harmonic profiles in permanent dentition',
        revista='International Journal of Morphology', anio=2023, doi='10.4067/s0717-95022023000401020',
        resumen='Normas cefalométricas propias de la población chilena, en vez de las obtenidas en población caucásica.'),
    'apnea_2021': dict(
        autores='Alvarado MJ, Oyonarte R',
        titulo='Apnea obstructiva del sueño y el rol del ortodoncista. Revisión bibliográfica',
        revista='International Journal of Interdisciplinary Dentistry', anio=2021, doi='10.4067/s2452-55882021000300242',
        resumen='Revisa el papel del odontólogo y del ortodoncista en la detección y el manejo de la apnea del sueño.'),
    'sutura_chile_2021': dict(
        autores='Villarroel T, Alvarado MJ, Concha G, Vicuña D, Oyonarte R',
        titulo='Maduración de la sutura palatina media en adolescentes y adultos jóvenes chilenos: estudio transversal',
        revista='International Journal of Interdisciplinary Dentistry', anio=2021, doi='10.4067/s2452-55882021000200140', resumen=''),
    'blanqueo_2021': dict(
        autores='Rojas V, Gómez MI, Sampaio C, Sáez M, Oyonarte R',
        titulo='Análisis comparativo in vitro de la resistencia adhesiva al cizallamiento de brackets metálicos adheridos a superficies dentarias tratadas con diferentes agentes blanqueadores',
        revista='International Journal of Interdisciplinary Dentistry', anio=2021, doi='10.4067/s2452-55882021000100017', resumen=''),
    'usbi_2020': dict(
        autores='Rojas V, Nineham A, Gajardo C, Rodríguez F, Oyonarte R',
        titulo='Efecto del ultrasonido de baja intensidad (USBI) sobre el movimiento dentario ortodóncico. Estudio in vivo en ratas Sprague-Dawley',
        revista='International Journal of Morphology', anio=2020, doi='10.4067/s0717-95022020000100101', resumen=''),
    'anb_2016': dict(
        autores='Oyonarte R, Hurtado M, Castro MV',
        titulo='Evolution of ANB and SN-GoGn angles during craniofacial growth: a retrospective longitudinal study',
        revista='APOS Trends in Orthodontics', anio=2016, doi='10.4103/2321-1407.194796', resumen=''),
    'cefalo_jovenes_2015': dict(
        autores='Montt J, Miquel MP, Oyonarte R',
        titulo='Características cefalométricas en jóvenes con oclusión normal y perfil armónico en población chilena',
        revista='International Journal of Morphology', anio=2015, doi='10.4067/s0717-95022015000100037', resumen=''),
    'sagital_2013': dict(
        autores='Castro MV, Hurtado M, Oyonarte R',
        titulo='Rendimiento de la evaluación cefalométrica para el diagnóstico sagital intermaxilar: revisión narrativa',
        revista='Revista Clínica de Periodoncia, Implantología y Rehabilitación Oral', anio=2013, doi='10.4067/s0719-01072013000200010', resumen=''),
    'celulas_2012': dict(
        autores='Becerra D, Díaz J, Carrión F, Inostroza S, Oyonarte R',
        titulo='Evaluación de la proliferación de células madres mesenquimales estimuladas con diferentes intensidades de ultrasonido de baja intensidad',
        revista='International Journal of Morphology', anio=2012, doi='10.4067/s0717-95022012000200043', resumen=''),
    'bruxismo_2010': dict(
        autores='Vicuña D, Id ME, Oyonarte R',
        titulo='Asociaciones entre signos clínicos de bruxismo, ansiedad y actividad electromiográfica maseterina utilizando el aparato Bite Strip en adolescentes',
        revista='International Journal of Odontostomatology', anio=2010, doi='10.4067/s0718-381x2010000300007', resumen=''),
}


def pub_url(p):
    if p.get('doi'):
        return 'https://doi.org/' + p['doi']
    if p.get('pmid'):
        return 'https://pubmed.ncbi.nlm.nih.gov/%s/' % p['pmid']
    return p['url']


# ═══════════════════════════════════════════════════════════════════════════
# DOCTORES
# ═══════════════════════════════════════════════════════════════════════════
MEMB_ORTO = ['American Association of Orthodontists (AAO)',
             'World Federation of Orthodontists (WFO)',
             'Sociedad de Ortodoncia de Chile (SORTCH)',
             'Colegio de Cirujano Dentistas de Chile']

DOCTORES = {
    'octavio': dict(
        slug='dr-octavio-del-real', nombre='Dr. Octavio Del Real S.',
        nombre_completo='Octavio Del Real', rol='Ortodoncista', especialidad='Orthodontic',
        foto='images/dr-octavio-del-real.jpeg',
        resumen='Ortodoncista con más de 40 años de especialidad y práctica exclusiva en ortodoncia. Ex Presidente de la Sociedad de Ortodoncia de Chile (2000–2004) y Presidente de la Comisión de Ortodoncia de CONACEO.',
        bio=['Especialista en Ortodoncia y Ortopedia Dentomaxilar de la Universidad de Chile desde 1985, con práctica privada exclusiva en ortodoncia en Ortodoncia Richard.',
             'Fue Presidente de la Sociedad de Ortodoncia de Chile entre 2000 y 2004 y hoy preside la Comisión de Ortodoncia de CONACEO, el organismo que certifica a los especialistas odontológicos en Chile. Es profesor del Programa de Especialización en Ortodoncia de la Universidad de los Andes y conferencista nacional e internacional.',
             'Fue editor de la Revista Chilena de Ortodoncia y coautor de investigación sobre el uso de inteligencia artificial en la planificación de tratamientos.'],
        formacion=['Cirujano Dentista — Universidad de Chile (1978)',
                   'Especialista en Ortodoncia y Ortopedia Dentomaxilar — Universidad de Chile (1985)'],
        cargos=['Presidente de la Comisión de Ortodoncia de CONACEO',
                'Ex Presidente de la Sociedad de Ortodoncia de Chile (2000–2004)',
                'Profesor del Programa de Especialización en Ortodoncia, Universidad de los Andes'],
        membresias=MEMB_ORTO, pubs=['ia_extracciones_2022'], alumni=['Universidad de Chile'],
        sameAs=[], pubmed=None),
    'rodrigo': dict(
        slug='dr-rodrigo-oyonarte', nombre='Dr. Rodrigo Oyonarte W.',
        nombre_completo='Rodrigo Oyonarte Weldt', rol='Ortodoncista', especialidad='Orthodontic',
        foto='images/dr-rodrigo-oyonarte.jpeg',
        resumen='Ortodoncista, Máster en Ciencias por la Universidad de Toronto. Profesor Titular y Director del Programa de Especialización en Ortodoncia de la Universidad de los Andes, autor de más de 30 publicaciones científicas.',
        bio=['Se especializó en Ortodoncia y Ortopedia Dentomaxilar y obtuvo su Máster en Ciencias en la Universidad de Toronto, Canadá (2002).',
             'Es Profesor Titular de la Universidad de los Andes y Director de su Programa de Especialización en Ortodoncia, donde se forman los nuevos ortodoncistas. Fue editor de la Revista Chilena de Ortodoncia entre 2009 y 2018 y ha recibido premios de investigación en Chile y Estados Unidos.',
             'Su investigación abarca la maduración esquelética y el momento óptimo de tratamiento en pacientes en crecimiento, el anclaje con implantes y microtornillos, materiales de alineadores impresos en 3D y el uso de inteligencia artificial en ortodoncia. Publica en las revistas de referencia de la especialidad, como American Journal of Orthodontics and Dentofacial Orthopedics y The Angle Orthodontist.'],
        formacion=['Cirujano Dentista — Universidad de Chile (1996)',
                   'Máster en Ciencias y Especialista en Ortodoncia y Ortopedia Dentomaxilar — Universidad de Toronto, Canadá (2002)',
                   'Diplomado en Medicina Basada en la Evidencia — Facultad de Medicina, Universidad de los Andes (2011)'],
        cargos=['Profesor Titular, Universidad de los Andes',
                'Director del Programa de Especialización en Ortodoncia, Universidad de los Andes',
                'Editor de la Revista Chilena de Ortodoncia (2009–2018)'],
        membresias=MEMB_ORTO,
        pubs=['alineadores_2026', 'clase_ii_2026', 'ia_molares_2026', 'molares_ijoid_2026',
              'modelos3d_2024', 'buccal_shelf_2024', 'sutura_2024', 'endo_2024', 'cefalo_chile_2023',
              'brackets_2023', 'premolares_2022', 'ia_extracciones_2022', 'apnea_2021',
              'sutura_chile_2021', 'blanqueo_2021', 'hueso_2021', 'microtornillos_2021',
              'molares_2020', 'usbi_2020', 'maduracion_hispanos_2017', 'anb_2016',
              'cirugia_primero_2015', 'cefalo_jovenes_2015', 'condilo_2013', 'sagital_2013',
              'celulas_2012', 'bruxismo_2010', 'condilo_2009', 'implantes_2006',
              'implantes_2005b', 'implantes_2005a'],
        alumni=['Universidad de Chile', 'University of Toronto', 'Universidad de los Andes'],
        sameAs=['https://www.uandes.cl/personas/rodrigo-oyonarte-weldt/',
                'https://aaoinfo.org/locator/dr-rodrigo-oyonarte',
                'https://www.researchgate.net/profile/Rodrigo-Oyonarte-2/research'],
        pubmed='https://www.researchgate.net/profile/Rodrigo-Oyonarte-2/research'),
    'alberto': dict(
        slug='dr-alberto-del-real', nombre='Dr. Alberto Del Real V.',
        nombre_completo='Alberto Del Real', rol='Ortodoncista', especialidad='Orthodontic',
        foto='images/dr-alberto-del-real.jpeg',
        resumen='Ortodoncista especialista de la Universidad de los Andes, con formación en medicina basada en la evidencia y educación en ciencias de la salud. Autor de publicaciones y conferencias sobre inteligencia artificial, impresión 3D, alineadores y cirugía ortognática; premio DENTAID 2019 a la mejor investigación en innovación para la salud oral.',
        bio=['Especialista en Ortodoncia y Ortopedia Dentomaxilofacial de la Universidad de los Andes (2019), con diplomados en Medicina Basada en la Evidencia (Universidad de los Andes) y en Educación en Ciencias de la Salud (Universidad de Chile).',
             'Lidera la ortodoncia digital de la clínica: escáner intraoral, planificación digital y alineadores. Desarrolló el informe de evaluación escrito que reciben los pacientes en su primera consulta, con mediciones comparadas con tablas de crecimiento por edad y sexo, y el tamizaje de respiración y sueño que se aplica en cada evaluación.',
             'Es autor principal de un estudio publicado en el Korean Journal of Orthodontics sobre inteligencia artificial para predecir la necesidad de extracciones, y coautor de investigaciones sobre materiales de alineadores impresos directamente en 3D (Scientific Reports) y sobre la precisión de modelos dentales impresos en 3D. Su trabajo sobre inteligencia artificial en la toma de decisiones clínicas recibió el premio DENTAID a la mejor investigación en innovación para la salud oral (2019).',
             'Dicta conferencias para especialistas sobre biomecánica de alineadores y nuevas tecnologías en ortodoncia.'],
        formacion=['Cirujano Dentista — Universidad de los Andes (2014)',
                   'Especialista en Ortodoncia y Ortopedia Dentomaxilofacial — Universidad de los Andes (2019)',
                   'Diplomado en Medicina Basada en la Evidencia — Facultad de Medicina, Universidad de los Andes (2015)',
                   'Diplomado en Educación en Ciencias de la Salud — Facultad de Medicina, Universidad de Chile (2016)'],
        cargos=[], membresias=MEMB_ORTO,
        pubs=['alineadores_2026', 'modelos3d_2024', 'ia_extracciones_2022', 'fisurados_2018',
              'docencia_2017', 'maduracion_hispanos_2017', 'cirugia_primero_2015'],
        conferencias=[
            ('2025', 'Unlocking New Horizons: The Hidden Potential of 3D Printing in Orthodontics (resumen publicado en Journal of the World Federation of Orthodontists)', 'https://doi.org/10.1016/j.ejwf.2025.07.727'),
            ('2024', '¿Qué hay de nuevo, viejo? Nuevas tecnologías en ortodoncia que el odontopediatra debería conocer', None),
            ('2024', 'Casos clínicos para socio activo: clase III con hiperplasia condilar, y extracciones tratadas con alineadores', None),
            ('2022', 'Nueva evidencia en biomecánica con attachments: cuándo, cuál y por qué', None),
            ('2019', 'Artificial Intelligence Assisted Decision Making in Dentistry: What’s Coming Next. Premio DENTAID a la mejor investigación en innovación para la salud oral', None),
            ('2018', 'Magnitud de las cargas en el hueso periimplantario en minitornillos cargados ortodóncicamente: estudio de elementos finitos (póster)', None),
        ],
        alumni=['Universidad de los Andes', 'Universidad de Chile'],
        sameAs=['https://www.researchgate.net/profile/Alberto-Del-Real/research'], pubmed='https://www.researchgate.net/profile/Alberto-Del-Real/research'),
    'patricio': dict(
        slug='dr-patricio-vial', nombre='Dr. Patricio Vial U.',
        nombre_completo='Patricio Vial', rol='Rehabilitador Oral e Implantólogo', especialidad=None,
        foto='images/dr-patricio-vial.jpeg',
        resumen='Especialista en Rehabilitación Oral de la Universidad de Chile e implantólogo. Coordinador del área quirúrgica del Programa de Especialización en Implantología Buco Máxilo Facial de la Universidad Andrés Bello.',
        bio=['Especialista en Rehabilitación Oral de la Universidad de Chile, con diplomado en Cirugía de Implantes y Magíster en Pedagogía Universitaria.',
             'Es Coordinador del área quirúrgica del Programa de Especialización en Implantología Buco Máxilo Facial de la Universidad Andrés Bello (UNAB).',
             'En Ortodoncia Richard atiende la rehabilitación oral, las coronas y los implantes, y trabaja junto a los ortodoncistas en los casos que necesitan ambos tratamientos: por ejemplo, abrir o cerrar espacios antes de un implante o restaurar dientes al terminar la ortodoncia.'],
        formacion=['Cirujano Dentista — Universidad de Chile',
                   'Especialista en Rehabilitación Oral — Universidad de Chile',
                   'Diplomado en Cirugía de Implantes — Universidad de Chile',
                   'Magíster en Pedagogía Universitaria — Universidad Mayor'],
        cargos=['Coordinador área quirúrgica, Programa de Especialización en Implantología Buco Máxilo Facial — UNAB'],
        membresias=['Colegio de Cirujano Dentistas de Chile'], pubs=[],
        alumni=['Universidad de Chile', 'Universidad Mayor'], sameAs=[], pubmed=None),
}
ORTODONCISTAS = ['rodrigo', 'octavio', 'alberto']


# ═══════════════════════════════════════════════════════════════════════════
# PÁGINAS POR PREGUNTA DEL PACIENTE
# Cada una parte con una respuesta corta y directa: es el párrafo que una IA cita.
# ═══════════════════════════════════════════════════════════════════════════
REVISOR = 'alberto'

TEMAS = [
    dict(
        slug='contenciones',
        titulo='Contenciones después de la ortodoncia: qué son y cuánto tiempo se usan',
        corto='Contenciones',
        descripcion='Contenciones o retenedores después de los brackets o alineadores: tipos (fija y removible), cuánto tiempo se usan, cuidados y qué hacer si se sueltan. Ortodoncia Richard, Las Condes.',
        imagen=None,
        respuesta='Las contenciones (también llamadas retenedores) son los aparatos que mantienen los dientes en su nueva posición al terminar el tratamiento con brackets o alineadores. Pueden ser fijas —un alambre delgado pegado por la cara interna de los dientes— o removibles —placas transparentes o de acrílico—. Los primeros 6 meses son los más críticos, pero como los dientes tienden a moverse durante toda la vida, recomendamos usarlas el mayor tiempo posible, especialmente las inferiores, con controles periódicos con el ortodoncista.',
        secciones=[
            ('Por qué los dientes se mueven después de la ortodoncia',
             '<p>Al terminar el tratamiento, el hueso y las encías que rodean los dientes todavía se están adaptando a su nueva posición, y los dientes tienden a volver hacia donde estaban. Además, con el crecimiento y el envejecimiento los dientes se desplazan naturalmente hacia adelante: por eso incluso adultos que nunca usaron brackets ven aparecer apiñamiento en los dientes de adelante con los años. La contención es lo que protege el resultado.</p>'),
            ('Tipos de contención',
             '<ul><li><strong>Contención fija:</strong> un alambre delgado pegado por detrás de los dientes de adelante, generalmente los inferiores. No se ve, no depende de que el paciente se acuerde de usarla y actúa las 24 horas. Exige una buena higiene, porque entre el alambre y los dientes se acumula sarro.</li>'
             '<li><strong>Contención removible:</strong> una placa transparente (tipo alineador) o de acrílico con un alambre, que se pone y se saca. Se usa según las indicaciones del ortodoncista, habitualmente de noche después de los primeros meses.</li></ul>'
             '<p>Es frecuente combinar las dos: fija abajo y removible arriba. Cuál conviene se decide según el caso y cómo se movieron los dientes durante el tratamiento.</p>'),
            ('Cuánto tiempo hay que usarlas',
             '<p>Los primeros 6 meses después de sacar los brackets o terminar los alineadores son los más críticos, y ahí el uso tiene que ser riguroso. Después, nuestra recomendación es mantener las contenciones la mayor cantidad de tiempo posible —especialmente en los dientes inferiores— y no hay problema en usarlas durante toda la vida, controlándolas periódicamente según te indique tu ortodoncista.</p>'),
            ('Cuidados',
             '<ul><li>Cepilla bien alrededor de la contención fija y usa seda dental con enhebrador o un cepillo interdental.</li>'
             '<li>Lava la contención removible con cepillo y agua fría o tibia, nunca caliente: se deforma.</li>'
             '<li>Guárdala siempre en su estuche. La mayoría de las contenciones se pierden envueltas en una servilleta.</li>'
             '<li>Asiste a los controles: en ellos revisamos que siga bien pegada o ajustada.</li></ul>'),
            ('Si se suelta, se rompe o se pierde',
             '<p>Avísanos lo antes posible, sin esperar al próximo control: un diente sin contención puede moverse en pocas semanas. Si la contención removible empieza a quedar apretada, es señal de que los dientes se están moviendo porque no se está usando lo suficiente. Para urgencias durante las vacaciones, revisa la sección de <a href="index.html#pacientes">urgencias</a>.</p>'),
        ],
        faq=[
            ('¿Las contenciones son de por vida?', 'Pueden serlo, y no hay problema en usarlas toda la vida. Los primeros 6 meses son los más críticos; después recomendamos mantenerlas el mayor tiempo posible, sobre todo las inferiores, porque los dientes tienden a moverse con los años aunque nunca se haya usado ortodoncia.'),
            ('¿Cuánto tiempo se usan las contenciones después de los brackets?', 'Durante los primeros 6 meses el uso tiene que ser riguroso. Después, el ortodoncista indica cuántas horas al día y por cuánto tiempo, y lo ideal es mantenerlas el mayor tiempo posible.'),
            ('¿Contención y retenedor es lo mismo?', 'Sí. En Chile se suele decir contención; en otros países, retenedor. Ambos nombres se refieren al aparato que mantiene los dientes en su posición después de la ortodoncia.'),
            ('¿Qué es mejor, la contención fija o la removible?', 'Depende del caso. La fija actúa todo el día y no depende del paciente, pero exige buena higiene; la removible es fácil de limpiar, pero solo funciona si se usa. Muchas veces se combinan.'),
            ('¿Qué hago si se me soltó la contención fija?', 'Avisa a la clínica lo antes posible para pegarla. No esperes al próximo control: un diente sin contención puede moverse en pocas semanas.'),
        ]),
    dict(
        slug='ortodoncia-invisible-alineadores',
        titulo='Ortodoncia invisible con alineadores (Invisalign) en Las Condes',
        corto='Alineadores invisibles',
        descripcion='Alineadores transparentes (Invisalign, ClearCorrect y otros) planificados por ortodoncistas especialistas en Las Condes, Santiago. Para adultos y adolescentes.',
        imagen='images/ejemplo-apiñamiento.jpg',
        respuesta='Los alineadores son placas transparentes y removibles que mueven los dientes por etapas, como alternativa a los brackets. Sirven para la mayoría de los casos de adultos y adolescentes —desde un apiñamiento leve hasta casos complejos—, siempre que el tratamiento lo planifique y controle un ortodoncista especialista y que el paciente los use entre 20 y 22 horas al día. En Ortodoncia Richard los planifican ortodoncistas especialistas, con escáner intraoral y planificación digital.',
        secciones=[
            ('Cómo funciona el tratamiento',
             '<p>Primero se hace una evaluación completa: examen clínico, radiografías y registros digitales de la boca con escáner intraoral, sin pastas de impresión. Con esos registros el ortodoncista planifica en el computador cada movimiento de los dientes y se fabrica una serie de alineadores, que el paciente va cambiando cada una o dos semanas.</p>'
             '<p>Los controles en la clínica son menos frecuentes que con brackets, pero igual de importantes: en ellos el especialista verifica que los dientes se muevan según lo planificado y, si hace falta, corrige el plan.</p>'),
            ('¿Para quién sirven?',
             '<p>Para adultos y adolescentes con dientes permanentes. Son especialmente cómodos para quien necesita un tratamiento discreto por su trabajo o vida social. Hay casos —ciertos problemas de mordida o de los maxilares— en que los brackets o un tratamiento combinado logran mejor resultado; eso se define en la primera consulta, con el caso a la vista y no por catálogo.</p>'),
            ('Alineadores o brackets',
             '<ul><li><strong>Alineadores:</strong> casi invisibles, se sacan para comer y cepillarse, sin alambres que rocen. Exigen disciplina: si no se usan, no funcionan.</li>'
             '<li><strong>Brackets (frenillos):</strong> fijos, no dependen de que el paciente se acuerde de usarlos; pueden ser metálicos o estéticos. También existe la <a href="ortodoncia-lingual.html">ortodoncia lingual</a>, con los brackets por dentro.</li></ul>'),
            ('Por qué con un especialista y no por correo',
             '<p>Existen empresas que envían alineadores a domicilio sin examen presencial. Sin radiografías ni controles no se pueden detectar problemas de encías, raíces o mordida, y un movimiento mal planificado puede dañarlos. La AAO recomienda que todo tratamiento de ortodoncia sea supervisado presencialmente por un ortodoncista.</p>'),
            ('Lo que investigamos',
             '<p>Nuestro equipo investiga los materiales de alineadores impresos directamente en 3D, una tecnología que puede cambiar cómo se fabrican:</p>' + '{{PUB:alineadores_2026}}'),
        ],
        faq=[
            ('¿Cuánto dura un tratamiento con alineadores?', 'Depende del caso. Correcciones leves pueden tomar algunos meses; un caso completo suele tomar entre uno y dos años. El plazo estimado para cada paciente se entrega en la evaluación.'),
            ('¿Los alineadores duelen?', 'Al cambiar a un alineador nuevo es normal sentir presión o molestia por uno o dos días. Suele ser más llevadero que los brackets porque no hay alambres ni piezas que rocen.'),
            ('¿Puedo comer con los alineadores puestos?', 'No. Se sacan para comer y para tomar cualquier cosa que no sea agua, y se vuelven a poner después de cepillarse los dientes.'),
            ('¿Necesito contención al terminar?', 'Sí, igual que con brackets. Al terminar se usa una contención fija o removible para que los dientes no vuelvan a moverse.'),
            ('¿Sirven los alineadores para adolescentes?', 'Sí, cuando ya tienen los dientes permanentes y se comprometen a usarlos. Los padres suelen apoyar el uso diario.'),
            ('¿Cuánto cuesta la ortodoncia con alineadores?', 'Depende del caso: de qué hay que corregir, de cuántos alineadores se necesitan y de la duración estimada. Por eso no publicamos precios; el valor, las alternativas (alineadores, brackets metálicos o estéticos, ortodoncia lingual) y las formas de pago se informan en la primera consulta, después del examen.'),
        ]),
    dict(
        slug='ortodoncia-ninos',
        titulo='¿A qué edad llevar a un niño al ortodoncista?',
        corto='Ortodoncia en niños',
        descripcion='La AAO recomienda la primera evaluación de ortodoncia a los 7 años. Qué revisar, cuándo conviene tratar temprano y qué incluye la evaluación en Ortodoncia Richard, Las Condes.',
        imagen='images/ejemplo-compresion.jpg',
        respuesta='La Asociación Americana de Ortodoncia (AAO) recomienda que todo niño tenga su primera evaluación con un ortodoncista a más tardar a los 7 años. No significa empezar un tratamiento a esa edad: la mayoría de los niños solo necesita controles. Pero algunos problemas —una mordida cruzada, un maxilar estrecho, falta severa de espacio o hábitos como chuparse el dedo— se corrigen mejor y más fácil mientras el niño está creciendo.',
        secciones=[
            ('Señales para consultar antes',
             '<ul><li>Dientes de leche que se caen muy temprano o muy tarde.</li>'
             '<li>Dientes muy apiñados, separados o que salen fuera de lugar.</li>'
             '<li>Dientes de arriba que quedan por dentro de los de abajo (mordida cruzada), o que no se tocan adelante (mordida abierta).</li>'
             '<li>Mandíbula que se ve muy adelante o muy atrás.</li>'
             '<li>Respirar por la boca, roncar o dormir mal: ver <a href="respiracion-ronquido-ninos.html">respiración y sueño en niños</a>.</li>'
             '<li>Chuparse el dedo o el chupete pasados los 4 o 5 años.</li></ul>'),
            ('Ortodoncia interceptiva y ortopedia',
             '<p>Cuando conviene tratar temprano se usa ortodoncia interceptiva u ortopedia dentomaxilar: aparatos que guían el crecimiento de los maxilares o ganan espacio para los dientes que vienen. El objetivo es evitar que el problema se agrave y, muchas veces, simplificar o acortar el tratamiento de la adolescencia.</p>'),
            ('Qué incluye la evaluación en nuestra clínica',
             '<p>Examinamos al niño, escuchamos a la familia y explicamos si necesita tratamiento y cuál es el mejor momento. Al terminar, la familia recibe un <strong>informe de evaluación escrito</strong> con lo que se encontró, el plan propuesto y las mediciones del ancho de los maxilares comparadas con tablas de crecimiento por edad y sexo, más un tamizaje de respiración y sueño con instrumentos validados.</p>'
             '<p>Para decidir el momento de tratar, nuestro equipo ha investigado cómo estimar la etapa de crecimiento a partir de la maduración de los molares:</p>' + '{{PUB:molares_2020}}{{PUB:ia_molares_2026}}'),
        ],
        faq=[
            ('¿A qué edad debe ir un niño al ortodoncista por primera vez?', 'A más tardar a los 7 años, según la recomendación de la Asociación Americana de Ortodoncia (AAO). Si los padres notan algo antes, se puede consultar antes.'),
            ('¿Mi hijo tendrá que usar aparatos a los 7 años?', 'No necesariamente. La mayoría de los niños solo necesita controles hasta que llegue el momento adecuado. Se trata temprano solo cuando esperar empeoraría el problema.'),
            ('¿Qué problemas conviene tratar temprano?', 'Mordidas cruzadas, maxilares estrechos, algunos casos de mandíbula adelantada, falta severa de espacio y hábitos como chuparse el dedo, entre otros.'),
            ('¿La ortodoncia temprana evita los brackets después?', 'A veces sí y a veces no, pero suele hacer que el tratamiento posterior sea más simple y corto.'),
        ]),
    dict(
        slug='respiracion-ronquido-ninos',
        titulo='Mi hijo ronca o respira por la boca: ¿tiene que ver con la ortodoncia?',
        corto='Respiración y sueño en niños',
        descripcion='Ronquido, respiración bucal y sueño en niños: qué señales consultar, qué puede y qué no puede hacer la ortodoncia, y el tamizaje validado que aplicamos en Ortodoncia Richard.',
        imagen=None,
        respuesta='Que un niño ronque habitualmente, respire por la boca o haga pausas al dormir no es normal y conviene evaluarlo. El ortodoncista no diagnostica la apnea del sueño —eso requiere un estudio del sueño indicado por un médico—, pero sí puede detectar señales de alerta en el examen y derivar a tiempo. En Ortodoncia Richard aplicamos en cada evaluación un tamizaje con instrumentos validados: el cuestionario PSQ en su versión chilena para niños, STOP-BANG en adultos y un examen clínico estructurado (FAIREST).',
        secciones=[
            ('Señales de alerta',
             '<ul><li>Ronquido frecuente (tres o más noches por semana).</li>'
             '<li>Pausas en la respiración o ahogos mientras duerme.</li>'
             '<li>Respiración por la boca, de día o de noche.</li>'
             '<li>Sueño inquieto, mojar la cama o despertar cansado.</li>'
             '<li>Somnolencia, irritabilidad, dificultad para concentrarse o hiperactividad durante el día.</li></ul>'),
            ('Qué hacemos en la consulta',
             '<p>El apoderado responde un cuestionario breve desde su celular durante la consulta (el PSQ, validado en Chile por Bertrán y colaboradores, 2024) y el ortodoncista examina amígdalas, posición de la lengua, paladar y forma de respirar. El resultado queda en el informe que se lleva la familia.</p>'
             '<p>Si hay señales de riesgo, derivamos al otorrinolaringólogo o a un especialista en medicina del sueño, que son quienes confirman el diagnóstico. Nuestro equipo publicó una revisión sobre el papel del ortodoncista en la apnea del sueño:</p>{{PUB:apnea_2021}}'),
            ('Lo que la ortodoncia puede y no puede hacer',
             '<p>Somos claros con esto: la expansión del paladar u otros aparatos se indican por razones ortodóncicas, como un maxilar estrecho o una mordida cruzada. La AAO advierte que no hay evidencia suficiente para indicar ortodoncia como tratamiento de la apnea del sueño en sí misma. Nuestro aporte es detectar a tiempo y trabajar coordinados con el médico tratante.</p>'
             '<p>Para decidir una expansión en adolescentes y adultos, nuestro equipo ha estudiado la maduración de la sutura del paladar con tomografía:</p>{{PUB:sutura_2024}}'),
        ],
        faq=[
            ('¿Es normal que un niño ronque?', 'Un ronquido ocasional durante un resfrío puede ser normal. Roncar tres o más noches por semana no lo es y conviene evaluarlo.'),
            ('¿El ortodoncista puede diagnosticar apnea del sueño?', 'No. El diagnóstico lo hace un médico con un estudio del sueño (polisomnografía). El ortodoncista detecta señales de alerta y deriva a tiempo.'),
            ('¿La expansión del paladar cura la apnea?', 'No hay evidencia suficiente para indicarla con ese fin. La expansión se indica cuando hay un problema ortodóncico, como un maxilar estrecho o una mordida cruzada.'),
            ('¿Qué cuestionario usan para evaluar el sueño?', 'En niños, el PSQ (Pediatric Sleep Questionnaire) en su versión validada en Chile; en adultos, el STOP-BANG; y en ambos, un examen clínico estructurado (FAIREST).'),
        ]),
    dict(
        slug='ortodoncia-lingual',
        titulo='Ortodoncia lingual: brackets por dentro de los dientes',
        corto='Ortodoncia lingual',
        descripcion='Ortodoncia lingual en Las Condes: brackets por la cara interna de los dientes, invisibles desde afuera. Para quién sirve, adaptación y cuidados.',
        imagen='images/ejemplo-ortodoncia-lingual.jpg',
        respuesta='La ortodoncia lingual usa brackets pegados por la cara interna de los dientes, así que no se ven al hablar ni al sonreír. Es una alternativa fija y estética para adultos y adolescentes que no quieren aparatos visibles y prefieren no depender de usar alineadores todo el día. Requiere un ortodoncista con entrenamiento específico en la técnica.',
        secciones=[
            ('Para quién es',
             '<p>Para quien necesita un tratamiento completo con aparatos fijos pero no quiere que se noten: profesionales, personas que hablan en público o pacientes que ya probaron alineadores y prefieren algo fijo. Se define en la evaluación si el caso es adecuado para esta técnica.</p>'),
            ('Adaptación',
             '<p>Las primeras una o dos semanas la lengua roza los brackets y puede alterar levemente la pronunciación de algunas letras. Es transitorio: la lengua se acostumbra rápido. Los controles son similares a los de los brackets convencionales.</p>'),
            ('Otras opciones estéticas',
             '<p>Si la ortodoncia lingual no es la indicada, existen los <a href="ortodoncia-invisible-alineadores.html">alineadores invisibles</a> y los brackets estéticos de cerámica.</p>'),
        ],
        faq=[
            ('¿Se notan los brackets linguales?', 'No desde afuera: van pegados por la cara interna de los dientes.'),
            ('¿La ortodoncia lingual afecta el habla?', 'Durante las primeras una o dos semanas puede cambiar levemente la pronunciación de algunas letras. Luego la lengua se adapta.'),
            ('¿Es más cara que los brackets tradicionales?', 'Sí, suele serlo, porque los aparatos son personalizados y la técnica es más compleja. El valor de cada caso se entrega en la evaluación.'),
        ]),
    dict(
        slug='cirugia-ortognatica',
        titulo='Ortodoncia y cirugía ortognática: cuándo se necesitan las dos',
        corto='Ortodoncia y cirugía ortognática',
        descripcion='Tratamiento combinado de ortodoncia y cirugía ortognática en Las Condes: cuándo se indica, cómo se coordina con el cirujano maxilofacial y el enfoque Cirugía Primero.',
        imagen='images/ejemplo-clase-II.jpg',
        respuesta='Cuando los maxilares están muy desalineados —la mandíbula muy adelante o muy atrás, una mordida abierta marcada o una asimetría facial—, los aparatos por sí solos no logran una mordida correcta en un adulto. Entonces se combina la ortodoncia con una cirugía de los maxilares (cirugía ortognática), que realiza un cirujano maxilofacial. El ortodoncista prepara los dientes antes de la cirugía y termina de ajustarlos después, en un plan coordinado entre ambos.',
        secciones=[
            ('Cuándo se indica',
             '<p>En adultos que ya terminaron de crecer, con diferencias de tamaño o posición entre los maxilares que afectan la mordida, la masticación, la respiración o la armonía del rostro. En niños y adolescentes, en cambio, muchas veces se puede guiar el crecimiento con ortopedia y evitar la cirugía; por eso importa evaluar a tiempo.</p>'),
            ('Cómo es el tratamiento',
             '<ol><li><strong>Ortodoncia prequirúrgica:</strong> se alinean y ordenan los dientes en cada maxilar.</li>'
             '<li><strong>Cirugía:</strong> el cirujano maxilofacial reposiciona los maxilares.</li>'
             '<li><strong>Ortodoncia posquirúrgica:</strong> se termina de ajustar la mordida.</li></ol>'
             '<p>En casos seleccionados se usa el enfoque <strong>Cirugía Primero</strong>: los aparatos se instalan justo antes de la cirugía, lo que acorta el tratamiento total y entrega el cambio facial desde el inicio.</p>'),
            ('Beneficios',
             '<ul><li>Mordida y masticación correctas.</li><li>Mejor armonía facial.</li><li>Resultados más estables en el tiempo que forzar solo con dientes una diferencia de los huesos.</li></ul>'
             '<p>Para entender cómo evoluciona la relación entre los maxilares después de la pubertad, nuestro equipo publicó:</p>{{PUB:clase_ii_2026}}'),
        ],
        faq=[
            ('¿Toda mandíbula adelantada necesita cirugía?', 'No. En niños y adolescentes muchas veces se puede tratar con ortopedia. En adultos depende de la magnitud de la diferencia; se define con radiografías y un estudio completo.'),
            ('¿Quién hace la cirugía ortognática?', 'Un cirujano maxilofacial. El ortodoncista planifica junto a él y prepara y termina la mordida.'),
            ('¿Qué es Cirugía Primero?', 'Un enfoque en que la cirugía se hace al comienzo del tratamiento, con los aparatos instalados justo antes, lo que puede acortar la duración total.'),
        ]),
]

TECNOLOGIA = dict(
    slug='tecnologia',
    titulo='Ortodoncia digital, tecnología y evidencia científica',
    corto='Tecnología y evidencia',
    descripcion='Escáner intraoral, planificación digital, laboratorio propio, informe de evaluación escrito y la investigación publicada por los ortodoncistas de Ortodoncia Richard, Las Condes.',
    respuesta='En Ortodoncia Richard combinamos ortodoncia digital —escáner intraoral, planificación en computador y alineadores— con decisiones basadas en evidencia científica. Nuestros ortodoncistas forman especialistas en la Universidad de los Andes y publican investigación en revistas internacionales de ortodoncia, incluida investigación sobre inteligencia artificial y alineadores impresos en 3D.',
    secciones=[
        ('Diagnóstico y planificación digital',
         '<ul><li><strong>Escáner intraoral 3D:</strong> registros digitales de los dientes sin pastas de impresión.</li>'
         '<li><strong>Sala de rayos X en la clínica</strong> y, cuando el caso lo requiere, tomografía (CBCT).</li>'
         '<li><strong>Planificación digital</strong> de cada movimiento antes de empezar, especialmente en tratamientos con <a href="ortodoncia-invisible-alineadores.html">alineadores</a>.</li>'
         '<li><strong>Laboratorio propio</strong> para aparatos y contenciones.</li></ul>'),
        ('Un informe escrito después de la primera consulta',
         '<p>Al terminar la evaluación, el paciente o su familia recibe un informe impreso y firmado por el especialista: motivo de consulta, hallazgos, impresión diagnóstica, plan de acción y exámenes indicados. Incluye las mediciones del ancho de los maxilares comparadas con tablas de crecimiento por edad y sexo (Bishara et al., 1997) y un <a href="respiracion-ronquido-ninos.html">tamizaje de respiración y sueño</a> con instrumentos validados. Así lo conversado en la consulta queda por escrito y se puede revisar en casa.</p>'),
        ('Investigación publicada por nuestro equipo',
         '<p>Una selección de los artículos de nuestros ortodoncistas en revistas científicas indexadas:</p>'
         '{{PUB:alineadores_2026}}{{PUB:modelos3d_2024}}{{PUB:ia_extracciones_2022}}{{PUB:ia_molares_2026}}{{PUB:sutura_2024}}{{PUB:clase_ii_2026}}{{PUB:molares_2020}}'
         '<p>Lista completa en el perfil de cada doctor: <a href="dr-rodrigo-oyonarte.html">Dr. Rodrigo Oyonarte</a>, <a href="dr-alberto-del-real.html">Dr. Alberto Del Real</a> y <a href="dr-octavio-del-real.html">Dr. Octavio Del Real</a>.</p>'),
        ('Formación y educación continua',
         '<ul><li>Los tres ortodoncistas son miembros de la American Association of Orthodontists (AAO), la World Federation of Orthodontists (WFO) y la Sociedad de Ortodoncia de Chile (SORTCH).</li>'
         '<li>Docencia de especialidad: el Dr. Rodrigo Oyonarte dirige el Programa de Especialización en Ortodoncia de la Universidad de los Andes, donde el Dr. Octavio Del Real es profesor; el Dr. Patricio Vial coordina el área quirúrgica del programa de Implantología de la UNAB.</li>'
         '<li>El Dr. Octavio Del Real preside la Comisión de Ortodoncia de CONACEO y fue Presidente de la Sociedad de Ortodoncia de Chile.</li>'
         '<li>Todos los especialistas están inscritos en el Registro Nacional de Prestadores Individuales de la Superintendencia de Salud.</li></ul>'),
    ],
    faq=[
        ('¿Usan escáner intraoral en vez de impresiones con pasta?', 'Sí, para los registros digitales y la planificación de tratamientos, especialmente con alineadores.'),
        ('¿Los ortodoncistas de la clínica hacen investigación?', 'Sí. Publican en revistas internacionales como American Journal of Orthodontics and Dentofacial Orthopedics, The Angle Orthodontist, Korean Journal of Orthodontics y Scientific Reports, entre otras.'),
        ('¿Qué recibo después de la primera consulta?', 'Un informe de evaluación escrito y firmado con los hallazgos, el plan propuesto, las mediciones y el tamizaje de respiración y sueño.'),
    ])


# ═══════════════════════════════════════════════════════════════════════════
# PLANTILLA
# ═══════════════════════════════════════════════════════════════════════════
def esc(t):
    return html.escape(t, quote=True)


def texto_plano(h):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', h))).strip()


def bloque_pub(p):
    extra = ('<br/><span class="pub-resumen">%s</span>' % esc(p['resumen'])) if p.get('resumen') else ''
    ident = ('DOI: ' + p['doi']) if p.get('doi') else ('PMID: ' + p['pmid']) if p.get('pmid') else 'ResearchGate'
    return ('<li class="pub"><a href="%s" rel="noopener" target="_blank">%s</a><br/>'
            '<span class="pub-meta">%s · <em>%s</em> (%d) · %s</span>%s</li>'
            % (pub_url(p), esc(p['titulo']), esc(p['autores']), esc(p['revista']),
               p['anio'], esc(ident), extra))


def expandir_pubs(h):
    # {{PUB:x}} consecutivos se agrupan en una sola lista
    def grupo(m):
        claves = re.findall(r'\{\{PUB:(\w+)\}\}', m.group(0))
        return '<ul class="pub-list">' + ''.join(bloque_pub(PUB[c]) for c in claves) + '</ul>'
    return re.sub(r'(?:\{\{PUB:\w+\}\})+', grupo, h)


def schema_articulo(p):
    d = {'@type': 'ScholarlyArticle', 'headline': p['titulo'], 'datePublished': str(p['anio']),
         'isPartOf': {'@type': 'Periodical', 'name': p['revista']}, 'url': pub_url(p)}
    if p.get('doi'):
        d['identifier'] = {'@type': 'PropertyValue', 'propertyID': 'DOI', 'value': p['doi']}
    return d


CLINICA_REF = {'@id': SITIO + '#clinica'}


def schema_doctor(key, reg, completo=False):
    d = DOCTORES[key]
    s = {'@type': 'Physician', '@id': SITIO + d['slug'] + '.html#doctor', 'name': d['nombre'],
         'url': SITIO + d['slug'] + '.html', 'image': SITIO + d['foto'], 'jobTitle': d['rol'],
         'worksFor': CLINICA_REF,
         'identifier': {'@type': 'PropertyValue',
                        'propertyID': 'Registro Superintendencia de Salud (Chile)', 'value': reg[key]}}
    if d['especialidad']:
        s['medicalSpecialty'] = d['especialidad']
    if completo:
        s['description'] = d['resumen']
        s['alumniOf'] = [{'@type': 'CollegeOrUniversity', 'name': u} for u in d['alumni']]
        s['memberOf'] = [{'@type': 'Organization', 'name': m} for m in d['membresias']]
        s['hasCredential'] = [{'@type': 'EducationalOccupationalCredential', 'name': f} for f in d['formacion']]
        if d['sameAs']:
            s['sameAs'] = d['sameAs']
        if d['pubs']:
            s['subjectOf'] = [schema_articulo(PUB[c]) for c in d['pubs']]
    return s


def breadcrumb(nombre, url):
    return {'@type': 'BreadcrumbList', 'itemListElement': [
        {'@type': 'ListItem', 'position': 1, 'name': 'Inicio', 'item': SITIO},
        {'@type': 'ListItem', 'position': 2, 'name': nombre, 'item': url}]}


def faq_schema(faq):
    return {'@type': 'FAQPage', 'mainEntity': [
        {'@type': 'Question', 'name': q,
         'acceptedAnswer': {'@type': 'Answer', 'text': a}} for q, a in faq]}


NAV = '''<nav class="scrolled" id="navbar">
<div class="nav-container">
<a class="nav-logo" href="index.html">
<img alt="Ortodoncia Richard" class="logo-on-dark" src="images/logo-png.png"/>
<img alt="Ortodoncia Richard" class="logo-on-light" src="images/logo.jpg"/>
</a>
<ul class="nav-links" id="navLinks">
<li><a class="nav-link" href="index.html#equipo">Equipo</a></li>
<li><a class="nav-link" href="index.html#tratamientos">Tratamientos</a></li>
<li><a class="nav-link" href="tecnologia.html">Tecnología</a></li>
<li><a class="nav-link" href="index.html#pacientes">Pacientes</a></li>
<li><a class="nav-link" href="index.html#contacto">Contacto</a></li>
</ul>
<a class="btn-nav-cta" href="index.html#agendar"><i class="fas fa-calendar-check"></i><span>Reservar hora</span></a>
<button aria-label="Abrir menú" class="nav-toggle" id="navToggle"><span></span><span></span><span></span></button>
</div>
</nav>'''


def footer():
    temas = ''.join('<li><a href="%s.html">%s</a></li>' % (t['slug'], esc(t['corto']))
                    for t in TEMAS + [TECNOLOGIA])
    docs = ''.join('<li><a href="%s.html">%s</a></li>' % (DOCTORES[k]['slug'], esc(DOCTORES[k]['nombre']))
                   for k in ORTODONCISTAS + ['patricio'])
    return '''<footer class="footer">
<div class="container">
<div class="footer-grid">
<div class="footer-brand">
<img alt="Ortodoncia Richard" class="footer-logo" src="images/logo-png.png"/>
<p>Clínica de ortodoncia en Las Condes, Santiago. Ortodoncistas especialistas miembros de AAO, WFO y SORTCH.</p>
</div>
<div class="footer-links"><h4>Especialistas</h4><ul>%s</ul></div>
<div class="footer-links"><h4>Para pacientes</h4><ul>%s</ul></div>
<div class="footer-contact">
<h4>Contacto</h4>
<p><i class="fas fa-location-dot"></i> Paul Harris 10.349, Of. 305<br/>Las Condes, Santiago</p>
<p><i class="fas fa-phone"></i> <a href="tel:%s">%s</a></p>
<p><i class="fab fa-whatsapp"></i> <a href="%s" rel="noopener" target="_blank">+56 9 3355 8189</a></p>
<p><i class="fab fa-google"></i> <a href="%s" rel="noopener" target="_blank">Opiniones en Google</a></p>
</div>
</div>
<div class="footer-bottom">
<p>© 2026 Clínica Ortodoncia Richard · <a href="privacidad.html">Política de Privacidad</a></p>
<p>Paul Harris 10.349, Of. 305, Las Condes, Santiago · Chile</p>
</div>
</div>
</footer>''' % (docs, temas, TEL, TEL_TXT, WA, GOOGLE_PERFIL)


SCRIPT_NAV = '''<script>
(function(){var t=document.getElementById('navToggle'),l=document.getElementById('navLinks');
if(t&&l){t.addEventListener('click',function(){var o=l.classList.toggle('open');t.classList.toggle('open',o);});}})();
</script>'''


def pagina(slug, titulo_html, descripcion, schemas, cuerpo, og_img='images/logo.jpg'):
    url = SITIO + ('' if slug == 'index' else slug + '.html')
    ld = json.dumps({'@context': 'https://schema.org', '@graph': schemas},
                    ensure_ascii=False, indent=1)
    return '''<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>{t}</title>
<meta content="{d}" name="description"/>
<link href="{u}" rel="canonical"/>
<meta content="index, follow, max-image-preview:large" name="robots"/>
<meta content="article" property="og:type"/>
<meta content="{t}" property="og:title"/>
<meta content="{d}" property="og:description"/>
<meta content="{u}" property="og:url"/>
<meta content="{img}" property="og:image"/>
<meta content="Ortodoncia Richard" property="og:site_name"/>
<meta content="es_CL" property="og:locale"/>
<!-- Generada por tools/generar_paginas.py: editar el texto allá, no acá. -->
<link href="https://fonts.googleapis.com" rel="preconnect"/>
<link crossorigin="" href="https://fonts.gstatic.com" rel="preconnect"/>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;600;700&amp;family=Inter:ital,wght@0,300;0,400;0,500;0,600;1,400&amp;display=swap" rel="stylesheet"/>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet"/>
<link href="images/logo-png.png" rel="icon" type="image/png"/>
<link href="css/styles.css" rel="stylesheet"/>
<script type="application/ld+json">
{ld}
</script>
</head>
<body class="page-sub">
{nav}
<main class="page-content">
{cuerpo}
</main>
{footer}
{script}
</body>
</html>
'''.format(t=esc(titulo_html), d=esc(descripcion), u=url, img=SITIO + og_img, ld=ld,
           nav=NAV, cuerpo=cuerpo, footer=footer(), script=SCRIPT_NAV)


def cta():
    return ('<aside class="page-cta"><h2>¿Quieres una evaluación?</h2>'
            '<p>La primera consulta es con un ortodoncista especialista. Al terminar te llevas un informe escrito con lo que encontramos y el plan propuesto.</p>'
            '<div class="page-cta-btns"><a class="btn btn-primary" href="index.html#agendar"><i class="fas fa-calendar-check"></i> Reservar hora online</a>'
            '<a class="btn btn-outline-navy" href="%s" rel="noopener" target="_blank"><i class="fab fa-whatsapp"></i> Escribir por WhatsApp</a></div>'
            '<p class="page-cta-dir">Paul Harris 10.349, of. 305, Las Condes · <a href="tel:%s">%s</a></p></aside>'
            % (WA, TEL, TEL_TXT))


def tarjetas_doctores(keys):
    out = []
    for k in keys:
        d = DOCTORES[k]
        out.append('<a class="doc-mini" href="%s.html"><img alt="%s" src="%s" loading="lazy"/>'
                   '<span><strong>%s</strong><br/>%s</span></a>'
                   % (d['slug'], esc(d['nombre']), d['foto'], esc(d['nombre']), esc(d['rol'])))
    return '<div class="doc-minis">' + ''.join(out) + '</div>'


def faq_html(faq):
    return ('<section><h2>Preguntas frecuentes</h2><div class="page-faq">' +
            ''.join('<details><summary>%s</summary><p>%s</p></details>' % (esc(q), esc(a)) for q, a in faq)
            + '</div></section>')


def generar_tema(t, reg):
    url = SITIO + t['slug'] + '.html'
    rev = DOCTORES[REVISOR]
    cuerpo = ['<nav aria-label="Ruta" class="migas"><a href="index.html">Inicio</a> › %s</nav>' % esc(t['corto']),
              '<h1>%s</h1>' % esc(t['titulo']),
              '<p class="page-revision">Revisado por <a href="%s.html">%s</a>, ortodoncista · Registro Superintendencia de Salud N° %s · Actualizado en octubre de 2026</p>'
              % (rev['slug'], esc(rev['nombre']), reg[REVISOR]),
              '<div class="respuesta-corta"><p>%s</p></div>' % esc(t['respuesta'])]
    if t.get('imagen'):
        cuerpo.append('<img alt="%s" class="page-img" src="%s" loading="lazy"/>' % (esc(t['corto']), t['imagen']))
    for h2, contenido in t['secciones']:
        cuerpo.append('<section><h2>%s</h2>%s</section>' % (esc(h2), expandir_pubs(contenido)))
    cuerpo.append('<section><h2>Quién te atiende</h2><p>Ortodoncistas especialistas, miembros de la AAO, la WFO y la Sociedad de Ortodoncia de Chile:</p>%s</section>'
                  % tarjetas_doctores(ORTODONCISTAS))
    cuerpo.append(faq_html(t['faq']))
    cuerpo.append(cta())
    schemas = [
        {'@type': 'MedicalWebPage', '@id': url, 'url': url, 'name': t['titulo'],
         'description': t['descripcion'], 'inLanguage': 'es-CL', 'lastReviewed': HOY,
         'reviewedBy': {'@id': SITIO + rev['slug'] + '.html#doctor'},
         'about': {'@type': 'MedicalCondition' if 'ronca' in t['titulo'] else 'MedicalProcedure',
                   'name': t['corto']},
         'publisher': CLINICA_REF, 'mainContentOfPage': {'@type': 'WebPageElement', 'text': t['respuesta']}},
        breadcrumb(t['corto'], url), faq_schema(t['faq']), schema_doctor(REVISOR, reg)]
    return pagina(t['slug'], t['titulo'] + ' | Ortodoncia Richard', t['descripcion'],
                  schemas, '\n'.join(cuerpo), t.get('imagen') or 'images/logo.jpg')


def generar_doctor(key, reg):
    d = DOCTORES[key]
    url = SITIO + d['slug'] + '.html'
    li = lambda xs: '<ul>' + ''.join('<li>%s</li>' % esc(x) for x in xs) + '</ul>'
    cuerpo = ['<nav aria-label="Ruta" class="migas"><a href="index.html">Inicio</a> › <a href="index.html#equipo">Equipo</a> › %s</nav>' % esc(d['nombre']),
              '<div class="doc-cabecera"><img alt="%s" src="%s"/><div><h1>%s</h1><p class="doc-rol">%s · Ortodoncia Richard, Las Condes</p>'
              '<p class="doc-registro"><i class="fas fa-circle-check"></i> Registro Nacional de Prestadores Individuales, Superintendencia de Salud N° %s · '
              '<a href="https://rnpi.superdesalud.gob.cl/" rel="noopener" target="_blank">Verificar</a></p></div></div>'
              % (esc(d['nombre']), d['foto'], esc(d['nombre']), esc(d['rol']), reg[key]),
              '<div class="respuesta-corta"><p>%s</p></div>' % esc(d['resumen'])]
    cuerpo.append('<section><h2>Trayectoria</h2>%s</section>' % ''.join('<p>%s</p>' % esc(p) for p in d['bio']))
    cuerpo.append('<section><h2>Formación</h2>%s</section>' % li(d['formacion']))
    if d['cargos']:
        cuerpo.append('<section><h2>Cargos académicos y gremiales</h2>%s</section>' % li(d['cargos']))
    cuerpo.append('<section><h2>Sociedades científicas</h2>%s</section>' % li(d['membresias']))
    if d['pubs']:
        fuente = 'ResearchGate' if 'researchgate' in (d['pubmed'] or '') else 'PubMed'
        extra = ('<p><a href="%s" rel="noopener" target="_blank">Ver todas en %s</a></p>' % (d['pubmed'], fuente)) if d['pubmed'] else ''
        cuerpo.append('<section><h2>Publicaciones científicas</h2>%s%s</section>'
                      % (expandir_pubs(''.join('{{PUB:%s}}' % c for c in d['pubs'])), extra))
    if d.get('conferencias'):
        filas = ''.join('<li><strong>%s</strong> · %s%s</li>' % (a, esc(t), (' · <a href="%s" rel="noopener" target="_blank">ver</a>' % u) if u else '')
                        for a, t, u in d['conferencias'])
        cuerpo.append('<section><h2>Conferencias y premios</h2><ul>%s</ul></section>' % filas)
    if key in ORTODONCISTAS:
        temas = ''.join('<li><a href="%s.html">%s</a></li>' % (t['slug'], esc(t['corto'])) for t in TEMAS)
        cuerpo.append('<section><h2>Tratamientos</h2><p>Ortodoncia en niños, adolescentes y adultos: brackets (frenillos), alineadores invisibles, ortodoncia lingual, ortopedia dentomaxilar y tratamientos combinados con cirugía.</p><ul>%s</ul></section>' % temas)
    cuerpo.append(cta())
    schemas = [{'@type': 'ProfilePage', '@id': url, 'url': url, 'name': d['nombre'],
                'inLanguage': 'es-CL', 'dateModified': HOY,
                'mainEntity': {'@id': url + '#doctor'}},
               schema_doctor(key, reg, completo=True), breadcrumb(d['nombre'], url)]
    titulo = '%s — %s en Las Condes | Ortodoncia Richard' % (d['nombre'], d['rol'])
    return pagina(d['slug'], titulo, d['resumen'], schemas, '\n'.join(cuerpo), d['foto'])


def generar_llms(reg):
    l = ['# Ortodoncia Richard', '',
         '> Clínica de ortodoncia en Las Condes, Santiago de Chile (Paul Harris 10.349, oficina 305). '
         'Ortodoncistas especialistas para niños, adolescentes y adultos: brackets, alineadores invisibles '
         '(Invisalign y otros), ortodoncia lingual, ortopedia dentomaxilar y tratamientos combinados con cirugía '
         'ortognática; además rehabilitación oral e implantes. Teléfono %s, WhatsApp +56 9 3355 8189, '
         'recepcion@ortodonciarichard.cl. Lunes a viernes 9:00–19:30.' % TEL_TXT, '',
         'Los tres ortodoncistas son miembros de la American Association of Orthodontists (AAO), la World '
         'Federation of Orthodontists (WFO) y la Sociedad de Ortodoncia de Chile, e investigan y publican en '
         'revistas internacionales de ortodoncia. Cada evaluación termina con un informe escrito e incluye un '
         'tamizaje validado de respiración y sueño.', '', '## Especialistas', '']
    for k in ORTODONCISTAS + ['patricio']:
        d = DOCTORES[k]
        l.append('- [%s](%s%s.html): %s Registro Superintendencia de Salud N° %s.'
                 % (d['nombre'], SITIO, d['slug'], d['resumen'], reg[k]))
    l += ['', '## Guías para pacientes', '']
    for t in TEMAS + [TECNOLOGIA]:
        l.append('- [%s](%s%s.html): %s' % (t['titulo'], SITIO, t['slug'], t['respuesta']))
    l += ['', '## Más', '', '- [Sitio principal y agenda online](%s)' % SITIO,
          '- [Opiniones de pacientes en Google](%s)' % GOOGLE_PERFIL,
          '- [Política de privacidad](%sprivacidad.html)' % SITIO, '']
    return '\n'.join(l)


def generar_sitemap(slugs):
    filas = [('', '1.0', 'monthly')] + [(s + '.html', '0.8', 'monthly') for s in slugs] + \
            [('privacidad.html', '0.3', 'yearly')]
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, pri, freq in filas:
        out.append('  <url>\n    <loc>%s%s</loc>\n    <lastmod>%s</lastmod>\n'
                   '    <changefreq>%s</changefreq>\n    <priority>%s</priority>\n  </url>'
                   % (SITIO, loc, HOY, freq, pri))
    out.append('</urlset>\n')
    return '\n'.join(out)


# ═══════════════════════════════════════════════════════════════════════════
# DIRECCIONES DEL SITIO ANTIGUO (Wix)
# Google las seguía mostrando (el artículo de contenciones: 36.737 apariciones en
# 16 meses) y desde el cambio de sitio llevaban a un error. GitHub Pages no
# permite redirecciones de servidor: cada una es un HTML mínimo con
# meta refresh 0 + canonical, que Google trata como redirección permanente.
# GitHub Pages resuelve /ruta probando /ruta.html y /ruta/index.html: por eso
# /team/dr.-octavio-del-real-s. es el archivo "team/dr.-octavio-del-real-s..html".
# Lista sacada de Search Console + el archivo de Internet (2026-10-07).
# ═══════════════════════════════════════════════════════════════════════════
_P = 'index.html#pacientes'
REDIRECCIONES = {
    'team/dr.-octavio-del-real-s.': 'dr-octavio-del-real.html',
    'team/dr.-rodrigo-oyonarte-w.': 'dr-rodrigo-oyonarte.html',
    'team/dr.-alberto-del-real-v.': 'dr-alberto-del-real.html',
    'team/dr.-patricio-vial-u.': 'dr-patricio-vial.html',
    'team/': 'index.html#equipo',
    'profesionales-y-staff': 'index.html#equipo',
    'contactenos-1': 'index.html#contacto',
    'agenda': 'index.html#agendar',
    'agenda-online': 'index.html#agendar',
    'copia-de-agenda-online': 'index.html#agendar',
    'confirmacion': 'index.html#agendar',
    'primera-consulta': 'index.html#pacientes',
    'por-que-nosotros': 'index.html#nosotros',
    'publicaciones-cientificas': 'tecnologia.html',
    'curso-ortodoncia-digital': 'tecnologia.html',
    'charlas': 'tecnologia.html',
    'certificados-del-personal': 'tecnologia.html',
    'blog/': _P,
    'post/qué-son-las-contenciones-y-por-cuanto-tiempo-tendré-que-usarlas': 'contenciones.html',
    'post/son-permanentes-los-cambios-logrados-con-el-tratamiento-de-ortodoncia': 'contenciones.html',
    'post/cuáles-son-los-principales-beneficios-de-la-ortodoncia-con-cirugía-ortognática': 'cirugia-ortognatica.html',
    'post/cuáles-son-los-problemas-que-la-cirugía-ortognática-mejor-corrige': 'cirugia-ortognatica.html',
    'post/cuándo-se-indica-la-cirugía-ortognática': 'cirugia-ortognatica.html',
    'post/cómo-progresa-el-tratamiento-de-ortodoncia-combinado-con-cirugía-ortognática': 'cirugia-ortognatica.html',
    'post/de-qué-se-trata-la-cirugía-máxilofacial-o-cirugía-ortognática': 'cirugia-ortognatica.html',
    'post/es-posible-evitar-la-cirugía': 'cirugia-ortognatica.html',
    'post/existen-riesgos-asociados-a-la-cirugía-ortognática': 'cirugia-ortognatica.html',
    'post/qué-diferencia-a-la-técnica-cirugía-primero': 'cirugia-ortognatica.html',
    'post/alineadores-a-la-casa': 'ortodoncia-invisible-alineadores.html',
    'post/uso-cuidado-alineadores': 'ortodoncia-invisible-alineadores.html',
    'post/una-mirada-a-la-ortodoncia-en-adultos': 'ortodoncia-invisible-alineadores.html',
    'post/existe-algún-límite-de-edad-para-la-ortodoncia': 'ortodoncia-invisible-alineadores.html',
    'post/qué-cosas-controlar-en-su-hijo-para-saber-si-necesita-ortodoncia': 'ortodoncia-ninos.html',
    'post/por-qué-se-aconseja-tener-un-tratamiento-de-ortodoncia-ahora': 'ortodoncia-ninos.html',
    'post/qué-rol-juega-lo-hereditario': 'ortodoncia-ninos.html',
    'post/qué-causa-la-falta-de-alineación-de-los-dientes': 'ortodoncia-ninos.html',
    'post/por-qué-es-bueno-preocuparme-de-corregir-una-mala-mordida': 'ortodoncia-ninos.html',
    'post/qué-oasa-con-las-actividades-extracurriculares': 'ortodoncia-ninos.html',
    'post/egresa-la-primera-generación-de-ortodoncistas-de-la-universidad-de-los-andes': 'dr-rodrigo-oyonarte.html',
    'post/dr-oyonarte-presenta-en-el-congreso-nacional-de-estudiantes-de-odontología': 'dr-rodrigo-oyonarte.html',
    'post/cuánto-se-podría-demorar-la-ortodoncia': _P,
    'post/con-qué-frecuencia-tendré-que-asistir-a-mis-controles': _P,
    'post/me-molestará-o-dolerá-mucho': _P,
    'post/va-a-doler': _P,
    'post/factores-a-considerar-al-utilizar-aparatos-de-ortodoncia': _P,
    'post/decálogo': _P,
    'post/manejo-de-urgencias-durante-las-vacaciones': _P,
    'post/urgencias-en-ortodoncia-durante-covid-19': _P,
    'post/protocolos-covid-19': _P,
    'post/estamos-atendiendo-con-todas-las-medidas-de-seguridad': _P,
    'post/nuevo-video-de-la-clínica': 'index.html#galeria',
}


def archivo_redireccion(ruta):
    """/x/ -> x/index.html ; /x -> x.html (lo que GitHub Pages busca)."""
    return ruta + 'index.html' if ruta.endswith('/') else ruta + '.html'


def html_redireccion(ruta, destino):
    prof = ruta.rstrip('/').count('/') + (1 if ruta.endswith('/') else 0)
    rel = '../' * prof + destino
    # A Google se le declara la página, sin el #sección; la portada es la raíz del sitio.
    pagina_dest = destino.split('#')[0]
    absoluta = SITIO + ('' if pagina_dest == 'index.html' else pagina_dest)
    return ('<!DOCTYPE html>\n<html lang="es"><head><meta charset="utf-8"/>'
            '<title>Ortodoncia Richard</title>'
            '<link rel="canonical" href="%s"/>'
            '<meta http-equiv="refresh" content="0; url=%s"/>'
            '<script>location.replace(%s);</script>'
            '</head><body><p>Esta página se movió a <a href="%s">%s</a>.</p></body></html>\n'
            % (absoluta, rel, json.dumps(rel), rel, absoluta))


DIENTE_404 = """<svg class="e404-diente" viewBox="0 0 260 260" role="img" aria-label="Un diente con brackets buscando con una lupa">
 <ellipse cx="120" cy="238" rx="78" ry="10" fill="#1A2E4A" opacity=".12"/>
 <g class="e404-cuerpo">
  <path d="M62 70c0-30 22-46 44-42 9 2 14 7 20 7s11-5 20-7c22-4 44 12 44 42 0 22-8 36-12 58-4 22-6 52-18 82-5 12-19 12-22-1-4-18-5-40-12-40s-8 22-12 40c-3 13-17 13-22 1-12-30-14-60-18-82-4-22-12-36-12-58z" fill="#fff" stroke="#1A2E4A" stroke-width="5" stroke-linejoin="round"/>
  <path d="M84 52c6-6 14-8 22-6" fill="none" stroke="#DDE6F0" stroke-width="6" stroke-linecap="round"/>
  <ellipse cx="102" cy="86" rx="9" ry="11" fill="#1A2E4A"/>
  <ellipse cx="150" cy="86" rx="9" ry="11" fill="#1A2E4A"/>
  <circle cx="105" cy="82" r="3" fill="#fff"/><circle cx="153" cy="82" r="3" fill="#fff"/>
  <path d="M90 70q9-9 18-6M164 70q-9-9-18-6" stroke="#1A2E4A" stroke-width="4" stroke-linecap="round"/>
  <ellipse cx="88" cy="106" rx="8" ry="5" fill="#F4A6A6" opacity=".7"/><ellipse cx="164" cy="106" rx="8" ry="5" fill="#F4A6A6" opacity=".7"/>
  <ellipse cx="126" cy="113" rx="7" ry="8" fill="#1A2E4A"/>
  <line x1="90" y1="132" x2="162" y2="132" stroke="#8A97A8" stroke-width="3"/>
  <rect x="94" y="125" width="12" height="13" rx="2" fill="#C9A84C" stroke="#1A2E4A" stroke-width="2"/>
  <rect x="120" y="125" width="12" height="13" rx="2" fill="#C9A84C" stroke="#1A2E4A" stroke-width="2"/>
  <rect x="146" y="125" width="12" height="13" rx="2" fill="#C9A84C" stroke="#1A2E4A" stroke-width="2"/>
 </g>
 <g class="e404-lupa">
  <line x1="196" y1="150" x2="226" y2="186" stroke="#1A2E4A" stroke-width="10" stroke-linecap="round"/>
  <circle cx="186" cy="136" r="24" fill="#EAF2FB" fill-opacity=".85" stroke="#1A2E4A" stroke-width="6"/>
  <path d="M174 128c4-6 10-8 16-7" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round"/>
 </g>
 <text x="22" y="52" class="e404-signo">?</text><text x="214" y="70" class="e404-signo e404-signo2">?</text>
</svg>"""


def generar_404():
    """Página de error con humor (y con salida): cualquier dirección vieja que no
    esté en REDIRECCIONES lleva igual a algo útil. GitHub Pages sirve 404.html
    para todo lo que no existe, a cualquier profundidad."""
    guias = ''.join('<a class="e404-chip" href="%s.html">%s</a>' % (t['slug'], esc(t['corto']))
                    for t in TEMAS + [TECNOLOGIA])
    docs = ''.join('<a class="e404-chip" href="%s.html">%s</a>' % (DOCTORES[k]['slug'], esc(DOCTORES[k]['nombre']))
                   for k in ORTODONCISTAS + ['patricio'])
    cuerpo = ('<section class="e404">' + DIENTE_404 +
              '<p class="e404-num">404</p>'
              '<h1>¡Ups! Esta página se desalineó</h1>'
              '<p class="e404-texto">La buscamos con lupa, pero se movió de su lugar… '
              'y claramente no estaba usando su contención. 😬</p>'
              '<p class="e404-texto">Si llegaste desde nuestro sitio anterior, todo sigue aquí, solo que mejor ordenado:</p>'
              '<div class="e404-btns"><a class="btn btn-primary" href="index.html"><i class="fas fa-house"></i> Volver al inicio</a>'
              '<a class="btn btn-primary e404-agenda" href="index.html#agendar"><i class="fas fa-calendar-check"></i> Reservar hora</a></div>'
              '<h2>Nuestros especialistas</h2><div class="e404-chips">' + docs + '</div>'
              '<h2>Guías para pacientes</h2><div class="e404-chips">' + guias + '</div>'
              '</section>')
    h = pagina('404', 'Página no encontrada | Ortodoncia Richard', 'Página no encontrada.', [], cuerpo)
    # Rutas absolutas: 404.html se sirve desde cualquier profundidad.
    h = re.sub(r'(href|src)="(?!https?:|#|/|mailto:|tel:)', r'\1="/', h)
    return h.replace('<meta content="index, follow, max-image-preview:large" name="robots"/>',
                     '<meta content="noindex" name="robots"/>')


def escribir(nombre, contenido):
    ruta = os.path.join(RAIZ, nombre)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with io.open(ruta, 'w', encoding='utf-8', newline='\n') as f:
        f.write(contenido)
    print('  ', nombre)


def actualizar_faq_portada():
    """Copia las preguntas frecuentes de la portada (el acordeón) a un bloque
    FAQPage de JSON-LD, entre marcadores. Así el schema nunca queda distinto del
    texto visible: si se edita una pregunta en index.html, se corre este script."""
    p = os.path.join(RAIZ, 'index.html')
    s = io.open(p, encoding='utf-8').read()
    pares = re.findall(r'<button class="acc-btn">(.*?)<i class.*?</button>\s*<div class="acc-body">(.*?)</div>', s, re.S)
    faq = [(texto_plano(q), texto_plano(a)) for q, a in pares]
    bloque = ('<!-- FAQ-SCHEMA (generado por tools/generar_paginas.py) -->\n'
              '<script type="application/ld+json">\n%s\n</script>\n<!-- /FAQ-SCHEMA -->'
              % json.dumps(dict({'@context': 'https://schema.org'}, **faq_schema(faq)),
                           ensure_ascii=False, indent=1))
    if '<!-- FAQ-SCHEMA' in s:
        s = re.sub(r'<!-- FAQ-SCHEMA.*?<!-- /FAQ-SCHEMA -->', lambda m: bloque, s, flags=re.S)
    else:
        s = s.replace('</head>', bloque + '\n</head>', 1)
    with io.open(p, 'w', encoding='utf-8', newline='') as f:
        f.write(s)
    print('   index.html (FAQPage: %d preguntas)' % len(faq))


def main():
    reg = registros_main_js()
    faltan = [k for k in DOCTORES if k not in reg]
    if faltan:
        raise SystemExit('Sin registro en js/main.js para: %s' % faltan)
    slugs = []
    for k in DOCTORES:
        escribir(DOCTORES[k]['slug'] + '.html', generar_doctor(k, reg))
        slugs.append(DOCTORES[k]['slug'])
    for t in TEMAS + [TECNOLOGIA]:
        escribir(t['slug'] + '.html', generar_tema(t, reg))
        slugs.append(t['slug'])
    for ruta, destino in REDIRECCIONES.items():
        escribir(archivo_redireccion(ruta), html_redireccion(ruta, destino))
    escribir('404.html', generar_404())
    escribir('sitemap.xml', generar_sitemap(slugs))
    escribir('llms.txt', generar_llms(reg))
    actualizar_faq_portada()


if __name__ == '__main__':
    main()
