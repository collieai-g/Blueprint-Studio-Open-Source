"""Personal layout durability, version races and authenticated account boundaries."""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from blueprint_library.presentation import MAX_DOCUMENTS
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio


def overview(title='測試排版'):
    return {'version': 1, 'layoutVersion': 4, 'pages': 1, 'columns': 43,
            'mode': 'ai', 'visible': ['heading'],
            'cards': {'heading': {'x': 1, 'y': 0, 'w': 16, 'h': 3, 'collapsed': False}},
            'texts': {'heading': title}}


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-presentation-test-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private'
        initialize_library(self.directory, 'owner', 'fictional-owner-password')
        self.store = Store(self.directory)
        self.owner = Studio(self.store)
        self.member = Studio(self.store, {'username': 'staff', 'role': 'member', 'name': '測試同事'})

    def error(self, status, function, *args, **kwargs):
        with self.assertRaises(LibraryError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.status, status, str(error.exception))

    def save(self, key='overview', version=0, data=None, studio=None):
        return (studio or self.owner).save_presentation({
            'key': key, 'expected_version': version, 'data': overview() if data is None else data})

    def test_persistence_same_account_new_session_and_other_account_isolation(self):
        self.assertEqual(self.owner.presentation(), {'version': 0, 'documents': {}})
        value = self.save()
        later = Studio(Store(self.directory))
        self.assertEqual(later.presentation(), value)
        self.assertEqual(value['documents']['overview']['data']['texts']['heading'], '測試排版')
        self.assertEqual(self.member.presentation(), {'version': 0, 'documents': {}})
        self.save(data=overview('另一個帳戶'), studio=self.member)
        self.assertEqual(self.owner.presentation(), value)
        self.assertNotIn('_presentation', self.owner.preferences()['data'])
        self.assertNotIn('_presentation', self.owner.state()['preferences']['data'])
        self.assertTrue(self.owner.state()['capabilities']['presentation_sync'])

    def test_document_versions_do_not_conflict_across_pages_and_preserve_literal_text(self):
        first = self.save(data=overview('<b>純文字</b>'))
        page = {'version': 1, 'active': True, 'cards': {
            'heading': {'x': 0, 'y': 0, 'w': 20, 'h': 2, 'text': '  保留空白\n第二行  '}}}
        second = self.save('page:projects', data=page)
        self.assertEqual((second['version'], second['documents']['page:projects']['version']), (2, 1))
        self.assertEqual(self.owner.presentation(key='overview')['documents'], first['documents'])
        changed = self.save(version=1, data=overview('第三次'))
        self.assertEqual((changed['version'], changed['documents']['overview']['version']), (3, 2))
        self.assertEqual(self.owner.presentation(key='page:projects')['documents']['page:projects']['data'], page)
        self.assertEqual(self.owner.presentation(since_version=3), {'version': 3, 'unchanged': True})
        self.assertEqual(self.owner.presentation(key='page:projects', since_version=3), second | {'version': 3})
        self.assertEqual(self.owner.presentation(key='page:missing'), {'version': 3, 'documents': {}})
        self.assertIn('documents', self.owner.presentation(since_version=999))

    def test_stale_update_does_not_overwrite_and_no_op_preserves_revision(self):
        first = self.save()
        self.error(409, self.save, version=0, data=overview('過時文字'))
        self.assertEqual(self.owner.presentation(), first)
        self.assertEqual(self.save(version=1), first)
        self.assertEqual(self.owner.presentation(), first)
        reset = self.save(version=1, data={})
        self.assertEqual(reset['documents']['overview'], {'version': 2, 'data': {}})

    def test_racing_writers_have_one_winner_and_distinct_pages_both_survive(self):
        barrier = threading.Barrier(2)
        def race(title):
            barrier.wait()
            try:
                return self.save(data=overview(title))
            except LibraryError as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(race, ['第一邊', '第二邊']))
        self.assertEqual(sum(result == 409 for result in results), 1)
        winner = next(result for result in results if isinstance(result, dict))
        self.assertEqual(self.owner.presentation(), winner)
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda i: self.save('page:record/' + str(i), data={'version': 1}), range(8)))
        state = self.owner.presentation()
        self.assertEqual(state['version'], 9)
        self.assertEqual(len(state['documents']), 9)

    def test_settings_interleave_keeps_latest_layout_and_independent_versions(self):
        before = self.owner.preferences()
        original = self.owner.preferences
        def interleave():
            captured = original()
            self.save(data=overview('設定儲存期間的修改'))
            return captured
        with patch.object(self.owner, 'preferences', side_effect=interleave):
            preferences = self.owner.save_preferences({'expected_version': before['version'], 'data': {'notifications': False}})
        self.assertEqual(preferences['version'], 1)
        self.assertFalse(preferences['data']['notifications'])
        self.assertEqual(self.owner.presentation()['documents']['overview']['data']['texts']['heading'], '設定儲存期間的修改')
        self.save(version=1, data=overview('之後修改'))
        self.assertEqual(self.owner.preferences(), preferences)
        self.assertEqual(self.owner.presentation()['version'], 2)
        self.error(400, self.owner.save_preferences, {'expected_version': 1, 'data': {'_presentation': {}}})

    def test_reference_only_pins_check_current_resource_permissions(self):
        restricted = self.owner.create({'kind': 'project', 'title': '私人資料', 'visibility': 'admin'})['record']
        reference = {'kind': 'studio_record', 'id': restricted['id']}
        data = {'version': 1, 'refs': [reference]}
        self.error(403, self.save, 'pins', data=data, studio=self.member)
        saved = self.save('pins', data=data)
        self.assertEqual(saved['documents']['pins']['data'], data)
        self.assertNotIn('私人資料', json.dumps(saved, ensure_ascii=False))
        self.error(404, self.save, 'pins', version=1, data={'refs': [{'kind': 'item', 'id': 'missing-item'}]})
        self.assertEqual(self.owner.presentation(key='pins'), saved)
        self.save('pins', version=1, data={'refs': [{'kind': 'skill', 'id': 'general'}, {'kind': 'route', 'route': 'projects'}]})

    def test_unknown_fields_payloads_and_malformed_geometry_are_rejected(self):
        invalid = [
            ('overview', {'username': 'other'}), ('overview', {'cards': {'heading': {'w': True}}}),
            ('overview', {'cards': {'heading': {'y': 144}}}), ('overview', {'pages': 4}),
            ('overview', {'columns': 97}), ('overview', {'texts': {'heading': 'x' * 2001}}),
            ('overview', {'texts': {'heading': '\ud800'}}), ('overview', {'texts': {'heading': '\x00'}}),
            ('overview', {'visible': ['heading', 'heading']}), ('overview', {'visible': [{}]}),
            ('overview', {'cards': {'unknown': {'x': 0}}}), ('overview', {'cards': {'heading': {'html': '<b>x</b>'}}}),
            ('page:projects', {'cards': {'constructor': {'text': 'x'}}}),
            ('page:projects', {'cards': {'heading': {'text': 'x' * 4001}}}),
            ('page:projects', {'cards': {'heading': {'visible': 1}}}),
            ('pins', {'refs': [{'kind': 'route', 'route': []}]}),
            ('pins', {'refs': [{'kind': 'skill', 'id': 'unknown'}]}),
            ('pins', {'refs': [{'kind': 'route', 'route': 'projects', 'payload': 'snapshot'}]}),
            ('pins', {'refs': [{'kind': 'route', 'route': 'projects'}] * 2}),
        ]
        for key, data in invalid:
            with self.subTest(key=key, data=str(data)[:100]):
                self.error(400, self.save, key, data=data)
        for key in ['other', 'page:', 'page:../other', 'page:constructor', 'page:a?username=b', 'page:' + 'a' * 301, ['overview']]:
            with self.subTest(key=key):
                self.error(400, self.save, key, data={})
        for version in [True, -1, 0.1, '0', 9007199254740992]:
            self.error(400, self.save, version=version)
        self.assertEqual(self.owner.presentation(), {'version': 0, 'documents': {}})

    def test_document_and_account_limits_fail_without_partial_writes(self):
        data = {'cards': {f'card-{index}': {'text': '測' * 4000} for index in range(12)}}
        self.error(413, self.save, 'page:large', data=data)
        for index in range(MAX_DOCUMENTS):
            self.save('page:' + str(index), data={})
        before = self.owner.presentation()
        self.error(413, self.save, 'page:overflow', data={})
        self.assertEqual(self.owner.presentation(), before)
        # A bounded update of an existing document is still possible at the limit.
        self.save('page:0', version=1, data={'active': True})

    def test_aggregate_byte_limit_preserves_previously_saved_pages(self):
        data = {'cards': {f'card-{index}': {'text': '測' * 4000} for index in range(10)}}
        for index in range(4):
            self.save('page:large-' + str(index), data=data)
        before = self.owner.presentation()
        self.error(413, self.save, 'page:too-many-bytes', data=data)
        self.assertEqual(self.owner.presentation(), before)

    def test_http_identity_csrf_queries_and_user_revocation(self):
        user = self.owner.save_user({'username': 'staff', 'password': 'fictional-staff-password', 'role': 'member'})
        server = make_server(self.directory)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(path='/api/studio/presentation', method='GET', data=None, account='owner', extra=None):
            headers = {'Content-Type': 'application/json', 'X-Blueprint-Request': '1'}
            if account:
                password = 'fictional-owner-password' if account == 'owner' else 'fictional-staff-password'
                headers['Authorization'] = 'Basic ' + base64.b64encode((account + ':' + password).encode()).decode()
            headers.update(extra or {})
            encoded = json.dumps(data).encode() if data is not None else None
            req = Request(f'http://127.0.0.1:{server.server_port}' + path, method=method, headers=headers, data=encoded)
            try:
                with urlopen(req) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                return error.code, json.loads(error.read())
        payload = {'key': 'overview', 'expected_version': 0, 'data': overview()}
        self.assertEqual(request(account=None)[0], 401)
        self.assertEqual(request(method='PATCH', data=payload, extra={'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(request(method='PATCH', data=payload)[0], 200)
        self.assertEqual(request(account='staff')[1], {'version': 0, 'documents': {}})
        # Cookies can switch accounts in another tab. An old tab's layout and
        # expected version must never write into the newly authenticated account.
        old_tab={'X-Blueprint-Account': 'owner'}
        self.assertEqual(request(account='staff', extra=old_tab)[0], 403)
        self.assertEqual(request(account='staff', method='PATCH', data=payload, extra=old_tab)[0], 403)
        self.assertEqual(request(account='staff')[1], {'version': 0, 'documents': {}})
        self.assertEqual(request(extra=old_tab)[0], 200)
        unicode_username="同事's!*()"
        self.owner.save_user({'username': unicode_username, 'password': 'fictional-staff-password', 'role': 'member'})
        unicode_header={'X-Blueprint-Account': quote(unicode_username,safe="~()*!.'-_")}
        self.assertEqual(request(account=unicode_username,extra=unicode_header)[0],200)
        self.assertEqual(request(account=unicode_username,method='PATCH',data=payload,extra=unicode_header)[0],200)
        self.assertEqual(request(account=unicode_username,extra=old_tab)[0],403)
        self.assertEqual(request('/api/studio/presentation?since_version=1')[1], {'version': 1, 'unchanged': True})
        for suffix in ['?username=staff', '?since_version=-1', '?since_version=true', '?since_version=1&since_version=2', '?key=']:
            self.assertEqual(request('/api/studio/presentation' + suffix)[0], 400, suffix)
        self.assertEqual(request(method='PATCH', data=payload)[0], 409)
        self.owner.save_user({'username': 'staff', 'role': 'member', 'enabled': False, 'expected_version': user['version']})
        self.assertEqual(request(account='staff')[0], 401)


if __name__ == '__main__':
    unittest.main()
