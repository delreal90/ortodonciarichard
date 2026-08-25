"""
test_dolar.py - El dolar observado que se propone en las compras en USD.

Cero red: la API (mindicador.cl) se intercepta SIEMPRE. Base SQLite temporal.

    cd admin && python test_dolar.py

Cubre lo que duele si falla:
  - Que un fin de semana o feriado NO invente un valor: usa el del ultimo dia
    habil anterior y avisa que no es exacto (exacto=False).
  - Que si la API se cae (devuelve 500/timeout, pasa seguido) el sistema NO
    reviente ni bloquee la compra: responde None y el valor se escribe a mano.
  - Que el cache evite ir a la red de nuevo, y que el año en curso se refresque
    solo cuando ya paso el TTL.
  - Que refrescar_anio funcione llamado directo (bug real: fallaba con "no such
    table" porque solo observado() creaba las tablas).
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='dolar_test_'))
os.environ['COMPRAS_DB_PATH'] = str(_TMP / 'compras.db')
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
sys.path.insert(0, str(Path(__file__).parent))

import compras   # noqa: E402
import dolar     # noqa: E402

# Serie real de agosto 2026 (dias habiles). Ojo: 15, 16, 22 y 23 son fin de semana
# y NO estan, igual que en la serie de verdad.
SERIE = [
    ('2026-08-14', 913.2), ('2026-08-17', 913.15), ('2026-08-18', 914.19),
    ('2026-08-19', 922.12), ('2026-08-20', 920.26), ('2026-08-21', 923.23),
    ('2026-08-24', 918.17), ('2026-08-25', 914.64),
]


def _respuesta_api(serie=SERIE):
    """Imita lo que devuelve mindicador.cl (mas nuevo primero)."""
    cuerpo = json.dumps({
        'codigo': 'dolar', 'nombre': 'Dólar observado',
        'serie': [{'fecha': f + 'T04:00:00.000Z', 'valor': v} for f, v in reversed(serie)],
    }).encode('utf-8')
    ctx = mock.MagicMock()
    ctx.__enter__.return_value.read.return_value = cuerpo
    ctx.__exit__.return_value = False
    return ctx


class _Base(unittest.TestCase):

    def setUp(self):
        for suf in ('', '-wal', '-shm'):
            p = Path(str(compras.DB_PATH) + suf)
            if p.exists():
                p.unlink()
        compras.init_db()
        dolar._init()

    def _sembrar(self, serie=SERIE, refrescado_recien=True):
        """Deja la serie en el cache, sin pasar por la red."""
        con = compras._conn()
        con.executemany('INSERT OR REPLACE INTO dolar_dia(fecha,valor) VALUES(?,?)', serie)
        if refrescado_recien:
            con.execute('INSERT OR REPLACE INTO dolar_refresco(anio,ts) VALUES(?,?)',
                        ('2026', compras.ahora_cl().isoformat(timespec='seconds')))
        con.commit()
        con.close()


class TestDiaHabil(_Base):

    def test_fecha_con_valor_publicado(self):
        self._sembrar()
        r = dolar.observado('2026-08-19')
        self.assertEqual(r['valor'], 922.12)
        self.assertEqual(r['fecha'], '2026-08-19')
        self.assertTrue(r['exacto'])

    def test_no_toca_la_red_si_esta_en_cache(self):
        self._sembrar()
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('fue a la red')):
            self.assertEqual(dolar.observado('2026-08-25')['valor'], 914.64)


class TestFinDeSemanaYFeriado(_Base):
    """Lo mas importante: esos dias NO tienen publicacion. Debe usar el ultimo
    dia habil anterior y decir que no es exacto (para que la UI lo explique)."""

    def test_sabado_usa_el_viernes(self):
        self._sembrar()
        r = dolar.observado('2026-08-22')      # sabado
        self.assertEqual(r['valor'], 923.23)
        self.assertEqual(r['fecha'], '2026-08-21')   # viernes
        self.assertFalse(r['exacto'])

    def test_domingo_usa_el_viernes(self):
        self._sembrar()
        r = dolar.observado('2026-08-23')      # domingo
        self.assertEqual(r['fecha'], '2026-08-21')
        self.assertFalse(r['exacto'])

    def test_feriado_largo_dentro_del_limite(self):
        self._sembrar([('2026-09-11', 900.0)])
        r = dolar.observado('2026-09-18')      # 7 dias despues, dentro de MAX_DIAS_ATRAS
        self.assertEqual(r['fecha'], '2026-09-11')
        self.assertFalse(r['exacto'])

    def test_no_usa_un_valor_demasiado_viejo(self):
        """Mas de MAX_DIAS_ATRAS: prefiere no proponer nada antes que un valor
        de hace semanas (el tipo de cambio se mueve)."""
        self._sembrar([('2026-09-01', 900.0)])
        with mock.patch('urllib.request.urlopen', side_effect=OSError('sin red')):
            self.assertIsNone(dolar.observado('2026-10-15'))


class TestApiCaida(_Base):
    """La API devuelve 500/timeouts seguido: nunca debe romper ni bloquear."""

    def test_sin_red_y_sin_cache_devuelve_none(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError('conexion rechazada')):
            self.assertIsNone(dolar.observado('2026-08-19'))

    def test_sin_red_pero_con_cache_igual_responde(self):
        self._sembrar(refrescado_recien=False)
        with mock.patch('urllib.request.urlopen', side_effect=OSError('caida')):
            self.assertEqual(dolar.observado('2026-08-19')['valor'], 922.12)

    def test_refrescar_devuelve_menos_uno_si_falla(self):
        with mock.patch('urllib.request.urlopen', side_effect=OSError('caida')):
            self.assertEqual(dolar.refrescar_anio(2026), -1)

    def test_reintenta_y_se_recupera(self):
        """Primer intento falla, el segundo funciona: debe quedarse con el dato."""
        intentos = {'n': 0}

        def _flaky(*a, **k):
            intentos['n'] += 1
            if intentos['n'] == 1:
                raise OSError('500 temporal')
            return _respuesta_api()

        with mock.patch('urllib.request.urlopen', side_effect=_flaky), \
             mock.patch('time.sleep'):
            self.assertEqual(dolar.refrescar_anio(2026), len(SERIE))
        self.assertEqual(intentos['n'], 2)

    def test_json_corrupto_no_revienta(self):
        ctx = mock.MagicMock()
        ctx.__enter__.return_value.read.return_value = b'<html>error</html>'
        ctx.__exit__.return_value = False
        with mock.patch('urllib.request.urlopen', return_value=ctx), mock.patch('time.sleep'):
            self.assertEqual(dolar.refrescar_anio(2026), -1)

    def test_serie_vacia(self):
        with mock.patch('urllib.request.urlopen', return_value=_respuesta_api([])), \
             mock.patch('time.sleep'):
            self.assertEqual(dolar.refrescar_anio(2026), 0)


class TestRefresco(_Base):

    def test_refrescar_anio_funciona_llamado_directo(self):
        """Bug real: fallaba con 'no such table' porque solo observado() creaba
        las tablas. Una precarga que llame refrescar_anio debe funcionar sola."""
        for suf in ('', '-wal', '-shm'):
            p = Path(str(compras.DB_PATH) + suf)
            if p.exists():
                p.unlink()
        compras.init_db()          # base sin las tablas del dolar
        with mock.patch('urllib.request.urlopen', return_value=_respuesta_api()):
            self.assertEqual(dolar.refrescar_anio(2026), len(SERIE))

    def test_baja_de_la_api_cuando_no_esta_en_cache(self):
        with mock.patch('urllib.request.urlopen', return_value=_respuesta_api()) as m:
            r = dolar.observado('2026-08-19')
            self.assertEqual(r['valor'], 922.12)
            self.assertEqual(m.call_count, 1)

    def test_no_vuelve_a_la_red_dentro_del_ttl(self):
        """Si el año ya se refresco hace poco y la fecha sigue sin valor (dia no
        habil), no debe golpear la API de nuevo."""
        self._sembrar()
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('fue a la red')):
            self.assertFalse(dolar.observado('2026-08-22')['exacto'])

    def test_refresca_de_nuevo_pasado_el_ttl(self):
        self._sembrar(refrescado_recien=False)
        con = compras._conn()
        viejo = (compras.ahora_cl() - timedelta(hours=dolar.TTL_HORAS + 1))
        con.execute('INSERT OR REPLACE INTO dolar_refresco(anio,ts) VALUES(?,?)',
                    ('2026', viejo.isoformat(timespec='seconds')))
        con.commit()
        con.close()
        nueva = SERIE + [('2026-08-26', 910.0)]
        with mock.patch('urllib.request.urlopen', return_value=_respuesta_api(nueva)) as m:
            self.assertEqual(dolar.observado('2026-08-26')['valor'], 910.0)
            self.assertEqual(m.call_count, 1)


class TestEntradasInvalidas(_Base):

    def test_fecha_con_formato_malo(self):
        self.assertIsNone(dolar.observado('25/08/2026'))
        self.assertIsNone(dolar.observado('no-es-fecha'))

    def test_descarta_valores_basura_de_la_api(self):
        serie_sucia = [{'fecha': '2026-08-19T04:00:00.000Z', 'valor': 922.12},
                       {'fecha': 'malo', 'valor': 900},
                       {'fecha': '2026-08-18T04:00:00.000Z', 'valor': None},
                       {'fecha': '2026-08-17T04:00:00.000Z', 'valor': -5}]
        cuerpo = json.dumps({'serie': serie_sucia}).encode('utf-8')
        ctx = mock.MagicMock()
        ctx.__enter__.return_value.read.return_value = cuerpo
        ctx.__exit__.return_value = False
        with mock.patch('urllib.request.urlopen', return_value=ctx):
            self.assertEqual(dolar.refrescar_anio(2026), 1)   # solo la fila valida


if __name__ == '__main__':
    unittest.main(verbosity=2)
