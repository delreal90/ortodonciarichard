"""
test_jsonstore.py - La capa de guardado que usan todos los modulos.

Cero red. Archivos temporales.

    cd admin && python test_jsonstore.py

Si esto falla, falla el guardado de TODO el proyecto: pacientes, consentimientos,
seguros, recordatorios, control dental, NPS, confirmaciones y cumpleanos.
"""

import os
import sys
import json
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import jsonstore   # noqa: E402


class _Base(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='jsonstore_test_'))
        self.path = self.dir / 'datos.json'


class TestBasico(_Base):

    def test_archivo_que_no_existe_devuelve_el_default(self):
        s = jsonstore.JsonStore(self.path, default={'a': 1})
        self.assertEqual(s.load(), {'a': 1})

    def test_el_default_no_se_comparte_entre_lecturas(self):
        """Si se entregara la MISMA instancia, quien modifique lo leido estaria
        modificando el default de todos los que lean despues."""
        s = jsonstore.JsonStore(self.path, default={'lista': []})
        primera = s.load()
        primera['lista'].append('contaminado')
        self.assertEqual(s.load(), {'lista': []})

    def test_guardar_y_leer(self):
        s = jsonstore.JsonStore(self.path)
        s.save({'x': [1, 2, 3], 'y': 'ñandú'})
        self.assertEqual(s.load(), {'x': [1, 2, 3], 'y': 'ñandú'})

    def test_crea_la_carpeta_si_no_existe(self):
        s = jsonstore.JsonStore(self.dir / 'sub' / 'otra' / 'd.json')
        s.save({'ok': True})
        self.assertTrue(s.path.exists())

    def test_acentos_y_enies_se_guardan_legibles(self):
        """ensure_ascii=False: los RUT no, pero los nombres de pacientes tienen
        tildes y hay que poder leer el archivo a mano."""
        s = jsonstore.JsonStore(self.path)
        s.save({'nombre': 'José Muñoz'})
        self.assertIn('José Muñoz', self.path.read_text(encoding='utf-8'))

    def test_claves_garantizadas(self):
        """Un archivo viejo, escrito antes de que existiera una clave, no puede
        hacer reventar al codigo que la espera."""
        self.path.write_text('{"inscritos": {"1": {}}}', encoding='utf-8')
        s = jsonstore.JsonStore(self.path,
                                claves={'inscritos': {}, 'no_molestar': [], 'vistos': {}})
        d = s.load()
        self.assertEqual(d['no_molestar'], [])
        self.assertEqual(d['vistos'], {})
        self.assertEqual(d['inscritos'], {'1': {}})

    def test_default_si_falta_distingue_vacio_de_nunca_corrido(self):
        """confirmaciones.py depende de esto: si el archivo NO existe, la primera
        corrida solo siembra (no le manda correo a media cartera). Un archivo
        vacio, en cambio, si es una corrida previa."""
        s = jsonstore.JsonStore(self.path, default={}, default_si_falta=None)
        self.assertIsNone(s.load())
        s.save({})
        self.assertEqual(s.load(), {})


class TestEscrituraAtomica(_Base):

    def test_no_queda_temporal_tras_guardar(self):
        s = jsonstore.JsonStore(self.path)
        s.save({'a': 1})
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_un_fallo_al_escribir_no_destruye_lo_anterior(self):
        """Lo que protege la escritura atomica: si el proceso muere a mitad, el
        archivo viejo sigue entero (no truncado)."""
        s = jsonstore.JsonStore(self.path)
        s.save({'importante': 'no perder'})
        class NoSerializable:
            pass
        with self.assertRaises(TypeError):
            s.save({'malo': NoSerializable()})
        self.assertEqual(s.load(), {'importante': 'no perder'})


class TestArchivoCorrupto(_Base):
    """El comportamiento nuevo: antes un JSON ilegible se devolvia como default
    y el siguiente save lo pisaba — los datos se perdian sin que nadie supiera."""

    def test_un_archivo_ilegible_se_aparta_y_no_se_pierde(self):
        self.path.write_text('{esto no es json', encoding='utf-8')
        s = jsonstore.JsonStore(self.path, default={'a': 1})
        self.assertEqual(s.load(), {'a': 1})
        respaldos = list(self.dir.glob('*.corrupto*'))
        self.assertEqual(len(respaldos), 1, 'el archivo malo tiene que quedar guardado')
        self.assertEqual(respaldos[0].read_text(encoding='utf-8'), '{esto no es json')

    def test_varios_corruptos_no_se_pisan_entre_si(self):
        for i in range(3):
            self.path.write_text(f'roto {i}', encoding='utf-8')
            jsonstore.JsonStore(self.path).load()
        self.assertEqual(len(list(self.dir.glob('*.corrupto*'))), 3)

    def test_tras_apartarlo_se_puede_seguir_trabajando(self):
        self.path.write_text('roto', encoding='utf-8')
        s = jsonstore.JsonStore(self.path, default={})
        s.load()
        s.save({'nuevo': True})
        self.assertEqual(s.load(), {'nuevo': True})


class TestConcurrencia(_Base):

    def test_actualizar_no_pierde_escrituras(self):
        """El motivo de que exista actualizar(): entre un load y un save sueltos,
        otro hilo se cuela y su escritura se pierde. Es exactamente el bug que
        tenia stats.eliminar()."""
        s = jsonstore.JsonStore(self.path, default={'n': 0})
        s.save({'n': 0})
        N = 40

        def sumar():
            s.actualizar(lambda d: d.update({'n': d['n'] + 1}))

        hilos = [threading.Thread(target=sumar) for _ in range(N)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(s.load()['n'], N)

    def test_actualizar_acepta_que_fn_devuelva_la_version_nueva(self):
        s = jsonstore.JsonStore(self.path, default={'a': 1})
        s.actualizar(lambda d: {'reemplazado': True})
        self.assertEqual(s.load(), {'reemplazado': True})

    def test_el_lock_es_reentrante(self):
        """actualizar() llama a load() y save(), que toman el mismo lock. Con un
        Lock normal en vez de RLock, esto se cuelga para siempre."""
        s = jsonstore.JsonStore(self.path, default={})
        with s.lock:
            s.save({'a': 1})
            self.assertEqual(s.load(), {'a': 1})
            s.actualizar(lambda d: d.update({'b': 2}))
        self.assertEqual(s.load(), {'a': 1, 'b': 2})

    def test_lecturas_y_escrituras_a_la_vez_nunca_ven_algo_a_medias(self):
        s = jsonstore.JsonStore(self.path, default={'items': []})
        s.save({'items': list(range(200))})
        errores = []

        def escribir():
            for i in range(30):
                s.actualizar(lambda d: d.update({'items': list(range(200 + i))}))

        def leer():
            for _ in range(60):
                try:
                    d = s.load()
                    if not isinstance(d.get('items'), list):
                        errores.append('estructura rota')
                except Exception as e:
                    errores.append(repr(e))

        hilos = [threading.Thread(target=escribir), threading.Thread(target=leer),
                 threading.Thread(target=leer)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])


class TestModulosRealesMigrados(unittest.TestCase):
    """Los 9 modulos delegan en JsonStore pero conservan sus nombres de funcion,
    asi que ninguna llamada cambio. Estas pruebas verifican que la migracion
    respeta el comportamiento que cada uno necesita."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='migracion_test_'))
        os.environ['PATIENT_INDEX_PATH'] = str(cls.dir / 'patient_index.json')
        os.environ['DENTIDESK_ENABLED'] = 'false'

    def test_confirmaciones_distingue_nunca_corrido_de_vacio(self):
        """Lo mas delicado de la migracion: si esto se rompe, la primera corrida
        del barrido le manda el correo de confirmacion a media cartera."""
        import confirmaciones
        if confirmaciones.ENVIADAS_PATH.exists():
            confirmaciones.ENVIADAS_PATH.unlink()
        self.assertIsNone(confirmaciones._load(), 'sin archivo = nunca se ha corrido')
        confirmaciones._save({})
        self.assertEqual(confirmaciones._load(), {}, 'archivo vacio = ya corrio')

    def test_control_dental_garantiza_sus_claves(self):
        import control_dental
        control_dental.REGISTRO_PATH.write_text('{"inscritos": {}}', encoding='utf-8')
        reg = control_dental._load_registro()
        for k in ('inscritos', 'no_molestar', 'vistos', 'motivos_desconocidos'):
            self.assertIn(k, reg)

    def test_un_registro_corrupto_no_se_pierde(self):
        """Antes: JSON ilegible -> se devolvia vacio -> el siguiente guardado lo
        pisaba y los datos se iban en silencio. Ahora queda un respaldo."""
        import recaptacion
        recaptacion.REGISTRO_PATH.parent.mkdir(parents=True, exist_ok=True)
        recaptacion.REGISTRO_PATH.write_text('{roto', encoding='utf-8')
        reg = recaptacion._load_registro()
        self.assertEqual(reg['envios'], {})
        respaldos = list(recaptacion.REGISTRO_PATH.parent.glob(
            recaptacion.REGISTRO_PATH.name + '.corrupto*'))
        self.assertTrue(respaldos, 'el archivo corrupto tiene que quedar guardado')
        self.assertEqual(respaldos[0].read_text(encoding='utf-8'), '{roto')

    def test_pacientes_ida_y_vuelta(self):
        import pacientes
        pacientes.vaciar()
        pacientes._save_index({'123456789': {'nombres': 'José', 'email': 'a@b.cl'}})
        self.assertEqual(pacientes._load_index()['123456789']['nombres'], 'José')

    def test_seguros_usa_un_store_por_archivo(self):
        """Cada archivo necesita su propio lock: si compartieran uno solo, dos
        escrituras a archivos distintos se bloquearian entre si sin razon; si no
        cachearan, dos escrituras al MISMO archivo no se excluirian."""
        import seguros
        a, b = self.dir / 'uno.json', self.dir / 'dos.json'
        self.assertIs(seguros._store(a), seguros._store(a), 'mismo archivo, mismo store')
        self.assertIsNot(seguros._store(a), seguros._store(b), 'archivos distintos, stores distintos')


if __name__ == '__main__':
    unittest.main(verbosity=2)
