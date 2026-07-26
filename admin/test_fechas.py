"""
test_fechas.py - La hora de Chile vs. el reloj UTC de Render.

Cero red. Simula el reloj del servidor para reproducir el bug que le escondia
horas disponibles a los pacientes.

    cd admin && python test_fechas.py

El bug: Render corre en UTC, 3-4h ADELANTE de Chile. scheduling.cumple_anticipacion
comparaba `datetime.now()` (UTC) contra la hora de pared chilena de la cita, asi que
el margen calculado salia 4h mas chico que el real y se descartaban horas validas.
"""

import os
import sys
import unittest
from pathlib import Path
from datetime import datetime, date, timedelta
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import fechas          # noqa: E402
import scheduling      # noqa: E402


class TestHelpers(unittest.TestCase):

    def test_ahora_chile_es_naive(self):
        """Naive a proposito: se compara con horas de pared de DentiDesk."""
        self.assertIsNone(fechas.ahora_chile().tzinfo)

    def test_ahora_chile_aware_tiene_offset(self):
        self.assertIsNotNone(fechas.ahora_chile_aware().tzinfo)

    def test_hoy_chile_no_es_el_dia_siguiente(self):
        """A las 23:00 de Chile, UTC ya es manana. hoy_chile debe decir HOY."""
        ahora_cl = fechas.ahora_chile_aware()
        self.assertEqual(fechas.hoy_chile(), ahora_cl.date())

    def test_desfase_real_con_utc(self):
        """Chile esta 3 o 4 horas detras de UTC segun el horario de verano."""
        utc_offset = fechas.ahora_chile_aware().utcoffset()
        self.assertIn(utc_offset, (timedelta(hours=-3), timedelta(hours=-4)))


class TestAnticipacion(unittest.TestCase):
    """El corazon del bug: que horas se le ofrecen al paciente."""

    CFG = {'reglas': {'anticipacion_minima_horas': 12}}

    def test_hora_valida_ya_no_se_descarta(self):
        """Escenario real: son las 20:00 en Chile (= 00:00 UTC del dia siguiente).
        Una cita manana a las 09:00 esta a 13 horas: cumple el minimo de 12.

        Con el reloj UTC daba 9 horas y se descartaba."""
        ahora_chile = datetime(2026, 3, 10, 20, 0)          # 20:00 hora Chile
        manana = date(2026, 3, 11)
        self.assertTrue(
            scheduling.cumple_anticipacion(manana, '09:00', None, self.CFG, ahora_chile),
            'una cita a 13h de distancia debe ofrecerse con un minimo de 12h')

    def test_el_reloj_utc_habria_descartado_esa_hora(self):
        """Demuestra el bug antiguo: mismo instante, reloj UTC (4h adelante)."""
        ahora_utc = datetime(2026, 3, 11, 0, 0)             # el MISMO instante, en UTC
        manana = date(2026, 3, 11)
        self.assertFalse(
            scheduling.cumple_anticipacion(manana, '09:00', None, self.CFG, ahora_utc),
            'este es el comportamiento viejo que se corrigio')

    def test_hora_realmente_justa_sigue_rechazada(self):
        """No se afloja la regla: a 3 horas de distancia sigue sin ofrecerse."""
        ahora_chile = datetime(2026, 3, 10, 20, 0)
        self.assertFalse(
            scheduling.cumple_anticipacion(date(2026, 3, 10), '23:00', None,
                                           self.CFG, ahora_chile))

    def test_default_usa_hora_chile_no_utc(self):
        """Sin pasar `ahora`, la funcion debe tomar la hora de Chile.

        server.py llama a cumple_anticipacion() en 6 sitios y NUNCA pasa `ahora`,
        asi que el default es lo que corre en produccion."""
        falso_ahora = datetime(2026, 3, 10, 20, 0)
        with mock.patch.object(scheduling.fechas, 'ahora_chile', return_value=falso_ahora):
            self.assertTrue(
                scheduling.cumple_anticipacion(date(2026, 3, 11), '09:00', None, self.CFG))

    def test_horas_disponibles_libre_usa_hora_chile(self):
        """El otro camino (reagendar) tenia el mismo default naive."""
        cfg = {
            'reglas': {'anticipacion_minima_horas': 12},
            'horario': {'cierre': '19:30'},
            'doctores': {'octavio': {'ocupacion': {}}},
        }
        falso_ahora = datetime(2026, 3, 10, 20, 0)
        libres = ['09:00', '10:00', '11:00']
        with mock.patch.object(scheduling.fechas, 'ahora_chile', return_value=falso_ahora), \
             mock.patch.object(scheduling, 'aplicar_ocupacion_simulada',
                               side_effect=lambda *a, **k: list(libres)):
            horas = scheduling.horas_disponibles_libre(
                'octavio', date(2026, 3, 11), libres, [], cfg)
        self.assertEqual(horas, libres, 'con 13h+ de anticipacion no se descarta ninguna')


if __name__ == '__main__':
    unittest.main(verbosity=2)
