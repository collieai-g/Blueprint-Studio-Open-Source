"""Real, synthetic managed-preview adoption, HTTP update and restart acceptance.

The test retains its evidence in a new private temporary directory. It never uses
an existing preview, real customer data, model provider or channel connection.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import queue
import secrets
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import zipfile

from blueprint_library import backup
from blueprint_library.installer import _state, read_status
from blueprint_library.installer_preview import (
    MANAGED_MARKER, PREVIEW_TITLE, enable_preview_installer, run_managed_preview,
)
from blueprint_library.installer_release import canonical_json
from blueprint_library.store import Store
from blueprint_library.studio import Studio
from tools import dev_preview
from tools.package_managed_release import build
from tools.package_release_catalog import build_catalog

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey = None


@unittest.skipIf(Ed25519PrivateKey is None, 'Managed preview acceptance requires the cryptography extra.')
class ManagedPreviewRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='blueprint-managed-preview-runtime-'))
        self.work.chmod(0o700)
        self.password = secrets.token_urlsafe(30)
        self.runners = []

    def tearDown(self):
        for runner in self.runners:
            runner['stop'].set()
        for runner in self.runners:
            runner['thread'].join(180)
            self.assertFalse(runner['thread'].is_alive(), 'Owned preview wrapper did not stop: ' + str(self.work))

    def api(self, url, method, path, body=None, *, username='preview', password=None):
        parts = urlsplit(url)
        headers = {'Authorization': 'Basic ' + base64.b64encode(
            (username + ':' + (self.password if password is None else password)).encode('utf-8')).decode('ascii'),
            'X-Blueprint-Account': username}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-Blueprint-Request': '1',
                            'Origin': parts.scheme + '://' + parts.netloc})
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
        try:
            connection.request(method, path, None if body is None else json.dumps(body).encode('utf-8'), headers)
            response = connection.getresponse()
            payload = response.read()
            if response.getheader('Content-Type', '').startswith('application/json'):
                payload = json.loads(payload)
            return response.status, payload
        finally:
            connection.close()

    def launch(self, root):
        stop = threading.Event()
        announced = queue.Queue()
        errors = []
        def run():
            try:
                run_managed_preview(root, source=dev_preview.ROOT, announce=announced.put,
                                    stop_event=stop, startup_timeout=120)
            except BaseException as error:
                errors.append(str(error))
        worker = threading.Thread(target=run, name='synthetic-managed-preview-acceptance')
        runner = {'stop': stop, 'thread': worker, 'errors': errors}
        self.runners.append(runner)
        worker.start()
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            self.assertFalse(errors, {'errors': errors, 'fixture': str(self.work)})
            try:
                ready = announced.get(timeout=.1)
                self.assertTrue(ready['url'].startswith('http://127.0.0.1:'))
                self.assertGreater(ready['pid'], 0)
                return runner, ready
            except queue.Empty:
                self.assertTrue(worker.is_alive(), 'Preview exited before verified readiness: ' + str(self.work))
        self.fail('Preview readiness timed out: ' + str(self.work))

    def stop(self, runner, program):
        runner['stop'].set()
        runner['thread'].join(180)
        self.assertFalse(runner['thread'].is_alive(), 'Owned preview did not stop: ' + str(self.work))
        self.assertFalse(runner['errors'], runner['errors'])
        state = _state(program)
        self.assertFalse(state.get('child'))
        self.assertFalse(state.get('module_children'))

    def test_real_signed_update_keeps_original_preview_and_authenticated_installer_api(self):
        key = Ed25519PrivateKey.generate()
        private = self.work / 'test-only-signing.key'
        private.write_bytes(base64.b64encode(key.private_bytes_raw()))
        private.chmod(0o600)
        keys = {'preview-test-publisher': base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii')}
        old_zip, old_json = self.work / 'core-old.zip', self.work / 'core-old.json'
        build(old_zip, old_json, private, 'preview-test-publisher', str(old_zip),
              allow_working_tree=True, platforms=[sys.platform], notes='僅供合成預覽更新流程驗收。')
        old = json.loads(old_json.read_text(encoding='utf-8'))
        new = deepcopy(old)
        version = list(map(int, old['manifest']['version'].split('.')))
        version[2] += 1
        new_version = '.'.join(map(str, version))
        new_zip = self.work / 'core-next-test-only.zip'
        with zipfile.ZipFile(old_zip) as original, zipfile.ZipFile(new_zip, 'x', compression=zipfile.ZIP_DEFLATED) as target:
            for member in original.infolist():
                value = original.read(member.filename)
                if member.filename == 'blueprint_folders/__init__.py':
                    value = value.replace(old['manifest']['version'].encode(), new_version.encode())
                target.writestr(member, value)
                new['manifest']['files'][member.filename] = hashlib.sha256(value).hexdigest()
        new['manifest'].update(version=new_version, notes='僅供合成預覽驗收的下一版候選，並非正式發行。',
            source_tree=hashlib.sha256(canonical_json(new['manifest']['files'])).hexdigest())
        new['manifest']['archive'].update(url=str(new_zip), size=new_zip.stat().st_size, sha256=backup.digest(new_zip))
        new['signature'] = base64.b64encode(key.sign(canonical_json(new['manifest']))).decode('ascii')
        (self.work / 'core-next-test-only.json').write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding='utf-8')
        feed = self.work / 'signed-test-catalog.json'
        build_catalog([new], feed, private, 'preview-test-publisher', keys, channel='test',
                      audience=['*'], sequence=1, expires_at=int(time.time()) + 3600)
        with patch.object(dev_preview, 'PREVIEW_BASE', self.work / 'previews'):
            root = dev_preview.create_preview(self.password)
            store = Store(root / 'data')
            attachment = store.add_file('synthetic-retained.txt', b'Synthetic managed preview attachment.', actor='preview')
            note = store.create_item({'title': 'Synthetic retained note', 'url': 'https://example.com/managed-preview',
                                      'notes': 'Keep this synthetic note, attachment, identity and history.'}, actor='preview')
            owner = Studio(store, {'username': 'preview', 'role': 'owner'})
            owner.save_user({'username': 'synthetic-member', 'password': 'synthetic-member-password', 'role': 'member'})
            member = Studio(store, {'username': 'synthetic-member', 'role': 'member'})
            owner.save_preferences({'expected_version': 0, 'data': {'monthly_review': True, 'groups': ['work', 'company']}})
            member.save_preferences({'expected_version': 0, 'data': {'monthly_review': False, 'groups': ['company']}})
            for account, title in [(owner, 'Synthetic owner layout'), (member, 'Synthetic member layout')]:
                account.save_presentation({'key': 'overview', 'expected_version': 0, 'data': {
                    'version': 1, 'layoutVersion': 4, 'pages': 1, 'columns': 43, 'mode': 'ai',
                    'visible': ['heading'], 'cards': {'heading': {'x': 1, 'y': 0, 'w': 16, 'h': 3, 'collapsed': False}},
                    'texts': {'heading': title}}})
            owner_layout, member_layout = owner.presentation(), member.presentation()
            owner_preferences, member_preferences = owner.preferences(), member.preferences()
            original_marker = (root / dev_preview.MARKER).read_bytes()
            original_access = (root / 'data/access.json').read_bytes()
            ordinary = dev_preview.start_preview(root)
            ordinary.server_close()
            dev_preview._validate(root, resuming=True)
        native_before = json.loads((root / 'backups/installation.json').read_text(encoding='utf-8'))
        old_backup = native_before['last_backup']['id']
        old_backup_zip = root / 'backups' / old_backup / 'backup.zip'
        old_backup_sha = backup.digest(old_backup_zip)
        coverage = backup.coverage(root / 'data')
        adopted = enable_preview_installer(root, dev_preview.ROOT, old, keys, feed=str(feed))
        self.assertTrue(adopted['backup_verified'])
        self.assertEqual(backup.coverage(root / 'data'), coverage)
        program = root / 'managed-installer'
        self.assertEqual(_state(program)['checkpoint']['native_backup_id'], old_backup)
        sidecar = (root / MANAGED_MARKER).read_bytes()
        runner, ready = self.launch(root)
        try:
            url = ready['url']
            status, info = self.api(url, 'GET', '/api/info')
            self.assertEqual(status, 200, info)
            self.assertEqual(info['product_title'], PREVIEW_TITLE)
            self.assertEqual(info['development_preview_mode'], 'managed_candidate')
            self.assertEqual(info['version'], old['manifest']['version'])
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status, public = self.api(url, 'GET', '/api/installer')
                self.assertEqual(status, 200, public)
                self.assertTrue(public['supported'])
                self.assertNotIn('token', json.dumps(public))
                if public['phase'] == 'ready' and public['can_apply']:
                    break
                self.assertFalse(runner['errors'], runner['errors'])
                time.sleep(.1)
            else:
                self.fail('Prepared update not visible in real authenticated API: ' + str(public))
            apply = {'release_id': public['prepared']['release_id'], 'expected_version': public['version'], 'drafts_saved': True}
            status, queued = self.api(url, 'POST', '/api/installer/apply', apply)
            self.assertEqual(status, 202, queued)
            operation_id = queued['operation_id']
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                state = _state(program)
                self.assertNotEqual(state['phase'], 'recovery_required', read_status(program))
                self.assertFalse(runner['errors'], runner['errors'])
                if state['phase'] == 'complete' and state['installed']['version'] == new_version:
                    break
                time.sleep(.1)
            else:
                self.fail('Managed preview apply timed out: ' + str(read_status(program)))
            status, updated = self.api(url, 'GET', '/api/info')
            self.assertEqual(status, 200, updated)
            self.assertEqual(updated['version'], new_version)
            self.assertEqual(updated['product_title'], PREVIEW_TITLE)
            self.assertEqual(updated['development_preview_mode'], 'managed_candidate')
            new_pid = _state(program)['child']['pid']
            self.assertNotEqual(new_pid, ready['pid'])
            status, replay = self.api(url, 'POST', '/api/installer/apply', apply)
            self.assertEqual(status, 202, replay)
            self.assertTrue(replay['idempotent'])
            self.assertEqual(replay['operation_id'], operation_id)
            self.assertEqual(len(_state(program)['history']), 1)
            self.assertEqual(self.api(url, 'GET', '/api/items/' + note['id']), (200, {'item': note}))
            self.assertEqual(self.api(url, 'GET', '/api/files/' + attachment['id']), (200, b'Synthetic managed preview attachment.'))
            self.assertEqual(self.api(url, 'GET', '/api/studio/presentation'), (200, owner_layout))
            self.assertEqual(self.api(url, 'GET', '/api/studio/preferences'), (200, owner_preferences))
            member_auth = {'username': 'synthetic-member', 'password': 'synthetic-member-password'}
            self.assertEqual(self.api(url, 'GET', '/api/studio/presentation', **member_auth), (200, member_layout))
            self.assertEqual(self.api(url, 'GET', '/api/studio/preferences', **member_auth), (200, member_preferences))
            self.assertNotEqual(owner_layout, member_layout)
        finally:
            self.stop(runner, program)
        self.assertEqual(backup.coverage(root / 'data'), coverage)
        self.assertEqual((root / dev_preview.MARKER).read_bytes(), original_marker)
        self.assertEqual((root / 'data/access.json').read_bytes(), original_access)
        self.assertEqual((root / MANAGED_MARKER).read_bytes(), sidecar)
        native_after = json.loads((root / 'backups/installation.json').read_text(encoding='utf-8'))
        self.assertEqual(native_after['service_id'], native_before['service_id'])
        self.assertEqual(backup.digest(old_backup_zip), old_backup_sha)
        restarted, second = self.launch(root)
        try:
            status, info = self.api(second['url'], 'GET', '/api/info')
            self.assertEqual(status, 200, info)
            self.assertEqual(info['version'], new_version)
            self.assertEqual(info['product_title'], PREVIEW_TITLE)
            self.assertEqual(info['development_preview_mode'], 'managed_candidate')
            status, public = self.api(second['url'], 'GET', '/api/installer')
            self.assertEqual(status, 200, public)
            self.assertTrue(public['supported'])
            self.assertEqual(public['current']['version'], new_version)
            self.assertEqual(self.api(second['url'], 'GET', '/api/files/' + attachment['id']), (200, b'Synthetic managed preview attachment.'))
            self.assertEqual(self.api(second['url'], 'GET', '/api/studio/presentation'), (200, owner_layout))
            self.assertEqual(self.api(second['url'], 'GET', '/api/studio/presentation', **member_auth), (200, member_layout))
        finally:
            self.stop(restarted, program)
        self.assertEqual(backup.coverage(root / 'data'), coverage)
        self.assertEqual((root / 'data/access.json').read_bytes(), original_access)
        self.assertEqual((root / dev_preview.MARKER).read_bytes(), original_marker)
        evidence = {'scope': 'Synthetic isolated acceptance only; no existing preview or real channel/model.',
            'root': str(root), 'versions': [old['manifest']['version'], new_version],
            'first_url': ready['url'], 'restart_url': second['url'], 'original_pid': ready['pid'], 'updated_pid': new_pid,
            'operation_id': operation_id, 'coverage': coverage, 'native_service_id': native_before['service_id'],
            'retained_backup_id': old_backup, 'retained_backup_sha256': old_backup_sha,
            'old_package_sha256': backup.digest(old_zip), 'new_package_sha256': backup.digest(new_zip),
            'marker_sha256': hashlib.sha256(original_marker).hexdigest(),
            'credential_file_sha256': hashlib.sha256(original_access).hexdigest(),
            'assertions': ['real authenticated installer API supported', 'real HTTP apply and same operation retry',
                'PID and actual version changed', 'preview title/mode retained', 'original data/credential/marker retained',
                'native identity and original backup retained', 'same binding restart retained installed version',
                'two account layouts and customer overrides retained and isolated'],
            'cleanup': {'child': _state(program).get('child'), 'module_children': _state(program).get('module_children', {})}}
        (self.work / 'acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
