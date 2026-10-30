"""Signed channel plans and native backup evidence reuse on isolated studios."""
import base64
import copy
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import test_installer as fixtures
from blueprint_library import backup
from blueprint_library.installer import Supervisor, _config, _state, read_status, request_apply, create_checkpoint
from blueprint_library.installer_release import canonical_json
from blueprint_library.installer_components import configure_channel
from blueprint_library.maintenance import Maintenance, DataLease, atomic_json


@unittest.skipIf(fixtures.Ed25519PrivateKey is None, 'Optional signing runtime is required.')
class ComponentInstallerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstallerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize()
        self.root = self.fixture.root

    def catalog(self, releases, sequence=1, revoked=None, audience=None):
        value = {'contract_version': 1, 'channel': 'test', 'sequence': sequence,
                 'audience': audience or ['*'], 'expires_at': int(time.time()) + 3600,
                 'releases': releases, 'revoked': revoked or []}
        return {'catalog': value, 'key_id': 'publisher', 'signature': base64.b64encode(
            self.fixture.private.sign(canonical_json(value))).decode()}

    def bind(self, catalog):
        self.fixture.feed.write_text(json.dumps(catalog), encoding='utf-8')
        configure_channel(self.root, self.fixture.feed, 'test')

    def test_native_checkpoint_is_reused_by_operations_and_actual_next_startup(self):
        config = _config(self.root)
        with DataLease(self.fixture.data):
            first = create_checkpoint(config, 'first-update')
            second = create_checkpoint(config, 'second-update')
        self.assertEqual(first['native_backup_id'], second['native_backup_id'])
        self.assertEqual(first['private_copy'], second['private_copy'])
        self.assertTrue(second['reused_native_backup'])
        with patch.object(Maintenance, '_snapshot', side_effect=AssertionError('duplicate backup')):
            native = Maintenance(self.fixture.data, self.fixture.backups)
            try:
                native.prepare()
                self.assertEqual(native.status(actor='synthetic-owner')['last_backup']['id'], first['native_backup_id'])
            finally:
                native.close()

    def test_changed_data_invalidates_protection_without_overwriting_old_snapshot(self):
        config = _config(self.root)
        with DataLease(self.fixture.data):
            first = create_checkpoint(config, 'first-update')
        (self.fixture.data / 'synthetic-note.txt').write_bytes(b'new data')
        with DataLease(self.fixture.data):
            second = create_checkpoint(config, 'different-update')
        self.assertNotEqual(first['native_backup_id'], second['native_backup_id'])
        self.assertTrue(Path(first['private_copy']).is_file())
        self.assertNotEqual(first['coverage'], second['coverage'])

    def test_same_operation_does_not_reuse_expired_protection(self):
        config = _config(self.root)
        with DataLease(self.fixture.data):
            first = create_checkpoint(config, 'retained-operation')
        archive = Path(first['private_copy'])
        digest = backup.digest(archive)
        with patch('blueprint_library.maintenance.time.time', return_value=time.time() + 172800):
            with DataLease(self.fixture.data), self.assertRaisesRegex(ValueError, '過期'):
                create_checkpoint(config, 'retained-operation')
        self.assertEqual(backup.digest(archive), digest)

    def test_signed_core_catalog_prepares_and_applies_same_fixed_plan(self):
        self.bind(self.catalog([self.fixture.new]))
        supervisor = Supervisor(self.root)
        ready = supervisor.check()
        self.assertEqual(ready['phase'], 'ready')
        self.assertEqual(ready['plan'], [{'component_id': 'blueprint-core', 'from': '2.10.0', 'to': '2.10.1'}])
        pending = request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        with patch.object(supervisor, '_stop_child'), patch.object(supervisor, '_launch'):
            result = supervisor.apply()
        self.assertEqual(result['phase'], 'complete')
        self.assertEqual(result['current']['version'], '2.10.1')
        self.assertEqual(result['operation_id'], pending['operation_id'])

    def test_catalog_replay_and_withdrawal_never_stop_the_old_service(self):
        self.bind(self.catalog([self.fixture.new], sequence=2))
        supervisor = Supervisor(self.root)
        ready = supervisor.check()
        self.assertEqual(ready['phase'], 'ready')
        request_apply(self.root, ready['prepared']['release_id'], ready['version'])
        self.fixture.feed.write_text(json.dumps(self.catalog([], sequence=3)), encoding='utf-8')
        with patch.object(supervisor, '_stop_child') as stop:
            result = supervisor.apply()
        self.assertEqual(result['phase'], 'prepare_failed')
        stop.assert_not_called()
        self.fixture.feed.write_text(json.dumps(self.catalog([self.fixture.new], sequence=1)), encoding='utf-8')
        self.assertEqual(supervisor.check()['phase'], 'prepare_failed')

    def test_private_channel_cannot_be_retargeted_to_another_studio(self):
        self.bind(self.catalog([self.fixture.new], audience=['different-studio']))
        before = backup.coverage(self.fixture.data)
        result = Supervisor(self.root).check()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertEqual(before, backup.coverage(self.fixture.data))


if __name__ == '__main__':
    unittest.main()
