"""Company calendar acceptance against an isolated authenticated native backend.

All employees, time records and work below are synthetic. This test controls its
own browser, never a company deployment or the user's logged-in Codex pane.
"""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio


def main():
    from playwright.sync_api import sync_playwright

    output = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/blueprint-calendar-check')
    output.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo('Asia/Hong_Kong')).date()
    day, month = today.isoformat(), today.strftime('%Y-%m')
    with tempfile.TemporaryDirectory(prefix='blueprint-calendar-browser-') as temp:
        data = Path(temp) / 'private'
        password = secrets.token_urlsafe(24)
        initialize_library(data, 'owner', password, company_profile={'name': '公司日曆隔離驗收'})
        store = Store(data)
        studio = Studio(store)
        studio.save_user({'username': 'staff', 'role': 'member', 'password': 'test-member-password'})
        legacy_task = studio.create({'kind': 'task', 'title': 'CAL-LEGACY-DUE',
            'data': {'due_date': day, 'content': '原有待辦，日期聚合不能複製。'}})['record']
        legacy_project = studio.create({'kind': 'project', 'title': 'CAL-LEGACY-PROJECT',
            'data': {'due_date': day, 'summary': '原有項目的截止日期。'}})['record']
        server = make_server(data)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        errors = []
        try:
            with sync_playwright() as playwright:
                executable = os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser = playwright.chromium.launch(headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage'],
                    **({'executable_path': executable} if executable else {}))
                owner = browser.new_context(viewport={'width': 1440, 'height': 1050},
                    timezone_id='Asia/Hong_Kong',
                    http_credentials={'username': 'owner', 'password': password})
                staff_context = browser.new_context(viewport={'width': 1440, 'height': 1050},
                    timezone_id='Asia/Hong_Kong',
                    http_credentials={'username': 'staff', 'password': 'test-member-password'})

                def api(context, method, path, payload=None):
                    response = context.request.fetch(base + path, method=method,
                        data=payload, headers={'X-Blueprint-Request': '1'})
                    assert response.ok, (method, path, response.status, response.text())
                    return response.json()

                def seed_employee(name, employment_type, username=''):
                    return api(owner, 'POST', '/api/studio/calendar/employees', {
                        'name': name, 'employment_type': employment_type, 'department': '合成團隊',
                        'username': username, 'active': True})['employee']

                owner_employee = seed_employee('CAL-OWNER-員工', 'full_time', 'owner')
                staff_employee = seed_employee('CAL-STAFF-員工', 'part_time', 'staff')
                contractor = seed_employee('CAL-CONTRACT-員工', 'contract')

                def seed_entry(title, kind, employee_id='', context=owner):
                    payload = {'kind': kind, 'title': title, 'employee_id': employee_id,
                               'date': day, 'notes': '只存在隔離測試的合成內容。'}
                    if kind == 'work_log':
                        payload['minutes'] = 35
                    return api(context, 'POST', '/api/studio/calendar/entries', payload)['entry']

                owner_log = seed_entry('CAL-OWNER-PRIVATE-LOG', 'work_log', owner_employee['id'])
                owner_todo = seed_entry('CAL-OWNER-PRIVATE-TODO', 'todo', owner_employee['id'])
                staff_log = seed_entry('CAL-STAFF-LOG', 'work_log', staff_employee['id'])
                staff_todo = seed_entry('CAL-STAFF-TODO', 'todo', staff_employee['id'])
                company_todo = seed_entry('CAL-COMPANY-TODO', 'todo')
                completed = datetime.now(ZoneInfo('UTC')) - timedelta(days=1)
                manual = api(owner, 'POST', '/api/studio/calendar/attendance/manual', {
                    'employee_id': contractor['id'],
                    'started_at': (completed - timedelta(minutes=45)).isoformat(),
                    'ended_at': completed.isoformat(),
                    'request_key': 'browser-contractor-manual',
                    'reason': '隔離驗收：未綁定登入的合約員工補登。'})['attendance']
                assert api(owner, 'GET', '/api/studio/calendar/attendance/' + manual['id'])['attendance']['employee_id'] == contractor['id']

                page = owner.new_page()
                requests = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda request: requests.append(request.url))
                page.goto(base)
                page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('#studio-navigation .studio-primary .studio-nav-label').all_text_contents() == ['總覽', '工作', 'AI', '資料']
                page.locator('#studio-navigation [data-group=work]').click()
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents() == ['項目', '收件匣', '待辦', '工作紀錄', '日曆']
                page.locator('#studio-section-navigation [data-studio-route=calendar]').click()

                def ready(target=page):
                    target.wait_for_selector('#calendar-workspace[data-calendar-ready=true]')

                def agenda(target=page):
                    return target.locator('#calendar-day-agenda')

                def choose_day(target=page):
                    target.locator('[data-calendar-date="' + day + '"]').click()
                    target.wait_for_selector('#calendar-day-agenda[data-date="' + day + '"]')

                def await_post(target, path, work, *, expected_fields=None, forbidden_fields=()):
                    with target.expect_response(lambda response: response.url.split('?')[0] == base + path
                                                and response.request.method == 'POST') as pending:
                        work()
                    response = pending.value
                    assert response.ok, (path, response.status, response.text())
                    submitted = response.request.post_data_json
                    for key, value in (expected_fields or {}).items():
                        assert submitted.get(key) == value, (key, submitted)
                    for key in forbidden_fields:
                        assert key not in submitted, (key, submitted)
                    return response.json()

                ready()
                assert page.evaluate('location.hash') == '#/calendar'
                assert page.locator('#calendar-month').input_value() == month
                choose_day()
                for title in [owner_log['title'], staff_log['title'], company_todo['title']]:
                    agenda().get_by_text(title, exact=True).wait_for(state='visible')
                for record in [legacy_task, legacy_project]:
                    source = agenda().locator('[data-calendar-source-id="' + record['id'] + '"]')
                    assert source.count(), record['title']
                assert page.locator('#calendar-manage-employees').is_visible()
                assert page.locator('#calendar-settings').count() == 1
                for asset in ['style.css', 'studio.css', 'calendar.css']:
                    response = owner.request.get(base + '/' + asset)
                    assert response.status == 200 and response.body() == (ROOT / 'library_web' / asset).read_bytes(), asset

                # Employment type changes the company view, not login authority.
                page.locator('#calendar-employment-type').select_option('part_time')
                agenda().get_by_text(staff_log['title'], exact=True).wait_for(state='visible')
                page.wait_for_function('(title) => !document.getElementById("calendar-day-agenda").textContent.includes(title)', arg=owner_log['title'])
                page.locator('#calendar-employment-type').select_option('')
                agenda().get_by_text(owner_log['title'], exact=True).wait_for(state='visible')
                page.locator('#calendar-employee').select_option(staff_employee['id'])
                page.wait_for_function('(title) => !document.getElementById("calendar-day-agenda").textContent.includes(title)', arg=owner_log['title'])
                assert agenda().get_by_text(staff_log['title'], exact=True).is_visible()
                page.locator('#calendar-employee').select_option('')
                agenda().get_by_text(owner_log['title'], exact=True).wait_for(state='visible')

                # A new employee is saved through the real form, with no login
                # role field masquerading as an employment type.
                page.locator('#calendar-manage-employees').click()
                employee_form = page.locator('[data-calendar-form=employee]')
                employee_form.wait_for(state='visible')
                assert employee_form.locator('[name=role]').count() == 0
                employee_form.locator('[name=name]').fill('CAL-BROWSER-EMPLOYEE')
                employee_form.locator('[name=employment_type]').select_option('temporary')
                employee_form.locator('[name=department]').fill('合成臨時團隊')
                employee_form.locator('[name=username]').select_option('')
                employee_result = await_post(page, '/api/studio/calendar/employees',
                    lambda: employee_form.locator('button[type=submit]').click())['employee']
                assert employee_result['employment_type'] == 'temporary'
                assert api(owner, 'GET', '/api/studio/calendar/employees/' + employee_result['id'])['employee']['name'] == 'CAL-BROWSER-EMPLOYEE'
                page.wait_for_function('() => !document.getElementById("calendar-dialog")?.open')
                all_employees = api(owner, 'GET', '/api/studio/calendar/meta')['employees']
                assert len(all_employees) == 4
                assert {employee['employment_type'] for employee in all_employees} == {'full_time', 'part_time', 'contract', 'temporary'}

                # Save a work record via the actual UI, read it independently,
                # then reopen the page to prove persistence.
                page.locator('#calendar-add-work-log').click()
                form = page.locator('[data-calendar-form=work_log]')
                form.locator('[name=employee_id]').select_option(owner_employee['id'])
                form.locator('[name=title]').fill('CAL-BROWSER-SAVED-LOG')
                form.locator('[name=date]').fill(day)
                form.locator('[name=notes]').fill('瀏覽器正式表單保存，重開仍可讀取。')
                form.locator('[name=minutes]').fill('42')
                saved_log = await_post(page, '/api/studio/calendar/entries',
                    lambda: form.locator('button[type=submit]').click())['entry']
                assert api(owner, 'GET', '/api/studio/calendar/entries/' + saved_log['id'])['entry']['minutes'] == 42
                page.reload()
                page.wait_for_function('() => BlueprintStudio.isReady()')
                ready()
                choose_day()
                agenda().get_by_text('CAL-BROWSER-SAVED-LOG', exact=True).wait_for(state='visible')
                assert page.locator('#calendar-employee option[value="' + employee_result['id'] + '"]').count() == 1

                # Commit the real create, then lose only its response body.
                # Closing/reopening the form must retain the original key and
                # retry the same intent, rather than create a second work log.
                unknown_posts, committed_ids = [], []
                create_url = '**/api/studio/calendar/entries'
                def malformed_create_once(route):
                    if route.request.method != 'POST':
                        route.continue_()
                        return
                    unknown_posts.append(route.request.post_data_json)
                    upstream = route.fetch()
                    assert upstream.ok, upstream.text()
                    committed_ids.append(upstream.json()['entry']['id'])
                    if len(unknown_posts) == 1:
                        route.fulfill(status=201, content_type='application/json', body='{"entry":')
                    else:
                        route.fulfill(response=upstream)
                page.route(create_url, malformed_create_once)
                page.locator('#calendar-add-work-log').click()
                unknown_form = page.locator('[data-calendar-form=work_log]')
                unknown_form.locator('[name=employee_id]').select_option(owner_employee['id'])
                unknown_form.locator('[name=title]').fill('CAL-UNKNOWN-CREATE')
                unknown_form.locator('[name=date]').fill(day)
                unknown_form.locator('[name=notes]').fill('已提交但回應正文遺失，必須沿原請求核對。')
                unknown_form.locator('[name=minutes]').fill('12')
                unknown_form.locator('button[type=submit]').click()
                unknown_form.locator('.form-error:not([hidden])').wait_for(state='visible')
                unknown_form.locator('[data-reconcile]').wait_for(state='visible')
                assert len(unknown_posts) == 1 and unknown_posts[0].get('request_key')
                page.keyboard.press('Escape')
                page.wait_for_function('() => !document.getElementById("calendar-dialog")?.open')
                page.locator('#calendar-add-work-log').click()
                unknown_form = page.locator('[data-calendar-form=work_log]')
                assert unknown_form.locator('[name=title]').input_value() == 'CAL-UNKNOWN-CREATE'
                assert unknown_form.locator('[name=title]').is_disabled()
                unknown_form.locator('[data-reconcile]').click()
                unknown_form.locator('[data-reconcile]').wait_for(state='detached')
                retried = await_post(page, '/api/studio/calendar/entries',
                    lambda: unknown_form.locator('button[type=submit]').click(),
                    expected_fields={'request_key': unknown_posts[0]['request_key']})
                assert retried['idempotent'] and retried['entry']['id'] == committed_ids[0]
                assert len(unknown_posts) == 2 and unknown_posts[0] == unknown_posts[1]
                assert committed_ids == [committed_ids[0], committed_ids[0]]
                page.unroute(create_url, malformed_create_once)
                page.wait_for_function('() => !document.getElementById("calendar-dialog")?.open')
                persisted = api(owner, 'GET', '/api/studio/calendar/month?' + urlencode({'month': month}))
                copies = [entry for date in persisted['days'] for entry in date['work_logs']
                          if entry['title'] == 'CAL-UNKNOWN-CREATE']
                assert len(copies) == 1 and copies[0]['id'] == committed_ids[0]
                agenda().get_by_text('CAL-UNKNOWN-CREATE', exact=True).wait_for(state='visible')

                # Existing calendar todo is updated in place and read back.
                checkbox = page.locator('input[data-calendar-entry-id="' + owner_todo['id'] + '"]')
                with page.expect_response(lambda response: response.url == base + '/api/studio/calendar/entries/' + owner_todo['id']
                                          and response.request.method == 'PATCH') as changed:
                    checkbox.check()
                assert changed.value.ok, changed.value.text()
                updated_todo = api(owner, 'GET', '/api/studio/calendar/entries/' + owner_todo['id'])['entry']
                assert updated_todo['done'] and updated_todo['id'] == owner_todo['id']

                # External writes do not trigger a company-data poll on focus;
                # the calendar's explicit refresh reveals the same saved object.
                external = seed_entry('CAL-EXPLICIT-REFRESH', 'work_log', owner_employee['id'])
                page.evaluate('() => {window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange"));}')
                assert not agenda().get_by_text(external['title'], exact=True).count()
                page.locator('#calendar-refresh').click()
                agenda().get_by_text(external['title'], exact=True).wait_for(state='visible')

                # Idle, focus and refresh must not replace a dirty form. A
                # separate layout edit retains the existing calendar controls.
                page.locator('#calendar-add-work-log').click()
                draft = page.locator('[data-calendar-form=work_log] [name=title]')
                draft.fill('CAL-UNSAVED-DRAFT')
                page.evaluate('() => {window.calendarDraftNode=document.querySelector("[data-calendar-form=work_log] [name=title]");window.dispatchEvent(new Event("focus"));document.dispatchEvent(new Event("visibilitychange"));}')
                before = len([url for url in requests if '/api/studio/calendar/' in url])
                page.wait_for_timeout(2300)
                page.evaluate('() => BlueprintStudio.refreshVisible()')
                assert draft.input_value() == 'CAL-UNSAVED-DRAFT'
                assert page.evaluate('() => calendarDraftNode === document.querySelector("[data-calendar-form=work_log] [name=title]")')
                assert len([url for url in requests if '/api/studio/calendar/' in url]) == before
                page.keyboard.press('Escape')
                page.wait_for_function('() => !document.getElementById("calendar-dialog")?.open')
                page.evaluate('() => window.calendarMonthNode=document.getElementById("calendar-month")')
                layout = page.locator('[data-page-layout-edit=calendar]')
                layout.click()
                assert page.evaluate('() => calendarMonthNode === document.getElementById("calendar-month")')
                assert page.locator('#calendar-month').input_value() == month
                layout.click()

                # A delayed month response must not resurrect a calendar after
                # the user has navigated to another working page.
                previous_month = (today.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
                pending_month = []
                def hold_previous(route):
                    if 'month=' + previous_month in route.request.url:
                        pending_month.append(route)
                    else:
                        route.continue_()
                page.route('**/api/studio/calendar/month?**', hold_previous)
                page.locator('#calendar-month').fill(previous_month)
                page.locator('#calendar-month').dispatch_event('change')
                for _ in range(100):
                    if pending_month:
                        break
                    page.wait_for_timeout(25)
                assert pending_month, 'Hold a real month request before navigating away'
                page.evaluate('() => BlueprintStudio.navigate("projects")')
                page.wait_for_selector('#studio-panel h1:text-is("項目")')
                for route in pending_month:
                    route.continue_()
                page.wait_for_timeout(100)
                assert page.evaluate('location.hash') == '#/projects'
                assert page.locator('#calendar-workspace').count() == 0
                page.unroute('**/api/studio/calendar/month?**', hold_previous)
                page.evaluate('() => BlueprintStudio.navigate("calendar")')
                ready()
                page.locator('#calendar-month').fill(month)
                page.locator('#calendar-month').dispatch_event('change')
                ready()
                choose_day()

                # The staff account receives only bound personal records and
                # shared company todos; denied records are not merely hidden.
                member = staff_context.new_page()
                member.on('pageerror', lambda error: errors.append(str(error)))
                member.goto(base + '/#/calendar')
                member.wait_for_function('() => BlueprintStudio.isReady()')
                ready(member)
                choose_day(member)
                agenda(member).get_by_text(staff_log['title'], exact=True).wait_for(state='visible')
                agenda(member).get_by_text(company_todo['title'], exact=True).wait_for(state='visible')
                assert owner_log['title'] not in member.locator('#calendar-workspace').inner_text()
                assert owner_todo['title'] not in member.locator('#calendar-workspace').inner_text()
                assert not member.locator('#calendar-manage-employees').count()
                assert not member.locator('#calendar-settings').count()
                assert not member.locator('#calendar-add-manual-attendance').count()
                meta = api(staff_context, 'GET', '/api/studio/calendar/meta')
                assert not meta['admin'] and meta['self_employee']['id'] == staff_employee['id']
                assert {employee['id'] for employee in meta['employees']} == {staff_employee['id']}
                for hidden in [owner_log, owner_todo]:
                    denied = staff_context.request.get(base + '/api/studio/calendar/entries/' + hidden['id'])
                    assert denied.status in (403, 404), denied.text()
                filtered = staff_context.request.get(base + '/api/studio/calendar/month?' + urlencode({'month': month, 'employee_id': owner_employee['id']}))
                assert filtered.status in (403, 404), filtered.text()
                pending_todo_readback = []
                todo_url = '**/api/studio/calendar/entries/' + staff_todo['id']
                def hold_todo_readback(route):
                    if route.request.method == 'GET':
                        pending_todo_readback.append(route)
                    else:
                        route.continue_()
                member.route(todo_url, hold_todo_readback)
                own_check = member.locator('input[data-calendar-entry-id="' + staff_todo['id'] + '"]')
                with member.expect_response(lambda response: response.url == base + '/api/studio/calendar/entries/' + staff_todo['id']
                                            and response.request.method == 'PATCH') as done:
                    own_check.check()
                assert done.value.ok, done.value.text()
                for _ in range(100):
                    if pending_todo_readback:
                        break
                    member.wait_for_timeout(25)
                assert pending_todo_readback, 'Hold readback while the todo save is still pending'
                assert member.locator('#calendar-clock').is_disabled(), 'Pending save must not silently swallow an enabled clock action'
                assert api(staff_context, 'GET', '/api/studio/calendar/entries/' + staff_todo['id'])['entry']['done']
                for route in pending_todo_readback:
                    route.continue_()
                member.unroute(todo_url, hold_todo_readback)
                shared_check = member.locator('input[data-calendar-entry-id="' + company_todo['id'] + '"]')
                if shared_check.count():
                    assert shared_check.is_disabled()

                # Clocking uses the server-backed current staff session. The UI
                # turns to clock out only after a successful persistent write.
                started = await_post(member, '/api/studio/calendar/attendance',
                    lambda: member.locator('#calendar-clock[data-action=clock_in]').click(),
                    forbidden_fields=('attendance_id', 'expected_version'))['attendance']
                assert started['employee_id'] == staff_employee['id'] and not started['ended_at']
                current = api(staff_context, 'GET', '/api/studio/calendar/attendance/' + started['id'])['attendance']
                assert current['id'] == started['id'] and current['started_at'] == started['started_at']
                member.locator('#calendar-clock[data-action=clock_out]').wait_for(state='visible')
                ended = await_post(member, '/api/studio/calendar/attendance',
                    lambda: member.locator('#calendar-clock[data-action=clock_out]').click(),
                    expected_fields={'attendance_id': started['id'],
                                     'expected_version': started['version']})['attendance']
                assert ended['id'] == started['id'] and ended['ended_at']
                assert ended['version'] > started['version']
                assert api(staff_context, 'GET', '/api/studio/calendar/attendance/' + ended['id'])['attendance']['ended_at'] == ended['ended_at']
                member.reload()
                member.wait_for_function('() => BlueprintStudio.isReady()')
                ready(member)
                member.locator('#calendar-clock[data-action=clock_in]').wait_for(state='visible')

                page.set_viewport_size({'width': 1440, 'height': 1050})
                page.screenshot(path=str(output / 'calendar-company-desktop.png'), full_page=True)
                for target in [page, member]:
                    target.set_viewport_size({'width': 390, 'height': 844})
                    assert target.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Calendar must fit a 390px viewport'
                    assert target.locator('#calendar-day-agenda').is_visible()
                page.screenshot(path=str(output / 'calendar-company-mobile.png'), full_page=True)
                member.screenshot(path=str(output / 'calendar-personal-mobile.png'), full_page=True)
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
    print('Calendar browser checks passed: preserved navigation/styles, employment types, real employee/work/todo saves, source deadlines, staff authorization, explicit refresh, drafts/layout, stale responses, clock readback and mobile geometry. Synthetic native backend only; no private deployment or live Codex acceptance.')


if __name__ == '__main__':
    main()
