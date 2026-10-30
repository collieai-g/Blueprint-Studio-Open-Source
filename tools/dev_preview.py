#!/usr/bin/env python3
"""Start the current Blueprint source with an isolated, synthetic-data store."""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blueprint_folders.console import configure_utf8_streams
from blueprint_folders.path_safety import has_link
from blueprint_library.backup import external, safe_path
from blueprint_library.server import LibraryHandler, initialize_library, make_server
from blueprint_library.store import LibraryError

PREVIEW_BASE = Path(tempfile.gettempdir()) / 'blueprint-dev-previews'
USERNAME = 'preview'
MARKER = '.blueprint-dev-preview.json'
FORMAT = 'blueprint-dev-preview/1'
PREVIEW_TITLE = '藍圖工作室開源開發預覽'


def _safe_location(value):
    """Refuse links and Windows reparse points before resolving any location."""
    path = Path(value).expanduser()
    if has_link(path):
        raise ValueError('預覽路徑不能包含符號連結或重新解析點。')
    result = safe_path(path)
    external(result)
    return result


def _base():
    base = _safe_location(PREVIEW_BASE)
    # A temporary directory may be overridden by the caller. Never create a
    # preview inside any Git checkout or an existing Blueprint data directory.
    for parent in (base, *base.parents):
        if (parent / '.git').exists():
            raise ValueError('預覽位置不能在任何程式倉庫內。')
        if (parent / 'access.json').exists() and (parent / 'library.sqlite3').exists():
            raise ValueError('預覽位置不能在既有工作室資料目錄內。')
        if (parent / 'installation.json').exists():
            raise ValueError('預覽位置不能在既有工作室維護目錄內。')
    return base


def _marker(root):
    return {'format': FORMAT, 'root': str(root), 'source': str(ROOT),
            'data': str(root / 'data'), 'backups': str(root / 'backups'),
            'username': USERNAME}


def create_preview(password):
    """Make a new preview directory; never use or overwrite an existing store."""
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('預覽密碼需要 12 至 1024 個字元。')
    base = _base()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    _safe_location(base)
    root = _safe_location(Path(tempfile.mkdtemp(prefix='preview-', dir=base)))
    # initialize_library has its own empty-directory check and OS data lease.
    # If initialization fails, preserve the partial directory for inspection.
    initialize_library(root / 'data', USERNAME, password)
    marker = root / MARKER
    with marker.open('x', encoding='utf-8') as out:
        json.dump(_marker(root), out, ensure_ascii=False, indent=2)
        out.write('\n')
    try:
        root.chmod(0o700)
        marker.chmod(0o600)
    except OSError:
        pass
    return root


def _validate(root, *, resuming=False):
    base = _base()
    root = _safe_location(root)
    if root.parent != base or not root.name.startswith('preview-') or not root.is_dir():
        raise ValueError('請選擇本來源建立的預覽目錄。')
    marker = _safe_location(root / MARKER)
    try:
        value = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('預覽識別檔不存在或格式不正確；不會初始化資料。') from None
    if value != _marker(root):
        raise ValueError('預覽識別檔與來源或資料位置不一致。')
    data, backups = _safe_location(root / 'data'), _safe_location(root / 'backups')
    if data.parent != root or backups.parent != root or data == backups:
        raise ValueError('預覽資料與備份位置不正確。')
    for name in ('access.json', 'library.sqlite3'):
        if not _safe_location(data / name).is_file():
            raise ValueError('預覽資料不完整；不會重建或覆寫。')
    if resuming and not _safe_location(backups / 'installation.json').is_file():
        raise ValueError('找不到原預覽的維護紀錄；不會重建或覆寫。')
    return root, data, backups


class PreviewHandler(LibraryHandler):
    """Give this isolated server a distinct browser title, without changing UI source."""

    def _json(self, value, status=200, extra=None):
        if (self.command == 'GET' and urlsplit(self.path).path == '/api/info'
                and status == 200 and isinstance(value, dict)):
            value = {**value, 'product_title': PREVIEW_TITLE, 'development_preview_mode': 'source'}
        return super()._json(value, status=status, extra=extra)


def start_preview(root, *, resuming=False):
    """Open only a verified preview, using the normal managed native backend."""
    _, data, backups = _validate(root, resuming=resuming)
    from blueprint_library.installer_preview import requires_managed_resume
    if requires_managed_resume(root):
        raise ValueError('此預覽已有安裝器接駁或未完成紀錄，請用同一 --resume 接續，不能退回即時來源模式。')
    # Explicit backups prevent inheriting BLUEPRINT_BACKUP_DIR from another app.
    server = make_server(data, host='127.0.0.1', port=0, managed=True, backup_dir=backups)
    server.RequestHandlerClass = PreviewHandler
    return server


def _clean_environment():
    # Preview state is synthetic. Do not inherit real workbench bindings,
    # credentials, provider keys or backup configuration into its server.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'COMSPEC',
               'TEMP', 'TMP', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
               'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ'}
    for key in list(os.environ):
        if key.upper() not in allowed:
            del os.environ[key]


def _dialog_password():
    """Ask locally for a new preview password without putting it in argv."""
    try:
        import tkinter as tk
        from tkinter import simpledialog
    except ImportError:
        raise ValueError('此 Python 沒有視窗輸入功能，請改用終端機輸入。') from None
    window = None
    try:
        window = tk.Tk()
        window.withdraw()
        first = simpledialog.askstring('藍圖開發預覽', '設定預覽專用密碼（至少 12 字元）：', show='*', parent=window)
        if first is None:
            raise ValueError('已取消設定密碼，未有建立預覽。')
        second = simpledialog.askstring('藍圖開發預覽', '再次輸入相同密碼：', show='*', parent=window)
        if second is None:
            raise ValueError('已取消設定密碼，未有建立預覽。')
        if first != second:
            raise ValueError('兩次密碼不一致，未有建立預覽。')
        return first
    except tk.TclError:
        raise ValueError('未能開啟密碼輸入視窗，請改用終端機輸入。') from None
    finally:
        if window is not None:
            window.destroy()


def main(argv=None):
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='以獨立測試資料預覽目前藍圖來源')
    parser.add_argument('--resume', metavar='預覽目錄', help='重新開啟先前建立的預覽，保留其測試資料')
    parser.add_argument('--password-dialog', action='store_true', help='使用可貼上的密碼輸入視窗')
    parser.add_argument('--enable-installer', action='store_true', help='把原預覽接入固定測試候選安裝器')
    parser.add_argument('--release', help='已簽章的本機或 HTTPS 核心候選描述')
    parser.add_argument('--trusted-keys', help='明確選定的可信公鑰 JSON')
    parser.add_argument('--feed', help='固定的已簽章測試渠道目錄')
    args = parser.parse_args(argv)
    _clean_environment()
    try:
        setup = (args.release, args.trusted_keys, args.feed)
        if args.enable_installer and (not args.resume or not all(setup)):
            raise ValueError('接入安裝器須使用 --resume、--release、--trusted-keys 及 --feed。')
        if not args.enable_installer and any(setup):
            raise ValueError('候選及渠道參數只用於明確的 --enable-installer 接入。')
        if args.resume:
            root = _safe_location(args.resume)
            _validate(root, resuming=True)
            from blueprint_library.installer_preview import (enable_preview_installer,
                requires_managed_resume, run_managed_preview)
            if args.enable_installer:
                from blueprint_library.installer import read_feed
                enable_preview_installer(root, ROOT, read_feed(args.release),
                    json.loads(Path(args.trusted_keys).read_text(encoding='utf-8')), feed=args.feed)
            if requires_managed_resume(root):
                def announce(value):
                    print(PREVIEW_TITLE + '（安裝器固定候選；獨立測試資料；不連接客服 Agent）', flush=True)
                    print('維護來源：' + value['source'], flush=True)
                    print('實際候選目錄：' + value['package_path'], flush=True)
                    print('已載入版本：' + value['version'], flush=True)
                    print('預覽網址：' + value['url'], flush=True)
                    print('帳號：' + USERNAME + '；使用原預覽專用密碼登入。', flush=True)
                    print('預覽目錄：' + str(root), flush=True)
                    print('此模式使用固定產物；修改來源後須建立並準備新候選。', flush=True)
                    print('按 Ctrl+C 正常停止；重新開啟仍使用同一 --resume 目錄。', flush=True)
                return run_managed_preview(root, source=ROOT, announce=announce)
        else:
            if args.password_dialog:
                password = _dialog_password()
            else:
                if not sys.stdin.isatty():
                    raise ValueError('請在可安全輸入密碼的終端機啟動預覽。')
                password = getpass.getpass('設定此預覽專用密碼（最少 12 字元）：')
                if password != getpass.getpass('再次輸入預覽密碼：'):
                    raise ValueError('兩次密碼不一致，未有建立預覽。')
            root = create_preview(password)
            del password
        server = start_preview(root, resuming=bool(args.resume))
    except (OSError, ValueError, LibraryError) as error:
        print('未能開啟預覽：' + str(error), file=sys.stderr)
        return 1
    url = f'http://127.0.0.1:{server.server_port}/'
    print(PREVIEW_TITLE + '（獨立測試資料；不連接客服 Agent）', flush=True)
    print('來源目錄：' + str(ROOT), flush=True)
    print('預覽網址：' + url, flush=True)
    print('帳號：' + USERNAME + '；使用剛設定或原預覽的專用密碼登入。', flush=True)
    print('預覽目錄：' + str(root), flush=True)
    try:
        ZoneInfo('Asia/Hong_Kong')
    except ZoneInfoNotFoundError:
        print('提示：目前 Python 缺少時區資料，月曆功能可能不能使用；請參閱開發指南。', flush=True)
    print('請在私人瀏覽視窗開啟網址，避免沿用其他網站的本機偏好。', flush=True)
    print('按 Ctrl+C 停止；資料不會自動刪除。重新開啟時使用 --resume 預覽目錄。', flush=True)
    previous_term = None
    if threading.current_thread() is threading.main_thread():
        previous_term = signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if previous_term is not None:
            signal.signal(signal.SIGTERM, previous_term)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
