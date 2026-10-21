"""Authenticated browser acceptance for the update handoff and preference restore."""
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
from blueprint_folders import __version__
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio


def main():
    from playwright.sync_api import sync_playwright
    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-update-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-update-browser-') as temporary:
        data = Path(temporary) / 'data'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password, company_profile={'name': 'Blueprint Studio'})
        store = Store(data)
        old = store.add_file('original.txt', b'Preserve original attachment')
        Studio(store, {'username': 'owner', 'role': 'owner', 'name': 'owner'}).state()
        original = store.export_metadata()
        server = make_server(data)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = p.chromium.launch(**({'executable_path': executable} if executable else {}), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050}, http_credentials={'username': 'owner', 'password': password})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(origin)
                page.locator('#company-name').filter(has_text='Blueprint Studio').wait_for()
                page.evaluate("localStorage.setItem('blueprint-backend-display-v1', JSON.stringify({layout:'list',sort:'title'})); localStorage.setItem('unrelated-private-token','must-not-export')")
                page.locator('#settings-button').click()
                page.locator('[data-update-open]').click()
                page.wait_for_function("() => !document.getElementById('update-copy').disabled")
                assert origin in page.locator('#update-backend').inner_text()
                assert __version__ in page.locator('#update-backend').inner_text()
                assert page.locator('#update-route').input_value() == 'native'
                assert 'UPDATE_AI.txt' in page.locator('#update-prompt').input_value()
                assert '已在此瀏覽器保留' in page.locator('#update-backup-status').inner_text()
                with page.expect_download() as download_event:
                    page.locator('#update-download').click()
                backup = json.loads(Path(download_event.value.path()).read_text())
                assert backup['origin'] == origin
                assert json.loads(backup['preferences']['blueprint-backend-display-v1'])['sort'] == 'title'
                assert 'must-not-export' not in json.dumps(backup)
                page.screenshot(path=str(output / 'update-desktop.png'), full_page=True)
                page.locator('summary').filter(has_text='還原之前的排版').click()
                bad = {**backup, 'origin': 'https://another.example'}
                page.locator('#update-backup-file').set_input_files({'name': 'wrong.json', 'mimeType': 'application/json', 'buffer': json.dumps(bad).encode()})
                page.locator('#update-restore').click()
                page.locator('#update-error').filter(has_text='同一個工作台').wait_for()
                page.evaluate("localStorage.setItem('blueprint-backend-display-v1', JSON.stringify({layout:'grid',sort:'updated'}))")
                page.locator('#update-backup-file').set_input_files({'name': 'correct.json', 'mimeType': 'application/json', 'buffer': json.dumps(backup).encode()})
                with page.expect_navigation():
                    page.locator('#update-restore').click()
                assert page.evaluate("JSON.parse(localStorage.getItem('blueprint-backend-display-v1')).sort") == 'title'
                assert page.evaluate("localStorage.getItem('unrelated-private-token')") == 'must-not-export'
                page.locator('#settings-button').click(); page.locator('[data-update-open]').click()
                page.wait_for_function("() => !document.getElementById('update-copy').disabled")
                page.set_viewport_size({'width': 390, 'height': 844})
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                page.screenshot(path=str(output / 'update-mobile.png'), full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate("[...document.querySelectorAll('body *')].filter(el => el.getBoundingClientRect().right > innerWidth + 1).map(el => [el.tagName,el.id,el.className,el.getBoundingClientRect().right]).slice(0,20)")
                assert page.locator('#update-dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth')
                page.locator('#update-dialog [data-close]').click()
                page.route('**/api/info', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"temporarily unavailable"}'))
                page.locator('[data-update-open]').click()
                page.locator('#update-error').filter(has_text='未能完整準備').wait_for()
                assert page.locator('#update-copy').is_disabled()
                assert not errors, errors
                browser.close()
            after = store.export_metadata()
            original.pop('exported_at'); after.pop('exported_at')
            assert after == original
            assert store.get_file(old['id'])[0].read_bytes() == b'Preserve original attachment'
            print('Update browser acceptance passed: authenticated handoff, private preferences, same-site restore, mobile, failed reads, unchanged company data.')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__': main()
