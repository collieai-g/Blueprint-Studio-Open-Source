"""Trusted managed-release verification and isolated preparation.

Only signed manifests can select archive locations. This module never executes
downloaded code, installs dependencies, changes system Python or writes company data.
"""
from __future__ import annotations

from blueprint_folders.path_safety import has_link

import ast
import base64
import hashlib
from http.client import HTTPException
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener, url2pathname
import zipfile

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_FILES = 20000
HASH = re.compile(r'[0-9a-f]{64}\Z')
VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?\Z')
IDENTIFIER = re.compile(r'[a-z][a-z0-9-]{0,79}\Z')
REQUIRED_FILES = {'blueprint.py', 'blueprint_folders/__init__.py', 'blueprint_library/store.py'}


class InstallerReleaseError(ValueError):
    """A release is invalid or cannot be prepared safely."""


def canonical_json(value):
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise InstallerReleaseError('發行資料不是有效 JSON。') from exc


def release_id(manifest):
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or len(name) > 240 or
            unicodedata.normalize('NFC', name) != name or
            any(ord(c) < 32 or ord(c) == 127 for c in name) or
            any(c in name for c in '\\:<>"|?*') or name.startswith('/') or
            any(part in {'', '.', '..'} or part.endswith(('.', ' ')) for part in name.split('/'))):
        raise InstallerReleaseError('套件路徑不安全。')
    for part in name.split('/'):
        if re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I):
            raise InstallerReleaseError('套件包含不支援的平台檔名。')
    if PurePosixPath(name).is_absolute():
        raise InstallerReleaseError('套件路徑必須是相對路徑。')
    return name


def _safe_path(path):
    path = Path(path).expanduser()
    if has_link(path):
        raise InstallerReleaseError('安裝路徑不能包含符號連結。')
    return path.resolve()


def _source(url):
    if not isinstance(url, str) or len(url) > 2048 or any(ord(c) < 32 for c in url):
        raise InstallerReleaseError('套件來源不正確。')
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise InstallerReleaseError('套件來源不正確。') from exc
    if parsed.scheme == 'https' and parsed.hostname and parsed.username is None and parsed.password is None and not parsed.fragment:
        if any(c.isspace() or ord(c) == 127 for c in url):
            raise InstallerReleaseError('HTTPS 套件網址不能包含未編碼的空白或控制字元。')
        return ('https', url)
    if parsed.scheme == 'file' and parsed.netloc in {'', 'localhost'} and not parsed.query and not parsed.fragment:
        return ('file', _safe_path(url2pathname(parsed.path)))
    if Path(url).is_absolute() and not url.startswith(('//', '\\\\')):
        return ('file', _safe_path(url))
    raise InstallerReleaseError('套件來源只支援 HTTPS 或已明確選定的本機絕對路徑。')


def validate_release(manifest):
    required = {'contract_version', 'component_id', 'adapter', 'version', 'source_revision',
                'platforms', 'runtime', 'archive', 'files', 'entrypoint', 'database_schema'}
    optional = {'notes', 'requires', 'candidate', 'source_tree', 'revoked', 'source_repository'}
    if not isinstance(manifest, dict) or not required.issubset(manifest) or set(manifest) - required - optional:
        raise InstallerReleaseError('發行清單欄位不完整或不受支援。')
    # Normalize into JSON primitives, without executing publisher-supplied objects.
    result = json.loads(canonical_json(manifest))
    if result['contract_version'] not in {1, 2} or type(result['contract_version']) is not int:
        raise InstallerReleaseError('不支援此發行合約。')
    if not isinstance(result['component_id'], str) or not IDENTIFIER.fullmatch(result['component_id']):
        raise InstallerReleaseError('元件識別碼不正確。')
    if not isinstance(result['adapter'], str) or not IDENTIFIER.fullmatch(result['adapter']):
        raise InstallerReleaseError('元件 adapter 不正確。')
    if not isinstance(result['version'], str) or not VERSION.fullmatch(result['version']):
        raise InstallerReleaseError('版本必須是明確的語意版本。')
    if not isinstance(result['source_revision'], str) or not re.fullmatch(r'[0-9a-f]{40}', result['source_revision']):
        raise InstallerReleaseError('來源必須固定完整 commit。')
    platforms = result['platforms']
    if (not isinstance(platforms, list) or not platforms or
            any(not isinstance(p, str) or p not in {'win32', 'linux', 'darwin'} for p in platforms) or
            len(set(platforms)) != len(platforms)):
        raise InstallerReleaseError('平台清單不正確。')
    runtime = result['runtime']
    node = result['contract_version'] == 2 and result['adapter'] == 'bluesai-node'
    if node or 'source_repository' in result:
        if not isinstance(result.get('source_repository'), str):
            raise InstallerReleaseError('模組來源需固定 GitHub HTTPS 倉庫。')
        repository = urlsplit(result['source_repository'])
        if (repository.scheme != 'https' or repository.netloc != 'github.com' or
                repository.query or repository.fragment or
                not re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository.path)):
            raise InstallerReleaseError('模組來源需固定 GitHub HTTPS 倉庫。')
    runtime_fields = {'node_min', 'node_max_exclusive'} if node else {'python_min', 'python_max_exclusive'}
    if not isinstance(runtime, dict) or set(runtime) != runtime_fields:
        raise InstallerReleaseError('必須宣告已驗證的執行環境相容範圍。')
    for value in runtime.values():
        if not isinstance(value, list) or len(value) != (3 if node else 2) or any(type(n) is not int or n < 0 or n > 999 for n in value):
            raise InstallerReleaseError('執行環境版本範圍不正確。')
    low, high = ('node_min', 'node_max_exclusive') if node else ('python_min', 'python_max_exclusive')
    if runtime[low] < ([24, 22, 0] if node else [3, 10]) or runtime[low] >= runtime[high]:
        raise InstallerReleaseError('執行環境版本範圍不正確。')
    archive = result['archive']
    if (not isinstance(archive, dict) or not {'url', 'sha256', 'size'}.issubset(archive) or
            set(archive) - {'url', 'sha256', 'size', 'redirect_hosts'} or
            not isinstance(archive['sha256'], str) or not HASH.fullmatch(archive['sha256']) or
            type(archive['size']) is not int or not 0 < archive['size'] <= MAX_ARCHIVE_BYTES):
        raise InstallerReleaseError('套件完整性資料不正確。')
    _source(archive['url'])
    hosts = archive.get('redirect_hosts', [])
    if (not isinstance(hosts, list) or len(hosts) > 10 or
            any(not isinstance(host, str) or len(host) > 253 or
                not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or
                '..' in host or any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-') for label in host.split('.'))
                for host in hosts) or len(set(hosts)) != len(hosts)):
        raise InstallerReleaseError('重新導向需列出簽章保護的明確小寫主機名稱。')
    files = result['files']
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise InstallerReleaseError('套件檔案清單不正確。')
    seen = set()
    for name, digest in files.items():
        safe_name(name)
        if name.casefold() in seen or not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise InstallerReleaseError('套件檔案重複或雜湊不正確。')
        seen.add(name.casefold())
    if any('/'.join(name.split('/')[:i]).casefold() in seen for name in files for i in range(1, len(name.split('/')))):
        raise InstallerReleaseError('套件檔案與目錄互相衝突。')
    if node:
        if (result['component_id'] != 'bluesai-cs-0001' or result['entrypoint'] != 'src/managed-runtime.js' or
                not {'package.json', 'managed-component.json', result['entrypoint']}.issubset(files)):
            raise InstallerReleaseError('缺少客服受管入口或元件版本描述。')
    elif result['entrypoint'] != 'blueprint.py' or not REQUIRED_FILES.issubset(files):
        raise InstallerReleaseError('缺少原生工作室入口或版本資料。')
    if type(result['database_schema']) is not int or not 1 <= result['database_schema'] <= 10000:
        raise InstallerReleaseError('資料格式版本不正確。')
    requires = result.get('requires', {})
    if not isinstance(requires, dict) or len(requires) > 50:
        raise InstallerReleaseError('元件依賴不正確。')
    for component, versions in requires.items():
        if not IDENTIFIER.fullmatch(component) or not isinstance(versions, list) or not versions or any(not isinstance(v, str) or not VERSION.fullmatch(v) for v in versions):
            raise InstallerReleaseError('依賴需列出已驗證的明確版本。')
    if node and 'blueprint-core' not in requires:
        raise InstallerReleaseError('客服發行必須列出已驗證的工作室核心版本。')
    if 'notes' in result and (not isinstance(result['notes'], str) or len(result['notes']) > 16000):
        raise InstallerReleaseError('更新摘要不正確。')
    for field in ('candidate', 'revoked'):
        if field in result and type(result[field]) is not bool:
            raise InstallerReleaseError('發行狀態不正確。')
    if 'source_tree' in result and (not isinstance(result['source_tree'], str) or not HASH.fullmatch(result['source_tree'])):
        raise InstallerReleaseError('來源指紋不正確。')
    if result.get('candidate') and 'source_tree' not in result:
        raise InstallerReleaseError('未提交候選必須保留來源指紋。')
    return result


def verify_signed(value, trusted_keys, payload_field, *, component=None, channel=None):
    """Verify a canonical envelope with optional publisher scope restrictions."""
    if not isinstance(value, dict) or set(value) != {payload_field, 'signature', 'key_id'}:
        raise InstallerReleaseError('簽章封套格式不正確。')
    key_id = value['key_id']
    if not isinstance(key_id, str) or not isinstance(trusted_keys, dict) or key_id not in trusted_keys:
        raise InstallerReleaseError('發行來源不在已信任金鑰清單。')
    record = trusted_keys[key_id]
    if isinstance(record, dict):
        if not {'public_key'}.issubset(record) or set(record) - {'public_key', 'components', 'channels'}:
            raise InstallerReleaseError('可信金鑰設定格式不正確。')
        if channel is not None and 'channels' not in record:
            raise InstallerReleaseError('受限元件金鑰沒有渠道發行權限。')
        for field, requested in [('components', component), ('channels', channel)]:
            scope = record.get(field)
            if field in record and (not isinstance(scope, list) or not scope or any(not isinstance(x, str) or not x for x in scope)):
                raise InstallerReleaseError('可信金鑰範圍不正確。')
            if requested is not None and field in record and requested not in scope:
                raise InstallerReleaseError('簽章金鑰沒有這項發行權限。')
        record = record['public_key']
    try:
        key = base64.b64decode(record, validate=True)
        signature = base64.b64decode(value['signature'], validate=True)
        if len(key) != 32 or len(signature) != 64:
            raise ValueError('length')
    except (ValueError, TypeError) as exc:
        raise InstallerReleaseError('發行簽章或公開金鑰不正確。') from exc
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise InstallerReleaseError('尚未安裝簽章驗證 runtime；請在獨立安裝器環境安裝 requirements-installer.txt。') from exc
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, canonical_json(value[payload_field]))
    except (InvalidSignature, ValueError) as exc:
        raise InstallerReleaseError('發行簽章驗證失敗。') from exc
    return json.loads(canonical_json(value[payload_field]))


def verify_release(envelope, trusted_keys):
    if not isinstance(envelope, dict) or set(envelope) != {'manifest', 'signature', 'key_id'}:
        raise InstallerReleaseError('簽章封套格式不正確。')
    manifest = envelope.get('manifest')
    result = validate_release(verify_signed(envelope, trusted_keys, 'manifest',
                              component=manifest.get('component_id') if isinstance(manifest, dict) else None))
    if result.get('revoked'):
        raise InstallerReleaseError('此發行已撤回。')
    return result


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class _RedirectPolicy(HTTPRedirectHandler):
    """Allow only explicitly signed HTTPS redirect hosts, for at most 3 hops."""
    def __init__(self, hosts):
        super().__init__()
        self.hosts = frozenset(hosts)
        self.count = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            parsed = urlsplit(newurl)
        except ValueError as exc:
            raise InstallerReleaseError('套件重新導向網址不正確。') from exc
        self.count += 1
        if (self.count > 3 or any(ord(c) < 32 or c.isspace() or ord(c) == 127 for c in newurl) or parsed.scheme != 'https' or
                parsed.username is not None or parsed.password is not None or
                parsed.fragment or parsed.hostname not in self.hosts):
            raise InstallerReleaseError('套件重新導向不符合已簽章的 HTTPS 主機清單。')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            for header in ('Authorization', 'Cookie', 'Proxy-Authorization'):
                redirected.remove_header(header)
        return redirected


def _download(archive, destination):
    kind, source = _source(archive['url'])
    opener = build_opener(_RedirectPolicy(archive.get('redirect_hosts', [])))
    count = 0
    digest = hashlib.sha256()
    try:
        stream = open(source, 'rb') if kind == 'file' else opener.open(source, timeout=30)
        with stream, destination.open('xb') as output:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                count += len(chunk)
                if count > archive['size']:
                    raise InstallerReleaseError('下載套件超過宣告大小。')
                digest.update(chunk)
                output.write(chunk)
    except HTTPException as exc:
        raise InstallerReleaseError('套件 HTTP 下載未完成；現有版本保持不變。') from exc
    if count != archive['size'] or digest.hexdigest() != archive['sha256']:
        raise InstallerReleaseError('下載套件大小或雜湊不符。')


def _constant(path, name):
    try:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        values = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and
                  any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        if len(values) != 1:
            raise ValueError('count')
        return values[0]
    except (ValueError, SyntaxError, OSError) as exc:
        raise InstallerReleaseError('套件來源版本資料不正確。') from exc


def verify_prepared(path, manifest):
    path = _safe_path(path)
    observed = set()
    for child in path.rglob('*'):
        if (has_link(child) or
                (not child.is_file() and not child.is_dir())):
            raise InstallerReleaseError('準備目錄包含不安全檔案。')
        if child.is_file():
            name = child.relative_to(path).as_posix()
            observed.add(name)
            if manifest['files'].get(name) != file_digest(child):
                raise InstallerReleaseError('準備目錄內容已變更。')
    if observed != set(manifest['files']):
        raise InstallerReleaseError('準備目錄的檔案清單不符。')
    if manifest['adapter'] == 'bluesai-node':
        try:
            package = json.loads((path / 'package.json').read_text(encoding='utf-8'))
            descriptor = json.loads((path / 'managed-component.json').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise InstallerReleaseError('客服套件版本描述不正確。') from exc
        expected = {field: manifest[field] for field in ('component_id', 'adapter', 'version', 'database_schema')}
        agent_manifest = descriptor.get('agent_manifest') if isinstance(descriptor, dict) else None
        if not isinstance(agent_manifest, str):
            raise InstallerReleaseError('客服套件缺少宿主宣告位置。')
        safe_name(agent_manifest)
        expected['agent_manifest'] = agent_manifest
        if (descriptor != expected or package.get('version') != manifest['version'] or
                agent_manifest not in manifest['files']):
            raise InstallerReleaseError('客服套件實際版本或資料格式與發行清單不符。')
        try:
            native_agent = json.loads((path / agent_manifest).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise InstallerReleaseError('客服宿主宣告格式不正確。') from exc
        if (not isinstance(native_agent, dict) or native_agent.get('id') != manifest['component_id'] or
                native_agent.get('version') != manifest['version']):
            raise InstallerReleaseError('客服宿主宣告版本與程式套件不符。')
    elif (_constant(path / 'blueprint_folders/__init__.py', '__version__') != manifest['version'] or
          _constant(path / 'blueprint_library/store.py', 'SCHEMA_VERSION') != manifest['database_schema']):
        raise InstallerReleaseError('套件實際版本或資料格式與發行清單不符。')
    return path


def verify_runtime(manifest, runtimes=None):
    runtime = manifest['runtime']
    if manifest['adapter'] == 'bluesai-node':
        configured = (runtimes or {}).get('node')
        if not configured:
            raise InstallerReleaseError('請先配置客服專用的固定 Node 執行器。')
        executable = _safe_path(configured)
        if executable.name.lower() not in {'node', 'node.exe'} or not executable.is_file():
            raise InstallerReleaseError('Node 必須是可信設定中的實際執行檔。')
        before = file_digest(executable)
        env = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
        try:
            completed = subprocess.run([str(executable), '--version'], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=10, env=env, shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)\s*', completed.stdout)
            if completed.returncode or not match:
                raise ValueError('version')
            version = list(map(int, match.groups()))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise InstallerReleaseError('未能核對指定 Node 執行器。') from exc
        if not runtime['node_min'] <= version < runtime['node_max_exclusive']:
            raise InstallerReleaseError('指定 Node 不符合已驗證的客服版本條件。')
        after = file_digest(executable)
        if before != after:
            raise InstallerReleaseError('Node 執行器在核對期間改變，未顯示更新就緒。')
        return {'kind': 'node', 'executable': str(executable), 'version': version,
                'sha256': after}
    if not runtime['python_min'] <= list(sys.version_info[:2]) < runtime['python_max_exclusive']:
        raise InstallerReleaseError('現有 Python 不符合已驗證 runtime 範圍，未顯示安裝就緒。')
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo('Asia/Hong_Kong')
    except ZoneInfoNotFoundError as exc:
        raise InstallerReleaseError('目前執行環境缺少 IANA 時區資料，請先安裝 requirements-installer.txt。') from exc
    return {'kind': 'python', 'executable': str(Path(sys.executable).resolve()),
            'version': list(sys.version_info[:3])}


def prepare_release(envelope, trusted_keys, cache_root, *, installed=None, runtimes=None):
    manifest = verify_release(envelope, trusted_keys)
    if (manifest['adapter'], manifest['component_id']) not in {
            ('native-core', 'blueprint-core'), ('bluesai-node', 'bluesai-cs-0001')}:
        raise InstallerReleaseError('此宿主尚未實作該元件的安裝 adapter。')
    if sys.platform not in manifest['platforms']:
        raise InstallerReleaseError('此發行不支援目前平台。')
    checked_runtime = verify_runtime(manifest, runtimes)
    for component, versions in manifest.get('requires', {}).items():
        value = (installed or {}).get(component)
        current = value.get('version') if isinstance(value, dict) else value
        if current not in versions:
            raise InstallerReleaseError('已安裝元件不符合已驗證的依賴組合。')
    cache = _safe_path(cache_root)
    cache.mkdir(parents=True, exist_ok=True)
    identifier = release_id(manifest)
    target = _safe_path(cache / identifier)
    if target.exists():
        verify_prepared(target, manifest)
    else:
        staging = Path(tempfile.mkdtemp(prefix='.prepare-', dir=cache))
        try:
            archive_path = staging / 'package.zip'
            _download(manifest['archive'], archive_path)
            extracted = staging / 'content'
            extracted.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                if len(archive.infolist()) > MAX_FILES:
                    raise InstallerReleaseError('套件檔案數量超過限制。')
                seen = set()
                total = 0
                for member in archive.infolist():
                    if member.is_dir():
                        safe_name(member.filename.rstrip('/'))
                        if stat.S_ISLNK(member.external_attr >> 16):
                            raise InstallerReleaseError('套件包含不安全的連結。')
                        continue
                    name = safe_name(member.filename)
                    mode = member.external_attr >> 16
                    if (stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG}) or
                            member.flag_bits & 1 or name not in manifest['files'] or name.casefold() in seen):
                        raise InstallerReleaseError('套件包含重複、額外、加密或不安全的檔案。')
                    seen.add(name.casefold())
                    total += member.file_size
                    if total > MAX_EXPANDED_BYTES or len(seen) > MAX_FILES:
                        raise InstallerReleaseError('套件解壓大小超過限制。')
                    destination = extracted.joinpath(*name.split('/'))
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open('xb') as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                if seen != {name.casefold() for name in manifest['files']}:
                    raise InstallerReleaseError('套件檔案不完整。')
            verify_prepared(extracted, manifest)
            try:
                extracted.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
                verify_prepared(target, manifest)
        except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise InstallerReleaseError('套件準備未完成，現有版本保持不變。') from exc
        finally:
            shutil.rmtree(staging)
    return {'release_id': identifier, 'version': manifest['version'],
            'component_id': manifest['component_id'], 'path': str(target),
            'manifest': manifest, 'sha256': manifest['archive']['sha256'], 'runtime': checked_runtime}
