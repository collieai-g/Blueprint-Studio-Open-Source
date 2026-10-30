"""Codex can save a curated day from an external private working directory."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from blueprint_library.server import initialize_library
from blueprint_library.store import Store


CLI = Path(__file__).resolve().parents[1] / "blueprint.py"


class WorkdayCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-workday-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "公司資料"
        self.workspace = self.root / "藍圖工作室"
        self.workspace.mkdir()
        initialize_library(self.data, "test-owner", "temporary-cli-test-password")
        self.payload = {
            "date": "2026-10-19", "source": "Codex", "source_key": "stable-chat-甲:2026-10-19",
            "title": "工作室介面整理", "summary": "整理今日可保留的工作。",
            "entries": [
                {"type": "outcome", "title": "完成線條圖示", "content": "內部私人工作說明不應重複輸出到 CLI 日誌。"},
                {"type": "decision", "title": "使用簡潔線框", "content": "資料夾保持無填色。"},
                {"type": "reference", "title": "介面參考", "url": "https://example.com/ui", "tags": ["UI"]},
                {"type": "todo", "title": "檢查手機版", "due_date": "2026-10-20"},
            ],
        }

    def run_cli(self, *arguments, raw=None, expected=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, arguments)], cwd=self.root,
                                input=raw, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        channel = result.stdout if expected == 0 else result.stderr
        return json.loads(channel), result

    def test_connected_workspace_saves_from_stdin_and_reads_without_echoing_notes(self):
        original = "# 我的公司指引\r\n保留這一行。\r\n".encode()
        (self.workspace / "AGENTS.md").write_bytes(original)
        (self.workspace / "private-project.py").write_bytes(b"private_source = True\n")
        linked, _ = self.run_cli("workday-connect", self.workspace, "--data", self.data)
        self.assertEqual(Path(linked["data_path"]), self.data)
        self.assertTrue((self.workspace / "AGENTS.md").read_bytes().startswith(original))
        self.assertTrue((self.workspace / "WORKDAY.md").is_file())
        saved, result = self.run_cli("workday-save", "--workspace", self.workspace,
                                    raw=json.dumps(self.payload, ensure_ascii=False))
        self.assertTrue(saved["created"])
        self.assertEqual(saved["entry_count"], 4)
        self.assertNotIn("內部私人", result.stdout)
        self.assertNotIn("temporary-cli-test-password", result.stdout + result.stderr)
        listing, _ = self.run_cli("workday-list", "--workspace", self.workspace, "--date", "2026-10-19", "--query", "線條")
        self.assertEqual(listing["total"], 1)
        shown, _ = self.run_cli("workday-show", saved["id"], "--workspace", self.workspace)
        self.assertEqual(shown["workday"]["entries"][0]["title"], "完成線條圖示")
        self.assertEqual((self.workspace / "private-project.py").read_bytes(), b"private_source = True\n")

    def test_retries_preserve_todo_progress_and_conflicts_do_not_create_duplicates(self):
        raw = json.dumps(self.payload, ensure_ascii=False)
        saved, _ = self.run_cli("workday-save", "--data", self.data, raw=raw)
        store = Store(self.data)
        todo = store.list_todos()["todos"][0]
        store.update_todo(todo["id"], {"expected_version": todo["version"], "done": True})
        retried, _ = self.run_cli("workday-save", "--data", self.data, raw=raw)
        self.assertEqual(retried["id"], saved["id"])
        self.assertTrue(retried["duplicate"])
        self.assertFalse(retried["created"])
        self.assertTrue(store.get_workday(saved["id"])["entries"][-1]["done"])
        changed = {**self.payload, "summary": "不同內容不可覆寫。"}
        error, _ = self.run_cli("workday-save", "--data", self.data, raw=json.dumps(changed), expected=2)
        self.assertIn("error", error)
        self.assertEqual(store.workday_stats()["workdays"], 1)
        self.assertEqual(store.get_workday(saved["id"])["summary"], self.payload["summary"])

    def test_bad_and_oversized_json_leave_no_records_then_bom_file_saves(self):
        for raw in ("{", "[]", "null", " " * (512 * 1024 + 1)):
            with self.subTest(raw_size=len(raw)):
                result, _ = self.run_cli("workday-save", "--data", self.data, raw=raw, expected=2)
                self.assertIn("error", result)
        store = Store(self.data)
        self.assertEqual(store.workday_stats()["workdays"], 0)
        self.assertEqual(store.stats()["links"], 0)
        result_file = self.root / "今日整理.json"
        result_file.write_bytes(json.dumps(self.payload, ensure_ascii=False).encode("utf-8-sig"))
        saved, _ = self.run_cli("workday-save", "--data", self.data, "--input", result_file)
        self.assertTrue(saved["created"])

    def test_missing_binding_or_wrong_data_path_does_not_initialize_a_library(self):
        self.run_cli("workday-save", "--workspace", self.workspace, raw="{}", expected=2)
        missing = self.root / "打錯的資料位置"
        self.run_cli("workday-save", "--data", missing, raw="{}", expected=2)
        self.assertFalse(missing.exists())
        self.assertFalse((self.workspace / "library.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
