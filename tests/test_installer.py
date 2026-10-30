"""Installer state, checkpoints and transitions stay scoped to one workspace."""
import base64
from copy import deepcopy
import hashlib
import os
import socket
import threading
import time
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import zipfile

from blueprint_library import backup
from blueprint_library.installer import (Supervisor, initialize_installation, read_status,
    request_apply, request_check, create_checkpoint, _config, _lock, _same_schema, read_feed)
from blueprint_library.installer_release import canonical_json
from blueprint_library.maintenance import DataLease, atomic_json
from blueprint_library.store import LibraryError

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey = None


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional signing runtime is required.')
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name).resolve()
        self.private = Ed25519PrivateKey.generate()
        self.keys = {'publisher': base64.b64encode(self.private.public_key().public_bytes_raw()).decode()}
        self.old = self.release('2.10.0')
        self.new = self.release('2.10.1')
        self.root = self.work / 'installation'
        self.data = self.work / 'company'
        self.backups = self.work / 'backups'
        self.recovery = self.work / 'recovery'
        self.feed = self.work / 'feed.json'

    def release(self, version, schema=8):
        files = {'blueprint.py': b"print('fixture only')\n",
                 'blueprint_folders/__init__.py': f'__version__ = "{version}"\n'.encode(),
                 'blueprint_library/store.py': f'SCHEMA_VERSION = {schema}\n'.encode()}
        archive = self.work / (version + '.zip')
        with zipfile.ZipFile(archive, 'w') as out:
            for name, value in files.items():
                out.writestr(name, value)
        manifest = {'contract_version': 1, 'component_id': 'blueprint-core', 'adapter': 'native-core',
            'version': version, 'source_revision': 'a' * 40, 'platforms': [sys.platform],
            'runtime': {'python_min': [3, 10], 'python_max_exclusive': [4, 0]},
            'archive': {'url': str(archive), 'sha256': backup.digest(archive), 'size': archive.stat().st_size},
            'files': {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
            'entrypoint': 'blueprint.py', 'database_schema': schema, 'notes': '合成更新'}
        return {'manifest': manifest, 'key_id': 'publisher',
                'signature': base64.b64encode(self.private.sign(canonical_json(manifest))).decode()}

    def initialize(self, root=None, data=None, backups=None, recovery=None):
        return initialize_installation(root or self.root, self.old, self.keys,
                    data_dir=data or self.data, backup_dir=backups or self.backups,
                    recovery_dir=recovery or self.recovery, username='synthetic-owner',
                    password='synthetic-password-012345', feed=str(self.feed), port=18787)

    def prepare(self):
        self.feed.write_text(json.dumps({'releases': [self.new]}), encoding='utf-8')
        return Supervisor(self.root).check()

    def test_local_customization_blocks_preparation_without_overwriting(self):
        self.initialize()
        from blueprint_library.installer import _state
        program = Path(_state(self.root)['installed']['path']) / 'blueprint.py'
        program.write_bytes(b'# synthetic customer customization\n')
        status = self.prepare()
        self.assertFalse(status['can_apply'])
        self.assertIn('客製', status['error'])
        self.assertEqual(program.read_bytes(), b'# synthetic customer customization\n')

    def test_local_customization_after_download_blocks_before_service_stop(self):
        self.initialize(); ready = self.prepare()
        from blueprint_library.installer import _state
        program = Path(_state(self.root)['installed']['path']) / 'blueprint.py'
        program.write_bytes(b'# synthetic customer customization after prepare\n')
        request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        supervisor = Supervisor(self.root)
        with patch.object(supervisor, '_stop_child') as stop, patch.object(supervisor, '_launch') as launch:
            status = supervisor.apply()
        stop.assert_not_called(); launch.assert_not_called()
        self.assertEqual(status['phase'], 'prepare_failed')
        self.assertIn('客製', status['error'])
        self.assertIn(b'customer customization', program.read_bytes())

    def test_heartbeat_does_not_invalidate_browser_apply_version(self):
        self.initialize()
        supervisor = Supervisor(self.root)
        before = read_status(self.root)
        supervisor._healthy()
        supervisor._healthy()
        self.assertEqual(read_status(self.root)['version'], before['version'])

    def test_initialize_preserves_private_identity_and_hides_paths_and_keys(self):
        result = self.initialize()
        self.assertEqual(result['phase'], 'installed')
        credential = json.loads((self.data / 'access.json').read_text(encoding='utf-8'))
        self.assertEqual(credential['username'], 'synthetic-owner')
        self.assertNotIn('password', credential)
        public = json.dumps(result)
        for secret in (str(self.data), str(self.root), 'trusted_keys', self.keys['publisher'], 'synthetic-password'):
            self.assertNotIn(secret, public)
        with self.assertRaises(ValueError):
            self.initialize()

    def test_five_installations_update_state_independently(self):
        roots = []
        for index in range(5):
            base = self.work / str(index)
            roots.append(base / 'installation')
            self.initialize(roots[-1], base / 'data', base / 'backups', base / 'recovery')
        request_check(roots[0])
        self.assertGreater(read_status(roots[0])['version'], read_status(roots[1])['version'])
        for root in roots[1:]:
            self.assertEqual(read_status(root)['version'], 1)
            self.assertEqual(read_status(root)['phase'], 'installed')

    def test_nonempty_and_overlapping_directories_are_rejected(self):
        self.data.mkdir()
        (self.data / 'keep.txt').write_text('preserved')
        with self.assertRaises(ValueError):
            self.initialize()
        self.assertEqual((self.data / 'keep.txt').read_text(), 'preserved')
        with self.assertRaises(ValueError):
            self.initialize(data=self.root / 'data')

    def test_config_cannot_be_copied_to_other_installation(self):
        self.initialize()
        other = self.work / 'other'
        other.mkdir()
        (other / 'installation.json').write_bytes((self.root / 'installation.json').read_bytes())
        with self.assertRaises(ValueError):
            read_status(other)

    def test_one_data_path_cannot_be_claimed_by_another_installation(self):
        self.initialize()
        with self.assertRaises(ValueError):
            initialize_installation(self.work / 'other', self.old, self.keys,
                 data_dir=self.data, backup_dir=self.work / 'other-backups',
                 recovery_dir=self.work / 'other-recovery', existing_data=True)

    def test_prepare_does_not_change_installed_release_or_company_data(self):
        initial = self.initialize()
        before = backup.coverage(self.data)
        status = self.prepare()
        self.assertEqual(status['phase'], 'ready')
        self.assertEqual(status['installed'], initial['installed'])
        self.assertEqual(status['prepared']['version'], '2.10.1')
        self.assertEqual(backup.coverage(self.data), before)

    def test_apply_request_cas_and_idempotent_retry(self):
        self.initialize()
        ready = self.prepare()
        release_id = ready['prepared']['release_id']
        with self.assertRaises(LibraryError):
            request_apply(self.root, release_id, ready['version'] - 1)
        with self.assertRaises(LibraryError):
            request_apply(self.root, 'wrong', ready['version'])
        pending = request_apply(self.root, release_id, ready['version'])
        repeated = request_apply(self.root, release_id, ready['version'])
        self.assertEqual(pending['operation_id'], repeated['operation_id'])
        self.assertEqual(pending['version'], repeated['version'])

    def test_corrupt_or_missing_feed_keeps_old_installed_version(self):
        self.initialize()
        self.feed.write_text('{broken', encoding='utf-8')
        result = Supervisor(self.root).check()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertEqual(result['installed']['version'], '2.10.0')
        self.assertIsNone(result['prepared'])

    def test_schema_migration_not_marked_ready(self):
        self.initialize()
        # Signed schema differs, while fixture still has a valid exact inventory.
        schema_release = self.release('2.11.0', 9)
        self.feed.write_text(json.dumps({'releases': [schema_release]}), encoding='utf-8')
        result = Supervisor(self.root).check()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertIsNone(result['prepared'])
        self.assertEqual(backup.coverage(self.data)['schema'], 8)

    def test_offline_checkpoint_restore_and_private_copy_are_read_back(self):
        self.initialize()
        config = _config(self.root)
        with DataLease(self.data):
            receipt = create_checkpoint(config, 'synthetic-operation')
            again = create_checkpoint(config, 'synthetic-operation')
        self.assertEqual(receipt, again)
        self.assertTrue(receipt['verified'])
        self.assertTrue(receipt['private_copy_readback_verified'])
        self.assertFalse(receipt['off_host_backup_verified'])
        copy = Path(receipt['private_copy'])
        self.assertEqual(backup.digest(copy), receipt['sha256'])
        self.assertEqual(backup.coverage(self.data), receipt['coverage'])
        (self.data / 'new.txt').write_text('new data')
        with DataLease(self.data), self.assertRaises(ValueError):
            create_checkpoint(config, 'synthetic-operation')

    def test_apply_checkpoint_then_launch_and_same_operation_readback(self):
        self.initialize()
        ready = self.prepare()
        pending = request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        supervisor = Supervisor(self.root)
        order = []
        def stop():
            order.append('stop')
        def launch(value):
            order.append('launch')
            self.assertEqual(value['version'], '2.10.1')
            self.assertTrue(any(self.recovery.glob('*.zip')))
        with patch.object(supervisor, '_stop_child', side_effect=stop), patch.object(supervisor, '_launch', side_effect=launch):
            result = supervisor.apply()
        self.assertEqual(order, ['stop', 'launch'])
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['installed']['version'], '2.10.1')
        retry = request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        self.assertEqual(retry['operation_id'], pending['operation_id'])

    def test_unknown_launch_preserves_data_and_requires_reconciliation(self):
        self.initialize()
        ready = self.prepare()
        request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        supervisor = Supervisor(self.root)
        before = backup.coverage(self.data)
        with patch.object(supervisor, '_stop_child'), patch.object(supervisor, '_launch', side_effect=ValueError('failure')):
            result = supervisor.apply()
        self.assertEqual(result['phase'], 'recovery_required')
        self.assertEqual(result['installed']['version'], '2.10.0')
        self.assertEqual(backup.coverage(self.data), before)
        self.assertTrue(result['backup_verified'])

    def test_withdrawn_release_is_rejected_before_stopping_old_service(self):
        self.initialize()
        ready = self.prepare()
        request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        self.feed.write_text(json.dumps({'releases': []}), encoding='utf-8')
        supervisor = Supervisor(self.root)
        with patch.object(supervisor, '_stop_child') as stop:
            result = supervisor.apply()
        stop.assert_not_called()
        self.assertEqual(result['phase'], 'prepare_failed')

    def test_active_agent_defers_preparation_without_disabling_it(self):
        self.initialize()
        store = __import__('blueprint_library.store', fromlist=['Store']).Store(self.data)
        with store._connection() as conn:
            conn.execute('INSERT INTO agent_registrations VALUES(?,?,?,?,?)',
                         ('synthetic-agent', 'synthetic-pin', json.dumps({'enabled': True, 'connection_state': 'connected'}), 1, 'synthetic'))
        result = self.prepare()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertIn('AI', result['error'])
        self.assertIsNone(result['prepared'])
        with store._connection() as conn:
            saved = json.loads(conn.execute('SELECT document FROM agent_registrations').fetchone()[0])
        self.assertTrue(saved['enabled'])
        # SQL identity is authoritative even if a historical document omits or
        # misstates its redundant id; it must not impersonate a managed Agent.
        with store._connection() as conn:
            saved['id'] = 'different-managed-agent'
            conn.execute('UPDATE agent_registrations SET document=?', (json.dumps(saved),))
        from blueprint_library.installer import ensure_quiescent, InstallerBusyError
        config = {**_config(self.root), 'modules': {'synthetic': {'expected_agent_id': 'different-managed-agent'}}}
        with self.assertRaises(InstallerBusyError):
            ensure_quiescent(config)

    def test_active_integration_rechecked_before_stopping_service(self):
        self.initialize()
        ready = self.prepare()
        request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        store = __import__('blueprint_library.store', fromlist=['Store']).Store(self.data)
        with store._connection() as conn:
            conn.execute('INSERT INTO integration_registry VALUES(?,?,?)',
                         ('synthetic-integration', 'synthetic-token-hash', json.dumps({'enabled': True, 'revoked': False})))
        supervisor = Supervisor(self.root)
        with patch.object(supervisor, '_stop_child') as stop:
            result = supervisor.apply()
        stop.assert_not_called()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertIn('整合', result['error'])

    def test_changed_interpreter_config_is_rejected(self):
        self.initialize()
        config = _config(self.root)
        config['python'] = str(self.work / 'different-python.exe')
        atomic_json(self.root / 'installation.json', config)
        with self.assertRaisesRegex(ValueError, 'Python'):
            read_status(self.root)

    def test_real_native_process_upgrade_and_graceful_shutdown(self):
        # Build exactly the public candidate, then make a synthetic patch release.
        # All credentials, packages, company records and processes stay in this fixture.
        from tools.package_managed_release import build
        from blueprint_library.store import Store
        private_path = self.work / 'synthetic-signing.key'
        private_path.write_bytes(base64.b64encode(self.private.private_bytes_raw()))
        original_path = self.work / 'real-old.zip'
        original_json = self.work / 'real-old.json'
        build(original_path, original_json, private_path, 'publisher', str(original_path),
              allow_working_tree=True, platforms=[sys.platform])
        old = json.loads(original_json.read_text(encoding='utf-8'))
        new = deepcopy(old)
        parts = list(map(int, old['manifest']['version'].split('.')))
        parts[2] += 1
        new_version = '.'.join(map(str, parts))
        next_path = self.work / 'real-new.zip'
        with zipfile.ZipFile(original_path) as source, zipfile.ZipFile(next_path, 'x', compression=zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                payload = source.read(item.filename)
                if item.filename == 'blueprint_folders/__init__.py':
                    payload = payload.replace(old['manifest']['version'].encode(), new_version.encode())
                target.writestr(item.filename, payload)
                new['manifest']['files'][item.filename] = hashlib.sha256(payload).hexdigest()
        new['manifest'].update(version=new_version,
            source_tree=hashlib.sha256(canonical_json(new['manifest']['files'])).hexdigest())
        new['manifest']['archive'].update(url=str(next_path), size=next_path.stat().st_size, sha256=backup.digest(next_path))
        new['signature'] = base64.b64encode(self.private.sign(canonical_json(new['manifest']))).decode()
        self.feed.write_text(json.dumps({'releases': [new]}), encoding='utf-8')
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0))
            port = available.getsockname()[1]
        initialize_installation(self.root, old, self.keys, data_dir=self.data,
            backup_dir=self.backups, recovery_dir=self.recovery, username='synthetic-owner',
            password='synthetic-password-012345', port=port, feed=str(self.feed))
        item = Store(self.data).add_file('synthetic.txt', b'synthetic persistent content')
        before = backup.coverage(self.data)
        controller = Supervisor(self.root, startup_timeout=35)
        errors = []
        def run():
            try:
                controller.run()
            except Exception as exc:
                errors.append(str(exc))
        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                status = read_status(self.root)
                if status['phase'] == 'ready' and status['can_apply']:
                    break
                self.assertFalse(errors, errors)
                time.sleep(.1)
            else:
                self.fail('Initial readiness timed out: ' + str(status))
            live = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))
            with self.assertRaises(OSError):
                controller._launch(live['installed'])
            still_live = controller._internal(live['child'])
            self.assertEqual(still_live['pid'], live['child']['pid'])
            self.assertTrue(still_live['ready'])
            request_apply(self.root, status['prepared']['release_id'], status['version'])
            deadline = time.monotonic() + 50
            while time.monotonic() < deadline:
                status = read_status(self.root)
                if status['phase'] == 'complete' and status['installed']['version'] == new_version:
                    break
                failure = status
                if status['phase'] == 'recovery_required':
                    failure = {'status': status, 'service_log':
                        (self.root / 'service.log').read_text(encoding='utf-8', errors='replace')[-8000:]}
                self.assertNotEqual(status['phase'], 'recovery_required', failure)
                self.assertFalse(errors, errors)
                time.sleep(.1)
            else:
                self.fail('Apply timed out: ' + str(status))
            private_state = json.loads((self.root / 'state.json').read_text(encoding='utf-8'))
            actual = controller._internal(private_state['child'])
            self.assertEqual(actual['version'], new_version)
            self.assertTrue(actual['ready'])
            self.assertTrue(status['backup_verified'])
        finally:
            controller.stop()
            worker.join(50)
            self.assertFalse(worker.is_alive(), 'Owned child did not stop gracefully.')
        self.assertFalse(errors, errors)
        if os.name != 'nt':
            legacy_error = None
            with socket.socket() as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError as error:
                    legacy_error = error.errno
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(('127.0.0.1', port))
            print('POSIX probe after confirmed graceful shutdown: '
                  f'legacy bind errno={legacy_error}; reusable bind succeeded.', flush=True)
        self.assertEqual(backup.coverage(self.data), before)
        self.assertEqual(Store(self.data).get_item(item['id'])['id'], item['id'])

    def test_http_feed_and_wrong_formats_are_rejected(self):
        with self.assertRaises(ValueError):
            read_feed('http://example.invalid/feed.json')
        self.feed.write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):
            read_feed(str(self.feed))


if __name__ == '__main__':
    unittest.main()
