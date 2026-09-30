"""
test_vigilante_respaldos.py - Aviso cuando un respaldo lleva dias sin hacerse.

Cero red, cero Drive, cero correo: todo interceptado.

    cd admin && python test_vigilante_respaldos.py

Por que se prueba: el vigilante existe para que un respaldo roto no pase semanas
inadvertido. Si el mismo vigilante falla en silencio (no lee el estado, repite el aviso
todos los dias o se calla para siempre), el problema original sigue igual.
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='vigilante_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['VIGILANTE_RESPALDOS_PATH'] = str(_TMP / 'vigilante_respaldos.json')
os.environ['BACKUP_REGISTRO_PATH'] = str(_TMP / 'backup_registro.json')
sys.path.insert(0, str(Path(__file__).parent))

import backup                 # noqa: E402
import vigilante_respaldos as v   # noqa: E402
import notify                 # noqa: E402

HOY = date(2026, 9, 30)


def estado_digital1(dias_atras, resultado='COMPLETO'):
    f = date.fromordinal(HOY.toordinal() - dias_atras).isoformat()
    return {'ok': True, 'texto': f'Respaldo de DIGITAL1\nÚltima corrida: {f} 21:00 a 22:08\n'
                                 f'Resultado: {resultado}\n'}


def leer_de(dias_atras):
    return lambda nombre: estado_digital1(dias_atras)


def historial_render(*fechas_ok, fallos=()):
    reg = {'ultimo': {}, 'historial': (
        [{'fecha': f + 'T03:30:00', 'ok': True} for f in fechas_ok]
        + [{'fecha': f + 'T03:30:00', 'ok': False, 'error': 'x'} for f in fallos])}
    backup._STORE.save(reg)


class Base(unittest.TestCase):
    def setUp(self):
        v._STORE.save({})
        historial_render('2026-09-30')      # Render al dia salvo que el test diga otra cosa


class TestParseo(Base):
    def test_fecha_de_la_corrida(self):
        self.assertEqual(v.parsear_corrida('x\nÚltima corrida: 2026-09-29 21:00 a 22:08\n'),
                         date(2026, 9, 29))

    def test_texto_ilegible(self):
        self.assertIsNone(v.parsear_corrida('nada que ver'))
        self.assertIsNone(v.parsear_corrida(''))
        self.assertIsNone(v.parsear_corrida(None))


class TestDiasRender(Base):
    def test_cuenta_desde_el_ultimo_ok_no_desde_el_ultimo_intento(self):
        historial_render('2026-09-24', fallos=('2026-09-28', '2026-09-29', '2026-09-30'))
        dias, _ = v.dias_render(HOY)
        self.assertEqual(dias, 6)

    def test_sin_ningun_ok_cuenta_desde_el_primer_intento(self):
        historial_render(fallos=('2026-09-25', '2026-09-30'))
        self.assertEqual(v.dias_render(HOY)[0], 5)

    def test_sin_historial_no_hay_con_que_comparar(self):
        backup._STORE.save({'ultimo': {}, 'historial': []})
        self.assertIsNone(v.dias_render(HOY)[0])


class TestEvaluar(Base):
    def test_todo_al_dia_no_avisa(self):
        ev = v.evaluar(HOY, leer=leer_de(1))
        self.assertFalse(any(x['avisar'] for x in ev.values()))
        self.assertFalse(any(x['atrasado'] for x in ev.values()))

    def test_tres_dias_todavia_no(self):
        self.assertFalse(v.evaluar(HOY, leer=leer_de(3))['digital1']['avisar'])

    def test_cuatro_dias_si(self):
        d = v.evaluar(HOY, leer=leer_de(4))['digital1']
        self.assertTrue(d['atrasado'])
        self.assertTrue(d['avisar'])

    def test_render_atrasado_se_detecta(self):
        historial_render('2026-09-25')
        self.assertTrue(v.evaluar(HOY, leer=leer_de(1))['render']['avisar'])

    def test_estado_ilegible_cuenta_como_sin_verificar_y_no_avisa_el_primer_dia(self):
        ev = v.evaluar(HOY, leer=lambda n: {'ok': False, 'error': 'no encontrado'})
        d = ev['digital1']
        self.assertTrue(d['sin_verificar'])
        self.assertFalse(d['avisar'])


class TestRevisar(Base):
    def _revisar(self, hoy, dias_digital1):
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': True}) as m, \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            r = v.revisar(hoy=hoy, leer=leer_de(dias_digital1))
        return r, m

    def test_no_repite_el_aviso_al_dia_siguiente(self):
        _, m1 = self._revisar(HOY, 4)
        self.assertEqual(m1.call_count, 1)
        # Al dia siguiente la ultima corrida sigue siendo la misma -> 5 dias.
        historial_render('2026-10-01')
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': True}) as m2, \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            f = date(2026, 9, 26).isoformat()
            v.revisar(hoy=date(2026, 10, 1),
                      leer=lambda n: {'ok': True, 'texto': f'Última corrida: {f} 21:00'})
        self.assertEqual(m2.call_count, 0)

    def test_vuelve_a_avisar_a_los_ocho_dias(self):
        self._revisar(HOY, 4)
        historial_render('2026-10-04')
        f = date(2026, 9, 26).isoformat()
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': True}) as m, \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            v.revisar(hoy=date(2026, 10, 4),
                      leer=lambda n: {'ok': True, 'texto': f'Última corrida: {f} 21:00'})
        self.assertEqual(m.call_count, 1)

    def test_si_el_correo_falla_se_reintenta(self):
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': False, 'error': 'smtp'}), \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            r = v.revisar(hoy=HOY, leer=leer_de(4))
        self.assertTrue(r['correo'].startswith('error'))
        _, m = self._revisar(HOY, 4)
        self.assertEqual(m.call_count, 1)

    def test_al_recuperarse_se_reinicia_y_un_nuevo_corte_avisa_de_nuevo(self):
        self._revisar(HOY, 4)
        self._revisar(HOY, 0)                  # se recupero
        _, m = self._revisar(HOY, 4)           # vuelve a caerse
        self.assertEqual(m.call_count, 1)

    def test_un_solo_correo_con_los_dos_atrasados(self):
        historial_render('2026-09-20')
        r, m = self._revisar(HOY, 6)
        self.assertEqual(m.call_count, 1)
        self.assertEqual(len(m.call_args[0][1]), 2)

    def test_solo_consulta_no_envia_ni_guarda(self):
        with mock.patch.object(notify, 'avisar_respaldos_atrasados') as m:
            r = v.revisar(enviar=False, hoy=HOY, leer=leer_de(9))
        m.assert_not_called()
        self.assertEqual(v._STORE.load(), {})

    def test_sin_verificar_avisa_a_los_cuatro_dias(self):
        malo = lambda n: {'ok': False, 'error': 'no encontrado'}
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': True}) as m, \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            for d in range(27, 31):            # 27, 28, 29 y 30 de septiembre
                historial_render(date(2026, 9, d).isoformat())
                v.revisar(hoy=date(2026, 9, d), leer=malo)
        self.assertEqual(m.call_count, 0)
        historial_render('2026-10-01')
        with mock.patch.object(notify, 'avisar_respaldos_atrasados',
                               return_value={'ok': True}) as m, \
             mock.patch.object(v, '_destinatario', return_value='a@b.cl'):
            v.revisar(hoy=date(2026, 10, 1), leer=malo)    # 4 dias desde el 27
        self.assertEqual(m.call_count, 1)
        self.assertTrue(m.call_args[0][1][0]['sin_verificar'])


class TestCorreo(Base):
    def test_sin_smtp_no_falla_ni_envia(self):
        with mock.patch.dict(os.environ, {'SMTP_USER': '', 'SMTP_PASS': ''}):
            r = notify.avisar_respaldos_atrasados(
                'a@b.cl', [{'sistema': 'x', 'dias': 5, 'detalle': 'd'}], 4)
        self.assertFalse(r['ok'])

    def test_lista_vacia_no_envia(self):
        self.assertFalse(notify.avisar_respaldos_atrasados('a@b.cl', [], 4)['ok'])


def suite():
    s = unittest.TestSuite()
    for c in (TestParseo, TestDiasRender, TestEvaluar, TestRevisar, TestCorreo):
        s.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(c))
    return s


if __name__ == '__main__':
    r = unittest.TextTestRunner(verbosity=1).run(suite())
    sys.exit(0 if r.wasSuccessful() else 1)
