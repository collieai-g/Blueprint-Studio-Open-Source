"""Native MCP and Sites MCP use the same authenticated Agent HTTP service.

Temporary fixtures prove transport/persistence, not live Codex tool loading or AI quality.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.mcp_server import Bridge
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import LibraryError

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = {
    'contract_version': 1, 'id': 'bridge-summary', 'name': '接駁測試摘要', 'version': '1.0.0',
    'description': '隔離測試，不是已完成的 AI 回覆。', 'adapter': 'connected_client',
    'instructions': '只整理使用者選定資料，保留來源。',
    'settings': [{'key': 'language', 'label': '語言', 'type': 'text', 'default': '繁體中文'}],
    'operations': [{'id': 'summarize', 'name': '整理摘要', 'instructions': '整理已提供的內容。',
                    'input_fields': [{'key': 'focus', 'label': '重點', 'type': 'text', 'required': True}]}],
    'ui': {'layout': 'standard', 'result_label': '摘要'},
}


class AgentMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-agents-mcp-')
        self.data = Path(self.temp.name) / 'company'
        self.password = secrets.token_urlsafe(24)
        initialize_library(self.data, 'test-owner', self.password)
        self.server = make_server(self.data, managed=True, backup_dir=Path(self.temp.name) / 'backups')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.environment = patch.dict(os.environ, {
            'BLUEPRINT_BACKEND_USERNAME': 'test-owner', 'BLUEPRINT_BACKEND_PASSWORD': self.password})
        self.environment.start()
        self.bridge = Bridge(self.url)

    def tearDown(self):
        self.environment.stop()
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    def call(self, name, **arguments):
        return self.bridge.call('blueprint_' + name, arguments)

    def api(self, method, path, payload=None):
        return self.bridge.remote._request(method, '/api/studio/' + path, payload=payload)

    def register(self):
        agent = self.call('agent_register', manifest_json=json.dumps(MANIFEST),
            source_json=json.dumps({'repository': 'https://github.com/example/bridge-summary',
                                    'revision': 'b' * 40}))['agent']
        self.assertEqual(agent['connection_state'], 'added')
        self.assertEqual(self.call('agents_list')['agents'][0]['id'], agent['id'])
        return self.call('agent_action', id=agent['id'], expected_version=agent['version'], action='connect')['agent']

    def job(self, agent):
        record = self.api('POST', 'records', {'kind': 'report', 'title': '測試資料',
            'data': {'content': '項目於星期五完成，下一步是核對交付。', 'source': 'test fixture'}})['record']
        refs = [{'kind': 'report', 'id': record['id']}]
        payload = {'agent_id': agent['id'], 'operation_id': 'summarize', 'title': '整理指定資料',
                   'inputs': {'focus': '進度及下一步'}, 'refs': refs, 'source_key': 'test-summary-job'}
        job = self.api('POST', 'agent-jobs', payload)['job']
        self.assertEqual(job['state'], 'awaiting_codex')
        return job, refs

    def test_native_and_sites_share_pinned_job_result_and_settings(self):
        agent = self.register()
        job, refs = self.job(agent)
        context = self.call('agent_job_context', id=job['id'])
        self.assertEqual(context['sources'][0]['id'], refs[0]['id'])
        source = 'Agent bridge integration test'
        started = self.call('agent_job_action', id=job['id'], expected_version=job['version'], action='start',
            arguments_json=json.dumps({'source': source, 'context_digest': context['context_digest']}))['job']
        self.assertEqual(started['state'], 'in_progress')
        result = '測試輸出：星期五完成項目；下一步核對交付。'
        fixture = subprocess.run(['node', str(ROOT / 'tests/agent_bridge_fixture.mjs')],
            input=json.dumps({'backend': self.url, 'username': 'test-owner', 'password': self.password,
                             'agent_id': agent['id'], 'job_id': job['id'], 'version': started['version'],
                             'context_digest': context['context_digest'], 'source': source, 'refs': refs, 'result': result}),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(fixture.returncode, 0, fixture.stderr)
        evidence = json.loads(fixture.stdout)
        self.assertEqual(self.call('agent_job_get', id=job['id'])['job'], evidence['job'])
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], evidence['agent'])
        self.assertEqual(self.call('agent_jobs_list', agent_id=agent['id'], state='completed')['total'], 1)
        with self.assertRaises(LibraryError) as conflict:
            self.call('agent_update', id=agent['id'], expected_version=agent['version'], settings_json='{}')
        self.assertEqual(conflict.exception.status, 409)

    def test_failures_readonly_and_invalid_json_never_create_success(self):
        agent = self.register()
        job, refs = self.job(agent)
        readonly = Bridge(self.url, read_only=True)
        names = {tool['name'] for tool in readonly.tools()}
        self.assertIn('blueprint_agent_job_context', names)
        self.assertNotIn('blueprint_agent_job_action', names)
        with self.assertRaises(LibraryError):
            readonly.call('blueprint_agent_job_action', {'id': job['id'], 'expected_version': job['version'],
                'action': 'fail', 'arguments_json': '{"reason":"blocked"}'})
        for name, arguments in [
            ('agent_register', {'manifest_json': '[]', 'source_json': '{}'}),
            ('agent_update', {'id': agent['id'], 'expected_version': agent['version'], 'settings_json': 'null'}),
            ('agent_job_create', {'agent_id': agent['id'], 'operation_id': 'summarize', 'title': 'invalid',
                                  'inputs_json': '[]', 'refs_json': json.dumps(refs)}),
            ('agent_job_get', {'id': '../access.json'}),
        ]:
            with self.assertRaises(LibraryError): self.call(name, **arguments)
        failed = self.call('agent_job_action', id=job['id'], expected_version=job['version'], action='fail',
                           arguments_json=json.dumps({'reason': '測試客戶端缺少所需工具，沒有產生成果。'}))['job']
        self.assertEqual(failed['state'], 'failed')
        self.assertFalse(failed['result'])
        self.assertIn('缺少', failed['error'])
        self.assertEqual(self.api('GET', 'agent-jobs/' + job['id'])['job'], failed)


if __name__ == '__main__':
    unittest.main()
