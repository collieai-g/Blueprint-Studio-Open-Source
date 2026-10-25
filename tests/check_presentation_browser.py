"""Account presentation synchronization in independent browsers and a real backend."""
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
from blueprint_library.studio import Studio


def main():
    from playwright.sync_api import sync_playwright

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-presentation-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-presentation-browser-') as temp:
        directory = Path(temp) / 'private'
        password = secrets.token_urlsafe(24)
        initialize_library(directory, 'owner', password, company_profile={'name': '版面同步隔離驗收'})
        store = Store(directory)
        studio = Studio(store)
        project = studio.create({'kind': 'project', 'title': 'SYNC-PROJECT',
            'data': {'summary': 'CURRENT-AUTHORIZED-CONTENT'}})['record']
        report = studio.create({'kind': 'report', 'title': 'SYNC-MEMBER-REPORT',
            'data': {'content': 'MEMBER-CURRENT-CONTENT', 'source': '隔離驗收'}})['record']
        restricted = studio.create({'kind': 'project', 'title': 'OWNER-ONLY-SOURCE',
            'visibility': 'admin', 'data': {}})['record']
        studio.save_user({'username': 'staff', 'role': 'member', 'password': 'presentation-test-password'})
        server = make_server(directory)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = playwright.chromium.launch(headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'],
                    **({'executable_path': executable} if executable else {}))
                contexts = [browser.new_context(viewport={'width': 1440, 'height': 1050},
                    http_credentials={'username': username, 'password': secret})
                    for username, secret in [('owner', password), ('owner', password),
                                             ('staff', 'presentation-test-password')]]
                first, second, member = [context.new_page() for context in contexts]
                for page in [first, second, member]:
                    page.set_default_timeout(6000)
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base)
                    page.wait_for_function('() => BlueprintStudio.isReady()')

                def go(page, route):
                    page.evaluate('(route) => BlueprintStudio.navigate(route)', route)
                    page.locator('#page-layout-tools button').first.wait_for(state='visible')

                def document(key, context=contexts[0]):
                    response = context.request.get(base + '/api/studio/presentation')
                    assert response.ok, response.text()
                    return response.json()['documents'].get(key)

                def write(key, data, context=contexts[0]):
                    previous = document(key, context)
                    response = context.request.patch(base + '/api/studio/presentation',
                        headers={'X-Blueprint-Request': '1'},
                        data={'key': key, 'expected_version': previous['version'] if previous else 0,
                              'data': data})
                    assert response.ok, response.text()
                    result = response.json()['documents'][key]
                    assert document(key, context) == result
                    return result

                def received(page, key, path, value):
                    page.wait_for_function('''({key,path,value}) => {
                        let data=BlueprintStudio.presentation().get(key);
                        for(const part of path)data=data?.[part];
                        return JSON.stringify(data)===JSON.stringify(value);
                    }''', arg={'key': key, 'path': path, 'value': value})

                def heading(page, value):
                    page.wait_for_function('(value) => document.querySelector("[data-widget=heading] h1").textContent===value', arg=value)

                def synced(page):
                    page.wait_for_function('() => BlueprintStudio.presentation().status().state==="synced"')

                def overview_edit(page, title, size=None):
                    page.locator('#edit-board').click()
                    if not page.locator('#board-advanced').evaluate('node => node.open'):
                        page.locator('#board-advanced > summary').click()
                    page.locator('#board-selected').select_option('heading')
                    page.locator('#board-text-content').fill(title)
                    page.locator('#apply-board-text').click()
                    if size:
                        page.locator('#board-width').fill(str(size[0]))
                        page.locator('#board-height').fill(str(size[1]))
                        page.locator('#apply-board-size').click()
                    page.locator('#edit-board').click()

                def page_editor(page, route):
                    page.locator(f'[data-page-layout-edit="{route}"]').click()
                    editor = page.locator('.page-layout-editor:visible')
                    if not editor.locator('.page-layout-advanced').evaluate('node => node.open'):
                        editor.locator('.page-layout-advanced > summary').click()
                    return editor

                def page_heading(page, value):
                    page.wait_for_function('(value) => document.querySelector("#studio-panel h1")?.textContent===value', arg=value)

                def refresh(page):
                    page.locator('#add-item').click()
                    page.locator('#refresh-workspace').click()

                # The contexts have independent browser storage; only the server shares preferences.
                first.evaluate('localStorage.setItem("presentation-isolation-probe","first-only")')
                assert second.evaluate('localStorage.getItem("presentation-isolation-probe")') is None
                assert member.evaluate('localStorage.getItem("presentation-isolation-probe")') is None
                overview_edit(first, '跨裝置同步總覽', (14, 3))
                synced(first)
                heading(second, '跨裝置同步總覽')
                second.wait_for_function('''() => {
                    const style=document.querySelector('[data-widget=heading]').style;
                    return style.gridColumn.endsWith('span 14')&&style.gridRow.endsWith('span 3');
                }''')
                heading(member, '總覽')
                assert document('overview', contexts[2]) is None

                # Idle synchronization only reads presentation metadata, never company lists.
                traffic = []
                second.on('request', lambda request: traffic.append(request.url)
                          if request.method == 'GET' and '/api/' in request.url else None)
                second.wait_for_function('''() => {
                    const sync=BlueprintStudio.presentation();
                    return sync.status().state==='synced';
                }''')
                traffic.clear()
                second.wait_for_timeout(4300)
                assert sum('/api/studio/presentation' in url for url in traffic) >= 2, traffic
                assert all('/api/studio/presentation' in url for url in traffic), traffic

                # The same authenticated endpoint used by AI tools updates both open pages.
                go(first, 'projects')
                go(second, 'projects')
                page_data = {'version': 1, 'active': True, 'pages': 1, 'columns': 43,
                             'cards': {'title': {'text': '由工具更新的項目頁', 'visible': True}}}
                write('page:projects', page_data)
                page_heading(first, '由工具更新的項目頁')
                page_heading(second, '由工具更新的項目頁')
                assert studio.get(project['id'])['title'] == 'SYNC-PROJECT'

                # Remote changes wait for an active layout editor to finish.
                page_editor(second, 'projects')
                page_data['cards']['title']['text'] = '完成調整後的遠端標題'
                write('page:projects', page_data)
                received(second, 'page:projects', ['cards', 'title', 'text'], page_data['cards']['title']['text'])
                assert second.locator('#studio-panel h1').inner_text() == '由工具更新的項目頁'
                second.locator('[data-page-layout-edit=projects]').click()
                page_heading(second, '完成調整後的遠端標題')

                # A pending business form draft survives an explicit presentation-only apply.
                go(second, 'skills')
                second.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                second.locator('[name=goal]').fill('UNSAVED-SKILL-DRAFT')
                old_heading = second.locator('#studio-panel h1').inner_text()
                write('page:skills', {'version': 1, 'active': True, 'cards': {
                    'title': {'text': '保留草稿的 AI 工作頁', 'visible': True}}})
                received(second, 'page:skills', ['cards', 'title', 'text'], '保留草稿的 AI 工作頁')
                assert second.locator('#studio-panel h1').inner_text() == old_heading
                assert second.locator('[name=goal]').input_value() == 'UNSAVED-SKILL-DRAFT'
                second.get_by_role('button', name='套用新版面', exact=True).click()
                page_heading(second, '保留草稿的 AI 工作頁')
                assert second.locator('[name=goal]').input_value() == 'UNSAVED-SKILL-DRAFT'

                # New reference choices create cards on another device without a reload.
                go(second, 'overview')
                page_editor(first, 'projects')
                first.locator('[data-page-layout-id="studio_record-' + project['id'] + '"]').get_by_role(
                    'button', name='加入總覽', exact=True).click()
                first.locator('[data-page-layout-edit=projects]').click()
                pin_selector = '[data-widget="pin-studio_record-' + project['id'] + '"]'
                second.locator(pin_selector).get_by_role('heading', name='SYNC-PROJECT', exact=True).wait_for()
                assert 'CURRENT-AUTHORIZED-CONTENT' in second.locator(pin_selector).inner_text()
                assert member.locator(pin_selector).count() == 0
                assert document('pins', contexts[2]) is None
                go(first, 'overview')
                first.locator(pin_selector).get_by_role('button', name='從總覽移除此卡片', exact=True).click()
                second.wait_for_function('(selector) => !document.querySelector(selector)', arg=pin_selector)
                assert document('pins')['data']['refs'] == []

                # Member source permissions still apply after references synchronize.
                denied = contexts[2].request.patch(base + '/api/studio/presentation',
                    headers={'X-Blueprint-Request': '1'}, data={'key': 'pins', 'expected_version': 0,
                        'data': {'version': 1, 'refs': [{'kind': 'studio_record', 'id': restricted['id']}]}})
                assert denied.status in (403, 404), denied.text()
                go(member, 'reports')
                page_editor(member, 'reports')
                member.locator('[data-page-layout-id="studio_record-' + report['id'] + '"]').get_by_role(
                    'button', name='加入總覽', exact=True).click()
                member.locator('[data-page-layout-edit=reports]').click()
                go(member, 'overview')
                member_pin = member.locator('[data-widget="pin-studio_record-' + report['id'] + '"]')
                member_pin.get_by_role('heading', name='SYNC-MEMBER-REPORT', exact=True).wait_for()
                studio.update(report['id'], {'expected_version': report['version'],
                    'refs': [{'kind': 'project', 'id': restricted['id']}]})
                refresh(member)
                member_pin.get_by_text('來源已移除或目前沒有讀取權限。', exact=True).wait_for()
                assert 'MEMBER-CURRENT-CONTENT' not in member_pin.inner_text()
                assert 'SYNC-MEMBER-REPORT' not in member_pin.inner_text()

                # Simultaneous same-field edits retain local work until explicit conflict resolution.
                synced(first)
                synced(second)
                contexts[1].route('**/api/studio/presentation*', lambda route: route.abort())
                overview_edit(first, '另一裝置的版本')
                synced(first)
                overview_edit(second, '保留在本機的衝突內容')
                second.wait_for_function('() => BlueprintStudio.presentation().status().state==="offline"')
                assert document('overview')['data']['texts']['heading'] == '另一裝置的版本'
                contexts[1].unroute('**/api/studio/presentation*')
                second.locator('#presentation-retry').click()
                second.locator('#presentation-conflicts').wait_for(state='visible')
                heading(second, '保留在本機的衝突內容')
                second.locator('#presentation-use-remote').click()
                heading(second, '另一裝置的版本')
                synced(second)

                contexts[1].route('**/api/studio/presentation*', lambda route: route.abort())
                overview_edit(first, '另一裝置的第二個版本')
                synced(first)
                overview_edit(second, '明確選擇保留本機修改')
                second.wait_for_function('() => BlueprintStudio.presentation().status().state==="offline"')
                contexts[1].unroute('**/api/studio/presentation*')
                second.locator('#presentation-retry').click()
                second.locator('#presentation-conflicts').wait_for(state='visible')
                heading(second, '明確選擇保留本機修改')
                second.locator('#presentation-use-local').click()
                synced(second)
                heading(first, '明確選擇保留本機修改')
                assert document('overview')['data']['texts']['heading'] == '明確選擇保留本機修改'

                # Offline work survives a reload and is retried against the original account.
                contexts[1].route('**/api/studio/presentation*', lambda route: route.abort())
                overview_edit(second, '重新載入後保留的離線版面')
                second.wait_for_function('() => BlueprintStudio.presentation().status().state==="offline"')
                second.reload()
                second.wait_for_function('() => BlueprintStudio.isReady()')
                heading(second, '重新載入後保留的離線版面')
                contexts[1].unroute('**/api/studio/presentation*')
                second.locator('#presentation-retry').click()
                synced(second)
                heading(first, '重新載入後保留的離線版面')
                assert document('overview')['data']['texts']['heading'] == '重新載入後保留的離線版面'
                heading(member, '總覽')
                second.screenshot(path=str(output / 'presentation-sync-local-qa.png'), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(json.dumps({'verified': 'isolated-presentation-browser', 'real_backend': True,
                      'independent_owner_browsers': 2, 'account_isolation': True,
                      'conflict_resolution': True, 'offline_reload': True,
                      'production_deployment': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
