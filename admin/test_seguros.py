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


if __name__ == '__main__':
    unittest.main(verbosity=2)
