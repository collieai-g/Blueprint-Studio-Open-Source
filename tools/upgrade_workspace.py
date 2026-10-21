#!/usr/bin/env python3
"""Snapshot and verify an existing native workspace without initializing it.

Run with the service stopped for a complete point-in-time backup. This tool never
upgrades code, recreates credentials, modifies the source database or restores data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

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
        if path.is_file() and path.name not in {'library.sqlite3', 'library.sqlite3-wal', 'library.sqlite3-shm'}:
            files[path.relative_to(directory).as_posix()] = digest(path)
    return files


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
        if scan(data) != files:
            raise ValueError('備份期間資料有變動；請停止服務後重試。')
        (payload / 'library.sqlite3').chmod(0o600)
        manifest = {'format': 'blueprint-native-snapshot', 'version': 1,
                    'database_sha256': digest(payload / 'library.sqlite3'), 'files': files}
        manifest_path = output / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        manifest_path.chmod(0o600)
        return {'status': 'snapshot_created', 'snapshot': str(output), 'files': len(files),
                'note': '包含原資料庫、登入設定及附件；保持私人存取。'}
    finally:
        conn.close()


def verify(data, backup):
    external(data); external(backup)
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


def main(argv=None):
    parser = argparse.ArgumentParser(description='V2 原生工作台升級：完整私人備份及資料保留驗證')
    sub = parser.add_subparsers(dest='command', required=True)
    save = sub.add_parser('snapshot', help='停止服務後備份已有資料、登入及附件')
    save.add_argument('--data', required=True); save.add_argument('--output', required=True)
    save.add_argument('--service-stopped', action='store_true', required=True, help='確認原服務已停止，避免備份期間有寫入')
    check = sub.add_parser('verify', help='驗證升級前的每筆紀錄、完成狀態及附件仍然保留')
    check.add_argument('--data', required=True); check.add_argument('--snapshot', required=True)
    args = parser.parse_args(argv)
    try:
        data = safe_path(args.data)
        result = snapshot(data, safe_path(args.output)) if args.command == 'snapshot' else verify(data, safe_path(args.snapshot))
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
        # Never echo database contents, credentials or arbitrary upstream errors.
        print(json.dumps({'error': '備份／驗證未完成。請核對既有路徑、私人目錄權限、備份完整性及服務是否已停止；原資料未被覆寫。'}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
