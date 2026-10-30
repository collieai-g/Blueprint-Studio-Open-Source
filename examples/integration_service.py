#!/usr/bin/env python3
"""Fictional Blueprint Integration Contract 1.0 HTTP acceptance example.

This creates and destroys an isolated local backend and a separate simulated
service inbox. It never connects a production account, starts an AI runtime, or
sends a message. All temporary private data remains outside the source checkout.
"""
from __future__ import annotations

from contextlib import closing

import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blueprint_library.server import initialize_library, make_server


PREFIX = "/api/integrations/v1"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Small HTTP client; credentials stay in headers on a fixed local origin."""

    def __init__(self, origin: str, authorization: str):
        self.origin = origin
        self.authorization = authorization
        self.opener = build_opener(_NoRedirect())

    def request(self, method: str, path: str, payload=None):
        if not path.startswith(PREFIX + "/"):
            raise ValueError("The example only calls Integration Contract routes.")
        headers = {"Authorization": self.authorization}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "X-Blueprint-Request": "1"})
        request = Request(self.origin + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            # Do not echo headers, credentials or private response content.
            raise RuntimeError(f"Local fictional demo request failed: {method} {path.split('?')[0]} HTTP {error.code}") from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(condition: bool, message: str):
    if not condition:
        raise RuntimeError(message)


def _save_delivery(path: Path, response: dict):
    """The simulated external service saves its inbox and cursor atomically."""
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS inbox (event_id TEXT PRIMARY KEY, document TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), cursor TEXT NOT NULL)")
        for event in response["events"]:
            conn.execute("INSERT OR IGNORE INTO inbox VALUES(?,?)", (event["event_id"], json.dumps(event)))
        conn.execute("INSERT OR REPLACE INTO checkpoint VALUES(1,?)", (response["cursor"],))


def run_demo() -> dict:
    """Exercise inbox → durable record → pending takeover → actual result readback."""
    with tempfile.TemporaryDirectory(prefix="blueprint-fictional-integration-") as temporary:
        root = Path(temporary)
        directory = root / "blueprint-private"
        username = "fictional-owner"
        password = secrets.token_urlsafe(32)
        initialize_library(directory, username, password,
                           company_profile={"name": "虛構整合驗收公司（測試資料）"})
        server = make_server(directory, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_port}"
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            admin = Client(origin, "Basic " + encoded)
            registration = admin.request("POST", PREFIX + "/registry", {
                "name": "虛構客服模擬服務（測試資料）", "version": "0.1.0",
                "record_types": ["thread", "message", "work_item", "artifact", "note", "status"],
                "commands": [{"name": "takeover", "label": "我接手", "input_schema": {
                    "type": "object", "properties": {}, "required": [], "additionalProperties": False}}],
                "resource_refs": [], "allowed_origins": [],
            })
            integration_id = registration["integration"]["id"]
            service = Client(origin, "Bearer " + registration["token"])
            service.request("GET", PREFIX + "/capabilities")

            received = {
                "schema_version": "1.0", "record_type": "message", "external_id": "fictional-message-001",
                "event_id": "fictional-inbox-event-001", "source_revision": 1,
                "source": "fictional-service", "occurred_at": _now(),
                "payload": {"thread_external_id": "fictional-thread-001", "sender_type": "customer",
                            "direction": "inbound", "text": "測試資料：請由同事接手此虛構個案。"},
                "idempotency_key": "fictional-inbox-001",
            }
            saved = service.request("POST", PREFIX + "/records", received)
            duplicate = service.request("POST", PREFIX + "/records", received)
            _check(saved["record_id"] == duplicate["record_id"], "Inbox retry created another record.")
            record_id = saved["record_id"]
            readback = service.request("GET", PREFIX + "/records/" + record_id)["record"]
            _check(readback["payload"] == received["payload"], "Record readback did not preserve the fictional message.")

            queued = admin.request("POST", PREFIX + "/commands", {
                "integration_id": integration_id, "name": "takeover", "payload": {},
                "idempotency_key": "fictional-takeover-001", "record_id": record_id,
                "control_epoch": 1, "input_cursor": "fictional-input-1",
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            })
            command = queued["command"]
            command_id = command["id"]
            _check(command["state"] == "pending", "A queued intention was incorrectly reported as executed.")

            delivery = service.request("GET", PREFIX + "/events?" + urlencode({"limit": 100}))
            _save_delivery(root / "simulated-service.sqlite3", delivery)
            _check(any(event.get("command", {}).get("id") == command_id for event in delivery["events"]),
                   "External service did not receive the pending command.")
            accepted = {
                "schema_version": "1.0", "command_id": command_id,
                "event_id": "fictional-takeover-accepted-001", "idempotency_key": "fictional-accepted-001",
                "status": "accepted", "occurred_at": _now(),
                "payload": {"evidence": "測試資料：模擬服務已持久保存指令。"},
            }
            service.request("POST", PREFIX + "/command-results", accepted)
            completed = {
                **accepted, "event_id": "fictional-takeover-completed-001",
                "idempotency_key": "fictional-completed-001", "status": "completed", "occurred_at": _now(),
                "control_epoch": 2, "input_cursor": "fictional-input-1",
                "payload": {"evidence": "測試資料：模擬服務已切換控制權。", "inflight": []},
            }
            result = service.request("POST", PREFIX + "/command-results", completed)
            final = admin.request("GET", PREFIX + "/commands/" + command_id)["command"]
            _check(final["state"] == "completed", "Actual completed result did not read back.")
            with closing(sqlite3.connect(root / "simulated-service.sqlite3")) as conn, conn:
                cursor = conn.execute("SELECT cursor FROM checkpoint WHERE id=1").fetchone()[0]
            resumed = service.request("GET", PREFIX + "/events?" + urlencode({"cursor": cursor, "limit": 100}))
            _save_delivery(root / "simulated-service.sqlite3", resumed)
            _check(result["command"]["id"] == final["id"], "The result was attached to another command.")
            return {"fictional": True, "transport": "local HTTP", "record_id": record_id,
                    "command_id": command_id, "states": ["pending", "accepted", "completed"],
                    "record_readback": True, "duplicate_event_deduplicated": True,
                    "service_cursor_saved_atomically": True, "command_readback": True,
                    "real_message_sent": False}
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true", help="Run the isolated fictional HTTP example (also the default).")
    parser.parse_args()
    print(json.dumps(run_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
