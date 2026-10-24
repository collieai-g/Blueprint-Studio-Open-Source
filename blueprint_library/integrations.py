"""Declarative external services, durable evidence and recoverable commands.

Each Store is one company. A service credential selects one installation in that
Store; request bodies can neither select another company nor impersonate a user.
No remote code, model, scheduler or message transport is run by this module.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import secrets
from urllib.parse import urlsplit
import uuid

from .store import LibraryError, _now
from .studio import Studio

CONTRACT_VERSION = '1.0'
RECORD_TYPES = ('thread', 'message', 'work_item', 'artifact', 'note', 'status', 'knowledge')
RESULT_STATES = ('accepted', 'running', 'completed', 'rejected', 'expired', 'failed', 'unknown')
TERMINAL_STATES = {'completed', 'rejected', 'expired', 'failed'}
_NAME = re.compile(r'[a-zA-Z][a-zA-Z0-9_.-]{0,79}\Z')
_MAX_PAYLOAD = 128 * 1024


def _error(message, status=400):
    raise LibraryError(message, status)


def _dump(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        _error('內容必須是有效的 JSON 資料。')


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        _error('內容缺少必要欄位，或包含不支援的欄位。')
    return value


def _text(value, maximum=160, required=True):
    if not isinstance(value, str) or '\x00' in value or len(value) > maximum or (required and not value.strip()):
        _error('請提供有效文字，並留意欄位長度。')
    return value.strip()


def _integer(value, minimum=1, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _error('整數超出可用範圍。')
    return value


def _timestamp(value):
    value = _text(value, 80)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})', value):
        _error('時間需要使用含時區的 RFC3339 格式。')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or 'T' not in value:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, OverflowError):
        _error('時間需要使用含時區的 RFC3339 格式。')


def _payload(value):
    if not isinstance(value, dict) or len(_dump(value).encode()) > _MAX_PAYLOAD:
        _error('payload 必須是 JSON 物件，最多 128 KiB。')
    return value


def _page(limit, offset=0):
    return _integer(limit, 1, 100), _integer(offset, 0)


def _types(value):
    if not isinstance(value, list) or not value or len(value) > len(RECORD_TYPES) or any(not isinstance(v, str) or v not in RECORD_TYPES for v in value) or len(set(value)) != len(value):
        _error('請選擇不重複的支援紀錄種類。')
    return value


def _origin(value):
    value = _text(value, 2048)
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or re.search(r'[\x00-\x20\x7f]', value)):
        _error('核准來源需要是沒有登入資料或路徑的 HTTPS origin。')
    try:
        parsed.port
    except ValueError:
        _error('來源連接埠無效。')
    return parsed.scheme + '://' + parsed.netloc.lower()


def _origins(value):
    if not isinstance(value, list) or len(value) > 20:
        _error('核准來源最多 20 個。')
    return sorted(set(_origin(v) for v in value))


def _schema(value):
    _object(value, {'type', 'properties', 'required', 'additionalProperties'}, {'type', 'properties', 'required', 'additionalProperties'})
    if value['type'] != 'object' or value['additionalProperties'] is not False or not isinstance(value['properties'], dict) or len(value['properties']) > 32:
        _error('指令只支援有固定欄位的簡單 object schema。')
    required = value['required']
    if not isinstance(required, list) or any(not isinstance(k, str) for k in required) or len(required) != len(set(required)) or set(required) - set(value['properties']):
        _error('schema 的必要欄位無效。')
    for name, spec in value['properties'].items():
        if not _NAME.fullmatch(name) or name in {'__proto__', 'prototype', 'constructor'}:
            _error('schema 欄位名稱無效。')
        _object(spec, {'type', 'title', 'maxLength', 'enum'}, {'type'})
        if not isinstance(spec['type'], str) or spec['type'] not in {'string', 'integer', 'number', 'boolean'}:
            _error('schema 只支援文字、數字、整數及布林值。')
        if 'title' in spec:
            _text(spec['title'], 200)
        if 'maxLength' in spec:
            if spec['type'] != 'string':
                _error('只有文字欄位可設定 maxLength。')
            _integer(spec['maxLength'], 1, 40000)
        if 'enum' in spec:
            if not isinstance(spec['enum'], list) or not 1 <= len(spec['enum']) <= 64:
                _error('enum 需要 1 至 64 個選項。')
            for item in spec['enum']:
                _primitive(item, {k: v for k, v in spec.items() if k != 'enum'})
    return json.loads(_dump(value))


def _primitive(value, spec):
    typ = spec['type']
    valid = (isinstance(value, str) if typ == 'string' else type(value) is bool if typ == 'boolean'
             else type(value) is int if typ == 'integer' else type(value) in {int, float} and abs(value) <= 2**53 - 1 and math.isfinite(value))
    if not valid or (typ == 'string' and ('\x00' in value or len(value) > spec.get('maxLength', 40000))):
        _error('指令欄位不符合已核准的 schema。')
    if typ in {'integer', 'number'} and abs(value) > 2**53 - 1:
        _error('指令數字超出跨語言安全範圍。')
    if 'enum' in spec and not any(type(value) is type(v) and value == v for v in spec['enum']):
        _error('指令欄位不在可用選項內。')


def _commands(value):
    if not isinstance(value, list) or len(value) > 32:
        _error('最多宣告 32 項指令。')
    result = []
    for command in value:
        _object(command, {'name', 'label', 'input_schema'}, {'name', 'label', 'input_schema'})
        name = _text(command['name'], 80)
        if not _NAME.fullmatch(name) or any(c['name'] == name for c in result):
            _error('指令名稱無效或重複。')
        result.append({'name': name, 'label': _text(command['label'], 200), 'input_schema': _schema(command['input_schema'])})
    return result


def initialize(conn):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS integration_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS integration_registry (
            id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, document TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS integration_records (
            id TEXT PRIMARY KEY, integration_id TEXT NOT NULL REFERENCES integration_registry(id),
            record_type TEXT NOT NULL, external_id TEXT NOT NULL, document TEXT NOT NULL,
            UNIQUE(integration_id,record_type,external_id))''',
        '''CREATE TABLE IF NOT EXISTS integration_history (
            record_id TEXT NOT NULL REFERENCES integration_records(id),version INTEGER NOT NULL,
            document TEXT NOT NULL,PRIMARY KEY(record_id,version))''',
        '''CREATE TABLE IF NOT EXISTS integration_commands (
            id TEXT PRIMARY KEY, integration_id TEXT NOT NULL REFERENCES integration_registry(id),
            state TEXT NOT NULL, document TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS integration_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
            integration_id TEXT NOT NULL REFERENCES integration_registry(id), document TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS integration_receipts (
            integration_id TEXT NOT NULL, operation TEXT NOT NULL, key TEXT NOT NULL,
            digest TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(integration_id,operation,key))''',
        '''CREATE TABLE IF NOT EXISTS integration_source_events (
            integration_id TEXT NOT NULL,event_id TEXT NOT NULL,digest TEXT NOT NULL,
            PRIMARY KEY(integration_id,event_id))''',
        'CREATE INDEX IF NOT EXISTS integration_event_stream ON integration_events(integration_id,sequence)',
        'CREATE INDEX IF NOT EXISTS integration_command_state ON integration_commands(integration_id,state)',
    ):
        conn.execute(statement)
    conn.execute("INSERT OR IGNORE INTO integration_meta VALUES('company_id',?)", (uuid.uuid4().hex,))


def authenticate(store, token):
    """Resolve a high-entropy credential without exposing it or trusting a body ID."""
    if not isinstance(token, str) or len(token) > 512 or not token.startswith('bpi_'):
        _error('外部服務憑證無效。', 401)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with store._connection() as conn:
        row = conn.execute('SELECT id,token_hash,document FROM integration_registry WHERE token_hash=?', (digest,)).fetchone()
        if not row or not hmac.compare_digest(row['token_hash'], digest):
            _error('外部服務憑證無效。', 401)
        record = json.loads(row['document'])
        if record['revoked'] or not record['enabled']:
            _error('外部服務憑證已撤銷或停用。', 401)
        return row['id']


class Integrations:
    def __init__(self, store, identity=None, integration_id=None):
        self.store = store
        self.integration_id = integration_id
        self.studio = Studio(store, identity)
        self.actor = self.studio.actor if not integration_id else 'integration:' + integration_id

    def _admin(self):
        if self.integration_id or not self.studio.admin:
            _error('請由管理員管理外部服務。', 403)

    def _registry(self, conn, integration_id=None, active=False):
        target = integration_id or self.integration_id
        if not target:
            _error('請提供 integration_id。')
        if self.integration_id and target != self.integration_id:
            _error('不能存取其他整合實例。', 403)
        row = conn.execute('SELECT document FROM integration_registry WHERE id=?', (_text(target),)).fetchone()
        if not row:
            _error('找不到此整合實例。', 404)
        record = json.loads(row['document'])
        if (active or self.integration_id) and (record['revoked'] or not record['enabled']):
            _error('外部服務已撤銷或停用。', 403)
        return record

    def _refs(self, conn, refs):
        if not isinstance(refs, list) or len(refs) > 100:
            _error('最多授權 100 項指定來源。')
        result = []
        member = Studio(self.store, {'username': self.studio.actor, 'role': 'member'})
        for ref in refs:
            _object(ref, {'kind', 'id'}, {'kind', 'id'})
            cleaned = {'kind': _text(ref['kind'], 80), 'id': _text(ref['id'], 80)}
            # External grants are deliberately limited to currently team-readable
            # sources. Changing a source to private immediately revokes the grant.
            if cleaned['kind'] not in {'item', 'report'}:
                _error('第一版只允許選定檔案、文件及報告。')
            if member._reference(conn, cleaned) != 'team':
                _error('不能把私人來源授權給外部服務。', 403)
            if cleaned not in result:
                result.append(cleaned)
        return result

    @staticmethod
    def _expected(record, expected):
        if type(expected) is not int or expected != record['version']:
            _error('內容已有更新，請重新讀取後再修改。', 409)

    def register(self, payload):
        self._admin()
        _object(payload, {'name', 'version', 'record_types', 'commands', 'resource_refs', 'allowed_origins'}, {'name', 'version', 'record_types', 'commands'})
        token = 'bpi_' + secrets.token_urlsafe(32)
        now = _now()
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = {'id': uuid.uuid4().hex, 'name': _text(payload['name'], 200),
                      'app_version': _text(payload['version'], 80), 'record_types': _types(payload['record_types']),
                      'commands': _commands(payload['commands']), 'resource_refs': self._refs(conn, payload.get('resource_refs', [])),
                      'allowed_origins': _origins(payload.get('allowed_origins', [])),
                      'enabled': True, 'revoked': False, 'version': 1, 'created_at': now, 'updated_at': now, 'actor': self.actor,
                      'company_id': conn.execute("SELECT value FROM integration_meta WHERE key='company_id'").fetchone()[0]}
            conn.execute('INSERT INTO integration_registry VALUES(?,?,?)', (record['id'], hashlib.sha256(token.encode()).hexdigest(), _dump(record)))
            return {'integration': record, 'token': token, 'created': True}

    def list_integrations(self):
        self._admin()
        with self.store._connection() as conn:
            records = [json.loads(row[0]) for row in conn.execute('SELECT document FROM integration_registry ORDER BY id')]
            return {'integrations': records}

    def get_integration(self, integration_id=None):
        self._admin()
        with self.store._connection() as conn:
            return self._registry(conn, integration_id)

    def update_integration(self, integration_id, payload):
        self._admin()
        _object(payload, {'expected_version', 'enabled', 'record_types', 'resource_refs', 'commands', 'allowed_origins', 'name', 'app_version'}, {'expected_version'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._registry(conn, integration_id)
            self._expected(record, payload['expected_version'])
            if record['revoked']:
                _error('已撤銷的憑證不能重新啟用，請另行註冊。', 409)
            for key in set(payload) - {'expected_version'}:
                value = payload[key]
                if key == 'enabled':
                    if type(value) is not bool:
                        _error('enabled 需要是布林值。')
                elif key == 'record_types':
                    value = _types(value)
                elif key == 'commands':
                    value = _commands(value)
                elif key == 'resource_refs':
                    value = self._refs(conn, value)
                elif key == 'allowed_origins':
                    value = _origins(value)
                else:
                    value = _text(value, 200 if key == 'name' else 80)
                record[key] = value
            record.update(version=record['version'] + 1, actor=self.actor, updated_at=_now())
            conn.execute('UPDATE integration_registry SET document=? WHERE id=?', (_dump(record), record['id']))
            return record

    def revoke(self, integration_id, payload):
        self._admin()
        _object(payload, {'expected_version'}, {'expected_version'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._registry(conn, integration_id)
            self._expected(record, payload['expected_version'])
            record.update(revoked=True, enabled=False, version=record['version'] + 1, actor=self.actor, updated_at=_now())
            conn.execute('UPDATE integration_registry SET document=? WHERE id=?', (_dump(record), record['id']))
            return record

    def capabilities(self):
        with self.store._connection() as conn:
            registration = self._registry(conn) if self.integration_id else None
            if not registration:
                self._admin()
            return {'schema_version': CONTRACT_VERSION, 'contract_version': CONTRACT_VERSION,
                    'record_types': registration['record_types'] if registration else list(RECORD_TYPES),
                    'commands': registration['commands'] if registration else [],
                    'resource_refs': registration['resource_refs'] if registration else [],
                    'permissions': {'records_read': registration['record_types'] if registration else list(RECORD_TYPES),
                                    'records_write': [kind for kind in registration['record_types'] if kind != 'knowledge'] if registration else list(RECORD_TYPES),
                                    'commands_create': not bool(registration), 'commands_read': True,
                                    'command_results_write': bool(registration)},
                    'integration_id': self.integration_id, 'max_page_size': 100,
                    'sync': {'cursor': True, 'replay': True, 'gap_status': 410, 'webhook': False},
                    'knowledge': {'authority': 'platform', 'read_current_approved_only': True},
                    'company_id': conn.execute("SELECT value FROM integration_meta WHERE key='company_id'").fetchone()[0]}

    def _select(self, conn, requested=None):
        if not self.integration_id:
            self._admin()
        return self._registry(conn, requested, active=True)

    def _retry(self, conn, integration_id, operation, key, request):
        key = _text(key)
        row = conn.execute('SELECT digest,response FROM integration_receipts WHERE integration_id=? AND operation=? AND key=?', (integration_id, operation, key)).fetchone()
        if row:
            if not hmac.compare_digest(row['digest'], _digest(request)):
                _error('相同 idempotency_key 已用於不同內容。', 409)
            return json.loads(row['response'])
        return None

    def _receipt(self, conn, integration_id, operation, key, request, response):
        conn.execute('INSERT INTO integration_receipts VALUES(?,?,?,?,?)', (integration_id, operation, key, _digest(request), _dump(response)))

    def _source_event(self, conn, integration_id, event_id, request):
        if conn.execute('SELECT 1 FROM integration_source_events WHERE integration_id=? AND event_id=?', (integration_id, event_id)).fetchone():
            _error('event_id 已保存；請沿用原 idempotency_key 讀回結果。', 409)
        conn.execute('INSERT INTO integration_source_events VALUES(?,?,?)', (integration_id, event_id, _digest(request)))

    def _cursor(self, conn, stream, sequence=None):
        if sequence is None:
            sequence = conn.execute('SELECT COALESCE(MAX(sequence),0) FROM integration_events').fetchone()[0]
        row = conn.execute('SELECT event_id FROM integration_events WHERE sequence=?', (sequence,)).fetchone() if sequence else None
        body = {'company': conn.execute("SELECT value FROM integration_meta WHERE key='company_id'").fetchone()[0],
                'stream': stream, 'sequence': sequence, 'anchor': row['event_id'] if row else ''}
        return base64.urlsafe_b64encode(_dump(body).encode()).decode().rstrip('=')

    def _position(self, conn, stream, cursor):
        if not cursor:
            return 0
        try:
            cursor = _text(cursor, 2048)
            body = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
            if not isinstance(body, dict) or set(body) != {'company', 'stream', 'sequence', 'anchor'} or type(body['sequence']) is not int or body['sequence'] < 0:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            _error('同步游標格式無效。')
        company = conn.execute("SELECT value FROM integration_meta WHERE key='company_id'").fetchone()[0]
        row = conn.execute('SELECT event_id FROM integration_events WHERE sequence=?', (body['sequence'],)).fetchone() if body['sequence'] else None
        if body['company'] != company or body['stream'] != stream or (body['sequence'] and (not row or row['event_id'] != body['anchor'])) or (not body['sequence'] and body['anchor']):
            _error('同步位置有缺口或屬於其他資料分支；請重新核對並完整同步。', 410)
        return body['sequence']

    def _event(self, conn, integration_id, kind, value, occurred_at=None):
        now = _now()
        event = {'event_id': uuid.uuid4().hex, 'type': kind, 'integration_id': integration_id,
                 'occurred_at': occurred_at or now, 'received_at': now, **value}
        conn.execute('INSERT INTO integration_events(event_id,integration_id,document) VALUES(?,?,?)', (event['event_id'], integration_id, _dump(event)))
        return self._cursor(conn, integration_id)

    @staticmethod
    def _knowledge(payload):
        _object(payload, {'title', 'content', 'state', 'effective_at', 'expires_at'}, {'title', 'content', 'state', 'effective_at'})
        result = {'title': _text(payload['title'], 300), 'content': _text(payload['content'], 40000, required=payload['state'] == 'approved'),
                  'state': payload['state'], 'effective_at': _timestamp(payload['effective_at'])}
        if not isinstance(result['state'], str) or result['state'] not in {'approved', 'revoked'}:
            _error('知識狀態需要是 approved 或 revoked。')
        if 'expires_at' in payload:
            result['expires_at'] = _timestamp(payload['expires_at'])
            if result['expires_at'] <= result['effective_at']:
                _error('知識到期時間需要遲於生效時間。')
        return result

    def _safe_payload(self, registration, payload):
        payload = _payload(payload)
        if 'external_url' in payload:
            value = _text(payload['external_url'], 4096)
            parsed = urlsplit(value)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                    or re.search(r'[\x00-\x20\x7f]', value) or parsed.scheme + '://' + parsed.netloc.lower() not in registration['allowed_origins']):
                _error('個案連結不屬於已核准的 HTTPS 來源。')
        return payload

    def _visible_record(self, conn, record, *, event=False):
        record = json.loads(_dump(record))
        if self.integration_id:
            registration = self._registry(conn, record['integration_id'])
            if record['record_type'] not in registration['record_types']:
                _error('此整合沒有這種紀錄的權限。', 403)
        if self.integration_id and record['record_type'] == 'knowledge':
            current_row = conn.execute('SELECT document FROM integration_records WHERE id=?', (record['id'],)).fetchone()
            current = json.loads(current_row[0])
            policy = current['payload']
            state = policy['state']
            now = _now()
            if state == 'approved' and policy['effective_at'] > now:
                state = 'scheduled'
            if state == 'approved' and policy.get('expires_at') and policy['expires_at'] <= now:
                state = 'expired'
            if event or state != 'approved' or record['version'] != current['version']:
                record['payload'] = {k: v for k, v in policy.items() if k != 'content'}
                record['payload']['state'] = state
                record['content_available'] = state == 'approved'
                record['current_version'] = current['version']
        return record

    def write_record(self, payload):
        fields = {'schema_version', 'integration_id', 'record_type', 'external_id', 'event_id', 'source_revision', 'source', 'occurred_at', 'payload', 'idempotency_key', 'expected_version', 'correlation_id', 'causation_id'}
        _object(payload, fields, fields - {'integration_id', 'expected_version', 'correlation_id', 'causation_id'})
        if payload['schema_version'] != CONTRACT_VERSION or not isinstance(payload['record_type'], str) or payload['record_type'] not in RECORD_TYPES:
            _error('紀錄種類或合約版本無效。')
        external_id, event_id = _text(payload['external_id']), _text(payload['event_id'])
        revision = _integer(payload['source_revision'])
        occurred = _timestamp(payload['occurred_at'])
        source = _text(payload['source'], 300)
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            registration = self._select(conn, payload.get('integration_id'))
            integration_id = registration['id']
            kind = payload['record_type']
            if kind not in registration['record_types'] or (self.integration_id and kind == 'knowledge'):
                _error('此整合沒有寫入這種紀錄的權限。', 403)
            existing = self._retry(conn, integration_id, 'record', payload['idempotency_key'], payload)
            if existing:
                return existing
            data = self._knowledge(payload['payload']) if kind == 'knowledge' else self._safe_payload(registration, payload['payload'])
            row = conn.execute('SELECT document FROM integration_records WHERE integration_id=? AND record_type=? AND external_id=?', (integration_id, kind, external_id)).fetchone()
            previous = json.loads(row[0]) if row else None
            if previous:
                self._expected(previous, payload.get('expected_version'))
                if revision <= previous['source_revision']:
                    _error('source_revision 需要遞增，不能覆蓋較新證據。', 409)
            elif 'expected_version' in payload and payload['expected_version'] is not None and (type(payload['expected_version']) is not int or payload['expected_version'] != 0):
                _error('新紀錄不能指定既有版本。', 409)
            self._source_event(conn, integration_id, event_id, payload)
            now = _now()
            record_id = previous['id'] if previous else uuid.uuid4().hex
            record = {'id': record_id, 'record_id': record_id, 'schema_version': CONTRACT_VERSION,
                      'integration_id': integration_id, 'record_type': kind, 'external_id': external_id,
                      'event_id': event_id, 'source_revision': revision, 'source': source, 'occurred_at': occurred,
                      'received_at': now, 'payload': data, 'version': previous['version'] + 1 if previous else 1,
                      'created_at': previous['created_at'] if previous else now, 'actor': self.actor}
            for key in ('correlation_id', 'causation_id'):
                if key in payload:
                    record[key] = _text(payload[key])
            conn.execute('INSERT INTO integration_records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET document=excluded.document', (record_id, integration_id, kind, external_id, _dump(record)))
            conn.execute('INSERT INTO integration_history VALUES(?,?,?)', (record_id, record['version'], _dump(record)))
            cursor = self._event(conn, integration_id, 'record', {'record': record}, occurred)
            response = {'record': record, 'record_id': record_id, 'version': record['version'], 'cursor': cursor, 'created': previous is None}
            self._receipt(conn, integration_id, 'record', payload['idempotency_key'], payload, response)
            return response

    def _record(self, conn, record_id):
        row = conn.execute('SELECT document FROM integration_records WHERE id=?', (_text(record_id),)).fetchone()
        if not row:
            _error('找不到此紀錄。', 404)
        record = json.loads(row[0])
        if self.integration_id:
            self._registry(conn, record['integration_id'])
        else:
            self._admin()
        return record

    def get_record(self, record_id):
        with self.store._connection() as conn:
            return self._visible_record(conn, self._record(conn, record_id))

    def history(self, record_id):
        with self.store._connection() as conn:
            self._record(conn, record_id)
            return [self._visible_record(conn, json.loads(row[0]), event=True) for row in conn.execute('SELECT document FROM integration_history WHERE record_id=? ORDER BY version', (record_id,))]

    def list_records(self, integration_id='', record_type='', external_id='', record_id='', limit=50, offset=0):
        limit, offset = _page(limit, offset)
        with self.store._connection() as conn:
            if self.integration_id:
                registration = self._registry(conn, integration_id or None)
                integration_id = registration['id']
            else:
                self._admin()
                registration = self._registry(conn, integration_id) if integration_id else None
            clauses, values = [], []
            for column, value in [('integration_id', integration_id), ('record_type', record_type), ('external_id', external_id), ('id', record_id)]:
                if value:
                    clauses.append(column + '=?')
                    values.append(_text(value))
            if self.integration_id:
                clauses.append('record_type IN (' + ','.join('?' for _ in registration['record_types']) + ')')
                values.extend(registration['record_types'])
            where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
            count = conn.execute('SELECT COUNT(*) FROM integration_records' + where, values).fetchone()[0]
            records = [self._visible_record(conn, json.loads(row[0])) for row in conn.execute("SELECT document FROM integration_records" + where + " ORDER BY json_extract(document,'$.received_at') DESC,id LIMIT ? OFFSET ?", [*values, limit, offset])]
            return {'records': records, 'total': count, 'limit': limit, 'offset': offset}

    def read_resource(self, kind, record_id):
        if not self.integration_id:
            self._admin()
        with self.store._connection() as conn:
            registration = self._registry(conn) if self.integration_id else None
            ref = {'kind': _text(kind, 80), 'id': _text(record_id, 80)}
            if self.integration_id and ref not in registration['resource_refs']:
                _error('此來源不在整合的授權範圍。', 403)
            member = Studio(self.store, {'username': self.actor, 'role': 'member'})
            member._reference(conn, ref)
            if kind == 'item':
                row = conn.execute('SELECT * FROM items WHERE id=?', (record_id,)).fetchone()
                if row['archived']:
                    _error('來源已封存。', 403)
                record = self.store._public(row)
            else:
                record = member._get(conn, record_id)
                if record['archived']:
                    _error('來源已封存。', 403)
            return {'resource': record, 'ref': ref, 'version': record['version']}

    def _command(self, conn, command_id):
        row = conn.execute('SELECT document FROM integration_commands WHERE id=?', (_text(command_id),)).fetchone()
        if not row:
            _error('找不到此指令。', 404)
        command = json.loads(row[0])
        if self.integration_id:
            self._registry(conn, command['integration_id'])
        else:
            self._admin()
        return command

    def _validate_command(self, conn, registration, command):
        declaration = next((c for c in registration['commands'] if c['name'] == command['name']), None)
        if not declaration:
            _error('指令不在目前核准的能力範圍。', 403)
        data = _payload(command['payload'])
        schema = declaration['input_schema']
        if set(data) - set(schema['properties']) or set(schema['required']) - set(data):
            _error('指令內容不符合核准欄位。')
        for key, value in data.items():
            _primitive(value, schema['properties'][key])
        if command.get('record_id'):
            record = self._record(conn, command['record_id'])
            if record['integration_id'] != registration['id'] or record['record_type'] not in registration['record_types']:
                _error('指令目標不在整合的授權範圍。', 403)

    def create_command(self, payload):
        self._admin()
        _object(payload, {'integration_id', 'name', 'payload', 'idempotency_key', 'record_id', 'correlation_id', 'control_epoch', 'input_cursor', 'expires_at'}, {'integration_id', 'name', 'payload', 'idempotency_key'})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            registration = self._select(conn, payload['integration_id'])
            existing = self._retry(conn, registration['id'], 'command', payload['idempotency_key'], {'actor': self.actor, **payload})
            if existing:
                return existing
            now = _now()
            command_id = uuid.uuid4().hex
            command = {'id': command_id, 'command_id': command_id, 'schema_version': CONTRACT_VERSION,
                       'integration_id': registration['id'], 'name': _text(payload['name'], 80),
                       'payload': _payload(payload['payload']), 'state': 'pending', 'version': 1, 'actor': self.actor,
                       'created_at': now, 'updated_at': now, 'results': []}
            for key in ('record_id', 'correlation_id', 'input_cursor'):
                if key in payload:
                    command[key] = _text(payload[key], 2048 if key == 'input_cursor' else 160)
            if 'control_epoch' in payload:
                command['control_epoch'] = _integer(payload['control_epoch'], 0)
            if 'expires_at' in payload:
                command['expires_at'] = _timestamp(payload['expires_at'])
                if command['expires_at'] <= now:
                    _error('新指令的有效期限已過。', 409)
            self._validate_command(conn, registration, command)
            conn.execute('INSERT INTO integration_commands VALUES(?,?,?,?)', (command_id, registration['id'], 'pending', _dump(command)))
            cursor = self._event(conn, registration['id'], 'command', {'command': command})
            response = {'command': command, 'command_id': command_id, 'version': 1, 'cursor': cursor, 'created': True}
            self._receipt(conn, registration['id'], 'command', payload['idempotency_key'], {'actor': self.actor, **payload}, response)
            return response

    def get_command(self, command_id):
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            command = self._command(conn, command_id)
            registration = self._registry(conn, command['integration_id'])
            self._pending_valid(conn, command, registration)
            return self._present_command(conn, command)

    def list_commands(self, integration_id='', limit=50, offset=0):
        limit, offset = _page(limit, offset)
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if self.integration_id:
                integration_id = self._registry(conn, integration_id or None)['id']
            else:
                self._admin()
                if integration_id:
                    self._registry(conn, integration_id)
            where, values = (' WHERE integration_id=?', [integration_id]) if integration_id else ('', [])
            count = conn.execute('SELECT COUNT(*) FROM integration_commands' + where, values).fetchone()[0]
            commands = [json.loads(row[0]) for row in conn.execute("SELECT document FROM integration_commands" + where + " ORDER BY json_extract(document,'$.created_at') DESC,id LIMIT ? OFFSET ?", [*values, limit, offset])]
            for command in commands:
                self._pending_valid(conn, command, self._registry(conn, command['integration_id']))
            commands = [self._present_command(conn, command) for command in commands]
            return {'commands': commands, 'total': count, 'limit': limit, 'offset': offset}

    def _present_command(self, conn, command):
        """Withhold an undeliverable intent on every external read surface."""
        if not self.integration_id:
            return command
        registration = self._registry(conn, command['integration_id'])
        permitted = command['state'] not in {'expired', 'rejected'}
        try:
            self._validate_command(conn, registration, command)
        except LibraryError:
            permitted = False
        result = json.loads(_dump(command))
        result['deliverable'] = permitted and command['state'] == 'pending'
        if not permitted:
            result['payload'] = {}
            result['redacted'] = True
            for key in ('control_epoch', 'input_cursor'):
                result.pop(key, None)
        return result

    def _save_result(self, conn, command, result):
        command['results'].append(result)
        command.update(state=result['status'], version=command['version'] + 1, updated_at=result['received_at'])
        conn.execute('UPDATE integration_commands SET state=?,document=? WHERE id=?', (command['state'], _dump(command), command['id']))
        return self._event(conn, command['integration_id'], 'command_result', {'command': command, 'result': result}, result['occurred_at'])

    def _actor_current(self, conn, actor):
        try:
            owner = json.loads((self.store.data_dir / 'access.json').read_text(encoding='utf-8'))['username']
        except (OSError, ValueError, KeyError):
            return False
        if actor == owner:
            return True
        row = conn.execute('SELECT role,enabled FROM studio_users WHERE username=?', (actor,)).fetchone()
        return bool(row and row['enabled'] and row['role'] in {'owner', 'admin'})

    def _pending_valid(self, conn, command, registration):
        if command['state'] != 'pending':
            return
        status, reason = None, None
        if command.get('expires_at') and command['expires_at'] <= _now():
            status, reason = 'expired', '指令在外部服務確認前已過期。'
        elif not registration['enabled'] or registration['revoked']:
            status, reason = 'rejected', '整合實例已停用或撤銷。'
        elif not self._actor_current(conn, command['actor']):
            status, reason = 'rejected', '原操作者目前沒有發出指令的權限。'
        else:
            try:
                self._validate_command(conn, registration, command)
            except LibraryError:
                status, reason = 'rejected', '目前授權或能力已改變，未交出此待處理指令。'
        if status:
            now = _now()
            result = {'event_id': uuid.uuid4().hex, 'status': status, 'payload': {'reason': reason},
                      'occurred_at': now, 'received_at': now, 'source': 'platform'}
            self._save_result(conn, command, result)

    def command_result(self, payload):
        fields = {'schema_version', 'command_id', 'event_id', 'idempotency_key', 'status', 'occurred_at', 'payload', 'control_epoch', 'input_cursor'}
        _object(payload, fields, fields - {'control_epoch', 'input_cursor'})
        if payload['schema_version'] != CONTRACT_VERSION or not isinstance(payload['status'], str) or payload['status'] not in RESULT_STATES:
            _error('指令結果狀態或合約版本無效。')
        if not self.integration_id:
            _error('執行結果必須由所屬外部服務回報。', 403)
        # Persist expiry/permission rejection even when the late inbound result
        # itself must subsequently fail with a conflict.
        self.get_command(payload['command_id'])
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            command = self._command(conn, payload['command_id'])
            registration = self._registry(conn)
            existing = self._retry(conn, registration['id'], 'result', payload['idempotency_key'], payload)
            if existing:
                existing['command'] = self._present_command(conn, existing['command'])
                return existing
            self._pending_valid(conn, command, registration)
            old, new = command['state'], payload['status']
            allowed = ({'accepted', 'running', 'completed', 'rejected', 'expired', 'failed', 'unknown'} if old == 'pending'
                       else {'running', 'completed', 'rejected', 'expired', 'failed', 'unknown'} if old == 'accepted'
                       else {'completed', 'failed', 'unknown'} if old == 'running'
                       else {'completed', 'failed', 'rejected'} if old == 'unknown' else set())
            if new not in allowed:
                _error('不能倒退或覆寫已確認的指令結果。', 409)
            data = _payload(payload['payload'])
            if old == 'unknown' and not data.get('evidence'):
                _error('結果不明的指令需要核對證據才能確認結果。', 409)
            event_id = _text(payload['event_id'])
            self._source_event(conn, registration['id'], event_id, payload)
            result = {'event_id': event_id, 'status': new, 'payload': data,
                      'occurred_at': _timestamp(payload['occurred_at']), 'received_at': _now(), 'source': 'integration'}
            for key in ('control_epoch', 'input_cursor'):
                if key in payload:
                    result[key] = _integer(payload[key], 0) if key == 'control_epoch' else _text(payload[key], 2048)
            cursor = self._save_result(conn, command, result)
            response = {'command': command, 'result': result, 'cursor': cursor, 'created': True}
            self._receipt(conn, registration['id'], 'result', payload['idempotency_key'], payload, response)
            response['command'] = self._present_command(conn, command)
            return response

    def events(self, cursor='', limit=50, integration_id=''):
        limit, _ = _page(limit)
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if self.integration_id:
                registration = self._registry(conn, integration_id or None)
                integration_id = registration['id']
            else:
                self._admin()
                registration = self._registry(conn, integration_id) if integration_id else None
            stream = integration_id or '*'
            position = self._position(conn, stream, cursor)
            if registration:
                for row in list(conn.execute("SELECT document FROM integration_commands WHERE integration_id=? AND state='pending'", (integration_id,))):
                    self._pending_valid(conn, json.loads(row[0]), registration)
            where = 'sequence>?' + (' AND integration_id=?' if integration_id else '')
            args = [position, integration_id] if integration_id else [position]
            rows = list(conn.execute('SELECT sequence,document FROM integration_events WHERE ' + where + ' ORDER BY sequence LIMIT ?', [*args, limit + 1]))
            result = []
            for row in rows[:limit]:
                event = json.loads(row['document'])
                if 'record' in event:
                    try:
                        event['record'] = self._visible_record(conn, event['record'], event=True)
                    except LibraryError as exc:
                        if exc.status == 403:
                            event = {key: event[key] for key in ('event_id', 'type', 'integration_id', 'occurred_at', 'received_at')}
                            event['redacted'] = True
                        else:
                            raise
                if 'command' in event and self.integration_id:
                    # Replays expose current delivery eligibility, never a stale
                    # pending snapshot after expiry, revocation or acceptance.
                    event['command'] = self._present_command(conn, self._command(conn, event['command']['id']))
                result.append(event)
            position = rows[min(limit, len(rows)) - 1]['sequence'] if rows else position
            return {'events': result, 'cursor': self._cursor(conn, stream, position), 'has_more': len(rows) > limit}
