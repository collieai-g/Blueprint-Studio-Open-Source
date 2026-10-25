"""Optional browser acceptance for the opt-in overview and preserved local layouts."""
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

KEY = 'blueprint-workspace-v2'
BACKUP = 'blueprint-workspace-v2.2-backup'
VISIBILITY_BACKUP = 'blueprint-workspace-v2.7.3-backup'


def main():
    from playwright.sync_api import sync_playwright
    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-layout-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-layout-') as temp:
        data = Path(temp) / 'data'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'layout-test', password, company_profile={'name': 'Blueprint Studio'})
        store = Store(data)
        store.add_file('設計參考.md', '示範資料：每週整理工作。'.encode())
        server = make_server(data)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = p.chromium.launch(**({'executable_path': executable} if executable else {}), headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1100}, http_credentials={'username': 'layout-test', 'password': password})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:' + str(server.server_port))
                def ready():
                    page.wait_for_function("() => BlueprintStudio.isReady() && document.querySelector('#workspace-board').style.height !== ''")
                def saved():
                    return page.evaluate('(key) => localStorage.getItem(key)', KEY)
                def geometry():
                    return page.evaluate('''() => {
                      const board=document.getElementById('workspace-board'),b=board.getBoundingClientRect();
                      return [...board.querySelectorAll('[data-widget]')].filter(n=>!n.hidden).map(n=>{const r=n.getBoundingClientRect();return {id:n.dataset.widget,x:r.x-b.x,y:r.y-b.y,w:r.width,h:r.height};});
                    }''')
                def ids():
                    return [r['id'] for r in geometry()]
                def check():
                    rects = geometry()
                    assert rects and rects[0]['id'] == 'heading'
                    for i, r in enumerate(rects):
                        assert all(abs(r[k] / 24 - round(r[k] / 24)) < .01 for k in ['x', 'y', 'w', 'h']), r
                        for other in rects[i + 1:]:
                            assert not (r['x'] < other['x'] + other['w'] and r['x'] + r['w'] > other['x'] and r['y'] < other['y'] + other['h'] and r['y'] + r['h'] > other['y']), (r, other)
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                def toggle(module, checked):
                    page.locator(f'#board-visible-fields [data-board-visible={module}]').set_checked(checked)
                ready(); check()
                assert ids() == ['heading']
                assert page.locator('[data-widget=heading] h1').inner_text() == '總覽'
                assert page.locator('#board-end').is_hidden()
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 72
                assert page.locator('#workspace-sync').inner_text() == ''
                assert page.locator('#board-editor').is_hidden()
                assert saved() is None
                assert page.locator('[data-widget=connection],[data-widget=intro-title],[data-widget=intro-description]').count() == 0
                page.screenshot(path=str(output / 'layout-desktop-minimal.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844}); page.wait_for_timeout(100)
                check(); assert ids() == ['heading']; assert saved() is None
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 48
                assert page.locator('#board-end').is_hidden()
                page.screenshot(path=str(output / 'layout-mobile-minimal.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1100}); page.wait_for_timeout(100)
                page.locator('#edit-board').click()
                assert page.locator('#board-end').is_visible()
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 48 * 24
                assert page.locator('#board-selected option').evaluate_all('(nodes) => nodes.map(n=>n.value)') == ['heading']
                assert page.locator('[data-board-visible=heading]').is_disabled()
                assert saved() is None  # Opening the editor is not a migration write.
                for module in ['files', 'todos', 'date', 'recent']:
                    toggle(module, True)
                check()
                assert set(ids()) == {'heading', 'files', 'todos', 'date', 'recent'}
                assert json.loads(saved())['layoutVersion'] == 4
                assert page.locator('#board-selected option').count() == 5
                assert page.locator('#board-visible-fields [data-board-visible=connection]').count() == 0
                # Hiding a selected card updates the inspector but preserves its size.
                file_geometry = json.loads(saved())['cards']['files']
                page.locator('#board-selected').select_option('files'); toggle('files', False)
                assert page.locator('#board-selected').input_value() == 'heading'
                assert json.loads(saved())['cards']['files'] == file_geometry
                toggle('files', True)
                for name in ['focus', 'compact', 'balanced']:
                    page.locator('#board-preset').select_option(name); page.locator('#apply-board-preset').click(); check()
                    assert set(json.loads(saved())['visible']) == {'heading', 'files', 'todos', 'date', 'recent'}
                expanded = next(r for r in geometry() if r['id'] == 'files')['h']
                page.locator('[data-widget=files] [data-board-action=collapse]').click()
                assert json.loads(saved())['cards']['files']['h'] == expanded / 24
                page.locator('[data-widget=files] [data-board-action=collapse]').click()
                assert next(r for r in geometry() if r['id'] == 'files')['h'] == expanded
                page.locator('#board-selected').select_option('heading'); page.locator('[data-text-size="2"]').click()
                assert next(r for r in geometry() if r['id'] == 'heading')['w'] == 48
                page.locator('[data-text-size="4"]').click()
                assert next(r for r in geometry() if r['id'] == 'heading')['h'] == 48
                grip = page.locator('[data-widget=heading] [data-board-action=move]')
                before = next(r for r in geometry() if r['id'] == 'heading')
                grip.focus(); grip.press('ArrowRight')
                assert next(r for r in geometry() if r['id'] == 'heading')['x'] == before['x'] + 24
                before_saved = saved(); before = next(r for r in geometry() if r['id'] == 'heading')
                box = grip.bounding_box(); page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down()
                page.mouse.move(box['x'] + 59, box['y'] + 8, steps=4)
                page.keyboard.press('Escape'); page.mouse.up(); assert saved() == before_saved
                box = grip.bounding_box(); page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down()
                page.mouse.move(box['x'] + 59, box['y'] + 8, steps=4); page.mouse.up()
                assert next(r for r in geometry() if r['id'] == 'heading')['x'] == before['x'] + 48
                handle = page.locator('[data-widget=files] [data-board-action=resize]')
                handle.scroll_into_view_if_needed(); box = handle.bounding_box(); before = next(r for r in geometry() if r['id'] == 'files')
                page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down(); page.mouse.move(box['x'] + 32, box['y'] + 32, steps=4); page.mouse.up()
                after = next(r for r in geometry() if r['id'] == 'files')
                assert (after['w'], after['h']) == (before['w'] + 24, before['h'] + 24)
                check()
                # Extra pages only come from an explicit edit-mode action.
                page.locator('#add-board-page').click(); page.locator('#add-board-page').click()
                assert json.loads(saved())['pages'] == 3 and page.locator('#add-board-page').is_disabled()
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 3 * 48 * 24
                page.locator('#board-selected').select_option('date'); page.locator('#board-page').select_option('2')
                assert next(r for r in geometry() if r['id'] == 'date')['y'] >= 96 * 24
                page.locator('#edit-board').click(); assert page.locator('#board-end').is_hidden()
                expected_height = max(r['y'] + r['h'] for r in geometry())
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == expected_height
                before_saved = saved(); page.reload(); ready(); check(); assert saved() == before_saved
                assert next(r for r in geometry() if r['id'] == 'date')['y'] >= 96 * 24
                page.set_viewport_size({'width': 390, 'height': 844}); page.wait_for_timeout(100); check(); assert saved() == before_saved
                page.set_viewport_size({'width': 1440, 'height': 1100}); page.wait_for_timeout(100); assert saved() == before_saved
                page.evaluate('() => BlueprintStudio.navigate("documents")');page.wait_for_selector('#library-view-slot #item-grid .item-card')
                page.evaluate('() => BlueprintStudio.navigate("overview")');page.wait_for_selector('#dashboard-library-slot #item-grid .item-card')
                # Older backends retain local-only migration semantics. An account
                # snapshot on a newer backend is authoritative over browser cache.
                page.evaluate('() => BlueprintStudio.presentation().flush()')
                page.route('**/api/studio/presentation*', lambda route: route.fulfill(
                    status=404, content_type='application/json', body='{"error":"older backend"}'))
                # Exact legacy bytes survive reading, resizing and merely entering edit mode.
                legacy = {'version': 1, 'layoutVersion': 3, 'mode': 'manual', 'pages': 3, 'columns': 43, 'cards': {'heading': {'x': 0, 'y': 0, 'w': 16, 'h': 3}, 'files': {'x': 2, 'y': 14, 'w': 12, 'h': 8, 'collapsed': True}, 'todos': {'x': 0, 'y': 96, 'w': 12, 'h': 20}, 'connection': {'x': 29, 'y': 6, 'w': 14, 'h': 5}}}
                raw = json.dumps(legacy, ensure_ascii=False, indent=2)
                page.evaluate('(v) => {localStorage.setItem(v.key,v.raw);localStorage.setItem(v.oldBackup,"keep-existing-backup");localStorage.removeItem(v.backup)}', {'key': KEY, 'raw': raw, 'oldBackup': BACKUP, 'backup': VISIBILITY_BACKUP})
                page.reload(); ready(); assert saved() == raw; assert ids() == ['heading']
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 72
                page.set_viewport_size({'width': 390, 'height': 844}); page.wait_for_timeout(100); assert saved() == raw
                page.set_viewport_size({'width': 1440, 'height': 1100}); page.wait_for_timeout(100)
                page.locator('#edit-board').click(); assert saved() == raw
                assert page.evaluate('(key) => localStorage.getItem(key)', VISIBILITY_BACKUP) is None
                toggle('files', True)
                assert page.evaluate('(key) => localStorage.getItem(key)', VISIBILITY_BACKUP) == raw
                assert page.evaluate('(key) => localStorage.getItem(key)', BACKUP) == 'keep-existing-backup'
                migrated = json.loads(saved()); assert migrated['mode'] == 'manual' and migrated['pages'] == 3
                assert migrated['cards']['files'] == {**legacy['cards']['files']}
                assert migrated['cards']['todos'] == {**legacy['cards']['todos'], 'collapsed': False}
                assert 'connection' not in migrated['cards'] and 'connection' not in migrated['visible']
                assert not errors, errors
                browser.close()
            assert store.stats()['files'] == 1
        finally:
            server.shutdown(); server.server_close()
    print('Overview layout browser checks passed: minimal defaults, opt-in modules, visibility, geometry, pointers, persistence, exact migration backup, mobile and retained data.')


if __name__ == '__main__':
    main()
