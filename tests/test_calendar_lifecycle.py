"""Synthetic schema-7 upgrades retain existing bytes; calendar data restores whole."""
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blueprint_library import backup
from blueprint_library.calendar import Calendar
from blueprint_library.maintenance import Maintenance
from blueprint_library.server import initialize_library
from blueprint_library.store import Store
from blueprint_library.studio import Studio


class CalendarLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-calendar-lifecycle-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'company'
        initialize_library(self.data, 'owner', 'synthetic-calendar-password')
        self.store = Store(self.data)
        self.auth = (self.data / 'access.json').read_bytes()
        self.file = self.store.add_file('original.txt', b'original schema-7 attachment')

    def test_schema7_rehearsal_is_additive_and_keeps_source_ids_and_credentials(self):
        legacy = Studio(self.store).create({'kind': 'task', 'title': 'Existing deadline',
                                           'data': {'due_date': '2026-10-20', 'done': True}})['record']
        # Reproduce the prior schema using only synthetic data.  No native
        # process is running, and no company calendar data exists to discard.
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            conn.execute('PRAGMA foreign_keys=OFF')
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'calendar_%'")]
            for name in tables:
                conn.execute('DROP TABLE "' + name + '"')
            conn.execute('PRAGMA user_version=7')
        before = self.root / 'before-calendar'
        backup.snapshot(self.data, before)
        runtime = Maintenance(self.data, self.root / 'private-backups',
                              version='2.10.0', fingerprint='synthetic-calendar-upgrade')
        try:
            runtime.prepare()
            self.assertTrue(runtime.status()['last_backup']['restore_verified'])
        finally:
            runtime.close()
        upgraded = Store(self.data)
        self.assertEqual(Studio(upgraded).get(legacy['id']), legacy)
        self.assertEqual(upgraded.get_file(self.file['id'])[0].read_bytes(), b'original schema-7 attachment')
        self.assertEqual((self.data / 'access.json').read_bytes(), self.auth)
        backup.verify(self.data, before)
        with upgraded._connection() as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0], 8)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM calendar_employees').fetchone()[0], 0)

    def test_full_backup_restores_calendar_rows_history_and_retry_receipts(self):
        calendar = Calendar(self.store, {'username': 'owner', 'role': 'owner', 'name': 'Owner'})
        employee_result = calendar.create_employee({'name': 'Synthetic employee',
                'employment_type': 'part_time', 'request_key': 'lifecycle-employee'})
        employee = employee_result['employee']
        entry_result = calendar.create_entry({'kind': 'work_log', 'employee_id': employee['id'],
                'title': 'Completed preparation', 'date': '2026-10-20', 'minutes': 45,
                'request_key': 'lifecycle-entry'})
        entry = entry_result['entry']
        attendance_result = calendar.manual_attendance({'employee_id': employee['id'],
                'started_at': '2026-10-19T23:00:00+00:00',
                'ended_at': '2026-10-20T01:00:00+00:00', 'reason': 'Synthetic signed timesheet',
                'request_key': 'lifecycle-manual'})
        attendance = attendance_result['attendance']
        calendar.update_attendance(attendance['id'], {'expected_version': attendance['version'],
                'started_at': attendance['started_at'], 'ended_at': '2026-10-20T01:10:00+00:00',
                'reason': 'Synthetic reviewed correction'})
        with self.store._connection() as conn:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'calendar_%'")]
            expected = {name: [tuple(row) for row in conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid')]
                        for name in names}
        saved = self.root / 'full-calendar-backup'
        backup.snapshot(self.data, saved)
        restored_path = self.root / 'isolated-restoration'
        backup.restore(saved, restored_path)
        restored = Store(restored_path)
        with restored._connection() as conn:
            for name, rows in expected.items():
                self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid')], rows, name)
        readback = Calendar(restored, {'username': 'owner', 'role': 'owner', 'name': 'Owner'})
        self.assertEqual(readback.get_entry(entry['id'])['entry'], entry)
        self.assertEqual(len(readback.history('attendance', attendance['id'])['versions']), 2)
        self.assertEqual(readback.create_entry({'kind': 'work_log', 'employee_id': employee['id'],
                'title': 'Completed preparation', 'date': '2026-10-20', 'minutes': 45,
                'request_key': 'lifecycle-entry'})['entry']['id'], entry['id'])
        self.assertEqual((restored_path / 'access.json').read_bytes(), self.auth)
        self.assertEqual(restored.get_file(self.file['id'])[0].read_bytes(), b'original schema-7 attachment')


if __name__ == '__main__':
    unittest.main()
