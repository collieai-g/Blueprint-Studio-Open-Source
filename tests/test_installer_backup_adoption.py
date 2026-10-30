"""An existing native backup journal is adopted explicitly, without repair/reset."""
from contextlib import closing, redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_installer as fixtures
from blueprint_library import backup
from blueprint_library.installer import (initialize_installation, read_status, Supervisor,
                                         _config, _owner_path, _private, _program_root)
from blueprint_library.maintenance import Maintenance, DataLease, atomic_json
from blueprint_library.server import initialize_library
from blueprint_library.store import Store
from tools import studio_installer


@unittest.skipIf(fixtures.Ed25519PrivateKey is None, 'Optional signing runtime is required.')
class ExistingBackupAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstallerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data, self.backups = self.fixture.data, self.fixture.backups
        initialize_library(self.data, 'original-preview', 'synthetic-existing-preview-password')
        self.item = Store(self.data).add_file('retained.txt', b'synthetic retained attachment')
        native = Maintenance(self.data, self.backups).prepare()
        try:
            # Complete requests/history are inert and must remain byte-identical.
            native.state['backup_requests']['synthetic-completed-operation'] = {
                'status': 'complete', 'operation_id': 'synthetic-completed-operation',
                'plan_digest': 'b' * 64, 'backup_id': native.state['last_backup']['id']}
            native.state['client_copies']['synthetic-existing-history'] = {'original-preview': {
                'evidence': 'synthetic preserved history'}}
            native.save()
        finally:
            native.close()
        self.state_path = self.backups / 'installation.json'
        self.original_state = json.loads(self.state_path.read_text(encoding='utf-8'))

    def initialize(self, **changes):
        kwargs = {'data_dir': self.data, 'backup_dir': self.backups,
                  'recovery_dir': self.fixture.recovery, 'existing_data': True,
                  'existing_backups': True, 'port': 18787}
        kwargs.update(changes)
        return initialize_installation(self.fixture.root, self.fixture.old, self.fixture.keys, **kwargs)

    def inventory(self):
        return {p.relative_to(self.backups).as_posix(): backup.digest(p)
                for p in self.backups.rglob('*') if p.is_file()}

    def assert_rejected_untouched(self, **changes):
        before = self.inventory()
        database = backup.digest(self.data / 'library.sqlite3')
        access = (self.data / 'access.json').read_bytes()
        with self.assertRaises(ValueError):
            self.initialize(**changes)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(backup.digest(self.data / 'library.sqlite3'), database)
        self.assertEqual((self.data / 'access.json').read_bytes(), access)
        self.assertFalse(self.fixture.root.exists())
        self.assertFalse(self.fixture.recovery.exists())
        self.assertFalse(_owner_path(self.data).exists())

    def test_explicit_adoption_preserves_native_identity_history_data_and_credentials(self):
        before, access = self.inventory(), (self.data / 'access.json').read_bytes()
        coverage = backup.coverage(self.data)
        self.assertEqual(self.initialize()['phase'], 'installed')
        self.assertEqual(self.inventory(), before)
        self.assertEqual((self.data / 'access.json').read_bytes(), access)
        self.assertEqual(backup.coverage(self.data), coverage)
        self.assertEqual(Store(self.data).get_file(self.item['id'])[0].read_bytes(), b'synthetic retained attachment')
        native = json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(native, self.original_state)
        self.assertEqual(_config(self.fixture.root)['backup_dir'], str(self.backups))
        self.assertTrue(self.fixture.recovery.is_dir())

    def test_default_and_flag_without_existing_data_do_not_adopt_backup_records(self):
        self.assert_rejected_untouched(existing_backups=False)
        self.assert_rejected_untouched(existing_data=False)

    def test_incomplete_or_foreign_native_journal_does_not_allocate_program_or_claim(self):
        changes = [
            {'format': 'other-format'}, {'version': True}, {'data': str(self.data.with_name('other-data'))},
            {'service_id': None}, {'service_id': 'not-a-native-service-id'}, {'status': 'migrating'},
            {'status': 'recovered_choose_previous_release'}, {'pending': {'snapshot': 'unknown'}},
            {'failed_code': 'failed-release'}, {'backup_requests': []}, {'client_copies': []},
            {'client_copies': {'existing-backup': {'original-preview': 'malformed-receipt'}}},
            {'backup_requests': {'prior': {'status': 'complete', 'backup': 'malformed-backup'}}},
            {'last_backup': {}}, {'product_version': 'unknown'},
        ]
        for change in changes:
            with self.subTest(change=change):
                atomic_json(self.state_path, {**deepcopy(self.original_state), **change})
                self.assert_rejected_untouched()
        atomic_json(self.state_path, self.original_state)

    def test_missing_or_malformed_journal_is_not_recreated(self):
        original = self.state_path.read_bytes()
        self.state_path.unlink()
        self.assert_rejected_untouched()
        self.assertFalse(self.state_path.exists())
        for invalid in (b'{partial json', b'[]'):
            with self.subTest(invalid=invalid):
                self.state_path.write_bytes(invalid)
                self.assert_rejected_untouched()
                self.assertEqual(self.state_path.read_bytes(), invalid)
        self.state_path.write_bytes(original)

    def test_unknown_or_unfinished_backup_request_is_not_silently_resumed(self):
        for status in ('queued', 'running', 'unknown'):
            with self.subTest(status=status):
                state = deepcopy(self.original_state)
                state['backup_requests']['original-operation'] = {'status': status}
                atomic_json(self.state_path, state)
                self.assert_rejected_untouched()

    def test_wrong_schema_and_downgrade_are_rejected_before_program_allocation(self):
        with closing(sqlite3.connect(self.data / 'library.sqlite3')) as conn:
            conn.execute('PRAGMA user_version=7')
        self.assert_rejected_untouched()
        with closing(sqlite3.connect(self.data / 'library.sqlite3')) as conn:
            conn.execute('PRAGMA user_version=8')
        atomic_json(self.state_path, {**self.original_state, 'product_version': '2.11.0'})
        self.assert_rejected_untouched()

    def test_live_data_lease_and_git_ancestor_cannot_be_adopted(self):
        with DataLease(self.data):
            self.assert_rejected_untouched()
        (self.fixture.work / '.git').mkdir()
        self.assert_rejected_untouched()

    def test_journal_change_during_package_preparation_prevents_claim_without_repair(self):
        from blueprint_library.installer_release import prepare_release
        changed = {**self.original_state, 'status': 'migrating'}
        def concurrent_change(*args, **kwargs):
            prepared = prepare_release(*args, **kwargs)
            atomic_json(self.state_path, changed)
            return prepared
        access = (self.data / 'access.json').read_bytes()
        with patch('blueprint_library.installer_release.prepare_release', side_effect=concurrent_change):
            with self.assertRaises(ValueError):
                self.initialize()
        self.assertFalse(_owner_path(self.data).exists())
        self.assertFalse((self.fixture.root / 'installation.json').exists())
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8')), changed)
        self.assertEqual((self.data / 'access.json').read_bytes(), access)

    def test_cli_requires_existing_data_before_password_or_file_reads(self):
        args = ['init', '--root', str(self.fixture.root), '--release', 'not-read.json',
                '--trusted-keys', 'not-read-keys.json', '--data', str(self.data),
                '--backups', str(self.backups), '--existing-backups']
        stdout, stderr = io.StringIO(), io.StringIO()
        with (patch.object(studio_installer.getpass, 'getpass', side_effect=AssertionError('no prompt')),
              redirect_stdout(stdout), redirect_stderr(stderr)):
            result = studio_installer.main(args)
        self.assertEqual(result, 1)
        self.assertIn('--existing-data', json.loads(stderr.getvalue())['error'])
        self.assertFalse(self.fixture.root.exists())


@unittest.skipIf(fixtures.Ed25519PrivateKey is None, 'Optional signing runtime is required.')
class PackagedProgramRootTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstallerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize()
        self.source = Path(json.loads((self.fixture.root / 'state.json').read_text(encoding='utf-8'))['installed']['path'])

    def test_packaged_child_can_read_its_own_installer_but_private_roots_stay_strict(self):
        with patch.object(backup, 'SOURCE', self.source):
            with self.assertRaises(ValueError):
                _private(self.fixture.root)
            self.assertEqual(_program_root(self.fixture.root), self.fixture.root)
            self.assertEqual(read_status(self.fixture.root)['phase'], 'installed')
            self.assertEqual(Supervisor(self.fixture.root).root, self.fixture.root)
            with self.assertRaises(ValueError):
                _private(self.source)

    def test_other_ancestors_and_source_descendants_are_not_program_roots(self):
        with patch.object(backup, 'SOURCE', self.source):
            for path in (self.source, self.source.parent, self.fixture.root.parent, self.source / 'nested'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    _program_root(path)

    def test_source_checkout_cannot_gain_exception_from_hash_name_alone(self):
        ordinary = self.fixture.work / 'checkouts' / ('a' * 64)
        ordinary.mkdir(parents=True)
        with patch.object(backup, 'SOURCE', ordinary), self.assertRaises(ValueError):
            _program_root(ordinary.parent.parent)

    def test_git_or_reparse_point_in_package_path_is_not_a_program_root(self):
        with patch.object(backup, 'SOURCE', self.source):
            (self.source / '.git').mkdir()
            with self.assertRaises(ValueError):
                _program_root(self.fixture.root)
            (self.source / '.git').rmdir()
            original = Path.lstat
            def reparse(path, *args, **kwargs):
                info = original(path, *args, **kwargs)
                if path == self.source.parent:
                    return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                return info
            with patch.object(Path, 'lstat', reparse), self.assertRaises(ValueError):
                _program_root(self.fixture.root)


if __name__ == '__main__':
    unittest.main()
