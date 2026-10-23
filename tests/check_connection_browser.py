"""Optional real-browser acceptance; uses temporary data and actual MCP stdio."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
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
    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-connection-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-v22-browser-') as temp:
        data = Path(temp) / 'data'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'test-owner', password, company_profile={'name': 'Blueprint Studio', 'industry': '創作與日常工作'})
        store = Store(data)
        store.add_file('保留的文件.md', '原有工作資料'.encode())
        server = make_server(data)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = 'http://127.0.0.1:' + str(server.server_port)
        env = {**os.environ, 'BLUEPRINT_BACKEND_USERNAME': 'test-owner', 'BLUEPRINT_BACKEND_PASSWORD': password}
        async def call(name, args):
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'blueprint.py'), 'mcp', '--url', origin], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    initialized = await client.initialize()
                    assert 'blueprint_connection_guide' in initialized.instructions
                    assert len((await client.list_tools()).tools) == 42
                    result = await client.call_tool(name, args)
                    assert not result.isError, result.content
                    return result.structuredContent or json.loads(result.content[0].text)
        def mcp_call(name, arguments):
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, call(name, arguments)).result(timeout=30)
        try:
            with sync_playwright() as p:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = p.chromium.launch(**({'executable_path': executable} if executable else {}), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                ctx = browser.new_context(viewport={'width': 1440, 'height': 1100}, http_credentials={'username': 'test-owner', 'password': password})
                page = ctx.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                page.locator('.connection-banner [data-connection-open]').click()
                page.wait_for_function("() => !document.getElementById('connection-copy').disabled")
                assert page.locator('#connection-route').input_value() == 'native'
                instruction = page.locator('#connection-prompt').input_value()
                args = next(json.loads(line) for line in instruction.splitlines() if line.startswith('{"challenge"'))
                assert origin in instruction
                assert page.locator('#connection-progress .complete').count() == 1
                page.screenshot(path=str(output / 'connection-desktop.png'), full_page=True)
                receipt = mcp_call('blueprint_verify_connection', args)
                page.wait_for_function("() => document.querySelectorAll('#connection-progress .complete').length === 4", timeout=12000)
                assert receipt['workday_id'] in page.locator('#connection-evidence').inner_text()
                assert store.workday_stats()['workdays'] == 1
                # Changing client never inherits another client's success.
                page.locator('#connection-client').select_option('claude')
                assert page.locator('#connection-progress .complete').count() == 1
                assert 'Claude Code' in page.locator('#connection-prompt').input_value()
                page.locator('#connection-route').select_option('sites')
                assert 'oauth_resource' in page.locator('#connection-prompt').input_value()
                page.locator('#connection-problem').select_option('network')
                assert '平台外掛' in page.locator('#connection-help-text').inner_text()
                assert '127.0.0.1' in page.locator('#connection-help-text').inner_text()
                page.locator('#connection-client').select_option('codex')
                page.locator('#connection-route').select_option('native')
                page.locator('#connection-check').click()
                page.wait_for_function("() => document.querySelectorAll('#connection-progress .complete').length === 4")
                # Failed reads invalidate the current displayed success.
                page.route('**/api/workdays?*', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"test unavailable"}'))
                page.locator('#connection-check').click()
                page.locator('#connection-error:not([hidden])').wait_for()
                assert page.locator('#connection-progress .complete').count() == 0
                page.unroute('**/api/workdays?*')
                page.locator('#connection-check').click()
                page.wait_for_function("() => document.querySelectorAll('#connection-progress .complete').length === 4")
                page.locator('#connection-dialog [data-close]').click()
                page.reload()
                page.locator('.connection-banner [data-connection-open]').click()
                page.wait_for_function("() => document.querySelectorAll('#connection-progress .complete').length === 4")
                assert receipt['workday_id'] in page.locator('#connection-evidence').inner_text()
                # Keyboard/mobile presentation remains usable without horizontal overflow.
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_timeout(200)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator('#connection-dialog').evaluate('(d) => d.scrollWidth <= d.clientWidth')
                page.screenshot(path=str(output / 'connection-mobile.png'), full_page=True)
                page.locator('#connection-dialog [data-close]').click()
                mcp_call('blueprint_save_text_file', {'name': 'AI 保存驗證.txt', 'text': '由真實 MCP 保存的工作內容。'})
                assert page.get_by_text('AI 保存驗證.txt', exact=True).count() == 0
                page.locator('#refresh-workspace').click()
                page.get_by_text('AI 保存驗證.txt', exact=True).wait_for()
                assert store.stats()['files'] == 2
                # Incomplete deployments must not leave an old green completion state.
                page.locator('.connection-banner [data-connection-open]').click()
                page.wait_for_function("() => document.querySelectorAll('#connection-progress .complete').length === 4")
                page.locator('#connection-dialog [data-close]').click()
                page.route('**/connection-guide.json', lambda route: route.fulfill(status=404, content_type='application/json', body='{"error":"missing asset"}'))
                page.locator('.connection-banner [data-connection-open]').click()
                page.locator('#connection-error:not([hidden])').wait_for()
                assert page.locator('#connection-copy').is_disabled()
                assert page.locator('#connection-progress .complete').count() == 0
                assert not errors, errors
                browser.close()
                print(json.dumps({'checks': ['client-specific instructions', 'actual MCP write/readback/todo update',
                    'right-side matching receipt', 'client isolation', 'Sites routing', 'network guidance',
                    'failure and recovery', 'reload persistence', 'mobile layout', 'real file write manual refresh'],
                    'browser_errors': errors, 'screenshots': str(output)}, ensure_ascii=False))
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    main()
