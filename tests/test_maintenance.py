"""Real files/HTTP recovery: first install, repeated upgrades and failure paths."""
import base64
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
import zipfile

from blueprint_library import backup
from blueprint_library.maintenance import Maintenance, DataLease, RequestGate, atomic_json
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from tools.check_installation import verify_installation


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-lifecycle-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        self.backups = self.root / 'backups'
        self.password = 'only-for-local-lifecycle-tests'
        initialize_library(self.data, 'owner', self.password, company_profile={'name': '測試公司'})
        self.item = Store(self.data).add_file('retain.txt', b'attachment-before-update')

    def runtime(self, version='2.5.0', fingerprint='release-a'):
        return Maintenance(self.data, self.backups, version=version, fingerprint=fingerprint)

    def test_first_boot_restore_drill_and_repeated_releases_preserve_data(self):
        first = self.runtime().prepare()
        snapshot = self.backups / first.state['last_backup']['id']
        self.assertTrue(first.status()['last_backup']['restore_verified'])
        first.close()
        original_auth = (self.data / 'access.json').read_bytes()
        for version, code in [('2.5.1','release-b'), ('2.6.0','release-c')]:
            runtime = self.runtime(version, code).prepare()
            self.assertEqual(runtime.state['product_version'], version)
            backup.verify(self.data, snapshot)
            runtime.close()
        self.assertEqual((self.data / 'access.json').read_bytes(), original_auth)
        self.assertEqual(Store(self.data).get_file(self.item['id'])[0].read_bytes(), b'attachment-before-update')

    def test_same_source_restart_does_not_repeat_backup_or_reset_data(self):
        first = self.runtime().prepare(); last = first.state['last_backup']; first.close()
        Store(self.data).create_item({'title': 'After first boot', 'url': 'https://example.com'})
        second = self.runtime().prepare()
        self.assertEqual(second.state['last_backup'], last)
        second.close()

    def test_rehearsal_failure_leaves_live_database_and_login_unchanged(self):
        first = self.runtime().prepare(); first.close()
        original = (self.data / 'library.sqlite3').read_bytes()
        def invalid_migration(path):
            raise ValueError('simulated bad candidate')
        with self.assertRaises(ValueError):
            self.runtime('2.5.1','broken').prepare(invalid_migration)
        self.assertEqual((self.data / 'library.sqlite3').read_bytes(), original)
        self.assertFalse(list(self.root.glob('data.failed-*')))

    def test_failed_live_migration_restores_snapshot_and_keeps_failed_contents(self):
        first = self.runtime().prepare(); first.close()
        def migration(path):
            store = Store(path)
            if path == self.data:
                store.create_item({'title': 'Keep failed candidate data', 'url': 'https://example.com'})
                raise ValueError('failure after rehearsal')
        with self.assertRaises(ValueError):
            self.runtime('2.5.1','broken').prepare(migration)
        state = json.loads((self.backups / 'installation.json').read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'recovered_choose_previous_release')
        self.assertTrue(Path(state['preserved_failed_data']).is_dir())
        self.assertEqual(Store(self.data).get_file(self.item['id'])[0].read_bytes(), b'attachment-before-update')
        with self.assertRaises(ValueError):
            self.runtime('2.5.1','broken').prepare()
        old = self.runtime().prepare(); old.close()

    def test_interrupted_update_recovers_even_if_data_was_renamed(self):
        old = self.runtime().prepare()
        state = dict(old.state)
        state['pending'] = {'snapshot': state['last_backup']['id'], 'code': 'interrupted'}
        old.close()
        atomic_json(self.backups / 'installation.json', state)
        self.data.rename(self.root / 'interrupted-data')
        with self.assertRaises(ValueError):
            self.runtime('2.5.1','interrupted').prepare()
        self.assertTrue((self.data / 'access.json').is_file())
        self.assertTrue((self.root / 'interrupted-data').is_dir())

    def test_downgrade_and_overlapping_backup_locations_are_rejected(self):
        old = self.runtime().prepare(); old.close()
        with self.assertRaises(ValueError): self.runtime('2.4.2','older').prepare()
        with self.assertRaises(ValueError): Maintenance(self.data, self.data / 'backup')
        with self.assertRaises(ValueError): Maintenance(self.data, self.root)

    def test_restore_refuses_corrupt_backup_or_existing_target(self):
        saved = self.root / 'snapshot'
        backup.snapshot(self.data, saved)
        with self.assertRaises(ValueError): backup.restore(saved, self.data)
        (saved / 'data/access.json').write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError): backup.restore(saved, self.root / 'restored')
        self.assertFalse((self.root / 'restored').exists())

    def test_backup_rejects_unlisted_files_and_symlinks(self):
        saved = self.root / 'snapshot'; backup.snapshot(self.data, saved)
        (saved / 'data/extra').write_text('unexpected')
        with self.assertRaises(ValueError): backup.check(saved)
        (saved / 'data/extra').unlink()
        with self.subTest(capability='symlink'):
            try:
                (saved / 'data/extra').symlink_to(self.data / 'access.json')
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise
            with self.assertRaises(ValueError): backup.check(saved)

    def test_zip_import_rejects_traversal_and_duplicate_names(self):
        for index, names in enumerate([['../outside'], ['data/duplicate', 'data/duplicate']]):
            archive = self.root / f'unsafe-{index}.zip'
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(archive, 'w') as z:
                    for name in names: z.writestr(name, 'unsafe')
            target = self.root / f'import-{index}'
            with self.assertRaises(ValueError): backup.unpack(archive, target, backup.digest(archive))
            self.assertFalse(target.exists())

    def test_other_process_cannot_open_managed_data_or_take_lease(self):
        runtime = self.runtime().prepare()
        try:
            command = 'from blueprint_library.store import Store; import sys; Store(sys.argv[1])'
            result = subprocess.run([sys.executable, '-c', command, str(self.data)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            with self.assertRaises(ValueError):
                with DataLease(self.data): pass
        finally:
            runtime.close()
        result = subprocess.run([sys.executable, '-c', command, str(self.data)], capture_output=True)
        self.assertEqual(result.returncode, 0)

    def test_backup_gate_waits_for_requests_and_reopens_after_failure(self):
        gate = RequestGate(); entered = threading.Event(); release = threading.Event()
        def request():
            with gate.request(): entered.set(); release.wait(5)
        worker = threading.Thread(target=request); worker.start(); entered.wait(5)
        paused = threading.Event()
        def saving():
            with gate.pause(): paused.set()
        saver = threading.Thread(target=saving); saver.start()
        self.assertFalse(paused.wait(.05))
        release.set(); worker.join(5); saver.join(5)
        self.assertTrue(paused.is_set())
        with self.assertRaises(ValueError):
            with gate.pause(): raise ValueError('backup failed')
        with gate.request(): self.assertFalse(gate.paused)

    def test_low_disk_space_blocks_startup_before_migration(self):
        usage = shutil._ntuple_diskusage(1024, 1024, 0)
        with patch('blueprint_library.maintenance.shutil.disk_usage', return_value=usage):
            with self.assertRaises(ValueError): self.runtime().prepare()
        self.assertTrue((self.data / 'access.json').exists())

    def test_idle_http_client_cannot_block_backup_indefinitely(self):
        server = make_server(self.data, managed=True, backup_dir=self.backups)
        server.request_timeout = .1
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            previous = server.maintenance.state['last_backup']['id']
            with socket.create_connection(('127.0.0.1', server.server_port), timeout=2):
                deadline = time.monotonic() + 3
                while not server.maintenance.gate.readers and time.monotonic() < deadline:
                    time.sleep(.005)
                self.assertEqual(server.maintenance.gate.readers, 1)
                server.maintenance.request_backup()
                while server.maintenance.state['last_backup']['id'] == previous and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertNotEqual(server.maintenance.state['last_backup']['id'], previous)
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_managed_http_download_restore_authentication_and_scheduled_backup(self):
        from blueprint_library.studio import Studio
        from blueprint_library.mcp_server import Bridge
        Studio(Store(self.data)).save_user({'username': 'staff', 'password': 'staff-testing-password', 'role': 'member'})
        server = make_server(self.data, managed=True, backup_dir=self.backups)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(HTTPError) as denied:
                urlopen(url + '/api/maintenance')
            self.assertEqual(denied.exception.code, 401)
            staff = 'Basic ' + base64.b64encode(b'staff:staff-testing-password').decode()
            for path in ['/api/maintenance', '/api/maintenance/backups/' + server.maintenance.state['last_backup']['id']]:
                with self.assertRaises(HTTPError) as denied:
                    urlopen(Request(url + path, headers={'Authorization': staff}))
                self.assertEqual(denied.exception.code, 403)
            with patch.dict('os.environ', {'BLUEPRINT_BACKEND_USERNAME':'owner', 'BLUEPRINT_BACKEND_PASSWORD':self.password}):
                bridge = Bridge(url)
                self.assertEqual(len(bridge.tools()), 52)
                self.assertTrue(bridge.call('blueprint_backup_status', {})['supported'])
                self.assertNotIn('blueprint_backup_create', {t['name'] for t in Bridge(url, read_only=True).tools()})
                result = verify_installation(url, self.root / 'private-acceptance')
            self.assertTrue(result['authenticated_restore'])
            self.assertFalse(result['ai_connection_verified'])
            self.assertFalse(result['off_host_durable_copy_verified'])
            previous = server.maintenance.state['last_backup']['id']
            server.maintenance.state['last_backup']['created_at'] = 0
            server.maintenance.wake.set()
            deadline = time.monotonic() + 5
            while server.maintenance.state['last_backup']['id'] == previous and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertNotEqual(server.maintenance.state['last_backup']['id'], previous)
            self.assertEqual(server.maintenance.state['last_backup']['reason'], 'scheduled')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
