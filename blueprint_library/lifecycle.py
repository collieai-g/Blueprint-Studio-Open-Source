"""One versioned flow definition for owner operations and read-only CLI plans.

No service access, filesystem writes, network calls or deployment execution.
"""
from __future__ import annotations

import hashlib
import json
import re

PHASES = ['discover', 'plan', 'backup_gate', 'apply', 'publish_if_needed', 'verify', 'complete']
ROUTES = {
    'source_release': (['source_review', 'affected_tests', 'release_artifacts'], [], 'none'),
    'daily_data': (['authorization', 'version_check', 'same_id_readback'], [], 'none'),
    'compatible_package': (['package_preflight', 'authorization', 'version_check', 'same_id_readback'], [], 'policy'),
    'ui': (['affected_ui', 'assets', 'accessibility', 'existing_data_readonly'], [], 'policy'),
    'persistence': (['authorization', 'cas', 'retry', 'rollback', 'one_live_ai_roundtrip', 'restart_persistence'], [], 'current'),
    'schema_identity': (['current_consistent_backup', 'private_copy_readback', 'migration', 'restore',
                         'old_rows_and_attachments', 'authorization', 'one_live_ai_roundtrip'], [], 'current'),
    'fresh_install': (['independent_service_and_storage', 'persistent_paths', 'backup_download_restore',
                       'restart_persistence', 'one_live_ai_roundtrip', 'ui_acceptance'], [], 'new_service'),
}
SOURCE_FIELDS = {'upstream_commit', 'adapter_version', 'private_commit', 'platform_commit',
                 'artifact_sha256', 'saved_version', 'product_version'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _text(value, limit=300):
    if not isinstance(value, str) or not value or len(value) > limit or re.search(r'[\x00-\x1f\x7f]', value):
        raise ValueError('請使用有界限、沒有控制字元的文字。')
    return value


def provenance(value):
    if not isinstance(value, dict) or set(value) - SOURCE_FIELDS:
        raise ValueError('來源證據欄位不正確。')
    clean = {}
    for key, item in value.items():
        item = _text(item)
        if key.endswith('_commit') and not re.fullmatch(r'[0-9a-f]{40}', item):
            raise ValueError('來源 commit 必須是完整的 40 位小寫雜湊。')
        if key == 'artifact_sha256' and not re.fullmatch(r'[0-9a-f]{64}', item):
            raise ValueError('產物 SHA256 必須是完整的 64 位小寫雜湊。')
        clean[key] = item
    return clean


def plan(value):
    """Return a deterministic plan; no timestamp or self-referential commit."""
    fields = {'contract_version', 'task', 'target_services', 'protected_services', 'source', 'layout_change'}
    if not isinstance(value, dict) or set(value) - fields or type(value.get('contract_version')) is not int or value['contract_version'] != 1:
        raise ValueError('安裝更新計劃需要 contract_version=1。')
    task = value.get('task')
    if not isinstance(task, str) or task not in ROUTES:
        raise ValueError('未知的安裝更新任務。')
    def services(name):
        items = value.get(name, [])
        if not isinstance(items, list) or len(items) > 12:
            raise ValueError('服務清單格式不正確。')
        return sorted(set(_text(item) for item in items))
    targets, protected = services('target_services'), services('protected_services')
    if set(targets) & set(protected):
        raise ValueError('目標服務不能同時列為不可變更的舊服務。')
    if task in {'source_release', 'daily_data', 'compatible_package'} and targets:
        raise ValueError('這類任務毋須部署服務；不要把資料操作變成部署。')
    layout = value.get('layout_change', False)
    if type(layout) is not bool:
        raise ValueError('layout_change 需要是或否。')
    required, optional, backup_policy = ROUTES[task]
    required = list(required)
    if layout and task not in {'source_release', 'daily_data', 'compatible_package'}:
        required += ['browser_layout_hash', 'layout_baseline_if_not_saved']
    result = {'contract_version': 1, 'task': task, 'target_services': targets, 'protected_services': protected,
              'source': provenance(value.get('source', {})), 'layout_change': layout,
              'phases': PHASES.copy(), 'waiting_stage': 'waiting_user', 'required_checks': required,
              'optional_checks': optional.copy(), 'backup_policy': backup_policy,
              'publish_services': targets.copy(), 'production_status': 'not_verified'}
    result['plan_digest'] = digest(result)
    return result
