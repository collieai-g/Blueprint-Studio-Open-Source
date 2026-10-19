"""A private studio uses updated shared code without moving its own contents."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from blueprint_folders.core import FolderError, create_folder
from blueprint_folders.workspaces import LAUNCHER_NAME, link_workspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="blueprint-binding-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.tool = self.base / "共用 tool"
        self.tool.mkdir()
        self.workspace = self.base / "私人 藍圖專員工作室"
        self.outside = self.base / "其他 cwd"
        self.outside.mkdir()
        self.write_engine("first feature")

    def write_engine(self, feature):
        (self.tool / "blueprint.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
            "'feature': " + repr(feature) + "}, ensure_ascii=False))\n",
            encoding="utf-8",
        )

    def link(self, path=None):
        with patch("blueprint_folders.workspaces._tool_root", return_value=self.tool):
            return link_workspace(self.workspace if path is None else path)

    def run_launcher(self, *arguments):
        return subprocess.run(
            [sys.executable, str(self.workspace / LAUNCHER_NAME), *arguments],
            cwd=self.outside,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=15,
            check=False,
        )

    def test_external_cwd_unicode_paths_and_workspace_commands(self):
        result = self.link()
        self.assertEqual(result["tool_path"], str(self.tool.resolve()))
        self.assertEqual(result["launcher"], str(self.workspace / LAUNCHER_NAME))
        for arguments in (
            ("create", "客服 $(literal)", "--tag", "中文 標籤"),
            ("search", "WhatsApp 客服"),
            ("reindex",),
        ):
            with self.subTest(arguments=arguments):
                process = self.run_launcher(*arguments)
                self.assertEqual(process.returncode, 0, process.stderr)
                received = json.loads(process.stdout)
                self.assertEqual(received["args"], [arguments[0], str(self.workspace), *arguments[1:]])
                self.assertEqual(received["cwd"], str(self.workspace))

    def test_relative_project_paths_and_other_options_are_forwarded(self):
        self.link()
        for arguments in (
            ("mark", "客服專員", "--description", "企業客服"),
            ("inspect", "客服專員"),
            ("pack", "客服專員", "--output", "../備份 客服.zip"),
            ("--version",),
            ("--help",),
            ("gui",),
            ("serve", "--no-open", "--port", "9876"),
        ):
            with self.subTest(arguments=arguments):
                process = self.run_launcher(*arguments)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout)["args"], list(arguments))
                self.assertEqual(json.loads(process.stdout)["cwd"], str(self.workspace))

    def test_no_arguments_shows_usage_without_launching_shared_tool(self):
        self.link()
        (self.tool / "blueprint.py").write_text("raise RuntimeError('must not run')\n", encoding="utf-8")
        process = self.run_launcher()
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("使用方法", process.stdout)
        self.assertEqual(process.stderr, "")

    def test_updated_shared_engine_is_used_without_regenerating_launcher(self):
        self.workspace.mkdir()
        (self.workspace / "AGENTS.md").write_text("# 私人指引\n保留。\n", encoding="utf-8")
        (self.workspace / "private.py").write_bytes(b"private source\x00")
        project = Path(create_folder(self.workspace, "客服專員", tags=["私人"])["path"])
        marker = project / ".blueprint" / "folder.json"
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        metadata["private_extension"] = {"owner": "自己"}
        marker.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        files = [self.workspace / "AGENTS.md", self.workspace / "private.py", marker, project / "AGENTS.md", project / ".blueprint" / "icon.png"]
        original = {path: path.read_bytes() for path in files}
        self.link()
        launcher = self.workspace / LAUNCHER_NAME
        launcher_before = launcher.read_bytes()
        self.assertEqual(json.loads(self.run_launcher("--version").stdout)["feature"], "first feature")

        self.write_engine("new feature after shared update")
        process = self.run_launcher("--version")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["feature"], "new feature after shared update")
        self.assertEqual(launcher.read_bytes(), launcher_before)
        self.assertEqual({path: path.read_bytes() for path in files}, original)

    def test_relink_keeps_unknown_settings_and_existing_launcher(self):
        self.link()
        config_path = self.workspace / ".blueprint" / "tool.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["personal"] = {"display_name": "我的工作室", "theme": "blue"}
        config["tool_path"] = "/old/location"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        launcher = self.workspace / LAUNCHER_NAME
        original_launcher = launcher.read_bytes()
        self.link()
        updated = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["personal"], config["personal"])
        self.assertEqual(updated["tool_path"], str(self.tool))
        self.assertEqual(updated["schema_version"], 1)
        self.assertEqual(launcher.read_bytes(), original_launcher)

    def test_stale_or_malformed_binding_gives_relink_instruction(self):
        self.link()
        config_path = self.workspace / ".blueprint" / "tool.json"
        for raw in (
            '{"tool_path": "/missing/blueprint-tool"}',
            '{"tool_path": "relative/tool"}',
            '{"tool_path": 123}',
            "{bad json",
            "[]",
        ):
            with self.subTest(raw=raw):
                config_path.write_text(raw, encoding="utf-8")
                process = self.run_launcher("search", "客服")
                self.assertEqual(process.returncode, 2)
                self.assertIn("重新連結", process.stderr)
                self.assertIn("blueprint.py link", process.stderr)
        config_path.unlink()
        self.link()
        (self.tool / "blueprint.py").unlink()
        process = self.run_launcher("search")
        self.assertEqual(process.returncode, 2)
        self.assertIn("重新連結", process.stderr)

    def test_overlapping_paths_are_rejected_before_writing(self):
        for path in (self.tool, self.tool / "private workspace", self.base):
            with self.subTest(path=path), self.assertRaises(FolderError):
                self.link(path)
            self.assertFalse((path / ".blueprint").exists())
            self.assertFalse((path / LAUNCHER_NAME).exists())
        self.assertFalse((self.tool / "private workspace").exists())

    def test_launcher_collision_and_invalid_configuration_are_not_modified(self):
        self.workspace.mkdir()
        launcher = self.workspace / LAUNCHER_NAME
        original = "# My own unrelated script\n"
        launcher.write_text(original, encoding="utf-8")
        with self.assertRaises(FolderError):
            self.link()
        self.assertEqual(launcher.read_text(encoding="utf-8"), original)
        self.assertFalse((self.workspace / ".blueprint").exists())
        launcher.unlink()
        private = self.workspace / ".blueprint"
        private.mkdir()
        config = private / "tool.json"
        for raw in (b"{bad json", b"[]"):
            with self.subTest(raw=raw):
                config.write_bytes(raw)
                with self.assertRaises(FolderError):
                    self.link()
                self.assertEqual(config.read_bytes(), raw)
                self.assertFalse(launcher.exists())

    def test_symlink_resources_are_rejected_without_touching_targets(self):
        self.workspace.mkdir()
        target = self.base / "do not modify.txt"
        target.write_text("original", encoding="utf-8")
        launcher = self.workspace / LAUNCHER_NAME
        try:
            launcher.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("This operating system does not allow test symlinks")
        with self.assertRaises(FolderError):
            self.link()
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertFalse((self.workspace / ".blueprint").exists())
        launcher.unlink()
        private = self.workspace / ".blueprint"
        outside = self.base / "outside metadata"
        outside.mkdir()
        private.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FolderError):
            self.link()
        self.assertEqual(list(outside.iterdir()), [])
        private.unlink()
        private.mkdir()
        config = private / "tool.json"
        config.symlink_to(target)
        with self.assertRaises(FolderError):
            self.link()
        self.assertEqual(target.read_text(encoding="utf-8"), "original")
        self.assertFalse(launcher.exists())


if __name__ == "__main__":
    unittest.main()
