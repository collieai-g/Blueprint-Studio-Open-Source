"""Optional V2.3 acceptance against the real UI and an external temporary library."""
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
                page.wait_for_function("() => document.querySelector('#workspace-board').style.height !== ''")
                page.wait_for_selector('#item-grid .item-card')
                def saved():
                    return page.evaluate("() => localStorage.getItem('blueprint-workspace-v2')")
                def geometry():
                    return page.evaluate('''() => {
                      const board=document.getElementById('workspace-board'),b=board.getBoundingClientRect();
                      return [...board.querySelectorAll('[data-widget]')].map(n=>{const r=n.getBoundingClientRect();return {id:n.dataset.widget,x:r.x-b.x,y:r.y-b.y,w:r.width,h:r.height};});
                    }''')
                def check():
                    rects = geometry()
                    assert len(rects) == 13
                    for i, r in enumerate(rects):
                        assert all(abs(r[k] / 24 - round(r[k] / 24)) < .01 for k in ['x', 'y', 'w', 'h']), r
                        for other in rects[i + 1:]:
                            assert not (r['x'] < other['x'] + other['w'] and r['x'] + r['w'] > other['x'] and r['y'] < other['y'] + other['h'] and r['y'] + r['h'] > other['y']), (r, other)
                    assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                check()
                assert saved() is None  # Viewing/reflowing never overwrites desktop preferences.
                assert page.locator('#board-editor').is_hidden()
                assert page.locator('.workspace-text .widget-grip').first.is_hidden()
                assert page.locator('#view-heading').is_hidden()
                assert page.locator('.workspace-text').first.evaluate("n => {const s=getComputedStyle(n);return s.backgroundColor==='rgba(0, 0, 0, 0)' && s.borderWidth==='0px' && s.boxShadow==='none'}")
                page.wait_for_function("() => document.querySelector('#workspace-sync').dataset.state === 'ready'")
                page.screenshot(path=str(output / 'layout-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844}); page.wait_for_timeout(200); check(); assert saved() is None
                page.screenshot(path=str(output / 'layout-mobile.png'), full_page=True)
                page.set_viewport_size({'width': 1440, 'height': 1100}); page.wait_for_timeout(200)
                page.locator('#edit-board').click()
                for name in ['focus', 'compact', 'balanced']:
                    page.locator('#board-preset').select_option(name); page.locator('#apply-board-preset').click(); check()
                expanded = next(r for r in geometry() if r['id'] == 'files')['h']
                page.locator('[data-widget=files] [data-board-action=collapse]').click()
                assert json.loads(saved())['cards']['files']['h'] == expanded / 24
                page.locator('[data-widget=files] [data-board-action=collapse]').click()
                assert next(r for r in geometry() if r['id'] == 'files')['h'] == expanded
                page.locator('#board-selected').select_option('heading')
                page.locator('[data-text-size="2"]').click()
                assert next(r for r in geometry() if r['id'] == 'heading')['w'] == 48
                assert next(r for r in geometry() if r['id'] == 'heading')['h'] == 24
                page.locator('[data-text-size="4"]').click()
                assert next(r for r in geometry() if r['id'] == 'heading')['h'] == 48
                # Keyboard works even for transparent two/four-cell blocks.
                grip = page.locator('[data-widget=heading] [data-board-action=move]')
                before = next(r for r in geometry() if r['id'] == 'heading')
                grip.focus(); grip.press('ArrowRight')
                after = next(r for r in geometry() if r['id'] == 'heading')
                assert after['x'] == before['x'] + 24
                # Pointer movement snaps; Escape cancels without changing saved preferences.
                before_saved = saved(); before = next(r for r in geometry() if r['id'] == 'heading')
                box = grip.bounding_box(); page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down()
                page.mouse.move(box['x'] + 59, box['y'] + 8, steps=4)
                page.keyboard.press('Escape'); page.mouse.up()
                assert saved() == before_saved
                box = grip.bounding_box(); page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down()
                page.mouse.move(box['x'] + 59, box['y'] + 8, steps=4); page.mouse.up()
                assert next(r for r in geometry() if r['id'] == 'heading')['x'] == before['x'] + 48
                # Resize content with a real pointer, then verify exact grid edges.
                handle = page.locator('[data-widget=files] [data-board-action=resize]')
                handle.scroll_into_view_if_needed(); box = handle.bounding_box()
                before = next(r for r in geometry() if r['id'] == 'files')
                page.mouse.move(box['x'] + 8, box['y'] + 8); page.mouse.down()
                page.mouse.move(box['x'] + 32, box['y'] + 32, steps=4); page.mouse.up()
                after = next(r for r in geometry() if r['id'] == 'files')
                assert (after['w'], after['h']) == (before['w'] + 24, before['h'] + 24)
                check()
                # Hover only reveals the invitation. A page needs an explicit click.
                page.locator('#board-end').hover()
                assert json.loads(saved())['pages'] == 1
                page.locator('#add-board-page').click(); page.locator('#board-end').hover(); page.locator('#add-board-page').click()
                assert json.loads(saved())['pages'] == 3
                assert page.locator('#add-board-page').is_disabled()
                assert page.locator('#workspace-board').evaluate('n => n.clientHeight') == 3 * 48 * 24
                page.locator('#board-selected').select_option('date'); page.locator('#board-page').select_option('2')
                assert next(r for r in geometry() if r['id'] == 'date')['y'] >= 96 * 24
                before_saved = saved(); page.reload(); page.wait_for_function("() => document.querySelector('#workspace-board').style.height !== ''")
                assert saved() == before_saved; check()
                assert next(r for r in geometry() if r['id'] == 'date')['y'] >= 96 * 24
                # Moving the recent library into the board must not break other views.
                page.locator('.main-nav [data-view=files]').click(); page.wait_for_selector('#library-view-slot #item-grid .item-card')
                assert page.locator('#view-heading').is_visible(); assert page.locator('#dashboard-panel').is_hidden()
                page.locator('.main-nav [data-view=dashboard]').click(); page.wait_for_selector('#dashboard-library-slot #item-grid .item-card')
                assert page.locator('#view-heading').is_hidden()
                # Narrow view is temporary; desktop preferences and data remain unchanged.
                page.set_viewport_size({'width': 390, 'height': 844}); page.wait_for_timeout(200); check(); assert saved() == before_saved
                page.set_viewport_size({'width': 1440, 'height': 1100}); page.wait_for_timeout(200); assert saved() == before_saved
                # Legacy layout is backed up on the first edit, never on initial load.
                legacy = {'version': 1, 'mode': 'manual', 'cards': {'files': {'x': 0, 'y': 0, 'w': 12, 'h': 8, 'collapsed': True}}}
                page.evaluate("v => {localStorage.setItem('blueprint-workspace-v2',JSON.stringify(v));localStorage.removeItem('blueprint-workspace-v2.2-backup')}", legacy)
                page.reload(); page.wait_for_function("() => document.querySelector('#workspace-board').style.height !== ''")
                assert json.loads(saved()) == legacy
                page.locator('#edit-board').click(); page.locator('#board-selected').select_option('date'); page.locator('[data-text-size="2"]').click()
                assert page.evaluate("() => JSON.parse(localStorage.getItem('blueprint-workspace-v2.2-backup'))") == legacy
                assert json.loads(saved())['cards']['files']['collapsed']
                assert not errors, errors
                browser.close()
            assert store.stats()['files'] == 1
        finally:
            server.shutdown(); server.server_close()
    print('V2.3 browser checks passed: transparent text, grid, pointers, keyboard, pages, persistence, migration, navigation, mobile, data preservation.')


if __name__ == '__main__':
    main()
