"""Client-led acceptance with real MCP stdio and normal workday UI readback.

All data, credentials and resumable parameters belong to an isolated fixture;
this test never controls the user's tab or connects to a production workspace.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store


def main():
    from playwright.sync_api import sync_playwright
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-client-connection-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-client-acceptance-') as temp:
        data = Path(temp) / 'data'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'test-owner', password, company_profile={'name': 'Blueprint Studio', 'industry': '創作與日常工作'})
        store = Store(data)
        store.add_file('保留的文件.md', '原有工作資料'.encode())
        old = store.import_workday({'date': '2026-10-20', 'source': 'Owner', 'source_key': 'keep-existing-work',
            'title': '保留的工作', 'entries': [{'type': 'todo', 'title': '原有未完成工作'}]})['workday']
        original = store.get_workday(old['id'])
        server = make_server(data)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        env = {**os.environ, 'BLUEPRINT_BACKEND_USERNAME': 'test-owner', 'BLUEPRINT_BACKEND_PASSWORD': password}

        async def call(name, arguments, expect_error=False):
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'blueprint.py'), 'mcp', '--url', origin], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert 'blueprint_connection_guide' in initialized.instructions
                    assert len((await client.list_tools()).tools) == 42
                    result = await client.call_tool(name, arguments)
                    assert bool(result.isError) == expect_error, result.content
                    return result.structuredContent or json.loads(result.content[0].text)

        def mcp_call(name, arguments, expect_error=False):
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, call(name, arguments, expect_error)).result(timeout=30)

        try:
            with sync_playwright() as playwright:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = playwright.chromium.launch(**({'executable_path': executable} if executable else {}),
                    headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1100},
                    http_credentials={'username': 'test-owner', 'password': password})
                page = context.new_page()
                errors, loaded_scripts = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: loaded_scripts.append(request.url) if request.resource_type == 'script' else None)
                page.goto(origin)
                page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('#connection-dialog, [data-connection-open], #settings-ai-connection').count() == 0
                assert page.locator('script[src="/connection.js"]').count() == 0
                assert not any(url.endswith('/connection.js') for url in loaded_scripts)
                assert page.evaluate('typeof window.BlueprintConnection') == 'undefined'

                # The active client discovers the authenticated built-in entry,
                # current company and machine guide; no right-side wizard runs.
                for path, filename in [('/AI_CONNECT.md', 'AI_CONNECT.md'), ('/connection-guide.json', 'library_web/connection-guide.json')]:
                    response = context.request.get(origin + path)
                    assert response.status == 200
                    assert response.body() == (ROOT / filename).read_bytes()
                    anonymous = playwright.request.new_context()
                    try:
                        assert anonymous.get(origin + path).status == 401
                    finally:
                        anonymous.dispose()
                info = mcp_call('blueprint_workspace_info', {})
                assert info['connected_backend_url'] == origin
                assert info['company_profile']['name'] == 'Blueprint Studio'
                guide = mcp_call('blueprint_connection_guide', {})
                assert guide['entrypoint'] == 'AI_CONNECT.md'
                assert guide['setup_owner'] == 'active_ai_client'

                # The test itself explicitly authorizes its own labelled probe.
                # Generate one cryptographic nonce in this client, save it in
                # private temporary progress, and resume those exact parameters.
                arguments = {'challenge': secrets.token_hex(16), 'client': 'Blueprint MCP integration test', 'date': date.today().isoformat()}
                progress = Path(temp) / 'client-acceptance.json'
                progress.write_text(json.dumps({'origin': origin, 'company': 'Blueprint Studio', 'parameters': arguments}))
                for change in [{'challenge': '../data'}, {'date': '2026-02-30'}, {'client': ' '}]:
                    mcp_call('blueprint_verify_connection', {**arguments, **change}, expect_error=True)
                assert store.workday_stats()['workdays'] == 1
                receipt = mcp_call('blueprint_verify_connection', arguments)
                assert receipt['verified'] is True
                assert '接駁中心' not in receipt['next_step']
                resumed = mcp_call('blueprint_verify_connection', json.loads(progress.read_text())['parameters'])
                assert resumed['workday_id'] == receipt['workday_id']
                assert store.workday_stats()['workdays'] == 2

                # Independent tool calls verify both the same workday and its
                # completed todo rather than trusting the verify tool response.
                report = mcp_call('blueprint_get_workday', {'id': receipt['workday_id']})['workday']
                assert report['source_key'] == 'blueprint-connect:' + arguments['challenge']
                assert report['source'] == arguments['client'] and report['date'] == arguments['date']
                assert report['summary'] == 'blueprint-connection/1:verified'
                assert len(report['entries']) == 2
                assert all(entry['content'] == arguments['challenge'] for entry in report['entries'])
                todo = report['entries'][1]
                todos = mcp_call('blueprint_list_todos', {'q': arguments['challenge'], 'done': 'all'})['todos']
                matched = [entry for entry in todos if entry['id'] == todo['id'] and entry['workday_id'] == report['id']]
                assert len(matched) == 1 and matched[0]['done'] is True
                assert store.get_workday(old['id']) == original

                # The user-visible proof is the normal Work/Workdays view and
                # the actual detail GET for the returned ID, not a status panel.
                page.locator('#studio-navigation [data-group=work]').click()
                page.locator('#studio-section-navigation [data-studio-route="overview/workdays"]').click()
                title = page.locator('#work-list .work-card').filter(has_text=arguments['client']).locator('.work-title-button')
                with page.expect_response(lambda response: response.url == origin + '/api/workdays/' + report['id'] and response.request.method == 'GET') as detail:
                    title.click()
                assert detail.value.json()['workday']['id'] == receipt['workday_id']
                page.wait_for_function('() => document.getElementById("work-report-dialog").open')
                assert page.locator('#work-report-title').inner_text() == 'AI 接駁測試'
                assert arguments['client'] in page.locator('#work-report-meta').inner_text()
                assert arguments['challenge'] in page.locator('#work-report-entries').inner_text()
                assert page.locator('#work-report-entries .work-todo input[type=checkbox]').is_checked()
                page.screenshot(path=str(output / 'connection-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('#work-report-dialog').evaluate('(dialog) => dialog.scrollWidth <= dialog.clientWidth')
                page.screenshot(path=str(output / 'connection-mobile.png'), full_page=True)
                page.locator('#work-report-dialog [data-close]').click()
                page.set_viewport_size({'width': 1440, 'height': 1100})

                # Normal externally saved work waits for explicit refresh.
                page.locator('#studio-navigation [data-group=inspiration]').click()
                mcp_call('blueprint_save_text_file', {'name': 'AI 保存驗證.txt', 'text': '由真實 MCP 保存的工作內容。'})
                assert page.get_by_text('AI 保存驗證.txt', exact=True).count() == 0
                page.locator('#add-item').click()
                page.locator('#refresh-workspace').click()
                page.get_by_text('AI 保存驗證.txt', exact=True).wait_for()
                assert store.stats()['files'] == 2

                # An edited receipt remains untouched when a later retry fails.
                current = store.get_workday(report['id'])
                store.update_workday(current['id'], {'expected_version': current['version'], 'summary': '使用者保留的修改',
                    'entries': [{key: entry[key] for key in ('id', 'type', 'title', 'content')} for entry in current['entries']]})
                mcp_call('blueprint_verify_connection', arguments, expect_error=True)
                assert store.get_workday(report['id'])['summary'] == '使用者保留的修改'
                assert store.workday_stats()['workdays'] == 2
                page.reload()
                page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('#connection-dialog, [data-connection-open]').count() == 0
                assert not errors, errors
                browser.close()
                print(json.dumps({'checks': ['no browser wizard or setup script', 'authenticated canonical entry and guide',
                    'client-generated nonce and private resume', 'actual MCP verification and retry idempotence',
                    'independent workday and completed-todo readback', 'normal matching workday UI',
                    'desktop/mobile layout', 'real file write with manual refresh', 'invalid-input and edited-record rejection',
                    'old work preserved'], 'browser_errors': errors, 'screenshots': str(output)}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
