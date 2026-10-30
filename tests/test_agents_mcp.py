"""Native MCP and Sites MCP use the same authenticated Agent HTTP service.

Temporary fixtures prove transport/persistence, not live Codex tool loading or AI quality.
"""
import base64
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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

    def call(self, tool_name, **arguments):
        return self.bridge.call('blueprint_' + tool_name, arguments)

    def api(self, method, path, payload=None):
        return self.bridge.remote._request(method, '/api/studio/' + path, payload=payload)

    def http(self, method, path, payload=None, *, authenticated=True, marker=True,
             username='test-owner', password=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if authenticated:
            credentials = f'{username}:{password or self.password}'.encode()
            headers['Authorization'] = 'Basic ' + base64.b64encode(credentials).decode()
        if marker:
            headers['X-Blueprint-Request'] = '1'
        if origin:
            headers['Origin'] = origin
        request = Request(self.url + path, method=method, headers=headers,
                          data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def upgrade(self, agent, key='bridge-upgrade-1'):
        manifest = copy.deepcopy(MANIFEST)
        manifest['version'] = '1.1.0'
        manifest['instructions'] = '整理選定內容，分開進度與下一步。'
        manifest['settings'][0]['default'] = '新版預設'
        manifest['settings'].append({'key': 'concise', 'label': '精簡', 'type': 'boolean', 'default': False})
        return {'id': agent['id'], 'expected_version': agent['version'], 'upgrade_key': key,
                'manifest_json': json.dumps(manifest),
                'source_json': json.dumps({**agent['source'], 'revision': 'c' * 40}),
                'settings_json': json.dumps({'concise': True})}

    def restart(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.server = make_server(self.data, managed=True, backup_dir=Path(self.temp.name) / 'backups')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.bridge = Bridge(self.url)

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

    def test_upgrade_extends_existing_tool_without_changing_catalog_or_legacy_settings(self):
        definitions = {tool['name']: tool for tool in self.bridge.tools()}
        self.assertEqual(len(definitions), 52)
        schema = definitions['blueprint_agent_update']['inputSchema']
        self.assertEqual(schema['required'], ['id', 'expected_version'])
        self.assertTrue({'settings_json', 'enabled', 'manifest_json', 'source_json', 'upgrade_key'} <=
                        set(schema['properties']))
        agent = self.register()
        saved = self.call('agent_update', id=agent['id'], expected_version=agent['version'],
                          settings_json=json.dumps({'language': 'English'}), enabled=False)['agent']
        self.assertEqual(saved['settings'], {'language': 'English'})
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['source'], agent['source'])
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], saved)

    def test_native_upgrade_replay_restart_preserves_jobs_company_record_and_file(self):
        agent = self.register()
        job, refs = self.job(agent)
        record = self.api('GET', 'records/' + refs[0]['id'])['record']
        attachment_text = '合成附件：升級後須保留相同內容。'
        attachment = self.call('save_text_file', name='upgrade-retention.txt', text=attachment_text)['item']
        context = self.call('agent_job_context', id=job['id'])
        started = self.call('agent_job_action', id=job['id'], expected_version=job['version'], action='start',
                            arguments_json=json.dumps({'source': 'Upgrade transport fixture',
                                                       'context_digest': context['context_digest']}))['job']
        before = self.call('agent_update', id=agent['id'], expected_version=agent['version'],
                           settings_json=json.dumps({'language': 'English'}))['agent']
        arguments = self.upgrade(before)
        upgraded = self.call('agent_update', **arguments)['agent']
        for field in ['id', 'created_at', 'enabled', 'connection_state']:
            self.assertEqual(upgraded[field], before[field])
        self.assertEqual(upgraded['settings'], {'language': 'English', 'concise': True})
        self.assertEqual(upgraded['source']['revision'], 'c' * 40)
        self.assertEqual(upgraded['manifest']['version'], '1.1.0')
        self.assertEqual(upgraded['version'], before['version'] + 1)
        self.assertEqual(self.call('agents_list')['total'], 1)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], upgraded)
        self.assertEqual(self.call('agent_job_get', id=job['id'])['job'], started)
        self.assertEqual(self.call('agent_job_context', id=job['id'])['agent'], context['agent'])
        saved = self.call('agent_job_action', id=job['id'], expected_version=started['version'], action='complete',
                          arguments_json=json.dumps({'result': '合成結果：星期五完成，接著核對交付。',
                                                     'source': 'Upgrade transport fixture', 'source_refs': refs,
                                                     'context_digest': context['context_digest']}))['job']
        self.assertEqual(saved['agent_snapshot'], job['agent_snapshot'])
        current = self.call('agent_update', id=agent['id'], expected_version=upgraded['version'],
                            settings_json=json.dumps({'language': '保留後續設定', 'concise': False}))['agent']
        self.assertEqual(self.call('agent_update', **arguments)['agent'], upgraded)
        self.assertEqual(self.call('agent_update', **{**arguments, 'expected_version': current['version']})['agent'], upgraded)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], current)
        with self.assertRaises(LibraryError) as conflict:
            self.call('agent_update', **{**arguments, 'settings_json': json.dumps({'concise': False})})
        self.assertEqual(conflict.exception.status, 409)
        self.restart()
        self.assertEqual(self.call('agent_update', **arguments)['agent'], upgraded)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], current)
        self.assertEqual(self.call('agent_job_get', id=job['id'])['job'], saved)
        self.assertEqual(self.api('GET', 'records/' + record['id'])['record'], record)
        self.assertEqual(self.call('get_item', id=attachment['id'])['item'], attachment)
        status, content = self.http('GET', '/api/files/' + attachment['id'])
        self.assertEqual(status, 200)
        self.assertEqual(content.decode(), attachment_text)

    def test_http_upgrade_enforces_auth_write_guard_pairing_and_cas(self):
        agent = self.register()
        arguments = self.upgrade(agent)
        payload = {'expected_version': arguments['expected_version'], 'upgrade_key': arguments['upgrade_key'],
                   'manifest': json.loads(arguments['manifest_json']), 'source': json.loads(arguments['source_json'])}
        path = '/api/studio/agents/' + agent['id']
        self.assertEqual(self.http('PATCH', path, payload, authenticated=False)[0], 401)
        self.assertEqual(self.http('PATCH', path, payload, marker=False)[0], 403)
        self.assertEqual(self.http('PATCH', path, payload, origin='https://untrusted.example')[0], 403)
        member_password = secrets.token_urlsafe(24)
        self.api('POST', 'users', {'username': 'test-member', 'password': member_password, 'role': 'member'})
        self.assertEqual(self.http('PATCH', path, payload, username='test-member', password=member_password)[0], 403)
        readonly = Bridge(self.url, read_only=True)
        self.assertNotIn('blueprint_agent_update', {tool['name'] for tool in readonly.tools()})
        with self.assertRaises(LibraryError) as forbidden:
            readonly.call('blueprint_agent_update', arguments)
        self.assertEqual(forbidden.exception.status, 403)
        for field in ['manifest', 'source', 'upgrade_key']:
            with self.subTest(missing=field):
                self.assertEqual(self.http('PATCH', path, {key: value for key, value in payload.items() if key != field})[0], 400)
        for key in ['', '../different', 'x' * 101]:
            with self.subTest(upgrade_key=key):
                self.assertEqual(self.http('PATCH', path, {**payload, 'upgrade_key': key})[0], 400)
        self.assertEqual(self.http('PATCH', path, {**payload, 'enabled': False})[0], 400)
        self.assertEqual(self.http('PATCH', path, {**payload, 'expected_version': agent['version'] - 1})[0], 409)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], agent)
        status, body = self.http('PATCH', path, payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], json.loads(body)['agent'])

    def test_sites_mcp_upgrades_and_replays_same_id_without_replacing_jobs(self):
        agent = self.register()
        job, refs = self.job(agent)
        fixture = subprocess.run(['node', str(ROOT / 'tests/agent_bridge_fixture.mjs')],
            input=json.dumps({'mode': 'upgrade', 'backend': self.url, 'username': 'test-owner',
                             'password': self.password, 'agent_id': agent['id'], 'job_id': job['id'],
                             'upgrade': self.upgrade(agent, 'sites-upgrade-1')}),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(fixture.returncode, 0, fixture.stderr)
        evidence = json.loads(fixture.stdout)
        self.assertEqual(self.call('agent_get', id=agent['id'])['agent'], evidence['agent'])
        self.assertEqual(self.call('agent_job_get', id=job['id'])['job'], job)
        self.assertEqual(evidence['upgraded']['id'], agent['id'])
        self.assertEqual(evidence['upgraded']['source']['revision'], 'c' * 40)
        self.assertEqual(evidence['upgraded']['settings'], {'language': '繁體中文', 'concise': True})
        self.assertEqual(self.call('agents_list')['total'], 1)


if __name__ == '__main__':
    unittest.main()
