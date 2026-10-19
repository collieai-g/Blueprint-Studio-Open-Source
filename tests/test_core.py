"""Filesystem-level checks for the portable, Codex-readable folder format.

Run from the repository root with ``python -m unittest discover -s tests -v``.
Only the standard library is required; all writes stay in temporary directories.
"""

from __future__ import annotations

import csv
import io
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import tempfile
import unittest

from blueprint_folders.core import (
    FolderError,
    create_folder,
    init_studio,
    mark_folder,
    reindex,
    search_folders,
)


def _create_in_worker(arguments):
    """A top-level worker also runs under Windows' spawn process model."""
    root, number = arguments
    return create_folder(root, f"專員-{number:02d}", tags=["parallel"])["id"]


class FolderCoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blueprint-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "工作室"
        init_studio(self.root)

    def metadata(self, folder):
        return json.loads((folder / ".blueprint" / "folder.json").read_text(encoding="utf-8"))

    def index(self):
        return json.loads((self.root / ".blueprint" / "index.json").read_text(encoding="utf-8"))["folders"]

    def test_blank_folder_has_portable_identity_without_assigned_purpose(self):
        result = create_folder(self.root, "未命名專員")
        folder = Path(result["path"])
        data = self.metadata(folder)
        self.assertEqual(data["description"], "")
        self.assertEqual(data["category"], "general")
        self.assertEqual(data["tags"], [])
        self.assertTrue(data["id"])
        self.assertIn(".blueprint/folder.json", (folder / "AGENTS.md").read_text(encoding="utf-8"))
        self.assertFalse((folder / "desktop.ini").exists())
        self.assertFalse((folder / ".directory").exists())
        self.assertEqual(search_folders(self.root)[0]["id"], data["id"])

    def test_all_eight_icon_choices_are_portable_and_distinct(self):
        icons = set()
        for category in ("general", "chat", "web", "finance", "document", "calendar", "automation", "research"):
            with self.subTest(category=category):
                result = create_folder(self.root, category, category=category)
                folder = Path(result["path"])
                data = self.metadata(folder)
                self.assertEqual(data["category"], category)
                self.assertEqual(data["icon"]["platform"], "portable")
                self.assertFalse(data["icon"]["applied"])
                png = (folder / data["icon"]["icon"]).read_bytes()
                self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
                self.assertIn("<svg", (folder / ".blueprint" / "icon.svg").read_text(encoding="utf-8"))
                icons.add(png)
        self.assertEqual(len(icons), 8)

    def test_create_collision_preserves_existing_unmarked_folder(self):
        folder = self.root / "現有內容"
        folder.mkdir()
        original = b"original project contents\x00"
        (folder / "project.bin").write_bytes(original)
        with self.assertRaises(FolderError):
            create_folder(self.root, folder.name)
        self.assertEqual((folder / "project.bin").read_bytes(), original)
        self.assertFalse((folder / ".blueprint").exists())
        self.assertEqual(self.index(), [])

    def test_unsafe_and_windows_reserved_names_are_rejected(self):
        invalid = ("", ".", "..", "../escape", "a/b", "a\\b", "/absolute", " CON", "CON", "con.txt", "NUL", "COM1.log", "LPT9", "trailing.", "x:y", "line\nbreak", ".blueprint")
        for name in invalid:
            with self.subTest(name=name), self.assertRaises(FolderError):
                create_folder(self.root, name)
        self.assertFalse((self.base / "escape").exists())
        self.assertEqual(self.index(), [])

    def test_mark_preserves_project_guidance_existing_files_and_identity(self):
        folder = self.root / "現有專案"
        folder.mkdir()
        guidance = "# 客戶原有指引\n保留呢份文件。\n"
        (folder / "AGENTS.md").write_text(guidance, encoding="utf-8")
        (folder / "main.py").write_text("print('existing')\n", encoding="utf-8")
        first = mark_folder(folder, "第一份說明", category="chat", tags=["客戶"])
        metadata_path = folder / ".blueprint" / "folder.json"
        extended = self.metadata(folder)
        extended["custom_extension"] = {"owner": "企業 A"}
        metadata_path.write_text(json.dumps(extended, ensure_ascii=False), encoding="utf-8")
        second = mark_folder(folder, "修改用途", category="web", tags=["新標記"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["created_at"], second["created_at"])
        self.assertEqual(second["custom_extension"], {"owner": "企業 A"})
        self.assertEqual((folder / "AGENTS.md").read_text(encoding="utf-8"), guidance)
        self.assertEqual((folder / "main.py").read_text(encoding="utf-8"), "print('existing')\n")
        self.assertEqual(len(self.index()), 1)
        self.assertEqual(search_folders(self.root, "修改用途")[0]["id"], first["id"])
        self.assertEqual(search_folders(self.root, "第一份說明"), [])

    def test_search_and_metadata_round_trip_chinese_quotes_and_csv_formulas(self):
        description = '= "廣東話客服", customer support\n處理客戶\'s 查詢'
        tags = ["廣東話", 'quoted "tag"', "WhatsApp", "廣東話"]
        result = create_folder(self.root, "客服專員", description, category="chat", tags=tags)
        data = self.metadata(Path(result["path"]))
        self.assertEqual(data["description"], description)
        self.assertEqual(data["tags"], tags[:3])
        self.assertEqual(len(search_folders(self.root, "廣東話 WHATSAPP", category="chat")), 1)
        self.assertEqual(search_folders(self.root, "廣東話", category="web"), [])
        self.assertEqual(search_folders(self.root, "不存在"), [])
        csv_text = (self.root / ".blueprint" / "INDEX.csv").read_text(encoding="utf-8-sig")
        row = next(csv.DictReader(io.StringIO(csv_text, newline="")))
        self.assertEqual(row["description"], "'" + description)
        self.assertEqual(row["name"], "客服專員")

    def test_reindex_finds_moved_renamed_folders_and_skips_directory_symlinks(self):
        result = create_folder(self.root, "舊名稱", "支援預約", category="calendar")
        group = self.root / "服務組"
        group.mkdir()
        moved = group / "新名稱"
        Path(result["path"]).rename(moved)
        outside = self.base / "另一工作室"
        external = create_folder(outside, "外部專員", category="finance")
        try:
            (self.root / "捷徑").symlink_to(Path(external["path"]), target_is_directory=True)
        except (OSError, NotImplementedError):
            pass  # Windows can restrict symlink creation without developer mode.
        summary = reindex(self.root)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["warnings"], [])
        entry = self.index()[0]
        self.assertEqual(entry["id"], result["id"])
        self.assertEqual(entry["name"], "新名稱")
        self.assertEqual(entry["path"], "服務組/新名稱")
        self.assertEqual(search_folders(self.root, "新名稱")[0]["path"], str(moved.resolve()))

    def test_invalid_metadata_is_reported_and_not_overwritten(self):
        result = create_folder(self.root, "資料損壞測試")
        folder = Path(result["path"])
        metadata = folder / ".blueprint" / "folder.json"
        original_index = (self.root / ".blueprint" / "index.json").read_bytes()
        original_icon = (folder / ".blueprint" / "icon.png").read_bytes()
        for damaged in (b"{not json", b"[]"):
            with self.subTest(damaged=damaged):
                metadata.write_bytes(damaged)
                with self.assertRaises(FolderError):
                    mark_folder(folder, "不可覆蓋", category="finance")
                self.assertEqual(metadata.read_bytes(), damaged)
                self.assertEqual((folder / ".blueprint" / "icon.png").read_bytes(), original_icon)
                self.assertEqual((self.root / ".blueprint" / "index.json").read_bytes(), original_index)

    def test_invalid_index_is_rejected_before_create_or_mark_changes(self):
        existing = create_folder(self.root, "既有專員")
        metadata = Path(existing["path"]) / ".blueprint" / "folder.json"
        original_metadata = metadata.read_bytes()
        index_path = self.root / ".blueprint" / "index.json"
        for damaged in (b"{not json", b'{"folders": {}}', b'{"folders": [42]}'):
            with self.subTest(damaged=damaged):
                index_path.write_bytes(damaged)
                with self.assertRaises(FolderError):
                    create_folder(self.root, "拒絕新增")
                self.assertFalse((self.root / "拒絕新增").exists())
                with self.assertRaises(FolderError):
                    mark_folder(Path(existing["path"]), "不可修改")
                self.assertEqual(metadata.read_bytes(), original_metadata)
                self.assertEqual(index_path.read_bytes(), damaged)

    def test_reindex_reports_bad_metadata_without_modifying_it(self):
        valid = create_folder(self.root, "完整專員")
        broken = Path(create_folder(self.root, "損壞專員")["path"])
        marker = broken / ".blueprint" / "folder.json"
        marker.write_bytes(b"{broken")
        result = reindex(self.root)
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("損壞專員", result["warnings"][0])
        self.assertEqual(marker.read_bytes(), b"{broken")
        self.assertEqual(self.index()[0]["id"], valid["id"])

        # Hand-edited identity fields must be skipped, not crash CSV generation.
        malformed = self.metadata(Path(valid["path"]))
        for invalid_tags in ([123], [{"tag": "客服"}]):
            malformed["tags"] = invalid_tags
            raw = json.dumps(malformed, ensure_ascii=False).encode("utf-8")
            marker.write_bytes(raw)
            report = reindex(self.root)
            self.assertEqual(report["count"], 1)
            self.assertEqual(len(report["warnings"]), 1)
            self.assertEqual(marker.read_bytes(), raw)

    def test_parallel_process_creation_does_not_lose_index_entries(self):
        count = 12
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
            identities = list(pool.map(_create_in_worker, [(str(self.root), n) for n in range(count)]))
        entries = self.index()
        self.assertEqual(len(entries), count)
        self.assertEqual({entry["id"] for entry in entries}, set(identities))
        self.assertEqual(len(search_folders(self.root, "parallel")), count)
        for entry in entries:
            self.assertEqual(self.metadata(self.root / entry["path"])["id"], entry["id"])


if __name__ == "__main__":
    unittest.main()
