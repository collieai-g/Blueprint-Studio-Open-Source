"""Legacy calendar reads retain source dates, permissions and current state."""
from datetime import date
from pathlib import Path
import tempfile
import unittest

from blueprint_library.calendar_sources import aggregate
from blueprint_library.store import LibraryError, Store
from blueprint_library.studio import Studio


class CalendarSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-calendar-sources-')
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'company')
        self.owner = Studio(self.store)
        self.member = Studio(self.store, {'username': 'staff', 'role': 'member'})

    def record(self, kind='task', title='合成工作', **data):
        return self.owner.create({'kind': kind, 'title': title, 'data': data})['record']

    def report(self, key, day='2026-10-10', due_date='2026-10-20', **overrides):
        entry = {'type': 'todo', 'title': '合成待辦 ' + key}
        if due_date is not None:
            entry['due_date'] = due_date
        payload = {'date': day, 'source': 'Synthetic test', 'source_key': key,
                   'title': '合成工作紀錄 ' + key, 'entries': [entry], **overrides}
        return self.store.import_workday(payload, actor='staff')['workday']

    def events(self, studio=None, start='2026-10-01', end='2026-11-01',
               timezone='Asia/Hong_Kong'):
        with self.store._connection() as conn:
            conn.execute('BEGIN')
            return aggregate(studio or self.owner, conn, start, end, timezone)

    def test_due_dates_and_workday_dates_are_distinct_civil_days(self):
        task = self.record(due_date='2026-10-01', owner='staff')
        project = self.record('project', due_date='2026-10-31', owner='staff')
        self.record(due_date='2026-11-01')
        self.record(due_date='2026-09-30')
        self.record(title='沒有期限')
        before = self.report('previous-month', day='2026-09-30', due_date='2026-10-01')
        last = self.report('last-day', day='2026-10-31', due_date='2026-11-01')
        undated = self.report('undated', due_date=None)
        events = self.events()
        by_id = {event['id']: event for event in events}
        self.assertEqual(set(by_id), {
            task['id'], project['id'], before['entries'][0]['id'], last['id'], undated['id']})
        self.assertEqual(by_id[before['entries'][0]['id']]['date'], '2026-10-01')
        self.assertEqual(by_id[last['id']]['date'], '2026-10-31')
        self.assertEqual(by_id[task['id']]['route'], 'record/' + task['id'])
        self.assertEqual(by_id[before['entries'][0]['id']]['open_report'], before['id'])
        for event in events:
            self.assertEqual((event['scope'], event['assignment']), ('company', 'unassigned'))
            self.assertNotIn('employee_id', event)
        # Neither the free-text task owner nor report author assigns an employee.
        self.assertEqual(self.events(timezone='Pacific/Kiritimati'), events)
        self.assertEqual(self.events(timezone='America/Los_Angeles'), events)
        self.assertEqual(self.events(start=date(2026, 10, 1), end=date(2026, 11, 1)), events)

    def test_full_range_has_no_first_page_limit(self):
        task_ids, report_ids, todo_ids = set(), set(), set()
        for index in range(115):
            task_ids.add(self.record(title=f'完整範圍工作 {index}', due_date='2026-10-22')['id'])
            report = self.report(f'full-range-{index}')
            report_ids.add(report['id'])
            todo_ids.add(report['entries'][0]['id'])
        project = self.record('project', due_date='2026-10-22')
        events = self.events()
        self.assertEqual(len(events), 346)
        self.assertEqual({event['id'] for event in events if event['source_kind'] == 'task'}, task_ids)
        self.assertEqual({event['id'] for event in events if event['source_kind'] == 'workday'}, report_ids)
        self.assertEqual({event['id'] for event in events if event['source_kind'] == 'todo'}, todo_ids)
        self.assertIn(project['id'], {event['id'] for event in events})

    def test_member_respects_record_visibility_and_workday_and_todo_acl(self):
        public = self.record(due_date='2026-10-20')
        private = self.owner.create({'kind': 'project', 'title': '私人項目',
                                     'data': {'due_date': '2026-10-20'},
                                     'visibility': 'admin'})['record']
        report = self.report('private-report')
        self.owner.create({'kind': 'member', 'title': '合成人事引用',
                           'refs': [{'kind': 'workday', 'id': report['id']}]})
        todo_report = self.report('private-todo')
        todo_id = todo_report['entries'][0]['id']
        # Existing ACL storage can protect a todo independently of its parent.
        with self.store._connection() as conn:
            conn.execute("INSERT INTO studio_resource_acl VALUES('todo',?)", (todo_id,))
        inherited = self.owner.create({'kind': 'task', 'title': '沿用私人來源的工作',
                                       'data': {'due_date': '2026-10-20'},
                                       'refs': [{'kind': 'workday', 'id': report['id']}]})['record']
        member_ids = {event['id'] for event in self.events(self.member)}
        owner_ids = {event['id'] for event in self.events()}
        self.assertEqual(member_ids, {public['id'], todo_report['id']})
        for hidden_id in (private['id'], report['id'], report['entries'][0]['id'],
                          todo_id, inherited['id']):
            self.assertNotIn(hidden_id, member_ids)
            self.assertIn(hidden_id, owner_ids)

    def test_archives_and_unrelated_restricted_due_dates_are_excluded(self):
        task = self.record(due_date='2026-10-20')
        project = self.record('project', due_date='2026-10-20')
        for record in (task, project):
            self.owner.update(record['id'], {'expected_version': 1, 'archived': True})
        self.record('invoice', due_date='2026-10-20')
        self.assertEqual(self.events(), [])

    def test_reads_current_completion_and_never_mutates_sources(self):
        task = self.record(due_date='2026-10-20')
        report = self.report('completion')
        todo = report['entries'][0]
        before = {event['id']: event for event in self.events()}
        self.assertIs(before[task['id']]['done'], False)
        self.assertIs(before[todo['id']]['done'], False)
        task = self.owner.update(task['id'], {'expected_version': 1, 'data': {'done': True}})
        todo = self.store.update_todo(todo['id'], {'expected_version': 1, 'done': True})
        with self.store._connection() as conn:
            conn.execute('BEGIN')
            tables = ('studio_records', 'workdays', 'workday_entries', 'studio_resource_acl')
            original = {table: conn.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
                        for table in tables if table != 'studio_resource_acl'}
            original['studio_resource_acl'] = conn.execute('SELECT * FROM studio_resource_acl').fetchall()
            changes = conn.total_changes
            events = aggregate(self.owner, conn, '2026-10-01', '2026-11-01', 'Asia/Hong_Kong')
            self.assertEqual(conn.total_changes, changes)
            for table in tables:
                order = '' if table == 'studio_resource_acl' else ' ORDER BY id'
                self.assertEqual(conn.execute('SELECT * FROM ' + table + order).fetchall(), original[table])
        current = {event['id']: event for event in events}
        for source in (task, todo):
            self.assertIs(current[source['id']]['done'], True)
            self.assertEqual(current[source['id']]['version'], source['version'])
        self.assertEqual(set(current), set(before))

    def test_invalid_range_and_timezone_are_rejected(self):
        for start, end, timezone in (
            ('2026-02-30', '2026-11-01', 'Asia/Hong_Kong'),
            ('20261001', '2026-11-01', 'Asia/Hong_Kong'),
            ('2026-10-01', '2026-10-01', 'Asia/Hong_Kong'),
            ('2026-11-01', '2026-10-01', 'Asia/Hong_Kong'),
            ('2026-10-01', '2026-11-01', 'Unknown/Zone'),
        ):
            with self.subTest(start=start, end=end, timezone=timezone):
                with self.assertRaises(LibraryError):
                    self.events(start=start, end=end, timezone=timezone)


if __name__ == '__main__':
    unittest.main()
