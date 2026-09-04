"""
test_fotos_finales.py - Aviso de collage post-tratamiento.

Cero red y cero correo: no se llama a DentiDesk ni a smtplib. Los tests arman
las citas a mano y las pasan por fotos_finales._aplicar_barrido (la misma logica
que usa barrer(), sin la parte que baja getAgendaDay). 'hoy' se pasa explicito.

    cd admin && python test_fotos_finales.py

Un fallo aca es un collage que nunca se avisa (caso terminado que queda sin su
antes/despues) o un aviso que llega cuando no corresponde -- por ejemplo el dia
que el paciente vino de urgencia con el retenedor suelto, cuando todavia no hay
fotos que valgan.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import date
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='fotosfin_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['DENTIDESK_ENABLED'] = 'false'
sys.path.insert(0, str(Path(__file__).parent))

import fotos_finales as ff   # noqa: E402
import pacientes             # noqa: E402

HOY = date(2026, 9, 4)
RUT = '11111111-1'


def _cita(rut, fecha, reason, status='Atendido', doctor='Alberto Del Real',
          nombre='Juan Perez Soto', id_agenda=None):
    return {
        'IdAgenda': id_agenda or f'{rut}-{fecha}-{reason}',
        'PatientDocument': rut,
        'PatientName': nombre,
        'ProfessionalName': doctor,
        'Reason': reason,
        'Status': status,
        'Date': fecha,
    }


def _barrer(citas_por_dia, hoy=HOY, cfg=None, reg=None, solo_sembrar=False):
    """citas_por_dia: {fecha_iso: [citas]} -> corre _aplicar_barrido y devuelve
    el registro. Si se pasa `reg`, acumula sobre el (para probar dos barridos
    seguidos, que es como corre de verdad, un dia tras otro)."""
    cfg = cfg or ff.load_config()
    if reg is None:
        ff._save_registro({})
        reg = ff._load_registro()
    resultados = [(date.fromisoformat(f), cs) for f, cs in sorted(citas_por_dia.items())]
    ff._aplicar_barrido(reg, cfg, resultados, hoy, solo_sembrar=solo_sembrar)
    ff._save_registro(reg)
    return reg


def _inscribir(rut, nombre='', nota='', cuando='2026-09-01'):
    """Inscribe en la watchlist con fecha FIJA. agregar_watchlist() sella el dia
    con fechas.hoy_chile(), asi que sin esto los tests dependerian del reloj de
    la maquina y empezarian a fallar solos al dia siguiente."""
    with mock.patch('fechas.hoy_chile', return_value=date.fromisoformat(cuando)):
        return ff.agregar_watchlist(rut, nombre, nota)


def _pendientes(reg):
    return sorted(reg.get('pendientes', {}).values(), key=lambda p: p['fecha_control'])


class TestClasificacion(unittest.TestCase):
    def test_retiros_reconocidos(self):
        for m in ('Retiro Total', 'retiro digitrack', 'Retiro Invisalign',
                  'Retiro Clear Correct', 'Retiro Total + Inicio'):
            self.assertEqual(ff.clasificar_motivo(m), 'retiro', m)

    def test_urgencias_reconocidas(self):
        """Los nombres reales que usa la clinica, con sus variantes."""
        for m in ('Retenedor Fijo Suelto / Roto', 'Essix / Placa Perdida',
                  'Placa/Essix Roto / Desajustado', 'Tornillo Suelto con Dolor'):
            self.assertEqual(ff.clasificar_motivo(m), 'urgencia', m)

    def test_control_post_retiro_no_es_retiro(self):
        """La razon por la que este modulo NO reusa control_dental._FIN_DEFINITIVO:
        alli 'Control Contencion' y 'Retenedor Fijo' cuentan como fin de
        tratamiento. Aca son la cita de DESTINO -- si se leyeran como un retiro
        nuevo, el aviso no saldria nunca."""
        for m in ('Control Contención', 'Control Removible', 'Impresión p/Essix',
                  'Aligner / Essix', 'Retenedor Fijo'):
            self.assertIsNone(ff.clasificar_motivo(m), m)

    def test_motivos_extra_mandan_sobre_las_constantes(self):
        cfg = dict(ff.load_config(), motivos_extra={'retiro total': 'ignorar',
                                                    'placa': 'retiro'})
        self.assertIsNone(ff.clasificar_motivo('Retiro Total', cfg))
        self.assertEqual(ff.clasificar_motivo('Placa', cfg), 'retiro')

    def test_doctor_con_y_sin_titulo(self):
        cfg = ff.load_config()
        self.assertTrue(ff.es_del_doctor('Alberto Del Real', cfg))
        self.assertTrue(ff.es_del_doctor('Dr. Alberto Del Real', cfg))
        self.assertFalse(ff.es_del_doctor('Rodrigo Oyonarte', cfg))


class TestDeteccion(unittest.TestCase):
    def test_primera_cita_tras_el_retiro_dispara(self):
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        })
        p = _pendientes(reg)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['origen'], 'retiro')
        self.assertEqual(p[0]['fecha_retiro'], '2026-07-28')
        self.assertEqual(p[0]['motivo_control'], 'Control Contención')

    def test_la_segunda_cita_ya_no_dispara(self):
        """Un solo aviso por paciente: si no, cada control posterior volveria a
        proponer el mismo collage."""
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        })
        ff.marcar_avisados([ff._rut_key(RUT)], HOY)
        reg = ff._load_registro()
        _barrer({'2026-09-02': [_cita(RUT, '2026-09-02', 'Control Removible')]},
                reg=reg)
        self.assertEqual(_pendientes(ff._load_registro()), [])

    def test_una_urgencia_no_dispara_pero_la_siguiente_si(self):
        """El caso medido: 15 de 302 pacientes volvieron primero por una
        urgencia. Esa visita no trae fotos utiles y NO debe consumir el aviso."""
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-11': [_cita(RUT, '2026-08-11', 'Retenedor Fijo Suelto / Roto')],
        })
        self.assertEqual(_pendientes(reg), [])
        _barrer({'2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')]}, reg=reg)
        p = _pendientes(ff._load_registro())
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['fecha_control'], '2026-08-25')

    def test_antes_del_minimo_no_dispara(self):
        """A los 3 dias del retiro la encia no esta sana: esa foto no sirve."""
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-07-31': [_cita(RUT, '2026-07-31', 'Control Contención')],
        })
        self.assertEqual(_pendientes(reg), [])

    def test_despues_del_maximo_no_dispara(self):
        """Un control un anio despues ya no es 'el control post-retiro'."""
        reg = _barrer({
            '2025-07-28': [_cita(RUT, '2025-07-28', 'Retiro Total')],
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Control Contención')],
        })
        self.assertEqual(_pendientes(reg), [])

    def test_sin_retiro_previo_no_dispara(self):
        reg = _barrer({'2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')]})
        self.assertEqual(_pendientes(reg), [])

    def test_solo_el_doctor_configurado(self):
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total', doctor='Rodrigo Oyonarte')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención', doctor='Rodrigo Oyonarte')],
        })
        self.assertEqual(_pendientes(reg), [])

    def test_cita_que_no_ocurrio_se_ignora(self):
        """Una cita cancelada o a la que el paciente no llego no prueba nada."""
        for status in ('Hora Cancelada', 'Paciente no llega', 'Re-agendado'):
            reg = _barrer({
                '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
                '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención', status=status)],
            })
            self.assertEqual(_pendientes(reg), [], status)

    def test_cita_no_confirmada_todavia_no_cuenta(self):
        """Solo 'Atendido' prueba que el paciente vino de verdad."""
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención', status='No confirmado')],
        })
        self.assertEqual(_pendientes(reg), [])

    def test_barrido_repetido_es_idempotente(self):
        """El barrido re-mira 45 dias hacia atras todos los dias (la clinica marca
        'Atendido' despues de la visita). Correrlo dos veces no puede duplicar."""
        citas = {
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        }
        reg = _barrer(citas)
        _barrer(citas, reg=reg)
        self.assertEqual(len(_pendientes(ff._load_registro())), 1)

    def test_retiro_mas_reciente_manda(self):
        """Paciente que termina una fase, vuelve a tratamiento y se retira de
        nuevo: el control se mide contra el ultimo retiro, no el primero."""
        reg = _barrer({
            '2025-01-10': [_cita(RUT, '2025-01-10', 'Retiro Total')],
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Digitrack')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        })
        p = _pendientes(reg)
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['fecha_retiro'], '2026-07-28')


class TestWatchlist(unittest.TestCase):
    def setUp(self):
        ff._save_registro({})

    def test_dispara_sin_retiro_previo(self):
        """El caso que pidio el usuario: 'cuando venga tal paciente,
        recuerdamelo'. Su retiro puede ser anterior a que existiera el sistema,
        asi que no se le puede exigir uno visto por el barrido."""
        _inscribir(RUT, 'Juan Perez Soto', 'terminó con Digitrack')
        reg = ff._load_registro()
        _barrer({'2026-09-03': [_cita(RUT, '2026-09-03', 'Control Removible')]}, reg=reg)
        p = _pendientes(ff._load_registro())
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]['origen'], 'watchlist')
        self.assertEqual(p[0]['nota'], 'terminó con Digitrack')

    def test_no_dispara_con_una_cita_anterior_a_la_inscripcion(self):
        """Inscribirlo hoy no debe avisar por una cita de la semana pasada: lo
        que se pidio es 'cuando VENGA', no 'que ya vino'."""
        _inscribir(RUT, 'Juan Perez Soto')
        reg = ff._load_registro()
        _barrer({'2026-08-20': [_cita(RUT, '2026-08-20', 'Control Removible')]}, reg=reg)
        self.assertEqual(_pendientes(ff._load_registro()), [])

    def test_una_urgencia_tampoco_dispara_al_inscrito(self):
        _inscribir(RUT, 'Juan Perez Soto')
        reg = ff._load_registro()
        _barrer({'2026-09-03': [_cita(RUT, '2026-09-03', 'Essix / Placa Perdida')]}, reg=reg)
        self.assertEqual(_pendientes(ff._load_registro()), [])

    def test_avisar_lo_saca_de_la_watchlist(self):
        """Ya cumplio su proposito: si se quedara, avisaria en cada cita futura."""
        _inscribir(RUT, 'Juan Perez Soto')
        reg = ff._load_registro()
        _barrer({'2026-09-03': [_cita(RUT, '2026-09-03', 'Control Removible')]}, reg=reg)
        ff.marcar_avisados([ff._rut_key(RUT)], HOY)
        self.assertEqual(ff.listar_watchlist(), [])

    def test_rut_en_cualquier_formato_es_el_mismo_paciente(self):
        _inscribir('11.111.111-1', 'Juan Perez Soto')
        self.assertTrue(ff.quitar_watchlist('111111111'))


class TestCierre(unittest.TestCase):
    def setUp(self):
        ff._save_registro({})
        _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        })

    def test_marcar_avisados_mueve_a_historial(self):
        ff.marcar_avisados([ff._rut_key(RUT)], HOY)
        reg = ff._load_registro()
        self.assertEqual(reg['pendientes'], {})
        self.assertEqual(len(reg['historial']), 1)
        self.assertEqual(reg['historial'][0]['avisado'], HOY.isoformat())

    def test_descartar_no_vuelve_a_proponer(self):
        ff.descartar(RUT)
        reg = ff._load_registro()
        self.assertEqual(reg['pendientes'], {})
        _barrer({'2026-09-01': [_cita(RUT, '2026-09-01', 'Control Removible')]},
                reg=reg)
        self.assertEqual(_pendientes(ff._load_registro()), [])

    def test_pendientes_respeta_el_tope(self):
        cfg = dict(ff.load_config(), max_por_correo=1)
        ff._save_registro({})
        _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total'),
                           _cita('11111111-1', '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención'),
                           _cita('11111111-1', '2026-08-25', 'Control Removible')],
        })
        self.assertEqual(len(ff.pendientes(cfg)), 1)


class TestBackfill(unittest.TestCase):
    def test_solo_siembra_retiros_sin_avisar(self):
        """La leccion de confirmaciones.py: la primera corrida NO puede mandar
        de golpe los controles de medio anio."""
        reg = _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        }, solo_sembrar=True)
        self.assertEqual(_pendientes(reg), [])
        self.assertIn(ff._rut_key(RUT), reg['retiros'])

    def test_tras_el_backfill_el_barrido_normal_si_avisa(self):
        """Sembrado el retiro, un control POSTERIOR (el de manana) si dispara."""
        reg = _barrer({'2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')]},
                      solo_sembrar=True)
        _barrer({'2026-09-04': [_cita(RUT, '2026-09-04', 'Control Contención')]}, reg=reg)
        self.assertEqual(len(_pendientes(ff._load_registro())), 1)


class TestEnvio(unittest.TestCase):
    """El correo se prueba sin tocar smtplib: interesa la decision de marcar o
    no, no el SMTP."""

    def setUp(self):
        ff._save_registro({})
        _barrer({
            '2026-07-28': [_cita(RUT, '2026-07-28', 'Retiro Total')],
            '2026-08-25': [_cita(RUT, '2026-08-25', 'Control Contención')],
        })

    def test_fallo_de_smtp_no_marca_avisado(self):
        """Un problema de red no puede consumir el aviso: el candidato sigue
        pendiente y se reintenta al dia siguiente."""
        import notify
        with mock.patch.dict(os.environ, {'SMTP_USER': '', 'SMTP_PASS': ''}):
            r = notify.avisar_collage_pendiente('doctor@ejemplo.cl', ff.pendientes())
        self.assertFalse(r['ok'])
        self.assertEqual(len(ff.pendientes()), 1)   # sigue ahi

    def test_lista_vacia_no_manda_correo(self):
        import notify
        self.assertFalse(notify.avisar_collage_pendiente('doctor@ejemplo.cl', [])['ok'])

    def test_el_html_nombra_al_paciente_y_su_retiro(self):
        import notify
        html = notify._filas_collage(ff.pendientes())
        self.assertIn('Juan Perez Soto', html)
        self.assertIn('Control Contención', html)
        self.assertIn('28 de julio', html)

    def test_el_html_del_inscrito_a_mano_muestra_la_nota(self):
        import notify
        ff._save_registro({})
        _inscribir(RUT, 'Juan Perez Soto', 'terminó con Digitrack')
        reg = ff._load_registro()
        _barrer({'2026-09-03': [_cita(RUT, '2026-09-03', 'Control Removible')]}, reg=reg)
        html = notify._filas_collage(ff.pendientes())
        self.assertIn('terminó con Digitrack', html)


class TestBuscarPorNombre(unittest.TestCase):
    """La pieza que permite inscribir 'diciendo el nombre'."""

    def setUp(self):
        pacientes._save_index({
            '111111111': {'nombres': 'Juan', 'apellidos': 'Pérez Soto'},
            '222222222': {'nombres': 'María José', 'apellidos': 'Pérez Lira'},
            '333333333': {'nombres': 'Juan', 'apellidos': 'Ramírez Lira'},
        })

    def tearDown(self):
        pacientes._save_index({})

    def test_encuentra_por_nombre_y_apellido_en_cualquier_orden(self):
        for q in ('juan perez', 'perez juan', 'Pérez Soto Juan'):
            r = pacientes.buscar_por_nombre(q)
            self.assertEqual([p['rut'] for p in r], ['111111111'], q)

    def test_ignora_tildes(self):
        self.assertEqual(len(pacientes.buscar_por_nombre('maria jose perez')), 1)

    def test_prefijo_acota(self):
        """'juan' trae dos; agregar apellido deja uno."""
        self.assertEqual(len(pacientes.buscar_por_nombre('juan')), 2)
        self.assertEqual(len(pacientes.buscar_por_nombre('juan per')), 1)

    def test_consulta_vacia_no_devuelve_la_base_entera(self):
        self.assertEqual(pacientes.buscar_por_nombre(''), [])
        self.assertEqual(pacientes.buscar_por_nombre('   '), [])


class TestConfig(unittest.TestCase):
    def tearDown(self):
        if ff.CONFIG_PATH.exists():
            ff.CONFIG_PATH.unlink()

    def test_arranca_apagado(self):
        self.assertFalse(ff.load_config()['activo'])

    def test_save_preserva_lo_no_enviado(self):
        ff.save_config({'activo': True})
        cfg = ff.save_config({'dias_minimos': 15})
        self.assertTrue(cfg['activo'])
        self.assertEqual(cfg['dias_minimos'], 15)

    def test_ventana_invalida_vuelve_al_default(self):
        """dias_minimos >= dias_maximos dejaria el sistema mudo sin avisar."""
        cfg = ff.save_config({'dias_minimos': 200, 'dias_maximos': 30})
        self.assertLess(cfg['dias_minimos'], cfg['dias_maximos'])

    def test_motivos_extra_solo_acepta_categorias_validas(self):
        cfg = ff.save_config({'motivos_extra': {'Placa': 'retiro', 'Otra': 'basura'}})
        self.assertEqual(cfg['motivos_extra'], {'placa': 'retiro'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
