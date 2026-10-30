"""Reviewed exact release inventories shared by all Blueprint packagers.

Changing a tracked file's contents never approves another pathname for release.
The policy is deliberately data, reviewed alongside source changes, rather than
a scan of whatever files happen to be present in a developer's checkout.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import zipfile

from blueprint_folders.path_safety import has_link

POLICY_PATH = 'tools/release_files.json'
PACKAGES = {'installer', 'workbench', 'managed-core'}


def safe_relative(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or name.startswith('/') or PurePosixPath(name).as_posix() != name
            or any(part in {'', '.', '..'} for part in name.split('/'))):
        raise ValueError('發行清單包含不安全的相對路徑。')
    return name


def read_policy(root):
    root = Path(root).absolute()
    path = root / POLICY_PATH
    if has_link(path):
        raise ValueError('發行清單不能經過連結。')
    value = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'version', 'files'}
            or value['version'] != 1 or not isinstance(value['files'], dict)):
        raise ValueError('發行檔案政策格式不正確。')
    for name, groups in value['files'].items():
        safe_relative(name)
        if (not isinstance(groups, list) or any(group not in PACKAGES for group in groups)
                or len(groups) != len(set(groups))):
            raise ValueError('發行檔案分類不正確。')
    return value['files']


def selected_names(root, package, *, allow_working_tree=False, required=()):
    root = Path(root).absolute()
    if package not in PACKAGES:
        raise ValueError('未知的發行套件分類。')
    policy = read_policy(root)
    tracked = set(filter(None, subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached'], cwd=root).decode('utf-8').split('\0')))
    unknown = tracked - policy.keys()
    if unknown:
        raise ValueError('有未審核分類的 Git 檔案；請先明確更新發行清單。')
    available = tracked
    if allow_working_tree:
        untracked = set(filter(None, subprocess.check_output(
            ['git', 'ls-files', '-z', '--others', '--exclude-standard'], cwd=root
        ).decode('utf-8').split('\0')))
        available = tracked | (untracked & policy.keys())
    expected = {name for name, groups in policy.items() if package in groups}
    if not expected or not set(required).issubset(expected) or not expected.issubset(available):
        raise ValueError('發行來源不完整；清單內的公開來源必須齊全並已加入 Git。')
    for name in sorted(expected):
        path = root / name
        if (has_link(path) or not path.is_file() or not path.resolve().is_relative_to(root.resolve())
                or any(part.startswith('.env') and part != '.env.example' for part in path.parts)
                or path.name in {'access.json', 'installation.json', 'INSTALL_PROGRESS.md'}
                or path.suffix.lower() in {'.zip', '.sqlite', '.sqlite3', '.pyc'}):
            raise ValueError('發行來源包含不允許的檔案或連結。')
    return sorted(expected)


def external_output(path, root):
    path = Path(path).expanduser().absolute()
    if has_link(path):
        raise ValueError('發行輸出路徑不能經過連結。')
    path = path.resolve()
    root = Path(root).resolve()
    if (path == root or root in path.parents
            or any((parent / '.git').exists() for parent in (path.parent, *path.parents))):
        raise ValueError('發行輸出必須位於所有來源倉庫之外。')
    return path


def write_archive(output, files, manifest_name, manifest):
    """Write once, with deterministic metadata and a complete payload inventory."""
    if manifest_name in files:
        raise ValueError('發行來源與產物清單名稱衝突。')
    manifest = {**manifest, 'files': {name: hashlib.sha256(data).hexdigest()
                                     for name, data in sorted(files.items())}}
    payload = {**files, manifest_name: (json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            entry = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    return manifest


def verify_archive(path, manifest_name):
    """Validate the delivered bytes, not just the checkout used to build them."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if (len(names) != len(set(names)) or len(names) != len({name.casefold() for name in names})
                or manifest_name not in names):
            raise ValueError('發行包清單缺失或有重複檔案。')
        for entry in archive.infolist():
            safe_relative(entry.filename)
            if (((entry.external_attr >> 16) & 0o170000) not in {0, 0o100000}
                    or entry.flag_bits & 1):
                raise ValueError('發行包不能包含連結、特殊或加密檔案。')
        manifest = json.loads(archive.read(manifest_name))
        expected = manifest.get('files')
        if not isinstance(expected, dict) or set(names) != set(expected) | {manifest_name}:
            raise ValueError('發行包內容與完整清單不符。')
        for name, checksum in expected.items():
            safe_relative(name)
            entry = archive.getinfo(name)
            if ((entry.external_attr >> 16) & 0o170000) not in {0, 0o100000}:
                raise ValueError('發行包不能包含連結或特殊檔案。')
            if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                raise ValueError('發行包內容雜湊不符。')
        sums = 'contracts/integration-v1/SHA256SUMS'
        if sums in expected:
            for line in archive.read(sums).decode('utf-8').splitlines():
                checksum, name = line.split(None, 1)
                name = 'contracts/integration-v1/' + name.lstrip('* ')
                if name not in expected or hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                    raise ValueError('發行包的整合合約雜湊不符。')
    return manifest
