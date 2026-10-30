"""Source previews use isolated synthetic storage and the real authenticated UI."""
import base64
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from tools import dev_preview as preview
from blueprint_library import installer_preview as managed_preview


class DevPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='blueprint-dev-preview-test-')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.base = self.directory / 'previews'
        self.base_patch = patch.object(preview, 'PREVIEW_BASE', self.base)
        self.base_patch.start()
        self.addCleanup(self.base_patch.stop)
        self.password = secrets.token_urlsafe(24)

    def create(self):
        return Path(preview.create_preview(self.password))

    def serve(self, root, *, resuming=False):
        server = preview.start_preview(root, resuming=resuming)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stopped = False

        def stop():
            nonlocal stopped
            if not stopped:
                server.shutdown()
                server.server_close()
                thread.join(timeout=10)
                stopped = True
                self.assertFalse(thread.is_alive(), 'Preview HTTP server did not stop')
        self.addCleanup(stop)
        return server, stop

    def request(self, server, method, path, data=None, *, password=None, authenticated=True):
        headers = {'X-Blueprint-Request': '1'}
        if authenticated:
            credentials = f'{preview.USERNAME}:{password or self.password}'.encode()
            headers['Authorization'] = 'Basic ' + base64.b64encode(credentials).decode()
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode()
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection(*server.server_address[:2], timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_new_previews_have_distinct_external_storage_and_ignore_company_environment(self):
        protected = self.directory / 'synthetic-protected-company'
        protected.mkdir()
        sentinel = protected / 'do-not-use.txt'
        sentinel.write_bytes(b'synthetic protected data')
        before = {path.name: path.read_bytes() for path in protected.iterdir()}
        output = io.StringIO()
        with patch.dict(os.environ, {'BLUEPRINT_DATA_DIR': str(protected), 'BLUEPRINT_BACKUP_DIR': str(protected),
                                     'BLUEPRINT_ADMIN_USERNAME': 'unrelated-account',
                                     'BLUEPRINT_ADMIN_PASSWORD': 'synthetic-unrelated-password'}), contextlib.redirect_stdout(output):
            first, second = self.create(), self.create()
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.base.resolve())
        self.assertEqual(second.parent, self.base.resolve())
        for root in [first, second]:
            self.assertTrue((root / 'data' / 'access.json').is_file())
            self.assertTrue((root / 'data' / 'library.sqlite3').is_file())
            self.assertNotEqual((root / 'data').resolve(), (root / 'backups').resolve())
            self.assertFalse(any((parent / '.git').exists() for parent in (root, *root.parents)))
        self.assertEqual(before, {path.name: path.read_bytes() for path in protected.iterdir()})
        self.assertNotIn(self.password, output.getvalue())
        self.assertNotIn('synthetic-unrelated-password', output.getvalue())

    def test_real_library_web_requires_login_and_stopping_one_preview_preserves_another(self):
        first_root, second_root = self.create(), self.create()
        first, stop_first = self.serve(first_root)
        second, _ = self.serve(second_root)
        self.assertEqual(first.server_address[0], '127.0.0.1')
        self.assertEqual(second.server_address[0], '127.0.0.1')
        self.assertNotEqual(first.server_port, second.server_port)
        self.assertEqual(self.request(first, 'GET', '/', authenticated=False)[0], 401)
        self.assertEqual(self.request(first, 'GET', '/api/info', password='wrong-preview-password')[0], 401)
        status, _, info = self.request(first, 'GET', '/api/info')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(info)['product_title'], '藍圖工作室開源開發預覽')
        web = Path(__file__).resolve().parents[1] / 'library_web'
        for route in ['/', '/workspace.js', '/style.css']:
            with self.subTest(route=route):
                status, headers, body = self.request(first, 'GET', route)
                self.assertEqual(status, 200)
                filename = 'index.html' if route == '/' else route[1:]
                self.assertEqual(body, (web / filename).read_bytes())
                self.assertNotIn(self.password.encode(), body)
        for path in ['/access.json', '/data/access.json', '/preview.json', '/backups/']:
            with self.subTest(private_path=path):
                self.assertEqual(self.request(first, 'GET', path)[0], 404)
        self.assertEqual(json.loads(self.request(first, 'GET', '/api/studio/agents')[2])['total'], 0)
        status, _, raw = self.request(first, 'POST', '/api/items', {'title': 'Synthetic preview item', 'url': 'https://example.com/preview'})
        self.assertEqual(status, 201)
        item = json.loads(raw)['item']
        self.assertEqual(self.request(second, 'GET', '/api/items/' + item['id'])[0], 404)
        stop_first()
        self.assertEqual(self.request(second, 'GET', '/api/info')[0], 200)
        resumed, _ = self.serve(first_root, resuming=True)
        self.assertEqual(json.loads(self.request(resumed, 'GET', '/api/items/' + item['id'])[2])['item'], item)

    def test_preview_title_does_not_change_a_native_server(self):
        root = self.create()
        ordinary = preview.make_server(root / 'data', host='127.0.0.1', port=0)
        thread = threading.Thread(target=ordinary.serve_forever, daemon=True)
        thread.start()
        try:
            status, _, body = self.request(ordinary, 'GET', '/api/info')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)['product_title'], 'Blueprint Studio Backend｜藍圖工作室（後台）')
        finally:
            ordinary.shutdown()
            ordinary.server_close()
            thread.join(timeout=10)

    def test_resume_refuses_unmarked_existing_or_outside_paths_without_initializing(self):
        unmarked = self.base / 'preview-unmarked'
        unmarked.mkdir(parents=True)
        sentinel = unmarked / 'library.sqlite3'
        sentinel.write_bytes(b'synthetic existing database sentinel')
        outside = self.directory / 'outside-preview-base'
        outside.mkdir()
        for root in [unmarked, outside, self.base / 'missing-preview']:
            with self.subTest(root=root.name), self.assertRaises((ValueError, OSError)):
                preview.start_preview(root)
        self.assertEqual(sentinel.read_bytes(), b'synthetic existing database sentinel')
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.base / 'missing-preview').exists())

    def test_new_preview_refuses_git_ancestor(self):
        for kind in ['directory', 'file']:
            repository = self.directory / ('synthetic-repository-' + kind)
            repository.mkdir()
            if kind == 'directory':
                (repository / '.git').mkdir()
            else:
                (repository / '.git').write_text('gitdir: synthetic-worktree', encoding='utf-8')
            nested_base = repository / 'preview-storage'
            with self.subTest(marker=kind), patch.object(preview, 'PREVIEW_BASE', nested_base), self.assertRaises((ValueError, OSError)):
                self.create()
            self.assertFalse(nested_base.exists())

    def test_tampered_marker_cannot_redirect_source_data_backup_or_identity(self):
        root = self.create()
        marker = root / preview.MARKER
        original = marker.read_bytes()
        value = json.loads(original)
        credentials = (root / 'data' / 'access.json').read_bytes()
        replacements = {'source': str(self.directory / 'other-source'), 'data': str(root / 'backups'),
                        'backups': str(root / 'data'), 'root': str(root.parent), 'username': 'other-account'}
        try:
            for field, replacement in replacements.items():
                marker.write_text(json.dumps({**value, field: replacement}), encoding='utf-8')
                with self.subTest(field=field), patch.object(preview, 'make_server') as make_server:
                    with self.assertRaises((ValueError, OSError)):
                        preview.start_preview(root)
                    make_server.assert_not_called()
                self.assertEqual((root / 'data' / 'access.json').read_bytes(), credentials)
                self.assertFalse((root / 'backups').exists())
        finally:
            marker.write_bytes(original)

    def test_resume_does_not_reinitialize_missing_login_or_maintenance_state(self):
        root = self.create()
        credentials = root / 'data' / 'access.json'
        with patch.object(preview, 'initialize_library') as initialize:
            with self.assertRaises((ValueError, OSError)):
                preview.start_preview(root, resuming=True)
            initialize.assert_not_called()
        self.assertFalse((root / 'backups').exists())
        saved = credentials.read_bytes()
        credentials.unlink()
        try:
            with patch.object(preview, 'initialize_library') as initialize:
                with self.assertRaises((ValueError, OSError)):
                    preview.start_preview(root)
                initialize.assert_not_called()
            self.assertFalse(credentials.exists())
        finally:
            credentials.write_bytes(saved)

    def test_cli_requires_private_terminal_and_never_prints_inherited_secrets(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'BLUEPRINT_ADMIN_PASSWORD': self.password,
                                     'OPENAI_API_KEY': 'synthetic-provider-secret'}), \
                patch.object(preview.sys.stdin, 'isatty', return_value=False), \
                patch.object(preview.getpass, 'getpass') as prompt, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(preview.main([]), 1)
            prompt.assert_not_called()
            self.assertNotIn('BLUEPRINT_ADMIN_PASSWORD', os.environ)
            self.assertNotIn('OPENAI_API_KEY', os.environ)
        self.assertFalse(self.base.exists())
        for secret in [self.password, 'synthetic-provider-secret']:
            self.assertNotIn(secret, output.getvalue() + errors.getvalue())

    def test_cli_rejects_custom_binding_flags_without_creating_preview(self):
        for args in [['--host', '0.0.0.0'], ['--port', '8787'], ['--data', str(self.directory)],
                     ['--backup-dir', str(self.directory)]]:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                preview.main(args)
            self.assertEqual(raised.exception.code, 2)
        self.assertFalse(self.base.exists())

    def test_password_dialog_masks_both_entries_and_returns_matching_value(self):
        # Synthetic value: no user clipboard, real window, or real secret involved.
        with patch('tkinter.Tk') as tk, patch('tkinter.simpledialog.askstring',
                                              side_effect=[self.password, self.password]) as ask:
            result = preview._dialog_password()
        self.assertEqual(result, self.password)
        self.assertEqual(ask.call_count, 2)
        for call in ask.call_args_list:
            self.assertTrue(call.kwargs.get('show'))
        tk.return_value.destroy.assert_called_once()

    def test_password_dialog_cancel_or_mismatch_refuses_creation(self):
        cases = [[None], [self.password, None], [self.password, self.password + 'x']]
        for answers in cases:
            with self.subTest(number_of_answers=len(answers)), patch('tkinter.Tk') as tk, \
                    patch('tkinter.simpledialog.askstring', side_effect=answers):
                with self.assertRaises(ValueError) as raised:
                    preview._dialog_password()
                self.assertNotIn(self.password, str(raised.exception))
                tk.return_value.destroy.assert_called_once()
        self.assertFalse(self.base.exists())

    def test_dialog_cli_checks_short_password_without_initializing_or_printing_it(self):
        short = 'short-test'
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}), patch.object(preview, '_dialog_password', return_value=short), \
                patch.object(preview.sys.stdin, 'isatty', return_value=False), \
                patch.object(preview, 'initialize_library') as initialize, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(preview.main(['--password-dialog']), 1)
            initialize.assert_not_called()
        self.assertFalse(self.base.exists())
        self.assertNotIn(short, out.getvalue() + err.getvalue())

    def test_dialog_cli_starts_with_verified_password_without_printing_it(self):
        server = MagicMock(server_port=12345)
        root = self.directory / 'synthetic-preview'
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {}), patch.object(preview, '_dialog_password', return_value=self.password), \
                patch.object(preview.sys.stdin, 'isatty', return_value=False), \
                patch.object(preview, 'create_preview', return_value=root) as create, \
                patch.object(preview, 'start_preview', return_value=server) as start, \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(preview.main(['--password-dialog']), 0)
        create.assert_called_once_with(self.password)
        start.assert_called_once_with(root, resuming=False)
        server.serve_forever.assert_called_once()
        server.server_close.assert_called_once()
        self.assertNotIn(self.password, out.getvalue() + err.getvalue())

    def managed_fixture(self):
        import test_installer as fixtures
        if fixtures.Ed25519PrivateKey is None:
            self.skipTest('Optional signing runtime is required.')
        fixture = fixtures.InstallerTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root = self.create()
        server = preview.start_preview(root)
        server.server_close()
        return root, fixture

    def test_managed_adoption_keeps_marker_data_credentials_and_native_backup_identity(self):
        from blueprint_library import backup
        root, fixture = self.managed_fixture()
        marker = (root / preview.MARKER).read_bytes()
        access = (root / 'data/access.json').read_bytes()
        coverage = backup.coverage(root / 'data')
        native = json.loads((root / 'backups/installation.json').read_text(encoding='utf-8'))
        result = managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
            fixture.keys, feed=str(fixture.feed))
        self.assertEqual(result['phase'], 'installed')
        self.assertTrue(result['backup_verified'])
        config = managed_preview.managed_preview_config(root, preview.ROOT)
        with patch.object(preview, 'make_server') as make_server, self.assertRaises(ValueError):
            preview.start_preview(root, resuming=True)
        make_server.assert_not_called()
        self.assertEqual(config['data_dir'], str(root / 'data'))
        self.assertEqual(config['backup_dir'], str(root / 'backups'))
        self.assertEqual(managed_preview.validate_preview_binding(config)['root'], str(root))
        self.assertEqual((root / preview.MARKER).read_bytes(), marker)
        self.assertEqual((root / 'data/access.json').read_bytes(), access)
        self.assertEqual(backup.coverage(root / 'data'), coverage)
        after = json.loads((root / 'backups/installation.json').read_text(encoding='utf-8'))
        self.assertEqual(after['service_id'], native['service_id'])
        self.assertEqual(after['last_backup']['id'], native['last_backup']['id'])
        sidecar = (root / managed_preview.MANAGED_MARKER).read_bytes()
        again = managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
            fixture.keys, feed=str(fixture.feed))
        self.assertEqual(again['version'], result['version'])
        self.assertEqual((root / managed_preview.MANAGED_MARKER).read_bytes(), sidecar)
        self.assertEqual(managed_preview.validate_preview_binding({'host': '127.0.0.1'}), None)
        # In the packaged child, the program namespace contains SOURCE. Its
        # sibling business-data domains still remain outside that source.
        with patch.object(backup, 'SOURCE', root / 'managed-installer/releases/synthetic'):
            self.assertEqual(managed_preview.validate_preview_binding(config)['data'], str(root / 'data'))

    def test_managed_binding_tamper_and_lost_data_do_not_fall_back_or_reinitialize(self):
        root, fixture = self.managed_fixture()
        managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
            fixture.keys, feed=str(fixture.feed))
        config = managed_preview.managed_preview_config(root, preview.ROOT)
        for changes in ({'data_dir': str(root / 'backups')}, {'modules': {'unexpected': {}}},
                        {'development_preview': {'root': str(root), 'source': str(self.directory)}}):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError):
                managed_preview.validate_preview_binding({**config, **changes})
        sidecar = root / managed_preview.MANAGED_MARKER
        saved = sidecar.read_bytes()
        sidecar.write_text('{}', encoding='utf-8')
        try:
            with patch.object(preview, 'start_preview') as start, patch.dict(os.environ, {}), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(preview.main(['--resume', str(root)]), 1)
                start.assert_not_called()
        finally:
            sidecar.write_bytes(saved)
        sidecar.unlink()
        try:
            with patch.object(preview, 'start_preview') as start, patch.dict(os.environ, {}), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(preview.main(['--resume', str(root)]), 1)
                start.assert_not_called()
        finally:
            sidecar.write_bytes(saved)
        credentials = root / 'data/access.json'
        original = credentials.read_bytes()
        credentials.unlink()
        try:
            with self.assertRaises((ValueError, OSError)):
                managed_preview.managed_preview_config(root, preview.ROOT)
            self.assertFalse(credentials.exists())
        finally:
            credentials.write_bytes(original)

    def test_interrupted_adoption_finishes_known_initialization_but_preserves_unknown_files(self):
        root, fixture = self.managed_fixture()
        with patch('blueprint_library.installer.create_checkpoint', side_effect=ValueError('synthetic interruption')):
            with self.assertRaisesRegex(ValueError, 'synthetic interruption'):
                managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
                    fixture.keys, feed=str(fixture.feed))
        self.assertFalse((root / managed_preview.MANAGED_MARKER).exists())
        self.assertTrue((root / 'managed-installer/state.json').is_file())
        with patch.object(preview, 'start_preview') as start, patch.dict(os.environ, {}), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(preview.main(['--resume', str(root)]), 1)
            start.assert_not_called()
        result = managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
            fixture.keys, feed=str(fixture.feed))
        self.assertTrue(result['backup_verified'])
        other = self.create()
        server = preview.start_preview(other)
        server.server_close()
        program = other / 'managed-installer'
        program.mkdir()
        sentinel = program / 'unknown.txt'
        sentinel.write_bytes(b'unknown unfinished operation')
        with self.assertRaises((ValueError, OSError)):
            managed_preview.enable_preview_installer(other, preview.ROOT, fixture.old,
                fixture.keys, feed=str(fixture.feed))
        self.assertEqual(sentinel.read_bytes(), b'unknown unfinished operation')
        self.assertFalse((other / managed_preview.MANAGED_MARKER).exists())

    def test_managed_cli_requires_explicit_existing_preview_and_complete_candidate_arguments(self):
        for args in (['--enable-installer'], ['--enable-installer', '--resume', str(self.base / 'missing')],
                     ['--release', 'synthetic.json']):
            with self.subTest(args=args), patch.dict(os.environ, {}), \
                    patch.object(preview, 'create_preview') as create, \
                    patch.object(preview.getpass, 'getpass') as prompt, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(preview.main(args), 1)
                create.assert_not_called()
                prompt.assert_not_called()

    def test_managed_resume_verifies_ready_identity_before_announcing_and_uses_dynamic_port(self):
        from blueprint_library.installer import _state, _config
        root, fixture = self.managed_fixture()
        managed_preview.enable_preview_installer(root, preview.ROOT, fixture.old,
            fixture.keys, feed=str(fixture.feed))
        program = root / 'managed-installer'
        original_state = _state(program)
        calls, announced = [], []
        class FakeSupervisor:
            def __init__(self, directory, **kwargs):
                self.config = _config(directory)
                self.stop_event = threading.Event()
                self.stop_timeout = 1
            def run(self):
                self.stop_event.wait(5)
            def stop(self):
                self.stop_event.set()
            def _get(self):
                return {**original_state, 'child': {'pid': 123, 'release_id': 'synthetic-release'}}
            def _internal(self, child):
                calls.append(child)
                return {'ready': True, 'development_preview': len(calls) > 1,
                        'product_title': managed_preview.PREVIEW_TITLE, 'pid': 123,
                        'version': fixture.old['manifest']['version'], 'database_schema': 8}
        stop = threading.Event()
        def ready(value):
            announced.append(value)
            stop.set()
        with patch('blueprint_library.installer.Supervisor', FakeSupervisor):
            self.assertEqual(managed_preview.run_managed_preview(root, source=preview.ROOT,
                announce=ready, stop_event=stop, startup_timeout=5), 0)
        self.assertEqual(len(announced), 1)
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(announced[0]['url'], 'http://127.0.0.1:' + str(_config(program)['port']) + '/')
        self.assertEqual(announced[0]['pid'], 123)
        self.assertEqual(announced[0]['source'], str(preview.ROOT))


if __name__ == '__main__':
    unittest.main()
