"""Characterization of write/readback and validation boundaries before refactoring."""
from copy import deepcopy
import json
import sqlite3
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from blueprint_library.connection import COMPLETE, INITIAL, PREFIX, TITLE, TODO, verify_connection
from blueprint_library.integrations import _schema
from blueprint_library.operations import Operations, _changes
from blueprint_library.presentation import validate_document
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio, _url, default_preferences


class WorkflowBoundariesTests(unittest.TestCase):
    def report(self, *, completed=False):
        return {'id': 'report-1', 'version': 3, 'source_key': PREFIX + 'a' * 32,
                'source': 'Synthetic client', 'date': '2026-10-29', 'title': TITLE,
                'summary': COMPLETE if completed else INITIAL, 'updated_at': 'synthetic-time',
                'entries': [
                    {'id': 'outcome-1', 'type': 'outcome', 'title': TITLE, 'content': 'a' * 32,
                     'url': '', 'tags': [], 'version': 1},
                    {'id': 'todo-1', 'type': 'todo', 'title': TODO, 'content': 'a' * 32,
                     'url': '', 'tags': [], 'version': 2, 'due_date': '', 'done': completed}]}

    @staticmethod
    def arguments():
        return {'challenge': 'a' * 32, 'client': 'Synthetic client', 'date': '2026-10-29'}

    def test_connection_reads_after_each_write_and_uses_current_versions(self):
        pending = self.report()
        todo_done = deepcopy(pending)
        todo_done['entries'][1]['done'] = True
        request = Mock(side_effect=[{'workdays': []}, {'workday': {'id': 'report-1'}},
                                    {'workday': pending}, {}, {'workday': todo_done}, {},
                                    {'workday': self.report(completed=True)}])
        result = verify_connection(request, self.arguments())
        self.assertTrue(result['verified'])
        self.assertEqual(result['workday_id'], 'report-1')
        calls = request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ['GET', 'POST', 'GET', 'PATCH', 'GET', 'PATCH', 'GET'])
        self.assertEqual(calls[3].kwargs['payload'], {'expected_version': 2, 'done': True})
        self.assertEqual(calls[5].kwargs['payload']['expected_version'], 3)
        self.assertEqual(calls[5].kwargs['payload']['entries'][1]['id'], 'todo-1')

    def test_changed_connection_record_stops_before_any_followup_write(self):
        report = self.report()
        report['entries'][1]['content'] = 'unrelated user work'
        request = Mock(side_effect=[{'workdays': [report]}, {'workday': report}])
        with self.assertRaises(LibraryError) as caught:
            verify_connection(request, self.arguments())
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual([call.args[0] for call in request.call_args_list], ['GET', 'GET'])

    def test_connection_write_permission_failure_never_marks_result_complete(self):
        report = self.report()
        request = Mock(side_effect=[{'workdays': [report]}, {'workday': report}, LibraryError('denied', 403)])
        with self.assertRaises(LibraryError) as caught:
            verify_connection(request, self.arguments())
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(request.call_count, 3)

    def test_workspace_urls_keep_optional_blank_and_reject_credentials(self):
        self.assertEqual(_url(''), '')
        self.assertEqual(_url(' https://example.test/path '), 'https://example.test/path')
        for value in ('ftp://example.test/', 'https://user:secret@example.test/', 'https://example.test/a b'):
            with self.subTest(value=value), self.assertRaises(LibraryError):
                _url(value)

    def test_account_preferences_save_preserves_layout_and_other_accounts(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        connection.execute('CREATE TABLE studio_preferences (username TEXT PRIMARY KEY, version INTEGER, data TEXT)')
        saved = {**default_preferences(), '_presentation': {'overview': {'version': 7, 'data': {'heading': 'Synthetic owner'}}}}
        other = {**default_preferences(), '_presentation': {'overview': {'version': 2, 'data': {'heading': 'Synthetic staff'}}}}
        connection.executemany('INSERT INTO studio_preferences VALUES(?,?,?)',
            [('owner', 3, json.dumps(saved)), ('staff', 5, json.dumps(other))])
        connection.commit()
        studio = Studio(SimpleNamespace(_connection=lambda: connection))
        result = studio.save_preferences({'expected_version': 3, 'data': {'notifications': False}})
        self.assertEqual(result['version'], 4)
        self.assertFalse(studio.preferences()['data']['notifications'])
        owner = connection.execute('SELECT data FROM studio_preferences WHERE username=?', ('owner',)).fetchone()
        staff = connection.execute('SELECT version,data FROM studio_preferences WHERE username=?', ('staff',)).fetchone()
        self.assertEqual(json.loads(owner['data'])['_presentation'], saved['_presentation'])
        self.assertEqual((staff['version'], json.loads(staff['data'])), (5, other))
        with self.assertRaises(LibraryError) as caught:
            studio.save_preferences({'expected_version': 3, 'data': {'notifications': True}})
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(studio.preferences()['data']['notifications'])

    def test_integration_schema_keeps_boolean_integer_and_enum_types_distinct(self):
        schema = {'type': 'object', 'additionalProperties': False,
                  'required': ['count'], 'properties': {'count': {'type': 'integer', 'enum': [1, 2]}}}
        self.assertEqual(_schema(schema), schema)
        for value in (True, 1.5, '1'):
            changed = deepcopy(schema)
            changed['properties']['count']['enum'] = [value]
            with self.subTest(value=value), self.assertRaises(LibraryError):
                _schema(changed)

    def test_operation_retry_reads_original_receipt_and_retains_latest_stage(self):
        ledger = Operations.__new__(Operations)
        ledger.lock = threading.RLock()
        ledger.path = 'synthetic-in-memory-ledger'
        document = {'format_version': 1, 'operations': {}}
        ledger._read = lambda: deepcopy(document)

        def persist(path, value):
            document.clear()
            document.update(deepcopy(value))

        first = {'contract_version': 1, 'operation_id': 'synthetic-op', 'plan_digest': 'a' * 64,
                 'request_id': 'discover', 'expected_version': 0,
                 'changes': {'change_type': 'source_release', 'target_services': [], 'stage': 'discover'}}
        next_step = {**first, 'request_id': 'plan', 'expected_version': 1, 'changes': {'stage': 'plan'}}
        with patch('blueprint_library.maintenance.atomic_json', side_effect=persist):
            saved = ledger.write(first, 'owner')
            current = ledger.write(next_step, 'owner')
            self.assertEqual(ledger.write(first, 'owner'), saved)
            self.assertEqual(ledger.get('synthetic-op', 'owner'), current)
            self.assertEqual(current['operation']['version'], 2)
            with self.assertRaises(LibraryError) as caught:
                ledger.write({**next_step, 'request_id': 'stale'}, 'owner')
            self.assertEqual(caught.exception.status, 409)
            with self.assertRaises(LibraryError) as caught:
                ledger.write(first, 'different-owner')
            self.assertEqual(caught.exception.status, 404)

    def test_operation_evidence_keeps_json_bounds_and_credential_rejection(self):
        value = {'sources': {'upstream_commit': 'a' * 40}, 'evidence': {'checks': ['passed']}}
        result = _changes(value)
        self.assertEqual(result, value)
        result['evidence']['checks'].append('only-copy')
        self.assertEqual(value['evidence']['checks'], ['passed'])
        for evidence in ({'nested': {'authorization': 'synthetic-canary'}}, {'value': float('nan')}):
            with self.subTest(evidence=evidence), self.assertRaises(LibraryError):
                _changes({'evidence': evidence})

    def test_presentation_validation_preserves_literal_text_and_no_defaults(self):
        document = {'version': 1, 'texts': {'heading': '  Synthetic title  '},
                    'cards': {'heading': {'x': 2, 'w': 12, 'collapsed': False}}}
        self.assertIs(validate_document('overview', document), document)
        self.assertEqual(document['texts']['heading'], '  Synthetic title  ')
        self.assertNotIn('pages', document)
        for change in ({'cards': {'heading': {'x': True}}}, {'texts': {'heading': 'bad\x00value'}},
                       {'visible': ['heading', 'heading']}):
            with self.subTest(change=change), self.assertRaises(LibraryError):
                validate_document('overview', change)

    def test_workday_refinement_keeps_completed_todo_identity_and_history(self):
        connection = sqlite3.connect(':memory:')
        self.addCleanup(connection.close)
        connection.row_factory = sqlite3.Row
        store = Store.__new__(Store)
        store._connection = lambda: connection
        store._initialize()
        imported = store.import_workday({'date': '2026-10-29', 'source': 'Synthetic client',
            'source_key': 'synthetic-refinement', 'title': 'Synthetic report',
            'entries': [{'type': 'todo', 'title': 'Keep completion', 'content': 'Before'}]})['workday']
        original = imported['entries'][0]
        store.update_todo(original['id'], {'expected_version': original['version'], 'done': True})
        current = store.get_workday(imported['id'])
        entry = current['entries'][0]
        payload = {'expected_version': current['version'], 'summary': 'Refined',
                   'entries': [{key: entry[key] for key in ('id', 'type', 'title', 'url', 'tags', 'due_date')}
                               | {'content': 'After'}]}
        updated = store.update_workday(current['id'], payload)
        saved = updated['workday']['entries'][0]
        self.assertTrue(updated['updated'])
        self.assertEqual(saved['id'], original['id'])
        self.assertTrue(saved['done'])
        self.assertEqual(saved['completed_at'], entry['completed_at'])
        self.assertEqual(saved['created_at'], original['created_at'])
        replay = {**payload, 'expected_version': updated['workday']['version']}
        history_count = len(store.item_workday_history(current['id']))
        self.assertFalse(store.update_workday(current['id'], replay)['updated'])
        self.assertEqual(len(store.item_workday_history(current['id'])), history_count)
        with self.assertRaises(LibraryError) as caught:
            store.update_workday(current['id'], payload)
        self.assertEqual(caught.exception.status, 409)


if __name__ == '__main__':
    unittest.main()
