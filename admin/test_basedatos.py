"""
test_basedatos.py — El archivo de la base y su renombre kpi.db -> clinica.db.

Cero red. Cada prueba trabaja sobre un directorio temporal propio y RECARGA el
modulo, porque `basedatos` resuelve la ruta y corre el renombre AL IMPORTAR (es
lo que garantiza que nadie abra una conexion antes de mover el archivo).

Lo que se vigila aca es una sola cosa, y es la que importa: **el renombre no
puede perder datos**. La base tiene la tabla `disponibilidad`, que no se puede
reconstruir de ninguna parte.
"""

import os
import sys
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
import importlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _sembrar(path, filas=3):
    """Una base SQLite en modo WAL con datos, como la real."""
    con = sqlite3.connect(str(path))
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('CREATE TABLE IF NOT EXISTS disponibilidad (fecha TEXT, min_libres INT)')
    con.executemany('INSERT INTO disponibilidad VALUES (?,?)',
                    [(f'2026-01-0{i+1}', i * 10) for i in range(filas)])
    con.commit()
    con.close()


_HIJO = (
    'import sqlite3, os, sys\n'
    'con = sqlite3.connect(sys.argv[1])\n'
    "con.execute('PRAGMA journal_mode=WAL')\n"
    "con.execute('INSERT INTO disponibilidad VALUES (?,?)', (sys.argv[2], int(sys.argv[3])))\n"
    'con.commit()\n'
    'os._exit(0)\n'          # muere sin cerrar: el -wal queda poblado en disco
)


def _escribir_y_morir(path, fecha, valor):
    """Escribe una fila y mata el proceso SIN cerrar la conexion.

    Es la unica forma honesta de dejar un `-wal` con datos: al cerrar limpio,
    SQLite lo consolida y lo borra. Reproduce lo que pasa cuando Render reinicia
    el servicio a mitad de una escritura.
    """
    subprocess.run([sys.executable, '-c', _HIJO, str(path), fecha, str(valor)],
                   check=True)


def _filas(path):
    con = sqlite3.connect(str(path))
    try:
        return con.execute('SELECT COUNT(*) FROM disponibilidad').fetchone()[0]
    finally:
        con.close()


class BaseTmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='basedatos_test_'))
        self._env = {k: os.environ.get(k)
                     for k in ('PATIENT_INDEX_PATH', 'CLINICA_DB_PATH', 'KPI_DB_PATH')}
        for k in self._env:
            os.environ.pop(k, None)
        os.environ['PATIENT_INDEX_PATH'] = str(self.tmp / 'patient_index.json')

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cargar(self):
        import basedatos
        return importlib.reload(basedatos)


class TestRenombre(BaseTmp):

    def test_renombra_conservando_los_datos(self):
        _sembrar(self.tmp / 'kpi.db', filas=5)
        bd = self.cargar()
        self.assertEqual(bd.DB_PATH, self.tmp / 'clinica.db')
        self.assertTrue((self.tmp / 'clinica.db').exists())
        self.assertFalse((self.tmp / 'kpi.db').exists())
        self.assertEqual(_filas(self.tmp / 'clinica.db'), 5)

    def test_lo_escrito_en_el_wal_no_se_pierde(self):
        """La razon por la que hay un checkpoint antes de mover el archivo.

        En modo WAL los cambios recientes viven en kpi.db-wal, y SQLite solo los
        consolida al cerrar limpio. Si el proceso muere antes —un reinicio de
        Render— el -wal queda con datos. Mover solo el .db los dejaria atras, y
        serian justo los ULTIMOS capturados.
        """
        db = self.tmp / 'kpi.db'
        _sembrar(db, filas=2)
        _escribir_y_morir(db, '2026-02-01', 99)
        self.assertTrue((self.tmp / 'kpi.db-wal').exists(),
                        'el escenario exige que quede un -wal poblado')

        self.cargar()
        self.assertEqual(_filas(self.tmp / 'clinica.db'), 3,
                         'la fila que vivia en el -wal tiene que haber viajado')

    def test_es_idempotente(self):
        _sembrar(self.tmp / 'kpi.db', filas=4)
        bd = self.cargar()
        self.assertFalse(bd.migrar_nombre(), 'ya estaba renombrado, no debe mover nada')
        self.assertEqual(_filas(self.tmp / 'clinica.db'), 4)

    def test_sin_archivo_viejo_no_hace_nada(self):
        bd = self.cargar()
        self.assertFalse(bd.migrar_nombre())
        self.assertFalse((self.tmp / 'clinica.db').exists(),
                         'no se crea la base hasta que alguien conecte')

    def test_no_pisa_una_clinica_db_que_ya_existe(self):
        """Con las dos presentes gana la nueva: mover kpi.db encima borraria
        clinica.db, que es la que el sistema viene usando."""
        _sembrar(self.tmp / 'kpi.db', filas=2)
        _sembrar(self.tmp / 'clinica.db', filas=7)
        bd = self.cargar()
        self.assertEqual(_filas(bd.DB_PATH), 7)
        self.assertTrue((self.tmp / 'kpi.db').exists(), 'la vieja se conserva intacta')


    def test_si_el_renombre_falla_no_se_arranca_contra_una_base_vacia(self):
        """La propiedad que importa: pase lo que pase, DB_PATH apunta a donde
        estan los datos. Si `os.replace` falla y la base nueva tampoco existe,
        se sigue usando la vieja."""
        _sembrar(self.tmp / 'kpi.db', filas=5)
        bd = self.cargar()
        # Deshago el renombre para volver al estado inicial y simular el fallo.
        os.replace(str(self.tmp / 'clinica.db'), str(self.tmp / 'kpi.db'))
        real = os.replace

        def falla(a, b):
            raise OSError('disco lleno')

        os.replace = falla
        try:
            bd.DB_PATH = self.tmp / 'clinica.db'
            self.assertFalse(bd.migrar_nombre())
        finally:
            os.replace = real
        self.assertEqual(bd.DB_PATH, self.tmp / 'kpi.db')
        self.assertEqual(_filas(bd.DB_PATH), 5)

    def test_si_otro_proceso_ya_renombro_no_cae_a_un_archivo_inexistente(self):
        """⚠️ Con varios workers, uno puede ganar la carrera. Caer al nombre
        viejo cuando ya no existe crearia una base VACIA al lado de los datos
        reales. Hoy Render usa --workers 1, pero subir a 2 no puede romper esto
        en silencio."""
        _sembrar(self.tmp / 'clinica.db', filas=4)
        bd = self.cargar()
        real = os.replace

        def falla(a, b):
            raise OSError('lo renombro otro worker')

        os.replace = falla
        try:
            # `kpi.db` no existe y `clinica.db` si: es el estado post-carrera.
            self.assertFalse(bd.migrar_nombre())
        finally:
            os.replace = real
        self.assertEqual(bd.DB_PATH, self.tmp / 'clinica.db')
        self.assertEqual(_filas(bd.DB_PATH), 4)


class TestRutaPorVariable(BaseTmp):

    def test_kpi_db_path_manda_y_no_renombra(self):
        """Compatibilidad: un Render que ya tenga KPI_DB_PATH seteada, y
        test_kpi.py, que la fija antes de importar."""
        destino = self.tmp / 'donde_yo_diga.db'
        _sembrar(destino, filas=6)
        os.environ['KPI_DB_PATH'] = str(destino)
        bd = self.cargar()
        self.assertEqual(bd.DB_PATH, destino)
        self.assertFalse(bd.migrar_nombre())
        self.assertFalse((self.tmp / 'clinica.db').exists())

    def test_clinica_db_path_tiene_prioridad(self):
        os.environ['KPI_DB_PATH'] = str(self.tmp / 'no.db')
        os.environ['CLINICA_DB_PATH'] = str(self.tmp / 'si.db')
        bd = self.cargar()
        self.assertEqual(bd.DB_PATH, self.tmp / 'si.db')

    def test_una_ruta_vieja_con_datos_se_sigue_usando(self):
        """Con KPI_DB_PATH apuntando a kpi.db, el archivo conserva su nombre y
        sus datos: la variable manda por sobre el renombre."""
        _sembrar(self.tmp / 'kpi.db', filas=3)
        os.environ['KPI_DB_PATH'] = str(self.tmp / 'kpi.db')
        bd = self.cargar()
        self.assertEqual(_filas(bd.DB_PATH), 3)


class TestConectar(BaseTmp):

    def test_conectar_crea_el_directorio_y_deja_wal(self):
        os.environ['CLINICA_DB_PATH'] = str(self.tmp / 'sub' / 'dir' / 'c.db')
        bd = self.cargar()
        con = bd.conectar()
        try:
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0].lower(), 'wal')
            con.execute('CREATE TABLE t (a)')
            con.execute("INSERT INTO t VALUES ('x')")
            con.commit()
            fila = con.execute('SELECT a FROM t').fetchone()
            self.assertEqual(fila['a'], 'x', 'row_factory debe permitir acceso por nombre')
        finally:
            con.close()


def suite():
    s = unittest.TestSuite()
    for clase in (TestRenombre, TestRutaPorVariable, TestConectar):
        s.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(clase))
    return s


if __name__ == '__main__':
    ok = unittest.TextTestRunner(verbosity=2).run(suite()).wasSuccessful()
    sys.exit(0 if ok else 1)
