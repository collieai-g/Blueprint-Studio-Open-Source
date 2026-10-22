"""Cross-environment recovery using real Git/ZIP and a GitHub API test double."""
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from tools import source_checkpoint as checkpoint


class FakeGitHub:
    def __init__(self):
        self.private = True
        self.push = True
        self.blobs = {}; self.trees = {}; self.commits = {}; self.refs = {}
        self.writes = []
        self.fail_readback = False

    def api(self, endpoint, body=None):
        assert endpoint.startswith('repos/example/private-workspace')
        route = endpoint[len('repos/example/private-workspace'):]
        if body is not None:
            self.writes.append(route)
            if route == '/git/blobs':
                raw = base64.b64decode(body['content'])
                sha = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                self.blobs[sha] = dict(body, sha=sha, size=len(raw))
            elif route == '/git/trees':
                sha = hashlib.sha1(json.dumps(body).encode()).hexdigest()
                self.trees[sha] = dict(body, sha=sha, truncated=False)
            elif route == '/git/commits':
                sha = hashlib.sha1(json.dumps(body).encode()).hexdigest()
                self.commits[sha] = dict(body, sha=sha, tree={'sha': body['tree']})
            elif route == '/git/refs':
                if body['ref'] in self.refs:
                    raise ValueError('Ref already exists')
                self.refs[body['ref']] = body['sha']
                return {}
            else:
                raise AssertionError(route)
            return {'sha': sha}
        if not route:
            return {'full_name': 'example/private-workspace', 'private': self.private,
                    'permissions': {'push': self.push}}
        if route.startswith('/git/ref/'):
            return {'object': {'sha': self.refs['refs/' + route[len('/git/ref/'):]]}}
        if self.fail_readback:
            raise ValueError('Readback failed')
        for prefix, objects in [('/git/blobs/', self.blobs), ('/git/trees/', self.trees), ('/git/commits/', self.commits)]:
            if route.startswith(prefix):
                return objects[route[len(prefix):]]
        raise AssertionError(route)


class SourceCheckpointTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='blueprint-checkpoint-')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.source = self.root / 'old-environment'; self.source.mkdir()
        self.out = self.root / 'private-export'; self.api = FakeGitHub()
        self.repo = 'example/private-workspace'; self.url = 'https://workspace.example'
        (self.source / 'main.py').write_text('# original\n')
        (self.source / 'deleted.js').write_text('// remove in candidate\n')
        self.git('init', '-q'); self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Test'); self.git('add', '.'); self.git('commit', '-qm', 'baseline')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], stderr=subprocess.DEVNULL)

    def export(self, **kwargs):
        return checkpoint.export_source(self.source, self.out, kwargs.pop('repo', self.repo), self.url, '2.4.2', **kwargs)

    def test_roundtrip_dirty_tree_deletions_binary_and_executable_in_new_environment(self):
        (self.source / 'main.py').write_text('# candidate\n')
        (self.source / 'deleted.js').unlink()
        (self.source / 'image.png').write_bytes(b'\x89PNG\x00\xff')
        script = self.source / 'run.sh'; script.write_text('#!/bin/sh\ntrue\n'); script.chmod(0o755)
        local = self.export()
        self.assertEqual(local['status'], 'local_only')
        saved = checkpoint.save(self.out, 'blueprint-checkpoints/test', self.api)
        commit = saved['commit']
        self.assertEqual(saved['status'], 'remote_source_verified')
        self.assertEqual(saved['production_status'], 'not_verified')
        self.assertEqual(set(self.api.refs), {'refs/heads/blueprint-checkpoints/test'})
        self.assertEqual(self.api.commits[commit]['parents'], [])
        # Fetch pinned commit even after the branch changes in a different chat.
        self.api.refs['refs/heads/blueprint-checkpoints/test'] = 'f' * 40
        new = self.root / 'new-environment'
        result = checkpoint.fetch(self.repo, commit, new, self.url, api=self.api)
        recovered = Path(result['project'])
        self.assertEqual((recovered / 'main.py').read_text(), '# candidate\n')
        self.assertEqual((recovered / 'image.png').read_bytes(), b'\x89PNG\x00\xff')
        self.assertFalse((recovered / 'deleted.js').exists())
        self.assertEqual((recovered / 'run.sh').stat().st_mode & 0o777, 0o755)
        self.assertFalse((recovered / '.git').exists())
        self.assertEqual(result['production_status'], 'not_verified')

    def test_public_upstream_unknown_repo_visibility_and_missing_permission_never_upload(self):
        with self.assertRaises(ValueError): self.export(repo=checkpoint.PUBLIC_REPO.upper())
        self.assertFalse(self.out.exists())
        self.export()
        for private, push in [(False, True), (None, True), (True, False)]:
            self.api.private = private; self.api.push = push
            with self.assertRaises(ValueError): checkpoint.save(self.out, 'blueprint-checkpoints/test', self.api)
            self.assertEqual(self.api.writes, [])
        with self.assertRaises(ValueError):
            checkpoint.private_repo({'repository_full_name': checkpoint.PUBLIC_REPO, 'visibility': 'private'}, self.repo)

    def test_mixed_database_credentials_backup_and_progress_are_blocked_locally(self):
        for name in ['access.json', '.env', '.dev.vars', 'backup.zip', 'company.db', 'INSTALL_PROGRESS.md']:
            f = self.source / name; f.write_text('private fixture')
            with self.assertRaises(ValueError): self.export()
            self.assertFalse(self.out.exists()); f.unlink()

    def test_platform_config_is_omitted_but_site_id_must_match_on_restore(self):
        d = self.source / '.openai'; d.mkdir()
        (d / 'hosting.json').write_text(json.dumps({'project_id': 'original-site', 'token': 'test-only-placeholder'}))
        self.export()
        with zipfile.ZipFile(self.out / 'source.zip') as archive:
            self.assertNotIn('.openai/hosting.json', archive.namelist())
        with self.assertRaises(ValueError):
            checkpoint.restore(self.out, self.root / 'wrong-site', self.repo, self.url, 'another-site')
        self.assertFalse((self.root / 'wrong-site').exists())
        checkpoint.restore(self.out, self.root / 'same-site', self.repo, self.url, 'original-site')

    def test_failed_remote_readback_cannot_produce_success_receipt(self):
        self.export(); self.api.fail_readback = True
        with self.assertRaises(ValueError): checkpoint.save(self.out, 'blueprint-checkpoints/test', self.api)
        self.assertFalse((self.out / 'remote-receipt.json').exists())
        self.assertIn('refs/heads/blueprint-checkpoints/test', self.api.refs)
        self.api.fail_readback = False
        commit = self.api.refs['refs/heads/blueprint-checkpoints/test']
        checkpoint.fetch(self.repo, commit, self.root / 'recovered', self.url, api=self.api)

    def test_existing_branch_and_output_never_overwritten(self):
        self.export(); before = '1' * 40
        self.api.refs['refs/heads/blueprint-checkpoints/test'] = before
        with self.assertRaises(ValueError): checkpoint.save(self.out, 'blueprint-checkpoints/test', self.api)
        self.assertEqual(self.api.refs['refs/heads/blueprint-checkpoints/test'], before)
        with self.assertRaises(ValueError): checkpoint.save(self.out, 'main', self.api)
        with self.assertRaises(ValueError): self.export()
        with self.assertRaises(ValueError): checkpoint.restore(self.out, self.source, self.repo, self.url)
        self.assertEqual((self.source / 'main.py').read_text(), '# original\n')

    def test_tampered_archive_wrong_origin_manifest_and_git_blob_are_rejected(self):
        exported = self.export()
        with self.assertRaises(ValueError):
            checkpoint.restore(self.out, self.root / 'bad', self.repo, 'https://another.example')
        with self.assertRaises(ValueError):
            checkpoint.restore(self.out, self.root / 'bad', self.repo, self.url, expected_manifest='0' * 64)
        checkpoint.save(self.out, 'blueprint-checkpoints/test', self.api)
        for blob in self.api.blobs.values():
            blob['content'] = base64.b64encode(b'changed').decode(); blob['size'] = 7
        with self.assertRaises(ValueError):
            checkpoint.fetch(self.repo, next(iter(self.api.commits)), self.root / 'bad', self.url, api=self.api)
        self.assertFalse((self.root / 'bad').exists())
        (self.out / 'source.zip').write_bytes(b'corrupt')
        with self.assertRaises(ValueError): checkpoint.restore(self.out, self.root / 'bad', self.repo, self.url)

    def test_traversal_symlink_duplicate_archive_and_nested_output_rejected(self):
        with self.assertRaises(ValueError):
            checkpoint.export_source(self.source, self.source / 'export', self.repo, self.url, '2.4.2')
        (self.source / 'link').symlink_to(self.source / 'main.py')
        with self.assertRaises(ValueError): self.export()
        (self.source / 'link').unlink(); self.export()
        for name in ['../escape', '/absolute', 'a//b', 'a/./b', 'a\\b', '.git/config']:
            with self.subTest(name=name):
                raw = io.BytesIO()
                with zipfile.ZipFile(raw, 'w') as archive: archive.writestr(name, b'bad')
                m = json.loads((self.out / 'checkpoint.json').read_text())
                m['files'] = {name: {'sha256': checkpoint.digest(b'bad'), 'mode': 0o644}}
                m['archive_sha256'] = checkpoint.digest(raw.getvalue())
                (self.out / 'checkpoint.json').write_text(json.dumps(m))
                (self.out / 'source.zip').write_bytes(raw.getvalue())
                with self.assertRaises(ValueError): checkpoint.restore(self.out, self.root / 'bad', self.repo, self.url)
                self.assertFalse((self.root / 'bad').exists())


if __name__ == '__main__':
    unittest.main()
