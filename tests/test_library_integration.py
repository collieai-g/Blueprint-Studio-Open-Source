"""Local workday bindings preserve private workspace content and boundaries."""

from datetime import date
import json
from pathlib import Path
import shlex
import sqlite3
import tempfile
import unittest

from blueprint_library.integration import (
    connect_workday, existing_workday_data, resolve_workday_data,
    workday_example, workday_instruction,
)
from blueprint_library.server import initialize_library
from blueprint_library.store import LibraryError, Store


class WorkdayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blueprint-workday-integration-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.workspace = self.base / "私人 工作室 $(literal)"
        self.data = self.base / "私人 資料"
        self.tool = self.base / "工具 ' $(literal)"
        self.tool.mkdir()
        (self.tool / "blueprint.py").write_text("# fixture entrypoint\n", encoding="utf-8")
        initialize_library(self.data, "owner", "private-passphrase-for-tests")

    def connect(self, **kwargs):
        return connect_workday(kwargs.get("workspace", self.workspace),
                               kwargs.get("data_dir", self.data),
                               kwargs.get("tool_root", self.tool))

    def assert_error(self, status, callback):
        with self.assertRaises(LibraryError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def symlink(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError:
            self.skipTest("Test filesystem does not permit symlinks")

    def test_connect_preserves_private_bytes_and_resolves_existing_data(self):
        self.workspace.mkdir()
        private = self.workspace / ".blueprint"
        private.mkdir()
        original = {
            self.workspace / "AGENTS.md": b"# Private guidance\r\nKeep custom newlines.\r\n",
            private / "tool.json": b'{"tool_path":"custom","extension":{"keep":true}}\r\n',
            private / "folder.json": b'{"private_extension":"keep"}\n',
            self.workspace / "blueprint-studio.py": b"# custom launcher\n",
            self.workspace / "private.py": b"# private source\n",
        }
        for path, content in original.items():
            path.write_bytes(content)
        credentials = (self.data / "access.json").read_bytes()
        result = self.connect()
        self.assertEqual(set(result), {"workspace_path", "guide_path", "config_path", "data_path"})
        self.assertEqual(result["workspace_path"], str(self.workspace))
        self.assertEqual(result["data_path"], str(self.data))
        self.assertEqual(resolve_workday_data(self.workspace), self.data)
        self.assertEqual(existing_workday_data(self.data), self.data)
        for path, content in original.items():
            if path.name == "AGENTS.md":
                self.assertTrue(path.read_bytes().startswith(content))
                self.assertNotIn(b"\n", path.read_bytes().replace(b"\r\n", b""))
            else:
                self.assertEqual(path.read_bytes(), content)
        config_bytes = Path(result["config_path"]).read_bytes()
        config = json.loads(config_bytes)
        self.assertEqual(config["schema"], "blueprint-workday-link")
        self.assertEqual(config["version"], 1)
        self.assertEqual(config["data_path"], str(self.data))
        self.assertEqual(config["tool_path"], str(self.tool))
        self.assertNotIn(b"password", config_bytes)
        self.assertEqual((self.data / "access.json").read_bytes(), credentials)
        guide = Path(result["guide_path"]).read_text(encoding="utf-8")
        save_line = next(line for line in guide.splitlines() if " workday-save " in line)
        self.assertEqual(shlex.split(save_line), ["python3", str(self.tool / "blueprint.py"),
                                                 "workday-save", "--workspace", str(self.workspace), "--input", "-"])

    def test_reconnect_is_idempotent_preserves_extensions_and_added_guidance(self):
        result = self.connect()
        config_path = Path(result["config_path"])
        config = json.loads(config_path.read_bytes())
        config["extension"] = {"private": ["retain", 7]}
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        agents = self.workspace / "AGENTS.md"
        agents.write_bytes(b"# Before\n\n" + agents.read_bytes() + b"\n# After\nRetain this too.\n")
        paths = [agents, config_path, self.workspace / "WORKDAY.md"]
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        self.assertEqual(self.connect(), result)
        for path, (content, mtime) in before.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mtime_ns, mtime)
        self.assertEqual(json.loads(config_path.read_bytes())["extension"], {"private": ["retain", 7]})

    def test_default_tool_root_and_move_to_another_known_tool(self):
        result = connect_workday(self.workspace, self.data)
        source = Path(__file__).resolve().parents[1]
        self.assertEqual(json.loads(Path(result["config_path"]).read_bytes())["tool_path"], str(source))
        agents = (self.workspace / "AGENTS.md").read_bytes()
        result = self.connect()
        self.assertIn(shlex.quote(str(self.tool / "blueprint.py")), Path(result["guide_path"]).read_text(encoding="utf-8"))
        self.assertEqual((self.workspace / "AGENTS.md").read_bytes(), agents)

    def test_custom_guide_or_binding_conflict_does_not_change_anything(self):
        self.workspace.mkdir()
        custom = self.workspace / "WORKDAY.md"
        custom.write_bytes(b"# My own daily guide\r\n")
        agents = self.workspace / "AGENTS.md"
        agents.write_bytes(b"private guidance")
        self.assert_error(409, self.connect)
        self.assertEqual(custom.read_bytes(), b"# My own daily guide\r\n")
        self.assertEqual(agents.read_bytes(), b"private guidance")
        self.assertFalse((self.workspace / ".blueprint").exists())
        custom.unlink()
        (self.workspace / ".blueprint").mkdir()
        config = self.workspace / ".blueprint" / "workday.json"
        for content in (b"not json", b"[]", b'{"custom":true}',
                        b'{"schema":"blueprint-workday-link","version":2}'):
            config.write_bytes(content)
            self.assert_error(409, self.connect)
            self.assertEqual(config.read_bytes(), content)
            self.assertFalse(custom.exists())
            self.assertEqual(agents.read_bytes(), b"private guidance")

    def test_modified_managed_guide_and_block_are_never_overwritten(self):
        self.connect()
        guide = self.workspace / "WORKDAY.md"
        generated = guide.read_bytes()
        guide.write_bytes(generated + b"\n# My addition\n")
        self.assert_error(409, self.connect)
        self.assertEqual(guide.read_bytes(), generated + b"\n# My addition\n")
        guide.write_bytes(generated)
        agents = self.workspace / "AGENTS.md"
        agents.write_bytes(agents.read_bytes().replace("先讀取".encode(), "請勿讀取".encode()))
        altered = agents.read_bytes()
        self.assert_error(409, self.connect)
        self.assertEqual(agents.read_bytes(), altered)

    def test_incomplete_or_duplicate_managed_markers_are_conflicts(self):
        self.workspace.mkdir()
        agents = self.workspace / "AGENTS.md"
        for content in (b"private <!-- BLUEPRINT-WORKDAY:START -->",
                        b"<!-- BLUEPRINT-WORKDAY:END -->",
                        b"<!-- BLUEPRINT-WORKDAY:START -->\n<!-- BLUEPRINT-WORKDAY:START -->\n<!-- BLUEPRINT-WORKDAY:END -->"):
            agents.write_bytes(content)
            self.assert_error(409, self.connect)
            self.assertEqual(agents.read_bytes(), content)
            self.assertFalse((self.workspace / "WORKDAY.md").exists())

    def test_private_source_boundaries_and_missing_entrypoint(self):
        source = Path(__file__).resolve().parents[1]
        for workspace in (source, source / "private-workday-test", source.parent,
                          self.tool, self.tool / "private", self.tool.parent):
            with self.subTest(workspace=workspace):
                self.assert_error(400, lambda: self.connect(workspace=workspace))
        self.assert_error(400, lambda: self.connect(data_dir=source))
        self.assertFalse(self.workspace.exists())
        (self.tool / "blueprint.py").unlink()
        self.assert_error(400, self.connect)
        self.assertFalse(self.workspace.exists())

    def test_missing_or_uninitialized_library_is_not_created(self):
        missing = self.base / "typo-library"
        self.assert_error(400, lambda: self.connect(data_dir=missing))
        self.assert_error(400, lambda: existing_workday_data(missing))
        self.assertFalse(missing.exists())
        self.assertFalse(self.workspace.exists())
        empty = self.base / "empty-library"
        empty.mkdir()
        self.assert_error(400, lambda: self.connect(data_dir=empty))
        self.assertEqual(list(empty.iterdir()), [])
        (empty / "library.sqlite3").write_bytes(b"")
        (empty / "access.json").write_bytes(b"{}")
        self.assert_error(400, lambda: self.connect(data_dir=empty))
        self.assertEqual((empty / "library.sqlite3").read_bytes(), b"")
        self.assertFalse((empty / "blobs").exists())
        (empty / "library.sqlite3").write_bytes(b"invalid database")
        self.assert_error(400, lambda: existing_workday_data(empty))
        self.assertEqual((empty / "library.sqlite3").read_bytes(), b"invalid database")
        (self.data / "access.json").unlink()
        self.assert_error(400, self.connect)
        self.assertFalse(self.workspace.exists())

    def test_resolve_needs_binding_and_never_recreates_deleted_library(self):
        self.assert_error(400, lambda: resolve_workday_data(self.workspace))
        self.connect()
        (self.data / "library.sqlite3").unlink()
        self.assert_error(400, lambda: resolve_workday_data(self.workspace))
        self.assertFalse((self.data / "library.sqlite3").exists())

    def test_future_database_schema_is_rejected_without_workspace_writes(self):
        from blueprint_library.store import SCHEMA_VERSION
        with sqlite3.connect(self.data / "library.sqlite3") as conn:
            conn.execute("PRAGMA user_version=" + str(SCHEMA_VERSION + 1))
        self.assert_error(500, self.connect)
        self.assertFalse(self.workspace.exists())

    def test_symlink_workspace_parent_tool_and_data_are_rejected(self):
        target = self.base / "target"
        target.mkdir()
        link = self.base / "symlink-workspace"
        self.symlink(link, target, directory=True)
        self.assert_error(400, lambda: self.connect(workspace=link))
        self.assert_error(400, lambda: self.connect(workspace=link / "child"))
        self.assertFalse((target / "child").exists())
        self.assertEqual(list(target.iterdir()), [])
        tool_alias = self.base / "tool-alias"
        self.symlink(tool_alias, self.tool, directory=True)
        self.assert_error(400, lambda: self.connect(tool_root=tool_alias))
        data_alias = self.base / "data-alias"
        self.symlink(data_alias, self.data, directory=True)
        self.assert_error(400, lambda: self.connect(data_dir=data_alias))
        self.assert_error(400, lambda: existing_workday_data(data_alias))
        self.assertFalse(self.workspace.exists())

    def test_symlink_managed_paths_never_touch_targets(self):
        self.workspace.mkdir()
        private = self.workspace / ".blueprint"
        target = self.base / "target.txt"
        target.write_bytes(b"do not touch")
        for filename in ("WORKDAY.md", "AGENTS.md"):
            link = self.workspace / filename
            self.symlink(link, target)
            self.assert_error(400, self.connect)
            self.assertEqual(target.read_bytes(), b"do not touch")
            link.unlink()
        private.mkdir()
        config = private / "workday.json"
        self.symlink(config, target)
        self.assert_error(400, self.connect)
        self.assert_error(400, lambda: resolve_workday_data(self.workspace))
        self.assertEqual(target.read_bytes(), b"do not touch")
        config.unlink()
        private.rmdir()
        target_dir = self.base / "target-dir"
        target_dir.mkdir()
        self.symlink(private, target_dir, directory=True)
        self.assert_error(400, self.connect)
        self.assertEqual(list(target_dir.iterdir()), [])

    def test_nonfile_targets_are_rejected(self):
        self.workspace.mkdir()
        for filename in ("WORKDAY.md", "AGENTS.md"):
            target = self.workspace / filename
            target.mkdir()
            self.assert_error(409, self.connect)
            target.rmdir()
        private = self.workspace / ".blueprint"
        private.write_bytes(b"custom file")
        self.assert_error(409, self.connect)
        self.assertEqual(private.read_bytes(), b"custom file")

    def test_database_or_access_symlinks_are_rejected(self):
        for filename in ("library.sqlite3", "access.json"):
            path = self.data / filename
            original = path.read_bytes()
            target = self.base / (filename + ".original")
            path.rename(target)
            self.symlink(path, target)
            self.assert_error(400, self.connect)
            self.assertEqual(target.read_bytes(), original)
            path.unlink()
            target.rename(path)
        self.assertFalse(self.workspace.exists())

    def test_example_and_instruction_are_explicit_and_accept_browser_iso_date(self):
        example = workday_example("2026-10-19")
        self.assertEqual(set(example), {"date", "source", "source_key", "title", "summary", "entries"})
        self.assertEqual(example["date"], "2026-10-19")
        self.assertEqual(example["date"], workday_example(date(2026, 10, 19))["date"])
        self.assertEqual([entry["type"] for entry in example["entries"]], ["outcome", "decision", "reference", "todo"])
        self.assertIn("格式示範", example["summary"])
        self.assertTrue(all("url" not in entry for entry in example["entries"]))
        instruction = workday_instruction("2026-10-19")
        for text in ("2026-10-19", "workday-connect", "source_key", "409", "有效 JSON", "不讀取其他平台", "毋須每天再要求確認"):
            self.assertIn(text, instruction)
        self.assertNotIn(str(self.base), instruction)
        self.assertNotIn(str(Path(__file__).resolve().parents[1]), instruction)
        for invalid in ("2026-10-9", "2026-02-30", "20261019", 1, "yesterday"):
            with self.subTest(today=invalid):
                self.assert_error(400, lambda: workday_instruction(invalid))
        # No shared mutable payload can leak edits from a previous preview.
        example["entries"].clear()
        self.assertEqual(len(workday_example("2026-10-19")["entries"]), 4)


if __name__ == "__main__":
    unittest.main()
