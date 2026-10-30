"""Release selection is an explicit privacy boundary, including on Windows."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import package_installer, package_workbench
from tools.release_policy import external_output, selected_names, verify_archive
from tools.verify_release_artifacts import accept_artifacts


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-release-policy-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git('init', '-q')
        self.contents = {'blueprint.py': b'# synthetic entry\n',
            'blueprint_library/store.py': b'SCHEMA_VERSION = 8\n',
            'contracts/integration-v1/example.json': b'{"synthetic":true}\n',
            'research/maintainer.md': b'synthetic research excluded from product\n'}
        self.contents['contracts/integration-v1/SHA256SUMS'] = (
            hashlib.sha256(self.contents['contracts/integration-v1/example.json']).hexdigest()
            + '  example.json\n').encode()
        for name, data in self.contents.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.policy = {name: ([] if name.startswith('research/') else ['installer', 'workbench', 'managed-core'])
                       for name in self.contents}
        self.policy['tools/release_files.json'] = ['installer']
        self.save_policy()
        self.git('add', '.')
        self.git('-c', 'user.name=Synthetic Fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '-qm', 'synthetic release fixture')
        for publisher in (package_installer, package_workbench):
            patch = mock.patch.object(publisher, 'ROOT', self.source)
            patch.start(); self.addCleanup(patch.stop)
        for module, name, value in [(package_installer, 'REQUIRED', {'blueprint.py'}),
                (package_installer, 'MANAGED_INSTALLER_FILES', set()),
                (package_workbench, 'FILES', ['blueprint.py'])]:
            patch = mock.patch.object(module, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.source, capture_output=True,
                              check=True, text=True, encoding='utf-8').stdout

    def save_policy(self):
        path = self.source / 'tools/release_files.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'version': 1, 'files': self.policy}), encoding='utf-8')

    def test_both_archives_have_exact_reviewed_inventory_and_valid_contract_bytes(self):
        for module, kind, manifest_name in [(package_installer, 'installer', 'installer-manifest.json'),
                (package_workbench, 'workbench', 'workbench-release.json')]:
            with self.subTest(kind=kind):
                path = self.root / (kind + '.zip')
                result = module.build(path)
                manifest = verify_archive(path, manifest_name)
                expected = {name for name, groups in self.policy.items() if kind in groups}
                self.assertEqual(set(manifest['files']), expected)
                self.assertEqual(result['files'], len(expected))
                self.assertNotIn('research/maintainer.md', manifest['files'])

    def test_tracked_private_canaries_outside_and_inside_product_directory_are_rejected(self):
        for name in ('exports/CANARY_SECRET.csv', 'blueprint_library/CANARY_PRIVATE.json'):
            with self.subTest(name=name):
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('synthetic canary, never real customer content', encoding='utf-8')
                self.git('add', name)
                for kind in ('installer', 'workbench', 'managed-core'):
                    with self.assertRaisesRegex(ValueError, '未審核'):
                        selected_names(self.source, kind)
                self.assertFalse((self.root / 'rejected.zip').exists())
                self.git('rm', '-f', name)

    def test_untracked_files_require_explicit_candidate_and_exact_policy_membership(self):
        path = self.source / 'blueprint_library/new_reviewed.py'
        path.write_bytes(b'# reviewed synthetic feature\n')
        self.policy[path.relative_to(self.source).as_posix()] = ['managed-core']
        self.save_policy()
        with self.assertRaises(ValueError):
            selected_names(self.source, 'managed-core')
        self.assertIn('blueprint_library/new_reviewed.py',
                      selected_names(self.source, 'managed-core', allow_working_tree=True))
        (self.source / 'blueprint_library/private-note.json').write_text('{}', encoding='utf-8')
        self.assertNotIn('blueprint_library/private-note.json',
                         selected_names(self.source, 'managed-core', allow_working_tree=True))

    def test_archives_are_deterministic_and_existing_output_is_never_replaced(self):
        for module in (package_installer, package_workbench):
            first = self.root / (module.__name__.split('.')[-1] + '-1.zip')
            second = first.with_name(first.stem + '-2.zip')
            module.build(first)
            os.utime(self.source / 'blueprint.py', (1800000000, 1800000000))
            module.build(second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            previous = first.read_bytes()
            with self.assertRaises(FileExistsError):
                module.build(first)
            self.assertEqual(first.read_bytes(), previous)

    def test_contract_hash_mismatch_is_caught_in_delivered_zip(self):
        path = self.source / 'contracts/integration-v1/example.json'
        path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        archive = self.root / 'bad-contract.zip'
        package_installer.build(archive)
        with self.assertRaisesRegex(ValueError, '合約雜湊'):
            verify_archive(archive, 'installer-manifest.json')

    def test_acceptance_binds_both_archives_to_current_source(self):
        first, second = self.root / 'installer.zip', self.root / 'workbench.zip'
        package_installer.build(first); package_workbench.build(second)
        accepted = accept_artifacts(first, second, root=self.source)
        self.assertTrue(accepted['accepted'])
        self.assertEqual(len(accepted['artifacts']), 2)
        (self.source / 'blueprint.py').write_bytes(b'# source changed after build\n')
        with self.assertRaisesRegex(ValueError, '來源內容'):
            accept_artifacts(first, second, root=self.source)

    def test_formal_acceptance_rejects_dirty_source_even_when_archives_are_unchanged(self):
        first, second = self.root / 'installer.zip', self.root / 'workbench.zip'
        package_installer.build(first); package_workbench.build(second)
        (self.source / 'research/maintainer.md').write_bytes(b'changed excluded research\n')
        with self.assertRaisesRegex(ValueError, '乾淨固定來源'):
            accept_artifacts(first, second, root=self.source)

    def test_source_and_output_junction_ancestors_are_rejected(self):
        link = self.source / 'linked'
        link.mkdir()
        (link / 'file.json').write_text('{}', encoding='utf-8')
        self.policy['linked/file.json'] = ['installer']
        self.save_policy(); self.git('add', '.')
        outside = self.root / 'outside'
        link.rename(outside)
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            os.symlink(outside, link, target_is_directory=True)
            self.addCleanup(lambda: link.unlink(missing_ok=True))
        with self.assertRaisesRegex(ValueError, '連結'):
            package_installer.build(self.root / 'linked.zip')
        with self.assertRaisesRegex(ValueError, '連結'):
            external_output(link / 'nested' / 'output.zip', self.source)
        self.assertEqual((outside / 'file.json').read_text(encoding='utf-8'), '{}')

    def test_policy_path_escape_and_unsafe_allowlisting_are_rejected(self):
        self.policy['../escape.json'] = ['installer']
        self.save_policy()
        with self.assertRaises(ValueError):
            package_installer.build(self.root / 'escape.zip')
        self.assertFalse((self.root / 'escape.zip').exists())


if __name__ == '__main__':
    unittest.main()
