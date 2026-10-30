"""Publishing preparation preserves the signed objects and never uploads."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
import zipfile

from blueprint_library.installer_catalog import catalog_id
from blueprint_library.installer_release import InstallerReleaseError, canonical_json
from tools.package_release_catalog import promote_catalog
from tools.prepare_update_publication import prepare_publication, verify_hosted

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    Ed25519PrivateKey = None


@unittest.skipIf(Ed25519PrivateKey is None, 'Optional signing runtime is not installed.')
class UpdatePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='blueprint-publication-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.keys = {'publisher': base64.b64encode(self.key.public_key().public_bytes_raw()).decode()}
        self.files = {'blueprint.py': b'# synthetic entry\n',
            'blueprint_folders/__init__.py': b'__version__ = "2.10.0"\n',
            'blueprint_library/store.py': b'SCHEMA_VERSION = 8\n',
            'library_web/index.html': b'<h1>Synthetic studio</h1>'}
        self.archive = self.root / 'core.zip'
        with zipfile.ZipFile(self.archive, 'x', zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.files.items():
                archive.writestr(name, data)
        self.manifest = {'contract_version': 1, 'component_id': 'blueprint-core',
            'adapter': 'native-core', 'version': '2.10.0', 'source_revision': 'a' * 40,
            'platforms': [sys.platform], 'runtime': {'python_min': [3, 10], 'python_max_exclusive': [4, 0]},
            'archive': {'url': 'https://github.com/example/updates/releases/download/core-v2.10.0/core.zip',
                'sha256': hashlib.sha256(self.archive.read_bytes()).hexdigest(),
                'size': self.archive.stat().st_size,
                'redirect_hosts': ['release-assets.githubusercontent.com']},
            'files': {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()},
            'entrypoint': 'blueprint.py', 'database_schema': 8, 'candidate': False}
        self.catalog_path = self.root / 'test.json'
        self.write_catalog()

    def envelope(self, field, value):
        value = deepcopy(value)
        return {field: value, 'key_id': 'publisher',
                'signature': base64.b64encode(self.key.sign(canonical_json(value))).decode()}

    def write_catalog(self, *, channel='test', audience=None):
        catalog = {'contract_version': 1, 'channel': channel, 'audience': audience or ['*'],
            'sequence': 1, 'expires_at': int(time.time()) + 3600,
            'releases': [self.envelope('manifest', self.manifest)], 'revoked': []}
        self.source = self.envelope('catalog', catalog)
        self.catalog_path.write_text(json.dumps(self.source, indent=2) + '\n', encoding='utf-8')

    def prepare(self, name='publication', **options):
        return prepare_publication(self.catalog_path, self.keys, 'synthetic-studio',
            [self.archive], self.root / name, repository=options.get('repository', 'example/updates'),
            tag=options.get('tag', 'core-v2.10.0'))

    def test_preparation_copies_exact_zip_and_original_signed_catalog_without_upload(self):
        original_archive = self.archive.read_bytes()
        original_catalog = self.catalog_path.read_bytes()
        result = self.prepare()
        self.assertFalse(result['uploaded'])
        self.assertEqual((self.root / 'publication/release-assets/core.zip').read_bytes(), original_archive)
        self.assertEqual((self.root / 'publication/pages/channels/test.json').read_bytes(), original_catalog)
        self.assertEqual(self.archive.read_bytes(), original_archive)
        self.assertEqual(self.catalog_path.read_bytes(), original_catalog)
        self.assertEqual(result['catalog_id'], catalog_id(self.source['catalog']))

    def test_stable_promotion_preserves_release_envelope_and_zip_bytes(self):
        original = self.archive.read_bytes()
        key_path = self.root / 'publisher.key'
        key_path.write_bytes(base64.b64encode(self.key.private_bytes_raw()))
        promoted = self.root / 'stable.json'
        promote_catalog(self.source, promoted, key_path, 'publisher', self.keys,
            installation_id='synthetic-studio', sequence=2, expires_at=int(time.time()) + 7200)
        stable = json.loads(promoted.read_text(encoding='utf-8'))
        self.assertEqual(stable['catalog']['releases'], self.source['catalog']['releases'])
        self.assertEqual(self.archive.read_bytes(), original)

    def test_wrong_download_location_or_missing_signed_redirect_is_rejected(self):
        variants = [('url', self.archive.as_uri()),
                    ('url', 'https://github.com/example/other/releases/download/core-v2.10.0/core.zip'),
                    ('redirect_hosts', [])]
        for field, value in variants:
            with self.subTest(field=field, value=value):
                previous = deepcopy(self.manifest)
                self.manifest['archive'][field] = value
                self.write_catalog()
                with self.assertRaises(InstallerReleaseError):
                    self.prepare()
                self.assertFalse((self.root / 'publication').exists())
                self.manifest = previous

    def test_tampered_zip_or_signature_never_creates_publication(self):
        self.archive.write_bytes(self.archive.read_bytes() + b'synthetic tamper')
        with self.assertRaises(InstallerReleaseError):
            self.prepare()
        self.assertFalse((self.root / 'publication').exists())
        self.source['catalog']['sequence'] = 2
        self.catalog_path.write_text(json.dumps(self.source), encoding='utf-8')
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_development_candidate_and_private_channel_are_not_public_publishing(self):
        self.manifest['candidate'] = True
        self.manifest['source_tree'] = 'b' * 64
        self.write_catalog()
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

        self.manifest['candidate'] = False
        self.write_catalog(channel='private', audience=['synthetic-studio'])
        with self.assertRaises(InstallerReleaseError):
            self.prepare()

    def test_targeted_test_and_stable_catalogs_never_enter_public_pages(self):
        for channel in ('test', 'stable'):
            with self.subTest(channel=channel):
                self.write_catalog(channel=channel, audience=['synthetic-studio'])
                with self.assertRaisesRegex(InstallerReleaseError, '私人工作室身份'):
                    self.prepare()
                self.assertFalse((self.root / 'publication').exists())
                with mock.patch('tools.prepare_update_publication.read_feed', return_value=self.source):
                    with self.assertRaisesRegex(InstallerReleaseError, '私人工作室身份'):
                        verify_hosted('https://updates.example.invalid/channels/' + channel + '.json',
                            self.keys, 'synthetic-studio', catalog_id(self.source['catalog']))

    def test_existing_publication_is_not_overwritten(self):
        self.prepare()
        previous = (self.root / 'publication/publication.json').read_bytes()
        with self.assertRaises(InstallerReleaseError):
            self.prepare()
        self.assertEqual((self.root / 'publication/publication.json').read_bytes(), previous)

    def test_catalog_only_withdrawal_does_not_require_a_new_archive(self):
        catalog = deepcopy(self.source['catalog'])
        catalog['sequence'] = 2
        catalog['releases'] = []
        catalog['revoked'] = ['a' * 64]
        self.catalog_path.write_text(json.dumps(self.envelope('catalog', catalog)), encoding='utf-8')
        result = prepare_publication(self.catalog_path, self.keys, 'synthetic-studio', [],
            self.root / 'withdrawal', repository='example/updates', tag='core-v2.10.0')
        self.assertEqual(result['artifacts'], [])
        self.assertFalse(result['uploaded'])
        self.assertTrue((self.root / 'withdrawal/pages/channels/test.json').is_file())

    def test_hosted_readback_requires_expected_catalog_and_verifies_every_download(self):
        with mock.patch('tools.prepare_update_publication.read_feed', return_value=self.source) as read:
            with mock.patch('tools.prepare_update_publication.prepare_release') as prepare:
                result = verify_hosted('https://updates.example.invalid/channels/test.json',
                    self.keys, 'synthetic-studio', catalog_id(self.source['catalog']))
                self.assertTrue(result['download_verified'])
                read.assert_called_once_with('https://updates.example.invalid/channels/test.json')
                self.assertEqual(prepare.call_count, 1)
            with self.assertRaises(InstallerReleaseError):
                verify_hosted('https://updates.example.invalid/channels/test.json',
                              self.keys, 'synthetic-studio', '0' * 64)
        with self.assertRaises(InstallerReleaseError):
            verify_hosted('http://updates.example.invalid/test.json', self.keys,
                          'synthetic-studio', catalog_id(self.source['catalog']))


if __name__ == '__main__':
    unittest.main()
