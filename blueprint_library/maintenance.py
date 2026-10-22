"""Native lifecycle: exclusive ownership, verified backups, migration and recovery.

No network, deployment credentials or shell execution. The host selects a checked
release; this module protects its existing data before the server accepts traffic.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
import zipfile

from blueprint_folders import __version__
from . import backup

_OWNED = {}
_OWNERS_LOCK = threading.Lock()


def atomic_json(path, value):
    backup.safe_path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            temporary.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class DataLease:
    """OS-held lock beside data, surviving neither process exit nor data rename."""
    def __init__(self, data):
        self.data = backup.safe_path(data); backup.external(self.data)
        self.path = backup.safe_path(self.data.parent / ('.' + self.data.name + '.blueprint.lock'))
        self.file = None

    def __enter__(self):
        with _OWNERS_LOCK:
            if str(self.data) in _OWNED:
                raise ValueError('此資料目錄已有工作台或維護操作。')
            self.data.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = self.path.open('a+b'); self.path.chmod(0o600)
            try:
                if os.name == 'nt':
                    import msvcrt
                    if self.path.stat().st_size == 0:
                        stream.write(b'0'); stream.flush()
                    stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                raise ValueError('原工作台仍在運行；請先停止原服務，不能同時更新或還原。') from exc
            self.file = stream
            _OWNED[str(self.data)] = os.getpid()
        return self

    def __exit__(self, *_):
        with _OWNERS_LOCK:
            if self.file is not None:
                self.file.close(); self.file = None
                _OWNED.pop(str(self.data), None)


def assert_store_access(data):
    """Other processes must use the running service's HTTP/MCP API."""
    data = Path(data)
    if _OWNED.get(str(data)) == os.getpid():
        return
    path = data.parent / ('.' + data.name + '.blueprint.lock')
    if path.exists():
        with DataLease(data):
            pass


class RequestGate:
    """Allow concurrent requests; pause admissions and drain for a backup."""
    def __init__(self):
        self.condition = threading.Condition()
        self.readers = 0
        self.paused = False

    @contextmanager
    def request(self):
        with self.condition:
            while self.paused:
                self.condition.wait()
            self.readers += 1
        try:
            yield
        finally:
            with self.condition:
                self.readers -= 1; self.condition.notify_all()

    @contextmanager
    def pause(self):
        with self.condition:
            self.paused = True
            self.condition.wait_for(lambda: self.readers == 0)
        try:
            yield
        finally:
            with self.condition:
                self.paused = False; self.condition.notify_all()


def code_digest(source):
    digest = hashlib.sha256()
    files = [source / 'blueprint.py']
    for directory in ['blueprint_library', 'blueprint_folders', 'library_web', 'assets/icons']:
        files.extend(p for p in (source / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for path in sorted(files):
        if path.is_symlink():
            raise ValueError('正式程式不能包含符號連結。')
        digest.update(path.relative_to(source).as_posix().encode() + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()


class Maintenance:
    def __init__(self, data, directory=None, *, version=__version__, fingerprint=None, interval=86400):
        self.data = backup.safe_path(data); backup.external(self.data)
        self.directory = backup.safe_path(directory or self.data.parent / (self.data.name + '-backups'))
        backup.external(self.directory)
        if self.directory == self.data or self.data in self.directory.parents or self.directory in self.data.parents:
            raise ValueError('備份必須使用資料目錄以外的私人位置。')
        if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not 60 <= interval <= 604800:
            raise ValueError('版本或備份間隔不正確。')
        self.version = version
        self.fingerprint = fingerprint or code_digest(Path(__file__).resolve().parents[1])
        self.interval = interval
        self.lease = DataLease(self.data)
        self.gate = RequestGate()
        self.state = {}
        self.stop = threading.Event(); self.wake = threading.Event()
        self.thread = None
        self.busy = False
        self.requested = False
        self.mutex = threading.Lock()

    def save(self):
        atomic_json(self.directory / 'installation.json', self.state)

    def _space(self):
        size = sum(p.stat().st_size for p in self.data.rglob('*') if p.is_file())
        if shutil.disk_usage(self.directory).free < size * 4 + 16 * 1024 * 1024:
            raise ValueError('備份與還原演練的儲存空間不足；未繼續更新。')

    def _snapshot(self, reason):
        self._space()
        name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]
        target = self.directory / name
        backup.snapshot(self.data, target)
        restored = self.directory / ('.restore-check-' + uuid.uuid4().hex)
        backup.restore(target, restored)
        # The restored snapshot must be usable independently, before any migration.
        backup.verify(restored, target)
        shutil.rmtree(restored)
        archive = target / 'backup.zip'
        with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
            z.write(target / 'manifest.json', 'manifest.json')
            for path in sorted((target / 'data').rglob('*')):
                if path.is_file():
                    z.write(path, path.relative_to(target).as_posix())
        archive.chmod(0o600)
        receipt = {'id': name, 'created_at': time.time(), 'reason': reason,
                   'restore_verified': True, 'sha256': backup.digest(archive)}
        atomic_json(target / 'receipt.json', receipt)
        self.state['last_backup'] = receipt
        self.state['backup_error'] = None
        self.save()
        return target

    def _recover(self):
        pending = self.state['pending']
        name = pending['snapshot']
        if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', name):
            raise ValueError('未完成更新的備份識別碼無效。')
        saved = self.directory / name
        backup.check(saved)
        recovered = self.data.with_name(self.data.name + '.restore-' + uuid.uuid4().hex)
        backup.restore(saved, recovered)
        # Rename on the data filesystem: failed/new contents are never discarded.
        if self.data.exists():
            failed = self.data.with_name(self.data.name + '.failed-' + uuid.uuid4().hex)
            self.data.rename(failed)
            self.state['preserved_failed_data'] = str(failed)
        recovered.rename(self.data)
        self.state['failed_code'] = pending['code']
        self.state['pending'] = None
        self.state['status'] = 'recovered_choose_previous_release'
        self.save()

    def prepare(self, migrate=None):
        """Run before opening the HTTP port. On errors keep the service stopped."""
        self.lease.__enter__()
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.directory.chmod(0o700)
            state_file = backup.safe_path(self.directory / 'installation.json')
            if state_file.exists():
                self.state = json.loads(state_file.read_text())
                if self.state.get('format') != 'blueprint-native-installation' or self.state.get('data') != str(self.data):
                    raise ValueError('備份位置已屬於另一個安裝，請沿用正確位置。')
            else:
                if any(self.directory.iterdir()):
                    raise ValueError('備份目錄不是空白，請核對原安裝紀錄。')
                self.state = {'format': 'blueprint-native-installation', 'version': 1,
                              'data': str(self.data), 'status': 'preparing', 'pending': None}
                self.save()
            if self.state.get('pending'):
                self._recover()
            if self.state.get('failed_code') == self.fingerprint:
                raise ValueError('這份程式曾更新失敗；資料已回復。請啟動之前版本或改用已修復的程式。')
            previous = self.state.get('product_version')
            if previous and tuple(map(int, self.version.split('.'))) < tuple(map(int, previous.split('.'))):
                raise ValueError('不能直接降低程式版本；請使用相符的舊程式及私人備份。')
            if not (self.data / 'access.json').is_file():
                raise ValueError('找不到原登入設定；不會建立空白資料代替。')
            changed = self.state.get('code') != self.fingerprint
            last = self.state.get('last_backup', {})
            snapshot = None
            if changed or time.time() - last.get('created_at', 0) >= self.interval:
                snapshot = self._snapshot('before-update' if previous else 'first-install')
            if migrate is None:
                from .store import Store
                migrate = Store
            if changed:
                rehearsal = self.directory / ('.migration-check-' + uuid.uuid4().hex)
                backup.restore(snapshot, rehearsal)
                try:
                    migrate(rehearsal)
                    backup.verify(rehearsal, snapshot)
                finally:
                    shutil.rmtree(rehearsal)
                self.state['pending'] = {'snapshot': snapshot.name, 'code': self.fingerprint}
                self.state['status'] = 'migrating'; self.save()
                try:
                    migrate(self.data)
                    backup.verify(self.data, snapshot)
                except Exception:
                    self._recover()
                    raise
            else:
                migrate(self.data)
            self.state.update(product_version=self.version, code=self.fingerprint,
                              pending=None, status='ready', failed_code=None)
            self.save()
            return self
        except Exception:
            self.lease.__exit__()
            raise

    def status(self):
        last = self.state.get('last_backup')
        return {'supported': True, 'status': self.state.get('status'),
                'product_version': self.state.get('product_version'),
                'backup_interval_seconds': self.interval, 'busy': self.busy or self.requested,
                'last_backup': last, 'error': self.state.get('backup_error'),
                'backup_copy_required': True,
                'off_host_backup_verified': False, 'ai_connection_verified': False}

    def request_backup(self):
        with self.mutex:
            if not self.busy:
                self.requested = True; self.wake.set()
        return {'status': 'backup_requested'}

    def start(self):
        def work():
            while not self.stop.is_set():
                self.wake.wait(1); self.wake.clear()
                if self.stop.is_set():
                    break
                due = time.time() - self.state.get('last_backup', {}).get('created_at', 0) >= self.interval
                if not (self.requested or due):
                    continue
                with self.mutex:
                    self.busy = True; self.requested = False
                try:
                    with self.gate.pause():
                        if not self.stop.is_set():
                            self._snapshot('requested' if not due else 'scheduled')
                except Exception:
                    self.state['backup_error'] = '備份未完成，請檢查私人備份位置及可用空間。'
                    try:
                        self.save()
                    except OSError:
                        pass
                    # Avoid a tight retry loop when a disk is full.
                    self.stop.wait(60)
                finally:
                    self.busy = False
        self.thread = threading.Thread(target=work, name='blueprint-backup', daemon=True)
        self.thread.start()

    def download(self, name):
        if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', name):
            raise ValueError('備份識別碼不正確。')
        directory = backup.safe_path(self.directory / name)
        receipt = json.loads(backup.safe_path(directory / 'receipt.json').read_text())
        archive = backup.safe_path(directory / 'backup.zip')
        if backup.digest(archive) != receipt.get('sha256'):
            raise ValueError('備份封包驗證失敗。')
        return archive

    def close(self):
        self.stop.set(); self.wake.set()
        if self.thread:
            self.thread.join()
        self.lease.__exit__()
