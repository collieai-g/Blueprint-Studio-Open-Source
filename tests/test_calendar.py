"""Calendar persistence, employee identity and concurrent attendance acceptance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blueprint_library import backup
from blueprint_library.calendar import Calendar
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-calendar-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / 'company'
        initialize_library(self.directory, 'owner', 'fictional-calendar-password')
        self.store = Store(self.directory)
        self.owner_identity = {'username': 'owner', 'name': 'Owner', 'role': 'owner'}
        self.staff_identity = {'username': 'staff', 'name': 'Staff', 'role': 'member'}
        self.owner = Calendar(self.store, self.owner_identity)
        studio = Studio(self.store, self.owner_identity)
        studio.save_user({'username': 'staff', 'name': 'Staff', 'role': 'member', 'password': 'fictional-staff-password'})
        studio.save_user({'username': 'other', 'name': 'Other', 'role': 'member', 'password': 'fictional-other-password'})
        self.owner_employee = self.owner.create_employee({'name': 'Owner', 'username': 'owner', 'employment_type': 'full_time'})['employee']
        self.staff_employee = self.owner.create_employee({'name': 'Staff', 'username': 'staff', 'employment_type': 'part_time'})['employee']
        self.contractor = self.owner.create_employee({'name': 'No login', 'employment_type': 'contract'})['employee']
        self.staff = Calendar(self.store, self.staff_identity)

    def error(self, status, function, *args, **kwargs):
        with self.assertRaises(LibraryError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, str(caught.exception))

    @staticmethod
    def instant(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    def entry(self, kind='work_log', employee_id=None, **changes):
        payload = {'kind': kind, 'employee_id': employee_id if employee_id is not None else self.staff_employee['id'],
                   'title': 'Work', 'date': '2026-10-26', 'notes': 'Synthetic work'}
        payload.update(changes)
        return self.owner.create_entry(payload)['entry']

    def manual(self, employee_id=None, start='2026-10-26T01:00:00Z', end='2026-10-26T02:00:00Z', key='manual-one'):
        return self.owner.manual_attendance({'employee_id': employee_id or self.contractor['id'],
            'started_at': start, 'ended_at': end, 'reason': 'Verified synthetic correction', 'request_key': key})['attendance']

    def clock(self, action, key, attendance=None, when='2026-10-26T01:00:00Z'):
        payload = {'action': action, 'request_key': key}
        if attendance:
            payload.update(attendance_id=attendance['id'], expected_version=attendance['version'])
        with patch('blueprint_library.calendar._now', return_value=self.instant(when)):
            return self.staff.attendance_action(payload)

    def test_employee_bindings_do_not_grant_login_or_transfer_history(self):
        meta = self.staff.meta()
        self.assertEqual(meta['accounts'], [])
        self.assertEqual([value['id'] for value in meta['employees']], [self.staff_employee['id']])
        self.assertEqual(meta['self_employee']['id'], self.staff_employee['id'])
        self.assertIn('owner', {account['username'] for account in self.owner.meta()['accounts']})
        self.assertEqual(self.contractor['username'], '')
        self.error(400, self.owner.create_employee, {'name': 'Missing account', 'username': 'nonexistent'})
        self.error(409, self.owner.create_employee, {'name': 'Duplicate account', 'username': 'staff'})
        self.error(409, self.owner.update_employee, self.staff_employee['id'], {'expected_version': 1, 'username': 'other'})
        self.error(409, self.owner.update_employee, self.staff_employee['id'], {'expected_version': 1, 'username': ''})
        self.error(403, self.staff.get_employee, self.owner_employee['id'])
        self.error(403, self.staff.create_employee, {'name': 'Forbidden'})

    def test_employee_and_entry_creation_receipts_survive_restart_and_conflict(self):
        employee_payload = {'name': 'Receipt employee', 'request_key': 'employee-create'}
        first = self.owner.create_employee(employee_payload)
        restarted = Calendar(Store(self.directory), self.owner_identity)
        second = restarted.create_employee(employee_payload)
        self.assertEqual(first['employee'], second['employee'])
        self.assertTrue(second['idempotent'])
        self.error(409, restarted.create_employee, {**employee_payload, 'name': 'Changed input'})
        payload = {'kind': 'todo', 'title': 'Company retry', 'date': '', 'employee_id': '', 'request_key': 'entry-create'}
        created = self.owner.create_entry(payload)
        self.assertEqual(created['entry'], restarted.create_entry(payload)['entry'])
        self.error(409, restarted.create_entry, {**payload, 'title': 'Another intent'})
        self.error(409, restarted.create_employee, {'name': 'Wrong operation', 'request_key': 'entry-create'})

    def test_members_only_write_own_logs_and_toggle_own_assigned_todo(self):
        own = self.staff.create_entry({'kind': 'work_log', 'employee_id': self.staff_employee['id'],
                                       'title': 'My work', 'date': '2026-10-26', 'minutes': 45})['entry']
        saved = self.staff.update_entry(own['id'], {'expected_version': own['version'], 'notes': 'Updated'})['entry']
        self.assertEqual(saved['minutes'], 45)
        self.error(409, self.staff.update_entry, own['id'], {'expected_version': own['version'], 'notes': 'Stale'})
        other = self.entry(employee_id=self.owner_employee['id'], title='Owner private work')
        self.error(403, self.staff.get_entry, other['id'])
        self.error(403, self.staff.history, 'entry', other['id'])
        self.error(403, self.staff.create_entry, {'kind': 'work_log', 'employee_id': self.owner_employee['id'], 'title': 'Not mine', 'date': '2026-10-26'})
        self.error(403, self.staff.create_entry, {'kind': 'todo', 'employee_id': self.staff_employee['id'], 'title': 'No self assign', 'date': ''})
        todo = self.entry('todo')
        self.error(403, self.staff.update_entry, todo['id'], {'expected_version': 1, 'title': 'Cannot rewrite assignment'})
        done = self.staff.update_entry(todo['id'], {'expected_version': 1, 'done': True})['entry']
        self.assertTrue(done['done'])
        company = self.entry('todo', employee_id='', date='')
        self.assertEqual(self.staff.get_entry(company['id'])['entry']['employee_id'], '')
        self.error(403, self.staff.update_entry, company['id'], {'expected_version': 1, 'done': True})
        self.error(403, self.staff.history, 'entry', company['id'])
        self.error(409, self.owner.update_entry, todo['id'], {'expected_version': done['version'], 'employee_id': self.owner_employee['id']})
        self.assertEqual(len(self.staff.history('entry', own['id'])['versions']), 2)

    def test_clock_is_server_timed_idempotent_and_has_single_concurrent_winner(self):
        when = self.instant('2026-10-26T01:00:00Z')
        with patch('blueprint_library.calendar._now', return_value=when):
            def attempt(key):
                try:
                    return self.staff.attendance_action({'action': 'clock_in', 'request_key': key})['attendance']
                except LibraryError as error:
                    return error.status
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(attempt, ['start-one', 'start-two']))
        sessions = [value for value in results if isinstance(value, dict)]
        self.assertEqual(len(sessions), 1)
        self.assertIn(409, results)
        started = sessions[0]
        self.assertEqual(started['started_at'], '2026-10-26T01:00:00.000000Z')
        self.assertEqual(started['timezone'], 'Asia/Hong_Kong')
        winning_key = 'start-one' if isinstance(results[0], dict) else 'start-two'
        restarted = Calendar(Store(self.directory), self.staff_identity)
        replay = restarted.attendance_action({'action': 'clock_in', 'request_key': winning_key})
        self.assertEqual(replay['attendance']['id'], started['id'])
        self.assertTrue(replay['idempotent'])
        self.error(400, self.staff.attendance_action, {'action': 'clock_in', 'request_key': 'wrong', 'employee_id': self.owner_employee['id']})
        ended = self.clock('clock_out', 'out-one', started, when='2026-10-26T03:00:00Z')['attendance']
        self.assertIsNone(self.staff.meta()['current_attendance'])
        repeated = self.clock('clock_out', 'out-one', started, when='2026-10-26T04:00:00Z')
        self.assertEqual(repeated['attendance'], ended)
        self.assertEqual(len(self.staff.history('attendance', started['id'])['versions']), 2)

    def test_stale_session_id_cannot_close_new_session_with_same_version(self):
        old = self.clock('clock_in', 'old-start')['attendance']
        self.clock('clock_out', 'old-end', old, when='2026-10-26T02:00:00Z')
        new = self.clock('clock_in', 'new-start', when='2026-10-26T03:00:00Z')['attendance']
        self.assertEqual(old['version'], new['version'])
        self.error(409, self.clock, 'clock_out', 'stale-end', old, when='2026-10-26T04:00:00Z')
        self.assertEqual(self.staff.meta()['current_attendance']['id'], new['id'])
        inactive = self.owner.update_employee(self.staff_employee['id'], {'expected_version': 1, 'active': False})['employee']
        self.assertFalse(inactive['active'])
        self.assertIsNotNone(self.clock('clock_out', 'new-end', new, when='2026-10-26T04:00:00Z')['attendance']['ended_at'])
        self.error(409, self.clock, 'clock_in', 'inactive-start', when='2026-10-26T05:00:00Z')

    def test_manual_attendance_overlap_correction_history_and_self_acl(self):
        attendance = self.manual()
        self.error(409, self.manual, start='2026-10-26T01:30:00Z', end='2026-10-26T03:00:00Z', key='overlap')
        adjacent = self.manual(start='2026-10-26T02:00:00Z', end='2026-10-26T03:00:00Z', key='adjacent')
        self.assertNotEqual(adjacent['id'], attendance['id'])
        self.error(403, self.staff.get_attendance, attendance['id'])
        self.error(403, self.staff.history, 'attendance', attendance['id'])
        payload = {'started_at': '2026-10-26T00:30:00Z', 'ended_at': '2026-10-26T02:00:00Z',
                   'expected_version': 1, 'reason': 'Confirmed actual start'}
        corrected = self.owner.update_attendance(attendance['id'], payload)['attendance']
        self.assertEqual(corrected['version'], 2)
        self.error(409, self.owner.update_attendance, attendance['id'], payload)
        self.error(400, self.owner.update_attendance, attendance['id'], {**payload, 'expected_version': 2, 'reason': ''})
        self.error(400, self.owner.update_attendance, attendance['id'], {**payload, 'expected_version': 2, 'started_at': '2026-10-26T04:00:00Z'})
        self.error(403, self.staff.update_attendance, attendance['id'], payload)
        versions = self.owner.history('attendance', attendance['id'])['versions']
        self.assertEqual([value['version'] for value in versions], [2, 1])
        self.assertEqual(versions[0]['change_reason'], 'Confirmed actual start')

    def test_month_splits_cross_midnight_and_preserves_record_timezone(self):
        attendance = self.manual(start='2026-10-25T15:30:00Z', end='2026-10-25T17:30:00Z')
        result = self.owner.month('2026-10', employee_id=self.contractor['id'])
        days = {day['date']: day for day in result['days']}
        first = days['2026-10-25']['attendance'][0]
        second = days['2026-10-26']['attendance'][0]
        self.assertEqual((first['minutes'], second['minutes']), (30, 90))
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(result['summary']['attendance_sessions'], 1)
        self.owner.save_settings({'timezone': 'UTC', 'expected_version': 1})
        current = self.owner.month('2026-10', employee_id=self.contractor['id'])
        self.assertEqual(sum(len(day['attendance']) for day in current['days']), 1)
        self.assertEqual(self.owner.get_attendance(attendance['id'])['attendance']['timezone'], 'Asia/Hong_Kong')
        self.error(409, self.owner.save_settings, {'timezone': 'UTC', 'expected_version': 1})
        self.error(400, self.owner.save_settings, {'timezone': 'Not/A_Real_Zone', 'expected_version': 2})

    def test_dst_minutes_use_elapsed_utc_time_instead_of_wall_clock(self):
        self.owner.save_settings({'timezone': 'America/New_York', 'expected_version': 1})
        self.manual(start='2026-03-08T05:00:00Z', end='2026-03-09T04:00:00Z')
        march = self.owner.month('2026-03', employee_id=self.contractor['id'])
        day = next(value for value in march['days'] if value['date'] == '2026-03-08')
        self.assertEqual(day['attendance'][0]['minutes'], 23 * 60)

    def test_month_reads_full_range_and_employee_filters_exclude_unassigned_company_items(self):
        for index in range(105):
            self.entry(title='Staff work ' + str(index))
        self.entry(employee_id=self.owner_employee['id'], title='Owner private work')
        company = self.entry('todo', employee_id='', title='Company todo')
        undated = self.entry('todo', employee_id='', title='Undated company todo', date='')
        Studio(self.store, self.owner_identity).create({'kind': 'task', 'title': 'Legacy company task', 'data': {'due_date': '2026-10-26'}})
        full = self.staff.month('2026-10')
        self.assertEqual(full['summary']['work_logs'], 105)
        self.assertEqual(len(full['days']), 31)
        self.assertIn(company['id'], {entry['id'] for day in full['days'] for entry in day['todos']})
        self.assertEqual(full['undated_todos'][0]['id'], undated['id'])
        self.assertNotIn('Owner private work', json.dumps(full))
        own = self.owner.month('2026-10', employee_id=self.staff_employee['id'])
        typed = self.owner.month('2026-10', employment_type='part_time')
        for filtered in (own, typed):
            self.assertFalse(filtered['company_items_included'])
            self.assertEqual(filtered['summary']['work_logs'], 105)
            self.assertEqual(filtered['summary']['todos'], 0)
            self.assertEqual(filtered['undated_todos'], [])
            self.assertNotIn('Legacy company task', json.dumps(filtered))
        self.error(403, self.staff.month, '2026-10', employee_id=self.owner_employee['id'])

    def test_backup_restore_preserves_entries_employee_binding_receipts_and_history(self):
        attendance = self.manual()
        entry = self.entry()
        snapshot, restored = self.root / 'snapshot', self.root / 'restored'
        backup.snapshot(self.directory, snapshot)
        backup.restore(snapshot, restored)
        service = Calendar(Store(restored), self.owner_identity)
        self.assertEqual(service.get_entry(entry['id']), self.owner.get_entry(entry['id']))
        self.assertEqual(service.history('attendance', attendance['id']), self.owner.history('attendance', attendance['id']))
        self.assertEqual(service.meta()['employees'], self.owner.meta()['employees'])
        replay = service.manual_attendance({'employee_id': self.contractor['id'], 'started_at': '2026-10-26T01:00:00Z',
            'ended_at': '2026-10-26T02:00:00Z', 'reason': 'Verified synthetic correction', 'request_key': 'manual-one'})
        self.assertEqual(replay['attendance']['id'], attendance['id'])
        self.assertTrue(replay['idempotent'])

    def test_invalid_types_months_and_fields_do_not_create_records(self):
        for month in ['2026-13', '2026-1', '0000-01', '2026-10-01', None]:
            self.error(400, self.owner.month, month)
        self.error(400, self.owner.month, '2026-10', employment_type='admin')
        self.error(400, self.owner.create_entry, {'kind': [], 'title': 'Invalid', 'date': ''})
        self.error(400, self.staff.attendance_action, {'action': {}, 'request_key': 'invalid-action'})
        self.error(400, self.owner.create_entry, {'kind': 'work_log', 'employee_id': self.contractor['id'], 'title': 'Invalid', 'date': '2026-10-26', 'minutes': True})
        self.error(400, self.owner.create_entry, {'kind': 'todo', 'title': 'Invalid', 'date': '', 'done': 'true'})
        self.error(400, self.owner.manual_attendance, {'employee_id': self.contractor['id'], 'started_at': '2026-10-26T00:00:00', 'ended_at': None, 'reason': 'No timezone', 'request_key': 'no-zone'})
        with self.store._connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calendar_entries').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calendar_attendance').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
