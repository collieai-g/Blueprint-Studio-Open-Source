"""Synthetic state and interruption tests for maintenance contract v2."""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from blueprint_library import backup
from blueprint_library.maintenance import Maintenance, atomic_json
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, Store


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-coverage-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.directory = self.root / 'backups'
        initialize_library(self.data, 'owner', 'synthetic-password-only')
        self.item = Store(self.data).add_file('synthetic.txt', b'first content')
        self.runtime = Maintenance(self.data, self.directory, fingerprint='test-a').prepare()
        self.addCleanup(lambda: self.runtime.close())

    def private_copy(self):
        info = self.runtime.backup_info(self.runtime.state['last_backup']['id'])
        return self.runtime.record_client_receipt(info['id'], {
            'file_reference': '/private/downloads/' + info['id'] + '.zip',
            'size': info['size'], 'sha256': info['sha256'], 'readback_verified': True,
        }, actor='owner')

    def wait_backup(self, operation_id):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            request = self.runtime.request_backup(operation_id, 'a' * 64)
            if request['status'] in {'complete', 'unknown'}:
                return request
            time.sleep(.01)
        self.fail('backup did not finish')

    def test_rows_auth_and_attachment_content_change_digest_without_count_changes(self):
        original = backup.coverage(self.data)
        with sqlite3.connect(self.data / 'library.sqlite3') as conn:
            conn.execute('UPDATE items SET title=? WHERE id=?', ('changed title', self.item['id']))
        edited = backup.coverage(self.data)
        self.assertNotEqual(original['digest'], edited['digest'])
        blob = Store(self.data).get_file(self.item['id'])[0]
        blob.write_bytes(b'other content')
        attached = backup.coverage(self.data)
        self.assertNotEqual(edited['digest'], attached['digest'])
        credentials = json.loads((self.data / 'access.json').read_text())
        credentials['rounds'] += 1
        (self.data / 'access.json').write_text(json.dumps(credentials))
        self.assertNotEqual(attached['digest'], backup.coverage(self.data)['digest'])

    def test_private_attestation_is_scoped_and_does_not_change_coverage(self):
        before = self.runtime.status(actor='owner')
        self.assertEqual(before['protection']['state'], 'private_copy_pending')
        receipt = self.private_copy()
        after = self.runtime.status(actor='owner')
        self.assertEqual(before['coverage'], after['coverage'])
        self.assertEqual(after['protection']['state'], 'reusable')
        self.assertEqual(receipt['evidence'], 'client_attestation')
        self.assertFalse(after['off_host_backup_verified'])
        self.assertNotIn('client_copy', self.runtime.status(actor='another-owner')['last_backup'])
        self.assertFalse(self.runtime.status(actor='another-owner')['protection']['reusable'])
        atomic_json(self.directory / 'operations.json', {'synthetic': 'receipt outside business data'})
        self.assertEqual(after['coverage'], self.runtime.status(actor='owner')['coverage'])
        with sqlite3.connect(self.data / 'library.sqlite3') as conn:
            conn.execute('UPDATE items SET notes=? WHERE id=?', ('same number of rows', self.item['id']))
        self.assertFalse(self.runtime.status(actor='owner')['protection']['reusable'])

    def test_boot_summary_does_not_scan_private_files_or_claim_current_protection(self):
        self.private_copy()
        with patch.object(self.runtime, '_coverage', side_effect=AssertionError('must not scan on boot')):
            with patch.object(self.runtime, 'backup_info', side_effect=AssertionError('must not hash backup on boot')):
                summary = self.runtime.status(actor='owner', verify_current=False)
        self.assertIsNone(summary['coverage'])
        self.assertFalse(summary['verification_current'])
        self.assertFalse(summary['protection']['reusable'])
        self.assertEqual(summary['protection']['next_missing'], 'verify_current_state')

    def test_receipt_requires_actual_matching_size_hash_and_readback(self):
        info = self.runtime.backup_info(self.runtime.state['last_backup']['id'])
        body = {'file_reference': '/private/test.zip', 'size': info['size'],
                'sha256': info['sha256'], 'readback_verified': True}
        for change in [{'sha256': '0' * 64}, {'size': info['size'] + 1},
                       {'readback_verified': False}, {'file_reference': 'https://public.invalid/file'}]:
            with self.assertRaises(LibraryError):
                self.runtime.record_client_receipt(info['id'], {**body, **change}, actor='owner')
        first = self.runtime.record_client_receipt(info['id'], body, actor='owner')
        self.assertEqual(first, self.runtime.record_client_receipt(info['id'], body, actor='owner'))
        self.runtime.state['client_copies'][info['id']]['owner']['recorded_at'] = 0
        refreshed = self.runtime.record_client_receipt(info['id'], body, actor='owner')
        self.assertGreater(refreshed['recorded_at'], 0)
        self.assertEqual(first['sha256'], refreshed['sha256'])
        archive = self.runtime.download(info['id'])
        archive.write_bytes(archive.read_bytes() + b'corrupt')
        self.assertFalse(self.runtime.status(actor='owner')['protection']['reusable'])

    def test_operation_retry_survives_restart_and_never_repacks(self):
        self.runtime.start()
        queued = self.runtime.request_backup('synthetic-op', 'a' * 64)
        self.assertEqual(queued['backup_id'], self.runtime.request_backup('synthetic-op', 'a' * 64)['backup_id'])
        complete = self.wait_backup('synthetic-op')
        self.assertEqual(complete['status'], 'complete')
        with patch.object(self.runtime, '_snapshot', side_effect=AssertionError('must not repack')):
            self.assertEqual(complete, self.runtime.request_backup('synthetic-op', 'a' * 64))
        with self.assertRaises(LibraryError) as conflict:
            self.runtime.request_backup('synthetic-op', 'b' * 64)
        self.assertEqual(conflict.exception.status, 409)
        self.runtime.close()
        self.runtime = Maintenance(self.data, self.directory, fingerprint='test-a').prepare()
        with patch.object(self.runtime, '_snapshot', side_effect=AssertionError('must not repack')):
            self.assertEqual(complete, self.runtime.request_backup('synthetic-op', 'a' * 64))

    def test_interrupted_known_archive_is_read_back_but_partial_remains_unknown(self):
        info = self.runtime.backup_info(self.runtime.state['last_backup']['id'])
        self.runtime.state['backup_requests'] = {
            'known': {'operation_id': 'known', 'plan_digest': 'a' * 64, 'status': 'running', 'backup_id': info['id']},
            'partial': {'operation_id': 'partial', 'plan_digest': 'a' * 64, 'status': 'running',
                        'backup_id': '20260101T000000Z-123456789abc'},
        }
        self.runtime.save(); self.runtime.close()
        self.runtime = Maintenance(self.data, self.directory, fingerprint='test-a').prepare()
        self.assertEqual(self.runtime.request_backup('known', 'a' * 64)['status'], 'complete')
        self.assertEqual(self.runtime.request_backup('partial', 'a' * 64)['status'], 'unknown')

    def test_update_reuses_current_retained_private_copy_and_rechecks_changes(self):
        self.private_copy()
        before = self.runtime.state['last_backup']['id']
        self.runtime.close()
        self.runtime = Maintenance(self.data, self.directory, fingerprint='test-b').prepare()
        self.assertEqual(self.runtime.state['last_backup']['id'], before)
        self.runtime.close()
        Store(self.data).create_item({'title': 'new data', 'url': 'https://example.com'})
        self.runtime = Maintenance(self.data, self.directory, fingerprint='test-c').prepare()
        self.assertNotEqual(self.runtime.state['last_backup']['id'], before)

    def test_coverage_is_snapshot_stable_and_includes_presentation_preferences(self):
        before = backup.coverage(self.data)
        with sqlite3.connect(self.data / 'library.sqlite3') as conn:
            conn.execute('INSERT INTO studio_preferences VALUES (?, ?, ?)',
                         ('owner', 1, json.dumps({'_presentation': {'version': 1, 'documents': {}}})))
        changed = backup.coverage(self.data)
        self.assertNotEqual(before['digest'], changed['digest'])
        self.assertEqual(changed, backup.coverage(self.data))
        with sqlite3.connect(self.data / 'library.sqlite3') as conn:
            conn.execute('UPDATE studio_preferences SET data=? WHERE username=?',
                         (json.dumps({'_presentation': {'version': 1, 'documents': {'synthetic': 'change'}}}), 'owner'))
        self.assertNotEqual(changed['digest'], backup.coverage(self.data)['digest'])


if __name__ == '__main__': unittest.main()
