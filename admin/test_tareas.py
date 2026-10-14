"""
test_tareas.py - Bolsa de tareas para los tiempos muertos del personal.

Cero red, cero correo. Base SQLite temporal. Nombres INVENTADOS (repo público).

    cd admin && python test_tareas.py

Lo que duele si falla:
  - Proponer más trabajo del que cabe en el tiempo libre estimado.
  - Que dos personas tomen la misma tarea.
  - Que las tareas generadas aparezcan en la bolsa antes de que el Dr. apruebe.
  - Que lo administrativo del Dr. Vial se proponga un día que no es su bloque.
  - Que una plantilla se duplique al generar dos veces el mismo día.
"""

import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='tareas_test_'))
os.environ['COMPRAS_DB_PATH'] = str(_TMP / 'compras.db')
os.environ['COMPRAS_FOTOS_DIR'] = str(_TMP / 'fotos')
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['CLINICA_DB_PATH'] = str(_TMP / 'clinica.db')
sys.path.insert(0, str(Path(__file__).parent))

import compras   # noqa: E402
import tareas    # noqa: E402

MARTES = date(2026, 10, 13)
MIERCOLES = date(2026, 10, 14)
JUEVES = date(2026, 10, 15)


def _cita(doctor, hora, dur=60):
    return {'doctor': doctor, 'hora': hora, 'duracion': dur}


class _Base(unittest.TestCase):

    def setUp(self):
        for suf in ('', '-wal', '-shm'):
            p = Path(str(compras.DB_PATH) + suf)
            if p.exists():
                p.unlink()
        compras.init_db()
        tareas.init_db()
        self.p_orto = self._user('ana.prueba', 'Ana Prueba', 'asistente_orto')
        self.p_orto2 = self._user('bea.prueba', 'Bea Prueba', 'asistente_orto')
        self.p_sec = self._user('carla.prueba', 'Carla Prueba', 'secretaria')
        self.admin = {'id': compras.crear_usuario('doc', 'Doc Prueba', 'secreta1', rol='admin',
                                                  rol_tareas='admin'),
                      'rol': 'admin', 'puesto': '', 'rol_tareas': 'admin'}
        # Los generadores tocan otros módulos (agenda, consentimientos): fuera.
        self._gen = mock.patch.object(tareas, 'GENERADORES', {})
        self._gen.start()

    def tearDown(self):
        self._gen.stop()

    def _user(self, login, nombre, puesto):
        uid = compras.crear_usuario(login, nombre, 'secreta1', rol='equipo', puesto=puesto,
                                    rol_tareas='personal')
        return {'id': uid, 'rol': 'equipo', 'puesto': puesto, 'rol_tareas': 'personal'}

    def _encargo(self, titulo='Ordenar bodega', puesto='asistente_orto', minutos=30, **kw):
        return tareas.crear_encargo({'titulo': titulo, 'puesto': puesto, 'minutos': minutos, **kw})


class TestEstimacion(_Base):

    def test_sobran_asistentes_cuando_vienen_menos_doctores(self):
        # Jueves AM: 4 asistentes y solo 2 ortodoncistas con agenda llena.
        citas = [_cita('alberto', f'{h:02d}:45', 60) for h in range(8, 13)] + \
                [_cita('rodrigo', f'{h:02d}:45', 60) for h in range(8, 13)]
        e = tareas.estimar_libre(JUEVES, citas=citas)
        am = e['bloques']['AM']['asistente_orto']
        self.assertEqual(am['sobran'], 2)
        self.assertEqual(am['minutos'], 2 * (13 * 60 + 30 - (8 * 60 + 45)))

    def test_hueco_corto_no_cuenta(self):
        cfg = tareas.load_config()
        cfg['dotacion']['3']['AM']['asistente_orto'] = 1
        # Un solo doctor ocupado salvo 15 min: no llega al hueco mínimo de 30.
        citas = [_cita('alberto', '08:45', 270)]
        e = tareas.estimar_libre(MIERCOLES, cfg, citas=citas)
        self.assertEqual(e['bloques']['AM']['asistente_orto']['minutos'], 0)

    def test_sin_agenda_supone_doctores_atendiendo(self):
        e = tareas.estimar_libre(MIERCOLES, citas=[])
        self.assertTrue(e['sin_agenda'])
        self.assertEqual(e['bloques']['AM']['asistente_orto']['sobran'], 1)
        # Y a la asistente del Dr. Vial no se le inventa tiempo libre.
        self.assertEqual(e['bloques']['AM']['asistente_vial']['minutos'], 0)

    def test_lunes_am_sin_asistentes_de_orto(self):
        e = tareas.estimar_libre(date(2026, 10, 12), citas=[_cita('alberto', '15:00')])
        self.assertEqual(e['bloques']['AM']['asistente_orto']['personas'], 0)
        self.assertEqual(e['bloques']['AM']['asistente_orto']['minutos'], 0)
        self.assertEqual(e['bloques']['PM']['asistente_orto']['personas'], 4)

    def test_martes_am_es_bloque_admin_vial(self):
        e = tareas.estimar_libre(MARTES, citas=[_cita('alberto', '09:00')])
        self.assertTrue(e['admin_vial'])
        e = tareas.estimar_libre(MIERCOLES, citas=[_cita('alberto', '09:00')])
        self.assertFalse(e['admin_vial'])


class TestPropuesta(_Base):

    def test_nunca_propone_mas_de_lo_que_cabe(self):
        cfg = tareas.load_config()
        for i in range(30):
            self._encargo(f'Tarea {i}', minutos=45)
        libre = tareas.estimar_libre(MIERCOLES, cfg, citas=[])
        prop = tareas.armar_propuesta(MIERCOLES, cfg, citas=[])
        usado = sum(t['minutos'] for t in prop['tareas'] if t['puesto'] == 'asistente_orto')
        self.assertLessEqual(usado, libre['por_puesto']['asistente_orto'] * cfg['llenado_pct'] / 100)
        self.assertGreater(usado, 0)

    def test_prioridad_alta_primero(self):
        cfg = tareas.load_config()
        cfg['dotacion']['3'] = {'AM': {'asistente_orto': 4}, 'PM': {}}
        cfg['doctores_orto'] = ['alberto', 'rodrigo', 'octavio']
        # 1 asistente libre en la mañana = 285 min, 80 % = 228.
        baja = self._encargo('Baja', minutos=200, prioridad=3)
        alta = self._encargo('Alta', minutos=200, prioridad=1)
        prop = tareas.armar_propuesta(MIERCOLES, cfg, citas=[])
        self.assertEqual(prop['ids'], [alta])
        self.assertNotIn(baja, prop['ids'])

    def test_admin_vial_solo_su_dia(self):
        pid = tareas.crear_plantilla({'titulo': 'Presupuestos Dr. Vial', 'puesto': 'asistente_vial',
                                      'minutos': 30, 'frecuencia': 'diaria', 'admin_vial': True})
        tareas.generar_dia(MIERCOLES)
        prop = tareas.armar_propuesta(MIERCOLES, citas=[_cita('alberto', '09:00')])
        self.assertFalse([t for t in prop['tareas'] if t['plantilla_id'] == pid])
        tareas.generar_dia(MARTES)
        prop = tareas.armar_propuesta(MARTES, citas=[_cita('alberto', '09:00')])
        self.assertTrue([t for t in prop['tareas'] if t['plantilla_id'] == pid])

    def test_generadas_no_se_ven_antes_de_aprobar(self):
        tareas.crear_plantilla({'titulo': 'Reponer kits', 'puesto': 'asistente_orto',
                                'minutos': 20, 'frecuencia': 'diaria'})
        tareas.generar_dia(MIERCOLES)
        tareas.armar_propuesta(MIERCOLES, citas=[])
        self.assertEqual(tareas.bolsa(self.p_orto, MIERCOLES)['bolsa'], [])
        tareas.aprobar(MIERCOLES)
        b = tareas.bolsa(self.p_orto, MIERCOLES)['bolsa']
        self.assertEqual([t['titulo'] for t in b], ['Reponer kits'])
        self.assertTrue(b[0]['de_hoy'])

    def test_quitada_no_se_publica(self):
        tareas.crear_plantilla({'titulo': 'A', 'puesto': 'asistente_orto', 'minutos': 20,
                                'frecuencia': 'diaria'})
        tareas.generar_dia(MIERCOLES)
        prop = tareas.armar_propuesta(MIERCOLES, citas=[])
        tareas.aprobar(MIERCOLES, [{'id': prop['ids'][0], 'quitar': True}])
        self.assertEqual(tareas.bolsa(self.p_orto, MIERCOLES)['bolsa'], [])

    def test_aprobada_no_se_rehace(self):
        tareas.crear_plantilla({'titulo': 'A', 'puesto': 'asistente_orto', 'minutos': 20,
                                'frecuencia': 'diaria'})
        tareas.generar_dia(MIERCOLES)
        tareas.armar_propuesta(MIERCOLES, citas=[])
        tareas.aprobar(MIERCOLES, auto=True)
        self.assertEqual(tareas.armar_propuesta(MIERCOLES, citas=[])['estado'], 'auto')

    def test_token(self):
        tok = tareas.crear_token(MIERCOLES)
        self.assertEqual(tareas.leer_token(tok), {'ok': True, 'fecha': MIERCOLES.isoformat()})
        self.assertEqual(tareas.leer_token(tok + 'x')['motivo'], 'invalido')
        with mock.patch.object(tareas, 'VIGENCIA_TOKEN', -1):
            self.assertEqual(tareas.leer_token(tok)['motivo'], 'vencido')


class TestPlantillas(_Base):

    def test_no_duplica_el_mismo_dia(self):
        tareas.crear_plantilla({'titulo': 'X', 'puesto': 'aseo', 'frecuencia': 'diaria'})
        tareas.generar_dia(MIERCOLES)
        tareas.generar_dia(MIERCOLES)
        self.assertEqual(len(tareas.listar()), 1)

    def test_semanal_una_vez_por_semana(self):
        tareas.crear_plantilla({'titulo': 'Boxes a fondo', 'puesto': 'aseo',
                                'frecuencia': 'semanal', 'dias': [2]})
        for d in (MARTES, MIERCOLES, JUEVES):
            tareas.generar_dia(d)
        t = tareas.listar()
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]['vence'], '2026-10-17')

    def test_diaria_vence_al_dia_siguiente(self):
        tareas.crear_plantilla({'titulo': 'X', 'puesto': 'aseo', 'frecuencia': 'diaria'})
        tareas.generar_dia(MIERCOLES)
        tareas.generar_dia(JUEVES)
        estados = sorted((t['fecha'], t['estado']) for t in tareas.listar())
        self.assertEqual(estados, [('2026-10-14', 'vencida'), ('2026-10-15', 'pendiente')])

    def test_mensual_desde_su_dia(self):
        tareas.crear_plantilla({'titulo': 'Vencimientos', 'puesto': 'asistente_orto',
                                'frecuencia': 'mensual', 'dia_mes': 15})
        tareas.generar_dia(MIERCOLES)      # día 14: todavía no
        self.assertEqual(tareas.listar(), [])
        tareas.generar_dia(JUEVES)
        self.assertEqual(tareas.listar()[0]['vence'], '2026-10-31')

    def test_validaciones(self):
        with self.assertRaises(ValueError):
            tareas.crear_plantilla({'titulo': '', 'puesto': 'aseo'})
        with self.assertRaises(ValueError):
            tareas.crear_plantilla({'titulo': 'X', 'puesto': 'jardinero'})


class TestBolsa(_Base):

    def test_dos_personas_no_toman_la_misma(self):
        tid = self._encargo()
        tareas.tomar(tid, self.p_orto)
        with self.assertRaises(ValueError):
            tareas.tomar(tid, self.p_orto2)
        self.assertEqual(tareas.bolsa(self.p_orto2)['bolsa'], [])
        self.assertEqual(len(tareas.bolsa(self.p_orto)['mias']), 1)

    def test_otro_puesto_no_la_ve_ni_la_toma(self):
        tid = self._encargo(puesto='asistente_orto')
        self.assertEqual(tareas.bolsa(self.p_sec)['bolsa'], [])
        with self.assertRaises(ValueError):
            tareas.tomar(tid, self.p_sec)

    def test_asignada_a_otra_persona(self):
        self._encargo(asignada_a=self.p_orto['id'])
        self.assertEqual(len(tareas.bolsa(self.p_orto)['bolsa']), 1)
        self.assertEqual(tareas.bolsa(self.p_orto2)['bolsa'], [])

    def test_hecha_directo_y_registro(self):
        tid = self._encargo(minutos=20)
        tareas.terminar(tid, self.p_orto, 'listo')
        t = tareas.listar()[0]
        self.assertEqual((t['estado'], t['hecha_por'], t['nota']), ('hecha', self.p_orto['id'], 'listo'))
        with self.assertRaises(ValueError):
            tareas.terminar(tid, self.p_orto2)

    def test_soltar_vuelve_a_la_bolsa(self):
        tid = self._encargo()
        tareas.tomar(tid, self.p_orto)
        with self.assertRaises(ValueError):
            tareas.soltar(tid, self.p_orto2)
        tareas.soltar(tid, self.p_orto)
        self.assertEqual(len(tareas.bolsa(self.p_orto2)['bolsa']), 1)

    def test_sugerir_respeta_el_tiempo(self):
        self._encargo('Larga', minutos=60, prioridad=1)
        self._encargo('Corta', minutos=10, prioridad=3)
        self.assertEqual(tareas.sugerir(self.p_orto, 15)['titulo'], 'Corta')
        self.assertEqual(tareas.sugerir(self.p_orto, 60)['titulo'], 'Larga')
        self.assertIsNone(tareas.sugerir(self.p_orto, 5))

    def test_eliminar_usuario_conserva_la_tarea(self):
        tid = self._encargo()
        tareas.terminar(tid, self.p_orto)
        compras.eliminar_usuario(self.p_orto['id'])
        t = tareas.listar()[0]
        self.assertEqual((t['id'], t['estado'], t['hecha_por']), (tid, 'hecha', None))


class TestResumen(_Base):

    def test_resumen_por_persona(self):
        a = self._encargo(minutos=20)
        b = self._encargo(minutos=30)
        tareas.terminar(a, self.p_orto)
        tareas.terminar(b, self.p_orto)
        hoy = tareas._hoy()
        r = tareas.resumen(hoy, hoy)
        ana = next(p for p in r['personas'] if p['nombre'] == 'Ana Prueba')
        self.assertEqual((ana['hechas'], ana['minutos']), (2, 50))
        self.assertEqual(r['minutos_hechos']['asistente_orto'], 50)


class TestRolesSeparados(_Base):
    """Compras y Tareas se asignan por separado: el rol de uno no da acceso al otro."""

    def test_caps_independientes(self):
        caps = compras.caps_de
        self.assertEqual(caps({'rol': 'equipo', 'rol_tareas': 'personal'}), {'tareas'})
        self.assertEqual(caps({'rol': 'equipo', 'rol_tareas': 'admin'}), {'tareas', 'tareas_admin'})
        inv = caps({'rol': 'inventario', 'rol_tareas': ''})
        self.assertNotIn('tareas', inv)
        self.assertIn('stock', inv)
        # Un admin de Compras sin rol de Tareas no administra Tareas.
        self.assertNotIn('tareas_admin', caps({'rol': 'admin', 'rol_tareas': ''}))

    def test_migracion_respeta_lo_que_habia(self):
        con = compras._conn()
        try:
            con.execute("UPDATE usuarios SET rol_tareas=''")
            con.execute('ALTER TABLE usuarios DROP COLUMN rol_tareas')
            con.commit()
        finally:
            con.close()
        compras.init_db()
        rt = {u['email']: u['rol_tareas'] for u in compras.listar_usuarios()}
        self.assertEqual(rt['doc'], 'admin')            # admin de Compras → administraba Tareas
        self.assertEqual(rt['ana.prueba'], 'personal')  # tenía puesto → personal

    def test_sesion_trae_rol_tareas(self):
        tok = compras.crear_sesion(self.p_orto['id'])
        self.assertEqual(compras.usuario_por_sesion(tok)['rol_tareas'], 'personal')
        u = compras.verificar_login('ana.prueba', 'secreta1')
        self.assertEqual(u['rol_tareas'], 'personal')

    def test_rol_tareas_invalido(self):
        with self.assertRaises(ValueError):
            compras.crear_usuario('xx.prueba', 'X', 'secreta1', rol='equipo', rol_tareas='jefe')


class TestPersonasRutas(_Base):
    """Quien administra Tareas pero no Compras no puede tomar una cuenta de Compras."""

    def setUp(self):
        super().setUp()
        import server
        self.server = server
        self.c = server.app.test_client()
        jefa = compras.crear_usuario('jefa.prueba', 'Jefa Prueba', 'secreta1', rol='equipo',
                                     rol_tareas='admin')
        self.tok = compras.crear_sesion(jefa)
        self.jefa = jefa
        self.inv = compras.crear_usuario('inv.prueba', 'Inv Prueba', 'secreta1', rol='inventario')

    def _post(self, ruta, body):
        return self.c.post(ruta, json=body, headers={'X-Compras-Token': self.tok})

    def test_no_cambia_clave_de_usuario_de_compras(self):
        r = self._post('/api/compras/tareas/personas/actualizar', {'id': self.inv, 'password': 'nueva123'})
        self.assertEqual(r.status_code, 403)
        self.assertIsNotNone(compras.verificar_login('inv.prueba', 'secreta1'))

    def test_si_cambia_clave_de_quien_solo_usa_tareas(self):
        r = self._post('/api/compras/tareas/personas/actualizar', {'id': self.p_orto['id'], 'password': 'nueva123'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(compras.verificar_login('ana.prueba', 'nueva123'))

    def test_da_acceso_a_tareas_sin_tocar_compras(self):
        r = self._post('/api/compras/tareas/personas/actualizar',
                       {'id': self.inv, 'rol_tareas': 'personal', 'puesto': 'asistente_orto'})
        self.assertEqual(r.status_code, 200)
        u = next(x for x in compras.listar_usuarios() if x['id'] == self.inv)
        self.assertEqual((u['rol'], u['rol_tareas'], u['puesto']), ('inventario', 'personal', 'asistente_orto'))

    def test_crea_sin_acceso_a_compras(self):
        r = self._post('/api/compras/tareas/personas', {'nombre': 'Nueva Prueba', 'email': 'nueva.prueba',
                                                         'password': 'secreta1', 'puesto': 'aseo'})
        self.assertEqual(r.status_code, 200)
        u = compras.verificar_login('nueva.prueba', 'secreta1')
        self.assertEqual(compras.caps_de(u), {'tareas'})

    def test_no_entra_a_usuarios_de_compras(self):
        r = self.c.get('/api/compras/usuarios', headers={'X-Compras-Token': self.tok})
        self.assertEqual(r.status_code, 403)

    def test_personal_no_administra(self):
        tok = compras.crear_sesion(self.p_orto['id'])
        r = self.c.get('/api/compras/tareas/admin', headers={'X-Compras-Token': tok})
        self.assertEqual(r.status_code, 403)
        r = self.c.get('/api/compras/tareas', headers={'X-Compras-Token': tok})
        self.assertEqual(r.status_code, 200)

    def test_no_se_quita_a_si_misma_la_administracion(self):
        r = self._post('/api/compras/tareas/personas/actualizar', {'id': self.jefa, 'rol_tareas': 'personal'})
        self.assertEqual(r.status_code, 400)


class TestCatalogo(_Base):

    def test_catalogo_inicial_una_sola_vez(self):
        n = tareas.asegurar_catalogo_inicial()
        self.assertEqual(n, len(tareas.CATALOGO_SUGERIDO))
        pid = tareas.listar_plantillas()[0]['id']
        tareas.actualizar_plantilla(pid, {'activa': False})
        self.assertEqual(tareas.asegurar_catalogo_inicial(), 0)   # no las revive

    def test_catalogo_no_duplica_y_es_valido(self):
        n = tareas.cargar_catalogo()
        self.assertEqual(n, len(tareas.CATALOGO_SUGERIDO))
        self.assertEqual(tareas.cargar_catalogo(), 0)
        tareas.generar_dia(MARTES)        # todas sus frecuencias generan sin error
        self.assertTrue(tareas.listar())


class TestGeneradores(_Base):

    def test_un_generador_que_falla_no_frena_a_otro(self):
        def malo(d, cfg):
            raise RuntimeError('agenda caída')

        def bueno(d, cfg):
            return [{'ref': 'sis:x:' + d.isoformat(), 'titulo': 'Llamar 2', 'puesto': 'secretaria'}]
        with mock.patch.object(tareas, 'GENERADORES', {'malo': malo, 'bueno': bueno}):
            r = tareas.correr_generadores(MIERCOLES)
            r2 = tareas.correr_generadores(MIERCOLES)
        self.assertEqual(r['creadas'], 1)
        self.assertIn('malo', r['errores'])
        self.assertEqual(r2['creadas'], 0)       # misma ref: no duplica

    def test_stock_bajo_minimo(self):
        compras.crear_producto('Guantes M prueba', stock_minimo=5)
        with mock.patch.object(tareas, 'GENERADORES', {'stock': tareas._gen_stock}):
            tareas.correr_generadores(MIERCOLES)
        t = tareas.listar()
        self.assertEqual(len(t), 1)
        self.assertIn('Guantes M prueba', t[0]['detalle'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
