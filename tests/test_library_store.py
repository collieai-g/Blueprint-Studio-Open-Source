"""Persistent library tests use private data outside the public checkout."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from blueprint_library.store import LibraryError, Store


class LibraryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-library-test-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "company"
        self.store = Store(self.data)

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def test_persistence_collections_and_private_location(self):
        collection = self.store.create_collection({"name": "UI 參考", "icon": "web", "description": "公司設計研究"})
        item = self.store.create_item({"url": "https://example.com/design", "title": "介面設計", "collection_id": collection["id"]})
        reopened = Store(self.data)
        self.assertEqual(reopened.get_item(item["id"])["title"], "介面設計")
        self.assertEqual(reopened.list_collections()[0]["count"], 1)
        self.assertEqual(reopened.stats()["collections"], 1)
        source = Path(__file__).resolve().parent.parent
        self.assert_error(400, lambda: Store(source / "private-company"))
        self.assert_error(400, lambda: Store(source))
        self.assert_error(400, lambda: Store(source.parent))
        with sqlite3.connect(self.store.db_path) as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_file_bytes_text_search_and_empty_files(self):
        content = "# 金融網站研究\n比較色彩同排版，品牌採用海藍色。\nStraße".encode()
        item = self.store.add_file("介面研究.md", content, notes="每星期 UI 參考", tags=["自訂", "自訂"])
        path, name, mime = self.store.get_file(item["id"])
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(name, "介面研究.md")
        self.assertTrue(mime.startswith("text/"))
        self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(item["size"], len(content))
        self.assertEqual(item["tags"], ["自訂"])
        self.assertNotIn("blob_name", item)
        self.assertEqual(self.store.list_items(q="海藍色 自訂")["total"], 1)
        self.assertEqual(self.store.list_items(q="STRASSE")["total"], 1)
        self.assertEqual(self.store.list_items(q="海藍色 缺少")["total"], 0)
        listed = self.store.list_items()["items"][0]
        self.assertNotIn("extracted_text", listed)
        empty = self.store.add_file("empty.txt", b"")
        self.assertEqual(empty["size"], 0)
        self.assertEqual(self.store.get_file(empty["id"])[0].read_bytes(), b"")

    def test_binary_and_large_text_preserve_data_without_extraction(self):
        item = self.store.add_file("capture.png", b"\x89PNG\x00\xff", "image/png")
        self.assertEqual(item["extracted_text"], "")
        content = b"a" * (200 * 1024 + 1)
        large = self.store.add_file("large.txt", content)
        self.assertEqual(large["extracted_text"], "")
        self.assertEqual(self.store.get_file(large["id"])[0].read_bytes(), content)
        self.assert_error(413, lambda: self.store.add_file("huge.txt", b"a" * (20 * 1024 * 1024 + 1)))

    def test_bookmark_urls_types_and_unknown_resources(self):
        video = self.store.create_item({"url": "https://youtu.be/abcdefghijk", "notes": "公司學習內容"})
        self.assertEqual(video["kind"], "link")
        self.assertEqual(video["source_type"], "youtube")
        self.assertEqual(self.store.list_items(source_type="youtube")["total"], 1)
        for url in ["javascript:alert(1)", "file:///etc/passwd", "not a URL", "https://user:pass@example.com"]:
            with self.subTest(url=url):
                self.assert_error(400, lambda: self.store.create_item({"url": url}))
        self.assert_error(400, lambda: self.store.create_item({"title": "missing URL"}))
        self.assert_error(400, lambda: self.store.create_item({"url": "https://example.com", "kind": "file"}))
        self.assert_error(404, lambda: self.store.get_item("unknown"))
        self.assert_error(404, lambda: self.store.item_history("unknown"))
        self.assert_error(404, lambda: self.store.get_file(video["id"]))
        self.assert_error(404, lambda: self.store.create_item({"url": "https://example.com", "collection_id": "missing"}))

    def test_optimistic_versions_overrides_history_and_reclassification(self):
        item = self.store.create_item({"url": "https://example.com", "title": "介面設計", "category": "finance", "tags": ["保留"]}, actor="同事甲")
        updated = self.store.update_item(item["id"], {"expected_version": 1, "notes": "前端網站 UI 研究"}, actor="同事乙")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["category"], "finance")
        self.assertEqual(updated["tags"], ["保留"])
        self.assertTrue(updated["classification"]["overrides"]["category"])
        self.assert_error(409, lambda: self.store.update_item(item["id"], {"expected_version": 1, "notes": "舊版本"}))
        self.assertEqual(self.store.get_item(item["id"])["notes"], "前端網站 UI 研究")
        classified = self.store.update_item(item["id"], {"expected_version": 2, "reclassify": True})
        self.assertFalse(classified["classification"]["overrides"]["category"])
        self.assertFalse(classified["classification"]["overrides"]["tags"])
        self.assertNotIn("保留", classified["tags"])
        history = self.store.item_history(item["id"])
        self.assertEqual([entry["version"] for entry in history], [1, 2, 3])
        self.assertEqual([entry["actor"] for entry in history[:2]], ["同事甲", "同事乙"])
        self.assertEqual(history[0]["snapshot"]["notes"], "")
        self.assertEqual(history[-1]["action"], "reclassify")

    def test_archive_restore_and_metadata_export(self):
        collection = self.store.create_collection({"name": "文件"})
        item = self.store.add_file("財務.txt", "公司報告".encode(), collection_id=collection["id"])
        archived = self.store.update_item(item["id"], {"expected_version": 1, "archived": True})
        self.assertTrue(archived["archived"])
        self.assertEqual(self.store.list_items()["total"], 0)
        self.assertEqual(self.store.list_items(archived=True)["total"], 1)
        self.assertEqual(self.store.list_collections()[0]["count"], 0)
        self.assertEqual(self.store.stats()["archived"], 1)
        export = self.store.export_metadata()
        self.assertEqual(export["format"], "blueprint-library-metadata")
        self.assertFalse(export["includes_file_bytes"])
        self.assertTrue(export["items"][0]["archived"])
        self.assertEqual(len(export["history"]), 2)
        self.assertNotIn("blob_name", json.dumps(export))
        restored = self.store.update_item(item["id"], {"expected_version": 2, "archived": False})
        self.assertEqual(restored["version"], 3)
        self.assertEqual(self.store.stats()["files"], 1)

    def test_pagination_filters_and_sql_metacharacters(self):
        first = self.store.create_collection({"name": "甲"})
        second = self.store.create_collection({"name": "乙"})
        for index in range(5):
            self.store.create_item({"url": "https://example.com/" + str(index), "title": "參考 %_" + str(index), "category": "web", "collection_id": first["id"] if index < 3 else second["id"]})
        self.assertEqual(self.store.list_items(limit=2, offset=4)["total"], 5)
        self.assertEqual(len(self.store.list_items(limit=2, offset=4)["items"]), 1)
        self.assertEqual(self.store.list_items(collection_id=first["id"], category="web", kind="link")["total"], 3)
        self.assertEqual(self.store.list_items(q="%_")["total"], 5)
        self.assertEqual(self.store.list_items(q="' OR 1=1 --")["total"], 0)

    def test_malformed_fields_and_reserved_internal_fields(self):
        item = self.store.create_item({"url": "https://example.com"})
        for payload in [None, [], {"url": 123}, {"url": "https://example.com", "tags": "wrong"},
                        {"url": "https://example.com", "tags": [12]}, {"url": "https://example.com", "category": []},
                        {"url": "https://example.com", "id": "assigned"}, {"url": "https://example.com", "blob_name": "../secret"}]:
            with self.subTest(payload=payload):
                self.assert_error(400, lambda: self.store.create_item(payload))
        for payload in [{}, {"expected_version": True}, {"expected_version": 1, "title": []},
                        {"expected_version": 1, "archived": "false"}, {"expected_version": 1, "reclassify": "yes"},
                        {"expected_version": 1, "created_at": "replace"}, {"expected_version": 1, "tags": [[]]}]:
            with self.subTest(payload=payload):
                self.assert_error(400, lambda: self.store.update_item(item["id"], payload))
        for kwargs in [{"q": []}, {"archived": 1}, {"kind": []}, {"category": []}, {"limit": True}, {"offset": -1}, {"source_type": "unknown"}]:
            with self.subTest(kwargs=kwargs):
                self.assert_error(400, lambda: self.store.list_items(**kwargs))
        for name in ["../secret", "a/b", "a\\b", ".", "..", "invalid\nname"]:
            self.assert_error(400, lambda: self.store.add_file(name, b"value"))
        self.assert_error(400, lambda: self.store.add_file("valid.txt", "not bytes"))
        self.assert_error(400, lambda: self.store.add_file("valid.txt", b"", mime="bad mime"))
        self.assert_error(400, lambda: self.store.create_collection({"name": "", "icon": "web"}))

    def test_file_creation_rolls_back_and_blob_paths_are_protected(self):
        self.assert_error(400, lambda: self.store.add_file("file.txt", b"private", actor=""))
        self.assertEqual(self.store.stats()["total"], 0)
        self.assertEqual(list(self.store.blobs_dir.iterdir()), [])
        item = self.store.add_file("safe.txt", b"value")
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute("UPDATE items SET blob_name='../library.sqlite3' WHERE id=?", (item["id"],))
        self.assert_error(500, lambda: self.store.get_file(item["id"]))

    def test_concurrent_edit_does_not_overwrite_a_newer_version(self):
        item = self.store.create_item({"url": "https://example.com"})
        barrier = threading.Barrier(2)

        def edit(notes):
            barrier.wait(timeout=5)
            try:
                return self.store.update_item(item["id"], {"expected_version": 1, "notes": notes})
            except LibraryError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(edit, ["第一位同事", "第二位同事"]))
        self.assertEqual(sum(value == 409 for value in outcomes), 1)
        self.assertEqual(len(self.store.item_history(item["id"])), 2)
        self.assertEqual(self.store.get_item(item["id"])["version"], 2)

    def test_storage_rejects_symlink_replacement(self):
        item = self.store.add_file("safe.txt", b"company")
        original_blobs = self.store.blobs_dir
        moved_blobs = self.data / "moved-blobs"
        original_blobs.rename(moved_blobs)
        original_blobs.symlink_to(moved_blobs, target_is_directory=True)
        self.assert_error(500, lambda: self.store.get_file(item["id"]))
        self.assert_error(500, lambda: self.store.add_file("another.txt", b"value"))


if __name__ == "__main__":
    unittest.main()
