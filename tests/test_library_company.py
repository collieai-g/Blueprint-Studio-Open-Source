"""Private company setup is versioned and survives upgrades and restarts."""

from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from blueprint_library.store import LibraryError, SCHEMA_VERSION, Store


class CompanyProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="blueprint-company-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "private-data"
        self.store = Store(self.data)

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def test_unconfigured_profile_has_no_company_name_or_invented_dates(self):
        profile = self.store.get_company_profile()
        self.assertEqual(profile, {"name": "", "industry": "", "description": "", "goals": "", "version": 0,
                                   "created_at": None, "updated_at": None, "configured": False})
        self.assertEqual(self.store.export_metadata()["company_profile"], profile)
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM company_profile").fetchone()[0], 0)

    def test_update_profile_persists_fields_versions_and_explicit_clearing(self):
        first = self.store.update_company_profile({"expected_version": 0, "name": "  藍圖專員公司  ",
                                                   "industry": "AI 企業工具", "description": "提供專用 Agent 開發。", "goals": "先做好資料整理"})
        self.assertEqual(first["name"], "藍圖專員公司")
        self.assertEqual(first["version"], 1)
        self.assertTrue(first["configured"])
        self.assertEqual(first["created_at"], first["updated_at"])
        self.assertEqual(Store(self.data).get_company_profile(), first)
        second = self.store.update_company_profile({"expected_version": 1, "name": "藍圖工作室", "goals": ""})
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["created_at"], first["created_at"])
        self.assertEqual(second["industry"], first["industry"], "Omitted fields must not be erased")
        self.assertEqual(second["description"], first["description"])
        self.assertEqual(second["goals"], "")
        self.assertEqual(Store(self.data).export_metadata()["company_profile"], second)

    def test_stale_updates_conflict_and_never_reset_existing_profile(self):
        self.assert_error(409, lambda: self.store.update_company_profile({"expected_version": 1, "name": "Incorrect initial version"}))
        profile = self.store.update_company_profile({"expected_version": 0, "name": "公司甲"})
        self.assert_error(409, lambda: self.store.update_company_profile({"expected_version": 0, "name": "公司乙"}))
        self.assertEqual(self.store.get_company_profile(), profile)
        self.assert_error(400, lambda: self.store.update_company_profile({"expected_version": 1, "name": ""}))
        self.assertEqual(self.store.get_company_profile(), profile)

    def test_bootstrap_never_overwrites_manual_profile_on_restart(self):
        initial = self.store.bootstrap_company_profile({"name": "環境預設公司", "industry": "軟件"})
        self.assertEqual(initial["version"], 1)
        self.assertEqual(self.store.bootstrap_company_profile({"name": "另一個預設名稱", "industry": "金融", "goals": "新預設"}), initial)
        edited = self.store.update_company_profile({"expected_version": 1, "name": "私人自訂名稱", "description": "手動更新公司資料"})
        restarted = Store(self.data)
        self.assertEqual(restarted.bootstrap_company_profile({"name": "環境預設公司"}), edited)
        self.assertEqual(restarted.get_company_profile(), edited)

    def test_bad_payloads_are_rejected_before_any_profile_write(self):
        invalid = [None, [], {}, {"expected_version": 0}, {"expected_version": True, "name": "Company"},
                   {"expected_version": -1, "name": "Company"}, {"expected_version": "0", "name": "Company"},
                   {"expected_version": 0, "name": "x" * 121}, {"expected_version": 0, "name": []},
                   {"expected_version": 0, "name": "Company", "industry": "x" * 121},
                   {"expected_version": 0, "name": "Company", "description": "x" * 4001},
                   {"expected_version": 0, "name": "Company", "goals": "x" * 4001},
                   {"expected_version": 0, "name": "Company", "description": None},
                   {"expected_version": 0, "name": "Company", "configured": False},
                   {"expected_version": 0, "name": "Company", "created_at": "override"}]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assert_error(400, lambda: self.store.update_company_profile(payload))
        self.assertFalse(self.store.get_company_profile()["configured"])
        for payload in (None, {}, {"name": ""}, {"name": "Company", "industry": []}, {"name": "Company", "expected_version": 0}):
            self.assert_error(400, lambda: self.store.bootstrap_company_profile(payload))
        self.assertFalse(self.store.get_company_profile()["configured"])

    def test_concurrent_first_setup_and_edits_respect_versions(self):
        barrier = threading.Barrier(2)

        def initialize(name):
            barrier.wait(timeout=5)
            try:
                return self.store.update_company_profile({"expected_version": 0, "name": name})
            except LibraryError as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(initialize, ["公司甲", "公司乙"]))
        self.assertEqual(sum(result == 409 for result in results), 1)
        saved = self.store.get_company_profile()
        self.assertEqual(saved["version"], 1)
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM company_profile").fetchone()[0], 1)

    def test_v3_upgrade_preserves_all_company_library_and_workday_data(self):
        collection = self.store.create_collection({"name": "公司文件"})
        item = self.store.add_file("研究.txt", "公司研究原文".encode(), collection_id=collection["id"])
        item = self.store.update_item(item["id"], {"expected_version": 1, "pinned": True, "tags": ["保留"]})
        report = self.store.import_workday({"date": "2026-10-19", "source_key": "existing-chat", "entries": [{"type": "todo", "title": "保留原有待辦"}]})["workday"]
        self.store.update_todo(report["entries"][0]["id"], {"expected_version": 1, "done": True})
        report = self.store.get_workday(report["id"])
        auth = b'{"username":"private-owner","auth":"untouched"}\n'
        (self.data / "access.json").write_bytes(auth)
        with closing(sqlite3.connect(self.store.db_path)) as conn, conn:
            conn.executescript("DROP TABLE company_profile; PRAGMA user_version=3;")
            previous = {table: conn.execute("SELECT * FROM " + table).fetchall() for table in ("collections", "items", "history", "workdays", "workday_entries")}
        for _ in range(2):
            migrated = Store(self.data)
            self.assertFalse(migrated.get_company_profile()["configured"])
            self.assertEqual(migrated.get_item(item["id"]), item)
            self.assertEqual(migrated.get_workday(report["id"]), report)
            self.assertEqual(migrated.get_file(item["id"])[0].read_bytes(), "公司研究原文".encode())
            self.assertEqual((self.data / "access.json").read_bytes(), auth)
            with closing(sqlite3.connect(migrated.db_path)) as conn, conn:
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                for table, rows in previous.items():
                    self.assertEqual(conn.execute("SELECT * FROM " + table).fetchall(), rows)
        company = migrated.bootstrap_company_profile({"name": "自己的公司"})
        self.assertEqual(migrated.export_metadata()["company_profile"], company)
        self.assertTrue(migrated.get_item(item["id"])["pinned"])
        self.assertTrue(migrated.get_workday(report["id"])["entries"][0]["done"])


if __name__ == "__main__":
    unittest.main()
