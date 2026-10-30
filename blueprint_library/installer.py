"""Private native installation controller; no company records or credentials in source.

The supervisor owns only its recorded child. Packages are verified before launch;
updates currently require an unchanged database schema and an offline checkpoint.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from http.client import HTTPException
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, unquote
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid
import zipfile

from . import backup
from .maintenance import DataLease, atomic_json
from .store import LibraryError, SCHEMA_VERSION
from blueprint_folders.path_safety import has_link

FORMAT = 'blueprint-managed-installation/1'
STATE_FORMAT = 'blueprint-managed-state/1'
MAX_DOCUMENT = 2 * 1024 * 1024


class InstallerBusyError(ValueError):
    """A safe operational reason for deferring an update."""


def _private(value):
    original = Path(value).expanduser().absolute()
    if has_link(original):
        raise ValueError('私人安裝路徑不能包含目錄接點。')
    path = backup.safe_path(original)
    backup.external(path)
    if any((part / '.git').exists() for part in (path, *path.parents)):
        raise ValueError('程式、資料、備份及恢復位置必須位於所有來源倉庫之外。')
    return path


def _program_root(value):
    """Permit only the owning controller of the currently running fixed package."""
    original = Path(value).expanduser().absolute()
    path = backup.safe_path(original)
    source = backup.SOURCE
    if (source.is_dir() and source.parent.name == 'releases' and
            re.fullmatch(r'[0-9a-f]{64}', source.name) and source.parent.parent == path):
        # This exact root is necessarily an ancestor of its immutable package.
        # Data, backups and recovery paths still use the strict _private check.
        if has_link(original) or has_link(source):
            raise ValueError('受管程式位置不能包含重新解析點。')
        if any((part / '.git').exists() for part in (source, *source.parents)):
            raise ValueError('受管程式必須位於所有來源倉庫之外。')
        return path
    return _private(original)


def _read(path):
    path = backup.safe_path(path)
    if path.stat().st_size > MAX_DOCUMENT:
        raise ValueError('安裝紀錄過大，請核對私人安裝位置。')
    return json.loads(path.read_text(encoding='utf-8'))


@contextmanager
def _lock(root, name, *, wait=True):
    path = backup.safe_path(Path(root) / name)
    stream = path.open('a+b')
    path.chmod(0o600)
    try:
        if os.name == 'nt':
            import msvcrt
            if path.stat().st_size == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            # State writes are brief. A supervisor lock is always nonblocking.
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if wait else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        yield
    finally:
        stream.close()


def _config(root):
    root = _program_root(root)
    value = _read(root / 'installation.json')
    if value.get('format') != FORMAT or value.get('root') != str(root):
        raise ValueError('安裝身份不符，未變更工作室。')
    directories = [root, *(_private(value[key]) for key in ('data_dir', 'backup_dir', 'recovery_dir'))]
    for index, left in enumerate(directories):
        for right in directories[index + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('安裝、資料、備份及私人恢復副本位置必須互相獨立。')
    owner = _read(_owner_path(directories[1]))
    if owner != {'format': FORMAT, 'root': str(root), 'installation_id': value['installation_id']}:
        raise ValueError('資料目錄已屬於另一個受管安裝。')
    if value.get('host') != '127.0.0.1' or type(value.get('port')) is not int or not 1 <= value['port'] <= 65535:
        raise ValueError('第一版安裝器只支援明確的 loopback 位址及固定埠。')
    if (Path(value['python']).resolve() != Path(sys.executable).resolve()
            or value.get('python_prefix') != str(Path(sys.prefix).resolve())):
        raise ValueError('請使用初次安裝時的同一個隔離 Python 環境。')
    return value


def _owner_path(data):
    return data.parent / ('.' + data.name + '.blueprint-installer.json')


def _state(root):
    value = _read(Path(root) / 'state.json')
    if value.get('format') != STATE_FORMAT or type(value.get('version')) is not int:
        raise ValueError('更新狀態格式不正確，未重新建立紀錄。')
    return value


def _save(root, state, **changes):
    state.update(changes)
    state['version'] += 1
    state['updated_at'] = time.time()
    atomic_json(Path(root) / 'state.json', state)


def _public(state):
    def component(value):
        if not value:
            return None
        manifest = value.get('manifest', {})
        return {'release_id': value['release_id'], 'component_id': value['component_id'],
                'version': value['version'], 'summary': str(manifest.get('notes', ''))[:2000],
                'notes': str(manifest.get('notes', ''))[:2000]}
    components = dict(state.get('components', {}))
    components.setdefault('blueprint-core', state.get('installed'))
    result = {'supported': True, 'contract': FORMAT, 'version': state['version'],
            'phase': state['phase'], 'installed': component(state.get('installed')),
            'current': component(state.get('installed')),
            'can_apply': state['phase'] == 'ready' and bool(state.get('child')) and
                         time.time() - state.get('last_healthy_at', 0) < 15,
            'prepared': component(state.get('prepared')), 'error': state.get('error'),
            'operation_id': state.get('request', {}).get('operation_id'),
            'running': bool(state.get('child')) and time.time() - state.get('last_healthy_at', 0) < 15, 'last_check': state.get('last_check'),
            'schema_migration_supported': False, 'module_adapters': ['native-core', 'bluesai-node'],
            'backup_verified': bool(state.get('checkpoint', {}).get('verified')),
            'off_host_backup_verified': False}
    result['components'] = {key: component(value) for key, value in components.items() if value}
    result['plan'] = state.get('public_plan', [])
    result['observed_components'] = state.get('observed_components', {})
    prepared = state.get('prepared') or {}
    if prepared.get('component_id') in components:
        result['current'] = component(components[prepared['component_id']])
    return result


def read_status(root):
    config = _config(root)
    with _lock(root, '.state.lock'):
        result = _public(_state(root))
        result['installation_id'] = config['installation_id']
        result['channel'] = config.get('channel', 'legacy-signed-release')
        return result


def request_check(root):
    _config(root)
    with _lock(root, '.state.lock'):
        state = _state(root)
        if not state.get('check_requested'):
            _save(root, state, check_requested=True)
        return _public(state)


def request_apply(root, release_id, expected_version):
    _config(root)
    with _lock(root, '.state.lock'):
        state = _state(root)
        prior = state.get('request')
        if prior and prior.get('release_id') == release_id and state['phase'] in ('apply_requested', 'applying', 'complete'):
            return {**_public(state), 'idempotent': True}
        if type(expected_version) is not int or expected_version != state['version']:
            raise LibraryError('更新狀態已有變動，請重新讀取。', 409)
        prepared = state.get('prepared')
        if state['phase'] not in ('ready', 'complete') or not prepared or prepared['release_id'] != release_id:
            raise LibraryError('這項更新尚未準備完成。', 409)
        _save(root, state, phase='apply_requested', request={'operation_id': uuid.uuid4().hex,
              'release_id': release_id}, error=None)
        return _public(state)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('更新目錄不接受重新導向。')


def read_feed(source):
    """Only an explicitly configured local file or HTTPS feed; never UI input."""
    parts = urlsplit(str(source))
    if parts.scheme == 'https' and parts.hostname and not parts.username and not parts.password and not parts.fragment:
        from .installer_release import _source
        kind, address = _source(str(source))
        if kind != 'https':
            raise ValueError('更新目錄的 HTTPS 來源不正確。')
        try:
            with build_opener(_NoRedirect).open(Request(address, headers={'Accept': 'application/json'}), timeout=30) as response:
                payload = response.read(MAX_DOCUMENT + 1)
        except HTTPException as error:
            raise ValueError('更新目錄的 HTTP 傳輸未完成或回應格式不正確。') from error
    elif parts.scheme == 'file' and parts.netloc in ('', 'localhost'):
        from urllib.request import url2pathname
        with backup.safe_path(url2pathname(unquote(parts.path))).open('rb') as source_file:
            payload = source_file.read(MAX_DOCUMENT + 1)
    elif not parts.scheme or (len(parts.scheme) == 1 and str(source)[1:3] in (':\\', ':/')):
        with backup.safe_path(source).open('rb') as source_file:
            payload = source_file.read(MAX_DOCUMENT + 1)
    else:
        raise ValueError('更新目錄只接受 HTTPS 或明確設定的本機檔案。')
    if len(payload) > MAX_DOCUMENT:
        raise ValueError('更新目錄過大。')
    try:
        value = json.loads(payload)
    except RecursionError as error:
        raise ValueError('更新目錄的 JSON 巢狀層數過深。') from error
    if not isinstance(value, dict):
        raise ValueError('更新目錄格式不正確。')
    return value


def _existing_native_backup(data_dir, backup_dir):
    """Read a completed native identity without repairing or rewriting it."""
    state_file = _private(backup_dir / 'installation.json')
    try:
        value = _read(state_file)
        service_id = value.get('service_id')
        requests = value.get('backup_requests')
        copies = value.get('client_copies')
        last = value.get('last_backup')
        if (not isinstance(value, dict) or value.get('format') != 'blueprint-native-installation' or
                type(value.get('version')) is not int or value['version'] != 1 or
                value.get('data') != str(data_dir) or not isinstance(service_id, str) or
                not re.fullmatch(r'[0-9a-f]{32}', service_id)):
            raise ValueError('原生備份身份不完整或屬於另一個工作室。')
        if (value.get('status') != 'ready' or 'pending' not in value or value['pending'] is not None or
                value.get('failed_code') is not None):
            raise ValueError('原生維護仍未完成，請先核對原操作。')
        _version(value.get('product_version'))
        if (not isinstance(value.get('code'), str) or not value['code'] or
                not isinstance(requests, dict) or not isinstance(copies, dict) or
                any(not isinstance(item, dict) or any(not isinstance(receipt, dict) for receipt in item.values())
                    for item in copies.values()) or
                any(not isinstance(item, dict) or item.get('status') != 'complete' or
                    ('backup' in item and not isinstance(item['backup'], dict)) for item in requests.values())):
            raise ValueError('原生備份操作仍未完成或紀錄格式不正確。')
        if (not isinstance(last, dict) or not isinstance(last.get('id'), str) or
                not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', last['id']) or
                not isinstance(last.get('coverage'), dict) or last['coverage'].get('scope') != service_id):
            raise ValueError('原生備份收據與服務身份不符。')
    except (OSError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError('找不到可接續的原生備份身份；不會重新建立紀錄。') from exc
    return {'service_id': service_id, 'product_version': value['product_version'],
            'journal_sha256': backup.digest(state_file)}


def initialize_installation(root, envelope, trusted_keys, *, data_dir, backup_dir,
                            recovery_dir=None, username='admin', password=None,
                            existing_data=False, existing_backups=False, host='127.0.0.1',
                            port=8787, feed=None, channel=None):
    from .installer_release import prepare_release
    if type(existing_backups) is not bool or (existing_backups and not existing_data):
        raise ValueError('接續原備份必須同時使用 existing_data=True。')
    if channel is not None and channel not in {'test', 'stable', 'private'}:
        raise ValueError('更新渠道不正確。')
    root = _program_root(root)
    data_dir, backup_dir = map(_private, (data_dir, backup_dir))
    recovery_dir = _private(recovery_dir or root.with_name(root.name + '-recovery'))
    directories = [root, data_dir, backup_dir, recovery_dir]
    for i, left in enumerate(directories):
        for right in directories[i + 1:]:
            if left == right or left in right.parents or right in left.parents:
                raise ValueError('安裝、資料、備份及私人恢復副本位置必須互相獨立。')
    if host != '127.0.0.1' or type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('第一版只支援 loopback 位址及固定埠。')
    if root.exists() and any(root.iterdir()):
        raise ValueError('安裝目錄不是空白，請沿用既有安裝。')
    if _owner_path(data_dir).exists():
        raise ValueError('資料位置已有受管安裝身份，不能重複接入。')
    if existing_data:
        if not (data_dir / 'access.json').is_file() or not (data_dir / 'library.sqlite3').is_file():
            raise ValueError('找不到原有工作室，不會建立空白資料代替。')
    elif data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError('資料位置不是空白，未重新初始化。')
    if existing_backups:
        if any((parent / '.git').exists() for directory in directories for parent in (directory, *directory.parents)):
            raise ValueError('原生資料及備份必須位於所有來源倉庫之外。')
        original_backup = _existing_native_backup(data_dir, backup_dir)
        from .installer_release import verify_release
        manifest = verify_release(envelope, trusted_keys)
        if manifest['component_id'] != 'blueprint-core' or manifest['database_schema'] != SCHEMA_VERSION:
            raise ValueError('初次接入只支援目前原生 schema 的核心套件。')
        if _version(manifest['version']) < _version(original_backup['product_version']):
            raise ValueError('固定套件早於原生運行版本，未降低程式版本。')
    else:
        original_backup = None
    for directory in (backup_dir, recovery_dir):
        if directory == backup_dir and existing_backups:
            continue
        if directory.exists() and any(directory.iterdir()):
            raise ValueError('請選空白的私人備份及恢復副本位置。')
    with DataLease(data_dir):
        if existing_data:
            with closing(backup.database(data_dir / 'library.sqlite3')) as conn:
                if conn.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                    raise ValueError('原資料需要另行遷移，未接入自動更新。')
        if existing_backups and _existing_native_backup(data_dir, backup_dir) != original_backup:
            raise ValueError('原生備份身份已有變動，請先核對原操作。')
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        prepared = prepare_release(envelope, trusted_keys, root / 'releases')
        _version(prepared['version'])
        if prepared['component_id'] != 'blueprint-core' or prepared['manifest']['database_schema'] != SCHEMA_VERSION:
            raise ValueError('初次接入只支援目前原生 schema 的核心套件。')
        if existing_backups:
            if _existing_native_backup(data_dir, backup_dir) != original_backup:
                raise ValueError('原生備份紀錄在準備期間變動，未接入或覆寫。')
            if _version(prepared['version']) < _version(original_backup['product_version']):
                raise ValueError('固定套件早於原生運行版本，未降低程式版本。')
        if not existing_data:
            from .server import _initialize_library
            _initialize_library(data_dir, username, password)
        for directory in (backup_dir, recovery_dir):
            if directory == backup_dir and existing_backups:
                continue
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
        installation_id = uuid.uuid4().hex
        config = {'format': FORMAT, 'installation_id': installation_id, 'root': str(root),
                  'data_dir': str(data_dir), 'backup_dir': str(backup_dir), 'recovery_dir': str(recovery_dir),
                  'python': str(Path(sys.executable).resolve()), 'python_prefix': str(Path(sys.prefix).resolve()), 'host': host, 'port': port,
                  'feed': str(feed) if feed else None, 'trusted_keys': trusted_keys}
        if channel is not None:
            config['channel'] = channel
        # Claim is exclusive. It is outside company data and never enters its backups.
        claim = _owner_path(data_dir)
        with claim.open('x', encoding='utf-8') as stream:
            claim.chmod(0o600)
            json.dump({'format': FORMAT, 'root': str(root), 'installation_id': installation_id}, stream)
        atomic_json(root / 'installation.json', config)
        prepared['envelope'] = envelope
        atomic_json(root / 'state.json', {'format': STATE_FORMAT, 'version': 1,
                    'phase': 'installed', 'installed': prepared, 'prepared': None,
                    'child': None, 'history': [], 'error': None, 'updated_at': time.time()})
    return read_status(root)


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('原生受管服務目前只接受穩定版號。')
    return tuple(map(int, value.split('.')))


def _same_schema(config, current, candidate):
    schema = current['manifest']['database_schema']
    if candidate['manifest']['database_schema'] != schema:
        raise ValueError('這項更新需要資料遷移；第一版受管安裝器保留待辦，未自動套用。')
    with closing(backup.database(Path(config['data_dir']) / 'library.sqlite3')) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] != schema:
            raise ValueError('實際資料格式與已安裝版本不符，未套用更新。')


def ensure_quiescent(config):
    """No external runtime is stopped until it has a real quiescence adapter."""
    with closing(backup.database(Path(config['data_dir']) / 'library.sqlite3')) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'agent_jobs' in tables and conn.execute("SELECT 1 FROM agent_jobs WHERE state NOT IN ('completed','failed','cancelled') LIMIT 1").fetchone():
            raise InstallerBusyError('尚有未完成 AI 工作，請完成工作或使用支援任務接續的更新接駁。')
        if 'agent_registrations' in tables:
            managed = {value['expected_agent_id'] for value in config.get('modules', {}).values()}
            for row in conn.execute('SELECT id,document FROM agent_registrations'):
                agent = json.loads(row[1])
                if agent.get('enabled') and agent.get('connection_state') == 'connected' and row[0] not in managed:
                    raise InstallerBusyError('尚有已連接 AI 服務，需要支援暫停與恢復的更新接駁。')
        if 'integration_registry' in tables:
            for row in conn.execute('SELECT document FROM integration_registry'):
                integration = json.loads(row[0])
                if integration.get('enabled') and not integration.get('revoked'):
                    raise InstallerBusyError('尚有運行中的外部整合，需要支援暫停與恢復的更新接駁。')


def create_checkpoint(config, operation_id):
    from .installer_backup import checkpoint
    return checkpoint(config, operation_id)


class CoreSupervisor:
    """Owns a single loopback child and polls only configured release metadata."""
    def __init__(self, root, *, check_interval=300, startup_timeout=60, stop_timeout=45):
        self.root = _program_root(root)
        self.config = _config(self.root)
        self.check_interval = max(60, check_interval)
        self.startup_timeout, self.stop_timeout = startup_timeout, stop_timeout
        self.child = None
        self.stop_event = threading.Event()

    def _get(self):
        with _lock(self.root, '.state.lock'):
            return _state(self.root)

    def _state_lock(self):
        return _lock(self.root, '.state.lock')

    def _get_unlocked(self):
        return _state(self.root)

    def _save_unlocked(self, state, **changes):
        _save(self.root, state, **changes)

    def _update(self, **changes):
        with _lock(self.root, '.state.lock'):
            state = _state(self.root)
            _save(self.root, state, **changes)
            return state

    def _healthy(self):
        # Liveness is not a new update intent. Periodic heartbeats must not
        # invalidate a version just read by the browser's apply button.
        with _lock(self.root, '.state.lock'):
            state = _state(self.root)
            state['last_healthy_at'] = time.time()
            atomic_json(self.root / 'state.json', state)

    def _internal(self, child, action=None):
        host = '[::1]' if self.config['host'] == '::1' else self.config['host']
        data = json.dumps({'action': action}).encode() if action else None
        request = Request(f'http://{host}:{self.config["port"]}/api/installer/internal', data=data,
                          headers={'X-Blueprint-Installer': child['token'], 'Content-Type': 'application/json'})
        with build_opener(_NoRedirect).open(request, timeout=3) as response:
            value = json.loads(response.read(8192))
        if action is None and (value.get('release_id') != child['release_id'] or (child.get('pid') is not None and value.get('pid') != child['pid'])):
            raise ValueError('運行服務不是本次擁有的程式，未發出停止操作。')
        return value

    def _launch(self, release):
        from .installer_release import prepare_release
        checked = prepare_release(release['envelope'], self.config['trusted_keys'], self.root / 'releases')
        if checked['release_id'] != release['release_id']:
            raise ValueError('啟動前的固定產物已改變。')
        # A pre-existing listener must never be mistaken for the child about to start.
        family = socket.AF_INET6 if self.config['host'] == '::1' else socket.AF_INET
        with socket.socket(family) as probe:
            if os.name != 'nt':
                # Match the server's POSIX TIME_WAIT policy after an owned stop.
                # No SO_REUSEPORT: an active listener must still reject this bind.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((self.config['host'], self.config['port']))
        token = secrets.token_urlsafe(32)
        env = dict(os.environ, BLUEPRINT_INSTALLATION_ROOT=str(self.root),
                   BLUEPRINT_INSTALLER_TOKEN=token, BLUEPRINT_RELEASE_ID=release['release_id'],
                   BLUEPRINT_BACKUP_DIR=self.config['backup_dir'], PYTHONDONTWRITEBYTECODE='1')
        args = [self.config['python'], '-X', 'utf8', '-B', str(Path(checked['path']) / 'blueprint.py'),
                'library', '--data', self.config['data_dir'], '--host', self.config['host'],
                '--port', str(self.config['port']), '--no-open']
        log_path = backup.safe_path(self.root / 'service.log')
        log = log_path.open('ab')
        log_path.chmod(0o600)
        try:
            self.child = subprocess.Popen(args, cwd=checked['path'], env=env, stdin=subprocess.DEVNULL,
                                          stdout=log, stderr=log,
                                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        finally:
            log.close()
        child = {'pid': None, 'launcher_pid': self.child.pid, 'token': token, 'release_id': release['release_id']}
        self._update(child=child, last_healthy_at=0)
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.child.poll() is not None:
                raise ValueError('新版服務未能啟動；請查看私人服務日誌。')
            try:
                value = self._internal(child)
                if type(value.get('pid')) is not int or value['pid'] <= 0:
                    raise ValueError('服務未回報有效程序身份。')
                if child['pid'] is None:
                    child['pid'] = value['pid']
                    self._update(child=child)
                if (value.get('ready') is True and value.get('version') == release['version']
                        and value.get('database_schema') == release['manifest']['database_schema']):
                    self._healthy()
                    return
            except (OSError, ValueError):
                pass
            self.stop_event.wait(.2)
        raise ValueError('服務啟動狀態尚未確認；未宣稱更新完成。')

    def _stop_child(self):
        child = self._get().get('child')
        if not child:
            return
        if self.child is not None and self.child.poll() is not None:
            self._update(child=None)
            self.child = None
            return
        self._internal(child)  # Verify identity again immediately before shutdown.
        self._internal(child, 'shutdown')
        deadline = time.monotonic() + self.stop_timeout
        while time.monotonic() < deadline:
            if self.child is not None:
                if self.child.poll() is not None:
                    self.child = None
                    self._update(child=None)
                    return
            else:
                try:
                    self._internal(child)
                except (OSError, ValueError):
                    # Taking the data lease proves the owned server released its data.
                    try:
                        with DataLease(self.config['data_dir']):
                            pass
                        self._update(child=None)
                        return
                    except ValueError:
                        pass
            time.sleep(.1)
        raise ValueError('服務仍有工作未完成；未強制停止或更換程式。')

    def check(self):
        from .installer_release import prepare_release, verify_release
        if not self.config.get('feed'):
            self._update(check_requested=False, last_check=time.time())
            return read_status(self.root)
        with self._state_lock():
            state = self._get_unlocked()
            if state['phase'] in ('applying', 'apply_requested', 'recovery_required'):
                return _public(state)
            self._save_unlocked(state, phase='preparing', error=None)
        try:
            self._verify_installed()
            document = read_feed(self.config['feed'])
            envelopes = document.get('releases', [document])
            if not isinstance(envelopes, list) or len(envelopes) > 200:
                raise ValueError('更新目錄項目不正確。')
            options = []
            for envelope in envelopes:
                manifest = verify_release(envelope, self.config['trusted_keys'])
                if manifest['component_id'] == 'blueprint-core':
                    version = _version(manifest['version'])
                    if version > _version(state['installed']['version']):
                        options.append((version, envelope))
            if not options:
                self._update(phase='complete' if state.get('history') else 'installed',
                             prepared=None, check_requested=False, last_check=time.time())
            else:
                envelope = max(options, key=lambda option: option[0])[1]
                candidate = prepare_release(envelope, self.config['trusted_keys'], self.root / 'releases',
                                            installed={'blueprint-core': state['installed']})
                _same_schema(self.config, state['installed'], candidate)
                ensure_quiescent(self.config)
                candidate['envelope'] = envelope
                self._update(phase='ready', prepared=candidate, error=None,
                             check_requested=False, last_check=time.time())
        except (ValueError, OSError, HTTPException, KeyError, TypeError) as exc:
            self._update(phase='prepare_failed', prepared=None, error=str(exc) if isinstance(exc, InstallerBusyError) else '未能完成更新準備；目前版本仍可使用。',
                         check_requested=False, last_check=time.time())
        return read_status(self.root)

    def _verify_installed(self):
        """Do not replace locally customized code with an upstream package."""
        from .installer_release import verify_release, verify_prepared, release_id
        state = self._get()
        releases = dict(state.get('components', {}))
        releases.setdefault('blueprint-core', state['installed'])
        try:
            for release in releases.values():
                manifest = verify_release(release['envelope'], self.config['trusted_keys'])
                identifier = release_id(manifest)
                expected = self.root / 'releases' / identifier
                if release['release_id'] != identifier or Path(release['path']) != expected:
                    raise ValueError('Installed release identity differs.')
                verify_prepared(expected, manifest)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise InstallerBusyError('現有程式與固定版本不同，可能包含客製修改；請先整合來源，未覆蓋原有程式。') from exc

    def apply(self):
        from .installer_release import prepare_release
        state = self._get()
        candidate, current = state.get('prepared'), state['installed']
        if state['phase'] != 'apply_requested' or not candidate:
            raise ValueError('沒有已確認的更新操作。')
        request = state['request']
        try:
            self._verify_installed()
            # Re-fetch the configured feed immediately before a destructive transition.
            if self.config.get('feed'):
                document = read_feed(self.config['feed'])
                listed = document.get('releases', [document])
                if candidate['envelope'] not in listed:
                    self._update(phase='prepare_failed', prepared=None, error='候選已撤回或改變，請重新準備。')
                    return read_status(self.root)
            checked = prepare_release(candidate['envelope'], self.config['trusted_keys'], self.root / 'releases',
                                      installed={'blueprint-core': current})
            _same_schema(self.config, current, checked)
            ensure_quiescent(self.config)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._update(phase='prepare_failed', prepared=None, error=str(exc) if isinstance(exc, InstallerBusyError) else '套用前核對未通過；原有服務保持運行。')
            return read_status(self.root)
        self._update(phase='applying', error=None, pending_release=candidate)
        try:
            self._stop_child()
            with DataLease(self.config['data_dir']):
                ensure_quiescent(self.config)
                checkpoint = create_checkpoint(self.config, request['operation_id'])
                _same_schema(self.config, current, candidate)
            self._update(checkpoint=checkpoint)
            self._launch(candidate)
            state = self._get()
            history = state.get('history', []) + [{'operation_id': request['operation_id'],
                       'release_id': candidate['release_id'], 'from': current['version'],
                       'to': candidate['version'], 'completed_at': time.time()}]
            self._update(phase='complete', installed=candidate, prepared=None,
                         pending_release=None, error=None, history=history)
        except (OSError, ValueError):
            # Candidate may have written data or advanced maintenance. Never restore
            # an old database or silently lower that journal merely to restart code.
            self._update(phase='recovery_required', error='更新未完成；原資料與保護副本已保留，需核對私人操作後接續。')
        return read_status(self.root)

    def run(self):
        with _lock(self.root, '.supervisor.lock', wait=False):
            state = self._get()
            if state['phase'] in ('applying', 'recovery_required'):
                # Reconcile a completed launch before asking for manual recovery.
                pending = state.get('pending_release')
                child = state.get('child')
                if pending and child:
                    try:
                        value = self._internal(child)
                        if value.get('ready') is True and value.get('version') == pending['version']:
                            self._update(phase='complete', installed=pending, prepared=None,
                                         pending_release=None, error=None)
                        else:
                            raise ValueError('未確認新版運行。')
                    except (OSError, ValueError):
                        self._update(phase='recovery_required', error='上次套用結果未知；保留資料，請核對後接續。')
                        raise ValueError('上次套用未完成，未盲目重新啟動或還原。')
                else:
                    raise ValueError('上次套用未完成，未盲目重新啟動或還原。')
            state = self._get()
            next_check = time.monotonic()
            next_health = time.monotonic() + 5
            try:
                if state.get('child'):
                    # Adoption requires both identity and actual release readiness.
                    value = self._internal(state['child'])
                    if (value.get('ready') is not True or value.get('version') != state['installed']['version']
                            or value.get('database_schema') != state['installed']['manifest']['database_schema']):
                        raise ValueError('原程序尚未通過版本及啟動核對。')
                    self._healthy()
                else:
                    self._launch(state['installed'])
                while not self.stop_event.wait(.5):
                    state = self._get()
                    if state['phase'] == 'apply_requested':
                        self.apply()
                    elif state.get('check_requested') or time.monotonic() >= next_check:
                        self.check()
                        next_check = time.monotonic() + self.check_interval
                    state = self._get()
                    if time.monotonic() >= next_health and state.get('child'):
                        try:
                            value = self._internal(state['child'])
                            if value.get('ready') is True:
                                self._healthy()
                        except (OSError, ValueError):
                            self._update(last_healthy_at=0, error='工作室程序狀態尚未確認。')
                        next_health = time.monotonic() + 5
                    if self.child is not None and self.child.poll() is not None:
                        self._update(child=None, error='工作室服務已停止；請核對私人日誌。')
                        break
            finally:
                self._stop_child()

    def stop(self):
        self.stop_event.set()


# Resolve the public constructor lazily so either module can be imported first.
# The v1 core path remains available for existing signed, single-core feeds.
def __getattr__(name):
    if name == 'Supervisor':
        from .installer_components import ComponentSupervisor
        return ComponentSupervisor
    raise AttributeError(name)
