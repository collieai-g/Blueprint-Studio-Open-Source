#!/usr/bin/env python3
"""Snapshot and verify an existing native workspace without initializing it.

Run with the service stopped for a complete point-in-time backup. This tool never
upgrades code, recreates credentials or overwrites existing restore destinations.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import re
import zipfile
from datetime import datetime, timezone

SOURCE = Path(__file__).resolve().parents[1]


def safe_path(value):
    path = Path(value).expanduser().absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('路徑不能包含符號連結。')
    return path.resolve()


def external(path):
    if path == SOURCE or SOURCE in path.parents or path in SOURCE.parents:
        raise ValueError('私人資料及備份必須放在程式目錄之外。')


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def database(path):
    if not path.is_file() or path.is_symlink():
        raise ValueError('找不到已有的 library.sqlite3，未建立空資料庫。')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        conn.close()
        raise ValueError('資料庫完整性檢查失敗，請先修復原資料。')
    return conn


def scan(directory):
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('資料目錄包含符號連結，未繼續備份。')
        if not path.is_file() and not path.is_dir():
            raise ValueError('資料目錄包含不支援的檔案類型。')
        if path.is_file() and path.relative_to(directory).as_posix() not in {'library.sqlite3', 'library.sqlite3-wal', 'library.sqlite3-shm'}:
            files[path.relative_to(directory).as_posix()] = digest(path)
    return files


def coverage(data):
    """Hash business state, rather than time-varying SQLite/ZIP bytes.

    The reserved write lock gives all tables one snapshot while immutable files
    and login settings are read twice. Maintenance journals and copy attestations
    live in the separate backup directory and never enter this fingerprint.
    No private rows, filenames or credential hashes are returned to callers.
    """
    data = safe_path(data); external(data)
    path = safe_path(data / 'library.sqlite3')
    if not path.is_file():
        raise ValueError('找不到已有資料庫，未建立空白資料。')
    result = hashlib.sha256()
    def add(value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                             sort_keys=True, allow_nan=False).encode('utf-8')
        result.update(len(encoded).to_bytes(8, 'big')); result.update(encoded)
    def cell(value):
        # SQLite BLOB and TEXT with the same characters must remain distinct.
        return {'blob': value.hex()} if isinstance(value, bytes) else value
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=15)) as conn:
        conn.execute('BEGIN IMMEDIATE')
        schema = conn.execute('PRAGMA user_version').fetchone()[0]
        files = scan(data)
        add({'format': 'blueprint-coverage/1', 'schema': schema})
        definitions = list(conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master "
                                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
        add(definitions)
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                                "AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') ORDER BY name")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = []
            for row in conn.execute('SELECT * FROM ' + quoted):
                encoded = json.dumps([cell(value) for value in row], ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')
                rows.append(hashlib.sha256(encoded).hexdigest())
            add([table, sorted(rows)])
        if scan(data) != files:
            raise ValueError('資料指紋核對期間附件有變動，請重新讀取。')
        add(files)
    return {'version': 1, 'digest': result.hexdigest(), 'schema': schema}


def snapshot(data, output):
    external(data); external(output)
    if output == data or data in output.parents or output in data.parents:
        raise ValueError('備份目錄不能與資料目錄互相包含。')
    if output.exists():
        raise ValueError('備份目錄已存在，未有覆寫。請選新的目錄。')
    if not (data / 'access.json').is_file():
        raise ValueError('找不到原管理員設定，請核對真正的資料目錄。')
    files = scan(data)
    conn = database(data / 'library.sqlite3')
    try:
        revision = conn.execute('PRAGMA data_version').fetchone()[0]
        output.mkdir(parents=True, mode=0o700)
        payload = output / 'data'; payload.mkdir(mode=0o700)
        with sqlite3.connect(payload / 'library.sqlite3') as target:
            conn.backup(target)
        for name, checksum in files.items():
            target = payload / name; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(data / name, target)
            target.chmod(0o600)
            if digest(target) != checksum:
                raise ValueError('備份期間檔案有變動；請停止服務後使用新的備份目錄重試。')
        if scan(data) != files or conn.execute('PRAGMA data_version').fetchone()[0] != revision:
            raise ValueError('備份期間資料有變動；請停止服務後重試。')
        (payload / 'library.sqlite3').chmod(0o600)
        manifest = {'format': 'blueprint-native-snapshot', 'version': 1,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'database_sha256': digest(payload / 'library.sqlite3'), 'files': files}
        manifest_path = output / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        manifest_path.chmod(0o600)
        return {'status': 'snapshot_created', 'snapshot': str(output), 'files': len(files),
                'note': '包含原資料庫、登入設定及附件；保持私人存取。'}
    finally:
        conn.close()


def check(backup):
    """Validate the entire backup independently of the live installation."""
    backup = safe_path(backup); external(backup)
    manifest = json.loads(safe_path(backup / 'manifest.json').read_text())
    if manifest.get('format') != 'blueprint-native-snapshot' or manifest.get('version') != 1:
        raise ValueError('不支援的備份格式。')
    files = manifest.get('files')
    if not isinstance(files, dict) or 'access.json' not in files:
        raise ValueError('備份缺少登入設定。')
    for name, checksum in files.items():
        if (not isinstance(name, str) or not name or Path(name).is_absolute()
                or '..' in Path(name).parts or '\\' in name or ':' in name
                or not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum)):
            raise ValueError('備份清單不正確。')
    payload = safe_path(backup / 'data')
    if not payload.is_dir() or scan(payload) != files:
        raise ValueError('備份內容不完整或有未列出的檔案。')
    if digest(payload / 'library.sqlite3') != manifest.get('database_sha256'):
        raise ValueError('備份資料庫驗證失敗。')
    conn = database(payload / 'library.sqlite3')
    try:
        schema = conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()
    return {'status': 'backup_verified', 'files': len(files), 'database_schema': schema}


def restore(backup, output):
    """Restore only into a new private directory; never overwrite live data."""
    backup, output = safe_path(backup), safe_path(output)
    external(output)
    if output == backup or backup in output.parents or output in backup.parents:
        raise ValueError('還原位置不能與備份重疊。')
    check(backup)
    if output.exists():
        raise ValueError('還原位置已存在，未有覆寫。')
    shutil.copytree(backup / 'data', output)
    output.chmod(0o700)
    for path in output.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    verify(output, backup)
    return {'status': 'restore_verified', 'data': str(output)}


def unpack(archive, output, checksum):
    """Import a downloaded backup without trusting ZIP paths or metadata."""
    archive, output = safe_path(archive), safe_path(output)
    external(output)
    if not re.fullmatch('[0-9a-f]{64}', checksum or '') or digest(archive) != checksum:
        raise ValueError('備份下載校驗失敗。')
    with zipfile.ZipFile(archive) as z:
        entries = z.infolist()
        if len(entries) > 100000 or sum(i.file_size for i in entries) > 8 * 1024**3:
            raise ValueError('封包超出匯入上限，請在受控主機使用原備份目錄。')
        names = set()
        for entry in entries:
            name = entry.filename
            if (name in names or not name or Path(name).is_absolute() or '..' in Path(name).parts
                    or '\\' in name or ':' in name or name.endswith('/')
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    or (name != 'manifest.json' and not name.startswith('data/'))):
                raise ValueError('備份 ZIP 包含不安全或重複路徑。')
            names.add(name)
        output.mkdir(parents=True, mode=0o700)
        for entry in entries:
            target = output / entry.filename
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with z.open(entry) as source, target.open('xb') as destination:
                target.chmod(0o600)
                shutil.copyfileobj(source, destination)
    check(output)
    return {'status': 'downloaded_backup_verified', 'snapshot': str(output)}


def verify(data, backup):
    external(data); external(backup)
    check(backup)
    manifest = json.loads(safe_path(backup / 'manifest.json').read_text())
    if manifest.get('format') != 'blueprint-native-snapshot' or manifest.get('version') != 1:
        raise ValueError('不支援的備份格式。')
    payload = safe_path(backup / 'data')
    files = manifest.get('files')
    if not isinstance(files, dict):
        raise ValueError('備份清單格式不正確。')
    for name, checksum in files.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise ValueError('備份清單包含不安全路徑。')
        original, current = safe_path(payload / relative), safe_path(data / relative)
        if not original.is_file() or digest(original) != checksum:
            raise ValueError('備份檔案驗證失敗。')
        if not current.is_file() or digest(current) != checksum:
            raise ValueError('原附件或登入設定有改動／遺失，未通過保留資料驗證。')
    if digest(payload / 'library.sqlite3') != manifest.get('database_sha256'):
        raise ValueError('備份資料庫驗證失敗。')
    before, after = database(payload / 'library.sqlite3'), database(data / 'library.sqlite3')
    try:
        before.execute('BEGIN'); after.execute('BEGIN')
        tables = [row[0] for row in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        rows_checked = 0
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            info = list(before.execute(f'PRAGMA table_info({quoted})'))
            keys = [row['name'] for row in sorted(info, key=lambda row: row['pk']) if row['pk']]
            if not keys:
                raise ValueError('遇到沒有識別欄位的資料表，需人工核對。')
            where = ' AND '.join('"' + key.replace('"', '""') + '" IS ?' for key in keys)
            for row in before.execute(f'SELECT * FROM {quoted}'):
                current = after.execute(f'SELECT * FROM {quoted} WHERE {where}', [row[key] for key in keys]).fetchone()
                if current is None or any(key not in current.keys() or current[key] != row[key] for key in row.keys()):
                    raise ValueError('原有紀錄或完成狀態與升級前不同，未通過資料保留驗證。')
                rows_checked += 1
        return {'status': 'original_data_preserved', 'tables': len(tables), 'rows': rows_checked, 'files': len(files)}
    finally:
        before.close(); after.close()
