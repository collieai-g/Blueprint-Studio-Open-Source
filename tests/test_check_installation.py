"""Real HTTP acceptance resumes exact backups and private files without rewriting data."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import LibraryError
from blueprint_library.remote import WorkdayRemote
from tools import check_installation as helper


class InstallationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-install-check-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        initialize_library(self.data, 'owner', 'synthetic-install-password')
        self.server = make_server(self.data, managed=True, backup_dir=self.root / 'backups')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.close_server)
        self.credentials = patch.dict('os.environ', {'BLUEPRINT_BACKEND_USERNAME': 'owner',
                                                    'BLUEPRINT_BACKEND_PASSWORD': 'synthetic-install-password'})
        self.credentials.start(); self.addCleanup(self.credentials.stop)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.output = self.root / 'acceptance'

    def close_server(self):
        self.server.shutdown(); self.server.server_close(); self.worker.join()

    def test_current_backup_and_completed_verification_are_reused(self):
        initial = self.server.maintenance.state['last_backup']['id']
        with patch.object(self.server.maintenance, '_snapshot', side_effect=AssertionError('must reuse current backup')):
            first = helper.verify_installation(self.url, self.output)
            self.assertEqual(first['snapshot_id'], initial)
            self.assertFalse(first['reused_verification'])
            with patch.object(helper, '_authenticated_restore', side_effect=AssertionError('must reuse same restore proof')):
                with patch.object(helper, 'build_opener', side_effect=AssertionError('must reuse same private file')):
                    repeated = helper.verify_installation(self.url, self.output)
        self.assertTrue(repeated['reused_verification'])
        self.assertEqual(first['verified_at'], repeated['verified_at'])
        self.assertTrue(self.server.maintenance.status(actor='owner')['protection']['reusable'])
        self.assertFalse(repeated['off_host_durable_copy_verified'])
        progress = json.loads((self.output / 'installation-check.json').read_text(encoding='utf-8'))
        self.assertEqual(progress['stage'], 'complete')
        self.assertNotIn('password', json.dumps(progress))

    def test_interrupted_restore_reuses_downloaded_same_id_file(self):
        with patch.object(helper, '_authenticated_restore', side_effect=ValueError('synthetic interruption')):
            with self.assertRaises(ValueError):
                helper.verify_installation(self.url, self.output)
        progress = json.loads((self.output / 'installation-check.json').read_text(encoding='utf-8'))
        self.assertTrue((self.output / 'backup.zip').is_file())
        # Download completed, but the process may have stopped before renaming.
        (self.output / 'backup.zip').rename(self.output / 'backup.partial-synthetic.zip')
        with patch.object(helper, 'build_opener', side_effect=AssertionError('download already verified')):
            result = helper.verify_installation(self.url, self.output)
        self.assertEqual(progress['backup_id'], result['snapshot_id'])

    def test_ambiguous_create_reads_exact_operation_and_never_creates_twice(self):
        self.server.store.create_item({'title': 'new body changes coverage', 'url': 'https://example.com'})
        original = WorkdayRemote._request
        writes = []
        def ambiguous(remote, method, path, payload=None, query=None):
            result = original(remote, method, path, payload, query)
            if method == 'POST' and path == '/api/maintenance/backup':
                writes.append(payload)
                raise LibraryError('synthetic ambiguous response', 502)
            return result
        with patch.object(WorkdayRemote, '_request', new=ambiguous):
            result = helper.verify_installation(self.url, self.output)
            repeated = helper.verify_installation(self.url, self.output)
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0]['contract_version'], 2)
        progress = json.loads((self.output / 'installation-check.json').read_text(encoding='utf-8'))
        self.assertEqual(writes[0]['operation_id'], progress['operation_id'])
        self.assertEqual(result['snapshot_id'], repeated['snapshot_id'])
        self.assertEqual(self.server.maintenance.read_backup_request(progress['operation_id'])['status'], 'complete')

    def test_changed_live_data_preserves_previous_acceptance_without_new_claim(self):
        helper.verify_installation(self.url, self.output)
        previous = (self.output / 'verification.json').read_bytes()
        self.server.store.create_item({'title': 'changed after acceptance', 'url': 'https://example.com/new'})
        with patch.object(self.server.maintenance, '_snapshot', side_effect=AssertionError('must not silently restart')):
            with self.assertRaisesRegex(ValueError, '正式資料已改變'):
                helper.verify_installation(self.url, self.output)
        self.assertEqual(previous, (self.output / 'verification.json').read_bytes())

    def test_unknown_request_reads_existing_exact_id_without_repacking(self):
        self.server.store.create_item({'title': 'new current state', 'url': 'https://example.com'})
        original = WorkdayRemote._request
        writes = []
        def unknown_after_completion(remote, method, path, payload=None, query=None):
            result = original(remote, method, path, payload, query)
            if method == 'POST' and path == '/api/maintenance/backup':
                writes.append(payload)
            if query and query.get('operation_id') and result.get('backup_request', {}).get('status') == 'complete':
                result['backup_request']['status'] = 'unknown'
            return result
        with patch.object(WorkdayRemote, '_request', new=unknown_after_completion):
            result = helper.verify_installation(self.url, self.output)
        self.assertEqual(len(writes), 1)
        self.assertTrue(result['authenticated_restore'])

    def test_invalid_local_file_is_preserved_and_retrieved_from_same_backup(self):
        first = helper.verify_installation(self.url, self.output)
        (self.output / 'backup.zip').write_bytes(b'incomplete download')
        with patch.object(helper, '_authenticated_restore', side_effect=AssertionError('artifact SHA is unchanged')):
            repeated = helper.verify_installation(self.url, self.output)
        self.assertEqual(first['snapshot_id'], repeated['snapshot_id'])
        retained = list(self.output.glob('backup.invalid-*.zip'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), b'incomplete download')

    def test_ambiguous_client_receipt_reuses_actual_restore_evidence(self):
        original = WorkdayRemote._request
        interrupted = False
        def ambiguous(remote, method, path, payload=None, query=None):
            nonlocal interrupted
            result = original(remote, method, path, payload, query)
            if method == 'POST' and path.endswith('/private-copy') and not interrupted:
                interrupted = True
                raise LibraryError('synthetic ambiguous receipt', 502)
            return result
        with patch.object(WorkdayRemote, '_request', new=ambiguous):
            with self.assertRaises(LibraryError):
                helper.verify_installation(self.url, self.output)
        with patch.object(helper, '_authenticated_restore', side_effect=AssertionError('restore already succeeded')):
            with patch.object(helper, 'build_opener', side_effect=AssertionError('download already succeeded')):
                result = helper.verify_installation(self.url, self.output)
        self.assertTrue(result['reused_verification'])

    def test_legacy_contract_keeps_legacy_branch(self):
        fake = unittest.mock.Mock()
        fake._request.side_effect = [{'version': helper.__version__}, {'supported': True, 'status': 'ready'}]
        with patch.object(helper, 'WorkdayRemote', return_value=fake):
            with patch.object(helper, '_verify_legacy', return_value={'legacy': True}) as legacy:
                self.assertEqual(helper.verify_installation(self.url, self.output), {'legacy': True})
                legacy.assert_called_once_with(self.url, self.output)

    def test_legacy_contract_downloads_and_authenticates_real_isolated_restore(self):
        original = WorkdayRemote._request
        def legacy_status(remote, method, path, payload=None, query=None):
            result = original(remote, method, path, payload, query)
            if method == 'GET' and path == '/api/maintenance':
                result = {key: value for key, value in result.items()
                          if key != 'maintenance_contract'}
            return result
        with patch.object(WorkdayRemote, '_request', new=legacy_status):
            result = helper.verify_installation(self.url, self.output)
        self.assertTrue(result['authenticated_restore'])
        self.assertFalse(result['persistent_restart_verified'])
        self.assertFalse(result['ai_connection_verified'])
        self.assertTrue((self.output / 'backup.zip').is_file())
        self.assertTrue((self.output / 'snapshot/data/access.json').is_file())
        self.assertFalse((self.output / 'restore-check').exists())

    def test_version_mismatch_stops_before_any_backup_or_private_directory(self):
        fake = unittest.mock.Mock()
        fake._request.return_value = {'version': '0.0.0'}
        with patch.object(helper, 'WorkdayRemote', return_value=fake):
            with self.assertRaisesRegex(ValueError, '相同版本'):
                helper.verify_installation(self.url, self.output)
        fake._request.assert_called_once_with('GET', '/api/info')
        self.assertFalse(self.output.exists())


if __name__ == '__main__': unittest.main()
