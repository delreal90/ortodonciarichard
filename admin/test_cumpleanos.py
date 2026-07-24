"""
test_cumpleanos.py - Pruebas del Milestone 0 (fechas de nacimiento + cumpleanos)

Cero red y cero correo: todo corre contra una base temporal.

    cd admin && python test_cumpleanos.py

Cubre:
  - Parseo del export de cumpleanos (tabla HTML disfrazada de .xls).
  - Saneo de fechas implausibles.
  - Que el barrido de agenda NO borre los campos nuevos (el bug historico).
  - Fallback de fecha de nacimiento en Seguros (y que lo manual siempre mande).
  - Calculo de edad, borde del 29 de febrero y borde de fin de anio.
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from datetime import date

_TMP = Path(tempfile.mkdtemp(prefix='cumple_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['CUMPLEANOS_EQUIPO_PATH'] = str(_TMP / 'cumpleanos_equipo.json')
sys.path.insert(0, str(Path(__file__).parent))

import pacientes           # noqa: E402
import cumpleanos          # noqa: E402
import seguros             # noqa: E402


def _tabla_html(filas):
    """Arma un export como el de DentiDesk: tabla HTML, fecha en <th>."""
    cuerpo = ''.join(
        f'<tr><td><a href="ficha.php?id_paciente={i}">{n}</a></td>'
        f'<td class="text-right">{r}</td><td></td><td></td>'
        f'<th>{f}</th><td class="text-right">0</td></tr>'
        for i, (n, r, f) in enumerate(filas, start=1000))
    return ('<table id="tabla_pacientes"><thead><tr><th>Nombre</th><th>Rut</th>'
            '<th>Tel&eacute;fono</th><th>Correo</th><th>Fecha Nacimiento</th>'
            f'<th>Edad</th></tr></thead><tbody>{cuerpo}</tbody></table>')


def _escribir(tabla):
    p = _TMP / 'export.xls'
    p.write_text(tabla, encoding='utf-8')
    return str(p)


class TestImportador(unittest.TestCase):

    def setUp(self):
        pacientes.vaciar()

    def test_importa_fecha_e_id_paciente(self):
        ruta = _escribir(_tabla_html([('Perez Gomez Juan', '11.111.111-1', '05/03/1990')]))
        res = pacientes.importar_cumpleanos(ruta)
        self.assertEqual(res['nuevos'], 1)
        rec = pacientes.lookup('11.111.111-1')
        self.assertEqual(rec['fecha_nacimiento'], '1990-03-05')   # dd/mm/yyyy -> ISO
        self.assertTrue(rec['id_paciente'])
        self.assertEqual(rec['nombres'], 'Juan')
        self.assertEqual(rec['apellidos'], 'Perez Gomez')

    def test_descarta_fechas_implausibles(self):
        ruta = _escribir(_tabla_html([
            ('A B Uno',  '11.111.111-1', '01/01/1700'),   # 300+ anios
            ('C D Dos',  '22.222.222-2', '01/01/2090'),   # futura
            ('E F Tres', '33.333.333-3', '31/02/1990'),   # no existe
            ('G H Cuatro', '44.444.444-4', '10/10/2010'),  # valida
        ]))
        res = pacientes.importar_cumpleanos(ruta)
        self.assertEqual(res['nuevos'], 1)
        self.assertEqual(res['fecha_invalida'], 3)
        self.assertIsNone(pacientes.lookup('11.111.111-1'))
        self.assertEqual(pacientes.lookup('44.444.444-4')['fecha_nacimiento'], '2010-10-10')

    def test_no_pisa_datos_del_excel_principal(self):
        """El import de cumpleanos solo agrega DOB/id_paciente."""
        pacientes._save_index({'111111111': {
            'nombres': 'Juan', 'apellidos': 'Perez', 'email': 'j@p.cl',
            'telefono': '+56900000000', 'genero': 'M', 'direccion': 'Calle 1',
            'comuna': 'Las Condes', 'prevision': 'Isapre', 'convenio': 'X'}})
        ruta = _escribir(_tabla_html([('OTRO NOMBRE', '11.111.111-1', '05/03/1990')]))
        res = pacientes.importar_cumpleanos(ruta)
        self.assertEqual(res['actualizados'], 1)
        rec = pacientes.lookup('11.111.111-1')
        self.assertEqual(rec['fecha_nacimiento'], '1990-03-05')
        for campo, esperado in (('email', 'j@p.cl'), ('genero', 'M'),
                                ('direccion', 'Calle 1'), ('comuna', 'Las Condes'),
                                ('nombres', 'Juan')):
            self.assertEqual(rec[campo], esperado, f'{campo} fue pisado')

    def test_rut_repetido_en_el_archivo_se_reporta_aparte(self):
        ruta = _escribir(_tabla_html([
            ('A B Uno', '11.111.111-1', '05/03/1990'),
            ('A B Uno', '11.111.111-1', '05/03/1990'),   # ficha duplicada
        ]))
        res = pacientes.importar_cumpleanos(ruta)
        self.assertEqual(res['nuevos'], 1)
        self.assertEqual(res['duplicados_archivo'], 1)
        self.assertEqual(res['actualizados'], 0)         # no es una actualizacion

    def test_es_idempotente(self):
        ruta = _escribir(_tabla_html([('A B Uno', '11.111.111-1', '05/03/1990')]))
        pacientes.importar_cumpleanos(ruta)
        res = pacientes.importar_cumpleanos(ruta)
        self.assertEqual(res['nuevos'], 0)
        self.assertEqual(res['actualizados'], 1)
        self.assertEqual(pacientes.total(), 1)


class TestBarridoNoBorra(unittest.TestCase):
    """El bug historico: getAgendaDay trae 4 campos y un update() plano
    borraba el resto de la ficha. El barrido corre 2x/dia."""

    def test_construir_desde_agenda_preserva_campos_nuevos(self):
        import requests
        import dentidesk
        pacientes._save_index({'111111111': {
            'nombres': 'Juan', 'apellidos': 'Perez', 'email': 'viejo@p.cl',
            'telefono': '', 'genero': 'M', 'comuna': 'Las Condes',
            'fecha_nacimiento': '1990-03-05', 'id_paciente': '12345'}})

        class FakeResp:
            status_code = 200
            def json(self):
                return {'data': [{'PatientDocument': '11.111.111-1',
                                  'PatientEmail': 'nuevo@p.cl',
                                  'PatientName': 'Perez Gomez Juan',
                                  'Phone': '+56911112222'}]}

        orig_auth, orig_post = dentidesk._auth_token, requests.post
        dentidesk._auth_token = lambda cfg: 'tok'
        requests.post = lambda *a, **k: FakeResp()
        try:
            pacientes.construir_desde_agenda(
                {'dentidesk': {'base_url': 'https://x', 'id_location': 408}},
                dias_atras=0, dias_adelante=0)
        finally:
            dentidesk._auth_token, requests.post = orig_auth, orig_post

        rec = pacientes.lookup('11.111.111-1')
        self.assertEqual(rec['fecha_nacimiento'], '1990-03-05')
        self.assertEqual(rec['id_paciente'], '12345')
        self.assertEqual(rec['comuna'], 'Las Condes')
        self.assertEqual(rec['email'], 'nuevo@p.cl')      # esto SI se actualiza


class TestEdadYCumpleanos(unittest.TestCase):

    def setUp(self):
        pacientes.vaciar()

    def test_edad_a_fecha(self):
        self.assertEqual(pacientes.edad_a_fecha('1990-03-05', date(2026, 3, 4)), 35)
        self.assertEqual(pacientes.edad_a_fecha('1990-03-05', date(2026, 3, 5)), 36)
        self.assertEqual(pacientes.edad_a_fecha('no-es-fecha'), -1)

    def test_cumplen_el_devuelve_anios_que_cumple(self):
        pacientes._save_index({'111111111': {
            'nombres': 'Juan', 'apellidos': 'Perez', 'fecha_nacimiento': '1990-03-05'}})
        r = pacientes.cumplen_el(date(2026, 3, 5))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['edad'], 36)
        self.assertEqual(pacientes.cumplen_el(date(2026, 3, 6)), [])

    def test_borde_fin_de_anio(self):
        """Visto desde el 31-dic, el cumple del 1-ene es del anio SIGUIENTE."""
        pacientes._save_index({'111111111': {
            'nombres': 'Ana', 'apellidos': 'Soto', 'fecha_nacimiento': '2000-01-01'}})
        r = pacientes.cumplen_el(date(2027, 1, 1))
        self.assertEqual(r[0]['edad'], 27)

    def test_29_de_febrero_se_saluda_el_28_en_anio_no_bisiesto(self):
        pacientes._save_index({'111111111': {
            'nombres': 'Leap', 'apellidos': 'Year', 'fecha_nacimiento': '2000-02-29'}})
        self.assertEqual(len(pacientes.cumplen_el(date(2027, 2, 28))), 1)   # no bisiesto
        self.assertEqual(len(pacientes.cumplen_el(date(2028, 2, 28))), 0)   # bisiesto
        self.assertEqual(len(pacientes.cumplen_el(date(2028, 2, 29))), 1)

    def test_cobertura(self):
        pacientes._save_index({
            '1': {'nombres': 'A', 'fecha_nacimiento': '1990-01-01'},
            '2': {'nombres': 'B'},
        })
        self.assertEqual(pacientes.cobertura_fecha_nacimiento(),
                         {'total': 2, 'con_fecha': 1, 'pct': 50.0})


class TestEquipo(unittest.TestCase):

    TABLA = """
| Nombre               | Fecha de nacimiento |
| -------------------- | ------------------- |
| Rodrigo Oyonarte     | 13/04/1973          |
| Octavio Del Real     | 29/07/1954          |
| Felipe Pozo          | PENDIENTE           |
"""

    def test_parsea_y_marca_pendientes(self):
        res = cumpleanos.importar_equipo(self.TABLA)
        self.assertEqual(res['total'], 3)
        self.assertEqual(res['con_fecha'], 2)
        self.assertEqual(res['pendientes'], ['Felipe Pozo'])

    def test_equipo_cumple_el_con_edad(self):
        cumpleanos.importar_equipo(self.TABLA)
        r = cumpleanos.equipo_cumple_el(date(2026, 7, 29))
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['nombre'], 'Octavio Del Real')
        self.assertEqual(r[0]['edad'], 72)

    def test_proximos_junta_equipo_y_pacientes(self):
        cumpleanos.importar_equipo(self.TABLA)
        pacientes._save_index({'111111111': {
            'nombres': 'Juan', 'apellidos': 'Perez', 'fecha_nacimiento': '2010-07-29'}})
        d = cumpleanos.proximos(date(2026, 7, 29))
        self.assertEqual(len(d['equipo']), 1)
        self.assertEqual(len(d['pacientes']), 1)
        self.assertEqual(d['pacientes'][0]['edad'], 16)
        self.assertIn('29 de julio', d['fecha_legible'])


class TestSegurosFallback(unittest.TestCase):
    """La fecha de nacimiento tenia que tipearse a mano en cada formulario."""

    def setUp(self):
        pacientes._save_index({'111111111': {
            'nombres': 'Juan', 'apellidos': 'Perez',
            'fecha_nacimiento': '1990-03-05', 'direccion': 'Calle 1'}})

    def _valores(self, extra):
        return seguros.armar_valores(
            {'rut': '11.111.111-1', 'nombre': 'Juan', 'apellido': 'Perez',
             'datos_extra': extra}, [])

    def test_toma_la_fecha_de_la_base_si_no_hay_nada_escrito(self):
        v = self._valores({})
        self.assertEqual(v['paciente_fecha_nacimiento'], '05-03-1990')  # DD-MM-YYYY
        self.assertTrue(v['paciente_edad'])
        self.assertEqual(v['paciente_direccion'], 'Calle 1')

    def test_lo_escrito_a_mano_siempre_manda(self):
        v = self._valores({'fecha_nacimiento': '01-02-1985'})
        self.assertEqual(v['paciente_fecha_nacimiento'], '01-02-1985')

    def test_rut_desconocido_no_revienta(self):
        v = seguros.armar_valores({'rut': '99.999.999-9', 'nombre': 'X',
                                   'apellido': 'Y', 'datos_extra': {}}, [])
        self.assertEqual(v['paciente_fecha_nacimiento'], '')
        self.assertEqual(v['paciente_edad'], '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
