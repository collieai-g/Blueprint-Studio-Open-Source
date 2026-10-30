"""Private owner-scoped installation/update receipts; never a deployment executor.

This ledger lives beside backups, outside the company database and its coverage.
One managed service owns that directory through Maintenance's data lease.
"""
from __future__ import annotations

from blueprint_folders.path_safety import has_link

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import threading
import time

from .store import LibraryError

STAGES = ('discover', 'plan', 'backup_gate', 'apply', 'publish_if_needed', 'verify', 'complete')
CHANGE_TYPES = ('source_release', 'daily_data', 'compatible_package', 'ui', 'persistence', 'schema_identity', 'fresh_install')
FIELDS = {'stage', 'target_services', 'protected_services', 'change_type', 'sources', 'evidence', 'next_step', 'layout_change'}
SOURCE_FIELDS = {'upstream_commit', 'adapter_version', 'private_commit', 'platform_commit',
                 'artifact_sha256', 'saved_version', 'product_version'}
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        raise LibraryError('工作識別碼格式不正確。')
    return value


def plan_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise LibraryError('計劃需要完整 SHA256。')
    return value


def _text(value, maximum):
    return isinstance(value, str) and len(value) <= maximum and not re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value)


def verification_plan(change, targets=None, protected=None, sources=None, layout_change=False):
    """Required checks by scope, not a claim that any check has run."""
    from .lifecycle import plan
    try:
        value = plan({'contract_version': 1, 'task': change, 'target_services': targets or [],
                      'protected_services': protected or [], 'source': sources or {}, 'layout_change': layout_change})
        return {key: value[key] for key in ('task', 'required_checks', 'optional_checks', 'backup_policy',
                                            'publish_services', 'phases', 'waiting_stage')}
    except ValueError as error:
        raise LibraryError(str(error)) from None


def _validate_service_changes(data):
    for field in ('target_services', 'protected_services'):
        if field in data and (not isinstance(data[field], list) or len(data[field]) > 12 or any((not _text(value, 300) or not value for value in data[field]))):
            raise LibraryError('服務清單格式不正確。')


def _validate_sources(values):
    if not isinstance(values, dict) or set(values) - SOURCE_FIELDS:
        raise LibraryError('來源證據欄位不正確。')
    for key, value in values.items():
        if not _text(value, 500):
            raise LibraryError('來源證據格式不正確。')
        if key.endswith('_commit') and (not re.fullmatch('[0-9a-f]{40}', value)):
            raise LibraryError('來源提交需要完整 commit。')
        if key == 'artifact_sha256':
            plan_digest(value)


def _validate_evidence(value):
    if not isinstance(value, dict) or len(value) > 80:
        raise LibraryError('證據必須是有界 JSON 物件。')
    forbidden = {'password', 'token', 'authorization', 'cookie', 'secret', 'credential', 'content_base64'}

    def inspect(node, depth=0):
        if depth > 8:
            raise LibraryError('證據內容過深。')
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str) or not _text(key, 100) or any((term in key.lower() for term in forbidden)):
                    raise LibraryError('證據不能包含憑證或完整私人回應。')
                inspect(child, depth + 1)
        elif isinstance(node, list):
            if len(node) > 100:
                raise LibraryError('證據清單過長。')
            for child in node:
                inspect(child, depth + 1)
        elif not (node is None or type(node) in (bool, int, float) or _text(node, 4000)):
            raise LibraryError('證據格式不正確。')
    inspect(value)


def _changes(data):
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise LibraryError('工作更新欄位不正確。')
    if 'stage' in data and data['stage'] not in (*STAGES, 'waiting_user'):
        raise LibraryError('工作階段不正確。')
    if 'change_type' in data and data['change_type'] not in CHANGE_TYPES:
        raise LibraryError('變更類型不正確。')
    if 'next_step' in data and (not _text(data['next_step'], 2000)):
        raise LibraryError('下一步文字過長或格式不正確。')
    _validate_service_changes(data)
    if 'layout_change' in data and type(data['layout_change']) is not bool:
        raise LibraryError('layout_change 需要是或否。')
    if 'sources' in data:
        _validate_sources(data['sources'])
    if 'evidence' in data:
        _validate_evidence(data['evidence'])
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError):
        raise LibraryError('工作內容必須是有效 JSON。') from None
    if len(encoded) > 32768:
        raise LibraryError('工作證據超出大小上限。', 413)
    return deepcopy(data)



def _write_request(body, operation_id):
    if not isinstance(body, dict) or set(body) - {'contract_version', 'operation_id', 'plan_digest', 'request_id', 'expected_version', 'changes'}:
        raise LibraryError('安裝更新工作請求格式不正確。')
    if type(body.get('contract_version')) is not int or body['contract_version'] != 1:
        raise LibraryError('不支援此安裝更新合約版本。')
    op = identifier(body.get('operation_id', operation_id))
    if operation_id is not None and op != operation_id:
        raise LibraryError('工作識別碼與網址不一致。')
    digest = plan_digest(body.get('plan_digest'))
    request = identifier(body.get('request_id'))
    expected = body.get('expected_version')
    if type(expected) is not int or expected < 0:
        raise LibraryError('請提供 expected_version。')
    changes = _changes(body.get('changes'))
    request_digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return op, digest, request, expected, changes, request_digest


class Operations:
    def __init__(self, directory):
        from .backup import external, safe_path
        self.directory = safe_path(directory)
        external(self.directory)
        self.path = self.directory / 'operations.json'
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.path), threading.RLock())

    def _read(self):
        if has_link(self.path):
            raise LibraryError('私人工作紀錄無法安全讀取。', 503)
        if not self.path.exists():
            return {'format_version': 1, 'operations': {}}
        if self.path.stat().st_size > 16 * 1024 * 1024:
            raise LibraryError('私人工作紀錄無法安全讀取。', 503)
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('format_version') != 1 or not isinstance(value['operations'], dict):
                raise ValueError
            return value
        except (OSError, ValueError, KeyError, TypeError):
            raise LibraryError('私人工作紀錄損壞；請保留檔案並核對，不能重新開始。', 503) from None

    @staticmethod
    def _record(value, operation_id, actor):
        record = value['operations'].get(operation_id)
        if not record or record['owner'] != actor:
            raise LibraryError('找不到此安裝更新工作。', 404)
        return record

    @staticmethod
    def _public(record):
        return deepcopy({key: value for key, value in record.items() if key != '_requests'})

    def list(self, actor):
        with self.lock:
            records = [self._public(value) for value in self._read()['operations'].values() if value['owner'] == actor]
            return {'contract_version': 1, 'operations': sorted(records, key=lambda value: value['updated_at'], reverse=True)}

    def get(self, operation_id, actor):
        identifier(operation_id)
        with self.lock:
            return {'operation': self._public(self._record(self._read(), operation_id, actor))}

    def _continue_record(self, document, op, actor, digest, request, request_digest, expected, changes):
        current = self._record(document, op, actor)
        if current['plan_digest'] != digest:
            raise LibraryError('同一工作不能改用另一份計劃；請先讀回原紀錄。', 409)
        previous = current['_requests'].get(request)
        if previous:
            if previous['digest'] != request_digest:
                raise LibraryError('同一請求識別碼已用於不同內容。', 409)
            return (None, {'operation': deepcopy(previous['result'])})
        if current['version'] != expected:
            raise LibraryError('工作紀錄已改變；請先讀回再接續。', 409)
        if current['stage'] == 'complete':
            raise LibraryError('此工作已完成；不會重新發佈。', 409)
        if 'change_type' in changes and changes['change_type'] != current['change_type']:
            raise LibraryError('原計劃變更類型不能覆寫。', 409)
        if 'target_services' in changes and changes['target_services'] != current['target_services']:
            raise LibraryError('原計劃目標不能覆寫。', 409)
        if 'protected_services' in changes and changes['protected_services'] != current.get('protected_services', []):
            raise LibraryError('原計劃保護的服務不能覆寫。', 409)
        if 'layout_change' in changes and changes['layout_change'] != current.get('layout_change', False):
            raise LibraryError('原計劃排版範圍不能覆寫。', 409)
        record = deepcopy(current)
        return record, None

    def _new_record(self, document, op, actor, digest, expected, changes):
        if expected != 0:
            raise LibraryError('找不到預期版本；請先讀回工作紀錄。', 409)
        if len(document['operations']) >= 100:
            raise LibraryError('私人工作紀錄已達上限，請先保存及整理舊紀錄。', 409)
        if changes.get('stage', 'discover') != 'discover' or not {'change_type', 'target_services'} <= set(changes):
            raise LibraryError('新工作須從 discover 開始並列出變更類型及目標。')
        record = {'operation_id': op, 'plan_digest': digest, 'owner': actor, 'version': 0, 'stage': 'discover', 'sources': {}, 'evidence': {}, 'next_step': '', 'protected_services': [], 'layout_change': False, 'created_at': time.time(), '_requests': {}}
        return record

    @staticmethod
    def _advance_record(record, changes):
        stage = changes.get('stage', record['stage'])
        previous_stage = record.get('resume_stage', record['stage']) if record['stage'] == 'waiting_user' else record['stage']
        if stage == 'waiting_user':
            if not changes.get('next_step', record.get('next_step')):
                raise LibraryError('等待本人操作時需要一個明確下一步。')
            record['resume_stage'] = previous_stage
        else:
            if stage != previous_stage and STAGES.index(stage) != STAGES.index(previous_stage) + 1:
                raise LibraryError('請從未完成階段接續，不能跳過或重做已完成階段。', 409)
            record.pop('resume_stage', None)
        record.update({key: value for key, value in changes.items() if key not in {'sources', 'evidence'}})
        for field in ('sources', 'evidence'):
            record[field].update(changes.get(field, {}))
        record.update(version=record['version'] + 1, updated_at=time.time())
        record['verification_plan'] = verification_plan(record['change_type'], record['target_services'], record['protected_services'], record['sources'], record['layout_change'])

    def _commit_record(self, document, record, op, request, request_digest):
        result = self._public(record)
        if len(record['_requests']) >= 128:
            raise LibraryError('本工作請求過多；請保存現有紀錄再開始新的計劃。', 409)
        record['_requests'][request] = {'digest': request_digest, 'result': result}
        document['operations'][op] = record
        if len(json.dumps(document, ensure_ascii=False).encode()) > 16 * 1024 * 1024:
            raise LibraryError('私人工作紀錄已達容量上限，請先保存及整理舊紀錄。', 409)
        from .maintenance import atomic_json
        atomic_json(self.path, document)
        return {'operation': result}

    def write(self, body, actor, operation_id=None):
        op, digest, request, expected, changes, request_digest = _write_request(body, operation_id)
        with self.lock:
            document = self._read()
            current = document['operations'].get(op)
            if current:
                record, replay = self._continue_record(document, op, actor, digest, request,
                                                       request_digest, expected, changes)
                if replay is not None:
                    return replay
            else:
                record = self._new_record(document, op, actor, digest, expected, changes)
            self._advance_record(record, changes)
            return self._commit_record(document, record, op, request, request_digest)
