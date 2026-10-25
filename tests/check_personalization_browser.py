"""Personal layout and authorized overview pins against an isolated real backend."""
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
from blueprint_library.agents import Agents
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio

LAYOUT_KEY = 'blueprint-page-layouts-v1'
OVERVIEW_KEY = 'blueprint-workspace-v2'


def main():
    from playwright.sync_api import sync_playwright

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-personalization-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-personalization-browser-') as temp:
        data = Path(temp) / 'private'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password, company_profile={'name': '個人版面隔離驗收'})
        store = Store(data)
        studio = Studio(store)
        project = studio.create({'kind': 'project', 'title': 'PERSONALIZATION-PROJECT',
            'data': {'stage': 'development', 'progress': 40, 'summary': 'PROJECT-CURRENT-CONTENT'}})['record']
        report = studio.create({'kind': 'report', 'title': 'MEMBER-PIN-PUBLIC-TITLE',
            'data': {'content': 'MEMBER-PIN-PUBLIC-CONTENT', 'source': '隔離驗收'}})['record']
        private = studio.create({'kind': 'project', 'title': 'PRIVATE-ADMIN-REFERENCE',
            'data': {}, 'visibility': 'admin'})['record']
        workday = store.import_workday({'date': '2026-10-24', 'source': 'Browser QA',
            'source_key': 'personalization-real-workday', 'title': 'WORKDAY-CURRENT-TITLE',
            'summary': 'WORKDAY-CURRENT-CONTENT',
            'entries': [{'type': 'outcome', 'title': '隔離驗收成果', 'content': '真實保存的測試資料'}]})['workday']
        attachment = store.add_file('真實測試附件.md', b'LOCAL-QA-ATTACHMENT')
        studio.save_user({'username': 'staff', 'role': 'member', 'password': 'test-member-password'})
        agent = Agents(store).register({'manifest': {
            'contract_version': 1, 'id': 'personalization-agent', 'name': 'PERSONALIZATION-AGENT',
            'version': '1.0.0', 'description': 'AGENT-CURRENT-CONTENT',
            'adapter': 'connected_client', 'instructions': '只處理使用者選定的資料。',
            'settings': [], 'operations': [{'id': 'summarize', 'name': '整理摘要',
                'instructions': '保存實際摘要。', 'input_fields': []}],
            'ui': {'layout': 'standard', 'result_label': '結果'}},
            'source': {'repository': 'https://github.com/example/personalization-test', 'revision': 'a' * 40}})['agent']
        server = make_server(data)
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
                context = browser.new_context(viewport={'width': 1440, 'height': 1050},
                    http_credentials={'username': 'owner', 'password': password})
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function('() => BlueprintStudio.isReady()')

                def go(route, target=page):
                    target.evaluate('(route) => BlueprintStudio.navigate(route)', route)
                    target.locator('#page-layout-tools button').first.wait_for(state='visible')

                def edit(route, target=page):
                    target.locator(f'[data-page-layout-edit="{route}"]').click()
                    editor = target.locator('.page-layout-editor:visible')
                    advanced = editor.locator('.page-layout-advanced')
                    if not advanced.evaluate('node => node.open'):
                        assert editor.locator('.page-layout-quick select').is_visible()
                        assert editor.get_by_label('寬度（格）', exact=True).is_hidden()
                        advanced.locator('summary').click()
                    return editor

                def saved(target=page):
                    return target.evaluate('(key) => localStorage.getItem(key)', LAYOUT_KEY)

                def route_preferences(route, target=page):
                    raw = json.loads(saved(target))
                    matches = [scope['pages'][route] for scope in raw.get('scopes', {}).values()
                               if route in scope.get('pages', {})]
                    assert len(matches) == 1, (route, raw)
                    return matches[0]

                def refresh(target=page):
                    target.locator('#add-item').click()
                    target.locator('#refresh-workspace').click()

                assert page.locator('.breadcrumb, .studio-narrow-toggle').count() == 0
                assert page.locator('#add-item').inner_text().strip().startswith('操作')
                page.locator('#add-item').click()
                menu = page.locator('#add-menu')
                assert menu.get_by_role('menuitem', name='更新資料', exact=True).is_visible()
                assert menu.get_by_role('menuitem', name='搜尋工作空間').is_visible()
                for action in ['upload', 'link', 'collection']:
                    assert menu.locator('[data-action=' + action + ']').is_visible()
                page.keyboard.press('Escape')
                assert menu.is_hidden()

                # Every main area and its retained pages expose the same editor.
                for route in ['projects', 'inbox', 'overview/todos', 'overview/workdays',
                              'skills', 'agents', 'command', 'documents', 'inspirations',
                              'topics', 'pinned', 'archived', 'account']:
                    go(route)
                    assert page.locator(f'[data-page-layout-edit="{route}"]').is_visible(), route
                    if route != 'account':
                        assert page.locator('.topbar #studio-section-navigation').is_visible(), route
                    assert page.locator('.breadcrumb').count() == 0
                go('overview')
                assert page.locator('#page-layout-tools #edit-board').is_visible()
                assert page.locator('#edit-board').inner_text() == '調整版面'

                # Display text is local plain text, and each route keeps its own layout.
                go('projects')
                editor = edit('projects')
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('title')
                display_text = '<img src=x onerror=alert(1)> 我的工作版面'
                editor.locator('[data-page-layout-text=projects]').fill(display_text)
                assert page.locator('#studio-panel h1').inner_text() == display_text
                assert page.locator('#studio-panel h1 img').count() == 0
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('studio_record-' + project['id'])
                width = editor.get_by_label('寬度（格）', exact=True)
                height = editor.get_by_label('高度（格）', exact=True)
                minimum, maximum = int(width.get_attribute('min')), int(width.get_attribute('max'))
                original = saved()
                width.fill(str(maximum + 1))
                editor.get_by_role('button', name='套用大小', exact=True).click()
                assert not width.evaluate('node => node.checkValidity()') and saved() == original
                width.fill(str(minimum - 1))
                editor.get_by_role('button', name='套用大小', exact=True).click()
                assert not width.evaluate('node => node.checkValidity()') and saved() == original
                width.fill(str(minimum + 2))
                height.fill(str(int(height.get_attribute('min')) + 2))
                editor.get_by_role('button', name='套用大小', exact=True).click()
                project_layout = route_preferences('projects')
                assert project_layout['cards']['studio_record-' + project['id']]['w'] == minimum + 2
                wrapper = page.locator('[data-page-layout-id="studio_record-' + project['id'] + '"]')
                wrapper.get_by_role('button', name='加入總覽', exact=True).click()
                page.locator('[data-page-layout-edit=projects]').click()
                go('inbox')
                editor = edit('inbox')
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('title')
                editor.locator('[data-page-layout-text=inbox]').fill('我的收件匣顯示文字')
                page.locator('[data-page-layout-edit=inbox]').click()
                page.reload()
                page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('#studio-panel h1').inner_text() == '我的收件匣顯示文字'
                go('projects')
                assert page.locator('#studio-panel h1').inner_text() == display_text
                assert route_preferences('projects') == project_layout
                assert studio.get(project['id'])['title'] == 'PERSONALIZATION-PROJECT'

                # Legacy lists reset their layout wrappers before success/error
                # replacement, and pending background loads cannot duplicate edit nodes.
                go('overview/workdays')
                page.locator('#workdays-panel [data-workday-id="' + workday['id'] + '"]').wait_for()
                editor = edit('overview/workdays')
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('workday-' + workday['id'])
                editor.get_by_label('寬度（格）', exact=True).fill('18')
                editor.get_by_label('高度（格）', exact=True).fill('8')
                editor.get_by_role('button', name='套用大小', exact=True).click()
                page.locator('[data-page-layout-edit="overview/workdays"]').click()
                pending_workday = []
                page.route('**/api/workdays?*', lambda route: pending_workday.append(route))
                with page.expect_request(lambda request: '/api/workdays?' in request.url):
                    page.evaluate('() => { window.personalizationPendingList = BlueprintWorkdays.refreshCurrent(true); }')
                editor = edit('overview/workdays')
                assert len(pending_workday) == 1
                pending_workday[0].continue_()
                page.evaluate('() => window.personalizationPendingList')
                assert page.locator('#workdays-panel [data-workday-id="' + workday['id'] + '"]').count() == 1
                assert editor.is_visible()
                page.unroute('**/api/workdays?*')
                page.locator('[data-page-layout-edit="overview/workdays"]').click()
                page.route('**/api/workdays?*', lambda route: route.fulfill(status=403,
                    content_type='application/json', body=json.dumps({'error': '虛構權限已撤銷'})))
                page.evaluate('() => BlueprintWorkdays.refreshCurrent(false)')
                assert page.locator('#workdays-panel [data-workday-id="' + workday['id'] + '"]').count() == 0
                assert 'WORKDAY-CURRENT-TITLE' not in page.locator('#workdays-panel').inner_text()
                assert page.locator('#workdays-panel .error-text').is_visible()
                page.unroute('**/api/workdays?*')
                page.evaluate('() => BlueprintWorkdays.refreshCurrent(false)')
                page.locator('#workdays-panel [data-workday-id="' + workday['id'] + '"]').wait_for()

                go('documents')
                page.locator('#library-panel [data-item-id="' + attachment['id'] + '"]').wait_for()
                editor = edit('documents')
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('item-' + attachment['id'])
                editor.get_by_label('寬度（格）', exact=True).fill('18')
                editor.get_by_label('高度（格）', exact=True).fill('8')
                editor.get_by_role('button', name='套用大小', exact=True).click()
                page.locator('[data-page-layout-edit=documents]').click()
                page.route('**/api/items?*', lambda route: route.fulfill(status=403,
                    content_type='application/json', body=json.dumps({'error': '虛構文件權限已撤銷'})))
                refresh()
                page.wait_for_function('() => document.getElementById("result-count").textContent === "讀取失敗"')
                assert page.locator('#library-panel [data-item-id="' + attachment['id'] + '"]').count() == 0
                page.unroute('**/api/items?*')
                refresh()
                page.locator('#library-panel [data-item-id="' + attachment['id'] + '"]').wait_for()

                # Editing a page moves the actual form nodes, preserving draft input.
                go('skills')
                page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                page.locator('[name=goal]').fill('PERSONALIZATION-UNSAVED-DRAFT')
                editor = edit('skills')
                assert page.locator('[name=goal]').input_value() == 'PERSONALIZATION-UNSAVED-DRAFT'
                editor.locator('.page-layout-size-fields label').filter(has_text='調整項目').locator('select').select_option('title')
                editor.locator('[data-page-layout-text=skills]').fill('我的 AI 工作頁')
                editor.get_by_role('button', name='復原上一步', exact=True).click()
                assert page.locator('[name=goal]').input_value() == 'PERSONALIZATION-UNSAVED-DRAFT'
                assert page.locator('#studio-panel h1').inner_text() != '我的 AI 工作頁'
                assert editor.get_by_role('button', name='復原上一步', exact=True).is_disabled()
                page.screenshot(path=str(output / 'personal-editor-undo-local-qa.png'), full_page=True)
                editor.locator('[data-page-layout-text=skills]').fill('我的 AI 工作頁')
                page.locator('[data-page-layout-edit=skills]').click()
                assert page.locator('[name=goal]').input_value() == 'PERSONALIZATION-UNSAVED-DRAFT'
                page.evaluate('() => { window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); }')
                assert page.locator('[name=goal]').input_value() == 'PERSONALIZATION-UNSAVED-DRAFT'

                # Pin actual Agent metadata and read the current backend again on overview.
                go('agents')
                editor = edit('agents')
                page.locator('[data-page-layout-id="agent-' + agent['id'] + '"]').get_by_role('button', name='加入總覽', exact=True).click()
                page.locator('[data-page-layout-edit=agents]').click()
                go('overview')
                project_pin = page.locator('[data-widget="pin-studio_record-' + project['id'] + '"]')
                agent_pin = page.locator('[data-widget="pin-agent-' + agent['id'] + '"]')
                project_pin.get_by_role('heading', name='PERSONALIZATION-PROJECT', exact=True).wait_for()
                agent_pin.get_by_role('heading', name='PERSONALIZATION-AGENT', exact=True).wait_for()
                assert 'AGENT-CURRENT-CONTENT' in agent_pin.inner_text()
                pin_documents = page.evaluate('() => Object.entries(localStorage).filter(([key]) => key.startsWith("blueprint-overview-cards-v1:"))')
                assert pin_documents and all('CURRENT-CONTENT' not in raw and 'PERSONALIZATION-PROJECT' not in raw
                                             for _, raw in pin_documents)
                project = studio.update(project['id'], {'expected_version': project['version'],
                    'title': 'PERSONALIZATION-PROJECT-UPDATED', 'data': {'summary': 'PROJECT-FRESH-CONTENT'}})
                refresh()
                project_pin.get_by_role('heading', name='PERSONALIZATION-PROJECT-UPDATED', exact=True).wait_for()
                assert 'PROJECT-FRESH-CONTENT' in project_pin.inner_text()
                page.locator('#edit-board').click()
                assert page.locator('#board-advanced #reset-board').count() == 1
                assert page.locator('#reset-board').is_hidden()
                page.screenshot(path=str(output / 'overview-editor-simple-local-qa.png'), full_page=True)
                page.locator('#board-advanced > summary').click()
                page.locator('#board-selected').select_option('heading')
                page.locator('#board-text-content').fill('我的個人總覽')
                page.locator('#apply-board-text').click()
                page.locator('#edit-board').click()
                page.reload()
                page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('[data-widget=heading] h1').inner_text() == '我的個人總覽'
                project_pin.get_by_role('heading', name='PERSONALIZATION-PROJECT-UPDATED', exact=True).wait_for()
                page.screenshot(path=str(output / 'personal-overview-local-qa.png'), full_page=True)

                # Mobile viewing leaves the desktop preferences byte-for-byte intact.
                before_page, before_overview = saved(), page.evaluate('(key) => localStorage.getItem(key)', OVERVIEW_KEY)
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_function('() => document.documentElement.scrollWidth <= innerWidth')
                assert saved() == before_page
                assert page.evaluate('(key) => localStorage.getItem(key)', OVERVIEW_KEY) == before_overview
                go('projects')
                assert page.locator('.topbar #studio-section-navigation').is_visible()
                assert page.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
                page.locator('#add-item').click()
                assert page.locator('#add-menu').is_visible()
                page.keyboard.press('Escape')
                page.screenshot(path=str(output / 'personal-work-mobile-local-qa.png'), full_page=True)

                # A member's saved pin must lose prior content after permission revocation.
                owner_storage = page.evaluate('() => Object.fromEntries(Object.entries(localStorage))')
                member_context = browser.new_context(viewport={'width': 1440, 'height': 1050},
                    http_credentials={'username': 'staff', 'password': 'test-member-password'})
                member_context.add_init_script('Object.entries(' + json.dumps(owner_storage) +
                    ').forEach(([key,value]) => localStorage.setItem(key,value));')
                member = member_context.new_page()
                member.on('pageerror', lambda error: errors.append(str(error)))
                member.goto(base + '/#/projects')
                member.wait_for_function('() => BlueprintStudio.isReady()')
                assert member.locator('#studio-panel h1').inner_text() == '項目'
                go('reports', member)
                editor = edit('reports', member)
                member.locator('[data-page-layout-id="studio_record-' + report['id'] + '"]').get_by_role('button', name='加入總覽', exact=True).click()
                member.locator('[data-page-layout-edit=reports]').click()
                go('overview', member)
                assert member.locator('[data-widget=heading] h1').inner_text() == '總覽'
                member_pin = member.locator('[data-widget="pin-studio_record-' + report['id'] + '"]')
                member_pin.get_by_role('heading', name='MEMBER-PIN-PUBLIC-TITLE', exact=True).wait_for()
                assert 'MEMBER-PIN-PUBLIC-CONTENT' in member_pin.inner_text()
                studio.update(report['id'], {'expected_version': report['version'],
                    'refs': [{'kind': 'project', 'id': private['id']}]})
                refresh(member)
                member_pin.get_by_text('來源已移除或目前沒有讀取權限。', exact=True).wait_for()
                assert 'MEMBER-PIN-PUBLIC-TITLE' not in member_pin.inner_text()
                assert 'MEMBER-PIN-PUBLIC-CONTENT' not in member_pin.inner_text()
                assert 'PERSONALIZATION-PROJECT-UPDATED' not in member.locator('#workspace-board').inner_text()
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(json.dumps({'verified': 'isolated-personalization-browser', 'real_backend': True,
                      'production_deployment': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
