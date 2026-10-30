"""Authenticated UI control and a per-boot supervisor handshake.

The browser can select only an already prepared release for its own installation.
It cannot supply paths, executable commands, download URLs or another studio ID.
"""
from __future__ import annotations

from blueprint_folders.path_safety import has_link

import hmac
import ipaddress
import json
import os
from pathlib import Path
import threading
from urllib.parse import quote

from .store import LibraryError, SCHEMA_VERSION
from blueprint_folders import __version__


def development_preview_title(data_dir, username):
    """Recognize only a validated private preview binding, before opening a socket."""
    root = os.environ.get('BLUEPRINT_INSTALLATION_ROOT')
    if not root:
        return None
    try:
        value = json.loads((Path(root) / 'installation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None  # Ordinary installation control retains its existing checks.
    if 'development_preview' not in value:
        return None
    from .installer import _config
    from .installer_preview import PREVIEW_TITLE, validate_preview_binding
    value = _config(root)
    if Path(value['data_dir']).resolve() != Path(data_dir).resolve() or username != 'preview':
        raise ValueError('受管預覽與原資料或帳號不符。')
    if not validate_preview_binding(value):
        raise ValueError('受管預覽識別未能確認。')
    return PREVIEW_TITLE


def configure(server):
    server.installer_root = os.environ.get('BLUEPRINT_INSTALLATION_ROOT')
    server.installer_token = os.environ.get('BLUEPRINT_INSTALLER_TOKEN', '')
    server.installer_release_id = os.environ.get('BLUEPRINT_RELEASE_ID', '')
    server.installer_stopping = False
    server.store.agent_update_lock = threading.RLock()
    server.store.agent_updates_paused = set()
    if server.installer_root:
        try:
            root = Path(server.installer_root)
            config = json.loads((root / 'installation.json').read_text(encoding='utf-8'))
            state = json.loads((root / 'state.json').read_text(encoding='utf-8'))
            if state.get('phase') in {'applying', 'recovery_required'}:
                server.store.agent_updates_paused.update(value['expected_agent_id'] for value in config.get('modules', {}).values())
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Normal configuration validation still fails closed at the control route.


def internal(handler, method, path):
    if path != '/api/installer/internal':
        return False
    server = handler.server
    token = getattr(server, 'installer_token', '')
    provided = handler.headers.get('X-Blueprint-Installer', '')
    try:
        local = ipaddress.ip_address(handler.client_address[0]).is_loopback
    except ValueError:
        local = False
    if (not local or len(token) < 32 or len(provided) > 256 or
            not hmac.compare_digest(token.encode(), provided.encode()) or
            handler.headers.get('Origin') is not None):
        raise LibraryError('找不到此操作。', 404)
    if method == 'GET':
        maintenance = server.maintenance
        handler._json({'release_id': server.installer_release_id,
                       'version': __version__, 'database_schema': SCHEMA_VERSION,
                       'pid': os.getpid(),
                       'development_preview': bool(getattr(server, 'development_preview_title', None)),
                       'product_title': getattr(server, 'development_preview_title', None),
                       'ready': bool(maintenance and maintenance.state.get('status') == 'ready')})
    elif method == 'POST':
        if handler.headers.get('Content-Type', '').split(';', 1)[0] != 'application/json':
            raise LibraryError('API 寫入需要 application/json。', 415)
        body = handler._body()
        if body.get('action') in {'agent-status', 'agent-upgrade', 'agent-quiesce', 'agent-resume'}:
            root = _bound_root(server)
            if root is None:
                raise LibraryError('未綁定受管工作室。', 403)
            config = json.loads((root / 'installation.json').read_text(encoding='utf-8'))
            bindings = {value['expected_agent_id'] for value in config.get('modules', {}).values()}
            if body.get('agent_id') not in bindings:
                raise LibraryError('此 Agent 未綁定受管模組。', 403)
            from .agents import Agents
            identity = {'username': server.credential['username'], 'role': 'owner'}
            agents = Agents(server.store, identity)
            if body['action'] in {'agent-quiesce', 'agent-resume'}:
                if set(body) != {'action', 'agent_id'}:
                    raise LibraryError('Agent 控制參數不正確。')
                with server.store.agent_update_lock:
                    if body['action'] == 'agent-quiesce':
                        server.store.agent_updates_paused.add(body['agent_id'])
                    else:
                        server.store.agent_updates_paused.discard(body['agent_id'])
            with server.store._connection() as conn:
                busy = bool(conn.execute("SELECT 1 FROM agent_jobs WHERE agent_id=? AND state NOT IN ('completed','failed','cancelled') LIMIT 1", (body['agent_id'],)).fetchone())
            if body['action'] == 'agent-upgrade':
                if (set(body) != {'action', 'agent_id', 'manifest', 'source', 'upgrade_key'} or busy or
                        body['agent_id'] not in server.store.agent_updates_paused):
                    raise LibraryError('原 Agent 仍有未完成工作，未套用套件。', 409)
                original = agents.get_agent(body['agent_id'])
                agents.update_agent(body['agent_id'], {'expected_version': original['version'],
                    'manifest': body['manifest'], 'source': body['source'], 'upgrade_key': body['upgrade_key']})
            elif set(body) != {'action', 'agent_id'}:
                raise LibraryError('Agent 查詢參數不正確。')
            handler._json({'agent': agents.get_agent(body['agent_id']), 'busy': busy})
            return True
        if body != {'action': 'shutdown'}:
            raise LibraryError('不支援此安裝操作。')
        server.installer_stopping = True
        handler._json({'status': 'stopping'}, 202)
        threading.Thread(target=server.shutdown, daemon=True).start()
    else:
        raise LibraryError('不支援此安裝操作。', 405)
    return True


def _bound_root(server):
    root = getattr(server, 'installer_root', None)
    if not root:
        return None
    directory = Path(root).expanduser()
    if has_link(directory):
        raise LibraryError('安裝設定需要修復。', 503)
    directory = directory.resolve()
    config_file = directory / 'installation.json'
    if has_link(config_file):
        raise LibraryError('安裝設定需要修復。', 503)
    try:
        config = json.loads(config_file.read_text(encoding='utf-8'))
        if has_link(config['data_dir']) or Path(config['data_dir']).resolve() != server.store.data_dir:
            raise ValueError('different installation')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise LibraryError('安裝設定與目前工作室不符；未執行更新。', 503) from exc
    return directory


def handle(handler, method, path):
    if path not in {'/api/installer', '/api/installer/check', '/api/installer/apply'}:
        return False
    role = handler.identity.get('role')
    if role not in {'owner', 'admin'} or (method != 'GET' and role != 'owner'):
        raise LibraryError('只有工作室擁有人可以套用安裝更新。', 403)
    expected = quote(handler.identity['username'], safe="~()*!.'")
    account = handler.headers.get('X-Blueprint-Account')
    if (account is not None and account != expected) or (method != 'GET' and account is None):
        raise LibraryError('登入帳戶已改變；請重新開啟工作室。', 409)
    root = _bound_root(handler.server)
    if root is None:
        if method != 'GET':
            raise LibraryError('此工作室尚未接入受管安裝器。', 503)
        handler._json({'supported': False})
        return True
    from .installer import read_status, request_apply, request_check
    if method == 'GET' and path == '/api/installer':
        handler._json(read_status(root))
    elif method == 'POST' and path == '/api/installer/check':
        if handler._body() != {}:
            raise LibraryError('更新檢查不接受其他目標或參數。')
        handler._json(request_check(root), 202)
    elif method == 'POST' and path == '/api/installer/apply':
        body = handler._body()
        if set(body) != {'release_id', 'expected_version', 'drafts_saved'} or body.get('drafts_saved') is not True:
            raise LibraryError('請先保存工作並關閉其他工作分頁，再套用更新。', 409)
        handler._json(request_apply(root, body['release_id'], body['expected_version']), 202)
    else:
        raise LibraryError('不支援此安裝操作。', 405)
    return True
