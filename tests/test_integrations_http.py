"""Real HTTP checks for the dedicated external-service credential boundary."""
import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from blueprint_library.server import initialize_library, make_server
from blueprint_library.studio import Studio

PREFIX = '/api/integrations/v1'


class IntegrationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-integration-http-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        initialize_library(self.directory, 'owner', 'integration-http-test-password')
        self.server = make_server(self.directory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = 'http://127.0.0.1:' + str(self.server.server_port)
        self.admin = 'Basic ' + base64.b64encode(b'owner:integration-http-test-password').decode()

    def request(self, method, path, payload=None, authorization=None, marker=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if authorization:
            headers['Authorization'] = authorization
        if marker:
            headers['X-Blueprint-Request'] = '1'
        if origin:
            headers['Origin'] = origin
        request = Request(self.origin + path, method=method, headers=headers,
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def register(self):
        status, registration = self.request('POST', PREFIX + '/registry', {
            'name': '虛構外部服務', 'version': '0.1.0', 'record_types': ['message', 'note'],
            'commands': [{'name': 'takeover', 'label': '我接手', 'input_schema': {
                'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}}],
            'resource_refs': [], 'allowed_origins': [],
        }, self.admin)
        self.assertEqual(status, 201, registration)
        return registration['integration'], 'Bearer ' + registration['token']

    def record_payload(self, **changes):
        return {'schema_version': '1.0', 'record_type': 'message', 'external_id': 'fictional-message',
                'event_id': 'fictional-event', 'source_revision': 1, 'source': 'fictional-service',
                'occurred_at': '2026-10-24T10:00:00Z', 'payload': {'text': '虛構測試訊息'},
                'idempotency_key': 'fictional-retry', **changes}

    def test_bearer_can_only_enter_integration_namespace_and_cannot_administer_it(self):
        integration, bearer = self.register()
        self.assertEqual(self.request('GET', PREFIX + '/capabilities', authorization=bearer)[0], 200)
        for path in ['/api/info', '/api/studio/state', '/api/export', '/api/runtime', '/index.html']:
            self.assertEqual(self.request('GET', path, authorization=bearer)[0], 401, path)
        for method, path, payload in [('GET', '/registry', None),
                                      ('PATCH', '/registry/' + integration['id'], {'expected_version': 1, 'enabled': False}),
                                      ('POST', '/commands', {'integration_id': integration['id'], 'name': 'takeover',
                                                            'payload': {}, 'idempotency_key': 'forbidden'})]:
            status, result = self.request(method, PREFIX + path, payload, bearer)
            self.assertEqual(status, 403, result)
            self.assertEqual(result['code'], 'forbidden')

    def test_http_persistence_retry_conflict_and_cross_integration_isolation(self):
        integration, bearer = self.register()
        status, result = self.request('POST', PREFIX + '/records', self.record_payload(), bearer)
        self.assertEqual(status, 201, result)
        record_id = result['record_id']
        duplicate = self.request('POST', PREFIX + '/records', self.record_payload(), bearer)[1]
        self.assertEqual(duplicate['record_id'], record_id)
        readback = self.request('GET', PREFIX + '/records/' + record_id, authorization=bearer)[1]
        self.assertEqual(readback['record']['payload']['text'], '虛構測試訊息')
        status, conflict = self.request('POST', PREFIX + '/records', self.record_payload(payload={'text': '改寫'}), bearer)
        self.assertEqual((status, conflict['code']), (409, 'conflict'))
        other, other_bearer = self.register()
        self.assertNotEqual(integration['id'], other['id'])
        self.assertIn(self.request('GET', PREFIX + '/records/' + record_id, authorization=other_bearer)[0], (403, 404))
        status, denial = self.request('GET', PREFIX + '/records?integration_id=' + integration['id'], authorization=other_bearer)
        self.assertEqual(status, 403, denial)
        self.assertEqual(self.request('GET', PREFIX + '/records', authorization=other_bearer)[1]['records'], [])

    def test_revoked_credential_cannot_replay_a_saved_receipt(self):
        integration, bearer = self.register()
        self.assertEqual(self.request('POST', PREFIX + '/records', self.record_payload(), bearer)[0], 201)
        status, result = self.request('POST', PREFIX + '/registry/' + integration['id'] + '/revoke',
                                      {'expected_version': integration['version']}, self.admin)
        self.assertEqual(status, 200, result)
        for method, path, payload in [('GET', '/capabilities', None), ('POST', '/records', self.record_payload())]:
            status, error = self.request(method, PREFIX + path, payload, bearer)
            self.assertEqual((status, error['code']), (401, 'unauthorized'))
            self.assertFalse(error['retryable'])

    def test_existing_origin_write_guards_and_stable_errors_apply_to_external_writes(self):
        _, bearer = self.register()
        for options in [{'marker': False}, {'origin': 'https://unapproved.example'}]:
            status, error = self.request('POST', PREFIX + '/records', self.record_payload(), bearer, **options)
            self.assertEqual((status, error['code']), (403, 'forbidden'))
        for path in ['/records?limit=bad', '/records?limit=1&limit=2', '/events?limit=bad']:
            status, error = self.request('GET', PREFIX + path, authorization=bearer)
            self.assertEqual((status, error['code']), (400, 'invalid_request'))
        status, error = self.request('GET', PREFIX + '/unknown', authorization=bearer)
        self.assertEqual((status, error['code']), (404, 'not_found'))
        status, error = self.request('GET', PREFIX + '/capabilities')
        self.assertEqual((status, error['code']), (401, 'unauthorized'))
        self.assertEqual(set(error), {'error', 'code', 'retryable'})

    def test_member_never_receives_integration_setup_or_company_external_records(self):
        self.register()
        Studio(self.server.store).save_user({'username': 'staff', 'name': '同事', 'password': 'integration-member-test',
                                            'role': 'member', 'enabled': True})
        member = 'Basic ' + base64.b64encode(b'staff:integration-member-test').decode()
        owner_state = self.request('GET', '/api/studio/state', authorization=self.admin)[1]
        self.assertTrue(owner_state['capabilities']['integration_v1'])
        self.assertEqual(owner_state['integration_count'], 1)
        member_state = self.request('GET', '/api/studio/state', authorization=member)[1]
        self.assertFalse(member_state['capabilities']['integration_v1'])
        self.assertEqual(member_state['integration_count'], 0)
        self.assertEqual(self.request('GET', PREFIX + '/registry', authorization=member)[0], 403)


if __name__ == '__main__':
    unittest.main()
