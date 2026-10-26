"""Real HTTP calendar authorization, stale-account tabs and input boundaries."""
import base64
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from blueprint_library.calendar import Calendar
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio


class CalendarHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-calendar-http-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'company'
        self.password = 'fictional-calendar-http-password'
        initialize_library(self.directory, 'owner', self.password)
        self.store = Store(self.directory)
        self.identity = {'username': 'owner', 'name': 'Owner', 'role': 'owner'}
        studio = Studio(self.store, self.identity)
        for username in ('staff', '同事'):
            studio.save_user({'username': username, 'name': username, 'role': 'member', 'password': self.password})
        calendar = Calendar(self.store, self.identity)
        self.owner_employee = calendar.create_employee({'name': 'Owner', 'username': 'owner'})['employee']
        self.staff_employee = calendar.create_employee({'name': 'Staff', 'username': 'staff'})['employee']
        calendar.create_employee({'name': 'Unicode staff', 'username': '同事'})
        self.server = make_server(self.directory)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, suffix, payload=None, *, username='owner', account=None, headers=None):
        credential = base64.b64encode((username + ':' + self.password).encode()).decode()
        values = {'Authorization': 'Basic ' + credential}
        if account is not None:
            values['X-Blueprint-Account'] = account
        if payload is not None:
            values.update({'Content-Type': 'application/json', 'X-Blueprint-Request': '1'})
        values.update(headers or {})
        request = Request(self.url + '/api/studio/calendar' + suffix, method=method,
                          data=json.dumps(payload).encode() if payload is not None else None, headers=values)
        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            with error:
                return error.code, json.load(error)

    def test_authenticated_identity_rejects_stale_account_assertions_and_allows_mcp_omission(self):
        for username, wrong in [('owner', 'staff'), ('staff', 'owner')]:
            status, _ = self.request('GET', '/meta', username=username, account=wrong)
            self.assertEqual(status, 403)
            status, result = self.request('GET', '/meta', username=username, account=username)
            self.assertEqual(status, 200)
            self.assertEqual(result['identity']['username'], username)
            status, result = self.request('GET', '/meta', username=username)
            self.assertEqual(status, 200)
            self.assertEqual(result['identity']['username'], username)
        status, result = self.request('GET', '/meta', username='同事', account=quote('同事', safe="~()*!.'-_"))
        self.assertEqual(status, 200)
        self.assertEqual(result['identity']['username'], '同事')

    def test_stale_tab_and_cross_origin_clock_actions_never_create_attendance(self):
        payload = {'action': 'clock_in', 'request_key': 'http-clock'}
        status, _ = self.request('POST', '/attendance', payload, username='staff', account='owner')
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/attendance', payload, username='staff', account='staff',
                                 headers={'Origin': 'https://other.example'})
        self.assertEqual(status, 403)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calendar_attendance').fetchone()[0], 0)
        status, result = self.request('POST', '/attendance', payload, username='staff', account='staff',
                                      headers={'Origin': self.url})
        self.assertEqual(status, 201)
        identifier = result['attendance']['id']
        status, read = self.request('GET', '/attendance/' + identifier, username='staff', account='staff')
        self.assertEqual(status, 200)
        self.assertEqual(read['attendance']['employee_id'], self.staff_employee['id'])
        status, _ = self.request('GET', '/attendance/' + identifier, username='同事', account=quote('同事'))
        self.assertEqual(status, 403)

    def test_strict_query_validation_and_unhashable_actions_are_safe_errors(self):
        for suffix in ('/meta?unexpected=1', '/month?month=2026-10&month=2026-11',
                       '/month?month=2026-10&extra=1', '/month', '/history?kind=entry&id=x&id=y'):
            status, _ = self.request('GET', suffix)
            self.assertEqual(status, 400, suffix)
        for action in ({}, [], 1, None):
            status, _ = self.request('POST', '/attendance', {'action': action, 'request_key': 'invalid'}, username='staff')
            self.assertEqual(status, 400)
        status, _ = self.request('POST', '/entries', {'kind': [], 'title': 'Invalid', 'date': ''})
        self.assertEqual(status, 400)
        with self.store._connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calendar_receipts').fetchone()[0], 0)

    def test_employee_work_entries_are_filtered_and_read_back_by_same_identity(self):
        payload = {'kind': 'work_log', 'employee_id': self.staff_employee['id'], 'title': 'Staff result',
                   'date': '2026-10-26', 'notes': 'Synthetic work', 'request_key': 'staff-log'}
        status, result = self.request('POST', '/entries', payload, username='staff', account='staff')
        self.assertEqual(status, 201)
        identifier = result['entry']['id']
        status, own = self.request('GET', '/entries/' + identifier, username='staff', account='staff')
        self.assertEqual(status, 200)
        self.assertEqual(own['entry']['title'], 'Staff result')
        status, _ = self.request('GET', '/entries/' + identifier, username='同事', account=quote('同事'))
        self.assertEqual(status, 403)
        status, _ = self.request('GET', '/month?month=2026-10&employee_id=' + self.owner_employee['id'], username='staff')
        self.assertEqual(status, 403)
        status, result = self.request('GET', '/month?month=2026-10', username='staff')
        self.assertEqual(status, 200)
        self.assertEqual(result['summary']['work_logs'], 1)


if __name__ == '__main__':
    unittest.main()
