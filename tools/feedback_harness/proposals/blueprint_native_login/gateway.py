"""Portable loopback sign-in proposal; the native Blueprint backend stays unchanged.

The native backend authenticates every proxied request. Password-derived Basic
headers and opaque sessions exist only in this process and are never logged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import html
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit

VERSION = "0.1.0"
SESSION_COOKIE = "blueprint_browser_session"
NONCE_COOKIE = "blueprint_browser_login_nonce"
PREFIX = "/__blueprint_gateway/"
SESSION_SECONDS = 8 * 60 * 60
MAX_BODY = 32 * 1024 * 1024
REQUEST_HEADERS = {"accept", "accept-language", "content-type", "content-length",
                   "x-blueprint-request", "x-blueprint-account", "range", "if-range"}
RESPONSE_HEADERS = {"content-type", "content-length", "content-disposition",
                    "content-security-policy", "x-content-type-options", "referrer-policy",
                    "x-frame-options", "content-range", "accept-ranges", "retry-after",
                    "etag", "last-modified"}


@dataclass
class Session:
    authorization: str = field(repr=False)
    expires: float


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True

    def __init__(self, address=("127.0.0.1", 0), upstream=None,
                 *, title="Blueprint Studio", session_seconds=SESSION_SECONDS):
        def endpoint(value, *, ephemeral):
            return (isinstance(value, tuple) and len(value) == 2
                    and value[0] == "127.0.0.1" and type(value[1]) is int
                    and (0 if ephemeral else 1) <= value[1] <= 65535)
        if not endpoint(address, ephemeral=True) or not endpoint(upstream, ephemeral=False):
            raise ValueError("Select loopback endpoints and valid ports explicitly.")
        if (not isinstance(title, str) or not title.strip() or len(title) > 120
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in title)):
            raise ValueError("Use a short display title without control characters.")
        if type(session_seconds) is not int or not 1 <= session_seconds <= SESSION_SECONDS:
            raise ValueError("Session lifetime must be between one second and eight hours.")
        self.title = title
        self.upstream = upstream
        self.session_seconds = session_seconds
        self.sessions: dict[str, Session] = {}
        self.nonces: dict[str, float] = {}
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()
        super().__init__(address, GatewayHandler)
        self.expected_host = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{self.expected_host}"
        self.upstream_origin = f"http://{upstream[0]}:{upstream[1]}"

    def handle_error(self, request, client_address):
        # Never print requests, headers, form data or exceptions with credentials.
        print("A local gateway request ended unexpectedly.", file=sys.stderr, flush=True)

    def prune(self):
        now = time.monotonic()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v.expires > now}
            self.nonces = {k: v for k, v in self.nonces.items() if v > now}
            self.failures = {k: [t for t in v if now - t < 60]
                             for k, v in self.failures.items() if any(now - t < 60 for t in v)}


class GatewayHandler(BaseHTTPRequestHandler):
    server: GatewayServer
    protocol_version = "HTTP/1.1"
    server_version = "BlueprintLocalSignIn"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_):
        pass

    def _headers(self, status, content_type="application/json; charset=utf-8", length=0,
                 extra=(), *, csp=None, referrer_policy="no-referrer"):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", referrer_policy)
        self.send_header("X-Frame-Options", "DENY")
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        if csp:
            self.send_header("Content-Security-Policy", csp)
        for key, value in extra:
            self.send_header(key, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _json(self, value, status=200, extra=()):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, length=len(data), extra=extra,
                      csp="default-src 'none'; frame-ancestors 'none'")
        if self.command != "HEAD":
            self.wfile.write(data)

    def _cookie(self, name, value, max_age):
        return ("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}")

    def _cookies(self):
        if len(self.headers.get("Cookie", "")) > 16 * 1024:
            return {}
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            return {key: value.value for key, value in cookie.items()}
        except Exception:
            return {}

    def _valid_request(self):
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] != self.server.expected_host:
            self._json({"error": "請使用已確認的本機工作台地址。"}, 403)
            return False
        try:
            parts = urlsplit(self.path)
        except ValueError:
            self._json({"error": "網址格式不正確。"}, 400)
            return False
        if (len(self.path) > 8192 or not self.path.startswith("/") or self.path.startswith("//")
                or parts.scheme or parts.netloc or parts.fragment or "\\" in self.path
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in self.path)):
            self._json({"error": "網址格式不正確。"}, 400)
            return False
        if (self.headers.get_all("Authorization") or self.headers.get_all("Proxy-Authorization")
                or self.headers.get_all("Transfer-Encoding") or self.headers.get_all("Expect")
                or len(self.headers.get_all("Content-Length", [])) > 1):
            self._json({"error": "請使用本機登入頁及原版工作台。"}, 400)
            return False
        if self.command in {"POST", "PATCH"}:
            if len(origins) != 1 or origins[0] != self.server.origin:
                self._json({"error": "拒絕來自其他網站的操作。"}, 403)
                return False
        elif origins and (len(origins) != 1 or origins[0] != self.server.origin):
            self._json({"error": "拒絕來自其他網站的請求。"}, 403)
            return False
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = -1
        if size < 0 or size > MAX_BODY or (self.command in {"GET", "HEAD"} and size):
            self._json({"error": "內容長度不正確或超出上限。"}, 413 if size > MAX_BODY else 400)
            return False
        self.body_size = size
        return True

    def _session(self):
        self.server.prune()
        token = self._cookies().get(SESSION_COOKIE, "")
        if len(token) != 64:
            return None, None
        with self.server.lock:
            return token, self.server.sessions.get(token)

    def _login_page(self, message="", status=200, *, clear_session=False):
        self.server.prune()
        candidate = self._cookies().get(NONCE_COOKIE, "")
        with self.server.lock:
            nonce = (candidate if re.fullmatch(r"[0-9a-f]{64}", candidate)
                     and self.server.nonces.get(candidate, 0) > time.monotonic()
                     else secrets.token_hex(32))
            # Bound pending anonymous forms, without persisting anything.
            if len(self.server.nonces) >= 256:
                self.server.nonces.pop(next(iter(self.server.nonces)))
            self.server.nonces[nonce] = time.monotonic() + 600
        style_nonce = secrets.token_urlsafe(18)
        title = html.escape(self.server.title)
        content = f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}｜本機登入</title>
<style nonce="{style_nonce}">body{{font:16px system-ui,sans-serif;background:#f5f5f2;color:#242424;margin:0;padding:32px}}main{{max-width:360px;margin:8vh auto;background:white;border:1px solid #ddd;border-radius:12px;padding:28px}}h1{{font-size:24px}}label{{display:block;margin:18px 0 6px}}input,button{{box-sizing:border-box;width:100%;padding:11px;border:1px solid #aaa;border-radius:6px;font:inherit}}button{{margin-top:24px;background:#242424;color:white;cursor:pointer}}p{{line-height:1.6;color:#555}}.error{{color:#a02424}}</style>
</head><body><main><h1>{title}</h1><p>本機登入接駁入口<br>登入後開啟現有的原版藍圖工作台。</p>
<p class="error" role="status">{html.escape(message)}</p>
<form method="post" action="{PREFIX}login"><input type="hidden" name="nonce" value="{nonce}">
<label for="username">帳號</label><input id="username" name="username" value="admin" autocomplete="username" required maxlength="200">
<label for="password">密碼</label><input id="password" name="password" type="password" autocomplete="current-password" required maxlength="1024">
<button type="submit">登入工作台</button></form><p>使用你已設定的管理員帳號。密碼不會寫入檔案。</p></main></body></html>'''.encode("utf-8")
        extra = [self._cookie(NONCE_COOKIE, nonce, 600)]
        if clear_session:
            extra.append(self._cookie(SESSION_COOKIE, "", 0))
        self._headers(status, "text/html; charset=utf-8", len(content), extra,
                      csp=f"default-src 'none'; style-src 'nonce-{style_nonce}'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
                      referrer_policy="same-origin")
        if self.command != "HEAD":
            self.wfile.write(content)

    def _login(self):
        if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            self._json({"error": "請使用登入表單。"}, 415)
            return
        if not 0 < self.body_size <= 16 * 1024:
            self._json({"error": "登入表單大小不正確。"}, 400)
            return
        raw = self.rfile.read(self.body_size)
        if len(raw) != self.body_size:
            self._json({"error": "登入表單未完整傳送。"}, 400)
            return
        try:
            form = parse_qs(raw.decode("utf-8"), strict_parsing=True, max_num_fields=4)
        except (ValueError, UnicodeError):
            self._json({"error": "登入表單格式不正確。"}, 400)
            return
        if set(form) != {"username", "password", "nonce"} or any(len(v) != 1 for v in form.values()):
            self._json({"error": "登入表單格式不正確。"}, 400)
            return
        username, password, nonce = (form[k][0] for k in ("username", "password", "nonce"))
        cookie_nonce = self._cookies().get(NONCE_COOKIE, "")
        self.server.prune()
        with self.server.lock:
            valid_nonce = (bool(re.fullmatch(r"[0-9a-f]{64}", nonce))
                           and bool(re.fullmatch(r"[0-9a-f]{64}", cookie_nonce))
                           and hmac.compare_digest(nonce, cookie_nonce)
                           and self.server.nonces.pop(nonce, 0) > time.monotonic())
            attempts = self.server.failures.setdefault(self.client_address[0], [])
            limited = len(attempts) >= 5
            if valid_nonce and not limited:
                attempts.append(time.monotonic())
        if not valid_nonce:
            self._login_page("登入頁已過期，請在目前頁面重新輸入。", 403)
            return
        if limited:
            self._json({"error": "登入嘗試過多，請等候一分鐘再試。"}, 429, [("Retry-After", "60")])
            return
        if not username or ":" in username or len(username) > 200 or not password or len(password) > 1024:
            self._login_page("帳號或密碼格式不正確。", 401)
            return
        authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        connection = http.client.HTTPConnection(*self.server.upstream, timeout=15)
        try:
            connection.request("GET", "/api/info", headers={"Authorization": authorization, "Accept": "application/json"})
            response = connection.getresponse()
            status = response.status
            # Validate the response shape without exposing account or credential data.
            payload = response.read(256 * 1024 + 1)
            valid = status == 200 and len(payload) <= 256 * 1024 and isinstance(json.loads(payload), dict)
        except (OSError, ValueError, http.client.HTTPException):
            self._login_page("原生工作台暫時未能驗證登入，請稍後再試。", 502)
            return
        finally:
            connection.close()
            password = None
            form = None
            raw = None
        if not valid:
            if status == 429:
                self._json({"error": "原生工作台登入暫時受限，請等候一分鐘再試。"}, 429, [("Retry-After", "60")])
            else:
                self._login_page("帳號或密碼未通過驗證，請使用你已設定的登入資料。", 401)
            return
        token = secrets.token_hex(32)
        previous = self._cookies().get(SESSION_COOKIE, "")
        with self.server.lock:
            self.server.sessions.pop(previous, None)
            if len(self.server.sessions) >= 64:
                self.server.sessions.pop(next(iter(self.server.sessions)))
            self.server.sessions[token] = Session(authorization, time.monotonic() + self.server.session_seconds)
            self.server.failures.pop(self.client_address[0], None)
        self._headers(303, length=0, extra=[("Location", "/#/overview"),
            self._cookie(SESSION_COOKIE, token, int(self.server.session_seconds)), self._cookie(NONCE_COOKIE, "", 0)])

    def _proxy(self, token, session):
        connection = http.client.HTTPConnection(*self.server.upstream, timeout=60)
        try:
            connection.putrequest(self.command, self.path, skip_accept_encoding=True)
            for key, value in self.headers.items():
                if key.lower() in REQUEST_HEADERS:
                    connection.putheader(key, value)
            connection.putheader("Authorization", session.authorization)
            if self.headers.get("Origin"):
                connection.putheader("Origin", self.server.upstream_origin)
            connection.putheader("Connection", "close")
            connection.endheaders()
            remaining = self.body_size
            while remaining:
                chunk = self.rfile.read(min(64 * 1024, remaining))
                if not chunk:
                    raise OSError("Incomplete local request body")
                connection.send(chunk)
                remaining -= len(chunk)
            response = connection.getresponse()
            if response.status == 401:
                with self.server.lock:
                    self.server.sessions.pop(token, None)
                if self.path.startswith("/api/"):
                    self._json({"error": "登入已失效，請重新開啟本機登入頁。"}, 401,
                               [self._cookie(SESSION_COOKIE, "", 0)])
                else:
                    self._login_page("登入已失效，請重新登入。", clear_session=True)
                return
            extra = [(key, value) for key, value in response.getheaders()
                     if key.lower() in RESPONSE_HEADERS and key.lower() not in
                     {"content-type", "content-length", "x-content-type-options", "referrer-policy", "x-frame-options"}]
            location = response.getheader("Location")
            if location:
                if "\\" in location or any(ord(ch) < 32 or ord(ch) == 127 for ch in location):
                    self._json({"error": "原生工作台回覆了不受支援的轉址。"}, 502)
                    return
                target = urlsplit(location)
                if target.scheme or target.netloc:
                    if target.scheme != "http" or target.netloc != f"{self.server.upstream[0]}:{self.server.upstream[1]}":
                        self._json({"error": "原生工作台回覆了不受支援的轉址。"}, 502)
                        return
                    location = target.path + ("?" + target.query if target.query else "") + ("#" + target.fragment if target.fragment else "")
                if not location.startswith("/") or location.startswith("//"):
                    self._json({"error": "原生工作台回覆了不受支援的轉址。"}, 502)
                    return
                extra.append(("Location", location))
            length = response.getheader("Content-Length")
            self._headers(response.status, response.getheader("Content-Type", "application/octet-stream"),
                          length, extra)
            if self.command != "HEAD":
                while chunk := response.read(64 * 1024):
                    self.wfile.write(chunk)
        except (OSError, http.client.HTTPException):
            if not getattr(self, "_headers_sent", False):
                # Avoid leaking exception text, request URLs or credentials.
                self._json({"error": "原生工作台暫時未能連線；請稍後再試。"}, 502)
        finally:
            connection.close()

    def end_headers(self):
        super().end_headers()
        self._headers_sent = True

    def _route(self):
        if not self._valid_request():
            return
        path = urlsplit(self.path).path
        if path == PREFIX + "health" and self.command in {"GET", "HEAD"}:
            self._json({"status": "ok", "gateway_version": VERSION})
            return
        if path == PREFIX + "login":
            if self.command == "POST":
                self._login()
            elif self.command in {"GET", "HEAD"}:
                if urlsplit(self.path).query:
                    self._json({"error": "請勿將登入資料放在網址。"}, 400)
                else:
                    self._login_page()
            else:
                self._json({"error": "不支援此操作。"}, 405)
            return
        token, session = self._session()
        if path == PREFIX + "logout":
            if self.command != "POST":
                self._json({"error": "請使用登出操作。"}, 405)
                return
            with self.server.lock:
                self.server.sessions.pop(token, None)
            self._headers(303, extra=[("Location", "/"), self._cookie(SESSION_COOKIE, "", 0)])
            return
        if path.startswith(PREFIX):
            self._json({"error": "找不到此入口。"}, 404)
            return
        if not session:
            if path == "/favicon.ico" and self.command in {"GET", "HEAD"}:
                self._headers(204, content_type=None)
            elif (path.startswith("/api/") or self.command not in {"GET", "HEAD"}
                  or (path not in {"/", "/index.html"}
                      and self.headers.get("Sec-Fetch-Dest") != "document")):
                self._json({"error": "請先在本機工作台登入。"}, 401)
            else:
                self._login_page()
            return
        self._proxy(token, session)

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_POST(self):
        self._route()

    def do_PATCH(self):
        self._route()

    def do_OPTIONS(self):
        if self._valid_request():
            self._json({"error": "此入口只接受同來源工作台操作。"}, 403)

    def do_DELETE(self):
        if self._valid_request():
            self._json({"error": "不支援此操作。"}, 405)


def private_state_directory(value):
    """Require caller-owned runtime state outside source and every Git checkout."""
    target = Path(value).expanduser().absolute()
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise ValueError("Runtime state must not traverse symbolic links.")
    target = target.resolve()
    source_root = Path(__file__).resolve().parents[2]
    if target == source_root or source_root in target.parents:
        raise ValueError("Keep runtime state outside this source project.")
    if any((parent / ".git").exists() for parent in (target, *target.parents)):
        raise ValueError("Keep runtime state outside every Git checkout.")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def private_state_file(directory, name):
    """Reject linked state children before reading or replacing them."""
    directory = private_state_directory(directory)
    target = directory / name
    if (target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction())
            or (target.exists() and not target.is_file())):
        raise ValueError("Runtime state children must be ordinary files.")
    return target


def atomic_state_json(path, payload):
    path = private_state_file(path.parent, path.name)
    descriptor, temporary = tempfile.mkstemp(prefix=".gateway-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        private_state_file(path.parent, path.name)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(argv=None):
    parser = argparse.ArgumentParser(description="原生藍圖的獨立本機登入接駁參考實作；不修改公司資料。")
    parser.add_argument("--listen-port", type=int, default=0)
    parser.add_argument("--backend-port", type=int, required=True)
    parser.add_argument("--title", default="Blueprint Studio")
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args(argv)
    server = None
    lease = None
    lease_path = None
    state = None
    try:
        directory = private_state_directory(args.state_dir)
        state = private_state_file(directory, "gateway-state.json")
        stop = private_state_file(directory, "stop-gateway.request")
        lease_path = private_state_file(directory, "gateway-process.lock")
        lease = os.open(lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(lease, str(os.getpid()).encode("ascii"))
        if stop.exists():
            raise ValueError("Inspect the pending stop request before starting another process.")
        if state.exists():
            previous = json.loads(state.read_text(encoding="utf-8"))
            if previous.get("format") != "blueprint-login-gateway-state/1" or previous.get("status") != "stopped":
                raise ValueError("Inspect the existing runtime state before starting another process.")
        server = GatewayServer(("127.0.0.1", args.listen_port),
                               ("127.0.0.1", args.backend_port), title=args.title)

        def write_state(status):
            payload = {"format": "blueprint-login-gateway-state/1", "pid": os.getpid(),
                       "status": status, "url": server.origin + "/",
                       "backend": server.upstream_origin, "gateway_version": VERSION}
            atomic_state_json(state, payload)

        def watch():
            while not stop.exists():
                time.sleep(0.25)
            try:
                private_state_file(directory, "stop-gateway.request").unlink()
            except (OSError, ValueError):
                print("The local stop-request file needs inspection.", file=sys.stderr, flush=True)
            server.shutdown()

        write_state("running")
        threading.Thread(target=watch, daemon=True).start()
        print(json.dumps({"status": "ready", "url": server.origin + "/", "version": VERSION}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            with server.lock:
                server.sessions.clear()
                server.nonces.clear()
            write_state("stopped")
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error": "接駁未啟動或未正常完成；請檢查連接埠及外置私人狀態，不會重設帳號或公司資料。"}, ensure_ascii=False), file=sys.stderr)
        if server is not None:
            server.server_close()
        return 2
    finally:
        if lease is not None:
            os.close(lease)
            if lease_path is not None:
                lease_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(run())
