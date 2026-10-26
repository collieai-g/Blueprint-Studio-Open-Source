"""Isolated browser regression: synthetic credentials and ephemeral loopback only.

The broken policy reproduces an introduced form-adapter bug. This does not
reproduce or prove the separate historical Basic-auth client compatibility gap.
"""
from __future__ import annotations

import argparse
import base64
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode, urlsplit

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from proposals.blueprint_native_login.gateway import GatewayServer, GatewayHandler, PREFIX

CASE_ID = "blueprint-login-001"
TEST_USERNAME = "synthetic-user"
TEST_PASSWORD = "synthetic-test-only-password"
TEST_AUTHORIZATION = "Basic " + base64.b64encode(
    f"{TEST_USERNAME}:{TEST_PASSWORD}".encode("utf-8")).decode("ascii")


def private_directory(value):
    path = Path(value).expanduser().absolute()
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError("請使用沒有連結或接合點的私人目錄。")
    path = path.resolve()
    if path == PROJECT or PROJECT in path.parents or path in PROJECT.parents:
        raise ValueError("測試狀態必須在此程式專案以外。")
    if any((p / ".git").exists() for p in (path, *path.parents)):
        raise ValueError("測試狀態不能放在 Git checkout。")
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_state_file(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)()
           for p in (path, *path.parents)):
        raise ValueError("私人狀態檔案不能包含連結或接合點。")
    if path.exists() and not path.is_file():
        raise ValueError("私人狀態位置需要是一般檔案。")
    return path


def atomic_json(path, payload):
    path = safe_state_file(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        safe_state_file(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class Observations:
    def __init__(self, state):
        self.path = state / "browser-observations.json"
        self.lock = threading.RLock()
        self.data = {"contract_version": 1, "case_id": CASE_ID, "synthetic": True,
                     "backend_verified_requests": 0, "null_origin_probe": {"passed": False},
                     "variants": {name: {"login_posts": 0, "login_successes": 0,
                         "last_login_status": None, "origins": {kind: 0 for kind in
                             ("same", "null", "missing", "foreign", "duplicate")}}
                         for name in ("broken", "fixed")}}
        self.save()

    def save(self):
        with self.lock:
            atomic_json(self.path, self.data)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def origin(self, variant, values, expected):
        kind = ("missing" if not values else "duplicate" if len(values) != 1 else
                "same" if values[0] == expected else "null" if values[0] == "null" else "foreign")
        with self.lock:
            record = self.data["variants"][variant]
            record["login_posts"] += 1
            record["origins"][kind] += 1
            self.save()

    def status(self, variant, status):
        with self.lock:
            record = self.data["variants"][variant]
            record["last_login_status"] = status
            if status == 303:
                record["login_successes"] += 1
            self.save()


class SyntheticBackend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.headers.get("Authorization") != TEST_AUTHORIZATION:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Synthetic fixture"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        with self.server.observations.lock:
            self.server.observations.data["backend_verified_requests"] += 1
            self.server.observations.save()
        if urlsplit(self.path).path == "/api/info":
            content_type = "application/json; charset=utf-8"
            body = json.dumps({"version": "synthetic-fixture", "synthetic": True,
                "account": {"username": TEST_USERNAME, "role": "owner"}}).encode("utf-8")
        else:
            content_type = "text/html; charset=utf-8"
            body = ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
                '<title>Synthetic login fixture — passed</title><main>'
                '<h1>合成登入測試通過</h1><p>SYNTHETIC FIXTURE — no company data</p>'
                '<p>測試後台已驗證虛構帳號；這不是私人工作台驗收。</p>'
                '<p>角色：owner（合成測試）</p></main></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class DiagnosticHandler(GatewayHandler):
    variant = "fixed"

    def _valid_request(self):
        if self.command == "POST" and urlsplit(self.path).path == PREFIX + "login":
            self.server.observations.origin(self.variant,
                self.headers.get_all("Origin", []), self.server.origin)
        return super()._valid_request()

    def _headers(self, status, content_type="application/json; charset=utf-8", length=0,
                 extra=(), *, csp=None, referrer_policy="no-referrer"):
        if self.command == "POST" and urlsplit(self.path).path == PREFIX + "login":
            self.server.observations.status(self.variant, status)
        if self.variant == "broken" and referrer_policy == "same-origin":
            referrer_policy = "no-referrer"
        return super()._headers(status, content_type, length, extra,
                               csp=csp, referrer_policy=referrer_policy)

    def _route(self):
        if self.command in {"GET", "HEAD"} and urlsplit(self.path).path == "/__fixture/status":
            if self._valid_request():
                self._json(self.server.observations.snapshot())
            return
        return super()._route()


class BrokenPolicyHandler(DiagnosticHandler):
    variant = "broken"


def null_origin_probe(server):
    """A separate HTTP security check, never presented as browser execution."""
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        cookies = SimpleCookie()
        for name, value in response.getheaders():
            if name.lower() == "set-cookie":
                cookies.load(value)
        document = response.read().decode("utf-8")
        nonce = re.search(r'name="nonce" value="([0-9a-f]{64})"', document)
        if response.status != 200 or nonce is None:
            return False
        cookie_header = "; ".join(f"{name}={morsel.value}" for name, morsel in cookies.items())
    finally:
        connection.close()
    before = server.observations.snapshot()["backend_verified_requests"]
    body = urlencode({"username": TEST_USERNAME, "password": TEST_PASSWORD, "nonce": nonce[1]})
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("POST", PREFIX + "login", body=body, headers={
            "Origin": "null", "Cookie": cookie_header,
            "Content-Type": "application/x-www-form-urlencoded"})
        response = connection.getresponse()
        response.read()
        return response.status == 403 and before == server.observations.snapshot()["backend_verified_requests"]
    finally:
        connection.close()


def run(state):
    for name in ("fixture.json", "browser-observations.json", "stop.request", "fixture.lock"):
        safe_state_file(state / name)
    if any((state / name).exists() for name in
           ("fixture.json", "browser-observations.json", "stop.request")):
        raise ValueError("已有合成測試證據或停止要求；請先讀回，另選新的執行目錄。")
    lock = state / "fixture.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    servers = []
    threads = []
    started_servers = []
    metadata = {"contract_version": 1, "case_id": CASE_ID, "synthetic": True,
                "pid": os.getpid(), "status": "starting"}
    try:
        observations = Observations(state)
        backend = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticBackend)
        backend.observations = observations
        servers.append(backend)
        for variant, handler in (("broken", BrokenPolicyHandler), ("fixed", DiagnosticHandler)):
            server = GatewayServer(("127.0.0.1", 0), ("127.0.0.1", backend.server_port),
                                  title=f"Synthetic login fixture / {variant}")
            server.RequestHandlerClass = handler
            server.observations = observations
            servers.append(server)
            metadata[variant + "_url"] = server.origin + "/"
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
            started_servers.append(server)
        fixed = servers[-1]
        probe_passed = null_origin_probe(fixed)
        with observations.lock:
            observations.data["null_origin_probe"] = {
                "passed": probe_passed, "mechanism": "synthetic_http_security_probe",
                "backend_not_contacted": probe_passed}
            observations.save()
        if not probe_passed:
            raise ValueError("合成來源保護檢查未通過。")
        metadata.update({"status": "running", "backend_port": backend.server_port,
                         "result_file": "browser-observations.json", "stop_file": "stop.request"})
        atomic_json(state / "fixture.json", metadata)
        print(json.dumps(metadata, ensure_ascii=False), flush=True)
        stop = state / "stop.request"
        while not safe_state_file(stop).exists():
            time.sleep(0.2)
        safe_state_file(stop).unlink()
    finally:
        for server in reversed(servers):
            if server in started_servers:
                server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)
        metadata["status"] = "stopped"
        atomic_json(state / "fixture.json", metadata)
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="只用合成資料及隨機本機連接埠重現登入來源問題。")
    parser.add_argument("--state-dir", required=True, help="程式及 Git 以外的私人測試目錄")
    parser.add_argument("--stop", action="store_true", help="要求同一目錄的測試正常停止")
    args = parser.parse_args()
    try:
        state = private_directory(args.state_dir)
        if args.stop:
            metadata = json.loads(safe_state_file(state / "fixture.json").read_text(encoding="utf-8"))
            if metadata.get("case_id") != CASE_ID or metadata.get("synthetic") is not True:
                raise ValueError("不是此案例的合成測試。")
            if metadata.get("status") == "stopped":
                print(json.dumps({"status": "already_stopped"}))
                return 0
            stop = safe_state_file(state / "stop.request")
            if not stop.exists():
                descriptor = os.open(stop, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                    stream.write("stop-owned-fixture")
            print(json.dumps({"status": "stop_requested"}))
            return 0
        run(state)
        return 0
    except (OSError, ValueError):
        print(json.dumps({"error": "合成測試未啟動或未完成；請核對私人目錄與既有 fixture 狀態。"},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
