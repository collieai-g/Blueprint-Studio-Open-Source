"""Company calendar: scoped employees, work entries and durable attendance.

The calendar reads existing company work without copying it. Attendance and
employee work entries have their own subject identity, independent of the user
who last edited them and of the restricted personnel records.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import sqlite3
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import LibraryError
from .studio import Studio

EMPLOYMENT_TYPES = [
    {'value': 'full_time', 'label': '全職'},
    {'value': 'part_time', 'label': '兼職'},
    {'value': 'contract', 'label': '合約／外判'},
    {'value': 'temporary', 'label': '臨時'},
    {'value': 'other', 'label': '其他'},
]
TYPE_KEYS = {value['value'] for value in EMPLOYMENT_TYPES}
DEFAULT_TIMEZONE = 'Asia/Hong_Kong'


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def _text(value, maximum=240, *, required=False):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value or (required and not value.strip()):
        raise LibraryError('請提供有效文字，並留意欄位長度。')
    return value.strip()


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise LibraryError('日曆操作欄位不正確。')
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', value):
        raise LibraryError('日曆紀錄編號不正確。')
    return value


def _day(value, *, optional=False):
    if value == '' and optional:
        return ''
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise LibraryError('日期需要使用 YYYY-MM-DD。')
    try:
        result = date.fromisoformat(value)
    except ValueError:
        raise LibraryError('日期需要使用有效的 YYYY-MM-DD。') from None
    if not 1900 <= result.year <= 9998:
        raise LibraryError('日期需要介於 1900 年與 9998 年。')
    return value


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 64 or 'T' not in value:
        raise LibraryError('時間需要使用帶時區的 ISO 格式。')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None or result.utcoffset() is None or not 1900 <= result.year <= 9998:
            raise ValueError
        return _stamp(result)
    except (ValueError, OverflowError):
        raise LibraryError('時間需要使用有效的帶時區 ISO 格式。') from None


def _zone(value):
    value = _text(value, 100, required=True)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise LibraryError('請使用有效的 IANA 時區，例如 Asia/Hong_Kong。') from None
    return value


def _expected(row, value):
    if type(value) is not int or value != row['version']:
        raise LibraryError('紀錄已有更新，請重新讀取後再修改。', 409)


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def initialize(conn):
    """Called inside Store's additive schema migration transaction."""
    for statement in (
        '''CREATE TABLE IF NOT EXISTS calendar_settings (
            id TEXT PRIMARY KEY, timezone TEXT NOT NULL, version INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, actor TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS calendar_employees (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, employment_type TEXT NOT NULL,
            department TEXT NOT NULL, username TEXT UNIQUE, active INTEGER NOT NULL,
            version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            actor TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS calendar_entries (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('work_log','todo')),
            employee_id TEXT REFERENCES calendar_employees(id), title TEXT NOT NULL,
            date TEXT NOT NULL, notes TEXT NOT NULL, minutes INTEGER,
            done INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, actor TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS calendar_attendance (
            id TEXT PRIMARY KEY, employee_id TEXT NOT NULL REFERENCES calendar_employees(id),
            started_at TEXT NOT NULL, ended_at TEXT, timezone TEXT NOT NULL, version INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, actor TEXT NOT NULL)''',
        '''CREATE UNIQUE INDEX IF NOT EXISTS calendar_one_open_attendance
            ON calendar_attendance(employee_id) WHERE ended_at IS NULL''',
        '''CREATE INDEX IF NOT EXISTS calendar_entries_date
            ON calendar_entries(date,employee_id,kind,id)''',
        '''CREATE INDEX IF NOT EXISTS calendar_attendance_range
            ON calendar_attendance(employee_id,started_at,ended_at)''',
        '''CREATE TABLE IF NOT EXISTS calendar_history (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, record_id TEXT NOT NULL,
            employee_id TEXT, version INTEGER NOT NULL, actor TEXT NOT NULL,
            recorded_at TEXT NOT NULL, reason TEXT NOT NULL, snapshot TEXT NOT NULL,
            UNIQUE(kind,record_id,version))''',
        '''CREATE TABLE IF NOT EXISTS calendar_receipts (
            actor TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,
            response TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(actor,request_key))''',
    ):
        conn.execute(statement)
    now = _stamp(_now())
    conn.execute('INSERT OR IGNORE INTO calendar_settings VALUES(?,?,?,?,?,?)',
                 ('company', DEFAULT_TIMEZONE, 1, now, now, 'migration'))


class Calendar:
    def __init__(self, store, identity):
        if not isinstance(identity, dict) or not identity.get('username') or identity.get('role') not in {'owner', 'admin', 'member'}:
            raise LibraryError('請先登入公司工作台。', 403)
        self.store = store
        self.identity = dict(identity)
        self.actor = identity['username']
        self.admin = identity['role'] in {'owner', 'admin'}
        self.studio = Studio(store, identity)

    def _admin(self):
        if not self.admin:
            raise LibraryError('請由管理員處理這項日曆設定。', 403)

    @staticmethod
    def _settings(conn):
        return dict(conn.execute("SELECT * FROM calendar_settings WHERE id='company'").fetchone())

    @staticmethod
    def _employee(conn, identifier):
        row = conn.execute('SELECT * FROM calendar_employees WHERE id=?', (_identifier(identifier),)).fetchone()
        if row is None:
            raise LibraryError('找不到這位員工。', 404)
        result = dict(row)
        result['active'] = bool(result['active'])
        result['username'] = result['username'] or ''
        return result

    def _self(self, conn):
        row = conn.execute('SELECT id FROM calendar_employees WHERE username=?', (self.actor,)).fetchone()
        return self._employee(conn, row['id']) if row else None

    def _subject(self, conn, employee_id, *, writing=False):
        employee = self._employee(conn, employee_id)
        if not self.admin and employee['username'] != self.actor:
            raise LibraryError('你只能查看或處理自己的員工紀錄。', 403)
        if writing and not employee['active'] and not self.admin:
            raise LibraryError('這個員工檔案已停用，不能新增或修改工作。', 409)
        return employee

    @staticmethod
    def _safe_employee(employee):
        return {key: employee[key] for key in ('id', 'name', 'employment_type', 'department')}

    def _employees(self, conn):
        if self.admin:
            return [self._employee(conn, row['id']) for row in conn.execute('SELECT id FROM calendar_employees ORDER BY name,id')]
        own = self._self(conn)
        return [own] if own else []

    def _accounts(self, conn):
        if not self.admin:
            return []
        owner = json.loads((self.store.data_dir / 'access.json').read_text(encoding='utf-8'))['username']
        result = {owner: {'username': owner, 'name': owner, 'enabled': True}}
        for row in conn.execute('SELECT username,name,enabled FROM studio_users'):
            result[row['username']] = {'username': row['username'], 'name': row['name'], 'enabled': bool(row['enabled'])}
        bindings = {row['username']: row['id'] for row in conn.execute('SELECT id,username FROM calendar_employees WHERE username IS NOT NULL')}
        return [{**value, 'employee_id': bindings.get(key, '')} for key, value in sorted(result.items())]

    def _history(self, conn, kind, record, reason=''):
        subject = record['id'] if kind == 'employee' else record.get('employee_id')
        snapshot = {**record, 'change_reason': reason}
        conn.execute('INSERT INTO calendar_history VALUES(?,?,?,?,?,?,?,?,?)',
                     (uuid.uuid4().hex, kind, record['id'], subject or None, record['version'], self.actor,
                      _stamp(_now()), reason, _dump(snapshot)))

    def _retry(self, conn, payload, operation):
        key = payload.get('request_key')
        if key is None:
            return None
        key = _text(key, 160, required=True)
        digest = hashlib.sha256(_dump({'operation': operation, 'payload': payload}).encode()).hexdigest()
        row = conn.execute('SELECT digest,response FROM calendar_receipts WHERE actor=? AND request_key=?', (self.actor, key)).fetchone()
        if row is None:
            return None
        if row['digest'] != digest:
            raise LibraryError('相同 request_key 已用於不同日曆操作。', 409)
        response = json.loads(row['response'])
        if 'employee' in response:
            self._subject(conn, response['employee']['id'])
        if 'entry' in response:
            self._entry(conn, response['entry']['id'])
        if 'attendance' in response:
            self._attendance(conn, response['attendance']['id'])
        return {**response, 'created': False, 'idempotent': True}

    def _receipt(self, conn, payload, operation, response):
        if 'request_key' not in payload:
            return
        key = _text(payload['request_key'], 160, required=True)
        digest = hashlib.sha256(_dump({'operation': operation, 'payload': payload}).encode()).hexdigest()
        conn.execute('INSERT INTO calendar_receipts VALUES(?,?,?,?,?)',
                     (self.actor, key, digest, _dump(response), _stamp(_now())))

    def _current(self, conn, own):
        if not own:
            return None
        row = conn.execute('SELECT id FROM calendar_attendance WHERE employee_id=? AND ended_at IS NULL', (own['id'],)).fetchone()
        return self._attendance(conn, row['id']) if row else None

    def _meta(self, conn):
        settings = self._settings(conn)
        own = self._self(conn)
        return {'timezone': settings['timezone'], 'settings': settings, 'settings_version': settings['version'],
                'today': _now().astimezone(ZoneInfo(settings['timezone'])).date().isoformat(),
                'identity': self.identity, 'admin': self.admin, 'employees': self._employees(conn),
                'accounts': self._accounts(conn), 'employment_types': EMPLOYMENT_TYPES,
                'self_employee': own, 'current_attendance': self._current(conn, own)}

    def meta(self):
        with self.store._connection() as conn:
            conn.execute('BEGIN')
            return self._meta(conn)

    def save_settings(self, payload):
        self._admin()
        _object(payload, {'timezone', 'expected_version'}, {'timezone', 'expected_version'})
        zone = _zone(payload['timezone'])
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self._settings(conn)
            _expected(old, payload['expected_version'])
            conn.execute("UPDATE calendar_settings SET timezone=?,version=version+1,updated_at=?,actor=? WHERE id='company'",
                         (zone, _stamp(_now()), self.actor))
            saved = self._settings(conn)
            self._history(conn, 'settings', saved)
            return {'settings': saved}

    def _employee_fields(self, conn, payload, old=None):
        source = {**(old or {'employment_type': 'other', 'department': '', 'username': '', 'active': True}), **payload}
        name = _text(source.get('name'), 120, required=True)
        department = _text(source['department'], 120)
        employment_type = source['employment_type']
        if not isinstance(employment_type, str) or employment_type not in TYPE_KEYS:
            raise LibraryError('員工類型不正確。')
        if type(source['active']) is not bool:
            raise LibraryError('啟用狀態需要是或否。')
        username = _text(source['username'], 120)
        if old and old['username'] and username != old['username']:
            raise LibraryError('已綁定的員工不能改綁或移除登入帳戶；請保留原人員歷史。', 409)
        if username:
            available = {value['username'] for value in self._accounts(conn)}
            if username not in available:
                raise LibraryError('請先建立這個登入帳戶；員工檔案不會授予登入權限。')
            row = conn.execute('SELECT id FROM calendar_employees WHERE username=?', (username,)).fetchone()
            if row and (old is None or row['id'] != old['id']):
                raise LibraryError('這個登入帳戶已綁定另一位員工。', 409)
        return {'name': name, 'department': department, 'employment_type': employment_type,
                'username': username, 'active': source['active']}

    def create_employee(self, payload):
        self._admin()
        _object(payload, {'name', 'department', 'employment_type', 'username', 'active', 'request_key'}, {'name'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            retry = self._retry(conn, payload, 'create_employee')
            if retry:
                return retry
            fields = self._employee_fields(conn, payload)
            identifier, now = uuid.uuid4().hex, _stamp(_now())
            conn.execute('INSERT INTO calendar_employees VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (identifier, fields['name'], fields['employment_type'], fields['department'],
                          fields['username'] or None, int(fields['active']), 1, now, now, self.actor))
            saved = self._employee(conn, identifier)
            self._history(conn, 'employee', saved)
            response = {'employee': saved, 'created': True, 'idempotent': False}
            self._receipt(conn, payload, 'create_employee', response)
            return response

    def update_employee(self, identifier, payload):
        self._admin()
        _object(payload, {'name', 'department', 'employment_type', 'username', 'active', 'expected_version'}, {'expected_version'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self._employee(conn, identifier)
            _expected(old, payload['expected_version'])
            fields = self._employee_fields(conn, payload, old)
            conn.execute('UPDATE calendar_employees SET name=?,employment_type=?,department=?,username=?,active=?,version=version+1,updated_at=?,actor=? WHERE id=?',
                         (fields['name'], fields['employment_type'], fields['department'], fields['username'] or None,
                          int(fields['active']), _stamp(_now()), self.actor, identifier))
            saved = self._employee(conn, identifier)
            self._history(conn, 'employee', saved)
            return {'employee': saved}

    def get_employee(self, identifier):
        with self.store._connection() as conn:
            return {'employee': self._subject(conn, identifier)}

    def _entry(self, conn, identifier):
        row = conn.execute('SELECT * FROM calendar_entries WHERE id=?', (_identifier(identifier),)).fetchone()
        if row is None:
            raise LibraryError('找不到這項日曆工作。', 404)
        result = dict(row)
        result['employee_id'] = result['employee_id'] or ''
        result['done'] = bool(result['done'])
        if result['employee_id']:
            employee = self._subject(conn, result['employee_id'])
            result['employee'] = self._safe_employee(employee)
        elif result['kind'] == 'todo':
            result['employee'] = None
        else:
            raise LibraryError('員工工作紀錄缺少所屬員工。', 409)
        result.update(source_kind='calendar_entry', event_key='calendar:entry:' + result['id'],
                      scope='employee' if result['employee_id'] else 'company', route='calendar')
        return result

    @staticmethod
    def _entry_fields(payload, kind, old=None):
        source = {**(old or {'notes': '', 'minutes': None, 'done': False}), **payload}
        title = _text(source.get('title'), 240, required=True)
        day = _day(source.get('date'), optional=kind == 'todo')
        notes = _text(source['notes'], 20000)
        if kind == 'work_log':
            if 'done' in payload:
                raise LibraryError('工作紀錄沒有待辦完成欄位。')
            minutes = source['minutes']
            if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 1440):
                raise LibraryError('工作分鐘需要是 0 至 1440 的整數，或留空。')
            done = False
        else:
            if 'minutes' in payload:
                raise LibraryError('待辦沒有工作分鐘欄位。')
            if type(source['done']) is not bool:
                raise LibraryError('完成狀態需要是或否。')
            done, minutes = source['done'], None
        return {'title': title, 'date': day, 'notes': notes, 'minutes': minutes, 'done': done}

    def create_entry(self, payload):
        _object(payload, {'kind', 'employee_id', 'title', 'date', 'notes', 'minutes', 'done', 'request_key'}, {'kind', 'title', 'date'})
        kind = payload['kind']
        if not isinstance(kind, str) or kind not in {'work_log', 'todo'}:
            raise LibraryError('日曆工作只接受 work_log 或 todo。')
        if not self.admin and kind == 'todo':
            raise LibraryError('請由管理員建立及指派公司待辦。', 403)
        employee_id = payload.get('employee_id', '')
        if not isinstance(employee_id, str):
            raise LibraryError('所屬員工編號不正確。')
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            retry = self._retry(conn, payload, 'create_entry')
            if retry:
                return retry
            if not employee_id and kind == 'work_log':
                own = self._self(conn)
                if not self.admin and own:
                    employee_id = own['id']
                else:
                    raise LibraryError('工作紀錄需要指定員工。')
            if employee_id:
                self._subject(conn, employee_id, writing=True)
            elif not self.admin:
                raise LibraryError('你不能建立未指派的公司工作。', 403)
            fields = self._entry_fields(payload, kind)
            identifier, now = uuid.uuid4().hex, _stamp(_now())
            conn.execute('INSERT INTO calendar_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                         (identifier, kind, employee_id or None, fields['title'], fields['date'], fields['notes'],
                          fields['minutes'], int(fields['done']), 1, now, now, self.actor))
            saved = self._entry(conn, identifier)
            self._history(conn, 'entry', saved)
            response = {'entry': saved, 'created': True, 'idempotent': False}
            self._receipt(conn, payload, 'create_entry', response)
            return response

    def update_entry(self, identifier, payload):
        _object(payload, {'kind', 'employee_id', 'title', 'date', 'notes', 'minutes', 'done', 'expected_version'}, {'expected_version'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self._entry(conn, identifier)
            _expected(old, payload['expected_version'])
            if ('kind' in payload and payload['kind'] != old['kind']) or ('employee_id' in payload and payload['employee_id'] != old['employee_id']):
                raise LibraryError('工作種類和所屬員工不能改寫，請另建正確工作。', 409)
            if not self.admin:
                if not old['employee_id']:
                    raise LibraryError('公司共同待辦只能由管理員修改。', 403)
                self._subject(conn, old['employee_id'], writing=True)
                if old['kind'] == 'todo' and set(payload) - {'done', 'expected_version'}:
                    raise LibraryError('你只能更新指派給自己的待辦完成狀態。', 403)
            fields = self._entry_fields(payload, old['kind'], old)
            conn.execute('UPDATE calendar_entries SET title=?,date=?,notes=?,minutes=?,done=?,version=version+1,updated_at=?,actor=? WHERE id=?',
                         (fields['title'], fields['date'], fields['notes'], fields['minutes'], int(fields['done']),
                          _stamp(_now()), self.actor, identifier))
            saved = self._entry(conn, identifier)
            self._history(conn, 'entry', saved)
            return {'entry': saved}

    def get_entry(self, identifier):
        with self.store._connection() as conn:
            return {'entry': self._entry(conn, identifier)}

    def _attendance(self, conn, identifier):
        row = conn.execute('SELECT * FROM calendar_attendance WHERE id=?', (_identifier(identifier),)).fetchone()
        if row is None:
            raise LibraryError('找不到這段打卡紀錄。', 404)
        result = dict(row)
        employee = self._subject(conn, result['employee_id'])
        result.update(employee=self._safe_employee(employee), source_kind='calendar_attendance',
                      event_key='calendar:attendance:' + result['id'], route='calendar')
        return result

    @staticmethod
    def _overlap(conn, employee_id, started_at, ended_at, identifier=''):
        row = conn.execute('''SELECT id FROM calendar_attendance WHERE employee_id=? AND id<>?
            AND (ended_at IS NULL OR ended_at>?) AND (? IS NULL OR started_at<?) LIMIT 1''',
                           (employee_id, identifier, started_at, ended_at, ended_at)).fetchone()
        if row:
            raise LibraryError('這段時間與同一員工的其他打卡紀錄重疊。', 409)

    @staticmethod
    def _interval(started_at, ended_at):
        start = _timestamp(started_at)
        end = None if ended_at is None else _timestamp(ended_at)
        if end is not None and end < start:
            raise LibraryError('結束時間不能早於開始時間。')
        return start, end

    def _new_attendance(self, conn, employee_id, started_at, ended_at, reason):
        self._overlap(conn, employee_id, started_at, ended_at)
        identifier, now = uuid.uuid4().hex, _stamp(_now())
        try:
            conn.execute('INSERT INTO calendar_attendance VALUES(?,?,?,?,?,?,?,?,?)',
                         (identifier, employee_id, started_at, ended_at, self._settings(conn)['timezone'],
                          1, now, now, self.actor))
        except sqlite3.IntegrityError:
            raise LibraryError('已有未結束的打卡，請先讀回目前狀態。', 409) from None
        saved = self._attendance(conn, identifier)
        self._history(conn, 'attendance', saved, reason)
        return saved

    def attendance_action(self, payload):
        _object(payload, {'action', 'request_key', 'expected_version', 'attendance_id'}, {'action', 'request_key'})
        action = payload['action']
        if not isinstance(action, str) or action not in {'clock_in', 'clock_out'}:
            raise LibraryError('打卡動作只接受 clock_in 或 clock_out。')
        if action == 'clock_in' and ('expected_version' in payload or 'attendance_id' in payload):
            raise LibraryError('上班打卡不接受既有版本。')
        if action == 'clock_out' and not {'expected_version', 'attendance_id'} <= set(payload):
            raise LibraryError('下班打卡需要目前時段編號與版本。')
        if action == 'clock_out':
            _identifier(payload['attendance_id'])
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            own = self._self(conn)
            if own is None:
                raise LibraryError('目前登入帳戶尚未綁定員工，請由管理員設定。', 409)
            retry = self._retry(conn, payload, 'attendance_action')
            if retry:
                return retry
            if not own['active'] and action == 'clock_in':
                raise LibraryError('這個員工檔案已停用，不能打卡。', 409)
            now = _stamp(_now())
            current = self._current(conn, own)
            if action == 'clock_in':
                if current:
                    raise LibraryError('已有未結束的打卡，請先讀回目前狀態。', 409)
                saved = self._new_attendance(conn, own['id'], now, None, '本人上班打卡')
            else:
                if not current:
                    raise LibraryError('目前沒有未結束的打卡，請先讀回狀態。', 409)
                if current['id'] != payload['attendance_id']:
                    raise LibraryError('目前打卡時段已改變，請重新讀取後再下班。', 409)
                _expected(current, payload['expected_version'])
                if now < current['started_at']:
                    raise LibraryError('目前伺服器時間早於開始時間，請由管理員核對。', 409)
                self._overlap(conn, own['id'], current['started_at'], now, current['id'])
                conn.execute('UPDATE calendar_attendance SET ended_at=?,version=version+1,updated_at=?,actor=? WHERE id=?',
                             (now, now, self.actor, current['id']))
                saved = self._attendance(conn, current['id'])
                self._history(conn, 'attendance', saved, '本人下班打卡')
            response = {'attendance': saved, 'created': action == 'clock_in', 'idempotent': False}
            self._receipt(conn, payload, 'attendance_action', response)
            return response

    def manual_attendance(self, payload):
        self._admin()
        _object(payload, {'employee_id', 'started_at', 'ended_at', 'reason', 'request_key'},
                {'employee_id', 'started_at', 'ended_at', 'reason', 'request_key'})
        start, end = self._interval(payload['started_at'], payload['ended_at'])
        reason = _text(payload['reason'], 1000, required=True)
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            retry = self._retry(conn, payload, 'manual_attendance')
            if retry:
                return retry
            employee = self._subject(conn, payload['employee_id'])
            saved = self._new_attendance(conn, employee['id'], start, end, reason)
            response = {'attendance': saved, 'created': True, 'idempotent': False}
            self._receipt(conn, payload, 'manual_attendance', response)
            return response

    def update_attendance(self, identifier, payload):
        self._admin()
        _object(payload, {'started_at', 'ended_at', 'expected_version', 'reason'},
                {'started_at', 'ended_at', 'expected_version', 'reason'})
        start, end = self._interval(payload['started_at'], payload['ended_at'])
        reason = _text(payload['reason'], 1000, required=True)
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = self._attendance(conn, identifier)
            _expected(old, payload['expected_version'])
            self._overlap(conn, old['employee_id'], start, end, identifier)
            conn.execute('UPDATE calendar_attendance SET started_at=?,ended_at=?,version=version+1,updated_at=?,actor=? WHERE id=?',
                         (start, end, _stamp(_now()), self.actor, identifier))
            saved = self._attendance(conn, identifier)
            self._history(conn, 'attendance', saved, reason)
            return {'attendance': saved}

    def get_attendance(self, identifier):
        with self.store._connection() as conn:
            return {'attendance': self._attendance(conn, identifier)}

    def history(self, kind, identifier):
        if kind not in {'employee', 'entry', 'attendance'}:
            raise LibraryError('歷史種類不正確。')
        with self.store._connection() as conn:
            if kind == 'employee':
                self._subject(conn, identifier)
            elif kind == 'entry':
                entry = self._entry(conn, identifier)
                if not self.admin and not entry['employee_id']:
                    raise LibraryError('公司共同待辦歷史只供管理員查看。', 403)
            else:
                self._attendance(conn, identifier)
            rows = conn.execute('SELECT snapshot FROM calendar_history WHERE kind=? AND record_id=? ORDER BY version DESC', (kind, identifier))
            return {'versions': [json.loads(row['snapshot']) for row in rows]}

    def _legacy(self, conn, start, end, employee_id, employment_type):
        if employee_id or employment_type:
            return []
        from .calendar_sources import aggregate
        return aggregate(self.studio, conn, start, end, self._settings(conn)['timezone'])

    def month(self, month, employee_id='', employment_type=''):
        if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
            raise LibraryError('月份需要使用 YYYY-MM。')
        try:
            year, number = map(int, month.split('-'))
            if not 1900 <= year <= 9998:
                raise ValueError
            start = date(year, number, 1)
            end = start + timedelta(days=monthrange(year, number)[1])
        except (ValueError, OverflowError):
            raise LibraryError('月份需要使用有效的 YYYY-MM。') from None
        if not isinstance(employee_id, str) or (employee_id and not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', employee_id)):
            raise LibraryError('員工篩選不正確。')
        if not isinstance(employment_type, str) or (employment_type and employment_type not in TYPE_KEYS):
            raise LibraryError('員工類型篩選不正確。')
        with self.store._connection() as conn:
            conn.execute('BEGIN')
            result = self._meta(conn)
            if employee_id:
                self._subject(conn, employee_id)
            employees = [employee for employee in result['employees']
                         if (not employee_id or employee['id'] == employee_id)
                         and (not employment_type or employee['employment_type'] == employment_type)]
            allowed = {employee['id'] for employee in employees}
            zone = ZoneInfo(result['timezone'])
            left = datetime.combine(start, datetime.min.time(), zone)
            right = datetime.combine(end, datetime.min.time(), zone)
            days = { (start + timedelta(days=index)).isoformat():
                    {'date': (start + timedelta(days=index)).isoformat(), 'attendance': [], 'work_logs': [],
                     'todos': [], 'deadlines': [], 'workdays': []}
                    for index in range((end - start).days)}
            undated = []
            rows = conn.execute("SELECT id,employee_id,kind,date FROM calendar_entries WHERE date='' OR (date>=? AND date<?) ORDER BY date,created_at,id", (start.isoformat(), end.isoformat()))
            for row in rows:
                if row['employee_id'] and row['employee_id'] not in allowed:
                    continue
                if not row['employee_id'] and (employee_id or employment_type):
                    continue
                entry = self._entry(conn, row['id'])
                if not entry['date']:
                    undated.append(entry)
                else:
                    days[entry['date']]['work_logs' if entry['kind'] == 'work_log' else 'todos'].append(entry)
            attendance_ids = set()
            now = _now()
            rows = conn.execute('SELECT id,employee_id FROM calendar_attendance WHERE started_at<? AND (ended_at IS NULL OR ended_at>?) ORDER BY started_at,id', (_stamp(right), _stamp(left)))
            for row in rows:
                if row['employee_id'] not in allowed:
                    continue
                attendance = self._attendance(conn, row['id'])
                beginning = datetime.fromisoformat(attendance['started_at'].replace('Z', '+00:00'))
                ending = datetime.fromisoformat(attendance['ended_at'].replace('Z', '+00:00')) if attendance['ended_at'] else now
                cursor = max(beginning, left.astimezone(timezone.utc))
                limit = min(ending, right.astimezone(timezone.utc))
                while cursor < limit:
                    local_day = cursor.astimezone(zone).date()
                    next_day = datetime.combine(local_day + timedelta(days=1), datetime.min.time(), zone).astimezone(timezone.utc)
                    segment_end = min(limit, next_day)
                    if local_day.isoformat() in days:
                        segment = {**attendance, 'segment_start': _stamp(cursor), 'segment_end': _stamp(segment_end),
                                   'display_start': cursor.astimezone(zone).isoformat(),
                                   'display_end': segment_end.astimezone(zone).isoformat(),
                                   'minutes': int((segment_end - cursor).total_seconds() // 60),
                                   'open': attendance['ended_at'] is None}
                        days[local_day.isoformat()]['attendance'].append(segment)
                        attendance_ids.add(attendance['id'])
                    cursor = segment_end
            for item in self._legacy(conn, start, end, employee_id, employment_type):
                if item.get('date') not in days:
                    continue
                kind = item.get('source_kind', item.get('type'))
                bucket = 'workdays' if kind == 'workday' else 'todos' if kind in {'todo', 'task'} else 'deadlines'
                days[item['date']][bucket].append(item)
            totals = {key: sum(len(day[key]) for day in days.values()) for key in ('work_logs', 'todos', 'deadlines', 'workdays')}
            result.update(month=month, employees=employees, days=list(days.values()), undated_todos=undated,
                          company_items_included=not (employee_id or employment_type),
                          summary={'employees': len(employees), 'attendance_sessions': len(attendance_ids),
                                   **totals, 'undated_todos': len(undated)})
            return result
