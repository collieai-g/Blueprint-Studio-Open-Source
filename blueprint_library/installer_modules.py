"""Typed, per-studio BlueSAI runtime control and offline module protection."""
from __future__ import annotations

from blueprint_folders.path_safety import has_link

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import uuid
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import zipfile

from . import backup
from .maintenance import atomic_json


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('內部控制不接受重新導向。')


def private_path(value):
    path = Path(value).expanduser()
    if has_link(path):
        raise ValueError('模組私人位置不能包含目錄接點。')
    path = backup.safe_path(path)
    for parent in (path, *path.parents):
        if (parent / '.git').exists():
            raise ValueError('模組資料必須位於所有來源倉庫之外。')
    return path


def read_module_config(config, component):
    module = config.get('modules', {})[component]
    value = json.loads(private_path(module['config_file']).read_text(encoding='utf-8'))
    data = private_path(module['data_dir'])
    claim = data.parent / ('.' + data.name + '.blueprint-installer.json')
    identity = json.loads(private_path(claim).read_text(encoding='utf-8'))
    if (identity.get('root') != config['root'] or identity.get('installation_id') != config['installation_id'] or
            identity.get('component_id') != component):
        raise ValueError('模組資料已綁定另一個工作室。')
    if (value.get('component_id') != component or
            value.get('expected_agent_id') != module['expected_agent_id'] or
            private_path(value.get('data_dir')) != private_path(module['data_dir']) or
            value.get('host') != '127.0.0.1' or value.get('port') != module['port']):
        raise ValueError('模組設定與已綁定工作室不符。')
    return module, value


def _files(data):
    result = {}
    for path in sorted(data.rglob('*')):
        if has_link(path):
            raise ValueError('模組資料包含不安全連結。')
        if path.is_file():
            result[path.relative_to(data).as_posix()] = backup.digest(path)
    return result


def module_checkpoint(config, component, operation_id):
    """The managed module must already have exited and released service.lock."""
    if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', operation_id):
        raise ValueError('更新操作識別碼不正確。')
    module, private = read_module_config(config, component)
    data = private_path(module['data_dir'])
    if (data / 'service.lock').exists():
        raise ValueError('客服服務仍持有資料，未進行離線備份。')
    base = backup.safe_path(Path(config['backup_dir']) / 'modules' / component)
    recovery = backup.safe_path(Path(config['recovery_dir']) / 'modules' / component)
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    recovery.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = _files(data)
    settings = backup.digest(private_path(module['config_file']))
    coverage = hashlib.sha256(json.dumps({'files': files, 'settings': settings}, sort_keys=True).encode()).hexdigest()
    receipt_path = base / 'last.json'
    if receipt_path.exists():
        previous = json.loads(receipt_path.read_text(encoding='utf-8'))
        if (previous['coverage'] == coverage and 0 <= time.time() - previous['created_at'] < 86400 and
                backup.digest(backup.safe_path(previous['archive'])) == previous['sha256'] and
                backup.digest(backup.safe_path(previous['private_copy'])) == previous['sha256']):
            return {**previous, 'reused': True}
    target = backup.safe_path(base / operation_id)
    if target.exists():
        raise ValueError('模組保護結果未知，未覆寫原操作。')
    target.mkdir(mode=0o700)
    payload = target / 'data'
    payload.mkdir(mode=0o700)
    # Read SQLite only from an isolated copy. Even mode=ro can create WAL/SHM
    # sidecars beside a WAL database, changing the protected source inventory.
    # Copy its existing WAL too so committed pages are never ignored.
    raw = target / 'sqlite-source'
    raw.mkdir(mode=0o700)
    for name in files:
        saved = raw / name
        saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(data / name, saved)
        saved.chmod(0o600)
    if _files(raw) != files or _files(data) != files:
        raise ValueError('模組資料在離線複製期間改變。')
    databases = {name for name in files if Path(name).suffix in {'.sqlite', '.sqlite3', '.db'}}
    for name in files:
        original, saved = raw / name, payload / name
        saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if original.suffix in {'.sqlite', '.sqlite3', '.db'}:
            with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(saved)) as destination:
                if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('模組資料庫完整性檢查失敗。')
                source.backup(destination)
        elif name.endswith(('-wal', '-shm')) and name[:-4] in databases:
            continue
        else:
            shutil.copy2(original, saved)
        saved.chmod(0o600)
    shutil.copy2(module['config_file'], target / 'runtime-config.json')
    archive = target / 'backup.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as out:
        for path in sorted(payload.rglob('*')):
            if path.is_file():
                out.write(path, path.relative_to(target).as_posix())
        out.write(target / 'runtime-config.json', 'runtime-config.json')
    archive.chmod(0o600)
    restored = target / 'restore-check'
    with zipfile.ZipFile(archive) as source:
        # This archive was just made from the reviewed inventory above.
        source.extractall(restored)
    expected = {p.relative_to(target).as_posix(): backup.digest(p) for p in payload.rglob('*') if p.is_file()}
    expected['runtime-config.json'] = backup.digest(target / 'runtime-config.json')
    if _files(restored) != expected or _files(data) != files or backup.digest(Path(module['config_file'])) != settings:
        raise ValueError('模組還原或資料覆蓋未能核對。')
    for path in (restored / 'data').rglob('*'):
        if path.is_file() and path.suffix in {'.sqlite', '.sqlite3', '.db'}:
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('模組還原資料庫未通過核對。')
    shutil.rmtree(restored)
    shutil.rmtree(raw)
    copy = recovery / (operation_id + '.zip')
    with archive.open('rb') as source, copy.open('xb') as destination:
        copy.chmod(0o600)
        shutil.copyfileobj(source, destination)
        destination.flush(); os.fsync(destination.fileno())
    digest = backup.digest(archive)
    if backup.digest(copy) != digest:
        raise ValueError('模組私人副本讀回失敗。')
    result = {'component_id': component, 'coverage': coverage, 'archive': str(archive),
              'private_copy': str(copy), 'sha256': digest, 'created_at': time.time(),
              'verified': True, 'restore_verified': True, 'off_host_backup_verified': False, 'reused': False}
    atomic_json(receipt_path, result)
    return result


class ModuleRuntime:
    def __init__(self, supervisor, component):
        self.supervisor, self.component = supervisor, component
        self.config = supervisor.config
        self.module, self.private = read_module_config(self.config, component)
        self.process = None

    def control(self, action=None):
        child = self.supervisor._get().get('module_children', {}).get(self.component)
        if not child:
            raise ValueError('模組沒有可核對的受管程序。')
        body = json.dumps({'action': action}).encode() if action else None
        request = Request(f'http://127.0.0.1:{self.module["port"]}/api/managed/internal', data=body,
            headers={'Content-Type': 'application/json', 'X-Blueprint-Installer': child['token']})
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=5) as response:
            status = json.loads(response.read(16384))
        if (status.get('release_id') != child['release_id'] or
                status.get('expected_agent_id') != self.module['expected_agent_id'] or
                (child.get('pid') is not None and status.get('pid') != child['pid'])):
            raise ValueError('模組程序身份不符，未操作其他程序。')
        return status

    def _record(self, child):
        # Preserve other module children and the core heartbeat version.
        with self.supervisor._state_lock():
            state = self.supervisor._get_unlocked()
            children = dict(state.get('module_children', {}))
            if child is None:
                children.pop(self.component, None)
            else:
                children[self.component] = child
            self.supervisor._save_unlocked(state, module_children=children)

    def launch(self, release):
        from .installer_release import prepare_release
        # Re-read ownership and freeze the validated configuration for this boot.
        # Passing the mutable setup file would leave a validate/launch race.
        self.module, self.private = read_module_config(self.config, self.component)
        data = private_path(self.module['data_dir'])
        claim = data.parent / ('.' + data.name + '.blueprint-installer.json')
        ownership = json.loads(claim.read_text(encoding='utf-8'))
        if ownership.get('initialization_started'):
            if not (data / 'service.sqlite').is_file() or not (data / 'admin-token').is_file():
                raise ValueError('原客服資料或登入憑證遺失，未重新建立空白資料。')
        launch_configs = self.supervisor.root / 'module-launch'
        launch_configs.mkdir(exist_ok=True, mode=0o700)
        snapshot = launch_configs / (self.component + '-' + uuid.uuid4().hex + '.json')
        frozen = dict(self.private, data_dir=str(private_path(self.module['data_dir'])))
        if ownership.get('initialization_started'):
            frozen.update(initialize=False, existing_data=True)
        atomic_json(snapshot, frozen)
        prepared = prepare_release(release['envelope'], self.config['trusted_keys'], self.supervisor.root / 'releases',
            installed=self.supervisor._get().get('components', {}), runtimes={'node': self.module['node']})
        if prepared['release_id'] != release['release_id']:
            raise ValueError('模組固定版本已改變。')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', self.module['port']))
        # A failed/unknown first boot must never silently initialize again.
        ownership['initialization_started'] = True
        atomic_json(claim, ownership)
        token = secrets.token_urlsafe(32)
        env = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
        env.update(BLUEPRINT_MODULE_TOKEN=token, BLUEPRINT_MODULE_RELEASE_ID=release['release_id'])
        log_path = backup.safe_path(self.supervisor.root / (self.component + '.log'))
        log = log_path.open('ab')
        log_path.chmod(0o600)
        try:
            self.process = subprocess.Popen([self.module['node'], str(Path(prepared['path']) / prepared['manifest']['entrypoint']),
                '--config', str(snapshot)], cwd=prepared['path'], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        finally:
            log.close()
        child = {'pid': None, 'launcher_pid': self.process.pid, 'release_id': release['release_id'], 'token': token,
                 'config_sha256': backup.digest(snapshot)}
        self._record(child)
        deadline = time.monotonic() + self.supervisor.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ValueError('客服受管程序啟動失敗，請核對私人日誌。')
            try:
                status = self.control()
                if status.get('ready') and status.get('paused') and status.get('version') == release['version'] and status.get('database_schema') == release['manifest']['database_schema']:
                    child['pid'] = status['pid']; self._record(child)
                    return status
            except (OSError, ValueError):
                pass
            time.sleep(.1)
        raise ValueError('模組啟動結果尚未確認。')

    def quiesce(self):
        self.control('quiesce')
        deadline = time.monotonic() + max(75, self.supervisor.stop_timeout)
        while time.monotonic() < deadline:
            status = self.control()
            if status.get('paused') and not status.get('busy'):
                return
            time.sleep(.1)
        raise ValueError('客服工作仍未完成，已保留工作並延期更新。')

    def stop(self):
        if self.process is not None and self.process.poll() is not None:
            self._record(None)
            return
        self.quiesce()
        self.control('shutdown')
        deadline = time.monotonic() + max(75, self.supervisor.stop_timeout)
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                self._record(None)
                return
            if self.process is None:
                try:
                    self.control()
                except OSError:
                    if not (Path(self.module['data_dir']) / 'service.lock').exists():
                        self._record(None)
                        return
            time.sleep(.1)
        raise ValueError('客服程序尚未退出，未強制停止。')
