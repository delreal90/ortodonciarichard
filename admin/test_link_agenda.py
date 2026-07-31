"""
test_link_agenda.py - Los links de agenda pre-cargados (paciente + doctor +
motivo) que genera la secretaria desde el F2.

Cero red: solo el store JSON en un tempfile.

    cd admin && python test_link_agenda.py

Cubre: crear + resolver ida y vuelta, expiracion, uso, poda, token invalido,
y la contingencia de colision de token (que NUNCA debe pisar un link
existente).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='link_agenda_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['LINKS_AGENDA_PATH'] = str(_TMP / 'links_agenda.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fechas         # noqa: E402
import link_agenda     # noqa: E402

# RUT sintetico valido modulo 11 (mismo que usa test_avisos.py).
RUT = '17.406.985-9'


def _limpiar():
    link_agenda._save({'links': {}})


class TestCrearYResolver(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_crear_devuelve_token_url_y_expira(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo')
        self.assertIn('token', r)
        self.assertIn('url', r)
        self.assertIn('expira', r)
        self.assertIn('#cita=', r['url'])
        self.assertIn(r['token'], r['url'])

    def test_resolver_ida_y_vuelta(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo', id_agenda_origen='999')
        res = link_agenda.resolver(r['token'])
        self.assertTrue(res['ok'])
        self.assertEqual(res['rut'], link_agenda.avisos.rut_key(RUT))
        self.assertEqual(res['doctor'], 'alberto')
        self.assertEqual(res['motivo'], 'control_fijo')
        self.assertEqual(res['id_agenda_origen'], '999')
        self.assertIsNone(res['usado'])

    def test_expira_es_hoy_mas_dias_expira(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo', dias_expira=10)
        esperado = (fechas.hoy_chile() + timedelta(days=10)).isoformat()
        self.assertEqual(r['expira'], esperado)

    def test_url_base_desde_env(self):
        with mock.patch.dict(os.environ, {'SITIO_URL_BASE': 'https://prueba.cl'}):
            r = link_agenda.crear(RUT, 'alberto', 'control_fijo')
        self.assertTrue(r['url'].startswith('https://prueba.cl/#cita='))

    def test_url_base_desde_cfg_clinica(self):
        cfg = {'clinica': {'sitio_url': 'https://otro-dominio.cl'}}
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo', cfg=cfg)
        self.assertTrue(r['url'].startswith('https://otro-dominio.cl/#cita='))

    def test_url_base_fallback_por_defecto(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo')
        self.assertTrue(r['url'].startswith('https://www.ortodonciarichard.cl/#cita='))


class TestTokenInvalido(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_token_inexistente_no_existe(self):
        res = link_agenda.resolver('token-que-no-existe')
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'no_existe')

    def test_token_vacio_no_existe(self):
        res = link_agenda.resolver('')
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'no_existe')


class TestExpirado(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_link_expirado_no_resuelve(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo', dias_expira=-1)
        res = link_agenda.resolver(r['token'])
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'expirado')

    def test_link_que_expira_hoy_todavia_sirve(self):
        """dias_expira=0 -> expira = hoy; resolver() compara con hoy_chile(),
        asi que el mismo dia el link sigue siendo valido."""
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo', dias_expira=0)
        res = link_agenda.resolver(r['token'])
        self.assertTrue(res['ok'])


class TestUsado(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_marcar_usado_sella_y_resolver_falla_con_usado(self):
        r = link_agenda.crear(RUT, 'alberto', 'control_fijo')
        actualizado = link_agenda.marcar_usado(r['token'])
        self.assertIsNotNone(actualizado['usado'])

        res = link_agenda.resolver(r['token'])
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'usado')

    def test_marcar_usado_token_inexistente_devuelve_none(self):
        self.assertIsNone(link_agenda.marcar_usado('no-existe'))


class TestPoda(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def _viejo_iso(self, dias):
        return (fechas.hoy_chile() - timedelta(days=dias)).isoformat()

    def test_poda_descarta_links_vencidos_hace_mucho_y_conserva_recientes(self):
        datos = link_agenda._load()
        datos['links']['viejo'] = {
            'rut': '111111111', 'doctor': 'alberto', 'motivo': 'control_fijo',
            'creado': self._viejo_iso(200), 'expira': self._viejo_iso(150),
            'usado': None, 'id_agenda_origen': '',
        }
        datos['links']['reciente'] = {
            'rut': '222222222', 'doctor': 'alberto', 'motivo': 'control_fijo',
            'creado': self._viejo_iso(5), 'expira': self._viejo_iso(1),
            'usado': None, 'id_agenda_origen': '',
        }
        link_agenda._save(datos)

        # crear() llama a _podar() internamente -- se dispara con cualquier
        # creacion nueva, sin esperar un barrido aparte.
        link_agenda.crear(RUT, 'alberto', 'control_fijo')

        datos = link_agenda._load()
        self.assertNotIn('viejo', datos['links'], 'vencido hace mas de 90 dias: se poda')
        self.assertIn('reciente', datos['links'], 'vencido hace poco: se conserva')


class TestColisionDeToken(unittest.TestCase):
    """CONTINGENCIA 7: si el token generado ya existe, se reintenta -- nunca se
    pisa el link existente."""

    def setUp(self):
        _limpiar()

    def test_colision_no_pisa_el_link_existente(self):
        primero = link_agenda.crear(RUT, 'alberto', 'control_fijo',
                                     id_agenda_origen='original')
        token_existente = primero['token']

        # secrets.token_urlsafe primero repite el token ya usado (colision),
        # luego devuelve uno nuevo -- crear() debe descartar el primero y
        # reintentar, sin tocar el registro original.
        with mock.patch.object(link_agenda.secrets, 'token_urlsafe',
                                side_effect=[token_existente, 'token-nuevo-libre']):
            segundo = link_agenda.crear('11.111.111-1', 'rodrigo', 'primera_consulta',
                                         id_agenda_origen='segundo')

        self.assertNotEqual(segundo['token'], token_existente)
        self.assertEqual(segundo['token'], 'token-nuevo-libre')

        original = link_agenda.get(token_existente)
        self.assertEqual(original['id_agenda_origen'], 'original',
                          'el link original NO debe pisarse por la colision')

    def test_cinco_colisiones_seguidas_lanza_excepcion_clara(self):
        primero = link_agenda.crear(RUT, 'alberto', 'control_fijo')
        token_existente = primero['token']

        with mock.patch.object(link_agenda.secrets, 'token_urlsafe',
                                return_value=token_existente):
            with self.assertRaises(RuntimeError):
                link_agenda.crear('11.111.111-1', 'rodrigo', 'primera_consulta')

        # El link original sigue intacto tras el intento fallido.
        self.assertIsNotNone(link_agenda.get(token_existente))


class TestListarYGet(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_get_token_inexistente_devuelve_none(self):
        self.assertIsNone(link_agenda.get('no-existe'))

    def test_listar_ordena_mas_reciente_primero(self):
        link_agenda.crear(RUT, 'alberto', 'control_fijo')
        link_agenda.crear('11.111.111-1', 'rodrigo', 'primera_consulta')
        items = link_agenda.listar()
        self.assertEqual(len(items), 2)
        self.assertGreaterEqual(items[0]['creado'], items[1]['creado'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
