"""Daily summaries are explicit, bounded, atomic and safe to retry."""

from contextlib import closing
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.store import LibraryError, SCHEMA_VERSION, Store


class WorkdayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-workdays-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "company"
        self.store = Store(self.data)

    def payload(self, **overrides):
        data = {
            "date": "2026-10-19", "source": "Codex", "source_key": "chat-123/day-2026-10-19",
            "title": "網站介面研究", "summary": "整理公司今日工作重點，不包含原始對話。",
            "entries": [
                {"type": "outcome", "title": "完成版面比較", "content": "深藍色配色較適合金融網站。", "tags": ["UI", "研究"]},
                {"type": "decision", "title": "採用簡約導航", "content": "保留兩個核心功能。"},
                {"type": "reference", "title": "介面參考", "url": "https://example.com/design", "content": "參考側欄間距。", "tags": ["UI"]},
                {"type": "todo", "title": "整理手機版設計", "content": "檢查文字可讀性", "due_date": "2026-10-22", "tags": ["待辦"]},
            ],
        }
        data.update(overrides)
        return data

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def test_explicit_daily_import_persists_entries_bookmarks_and_statistics(self):
        result = self.store.import_workday(self.payload(), actor="公司管理員")
        self.assertTrue(result["created"])
        self.assertFalse(result["duplicate"])
        report = result["workday"]
        self.assertEqual(report["date"], "2026-10-19")
        self.assertEqual(report["created_by"], "公司管理員")
        self.assertEqual(len({entry["id"] for entry in report["entries"]}), 4)
        self.assertEqual([entry["type"] for entry in report["entries"]], ["outcome", "decision", "reference", "todo"])
        reference = report["entries"][2]
        saved = self.store.get_item(reference["library_item_id"])
        self.assertEqual(saved["url"], "https://example.com/design")
        self.assertEqual(saved["notes"], "參考側欄間距。")
        self.assertEqual(saved["tags"], ["UI"])
        self.assertIs(report["entries"][3]["done"], False)
        self.assertEqual(report["entries"][3]["version"], 1)
        reopened = Store(self.data)
        self.assertEqual(reopened.get_workday(report["id"]), report)
        self.assertEqual(reopened.workday_stats(), {"workdays": 1, "todos_open": 1, "todos_done": 0})
        for key, value in reopened.workday_stats().items():
            self.assertEqual(reopened.stats()[key], value)
        summary = reopened.list_workdays()["workdays"][0]
        self.assertEqual(summary["counts"], {"outcome": 1, "decision": 1, "reference": 1, "todo": 1})
        self.assertEqual(summary["entry_count"], 4)
        self.assertEqual(summary["open_todos"], 1)
        self.assertNotIn("entries", summary)
        exported = reopened.export_metadata()
        exported_report = dict(exported["workdays"][0])
        revisions = exported_report.pop("revisions")
        self.assertEqual(exported_report, report)
        self.assertEqual(revisions[0]["snapshot"], report)
        self.assertNotIn("payload_hash", json.dumps(exported))
        self.assertFalse(exported["includes_file_bytes"])

    def test_defaults_and_canonical_retries_preserve_completed_todos(self):
        payload = {"date": "2026-02-28", "source_key": "chat-1", "entries": [{"type": "todo", "title": "明日研究"}]}
        first = self.store.import_workday(payload)["workday"]
        self.assertEqual(first["source"], "Codex")
        self.assertEqual(first["title"], "2026-02-28 工作紀錄")
        todo = self.store.update_todo(first["entries"][0]["id"], {"expected_version": 1, "done": True}, actor="同事乙")
        retried = self.store.import_workday({
            "entries": [{"title": "明日研究", "content": "", "tags": [], "url": "", "type": "todo", "due_date": None}],
            "source_key": " chat-1 ", "date": "2026-02-28", "summary": "", "source": "Codex", "title": "",
        }, actor="另一個客戶端")
        self.assertFalse(retried["created"])
        self.assertTrue(retried["duplicate"])
        self.assertEqual(retried["workday"]["id"], first["id"])
        current = retried["workday"]["entries"][0]
        self.assertTrue(current["done"])
        self.assertEqual(current["version"], 2)
        self.assertEqual(current["completed_at"], todo["completed_at"])
        self.assertEqual(retried["workday"]["created_by"], "owner")
        self.assertEqual(retried["workday"]["updated_by"], "同事乙")
        self.assertEqual(self.store.workday_stats(), {"workdays": 1, "todos_open": 0, "todos_done": 1})
        changed = deepcopy(payload)
        changed["entries"][0]["title"] = "不能靜默覆寫"
        self.assert_error(409, lambda: self.store.import_workday(changed))
        self.assertEqual(self.store.get_workday(first["id"])["entries"][0]["title"], "明日研究")
        self.assertEqual(self.store.export_metadata()["workdays"][0]["entries"][0]["version"], 2)

    def test_same_day_supports_separate_sources_and_chats(self):
        one = self.store.import_workday(self.payload())["workday"]
        two = self.store.import_workday(self.payload(source_key="another-chat"))["workday"]
        three = self.store.import_workday(self.payload(source="Claude"))["workday"]
        self.assertEqual(len({one["id"], two["id"], three["id"]}), 3)
        self.assertEqual(self.store.list_workdays(date="2026-10-19")["total"], 3)
        # Shared references do not generate one bookmark for each summary.
        self.assertEqual(self.store.stats()["links"], 1)

    def test_reference_reuse_preserves_manual_data_and_skips_archived_bookmarks(self):
        manual = self.store.create_item({"url": "https://example.com/design", "title": "公司自訂標題", "notes": "保留公司筆記", "tags": ["客戶資料"], "category": "finance"})
        manual = self.store.update_item(manual["id"], {"expected_version": 1, "pinned": True})
        archived = self.store.create_item({"url": "https://example.com/archived", "notes": "封存內容"})
        self.store.update_item(archived["id"], {"expected_version": 1, "archived": True})
        payload = self.payload(entries=[
            {"type": "reference", "title": "新的描述", "url": "https://EXAMPLE.com/design", "content": "不覆蓋私人筆記", "tags": ["新標記"]},
            {"type": "reference", "title": "重新收藏", "url": "https://example.com/archived"},
            {"type": "reference", "title": "同一新連結", "url": "https://example.com/archived"},
            {"type": "reference", "title": "無網址參考筆記", "content": "自己歸納的觀察"},
        ])
        entries = self.store.import_workday(payload)["workday"]["entries"]
        self.assertEqual(entries[0]["library_item_id"], manual["id"])
        self.assertEqual(self.store.get_item(manual["id"]), manual)
        self.assertEqual(len(self.store.item_history(manual["id"])), 2)
        self.assertNotEqual(entries[1]["library_item_id"], archived["id"])
        self.assertEqual(entries[1]["library_item_id"], entries[2]["library_item_id"])
        self.assertIsNone(entries[3]["library_item_id"])
        self.assertEqual(self.store.stats()["links"], 2)

    def test_validation_and_write_failures_roll_back_whole_import(self):
        invalid = self.payload()
        invalid["entries"].append({"type": "reference", "title": "不可儲存", "url": "javascript:alert(1)"})
        self.assert_error(400, lambda: self.store.import_workday(invalid))
        self.assertEqual(self.store.stats()["total"], 0)
        self.assertEqual(self.store.workday_stats()["workdays"], 0)
        self.assert_error(400, lambda: self.store.import_workday(self.payload(), actor=[]))
        original_insert = self.store._insert
        calls = 0

        def fail_second_bookmark(conn, item, actor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise LibraryError("模擬第二項資料寫入失敗", 500)
            return original_insert(conn, item, actor)

        payload = self.payload(entries=[
            {"type": "reference", "title": "第一個參考", "url": "https://example.com/one"},
            {"type": "reference", "title": "第二個參考", "url": "https://example.com/two"},
        ])
        with patch.object(self.store, "_insert", side_effect=fail_second_bookmark):
            self.assert_error(500, lambda: self.store.import_workday(payload))
        self.assertEqual(self.store.stats()["total"], 0)
        self.assertEqual(self.store.workday_stats()["workdays"], 0)
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM history").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM workday_entries").fetchone()[0], 0)
        self.assertTrue(self.store.import_workday(payload)["created"])

    def test_concurrent_identical_imports_create_one_report_and_bookmark(self):
        barrier = threading.Barrier(2)

        def import_once(_):
            barrier.wait(timeout=5)
            return self.store.import_workday(self.payload())

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(import_once, range(2)))
        self.assertEqual(sum(result["created"] for result in results), 1)
        self.assertEqual(sum(result["duplicate"] for result in results), 1)
        self.assertEqual(results[0]["workday"], results[1]["workday"])
        self.assertEqual(self.store.workday_stats()["workdays"], 1)
        self.assertEqual(self.store.stats()["links"], 1)

    def test_concurrent_changed_import_conflicts_without_partial_data(self):
        barrier = threading.Barrier(2)

        def import_once(index):
            barrier.wait(timeout=5)
            try:
                return self.store.import_workday(self.payload(summary=str(index)))
            except LibraryError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(import_once, range(2)))
        self.assertEqual(sum(result == 409 for result in results), 1)
        self.assertEqual(self.store.workday_stats()["workdays"], 1)
        self.assertEqual(self.store.stats()["links"], 1)

    def test_todo_transitions_context_versions_and_immutable_entries(self):
        workday = self.store.import_workday(self.payload())["workday"]
        todo = workday["entries"][3]
        complete = self.store.update_todo(todo["id"], {"expected_version": 1, "done": True}, actor="同事甲")
        self.assertTrue(complete["done"])
        self.assertEqual(complete["version"], 2)
        self.assertEqual(complete["workday_id"], workday["id"])
        self.assertEqual(complete["workday_title"], workday["title"])
        self.assertEqual(complete["date"], "2026-10-19")
        self.assertEqual(complete["source"], "Codex")
        self.assertEqual(complete["updated_by"], "同事甲")
        self.assertIsNotNone(complete["completed_at"])
        self.assert_error(409, lambda: self.store.update_todo(todo["id"], {"expected_version": 1, "done": False}))
        self.assertEqual(self.store.list_todos(done=True)["total"], 1)
        self.assertEqual(self.store.list_todos(done=False)["total"], 0)
        still_done = self.store.update_todo(todo["id"], {"expected_version": 2, "done": True})
        self.assertEqual(still_done["completed_at"], complete["completed_at"])
        reopened = self.store.update_todo(todo["id"], {"expected_version": 3, "done": False})
        self.assertFalse(reopened["done"])
        self.assertIsNone(reopened["completed_at"])
        self.assertEqual(reopened["due_date"], todo["due_date"])
        self.assert_error(400, lambda: self.store.update_todo(workday["entries"][0]["id"], {"expected_version": 1, "done": True}))
        self.assert_error(404, lambda: self.store.update_todo("missing", {"expected_version": 1, "done": True}))
        self.assert_error(404, lambda: self.store.get_workday("missing"))
        for payload in ({"expected_version": True, "done": True}, {"expected_version": 4, "done": 1},
                        {"expected_version": 4}, {"expected_version": 4, "done": True, "title": "不能修改標題"}):
            self.assert_error(400, lambda: self.store.update_todo(todo["id"], payload))

    def test_date_source_unicode_search_pagination_and_todo_filters(self):
        first = self.store.import_workday(self.payload())["workday"]
        self.store.import_workday(self.payload(date="2026-10-20", source="Gemini", source_key="gemini-chat", title="資料搜集",
                                             entries=[{"type": "todo", "title": "研究配色", "content": "品牌海藍色"}]))
        self.assertEqual(self.store.list_workdays(q="深藍色")["total"], 1)
        self.assertEqual(self.store.list_workdays(q="Gemini")["total"], 1)
        self.assertEqual(self.store.list_workdays(q="Codex 手機版")["total"], 1)
        self.assertEqual(self.store.list_workdays(date="2026-10-19")["workdays"][0]["id"], first["id"])
        self.assertEqual(self.store.list_workdays(limit=1, offset=1)["total"], 2)
        self.assertEqual(self.store.list_workdays(limit=1, offset=1)["workdays"][0]["id"], first["id"])
        self.assertEqual(self.store.list_todos(q="GEMINI 海藍色")["total"], 1)
        self.assertEqual(self.store.list_todos(date="2026-10-19")["total"], 1)
        self.assertEqual(self.store.list_todos(q="深藍色")["total"], 0, "Unrelated outcome text must not match todos")
        self.assertEqual(self.store.list_todos(q="' OR 1=1 --")["total"], 0)
        self.assertEqual(self.store.list_workdays(q="' OR 1=1 --")["total"], 0)
        self.assertEqual(len(self.store.list_todos(limit=1, offset=1)["todos"]), 1)

    def test_strict_payload_bounds_dates_and_server_owned_fields(self):
        base = self.payload()
        invalid = [None, [], {**base, "messages": []}, {**base, "date": "2026-02-30"},
                   {**base, "date": "2026-2-01"}, {**base, "date": None}, {**base, "source_key": ""},
                   {**base, "source": []}, {**base, "summary": "x" * 8001}, {**base, "source_key": "x" * 201},
                   {**base, "entries": []}, {**base, "entries": [base["entries"][0]] * 81}]
        entry_cases = [None, {"type": "unknown", "title": "x"}, {"type": [], "title": "x"},
                       {"type": "todo", "title": ""}, {"type": "todo", "title": "x", "done": True},
                       {"type": "todo", "title": "x", "version": 2}, {"type": "todo", "title": "x", "due_date": "2026-02-29"},
                       {"type": "outcome", "title": "x", "due_date": None}, {"type": "todo", "title": "x", "tags": ["x"] * 17},
                       {"type": "reference", "title": "x", "url": "https://user:password@example.com"},
                       {"type": "reference", "title": "x", "url": "file:///etc/passwd"},
                       {"type": "reference", "title": "x", "content": "x" * 8001}, {"type": "todo", "title": "x", "tags": [None]}]
        invalid.extend({**base, "entries": [entry]} for entry in entry_cases)
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_error(400, lambda: self.store.import_workday(payload))
        oversized = self.payload(entries=[{"type": "outcome", "title": str(index), "content": "x" * 8000} for index in range(26)])
        self.assert_error(413, lambda: self.store.import_workday(oversized))
        self.assertEqual(self.store.workday_stats()["workdays"], 0)
        for callback in (lambda: self.store.list_workdays(limit=True), lambda: self.store.list_workdays(date="invalid"),
                         lambda: self.store.list_workdays(q=[]), lambda: self.store.list_workdays(offset=-1),
                         lambda: self.store.list_todos(done=1), lambda: self.store.list_todos(date=None),
                         lambda: self.store.list_todos(limit=201)):
            self.assert_error(400, callback)

    def test_v2_migration_preserves_files_auth_labels_ids_history_and_pins(self):
        item = self.store.add_file("現有研究.txt", "公司私人文件".encode())
        item = self.store.update_item(item["id"], {"expected_version": 1, "pinned": True, "category": "finance", "tags": ["保留標記"]})
        auth = b'{"username":"existing-owner","private-auth":"keep-bytes"}\n'
        (self.data / "access.json").write_bytes(auth)
        # Remove only the new v3 tables/index and mark the actual prior v2
        # schema, leaving realistic records, files and history in place.
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            conn.executescript("DROP TABLE workday_entries; DROP TABLE workdays; DROP INDEX items_active_url; PRAGMA user_version=2;")
            old_items = conn.execute("SELECT * FROM items").fetchall()
            old_history = conn.execute("SELECT * FROM history ORDER BY version").fetchall()
        for _ in range(2):
            migrated = Store(self.data)
            self.assertEqual(migrated.get_item(item["id"]), item)
            self.assertEqual(migrated.get_file(item["id"])[0].read_bytes(), "公司私人文件".encode())
            self.assertEqual((self.data / "access.json").read_bytes(), auth)
            self.assertEqual(migrated.workday_stats(), {"workdays": 0, "todos_open": 0, "todos_done": 0})
            with closing(sqlite3.connect(migrated.db_path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(conn.execute("SELECT * FROM items").fetchall(), old_items)
                self.assertEqual(conn.execute("SELECT * FROM history ORDER BY version").fetchall(), old_history)
        report = migrated.import_workday(self.payload())["workday"]
        self.assertEqual(Store(self.data).get_workday(report["id"]), report)
        self.assertTrue(migrated.get_item(item["id"])["pinned"])


if __name__ == "__main__":
    unittest.main()
