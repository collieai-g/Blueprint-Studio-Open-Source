"""Signed channels and dependency plans never grant cross-studio installation."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from blueprint_library.installer_release import InstallerReleaseError, canonical_json, release_id
from blueprint_library.installer_catalog import catalog_id, resolve_updates, verify_catalog, version_key

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey = None


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional installer signing runtime is not installed.')
class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.keys = {'publisher': base64.b64encode(self.key.public_key().public_bytes_raw()).decode()}
        self.now = 1800000000

    def release(self, component='blueprint-core', version='2.11.0', requires=None, **extra):
        manifest = {'contract_version': 1, 'component_id': component, 'adapter': 'native-core',
            'version': version, 'source_revision': 'a' * 40, 'platforms': [sys.platform],
            'runtime': {'python_min': [3, 10], 'python_max_exclusive': [4, 0]},
            'archive': {'url': 'https://updates.example.invalid/' + component + '.zip', 'sha256': 'c' * 64, 'size': 123},
            'files': {'blueprint.py': 'a' * 64, 'blueprint_folders/__init__.py': 'a' * 64,
                      'blueprint_library/store.py': 'a' * 64},
            'entrypoint': 'blueprint.py', 'database_schema': 8, 'requires': requires or {}, **extra}
        return {'manifest': manifest, 'key_id': 'publisher',
                'signature': base64.b64encode(self.key.sign(canonical_json(manifest))).decode()}

    def catalog(self, releases=None, **changes):
        value = {'contract_version': 1, 'channel': 'test', 'audience': ['*'], 'sequence': 1,
                 'expires_at': self.now + 3600, 'releases': releases or [], 'revoked': [], **changes}
        return {'catalog': value, 'key_id': 'publisher',
                'signature': base64.b64encode(self.key.sign(canonical_json(value))).decode()}

    def verify(self, envelope, **kwargs):
        return verify_catalog(envelope, self.keys, channel=kwargs.pop('channel', 'test'),
                              installation_id=kwargs.pop('installation_id', 'studio-a'), now=self.now, **kwargs)

    def test_catalog_signature_sequence_identity_and_expiry(self):
        envelope = self.catalog([self.release()])
        first = self.verify(envelope)
        same = self.verify(envelope, previous_sequence=1, previous_catalog_id=first['catalog_id'])
        self.assertEqual(first, same)
        self.assertEqual(first['catalog_id'], catalog_id(envelope['catalog']))
        with self.assertRaises(InstallerReleaseError):
            self.verify(self.catalog(sequence=1, expires_at=self.now + 7200), previous_sequence=1,
                        previous_catalog_id=first['catalog_id'])
        with self.assertRaises(InstallerReleaseError):
            self.verify(envelope, previous_sequence=2)
        with self.assertRaises(InstallerReleaseError):
            self.verify(self.catalog(expires_at=self.now))
        tampered = deepcopy(envelope)
        tampered['catalog']['sequence'] = 2
        with self.assertRaises(InstallerReleaseError):
            self.verify(tampered)

    def test_private_channel_cannot_leak_to_other_studio_or_wildcard(self):
        envelope = self.catalog(channel='private', audience=['studio-a'])
        self.verify(envelope, channel='private')
        with self.assertRaises(InstallerReleaseError):
            self.verify(envelope, channel='private', installation_id='studio-b')
        with self.assertRaises(InstallerReleaseError):
            self.verify(self.catalog(channel='private'), channel='private')
        with self.assertRaises(InstallerReleaseError):
            self.verify(envelope)

    def test_stable_channel_rejects_candidates_previews_and_wrong_key_scope(self):
        for release in [self.release(candidate=True, source_tree='f' * 64),
                        self.release(version='2.11.0-preview.1')]:
            with self.assertRaises(InstallerReleaseError):
                self.verify(self.catalog([release], channel='stable'), channel='stable')
        scoped = {'publisher': {'public_key': self.keys['publisher'], 'channels': ['private']}}
        with self.assertRaises(InstallerReleaseError):
            verify_catalog(self.catalog(), scoped, channel='test', installation_id='studio-a', now=self.now)
        module_only = {'publisher': {'public_key': self.keys['publisher'], 'components': ['bluesai-cs-0001']}}
        with self.assertRaises(InstallerReleaseError):
            verify_catalog(self.catalog(), module_only, channel='test', installation_id='studio-a', now=self.now)

    def test_duplicate_component_version_is_not_ambiguous(self):
        with self.assertRaises(InstallerReleaseError):
            self.verify(self.catalog([self.release(), self.release()]))
        another = self.release(notes='different package intent')
        with self.assertRaises(InstallerReleaseError):
            self.verify(self.catalog([self.release(), another]))

    def test_dependency_core_is_ordered_before_requested_module_and_unrelated_kept(self):
        core = self.release()
        module = self.release('bluesai-cs-0001', '0.5.0', {'blueprint-core': ['2.11.0']})
        unrelated = self.release('video-ai', '2.0.0')
        installed = {'blueprint-core': {'version': '2.10.0'},
                     'bluesai-cs-0001': {'version': '0.4.0'}, 'video-ai': {'version': '1.0.0'}}
        result = resolve_updates([unrelated, module, core], installed, list(installed), ['bluesai-cs-0001'])
        self.assertEqual([x['manifest']['component_id'] for x in result['releases']],
                         ['blueprint-core', 'bluesai-cs-0001'])
        self.assertEqual(result['final_versions']['video-ai'], '1.0.0')
        again = resolve_updates([core, unrelated, module], installed, list(installed), ['bluesai-cs-0001'])
        self.assertEqual(result['plan_digest'], again['plan_digest'])

    def test_missing_unlicensed_or_cyclic_dependency_is_blocked(self):
        module = self.release('module-a', '1.0.0', {'module-b': ['1.0.0']})
        for releases, allowed in [([module], ['module-a']),
                                  ([module], ['module-a', 'module-b']),
                                  ([module, self.release('module-b', '1.0.0', {'module-a': ['1.0.0']})], ['module-a', 'module-b'])]:
            with self.subTest(allowed=allowed), self.assertRaises(InstallerReleaseError):
                resolve_updates(releases, {}, allowed, ['module-a'])

    def test_existing_module_dependencies_prevent_incompatible_core_only_update(self):
        installed = {'blueprint-core': {'version': '2.10.0'}, 'bluesai-cs-0001': {'version': '0.4.0',
                     'manifest': {'component_id': 'bluesai-cs-0001', 'version': '0.4.0',
                                  'requires': {'blueprint-core': ['2.10.0']}}}}
        with self.assertRaises(InstallerReleaseError):
            resolve_updates([self.release()], installed, list(installed), ['blueprint-core'])

    def test_new_unrelated_modules_are_not_installed_and_revoked_update_is_filtered(self):
        core = self.release()
        other = self.release('video-ai', '1.0.0')
        installed = {'blueprint-core': {'version': '2.10.0'}}
        plan = resolve_updates([core, other], installed, ['blueprint-core', 'video-ai'])
        self.assertNotIn('video-ai', plan['final_versions'])
        blocked = resolve_updates({'releases': [core], 'revoked': [release_id(core['manifest'])]}, installed, ['blueprint-core'])
        self.assertEqual(blocked['releases'], [])

    def test_semantic_preview_order_is_numeric_and_stable_is_later(self):
        self.assertLess(version_key('0.4.0-preview.9'), version_key('0.4.0-preview.10'))
        self.assertLess(version_key('0.4.0-preview.10'), version_key('0.4.0'))

    def test_channel_promotion_keeps_exact_release_envelope_and_cannot_promote_preview(self):
        from tools.package_release_catalog import promote_catalog
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            private = directory / 'private.key'
            private.write_bytes(base64.b64encode(self.key.private_bytes_raw()))
            release = self.release()
            source = self.catalog([release], expires_at=int(time.time()) + 3600)
            result = promote_catalog(source, directory / 'stable.json', private, 'publisher', self.keys,
                installation_id='studio-a', sequence=4, expires_at=int(time.time()) + 7200)
            envelope = json.loads((directory / 'stable.json').read_text(encoding='utf-8'))
            self.assertEqual(envelope['catalog']['releases'], [release])
            self.assertEqual(result['channel'], 'stable')
            targeted = self.catalog([release], audience=['studio-a'], expires_at=int(time.time()) + 3600)
            promote_catalog(targeted, directory / 'stable-a.json', private, 'publisher', self.keys,
                installation_id='studio-a', sequence=4, expires_at=int(time.time()) + 7200)
            narrow = json.loads((directory / 'stable-a.json').read_text(encoding='utf-8'))
            self.assertEqual(narrow['catalog']['audience'], ['studio-a'])
            with self.assertRaises(InstallerReleaseError):
                verify_catalog(narrow, self.keys, channel='stable', installation_id='studio-b')
            preview = self.catalog([self.release(version='2.11.0-preview.1')], expires_at=int(time.time()) + 3600)
            with self.assertRaises(InstallerReleaseError):
                promote_catalog(preview, directory / 'bad-stable.json', private, 'publisher', self.keys,
                    installation_id='studio-a', sequence=5, expires_at=int(time.time()) + 7200)
            self.assertFalse((directory / 'bad-stable.json').exists())


if __name__ == '__main__':
    unittest.main()
