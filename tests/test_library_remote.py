"""Cloud bridge exercises real authenticated HTTP and safe failure behavior."""

from argparse import Namespace
import base64
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

from blueprint_library.commands import run_workday_command
from blueprint_library.integration import connect_workday
from blueprint_library.remote import WorkdayRemote, backend_url, environment_name
from blueprint_library.store import LibraryError


class _APIHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _handle(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.received.append({"method": self.command, "path": self.path,
                                     "headers": dict(self.headers), "body": body})
        status = 200
        content_type = "application/json; charset=utf-8"
        headers = {}
        if self.server.response_override is not None:
            status, content_type, headers, response = self.server.response_override
        elif self.headers.get("Authorization") != self.server.authorization:
            status, response = 401, {"error": "PRIVATE-UPSTREAM-BODY"}
        elif self.command in {"POST", "PATCH"} and self.headers.get("X-Blueprint-Request") != "1":
            status, response = 403, {"error": "PRIVATE-UPSTREAM-BODY"}
        elif self.path.startswith("/api/workdays/record-1/history"):
            response = {"versions": [{"version": 1, "snapshot": self.server.report}]}
        elif self.command == "PATCH":
            response = {"workday": self.server.report, "updated": True}
        elif self.command == "POST":
            status = 201
            response = {"workday": self.server.report, "created": True, "duplicate": False}
        elif self.path.startswith("/api/workdays/record-1"):
            response = {"workday": self.server.report}
        else:
            response = {"workdays": [self.server.report], "total": 1}
        raw = response if isinstance(response, bytes) else json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle


class WorkdayRemoteTests(unittest.TestCase):
    def setUp(self):
        self.username = "private-user-value"
        self.password = "private-password-value"
        self.environment = patch.dict(os.environ, {
            "BRIDGE_TEST_USER": self.username, "BRIDGE_TEST_PASS": self.password,
            "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _APIHandler)
        self.server.received = []
        self.server.response_override = None
        self.server.authorization = "Basic " + base64.b64encode(
            (self.username + ":" + self.password).encode()).decode()
        self.server.report = {"id": "record-1", "date": "2026-10-19", "source": "Claude Code",
                              "source_key": "stable-chat:2026-10-19", "title": "Useful work", "summary": "PRIVATE-SOURCE-TEXT",
                              "version": 1, "entries": [{"id": "entry-1", "type": "todo", "title": "Next step", "done": False}]}
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.close_server)
        self.binding = {"mode": "cloud", "url": "http://127.0.0.1:" + str(self.server.server_port),
                        "username_env": "BRIDGE_TEST_USER", "password_env": "BRIDGE_TEST_PASS"}
        self.remote = WorkdayRemote(self.binding)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)
        for secret in (self.username, self.password, "PRIVATE-SOURCE-TEXT", "PRIVATE-UPSTREAM-BODY"):
            self.assertNotIn(secret, str(caught.exception))
        return str(caught.exception)

    def test_authenticated_save_list_show_revise_history_use_exact_routes_and_json(self):
        payload = {"date": "2026-10-19", "source": "Gemini CLI", "source_key": "chat-day",
                   "title": "Useful work", "summary": "PRIVATE-SOURCE-TEXT", "entries": [{"type": "todo", "title": "Next step"}]}
        saved = self.remote.save(payload)
        self.assertTrue(saved["created"])
        self.assertEqual(self.remote.list(q="中文 & details", date="2026-10-19")["total"], 1)
        self.assertEqual(self.remote.show("record-1")["workday"]["id"], "record-1")
        revised = {"expected_version": 1, "entries": [{"id": "entry-1", "type": "todo", "title": "Next step"}]}
        self.assertTrue(self.remote.revise("record-1", revised)["updated"])
        self.assertEqual(len(self.remote.history("record-1")["versions"]), 1)
        requests = self.server.received
        self.assertEqual([request["method"] for request in requests], ["POST", "GET", "GET", "PATCH", "GET"])
        self.assertEqual(json.loads(requests[0]["body"]), payload)
        self.assertEqual(json.loads(requests[3]["body"]), revised)
        self.assertEqual(parse_qs(urlsplit(requests[1]["path"]).query), {"q": ["中文 & details"], "date": ["2026-10-19"]})
        self.assertEqual(requests[4]["path"], "/api/workdays/record-1/history")
        for request in requests:
            self.assertEqual(request["headers"]["Authorization"], self.server.authorization)
            self.assertEqual(request["headers"]["X-Blueprint-Request"], "1")
        self.assertEqual(requests[0]["headers"]["Content-Type"], "application/json")

    def test_commands_dispatch_cloud_without_creating_any_local_database(self):
        with tempfile.TemporaryDirectory(prefix="blueprint-remote-command-") as temporary:
            workspace = Path(temporary) / "private-workspace"
            connect_workday(workspace, url=self.binding["url"], username_env="BRIDGE_TEST_USER",
                            password_env="BRIDGE_TEST_PASS")
            args = Namespace(command="workday-save", workspace=workspace, data=None, input="-")
            output, errors = io.StringIO(), io.StringIO()
            with patch("sys.stdin", io.StringIO('{"summary":"PRIVATE-SOURCE-TEXT"}')), redirect_stdout(output), redirect_stderr(errors):
                saved = run_workday_command(args)
            self.assertEqual(saved, {"id": "record-1", "date": "2026-10-19", "title": "Useful work",
                                     "entry_count": 1, "created": True, "duplicate": False})
            for command in ("workday-list", "workday-show", "workday-history"):
                result = run_workday_command(Namespace(command=command, workspace=workspace, data=None,
                                                       query="", date="2026-10-19", id="record-1"))
                self.assertIsInstance(result, dict)
            args.command, args.id = "workday-revise", "record-1"
            with patch("sys.stdin", io.StringIO('{"expected_version":1,"entries":[]}')):
                self.assertTrue(run_workday_command(args)["updated"])
            self.assertEqual(output.getvalue(), "")
            self.assertEqual(errors.getvalue(), "")
            self.assertNotIn("PRIVATE-SOURCE-TEXT", json.dumps(saved))
            self.assertFalse(any(workspace.rglob("*.sqlite3")))

    def test_real_backend_roundtrip_preserves_completed_todo_and_revision_history(self):
        from blueprint_library.server import initialize_library, make_server
        from blueprint_library.store import Store
        with tempfile.TemporaryDirectory(prefix="blueprint-remote-live-backend-") as temporary:
            data = Path(temporary) / "company-data"
            initialize_library(data, self.username, self.password)
            backend = make_server(data, host="127.0.0.1", port=0)
            thread = threading.Thread(target=backend.serve_forever, daemon=True)
            thread.start()
            try:
                client = WorkdayRemote({**self.binding, "url": "http://127.0.0.1:" + str(backend.server_port)})
                created = client.save({"date": "2026-10-19", "source": "Gemini CLI", "source_key": "actual-chat:2026-10-19",
                                       "title": "Actual bridge result", "entries": [{"type": "todo", "title": "Review design"}]})
                report = created["workday"]
                self.assertEqual(client.list(date="2026-10-19")["total"], 1)
                todo = report["entries"][0]
                Store(data).update_todo(todo["id"], {"expected_version": todo["version"], "done": True})
                current = client.show(report["id"])["workday"]
                revised = client.revise(report["id"], {"expected_version": current["version"],
                    "summary": "More useful information", "entries": [
                        {"id": todo["id"], "type": "todo", "title": "Review design"},
                        {"type": "decision", "title": "Keep the navigation simple"},
                    ]})
                self.assertTrue(revised["updated"])
                kept = next(entry for entry in revised["workday"]["entries"] if entry["id"] == todo["id"])
                self.assertTrue(kept["done"])
                self.assertGreaterEqual(len(client.history(report["id"])["versions"]), 2)
            finally:
                backend.shutdown()
                backend.server_close()

    def test_missing_or_invalid_credentials_do_not_send_requests_or_echo_values(self):
        with patch.dict(os.environ, {"BRIDGE_TEST_PASS": ""}):
            message = self.assert_error(400, lambda: self.remote.list())
        self.assertIn("BRIDGE_TEST_USER", message)
        self.assertIn("BRIDGE_TEST_PASS", message)
        with patch.dict(os.environ, {"BRIDGE_TEST_USER": "private-user-value:bad"}):
            self.assert_error(400, lambda: self.remote.show("record-1"))
        self.assertEqual(self.server.received, [])

    def test_redirects_are_rejected_and_never_forward_authorization_or_private_body(self):
        destination = ThreadingHTTPServer(("127.0.0.1", 0), _APIHandler)
        destination.received = []
        thread = threading.Thread(target=destination.serve_forever, daemon=True)
        thread.start()
        try:
            for status in (301, 302, 303, 307, 308):
                self.server.response_override = (status, "application/json", {
                    "Location": "http://127.0.0.1:" + str(destination.server_port) + "/stolen"}, {"secret": "PRIVATE-UPSTREAM-BODY"})
                self.assert_error(502, lambda: self.remote.save({"summary": "PRIVATE-SOURCE-TEXT"}))
            self.assertEqual(destination.received, [])
        finally:
            destination.shutdown()
            destination.server_close()

    def test_http_errors_never_echo_upstream_body_and_keep_conflict_status(self):
        for status in (400, 401, 403, 404, 409, 413, 429, 500, 503):
            self.server.response_override = (status, "application/json", {}, {
                "error": self.password + " PRIVATE-UPSTREAM-BODY PRIVATE-SOURCE-TEXT"})
            with self.subTest(status=status):
                self.assert_error(status if status < 500 else 502, lambda: self.remote.save({}))

    def test_transport_errors_timeouts_invalid_json_and_size_limits_are_bounded(self):
        for error in (TimeoutError(self.password), URLError(self.password), OSError(self.password)):
            with patch("blueprint_library.remote.build_opener") as opener:
                opener.return_value.open.side_effect = error
                self.assert_error(502, lambda: self.remote.show("record-1"))
                self.assertEqual(opener.return_value.open.call_args.kwargs["timeout"], 20)
        for content_type, body in (("text/html", b"PRIVATE-UPSTREAM-BODY"),
                                   ("application/json", b"not-json PRIVATE-UPSTREAM-BODY"),
                                   ("application/json", b"[]")):
            self.server.response_override = (200, content_type, {}, body)
            self.assert_error(502, lambda: self.remote.list())
        self.server.response_override = (200, "application/json", {}, b'{"data":"' + b"x" * 200 + b'"}')
        with patch("blueprint_library.remote.MAX_RESPONSE_BYTES", 100):
            self.assert_error(502, lambda: self.remote.list())
        before = len(self.server.received)
        with patch("blueprint_library.remote.MAX_REQUEST_BYTES", 100):
            self.assert_error(413, lambda: self.remote.save({"data": "x" * 101}))
        self.assertEqual(len(self.server.received), before)

    def test_invalid_query_or_id_cannot_change_the_api_route(self):
        for workday_id in ("../private", "id?secret=1", "id/history", "", "a" * 101):
            self.assert_error(400, lambda: self.remote.show(workday_id))
            self.assert_error(400, lambda: self.remote.history(workday_id))
        for date in ("2026-02-30", "20261019", "2026-1-1", None):
            self.assert_error(400, lambda: self.remote.list(date=date))
        self.assertEqual(self.server.received, [])

    def test_url_origin_and_environment_validation(self):
        for value, expected in (("https://EXAMPLE.com/", "https://example.com"),
                                ("http://127.0.0.1:8787/", "http://127.0.0.1:8787"),
                                ("http://localhost", "http://localhost"),
                                ("http://[::1]:8787", "http://[::1]:8787")):
            self.assertEqual(backend_url(value), expected)
        for value in ("http://example.com", "http://127.0.0.2", "http://127.1", "http://localhost.",
                      "https://user:private-password-value@example.com", "https://example.com/prefix",
                      "https://example.com/?", "https://example.com/#", "https://example.com?key=value",
                      "file:///private", "https://example.com\\evil", "https://example.com\n", None):
            self.assert_error(400, lambda: backend_url(value))
        self.assertEqual(environment_name("_TEST_123"), "_TEST_123")
        for value in ("", "1TEST", "TEST=secret", "A" * 101, None):
            self.assert_error(400, lambda: environment_name(value))


if __name__ == "__main__":
    unittest.main()
