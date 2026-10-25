"""Native lifecycle: exclusive ownership, verified backups, migration and recovery.

No network, deployment credentials or shell execution. The host selects a checked
release; this module protects its existing data before the server accepts traffic.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import copy
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
from .store import LibraryError

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
        self.mutex = threading.RLock()

    def save(self):
        with self.mutex:
            atomic_json(self.directory / 'installation.json', self.state)

    def _space(self):
        size = sum(p.stat().st_size for p in self.data.rglob('*') if p.is_file())
        if shutil.disk_usage(self.directory).free < size * 4 + 16 * 1024 * 1024:
            raise ValueError('備份與還原演練的儲存空間不足；未繼續更新。')

    def _snapshot(self, reason, name=None):
        self._space()
        name = name or self._backup_id()
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
        coverage = backup.coverage(target / 'data')
        coverage['scope'] = self.state['service_id']
        receipt = {'id': name, 'created_at': time.time(), 'reason': reason,
                   'restore_verified': True, 'platform_verified': True,
                   'size': archive.stat().st_size, 'coverage': coverage,
                   'sha256': backup.digest(archive)}
        atomic_json(target / 'receipt.json', receipt)
        with self.mutex:
            self.state['last_backup'] = receipt
            self.state['backup_error'] = None
            self.save()
        return target

    @staticmethod
    def _backup_id():
        return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:12]

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
            self.state.setdefault('service_id', uuid.uuid4().hex)
            self.state.setdefault('backup_requests', {})
            self.state.setdefault('client_copies', {})
            # A known completed archive is read back after interruption. A partial
            # archive remains unknown; never silently create another backup ID.
            for request in self.state['backup_requests'].values():
                if request.get('status') == 'running':
                    try:
                        request['backup'] = self.backup_info(request['backup_id'])
                        request['status'] = 'complete'
                        if request['backup'].get('created_at', 0) > self.state.get('last_backup', {}).get('created_at', 0):
                            self.state['last_backup'] = request['backup']
                    except (OSError, ValueError, KeyError):
                        request['status'] = 'unknown'
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
                # Reuse only a complete, current, retained copy with a private
                # readback attestation by this installation's owner. Recheck now,
                # before opening HTTP or migrating, rather than trusting an old UI.
                if changed and last.get('id'):
                    try:
                        actor = json.loads((self.data / 'access.json').read_text())['username']
                        info = self.backup_info(last['id'], actor=actor)
                        if self._protection(info, self._coverage(), actor)['reusable']:
                            snapshot = self.directory / last['id']
                    except (OSError, ValueError, KeyError):
                        pass
                if snapshot is None:
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

    def _coverage(self):
        value = backup.coverage(self.data)
        value['scope'] = self.state['service_id']
        return value

    def backup_info(self, name, actor=None):
        archive = self.download(name)
        backup.check(archive.parent)
        receipt = json.loads((archive.parent / 'receipt.json').read_text())
        result = {**receipt, 'size': archive.stat().st_size, 'platform_verified': True,
                  'download_path': '/api/maintenance/backups/' + name}
        with self.mutex:
            private = self.state.get('client_copies', {}).get(name, {}).get(actor)
            if private:
                result['client_copy'] = copy.deepcopy(private)
        return result

    def _protection(self, last, coverage, actor):
        platform = bool(last and last.get('platform_verified'))
        restored = bool(platform and last.get('restore_verified'))
        matches = bool(last and last.get('coverage') == coverage)
        retained = bool(last and 0 <= time.time() - last.get('created_at', 0) < self.interval)
        receipt = last.get('client_copy') if last else None
        private = bool(receipt and receipt.get('readback_verified') is True
                       and receipt.get('sha256') == last.get('sha256')
                       and receipt.get('size') == last.get('size')
                       and 0 <= time.time() - receipt.get('recorded_at', 0) < self.interval)
        if not platform or not restored:
            state, missing = 'pending', 'create_verified_backup'
        elif not matches:
            state, missing = 'platform_verified', 'backup_current_data'
        elif not retained:
            state, missing = 'platform_verified', 'renew_retention_protection'
        elif not private:
            state, missing = 'private_copy_pending', 'verify_private_copy'
        else:
            state, missing = 'reusable', None
        return {'state': state, 'next_missing': missing, 'reusable': state == 'reusable',
                'platform_verified': platform, 'restore_verified': restored,
                'client_copy_verified': private, 'client_copy_evidence': 'client_attestation' if private else None,
                'current_coverage_matches': matches, 'retention_valid': retained}

    def status(self, actor=None, *, verify_current=True):
        with self.mutex:
            last = copy.deepcopy(self.state.get('last_backup'))
            status = self.state.get('status')
            version = self.state.get('product_version')
            busy = self.busy or self.requested or any(
                item.get('status') == 'queued' for item in self.state.get('backup_requests', {}).values())
            error = self.state.get('backup_error')
            if last:
                private = self.state.get('client_copies', {}).get(last['id'], {}).get(actor)
                if private:
                    last['client_copy'] = copy.deepcopy(private)
        coverage = self._coverage() if verify_current else None
        if last and verify_current:
            try:
                last = self.backup_info(last['id'], actor=actor)
            except (OSError, ValueError, KeyError):
                last['platform_verified'] = False
        protection = self._protection(last, coverage, actor)
        if not verify_current:
            protection.update(state='platform_verified' if last else 'pending',
                              next_missing='verify_current_state', reusable=False,
                              current_coverage_matches=None)
        return {'supported': True, 'maintenance_contract': 'blueprint-maintenance/2',
                'status': status, 'product_version': version,
                'backup_interval_seconds': self.interval, 'busy': bool(busy),
                'coverage': coverage, 'verification_current': verify_current,
                'last_backup': last, 'error': error,
                'protection': protection,
                'backup_copy_required': not protection['client_copy_verified'],
                # A browser/client declaration does not establish off-host durability.
                'off_host_backup_verified': False, 'ai_connection_verified': False}

    def record_client_receipt(self, name, body, *, actor):
        if (not isinstance(actor, str) or not actor or len(actor) > 80
                or re.search(r'[\x00-\x1f\x7f]', actor)):
            raise LibraryError('私人副本缺少有效登入身份。', 403)
        if not isinstance(body, dict) or set(body) != {'file_reference', 'size', 'sha256', 'readback_verified'}:
            raise LibraryError('私人副本核對格式不正確。')
        reference = body['file_reference']
        if (not isinstance(reference, str) or not reference.strip() or len(reference) > 1000
                or re.search(r'[\x00-\x1f\x7f]', reference)
                or re.search(r'(?i)(https?://|bearer\s|[?&](token|key|secret)=)', reference)):
            raise LibraryError('請使用私人檔案引用，不要填入公開連結或登入憑證。')
        if (type(body['size']) is not int or body['size'] <= 0
                or not isinstance(body['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', body['sha256'])
                or body['readback_verified'] is not True):
            raise LibraryError('請先讀取實際私人檔案並核對大小與 SHA256。')
        info = self.backup_info(name)
        if body['size'] != info['size'] or body['sha256'] != info['sha256']:
            raise LibraryError('私人副本與同一備份 ID 的大小或 SHA256 不符。', 409)
        with self.mutex:
            copies = self.state.setdefault('client_copies', {}).setdefault(name, {})
            previous = copies.get(actor)
            if (previous and all(previous.get(key) == value for key, value in body.items())
                    and 0 <= time.time() - previous.get('recorded_at', 0) < self.interval):
                return copy.deepcopy(previous)
            receipt = {**body, 'actor': actor, 'backup_id': name,
                       'evidence': 'client_attestation', 'recorded_at': time.time()}
            copies[actor] = receipt
            self.save()
            return copy.deepcopy(receipt)

    def request_backup(self, operation_id=None, plan_digest=None):
        if operation_id is not None or plan_digest is not None:
            if (not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', operation_id)
                    or not isinstance(plan_digest, str) or not re.fullmatch('[0-9a-f]{64}', plan_digest)):
                raise LibraryError('備份工作識別碼或計劃指紋不正確。')
            with self.mutex:
                requests = self.state.setdefault('backup_requests', {})
                prior = requests.get(operation_id)
                if prior:
                    if prior['plan_digest'] != plan_digest:
                        raise LibraryError('同一工作識別碼已使用另一份計劃，請先讀回原紀錄。', 409)
                    if prior.get('status') == 'unknown':
                        try:
                            prior.update(status='complete', backup=self.backup_info(prior['backup_id']))
                            self.save()
                        except (OSError, ValueError, KeyError):
                            pass
                    return copy.deepcopy(prior)
                if len(requests) >= 1000:
                    raise LibraryError('備份工作紀錄已達上限，請先由擁有人整理私人保留政策。', 409)
                request = {'status': 'queued', 'operation_id': operation_id,
                           'plan_digest': plan_digest, 'backup_id': self._backup_id()}
                requests[operation_id] = request
                self.save(); self.wake.set()
                return copy.deepcopy(request)
        with self.mutex:
            if not self.busy:
                self.requested = True; self.wake.set()
        return {'status': 'backup_requested'}

    def read_backup_request(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', operation_id):
            raise LibraryError('備份工作識別碼不正確。')
        with self.mutex:
            request = self.state.get('backup_requests', {}).get(operation_id)
            if request is None:
                raise LibraryError('找不到此備份工作，請核對原識別碼。', 404)
            return copy.deepcopy(request)

    def start(self):
        def work():
            while not self.stop.is_set():
                self.wake.wait(1); self.wake.clear()
                if self.stop.is_set():
                    break
                with self.mutex:
                    queued = next((item for item in self.state.get('backup_requests', {}).values()
                                   if item.get('status') == 'queued'), None)
                    due = time.time() - self.state.get('last_backup', {}).get('created_at', 0) >= self.interval
                    if not (self.requested or due or queued):
                        continue
                    self.busy = True; self.requested = False
                    if queued:
                        queued['status'] = 'running'; self.save()
                try:
                    with self.gate.pause():
                        if not self.stop.is_set():
                            saved = self._snapshot('requested' if queued or not due else 'scheduled',
                                                   queued['backup_id'] if queued else None)
                            if queued:
                                info = self.backup_info(saved.name)
                                with self.mutex:
                                    queued.update(status='complete', backup=info); self.save()
                except Exception:
                    with self.mutex:
                        self.state['backup_error'] = '備份未完成，請檢查私人備份位置及可用空間。'
                        if queued:
                            queued['status'] = 'unknown'
                        try:
                            self.save()
                        except OSError:
                            pass
                    # Avoid a tight retry loop when a disk is full.
                    self.stop.wait(60)
                finally:
                    with self.mutex:
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
