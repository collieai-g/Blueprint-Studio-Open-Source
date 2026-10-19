"""HTTP boundary checks for the private, persistent company library.

All credentials, records and uploaded bytes live in temporary directories outside
the public checkout.  No network service or third-party package is required.
"""

from __future__ import annotations

import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import quote

from blueprint_library.server import initialize_library, make_server


class LibraryHTTPTests(unittest.TestCase):
    username = "company-owner"
    password = "Local-only-test-passphrase-2026"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blueprint-private-http-")
        self.data_dir = Path(self.temporary.name) / "company-data"
        initialize_library(self.data_dir, self.username, self.password)
        self.server = None
        self.thread = None
        self.start_server()

    def tearDown(self):
        self.stop_server()
        self.temporary.cleanup()

    def start_server(self):
        self.server = make_server(self.data_dir, host="127.0.0.1", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.assertFalse(self.thread.is_alive(), "HTTP server did not stop")
            self.server = None

    def request(self, method, path, *, data=None, raw=None, authenticated=True,
                password=None, write_header=True, content_type="application/json",
                origin=None):
        headers = {}
        if authenticated:
            credentials = f"{self.username}:{password or self.password}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
        if write_header:
            headers["X-Blueprint-Request"] = "1"
        if origin is not None:
            headers["Origin"] = origin
        body = raw
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if body is not None and content_type is not None:
            headers["Content-Type"] = content_type
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def json_request(self, method, path, **kwargs):
        status, headers, payload = self.request(method, path, **kwargs)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        return status, headers, json.loads(payload.decode("utf-8"))

    def create_item(self, **overrides):
        data = {"title": "金融網站 UI 參考", "url": "https://example.com/design",
                "notes": "公司私人研究筆記：簡約介面及清晰導航"}
        data.update(overrides)
        status, _, result = self.json_request("POST", "/api/items", data=data)
        self.assertEqual(status, 201, result)
        self.assertTrue(result["item"]["id"])
        return result["item"]

    def test_health_is_public_but_company_routes_require_authentication(self):
        self.create_item()
        status, _, health = self.json_request("GET", "/health", authenticated=False)
        self.assertEqual(status, 200)
        self.assertEqual(health["status"], "ok")
        self.assertNotIn("私人研究", json.dumps(health, ensure_ascii=False))
        self.assertNotIn(self.password, json.dumps(health))
        for path in ("/", "/api/info", "/api/items", "/api/export"):
            with self.subTest(path=path):
                status, headers, payload = self.request("GET", path, authenticated=False)
                self.assertEqual(status, 401)
                self.assertIn("Basic", headers.get("WWW-Authenticate", ""))
                self.assertNotIn("私人研究".encode(), payload)
                self.assertNotIn(self.password.encode(), payload)
        status, _, _ = self.request("GET", "/api/info", password="wrong-passphrase")
        self.assertEqual(status, 401)
        status, _, _ = self.json_request("GET", "/api/info")
        self.assertEqual(status, 200)

    def test_writes_require_json_request_marker_and_matching_origin(self):
        item = {"title": "資料測試", "url": "https://example.com/reference",
                "notes": "只能由同源介面寫入"}
        status, _, _ = self.json_request("POST", "/api/items", data=item,
                                         write_header=False)
        self.assertEqual(status, 403)
        status, _, _ = self.json_request("POST", "/api/items", data=item,
                                         origin="https://untrusted.example")
        self.assertEqual(status, 403)
        status, _, _ = self.json_request("POST", "/api/items", data=item,
                                         content_type="text/plain")
        self.assertIn(status, (400, 415))
        status, _, listing = self.json_request("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(listing["total"], 0)
        for query in ("limit=not-a-number", "offset=-1", "limit=201"):
            with self.subTest(query=query):
                status, _, _ = self.json_request("GET", "/api/items?" + query)
                self.assertEqual(status, 400)
        status, _, result = self.json_request("POST", "/api/items", data=item,
                                              origin=f"http://{self.host}:{self.port}")
        self.assertEqual(status, 201, result)

    def test_invalid_json_and_unsafe_urls_are_rejected_without_records(self):
        for invalid in (b"{", b"[]", b"null"):
            with self.subTest(raw=invalid):
                status, _, _ = self.json_request("POST", "/api/items", raw=invalid)
                self.assertEqual(status, 400)
        status, _, _ = self.json_request("POST", "/api/items", data={
            "title": "unsafe", "url": "javascript:alert(1)"})
        self.assertEqual(status, 400)
        status, _, listing = self.json_request("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(listing["total"], 0)

    def test_unicode_search_and_records_survive_server_restart(self):
        item = self.create_item()
        self.create_item(title="設計字體參考", url="https://example.com/fonts",
                         notes="字體及間距資料")
        self.stop_server()
        self.start_server()
        status, _, result = self.json_request("GET", "/api/items?q=" + quote("簡約介面"))
        self.assertEqual(status, 200)
        self.assertEqual(result["total"], 1)
        self.assertEqual([entry["id"] for entry in result["items"]], [item["id"]])
        self.assertIn("私人研究", result["items"][0]["notes"])

    def test_stale_edits_cannot_overwrite_another_update(self):
        item = self.create_item()
        path = "/api/items/" + quote(item["id"], safe="")
        status, _, changed = self.json_request("PATCH", path, data={
            "title": "已確認的設計方向", "expected_version": item["version"]})
        self.assertEqual(status, 200, changed)
        self.assertGreater(changed["item"]["version"], item["version"])
        status, _, _ = self.json_request("PATCH", path, data={
            "title": "過期修改不應生效", "expected_version": item["version"]})
        self.assertEqual(status, 409)
        status, _, listing = self.json_request("GET", "/api/items")
        self.assertEqual(status, 200)
        self.assertEqual(listing["items"][0]["title"], "已確認的設計方向")

    def test_uploaded_bytes_need_authentication_and_download_as_attachment(self):
        contents = b"\x00company-private-artifact\xff\n" + "設計資料".encode("utf-8")
        encoded = base64.b64encode(contents).decode("ascii")
        status, _, result = self.json_request("POST", "/api/files", data={
            "name": "設計參考.bin", "mime": "application/octet-stream",
            "content_base64": encoded})
        self.assertEqual(status, 201, result)
        path = "/api/files/" + quote(result["item"]["id"], safe="")
        status, _, payload = self.request("GET", path, authenticated=False)
        self.assertEqual(status, 401)
        self.assertNotIn(contents, payload)
        status, headers, payload = self.request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(payload, contents)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        status, _, export = self.request("GET", "/api/export")
        self.assertEqual(status, 200)
        self.assertNotIn(encoded.encode(), export, "Metadata export must omit file bytes")

    def test_export_and_static_routes_do_not_expose_private_storage_or_credentials(self):
        item = self.create_item()
        status, _, payload = self.request("GET", "/api/export")
        self.assertEqual(status, 200)
        self.assertIn(item["id"].encode(), payload)
        self.assertNotIn(self.password.encode(), payload)
        self.assertNotIn(b"Authorization", payload)
        self.assertNotIn(b"password_hash", payload)
        self.assertNotIn(str(self.data_dir).encode(), payload)
        credential = json.loads((self.data_dir / "access.json").read_text(encoding="utf-8"))
        self.assertNotIn(credential["password_hash"].encode(), payload)
        for path in ("/missing-static-file.js", "/access.json", "/library.sqlite3",
                     "/../access.json", "/%2e%2e/access.json", "/.blueprint/auth.db"):
            with self.subTest(path=path):
                status, _, response = self.request("GET", path)
                self.assertIn(status, (400, 404))
                self.assertNotIn(b"SQLite format 3", response)
                self.assertNotIn(self.password.encode(), response)
                self.assertNotIn("私人研究".encode(), response)
        self.assertFalse(self.data_dir.resolve().is_relative_to(
            Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    unittest.main()
