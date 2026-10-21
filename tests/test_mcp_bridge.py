"""Real MCP client sessions write/read the same authenticated existing backend."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.mcp_server import Bridge, client_config
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import LibraryError, Store

HAS_MCP = importlib.util.find_spec('mcp') is not None
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAS_MCP, 'Optional MCP SDK: install requirements-mcp.txt')
class MCPBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-mcp-')
        self.data = Path(self.temp.name) / 'existing-company'
        self.password = secrets.token_urlsafe(24)
        initialize_library(self.data, 'existing-owner', self.password, company_profile={'name': 'Same company'})
        self.store = Store(self.data)
        self.server = make_server(self.data)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        self.env = {**os.environ, 'BLUEPRINT_BACKEND_USERNAME': 'existing-owner', 'BLUEPRINT_BACKEND_PASSWORD': self.password}

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()
        self.temp.cleanup()

    async def session(self, callback, *, read_only=False, password=None):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        env = {**self.env}
        if password is not None: env['BLUEPRINT_BACKEND_PASSWORD'] = password
        params = StdioServerParameters(command=sys.executable,
            args=[str(ROOT / 'blueprint.py'), 'mcp', '--url', self.url] + (['--read-only'] if read_only else []), env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                return await callback(client)

    async def call(self, client, name, arguments):
        result = await client.call_tool('blueprint_' + name, arguments)
        self.assertFalse(result.isError, result.content)
        return result.structuredContent or json.loads(result.content[0].text)

    async def test_independent_clients_share_existing_company_and_real_writes(self):
        for source in ['Codex', 'Claude', 'Gemini']:
            async def work(client):
                info = await self.call(client, 'workspace_info', {})
                self.assertEqual(info['connected_backend_url'], self.url)
                self.assertEqual(info['company_profile']['name'], 'Same company')
                result = await self.call(client, 'save_workday', {'date': '2026-10-21', 'source': source,
                    'source_key': source + '/day-2026-10-21', 'entries': [{'type': 'outcome', 'title': source + ' saved this'}]})
                reread = await self.call(client, 'get_workday', {'id': result['workday']['id']})
                self.assertEqual(reread['workday']['source'], source)
            await self.session(work)
        self.assertEqual(self.store.workday_stats()['workdays'], 3)

    async def test_files_collections_bookmarks_and_versioned_updates(self):
        async def work(client):
            collection = (await self.call(client, 'create_collection', {'name': 'Research'}))['collection']
            link = (await self.call(client, 'save_bookmark', {'title': 'Learning', 'url': 'https://www.youtube.com/watch?v=abc123', 'collection_id': collection['id']}))['item']
            updated = (await self.call(client, 'update_item', {'id': link['id'], 'expected_version': link['version'], 'pinned': True}))['item']
            self.assertTrue(updated['pinned'])
            result = await client.call_tool('blueprint_update_item', {'id': link['id'], 'expected_version': link['version'], 'notes': 'stale'})
            self.assertTrue(result.isError)
            item = (await self.call(client, 'save_text_file', {'name': '成果.md', 'text': '保留真正的中文內容。', 'collection_id': collection['id']}))['item']
            reread = (await self.call(client, 'get_item', {'id': item['id']}))['item']
            self.assertEqual(reread['collection_id'], collection['id'])
            filename, _, _ = self.store.get_file(item['id'])
            self.assertEqual(filename.read_text(), '保留真正的中文內容。')
            listed = await self.call(client, 'list_items', {'pinned': True})
            self.assertEqual(listed['items'][0]['id'], link['id'])
        await self.session(work)

    async def test_completed_todos_and_history_survive_ai_revision(self):
        async def work(client):
            report = (await self.call(client, 'save_workday', {'date': '2026-10-21', 'source': 'Another MCP client', 'source_key': 'shared-day', 'entries': [{'type': 'todo', 'title': 'Finish task'}]}))['workday']
            entry = report['entries'][0]
            await self.call(client, 'update_todo', {'id': entry['id'], 'expected_version': entry['version'], 'done': True})
            report = (await self.call(client, 'get_workday', {'id': report['id']}))['workday']
            revised = (await self.call(client, 'revise_workday', {'id': report['id'], 'expected_version': report['version'], 'summary': 'Refined', 'entries': [{'id': entry['id'], 'type': 'todo', 'title': 'Finish task'}]}))['workday']
            self.assertTrue(revised['entries'][0]['done'])
            self.assertEqual(revised['entries'][0]['id'], entry['id'])
            history = await self.call(client, 'workday_history', {'id': report['id']})
            self.assertGreaterEqual(len(history['versions']), 3)
        await self.session(work)

    async def test_read_only_denies_hidden_writes_and_invalid_ids(self):
        async def work(client):
            listed = await client.list_tools()
            self.assertNotIn('blueprint_save_bookmark', [t.name for t in listed.tools])
            for name, data in [('blueprint_save_bookmark', {'title': 'Denied', 'url': 'https://example.com'}),
                               ('blueprint_get_item', {'id': '../access.json'})]:
                result = await client.call_tool(name, data)
                self.assertTrue(result.isError)
            self.assertEqual(self.store.stats()['total'], 0)
        await self.session(work, read_only=True)

    async def test_bad_credentials_never_report_saved_or_expose_secret(self):
        async def work(client):
            result = await client.call_tool('blueprint_workspace_info', {})
            self.assertTrue(result.isError)
            self.assertNotIn('wrong-secret-1234', str(result))
            self.assertNotIn(self.password, str(result))
        await self.session(work, password='wrong-secret-1234')

    def test_config_contains_environment_references_not_values(self):
        with patch.dict(os.environ, self.env):
            for client in ['codex', 'claude', 'gemini']:
                result = client_config(client, self.url)
                self.assertIn('BLUEPRINT_BACKEND_PASSWORD', result)
                self.assertNotIn(self.password, result)
                if client != 'codex': self.assertIn('blueprint', json.loads(result)['mcpServers'])
        with self.assertRaises(LibraryError): Bridge('https://owner:secret@example.com')

    def test_backend_unavailable_and_payload_limits_fail_without_writing(self):
        bridge = Bridge(self.url)
        with patch.dict(os.environ, self.env):
            with self.assertRaises(LibraryError): bridge.call('blueprint_save_text_file', {'name': 'large.txt', 'text': 'x' * 100001})
            with self.assertRaises(LibraryError): bridge.call('blueprint_save_bookmark', {'title': 'URL override', 'url': 'https://example.com', 'backend_url': 'https://elsewhere.example'})
            with patch.dict(os.environ, {'BLUEPRINT_BACKEND_PASSWORD': ''}):
                with self.assertRaises(LibraryError): bridge.call('blueprint_workspace_info', {})
        self.assertEqual(self.store.stats()['total'], 0)
