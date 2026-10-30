"""Signed channel plans and native backup evidence reuse on isolated studios."""
import base64
import copy
from contextlib import contextmanager
from http.client import BadStatusLine, HTTPException, IncompleteRead, InvalidURL
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import test_installer as fixtures
from blueprint_library import backup
from blueprint_library import installer, installer_http
from blueprint_library.installer import CoreSupervisor, Supervisor, _config, _state, read_status, request_apply, create_checkpoint
from blueprint_library.installer_release import canonical_json
from blueprint_library.installer_components import configure_channel
from blueprint_library.maintenance import Maintenance, DataLease, atomic_json
from blueprint_library.store import LibraryError


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

    def controller(self, component_route):
        if component_route:
            self.bind(self.catalog([self.fixture.new]))
            controller = Supervisor(self.root)
        else:
            self.fixture.feed.write_text(json.dumps({'releases': [self.fixture.new]}), encoding='utf-8')
            controller = CoreSupervisor(self.root)
        ready = controller.check()
        self.assertEqual(ready['phase'], 'ready')
        return controller, ready

    def apply_through_handler(self, status):
        body = {'release_id': status['prepared']['release_id'],
                'expected_version': status['version'], 'drafts_saved': True}
        handler = SimpleNamespace(
            identity={'username': 'synthetic-owner', 'role': 'owner'},
            headers={'X-Blueprint-Account': 'synthetic-owner'},
            server=SimpleNamespace(installer_root=str(self.root),
                                   store=SimpleNamespace(data_dir=self.fixture.data)),
            _body=lambda: body, _json=Mock())
        try:
            self.assertTrue(installer_http.handle(handler, 'POST', '/api/installer/apply'))
        except LibraryError as error:
            return error.status, {'error': str(error)}
        payload, code = handler._json.call_args.args
        return code, payload

    def assert_atomic_check(self, component_route):
        controller, ready = self.controller(component_route)
        accepted_code, accepted = self.apply_through_handler(ready)
        self.assertEqual(accepted_code, 202)
        # A check that starts after acknowledgement must retain the same intent.
        self.assertEqual(controller.check()['phase'], 'apply_requested')
        self.assertEqual(read_status(self.root)['operation_id'], accepted['operation_id'])
        controller._update(phase='ready', request={})
        ready = read_status(self.root)
        unlocked, resume = threading.Event(), threading.Event()
        errors = []
        checked = []
        checker = None
        real_lock = installer._lock

        @contextmanager
        def scheduled_lock(root, name, **kwargs):
            with real_lock(root, name, **kwargs):
                yield
            # Retain the real OS lock and pause only after its first release.
            if (threading.current_thread() is checker and name == '.state.lock'
                    and not unlocked.is_set()):
                unlocked.set()
                if not resume.wait(10):
                    raise AssertionError('Synthetic checker schedule did not resume')

        def check():
            try:
                checked.append(controller.check())
            except Exception as error:
                errors.append(error)

        checker = threading.Thread(target=check, name='synthetic-check-apply-interleave')
        with patch('blueprint_library.installer._lock', scheduled_lock):
            checker.start()
            try:
                self.assertTrue(unlocked.wait(10), 'Checker never released its first real state lock')
                code, response = self.apply_through_handler(ready)
                self.assertIn(code, (202, 409))
            finally:
                resume.set()
                checker.join(10)
        self.assertFalse(checker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(checked), 1)
        current = read_status(self.root)
        if code == 202:
            self.assertEqual(current['phase'], 'apply_requested',
                             'A 202 acknowledgement was overwritten by background preparation')
            self.assertEqual(current['operation_id'], response['operation_id'])
        else:
            self.assertEqual(current['phase'], 'ready')
            retried_code, retried = self.apply_through_handler(current)
            self.assertEqual(retried_code, 202)
            self.assertEqual(read_status(self.root)['phase'], 'apply_requested')
            self.assertEqual(read_status(self.root)['operation_id'], retried['operation_id'])

    def test_core_check_guard_and_preparing_transition_are_atomic(self):
        self.assert_atomic_check(False)

    def test_component_check_guard_and_preparing_transition_are_atomic(self):
        self.assert_atomic_check(True)

    @staticmethod
    def failing_opener(error, stage='open'):
        opener, response = Mock(), MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{}'
        opener.open.return_value = response
        if stage == 'open':
            opener.open.side_effect = error
        elif stage == 'read':
            response.read.side_effect = error
        else:
            response.__exit__.side_effect = error
        return opener

    def test_feed_http_protocol_failures_are_normalized_without_hiding_programming_errors(self):
        for error in (InvalidURL('synthetic invalid target'), BadStatusLine('synthetic invalid status'),
                      IncompleteRead(b'synthetic partial JSON', 42)):
            for stage in ('open', 'read', 'close'):
                with self.subTest(error=type(error).__name__, stage=stage):
                    opener = self.failing_opener(error, stage)
                    with patch('blueprint_library.installer.build_opener', return_value=opener):
                        with self.assertRaises(ValueError) as caught:
                            installer.read_feed('https://updates.example.invalid/catalog.json')
                    self.assertIs(caught.exception.__cause__, error)
        # Keep the overall byte limit intact while exercising the parser's
        # separate nesting boundary. This remains valid JSON rooted in a dict.
        nested = b'{"releases":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}'
        opener = Mock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = nested
        opener.open.return_value = response
        with patch('blueprint_library.installer.build_opener', return_value=opener):
            with self.assertRaises(ValueError) as caught:
                installer.read_feed('https://updates.example.invalid/catalog.json')
        self.assertIsInstance(caught.exception.__cause__, RecursionError)
        with patch('blueprint_library.installer.build_opener',
                   return_value=self.failing_opener(RuntimeError('synthetic programming error'))):
            with self.assertRaisesRegex(RuntimeError, 'synthetic programming error'):
                installer.read_feed('https://updates.example.invalid/catalog.json')

    def assert_metadata_failure_keeps_owned_loop(self, component_route):
        controller, _ = self.controller(component_route)
        config = _config(self.root)
        config['feed'] = 'https://updates.example.invalid/catalog.json'
        atomic_json(self.root / 'installation.json', config)
        controller.config = config
        controller._internal = Mock(return_value={'ready': True, 'version': '2.10.0', 'database_schema': 8})
        controller._launch = Mock(side_effect=AssertionError('The owned old service must remain running'))
        controller._stop_child = Mock()

        for error in (InvalidURL('synthetic invalid target'), BadStatusLine('synthetic invalid status'),
                      IncompleteRead(b'synthetic partial JSON', 42)):
            with self.subTest(error=type(error).__name__):
                controller._update(phase='installed', child={'pid': 101,
                    'release_id': controller._get()['installed']['release_id']}, check_requested=True)
                controller._stop_child.reset_mock()
                observations = []

                class StopAfterChecks:
                    stopped = False

                    def wait(self, timeout):
                        observations.append({'status': read_status(controller.root),
                            'stop_calls': controller._stop_child.call_count})
                        self.stopped = len(observations) >= 3
                        return self.stopped

                stop = StopAfterChecks()
                controller.stop_event = stop
                failure = None
                opener = self.failing_opener(error)
                with patch('blueprint_library.installer.build_opener', return_value=opener):
                    try:
                        controller.run()
                    except HTTPException as escaped:
                        failure = escaped
                self.assertIsNone(failure, {'escaped': repr(failure),
                    'explicit_stop_requested': stop.stopped,
                    'old_service_stop_calls': controller._stop_child.call_count})
                self.assertTrue(stop.stopped)
                self.assertEqual(len(observations), 3)
                self.assertTrue(all(item['stop_calls'] == 0 for item in observations))
                self.assertEqual([item['status']['phase'] for item in observations[1:]],
                                 ['prepare_failed', 'prepare_failed'])
                self.assertTrue(all(item['status']['installed']['version'] == '2.10.0'
                                    for item in observations))
                controller._stop_child.assert_called_once()
                controller._launch.assert_not_called()

    def test_core_metadata_protocol_failure_keeps_owned_run_loop_available(self):
        self.assert_metadata_failure_keeps_owned_loop(False)

    def test_component_metadata_protocol_failure_keeps_owned_run_loop_available(self):
        self.assert_metadata_failure_keeps_owned_loop(True)

    def test_feed_sources_keep_local_spaces_and_encoded_https_paths(self):
        local = self.fixture.work / 'synthetic feed with spaces.json'
        local.write_text('{"releases": []}', encoding='utf-8')
        self.assertEqual(installer.read_feed(local), {'releases': []})
        self.assertEqual(installer.read_feed(local.as_uri()), {'releases': []})
        opener = Mock()
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"releases": []}'
        opener.open.return_value = response
        encoded = 'https://updates.example.invalid/release%20feed.json'
        with patch('blueprint_library.installer.build_opener', return_value=opener):
            self.assertEqual(installer.read_feed(encoded), {'releases': []})
        self.assertEqual(opener.open.call_args.args[0].full_url, encoded)
        for invalid in ('https://updates.example.invalid/feed file.json',
                        'https://updates.example.invalid/feed\tfile.json',
                        'https://updates.example.invalid/feed\x7ffile.json'):
            with self.subTest(source=repr(invalid)), patch(
                    'blueprint_library.installer.build_opener', return_value=opener) as opening:
                with self.assertRaises(ValueError):
                    installer.read_feed(invalid)
                opening.assert_not_called()


if __name__ == '__main__':
    unittest.main()
