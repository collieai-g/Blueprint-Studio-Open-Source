"""Upgrade backups preserve real rows, credentials and immutable attachments."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blueprint_library.server import initialize_library
from blueprint_library.store import Store

spec = importlib.util.spec_from_file_location('upgrade_workspace', Path(__file__).parents[1] / 'tools/upgrade_workspace.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-upgrade-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'existing'
        self.backup = self.root / 'backup'
        initialize_library(self.data, 'owner', 'Temporary-testing-only-2026', company_profile={'name': 'Existing company'})
        self.store = Store(self.data)
        self.item = self.store.add_file('original.txt', b'original attachment', notes='Keep this note')

    def test_snapshot_verify_preserves_all_original_rows_and_allows_new_content(self):
        result = upgrade.snapshot(self.data, self.backup)
        self.assertEqual(result['status'], 'snapshot_created')
        self.store.create_item({'title': 'Added after update', 'url': 'https://example.com/new'})
        result = upgrade.verify(self.data, self.backup)
        self.assertEqual(result['status'], 'original_data_preserved')
        self.assertGreater(result['rows'], 0)
        self.assertEqual((self.backup / 'data/access.json').read_bytes(), (self.data / 'access.json').read_bytes())
        with sqlite3.connect(self.backup / 'data/library.sqlite3') as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_missing_or_overlapping_paths_never_initialize_or_overwrite(self):
        with self.assertRaises(ValueError): upgrade.snapshot(self.root / 'missing', self.backup)
        self.assertFalse((self.root / 'missing').exists())
        with self.assertRaises(ValueError): upgrade.snapshot(self.data, self.data / 'backup')
        upgrade.snapshot(self.data, self.backup)
        original = (self.backup / 'manifest.json').read_bytes()
        with self.assertRaises(ValueError): upgrade.snapshot(self.data, self.backup)
        self.assertEqual((self.backup / 'manifest.json').read_bytes(), original)

    def test_changed_rows_or_missing_files_fail_verification(self):
        upgrade.snapshot(self.data, self.backup)
        self.store.update_item(self.item['id'], {'expected_version': self.item['version'], 'notes': 'changed'})
        with self.assertRaises(ValueError): upgrade.verify(self.data, self.backup)
        (self.data / 'access.json').unlink()
        with self.assertRaises(ValueError): upgrade.verify(self.data, self.backup)

    def test_untrusted_manifest_paths_are_rejected(self):
        upgrade.snapshot(self.data, self.backup)
        path = self.backup / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest['files']['../outside'] = 'not-a-hash'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): upgrade.verify(self.data, self.backup)


if __name__ == '__main__': unittest.main()
