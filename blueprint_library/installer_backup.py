"""Reuse native protection receipts instead of building a second backup engine."""
from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import os
import time
import uuid

from . import backup
from .maintenance import Maintenance, atomic_json


def checkpoint(config, operation_id):
    """Caller owns the stopped studio's DataLease for the whole transition."""
    if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', operation_id):
        raise ValueError('更新操作識別碼不正確。')
    data = backup.safe_path(config['data_dir'])
    directory = backup.safe_path(config['backup_dir'])
    recovery = backup.safe_path(config['recovery_dir'])
    marker_dir = backup.safe_path(directory / ('installer-' + operation_id))
    marker = marker_dir / 'installer-receipt.json'
    coverage = backup.coverage(data)
    value = None
    if marker.exists():
        value = json.loads(marker.read_text(encoding='utf-8'))
        if value['coverage'] != coverage:
            raise ValueError('同一更新操作的資料已改變，請先核對原操作。')
        archive = directory / value['native_backup_id'] / 'backup.zip'
        backup.check(archive.parent)
        if backup.digest(archive) != value['sha256'] or backup.digest(backup.safe_path(value['private_copy'])) != value['sha256']:
            raise ValueError('原操作的保護副本未能核對，未重造備份。')
    elif marker_dir.exists():
        raise ValueError('保護結果未知，請先讀回原操作。')
    native = Maintenance(data, directory)
    state_path = backup.safe_path(directory / 'installation.json')
    if state_path.exists():
        native.state = json.loads(state_path.read_text(encoding='utf-8'))
        if native.state.get('format') != 'blueprint-native-installation' or native.state.get('data') != str(data):
            raise ValueError('保護紀錄屬於另一個工作室。')
        if native.state.get('pending'):
            raise ValueError('原生資料仍有未完成遷移，未繼續更新。')
    else:
        if any(directory.iterdir()):
            raise ValueError('備份位置身份不明，未建立另一套紀錄。')
        native.state = {'format': 'blueprint-native-installation', 'version': 1,
                        'data': str(data), 'status': 'preparing', 'pending': None,
                        'service_id': uuid.uuid4().hex, 'backup_requests': {}, 'client_copies': {}}
        native.save()
    owner = json.loads((data / 'access.json').read_text(encoding='utf-8'))['username']
    if value is not None:
        original = native.backup_info(value['native_backup_id'], actor=owner)
        protection = native._protection(original, native._coverage(), owner)
        if not all(protection[key] for key in ('platform_verified', 'restore_verified',
                                               'current_coverage_matches', 'retention_valid')):
            raise ValueError('原操作保護已過期或未通過核對；先處理原操作，不覆寫舊備份。')
        return value
    info = None
    last = native.state.get('last_backup')
    if last:
        try:
            previous = native.backup_info(last['id'], actor=owner)
            protection = native._protection(previous, native._coverage(), owner)
            if (protection['platform_verified'] and protection['restore_verified'] and
                    protection['current_coverage_matches'] and protection['retention_valid']):
                info = previous
        except (OSError, ValueError, KeyError):
            pass
    reused = info is not None
    if info is None:
        # This is the existing snapshot/restore/ZIP implementation. No duplicate
        # installer-specific snapshot format or migration mechanism is used.
        target = native._snapshot('managed-update')
        info = native.backup_info(target.name, actor=owner)
    archive = directory / info['id'] / 'backup.zip'
    private_copy = None
    previous = info.get('client_copy') or {}
    if previous.get('file_reference'):
        try:
            candidate = backup.safe_path(previous['file_reference'])
            if candidate.parent == recovery and backup.digest(candidate) == info['sha256']:
                private_copy = candidate
        except (OSError, ValueError, TypeError):
            pass
    if private_copy is None:
        private_copy = backup.safe_path(recovery / (info['id'] + '.zip'))
        if private_copy.exists():
            if backup.digest(private_copy) != info['sha256']:
                raise ValueError('私人副本內容不符，保留原檔供核對。')
        else:
            with archive.open('rb') as source, private_copy.open('xb') as destination:
                private_copy.chmod(0o600)
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
    if backup.digest(private_copy) != info['sha256'] or backup.coverage(data) != coverage:
        raise ValueError('私人副本或目前資料核對失敗。')
    native.record_client_receipt(info['id'], {'file_reference': str(private_copy),
        'size': private_copy.stat().st_size, 'sha256': info['sha256'],
        'readback_verified': True}, actor=owner)
    # Native startup can now reuse this exact snapshot, restore evidence and
    # actual readback receipt under the normal coverage/retention policy.
    value = {'verified': True, 'operation_id': operation_id, 'coverage': coverage,
             'native_backup_id': info['id'], 'sha256': info['sha256'], 'size': info['size'],
             'private_copy': str(private_copy), 'private_copy_readback_verified': True,
             'off_host_backup_verified': False, 'reused_native_backup': reused,
             'created_at': time.time()}
    marker_dir.mkdir(mode=0o700)
    atomic_json(marker, value)
    return value
