"""Isolated browser/API acceptance with fictional records; no live service claim."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.integrations import Integrations
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store


def main():
    from playwright.sync_api import sync_playwright

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-integration-browser')
    output.mkdir(parents=True, exist_ok=True)
    fixtures = ROOT / 'contracts' / 'integration-v1' / 'fixtures'
    manifest = json.loads((fixtures / 'manifest.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='blueprint-integration-browser-') as temp:
        data = Path(temp) / 'private'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password, company_profile={'name': '外部服務瀏覽器驗收'})
        service = Integrations(Store(data))
        registered = service.register(manifest)
        integration = registered['integration']
        server = make_server(data)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'

        def external(method, path, payload=None):
            headers = {'Authorization': 'Bearer ' + registered['token'],
                       'Content-Type': 'application/json', 'X-Blueprint-Request': '1'}
            request = Request(base + '/api/integrations/v1' + path, method=method, headers=headers,
                              data=json.dumps(payload).encode() if payload is not None else None)
            with urlopen(request, timeout=10) as response:
                return json.load(response)

        record_payload = json.loads((fixtures / 'record.json').read_text(encoding='utf-8'))
        record = external('POST', '/records', record_payload)['record']
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = playwright.chromium.launch(
                    **({'executable_path': executable} if executable else {}), headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'])
                context = browser.new_context(viewport={'width': 1440, 'height': 1050},
                                              http_credentials={'username': 'owner', 'password': password})
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                command_posts = []
                page.on('request', lambda request: command_posts.append(request.post_data_json)
                        if request.method == 'POST' and request.url.endswith('/api/integrations/v1/commands') else None)
                page.goto(base + '/#/external-work')
                page.wait_for_selector('#integration-workspace[data-integrations-ready=true]')
                assert page.locator('[data-integration-record-id="' + record['id'] + '"]').is_visible()
                assert record_payload['payload']['text'] in page.locator('#studio-panel').inner_text()
                assert page.locator('select[name=integration_id]').input_value() == integration['id']

                # Reading new evidence must preserve the operator's unfinished command.
                reason = page.locator('[data-integration-field=reason]')
                reason.fill('這是虛構瀏覽器驗收，請交給同事。')
                page.locator('select[name=record_id]').select_option(record['id'])
                page.evaluate('() => { window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); }')
                page.get_by_role('button', name='更新外部紀錄', exact=True).click()
                assert reason.input_value() == '這是虛構瀏覽器驗收，請交給同事。'
                failed_readbacks = []
                def fail_one_readback(route):
                    if route.request.method == 'GET' and not failed_readbacks:
                        failed_readbacks.append(route.request.url)
                        route.fulfill(status=503, content_type='application/json',
                                      body=json.dumps({'error': '虛構讀回暫時失敗'}))
                    else:
                        route.continue_()
                page.route('**/api/integrations/v1/commands/*', fail_one_readback)
                page.get_by_role('button', name='保存指令', exact=True).click()
                page.locator('[data-integration-form] .form-error:not([hidden])').wait_for()
                assert reason.input_value() == '這是虛構瀏覽器驗收，請交給同事。'
                assert len(service.list_commands(integration_id=integration['id'])['commands']) == 1
                page.get_by_role('button', name='保存指令', exact=True).click()
                page.wait_for_selector('[data-command-state=pending]')
                # The previous POST already succeeded before its deliberately
                # failed readback. A pending card can therefore predate this
                # retry's final list refresh. Finish that explicit save first.
                page.wait_for_function('() => document.getElementById("notice").textContent === "指令已保存並讀回，請查看外部服務的後續回報。"')
                assert page.locator('[data-command-state=pending]').count() == 1
                page.unroute('**/api/integrations/v1/commands/*', fail_one_readback)
                assert len(command_posts) == 2
                assert command_posts[0]['idempotency_key'] == command_posts[1]['idempotency_key']

                commands = service.list_commands(integration_id=integration['id'])['commands']
                assert len(commands) == 1 and commands[0]['state'] == 'pending'
                command = commands[0]
                assert command['record_id'] == record['id']
                assert command['payload']['reason'] == '這是虛構瀏覽器驗收，請交給同事。'
                events = external('GET', '/events')['events']
                assert any(event.get('command', {}).get('id') == command['id'] for event in events)

                # The external fixture supplies actual acknowledgements, independently
                # of the browser write. Only explicit refresh changes the shown result.
                def report(status, index, evidence):
                    return external('POST', '/command-results', {
                        'schema_version': '1.0', 'command_id': command['id'],
                        'event_id': 'browser-result-' + str(index),
                        'idempotency_key': 'browser-result-' + str(index),
                        'status': status, 'occurred_at': datetime.now(timezone.utc).isoformat(),
                        'payload': evidence,
                    })
                report('accepted', 1, {'detail': '虛構服務已持久保存指令。'})
                report('completed', 2, {'detail': '虛構服務確認同事接手。'})
                assert page.locator('[data-command-state=pending]').count() == 1
                reason = page.locator('[data-integration-field=reason]')
                reason.fill('下一個尚未保存的操作原因')
                page.get_by_role('button', name='更新外部紀錄', exact=True).click()
                card = page.locator('[data-command-id="' + command['id'] + '"]')
                card.locator('[data-command-state=completed]').first.wait_for()
                assert reason.input_value() == '下一個尚未保存的操作原因'
                assert card.locator('[data-command-state=accepted]').count() == 1
                assert service.get_command(command['id'])['state'] == 'completed'
                assert not errors, errors
                page.screenshot(path=str(output / 'external-work-local-qa.png'), full_page=True)

                # A late readback from a submitted command must not restore the
                # old route or overwrite the screen after explicit navigation.
                delayed = []
                def hold_readback(route):
                    delayed.append(route)
                page.route('**/api/integrations/v1/commands/*', hold_readback)
                with page.expect_request(lambda request: request.method == 'GET' and
                                         '/api/integrations/v1/commands/' in request.url):
                    page.get_by_role('button', name='保存指令', exact=True).click()
                page.evaluate('() => BlueprintStudio.navigate("overview")')
                page.wait_for_selector('#dashboard-panel:not([hidden])')
                assert len(delayed) == 1
                with page.expect_response(lambda response: response.url == delayed[0].request.url):
                    delayed[0].continue_()
                page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                assert page.url.endswith('#/overview')
                assert page.locator('#studio-panel').is_hidden()
                assert page.locator('#dashboard-panel').is_visible()
                assert len(service.list_commands(integration_id=integration['id'])['commands']) == 2
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(json.dumps({'verified': 'isolated-browser-and-api', 'fictional_data': True,
                      'production_deployment': False, 'output': str(output)}))


if __name__ == '__main__':
    main()
