"""
test_fichas.py - La ficha de primera consulta (Google Form) que alimenta la base.

Cero red: prueba la interpretacion y la mezcla con datos sinteticos. La lectura
del Sheet en vivo (leer_filas) NO se prueba aca -- necesita credenciales y red;
se valido a mano contra el Sheet real.

    cd admin && python test_fichas.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix='fichas_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
sys.path.insert(0, str(Path(__file__).parent))

import fichas       # noqa: E402
import pacientes    # noqa: E402

# Encabezados como los del Sheet real, con las DOS "Fecha de Nacimiento" (una por
# rama) y las columnas de las dos ramas (adulto: Nombres/Apellidos; menor: Nombre
# y Apellidos del paciente).
HEADERS = [
    'Marca temporal',                    # 0
    'Dirección de correo electrónico',   # 1  (email del que responde)
    'Nombres',                           # 2  (adulto)
    'Apellidos',                         # 3  (adulto)
    'RUT del paciente',                  # 4
    '¿Qué edad tiene el paciente?',      # 5
    'Fecha de Nacimiento',               # 6  (adulto)
    'Celular de contacto',               # 7
    'Email de contacto',                 # 8
    'Dirección',                         # 9
    'Comuna, Ciudad',                    # 10
    'Fecha de Nacimiento',               # 11 (menor)  <- titulo repetido a proposito
    'Nombre y Apellidos del paciente',   # 12 (menor)
]


def fila(**kw):
    r = [''] * len(HEADERS)
    m = {'ts': 0, 'correo': 1, 'nombres': 2, 'apellidos': 3, 'rut': 4, 'edad': 5,
         'fnac_ad': 6, 'cel': 7, 'email_c': 8, 'dir': 9, 'comuna': 10,
         'fnac_men': 11, 'nombre_junto': 12}
    for k, v in kw.items():
        r[m[k]] = v
    return r


class TestInterpretar(unittest.TestCase):

    def test_adulto(self):
        f, _ = fichas.interpretar(HEADERS, [fila(
            rut='17.406.985-9', nombres='Alberto', apellidos='Del Real',
            fnac_ad='5/03/1990', cel='+56 9 1234 5678', correo='a@b.cl',
            dir='Paul Harris 10349', comuna='Las Condes, Santiago')])
        self.assertEqual(len(f), 1)
        r = f[0]
        self.assertEqual(r['nombres'], 'Alberto')
        self.assertEqual(r['apellidos'], 'Del Real')
        self.assertEqual(r['fecha_nacimiento'], '1990-03-05')
        self.assertEqual(r['email'], 'a@b.cl')
        self.assertEqual(r['comuna'], 'Las Condes')   # se queda con la comuna, no la ciudad

    def test_menor_nombre_va_en_columna_junta(self):
        """En la rama menor el nombre viene junto y la fecha en OTRA columna."""
        f, _ = fichas.interpretar(HEADERS, [fila(
            rut='11.111.111-1', nombre_junto='Juan Pablo Perez Soto',
            fnac_men='10/06/2015', correo='mama@b.cl', cel='+56 9 9999 0000')])
        r = f[0]
        self.assertEqual(r['nombres'], 'Juan Pablo')
        self.assertEqual(r['apellidos'], 'Perez Soto')
        self.assertEqual(r['fecha_nacimiento'], '2015-06-10')
        self.assertEqual(r['email'], 'mama@b.cl')   # el del apoderado

    def test_dedup_gana_la_ultima_respuesta(self):
        """Mismo RUT dos veces (respondio el form dos veces) -> gana la ultima."""
        f, _ = fichas.interpretar(HEADERS, [
            fila(rut='11.111.111-1', nombres='Viejo', apellidos='Dato', correo='v@b.cl'),
            fila(rut='11.111.111-1', nombres='Nuevo', apellidos='Dato', correo='n@b.cl'),
        ])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]['nombres'], 'Nuevo')

    def test_titulo_renombrado_no_revienta(self):
        """Si el formulario cambia un titulo, ese campo queda vacio, no crashea."""
        hdr = list(HEADERS); hdr[2] = 'Primer Nombre'   # ya no calza con 'nombres'
        f, _ = fichas.interpretar(hdr, [fila(rut='17.406.985-9', nombres='Alberto')])
        self.assertEqual(f[0]['nombres'], '')            # no lo encontro, pero no revento


class TestMerge(unittest.TestCase):

    def setUp(self):
        pacientes.vaciar()

    def test_crea_paciente_nuevo(self):
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'nombres': 'Alberto',
                                 'apellidos': 'Del Real', 'email': 'a@b.cl',
                                 'telefono': '+56 9 1', 'fecha_nacimiento': '1990-03-05',
                                 'direccion': 'x', 'comuna': 'Las Condes'}])
        rec = pacientes.lookup('17.406.985-9')
        self.assertEqual(rec['nombres'], 'Alberto')
        self.assertEqual(rec['email'], 'a@b.cl')

    def test_rut_basura_se_descarta(self):
        r = pacientes.merge_fichas([{'rut': '5-5', 'nombres': 'X', 'email': 'x@y.cl'}])
        self.assertEqual(r['nuevos'], 0)
        self.assertEqual(r['sin_rut_valido'], 1)

    def test_no_pisa_email_existente(self):
        """La regla de oro: en un menor el correo del form es del apoderado, y
        pisar el de DentiDesk romperia la dedup RUT+EMAIL."""
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'email': 'original@dentidesk.cl'}])
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'email': 'apoderado@form.cl'}])
        self.assertEqual(pacientes.lookup('17.406.985-9')['email'], 'original@dentidesk.cl')

    def test_rellena_solo_lo_que_falta(self):
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'nombres': 'Alberto'}])
        # segunda ficha trae apellido y telefono nuevos, pero otro nombre
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'nombres': 'OTRO',
                                 'apellidos': 'Del Real', 'telefono': '+56 9 2'}])
        rec = pacientes.lookup('17.406.985-9')
        self.assertEqual(rec['nombres'], 'Alberto')       # NO se piso
        self.assertEqual(rec['apellidos'], 'Del Real')    # se relleno (estaba vacio)
        self.assertEqual(rec['telefono'], '+56 9 2')

    def test_no_toca_la_parte_clinica(self):
        """La ficha trae antecedentes medicos; NO deben entrar a la base."""
        pacientes.merge_fichas([{'rut': '17.406.985-9', 'nombres': 'A',
                                 'alergias': 'SI', 'ronca': 'NO'}])
        rec = pacientes.lookup('17.406.985-9')
        self.assertNotIn('alergias', rec)
        self.assertNotIn('ronca', rec)

    def test_idempotente(self):
        fichas_ = [{'rut': '17.406.985-9', 'nombres': 'A', 'email': 'a@b.cl'}]
        pacientes.merge_fichas(fichas_)
        r2 = pacientes.merge_fichas(fichas_)
        self.assertEqual(r2['nuevos'], 0)
        self.assertEqual(r2['actualizados'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
