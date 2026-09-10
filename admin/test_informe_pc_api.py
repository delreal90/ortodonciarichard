"""
test_informe_pc_api.py - Los endpoints que hacen que un informe deje de ser
inalcanzable al dia siguiente.

Hasta el 2026-09-10 la unica lista era "los informes de HOY". Un informe de la
semana pasada existia en el registro pero no habia forma de llegar a el salvo
conocer su id y escribir la URL a mano. Estos endpoints son esa puerta.

Cero red: DentiDesk deshabilitado y los stores en tempfiles (mismo patron que
test_seguros_api.py).

    cd admin && python test_informe_pc_api.py

Lo que se protege:

  - Que el buscador encuentre por nombre CON y SIN tildes, y por RUT en
    cualquier formato: quien busca no tiene por que saber como quedo guardado.
  - Que 'limite' no se pueda usar para pedir el registro entero de una.
  - Que un paciente sin informes previos devuelva 200 con lista vacia y NO un
    404: "es su primera vez" es una respuesta valida, no un error.
  - Y la que protege el rendimiento: que resolver la firma del doctor NO cueste
    una lectura de disco por fila. Es lo que hacia que la lista del dia fuera
    tolerable y el historial completo no.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='informe_pc_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['INFORME_PC_REGISTRO_PATH'] = str(_TMP / 'informe_pc_registro.json')
os.environ['INFORME_PC_IMAGENES_DIR'] = str(_TMP / 'imagenes')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import server       # noqa: E402
import informe_pc   # noqa: E402

TOKEN = {'X-Admin-Token': 'token-de-prueba'}


class _Base(unittest.TestCase):

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        informe_pc._STORE.save({'informes': {}})

    def tearDown(self):
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def _crear(self, **ov):
        d = {'nombre': 'Ana Núñez Soto', 'rut': '11.111.111-1', 'edad': 10,
             'sexo': 'F', 'fecha': '2026-08-01', 'conclusion': 'corresponde',
             'doctor_texto': 'Dr. Alberto Del Real'}
        d.update(ov)
        return self.client.post('/api/informe-pc/guardar', json=d,
                                headers=TOKEN).get_json()['id']

    def _buscar(self, **params):
        qs = '&'.join('%s=%s' % (k, v) for k, v in params.items())
        r = self.client.get('/api/informe-pc/buscar?' + qs, headers=TOKEN)
        self.assertEqual(r.status_code, 200)
        return r.get_json()


class TestBuscar(_Base):

    def test_sin_token_no_abre(self):
        self.assertEqual(self.client.get('/api/informe-pc/buscar').status_code, 403)
        self.assertEqual(self.client.get('/api/informe-pc/previos?rut=1-9').status_code, 403)

    def test_encuentra_un_informe_de_otro_dia(self):
        """El caso que motivo todo: el de recepcion solo mostraba los de hoy."""
        self._crear(fecha='2026-03-15')
        d = self._buscar()
        self.assertEqual(d['total'], 1)
        self.assertEqual(d['informes'][0]['fecha'], '2026-03-15')

    def test_por_nombre_sin_tildes(self):
        self._crear()
        self.assertEqual(self._buscar(texto='nunez')['total'], 1)

    def test_por_rut_con_puntos_y_sin_puntos(self):
        self._crear()
        for forma in ('11.111.111-1', '111111111', '11111111'):
            self.assertEqual(self._buscar(texto=forma)['total'], 1, forma)

    def test_rango_de_fechas(self):
        self._crear(fecha='2026-01-10')
        self._crear(fecha='2026-08-10', rut='22.222.222-2', nombre='Bruno Pérez')
        self.assertEqual(self._buscar(desde='2026-06-01')['total'], 1)
        self.assertEqual(self._buscar(hasta='2026-06-01')['total'], 1)

    def test_la_fila_trae_lo_necesario_para_decidir_si_abrirlo(self):
        self._crear()
        fila = self._buscar()['informes'][0]
        for campo in ('id', 'nombre', 'rut_fmt', 'fecha', 'edad', 'impreso',
                      'conclusion_label', 'ordenes_labels', 'sin_firma', 'doctor_texto'):
            self.assertIn(campo, fila, campo)

    def test_el_limite_no_deja_pedir_el_registro_entero(self):
        """arg_int lo acota; sin eso un ?limite=999999 traeria todo de una."""
        for n in range(5):
            self._crear(rut='%d1111111-1' % n, nombre='P%d' % n)
        self.assertEqual(len(self._buscar(limite=2)['informes']), 2)
        # total sigue contando TODOS, que es lo que dice si hay "ver mas"
        self.assertEqual(self._buscar(limite=2)['total'], 5)
        self.assertLessEqual(len(self._buscar(limite=999999)['informes']), 200)

    def test_offset_pagina(self):
        for f in ('2026-08-01', '2026-08-02', '2026-08-03'):
            self._crear(fecha=f)
        fechas_ = [i['fecha'] for i in self._buscar(offset=1)['informes']]
        self.assertEqual(fechas_, ['2026-08-02', '2026-08-01'])

    def test_parametros_basura_no_revientan(self):
        """Un ?limite=abc daba pagina HTML de error 500 y el frontend no la
        podia parsear. arg_int existe justamente para eso."""
        self._crear()
        r = self.client.get('/api/informe-pc/buscar?limite=abc&offset=xyz', headers=TOKEN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['ok'])


class TestPrevios(_Base):

    def test_paciente_sin_informes_es_200_con_lista_vacia(self):
        """NO 404: que sea su primera vez no es un error."""
        r = self.client.get('/api/informe-pc/previos?rut=99.999.999-9', headers=TOKEN)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()['ok'])
        self.assertEqual(r.get_json()['informes'], [])

    def test_devuelve_los_del_mismo_paciente(self):
        self._crear(fecha='2026-01-10')
        self._crear(fecha='2026-08-10')
        self._crear(rut='22.222.222-2', nombre='Otro')
        r = self.client.get('/api/informe-pc/previos?rut=11111111-1', headers=TOKEN)
        self.assertEqual(len(r.get_json()['informes']), 2)

    def test_excluye_el_que_se_esta_mirando(self):
        iid = self._crear()
        self._crear(fecha='2026-01-10')
        r = self.client.get('/api/informe-pc/previos?rut=11111111-1&excluir=' + iid,
                            headers=TOKEN)
        ids = [i['id'] for i in r.get_json()['informes']]
        self.assertEqual(len(ids), 1)
        self.assertNotIn(iid, ids)

    def test_sin_rut_no_devuelve_nada(self):
        """Un rut vacio no puede significar "todos": seria mostrarle a un
        paciente los informes de los demas."""
        self._crear()
        r = self.client.get('/api/informe-pc/previos?rut=', headers=TOKEN)
        self.assertEqual(r.get_json()['informes'], [])


class TestLaFirmaNoSeResuelvePorFila(_Base):
    """Resolver 'sin_firma' cuesta leer el config, resolver el doctor y
    base64-ear su firma desde el disco. Se pagaba UNA VEZ POR FILA: con los diez
    informes de un dia pasaba, con el historial completo de un paciente no."""

    def test_se_resuelve_una_vez_por_doctor_distinto(self):
        for n in range(10):
            self._crear(rut='%d1111111-1' % n, nombre='P%d' % n,
                        doctor_texto='Dr. Alberto Del Real')
        for n in range(10):
            self._crear(rut='%d2222222-2' % n, nombre='Q%d' % n,
                        doctor_texto='Dr. Rodrigo Oyonarte')

        real = server._doctor_informe
        llamadas = {'n': 0}

        def contar(txt=''):
            llamadas['n'] += 1
            return real(txt)

        server._doctor_informe = contar
        try:
            d = self._buscar(limite=50)
        finally:
            server._doctor_informe = real

        self.assertEqual(d['total'], 20)
        self.assertLessEqual(llamadas['n'], 2,
                             'se resolvio la firma %d veces para 2 doctores' % llamadas['n'])

    def test_el_aviso_de_sin_firma_sigue_llegando(self):
        """El cache no puede costar la advertencia: recepcion tiene que saber
        que ese informe saldria sin firma ANTES de imprimirlo."""
        self._crear()
        self.assertIn('sin_firma', self._buscar()['informes'][0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
