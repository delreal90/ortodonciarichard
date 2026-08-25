"""
test_tamizaje_link.py - El QR con que el paciente contesta el cuestionario de
sueno desde su propio telefono, ahi mismo en la consulta.

Cero red: solo se firman y se leen tokens, y se arma el diccionario de
preguntas. El registro escribe en un tempfile.

    cd admin && python test_tamizaje_link.py

Lo que se protege:

  - Que el token sea de verdad una llave: adulterarlo no abre nada, y vence.
  - El corte de los 18 anios. No existe un STOP-BANG pediatrico validado: al
    menor le tiene que tocar el PSQ, siempre.
  - Que el cuestionario que ve el paciente y el que puntua el informe sean el
    MISMO instrumento. Se reformula como se PREGUNTA (el paciente lee preguntas,
    la hoja firmada lleva las afirmaciones clinicas), pero las claves de los
    items salen de stopbang.py / psq.py, que son sus duenios.
  - Que un borrador --el informe a medio llenar que queda al mostrar el QR
    apenas empieza la consulta-- NO aparezca en la lista de recepcion. Si
    apareciera, alguien podria entregarle al paciente un informe en blanco.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='tamizaje_link_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['INFORME_PC_REGISTRO_PATH'] = str(_TMP / 'informe_pc_registro.json')
os.environ['CONSENT_SECRET'] = 'secreto-de-prueba'
sys.path.insert(0, str(Path(__file__).parent))

import informe_pc      # noqa: E402
import psq             # noqa: E402
import stopbang        # noqa: E402
import tamizaje_link   # noqa: E402

INTERROGANTE = '¿'


class TestToken(unittest.TestCase):
    def test_ida_y_vuelta(self):
        t = tamizaje_link.crear_token('abc123', '11111111-1', 'Paciente Prueba', 9, 'M')
        d = tamizaje_link.leer_token(t)
        self.assertTrue(d['ok'])
        self.assertEqual(d['id'], 'abc123')
        self.assertEqual(d['nombre'], 'Paciente Prueba')
        self.assertEqual(d['edad'], 9)

    def test_adulterado_no_abre(self):
        t = tamizaje_link.crear_token('abc123', '11111111-1', 'Paciente', 9, 'M')
        d = tamizaje_link.leer_token(t[:-3] + 'xyz')
        self.assertFalse(d['ok'])
        self.assertEqual(d['motivo'], 'invalido')

    def test_vacio_y_basura(self):
        for malo in ('', None, 'no-es-un-token', 'a.b.c'):
            self.assertFalse(tamizaje_link.leer_token(malo)['ok'])

    def test_vencido_se_distingue_de_invalido(self):
        """Al paciente se le dice que pida uno nuevo, no que hizo algo malo."""
        t = tamizaje_link.crear_token('abc', '1-9', 'P', 9, 'M')
        real = tamizaje_link.VIGENCIA_SEGUNDOS
        try:
            # -1 en vez de dormir: itsdangerous compara segundos ENTEROS, asi
            # que un max_age de 1 con un sleep de 1,1 s daba edad 1 > 1 = False
            # y la prueba pasaba por casualidad segun donde cayera el reloj.
            tamizaje_link.VIGENCIA_SEGUNDOS = -1
            d = tamizaje_link.leer_token(t)
        finally:
            tamizaje_link.VIGENCIA_SEGUNDOS = real
        self.assertFalse(d['ok'])
        self.assertEqual(d['motivo'], 'expirado')

    def test_otro_secreto_no_abre(self):
        """Un token firmado con otra clave no sirve: es lo que impide fabricar
        uno para el informe de otro paciente."""
        t = tamizaje_link.crear_token('abc', '1-9', 'P', 9, 'M')
        os.environ['CONSENT_SECRET'] = 'otro-secreto'
        try:
            self.assertFalse(tamizaje_link.leer_token(t)['ok'])
        finally:
            os.environ['CONSENT_SECRET'] = 'secreto-de-prueba'


class TestQueCuestionarioToca(unittest.TestCase):
    def test_corte_18(self):
        self.assertEqual(tamizaje_link.tipo_para(17), 'psq')
        self.assertEqual(tamizaje_link.tipo_para(17.9), 'psq')
        self.assertEqual(tamizaje_link.tipo_para(18), 'stopbang')
        self.assertEqual(tamizaje_link.tipo_para(40), 'stopbang')

    def test_sin_edad_cae_al_psq(self):
        """No hay STOP-BANG pediatrico: ante la duda, el instrumento pediatrico.
        Aplicarle a un ninio el de adultos seria usar con el una escala que no
        existe para su edad."""
        for malo in (None, '', 'no-se'):
            self.assertEqual(tamizaje_link.tipo_para(malo), 'psq')


class TestFormulario(unittest.TestCase):
    def test_menor_recibe_el_psq_completo(self):
        f = tamizaje_link.formulario(9)
        self.assertEqual(f['tipo'], 'psq')
        self.assertEqual(len(f['preguntas']), len(psq.PREGUNTAS))
        self.assertEqual([p['id'] for p in f['preguntas']],
                         [p['id'] for p in psq.PREGUNTAS])

    def test_cada_pregunta_del_psq_trae_sus_opciones(self):
        """Sin opciones el telefono no puede pintar los botones, y la seccion de
        conducta no usa las mismas que el resto."""
        for p in tamizaje_link.formulario(9)['preguntas']:
            self.assertTrue(p['opciones'], p['id'])

    def test_adulto_solo_lo_que_el_puede_contestar(self):
        """El STOP-BANG tiene 8 items, pero cuatro los mide la clinica (cuello,
        edad, sexo, IMC). Preguntarle al paciente su circunferencia de cuello es
        pedirle un dato que no tiene."""
        f = tamizaje_link.formulario(34)
        ids = [p['id'] for p in f['preguntas']]
        self.assertEqual(f['tipo'], 'stopbang')
        self.assertEqual(ids, ['ronquido', 'cansancio', 'apneas', 'presion',
                               'peso', 'talla'])
        self.assertNotIn('cuello', ids)

    def test_las_claves_salen_de_stopbang(self):
        """Si stopbang.py renombra un item, esto tiene que romperse aca y no en
        silencio en produccion."""
        claves = {c for c, _l, _e, _t in stopbang.ITEMS}
        for p in tamizaje_link.formulario(34)['preguntas']:
            if p['id'] in ('peso', 'talla'):
                continue
            self.assertIn(p['id'], claves)

    def test_al_paciente_se_le_pregunta_no_se_le_afirma(self):
        """La hoja firmada lleva la afirmacion clinica; el telefono, la
        pregunta. Son el mismo item: lo que cambia es como se pregunta."""
        for p in tamizaje_link.formulario(34)['preguntas']:
            self.assertTrue(p['texto'].startswith(INTERROGANTE), p['id'])

    def test_todo_item_reformulado_existe_en_stopbang(self):
        """Un texto para el paciente cuya clave ya no existe seria una pregunta
        que no puntua nada."""
        claves = {c for c, _l, _e, _t in stopbang.ITEMS}
        for clave in tamizaje_link.TEXTO_PACIENTE:
            self.assertIn(clave, claves)

    def test_el_texto_clinico_es_el_respaldo(self):
        """Un item nuevo sin reformular igual se le muestra al paciente: sale
        con su texto clinico, que se lee raro pero se entiende. Quedarse sin
        pregunta seria peor."""
        real = dict(tamizaje_link.TEXTO_PACIENTE)
        try:
            tamizaje_link.TEXTO_PACIENTE.pop('ronquido')
            p = tamizaje_link.formulario(34)['preguntas'][0]
            self.assertEqual(p['id'], 'ronquido')
            self.assertTrue(p['texto'])
        finally:
            tamizaje_link.TEXTO_PACIENTE.clear()
            tamizaje_link.TEXTO_PACIENTE.update(real)

    def test_ambos_traen_su_nota_legal(self):
        """Es un tamizaje, no un diagnostico, y el paciente tiene que leerlo."""
        for edad in (9, 34):
            self.assertTrue(tamizaje_link.formulario(edad)['texto_legal'].strip())


class TestBorradorNoLlegaARecepcion(unittest.TestCase):
    """El QR se muestra apenas empieza la consulta, asi que deja guardado un
    informe sin impresion diagnostica. Ese borrador no puede terminar impreso."""

    def setUp(self):
        informe_pc._STORE.save({'informes': {}})

    def test_borrador_no_esta_entre_los_pendientes(self):
        hoy = informe_pc.fechas.hoy_chile().isoformat()
        informe_pc.guardar({'fecha': hoy, 'nombre': 'Borrador', 'rut': '1-9'})
        self.assertEqual(informe_pc.listar(hoy, solo_pendientes=True), [])

    def test_aparece_solo_al_completarlo(self):
        hoy = informe_pc.fechas.hoy_chile().isoformat()
        iid = informe_pc.guardar({'fecha': hoy, 'nombre': 'Borrador', 'rut': '1-9'})
        self.assertEqual(informe_pc.listar(hoy, solo_pendientes=True), [])
        informe_pc.guardar({'id': iid, 'fecha': hoy, 'nombre': 'Borrador',
                            'rut': '1-9', 'conclusion': 'corresponde'})
        self.assertEqual([i['id'] for i in informe_pc.listar(hoy, solo_pendientes=True)],
                         [iid])

    def test_el_borrador_igual_se_ve_en_la_lista_completa(self):
        """Se esconde de "pendiente de imprimir", no del dia: si el Dr. lo dejo
        a medias tiene que poder volver a el."""
        hoy = informe_pc.fechas.hoy_chile().isoformat()
        iid = informe_pc.guardar({'fecha': hoy, 'nombre': 'Borrador', 'rut': '1-9'})
        self.assertIn(iid, [i['id'] for i in informe_pc.listar(hoy)])


if __name__ == '__main__':
    unittest.main(verbosity=2)
