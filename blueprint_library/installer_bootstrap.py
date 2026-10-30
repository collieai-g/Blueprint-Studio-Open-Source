"""First-install dependency preparation; never alter an existing studio runtime.

This module deliberately uses the standard library before signed-release imports.
The reviewed wheel lock ships in the initial installer, not in an update feed.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import venv

from blueprint_folders.path_safety import has_link, is_link

ROOT = Path(__file__).resolve().parents[1]
LOCK_FILE = ROOT / 'tools' / 'runtime_lock.json'
FORMAT = 'blueprint-python-bootstrap/1'


def _private(value):
    path = Path(value).expanduser().absolute()
    if has_link(path):
        raise ValueError('安裝環境路徑不能包含符號連結或重新解析點。')
    path = path.resolve()
    if path == ROOT or ROOT in path.parents or path in ROOT.parents:
        raise ValueError('安裝環境必須位於來源目錄之外。')
    if any((part / '.git').exists() for part in (path, *path.parents)):
        raise ValueError('安裝環境必須位於所有來源倉庫之外。')
    return path


def _lock_document(filename=LOCK_FILE):
    data = Path(filename).read_bytes()
    value = json.loads(data)
    if (not isinstance(value, dict) or value.get('format') != 'blueprint-runtime-lock/1' or
            not isinstance(value.get('packages'), list) or not value['packages'] or
            not isinstance(value.get('python_versions'), list) or
            any(not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v)
                for v in value['python_versions'])):
        raise ValueError('安裝環境依賴清單不完整。')
    rows = []
    seen = set()
    for package in value['packages']:
        if not isinstance(package, dict):
            raise ValueError('安裝環境依賴清單不正確。')
        name, version, hashes = package.get('name'), package.get('version'), package.get('sha256')
        if (not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', name) or name in seen or
                not isinstance(version, str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', version) or
                not isinstance(hashes, list) or not hashes or
                any(not isinstance(h, str) or not re.fullmatch('[0-9a-f]{64}', h) for h in hashes)):
            raise ValueError('安裝環境依賴清單不正確。')
        seen.add(name)
        rows.append(name + '==' + version + ''.join(' --hash=sha256:' + h for h in hashes))
    return value, hashlib.sha256(data).hexdigest(), '\n'.join(rows) + '\n'


def _check_tree(runtime):
    if has_link(runtime):
        raise ValueError('Python 環境包含不安全連結。')
    # Check directory entries before os.walk can enter a Windows junction on
    # older Python versions, where followlinks=False alone is insufficient.
    for current, directories, files in os.walk(runtime, followlinks=False):
        if any(is_link(Path(current) / name) for name in directories + files):
            raise ValueError('Python 環境包含不安全連結。')


@contextmanager
def _setup_lock(runtime):
    # This lock is in the owned runtime, never in the still-empty program root.
    if has_link(runtime / '.bootstrap.lock'):
        raise ValueError('Python 環境鎖包含不安全連結。')
    with (runtime / '.bootstrap.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('另一個程序正在準備此安裝環境，請稍後沿用同一位置重試。') from exc
        yield


def _verify(executable, packages):
    script = (
        "import sys,json,importlib.metadata as m; "
        "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; "
        "from zoneinfo import ZoneInfo; "
        "k=Ed25519PrivateKey.generate();k.public_key().verify(k.sign(b'bootstrap'),b'bootstrap'); "
        "assert ZoneInfo('Asia/Hong_Kong').key=='Asia/Hong_Kong'; "
        "print(json.dumps({'prefix':sys.prefix,'versions':{p:m.version(p) for p in "
        + repr([p['name'] for p in packages]) + "}}))"
    )
    result = subprocess.run([str(executable), '-I', '-B', '-c', script],
                            capture_output=True, text=True, encoding='utf-8', timeout=60, check=True)
    value = json.loads(result.stdout)
    if (Path(value['prefix']).resolve() != executable.parent.parent.resolve() or
            any(value['versions'].get(p['name']) != p['version'] for p in packages)):
        raise ValueError('安裝環境版本與固定清單不符。')


def _install(executable, requirements):
    env = os.environ.copy()
    # Keep proxy and CA configuration, but ignore unrelated pip install targets
    # and extra indexes. Only hash-pinned binary wheels from PyPI are accepted.
    for name in list(env):
        if name.startswith('PIP_') and name not in {'PIP_CERT', 'PIP_CLIENT_CERT'}:
            env.pop(name)
    env['PIP_CONFIG_FILE'] = os.devnull
    subprocess.run([str(executable), '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--no-input', '--only-binary=:all:', '--no-deps', '--require-hashes',
                    '--index-url', 'https://pypi.org/simple', '-r', str(requirements)],
                   env=env, stdout=sys.stderr, stderr=sys.stderr, timeout=600, check=True)


def ensure_runtime(program_root, *, protected=(), lock_file=LOCK_FILE):
    """Return the owned venv Python; never initialize data or rebind an old install."""
    root = _private(program_root)
    if (root / 'installation.json').exists():
        return None
    if root.exists() and any(root.iterdir()):
        raise ValueError('安裝目錄不是空白，請沿用既有安裝。')
    if (sys.platform != 'win32' or platform.python_implementation() != 'CPython' or
            struct.calcsize('P') != 8 or platform.machine().lower() not in {'amd64', 'x86_64'}):
        raise ValueError('自動準備目前支援 Windows x64 CPython；其他平台請沿用已驗證的獨立環境安裝路線。')
    document, digest, requirements = _lock_document(lock_file)
    if list(sys.version_info[:2]) not in document['python_versions']:
        raise ValueError('此 Python 版本尚未有自動準備的固定 wheel 清單。')
    runtime = _private(root.with_name(root.name + '-python'))
    for value in protected:
        other = _private(value)
        if runtime == other or runtime in other.parents or other in runtime.parents:
            raise ValueError('Python 環境不可與工作室資料、備份或恢復位置重疊。')
    owner = {'format': FORMAT, 'program_root': str(root), 'runtime': str(runtime),
             'base_python': str(Path(sys._base_executable).resolve()),
             'python_version': list(sys.version_info[:3]), 'lock_sha256': digest}
    marker = runtime / 'bootstrap.json'
    if runtime.exists():
        if not marker.is_file() or has_link(marker) or json.loads(marker.read_text(encoding='utf-8')) != owner:
            raise ValueError('Python 位置未有相符安裝身份，未有覆寫。')
    else:
        runtime.mkdir(parents=True, mode=0o700)
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(owner, stream, ensure_ascii=False, sort_keys=True)
    executable = runtime / 'Scripts' / 'python.exe'
    with _setup_lock(runtime):
        if has_link(marker) or has_link(executable):
            raise ValueError('Python 環境包含不安全連結。')
        _check_tree(runtime)
        if not executable.is_file():
            venv.EnvBuilder(with_pip=True).create(runtime)
        _check_tree(runtime)
        requirements_path = runtime / 'requirements.lock'
        if has_link(requirements_path):
            raise ValueError('Python 依賴清單位置包含不安全連結。')
        requirements_path.write_text(requirements, encoding='utf-8')
        try:
            _verify(executable, document['packages'])
        except (OSError, ValueError, subprocess.SubprocessError):
            if Path(sys.prefix).resolve() == runtime:
                raise ValueError('專用 Python 環境未通過依賴驗證，未初始化工作室。') from None
            _install(executable, requirements_path)
            _verify(executable, document['packages'])
    return executable
