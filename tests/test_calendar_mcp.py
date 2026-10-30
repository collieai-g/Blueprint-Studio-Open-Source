"""Native calendar MCP definitions preserve routes and shared Sites isolation."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from blueprint_library.mcp_server import Bridge, catalog
from blueprint_library.store import LibraryError


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_NAMES = {
    'blueprint_calendar_meta', 'blueprint_calendar_month',
    'blueprint_calendar_entry_create', 'blueprint_calendar_entry_update',
}


class CalendarCatalogTests(unittest.TestCase):
    def test_calendar_is_native_only_and_never_exposes_attendance_writes(self):
        shared = json.loads((ROOT / 'integrations/mcp/tools.json').read_text(encoding='utf-8'))['tools']
        calendar = json.loads((ROOT / 'integrations/mcp/calendar-tools.json').read_text(encoding='utf-8'))['tools']
        native = catalog()
        self.assertEqual(len(shared), 42)
        self.assertTrue(CALENDAR_NAMES.isdisjoint(tool['name'] for tool in shared))
        self.assertEqual({tool['name'] for tool in calendar}, CALENDAR_NAMES)
        self.assertEqual(len(native), 52)
        self.assertEqual(len({tool['name'] for tool in native}), 52)
        self.assertTrue(all('clock' not in tool['name'] and
                            'attendance' not in tool['route']['path'] for tool in calendar))
        self.assertTrue(all(tool['inputSchema']['additionalProperties'] is False
                            for tool in calendar))

    def test_read_only_list_exposes_calendar_reads_and_hides_mutations(self):
        bridge = Bridge('https://native.example', read_only=True)
        names = {tool['name'] for tool in bridge.tools()}
        self.assertTrue({'blueprint_calendar_meta', 'blueprint_calendar_month'} <= names)
        self.assertTrue({'blueprint_calendar_entry_create',
                         'blueprint_calendar_entry_update'}.isdisjoint(names))
        with patch.object(bridge.remote, '_request') as request:
            for name in ('blueprint_calendar_entry_create', 'blueprint_calendar_entry_update'):
                with self.assertRaises(LibraryError) as error:
                    bridge.call(name, {})
                self.assertEqual(error.exception.status, 403)
            request.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'Optional MCP schema validator')
class CalendarBridgeRoutesTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge('https://native.example')

    def test_month_filters_and_meta_reuse_authenticated_read_routes(self):
        args = {'month': '2026-10', 'employee_id': 'employee-1', 'employment_type': 'part_time'}
        result = {'month': args['month'], 'days': [], 'undated_todos': []}
        with patch.object(self.bridge.remote, '_request', return_value=result) as request:
            self.assertIs(self.bridge.call('blueprint_calendar_month', args), result)
            request.assert_called_once_with('GET', '/api/studio/calendar/month', query=args)
        with patch.object(self.bridge.remote, '_request', return_value={'employees': []}) as request:
            self.bridge.call('blueprint_calendar_meta', {})
            request.assert_called_once_with('GET', '/api/studio/calendar/meta', query={})

    def test_create_and_update_preserve_retry_key_ownership_and_version(self):
        args = {'kind': 'work_log', 'title': '整理項目資料', 'date': '2026-10-26',
                'employee_id': 'employee-1', 'notes': '合成測試內容', 'minutes': 35,
                'request_key': 'calendar-mcp-synthetic-1'}
        entry = {'id': 'entry-1', 'version': 1, **args}
        with patch.object(self.bridge.remote, '_request', return_value={'entry': entry}) as request:
            self.assertEqual(self.bridge.call('blueprint_calendar_entry_create', args)['entry'], entry)
            request.assert_called_once_with('POST', '/api/studio/calendar/entries', payload=args)
        update = {'id': entry['id'], 'expected_version': entry['version'], 'notes': '已核對', 'minutes': None}
        with patch.object(self.bridge.remote, '_request', return_value={'entry': {**entry, 'version': 2}}) as request:
            self.bridge.call('blueprint_calendar_entry_update', update)
            request.assert_called_once_with('PATCH', '/api/studio/calendar/entries/entry-1',
                                            payload={'expected_version': 1, 'notes': '已核對', 'minutes': None})
        self.assertEqual(update['id'], entry['id'])

    def test_invalid_shapes_do_not_reach_the_backend(self):
        create = {'kind': 'todo', 'title': '核對紀錄', 'date': '', 'employee_id': '',
                  'request_key': 'calendar-mcp-todo-1'}
        bad = [
            ('blueprint_calendar_meta', {'username': 'another-user'}),
            ('blueprint_calendar_month', {'month': '2026-13'}),
            ('blueprint_calendar_month', {'month': '2026-10', 'employment_type': 'unknown'}),
            ('blueprint_calendar_month', {'month': '2026-10', 'employee_id': '../other'}),
            ('blueprint_calendar_entry_create', {key: value for key, value in create.items() if key != 'request_key'}),
            ('blueprint_calendar_entry_create', {**create, 'kind': 'attendance'}),
            ('blueprint_calendar_entry_create', {**create, 'title': '   '}),
            ('blueprint_calendar_entry_create', {**create, 'minutes': True}),
            ('blueprint_calendar_entry_create', {**create, 'minutes': 1441}),
            ('blueprint_calendar_entry_create', {**create, 'clock_in': '09:00'}),
            ('blueprint_calendar_entry_update', {'id': 'entry-1', 'expected_version': 0, 'done': True}),
            ('blueprint_calendar_entry_update', {'id': '../other', 'expected_version': 1, 'done': True}),
            ('blueprint_calendar_entry_update', {'id': 'entry-1', 'expected_version': 1, 'employee_id': 'other'}),
        ]
        with patch.object(self.bridge.remote, '_request') as request:
            for name, args in bad:
                with self.subTest(name=name, args=args), self.assertRaises(LibraryError):
                    self.bridge.call(name, args)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
