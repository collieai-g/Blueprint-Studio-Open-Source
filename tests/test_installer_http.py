"""The updater UI cannot select another studio or invoke the supervisor token API."""
import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.server import initialize_library, make_server
from blueprint_library.studio import Studio
from blueprint_library.store import Store
from blueprint_library.agents import Agents
from test_agents import sample_manifest


class InstallerHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-installer-http-')
        base = Path(self.temp.name).resolve()
        self.data = base / 'data'
        self.root = base / 'installation'
        self.root.mkdir()
        (self.root / 'installation.json').write_text(json.dumps({'data_dir': str(self.data)}), encoding='utf-8')
        initialize_library(self.data, 'owner', 'synthetic-owner-password')
        studio = Studio(Store(self.data))
        for user, role in [('admin', 'admin'), ('member', 'member')]:
            studio.save_user({'username': user, 'password': 'synthetic-' + user + '-password', 'role': role})
        self.server = make_server(self.data)
        self.server.installer_root = str(self.root)
        self.server.installer_token = 'synthetic-boot-token-' + 'a' * 32
        self.server.installer_release_id = 'b' * 64
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.temp.cleanup()

    def call(self, method, path='/api/installer', body=None, user='owner', extra=None):
        headers = {}
        if user:
            headers['Authorization'] = 'Basic ' + base64.b64encode(
                f'{user}:synthetic-{user}-password'.encode()).decode()
            headers['X-Blueprint-Account'] = user
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-Blueprint-Request': '1'})
        headers.update(extra or {})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            conn.request(method, path, None if body is None else json.dumps(body), headers)
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()

    def test_authenticated_owner_scope_and_fixed_installation_binding(self):
        with patch('blueprint_library.installer.read_status', return_value={'supported': True, 'version': 1}):
            self.assertEqual(self.call('GET', user=None)[0], 401)
            self.assertEqual(self.call('GET', user='member')[0], 403)
            self.assertEqual(self.call('GET', user='admin')[0], 200)
            self.assertEqual(self.call('GET', extra={'X-Blueprint-Account': 'stale'})[0], 409)
            (self.root / 'installation.json').write_text(json.dumps({'data_dir': str(self.data.parent / 'other')}), encoding='utf-8')
            self.assertEqual(self.call('GET')[0], 503)

    def test_apply_requires_owner_current_account_and_draft_acknowledgement(self):
        body = {'release_id': 'c' * 64, 'expected_version': 2, 'drafts_saved': True}
        with patch('blueprint_library.installer.request_apply', return_value={'phase': 'applying'}) as apply:
            self.assertEqual(self.call('POST', '/api/installer/apply', body, user='admin')[0], 403)
            self.assertEqual(self.call('POST', '/api/installer/apply', {**body, 'drafts_saved': False})[0], 409)
            self.assertEqual(self.call('POST', '/api/installer/apply', {**body, 'path': '/other'})[0], 409)
            self.assertEqual(self.call('POST', '/api/installer/apply', body, extra={'Origin': 'https://other.invalid'})[0], 403)
            self.assertEqual(self.call('POST', '/api/installer/apply', body)[0], 202)
            apply.assert_called_once_with(self.root, 'c' * 64, 2)

    def test_supervisor_token_is_not_user_authentication_and_is_per_boot(self):
        path = '/api/installer/internal'
        self.assertEqual(self.call('GET', path)[0], 404)
        self.assertEqual(self.call('GET', path, extra={'X-Blueprint-Installer': 'old-token'})[0], 404)
        header = {'X-Blueprint-Installer': self.server.installer_token}
        self.assertEqual(self.call('GET', path, user=None, extra={**header, 'Origin': 'https://other.invalid'})[0], 404)
        status, data = self.call('GET', path, user=None, extra=header)
        self.assertEqual(status, 200)
        self.assertEqual(data['release_id'], 'b' * 64)
        self.assertNotIn('token', data)
        self.assertFalse(data['ready'])

    def test_unmanaged_workbench_honestly_reports_unsupported(self):
        self.server.installer_root = None
        self.assertEqual(self.call('GET'), (200, {'supported': False}))
        self.assertEqual(self.call('POST', '/api/installer/check', {})[0], 503)

    def test_stopping_blocks_new_company_writes(self):
        self.server.installer_stopping = True
        self.assertEqual(self.call('POST', '/api/items', {'kind': 'note', 'title': 'synthetic'})[0], 503)

    def test_module_admission_gate_is_bound_to_the_original_agent_only(self):
        agents = Agents(self.server.store)
        first = agents.register({'manifest': sample_manifest(), 'source': {
            'repository': 'https://github.com/example/summary-test', 'revision': 'a' * 40}})['agent']
        other = sample_manifest()
        other['id'] = 'unrelated-agent'
        second = agents.register({'manifest': other, 'source': {
            'repository': 'https://github.com/example/other', 'revision': 'b' * 40}})['agent']
        config = {'data_dir': str(self.data), 'modules': {'bluesai-cs-0001': {'expected_agent_id': first['id']}}}
        (self.root / 'installation.json').write_text(json.dumps(config), encoding='utf-8')
        header = {'X-Blueprint-Installer': self.server.installer_token}
        path = '/api/installer/internal'
        self.assertEqual(self.call('POST', path, {'action': 'agent-quiesce', 'agent_id': second['id']},
                                   user=None, extra=header)[0], 403)
        status, result = self.call('POST', path, {'action': 'agent-quiesce', 'agent_id': first['id']},
                                   user=None, extra=header)
        self.assertEqual(status, 200)
        self.assertFalse(result['busy'])
        def job(agent):
            return agents.create_job({'agent_id': agent['id'], 'operation_id': 'summarize',
                                      'title': 'synthetic retained work', 'inputs': {}, 'refs': []})
        from blueprint_library.store import LibraryError
        with self.assertRaises(LibraryError) as caught:
            job(first)
        self.assertEqual(caught.exception.status, 409)
        unrelated = job(second)['job']
        self.assertEqual(unrelated['agent_id'], second['id'])
        self.assertEqual(self.call('POST', path, {'action': 'agent-resume', 'agent_id': first['id'], 'extra': True},
                                   user=None, extra=header)[0], 400)
        self.assertIn(first['id'], self.server.store.agent_updates_paused)
        self.assertEqual(self.call('POST', path, {'action': 'agent-resume', 'agent_id': first['id']},
                                   user=None, extra=header)[0], 200)
        pending = job(first)['job']
        status, result = self.call('POST', path, {'action': 'agent-quiesce', 'agent_id': first['id']},
                                   user=None, extra=header)
        self.assertEqual(status, 200)
        self.assertTrue(result['busy'])
        self.assertEqual(agents.get_job(pending['id']), pending)


if __name__ == '__main__':
    unittest.main()
