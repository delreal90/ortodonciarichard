"""
test_seguros.py - El prellenado nuevo de seguros (desde boleta): copia la
glosa / agrupa por patron / renombre por aseguradora.

Cero red, cero disco real: seguros.listar_prestaciones / mapeo_prestaciones /
guardar_prestacion se reemplazan a nivel de modulo por catalogos de prueba en
memoria (mismo patron de aislamiento de test_avisos.py / test_consentimientos.py).

    cd admin && python -m unittest test_seguros -v

Que prueba y por que:

  1. _norm_glosa() - la normalizacion (tildes, mayusculas, sufijo " PIEZA XXX")
     de la que dependen prestacion_por_glosa() y por lo tanto TODO el modelo
     nuevo de prellenado.
  2. filas_desde_items() con catalogo vacio - cada item de la boleta se COPIA
     tal cual (valor incluido) y AUTO-CREA su propia prestacion. Es el camino
     de "glosa nueva nunca vista".
  3. Agrupacion por PATRON (glosas_boleta) - dos glosas variantes del mismo mes
     ("...AGOSTO pieza Boca" / "...JULIO pieza Boca") deben resolver a la MISMA
     prestacion existente, sin crear una nueva por cada variante.
  4. Override por aseguradora (mapeo_prestaciones) - el nombre/codigo que ve
     cada aseguradora puede ser distinto al nombre interno; sin override, sale
     el nombre interno tal cual.
  5. filas_desde_boleta() (fallback de 1 item, glosa+monto sin desglose) sigue
     devolviendo (filas, False): con el modelo nuevo nunca es "no reconocido".
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='seguros_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['SEGUROS_ASEGURADORAS_PATH'] = str(_TMP / 'seguros_aseguradoras.json')
os.environ['SEGUROS_PRESTACIONES_PATH'] = str(_TMP / 'seguros_prestaciones.json')
os.environ['SEGUROS_MAPEO_PREST_PATH'] = str(_TMP / 'seguros_mapeo_prestaciones.json')
os.environ['SEGUROS_MAPEO_MOTIVOS_PATH'] = str(_TMP / 'seguros_mapeo_motivos.json')
os.environ['SEGUROS_PACIENTES_PATH'] = str(_TMP / 'seguros_pacientes.json')
os.environ['SEGUROS_FIRMAS_INDEX_PATH'] = str(_TMP / 'seguros_firmas.json')
os.environ['SEGUROS_REGISTRO_PATH'] = str(_TMP / 'seguros_registro.json')
os.environ['SEGUROS_AUTO_CONFIG_PATH'] = str(_TMP / 'seguros_auto_config.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import seguros  # noqa: E402


class _AislamientoCatalogo(unittest.TestCase):
    """Monkeypatch a nivel de modulo de listar_prestaciones/mapeo_prestaciones/
    guardar_prestacion, guardando y restaurando los originales para no filtrar
    estado entre tests (ninguno de estos tests toca disco real)."""

    def setUp(self):
        self._orig_listar = seguros.listar_prestaciones
        self._orig_mapeo = seguros.mapeo_prestaciones
        self._orig_guardar = seguros.guardar_prestacion
        self.catalogo = []       # lista de dicts de prestacion (con 'id')
        self.mapeo = {}          # {prest_id: {aseg_key: [{codigo,descripcion}]}}
        self.creadas = []        # lo que guardar_prestacion() crea en el test

        seguros.listar_prestaciones = lambda solo_activas=True: list(self.catalogo)
        seguros.mapeo_prestaciones = lambda: self.mapeo

        def _fake_guardar(prest_id, datos):
            if not prest_id:
                prest_id = 'p_nuevo_%d' % (len(self.creadas) + 1)
            registro = {'id': prest_id, **datos}
            self.creadas.append(registro)
            self.catalogo.append(registro)   # tambien queda "creada" en catalogo
            return prest_id

        seguros.guardar_prestacion = _fake_guardar

    def tearDown(self):
        seguros.listar_prestaciones = self._orig_listar
        seguros.mapeo_prestaciones = self._orig_mapeo
        seguros.guardar_prestacion = self._orig_guardar


# ── 1. _norm_glosa ───────────────────────────────────────────────────────────

class TestNormGlosa(unittest.TestCase):

    def test_saca_tildes_mayusculas_y_sufijo_pieza_texto(self):
        self.assertEqual(seguros._norm_glosa('Cone Beam 3D  pieza Boca'), 'CONE BEAM 3D')

    def test_saca_sufijo_pieza_numero(self):
        self.assertEqual(seguros._norm_glosa('Recementacion Bracket pieza 47'),
                         'RECEMENTACION BRACKET')

    def test_colapsa_espacios_multiples(self):
        self.assertEqual(seguros._norm_glosa('Control   Mensual    Ortodoncia'),
                         'CONTROL MENSUAL ORTODONCIA')

    def test_sin_sufijo_pieza_queda_igual_normalizado(self):
        self.assertEqual(seguros._norm_glosa('Instalación de Microtornillos'),
                         'INSTALACION DE MICROTORNILLOS')

    def test_vacio_da_vacio(self):
        self.assertEqual(seguros._norm_glosa(''), '')
        self.assertEqual(seguros._norm_glosa(None), '')


# ── 2. filas_desde_items: copia + auto-crea (catalogo vacio) ────────────────

class TestFilasDesdeItemsAutoCrea(_AislamientoCatalogo):

    def test_catalogo_vacio_copia_cada_item_y_crea_su_prestacion(self):
        items = [
            {'descripcion': 'Control Mensual de Ortodoncia Agosto', 'valor': 30000},
            {'descripcion': 'Recementacion Bracket pieza 22', 'valor': 15000},
            {'descripcion': 'Ajuste de Arco', 'valor': 5000},
        ]
        filas = seguros.filas_desde_items(items, 'zurich')

        self.assertEqual(len(filas), 3)
        self.assertEqual(len(self.creadas), 3)   # cada glosa nueva crea su prestacion

        self.assertEqual(filas[0]['descripcion'], 'Control Mensual de Ortodoncia Agosto')
        self.assertEqual(filas[0]['valor'], 30000)
        self.assertEqual(filas[1]['descripcion'], 'Recementacion Bracket pieza 22')
        self.assertEqual(filas[1]['valor'], 15000)
        self.assertEqual(filas[2]['descripcion'], 'Ajuste de Arco')
        self.assertEqual(filas[2]['valor'], 5000)

    def test_items_sin_descripcion_se_saltan_sin_fallar(self):
        items = [{'descripcion': '', 'valor': 1000}, {'descripcion': '  ', 'valor': 2000}]
        filas = seguros.filas_desde_items(items, 'zurich')
        self.assertEqual(filas, [])
        self.assertEqual(self.creadas, [])

    def test_lista_vacia_o_none_nunca_falla(self):
        self.assertEqual(seguros.filas_desde_items([], 'zurich'), [])
        self.assertEqual(seguros.filas_desde_items(None, 'zurich'), [])


# ── 3. Agrupacion por PATRON (glosas_boleta) ─────────────────────────────────

class TestAgrupacionPorPatron(_AislamientoCatalogo):

    def test_dos_glosas_del_mismo_mes_usan_la_misma_prestacion(self):
        self.catalogo = [{
            'id': 'p_control', 'nombre': 'Control Mensual de Ortodoncia',
            'glosas_boleta': ['CONTROL MENSUAL DE ORTODONCIA'],
        }]
        items = [
            {'descripcion': 'CONTROL MENSUAL DE ORTODONCIA AGOSTO pieza Boca', 'valor': 30000},
            {'descripcion': 'CONTROL MENSUAL DE ORTODONCIA JULIO pieza Boca', 'valor': 30000},
        ]
        filas = seguros.filas_desde_items(items, 'zurich')

        self.assertEqual(len(filas), 2)
        self.assertEqual(self.creadas, [])   # 0 creadas nuevas: ambas calzan por patron
        self.assertEqual(filas[0]['id'], 'p_control')
        self.assertEqual(filas[1]['id'], 'p_control')
        # sin override -> sale el nombre interno de la prestacion, no la glosa cruda
        self.assertEqual(filas[0]['descripcion'], 'Control Mensual de Ortodoncia')
        self.assertEqual(filas[1]['descripcion'], 'Control Mensual de Ortodoncia')

    def test_glosa_que_no_calza_ningun_patron_crea_una_nueva(self):
        self.catalogo = [{
            'id': 'p_control', 'nombre': 'Control Mensual de Ortodoncia',
            'glosas_boleta': ['CONTROL MENSUAL DE ORTODONCIA'],
        }]
        items = [{'descripcion': 'Cone Beam 3D pieza Boca', 'valor': 45000}]
        filas = seguros.filas_desde_items(items, 'zurich')

        self.assertEqual(len(filas), 1)
        self.assertEqual(len(self.creadas), 1)
        self.assertNotEqual(filas[0]['id'], 'p_control')


# ── 4. Override por aseguradora (mapeo_prestaciones) ─────────────────────────

class TestOverridePorAseguradora(_AislamientoCatalogo):

    def setUp(self):
        super().setUp()
        self.catalogo = [{
            'id': 'p_control', 'nombre': 'Control Mensual de Ortodoncia',
            'glosas_boleta': ['CONTROL MENSUAL'],
        }]
        self.mapeo = {
            'p_control': {'metlife': [{'codigo': 'X', 'descripcion': 'Nombre Y'}]},
        }

    def test_metlife_usa_el_override(self):
        items = [{'descripcion': 'Control Mensual Septiembre', 'valor': 30000}]
        filas = seguros.filas_desde_items(items, 'metlife')

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['descripcion'], 'Nombre Y')
        self.assertEqual(filas[0]['codigo'], 'X')
        self.assertEqual(filas[0]['valor'], 30000)
        self.assertEqual(self.creadas, [])   # ya existia por patron, no crea

    def test_otra_aseguradora_sin_override_usa_el_nombre_interno(self):
        items = [{'descripcion': 'Control Mensual Septiembre', 'valor': 30000}]
        filas = seguros.filas_desde_items(items, 'zurich')

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['descripcion'], 'Control Mensual de Ortodoncia')
        self.assertEqual(filas[0]['codigo'], '')
        self.assertEqual(filas[0]['valor'], 30000)

    def test_override_con_varios_items_el_valor_va_en_el_primero(self):
        self.mapeo['p_control']['metlife'] = [
            {'codigo': 'A', 'descripcion': 'Fila A'},
            {'codigo': 'B', 'descripcion': 'Fila B'},
        ]
        items = [{'descripcion': 'Control Mensual Septiembre', 'valor': 30000}]
        filas = seguros.filas_desde_items(items, 'metlife')

        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]['descripcion'], 'Fila A')
        self.assertEqual(filas[0]['valor'], 30000)
        self.assertEqual(filas[1]['descripcion'], 'Fila B')
        self.assertEqual(filas[1]['valor'], 0)


# ── 5. filas_desde_boleta (fallback 1 item) ──────────────────────────────────

class TestFilasDesdeBoleta(_AislamientoCatalogo):

    def test_devuelve_una_fila_con_el_monto_total_y_no_reconocido_false(self):
        filas, no_reconocido = seguros.filas_desde_boleta(
            'Control Mensual de Ortodoncia Agosto', 30000, 'zurich', fecha='2026-08-01')

        self.assertFalse(no_reconocido)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['valor'], 30000)
        self.assertEqual(filas[0]['fecha'], '2026-08-01')
        self.assertEqual(filas[0]['descripcion'], 'Control Mensual de Ortodoncia Agosto')
        self.assertEqual(len(self.creadas), 1)   # catalogo vacio -> se auto-crea


# ── 6. estado_aseguradora / asignar_si_vacio ─────────────────────────────────
# Usan seguros_pacientes.json real (aislado por SEGUROS_PACIENTES_PATH, un
# tempfile propio de este proceso de test). Se limpia el store en cada setUp
# para que las pruebas no se vean entre si.

RUT_A = '11.111.111-1'
RUT_B = '22.222.222-2'


class TestEstadoYAsignarSiVacio(unittest.TestCase):

    def setUp(self):
        seguros._save(seguros.PACIENTES_PATH, {})

    def test_sin_registro_es_sin_asignar(self):
        self.assertEqual(seguros.estado_aseguradora(RUT_A), 'sin_asignar')

    def test_sin_seguro_declarado_es_sin_seguro(self):
        seguros.guardar_paciente_seguro(RUT_A, aseguradora=seguros.SIN_SEGURO)
        self.assertEqual(seguros.estado_aseguradora(RUT_A), 'sin_seguro')

    def test_aseguradora_real_es_asignada(self):
        seguros.guardar_paciente_seguro(RUT_A, aseguradora='zurich')
        self.assertEqual(seguros.estado_aseguradora(RUT_A), 'asignada')

    def test_asignar_si_vacio_a_rut_nuevo_devuelve_true_y_asigna(self):
        ok = seguros.asignar_si_vacio(RUT_B, 'metlife')
        self.assertTrue(ok)
        self.assertEqual(seguros.estado_aseguradora(RUT_B), 'asignada')
        self.assertEqual(seguros.paciente_seguro(RUT_B)['ultima_aseguradora'], 'metlife')

    def test_asignar_si_vacio_no_pisa_una_aseguradora_ya_puesta(self):
        seguros.asignar_si_vacio(RUT_B, 'metlife')
        ok = seguros.asignar_si_vacio(RUT_B, 'zurich')
        self.assertFalse(ok)
        self.assertEqual(seguros.paciente_seguro(RUT_B)['ultima_aseguradora'], 'metlife')

    def test_asignar_si_vacio_no_pisa_sin_seguro(self):
        seguros.guardar_paciente_seguro(RUT_A, aseguradora=seguros.SIN_SEGURO)
        ok = seguros.asignar_si_vacio(RUT_A, 'zurich')
        self.assertFalse(ok)
        self.assertEqual(seguros.estado_aseguradora(RUT_A), 'sin_seguro')

    def test_asignar_si_vacio_sin_aseguradora_no_hace_nada(self):
        self.assertFalse(seguros.asignar_si_vacio(RUT_A, ''))
        self.assertFalse(seguros.asignar_si_vacio(RUT_A, None))
        self.assertEqual(seguros.estado_aseguradora(RUT_A), 'sin_asignar')


# ── 7. clasificar_items / registrar_glosas ───────────────────────────────────

class TestClasificarItems(_AislamientoCatalogo):

    def test_glosa_conocida_por_patron_cae_en_conocidos(self):
        self.catalogo = [{
            'id': 'p_control', 'nombre': 'Control Mensual de Ortodoncia',
            'glosas_boleta': ['CONTROL MENSUAL DE ORTODONCIA'],
        }]
        items = [
            {'descripcion': 'CONTROL MENSUAL DE ORTODONCIA AGOSTO pieza Boca', 'valor': 30000},
            {'descripcion': 'Cone Beam 3D pieza Boca', 'valor': 45000},
        ]
        resultado = seguros.clasificar_items(items)
        self.assertEqual(resultado['conocidos'],
                         ['CONTROL MENSUAL DE ORTODONCIA AGOSTO pieza Boca'])
        self.assertEqual(resultado['nuevos'], ['Cone Beam 3D pieza Boca'])
        self.assertEqual(self.creadas, [])   # consulta pura, no crea nada

    def test_glosa_conocida_por_glosa_original_exacta(self):
        # sembrado via guardar_prestacion (glosa_original), no glosas_boleta
        seguros.guardar_prestacion(None, {'nombre': 'Ajuste de Arco',
                                          'glosa_original': 'Ajuste de Arco'})
        items = [{'descripcion': 'Ajuste de Arco', 'valor': 5000}]
        resultado = seguros.clasificar_items(items)
        self.assertEqual(resultado['conocidos'], ['Ajuste de Arco'])
        self.assertEqual(resultado['nuevos'], [])

    def test_items_vacios_o_sin_descripcion_se_ignoran(self):
        resultado = seguros.clasificar_items([{'descripcion': '', 'valor': 1000}])
        self.assertEqual(resultado, {'conocidos': [], 'nuevos': []})
        self.assertEqual(seguros.clasificar_items([]), {'conocidos': [], 'nuevos': []})
        self.assertEqual(seguros.clasificar_items(None), {'conocidos': [], 'nuevos': []})


class TestRegistrarGlosas(_AislamientoCatalogo):

    def test_registra_las_glosas_nuevas_en_el_catalogo(self):
        items = [
            {'descripcion': 'Cone Beam 3D pieza Boca', 'valor': 45000},
            {'descripcion': 'Ajuste de Arco', 'valor': 5000},
        ]
        seguros.registrar_glosas(items)
        self.assertEqual(len(self.creadas), 2)

    def test_tras_registrar_glosas_clasificar_items_las_da_conocidas(self):
        items = [{'descripcion': 'Cone Beam 3D pieza Boca', 'valor': 45000}]
        # antes de registrar: nueva
        self.assertEqual(seguros.clasificar_items(items)['nuevos'],
                         ['Cone Beam 3D pieza Boca'])
        seguros.registrar_glosas(items)
        # despues de registrar: conocida, y no crea una segunda vez
        resultado = seguros.clasificar_items(items)
        self.assertEqual(resultado['conocidos'], ['Cone Beam 3D pieza Boca'])
        self.assertEqual(resultado['nuevos'], [])
        self.assertEqual(len(self.creadas), 1)

    def test_glosa_ya_conocida_no_duplica_al_registrar(self):
        self.catalogo = [{
            'id': 'p_control', 'nombre': 'Control Mensual de Ortodoncia',
            'glosas_boleta': ['CONTROL MENSUAL DE ORTODONCIA'],
        }]
        items = [{'descripcion': 'CONTROL MENSUAL DE ORTODONCIA AGOSTO pieza Boca',
                  'valor': 30000}]
        seguros.registrar_glosas(items)
        self.assertEqual(self.creadas, [])   # ya calzaba por patron, no crea

    def test_lista_vacia_o_none_no_falla(self):
        seguros.registrar_glosas([])
        seguros.registrar_glosas(None)
        self.assertEqual(self.creadas, [])


# ── 8. Informe generico conserva la PIEZA (aseguradora SIN plantilla) ────────

class TestInformeGenericoConservaPieza(_AislamientoCatalogo):

    def setUp(self):
        super().setUp()
        self._orig_obtener_aseguradora = seguros.obtener_aseguradora
        # euroamerica: SIN plantilla_pdf -> informe generico (usa glosa cruda).
        # zurich: CON plantilla_pdf -> usa el nombre agrupado de la prestacion.
        aseguradoras = {
            'euroamerica': {'nombre': 'Euroamerica'},
            'zurich': {'nombre': 'Zurich', 'plantilla_pdf': 'zurich.pdf'},
        }
        seguros.obtener_aseguradora = lambda key: aseguradoras.get(key)

    def tearDown(self):
        seguros.obtener_aseguradora = self._orig_obtener_aseguradora
        super().tearDown()

    def test_euroamerica_usa_la_glosa_cruda_con_la_pieza(self):
        self.catalogo = [{
            'id': 'p_recementacion', 'nombre': 'Recementación de Bracket',
            'glosas_boleta': ['RECEMENTACION BRACKET'],
        }]
        items = [{'descripcion': 'Recementacion Bracket pieza 22', 'valor': 15000}]
        filas = seguros.filas_desde_items(items, 'euroamerica')

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['descripcion'], 'Recementacion Bracket pieza 22')

    def test_zurich_usa_el_nombre_agrupado_sin_la_pieza(self):
        self.catalogo = [{
            'id': 'p_recementacion', 'nombre': 'Recementación de Bracket',
            'glosas_boleta': ['RECEMENTACION BRACKET'],
        }]
        items = [{'descripcion': 'Recementacion Bracket pieza 22', 'valor': 15000}]
        filas = seguros.filas_desde_items(items, 'zurich')

        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['descripcion'], 'Recementación de Bracket')


class TestSacaPiezaBoca(_AislamientoCatalogo):
    """'pieza Boca' (lo agrega DentiDesk cuando no se asignó un diente) no debe
    aparecer en el formulario; un diente real ('pieza 11') sí se conserva."""

    def setUp(self):
        super().setUp()
        self._orig = seguros.obtener_aseguradora
        seguros.obtener_aseguradora = lambda key: {'euroamerica': {'nombre': 'Euroamerica'}}.get(key)

    def tearDown(self):
        seguros.obtener_aseguradora = self._orig
        super().tearDown()

    def test_helper_saca_solo_boca(self):
        self.assertEqual(seguros._sin_pieza_boca('PLANO DE ALIVIO OCLUSAL pieza Boca'),
                         'PLANO DE ALIVIO OCLUSAL')
        self.assertEqual(seguros._sin_pieza_boca('EXAMEN pieza BOCA'), 'EXAMEN')
        self.assertEqual(seguros._sin_pieza_boca('RECEMENTACION pieza 11'),
                         'RECEMENTACION pieza 11')
        self.assertEqual(seguros._sin_pieza_boca('CONTROL'), 'CONTROL')

    def test_filas_sin_pieza_boca_pero_conserva_diente_real(self):
        self.catalogo = []
        filas = seguros.filas_desde_items(
            [{'descripcion': 'PLANO DE ALIVIO OCLUSAL pieza Boca', 'valor': 95000},
             {'descripcion': 'Recementacion pieza 11', 'valor': 15000}], 'euroamerica')
        self.assertEqual([f['descripcion'] for f in filas],
                         ['PLANO DE ALIVIO OCLUSAL', 'Recementacion pieza 11'])

    def test_prestacion_auto_creada_queda_sin_pieza_boca(self):
        self.catalogo = []
        p = seguros.obtener_o_crear_prestacion_glosa('CONTROL DE CONTENCION pieza Boca')
        self.assertEqual(p['nombre'], 'CONTROL DE CONTENCION')


class TestCapacidadFormulario(unittest.TestCase):
    """El formulario oficial tiene un número fijo de filas. Antes, las prestaciones
    que sobraban se descartaban EN SILENCIO mientras el total sí las sumaba (caso
    real Zurich: 7 prestaciones, se imprimieron 5 y el total decía la suma de las 7).
    preparar_filas_para_formulario garantiza que la suma NUNCA cambie."""

    def _filas(self, n):
        return [{'descripcion': f'Prestación {i}', 'valor': str(1000 * i),
                 'fecha': '18-08-2026'} for i in range(1, n + 1)]

    def test_si_caben_no_toca_nada(self):
        filas = self._filas(3)
        visibles, resto = seguros.preparar_filas_para_formulario(filas, 8)
        self.assertEqual(visibles, filas)
        self.assertEqual(resto, [])

    def test_justo_en_el_limite_no_resume(self):
        filas = self._filas(8)
        visibles, resto = seguros.preparar_filas_para_formulario(filas, 8)
        self.assertEqual(len(visibles), 8)
        self.assertEqual(resto, [])

    def test_si_sobran_resume_y_la_suma_no_cambia(self):
        filas = self._filas(12)
        total_original = sum(seguros._monto_int(f['valor']) for f in filas)
        visibles, resto = seguros.preparar_filas_para_formulario(filas, 8)

        self.assertEqual(len(visibles), 8)          # ni una fila de más
        self.assertEqual(len(resto), 5)             # 12 - (8-1) = 5 al resumen
        self.assertIn('Otras prestaciones', visibles[-1]['descripcion'])
        # LO CRÍTICO: lo que se ve suma exactamente lo que dice el total.
        self.assertEqual(sum(seguros._monto_int(f['valor']) for f in visibles),
                         total_original)
        # y la columna "Total" de esa fila también va rellena
        self.assertEqual(visibles[-1]['valor'], visibles[-1]['valor_total'])

    def test_capacidad_0_no_recorta(self):
        filas = self._filas(20)
        visibles, resto = seguros.preparar_filas_para_formulario(filas, 0)
        self.assertEqual(len(visibles), 20)
        self.assertEqual(resto, [])

    def test_capacidad_por_aseguradora(self):
        orig = seguros.obtener_aseguradora
        seguros.obtener_aseguradora = lambda k: {
            'con_tabla':  {'plantilla_pdf': 'x.pdf', 'max_prestaciones_por_form': 5,
                           'tabla_prestaciones': {'capacidad': 8}},
            'sin_tabla':  {'plantilla_pdf': 'x.pdf', 'max_prestaciones_por_form': 5},
            'sin_form':   {'nombre': 'Euroamerica'},
        }.get(k)
        try:
            self.assertEqual(seguros.capacidad_formulario('con_tabla'), 8)
            self.assertEqual(seguros.capacidad_formulario('sin_tabla'), 5)
            self.assertEqual(seguros.capacidad_formulario('sin_form'), 0)  # informe: sin límite
        finally:
            seguros.obtener_aseguradora = orig

    def test_aplicar_capacidad_reescribe_los_valores_y_limpia_las_sobrantes(self):
        valores = {}
        for i in range(1, 11):
            valores[f'prestacion_{i}_descripcion'] = f'Prestación {i}'
            valores[f'prestacion_{i}_valor'] = str(1000 * i)
            valores[f'prestacion_{i}_fecha'] = '18-08-2026'
        visibles, resto = seguros._aplicar_capacidad(valores, 8)

        self.assertEqual(len(visibles), 8)
        self.assertEqual(len(resto), 3)
        self.assertIn('Otras prestaciones', valores['prestacion_8_descripcion'])
        # las filas 9 y 10 ya no deben imprimirse
        self.assertNotIn('prestacion_9_descripcion', valores)
        self.assertNotIn('prestacion_10_descripcion', valores)


class TestMontoInt(unittest.TestCase):
    """DentiDesk manda montos en DOS formatos: '124.000' (punto = miles, como en
    pantalla) y '124000.000' (punto = decimal, en el detalle del abono). Quitar el
    punto a ciegas convertía 124.000 en 124 millones."""

    def test_entero_pelado(self):
        self.assertEqual(seguros._monto_int('124000'), 124000)
        self.assertEqual(seguros._monto_int(146000), 146000)

    def test_punto_como_separador_de_miles(self):
        self.assertEqual(seguros._monto_int('124.000'), 124000)
        self.assertEqual(seguros._monto_int('1.234.567'), 1234567)
        self.assertEqual(seguros._monto_int('$146.000'), 146000)

    def test_punto_como_decimal_del_detalle_del_abono(self):
        self.assertEqual(seguros._monto_int('124000.000'), 124000)
        self.assertEqual(seguros._monto_int('452000.000'), 452000)
        self.assertEqual(seguros._monto_int('0.000'), 0)

    def test_basura_y_vacio_dan_cero(self):
        for v in ('', None, 'abc'):
            self.assertEqual(seguros._monto_int(v), 0)


class TestItemsDeBoleta(unittest.TestCase):
    """El detalle del presupuesto trae TODO el plan del paciente; items_de_boleta
    debe usarlo SOLO si suma el total de la boleta, si no cae a 1 línea (glosa+total)."""

    def test_detalle_cuadra_con_el_monto_usa_el_detalle(self):
        items = [{'descripcion': 'A', 'valor': 95000}, {'descripcion': 'B', 'valor': 191000},
                 {'descripcion': 'C', 'valor': 54000}]
        r = seguros.items_de_boleta(items, 'glosa', 340000)
        self.assertEqual(len(r), 3)

    def test_detalle_no_cuadra_cae_a_una_linea(self):
        # presupuesto con TODO el plan (2.307.000) pero la boleta cobró 146.000
        items = [{'descripcion': 'Control Pasivo', 'valor': 41000},
                 {'descripcion': 'RADIOGRAFIA', 'valor': 25000},
                 {'descripcion': 'Otros', 'valor': 2241000}]
        r = seguros.items_de_boleta(items, 'CONTROL MENSUAL DE ORTODONCIA', 146000)
        self.assertEqual(r, [{'descripcion': 'CONTROL MENSUAL DE ORTODONCIA', 'valor': 146000}])

    def test_sin_monto_confia_en_el_detalle(self):
        items = [{'descripcion': 'A', 'valor': 10}, {'descripcion': 'B', 'valor': 20}]
        self.assertEqual(len(seguros.items_de_boleta(items, 'g', None)), 2)

    def test_sin_items_una_linea_con_la_glosa(self):
        self.assertEqual(seguros.items_de_boleta([], 'CONTROL pieza Boca', 146000),
                         [{'descripcion': 'CONTROL pieza Boca', 'valor': 146000}])

    def test_monto_como_texto_con_puntos_igual_cuadra(self):
        items = [{'descripcion': 'A', 'valor': '95.000'}, {'descripcion': 'B', 'valor': '51.000'}]
        self.assertEqual(len(seguros.items_de_boleta(items, 'g', '146.000')), 2)


class TestGeometriaTablas(unittest.TestCase):
    """La semilla declara la tabla de prestaciones de las 16 aseguradoras con
    plantilla. Estas pruebas fijan lo que se verifico A OJO formulario por formulario
    (render a 2 y a 8 prestaciones): si alguien mueve una coordenada sin volver a
    mirar el PDF, esto falla antes del deploy."""

    @classmethod
    def setUpClass(cls):
        ruta = Path(__file__).parent / 'seguros_seed' / 'aseguradoras_seed.json'
        cls.seed = json.loads(ruta.read_text(encoding='utf-8'))

    def test_todas_las_que_tienen_plantilla_llegan_a_8_filas(self):
        for key, aseg in self.seed.items():
            if not aseg.get('plantilla'):
                continue        # EUROAMERICA usa el informe propio: sin limite
            with self.subTest(aseguradora=key):
                self.assertTrue(aseg.get('tabla_prestaciones'),
                                f'{key} sin tabla_prestaciones')
                self.assertEqual(aseg.get('max_prestaciones_por_form'), 8)

    def test_la_geometria_de_cada_tabla_es_coherente(self):
        for key, aseg in self.seed.items():
            spec = aseg.get('tabla_prestaciones')
            if not spec:
                continue
            with self.subTest(aseguradora=key):
                cols = spec.get('columnas') or []
                self.assertTrue(cols, f'{key} sin columnas')
                self.assertGreater(float(spec['y1']), float(spec['y0']))
                # columnas ordenadas, sin solaparse ni dejar huecos
                # ordenadas y sin solaparse. En los AcroForm los limites salen del
                # rect de cada widget, que deja microhuecos de 1-3 pt: eso es normal,
                # un solape en cambio hace que el texto invada la columna vecina.
                for a, b in zip(cols, cols[1:]):
                    self.assertLess(a['x0'], a['x1'])
                    self.assertGreaterEqual(b['x0'], a['x1'] - 0.1,
                                            f'{key}: columnas solapadas')
                    self.assertLess(b['x0'] - a['x1'], 4.0,
                                    f'{key}: hueco grande entre columnas')
                # la letra no puede caer bajo lo legible con 8 filas
                alto = (float(spec['y1']) - float(spec['y0'])) / 8
                self.assertGreaterEqual(round(alto, 1), 7.5,
                                        f'{key}: {alto:.1f} pt por fila con 8 filas')
                # descripcion y valor son obligatorias: sin ellas el formulario no dice nada
                campos = {c.get('campo') for c in cols}
                self.assertIn('descripcion', campos)
                self.assertTrue({'valor', 'valor_total'} & campos)
                x_tap = spec.get('x_tapar_fin')
                if x_tap is not None:       # Cruz Blanca / Vida Security: celda alta del Total
                    self.assertGreater(float(x_tap), max(c['x1'] for c in cols))



if __name__ == '__main__':
    unittest.main(verbosity=2)
