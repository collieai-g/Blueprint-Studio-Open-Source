"""Browser/API/MCP acceptance with explicit fixture outputs, never a live AI claim."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.mcp_server import Bridge
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio


def main():
    from playwright.sync_api import sync_playwright

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-agent-browser')
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        'contract_version': 1, 'id': 'browser-summary', 'name': '瀏覽器摘要驗收 Agent',
        'version': '1.0.0', 'description': '測試介面與工具讀寫同一份工作。',
        'adapter': 'connected_client', 'instructions': '只整理指定資料，不猜測。',
        'settings': [
            {'key': 'language', 'label': '結果語言', 'type': 'text', 'required': True},
            {'key': 'format', 'label': '可選格式', 'type': 'select', 'options': [{'value': 'short', 'label': '簡短'}]},
        ],
        'operations': [{'id': 'summarize', 'name': '整理摘要', 'instructions': '整理輸入及指定來源。',
                        'input_fields': [{'key': 'text', 'label': '要整理的內容', 'type': 'textarea', 'required': True}]}],
        'ui': {'layout': 'standard', 'result_label': '摘要結果'},
    }
    with tempfile.TemporaryDirectory(prefix='blueprint-agent-browser-') as temp:
        data = Path(temp) / 'private'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password, company_profile={'name': 'Agent 驗收測試'})
        studio = Studio(Store(data))
        source = studio.create({'kind': 'report', 'title': '指定驗收來源',
                                'data': {'content': '已完成初稿，下一步核對文字。', 'source': '測試資料'}})['record']
        studio.save_user({'username': 'staff', 'role': 'member', 'password': 'test-member-password'})
        server = make_server(data)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        env = {'BLUEPRINT_BACKEND_USERNAME': 'owner', 'BLUEPRINT_BACKEND_PASSWORD': password}
        try:
            with patch.dict(os.environ, env), sync_playwright() as playwright:
                bridge = Bridge(base)

                def call(name, arguments):
                    return bridge.call('blueprint_' + name, arguments)

                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = playwright.chromium.launch(
                    **({'executable_path': executable} if executable else {}), headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050},
                                              http_credentials={'username': 'owner', 'password': password})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(base)
                page.wait_for_function('() => BlueprintStudio.isReady()')

                def go(route):
                    page.evaluate('(route) => BlueprintStudio.navigate(route)', route)

                go('agents')
                page.get_by_role('button', name='新增 Agent', exact=True).click()
                page.locator('[name=repository]').fill('https://github.com/example/browser-summary')
                page.locator('[name=revision]').fill('a' * 40)
                page.locator('[name=manifest_file]').set_input_files({
                    'name': 'agent.json', 'mimeType': 'application/json',
                    'buffer': json.dumps(manifest, ensure_ascii=False).encode(),
                })
                # Required settings without defaults are supplied before registration.
                page.locator('[name=initial_language]').fill('繁體中文')
                page.locator('[name=manifest]').fill(json.dumps(manifest, ensure_ascii=False, indent=2))
                assert page.get_by_role('button', name='加入 Agent', exact=True).is_disabled()
                page.get_by_role('button', name='讀取設定', exact=True).click()
                assert page.locator('[name=initial_language]').input_value() == '繁體中文'
                page.get_by_role('button', name='加入 Agent', exact=True).click()
                page.wait_for_selector('#studio-panel h1:text-is("瀏覽器摘要驗收 Agent")')
                agent_id = page.url.rsplit('/', 1)[-1]
                assert call('agent_get', {'id': agent_id})['agent']['settings']['language'] == '繁體中文'
                assert page.locator('[data-agent-operation=summarize]').is_disabled()
                assert '尚未連接' in page.locator('#studio-panel').inner_text()
                page.get_by_role('button', name='連接功能', exact=True).click()
                page.wait_for_selector('button:text-is("中斷功能連接")')
                assert page.locator('[data-agent-operation=summarize]').is_enabled()

                # UI drafts survive automatic refresh and a concurrent MCP edit.
                setting = page.locator('[name=setting_language]')
                setting.fill('尚未保存的介面修改')
                page.get_by_role('button', name='中斷功能連接', exact=True).click()
                page.wait_for_selector('.agent-settings-form .form-error:not([hidden])')
                assert '請先保存' in page.locator('.agent-settings-form .form-error').inner_text()
                assert setting.input_value() == '尚未保存的介面修改'
                page.evaluate('() => { document.activeElement.blur(); return BlueprintStudio.refreshVisible(); }')
                assert setting.input_value() == '尚未保存的介面修改'
                agent = call('agent_get', {'id': agent_id})['agent']
                call('agent_update', {'id': agent_id, 'expected_version': agent['version'],
                                     'settings_json': json.dumps({'language': '工具更新的語言'})})
                page.get_by_role('button', name='保存設定', exact=True).click()
                page.wait_for_selector('.agent-settings-form .form-error:not([hidden])')
                assert setting.input_value() == '尚未保存的介面修改'
                page.get_by_role('button', name='重新讀取並捨棄未保存修改', exact=True).click()
                page.wait_for_function('() => document.querySelector("[name=setting_language]")?.value === "工具更新的語言"')
                setting.fill('繁體中文')
                page.get_by_role('button', name='保存設定', exact=True).click()
                page.wait_for_function('() => !BlueprintAgents.isBusy()')
                assert call('agent_get', {'id': agent_id})['agent']['settings']['language'] == '繁體中文'

                # Creating a job only queues it; no summary is produced by the browser.
                page.locator('[data-agent-operation=summarize]').click()
                page.locator('.agent-job-form [name=title]').fill('瀏覽器與 MCP 共用工作')
                page.locator('[name=input_text]').fill('請整理目前進度與下一步。')
                selected_ref = {'kind': 'report', 'id': source['id']}
                page.locator('[name=refs]').select_option(json.dumps(selected_ref, separators=(',', ':')))
                page.get_by_role('button', name='保存並交給 Codex', exact=True).click()
                page.wait_for_selector('#agent-handoff-text')
                job_id = page.url.rsplit('/', 1)[-1]
                assert 'blueprint_agent_job_context' in page.locator('#agent-handoff-text').input_value()
                assert '只有最新狀態為 awaiting_codex' in page.locator('#agent-handoff-text').input_value()
                job = call('agent_job_get', {'id': job_id})['job']
                assert job['state'] == 'awaiting_codex' and not job['result']
                page.screenshot(path=str(output / 'agent-awaiting-codex.png'), full_page=True)

                # Explicit fixture client output tests transport and state, not AI quality.
                details = call('agent_job_context', {'id': job_id})
                assert len(details['sources']) == 1 and details['sources'][0]['id'] == source['id']
                client = 'Browser acceptance fixture client'
                job = call('agent_job_action', {'id': job_id, 'expected_version': job['version'],
                    'action': 'start', 'arguments_json': json.dumps({'source': client, 'context_digest': details['context_digest']})})['job']
                page.evaluate('() => { document.activeElement.blur(); return BlueprintStudio.refreshVisible(); }')
                page.wait_for_selector('[data-agent-status=in_progress]')
                resumed_handoff = page.locator('#agent-handoff-text').input_value()
                assert '不要重複呼叫 start' in resumed_handoff
                assert '使用 blueprint_agent_job_action 的 start' not in resumed_handoff
                assert 'job.started_by_client' in resumed_handoff and 'job.context_digest' in resumed_handoff
                assert '不可冒用原本的結果來源' in resumed_handoff
                result_text = '驗收固定輸出：已完成初稿；下一步核對文字。'
                call('agent_job_action', {'id': job_id, 'expected_version': job['version'], 'action': 'complete',
                    'arguments_json': json.dumps({'result': result_text, 'source': client,
                                                  'source_refs': [selected_ref], 'context_digest': details['context_digest']})})
                page.evaluate('() => { document.activeElement.blur(); return BlueprintStudio.refreshVisible(); }')
                page.wait_for_selector('[data-agent-status=completed]')
                assert page.locator('.agent-result').inner_text() == result_text
                assert client in page.locator('.agent-result-panel').inner_text()
                assert page.locator('.agent-result-panel .studio-related-link').get_attribute('title') == source['id']
                assert page.locator('#agent-handoff-text').count() == 0
                page.screenshot(path=str(output / 'agent-result.png'), full_page=True)

                # Failed jobs remain failed and show the actual recorded reason.
                failed = call('agent_job_create', {'agent_id': agent_id, 'operation_id': 'summarize',
                    'title': '失敗狀態驗收', 'inputs_json': json.dumps({'text': '失敗測試'}), 'refs_json': '[]'})['job']
                call('agent_job_action', {'id': failed['id'], 'expected_version': failed['version'], 'action': 'fail',
                    'arguments_json': json.dumps({'reason': '驗收固定錯誤：無法讀取來源。'})})
                go('agent-job/' + failed['id'])
                assert page.locator('[data-agent-status=failed]').is_visible()
                assert '無法讀取來源' in page.locator('.agent-error').inner_text()
                assert page.locator('.agent-result-panel').count() == 0

                go('agents')
                page.set_viewport_size({'width': 390, 'height': 844})
                page.wait_for_function('() => getComputedStyle(document.getElementById("sidebar")).visibility === "hidden"')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(output / 'agent-mobile.png'), full_page=True)
                member = browser.new_context(http_credentials={'username': 'staff', 'password': 'test-member-password'}).new_page()
                member.goto(base + '/#/agents')
                member.wait_for_function('() => BlueprintStudio.isReady()')
                assert member.get_by_role('button', name='新增 Agent', exact=True).count() == 0
                member.evaluate('(route) => BlueprintStudio.navigate(route)', 'agent/' + agent_id)
                assert member.locator('.agent-settings-form').count() == 0
                assert member.locator('[data-agent-operation=summarize]').is_enabled()
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    print('Agent browser checks passed: registration, connection states, shared settings/conflicts, draft preservation, queued handoff, MCP fixture completion, real failure state, role-aware controls and mobile layout. No live AI or external service was called.')


if __name__ == '__main__':
    main()
