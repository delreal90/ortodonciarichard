"""
test_stats.py - El registro de reservas no puede perder datos.

Cero red. Todo corre contra archivos temporales.

    cd admin && python test_stats.py

stats.py era el UNICO modulo de persistencia del proyecto sin threading.Lock:
- registrar() hacia un append sin lock desde cada request de reserva.
- eliminar() hacia un read-modify-write del archivo COMPLETO sin lock y sin
  escritura atomica. Una reserva que entraba en el medio se perdia.
"""

import os
import sys
import json
import tempfile
import threading
import unittest
from pathlib import Path
from datetime import date

_TMP = Path(tempfile.mkdtemp(prefix='stats_test_'))
os.environ['STATS_PATH'] = str(_TMP / 'agendamientos.jsonl')
os.environ['EVENTOS_PATH'] = str(_TMP / 'eventos.jsonl')
sys.path.insert(0, str(Path(__file__).parent))

import fechas    # noqa: E402
import stats     # noqa: E402


class TestConcurrencia(unittest.TestCase):

    def setUp(self):
        for p in (stats.STATS_PATH, stats.EVENTOS_PATH):
            if p.exists():
                p.unlink()

    def test_no_se_pierden_reservas_concurrentes(self):
        """20 hilos agendando a la vez: tienen que quedar las 20 lineas."""
        N = 20
        errores = []

        def agendar(i):
            try:
                stats.registrar({'doctor_nombre': f'doc{i}', 'motivo_label': 'control'})
            except Exception as e:      # pragma: no cover
                errores.append(e)

        hilos = [threading.Thread(target=agendar, args=(i,)) for i in range(N)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(errores, [])
        self.assertEqual(len(stats._leer()), N)

    def test_eliminar_no_pisa_una_reserva_que_entra_en_el_medio(self):
        """El caso real: el panel borra una reserva de prueba justo cuando un
        paciente agenda. Antes, el read-modify-write sin lock se comia la nueva."""
        stats.registrar({'doctor_nombre': 'viejo', 'motivo_label': 'control'})
        ts_a_borrar = stats._leer()[0]['ts']

        listo = threading.Event()
        resultados = {}

        def borrar():
            listo.wait()
            resultados['eliminados'] = stats.eliminar(ts_a_borrar)

        def agendar():
            listo.wait()
            for i in range(30):
                stats.registrar({'doctor_nombre': f'nuevo{i}', 'motivo_label': 'urgencia'})

        h1, h2 = threading.Thread(target=borrar), threading.Thread(target=agendar)
        h1.start(); h2.start()
        listo.set()
        h1.join(); h2.join()

        registros = stats._leer()
        nuevos = [r for r in registros if r.get('doctor_nombre', '').startswith('nuevo')]
        self.assertEqual(len(nuevos), 30, 'no se puede perder ninguna reserva nueva')

    def test_escritura_atomica_no_deja_archivo_corrupto(self):
        """Cada linea del archivo debe seguir siendo JSON valido tras un eliminar."""
        for i in range(5):
            stats.registrar({'doctor_nombre': f'doc{i}'})
        ts = stats._leer()[2]['ts']
        stats.eliminar(ts)
        for linea in stats.STATS_PATH.read_text(encoding='utf-8').splitlines():
            if linea.strip():
                json.loads(linea)      # lanza si quedo cortado
        self.assertFalse(list(_TMP.glob('*.tmp')), 'no debe quedar el temporal')


class TestHoraChile(unittest.TestCase):

    def setUp(self):
        for p in (stats.STATS_PATH, stats.EVENTOS_PATH):
            if p.exists():
                p.unlink()

    def test_reserva_y_embudo_usan_el_mismo_huso(self):
        """registrar() ya usaba hora de Chile; registrar_evento() usaba UTC.
        Quedaban en husos distintos dentro del mismo panel."""
        stats.registrar({'doctor_nombre': 'x'})
        stats.registrar_evento('sesion-1', 'abrir')
        ts_reserva = stats._leer()[0]['ts']
        ts_evento = stats._leer(stats.EVENTOS_PATH)[0]['ts']
        self.assertEqual(ts_reserva[:13], ts_evento[:13],
                         'misma fecha y hora: los dos en hora de Chile')

    def test_timeline_termina_hoy_en_chile(self):
        r = stats.resumen()
        self.assertEqual(r['timeline_30d'][-1]['fecha'], fechas.hoy_chile().isoformat())
        self.assertEqual(len(r['timeline_30d']), 30)

    def test_la_reserva_de_hoy_cae_en_el_ultimo_dia_del_timeline(self):
        """Con date.today() (UTC) la reserva de las 21:00 hora Chile quedaba
        fuera del timeline, en un dia que aun no existia."""
        stats.registrar({'doctor_nombre': 'x'})
        r = stats.resumen()
        self.assertEqual(r['timeline_30d'][-1]['total'], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
