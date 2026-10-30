"""Synthetic authenticated browser integration; supervisor execution has separate tests."""
import copy
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.server import initialize_library, make_server


def main():
    from playwright.sync_api import sync_playwright, expect
    with tempfile.TemporaryDirectory(prefix='blueprint-installer-browser-') as temporary:
        base = Path(temporary)
        data, installation = base / 'data', base / 'program'
        installation.mkdir()
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password)
        (installation / 'installation.json').write_text(json.dumps({'data_dir': str(data)}), encoding='utf-8')
        server = make_server(data)
        server.installer_root = str(installation)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        status = {'supported': True, 'version': 7, 'phase': 'ready', 'can_apply': True,
                  'current': {'version': '2.10.0'}, 'prepared': {'release_id': 'a' * 64,
                    'version': '2.10.1', 'notes': '<script>synthetic text</script>'}}
        applied = []

        def apply(root, release, version):
            assert root == installation and release == 'a' * 64 and version == 7
            applied.append(release)
            status.update(phase='apply_requested', version=8, can_apply=False)
            return copy.deepcopy(status)

        try:
            with patch('blueprint_library.installer.read_status', side_effect=lambda root: copy.deepcopy(status)), \
                 patch('blueprint_library.installer.request_apply', side_effect=apply), sync_playwright() as p:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER')
                browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
                context = browser.new_context(http_credentials={'username': 'owner', 'password': password},
                                              viewport={'width': 1440, 'height': 1000})
                page = context.new_page()
                page_errors = []
                page.on('pageerror', lambda error: page_errors.append(str(error)))
                origin = f'http://127.0.0.1:{server.server_port}'
                page.goto(origin)
                expect(page.locator('#installer-entry')).to_be_visible()
                page.locator('#installer-entry').click()
                expect(page.locator('#installer-notes')).to_have_text('<script>synthetic text</script>')
                expect(page.locator('#installer-apply')).to_be_enabled()
                page.locator('#installer-close').click()
                # Exercise a real existing form without submitting any company write.
                page.evaluate("document.getElementById('collection-dialog').showModal()")
                page.locator('#collection-name').fill('synthetic unsaved draft')
                page.evaluate("document.getElementById('collection-dialog').close()")
                page.locator('#installer-entry').click()
                expect(page.locator('#installer-apply')).to_be_disabled()
                assert not applied
                page.reload()  # Test deliberately discards only its own synthetic form.
                expect(page.locator('#installer-entry')).to_be_visible()
                page.locator('#installer-entry').click()
                expect(page.locator('#installer-apply')).to_be_enabled()
                page.locator('#installer-apply').click()
                page.wait_for_function("() => document.getElementById('installer-status').textContent.includes('等待')")
                expect(page.locator('#installer-apply')).to_be_disabled()
                assert applied == ['a' * 64]
                assert not page_errors, page_errors
                context.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
    print('Managed installer authenticated UI, draft protection and single apply: passed (synthetic).')


if __name__ == '__main__':
    main()
