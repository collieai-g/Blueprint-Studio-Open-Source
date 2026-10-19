"""Persistent library tests use private data outside the public checkout."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from blueprint_library.store import LibraryError, SCHEMA_VERSION, Store


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

    def test_pinning_filters_persistence_archive_counts_and_versions(self):
        collection = self.store.create_collection({"name": "UI 參考", "icon": "web"})
        first = self.store.create_item({"url": "https://example.com", "collection_id": collection["id"],
                                        "category": "finance", "tags": ["自訂保留"]})
        second = self.store.add_file("研究.txt", "公司知識".encode())
        third = self.store.create_item({"url": "https://example.com/other"})
        self.assertIs(first["pinned"], False)
        self.assertEqual(self.store.stats()["pinned"], 0)
        pinned = self.store.update_item(first["id"], {"expected_version": 1, "pinned": True})
        self.assertIs(pinned["pinned"], True)
        self.assertEqual(pinned["category"], "finance")
        self.assertEqual(pinned["tags"], ["自訂保留"])
        self.assertTrue(pinned["classification"]["overrides"]["category"])
        self.store.update_item(second["id"], {"expected_version": 1, "pinned": True})
        reopened = Store(self.data)
        self.assertIs(reopened.get_item(first["id"])["pinned"], True)
        self.assertEqual(reopened.stats()["pinned"], 2)
        self.assertEqual(reopened.stats()["unfiled"], 2)
        self.assertEqual(reopened.list_items(pinned=True)["total"], 2)
        self.assertEqual(reopened.list_items(pinned=False)["total"], 1)
        self.assertEqual(reopened.list_items(unfiled=True)["total"], 2)
        self.assertEqual(reopened.list_items(pinned=True, unfiled=True)["items"][0]["id"], second["id"])
        self.assertEqual(reopened.list_items(collection_id=collection["id"], unfiled=True)["total"], 0)
        reopened.update_item(second["id"], {"expected_version": 2, "archived": True})
        self.assertEqual(reopened.stats()["pinned"], 1)
        self.assertEqual(reopened.stats()["unfiled"], 1)
        self.assertEqual(reopened.list_items(archived=True, pinned=True)["total"], 1)
        self.assert_error(409, lambda: reopened.update_item(first["id"], {"expected_version": 1, "pinned": False}))
        self.assertIs(reopened.get_item(first["id"])["pinned"], True)
        unpinned = reopened.update_item(first["id"], {"expected_version": 2, "pinned": False})
        self.assertIs(unpinned["pinned"], False)
        self.assertEqual(reopened.stats()["pinned"], 0)
        self.assertEqual([entry["snapshot"]["pinned"] for entry in reopened.item_history(first["id"])], [False, True, False])
        self.assertIs(reopened.export_metadata()["items"][2]["pinned"], False)
        self.assertIs(reopened.get_item(third["id"])["pinned"], False)

    def test_sort_allowlist_and_strict_filter_types(self):
        first = self.store.create_item({"url": "https://example.com/a", "title": "Zulu"})
        second = self.store.create_item({"url": "https://example.com/b", "title": "alpha"})
        third = self.store.create_item({"url": "https://example.com/c", "title": "Beta"})
        self.store.update_item(first["id"], {"expected_version": 1, "notes": "最後修改"})
        self.assertEqual([item["id"] for item in self.store.list_items(sort="updated")["items"]],
                         [first["id"], third["id"], second["id"]])
        self.assertEqual([item["id"] for item in self.store.list_items(sort="created")["items"]],
                         [third["id"], second["id"], first["id"]])
        self.assertEqual([item["title"] for item in self.store.list_items(sort="title")["items"]], ["alpha", "Beta", "Zulu"])
        self.assertEqual(self.store.list_items(sort="title", limit=1, offset=1)["items"][0]["id"], third["id"])
        for kwargs in ({"pinned": 1}, {"pinned": "true"}, {"unfiled": None}, {"unfiled": "false"},
                       {"sort": []}, {"sort": "title; DROP TABLE items"}, {"sort": "oldest"}):
            with self.subTest(kwargs=kwargs):
                self.assert_error(400, lambda: self.store.list_items(**kwargs))
        for value in (None, 1, "true", [], {}):
            with self.subTest(pinned=value):
                self.assert_error(400, lambda: self.store.update_item(first["id"], {"expected_version": 2, "pinned": value}))

    def test_v1_schema_migration_preserves_private_records_files_and_history(self):
        # An actual v1 schema fixture, independent of the v2 constructor: no
        # pinned column and historical snapshots without the new field.
        data = Path(self.temp.name) / "previous-company"
        blobs = data / "blobs"
        blobs.mkdir(parents=True)
        file_bytes = "公司既有研究資料：保留原檔".encode()
        blob_id = "a" * 32
        (blobs / blob_id).write_bytes(file_bytes)
        auth_bytes = b'{"username":"existing-owner","salt":"unchanged-private-auth"}\n'
        (data / "access.json").write_bytes(auth_bytes)
        created = "2026-01-02T03:04:05+00:00"
        classification = {"method": "rules", "signals": ["topic:research"], "extension": {"keep": True},
                          "overrides": {"category": True, "tags": True}}
        item = dict(id="f" * 32, kind="file", title="原有研究", notes="不能遺失的公司筆記", url="",
                    collection_id="c" * 32, category="research", tags=json.dumps(["保留"]),
                    source_type="document", classification=json.dumps(classification),
                    category_override=1, tags_override=1, file_name="研究.txt", mime="text/plain",
                    size=len(file_bytes), sha256=hashlib.sha256(file_bytes).hexdigest(), blob_name=blob_id,
                    extracted_text=file_bytes.decode(), search_text="原有研究\n保留", version=7,
                    archived=1, created_at=created, updated_at=created)
        snapshot = {key: value for key, value in item.items() if key not in {"blob_name", "search_text", "category_override", "tags_override"}}
        snapshot.update(tags=["保留"], classification=classification, archived=True, icon="research")
        snapshot_json = json.dumps(snapshot, ensure_ascii=False)
        db = data / "library.sqlite3"
        with sqlite3.connect(db) as conn:
            conn.executescript("""
                CREATE TABLE collections(id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,icon TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE items(id TEXT PRIMARY KEY,kind TEXT NOT NULL,title TEXT NOT NULL,notes TEXT NOT NULL,url TEXT NOT NULL,
                    collection_id TEXT REFERENCES collections(id),category TEXT NOT NULL,tags TEXT NOT NULL,source_type TEXT NOT NULL,
                    classification TEXT NOT NULL,category_override INTEGER NOT NULL,tags_override INTEGER NOT NULL,
                    file_name TEXT NOT NULL,mime TEXT NOT NULL,size INTEGER NOT NULL,sha256 TEXT NOT NULL,blob_name TEXT NOT NULL,
                    extracted_text TEXT NOT NULL,search_text TEXT NOT NULL,version INTEGER NOT NULL,archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE history(id TEXT PRIMARY KEY,item_id TEXT NOT NULL REFERENCES items(id),version INTEGER NOT NULL,
                    action TEXT NOT NULL,actor TEXT NOT NULL,recorded_at TEXT NOT NULL,snapshot TEXT NOT NULL,UNIQUE(item_id,version));
                PRAGMA user_version=1;
            """)
            conn.execute("INSERT INTO collections VALUES(?,?,?,?,?)", (item["collection_id"], "既有收藏夾", "保留說明", "research", created))
            conn.execute("INSERT INTO items(" + ",".join(item) + ") VALUES(" + ",".join("?" for _ in item) + ")", list(item.values()))
            conn.execute("INSERT INTO history VALUES(?,?,?,?,?,?,?)", ("h" * 32, item["id"], 7, "update", "existing-owner", created, snapshot_json))
        for _ in range(2):
            migrated = Store(data)
            self.assertIs(migrated.get_item(item["id"])["pinned"], False)
            self.assertTrue(migrated.get_item(item["id"])["archived"])
            self.assertEqual(migrated.get_item(item["id"])["version"], 7)
            self.assertEqual(migrated.get_item(item["id"])["classification"], classification)
            self.assertEqual(migrated.get_file(item["id"])[0].read_bytes(), file_bytes)
            self.assertEqual((data / "access.json").read_bytes(), auth_bytes)
            self.assertIs(migrated.item_history(item["id"])[0]["snapshot"]["pinned"], False)
            self.assertIs(migrated.export_metadata()["history"][0]["snapshot"]["pinned"], False)
            with sqlite3.connect(db) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                stored = dict(conn.execute("SELECT * FROM items").fetchone())
                self.assertEqual(stored.pop("pinned"), 0)
                self.assertEqual(stored, item)
                self.assertEqual(conn.execute("SELECT snapshot FROM history").fetchone()[0], snapshot_json)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM history").fetchone()[0], 1)

    def test_future_schema_is_rejected_without_modifying_database(self):
        with sqlite3.connect(self.store.db_path) as conn:
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
        before = self.store.db_path.read_bytes()
        self.assert_error(500, lambda: Store(self.data))
        self.assertEqual(self.store.db_path.read_bytes(), before)
        with sqlite3.connect(self.store.db_path) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)


if __name__ == "__main__":
    unittest.main()
