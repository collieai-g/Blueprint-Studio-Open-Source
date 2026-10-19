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

    def workday_payload(self, **overrides):
        payload = {
            "date": "2026-10-19", "source": "Codex", "source_key": "http-chat-day-001",
            "title": "介面研究工作日", "summary": "PRIVATE-WORKDAY：整理金融網站導航參考",
            "entries": [
                {"type": "outcome", "title": "完成導航比較", "content": "保留三個介面方向"},
                {"type": "decision", "title": "採用簡潔導航", "content": "優先使用清楚文字標籤"},
                {"type": "reference", "title": "研究參考", "url": "https://example.com/workday-reference",
                 "tags": ["工作日參考"]},
                {"type": "todo", "title": "製作導航草圖", "content": "明日確認手機版排列",
                 "due_date": "2026-10-20"},
            ],
        }
        payload.update(overrides)
        return payload

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

    def test_context_handoff_contains_only_selected_sources_without_mutation(self):
        selected = self.create_item(title="選取的介面研究", notes="SELECTED-NOTE：比較導航")
        unselected = self.create_item(title="未選取的私人工作", notes="UNSELECTED-SECRET-DO-NOT-INCLUDE")
        status, _, before = self.json_request("GET", "/api/export")
        self.assertEqual(status, 200)
        status, headers, result = self.json_request("POST", "/api/context", data={
            "item_ids": [selected["id"]], "task": "design"},
            origin=f"http://{self.host}:{self.port}")
        self.assertEqual(status, 200, result)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["task"], "design")
        self.assertIs(result["truncated"], False)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        markdown = result["markdown"]
        self.assertIn(selected["id"], markdown)
        self.assertIn(selected["url"], markdown)
        self.assertIn("SELECTED-NOTE", markdown)
        self.assertNotIn(unselected["id"], markdown)
        self.assertNotIn("UNSELECTED-SECRET", markdown)
        status, _, after = self.json_request("GET", "/api/export")
        self.assertEqual(status, 200)
        before.pop("exported_at")
        after.pop("exported_at")
        self.assertEqual(after, before, "Preparing context must not change records or history")

    def test_context_handoff_enforces_request_protection_and_explicit_selection(self):
        selected = self.create_item(notes="PRIVATE-CONTEXT-SOURCE")
        valid = {"item_ids": [selected["id"]], "task": "general"}
        for options, expected in (
            ({"authenticated": False}, 401),
            ({"write_header": False}, 403),
            ({"origin": "https://other.example"}, 403),
            ({"content_type": "text/plain"}, 415),
        ):
            with self.subTest(protection=options):
                status, _, result = self.json_request("POST", "/api/context", data=valid, **options)
                self.assertEqual(status, expected, result)
                self.assertNotIn("PRIVATE-CONTEXT-SOURCE", json.dumps(result))
        for invalid in (
            {**valid, "include_all": True},
            {"item_ids": [], "task": "general"},
            {"item_ids": [selected["id"]], "task": "unsupported"},
        ):
            with self.subTest(payload=invalid):
                status, _, result = self.json_request("POST", "/api/context", data=invalid)
                self.assertEqual(status, 400, result)
                self.assertNotIn("PRIVATE-CONTEXT-SOURCE", json.dumps(result))
        status, _, result = self.json_request("POST", "/api/context", data={
            "item_ids": [selected["id"], "missing-item"], "task": "learning"})
        self.assertEqual(status, 404, result)
        self.assertNotIn("PRIVATE-CONTEXT-SOURCE", json.dumps(result))

    def test_workspace_filters_sorting_and_active_counts_reach_the_store(self):
        status, _, collection = self.json_request("POST", "/api/collections", data={
            "name": "設計參考", "icon": "web"})
        self.assertEqual(status, 201, collection)
        loose = self.create_item(title="Beta 未整理資料", notes="待加入收藏夾")
        filed = self.create_item(title="Alpha 設計參考", notes="已整理",
                                 collection_id=collection["collection"]["id"])
        archived = self.create_item(title="Archived 過往資料")
        for item, changes in (
            (filed, {"pinned": True}),
            (loose, {"notes": "最近更新的待整理資料"}),
            (archived, {"pinned": True, "archived": True}),
        ):
            status, _, result = self.json_request("PATCH", "/api/items/" + item["id"], data={
                **changes, "expected_version": item["version"]})
            self.assertEqual(status, 200, result)
        for query, expected_ids in (
            ("pinned=1", [filed["id"]]),
            ("pinned=0", [loose["id"]]),
            ("unfiled=1", [loose["id"]]),
            ("unfiled=0&sort=title", [filed["id"], loose["id"]]),
            ("unfiled=1&pinned=1", []),
            ("sort=created", [filed["id"], loose["id"]]),
            ("sort=updated", [loose["id"], filed["id"]]),
        ):
            with self.subTest(query=query):
                status, _, result = self.json_request("GET", "/api/items?" + query)
                self.assertEqual(status, 200, result)
                self.assertEqual(result["total"], len(expected_ids))
                self.assertEqual([item["id"] for item in result["items"]], expected_ids)
        status, _, info = self.json_request("GET", "/api/info")
        self.assertEqual(status, 200)
        self.assertEqual(info["stats"]["pinned"], 1)
        self.assertEqual(info["stats"]["unfiled"], 1)
        for query in ("pinned=true", "pinned=", "unfiled=2", "unfiled=",
                      "sort=random", "sort="):
            with self.subTest(invalid_query=query):
                status, _, result = self.json_request("GET", "/api/items?" + query)
                self.assertEqual(status, 400, result)

    def test_stale_pin_request_cannot_reverse_a_newer_favourite(self):
        item = self.create_item()
        path = "/api/items/" + item["id"]
        status, _, updated = self.json_request("PATCH", path, data={
            "pinned": True, "expected_version": item["version"]})
        self.assertEqual(status, 200, updated)
        self.assertIs(updated["item"]["pinned"], True)
        status, _, result = self.json_request("PATCH", path, data={
            "pinned": False, "expected_version": item["version"]})
        self.assertEqual(status, 409, result)
        status, _, current = self.json_request("GET", path)
        self.assertEqual(status, 200)
        self.assertIs(current["item"]["pinned"], True)
        self.assertEqual(current["item"]["version"], updated["item"]["version"])

    def test_workday_instructions_and_saved_company_routes_require_authentication(self):
        status, _, result = self.json_request("POST", "/api/workdays", data=self.workday_payload())
        self.assertEqual(status, 201, result)
        report_id = result["workday"]["id"]
        for path in ("/api/workday-instructions", "/api/workdays", "/api/workdays/" + report_id,
                     "/api/todos"):
            with self.subTest(path=path):
                status, _, response = self.json_request("GET", path, authenticated=False)
                self.assertEqual(status, 401, response)
                self.assertNotIn("PRIVATE-WORKDAY", json.dumps(response))
        status, headers, instructions = self.json_request("GET", "/api/workday-instructions")
        self.assertEqual(status, 200, instructions)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIsInstance(instructions["instruction"], str)
        self.assertTrue(instructions["instruction"].strip())
        self.assertIsInstance(instructions["format"], dict)
        self.assertIn("source_key", instructions["format"])
        self.assertIn("entries", instructions["format"])

    def test_workday_import_requires_write_protection_and_is_atomic_on_invalid_entry(self):
        payload = self.workday_payload()
        for options, expected in (
            ({"authenticated": False}, 401),
            ({"write_header": False}, 403),
            ({"origin": "https://other.example"}, 403),
            ({"content_type": "text/plain"}, 415),
        ):
            with self.subTest(protection=options):
                status, _, result = self.json_request("POST", "/api/workdays", data=payload, **options)
                self.assertEqual(status, expected, result)
        invalid = self.workday_payload(entries=[
            {"type": "reference", "title": "不能部分保存", "url": "https://example.com/rollback-reference"},
            {"type": "todo", "title": "無效日期", "due_date": "2026-02-30"},
        ])
        status, _, result = self.json_request("POST", "/api/workdays", data=invalid)
        self.assertEqual(status, 400, result)
        status, _, result = self.json_request("POST", "/api/workdays", data={**payload, "overwrite": True})
        self.assertEqual(status, 400, result)
        for path, key in (("/api/workdays", "workdays"), ("/api/todos", "todos"), ("/api/items", "items")):
            status, _, result = self.json_request("GET", path)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["total"], 0)
            self.assertEqual(result[key], [])

    def test_workday_retry_preserves_todo_progress_and_manual_bookmark_after_restart(self):
        bookmark = self.create_item(url="https://example.com/workday-reference", tags=["人工標記"],
                                    notes="請保留原有研究筆記")
        payload = self.workday_payload()
        status, _, imported = self.json_request("POST", "/api/workdays", data=payload)
        self.assertEqual(status, 201, imported)
        self.assertIs(imported["created"], True)
        self.assertIs(imported["duplicate"], False)
        report_id = imported["workday"]["id"]
        reference = next(entry for entry in imported["workday"]["entries"] if entry["type"] == "reference")
        self.assertEqual(reference["library_item_id"], bookmark["id"])
        status, _, todos = self.json_request("GET", "/api/todos")
        self.assertEqual(status, 200, todos)
        self.assertEqual(todos["total"], 1)
        todo = todos["todos"][0]
        self.assertIs(todo["done"], False)
        status, _, completed = self.json_request("PATCH", "/api/todos/" + todo["id"], data={
            "done": True, "expected_version": todo["version"]})
        self.assertEqual(status, 200, completed)
        self.assertIs(completed["todo"]["done"], True)
        self.assertTrue(completed["todo"]["completed_at"])
        self.stop_server()
        self.start_server()
        status, _, duplicate = self.json_request("POST", "/api/workdays", data=payload)
        self.assertEqual(status, 200, duplicate)
        self.assertIs(duplicate["created"], False)
        self.assertIs(duplicate["duplicate"], True)
        self.assertEqual(duplicate["workday"]["id"], report_id)
        saved_todo = next(entry for entry in duplicate["workday"]["entries"] if entry["type"] == "todo")
        self.assertIs(saved_todo["done"], True)
        self.assertEqual(saved_todo["version"], completed["todo"]["version"])
        status, _, conflict = self.json_request("POST", "/api/workdays", data={**payload, "summary": "不能覆寫已保存紀錄"})
        self.assertEqual(status, 409, conflict)
        status, _, report = self.json_request("GET", "/api/workdays/" + report_id)
        self.assertEqual(status, 200, report)
        self.assertEqual(report["workday"]["summary"], payload["summary"])
        status, _, original = self.json_request("GET", "/api/items/" + bookmark["id"])
        self.assertEqual(status, 200, original)
        self.assertEqual(original["item"]["tags"], bookmark["tags"])
        self.assertEqual(original["item"]["notes"], bookmark["notes"])
        self.assertEqual(original["item"]["version"], bookmark["version"])
        status, _, export = self.json_request("GET", "/api/export")
        self.assertEqual(status, 200, export)
        self.assertEqual([entry["id"] for entry in export["workdays"]], [report_id])
        self.assertNotIn(self.password, json.dumps(export))

    def test_workday_chat_keys_date_search_and_todo_queries_are_independent(self):
        for payload in (
            self.workday_payload(),
            self.workday_payload(source_key="another-chat-same-day", title="另一段工作對話", summary="另一段工作內容"),
        ):
            status, _, result = self.json_request("POST", "/api/workdays", data=payload)
            self.assertEqual(status, 201, result)
        status, _, reports = self.json_request("GET", "/api/workdays?date=2026-10-19&q=" + quote("金融網站"))
        self.assertEqual(status, 200, reports)
        self.assertEqual(reports["total"], 1)
        self.assertEqual(reports["workdays"][0]["title"], "介面研究工作日")
        status, _, todos = self.json_request("GET", "/api/todos?done=0&date=2026-10-19&q=" + quote("導航草圖"))
        self.assertEqual(status, 200, todos)
        self.assertEqual(todos["total"], 2)
        for query, expected in (("done=1", 0), ("done=all", 2), ("date=2026-10-18", 0), ("limit=1&offset=1", 2)):
            status, _, result = self.json_request("GET", "/api/todos?" + query)
            self.assertEqual(status, 200, result)
            self.assertEqual(result["total"], expected)
            if query.startswith("limit="):
                self.assertEqual(len(result["todos"]), 1)
        status, _, info = self.json_request("GET", "/api/info")
        self.assertEqual(status, 200, info)
        self.assertEqual(info["stats"]["workdays"], 2)
        self.assertEqual(info["stats"]["todos_open"], 2)
        self.assertEqual(info["stats"]["todos_done"], 0)
        for path in (
            "/api/workdays?date=2026-02-30", "/api/workdays?limit=bad", "/api/workdays?offset=-1",
            "/api/todos?done=true", "/api/todos?done=", "/api/todos?date=2026-13-01", "/api/todos?limit=0",
        ):
            with self.subTest(invalid_filter=path):
                status, _, result = self.json_request("GET", path)
                self.assertEqual(status, 400, result)

    def test_todo_completion_requires_write_protection_and_current_version(self):
        status, _, imported = self.json_request("POST", "/api/workdays", data=self.workday_payload())
        self.assertEqual(status, 201, imported)
        todo = next(entry for entry in imported["workday"]["entries"] if entry["type"] == "todo")
        path = "/api/todos/" + todo["id"]
        update = {"done": True, "expected_version": todo["version"]}
        for options, expected in (
            ({"authenticated": False}, 401),
            ({"write_header": False}, 403),
            ({"origin": "https://other.example"}, 403),
            ({"content_type": "text/plain"}, 415),
        ):
            with self.subTest(protection=options):
                status, _, result = self.json_request("PATCH", path, data=update, **options)
                self.assertEqual(status, expected, result)
        for invalid in ({**update, "title": "不能改寫原始紀錄"}, {**update, "done": "true"}):
            status, _, result = self.json_request("PATCH", path, data=invalid)
            self.assertEqual(status, 400, result)
        status, _, completed = self.json_request("PATCH", path, data=update)
        self.assertEqual(status, 200, completed)
        self.assertIs(completed["todo"]["done"], True)
        status, _, stale = self.json_request("PATCH", path, data={**update, "done": False})
        self.assertEqual(status, 409, stale)
        status, _, info = self.json_request("GET", "/api/info")
        self.assertEqual(status, 200, info)
        self.assertEqual(info["stats"]["todos_open"], 0)
        self.assertEqual(info["stats"]["todos_done"], 1)


if __name__ == "__main__":
    unittest.main()
