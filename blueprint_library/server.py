"""Authenticated, same-origin library API; company data never enters source Git."""

from __future__ import annotations

import base64
import binascii
from datetime import date as calendar_date
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, quote, urlsplit
import webbrowser

from blueprint_folders import __version__
from blueprint_folders.core import CATEGORIES, _atomic_json
from .classify import classify_item
from .context import build_context
from .store import LibraryError, Store

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "library_web"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 512 * 1024
PASSWORD_ROUNDS = 310_000


def initialize_library(data_dir, username: str, password: str, company_profile=None) -> dict:
    """Create a one-owner library. Existing credentials are never overwritten."""
    if (not isinstance(username, str) or not username.strip() or len(username) > 80
            or re.search(r"[:\x00-\x1f\x7f]", username)):
        raise LibraryError("管理員名稱需為 1–80 字，不能包含冒號或控制字元。")
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise LibraryError("管理員密碼需要 12–1,024 個字元。")
    if company_profile is not None:
        from .company import _fields
        _fields(company_profile, versioned=False)
    store = Store(data_dir)
    directory = Path(data_dir).expanduser().resolve()
    credential_path = directory / "access.json"
    if credential_path.exists() or credential_path.is_symlink():
        raise LibraryError("此資料庫已有登入設定，未有覆寫。", status=409)
    salt = secrets.token_hex(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ROUNDS).hex()
    _atomic_json(credential_path, {"username": username.strip(), "salt": salt,
                                  "password_hash": digest, "rounds": PASSWORD_ROUNDS})
    try:
        credential_path.chmod(0o600)
        directory.chmod(0o700)
    except OSError:
        pass  # ACL-based filesystems can enforce privacy differently.
    if company_profile is not None:
        store.bootstrap_company_profile(company_profile)
    return {"data_path": str(directory), "username": username.strip(), "version": __version__}


class LibraryHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store: Store, credential: dict):
        self.store = store
        self.credential = credential
        self.failures: dict[str, tuple[int, float]] = {}
        self.auth_lock = threading.Lock()
        super().__init__(address, LibraryHandler)


def make_server(data_dir, host="127.0.0.1", port=0) -> LibraryHTTPServer:
    store = Store(data_dir)
    path = Path(data_dir).expanduser().resolve() / "access.json"
    try:
        if path.is_symlink():
            raise ValueError("登入設定不能為符號連結。")
        credential = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(credential, dict) or not isinstance(credential.get("username"), str)
                or credential.get("rounds") != PASSWORD_ROUNDS
                or not isinstance(credential.get("salt"), str)
                or not re.fullmatch(r"[0-9a-f]{48}", credential["salt"])
                or not isinstance(credential.get("password_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", credential["password_hash"])):
            raise ValueError("登入設定格式不正確。")
    except (OSError, ValueError) as exc:
        raise LibraryError("請先使用 library-init 設定管理員，或修復外部資料目錄內的 access.json。") from exc
    return LibraryHTTPServer((host, port), store, credential)


class LibraryHandler(BaseHTTPRequestHandler):
    server_version = "BlueprintLibrary"
    sys_version = ""

    def log_message(self, format, *args):
        # Request URLs can contain private search terms. Do not log them.
        pass

    def _headers(self, status, content_type="application/json; charset=utf-8", length=None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("X-Frame-Options", "DENY")
        if length is not None:
            self.send_header("Content-Length", str(length))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()

    def _json(self, value, status=200, extra=None):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, length=len(data), extra=extra)
        self.wfile.write(data)

    def _authorized(self):
        address = self.client_address[0]
        now = time.monotonic()
        with self.server.auth_lock:
            count, until = self.server.failures.get(address, (0, now))
            if count >= 8 and until > now:
                self._json({"error": "登入嘗試過多，請稍後再試。"}, 429, {"Retry-After": "60"})
                return False
        valid = False
        raw = self.headers.get("Authorization", "")
        if raw.startswith("Basic ") and len(raw) <= 8192:
            try:
                decoded = base64.b64decode(raw[6:], validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
                if len(password) <= 1024:
                    profile = self.server.credential
                    identity = {'username': profile['username'], 'name': profile['username'], 'role': 'owner'}
                    if username != profile['username']:
                        with self.server.store._connection() as conn:
                            account = conn.execute('SELECT * FROM studio_users WHERE username=? AND enabled=1', (username,)).fetchone()
                        if account:
                            profile = dict(account)
                            identity = {key: profile[key] for key in ('username', 'name', 'role')}
                    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                        bytes.fromhex(profile["salt"]), PASSWORD_ROUNDS).hex()
                    valid = hmac.compare_digest(username.encode("utf-8"), profile["username"].encode("utf-8")) & hmac.compare_digest(digest, profile["password_hash"])
                    if valid:
                        self.identity = identity
            except (ValueError, UnicodeError, binascii.Error):
                pass
        with self.server.auth_lock:
            if valid:
                self.server.failures.pop(address, None)
            else:
                if len(self.server.failures) > 10_000:
                    self.server.failures.clear()
                previous = count if until > now else 0
                self.server.failures[address] = (previous + 1, now + 60)
        if not valid:
            self._json({"error": "請使用管理員帳號登入。"}, 401,
                       {"WWW-Authenticate": 'Basic realm="Blueprint Library", charset="UTF-8"'})
        return bool(valid)

    def _write_allowed(self):
        if self.headers.get("X-Blueprint-Request") != "1":
            raise LibraryError("請使用受支援的後台或 API 寫入。", status=403)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != self.headers.get("Host", "").casefold():
                raise LibraryError("拒絕來自其他網站的寫入。", status=403)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise LibraryError("API 寫入需要 application/json。", status=415)

    def _body(self, limit=MAX_JSON_BYTES):
        if self.headers.get("Transfer-Encoding"):
            raise LibraryError("請提供 Content-Length，暫不支援分段上傳。")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise LibraryError("無效的內容長度。") from exc
        if size <= 0:
            raise LibraryError("請提供 JSON 內容。")
        if size > limit:
            raise LibraryError("內容超出大小上限。", status=413)
        self.connection.settimeout(30)
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise LibraryError("內容未完整傳送。")
        try:
            data = json.loads(raw)
        except (UnicodeError, ValueError) as exc:
            raise LibraryError("JSON 格式不正確。") from exc
        if not isinstance(data, dict):
            raise LibraryError("JSON 內容必須是物件。")
        return data

    def _route(self, method):
        if len(self.path) > 8192:
            raise LibraryError("網址過長。", status=414)
        parsed = urlsplit(self.path)
        path = parsed.path
        if method == "GET" and path == "/health":
            return self._json({"status": "ok"})
        if not self._authorized():
            return
        if method in {"POST", "PATCH"}:
            self._write_allowed()
        store = self.server.store
        actor = self.identity['username']
        from .studio import Studio
        from .studio_http import handle as studio_route
        studio = Studio(store, self.identity)
        def collection_payload(data):
            collection_id = data.get('collection_id')
            if collection_id and not studio.admin:
                if not isinstance(collection_id, str):
                    raise LibraryError('收藏集編號不正確。')
                with store._connection() as conn:
                    if conn.execute("SELECT 1 FROM studio_records WHERE id=? AND visibility='admin'", (collection_id,)).fetchone():
                        raise LibraryError('你的帳戶不能把資料放入這個私人主題。', 403)
            return data
        if studio_route(self, method, path, parse_qs(parsed.query, keep_blank_values=True)):
            return
        if not studio.admin:
            if path == '/api/export' or (path == '/api/company-profile' and method == 'PATCH'):
                raise LibraryError('你的帳戶沒有這項權限。', 403)
            protected = re.fullmatch(r'/api/(items|files|workdays|todos)/([a-zA-Z0-9-]+)(?:/history)?', path)
            if protected and not studio.resource_allowed({'items':'item','files':'item','workdays':'workday','todos':'todo'}[protected.group(1)], protected.group(2)):
                raise LibraryError('你的帳戶沒有這項權限。', 403)
        if method == "GET" and path == "/api/info":
            profile = store.get_company_profile()
            return self._json({"version": __version__, "company_name": profile["name"] or "藍圖工作室 Blueprint Studio",
                               "company_profile": profile,
                               "product_name": "Blueprint Studio Backend",
                               "product_title": "Blueprint Studio Backend｜藍圖工作室（後台）",
                               "connection_mode": "native", "classification": "rules", "stats": store.stats(member_access=not studio.admin), "account": self.identity, "studio_schema": 5})
        if path == "/api/company-profile":
            if method == "GET":
                return self._json({"company_profile": store.get_company_profile()})
            if method == "PATCH":
                return self._json({"company_profile": store.update_company_profile(self._body())})
        if path == "/api/install-instructions" and method == "GET":
            from .onboarding import installation_instruction
            return self._json({"instruction": installation_instruction()})
        if method == "GET" and path == "/api/workday-instructions":
            from .integration import workday_example, workday_instruction
            requested_date = parse_qs(parsed.query, keep_blank_values=True).get("date", [None])[0]
            if requested_date is not None:
                try:
                    if calendar_date.fromisoformat(requested_date).isoformat() != requested_date:
                        raise ValueError
                except ValueError as exc:
                    raise LibraryError("日期需要使用 YYYY-MM-DD 格式。") from exc
            return self._json({"instruction": workday_instruction(requested_date),
                               "format": workday_example(requested_date)})
        if path == "/api/workdays":
            if method == "GET":
                query = parse_qs(parsed.query, keep_blank_values=True)
                return self._json(store.list_workdays(
                    q=query.get("q", [""])[0], date=query.get("date", [""])[0],
                    limit=int(query.get("limit", ["30"])[0]),
                    offset=int(query.get("offset", ["0"])[0]), member_access=not studio.admin))
            if method == "POST":
                result = store.import_workday(self._body(), actor=actor, member_access=not studio.admin)
                return self._json(result, 201 if result["created"] else 200)
        workday_match = re.fullmatch(r"/api/workdays/([a-zA-Z0-9-]+)(/history)?", path)
        if workday_match and method == "GET":
            if workday_match.group(2):
                return self._json({"versions": store.item_workday_history(workday_match.group(1))})
            return self._json({"workday": store.get_workday(workday_match.group(1))})
        if workday_match and method == "PATCH" and not workday_match.group(2):
            return self._json(store.update_workday(workday_match.group(1), self._body(), actor=actor))
        if path == "/api/todos" and method == "GET":
            query = parse_qs(parsed.query, keep_blank_values=True)
            done = query.get("done", ["0"])[0]
            if done not in {"0", "1", "all"}:
                raise LibraryError("待辦狀態需要 0、1 或 all。")
            return self._json(store.list_todos(
                done=None if done == "all" else done == "1",
                date=query.get("date", [""])[0], q=query.get("q", [""])[0],
                limit=int(query.get("limit", ["100"])[0]),
                offset=int(query.get("offset", ["0"])[0]), member_access=not studio.admin))
        todo_match = re.fullmatch(r"/api/todos/([a-zA-Z0-9-]+)", path)
        if todo_match and method == "PATCH":
            return self._json({"todo": store.update_todo(todo_match.group(1), self._body(), actor=actor)})
        if path == "/api/collections":
            if method == "GET":
                return self._json({"collections": store.list_collections(member_access=not studio.admin)})
            if method == "POST":
                return self._json({"collection": store.create_collection(self._body())}, 201)
        if path == "/api/items":
            if method == "GET":
                query = parse_qs(parsed.query, keep_blank_values=True)
                options = {key: query.get(key, [""])[0] for key in ("q", "kind", "category", "collection_id", "source_type")}
                options.update(archived=query.get("archived", ["0"])[0] == "1",
                               limit=int(query.get("limit", ["100"])[0]),
                               offset=int(query.get("offset", ["0"])[0]))
                if "pinned" in query:
                    if query["pinned"][0] not in {"0", "1"}:
                        raise LibraryError("星號篩選需要 0 或 1。")
                    options["pinned"] = query["pinned"][0] == "1"
                if "unfiled" in query:
                    if query["unfiled"][0] not in {"0", "1"}:
                        raise LibraryError("待整理篩選需要 0 或 1。")
                    options["unfiled"] = query["unfiled"][0] == "1"
                options["sort"] = query.get("sort", ["updated"])[0]
                return self._json(store.list_items(**options, member_access=not studio.admin))
            if method == "POST":
                return self._json({"item": store.create_item(collection_payload(self._body()), actor=actor)}, 201)
        match = re.fullmatch(r"/api/items/([a-zA-Z0-9-]+)(/history)?", path)
        if match:
            item_id, history = match.groups()
            if method == "GET":
                return self._json({"versions": store.item_history(item_id)} if history else {"item": store.get_item(item_id)})
            if method == "PATCH" and not history:
                return self._json({"item": store.update_item(item_id, collection_payload(self._body()), actor=actor)})
        if path == "/api/files" and method == "POST":
            data = collection_payload(self._body(MAX_FILE_BYTES * 4 // 3 + 128 * 1024))
            content = data.get("content_base64")
            if not isinstance(content, str):
                raise LibraryError("請提供 base64 文件內容。")
            if len(content) > (MAX_FILE_BYTES + 2) // 3 * 4:
                raise LibraryError("每份文件上限為 20 MB。", status=413)
            try:
                blob = base64.b64decode(content, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise LibraryError("文件編碼不正確。") from exc
            if len(blob) > MAX_FILE_BYTES:
                raise LibraryError("每份文件上限為 20 MB。", status=413)
            item = store.add_file(data.get("name"), blob, mime=data.get("mime", ""),
                collection_id=data.get("collection_id"), tags=data.get("tags"),
                notes=data.get("notes", ""), actor=actor)
            return self._json({"item": item}, 201)
        match = re.fullmatch(r"/api/files/([a-zA-Z0-9-]+)", path)
        if match and method == "GET":
            filename, original_name, mime = store.get_file(match.group(1))
            self._headers(200, "application/octet-stream", filename.stat().st_size,
                {"Content-Disposition": "attachment; filename*=UTF-8''" + quote(original_name, safe="")})
            with filename.open("rb") as stream:
                while chunk := stream.read(64 * 1024):
                    self.wfile.write(chunk)
            return
        if path == "/api/classify" and method == "POST":
            data = self._body()
            fields = {key: data.get(key, "") for key in ("name", "url", "mime", "text")}
            if any(not isinstance(value, str) for value in fields.values()):
                raise LibraryError("分類資料必須是文字。")
            return self._json({"classification": classify_item(**fields)})
        if path == "/api/context" and method == "POST":
            data = self._body()
            if set(data) - {"item_ids", "task"}:
                raise LibraryError("交接只接受資料編號及用途。")
            if isinstance(data.get('item_ids'), list) and any(not studio.resource_allowed('item', item_id) for item_id in data['item_ids']):
                raise LibraryError('你不能使用這項私人資料。', 403)
            return self._json(build_context(store, data.get("item_ids"), data.get("task", "general")))
        if path == "/api/export" and method == "GET":
            return self._json(store.export_metadata(), extra={"Content-Disposition": 'attachment; filename="blueprint-library-metadata.json"'})
        if method == "GET":
            if path in {"/", "/index.html", "/app.js", "/workdays.js", "/workspace.js", "/brand.svg", "/style.css", "/connection.js", "/connection-guide.json", "/studio.js", "/studio.css", "/navigation.js", "/manifest.webmanifest", "/service-worker.js"}:
                target = WEB / ("index.html" if path == "/" else path[1:])
                if target.is_file():
                    data = target.read_bytes()
                    self._headers(200, mimetypes.guess_type(target.name)[0] or "application/octet-stream", len(data))
                    self.wfile.write(data)
                    return
            icon = re.fullmatch(r"/icons/([a-z]+)\.svg", path)
            if icon and icon.group(1) in CATEGORIES:
                target = ROOT / "assets" / "icons" / (icon.group(1) + ".svg")
                data = target.read_bytes()
                self._headers(200, "image/svg+xml", len(data))
                self.wfile.write(data)
                return
        raise LibraryError("找不到此內容。", status=404)

    def _dispatch(self, method):
        try:
            self._route(method)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except LibraryError as exc:
            self._json({"error": str(exc)}, exc.status)
        except (ValueError, TypeError, UnicodeError) as exc:
            self._json({"error": "資料格式不正確：" + str(exc)}, 400)
        except (TimeoutError, OSError):
            self._json({"error": "文件或資料庫暫時無法使用，請檢查服務日誌及儲存空間。"}, 500)
        except Exception:
            self._json({"error": "暫時無法完成操作，請檢查資料庫狀態。"}, 500)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_OPTIONS(self):
        self._json({"error": "此 API 只接受同來源請求。"}, 403)


def serve_library(data_dir, host="127.0.0.1", port=8787, open_browser=True):
    server = make_server(data_dir, host=host, port=port)
    url = f"http://{'127.0.0.1' if host in {'0.0.0.0', '::'} else host}:{server.server_port}/"
    print("Blueprint Studio Backend｜藍圖工作室（後台）：" + url, flush=True)
    print("使用 library-init 設定的管理員帳號登入。雲端部署請使用 HTTPS 反向代理。", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
