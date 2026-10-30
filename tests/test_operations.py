"""Synthetic private operation receipts, HTTP permissions and retry/readback."""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from blueprint_library.operations import Operations, STAGES
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio


def request(**changes):
    return {'contract_version': 1, 'operation_id': 'synthetic-source-release', 'plan_digest': 'a' * 64,
            'request_id': 'discover-1', 'expected_version': 0,
            'changes': {'change_type': 'source_release', 'target_services': [],
                        'stage': 'discover', **changes}}


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-private-operations-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.operations = Operations(self.directory)

    def assert_status(self, status, fn, *args):
        with self.assertRaises(LibraryError) as error:
            fn(*args)
        self.assertEqual(error.exception.status, status)

    def test_retry_after_unknown_result_survives_restart_and_returns_original_receipt(self):
        first = self.operations.write(request(), 'owner')
        step = request(stage='plan')
        step.update(request_id='plan-1', expected_version=1)
        self.operations.write(step, 'owner')
        reopened = Operations(self.directory)
        self.assertEqual(reopened.get('synthetic-source-release', 'owner')['operation']['version'], 2)
        self.assertEqual(reopened.write(request(), 'owner'), first)
        self.assertEqual(reopened.get('synthetic-source-release', 'owner')['operation']['version'], 2)
        if os.name != 'nt':
            self.assertEqual((self.directory / 'operations.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue((self.directory / 'operations.json').is_file())

    def test_stale_cas_changed_plan_and_changed_retry_payload_conflict(self):
        self.operations.write(request(), 'owner')
        stale = request(stage='plan'); stale['request_id'] = 'second'
        self.assert_status(409, self.operations.write, stale, 'owner')
        changed = request(); changed['plan_digest'] = 'b' * 64
        self.assert_status(409, self.operations.write, changed, 'owner')
        changed = request(next_step='changed retry')
        self.assert_status(409, self.operations.write, changed, 'owner')
        self.assertEqual(self.operations.get('synthetic-source-release', 'owner')['operation']['version'], 1)

    def test_two_writers_cannot_advance_same_expected_version(self):
        self.operations.write(request(), 'owner')
        def write(number):
            body = request(stage='plan'); body.update(expected_version=1, request_id='plan-' + str(number))
            try:
                return self.operations.write(body, 'owner')['operation']['version']
            except LibraryError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(write, (1, 2))), [2, 409])

    def test_waiting_user_resumes_same_stage_and_complete_is_immutable(self):
        body = request()
        self.operations.write(body, 'owner')
        body.update(request_id='wait', expected_version=1, changes={'stage': 'waiting_user', 'next_step': '本人登入平台。'})
        paused = self.operations.write(body, 'owner')['operation']
        self.assertEqual(paused['resume_stage'], 'discover')
        body.update(request_id='resume', expected_version=2, changes={'stage': 'discover'})
        record = self.operations.write(body, 'owner')['operation']
        for index, stage in enumerate(STAGES[1:]):
            body.update(request_id='advance-' + str(index), expected_version=record['version'], changes={'stage': stage})
            record = self.operations.write(body, 'owner')['operation']
        self.assertEqual(record['stage'], 'complete')
        body.update(request_id='repeat-publish', expected_version=record['version'], changes={'stage': 'publish_if_needed'})
        self.assert_status(409, self.operations.write, body, 'owner')

    def test_owner_scope_and_release_plan_require_no_private_actions(self):
        record = self.operations.write(request(), 'owner')['operation']
        plan = record['verification_plan']
        self.assertEqual(plan['backup_policy'], 'none')
        self.assertEqual(plan['publish_services'], [])
        self.assertEqual(self.operations.list('another-owner')['operations'], [])
        self.assert_status(404, self.operations.get, record['operation_id'], 'another-owner')
        self.assert_status(404, self.operations.write, request(), 'another-owner')

    def test_skip_stage_secret_envelopes_and_bad_sources_rejected_without_writes(self):
        for changes in [{'stage': 'apply'}, {'sources': {'upstream_commit': 'short'}},
                        {'target_services': ['private-workbench']},
                        {'change_type': 'fresh_install', 'target_services': ['old-site'], 'protected_services': ['old-site']},
                        {'evidence': {'response': {'access_token': 'synthetic'}}}]:
            with self.subTest(changes=changes):
                with self.assertRaises(LibraryError):
                    self.operations.write(request(**changes), 'owner')
        self.assertFalse((self.directory / 'operations.json').exists())
        body = request(); body['contract_version'] = True
        with self.assertRaises(LibraryError):
            self.operations.write(body, 'owner')


class OperationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-private-operation-http-')
        self.directory = Path(self.temp.name)
        self.data = self.directory / 'company'
        initialize_library(self.data, 'owner', 'synthetic-owner-password')
        Studio(Store(self.data)).save_user({'username': 'staff', 'password': 'synthetic-staff-password', 'role': 'member'})
        self.server = make_server(self.data, managed=True, backup_dir=self.directory / 'backups')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.temp.cleanup()

    def call(self, method, path, data=None, user='owner', account=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {}
        if user:
            headers['Authorization'] = 'Basic ' + base64.b64encode(f'{user}:synthetic-{user}-password'.encode()).decode()
        if account is not None:
            headers['X-Blueprint-Account'] = account
        body = None if data is None else json.dumps(data).encode()
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-Blueprint-Request': '1'})
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_owner_only_operations_create_readback_and_client_receipt(self):
        self.assertEqual(self.call('GET', '/api/maintenance', account='stale-owner')[0], 409)
        self.assertEqual(self.call('POST', '/api/maintenance/operations', request(), account='stale-owner')[0], 409)
        for user, expected in ((None, 401), ('staff', 403)):
            for method, path, body in [('GET', '/api/maintenance/operations', None),
                                       ('POST', '/api/maintenance/operations', request())]:
                self.assertEqual(self.call(method, path, body, user=user)[0], expected)
        status, _, saved = self.call('POST', '/api/maintenance/operations', request())
        self.assertEqual(status, 200, saved)
        status, _, readback = self.call('GET', '/api/maintenance/operations?id=synthetic-source-release')
        self.assertEqual(saved, readback)
        backup_id = self.server.maintenance.state['last_backup']['id']
        metadata_path = '/api/maintenance/backups/' + backup_id + '/metadata'
        status, _, metadata = self.call('GET', metadata_path)
        self.assertEqual(status, 200, metadata)
        metadata = json.loads(metadata)
        download_path = metadata['download_path']
        status, head, content = self.call('HEAD', download_path)
        self.assertEqual((status, content), (200, b''))
        status, _, archive = self.call('GET', download_path)
        self.assertEqual(status, 200)
        self.assertEqual(int(head['Content-Length']), len(archive))
        self.assertEqual(hashlib.sha256(archive).hexdigest(), metadata['sha256'])
        receipt = {'file_reference': 'private-test-copy.zip', 'size': len(archive),
                   'sha256': hashlib.sha256(archive).hexdigest(), 'readback_verified': True}
        self.assertEqual(self.call('POST', download_path + '/private-copy', receipt, user='staff')[0], 403)
        self.assertEqual(self.call('POST', download_path + '/private-copy', receipt)[0], 200)
        status, _, payload = self.call('GET', '/api/maintenance')
        self.assertEqual(status, 200, payload)
        self.assertTrue(json.loads(payload)['protection']['reusable'])

    def test_backup_wrapper_is_versioned_and_mcp_transforms_operation_json(self):
        from unittest.mock import patch
        from blueprint_library.mcp_server import Bridge
        with patch.object(self.server.maintenance, 'request_backup', return_value={'queued': True}) as backup:
            self.assertEqual(self.call('POST', '/api/maintenance/backup', {})[0], 202)
            backup.assert_called_once_with()
            backup.reset_mock()
            payload = {'contract_version': 2, 'operation_id': 'backup-step', 'plan_digest': 'b' * 64}
            self.assertEqual(self.call('POST', '/api/maintenance/backup', payload)[0], 202)
            backup.assert_called_once_with('backup-step', 'b' * 64)
            bad = deepcopy(payload); bad['unexpected'] = 'value'
            self.assertEqual(self.call('POST', '/api/maintenance/backup', bad)[0], 400)
        with patch.dict('os.environ', {'BLUEPRINT_BACKEND_USERNAME': 'owner', 'BLUEPRINT_BACKEND_PASSWORD': 'synthetic-owner-password'}):
            bridge = Bridge('http://127.0.0.1:' + str(self.server.server_port))
            payload = request(); payload['changes_json'] = json.dumps(payload.pop('changes'))
            saved = bridge.call('blueprint_operation_write', payload)
            self.assertEqual(bridge.call('blueprint_operation_read', {'id': 'synthetic-source-release'}), saved)
            self.assertEqual(len(bridge.tools()), 52)
            readonly = {item['name'] for item in Bridge(bridge.remote.url, read_only=True).tools()}
            self.assertIn('blueprint_backup_artifact', readonly)
            self.assertNotIn('blueprint_backup_private_copy', readonly)
            self.assertNotIn('blueprint_operation_write', readonly)
            backup_id = self.server.maintenance.state['last_backup']['id']
            metadata = bridge.call('blueprint_backup_artifact', {'id': backup_id})
            self.assertEqual(metadata['id'], backup_id)
            receipt = {'id': backup_id, 'file_reference': 'private-mcp-test.zip', 'size': metadata['size'],
                       'sha256': metadata['sha256'], 'readback_verified': True}
            self.assertEqual(bridge.call('blueprint_backup_private_copy', receipt)['backup_id'], backup_id)

    def test_unknown_backup_operation_is_readable_after_restart_without_repackaging(self):
        from unittest.mock import patch
        receipt = {'operation_id': 'interrupted-backup', 'plan_digest': 'c' * 64,
                   'backup_id': '20261025T080000Z-cccccccccccc', 'status': 'unknown'}
        with self.server.maintenance.mutex:
            self.server.maintenance.state['backup_requests']['interrupted-backup'] = receipt
            self.server.maintenance.save()
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        self.server = make_server(self.data, managed=True, backup_dir=self.directory / 'backups')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        before = sorted(path.name for path in (self.directory / 'backups').iterdir())
        with patch.object(self.server.maintenance, '_snapshot', side_effect=AssertionError('GET cannot package')):
            status, _, body = self.call('GET', '/api/maintenance?operation_id=interrupted-backup')
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)['backup_request'], receipt)
            self.assertEqual(self.call('GET', '/api/maintenance?operation_id=missing')[0], 404)
            self.assertEqual(self.call('GET', '/api/maintenance?operation_id=first&operation_id=second')[0], 400)
        self.assertEqual(before, sorted(path.name for path in (self.directory / 'backups').iterdir()))
