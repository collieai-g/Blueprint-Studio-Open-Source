"""Synthetic tests; never connect to the user's real backend or credentials."""
import base64
import http.client
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from proposals.blueprint_native_login.gateway import (
    GatewayServer, PREFIX, SESSION_COOKIE, MAX_BODY, private_state_directory, Session,
    private_state_file, atomic_state_json,
)


class FakeBackend(BaseHTTPRequestHandler):
    accepted = "Basic " + base64.b64encode(b"admin:synthetic-test-only-password").decode()
    seen = []
    invalidate = False
    redirect = None
    binary = bytes(range(256)) * 1500

    def log_message(self, *_):
        pass

    def do_GET(self):
        self.seen.append((self.command, self.path, dict(self.headers), b""))
        if self.invalidate or self.headers.get("Authorization") != self.accepted:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="test"')
            self.end_headers()
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.redirect)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = self.binary if self.path == "/binary" else json.dumps({"version": "2.9.0"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream" if self.path == "/binary" else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Set-Cookie", "upstream-secret=must-not-pass")
        if self.path == "/binary":
            self.send_header("Content-Disposition", "attachment; filename*=UTF-8''test.bin")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.seen.append((self.command, self.path, dict(self.headers), data))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")


class PortableGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = ThreadingHTTPServer(("127.0.0.1", 0), FakeBackend)
        cls.gateway = GatewayServer(("127.0.0.1", 0), ("127.0.0.1", cls.backend.server_port))
        cls.threads = []
        for server in (cls.backend, cls.gateway):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gateway, cls.backend):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(2)

    def setUp(self):
        FakeBackend.invalidate = False
        FakeBackend.redirect = None
        FakeBackend.seen.clear()
        with self.gateway.lock:
            self.gateway.sessions.clear()
            self.gateway.nonces.clear()
            self.gateway.failures.clear()

    def request(self, method="GET", path="/", *, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        result = (response.status, response.getheaders(), response.read())
        connection.close()
        return result

    def login(self, password="synthetic-test-only-password"):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        cookie = next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
        nonce = re.search(rb'name="nonce" value="([a-f0-9]+)"', body).group(1).decode()
        payload = urlencode({"username": "admin", "password": password, "nonce": nonce})
        return self.request("POST", PREFIX + "login", headers={"Cookie": cookie,
            "Origin": self.gateway.origin, "Content-Type": "application/x-www-form-urlencoded"}, body=payload)

    def session_cookie(self):
        status, headers, body = self.login()
        self.assertEqual(status, 303)
        values = [v for k, v in headers if k.lower() == "set-cookie"]
        cookie = next(v for v in values if v.startswith(SESSION_COOKIE + "="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        return cookie.split(";", 1)[0]

    def test_unauthenticated_api_has_no_basic_challenge(self):
        status, headers, body = self.request(path="/api/info")
        self.assertEqual(status, 401)
        self.assertNotIn("www-authenticate", {k.lower() for k, _ in headers})
        self.assertEqual(FakeBackend.seen, [])

    def test_endpoints_and_session_lifetime_are_bounded(self):
        for address, upstream in (
            (("0.0.0.0", 0), ("127.0.0.1", self.backend.server_port)),
            (("127.0.0.1", 0), ("example.invalid", 1234)),
            (("127.0.0.1", True), ("127.0.0.1", self.backend.server_port)),
            (("127.0.0.1", 0), ("127.0.0.1", 0)),
            (("127.0.0.1", 0), None),
        ):
            with self.assertRaises(ValueError):
                GatewayServer(address, upstream)
        for lifetime in (0, True, 28801):
            with self.assertRaises(ValueError):
                GatewayServer(("127.0.0.1", 0),
                              ("127.0.0.1", self.backend.server_port), session_seconds=lifetime)

    def test_configured_title_is_escaped_without_changing_original_backend(self):
        server = GatewayServer(("127.0.0.1", 0),
                               ("127.0.0.1", self.backend.server_port), title='<Demo & Studio>')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read().decode()
            connection.close()
            self.assertIn("&lt;Demo &amp; Studio&gt;", body)
            self.assertNotIn("<Demo & Studio>", body)
            self.assertEqual(FakeBackend.seen, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_runtime_state_rejects_source_and_git_paths(self):
        with self.assertRaises(ValueError):
            private_state_directory(Path(__file__).resolve().parent / "runtime-state")
        with tempfile.TemporaryDirectory(prefix="portable-login-boundary-") as folder:
            root = Path(folder)
            (root / ".git").write_text("synthetic git marker", encoding="utf-8")
            with self.assertRaises(ValueError):
                private_state_directory(root / "state")
            self.assertFalse((root / "state").exists())

    def test_atomic_state_write_does_not_touch_preexisting_predictable_temp_file(self):
        with tempfile.TemporaryDirectory(prefix="portable-login-state-") as folder:
            root = Path(folder)
            sentinel = root / "gateway-state.tmp"
            sentinel.write_text("keep-existing-file", encoding="utf-8")
            atomic_state_json(root / "gateway-state.json", {"status": "stopped"})
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep-existing-file")
            self.assertEqual(json.loads((root / "gateway-state.json").read_text()), {"status": "stopped"})
            self.assertEqual(list(root.glob(".gateway-*.tmp")), [])

    def test_linked_state_child_is_rejected_without_os_symlink_privilege(self):
        with tempfile.TemporaryDirectory(prefix="portable-login-linked-state-") as folder:
            root = Path(folder)
            for name in ("gateway-state.json", "stop-gateway.request", "gateway-process.lock"):
                target = root / name
                target.write_text("keep-existing-file", encoding="utf-8")
                with patch.object(Path, "is_symlink", autospec=True, side_effect=lambda path: path == target):
                    with self.assertRaises(ValueError):
                        private_state_file(root, name)
                self.assertEqual(target.read_text(encoding="utf-8"), "keep-existing-file")
            target = root / "gateway-state.json"
            if hasattr(Path, "is_junction"):
                with patch.object(Path, "is_junction", autospec=True, side_effect=lambda path: path == target):
                    with self.assertRaises(ValueError):
                        atomic_state_json(target, {"status": "stopped"})
                self.assertEqual(target.read_text(encoding="utf-8"), "keep-existing-file")

    def test_oversized_request_is_rejected_before_read_or_backend(self):
        status, _, _ = self.request("POST", "/api/workdays", headers={
            "Origin": self.gateway.origin, "Content-Length": str(MAX_BODY + 1),
            "Content-Type": "application/json"})
        self.assertEqual(status, 413)
        self.assertEqual(FakeBackend.seen, [])

    def test_existing_backend_basic_auth_contract_stays_available(self):
        for headers, expected in (({}, 401), ({"Authorization": FakeBackend.accepted}, 200)):
            connection = http.client.HTTPConnection("127.0.0.1", self.backend.server_port, timeout=3)
            connection.request("GET", "/api/info", headers=headers)
            response = connection.getresponse()
            self.assertEqual(response.status, expected)
            response.read()
            connection.close()
        self.assertEqual(self.gateway.sessions, {})

    def test_session_repr_does_not_expose_authorization(self):
        value = Session("Basic synthetic-placeholder", time.monotonic() + 10)
        self.assertNotIn("Basic synthetic-placeholder", repr(value))

    def test_login_page_and_wrong_password(self):
        status, headers, body = self.login("incorrect-test-password")
        self.assertEqual(status, 401)
        self.assertIn("未通過驗證", body.decode())
        self.assertNotIn("www-authenticate", {k.lower() for k, _ in headers})
        self.assertFalse(self.gateway.sessions)

    def test_form_policy_preserves_same_origin_without_changing_proxy_policy(self):
        status, headers, _ = self.request()
        self.assertEqual(status, 200)
        self.assertEqual([v for k, v in headers if k.lower() == "referrer-policy"], ["same-origin"])
        cookie = self.session_cookie()
        status, headers, _ = self.request(path="/api/info", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual([v for k, v in headers if k.lower() == "referrer-policy"], ["no-referrer"])

    def test_null_missing_and_foreign_form_origins_never_reach_backend(self):
        for origin in ("null", None, "http://attacker.example"):
            _, headers, body = self.request()
            cookie = next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
            nonce = re.search(rb'name="nonce" value="([a-f0-9]+)"', body).group(1).decode()
            form_headers = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
            if origin is not None:
                form_headers["Origin"] = origin
            status, _, _ = self.request("POST", PREFIX + "login", headers=form_headers,
                body=urlencode({"username": "admin", "password": "synthetic-test-only-password", "nonce": nonce}))
            self.assertEqual(status, 403)
        self.assertEqual(FakeBackend.seen, [])

    def test_success_cookie_original_proxy_and_no_upstream_cookie(self):
        cookie = self.session_cookie()
        status, headers, body = self.request(path="/api/info", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "2.9.0")
        self.assertNotIn("set-cookie", {k.lower() for k, _ in headers})
        self.assertEqual(FakeBackend.seen[-1][2]["Authorization"], FakeBackend.accepted)
        self.assertNotIn("Cookie", FakeBackend.seen[-1][2])

    def test_host_and_csrf_rejected(self):
        self.assertEqual(self.request(headers={"Host": "attacker.example"})[0], 403)
        cookie = self.session_cookie()
        before = len(FakeBackend.seen)
        status, _, _ = self.request("POST", "/api/workdays", headers={"Cookie": cookie,
            "Origin": "http://attacker.example", "Content-Type": "application/json"}, body="{}")
        self.assertEqual(status, 403)
        self.assertEqual(len(FakeBackend.seen), before)

    def test_nonce_rejected_without_cookie(self):
        status, _, _ = self.request("POST", PREFIX + "login", headers={"Origin": self.gateway.origin,
            "Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode({"username": "admin", "password": "x", "nonce": "a" * 64}))
        self.assertEqual(status, 403)
        self.assertEqual(FakeBackend.seen, [])

    def test_favicon_and_assets_do_not_replace_login_nonce(self):
        _, headers, body = self.request()
        cookie = next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
        nonce = re.search(rb'name="nonce" value="([a-f0-9]+)"', body).group(1).decode()
        for path, expected in (("/favicon.ico", 204), ("/style.css", 401)):
            status, asset_headers, _ = self.request(path=path, headers={"Cookie": cookie})
            self.assertEqual(status, expected)
            self.assertNotIn("set-cookie", {k.lower() for k, _ in asset_headers})
        _, _, repeated = self.request(headers={"Cookie": cookie})
        self.assertIn(nonce.encode(), repeated)
        status, _, _ = self.request("POST", PREFIX + "login", headers={"Cookie": cookie,
            "Origin": self.gateway.origin, "Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode({"username": "admin", "password": "synthetic-test-only-password", "nonce": nonce}))
        self.assertEqual(status, 303)

    def test_expired_nonce_is_rejected_before_backend(self):
        _, headers, body = self.request()
        cookie = next(v.split(";", 1)[0] for k, v in headers if k.lower() == "set-cookie")
        nonce = re.search(rb'name="nonce" value="([a-f0-9]+)"', body).group(1).decode()
        with self.gateway.lock:
            self.gateway.nonces[nonce] = time.monotonic() - 1
        status, _, _ = self.request("POST", PREFIX + "login", headers={"Cookie": cookie,
            "Origin": self.gateway.origin, "Content-Type": "application/x-www-form-urlencoded"},
            body=urlencode({"username": "admin", "password": "synthetic-test-only-password", "nonce": nonce}))
        self.assertEqual(status, 403)
        self.assertEqual(FakeBackend.seen, [])

    def test_external_redirects_are_not_followed_or_exposed(self):
        cookie = self.session_cookie()
        for location in ("https://attacker.example/", "/\\attacker.example/"):
            FakeBackend.redirect = location
            status, headers, _ = self.request(path="/redirect", headers={"Cookie": cookie})
            self.assertEqual(status, 502)
            self.assertNotIn("location", {k.lower() for k, _ in headers})
        FakeBackend.redirect = self.gateway.upstream_origin + "/#/overview"
        status, headers, _ = self.request(path="/redirect", headers={"Cookie": cookie})
        self.assertEqual(status, 302)
        self.assertIn(("Location", "/#/overview"), headers)

    def test_duplicate_host_origin_and_length_are_rejected(self):
        for duplicate in ("Host", "Origin", "Content-Length"):
            connection = http.client.HTTPConnection("127.0.0.1", self.gateway.server_port, timeout=3)
            connection.putrequest("POST", PREFIX + "login", skip_host=True)
            for key, value in (("Host", self.gateway.expected_host), ("Origin", self.gateway.origin),
                               ("Content-Length", "0")):
                connection.putheader(key, value)
                if key == duplicate:
                    connection.putheader(key, value)
            connection.endheaders()
            response = connection.getresponse()
            self.assertIn(response.status, (400, 403))
            response.read()
            connection.close()
        self.assertEqual(FakeBackend.seen, [])

    def test_headers_and_body_rewritten_for_same_backend(self):
        cookie = self.session_cookie()
        status, _, _ = self.request("POST", "/api/workdays", headers={"Cookie": cookie,
            "Origin": self.gateway.origin, "Content-Type": "application/json",
            "X-Blueprint-Request": "1", "X-Blueprint-Account": "admin",
            "X-Forwarded-Host": "attacker.example"}, body='{"synthetic":true}')
        self.assertEqual(status, 200)
        method, path, headers, body = FakeBackend.seen[-1]
        self.assertEqual(headers["Host"], f"127.0.0.1:{self.backend.server_port}")
        self.assertEqual(headers["Origin"], self.gateway.upstream_origin)
        self.assertEqual(headers["X-Blueprint-Request"], "1")
        self.assertEqual(headers["X-Blueprint-Account"], "admin")
        self.assertNotIn("X-Forwarded-Host", headers)
        self.assertEqual(body, b'{"synthetic":true}')

    def test_binary_download_and_head(self):
        cookie = self.session_cookie()
        status, headers, body = self.request(path="/binary", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        self.assertEqual(body, FakeBackend.binary)
        self.assertIn("content-disposition", {k.lower() for k, _ in headers})
        self.assertEqual(self.request("HEAD", "/binary", headers={"Cookie": cookie})[2], b"")

    def test_upstream_rejection_removes_session(self):
        cookie = self.session_cookie()
        FakeBackend.invalidate = True
        status, headers, body = self.request(path="/api/info", headers={"Cookie": cookie})
        self.assertEqual(status, 401)
        self.assertFalse(self.gateway.sessions)
        self.assertNotIn("www-authenticate", {k.lower() for k, _ in headers})

    def test_session_expiry(self):
        cookie = self.session_cookie()
        with self.gateway.lock:
            for session in self.gateway.sessions.values():
                session.expires = time.monotonic() - 1
        self.assertEqual(self.request(path="/api/info", headers={"Cookie": cookie})[0], 401)

    def test_supplied_authorization_and_absolute_target_rejected(self):
        self.assertEqual(self.request(headers={"Authorization": "Basic abc"})[0], 400)
        self.assertEqual(self.request(path="http://attacker.example/", headers={"Host": self.gateway.expected_host})[0], 400)

    def test_login_throttle_and_logout(self):
        for _ in range(5):
            self.assertEqual(self.login("wrong")[0], 401)
        self.assertEqual(self.login("wrong")[0], 429)
        with self.gateway.lock:
            self.gateway.failures.clear()
        cookie = self.session_cookie()
        status, _, _ = self.request("POST", PREFIX + "logout", headers={"Cookie": cookie,
            "Origin": self.gateway.origin}, body="")
        self.assertEqual(status, 303)
        self.assertFalse(self.gateway.sessions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
