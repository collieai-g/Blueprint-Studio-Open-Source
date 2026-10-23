"""Skill handoff acceptance against an isolated authenticated native backend.

This controls a test browser, never the user's Codex tab or a production store.
"""
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

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-skills-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-skills-browser-') as temp:
        password = secrets.token_urlsafe(24)
        data = Path(temp) / 'private'
        initialize_library(data, 'owner', password, company_profile={'name': 'Skill 驗收測試'})
        store = Store(data)
        studio = Studio(store)
        selected = store.add_file('已選品牌指引-PRIVATE-CHOSEN.md', '只存在隔離測試的原始品牌素材。'.encode())
        omitted = store.add_file('未選資料-PRIVATE-OMIT.md', b'UNSELECTED-ATTACHMENT-CONTENT')
        private = store.add_file('權限隔離-PRIVATE-HIDDEN.md', b'PRIVATE-ADMIN-CONTENT')
        studio.create({'kind': 'report', 'title': '只供管理員使用', 'visibility': 'admin',
                       'data': {'content': '私人管理員測試資料', 'source': '隔離測試'},
                       'refs': [{'kind': 'item', 'id': private['id']}]})
        studio.save_user({'username': 'staff', 'role': 'member', 'password': 'test-member-password'})
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
                    http_credentials={'username': 'owner', 'password': password},
                    permissions=['clipboard-read', 'clipboard-write'])
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function('() => BlueprintStudio.isReady()')
                for asset in ['/skills.js', '/skills.css']:
                    response = context.request.get(base + asset)
                    assert response.status == 200, asset
                    assert response.body() == (ROOT / 'library_web' / asset[1:]).read_bytes(), asset
                page.locator('#studio-navigation [data-group=specialists]').click()
                page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                assert page.evaluate('location.hash') == '#/skills'
                assert page.locator('#studio-navigation .studio-primary .studio-nav-label').all_text_contents() == ['總覽', '工作', 'AI', '資料']
                assert page.locator('#skill-sources option:checked').count() == 0
                page.locator('[name=title]').fill('PRIVATE-TASK-個人化影片')
                page.locator('[name=goal]').fill('PRIVATE-GOAL-介紹本次活動並引導報名。')
                page.locator('[name=audience]').fill('PRIVATE-AUDIENCE-已報名的參加者')
                page.locator('[name=style]').fill('PRIVATE-STYLE-白色與淺灰色')
                page.locator('[name=duration]').fill('30 秒')
                page.locator('[name=constraints]').fill('PRIVATE-CONSTRAINT-不使用未授權音樂')
                page.locator('.skill-reusable-rules summary').click()
                page.locator('[name=reusableRules]').fill('逐鏡頭核對字幕，先交分鏡再決定生成。')
                page.locator('#skill-sources').select_option('item:' + selected['id'])

                # Upload uses the actual existing workbench form and preserves
                # the Skill draft; no external model or Codex process is run.
                page.locator('#skill-upload-material').click()
                page.wait_for_function('() => document.getElementById("upload-dialog").open')
                page.locator('#upload-files').set_input_files({
                    'name': '新上載素材.txt', 'mimeType': 'text/plain', 'buffer': '新上載的隔離測試素材'.encode(),
                })
                page.locator('#upload-form button[type=submit]').click()
                page.wait_for_function('() => document.getElementById("upload-results").textContent.includes("已保存")')
                page.wait_for_function('() => !document.getElementById("upload-form").querySelector("button[type=submit]").disabled')
                page.locator('#upload-dialog [data-close]').first.click()
                page.wait_for_function('() => !document.getElementById("upload-dialog").open')
                assert page.locator('[name=goal]').input_value() == 'PRIVATE-GOAL-介紹本次活動並引導報名。'
                assert page.locator('#skill-sources').input_value() == 'item:' + selected['id']
                page.locator('#skill-refresh-sources').click()
                page.wait_for_function('() => [...document.querySelectorAll("#skill-sources option")].some(option => option.textContent.includes("新上載素材"))')
                assert page.locator('#skill-sources').input_value() == 'item:' + selected['id']

                # Editing while selected-source validation is pending must not
                # mix the old source snapshot with a newly edited task.
                pending_sources, unexpected_downloads = [], []
                page.route('**/api/items/' + selected['id'], lambda route: pending_sources.append(route))
                page.on('download', lambda download: unexpected_downloads.append(download))
                page.locator('#skill-download-brief').click()
                page.wait_for_timeout(100)
                assert pending_sources, 'Hold selected-source validation before editing'
                page.locator('[name=goal]').fill('PRIVATE-NEWER-選取來源期間的新要求')
                page.locator('#skill-sources').select_option('item:' + omitted['id'])
                pending_sources.pop().continue_()
                page.wait_for_selector('#skill-error:not([hidden])')
                assert '交接內容已修改' in page.locator('#skill-error').inner_text()
                assert unexpected_downloads == []
                page.unroute('**/api/items/' + selected['id'])
                page.locator('[name=goal]').fill('PRIVATE-GOAL-介紹本次活動並引導報名。')
                page.locator('#skill-sources').select_option('item:' + selected['id'])

                page.locator('#skill-copy-handoff').click()
                page.wait_for_function('() => document.getElementById("skill-brief-preview").value.includes("PRIVATE-GOAL")')
                brief = page.locator('#skill-brief-preview').input_value()
                reusable = page.locator('#skill-reusable-preview').input_value()
                for text in [selected['id'], selected['title'], 'PRIVATE-GOAL', 'PRIVATE-AUDIENCE', 'PRIVATE-STYLE', 'PRIVATE-CONSTRAINT']:
                    assert text in brief, text
                    assert text not in reusable, text
                for text in [omitted['id'], omitted['title'], private['id'], private['title'], 'UNSELECTED-ATTACHMENT-CONTENT']:
                    assert text not in brief, text
                    assert text not in reusable, text
                assert '逐鏡頭核對字幕' in reusable
                assert reusable.startswith('---\nname:') and 'description:' in reusable
                assert '附件全文' in brief
                clipboard = page.evaluate('() => navigator.clipboard.readText()')
                assert 'PRIVATE-GOAL' in clipboard
                assert selected['id'] in clipboard

                with page.expect_download() as pending:
                    page.locator('#skill-download-brief').click()
                brief_download = pending.value
                brief_download.save_as(output / 'task-brief.md')
                assert (output / 'task-brief.md').read_text() == page.locator('#skill-brief-preview').input_value()
                with page.expect_download() as pending:
                    page.locator('#skill-download-skill').click()
                skill_download = pending.value
                assert skill_download.suggested_filename == 'SKILL.md'
                skill_download.save_as(output / 'SKILL.md')
                assert (output / 'SKILL.md').read_text() == page.locator('#skill-reusable-preview').input_value()

                # A selected source changed after review must be reviewed again;
                # the UI must not silently export a stale source version.
                store.update_item(selected['id'], {'expected_version': selected['version'], 'notes': '選取後更新的測試筆記'})
                stale_writes = []
                page.on('request', lambda request: stale_writes.append(request.url) if request.method == 'POST' and request.url == base + '/api/studio/records' else None)
                page.locator('#skill-save-brief').click()
                page.wait_for_selector('#skill-error:not([hidden])')
                assert '已更新' in page.locator('#skill-error').inner_text()
                assert stale_writes == [], stale_writes
                page.locator('#skill-refresh-sources').click()
                page.wait_for_function('() => !document.getElementById("skill-refresh-sources").disabled')

                # Saving uses a report with typed references, so the existing
                # server transaction inherits source permissions atomically.
                with page.expect_response(lambda response: response.url == base + '/api/studio/records' and response.request.method == 'POST') as saved:
                    page.locator('#skill-save-brief').click()
                assert saved.value.status == 201
                saved_report = saved.value.json()['record']
                page.wait_for_function('() => document.getElementById("skill-status").textContent.includes("已保存並讀回核對")')
                saved_brief = page.locator('#skill-brief-preview').input_value()
                assert saved_report['kind'] == 'report'
                assert saved_report['refs'] == [{'kind': 'item', 'id': selected['id']}]
                assert studio.get(saved_report['id'])['data']['content'] == saved_brief.strip()
                readback = context.request.get(base + '/api/studio/records/' + saved_report['id'])
                assert readback.status == 200
                assert readback.json()['record']['data']['content'].encode() == saved_brief.strip().encode()

                # Accepted writes finish their original readback even if the
                # user edits again; those newer changes must remain unsaved.
                page.locator('[name=constraints]').fill('PRIVATE-CONSTRAINT-新版本需要重新核對')
                pending_writes = []
                page.route('**/api/studio/records', lambda route: pending_writes.append(route) if route.request.method == 'POST' else route.continue_())
                page.locator('#skill-save-brief').click()
                page.wait_for_timeout(100)
                assert pending_writes, 'Hold a real save after its input snapshot was created'
                pending = pending_writes.pop()
                expected_content = pending.request.post_data_json['data']['content']
                page.locator('[name=goal]').fill('PRIVATE-NEWER-保存期間仍未保存的新修改')
                with page.expect_response(lambda response: response.url == base + '/api/studio/records' and response.request.method == 'POST') as old_saved:
                    pending.continue_()
                assert old_saved.value.status == 201
                older_id = old_saved.value.json()['record']['id']
                page.wait_for_function('() => document.getElementById("skill-status").textContent.includes("目前草稿另有未保存修改")')
                assert page.locator('[name=goal]').input_value() == 'PRIVATE-NEWER-保存期間仍未保存的新修改'
                page.evaluate('() => document.activeElement.blur()')
                assert page.evaluate('() => BlueprintSkills.isBusy()')
                assert studio.get(older_id)['data']['content'] == expected_content
                assert 'PRIVATE-NEWER' not in expected_content
                page.unroute('**/api/studio/records')
                page.locator('[name=goal]').fill('PRIVATE-GOAL-介紹本次活動並引導報名。')

                page.locator('#skill-sources').select_option('item:' + private['id'])
                with page.expect_response(lambda response: response.url == base + '/api/studio/records' and response.request.method == 'POST') as private_saved:
                    page.locator('#skill-save-brief').click()
                assert private_saved.value.status == 201
                private_report = private_saved.value.json()['record']
                page.wait_for_function('() => document.getElementById("skill-status").textContent.includes("已保存並讀回核對")')
                assert private_report['visibility'] == 'admin'
                assert private_report['refs'] == [{'kind': 'item', 'id': private['id']}]
                assert private['title'] in studio.get(private_report['id'])['data']['content']
                page.locator('#skill-sources').select_option('item:' + selected['id'])

                requests = []
                page.on('request', lambda request: requests.append(request.url) if '/api/' in request.url else None)
                page.evaluate('() => { document.activeElement.blur(); window.qaSkillForm = document.getElementById("skill-brief-form"); }')
                page.clock.install()
                page.clock.fast_forward(35000)
                page.evaluate('() => { window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); }')
                page.wait_for_timeout(100)
                assert requests == [], requests
                assert page.evaluate('() => window.qaSkillForm === document.getElementById("skill-brief-form")')
                assert page.locator('[name=goal]').input_value() == 'PRIVATE-GOAL-介紹本次活動並引導報名。'
                page.evaluate('() => window.scrollTo(0, 0)')
                page.screenshot(path=str(output / 'skills-desktop.png'), full_page=True)
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'skills-mobile.png'), full_page=True)

                # Available choices and export-time reads both honor the current
                # server role, including permission changes after selection.
                member = browser.new_context(viewport={'width': 1440, 'height': 1050},
                    http_credentials={'username': 'staff', 'password': 'test-member-password'})
                member_page = member.new_page()
                member_page.on('pageerror', lambda error: errors.append(str(error)))
                member_page.goto(base + '/#/skills')
                member_page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                assert member.request.get(base + '/api/studio/records/' + private_report['id']).status == 403
                member_reports = member.request.get(base + '/api/studio/records?kind=report&limit=100').json()['records']
                assert all(record['id'] != private_report['id'] for record in member_reports)
                member_options = member_page.locator('#skill-sources').inner_text()
                assert private['title'] not in member_options
                member_page.locator('[name=title]').fill('成員權限驗收')
                member_page.locator('[name=goal]').fill('只保存仍獲准使用的選定資料。')
                member_page.locator('#skill-sources').select_option('item:' + selected['id'])
                studio.create({'kind': 'report', 'title': '選取後改為私人', 'visibility': 'admin',
                               'data': {'content': '匯出前重新核對權限', 'source': '隔離測試'},
                               'refs': [{'kind': 'item', 'id': selected['id']}]})
                writes = []
                member_page.on('request', lambda request: writes.append(request.url) if request.method == 'POST' and request.url == base + '/api/studio/records' else None)
                member_page.locator('#skill-save-brief').click()
                member_page.wait_for_selector('#skill-error:not([hidden])')
                assert writes == [], writes
                assert '已保存並讀回核對' not in member_page.locator('#skill-status').inner_text()
                assert errors == [], errors
                browser.close()
                print(json.dumps({'passed': True, 'checks': [
                    'authenticated Skills assets and four-section navigation', 'explicit selected-source boundary',
                    'reusable Skill/private brief separation', 'actual upload with preserved draft',
                    'copy and Markdown downloads', 'persistent report and exact-content readback',
                    'in-flight edits preserve source/draft snapshots', 'idle and focus stability',
                    'source version conflict', 'derived report inherits private source permissions',
                    'current source permissions', 'desktop/mobile layout',
                    'no browser exceptions'], 'screenshots': str(output)}, ensure_ascii=False))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
