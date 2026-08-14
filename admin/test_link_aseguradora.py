"""
test_link_aseguradora.py - Links para que el PACIENTE actualice su aseguradora
(molde exacto de test_link_agenda.py, ver link_aseguradora.py).

Cero red: solo el store JSON en un tempfile.

    cd admin && python test_link_aseguradora.py

Cubre: crear + resolver ida y vuelta, url bien formada, expiracion (con
dias_expira negativo), uso (marcar_usado), token invalido ('no_existe'), y
la contingencia de colision de token (que NUNCA debe pisar un link existente).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='link_aseguradora_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['LINKS_ASEGURADORA_PATH'] = str(_TMP / 'links_aseguradora.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import link_aseguradora  # noqa: E402

# RUT sintetico valido (mismo que usa test_avisos.py / test_link_agenda.py).
RUT = '17.406.985-9'


def _limpiar():
    link_aseguradora._save({'links': {}})


class TestCrearYResolver(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_crear_devuelve_token_url_y_expira(self):
        r = link_aseguradora.crear(RUT, 'https://ortodonciarichard.onrender.com')
        self.assertIn('token', r)
        self.assertIn('url', r)
        self.assertIn('expira', r)
        self.assertTrue(r['url'].endswith(f'/actualizar-seguro?token={r["token"]}'))

    def test_url_saca_la_barra_final_del_base_url(self):
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com/')
        self.assertEqual(r['url'],
                         f'https://x.onrender.com/actualizar-seguro?token={r["token"]}')

    def test_resolver_ida_y_vuelta(self):
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com')
        res = link_aseguradora.resolver(r['token'])
        self.assertTrue(res['ok'])
        self.assertEqual(res['rut'], link_aseguradora.avisos.rut_key(RUT))
        self.assertEqual(res['expira'], r['expira'])
        self.assertIsNone(res['usado'])

    def test_resolver_token_inexistente(self):
        res = link_aseguradora.resolver('token-que-no-existe')
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'no_existe')

    def test_resolver_token_vacio_o_none(self):
        self.assertEqual(link_aseguradora.resolver('')['motivo'], 'no_existe')
        self.assertEqual(link_aseguradora.resolver(None)['motivo'], 'no_existe')

    def test_get_token_inexistente_da_none(self):
        self.assertIsNone(link_aseguradora.get('nada'))
        self.assertIsNone(link_aseguradora.get(''))
        self.assertIsNone(link_aseguradora.get(None))


class TestExpiracion(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_dias_expira_negativo_da_expirado(self):
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com', dias_expira=-1)
        res = link_aseguradora.resolver(r['token'])
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'expirado')

    def test_dias_expira_default_no_esta_expirado(self):
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com')
        res = link_aseguradora.resolver(r['token'])
        self.assertTrue(res['ok'])


class TestUsado(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_marcar_usado_y_resolver_da_usado(self):
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com')
        registro = link_aseguradora.marcar_usado(r['token'])
        self.assertIsNotNone(registro)
        self.assertIsNotNone(registro['usado'])

        res = link_aseguradora.resolver(r['token'])
        self.assertFalse(res['ok'])
        self.assertEqual(res['motivo'], 'usado')

    def test_marcar_usado_token_inexistente_no_falla(self):
        self.assertIsNone(link_aseguradora.marcar_usado('no-existe'))

    def test_no_se_invalida_solo_por_resolver(self):
        # A diferencia de link_agenda: resolver() NO sella el token — el
        # endpoint decide. Resolver dos veces seguidas debe seguir dando ok.
        r = link_aseguradora.crear(RUT, 'https://x.onrender.com')
        self.assertTrue(link_aseguradora.resolver(r['token'])['ok'])
        self.assertTrue(link_aseguradora.resolver(r['token'])['ok'])


class TestColisionDeToken(unittest.TestCase):

    def setUp(self):
        _limpiar()

    def test_nunca_pisa_un_token_existente(self):
        with mock.patch('link_aseguradora.secrets.token_urlsafe', return_value='FIJO'):
            r1 = link_aseguradora.crear(RUT, 'https://x.onrender.com')
            self.assertEqual(r1['token'], 'FIJO')
            with self.assertRaises(RuntimeError):
                link_aseguradora.crear('9.999.999-9', 'https://x.onrender.com')

        # El link original sigue intacto (no lo piso el intento fallido).
        res = link_aseguradora.resolver('FIJO')
        self.assertTrue(res['ok'])
        self.assertEqual(res['rut'], link_aseguradora.avisos.rut_key(RUT))


if __name__ == '__main__':
    unittest.main(verbosity=2)
