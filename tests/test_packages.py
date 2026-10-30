"""The actual folder exporter must preserve project files and existing backups."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from blueprint_folders.core import FolderError, create_folder
from blueprint_folders.packages import pack_folder


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.folder = Path(create_folder(self.base / "工作室", "空白起步")["path"])

    def test_export_keeps_source_binary_guidance_and_identity(self):
        content = "print('廣東話 🤖')\n".encode("utf-8")
        (self.folder / "main.py").write_bytes(content)
        binary = bytes(range(256))
        (self.folder / "sample.bin").write_bytes(binary)
        output = self.base / "export.zip"
        summary = pack_folder(self.folder, output)
        with zipfile.ZipFile(output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("空白起步/main.py"), content)
            self.assertEqual(archive.read("空白起步/sample.bin"), binary)
            self.assertEqual(archive.read("空白起步/AGENTS.md"), (self.folder / "AGENTS.md").read_bytes())
            original = json.loads((self.folder / ".blueprint/folder.json").read_text(encoding="utf-8"))
            exported = json.loads(archive.read("空白起步/.blueprint/folder.json"))
            self.assertEqual(exported["id"], original["id"])
            self.assertEqual(exported["description"], "")
            self.assertEqual(summary["files"], len(archive.infolist()))

    def test_existing_backup_and_project_are_not_overwritten(self):
        output = self.base / "existing.zip"
        output.write_bytes(b"keep this backup")
        with self.assertRaises(FolderError):
            pack_folder(self.folder, output)
        self.assertEqual(output.read_bytes(), b"keep this backup")
        inside = self.folder / "recursive.zip"
        with self.assertRaises(FolderError):
            pack_folder(self.folder, inside)
        self.assertFalse(inside.exists())

    def test_external_symlink_contents_are_not_packaged(self):
        outside = self.base / "outside.txt"
        outside.write_text("external data")
        try:
            (self.folder / "linked.txt").symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not permit symlink creation")
        output = self.base / "unsafe.zip"
        with self.assertRaises(FolderError):
            pack_folder(self.folder, output)
        self.assertFalse(output.exists())
