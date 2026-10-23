"""Declarative Agent packages and explicit connected-client work handoff.

UI and MCP use this service and the existing private Store. This module does not
download repositories, execute package code, wake a chat or call a model.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid

from .store import LibraryError, _now
from .studio import Studio, _text

TERMINAL_STATES = {'completed', 'failed', 'cancelled'}
JOB_STATES = {'awaiting_codex', 'in_progress', *TERMINAL_STATES}
FIELD_TYPES = {'text', 'textarea', 'boolean', 'select'}


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise LibraryError('Agent 內容格式不正確，請依接駁規格填寫。')
    return value


def _bounded(value, maximum):
    try:
        if len(_dump(value).encode('utf-8')) <= maximum:
            return value
    except (ValueError, TypeError, RecursionError):
        pass
    raise LibraryError('Agent 內容超出大小上限或包含不支援的資料。')


def _key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', value) or value in {'constructor', 'prototype'}:
        raise LibraryError('Agent 功能及欄位編號需要使用小寫英文字母、數字、底線或短橫線。')
    return value


def _field_value(field, value):
    if field['type'] == 'boolean':
        if type(value) is not bool:
            raise LibraryError('請使用是或否：' + field['label'])
        return value
    value = _text(value, 40000 if field['type'] == 'textarea' else 1000, field.get('required', False))
    if field['type'] == 'select' and value not in {option['value'] for option in field['options']}:
        raise LibraryError('請選擇有效選項：' + field['label'])
    return value


def _fields(value):
    if not isinstance(value, list) or len(value) > 24:
        raise LibraryError('每組 Agent 設定或輸入最多 24 個欄位。')
    clean = []
    seen = set()
    for entry in value:
        _object(entry, {'key', 'label', 'type', 'required', 'default', 'options'}, {'key', 'label', 'type'})
        key = _key(entry['key'])
        if key in seen or not isinstance(entry['type'], str) or entry['type'] not in FIELD_TYPES:
            raise LibraryError('Agent 欄位重複或類型不支援。')
        seen.add(key)
        field = {'key': key, 'label': _text(entry['label'], 120, True), 'type': entry['type']}
        if type(entry.get('required', False)) is not bool:
            raise LibraryError('required 需要是或否。')
        field['required'] = entry.get('required', False)
        if field['type'] == 'select':
            options = entry.get('options')
            if not isinstance(options, list) or not 1 <= len(options) <= 30:
                raise LibraryError('選擇欄位需要 1 至 30 個選項。')
            field['options'] = []
            for option in options:
                _object(option, {'value', 'label'}, {'value', 'label'})
                field['options'].append({'value': _text(option['value'], 100, True), 'label': _text(option['label'], 120, True)})
            if len({v['value'] for v in field['options']}) != len(field['options']):
                raise LibraryError('選項值不能重複。')
        elif 'options' in entry:
            raise LibraryError('只有選擇欄位可提供 options。')
        if 'default' in entry:
            field['default'] = _field_value(field, entry['default'])
        clean.append(field)
    return clean


def _values(fields, values):
    _object(values, {field['key'] for field in fields})
    result = {}
    for field in fields:
        key = field['key']
        if key in values:
            result[key] = _field_value(field, values[key])
        elif 'default' in field:
            result[key] = field['default']
        elif field['required']:
            raise LibraryError('請填寫：' + field['label'])
    return _bounded(result, 64 * 1024)


def validate_manifest(value):
    """Validate the complete declarative v1 package; never import package code."""
    _bounded(value, 128 * 1024)
    _object(value, {'contract_version', 'id', 'name', 'version', 'description', 'adapter', 'instructions', 'settings', 'operations', 'ui'},
            {'contract_version', 'id', 'name', 'version', 'adapter', 'instructions', 'settings', 'operations', 'ui'})
    if type(value['contract_version']) is not int or value['contract_version'] != 1:
        raise LibraryError('這個 Agent 接駁規格版本尚未支援。')
    if value['adapter'] != 'connected_client':
        raise LibraryError('本版只支援由已連接 AI 對話處理工作的 Agent。')
    version = value['version']
    if not isinstance(version, str) or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?', version) or len(version) > 80:
        raise LibraryError('Agent 版本需要使用固定版本號，例如 1.0.0。')
    operations = value['operations']
    if not isinstance(operations, list) or not 1 <= len(operations) <= 16:
        raise LibraryError('Agent 需要提供 1 至 16 個操作。')
    clean_operations = []
    seen = set()
    for operation in operations:
        _object(operation, {'id', 'name', 'instructions', 'input_fields'}, {'id', 'name', 'instructions', 'input_fields'})
        key = _key(operation['id'])
        if key in seen:
            raise LibraryError('Agent 操作編號不能重複。')
        seen.add(key)
        clean_operations.append({'id': key, 'name': _text(operation['name'], 120, True),
                                 'instructions': _text(operation['instructions'], 16000, True),
                                 'input_fields': _fields(operation['input_fields'])})
    ui = _object(value['ui'], {'layout', 'result_label'}, {'layout'})
    if ui['layout'] != 'standard':
        raise LibraryError('本版 Agent 介面需要使用 standard 藍圖版面。')
    return {'contract_version': 1, 'id': _key(value['id']), 'name': _text(value['name'], 120, True), 'version': version,
            'description': _text(value.get('description', ''), 2000), 'adapter': 'connected_client',
            'instructions': _text(value['instructions'], 16000, True), 'settings': _fields(value['settings']),
            'operations': clean_operations, 'ui': {'layout': 'standard', 'result_label': _text(ui.get('result_label', '工作結果'), 120, True)}}


def _source(value):
    _object(value, {'repository', 'revision'}, {'repository', 'revision'})
    repository = value['repository']
    if not isinstance(repository, str) or not re.fullmatch(r'https://github\.com/[A-Za-z0-9][A-Za-z0-9-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', repository):
        raise LibraryError('請提供不含帳密的 GitHub 倉庫網址。')
    revision = value['revision']
    if not isinstance(revision, str) or not re.fullmatch(r'[a-fA-F0-9]{40}', revision):
        raise LibraryError('請固定已核對的完整 Git commit，不能使用 main 或浮動版本。')
    return {'repository': repository, 'revision': revision.lower()}


def _agent_summary(agent):
    result = {key: agent[key] for key in ['id', 'source', 'enabled', 'connection_state', 'version', 'created_at', 'updated_at', 'actor']}
    manifest = agent['manifest']
    result['manifest'] = {key: manifest[key] for key in ['id', 'name', 'version']}
    result['manifest']['description'] = manifest['description'][:500]
    result['detail_available'] = True
    result['truncated_fields'] = ['manifest.description'] if len(manifest['description']) > 500 else []
    return result


def _job_summary(job):
    result = {key: job[key] for key in ['id', 'agent_id', 'operation_id', 'title', 'state', 'version', 'created_at', 'updated_at', 'actor']}
    result['agent_snapshot'] = {'manifest': {key: job['agent_snapshot']['manifest'][key] for key in ['id', 'name', 'version']}}
    result.update(detail_available=True, truncated_fields=[])
    for key in ['error', 'result']:
        result[key] = job[key][:500]
        if len(job[key]) > 500:
            result['truncated_fields'].append(key)
    return result


def initialize(conn):
    for statement in (
        '''CREATE TABLE IF NOT EXISTS agent_registrations (
            id TEXT PRIMARY KEY, package_key TEXT UNIQUE NOT NULL, document TEXT NOT NULL,
            version INTEGER NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE IF NOT EXISTS agent_jobs (
            id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agent_registrations(id),
            state TEXT NOT NULL, document TEXT NOT NULL, version INTEGER NOT NULL,
            source_key TEXT, request_digest TEXT NOT NULL, creator TEXT NOT NULL,
            updated_at TEXT NOT NULL, UNIQUE(creator, source_key))''',
        '''CREATE TABLE IF NOT EXISTS agent_history (
            entity TEXT NOT NULL, record_id TEXT NOT NULL, version INTEGER NOT NULL,
            snapshot TEXT NOT NULL, PRIMARY KEY(entity, record_id, version))''',
        'CREATE INDEX IF NOT EXISTS agent_jobs_state ON agent_jobs(agent_id, state, updated_at DESC)',
    ):
        conn.execute(statement)


class Agents:
    def __init__(self, store, identity=None):
        self.store = store
        self.studio = Studio(store, identity)
        self.actor = self.studio.actor

    def _admin(self):
        if not self.studio.admin:
            raise LibraryError('請由管理員接入或設定 Agent。', 403)

    @staticmethod
    def _expected(record, value):
        if type(value) is not int or value != record['version']:
            raise LibraryError('內容已有更新，請重新讀取後再修改。', 409)

    @staticmethod
    def _history(conn, entity, record):
        conn.execute('INSERT INTO agent_history VALUES(?,?,?,?)', (entity, record['id'], record['version'], _dump(record)))

    def _get_agent(self, conn, agent_id):
        row = conn.execute('SELECT document FROM agent_registrations WHERE id=?', (_text(agent_id, 80, True),)).fetchone()
        if not row:
            raise LibraryError('找不到這個 Agent。', 404)
        return json.loads(row['document'])

    def _save_agent(self, conn, record):
        record = {**record, 'version': record['version'] + 1, 'updated_at': _now(), 'actor': self.actor}
        conn.execute('UPDATE agent_registrations SET document=?,version=?,updated_at=? WHERE id=?',
                     (_dump(record), record['version'], record['updated_at'], record['id']))
        self._history(conn, 'agent', record)
        return record

    def register(self, payload):
        self._admin()
        _object(payload, {'manifest', 'source', 'settings'}, {'manifest', 'source'})
        manifest = validate_manifest(payload['manifest'])
        source = _source(payload['source'])
        settings = _values(manifest['settings'], payload.get('settings', {}))
        key = _digest({'id': manifest['id'], 'version': manifest['version'], 'source': source})
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing = conn.execute('SELECT document FROM agent_registrations WHERE package_key=?', (key,)).fetchone()
            if existing:
                record = json.loads(existing['document'])
                if record['manifest'] != manifest:
                    raise LibraryError('同一個固定來源已有不同內容，請核對 Agent 套件。', 409)
                return {'agent': record, 'created': False}
            now = _now()
            record = {'id': uuid.uuid4().hex, 'manifest': manifest, 'source': source, 'settings': settings,
                      'enabled': True, 'connection_state': 'added', 'version': 1, 'created_at': now,
                      'updated_at': now, 'actor': self.actor}
            conn.execute('INSERT INTO agent_registrations VALUES(?,?,?,?,?)', (record['id'], key, _dump(record), 1, now))
            self._history(conn, 'agent', record)
            return {'agent': record, 'created': True}

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 100000:
            raise LibraryError('分頁範圍不正確。')

    def list_agents(self, limit=50, offset=0):
        self._page(limit, offset)
        with self.store._connection() as conn:
            total = conn.execute('SELECT COUNT(*) FROM agent_registrations').fetchone()[0]
            rows = conn.execute('SELECT document FROM agent_registrations ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', (limit, offset))
            return {'agents': [_agent_summary(json.loads(row['document'])) for row in rows], 'total': total, 'offset': offset, 'limit': limit}

    def get_agent(self, agent_id):
        with self.store._connection() as conn:
            return self._get_agent(conn, agent_id)

    def update_agent(self, agent_id, payload):
        self._admin()
        _object(payload, {'expected_version', 'settings', 'enabled'}, {'expected_version'})
        if set(payload) == {'expected_version'}:
            raise LibraryError('請提供要修改的設定。')
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._get_agent(conn, agent_id)
            self._expected(record, payload['expected_version'])
            if 'settings' in payload:
                record['settings'] = _values(record['manifest']['settings'], payload['settings'])
            if 'enabled' in payload:
                if type(payload['enabled']) is not bool:
                    raise LibraryError('是否啟用需要是或否。')
                record['enabled'] = payload['enabled']
            return self._save_agent(conn, record)

    def agent_action(self, agent_id, payload):
        self._admin()
        _object(payload, {'expected_version', 'action', 'arguments'}, {'expected_version', 'action'})
        _object(payload.get('arguments', {}), set())
        if not isinstance(payload['action'], str) or payload['action'] not in {'connect', 'disconnect'}:
            raise LibraryError('這個 Agent 不支援此操作。')
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            record = self._get_agent(conn, agent_id)
            self._expected(record, payload['expected_version'])
            # Validates only this backend's declarative adapter, not chat presence.
            validate_manifest(record['manifest'])
            record['connection_state'] = 'connected' if payload['action'] == 'connect' else 'added'
            return self._save_agent(conn, record)

    def _refs(self, conn, values):
        if not isinstance(values, list) or len(values) > 30:
            raise LibraryError('每項 Agent 工作最多選擇 30 個來源。')
        clean = []
        visibility = 'team'
        for ref in values:
            _object(ref, {'kind', 'id'}, {'kind', 'id'})
            # The existing Studio verifier trims IDs when checking existence;
            # persist that same canonical ID so later context lookups agree.
            ref = {'kind': ref['kind'], 'id': _text(ref['id'], 80, True)}
            if self.studio._reference(conn, ref) == 'admin':
                visibility = 'admin'
            if ref not in clean:
                clean.append(dict(ref))
        return clean, visibility

    def _authorize_job(self, conn, job):
        if job['visibility'] == 'admin' and not self.studio.admin:
            raise LibraryError('你的帳戶沒有這項工作權限。', 403)
        for ref in job['refs']:
            try:
                self.studio._reference(conn, ref)
            except LibraryError as error:
                # Administrators retain the ability to inspect/cancel work after
                # a source is removed. Reading/executing context still fails.
                if error.status == 404 and self.studio.admin:
                    continue
                raise

    def _get_job(self, conn, job_id):
        row = conn.execute('SELECT document FROM agent_jobs WHERE id=?', (_text(job_id, 80, True),)).fetchone()
        if not row:
            raise LibraryError('找不到這項 Agent 工作。', 404)
        job = json.loads(row['document'])
        self._authorize_job(conn, job)
        return job

    def create_job(self, payload):
        _object(payload, {'agent_id', 'operation_id', 'title', 'inputs', 'refs', 'source_key'}, {'agent_id', 'operation_id', 'title', 'inputs'})
        title = _text(payload['title'], 240, True)
        source_key = _text(payload['source_key'], 200, True) if 'source_key' in payload else None
        request_digest = _digest(_bounded(payload, 96 * 1024))
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if source_key:
                existing = conn.execute('SELECT id,request_digest FROM agent_jobs WHERE creator=? AND source_key=?', (self.actor, source_key)).fetchone()
                if existing:
                    if existing['request_digest'] != request_digest:
                        raise LibraryError('同一個工作來源編號已有不同內容，請使用新的編號。', 409)
                    return {'job': self._get_job(conn, existing['id']), 'created': False}
            agent = self._get_agent(conn, payload['agent_id'])
            operation = next((value for value in agent['manifest']['operations'] if value['id'] == payload['operation_id']), None)
            if not operation:
                raise LibraryError('這個 Agent 沒有提供所選操作。')
            inputs = _values(operation['input_fields'], payload['inputs'])
            refs, visibility = self._refs(conn, payload.get('refs', []))
            now = _now()
            snapshot = {key: agent[key] for key in ['manifest', 'source', 'settings', 'version']}
            job = {'id': uuid.uuid4().hex, 'agent_id': agent['id'], 'operation_id': operation['id'], 'title': title,
                   'inputs': inputs, 'refs': refs, 'agent_snapshot': snapshot, 'visibility': visibility,
                   'state': 'awaiting_codex', 'context_digest': '', 'result': '', 'result_source': '', 'source_refs': [],
                   'error': '', 'started_by_client': '', 'source_versions': [], 'version': 1, 'created_at': now, 'updated_at': now, 'actor': self.actor}
            conn.execute('INSERT INTO agent_jobs VALUES(?,?,?,?,?,?,?,?,?)',
                         (job['id'], job['agent_id'], job['state'], _dump(job), 1, source_key, request_digest, self.actor, now))
            self._history(conn, 'job', job)
            return {'job': job, 'created': True}

    def get_job(self, job_id):
        with self.store._connection() as conn:
            return self._get_job(conn, job_id)

    def list_jobs(self, agent_id='', state='', limit=50, offset=0):
        self._page(limit, offset)
        if not isinstance(state, str) or (state and state not in JOB_STATES):
            raise LibraryError('工作狀態不正確。')
        clauses, parameters = [], []
        if agent_id:
            clauses.append('agent_id=?')
            parameters.append(_text(agent_id, 80, True))
        if state:
            clauses.append('state=?')
            parameters.append(state)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.store._connection() as conn:
            visible = []
            total = 0
            # Authorization precedes pagination/counts, so restricted jobs never
            # create leaked counts or empty pages revealing another user's work.
            for row in conn.execute('SELECT document FROM agent_jobs' + where + ' ORDER BY updated_at DESC,id', parameters):
                job = json.loads(row['document'])
                try:
                    self._authorize_job(conn, job)
                except LibraryError as error:
                    if error.status in {403, 404}:
                        continue
                    raise
                if offset <= total < offset + limit:
                    visible.append(_job_summary(job))
                total += 1
            return {'jobs': visible, 'total': total, 'offset': offset, 'limit': limit}

    def _context(self, conn, job):
        sources = []
        for ref in job['refs']:
            source = self.studio._resolve(conn, ref)
            table = {'item': 'items', 'workday': 'workdays', 'todo': 'workday_entries'}.get(ref['kind'])
            if table:
                # The legacy context presentation intentionally omits some
                # fields. Include its revision so any intervening edit matters.
                row = conn.execute(f'SELECT version FROM {table} WHERE id=?', (ref['id'],)).fetchone()
                source['version'] = row['version']
            sources.append(source)
        _bounded(sources, 256 * 1024)
        operation = next(value for value in job['agent_snapshot']['manifest']['operations'] if value['id'] == job['operation_id'])
        context = {'job_id': job['id'], 'agent': job['agent_snapshot'], 'operation': operation, 'inputs': job['inputs'], 'sources': sources}
        return {'job': job, 'agent': job['agent_snapshot'], 'operation': operation, 'sources': sources, 'sources_current': True,
                'context_digest': _digest(context)}

    def context(self, job_id):
        with self.store._connection() as conn:
            return self._context(conn, self._get_job(conn, job_id))

    def _save_job(self, conn, job):
        job = {**job, 'version': job['version'] + 1, 'updated_at': _now(), 'actor': self.actor}
        conn.execute('UPDATE agent_jobs SET state=?,document=?,version=?,updated_at=? WHERE id=?',
                     (job['state'], _dump(job), job['version'], job['updated_at'], job['id']))
        self._history(conn, 'job', job)
        return job

    def job_action(self, job_id, payload):
        _object(payload, {'expected_version', 'action', 'arguments'}, {'expected_version', 'action'})
        action = payload['action']
        arguments = payload.get('arguments', {})
        allowed = {'start': {'source', 'context_digest'}, 'complete': {'result', 'source', 'source_refs', 'context_digest'},
                   'fail': {'reason'}, 'cancel': set()}
        if not isinstance(action, str) or action not in allowed:
            raise LibraryError('這項工作不支援此操作。')
        _object(arguments, allowed[action], allowed[action])
        with self.store._connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            job = self._get_job(conn, job_id)
            self._expected(job, payload['expected_version'])
            if job['state'] in TERMINAL_STATES:
                raise LibraryError('這項工作已結束，請建立新的工作。', 409)
            if action in {'start', 'complete'}:
                agent = self._get_agent(conn, job['agent_id'])
                if not agent['enabled'] or agent['connection_state'] != 'connected':
                    raise LibraryError('Agent 尚未接入功能或已停用，工作仍未完成。', 409)
                context = self._context(conn, job)
                digest = context['context_digest']
                if arguments['context_digest'] != digest:
                    raise LibraryError('來源內容已有更新，請重新讀取工作內容。', 409)
            if action == 'start':
                if job['state'] != 'awaiting_codex':
                    raise LibraryError('這項工作已開始，請讀取目前狀態。', 409)
                job.update(state='in_progress', context_digest=digest,
                           started_by_client=_text(arguments['source'], 120, True),
                           source_versions=[{'kind': ref['kind'], 'id': ref['id'], 'version': source['version'], 'title': source['title']}
                                            for ref, source in zip(job['refs'], context['sources'])])
            elif action == 'complete':
                if job['state'] != 'in_progress' or digest != job['context_digest']:
                    raise LibraryError('工作尚未開始或開始後來源已變更。請取消後建立新工作。', 409)
                source = _text(arguments['source'], 120, True)
                if source != job['started_by_client']:
                    raise LibraryError('結果來源需要與開始工作的 AI 客戶端一致。', 409)
                refs, _ = self._refs(conn, arguments['source_refs'])
                if any(ref not in job['refs'] for ref in refs) or (job['refs'] and not refs):
                    raise LibraryError('結果來源需要選自這項工作的指定資料。')
                job.update(state='completed', result=_text(arguments['result'], 40000, True), result_source=source, source_refs=refs)
            elif action == 'fail':
                job.update(state='failed', error=_text(arguments['reason'], 8000, True))
            else:
                job['state'] = 'cancelled'
            return self._save_job(conn, job)

    def history(self, entity, record_id):
        if entity not in {'agent', 'job'}:
            raise LibraryError('找不到此類紀錄。', 404)
        with self.store._connection() as conn:
            if entity == 'job':
                self._get_job(conn, record_id)
            else:
                self._admin()
                self._get_agent(conn, record_id)
            rows = conn.execute('SELECT snapshot FROM agent_history WHERE entity=? AND record_id=? ORDER BY version DESC LIMIT 100', (entity, record_id))
            return [json.loads(row['snapshot']) for row in rows]
