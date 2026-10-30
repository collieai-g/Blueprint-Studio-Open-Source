"""Component failures preserve truthful versions and unrelated core availability.

The service doubles model separate running processes. No listener, customer data,
signing key, package download or real process is used by these control-flow tests.
"""
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from blueprint_library.installer import InstallerBusyError
from blueprint_library.installer_components import ComponentSupervisor
from blueprint_library.installer_release import release_id


COMPONENT = 'bluesai-cs-0001'


def release(component, version, schema):
    manifest = {'component_id': component, 'version': version,
                'database_schema': schema,
                'source_repository': 'https://github.com/example/synthetic-agent',
                'source_revision': version.replace('.', '0').ljust(40, '0')}
    return {'component_id': component, 'version': version, 'manifest': manifest,
            'release_id': release_id(manifest), 'envelope': {'manifest': manifest}}


class ServiceDouble:
    def __init__(self, state, installed):
        self.state, self.actual = state, installed
        self.calls = []
        self.paused = False
        self.fail_launch = False
        self.fail_health = False
        self.status_reads = 0

    def control(self, action=None):
        self.calls.append(('control', action))
        if action is None:
            self.status_reads += 1
            if self.fail_health and self.status_reads > 1:
                raise OSError('Synthetic module health timeout')
        if action == 'resume':
            self.paused = False
        return {'ready': True, 'paused': self.paused,
                'version': self.actual['version'],
                'database_schema': self.actual['manifest']['database_schema']}

    def quiesce(self):
        self.calls.append(('quiesce', None))
        self.paused = True

    def stop(self):
        self.calls.append(('stop', None))
        self.paused = True
        self.state['module_children'].pop(COMPONENT, None)

    def launch(self, candidate):
        self.calls.append(('launch', candidate['version']))
        if self.fail_launch:
            raise ValueError('Synthetic module startup failure')
        self.actual = candidate
        self.paused = True
        self.state['module_children'][COMPONENT] = {
            'pid': 303, 'release_id': candidate['release_id'], 'token': 'synthetic-module-token'}


class StopAfterObservations:
    """Observe live loop state before requesting the normal shutdown path."""
    def __init__(self, fixture, count=2):
        self.fixture, self.count = fixture, count
        self.observations = []

    def wait(self, timeout):
        self.observations.append({'state': deepcopy(self.fixture.state),
                                  'core_stops': self.fixture.controller._stop_child.call_count})
        return len(self.observations) >= self.count


class ComponentFailureTests(unittest.TestCase):
    def test_public_constructor_and_component_module_import_in_either_order(self):
        for first in ('installer_components', 'installer'):
            subprocess.run([sys.executable, '-X', 'utf8', '-B', '-c',
                'import blueprint_library.' + first + '; '
                'from blueprint_library.installer import Supervisor; '
                'from blueprint_library.installer_components import ComponentSupervisor; '
                'assert Supervisor is ComponentSupervisor'],
                cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, timeout=15)

    def setUp(self):
        self.core = release('blueprint-core', '2.10.0', 8)
        self.old = release(COMPONENT, '1.0.0', 7)
        self.new = release(COMPONENT, '1.0.1', 7)
        self.state = {'version': 1, 'phase': 'apply_requested',
                      'installed': self.core,
                      'components': {'blueprint-core': self.core, COMPONENT: self.old},
                      'child': {'pid': 101, 'release_id': self.core['release_id']},
                      'module_children': {COMPONENT: {'pid': 202, 'release_id': self.old['release_id']}},
                      'prepared_plan': {'plan_digest': 'synthetic-plan', 'releases': [self.new]},
                      'request': {'operation_id': 'synthetic-operation'}, 'history': []}
        self.controller = object.__new__(ComponentSupervisor)
        self.controller.root = Path('synthetic-no-filesystem-access')
        self.controller.config = {'trusted_keys': {},
                                  'modules': {COMPONENT: {'node': 'synthetic-unused-node'}}}
        self.controller.check_interval = 300
        self.controller._get = lambda: deepcopy(self.state)
        self.controller._update = self.update
        self.controller._catalog = Mock(return_value={
            'releases': [{'manifest': self.new['manifest']}], 'revoked': []})
        self.controller._internal = Mock(return_value={
            'ready': True, 'version': self.core['version'], 'database_schema': 8})
        self.controller._stop_child = Mock(side_effect=lambda: self.state.update(child=None))
        self.controller._launch = Mock(side_effect=AssertionError('Core must stay running'))
        self.controller._healthy = Mock()
        self.controller.check = Mock()
        # This suite deliberately has no files or signing keys. Package
        # integrity is exercised by the signed-release suites; here its
        # successful boundary must be explicit to reach the injected failures.
        self.controller._verify_installed = Mock()
        self.runtime = ServiceDouble(self.state, self.old)
        self.controller.modules = {COMPONENT: self.runtime}
        self.agent_calls = []
        self.agent_busy = False
        self.late_busy = False
        self.upgraded = False
        self.controller._agent = self.agent
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch('blueprint_library.installer_components._lock',
                                         side_effect=lambda *args, **kwargs: nullcontext()))
        self.patches.enter_context(patch('blueprint_library.installer_components.read_status',
                                         side_effect=lambda root: deepcopy(self.state)))
        self.prepare = self.patches.enter_context(patch(
            'blueprint_library.installer_components.prepare_release', return_value=self.new))
        self.checkpoint = self.patches.enter_context(patch(
            'blueprint_library.installer_components.module_checkpoint',
            return_value={'verified': True, 'restore_verified': True}))

    def update(self, **changes):
        self.state.update(deepcopy(changes))
        self.state['version'] += 1
        return deepcopy(self.state)

    def agent(self, component, candidate=None, operation_id=None, *, action=None, check_busy=True):
        self.agent_calls.append((component, candidate, action, check_busy))
        busy = self.agent_busy or (self.late_busy and self.upgraded and candidate is None and action is None)
        if check_busy and busy:
            raise InstallerBusyError('Synthetic retained pending job')
        if candidate is not None:
            self.upgraded = True
        current = self.new if self.upgraded else self.old
        return {'busy': busy, 'agent': {'id': 'synthetic-original-agent',
                'manifest': {'id': COMPONENT, 'version': current['version']},
                'source': {key: current['manifest']['source_' + key]
                           for key in ('repository', 'revision')}}}

    def run_once(self):
        stopper = StopAfterObservations(self)
        self.controller.stop_event = stopper
        self.controller.run()
        self.assertEqual(len(stopper.observations), 2,
                         'The loop must remain available until explicitly stopped')
        for observed in stopper.observations:
            self.assertEqual(observed['core_stops'], 0,
                             'A module failure must not stop the running core')
            self.assertEqual(observed['state']['child']['pid'], 101)
        # Only the deliberate final stop is allowed to stop the core.
        self.controller._stop_child.assert_called_once()
        return stopper.observations

    def assert_preflight_safe(self):
        result = self.controller.apply()
        self.assertEqual(result['phase'], 'prepare_failed')
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.agent_calls, [])
        self.assertEqual(self.state['components'][COMPONENT]['version'], self.old['version'])
        self.controller._stop_child.assert_not_called()
        self.checkpoint.assert_not_called()

    def test_unavailable_catalog_does_not_stop_or_pause_any_service(self):
        self.controller._catalog.side_effect = OSError('Synthetic catalog outage')
        self.assert_preflight_safe()
        self.controller._catalog.assert_called_once()
        self.prepare.assert_not_called()

    def test_damaged_prepared_package_does_not_stop_or_pause_any_service(self):
        self.prepare.side_effect = ValueError('Synthetic artifact hash mismatch')
        self.assert_preflight_safe()
        self.prepare.assert_called_once()

    def test_installed_integrity_refusal_precedes_catalog_and_service_effects(self):
        self.controller._verify_installed.side_effect = InstallerBusyError('Synthetic retained customization')
        self.assert_preflight_safe()
        self.controller._verify_installed.assert_called_once()
        self.controller._catalog.assert_not_called()
        self.prepare.assert_not_called()

    def test_module_checkpoint_failure_leaves_core_available_in_recovery(self):
        self.checkpoint.side_effect = OSError('Synthetic full backup disk')
        observed = self.run_once()[-1]['state']
        self.assertEqual(observed['phase'], 'recovery_required')
        self.assertNotIn(COMPONENT, observed['module_children'])
        self.assertEqual(observed['components'][COMPONENT]['version'], self.old['version'])
        self.assertFalse(any(action == 'launch' for action, value in self.runtime.calls))

    def test_busy_after_new_launch_preserves_observed_version_and_requires_recovery(self):
        self.late_busy = True
        result = self.controller.apply()
        self.assertEqual(result['phase'], 'recovery_required')
        self.assertEqual(self.runtime.actual['version'], self.new['version'])
        self.assertTrue(self.runtime.paused)
        self.assertEqual(result['observed_components'][COMPONENT]['version'], self.new['version'])
        self.assertTrue(result['observed_components'][COMPONENT]['runtime_verified'])
        self.assertFalse(result['observed_components'][COMPONENT]['registry_verified'])
        self.assertEqual(result['components'][COMPONENT]['version'], self.old['version'])
        self.assertEqual(result.get('completed_components'), [])
        self.controller._stop_child.assert_not_called()

    def test_matching_bootstrap_allows_pending_jobs_without_upgrading_or_stopping_core(self):
        self.state['phase'] = 'installed'
        self.agent_busy = True
        observed = self.run_once()
        self.assertTrue(all(value['state']['phase'] == 'installed' for value in observed))
        self.assertFalse(any(candidate is not None for component, candidate, action, busy in self.agent_calls))
        self.assertTrue(any(action == 'agent-resume' and not busy
                            for component, candidate, action, busy in self.agent_calls))
        self.assertFalse(any(action == 'launch' for action, value in self.runtime.calls))

    def test_bootstrap_module_launch_failure_keeps_core_available(self):
        self.state['phase'] = 'installed'
        self.state['module_children'].clear()
        self.runtime.fail_launch = True
        observed = self.run_once()
        self.assertTrue(all(value['state']['phase'] == 'recovery_required' for value in observed))
        self.assertEqual(self.runtime.calls, [('launch', self.old['version'])])
        self.assertFalse(self.upgraded)

    def test_module_health_timeout_keeps_core_available(self):
        self.state['phase'] = 'installed'
        self.runtime.fail_health = True
        observed = self.run_once()
        self.assertEqual(observed[-1]['state']['phase'], 'recovery_required')
        self.assertIn(COMPONENT, observed[-1]['state']['module_children'])
        self.controller._healthy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
