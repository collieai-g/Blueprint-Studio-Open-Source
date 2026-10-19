"""Refining a saved workday preserves completed work and recoverable history."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from blueprint_library.store import LibraryError, SCHEMA_VERSION, Store


class WorkdayRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-workday-revisions-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "private-company"
        self.store = Store(self.data)

    def payload(self, **overrides):
        result = {
            "date": "2026-10-19", "source": "Codex", "source_key": "chat-123/day-2026-10-19",
            "title": "今日網站研究", "summary": "整理有用結果，稍後再精簡。",
            "entries": [
                {"type": "outcome", "title": "初步研究", "content": "比較三個介面。", "tags": ["研究"]},
                {"type": "decision", "title": "待確認的方向", "content": "保留兩種導覽方案。"},
                {"type": "reference", "title": "介面參考", "url": "https://example.com/design", "content": "側欄間距", "tags": ["UI"]},
                {"type": "todo", "title": "整理手機版", "content": "檢查文字大小", "due_date": "2026-10-22", "tags": ["設計"]},
            ],
        }
        result.update(overrides)
        return result

    @staticmethod
    def editable_entry(entry, *, with_id=True):
        result = {key: deepcopy(entry[key]) for key in ("type", "title", "content", "url", "tags")}
        if entry["type"] == "todo":
            result["due_date"] = entry["due_date"]
        if with_id:
            result["id"] = entry["id"]
        return result

    def refinement(self, report, **overrides):
        result = {
            "expected_version": report["version"],
            "title": report["title"], "summary": report["summary"],
            "entries": [self.editable_entry(entry) for entry in report["entries"]],
        }
        result.update(overrides)
        return result

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        if isinstance(status, tuple):
            self.assertIn(caught.exception.status, status)
        else:
            self.assertEqual(caught.exception.status, status)

    def stored_metadata(self):
        result = self.store.export_metadata()
        result.pop("exported_at")
        return result

    def test_import_versions_and_complete_history_are_exported_separately(self):
        report = self.store.import_workday(self.payload(), actor="公司管理員")["workday"]
        self.assertEqual(report["version"], 1)
        self.assertNotIn("revisions", report)
        self.assertEqual(self.store.list_workdays()["workdays"][0]["version"], 1)
        history = self.store.item_workday_history(report["id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["version"], 1)
        self.assertEqual(history[0]["action"], "create")
        self.assertEqual(history[0]["actor"], "公司管理員")
        self.assertTrue(history[0]["recorded_at"])
        self.assertEqual(history[0]["snapshot"], report)
        exported = self.store.export_metadata()["workdays"][0]
        self.assertEqual(exported.pop("revisions"), history)
        self.assertEqual(exported, report)
        self.assertNotIn("payload_hash", json.dumps(history))
        history[0]["snapshot"]["entries"][0]["content"] = "不可從讀取結果修改歷史"
        self.assertEqual(self.store.item_workday_history(report["id"])[0]["snapshot"], report)
        self.assert_error(404, lambda: self.store.item_workday_history("missing"))

    def test_refinement_keeps_completed_todo_identity_and_unchanged_entries(self):
        initial = self.store.import_workday(self.payload())["workday"]
        todo = initial["entries"][3]
        completed = self.store.update_todo(todo["id"], {"expected_version": 1, "done": True}, actor="同事甲")
        before = self.store.get_workday(initial["id"])
        self.assertEqual(before["version"], 2)
        self.assert_error(409, lambda: self.store.update_workday(initial["id"], self.refinement(initial)))
        change = self.refinement(before, title="手機介面研究", summary="已完成比較，保留實際結論。")
        change["entries"][3].update(title="補充手機版檢查", content="補上已確認的字級", tags=["手機介面"], due_date="2026-10-23")
        change["entries"].append({"type": "outcome", "title": "新結論", "content": "採用單欄設計"})
        result = self.store.update_workday(before["id"], change, actor="整理者")
        self.assertTrue(result["updated"])
        after = result["workday"]
        self.assertEqual(after["version"], 3)
        self.assertEqual(after["entries"][1], before["entries"][1])
        refined_todo = after["entries"][3]
        self.assertEqual(refined_todo["id"], todo["id"])
        self.assertTrue(refined_todo["done"])
        self.assertEqual(refined_todo["completed_at"], completed["completed_at"])
        self.assertEqual(refined_todo["created_at"], todo["created_at"])
        self.assertEqual(refined_todo["created_by"], todo["created_by"])
        self.assertEqual(refined_todo["version"], 3)
        self.assertEqual(refined_todo["updated_by"], "整理者")
        self.assertEqual(after["entries"][4]["version"], 1)
        self.assertEqual(after["entries"][4]["created_by"], "整理者")
        for key in ("date", "source", "source_key", "created_at", "created_by"):
            self.assertEqual(after[key], initial[key])
        self.assertEqual(self.store.list_workdays(q="手機介面 採用單欄")["workdays"][0]["version"], 3)
        self.assertEqual(self.store.list_todos(q="手機介面", done=True)["total"], 1)
        revisions = self.store.item_workday_history(initial["id"])
        self.assertEqual([revision["version"] for revision in revisions], [1, 2, 3])
        self.assertEqual([revision["action"] for revision in revisions], ["create", "todo", "update"])
        self.assertEqual([revision["snapshot"] for revision in revisions], [initial, before, after])
        reopened = Store(self.data)
        self.assertEqual(reopened.get_workday(after["id"]), after)
        self.assertEqual(reopened.item_workday_history(after["id"]), revisions)

    def test_noop_is_unchanged_and_reordering_only_versions_the_report(self):
        report = self.store.import_workday(self.payload())["workday"]
        before_history = self.store.item_workday_history(report["id"])
        normalized = self.refinement(report)
        normalized["title"] = "  " + normalized["title"] + "  "
        normalized["entries"][0]["tags"] *= 2
        normalized.pop("summary")
        unchanged = self.store.update_workday(report["id"], normalized, actor="其他人")
        self.assertFalse(unchanged["updated"])
        self.assertEqual(unchanged["workday"], report)
        self.assertEqual(self.store.item_workday_history(report["id"]), before_history)
        payload = self.refinement(report)
        payload["entries"].reverse()
        reordered = self.store.update_workday(report["id"], payload)["workday"]
        self.assertEqual(reordered["version"], 2)
        self.assertEqual(reordered["entries"], list(reversed(report["entries"])))
        self.assertEqual(len(self.store.item_workday_history(report["id"])), 2)

    def test_type_changes_preserve_unrelated_completed_todos_by_id(self):
        report = self.store.import_workday(self.payload(entries=[
            {"type": "todo", "title": "保留已完成工作"},
            {"type": "todo", "title": "整理為成果", "due_date": "2026-10-22"},
            {"type": "outcome", "title": "整理為下一步"},
        ]))["workday"]
        for entry in report["entries"][:2]:
            self.store.update_todo(entry["id"], {"expected_version": entry["version"], "done": True})
        before = self.store.get_workday(report["id"])
        change = self.refinement(before)
        change["entries"][1]["type"] = "outcome"
        change["entries"][1].pop("due_date")
        change["entries"][2].update(type="todo", due_date="2026-10-23")
        after = self.store.update_workday(report["id"], change)["workday"]
        self.assertEqual(after["entries"][0], before["entries"][0])
        self.assertEqual(after["entries"][1]["id"], before["entries"][1]["id"])
        self.assertEqual(after["entries"][1]["version"], before["entries"][1]["version"] + 1)
        self.assertFalse(after["entries"][1]["done"])
        self.assertIsNone(after["entries"][1]["completed_at"])
        self.assertEqual(after["entries"][2]["version"], 2)
        self.assertFalse(after["entries"][2]["done"])
        self.assertEqual(self.store.workday_stats(), {"workdays": 1, "todos_open": 1, "todos_done": 1})
        self.assertTrue(self.store.item_workday_history(report["id"])[-2]["snapshot"]["entries"][1]["done"])

    def test_removing_noise_retains_full_history_bookmarks_and_file_bytes(self):
        manual = self.store.create_item({"url": "https://example.com/design", "title": "公司自訂參考", "notes": "人工筆記", "tags": ["私人標記"], "category": "finance"})
        manual = self.store.update_item(manual["id"], {"expected_version": 1, "pinned": True})
        file_item = self.store.add_file("研究原件.txt", "不可遺失的內容".encode())
        report = self.store.import_workday(self.payload())["workday"]
        self.assertEqual(report["entries"][2]["library_item_id"], manual["id"])
        change = self.refinement(report, entries=[self.editable_entry(report["entries"][0])], summary="刪除雜訊，只保留結論。")
        current = self.store.update_workday(report["id"], change)["workday"]
        self.assertEqual(len(current["entries"]), 1)
        self.assertEqual(self.store.list_todos()["total"], 0)
        self.assertEqual(self.store.list_workdays(q="待確認的方向")["total"], 0)
        history = self.store.item_workday_history(report["id"])
        self.assertEqual(history[0]["snapshot"], report)
        self.assertEqual(history[0]["snapshot"]["entries"][2]["content"], "側欄間距")
        self.assertEqual(history[1]["snapshot"], current)
        self.assertEqual(self.store.get_item(manual["id"]), manual)
        self.assertEqual(len(self.store.item_history(manual["id"])), 2)
        self.assertEqual(self.store.get_file(file_item["id"])[0].read_bytes(), "不可遺失的內容".encode())

    def test_added_references_reuse_active_bookmarks_without_overwriting_manual_data(self):
        manual = self.store.create_item({"url": "https://example.com/shared", "title": "保留標題", "notes": "保留筆記", "tags": ["人工"], "category": "finance"})
        archived = self.store.create_item({"url": "https://example.com/archived", "title": "舊收藏"})
        archived = self.store.update_item(archived["id"], {"expected_version": 1, "archived": True})
        report = self.store.import_workday(self.payload(entries=[{"type": "outcome", "title": "研究開始"}]))["workday"]
        change = self.refinement(report)
        change["entries"].extend([
            {"type": "reference", "title": "不同描述", "url": "https://EXAMPLE.com/shared", "content": "AI 新筆記", "tags": ["新標記"]},
            {"type": "reference", "title": "重新收藏", "url": "https://example.com/archived"},
            {"type": "reference", "title": "同一新收藏", "url": "https://example.com/archived"},
        ])
        entries = self.store.update_workday(report["id"], change)["workday"]["entries"]
        self.assertEqual(entries[1]["library_item_id"], manual["id"])
        self.assertEqual(self.store.get_item(manual["id"]), manual)
        self.assertNotEqual(entries[2]["library_item_id"], archived["id"])
        self.assertEqual(entries[2]["library_item_id"], entries[3]["library_item_id"])
        self.assertEqual(self.store.get_item(archived["id"]), archived)
        self.assertEqual(self.store.stats()["links"], 2)

    def test_invalid_ids_and_server_fields_fail_atomically(self):
        report = self.store.import_workday(self.payload())["workday"]
        other = self.store.import_workday(self.payload(source_key="other-chat"))["workday"]
        baseline = self.stored_metadata()
        original = self.refinement(report)
        cases = [(400, {**original, "expected_version": True}), (400, {**original, "expected_version": 0}),
                 (400, {**original, "expected_version": None}), (400, {**original, "entries": []}),
                 (400, {**original, "entries": [original["entries"][0]] * 81})]
        for field, value in (("date", "2026-10-20"), ("source", "Claude"), ("source_key", "changed"), ("version", 1)):
            cases.append((400, {**original, field: value}))
        for entry in ({**original["entries"][0], "id": []}, {**original["entries"][0], "done": True},
                      {**original["entries"][0], "version": 2}, {**original["entries"][0], "created_at": "forged"},
                      {**original["entries"][0], "type": []}, {**original["entries"][0], "tags": [None]},
                      {**original["entries"][0], "content": "x" * 8001},
                      {**original["entries"][2], "url": "javascript:alert(1)"},
                      {**original["entries"][3], "due_date": "2026-02-30"}):
            cases.append((400, {**original, "entries": [entry]}))
        duplicate = deepcopy(original)
        duplicate["entries"][1]["id"] = duplicate["entries"][0]["id"]
        cases.append((400, duplicate))
        for bad_id in ("missing-entry", other["entries"][0]["id"]):
            change = deepcopy(original)
            change["entries"] = [{"type": "reference", "title": "應回滾", "url": "https://example.com/must-not-exist"},
                                 {**change["entries"][0], "id": bad_id}]
            cases.append(((400, 404), change))
        for index, (status, payload) in enumerate(cases):
            with self.subTest(case=index):
                self.assert_error(status, lambda: self.store.update_workday(report["id"], payload))
                self.assertEqual(self.stored_metadata(), baseline)
        self.assert_error(400, lambda: self.store.update_workday(report["id"], original, actor=[]))
        self.assert_error(404, lambda: self.store.update_workday("missing", original))
        self.assertEqual(self.stored_metadata(), baseline)

    def test_competing_refinements_and_old_todo_version_conflict(self):
        report = self.store.import_workday(self.payload())["workday"]
        barrier = threading.Barrier(2)

        def refine(index):
            payload = self.refinement(report, summary=f"修改 {index}")
            payload["entries"][3]["title"] = f"新版待辦 {index}"
            barrier.wait(timeout=5)
            try:
                return self.store.update_workday(report["id"], payload)
            except LibraryError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(refine, range(2)))
        self.assertEqual(sum(result == 409 for result in results), 1)
        current = self.store.get_workday(report["id"])
        self.assertEqual(current["version"], 2)
        self.assertEqual(current["entries"][3]["version"], 2)
        self.assertEqual(len(self.store.item_workday_history(report["id"])), 2)
        self.assert_error(409, lambda: self.store.update_todo(report["entries"][3]["id"], {"expected_version": 1, "done": True}))
        self.assert_error(409, lambda: self.store.update_workday(report["id"], self.refinement(report)))
        self.assertEqual(self.store.get_workday(report["id"]), current)

    def test_edited_content_controls_create_retries_and_preserves_completed_state(self):
        original = self.payload()
        first = self.store.import_workday(original)["workday"]
        self.store.update_todo(first["entries"][3]["id"], {"expected_version": 1, "done": True})
        before = self.store.get_workday(first["id"])
        change = self.refinement(before, title="精簡後研究", summary="只保留有用內容。")
        change["entries"][0]["content"] = "已確認設計方向。"
        current = self.store.update_workday(first["id"], change)["workday"]
        history = self.store.item_workday_history(first["id"])
        self.assert_error(409, lambda: self.store.import_workday(original))
        matching = {key: current[key] for key in ("date", "source", "source_key", "title", "summary")}
        matching["entries"] = [self.editable_entry(entry, with_id=False) for entry in current["entries"]]
        retry = self.store.import_workday(matching, actor="另一個 AI")
        self.assertTrue(retry["duplicate"])
        self.assertFalse(retry["created"])
        self.assertEqual(retry["workday"], current)
        self.assertTrue(retry["workday"]["entries"][3]["done"])
        self.assertEqual(self.store.item_workday_history(first["id"]), history)

    def test_failed_refinement_rolls_back_bookmarks_current_report_and_history(self):
        report = self.store.import_workday(self.payload())["workday"]
        baseline = self.stored_metadata()
        change = self.refinement(report, title="不得局部保存")
        change["entries"] = [{"type": "reference", "title": "新參考一", "url": "https://example.com/new-one"},
                             {"type": "reference", "title": "新參考二", "url": "https://example.com/new-two"}]
        original_insert = self.store._insert
        calls = 0

        def fail_second(conn, item, actor):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise LibraryError("模擬儲存失敗", 500)
            return original_insert(conn, item, actor)

        with patch.object(self.store, "_insert", side_effect=fail_second):
            self.assert_error(500, lambda: self.store.update_workday(report["id"], change))
        self.assertEqual(self.stored_metadata(), baseline)
        self.assertTrue(self.store.update_workday(report["id"], change)["updated"])

    def test_real_v3_schema_migrates_without_losing_files_auth_or_legacy_history(self):
        file_item = self.store.add_file("公司研究.txt", "公司私人原文".encode())
        file_item = self.store.update_item(file_item["id"], {"expected_version": 1, "pinned": True, "tags": ["保留"]})
        report = self.store.import_workday(self.payload())["workday"]
        self.store.update_todo(report["entries"][3]["id"], {"expected_version": 1, "done": True}, actor="舊版使用者")
        legacy = self.store.get_workday(report["id"])
        legacy["version"] = 1
        auth = b'{"username":"private-owner","auth":"preserve-byte-for-byte"}\n'
        (self.data / "access.json").write_bytes(auth)
        # Rebuild the actual old table rather than merely lowering user_version:
        # schema 3 had entry versions but no workday version or revision table.
        with sqlite3.connect(self.store.db_path) as conn:
            conn.row_factory = sqlite3.Row
            old_report = dict(conn.execute("SELECT * FROM workdays WHERE id=?", (report["id"],)).fetchone())
            old_report.pop("version")
            old_entries = [dict(row) for row in conn.execute("SELECT * FROM workday_entries ORDER BY position")]
            previous = {table: [tuple(row) for row in conn.execute("SELECT * FROM " + table)] for table in ("items", "history")}
            entries_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='workday_entries'").fetchone()[0]
            conn.executescript("DROP TABLE workday_revisions; DROP TABLE workday_entries; DROP TABLE workdays; DROP TABLE company_profile;")
            conn.execute("""CREATE TABLE workdays (
                id TEXT PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL,
                source_key TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                payload_hash TEXT NOT NULL, search_text TEXT NOT NULL,
                context_text TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
                UNIQUE(date, source, source_key))""")
            conn.execute(entries_sql)
            for table, rows in (("workdays", [old_report]), ("workday_entries", old_entries)):
                for row in rows:
                    conn.execute("INSERT INTO " + table + "(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", list(row.values()))
            conn.execute("PRAGMA user_version=3")
        for _ in range(2):
            migrated = Store(self.data)
            self.assertEqual(migrated.get_workday(report["id"]), legacy)
            self.assertEqual(migrated.get_item(file_item["id"]), file_item)
            self.assertEqual(migrated.get_file(file_item["id"])[0].read_bytes(), "公司私人原文".encode())
            self.assertEqual((self.data / "access.json").read_bytes(), auth)
            self.assertFalse(migrated.get_company_profile()["configured"])
            with sqlite3.connect(migrated.db_path) as conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                for table, rows in previous.items():
                    self.assertEqual(conn.execute("SELECT * FROM " + table).fetchall(), rows)
        current = migrated.update_workday(report["id"], self.refinement(legacy, summary="新版精簡摘要"), actor="整理者")["workday"]
        history = migrated.item_workday_history(report["id"])
        self.assertEqual([revision["version"] for revision in history], [1, 2])
        self.assertEqual([revision["action"] for revision in history], ["legacy", "update"])
        self.assertEqual(history[0]["snapshot"], legacy)
        self.assertEqual(history[1]["snapshot"], current)
        self.assertTrue(current["entries"][3]["done"])
        self.assertEqual(current["entries"][3]["completed_at"], legacy["entries"][3]["completed_at"])
        self.assertEqual(Store(self.data).item_workday_history(report["id"]), history)


if __name__ == "__main__":
    unittest.main()
