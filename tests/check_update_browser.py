"""Authenticated browser acceptance for the update handoff and preference restore."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders import __version__
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio
from blueprint_library import presentation


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
        studio = Studio(store, {'username': 'owner', 'role': 'owner', 'name': 'owner'})
        studio.state()
        original_preferences = studio.preferences()
        original = store.export_metadata()
        server = make_server(data, managed=True)
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
                assert '只在排版遷移' in page.locator('#update-backup-status').inner_text()
                assert page.evaluate("localStorage.getItem('blueprint-update-browser-backup-v1')") is None
                assert not page.evaluate("Object.keys(localStorage).some(key => key.startsWith('blueprint-update-browser-backup-v1:'))")
                page.locator('#native-backup-status').filter(has_text='還原測試').wait_for()
                initial_backup = page.locator('#native-backup-download').get_attribute('href')
                # A current platform snapshot needs a private-copy check, not another ZIP.
                assert page.locator('#native-backup-create').is_hidden()
                with page.expect_download() as complete_backup:
                    page.locator('#native-backup-download').click()
                archive_path = Path(complete_backup.value.path())
                with zipfile.ZipFile(archive_path) as archive:
                    assert 'data/access.json' in archive.namelist()
                    assert 'data/library.sqlite3' in archive.namelist()
                    assert any(archive.read(name) == b'Preserve original attachment' for name in archive.namelist() if name.startswith('data/blobs/'))
                page.locator('#native-backup-verify-details summary').click()
                page.locator('#native-backup-file').set_input_files(str(archive_path))
                page.locator('#native-backup-reference').fill('test-private-download/original.zip')
                page.locator('#native-backup-verify').click()
                page.locator('#native-backup-status').filter(has_text='可重用').wait_for()
                assert page.locator('#native-backup-download').get_attribute('href') == initial_backup
                page.get_by_text('瀏覽器排版接續檔', exact=True).click()
                with page.expect_download() as download_event:
                    page.locator('#update-download').click()
                backup = json.loads(Path(download_event.value.path()).read_text())
                assert backup['origin'] == origin
                assert json.loads(backup['preferences']['blueprint-backend-display-v1'])['sort'] == 'title'
                assert 'must-not-export' not in json.dumps(backup)
                page.locator('#update-layout-file').set_input_files(str(download_event.value.path()))
                page.locator('#update-layout-verify').click()
                page.locator('#update-backup-status').filter(has_text='已有相同排版雜湊').wait_for()
                page.screenshot(path=str(output / 'update-desktop.png'), full_page=True)
                page.locator('#update-dialog [data-close]').click()
                # Instructions become ready before the separate current-data
                # verification. Wait for its response before checking reuse.
                with page.expect_response(origin + '/api/maintenance') as current_protection:
                    page.locator('[data-update-open]').click()
                    page.wait_for_function("() => !document.getElementById('update-copy').disabled")
                assert current_protection.value.json()['protection']['reusable'] is True
                assert '已有相同排版雜湊' in page.locator('#update-backup-status').inner_text()
                page.locator('#native-backup-status').filter(has_text='可重用').wait_for()
                assert page.locator('#native-backup-download').get_attribute('href') == initial_backup
                page.locator('summary').filter(has_text='還原之前的排版').click()
                bad = {**backup, 'origin': 'https://another.example'}
                page.locator('#update-backup-file').set_input_files({'name': 'wrong.json', 'mimeType': 'application/json', 'buffer': json.dumps(bad).encode()})
                page.locator('#update-restore').click()
                page.locator('#update-error').filter(has_text='同一個工作台').wait_for()
                # A later cloud edit must not silently replace the chosen backup
                # after the restore reloads the page.
                current_documents = presentation.read(studio)['documents']
                for key, content in {
                    'overview': {'version': 1, 'layoutVersion': 4, 'texts': {'heading': 'NEWER-CLOUD-TITLE'}},
                    'page:projects': {'cards': {'title': {'text': 'NEWER-CLOUD-PROJECTS'}}},
                    'pins': {'version': 1, 'refs': [{'kind': 'route', 'route': 'agents'}]},
                }.items():
                    presentation.save(studio, {'key': key, 'expected_version': current_documents.get(key, {}).get('version', 0), 'data': content})
                page.evaluate("localStorage.setItem('blueprint-backend-display-v1', JSON.stringify({layout:'grid',sort:'updated'}))")
                page.locator('#update-backup-file').set_input_files({'name': 'correct.json', 'mimeType': 'application/json', 'buffer': json.dumps(backup).encode()})
                with page.expect_navigation():
                    page.locator('#update-restore').click()
                assert page.evaluate("JSON.parse(localStorage.getItem('blueprint-backend-display-v1')).sort") == 'title'
                assert page.evaluate("localStorage.getItem('unrelated-private-token')") == 'must-not-export'
                restored = presentation.read(studio)['documents']
                assert restored['overview']['data'] == {}, restored
                assert restored['page:projects']['data'] == {}, restored
                assert restored['pins']['data'] == {'version': 1, 'refs': []}, restored
                page.locator('#settings-button').click(); page.locator('[data-update-open]').click()
                page.wait_for_function("() => !document.getElementById('update-copy').disabled")
                assert page.locator('#native-backup-download').get_attribute('href') == initial_backup
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
            # Only presentation documents changed. The regular account settings,
            # company records, history and attachments must retain their values.
            assert studio.preferences() == original_preferences
            assert all(row['username'] == 'owner' for row in after['studio'].pop('preferences'))
            assert all(row['username'] == 'owner' for row in original['studio'].pop('preferences'))
            assert after == original
            assert store.get_file(old['id'])[0].read_bytes() == b'Preserve original attachment'
            print('Update browser acceptance passed: authenticated handoff, private preferences, same-site restore, mobile, failed reads, unchanged company data.')
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__': main()
