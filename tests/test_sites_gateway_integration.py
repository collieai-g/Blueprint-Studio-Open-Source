"""Generated Worker -> real native API -> backup download -> isolated restore.

Everything uses temporary test accounts and loopback. This is not evidence of
live Sites authentication, platform publication, or tools loaded in a user chat.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from blueprint_library import backup
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio
from tools.build_sites_gateway import build

ROOT = Path(__file__).resolve().parents[1]


class SitesGatewayIntegrationTests(unittest.TestCase):
    def test_generated_gateway_shares_real_data_permissions_and_downloadable_restore(self):
        with tempfile.TemporaryDirectory(prefix='blueprint-gateway-test-') as directory:
            root = Path(directory)
            data, backups = root / 'data', root / 'backups'
            initialize_library(data, 'owner', 'temporary-owner-password')
            Studio(Store(data)).save_user({'username': 'staff', 'password': 'temporary-member-password', 'role': 'member'})
            worker = root / 'worker.mjs'
            built = build(worker)
            self.assertEqual(built['deployment'], 'not_performed')
            with self.assertRaises(FileExistsError): build(worker)
            with self.assertRaises(ValueError): build(ROOT / 'forbidden-worker.mjs')
            server = make_server(data, managed=True, backup_dir=backups)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = subprocess.run(['node', str(ROOT / 'tests/sites_gateway_fixture.mjs')],
                    input=json.dumps({'worker': str(worker), 'local_backend': f'http://127.0.0.1:{server.server_port}',
                        'archive': str(root / 'download.zip'), 'accounts': {
                            'owner-id': {'username': 'owner', 'password': 'temporary-owner-password'},
                            'member-id': {'username': 'staff', 'password': 'temporary-member-password'}}}),
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                evidence = json.loads(result.stdout)
                self.assertEqual(evidence['page_sha256'], hashlib.sha256((ROOT / 'library_web/index.html').read_bytes()).hexdigest())
                backup.unpack(root / 'download.zip', root / 'snapshot', evidence['archive_sha256'])
                backup.restore(root / 'snapshot', root / 'restored')
                restored = Store(root / 'restored')
                self.assertEqual(restored.get_item(evidence['item_id'])['title'], '跨入口資料')
                self.assertEqual(restored.get_file(evidence['file_id'])[0].read_bytes(), b'original attachment')
                self.assertEqual((root / 'restored/access.json').read_bytes(), (data / 'access.json').read_bytes())
            finally:
                server.shutdown(); server.server_close(); thread.join()
            # A new process/adapter version can use the same data and credentials.
            restarted = make_server(data, managed=True, backup_dir=backups)
            try:
                self.assertEqual(restarted.store.get_item(evidence['item_id'])['title'], '跨入口資料')
                self.assertEqual(restarted.store.get_file(evidence['file_id'])[0].read_bytes(), b'original attachment')
            finally:
                restarted.server_close()
