"""
test_backup.py - Respaldo de datos a Google Drive (backup.py).

Cero red: Google Drive queda mockeado (backup.drive_backup.*). Todo escribe en
un directorio temporal.

    cd admin && python test_backup.py

Un fallo aca es un respaldo que no incluye lo que deberia (perdida de datos si
alguna vez hay que restaurar) o una rotacion que borra de mas.
"""

import os
import sys
import zipfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TMP = Path(tempfile.mkdtemp(prefix='backup_test_'))
os.environ['PATIENT_INDEX_PATH'] = str(_TMP / 'patient_index.json')
os.environ['BACKUP_REGISTRO_PATH'] = str(_TMP / 'backup_registro.json')
sys.path.insert(0, str(Path(__file__).parent))

import backup  # noqa: E402


def _sembrar(base):
    base.mkdir(parents=True, exist_ok=True)
    (base / 'patient_index.json').write_text('{"a":1}', encoding='utf-8')
    (base / 'nps_registro.json').write_text('{}', encoding='utf-8')
    (base / 'eventos.jsonl').write_text('{"e":1}\n', encoding='utf-8')
    (base / 'compras.db').write_text('SQLITE', encoding='utf-8')
    (base / 'compras.db-wal').write_text('WAL', encoding='utf-8')
    firmas = base / 'seguros_firmas'
    firmas.mkdir(exist_ok=True)
    (firmas / 'alberto.png').write_text('PNG', encoding='utf-8')
    # Estos NO deben ir al zip:
    (base / 'backup_registro.json').write_text('{}', encoding='utf-8')
    (base / 'patient_index.json.tmp').write_text('tmp', encoding='utf-8')


class TestArchivos(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='backup_base_'))
        _sembrar(self.base)

    def test_incluye_lo_correcto_y_excluye_registro_y_tmp(self):
        arcnames = {a for _, a in backup.archivos_a_respaldar(self.base)}
        self.assertIn('patient_index.json', arcnames)
        self.assertIn('nps_registro.json', arcnames)
        self.assertIn('eventos.jsonl', arcnames)
        self.assertIn('compras.db', arcnames)
        self.assertIn('compras.db-wal', arcnames)
        self.assertIn('seguros_firmas/alberto.png', arcnames)
        # Exclusiones
        self.assertNotIn('backup_registro.json', arcnames)
        self.assertNotIn('patient_index.json.tmp', arcnames)

    def test_crear_zip_contiene_los_archivos(self):
        info = backup.crear_zip(destino_dir=self.base, base_dir=self.base)
        self.assertTrue(Path(info['ruta']).exists())
        with zipfile.ZipFile(info['ruta']) as z:
            nombres = set(z.namelist())
        self.assertIn('patient_index.json', nombres)
        self.assertIn('seguros_firmas/alberto.png', nombres)
        self.assertNotIn('backup_registro.json', nombres)


class TestRespaldar(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix='backup_base_'))
        _sembrar(self.base)

    def test_sube_y_borra_el_zip_local(self):
        with mock.patch.object(backup.drive_backup, 'subir_archivo',
                               return_value={'ok': True, 'file_id': 'FID1'}), \
             mock.patch.object(backup.drive_backup, 'listar_archivos',
                               return_value={'ok': True, 'archivos': []}):
            r = backup.respaldar(base_dir=self.base)
        self.assertTrue(r['ok'])
        self.assertTrue(r['subido'])
        self.assertEqual(r['file_id'], 'FID1')
        # El zip local se borra tras subir (no debe quedar en base_dir).
        self.assertEqual(list(self.base.glob('backup_ortodoncia_*.zip')), [])
        # Queda registrado en estado().
        self.assertEqual(backup.estado()['ultimo']['file_id'], 'FID1')

    def test_sin_credenciales_no_revienta(self):
        with mock.patch.object(backup.drive_backup, 'subir_archivo',
                               return_value={'ok': False, 'error': 'Sin credenciales'}):
            r = backup.respaldar(base_dir=self.base)
        self.assertFalse(r['ok'])
        self.assertFalse(r['subido'])
        self.assertIn('credenciales', r['error'])
        # Aun asi el zip local se limpia (no llenar el disco de Render).
        self.assertEqual(list(self.base.glob('backup_ortodoncia_*.zip')), [])


class TestRotacion(unittest.TestCase):
    def test_conserva_los_n_mas_nuevos(self):
        # 5 respaldos en Drive; retener 3 -> borra los 2 mas viejos (por nombre/fecha).
        fake = [{'id': f'id{i}', 'name': f'{backup._PREFIJO}2026-07-0{i}_0300.zip'}
                for i in range(1, 6)]
        eliminados = []
        with mock.patch.object(backup.drive_backup, 'listar_archivos',
                               return_value={'ok': True, 'archivos': fake}), \
             mock.patch.object(backup.drive_backup, 'eliminar_archivo',
                               side_effect=lambda fid: eliminados.append(fid) or {'ok': True}):
            n = backup._rotar_en_drive(retener=3)
        self.assertEqual(n, 2)
        self.assertEqual(eliminados, ['id1', 'id2'])   # los mas viejos


if __name__ == '__main__':
    unittest.main(verbosity=2)
