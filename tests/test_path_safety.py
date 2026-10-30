"""Synthetic path boundaries, including real unprivileged Windows junctions."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from blueprint_folders.core import FolderError, create_folder
from blueprint_folders.packages import pack_folder
from blueprint_folders.path_safety import has_link, is_link
from blueprint_library import backup
from blueprint_library.store import LibraryError, Store


@contextmanager
def directory_link(link, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, timeout=15)
        if result.returncode:
            raise AssertionError('Windows junction creation failed: ' + repr(result.stderr))
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        yield link
    finally:
        # Remove only the synthetic link itself, never its target.
        if os.name == 'nt':
            os.rmdir(link)
        else:
            link.unlink()


class PathSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-path-safety-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        (self.outside / 'synthetic-secret.txt').write_text('synthetic only', encoding='utf-8')

    def test_reparse_detection_does_not_need_python312_is_junction(self):
        fake = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        with patch.object(Path, 'lstat', return_value=fake):
            self.assertTrue(is_link(self.root / 'junction'))

    def test_missing_path_is_not_a_link_but_errors_are_not_hidden(self):
        self.assertFalse(is_link(self.root / 'missing'))
        with patch.object(Path, 'lstat', side_effect=PermissionError('synthetic denied')):
            with self.assertRaises(PermissionError):
                is_link(self.root)

    def test_scan_rejects_external_junction_before_reading_target(self):
        with directory_link(self.data / 'blobs', self.outside):
            with patch.object(backup, 'digest', side_effect=AssertionError('must not read external data')):
                with self.assertRaises(ValueError):
                    backup.scan(self.data)

    def test_direct_and_lexical_ancestor_links_are_rejected(self):
        with directory_link(self.data / 'linked', self.outside) as link:
            for target in (link, link / 'new-file', link / '..' / 'ordinary'):
                with self.subTest(target=str(target)):
                    self.assertTrue(has_link(target))
                    with self.assertRaises(ValueError):
                        backup.safe_path(target)
            with self.assertRaises(FolderError):
                create_folder(link / 'nested', 'synthetic')
            with self.assertRaises(LibraryError):
                Store(link / 'new-store')
            self.assertFalse((self.outside / 'new-store').exists())
            self.assertFalse((self.outside / 'nested').exists())

    def test_relative_path_checks_ancestors_above_working_directory(self):
        with directory_link(self.root / 'linked-cwd', self.outside) as link:
            with patch('os.getcwd', return_value=str(link)):
                self.assertTrue(has_link(Path('not-created')))

    def test_restore_and_snapshot_reject_linked_output_ancestors(self):
        with directory_link(self.root / 'linked-output', self.outside) as link:
            with self.assertRaises(ValueError):
                backup.restore(self.root / 'does-not-exist', link / 'restored')
            with self.assertRaises(ValueError):
                backup.snapshot(self.data, link / 'snapshot')
            self.assertFalse((self.outside / 'snapshot').exists())
            self.assertFalse((self.outside / 'restored').exists())

    def test_folder_archive_rejects_nested_external_junction(self):
        folder = Path(create_folder(self.data, 'export')['path'])
        output = self.root / 'unsafe.zip'
        with directory_link(folder / 'linked', self.outside):
            with self.assertRaises(FolderError):
                pack_folder(folder, output)
            self.assertFalse(output.exists())

    def test_snapshot_destination_database_is_closed_on_return(self):
        (self.data / 'access.json').write_text('{}', encoding='utf-8')
        with sqlite3.connect(self.data / 'library.sqlite3') as connection:
            connection.execute('CREATE TABLE synthetic (id INTEGER PRIMARY KEY, value TEXT)')
        # sqlite3 connection context managers commit but do not close.
        connection.close()
        target = self.root / 'snapshot'
        backup.snapshot(self.data, target)
        database = target / 'data' / 'library.sqlite3'
        moved = database.with_name('moved.sqlite3')
        database.rename(moved)
        moved.unlink()
        self.assertFalse(database.exists())
        self.assertFalse(moved.exists())


if __name__ == '__main__':
    unittest.main()
