"""Resume one marked development preview through the real managed installer.

Private bindings live beside the preview data. No credential is supplied by this
bridge, no new data store is initialized, and packages remain immutable.
"""
from __future__ import annotations

from blueprint_folders.path_safety import has_link

import json
from pathlib import Path
import signal
import socket
import threading
import time

from . import backup
from .maintenance import DataLease, atomic_json

PREVIEW_TITLE = '藍圖工作室開源開發預覽'
PREVIEW_MARKER = '.blueprint-dev-preview.json'
PREVIEW_FORMAT = 'blueprint-dev-preview/1'
MANAGED_MARKER = '.blueprint-managed-preview.json'
MANAGED_FORMAT = 'blueprint-managed-preview/1'


def _path(value, *, private=True):
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError('預覽位置需要明確的路徑。')
    path = Path(value).expanduser()
    if has_link(path):
        raise ValueError('預覽位置不能包含符號連結或重新解析點。')
    for part in (path.absolute(), *path.absolute().parents):
        if private and (part / '.git').exists():
            raise ValueError('預覽私人位置不能在程式倉庫內。')
    path = backup.safe_path(path)
    if private and (path == backup.SOURCE or backup.SOURCE in path.parents):
        raise ValueError('預覽私人位置不能在運行程式內。')
    return path


def _read(path):
    path = _path(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('預覽識別紀錄過大。')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('預覽識別紀錄格式不正確。')
    return value


def _original(root, source):
    root, source = _path(root), _path(source, private=False)
    if not root.is_dir() or not root.name.startswith('preview-') or not (source / 'tools/dev_preview.py').is_file():
        raise ValueError('找不到原開發預覽或來源，不會建立新預覽。')
    paths = {'root': root, 'source': source, 'program': root / 'managed-installer',
             'data': root / 'data', 'backups': root / 'backups', 'recovery': root / 'installer-recovery'}
    marker = {'format': PREVIEW_FORMAT, 'root': str(root), 'source': str(source),
              'data': str(paths['data']), 'backups': str(paths['backups']), 'username': 'preview'}
    if _read(root / PREVIEW_MARKER) != marker:
        raise ValueError('原預覽識別與來源或資料位置不符。')
    for key in ('data', 'backups', 'program', 'recovery'):
        _path(paths[key])
    # The preview/program namespaces contain releases, but data domains must
    # remain outside the running package even when validation runs in a child.
    for key in ('data', 'backups', 'recovery'):
        backup.external(paths[key])
    for name in ('library.sqlite3', 'access.json'):
        if not _path(paths['data'] / name).is_file():
            raise ValueError('原預覽資料或登入憑證遺失，不會重新初始化。')
    if _read(paths['data'] / 'access.json').get('username') != 'preview':
        raise ValueError('原預覽帳戶身份不符，不會變更登入。')
    native = _read(paths['backups'] / 'installation.json')
    if (native.get('format') != 'blueprint-native-installation' or
            native.get('data') != str(paths['data']) or not native.get('service_id') or native.get('pending')):
        raise ValueError('原預覽維護身份不符或仍有未完成遷移。')
    return {key: str(value) for key, value in paths.items()}


def validate_preview_binding(config):
    """Validate optional private preview identity without exposing credentials."""
    if 'development_preview' not in config:
        return None
    value = config['development_preview']
    if not isinstance(value, dict) or set(value) != {'root', 'source'}:
        raise ValueError('開發預覽接駁設定不正確。')
    paths = _original(value['root'], value['source'])
    expected = {'root': 'program', 'data_dir': 'data', 'backup_dir': 'backups', 'recovery_dir': 'recovery'}
    if any(str(_path(config[key])) != paths[name] for key, name in expected.items()):
        raise ValueError('安裝器不能把開發預覽轉向其他工作室。')
    modules = config.get('modules', {})
    if config.get('host') != '127.0.0.1' or not isinstance(modules, dict) or modules:
        raise ValueError('開發預覽只支援獨立 loopback 核心，不接入客服或其他工作室。')
    return paths


def has_managed_preview(root):
    return _path(Path(root) / MANAGED_MARKER).exists()


def requires_managed_resume(root):
    """A lost sidecar cannot bypass an existing or partial installer claim."""
    root = _path(root)
    return (has_managed_preview(root) or _path(root / 'managed-installer').exists() or
            _path(root / '.data.blueprint-installer.json').exists())


def managed_preview_config(root, source):
    """Read an existing sidecar; a damaged binding never falls back to source mode."""
    from .installer import _config
    paths = _original(root, source)
    sidecar = _read(Path(paths['root']) / MANAGED_MARKER)
    expected = {'format': MANAGED_FORMAT, 'root': paths['root'], 'source': paths['source'],
                'program': paths['program'], 'initial_release_id': sidecar.get('initial_release_id')}
    if (sidecar != expected or not isinstance(sidecar['initial_release_id'], str) or
            len(sidecar['initial_release_id']) != 64 or any(x not in '0123456789abcdef' for x in sidecar['initial_release_id'])):
        raise ValueError('受管預覽識別不正確，不會另開空白預覽。')
    config = _config(paths['program'])
    if validate_preview_binding(config) != paths:
        raise ValueError('受管預覽身份未能核對。')
    return config


def enable_preview_installer(root, source, envelope, trusted_keys, *, feed, channel='test'):
    """Adopt an existing stopped preview and its native protection identity."""
    from .installer import (_config, _lock, _state, _save, initialize_installation,
                            create_checkpoint, read_status)
    from .installer_release import release_id, verify_release
    if channel != 'test' or not feed:
        raise ValueError('開發預覽須明確使用測試發行渠道。')
    paths = _original(root, source)
    manifest = verify_release(envelope, trusted_keys)
    initial_id = release_id(manifest)
    preview_root, program = Path(paths['root']), Path(paths['program'])
    with _lock(preview_root, '.preview-launch.lock', wait=False):
        if has_managed_preview(preview_root):
            config = managed_preview_config(preview_root, source)
            sidecar = _read(preview_root / MANAGED_MARKER)
            if (sidecar['initial_release_id'] != initial_id or config['trusted_keys'] != trusted_keys or
                    config.get('feed') != str(feed) or config.get('channel') != channel):
                raise ValueError('此預覽已接入另一個固定計劃，請沿用 --resume。')
            return read_status(program)
        if program.exists() and any(program.iterdir()):
            # A known completed initialization can finish its missing protection
            # and sidecar; an incomplete/unknown directory is never erased.
            config = _config(program)
            state = _state(program)
            if 'development_preview' in config and validate_preview_binding(config) != paths:
                raise ValueError('原受管預覽接駁身份不同，未改寫。')
            if (state['phase'] != 'installed' or state.get('child') or
                    state['installed']['release_id'] != initial_id or config.get('modules') or
                    config['data_dir'] != paths['data'] or config['backup_dir'] != paths['backups'] or
                    config['recovery_dir'] != paths['recovery'] or config['trusted_keys'] != trusted_keys or
                    config.get('feed') != str(feed) or config.get('channel') != channel):
                raise ValueError('原接入操作結果未知，保留原紀錄供核對。')
        else:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
            initialize_installation(program, envelope, trusted_keys, data_dir=paths['data'],
                backup_dir=paths['backups'], recovery_dir=paths['recovery'], username='preview',
                existing_data=True, existing_backups=True, host='127.0.0.1', port=port,
                feed=feed, channel=channel)
            config = _config(program)
        with _lock(program, '.supervisor.lock', wait=False):
            config['development_preview'] = {'root': paths['root'], 'source': paths['source']}
            validate_preview_binding(config)
            atomic_json(program / 'installation.json', config)
            with DataLease(paths['data']):
                protected = create_checkpoint(config, 'preview-adoption-' + config['installation_id'])
            with _lock(program, '.state.lock'):
                _save(program, _state(program), checkpoint=protected)
            atomic_json(preview_root / MANAGED_MARKER, {'format': MANAGED_FORMAT,
                'root': paths['root'], 'source': paths['source'], 'program': paths['program'],
                'initial_release_id': initial_id})
        return read_status(program)


def run_managed_preview(root, *, source, announce=None, stop_event=None, startup_timeout=120):
    """Run the real supervisor and announce only an identity-verified ready child."""
    from .installer import Supervisor, _config, _lock, _state
    paths = _original(root, source)
    program = Path(paths['program'])
    with _lock(Path(paths['root']), '.preview-launch.lock', wait=False):
        config = managed_preview_config(root, source)
        with _lock(program, '.supervisor.lock', wait=False):
            state = _state(program)
            if not state.get('child') and state['phase'] not in {'apply_requested', 'applying', 'recovery_required'}:
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', 0)); config['port'] = probe.getsockname()[1]
                atomic_json(program / 'installation.json', config)
        supervisor = Supervisor(program, startup_timeout=startup_timeout)
        failures = []
        def worker():
            try:
                supervisor.run()
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=worker, name='blueprint-preview-installer', daemon=True)
        previous = None
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, lambda *_: supervisor.stop())
        thread.start()
        announced = False
        deadline = time.monotonic() + startup_timeout
        try:
            while thread.is_alive():
                if stop_event is not None and stop_event.is_set():
                    supervisor.stop()
                if not announced:
                    state = supervisor._get()
                    child = state.get('child')
                    if child and type(child.get('pid')) is int:
                        try:
                            actual = supervisor._internal(child)
                            active = state['installed']
                            if (actual.get('ready') is True and actual.get('development_preview') is True and
                                    actual.get('product_title') == PREVIEW_TITLE and
                                    actual.get('version') == active['version'] and
                                    actual.get('database_schema') == active['manifest']['database_schema']):
                                announced = True
                                if announce:
                                    announce({'url': f'http://127.0.0.1:{supervisor.config["port"]}/',
                                        'pid': actual['pid'], 'release_id': child['release_id'],
                                        'version': actual['version'], 'source': paths['source'],
                                        'root': paths['root'], 'package_path': active['path']})
                        except (OSError, ValueError):
                            pass
                    if not announced and time.monotonic() >= deadline:
                        raise ValueError('受管預覽身份或啟動尚未確認，未宣稱可使用。')
                thread.join(.2)
        except KeyboardInterrupt:
            supervisor.stop()
        finally:
            supervisor.stop()
            thread.join(startup_timeout + supervisor.stop_timeout + 5)
            if previous is not None:
                signal.signal(signal.SIGTERM, previous)
        if thread.is_alive():
            raise ValueError('受管預覽尚未確認停止，已保留原程序身份及紀錄。')
        if failures:
            raise ValueError('受管預覽未完成；請核對原安裝紀錄及私人服務日誌。') from failures[0]
        if not announced:
            raise ValueError('受管預覽未完成啟動。')
    return 0
