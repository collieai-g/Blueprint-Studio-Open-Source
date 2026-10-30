"""CLI behavior remains parseable under the native Windows code page."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from blueprint_folders import cli
from blueprint_folders.workspaces import link_workspace

ROOT = Path(__file__).resolve().parents[1]


class CliProtocolTests(unittest.TestCase):
    def test_create_and_launcher_json_use_utf8_without_global_utf8_mode(self):
        environment = dict(os.environ)
        environment.pop('PYTHONUTF8', None)
        environment.pop('PYTHONIOENCODING', None)
        with tempfile.TemporaryDirectory(prefix='blueprint-cli-utf8-') as temp:
            root = Path(temp) / 'studio'
            created = subprocess.run([sys.executable, '-B', str(ROOT / 'blueprint.py'),
                                      'create', str(root), '啲嘢客服'], env=environment,
                                     capture_output=True, timeout=30)
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(json.loads(created.stdout.decode('utf-8'))['name'], '啲嘢客服')
            link_workspace(root)
            listed = subprocess.run([sys.executable, '-B', str(root / 'blueprint-studio.py'),
                                     'search', '啲嘢'], env=environment,
                                    capture_output=True, timeout=30)
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertEqual(json.loads(listed.stdout.decode('utf-8'))[0]['name'], '啲嘢客服')

    def call(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = cli.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_folder_handlers_keep_outputs_and_errors(self):
        with tempfile.TemporaryDirectory(prefix='blueprint-cli-handlers-') as temp:
            root = str(Path(temp) / 'studio')
            for args in (['init', root], ['create', root, 'synthetic'],
                         ['mark', str(Path(root) / 'synthetic'), '--description', 'kept'],
                         ['search', root], ['reindex', root],
                         ['inspect', str(Path(root) / 'synthetic')],
                         ['pack', str(Path(root) / 'synthetic'), '--output', str(Path(temp) / 'copy.zip')]):
                with self.subTest(command=args[0]):
                    code, output, errors = self.call(args)
                    self.assertEqual(code, 0, errors)
                    self.assertTrue(json.loads(output))
            code, output, errors = self.call(['create', root, 'synthetic'])
            self.assertEqual(code, 2)
            self.assertEqual(output, '')
            self.assertIn('error', json.loads(errors))

    def test_default_command_still_uses_legacy_gui(self):
        with patch.object(cli.webbrowser, 'open', return_value=True) as opening:
            code, output, errors = self.call([])
        self.assertEqual((code, output, errors), (0, '', ''))
        self.assertIn('/dist/index.html', opening.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
