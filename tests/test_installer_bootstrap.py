"""First-install environment ownership and failure boundaries (synthetic only)."""
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

from blueprint_library import installer_bootstrap as bootstrap
from tests.test_path_safety import directory_link
from tools import studio_installer


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-bootstrap-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'program'
        self.runtime = self.base / 'program-python'
        self.data = self.base / 'data'

    def platform(self):
        return patch.multiple(bootstrap.platform,
            python_implementation=lambda: 'CPython', machine=lambda: 'AMD64')

    @staticmethod
    def fake_venv(path):
        executable = Path(path) / 'Scripts' / 'python.exe'
        executable.parent.mkdir(exist_ok=True)
        executable.write_bytes(b'synthetic owned executable')

    def prepare(self, **kwargs):
        with patch.object(bootstrap.sys, 'platform', 'win32'), self.platform(), \
                patch.object(bootstrap.venv.EnvBuilder, 'create', side_effect=self.fake_venv):
            return bootstrap.ensure_runtime(self.root, protected=[self.data], **kwargs)

    def test_lock_contains_fixed_transitive_wheels_and_minimum_python(self):
        lock, digest, requirements = bootstrap._lock_document()
        self.assertIn([3, 10], lock['python_versions'])
        self.assertEqual(len(digest), 64)
        self.assertEqual({p['name'] for p in lock['packages']},
            {'cryptography', 'cffi', 'pycparser', 'typing_extensions', 'tzdata'})
        self.assertEqual(requirements.count('=='), 5)
        self.assertIn('--hash=sha256:', requirements)

    def test_new_environment_is_outside_program_and_data(self):
        with patch.object(bootstrap, '_verify') as verify, patch.object(bootstrap, '_install') as install:
            executable = self.prepare()
        self.assertEqual(executable, self.runtime / 'Scripts' / 'python.exe')
        self.assertFalse(self.root.exists())
        self.assertFalse(self.data.exists())
        self.assertEqual(json.loads((self.runtime / 'bootstrap.json').read_text('utf-8'))['program_root'], str(self.root))
        verify.assert_called_once(); install.assert_not_called()

    def test_dependency_failure_leaves_data_and_claims_untouched(self):
        with patch.object(bootstrap, '_verify', side_effect=ValueError('missing dependency')), \
                patch.object(bootstrap, '_install', side_effect=subprocess.CalledProcessError(1, ['pip'])):
            with self.assertRaises(subprocess.CalledProcessError):
                self.prepare()
        self.assertFalse(self.root.exists()); self.assertFalse(self.data.exists())
        self.assertTrue((self.runtime / 'bootstrap.json').is_file())

    def test_owned_partial_setup_can_resume_without_recreating_environment(self):
        with patch.object(bootstrap, '_verify', side_effect=[ValueError('missing'), None]), \
                patch.object(bootstrap, '_install') as install:
            self.prepare()
        install.assert_called_once()
        with patch.object(bootstrap, '_verify'), patch.object(bootstrap.venv.EnvBuilder, 'create') as create, \
                patch.object(bootstrap.sys, 'platform', 'win32'), self.platform():
            bootstrap.ensure_runtime(self.root, protected=[self.data])
        create.assert_not_called()

    def test_existing_install_does_not_rebind_python(self):
        self.root.mkdir(); (self.root / 'installation.json').write_text('{}', encoding='utf-8')
        with patch.object(bootstrap, '_install') as install:
            self.assertIsNone(bootstrap.ensure_runtime(self.root))
        self.assertFalse(self.runtime.exists()); install.assert_not_called()

    def test_unknown_runtime_directory_is_not_overwritten(self):
        self.runtime.mkdir(); sentinel = self.runtime / 'private.txt'; sentinel.write_text('synthetic')
        with self.assertRaisesRegex(ValueError, '未有相符'):
            self.prepare()
        self.assertEqual(sentinel.read_text(), 'synthetic')

    def test_runtime_cannot_overlap_data(self):
        with patch.object(bootstrap.sys, 'platform', 'win32'), self.platform():
            with self.assertRaisesRegex(ValueError, '重疊'):
                bootstrap.ensure_runtime(self.root, protected=[self.runtime])
        self.assertFalse(self.runtime.exists())

    def test_rejects_source_and_git_directories(self):
        with self.assertRaises(ValueError):
            bootstrap.ensure_runtime(bootstrap.ROOT / 'unsafe-runtime')
        (self.base / '.git').mkdir()
        with self.assertRaises(ValueError):
            bootstrap.ensure_runtime(self.root)

    def test_invalid_hash_lock_rejected_before_environment_creation(self):
        lock = self.base / 'bad-lock.json'
        value, _, _ = bootstrap._lock_document()
        value['packages'][0]['sha256'] = ['not-a-hash']
        lock.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ValueError):
            self.prepare(lock_file=lock)
        self.assertFalse(self.runtime.exists())

    def test_malformed_lock_shapes_fail_before_environment_creation(self):
        valid, _, _ = bootstrap._lock_document()
        missing = json.loads(json.dumps(valid))
        missing['packages'][0].pop('version')
        shapes = [None, [], {}, {'format': 'blueprint-runtime-lock/1', 'packages': 'invalid'},
                  {**valid, 'python_versions': '3.12'}, missing,
                  {**valid, 'packages': [None]}]
        filename = self.base / 'invalid-shape.json'
        for value in shapes:
            with self.subTest(value=value):
                filename.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    self.prepare(lock_file=filename)
                self.assertFalse(self.runtime.exists())

    def test_broken_python_link_is_rejected_without_rebuilding_venv(self):
        with patch.object(bootstrap, '_verify'):
            executable = self.prepare()
        executable.unlink()
        with directory_link(executable, self.base / 'missing-target'), \
                patch.object(bootstrap.sys, 'platform', 'win32'), self.platform(), \
                patch.object(bootstrap.venv.EnvBuilder, 'create') as create, \
                patch.object(bootstrap, '_install') as install:
            self.assertFalse(executable.exists())
            with self.assertRaises(ValueError):
                bootstrap.ensure_runtime(self.root, protected=[self.data])
            create.assert_not_called(); install.assert_not_called()
        self.assertFalse(self.data.exists()); self.assertFalse(self.root.exists())

    def test_partial_environment_rejects_library_junction_before_pip(self):
        with patch.object(bootstrap, '_verify'):
            self.prepare()
        outside = self.base / 'outside'
        outside.mkdir()
        sentinel = outside / 'private.txt'
        sentinel.write_text('synthetic unchanged', encoding='utf-8')
        with directory_link(self.runtime / 'Lib', outside), \
                patch.object(bootstrap.sys, 'platform', 'win32'), self.platform(), \
                patch.object(bootstrap, '_verify', side_effect=ValueError('missing')), \
                patch.object(bootstrap, '_install') as install:
            with self.assertRaises(ValueError):
                bootstrap.ensure_runtime(self.root, protected=[self.data])
            install.assert_not_called()
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'synthetic unchanged')
        self.assertEqual([p.name for p in outside.iterdir()], ['private.txt'])

    def test_pip_preserves_proxy_ca_and_keeps_stdout_for_json(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://synthetic-proxy.invalid',
                'PYTHONPATH': 'synthetic-untrusted-import-path', 'PYTHONHOME': 'synthetic-untrusted-home',
                'PIP_CERT': 'synthetic-ca.pem', 'PIP_EXTRA_INDEX_URL': 'https://untrusted.invalid'}), \
                patch.object(bootstrap.subprocess, 'run') as run:
            bootstrap._install(Path('python.exe'), Path('lock.txt'))
        argv = run.call_args.args[0]; options = run.call_args.kwargs
        for flag in ['--only-binary=:all:', '--no-deps', '--require-hashes']:
            self.assertIn(flag, argv)
        self.assertEqual(argv[1:4], ['-I', '-m', 'pip'])
        self.assertEqual(options['env']['HTTPS_PROXY'], 'http://synthetic-proxy.invalid')
        self.assertEqual(options['env']['PIP_CERT'], 'synthetic-ca.pem')
        self.assertNotIn('PIP_EXTRA_INDEX_URL', options['env'])
        self.assertIs(options['stdout'], sys.stderr)

    def cli_arguments(self):
        return ['init', '--root', str(self.root), '--data', str(self.data),
                '--backups', str(self.base / 'backups'), '--release', str(self.base / 'release.json'),
                '--trusted-keys', str(self.base / 'keys.json')]

    def test_cli_rejects_invalid_flags_before_dependency_provision(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(studio_installer.sys, 'platform', 'win32'), \
                patch.object(bootstrap, 'ensure_runtime') as prepare, \
                redirect_stdout(output), redirect_stderr(errors):
            code = studio_installer.main(self.cli_arguments() + ['--existing-backups'])
        self.assertEqual(code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertIn('--existing-data', json.loads(errors.getvalue())['error'])
        prepare.assert_not_called()

    def test_cli_child_uses_isolated_owned_environment(self):
        executable = self.runtime / 'Scripts' / 'python.exe'
        with patch.object(studio_installer.sys, 'platform', 'win32'), \
                patch.object(bootstrap, 'ensure_runtime', return_value=executable), \
                patch.object(studio_installer.subprocess, 'call', return_value=0) as call:
            self.assertEqual(studio_installer.main(self.cli_arguments()), 0)
        arguments = call.call_args.args[0]
        self.assertEqual(arguments[:3], [str(executable), '-I', '-B'])
        self.assertEqual(arguments[4:], self.cli_arguments())

    def test_cli_help_needs_no_signature_dependency(self):
        result = subprocess.run([sys.executable, '-B', str(bootstrap.ROOT / 'tools/studio_installer.py'), '--help'],
            capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8'))


if __name__ == '__main__':
    unittest.main()
