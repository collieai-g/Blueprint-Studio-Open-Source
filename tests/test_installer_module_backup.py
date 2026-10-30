"""Offline module protection preserves committed WAL pages and source bytes."""
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

from blueprint_library.installer_modules import module_checkpoint, _files


class ModuleBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-module-backup-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / 'customer-data'
        self.data.mkdir()
        self.settings = self.base / 'runtime.json'
        private = {'component_id': 'bluesai-cs-0001', 'expected_agent_id': 'existing-agent',
                   'data_dir': str(self.data), 'host': '127.0.0.1', 'port': 49123}
        self.settings.write_text(json.dumps(private), encoding='utf-8')
        self.config = {'root': str(self.base / 'program'), 'installation_id': 'synthetic-studio',
                       'backup_dir': str(self.base / 'backups'), 'recovery_dir': str(self.base / 'recovery'),
                       'modules': {'bluesai-cs-0001': {**private, 'config_file': str(self.settings)}}}
        (self.base / '.customer-data.blueprint-installer.json').write_text(json.dumps({
            'root': self.config['root'], 'installation_id': 'synthetic-studio',
            'component_id': 'bluesai-cs-0001'}), encoding='utf-8')

    def test_wal_pages_are_restored_without_creating_sidecars_in_source(self):
        staging = self.base / 'staging'
        staging.mkdir()
        with closing(sqlite3.connect(staging / 'service.sqlite')) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA wal_autocheckpoint=0')
            conn.execute('CREATE TABLE retained(value TEXT)')
            conn.execute("INSERT INTO retained VALUES('committed WAL record')")
            conn.commit()
            self.assertGreater((staging / 'service.sqlite-wal').stat().st_size, 0)
            for original in staging.iterdir():
                shutil.copy2(original, self.data / original.name)
        (self.data / 'admin-token').write_bytes(b'synthetic-secret-retained')
        (self.data / 'unrelated-wal').write_bytes(b'ordinary file must be retained')
        before = _files(self.data)
        receipt = module_checkpoint(self.config, 'bluesai-cs-0001', 'wal-update')
        self.assertEqual(_files(self.data), before)
        restored = self.base / 'independent-readback'
        with zipfile.ZipFile(receipt['private_copy']) as archive:
            archive.extractall(restored)
            self.assertNotIn('data/service.sqlite-wal', archive.namelist())
            self.assertNotIn('data/service.sqlite-shm', archive.namelist())
            self.assertEqual(archive.read('data/unrelated-wal'), b'ordinary file must be retained')
        with closing(sqlite3.connect(restored / 'data' / 'service.sqlite')) as conn:
            self.assertEqual(conn.execute('SELECT value FROM retained').fetchone()[0], 'committed WAL record')
        again = module_checkpoint(self.config, 'bluesai-cs-0001', 'another-update')
        self.assertTrue(again['reused'])
        self.assertEqual(again['private_copy'], receipt['private_copy'])

    def test_live_service_and_unknown_previous_operation_are_not_overwritten(self):
        (self.data / 'service.lock').write_text('synthetic owner')
        with self.assertRaisesRegex(ValueError, '仍持有資料'):
            module_checkpoint(self.config, 'bluesai-cs-0001', 'blocked')
        (self.data / 'service.lock').unlink()
        target = self.base / 'backups' / 'modules' / 'bluesai-cs-0001' / 'unknown'
        target.mkdir(parents=True)
        (target / 'preserve.txt').write_bytes(b'unknown outcome')
        with self.assertRaisesRegex(ValueError, '結果未知'):
            module_checkpoint(self.config, 'bluesai-cs-0001', 'unknown')
        self.assertEqual((target / 'preserve.txt').read_bytes(), b'unknown outcome')


if __name__ == '__main__':
    unittest.main()
