"""Real Git checkpoints, private handoffs, and environment/tool misdiagnoses."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from blueprint_library.server import initialize_library
from blueprint_library.store import Store

spec = importlib.util.spec_from_file_location('update_assistant', Path(__file__).parents[1] / 'tools/update_assistant.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class UpdateAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-update-assistant-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'source'; self.project.mkdir()
        self.job = self.root / 'private-job'
        (self.project / 'blueprint.py').write_bytes(b'# Existing source\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('add', '.')
        self.git('commit', '-qm', 'Original source')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.project), *args], stderr=subprocess.DEVNULL)

    def prepare(self, **kwargs):
        return update.prepare(self.project, self.job, 'https://work.example', **kwargs)

    def test_prepare_is_repeatable_without_overwriting_custom_source_or_baseline(self):
        (self.project / 'blueprint.py').write_bytes(b'# My customization\n')
        (self.project / 'my-ui.js').write_text('const custom = true;', encoding='utf-8')
        self.assertEqual(self.prepare()['status'], 'prepared')
        first = (self.job / 'before-source/source.bundle').read_bytes()
        self.assertEqual(self.prepare()['status'], 'resumed')
        self.assertEqual((self.job / 'before-source/source.bundle').read_bytes(), first)
        with zipfile.ZipFile(self.job / 'before-source/working-tree.zip') as archive:
            self.assertEqual(archive.read('blueprint.py'), b'# My customization\n')
            self.assertIn('my-ui.js', archive.namelist())
        self.assertEqual((self.project / 'blueprint.py').read_text(encoding='utf-8'), '# My customization\n')
        self.assertFalse(list(self.job.rglob('*.sqlite3')))
        with self.assertRaises(ValueError): update.prepare(self.project, self.job, 'https://different.example')

    def test_sites_identity_and_browser_origin_must_match(self):
        (self.project / '.openai').mkdir()
        (self.project / '.openai/hosting.json').write_text('{"project_id":"original-site"}', encoding='utf-8')
        self.assertEqual(self.prepare()['route'], 'sites')
        with self.assertRaises(ValueError): self.prepare(site_id='new-site')
        browser = self.root / 'browser.json'
        browser.write_text(json.dumps({'format': 'blueprint-update-handoff', 'format_version': 1,
                                      'origin': 'https://another.example', 'preferences': {}}), encoding='utf-8')
        with self.assertRaises(ValueError): update.prepare(self.project, self.root / 'other-job', 'https://work.example', handoff=browser)

    def test_gateway_handoff_keeps_native_backend_and_rejects_silent_rebinding(self):
        (self.project / '.openai').mkdir()
        (self.project / '.openai/hosting.json').write_text('{"project_id":"original-site"}', encoding='utf-8')
        browser = self.root / 'browser.json'
        data = {'format': 'blueprint-update-handoff', 'format_version': 1,
                'origin': 'https://work.example', 'preferences': {}, 'route': 'sites',
                'deployment_mode': 'sites_native', 'backend_origin': 'https://data.example',
                'gateway_version': '1.0.0', 'password': 'must-not-copy'}
        browser.write_text(json.dumps(data), encoding='utf-8')
        self.prepare(handoff=browser)
        job = update.load_job(self.job)
        self.assertEqual(job['backend_origin'], data['backend_origin'])
        self.assertNotIn('password', job)
        report = update.assess(job, {})
        diagnosis = next(c for c in report['checks'] if c['code'] == 'data_backend')
        self.assertEqual(diagnosis['state'], 'not_verified')
        self.assertIn('不等待 Sites D1', diagnosis['message'])
        data['backend_origin'] = 'https://another.example'
        browser.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): self.prepare(handoff=browser)
        data['backend_origin'] = data['origin']
        browser.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(ValueError): update.handoff_data(browser)

    def test_pack_and_unpack_keep_baseline_and_uncommitted_candidate_and_can_resume_again(self):
        self.prepare()
        (self.project / 'blueprint.py').write_bytes(b'# Candidate, not deployed\n')
        package = self.root / 'resume.zip'
        result = update.pack(self.job, package)
        self.assertEqual(result['production_status'], 'not_verified')
        self.assertFalse(result['cross_environment_ready'])
        restored = self.root / 'restored'
        result = update.unpack(package, restored)
        with zipfile.ZipFile(restored / result['checkpoint'] / 'working-tree.zip') as archive:
            self.assertEqual(archive.read('blueprint.py'), b'# Candidate, not deployed\n')
        with zipfile.ZipFile(restored / 'before-source/working-tree.zip') as archive:
            self.assertEqual(archive.read('blueprint.py'), b'# Existing source\n')
        update.pack(restored, self.root / 'again.zip')
        update.unpack(self.root / 'again.zip', self.root / 'again')
        if os.name != 'nt':
            self.assertEqual(package.stat().st_mode & 0o777, 0o600)
            self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
        with self.assertRaises(ValueError): update.unpack(package, restored)

    def test_changed_or_traversing_packages_never_extract(self):
        self.prepare(); good = self.root / 'good.zip'; update.pack(self.job, good)
        for index, name in enumerate(['job.json', '../escape']):
            bad = self.root / f'bad-{index}.zip'
            with zipfile.ZipFile(good) as original, zipfile.ZipFile(bad, 'w') as changed:
                for item in original.namelist():
                    if item != name: changed.writestr(item, original.read(item))
                changed.writestr(name, b'changed')
            destination = self.root / f'output-{index}'
            with self.assertRaises(ValueError): update.unpack(bad, destination)
            self.assertFalse(destination.exists())

    def test_symlinks_private_source_and_nested_output_are_rejected(self):
        with self.assertRaises(ValueError): update.prepare(self.project, self.project / 'job', 'https://work.example')
        link = self.project / 'outside'
        with self.subTest(capability='symlink'):
            try:
                link.symlink_to(self.root / 'target')
            except OSError as exc:
                if getattr(exc, 'winerror', None) == 1314:
                    self.skipTest('Windows symlink privilege unavailable')
                raise
            with self.assertRaises(ValueError): self.prepare()
        link.unlink(missing_ok=True)
        (self.project / 'access.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError): update.prepare(self.project, self.root / 'another-job', 'https://work.example')

    def test_native_backup_preserves_real_attachment_credentials_and_rows(self):
        self.prepare()
        data = self.root / 'data'
        initialize_library(data, 'owner', 'Temporary-test-password-2026')
        store = Store(data)
        item = store.add_file('keep.txt', b'Keep this attachment')
        update.native_snapshot(self.job, data)
        from tools.upgrade_workspace import verify
        self.assertEqual(verify(data, self.job / 'database-before')['status'], 'original_data_preserved')
        self.assertEqual(Store(self.job / 'database-before/data').get_file(item['id'])[0].read_bytes(), b'Keep this attachment')
        with self.assertRaises(ValueError): update.native_snapshot(self.job, data)

    def test_stale_or_different_runtime_is_not_treated_as_applied(self):
        self.prepare(); job = update.load_job(self.job)
        env = {'observations_current': True, 'source_config_version_id': 'old',
               'network_policy': {'state': 'enforced', 'mode': 'restricted', 'effective_allowed_hosts': ['work.example']}}
        report = update.assess(job, {'environment': env, 'expected_config_version': 'new'})
        self.assertEqual(report['checks'][0]['state'], 'blocked')
        self.assertIn('不要重複加入', report['checks'][0]['message'])
        env['observations_current'] = False
        self.assertEqual(update.assess(job, {'environment': env})['checks'][0]['state'], 'unknown')
        env['observations_current'] = True
        self.assertEqual(update.assess(job, {'environment': env})['checks'][0]['state'], 'policy_allows')
        env['network_policy']['effective_allowed_hosts'] = ['notwork.example']
        self.assertEqual(update.assess(job, {'environment': env})['checks'][0]['state'], 'blocked')

    def test_installed_partial_and_loaded_tools_are_distinct_and_never_prove_a_write(self):
        self.prepare(); job = update.load_job(self.job)
        missing = update.assess(job, {'plugin_installed': True, 'tools': []})
        self.assertEqual(missing['checks'][1]['state'], 'missing')
        partial = update.assess(job, {'tools': ['mcp__app__blueprint_workspace_info']})
        self.assertEqual(partial['checks'][1]['state'], 'outdated_or_partial')
        names = [x['name'] for x in update.read_json(update.ROOT / 'integrations/mcp/tools.json')['tools']]
        loaded = update.assess(job, {'tools': names, 'production_status': 'completed'})
        self.assertEqual(loaded['checks'][1]['state'], 'loaded')
        self.assertEqual(loaded['production_status'], 'not_verified')

    def test_check_never_persists_raw_tool_tokens(self):
        self.prepare()
        observations = self.root / 'observations.json'
        observations.write_text(json.dumps({'site': {'token': 'never-copy-this'}, 'environment': {}, 'tools': []}), encoding='utf-8')
        update.check(self.job, observations)
        self.assertNotIn('never-copy-this', (self.job / 'checks.json').read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
