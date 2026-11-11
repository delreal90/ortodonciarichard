"""
test_seguros_api.py - El INTERRUPTOR CENTRAL del auto-envio de formularios de
seguro (panel -> Seguros -> Auto-envio).

Por que existe esta suite: el on/off vivia en el engranaje de CADA F2
(chrome.storage, por navegador), asi que apagarlo en un PC no apagaba los demas
y no habia forma de cortar todo de una. Ahora la extension siempre vigila y el
SERVIDOR decide. Si este gate se rompe, el auto-envio se reanudaria en SILENCIO
en toda la clinica -- por eso se fija con pruebas.

Cero red: DentiDesk deshabilitado y los stores en tempfiles (mismo patron que
test_link_agenda_api.py).

    cd admin && python test_seguros_api.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='seguros_api_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['SEGUROS_AUTO_CONFIG_PATH'] = str(_TMP / 'seguros_auto_config.json')
os.environ['SEGUROS_PACIENTES_PATH'] = str(_TMP / 'seguros_pacientes.json')
os.environ['SEGUROS_REGISTRO_PATH'] = str(_TMP / 'seguros_registro.json')
os.environ['SEGUROS_PRESTACIONES_PATH'] = str(_TMP / 'seguros_prestaciones.json')
# CRITICO: sin esto, importar server.py usa las credenciales REALES de
# DentiDesk si scheduling_secrets.json las tiene activas (ver CLAUDE.md).
os.environ['DENTIDESK_ENABLED'] = 'false'
os.environ.pop('RENDER', None)
os.environ.pop('RUN_PATIENT_SYNC', None)
sys.path.insert(0, str(Path(__file__).parent))

import server    # noqa: E402
import seguros   # noqa: E402

RUT = '14.141.411-9'          # RUT sintetico, DV valido (repo publico)


class _Base(unittest.TestCase):
    """Cliente autenticado + los avisos a recepcion interceptados (cero correo)."""

    def setUp(self):
        self.client = server.app.test_client()
        self._token_orig = os.environ.get('ADMIN_TOKEN')
        os.environ['ADMIN_TOKEN'] = 'token-de-prueba'
        self.avisos = []
        self._avisar_orig = server.notify.avisar_recepcion_seguro_no_enviado
        server.notify.avisar_recepcion_seguro_no_enviado = (
            lambda motivo, *a, **k: self.avisos.append(motivo))

    def tearDown(self):
        server.notify.avisar_recepcion_seguro_no_enviado = self._avisar_orig
        if self._token_orig is None:
            os.environ.pop('ADMIN_TOKEN', None)
        else:
            os.environ['ADMIN_TOKEN'] = self._token_orig

    def _auto(self, **body):
        datos = {'rut': RUT, 'glosa': 'CONTROL MENSUAL DE ORTODONCIA',
                 'monto': 146000, 'folio': '999001'}
        datos.update(body)
        return self.client.post('/api/seguro/auto-desde-boleta', json=datos,
                                headers={'X-Admin-Token': 'token-de-prueba'})


class TestInterruptorCentral(_Base):

    def test_apagado_no_envia_y_no_avisa(self):
        seguros.set_auto_config(activo=False)
        r = self._auto()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json().get('auto_apagado'))
        # Apagado NO es un error: no se molesta a recepcion.
        self.assertEqual(self.avisos, [])

    def test_apagado_manda_sobre_cualquier_paciente(self):
        """Ni siquiera un paciente con aseguradora asignada se envia."""
        seguros.guardar_paciente_seguro(RUT, aseguradora='zurich')
        seguros.set_auto_config(activo=False)
        r = self._auto(folio='999002')
        self.assertTrue(r.get_json().get('auto_apagado'))
        self.assertEqual(self.avisos, [])

    def test_encendido_pasa_el_gate(self):
        """Encendido, sigue el flujo normal: este paciente esta 'sin asignar'
        (silencioso, sin aviso), lo que prueba que el gate lo dejo pasar."""
        seguros.guardar_paciente_seguro('11.111.111-1', aseguradora='')
        seguros.set_auto_config(activo=True)
        r = self._auto(rut='11.111.111-1', folio='999003')
        d = r.get_json()
        self.assertFalse(d.get('auto_apagado'))
        self.assertTrue(d.get('sin_asignar'))
        self.assertEqual(self.avisos, [])


class TestAutoConfigEndpoint(_Base):

    def _cfg(self, **body):
        h = {'X-Admin-Token': 'token-de-prueba'}
        if body:
            return self.client.post('/api/seguro/auto-config', json=body, headers=h)
        return self.client.get('/api/seguro/auto-config', headers=h)

    def test_guarda_y_devuelve_activo(self):
        self.assertTrue(self._cfg(activo=True).get_json()['activo'])
        self.assertTrue(self._cfg().get_json()['activo'])
        # y apagarlo de verdad apaga (False no debe tratarse como "no vino")
        self.assertFalse(self._cfg(activo=False).get_json()['activo'])
        self.assertFalse(self._cfg().get_json()['activo'])

    def test_exige_token(self):
        self.assertEqual(self.client.get('/api/seguro/auto-config').status_code, 403)



class TestHistorialDelPanel(_Base):
    """El historial es lo que contesta "a este paciente, se le envio o no?".

    Nacio de un reclamo real: el apoderado de una paciente dijo que nunca le
    habia llegado el formulario y NO habia donde mirarlo -- el registro guarda
    solo el RUT, asi que buscarla por nombre no daba nada. Estas pruebas fijan
    que el historial traiga el NOMBRE y distinga "enviado" de "quedo en vista
    previa", que era justo el caso de esa paciente (29 PDF generados, 0 enviados).
    """

    def setUp(self):
        super().setUp()
        seguros._save(seguros.REGISTRO_PATH, {})
        import pacientes
        pacientes._STORE.save({'141414119': {'nombres': 'Ana Maria',
                                             'apellidos': 'Soto Vera',
                                             'email': 'ana@ejemplo.cl'}})
        self.gen = seguros.crear_registro({'rut': RUT, 'aseguradora': 'metlife',
                                      'doctor': 'alberto', 'email': 'ana@ejemplo.cl',
                                      'fecha_atencion': '2026-11-03',
                                      'prestaciones': [{'descripcion': 'CONTROL', 'valor': 50000},
                                                       {'descripcion': 'RX', 'valor': 25000}]})

    def _hist(self, qs=''):
        r = self.client.get('/api/seguro/admin/historial' + qs,
                            headers={'X-Admin-Token': 'token-de-prueba'})
        self.assertEqual(r.status_code, 200)
        return r.get_json()

    def test_trae_el_nombre_del_paciente(self):
        item = self._hist()['items'][0]
        self.assertIn('Ana Maria', item['nombre'])
        self.assertIn('Soto Vera', item['nombre'])

    def test_suma_el_total_y_cuenta_las_prestaciones(self):
        item = self._hist()['items'][0]
        self.assertEqual(item['total'], 75000)
        self.assertEqual(item['n_prestaciones'], 2)

    def test_distingue_enviado_de_solo_generado(self):
        d = self._hist()
        self.assertEqual(d['resumen']['sin_enviar'], 1)
        self.assertEqual(d['resumen']['enviados'], 0)
        seguros.marcar_enviado(self.gen, canal='email')
        d = self._hist()
        self.assertEqual(d['resumen']['sin_enviar'], 0)
        self.assertEqual(d['resumen']['enviados'], 1)

    def test_no_filtra_la_ruta_interna_del_pdf(self):
        # pdf_path es una ruta del disco del servidor: al panel solo va el flag
        item = self._hist()['items'][0]
        self.assertNotIn('pdf_path', item)
        self.assertIn('pdf_disponible', item)

    def test_exige_token(self):
        self.assertEqual(self.client.get('/api/seguro/admin/historial').status_code, 403)
        self.assertEqual(self.client.get('/api/seguro/admin/pdf-link?form_id=x').status_code, 403)

    def test_pdf_link_404_si_el_formulario_no_existe(self):
        r = self.client.get('/api/seguro/admin/pdf-link?form_id=noexiste',
                            headers={'X-Admin-Token': 'token-de-prueba'})
        self.assertEqual(r.status_code, 404)




class TestBorradoresNoSeDuplican(_Base):
    """Cada clic en "Vista previa del PDF" creaba un registro y un PDF nuevos.

    Caso real: una paciente quedo con 29 filas identicas en el historial (y 29
    PDF en el disco) porque se estuvieron ajustando los valores en la pagina.
    Con eso el historial no sirve para lo unico que importa: saber si al
    paciente se le envio su formulario o no.
    """

    def setUp(self):
        super().setUp()
        seguros._save(seguros.REGISTRO_PATH, {})

    def _borrador(self, **extra):
        datos = {'rut': RUT, 'aseguradora': 'metlife', 'doctor': 'alberto',
                 'fecha_atencion': '2026-11-03', 'email': 'ana@ejemplo.cl',
                 'prestaciones': [{'descripcion': 'CONTROL', 'valor': 50000}]}
        datos.update(extra)
        return seguros.crear_o_actualizar_borrador(datos)

    def test_previsualizar_dos_veces_deja_un_solo_registro(self):
        fid1, reusado1 = self._borrador()
        fid2, reusado2 = self._borrador(prestaciones=[{'descripcion': 'CONTROL', 'valor': 60000}])
        self.assertFalse(reusado1)
        self.assertTrue(reusado2)
        self.assertEqual(fid1, fid2)
        self.assertEqual(len(seguros.listar_registros(rut=RUT)), 1)
        # y se queda con lo ULTIMO que se previsualizo
        self.assertEqual(seguros.obtener_registro(fid1)['prestaciones'][0]['valor'], 60000)
        self.assertEqual(seguros.obtener_registro(fid1)['intentos'], 2)

    def test_el_token_del_pdf_sigue_sirviendo(self):
        # el form_id no cambia, asi que el link de la vista previa no se rompe
        fid, _ = self._borrador()
        token = seguros.generar_token_pdf(fid)
        self._borrador(prestaciones=[{'descripcion': 'OTRA', 'valor': 1}])
        self.assertEqual(seguros.validar_token_pdf(token)['form_id'], fid)

    def test_no_toca_lo_ya_enviado(self):
        """Un registro enviado es el comprobante de lo que salio: corregir y
        volver a generar despues de enviar crea uno NUEVO, no pisa el anterior."""
        fid1, _ = self._borrador()
        seguros.marcar_enviado(fid1, canal='email')
        fid2, reusado = self._borrador()
        self.assertFalse(reusado)
        self.assertNotEqual(fid1, fid2)
        self.assertEqual(seguros.obtener_registro(fid1)['estado'], 'enviado')

    def test_otra_fecha_o_aseguradora_es_otro_formulario(self):
        fid1, _ = self._borrador()
        fid2, _ = self._borrador(fecha_atencion='2026-11-10')
        fid3, _ = self._borrador(aseguradora='zurich')
        self.assertEqual(len({fid1, fid2, fid3}), 3)

    def test_compactar_deja_uno_por_formulario(self):
        # simula el historial que ya quedo sucio: varios registros sueltos
        ids = [seguros.crear_registro({'rut': RUT, 'aseguradora': 'metlife',
                                       'doctor': 'alberto', 'fecha_atencion': '2026-11-03',
                                       'prestaciones': [{'descripcion': 'X', 'valor': 1000}]})
               for _ in range(5)]
        enviado = seguros.crear_registro({'rut': RUT, 'aseguradora': 'metlife',
                                          'doctor': 'alberto', 'fecha_atencion': '2026-11-03',
                                          'prestaciones': []})
        seguros.marcar_enviado(enviado, canal='email')
        res = seguros.compactar_borradores()
        self.assertEqual(res['eliminados'], 4)
        quedan = seguros.listar_registros(rut=RUT)
        self.assertEqual(len(quedan), 2)                      # 1 borrador + 1 enviado
        self.assertIn(ids[-1], [q['id'] for q in quedan])     # se queda el ultimo
        self.assertIn(enviado, [q['id'] for q in quedan])     # el enviado intacto
        # idempotente
        self.assertEqual(seguros.compactar_borradores()['eliminados'], 0)

    def test_compactar_exige_token(self):
        self.assertEqual(
            self.client.post('/api/seguro/admin/compactar-borradores').status_code, 403)



if __name__ == '__main__':
    unittest.main(verbosity=2)
