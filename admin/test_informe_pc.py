"""
test_informe_pc.py - Informe de Primera Consulta: registro en disco y armado
del documento de tres hojas que el paciente se lleva impreso y firmado.

Cero red y cero correo: el modulo no llama a DentiDesk ni manda nada. El
registro escribe en un tempfile (PATIENT_INDEX_PATH se fija ANTES de importar).

    cd admin && python test_informe_pc.py

Lo que se protege:

  - Que reeditar un informe no borre el sello de impresion ni duplique la fila.
  - El corte de los 18 anios: menor -> PSQ-CL + FAIREST-6; adulto -> STOP-BANG
    + FAIREST 6+4.
  - Que al paciente al que se le dice "no requiere tratamiento" NO se le ofrezca
    el Estudio Integral. Es el paciente que hoy se va peor -- pago por una buena
    noticia -- y ofrecerle el estudio ahi es exactamente la venta que este
    documento existe para no parecer.
  - Que una medicion sin referencia se informe como tal y NUNCA como si
    estuviera dentro del promedio. Lo mismo con un PSQ que nadie contesto: no se
    inventa un "sin riesgo" a partir de un cuestionario en blanco.
  - Y la mas importante: que NINGUN texto del catalogo ni del documento armado
    contenga una frase prohibida. Todo esto se imprime con la firma del doctor.
"""

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='informe_pc_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['INFORME_PC_REGISTRO_PATH'] = str(_TMP / 'informe_pc_registro.json')
sys.path.insert(0, str(Path(__file__).parent))

import fairest      # noqa: E402
import informe_pc   # noqa: E402


def _limpiar_registro():
    """Cada prueba de seguimiento parte de cero: el historial de un paciente
    depende de lo que haya en el registro, asi que arrastrar informes de otra
    prueba haria fallar o pasar cosas por accidente."""
    informe_pc._STORE.save({'informes': {}})

FECHA = '2026-08-20'


def _base(**ov):
    """Un informe minimo, valido y sin datos reales de nadie."""
    d = {'fecha': FECHA, 'nombre': 'Paciente De Prueba', 'rut': '11111111-1',
         'edad': 10, 'sexo': 'M', 'motivo_consulta': 'Me molestan los dientes de adelante.',
         'conclusion': 'corresponde'}
    d.update(ov)
    return d


def _textos(obj):
    """Todas las cadenas de una estructura anidada (claves incluidas)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _textos(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _textos(v)


class TestRegistro(unittest.TestCase):

    def setUp(self):
        informe_pc._STORE.save({'informes': {}})

    def test_guardar_devuelve_id(self):
        iid = informe_pc.guardar(_base())
        self.assertTrue(iid)
        self.assertEqual(informe_pc.obtener(iid)['id'], iid)

    def test_reeditar_con_el_mismo_id_no_duplica_y_conserva_creado(self):
        iid = informe_pc.guardar(_base())
        creado = informe_pc.obtener(iid)['creado']
        informe_pc.guardar(_base(id=iid, motivo_consulta='Cambio de motivo.'))
        item = informe_pc.obtener(iid)
        self.assertEqual(item['creado'], creado)
        self.assertEqual(item['motivo_consulta'], 'Cambio de motivo.')
        self.assertEqual(len(informe_pc._STORE.load()['informes']), 1)

    def test_reeditar_no_borra_el_sello_de_impresion(self):
        iid = informe_pc.guardar(_base())
        informe_pc.marcar_impreso(iid, 'recepcion')
        sello = informe_pc.obtener(iid)['impreso']
        self.assertTrue(sello)
        informe_pc.guardar(_base(id=iid, motivo_consulta='Corregido.'))
        item = informe_pc.obtener(iid)
        self.assertEqual(item['impreso'], sello)
        self.assertEqual(item['impreso_por'], 'recepcion')

    def test_un_informe_nuevo_nace_sin_imprimir(self):
        iid = informe_pc.guardar(_base())
        self.assertIsNone(informe_pc.obtener(iid)['impreso'])

    def test_solo_pendientes_deja_de_incluirlo_tras_marcar_impreso(self):
        iid = informe_pc.guardar(_base())
        self.assertIn(iid, [i['id'] for i in
                            informe_pc.listar(fecha=FECHA, solo_pendientes=True)])
        self.assertTrue(informe_pc.marcar_impreso(iid, 'ana'))
        self.assertNotIn(iid, [i['id'] for i in
                               informe_pc.listar(fecha=FECHA, solo_pendientes=True)])
        # pero sigue en la lista completa del dia
        self.assertIn(iid, [i['id'] for i in informe_pc.listar(fecha=FECHA)])

    def test_marcar_impreso_de_un_id_inexistente_devuelve_false(self):
        self.assertFalse(informe_pc.marcar_impreso('no-existe'))

    def test_listar_filtra_por_fecha(self):
        informe_pc.guardar(_base())
        informe_pc.guardar(_base(fecha='2026-08-19'))
        self.assertEqual(len(informe_pc.listar(fecha=FECHA)), 1)
        self.assertEqual(len(informe_pc.listar(fecha='2026-08-19')), 1)

    def test_podar_saca_los_viejos(self):
        informe_pc.guardar(_base(fecha='2000-01-01'))
        informe_pc.guardar(_base())
        self.assertEqual(informe_pc.podar(dias=365), 1)
        self.assertEqual(len(informe_pc._STORE.load()['informes']), 1)


class TestMenorVsAdulto(unittest.TestCase):
    """El corte esta en los 18 anios."""

    def test_el_corte_declarado_es_18(self):
        self.assertEqual(informe_pc.EDAD_ADULTO, 18)

    def test_menor_usa_psq_y_el_fairest_6(self):
        doc = informe_pc.armar_documento(_base(edad=10))
        self.assertFalse(doc['tamizaje']['adulto'])
        self.assertEqual(doc['tamizaje']['cuestionario']['tipo'], 'PSQ-CL')
        self.assertEqual(doc['tamizaje']['fairest']['instrumento'], 'FAIREST-6')
        self.assertEqual(len(doc['tamizaje']['fairest']['items']), 6)

    def test_a_los_17_todavia_es_menor(self):
        doc = informe_pc.armar_documento(_base(edad=17))
        self.assertFalse(doc['tamizaje']['adulto'])
        self.assertEqual(doc['tamizaje']['cuestionario']['tipo'], 'PSQ-CL')

    def test_a_los_18_ya_es_adulto(self):
        doc = informe_pc.armar_documento(_base(edad=18))
        self.assertTrue(doc['tamizaje']['adulto'])
        self.assertEqual(doc['tamizaje']['cuestionario']['tipo'], 'STOP-BANG')
        self.assertEqual(doc['tamizaje']['fairest']['instrumento'], 'FAIREST 6+4')
        self.assertEqual(len(doc['tamizaje']['fairest']['items']), 10)

    def test_las_especialidades_sugeridas_cambian_con_la_edad(self):
        menor = informe_pc.armar_documento(_base(edad=10))['tamizaje']['especialidades']
        adulto = informe_pc.armar_documento(_base(edad=40))['tamizaje']['especialidades']
        self.assertIn('Broncopulmonar pediatrico', menor)
        self.assertIn('Medicina del sueño', adulto)

    def test_sin_edad_no_se_asume_adulto(self):
        doc = informe_pc.armar_documento(_base(edad=''))
        self.assertIsNone(doc['paciente']['edad'])
        self.assertFalse(doc['tamizaje']['adulto'])

    def test_la_edad_se_puede_calcular_de_la_fecha_de_nacimiento(self):
        doc = informe_pc.armar_documento(_base(edad='', fecha_nacimiento='2000-01-01'))
        self.assertIsNotNone(doc['paciente']['edad'])
        self.assertTrue(doc['tamizaje']['adulto'])


class TestNoRequiereTratamiento(unittest.TestCase):
    """Al que no necesita tratamiento no se le ofrece el Estudio Integral."""

    def test_con_no_requiere_el_bloque_del_estudio_viene_vacio(self):
        doc = informe_pc.armar_documento(_base(conclusion='no_requiere'))
        self.assertEqual(doc['que_aporta_estudio'], '')
        self.assertEqual(doc['conclusion']['clave'], 'no_requiere')

    def test_con_cualquier_otra_conclusion_el_bloque_si_viene(self):
        for clave, _, _ in informe_pc.CONCLUSIONES:
            if clave == 'no_requiere':
                continue
            with self.subTest(conclusion=clave):
                doc = informe_pc.armar_documento(_base(conclusion=clave))
                self.assertEqual(doc['que_aporta_estudio'], informe_pc.QUE_APORTA_ESTUDIO)

    def test_el_control_de_evolucion_rellena_los_meses(self):
        doc = informe_pc.armar_documento(_base(conclusion='control_evolucion',
                                               meses_control=9))
        self.assertIn('9 meses', doc['conclusion']['texto'])
        self.assertNotIn('{meses}', doc['conclusion']['texto'])

    def test_sin_conclusion_no_se_inventa_ninguna(self):
        doc = informe_pc.armar_documento(_base(conclusion=''))
        self.assertIsNone(doc['conclusion'])


class TestMediciones(unittest.TestCase):

    def test_una_medicion_con_referencia_trae_percentil_y_curva(self):
        doc = informe_pc.armar_documento(
            _base(edad=13, sexo='M', mediciones={'intermolar_maxilar': 50.0}))
        fila = doc['mediciones']['transversales'][0]
        self.assertIsNotNone(fila['percentil'])
        self.assertEqual(fila['lectura'], 'bajo el promedio')
        self.assertTrue(fila['svg'].startswith('<svg'))

    def test_sin_referencia_no_se_informa_como_dentro_del_promedio(self):
        # Bajo los 3 anios no hay tabla. Decir "dentro del promedio" ahi seria
        # inventar, y es la clase de error que nadie revisa.
        doc = informe_pc.armar_documento(
            _base(edad=2, sexo='M', mediciones={'intermolar_maxilar': 32.0}))
        fila = doc['mediciones']['transversales'][0]
        self.assertIsNone(fila['percentil'])
        self.assertEqual(fila['lectura'], 'sin referencia para esta edad')
        self.assertTrue(fila['motivo_sin_referencia'])
        self.assertNotIn('svg', fila)

    def test_sin_edad_ninguna_medicion_recibe_percentil(self):
        doc = informe_pc.armar_documento(
            _base(edad='', mediciones={'intercanino_maxilar': 33.0}))
        fila = doc['mediciones']['transversales'][0]
        self.assertIsNone(fila['percentil'])
        self.assertEqual(fila['lectura'], 'sin referencia para esta edad')

    def test_las_mediciones_vacias_no_aparecen(self):
        doc = informe_pc.armar_documento(
            _base(edad=13, mediciones={'intermolar_maxilar': 50.0,
                                       'intercanino_maxilar': '', 'resalte': 4}))
        claves = [f['clave'] for f in doc['mediciones']['transversales']]
        self.assertEqual(claves, ['intermolar_maxilar'])
        self.assertEqual([f['clave'] for f in doc['mediciones']['simples']], ['resalte'])

    def test_la_nota_y_la_cita_de_la_fuente_van_siempre(self):
        doc = informe_pc.armar_documento(_base())
        self.assertIn('orientativos', doc['mediciones']['nota'])
        self.assertIn('Bishara', doc['mediciones']['cita'])
        self.assertIn('15 hombres', doc['mediciones']['nota_muestra'])


class TestTamizaje(unittest.TestCase):

    def test_sin_psq_respondido_el_documento_lo_dice(self):
        doc = informe_pc.armar_documento(_base(edad=10))
        cuest = doc['tamizaje']['cuestionario']
        self.assertIsNone(cuest['resultado'])
        self.assertIn('no ha sido respondido', cuest['lectura'])
        # y no se asume "sin riesgo": la lectura no afirma nada del riesgo
        self.assertNotIn('bajo el corte', cuest['lectura'])

    def test_con_psq_bajo_el_corte_se_informa_el_puntaje(self):
        doc = informe_pc.armar_documento(
            _base(edad=10, tamizaje={'psq': {'puntaje': 0.1, 'riesgo_alto': False}}))
        self.assertIn('bajo el corte', doc['tamizaje']['cuestionario']['lectura'])
        self.assertFalse(doc['tamizaje']['derivar'])

    def test_con_psq_sobre_el_corte_se_deriva(self):
        doc = informe_pc.armar_documento(
            _base(edad=10, tamizaje={'psq': {'puntaje': 0.4, 'riesgo_alto': True}}))
        self.assertIn('sobre el corte', doc['tamizaje']['cuestionario']['lectura'])
        self.assertTrue(doc['tamizaje']['derivar'])

    def test_el_item_6_se_puntua_con_la_medicion_del_escaneo(self):
        doc = informe_pc.armar_documento(
            _base(edad=13, sexo='M', mediciones={'intermolar_maxilar': 50.0}))
        fila = next(f for f in doc['tamizaje']['fairest']['items']
                    if f['clave'] == 'paladar_estrecho')
        self.assertTrue(fila['positivo'])
        self.assertEqual(fila['origen'], 'intermolar')

    def test_el_criterio_del_item_6_aparece_en_el_documento(self):
        doc = informe_pc.armar_documento(_base())
        self.assertIn('criterio objetivo', doc['tamizaje']['fairest']['item6_criterio'])

    def test_el_stopbang_de_un_adulto_sin_datos_queda_incompleto(self):
        doc = informe_pc.armar_documento(_base(edad=40))
        res = doc['tamizaje']['cuestionario']['resultado']
        self.assertTrue(res['incompleto'])
        self.assertEqual(res['puntaje'], 0)

    def test_el_texto_legal_va_en_el_tamizaje(self):
        doc = informe_pc.armar_documento(_base())
        self.assertEqual(doc['tamizaje']['texto_legal'], fairest.TEXTO_LEGAL)


class TestHallazgos(unittest.TestCase):

    def test_sin_hallazgos_produce_lista_vacia_y_la_marca(self):
        doc = informe_pc.armar_documento(_base(sin_hallazgos=True,
                                               hallazgos=['apinamiento']))
        self.assertEqual(doc['hallazgos'], [])
        self.assertTrue(doc['sin_hallazgos'])
        self.assertTrue(doc['sin_hallazgos_texto'])

    def test_sin_marcar_nada_tambien_es_sin_hallazgos(self):
        doc = informe_pc.armar_documento(_base())
        self.assertEqual(doc['hallazgos'], [])
        self.assertTrue(doc['sin_hallazgos'])

    def test_los_hallazgos_traen_texto_y_relevancia(self):
        doc = informe_pc.armar_documento(_base(hallazgos=['apinamiento', 'mordida_cruzada']))
        self.assertFalse(doc['sin_hallazgos'])
        self.assertEqual([h['clave'] for h in doc['hallazgos']],
                         ['apinamiento', 'mordida_cruzada'])
        for h in doc['hallazgos']:
            self.assertTrue(h['texto'])
            self.assertTrue(h['relevancia'])

    def test_una_clave_desconocida_se_ignora_en_vez_de_reventar(self):
        doc = informe_pc.armar_documento(_base(hallazgos=['apinamiento', 'no_existe']))
        self.assertEqual([h['clave'] for h in doc['hallazgos']], ['apinamiento'])

    def test_las_ordenes_desconocidas_tambien_se_ignoran(self):
        doc = informe_pc.armar_documento(_base(ordenes=['rx_panoramica', 'inventada']))
        self.assertEqual([o['clave'] for o in doc['ordenes']], ['rx_panoramica'])

    def test_el_plan_de_accion_descarta_las_filas_vacias(self):
        doc = informe_pc.armar_documento(
            _base(plan_accion=[{'accion': 'Higiene con su dentista'}, {'accion': ''}, {}]))
        self.assertEqual(len(doc['plan_accion']), 1)


class TestFrasesProhibidas(unittest.TestCase):
    """La prueba mas importante del archivo: TODO lo que se imprime lleva la
    firma del doctor. La AAO lista "recomendar expansion palatina por apnea"
    entre lo que un ortodoncista NO debe hacer."""

    def _todo_el_texto(self, obj):
        return ' \n '.join(_textos(obj))

    def test_el_catalogo_completo_esta_limpio(self):
        texto = self._todo_el_texto(informe_pc.catalogo())
        self.assertEqual(fairest.frases_prohibidas_en(texto), [])

    def test_los_textos_fijos_del_modulo_estan_limpios(self):
        texto = ' '.join([informe_pc.QUE_APORTA_ESTUDIO, informe_pc.DISCLAIMER,
                          informe_pc.NOTA_MEDICIONES, informe_pc.TEXTO_ORDEN,
                          informe_pc.SIN_HALLAZGOS[2], informe_pc.TEXTO_LINK_ESTUDIO] +
                         [t for _, t in informe_pc.CATALOGO_EVALUACION])
        self.assertEqual(fairest.frases_prohibidas_en(texto), [])

    def test_el_documento_armado_esta_limpio_en_todas_sus_variantes(self):
        variantes = [
            _base(edad=10, mediciones={'intermolar_maxilar': 50.0},
                  hallazgos=['arcada_estrecha', 'respiracion_bucal'],
                  ordenes=['rx_panoramica', 'cbct'],
                  tamizaje={'psq': {'puntaje': 0.4, 'riesgo_alto': True},
                            'fairest': {'respiracion_bucal': 'si'}}),
            _base(edad=40, conclusion='no_requiere', sin_hallazgos=True,
                  mediciones={'intercanino_maxilar': 33.0, 'resalte': 5},
                  tamizaje={'stopbang': {'ronquido': 'si', 'cansancio': 'si',
                                         'apneas': 'si', 'cuello': 44, 'sexo': 'M'},
                            'fairest': {'amigdalas': '76-100', 'friedman': 4}}),
            _base(edad=6, conclusion='control_evolucion', meses_control=12,
                  mediciones={'intermolar_maxilar': 45.0, 'intercanino_maxilar': 29.0},
                  hallazgos=list(informe_pc.HALLAZGOS)),
        ]
        for i, item in enumerate(variantes):
            with self.subTest(variante=i):
                texto = self._todo_el_texto(informe_pc.armar_documento(item))
                self.assertEqual(fairest.frases_prohibidas_en(texto), [])

    def test_el_detector_si_pescaria_una_frase_metida_en_el_documento(self):
        # Control negativo: sin esto, la prueba de arriba pasaria aunque el
        # detector estuviera roto.
        doc = informe_pc.armar_documento(
            _base(plan_accion=[{'accion': 'La expansión cura la apnea del paciente.'}]))
        self.assertTrue(fairest.frases_prohibidas_en(self._todo_el_texto(doc)))


class TestDocumentoCompleto(unittest.TestCase):

    def test_trae_las_piezas_de_las_tres_hojas(self):
        iid = informe_pc.guardar(_base(hallazgos=['apinamiento'],
                                       ordenes=['rx_panoramica']))
        doc = informe_pc.armar_documento(informe_pc.obtener(iid),
                                         doctor={'nombre': 'Dr. Prueba'},
                                         clinica={'nombre': 'Clinica'})
        self.assertEqual(doc['id'], iid)
        self.assertEqual(doc['fecha'], FECHA)
        self.assertEqual(doc['fecha_legible'], '20 de agosto de 2026')
        self.assertEqual(doc['doctor']['nombre'], 'Dr. Prueba')
        self.assertTrue(doc['evaluacion_realizada'])     # hoja 1
        self.assertTrue(doc['tamizaje'])                 # hoja 2
        self.assertTrue(doc['ordenes'])                  # hoja 3
        self.assertTrue(doc['disclaimer'])

    def test_una_fecha_invalida_no_revienta_el_armado(self):
        doc = informe_pc.armar_documento(_base(fecha='20-08-2026'))
        self.assertTrue(doc['fecha_legible'])

    def test_fecha_legible_en_castellano(self):
        from datetime import date
        self.assertEqual(informe_pc.fecha_legible(date(2026, 1, 1)),
                         '1 de enero de 2026')


class TestMedicionesPrevias(unittest.TestCase):
    """El seguimiento: a partir del segundo informe la curva muestra la
    trayectoria del paciente."""

    def setUp(self):
        _limpiar_registro()

    def _guardar(self, edad, mm, tramo='molar_permanente', rut='11.111.111-1', fecha=None):
        return informe_pc.guardar({
            'rut': rut, 'nombre': 'Seguimiento', 'sexo': 'M', 'edad': edad,
            'conclusion': 'corresponde', 'fecha': fecha or '2020-01-01',
            'mediciones': {'intermolar_maxilar': mm, 'tramo_intermolar': tramo}})

    def test_devuelve_las_previas_ordenadas_por_edad(self):
        self._guardar(11.5, 48.1)
        self._guardar(8.2, 46.0)
        self._guardar(10.0, 47.2)
        hoy = self._guardar(13.0, 49.0)
        previas = informe_pc.mediciones_previas('11111111-1', 'intermolar_maxilar',
                                                'molar_permanente', excluir_id=hoy)
        self.assertEqual(previas, [(8.2, 46.0), (10.0, 47.2), (11.5, 48.1)])

    def test_no_se_incluye_a_si_mismo(self):
        hoy = self._guardar(13.0, 49.0)
        self.assertEqual(informe_pc.mediciones_previas('11111111-1', 'intermolar_maxilar',
                                                       'molar_permanente', excluir_id=hoy), [])

    def test_el_historico_atraviesa_el_recambio(self):
        # La curva de Bishara mide el diente que el paciente tiene a cada edad,
        # asi que la trayectoria tiene que atravesar el recambio igual: cortarla
        # escondia justo la parte que interesa mirar.
        self._guardar(5.0, 43.0, tramo='molar_temporal')
        self._guardar(8.2, 46.0)
        hoy = self._guardar(13.0, 49.0)
        previas = informe_pc.mediciones_previas('11111111-1', 'intermolar_maxilar',
                                                'molar_permanente', excluir_id=hoy)
        self.assertEqual(previas, [(5.0, 43.0), (8.2, 46.0)])

    def test_no_se_mezclan_pacientes(self):
        self._guardar(8.0, 46.0, rut='22.222.222-2')
        hoy = self._guardar(13.0, 49.0)
        self.assertEqual(informe_pc.mediciones_previas('11111111-1', 'intermolar_maxilar',
                                                       'molar_permanente', excluir_id=hoy), [])

    def test_un_informe_sin_edad_no_se_puede_graficar(self):
        # Sin edad no hay eje X. Suponerle una seria inventar el punto.
        informe_pc.guardar({'rut': '11.111.111-1', 'nombre': 'X', 'sexo': 'M',
                            'conclusion': 'corresponde', 'fecha': '2020-01-01',
                            'mediciones': {'intermolar_maxilar': 46.0,
                                           'tramo_intermolar': 'molar_permanente'}})
        hoy = self._guardar(13.0, 49.0)
        self.assertEqual(informe_pc.mediciones_previas('11111111-1', 'intermolar_maxilar',
                                                       'molar_permanente', excluir_id=hoy), [])

    def test_el_documento_lleva_la_cuenta_de_las_previas(self):
        self._guardar(8.2, 46.0)
        self._guardar(10.0, 47.2)
        hoy = self._guardar(13.0, 49.0)
        doc = informe_pc.armar_documento(informe_pc.obtener(hoy))
        fila = [m for m in doc['mediciones']['transversales']
                if m['clave'] == 'intermolar_maxilar'][0]
        self.assertEqual(fila['mediciones_previas'], 2)


class TestFilasDeLaTabla(unittest.TestCase):
    """Que va y que NO va en la tabla del informe impreso."""

    def setUp(self):
        _limpiar_registro()

    def _doc(self, **med):
        base = {'intermolar_maxilar': 49.0, 'intercanino_maxilar': 32.0,
                'tramo_intermolar': 'molar_permanente'}
        base.update(med)
        return informe_pc.armar_documento({
            'rut': '11.111.111-1', 'nombre': 'X', 'sexo': 'M', 'edad': 13,
            'conclusion': 'corresponde', 'mediciones': base})

    def test_los_anchos_transversales_no_van_en_la_tabla(self):
        # Decision del usuario: el grafico basta. Repetir el milimetraje arriba
        # de la curva hacia que los dos compitieran por la atencion.
        doc = self._doc()
        claves = [f['clave'] for f in doc['mediciones']['simples']]
        self.assertNotIn('intermolar_maxilar', claves)
        self.assertNotIn('intercanino_maxilar', claves)
        # pero si siguen estando como grafico
        self.assertEqual(len(doc['mediciones']['transversales']), 2)

    def test_la_linea_media_dice_hacia_que_lado(self):
        doc = self._doc(linea_media=2.0, linea_media_lado='der')
        fila = [f for f in doc['mediciones']['simples'] if f['clave'] == 'linea_media'][0]
        self.assertIn('derecha', fila['unidad'])

    def test_la_linea_media_sin_lado_no_inventa_uno(self):
        doc = self._doc(linea_media=2.0)
        fila = [f for f in doc['mediciones']['simples'] if f['clave'] == 'linea_media'][0]
        self.assertEqual(fila['unidad'], 'mm')

    def test_la_relacion_va_como_regla_y_no_como_fila_de_tabla(self):
        doc = self._doc(clase_molar_der='II-3/4', clase_molar_izq='I')
        claves = [f['clave'] for f in doc['mediciones']['simples']]
        self.assertNotIn('clase_molar', claves)
        reglas = doc['mediciones']['oclusion']
        self.assertEqual([r['clave'] for r in reglas], ['clase_molar'])
        self.assertTrue(reglas[0]['svg'].startswith('<svg'))
        # Un lado Clase I y el otro no tiene nombre propio en Angle: subdivisión,
        # y nombra el lado que NO es Clase I.
        self.assertEqual(reglas[0]['texto'], 'Clase II ¾ cúspide subdivisión derecha')

    def test_la_regla_marca_los_dos_lados(self):
        doc = self._doc(clase_canina_der='III-1/2', clase_canina_izq='II-1/4')
        svg = doc['mediciones']['oclusion'][0]['svg']
        self.assertIn('>Der<', svg)
        self.assertIn('>Izq<', svg)

    def test_un_lado_solo_igual_dibuja_la_regla(self):
        doc = self._doc(clase_canina_izq='III-1/4')
        reglas = doc['mediciones']['oclusion']
        self.assertEqual(len(reglas), 1)
        self.assertIn('>Izq<', reglas[0]['svg'])
        self.assertNotIn('>Der<', reglas[0]['svg'])

    def test_un_valor_fuera_de_la_escala_se_ignora(self):
        # Basura en el registro no puede dibujar una marca en un punto inventado.
        doc = self._doc(clase_molar_der='Clase IV')
        self.assertEqual(doc['mediciones']['oclusion'], [])

    def test_sin_relacion_registrada_no_se_dibuja_regla(self):
        # Una regla vacia se leeria como "medido y normal", que no es lo mismo
        # que "no registrado".
        self.assertEqual(self._doc()['mediciones']['oclusion'], [])


class TestPuntuarTamizaje(unittest.TestCase):
    """El formulario muestra el puntaje mientras se llena, y sale del MISMO
    codigo que imprime el papel. Si esto se separa, el Dr. ve un numero en
    pantalla y el paciente se lleva otro."""

    def test_devuelve_lo_mismo_que_el_documento(self):
        item = _base(edad=10, sexo='M',
                     mediciones={'intermolar_maxilar': 45.0},
                     tamizaje={'fairest': {'respiracion_bucal': 'si',
                                           'tension_mentoniano': 'no'}})
        self.assertEqual(informe_pc.puntuar_tamizaje(item),
                         informe_pc.armar_documento(item)['tamizaje'])

    def test_no_guarda_nada(self):
        _limpiar_registro()
        informe_pc.puntuar_tamizaje(_base(edad=10, sexo='M'))
        self.assertEqual(informe_pc.listar(), [])

    def test_una_clave_ausente_es_no_evaluado_y_no_un_negativo(self):
        # Es lo que distingue el formulario de tres estados: no marcar nada no
        # es lo mismo que marcar "No".
        t = informe_pc.puntuar_tamizaje(
            _base(edad=10, sexo='M', tamizaje={'fairest': {'respiracion_bucal': 'si'}}))
        fa = t['fairest']
        self.assertEqual(fa['puntaje_6'], 1)
        self.assertIn('tension_mentoniano', fa['sin_registrar'])
        self.assertIn('desgaste_dentario', fa['sin_registrar'])

    def test_marcar_no_si_cuenta_como_evaluado(self):
        t = informe_pc.puntuar_tamizaje(
            _base(edad=10, sexo='M',
                  tamizaje={'fairest': {'respiracion_bucal': 'no',
                                        'tension_mentoniano': 'no',
                                        'amigdalas': '0-25', 'anquiloglosia': '1',
                                        'desgaste_dentario': 'no'},
                            }, mediciones={'intermolar_maxilar': 52.0}))
        fa = t['fairest']
        self.assertEqual(fa['puntaje_6'], 0)
        self.assertEqual(fa['sin_registrar'], [])

    def test_el_stopbang_calcula_el_imc_desde_peso_y_talla(self):
        # El formulario pide peso y talla (que el paciente sabe), no el IMC.
        t = informe_pc.puntuar_tamizaje(
            _base(edad=45, sexo='M',
                  tamizaje={'stopbang': {'peso': 120, 'talla': 170,
                                         'edad': 45, 'sexo': 'M'}}))
        imc = [i for i in t['cuestionario']['resultado']['items'] if i['clave'] == 'imc'][0]
        self.assertTrue(imc['registrado'])
        self.assertTrue(imc['positivo'])      # 41,5 > 35

    def test_el_menor_usa_psq_y_el_adulto_stopbang(self):
        self.assertEqual(
            informe_pc.puntuar_tamizaje(_base(edad=17, sexo='M'))['cuestionario']['tipo'],
            'PSQ-CL')
        self.assertEqual(
            informe_pc.puntuar_tamizaje(_base(edad=18, sexo='M'))['cuestionario']['tipo'],
            'STOP-BANG')


class TestFraseDeAngle(unittest.TestCase):
    """La frase canónica sale sola del registro por lado. Es la prueba de que
    guardar cada lado por separado es lo correcto: la asimetría tiene nombre
    propio en la nomenclatura de Angle."""

    def f(self, der, izq):
        return informe_pc.frase_relacion('Relación molar', der, izq)

    def test_subdivision_nombra_el_lado_que_no_es_clase_i(self):
        self.assertEqual(self.f('II-completa', 'I'), 'Clase II completa subdivisión derecha')
        self.assertEqual(self.f('I', 'III-completa'), 'Clase III completa subdivisión izquierda')

    def test_los_dos_lados_iguales_es_bilateral(self):
        self.assertEqual(self.f('II-1/2', 'II-1/2'), 'Clase II ½ cúspide bilateral')

    def test_clase_i_en_ambos_lados_no_dice_bilateral(self):
        self.assertEqual(self.f('I', 'I'), 'Clase I')

    def test_dos_lados_distintos_y_ninguno_clase_i_se_describen_aparte(self):
        t = self.f('III-3/4', 'II-1/4')
        self.assertIn('Derecha: Clase III ¾ cúspide', t)
        self.assertIn('Izquierda: Clase II ¼ cúspide', t)

    def test_no_registrable_no_es_clase_i(self):
        t = self.f('II-completa', 'no_registrable')
        self.assertIn('Clase II completa', t)
        self.assertIn('no registrable', t)
        # y no dibuja marca en la regla
        svg = informe_pc.regla_oclusion_svg('x', 'II-completa', 'no_registrable')
        self.assertIn('>Der<', svg)
        self.assertNotIn('>Izq<', svg)

    def test_sin_ningun_lado_medible_no_hay_regla(self):
        self.assertIsNone(informe_pc.regla_oclusion_svg('x', 'no_registrable', 'no_registrable'))

    def test_el_sinonimo_entre_parentesis_no_va_en_la_frase(self):
        self.assertNotIn('(cúspide a cúspide)', self.f('II-1/2', 'I'))

    def test_la_escala_esta_en_cuartos_y_es_simetrica(self):
        pos = [c for _, _, c in informe_pc.RELACIONES if c is not None]
        self.assertEqual(pos, sorted(pos))
        self.assertEqual(min(pos), -informe_pc.CUARTOS_MAX)
        self.assertEqual(max(pos), informe_pc.CUARTOS_MAX)
        self.assertIn(0, pos)


def _png(color=b'\x00\x00\xff'):
    """Un PNG minimo real, como dataURL. No se usa Pillow: en produccion no
    existe, y estas pruebas tienen que correr igual que alla."""
    import base64
    import struct
    import zlib

    def trozo(tipo, datos):
        c = tipo + datos
        return struct.pack('>I', len(datos)) + c + struct.pack('>I', zlib.crc32(c))

    cab = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b'\x00' + color)
    crudo = (b'\x89PNG\r\n\x1a\n' + trozo(b'IHDR', cab) +
             trozo(b'IDAT', idat) + trozo(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(crudo).decode()


class TestImagenes(unittest.TestCase):
    """Fotos clinicas anexadas al informe. Se guardan como ARCHIVOS: meterlas
    en el JSON del registro haria que cada guardado arrastre megabytes."""

    def setUp(self):
        _limpiar_registro()
        self.iid = informe_pc.guardar(_base())

    def test_agregar_deja_el_archivo_en_disco_y_la_ficha_en_el_registro(self):
        r = informe_pc.agregar_imagen(self.iid, _png(), _png(), 'Intraoral frontal')
        self.assertTrue(r['ok'], r)
        img = r['imagen']
        self.assertEqual(img['titulo'], 'Intraoral frontal')
        self.assertTrue((informe_pc.IMAGENES_DIR / img['archivo']).exists())
        self.assertTrue((informe_pc.IMAGENES_DIR / img['thumb']).exists())
        self.assertEqual(len(informe_pc.obtener(self.iid)['imagenes']), 1)

    def test_el_registro_guarda_el_nombre_del_archivo_y_no_la_imagen(self):
        informe_pc.agregar_imagen(self.iid, _png(), _png())
        crudo = json.dumps(informe_pc.obtener(self.iid))
        self.assertNotIn('base64', crudo)
        self.assertLess(len(crudo), 4000)

    def test_topa_en_el_maximo(self):
        for _ in range(informe_pc.MAX_IMAGENES):
            self.assertTrue(informe_pc.agregar_imagen(self.iid, _png(), _png())['ok'])
        r = informe_pc.agregar_imagen(self.iid, _png(), _png())
        self.assertFalse(r['ok'])
        self.assertIn('aximo', r['error'])

    def test_rechaza_lo_que_no_sea_una_imagen_de_navegador(self):
        for basura in ('data:application/pdf;base64,QQ==', 'http://x/y.jpg', '', None,
                       'data:image/svg+xml;base64,QQ=='):
            with self.subTest(basura=basura):
                self.assertFalse(informe_pc.agregar_imagen(self.iid, basura, _png())['ok'])

    def test_a_un_informe_que_no_existe_no_se_le_agrega_nada(self):
        self.assertFalse(informe_pc.agregar_imagen('noexiste', _png(), _png())['ok'])

    def test_borrar_saca_la_ficha_y_los_archivos(self):
        img = informe_pc.agregar_imagen(self.iid, _png(), _png())['imagen']
        self.assertTrue(informe_pc.borrar_imagen(self.iid, img['archivo']))
        self.assertEqual(informe_pc.obtener(self.iid)['imagenes'], [])
        self.assertFalse((informe_pc.IMAGENES_DIR / img['archivo']).exists())
        self.assertFalse((informe_pc.IMAGENES_DIR / img['thumb']).exists())

    def test_un_nombre_con_traversal_no_lee_nada(self):
        # La guarda que impide que un nombre de archivo llegado de afuera saque
        # cualquier archivo del servidor.
        for malo in ('../informe_pc.py', '..\\informe_pc.py', '/etc/passwd',
                     '.oculto', 'sub/dir.jpg', ''):
            with self.subTest(malo=malo):
                self.assertEqual(informe_pc.imagen_data_uri(malo), '')

    def test_guardar_el_informe_no_pierde_las_imagenes(self):
        # El formulario manda titulos, no imagenes: si el guardado tomara la
        # lista del formulario, un guardado normal las borraria todas.
        img = informe_pc.agregar_imagen(self.iid, _png(), _png(), 'Perfil')['imagen']
        informe_pc.guardar({'id': self.iid, 'rut': '1-9', 'nombre': 'X',
                            'conclusion': 'corresponde'})
        self.assertEqual(len(informe_pc.obtener(self.iid)['imagenes']), 1)
        self.assertEqual(informe_pc.obtener(self.iid)['imagenes'][0]['archivo'], img['archivo'])

    def test_el_titulo_se_puede_cambiar_desde_el_formulario(self):
        img = informe_pc.agregar_imagen(self.iid, _png(), _png(), 'viejo')['imagen']
        informe_pc.guardar({'id': self.iid, 'rut': '1-9', 'nombre': 'X',
                            'conclusion': 'corresponde',
                            'titulos_imagenes': [{'archivo': img['archivo'], 'titulo': 'nuevo'}]})
        self.assertEqual(informe_pc.obtener(self.iid)['imagenes'][0]['titulo'], 'nuevo')

    def test_el_documento_trae_las_imagenes_embebidas(self):
        informe_pc.agregar_imagen(self.iid, _png(), _png(), 'Intraoral')
        doc = informe_pc.armar_documento(informe_pc.obtener(self.iid))
        self.assertEqual(len(doc['imagenes']), 1)
        self.assertTrue(doc['imagenes'][0]['src'].startswith('data:image/'))
        self.assertEqual(doc['imagenes'][0]['titulo'], 'Intraoral')

    def test_sin_imagenes_el_documento_trae_la_lista_vacia(self):
        self.assertEqual(informe_pc.armar_documento(informe_pc.obtener(self.iid))['imagenes'], [])


class TestEdicion(unittest.TestCase):
    """Reabrir un informe guardado y modificarlo."""

    def setUp(self):
        _limpiar_registro()

    def test_editar_conserva_creado_y_no_duplica(self):
        iid = informe_pc.guardar(_base(motivo_consulta='primero'))
        creado = informe_pc.obtener(iid)['creado']
        informe_pc.guardar(_base(id=iid, motivo_consulta='corregido'))
        self.assertEqual(len(informe_pc.listar(fecha=FECHA)), 1)
        self.assertEqual(informe_pc.obtener(iid)['creado'], creado)
        self.assertEqual(informe_pc.obtener(iid)['motivo_consulta'], 'corregido')

    def test_editar_algo_ya_impreso_queda_marcado_para_reimprimir(self):
        # El papel que tiene el paciente quedo desactualizado, y recepcion tiene
        # que enterarse sin tener que acordarse.
        iid = informe_pc.guardar(_base())
        informe_pc.marcar_impreso(iid)
        informe_pc.guardar(_base(id=iid, motivo_consulta='corregido'))
        it = informe_pc.obtener(iid)
        self.assertTrue(it.get('editado_tras_imprimir'))
        self.assertTrue(it['impreso'], 'la marca de impreso no se borra: paso por la impresora')

    def test_editar_algo_no_impreso_no_marca_nada(self):
        iid = informe_pc.guardar(_base())
        informe_pc.guardar(_base(id=iid, motivo_consulta='corregido'))
        self.assertIsNone(informe_pc.obtener(iid).get('editado_tras_imprimir'))


class TestEvaluacionRealizada(unittest.TestCase):
    """El informe no puede afirmar que se hizo algo que no se hizo."""

    def test_solo_sale_lo_marcado(self):
        doc = informe_pc.armar_documento(_base(evaluacion=['escaneo', 'examen']))
        self.assertEqual(len(doc['evaluacion_realizada']), 2)
        self.assertTrue(any('Escaneo' in x for x in doc['evaluacion_realizada']))
        self.assertFalse(any('Tamizaje' in x for x in doc['evaluacion_realizada']))

    def test_respeta_el_orden_del_catalogo(self):
        # Da igual en que orden lleguen: en el papel van siempre igual.
        doc = informe_pc.armar_documento(_base(evaluacion=['antecedentes', 'escaneo', 'facial']))
        self.assertEqual(doc['evaluacion_realizada'][0], informe_pc.EVALUACION_MAP['escaneo'])

    def test_el_campo_libre_va_al_final(self):
        doc = informe_pc.armar_documento(_base(evaluacion=['escaneo'],
                                               evaluacion_otros='Revisión del CBCT que trajo'))
        self.assertEqual(doc['evaluacion_realizada'][-1], 'Revisión del CBCT que trajo')

    def test_sin_nada_marcado_no_afirma_nada(self):
        doc = informe_pc.armar_documento(_base(evaluacion=[]))
        self.assertEqual(doc['evaluacion_realizada'], [])

    def test_un_informe_viejo_sin_la_lista_usa_el_default(self):
        # Los guardados antes de que esto fuera elegible no traen 'evaluacion'.
        doc = informe_pc.armar_documento(_base())
        self.assertEqual(len(doc['evaluacion_realizada']),
                         len(informe_pc.EVALUACION_POR_DEFECTO))

    def test_las_radiografias_y_fotos_no_vienen_por_defecto(self):
        # Dependen de que el paciente haya traido algo o de que se hayan tomado.
        self.assertNotIn('radiografias', informe_pc.EVALUACION_POR_DEFECTO)
        self.assertNotIn('fotografias', informe_pc.EVALUACION_POR_DEFECTO)


class TestOrdenesConDetalle(unittest.TestCase):
    """Una orden que dice solo 'CBCT' obliga al centro de imagenes a llamar."""

    def _ordenes(self, **ov):
        return informe_pc.armar_documento(_base(**ov))['ordenes']

    def test_el_cbct_lleva_su_alcance(self):
        o = self._ordenes(ordenes=['cbct'], ordenes_detalle={'cbct': 'Bimaxilar'})[0]
        self.assertEqual(o['precision'], 'Bimaxilar')

    def test_las_periapicales_llevan_las_piezas(self):
        o = self._ordenes(ordenes=['rx_periapical'],
                          ordenes_detalle={'rx_periapical': '1.1, 2.1, 3.6'})[0]
        self.assertEqual(o['precision'], '1.1, 2.1, 3.6')

    def test_sin_detalle_la_precision_va_vacia(self):
        self.assertEqual(self._ordenes(ordenes=['cbct'])[0]['precision'], '')

    def test_el_catalogo_declara_cuales_hay_que_precisar(self):
        self.assertIsNotNone(informe_pc.ORDENES['cbct']['precisa'])
        self.assertIsNotNone(informe_pc.ORDENES['rx_periapical']['precisa'])
        self.assertIsNone(informe_pc.ORDENES['rx_panoramica']['precisa'])

    def test_los_examenes_pedidos_existen_en_el_catalogo(self):
        for clave in ('rx_bitewing', 'cefalometria', 'rx_mano'):
            with self.subTest(clave=clave):
                self.assertIn(clave, informe_pc.ORDENES)
        # 'carpal' se renombro a 'de mano'.
        self.assertNotIn('rx_carpal', informe_pc.ORDENES)
        self.assertIn('mano', informe_pc.ORDENES['rx_mano']['etiqueta'].lower())


class TestHallazgosPersonalizados(unittest.TestCase):

    def test_se_imprimen_despues_de_los_del_catalogo(self):
        doc = informe_pc.armar_documento(_base(
            hallazgos=['apinamiento'],
            hallazgos_personalizados=[{'titulo': 'Frenillo con inserción baja',
                                       'descripcion': 'Llega hasta la papila.'}]))
        self.assertEqual(doc['hallazgos'][0]['clave'], 'apinamiento')
        self.assertEqual(doc['hallazgos'][-1]['etiqueta'], 'Frenillo con inserción baja')
        self.assertEqual(doc['hallazgos'][-1]['texto'], 'Llega hasta la papila.')

    def test_uno_sin_titulo_se_descarta(self):
        doc = informe_pc.armar_documento(_base(
            hallazgos=[], sin_hallazgos=False,
            hallazgos_personalizados=[{'titulo': '   ', 'descripcion': 'algo'}]))
        self.assertEqual(doc['hallazgos'], [])

    def test_uno_personalizado_alcanza_para_que_no_diga_sin_hallazgos(self):
        doc = informe_pc.armar_documento(_base(
            hallazgos=[], hallazgos_personalizados=[{'titulo': 'Algo', 'descripcion': ''}]))
        self.assertFalse(doc['sin_hallazgos'])


class TestQrYLinkDelEstudio(unittest.TestCase):

    def test_el_qr_es_un_data_uri(self):
        q = informe_pc.qr_data_uri('https://ejemplo.cl/x')
        self.assertTrue(q.startswith('data:image/svg+xml;base64,'))

    def test_sin_texto_no_hay_qr(self):
        self.assertEqual(informe_pc.qr_data_uri(''), '')
        self.assertEqual(informe_pc.qr_data_uri(None), '')

    def test_el_documento_trae_el_link_guardado(self):
        link = {'url': 'https://ortodonciarichard.cl/#agendar=abc', 'qr': 'data:image/svg+xml;base64,x',
                'texto': informe_pc.TEXTO_LINK_ESTUDIO}
        doc = informe_pc.armar_documento(_base(agendar_estudio_link=link))
        self.assertEqual(doc['agendar_estudio']['url'], link['url'])

    def test_sin_link_el_documento_no_lo_inventa(self):
        self.assertIsNone(informe_pc.armar_documento(_base())['agendar_estudio'])



class TestRegistroDelPrestador(unittest.TestCase):
    """El N° del Registro Nacional de Prestadores se imprime junto a la firma
    del doctor. Vive en TRES archivos --js/main.js (el modal del sitio),
    index.html (el schema para Google) y scheduling_config.json (el informe)--
    y el 2026-08-25 quedaron rotados entre doctores: el informe de Alberto salio
    con el numero de Rodrigo.

    Paso porque la doc los listaba sueltos ("312378 / 48538 / 33401 / 40662")
    despues de enumerar los doctores en OTRO orden del que usa el schema.

    La fuente de verdad es js/main.js. Esta prueba obliga a los otros dos a
    coincidir con ella, doctor por doctor.
    """

    RAIZ = Path(__file__).resolve().parent.parent

    def _de_main_js(self):
        js = io.open(self.RAIZ / 'js' / 'main.js', encoding='utf-8').read()
        out = {}
        for m in re.finditer(r'^    ([a-z]+): \{', js, re.M):
            trozo = js[m.end():m.end() + 2500]
            r = re.search(r"registro:\s*'(\d+)'", trozo)
            n = re.search(r"name:\s*'([^']+)'", trozo)
            if r:
                out[m.group(1)] = {'registro': r.group(1), 'nombre': n.group(1) if n else ''}
        return out

    def test_main_js_los_tiene_todos(self):
        datos = self._de_main_js()
        self.assertEqual(set(datos), {'octavio', 'rodrigo', 'alberto', 'patricio'})
        for k, v in datos.items():
            self.assertRegex(v['registro'], r'^\d{4,7}$', k)

    def test_no_hay_dos_doctores_con_el_mismo_numero(self):
        """Es lo que delata un intercambio: si dos comparten numero, alguien
        copio mal."""
        regs = [v['registro'] for v in self._de_main_js().values()]
        self.assertEqual(len(regs), len(set(regs)))

    def test_el_config_del_informe_coincide(self):
        """scheduling_config.json es el que usa el papel firmado."""
        cfg = json.loads(io.open(self.RAIZ / 'admin' / 'scheduling_config.json',
                                 encoding='utf-8').read())
        doctores = cfg.get('doctores') or {}
        for key, v in self._de_main_js().items():
            d = doctores.get(key) or {}
            if not d.get('registro_prestador'):
                continue   # todavia no cargado para ese doctor: no es un error
            self.assertEqual(d['registro_prestador'], v['registro'],
                             'registro_prestador de %s no coincide con js/main.js' % key)

    def test_el_schema_del_sitio_coincide(self):
        """index.html se lo entrega a Google y a los buscadores de IA. El bloque
        de cada doctor se ubica por su NOMBRE, no por su posicion: el orden del
        JSON-LD no es el mismo que el de doctorData, y confiar en la posicion es
        exactamente el error que esta prueba existe para atajar.
        """
        html = io.open(self.RAIZ / 'index.html', encoding='utf-8').read()
        for key, v in self._de_main_js().items():
            quien = v['nombre']
            i = html.find('"name": "%s"' % quien)
            self.assertNotEqual(i, -1, 'no se encontro a %s en el schema' % quien)
            trozo = html[i:i + 4000]
            m = re.search(r'"propertyID": "Registro Superintendencia de Salud \(Chile\)",'
                          r' "value": "(\d+)"', trozo)
            self.assertIsNotNone(m, 'sin identifier de registro para %s' % quien)
            self.assertEqual(m.group(1), v['registro'],
                             'el schema de %s no coincide con js/main.js' % quien)

if __name__ == '__main__':
    unittest.main(verbosity=2)
