"""Authenticated packages must be safe before the running workspace is touched."""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
from urllib.request import Request
import zipfile

from blueprint_library.installer_release import (InstallerReleaseError, canonical_json,
    prepare_release, release_id, validate_release, verify_release, _RedirectPolicy)

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey = None


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional installer signing runtime is not installed.')
class ManagedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.keys = {'test-publisher': base64.b64encode(self.private.public_key().public_bytes_raw()).decode()}
        self.files = {'blueprint.py': b"print('entrypoint')\n",
                      'blueprint_folders/__init__.py': b'__version__ = "2.10.0"\n',
                      'blueprint_library/store.py': b'SCHEMA_VERSION = 8\n',
                      'library_web/index.html': '<h1>工作室</h1>'.encode()}
        self.archive = self.root / 'release.zip'
        self.manifest = {'contract_version': 1, 'component_id': 'blueprint-core', 'adapter': 'native-core',
            'version': '2.10.0', 'source_revision': 'a' * 40, 'platforms': [sys.platform],
            'runtime': {'python_min': [3, 10], 'python_max_exclusive': [4, 0]},
            'archive': {'url': str(self.archive), 'sha256': '0' * 64, 'size': 1},
            'files': {}, 'entrypoint': 'blueprint.py', 'database_schema': 8}
        self.make_archive()

    def make_archive(self, extra=None):
        with zipfile.ZipFile(self.archive, 'w') as package:
            for name, value in self.files.items():
                package.writestr(name, value)
            if extra:
                package.writestr(*extra)
        self.manifest['files'] = {name: hashlib.sha256(value).hexdigest() for name, value in self.files.items()}
        self.manifest['archive'].update(size=self.archive.stat().st_size,
                                      sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest())

    def sign(self, manifest=None):
        value = deepcopy(manifest or self.manifest)
        return {'manifest': value, 'key_id': 'test-publisher',
                'signature': base64.b64encode(self.private.sign(canonical_json(value))).decode()}

    def prepare(self, manifest=None):
        return prepare_release(self.sign(manifest), self.keys, self.root / 'cache')

    def test_signature_and_canonical_id_ignore_object_key_order(self):
        envelope = self.sign()
        envelope['manifest'] = dict(reversed(list(envelope['manifest'].items())))
        self.assertEqual(verify_release(envelope, self.keys), self.manifest)
        self.assertEqual(release_id(envelope['manifest']), release_id(self.manifest))

    def test_unknown_publisher_tamper_and_malformed_signature_fail_closed(self):
        envelope = self.sign()
        with self.assertRaises(InstallerReleaseError):
            verify_release(envelope, {})
        envelope['manifest']['version'] = '2.11.0'
        with self.assertRaises(InstallerReleaseError):
            verify_release(envelope, self.keys)
        envelope = self.sign()
        envelope['signature'] = 'not-base64!'
        with self.assertRaises(InstallerReleaseError):
            verify_release(envelope, self.keys)

    def test_preparation_and_reuse_keep_running_data_untouched(self):
        data = self.root / 'company'
        data.mkdir()
        (data / 'keep.txt').write_text('private')
        result = self.prepare()
        self.assertEqual(result['release_id'], release_id(self.manifest))
        self.assertEqual((Path(result['path']) / 'blueprint.py').read_bytes(), self.files['blueprint.py'])
        self.assertEqual(self.prepare(), result)
        self.assertEqual((data / 'keep.txt').read_text(), 'private')

    def test_corrupt_cache_is_rejected_instead_of_becoming_ready(self):
        result = self.prepare()
        (Path(result['path']) / 'blueprint.py').write_text('tampered', encoding='utf-8')
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_extra_cache_files_are_rejected(self):
        result = self.prepare()
        (Path(result['path']) / 'extra.py').write_text('extra', encoding='utf-8')
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_archive_must_match_signed_hash_and_size(self):
        self.archive.write_bytes(self.archive.read_bytes() + b'changed')
        with self.assertRaises(InstallerReleaseError):
            self.prepare()
        self.assertEqual(list((self.root / 'cache').iterdir()), [])

    def test_inventory_must_match_content_not_just_archive(self):
        self.manifest['files']['blueprint.py'] = '0' * 64
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_missing_and_extra_inventory_files_are_rejected(self):
        self.make_archive(('extra.py', b'not declared'))
        with self.assertRaises(InstallerReleaseError):
            self.prepare()
        self.make_archive()
        self.manifest['files']['missing.py'] = '0' * 64
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_actual_package_version_and_schema_must_match_manifest(self):
        for field, value in [('version', '2.11.0'), ('database_schema', 9)]:
            with self.subTest(field=field):
                manifest = deepcopy(self.manifest)
                manifest[field] = value
                with self.assertRaises(InstallerReleaseError):
                    self.prepare(manifest)

    def test_parent_absolute_backslash_windows_alias_and_case_paths_are_rejected(self):
        for name in ['../escape.py', '/absolute.py', 'x\\escape.py', 'C:drive.py',
                     'dir/../escape.py', 'dir./file.py', 'dir /file.py', 'CON.txt',
                     'AUX/file.py', 'dir//file.py', './entry.py']:
            with self.subTest(name=name):
                self.make_archive((name, b'evil'))
                with self.assertRaises(InstallerReleaseError):
                    self.prepare()
        self.make_archive()
        self.manifest['files']['Blueprint.py'] = '0' * 64
        with self.assertRaises(InstallerReleaseError):
            validate_release(self.manifest)
        self.assertFalse((self.root / 'escape.py').exists())

    def test_zip_symlink_and_duplicate_are_rejected(self):
        member = zipfile.ZipInfo('link')
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.make_archive((member, b'/etc/passwd'))
        self.manifest['files']['link'] = hashlib.sha256(b'/etc/passwd').hexdigest()
        with self.assertRaises(InstallerReleaseError):
            self.prepare()
        self.make_archive(('blueprint.py', self.files['blueprint.py']))
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_unsupported_runtime_platform_and_adapter_never_ready(self):
        variants = [dict(runtime={'python_min': [3, 99], 'python_max_exclusive': [4, 0]}),
                    dict(platforms=['linux'] if sys.platform != 'linux' else ['win32']),
                    dict(adapter='unimplemented'), dict(component_id='customer-ai')]
        for changes in variants:
            with self.subTest(changes=changes):
                with self.assertRaises(InstallerReleaseError):
                    self.prepare({**self.manifest, **changes})

    def test_insecure_download_scheme_and_revoked_release_are_rejected(self):
        for url in ['http://example.invalid/release.zip', 'https://user:secret@example.invalid/a',
                    'ftp://example.invalid/release.zip', 'relative.zip']:
            with self.subTest(url=url):
                manifest = deepcopy(self.manifest)
                manifest['archive']['url'] = url
                with self.assertRaises(InstallerReleaseError):
                    self.prepare(manifest)
        with self.assertRaises(InstallerReleaseError):
            self.prepare({**self.manifest, 'revoked': True})

    def test_dependency_requires_explicit_tested_version(self):
        manifest = {**self.manifest, 'requires': {'other-module': ['1.0.0']}}
        with self.assertRaises(InstallerReleaseError):
            self.prepare(manifest)
        result = prepare_release(self.sign(manifest), self.keys, self.root / 'cache',
                                 installed={'other-module': {'version': '1.0.0'}})
        self.assertEqual(result['version'], '2.10.0')

    def test_candidate_requires_a_real_tree_fingerprint(self):
        with self.assertRaises(InstallerReleaseError):
            validate_release({**self.manifest, 'candidate': True})

    def test_signed_https_redirect_host_and_hop_limit(self):
        manifest = deepcopy(self.manifest)
        manifest['archive']['redirect_hosts'] = ['release-assets.githubusercontent.com']
        verified = verify_release(self.sign(manifest), self.keys)
        policy = _RedirectPolicy(verified['archive']['redirect_hosts'])
        request = Request('https://github.com/org/project/releases/file.zip', headers={'Authorization': 'do-not-forward'})
        url = 'https://release-assets.githubusercontent.com/fixed-file.zip?token=short-lived'
        for _ in range(3):
            redirected = policy.redirect_request(request, None, 302, '', {}, url)
            self.assertEqual(redirected.full_url, url)
            self.assertFalse(redirected.has_header('Authorization'))
        with self.assertRaises(InstallerReleaseError):
            policy.redirect_request(request, None, 302, '', {}, url)

    def test_redirect_default_unlisted_hosts_downgrade_and_credentials_are_rejected(self):
        request = Request('https://github.com/org/project/releases/file.zip')
        allowed = 'release-assets.githubusercontent.com'
        urls = ['https://unlisted.invalid/file.zip', 'http://' + allowed + '/file.zip',
                'file:///private/secret', 'https://user:password@' + allowed + '/file.zip',
                'https://' + allowed + '.evil.invalid/file.zip']
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(InstallerReleaseError):
                    _RedirectPolicy([allowed]).redirect_request(request, None, 302, '', {}, url)
        with self.assertRaises(InstallerReleaseError):
            _RedirectPolicy([]).redirect_request(request, None, 302, '', {}, 'https://' + allowed + '/file.zip')
        for host in ['*.githubusercontent.com', 'Release-assets.githubusercontent.com', 'https://host.invalid', 'host.invalid.']:
            with self.subTest(host=host):
                manifest = deepcopy(self.manifest)
                manifest['archive']['redirect_hosts'] = [host]
                with self.assertRaises(InstallerReleaseError):
                    validate_release(manifest)


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional installer signing runtime is not installed.')
class ManagedPublisherTests(unittest.TestCase):
    def setUp(self):
        from tools import package_managed_release
        self.publisher = package_managed_release
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.source_patch = mock.patch.object(self.publisher, 'ROOT', self.source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        for name in self.publisher.REQUIRED:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'# runtime\n')
        (self.source / 'blueprint_folders/__init__.py').write_text('__version__ = "2.10.0"\n', encoding='utf-8')
        (self.source / 'blueprint_library/store.py').write_text('SCHEMA_VERSION = 8\n', encoding='utf-8')
        # Synthetic publisher sources carry their own explicit reviewed policy.
        (self.source / 'tools/release_files.json').write_text(json.dumps({
            'version': 1, 'files': {name: ['managed-core'] for name in self.publisher.REQUIRED}
        }), encoding='utf-8')
        self.key_path = self.root / 'signing.key'
        self.public_path = self.root / 'trusted.json'
        self.publisher.generate_key(self.key_path, self.public_path, 'test-key')
        self.dirty = False

        def git_output(args, **kwargs):
            if args[1] == 'status':
                return b' M blueprint.py\n' if self.dirty else b''
            if args[1] == 'rev-parse':
                return b'a' * 40 + b'\n'
            if '--cached' in args:
                return ('\0'.join(self.publisher.REQUIRED) + '\0').encode()
            return b'docs/private-customer-note.md\0arbitrary.py\0'
        self.git_patch = mock.patch.object(self.publisher.subprocess, 'check_output', side_effect=git_output)
        self.git_patch.start()
        self.addCleanup(self.git_patch.stop)

    def build(self, name, **kwargs):
        output = self.root / (name + '.zip')
        return self.publisher.build(output, self.root / (name + '.json'), self.key_path,
                                    'test-key', output.as_uri(), **kwargs)

    def test_build_is_deterministic_and_signature_verifies(self):
        first = self.build('first')
        second = self.build('second')
        self.assertEqual(first['sha256'], second['sha256'])
        self.assertEqual(first['source_tree'], second['source_tree'])
        envelope = json.loads((self.root / 'first.json').read_text(encoding='utf-8'))
        public = json.loads(self.public_path.read_text(encoding='utf-8'))
        self.assertEqual(verify_release(envelope, public)['source_revision'], 'a' * 40)
        self.assertFalse(envelope['manifest']['candidate'])

    def test_dirty_source_requires_explicit_candidate_without_fake_commit(self):
        self.dirty = True
        with self.assertRaises(InstallerReleaseError):
            self.build('rejected')
        self.assertFalse((self.root / 'rejected.zip').exists())
        self.build('candidate', allow_working_tree=True)
        manifest = json.loads((self.root / 'candidate.json').read_text(encoding='utf-8'))['manifest']
        self.assertTrue(manifest['candidate'])
        self.assertEqual(manifest['source_revision'], 'a' * 40)
        self.assertEqual(len(manifest['source_tree']), 64)

    def test_untracked_customer_notes_never_enter_candidate(self):
        note = self.source / 'docs/private-customer-note.md'
        note.parent.mkdir()
        note.write_text('private customer content', encoding='utf-8')
        self.build('candidate', allow_working_tree=True)
        with zipfile.ZipFile(self.root / 'candidate.zip') as archive:
            self.assertNotIn('docs/private-customer-note.md', archive.namelist())

    def test_key_generation_refuses_source_and_existing_files(self):
        with self.assertRaises(InstallerReleaseError):
            self.publisher.generate_key(self.source / 'private.key', self.public_path, 'test-key')
        with self.assertRaises(InstallerReleaseError):
            self.publisher.generate_key(self.key_path, self.public_path, 'test-key')


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional installer signing runtime is not installed.')
class ModuleReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.keys = {'module-key': base64.b64encode(self.key.public_key().public_bytes_raw()).decode()}
        self.node = self.root / ('node.exe' if sys.platform == 'win32' else 'node')
        self.node.write_bytes(b'synthetic trusted executable; subprocess is mocked')
        self.descriptor = {'component_id': 'bluesai-cs-0001', 'adapter': 'bluesai-node',
            'version': '0.4.0-preview.2', 'database_schema': 7,
            'agent_manifest': 'integrations/blueprint/agent.json'}
        self.files = {'package.json': json.dumps({'version': self.descriptor['version']}).encode(),
            'managed-component.json': json.dumps(self.descriptor).encode(),
            'src/managed-runtime.js': b'// synthetic entry\n',
            'integrations/blueprint/agent.json': json.dumps({'id': 'bluesai-cs-0001',
                'version': self.descriptor['version']}).encode()}
        archive = self.root / 'module.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            for name, content in self.files.items():
                output.writestr(name, content)
        self.manifest = {'contract_version': 2, 'component_id': self.descriptor['component_id'],
            'adapter': 'bluesai-node', 'version': self.descriptor['version'],
            'source_repository': 'https://github.com/example/bluesai', 'source_revision': 'b' * 40,
            'platforms': [sys.platform], 'runtime': {'node_min': [24, 22, 0], 'node_max_exclusive': [25, 0, 0]},
            'entrypoint': 'src/managed-runtime.js', 'database_schema': 7,
            'archive': {'url': str(archive), 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'size': archive.stat().st_size},
            'files': {name: hashlib.sha256(content).hexdigest() for name, content in self.files.items()},
            'requires': {'blueprint-core': ['2.10.0']}}

    def sign(self):
        return {'manifest': self.manifest, 'key_id': 'module-key',
                'signature': base64.b64encode(self.key.sign(canonical_json(self.manifest))).decode()}

    def prepare(self):
        return prepare_release(self.sign(), self.keys, self.root / 'cache',
            installed={'blueprint-core': '2.10.0'}, runtimes={'node': str(self.node)})

    def test_module_uses_explicit_node_and_static_component_descriptor(self):
        result = mock.Mock(returncode=0, stdout='v24.22.1\n')
        with mock.patch('blueprint_library.installer_release.subprocess.run', return_value=result) as run:
            ready = self.prepare()
        self.assertEqual(ready['component_id'], 'bluesai-cs-0001')
        self.assertEqual(ready['runtime']['version'], [24, 22, 1])
        self.assertEqual(run.call_args.args[0], [str(self.node), '--version'])
        self.assertFalse(run.call_args.kwargs['shell'])

    def test_module_never_uses_path_runtime_or_executes_downloaded_code(self):
        with self.assertRaises(InstallerReleaseError):
            prepare_release(self.sign(), self.keys, self.root / 'cache', installed={'blueprint-core': '2.10.0'})
        for version in ['v24.19.0\n', 'v25.0.0\n', 'node wrapper output']:
            with self.subTest(version=version), mock.patch('blueprint_library.installer_release.subprocess.run',
                    return_value=mock.Mock(returncode=0, stdout=version)), self.assertRaises(InstallerReleaseError):
                self.prepare()

    def test_module_descriptor_and_native_agent_inventory_must_match(self):
        self.manifest['database_schema'] = 8
        with mock.patch('blueprint_library.installer_release.subprocess.run',
                return_value=mock.Mock(returncode=0, stdout='v24.22.1\n')), self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_publisher_key_can_be_limited_to_component(self):
        key = {'module-key': {'public_key': self.keys['module-key'], 'components': ['another-module']}}
        with self.assertRaises(InstallerReleaseError):
            verify_release(self.sign(), key)
        key['module-key']['components'] = ['bluesai-cs-0001']
        self.assertEqual(verify_release(self.sign(), key)['database_schema'], 7)

    def test_v2_module_requires_explicit_repository_and_schema(self):
        for value in [None, 'http://github.com/example/bluesai', 'https://github.com/example/bluesai?token=x']:
            with self.subTest(value=value):
                manifest = {**self.manifest, 'source_repository': value}
                with self.assertRaises(InstallerReleaseError):
                    validate_release(manifest)


if __name__ == '__main__':
    unittest.main()
