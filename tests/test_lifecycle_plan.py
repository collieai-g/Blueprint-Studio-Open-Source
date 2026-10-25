"""Risk routes and frozen-input receipt invalidation use synthetic source only."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('lifecycle_plan', Path(__file__).parents[1] / 'tools/lifecycle_plan.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class LifecyclePlanTests(unittest.TestCase):
    def plan(self, task, **extra):
        return lifecycle.plan({'contract_version': 1, 'task': task, **extra})

    def test_cli_reuses_backend_flow_definition(self):
        from blueprint_library import lifecycle as shared
        self.assertIs(lifecycle.plan, shared.plan)
        self.assertIs(lifecycle.ROUTES, shared.ROUTES)

    def test_source_release_and_daily_data_have_no_private_deployment_gate(self):
        release = self.plan('source_release', layout_change=True)
        self.assertEqual(release['backup_policy'], 'none')
        self.assertEqual(release['publish_services'], [])
        self.assertNotIn('layout_baseline_if_not_saved', release['required_checks'])
        daily = self.plan('daily_data')
        self.assertIn('same_id_readback', daily['required_checks'])
        self.assertNotIn('release_artifacts', daily['required_checks'])
        for task in ['source_release', 'daily_data', 'compatible_package']:
            with self.assertRaises(ValueError): self.plan(task, target_services=['private-workbench'])

    def test_fresh_install_protects_old_target_without_backup_dependency(self):
        value = self.plan('fresh_install', target_services=['new-native'], protected_services=['old-site'])
        self.assertEqual(value['backup_policy'], 'new_service')
        self.assertEqual(value['publish_services'], ['new-native'])
        self.assertEqual(value['protected_services'], ['old-site'])
        self.assertIn('independent_service_and_storage', value['required_checks'])
        with self.assertRaises(ValueError):
            self.plan('fresh_install', target_services=['old-site'], protected_services=['old-site'])

    def test_ui_and_schema_require_different_validation(self):
        ui = self.plan('ui', target_services=['native'], layout_change=True)
        self.assertIn('accessibility', ui['required_checks'])
        self.assertIn('layout_baseline_if_not_saved', ui['required_checks'])
        self.assertNotIn('restart_persistence', ui['required_checks'])
        schema = self.plan('schema_identity', target_services=['native'])
        for name in ['current_consistent_backup', 'private_copy_readback', 'migration', 'restore', 'old_rows_and_attachments']:
            self.assertIn(name, schema['required_checks'])

    def test_plan_digest_canonical_but_source_and_scope_sensitive(self):
        source = {'upstream_commit': 'a' * 40, 'platform_commit': 'b' * 40,
                  'artifact_sha256': 'c' * 64, 'product_version': '2.8.1', 'saved_version': 'opaque-version-7'}
        a = self.plan('ui', target_services=['native', 'gateway'], source=source)
        b = self.plan('ui', source=dict(reversed(list(source.items()))), target_services=['gateway', 'native', 'gateway'])
        self.assertEqual(a['plan_digest'], b['plan_digest'])
        self.assertNotEqual(a['plan_digest'], self.plan('ui', target_services=['native'], source=source)['plan_digest'])
        self.assertNotEqual(a['plan_digest'], self.plan('ui', target_services=['native', 'gateway'], source={**source, 'saved_version': 'opaque-version-8'})['plan_digest'])
        self.assertEqual(a['source']['product_version'], '2.8.1')
        with self.assertRaises(ValueError): self.plan('ui', source={'upstream_commit': 'main'})

    def test_input_receipts_invalidate_content_deletion_modes_command_and_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'ui.js').write_text('old')
            before = lifecycle.inputs(root, ['ui.js'])
            commands = [['node', '--test', 'tests/ui.cjs']]
            artifacts = {'workbench.zip': 'a' * 64}
            receipt = {'contract_version': 1, 'outcome': 'passed',
                       'validation_key': lifecycle.validation_key(before['input_tree'], commands, artifacts)}
            self.assertTrue(lifecycle.reuse_check(receipt, before['input_tree'], commands, artifacts)['reusable'])
            self.assertFalse(lifecycle.reuse_check({**receipt, 'outcome': 'failed'}, before['input_tree'], commands, artifacts)['reusable'])
            self.assertFalse(lifecycle.reuse_check(receipt, before['input_tree'], [['node', 'different.cjs']], artifacts)['reusable'])
            self.assertFalse(lifecycle.reuse_check(receipt, before['input_tree'], commands, {'workbench.zip': 'b' * 64})['reusable'])
            (root / 'ui.js').write_text('new')
            after = lifecycle.inputs(root, ['ui.js'])
            self.assertFalse(lifecycle.reuse_check(receipt, after['input_tree'], commands, artifacts)['reusable'])
            (root / 'ui.js').chmod(0o700)
            executable = lifecycle.inputs(root, ['ui.js'])
            self.assertNotEqual(after['input_tree'], executable['input_tree'])
            (root / 'ui.js').unlink()
            deleted = lifecycle.inputs(root, ['ui.js'])
            self.assertEqual(deleted['files']['ui.js'], {'deleted': True})
            self.assertNotEqual(deleted['input_tree'], executable['input_tree'])

    def test_no_path_escape_or_symlink_and_no_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['../private.json', '/etc/passwd', '.git/config', 'a\\b']:
                with self.assertRaises(ValueError): lifecycle.inputs(root, [name])
            (root / 'link').symlink_to('/etc/passwd')
            with self.assertRaises(ValueError): lifecycle.inputs(root, ['link'])
        with self.assertRaises(ValueError): lifecycle.validation_key('a' * 64, ['node tests/test.cjs'])

    def test_package_static_preflight_uses_existing_contract_and_pinned_source(self):
        manifest = json.loads((Path(__file__).parents[1] / 'templates/agent/agent.json').read_text())
        source = {'repository': 'https://github.com/example/synthetic-agent', 'revision': 'd' * 40}
        value = lifecycle.package_preflight(manifest, source)
        self.assertTrue(value['compatible'])
        self.assertFalse(value['registered'])
        self.assertFalse(value['execution_verified'])
        self.assertFalse(value['requires_host_deployment'])
        with self.assertRaises(ValueError): lifecycle.package_preflight(manifest, {**source, 'revision': 'main'})
        with self.assertRaises(ValueError): lifecycle.package_preflight({**manifest, 'contract_version': 2}, source)


if __name__ == '__main__':
    unittest.main()
