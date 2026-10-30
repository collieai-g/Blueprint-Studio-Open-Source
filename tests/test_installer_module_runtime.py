"""Opt-in cross-repository acceptance with real, owned Python and Node services.

Set BLUEPRINT_BLUESAI_SOURCE to a reviewed BlueSAI checkout and
BLUEPRINT_NODE to an explicit Node 24.22+ executable when it is not on PATH.
All packages, Git fixtures and company data are temporary;
this test never opens an existing studio or contacts a delivery provider.
"""
from __future__ import annotations

import base64
from contextlib import closing
import errno
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
import zipfile

from blueprint_library import backup
from blueprint_library.agents import Agents
from blueprint_library.installer import (
    Supervisor, _config, create_checkpoint, initialize_installation,
    read_status, request_apply,
)
from blueprint_library.installer_components import configure_channel, register_module
from blueprint_library.installer_modules import ModuleRuntime
from blueprint_library.maintenance import DataLease
from blueprint_library.store import Store


COMPONENT = 'bluesai-cs-0001'


def _git(source, *arguments):
    return subprocess.check_output(['git', *arguments], cwd=source, text=True,
                                   encoding='utf-8', stderr=subprocess.PIPE, timeout=30).strip()


def _free_port():
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0))
        return available.getsockname()[1]


def _business_snapshot(data):
    """Compare every company table; only the intentional Agent package edit differs."""
    with closing(sqlite3.connect(data / 'library.sqlite3')) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
            if row[0] not in {'agent_registrations', 'agent_history'}]
        return {name: sorted(connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall(),
                             key=repr) for name in tables}


class ModuleListenerProbeTests(unittest.TestCase):
    """Real Node/socket guard checks, independent of the optional BlueSAI source."""

    def test_live_listener_refused_and_owned_closed_address_can_restart(self):
        node = os.environ.get('BLUEPRINT_NODE') or shutil.which('node')
        if not node:
            self.skipTest('The standalone listener guard requires Node on PATH or BLUEPRINT_NODE.')
        node = str(Path(node).resolve())
        work = Path(tempfile.mkdtemp(prefix='blueprint-module-listener-')).resolve()
        process = None
        script = """
const http = require('node:http');
let stopping = false;
const server = http.createServer((request, response) => {
  const payload = JSON.stringify({pid: process.pid});
  response.writeHead(200, {'Content-Type': 'application/json',
    'Content-Length': Buffer.byteLength(payload), 'Connection': 'close'});
  response.end(payload);
  if (request.url === '/shutdown') stop();
});
function stop() {
  if (stopping) return;
  stopping = true;
  server.close(() => process.exit(0));
}
process.stdin.on('data', stop);
server.listen(0, '127.0.0.1', () => {
  console.log(JSON.stringify({port: server.address().port, pid: process.pid}));
});
"""
        try:
            environment = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
            process = subprocess.Popen([node, '-e', script], cwd=work, env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding='utf-8',
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            announced = queue.Queue()
            reader = threading.Thread(target=lambda: announced.put(process.stdout.readline()), daemon=True)
            reader.start()
            ready = json.loads(announced.get(timeout=15))
            reader.join(1)
            self.assertEqual(ready['pid'], process.pid)
            port = ready['port']

            def exchange(path):
                with socket.create_connection(('127.0.0.1', port), timeout=5) as client:
                    client.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                                    'Connection: close\r\n\r\n').encode('ascii'))
                    chunks = []
                    while True:
                        chunk = client.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                response = b''.join(chunks)
                self.assertTrue(response.startswith(b'HTTP/1.1 200 '), response)
                return json.loads(response.split(b'\r\n\r\n', 1)[1])

            program, data = work / 'program', work / 'data'
            program.mkdir(); data.mkdir()
            private = {'component_id': COMPONENT, 'expected_agent_id': 'synthetic-listener-agent',
                       'host': '127.0.0.1', 'port': port, 'data_dir': str(data), 'initialize': True}
            config_file = work / 'module.json'
            config_file.write_text(json.dumps(private), encoding='utf-8')
            claim = data.parent / ('.' + data.name + '.blueprint-installer.json')
            claim.write_text(json.dumps({'root': str(program), 'installation_id': 'synthetic-listener',
                'component_id': COMPONENT, 'initialization_started': False}), encoding='utf-8')
            module = {'config_file': str(config_file), 'data_dir': str(data), 'port': port,
                      'expected_agent_id': private['expected_agent_id'], 'node': node}
            config = {'root': str(program), 'installation_id': 'synthetic-listener',
                      'modules': {COMPONENT: module}, 'trusted_keys': {}}
            supervisor = SimpleNamespace(root=program, config=config, _get=lambda: {'components': {}})
            runtime = ModuleRuntime(supervisor, COMPONENT)
            release = {'release_id': 'a' * 64, 'envelope': {}}
            prepared = {'release_id': release['release_id'], 'path': str(work / 'prepared'),
                        'manifest': {'entrypoint': 'synthetic.js'}}

            class ReachedProcessStart(Exception):
                pass

            # Package verification is a successful precondition in this guard
            # test; no fake service is started after the real socket check.
            with patch('blueprint_library.installer_release.prepare_release', return_value=prepared), \
                    patch('blueprint_library.installer_modules.subprocess.Popen',
                          side_effect=ReachedProcessStart('Verified port probe reached launch.')) as launch:
                with self.assertRaises(OSError):
                    runtime.launch(release)
                launch.assert_not_called()
                self.assertFalse(json.loads(claim.read_text(encoding='utf-8'))['initialization_started'])
                self.assertIsNone(process.poll())
                self.assertEqual(exchange('/'), {'pid': process.pid})
                # Read through EOF before closing the client: Node sends FIN
                # first, leaving a real server-side TIME_WAIT on POSIX.
                self.assertEqual(exchange('/shutdown'), {'pid': process.pid})
                self.assertEqual(process.wait(timeout=15), 0)
                if os.name != 'nt':
                    legacy_errno = None
                    with socket.socket() as legacy:
                        try:
                            legacy.bind(('127.0.0.1', port))
                        except OSError as error:
                            legacy_errno = error.errno
                    if sys.platform == 'linux':
                        self.assertEqual(legacy_errno, errno.EADDRINUSE,
                            'The real Node shutdown must reproduce the original closed-address conflict.')
                    print('Module POSIX probe after owned Node HTTP shutdown: '
                          f'legacy bind errno={legacy_errno}.', flush=True)
                with self.assertRaises(ReachedProcessStart):
                    runtime.launch(release)
                launch.assert_called_once()
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.stdin.write('stop\n'); process.stdin.flush()
                    except BrokenPipeError:
                        pass  # The owned child may already be completing shutdown.
                    process.wait(timeout=15)
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            if (process is None or process.poll() is not None) and os.environ.get('BLUEPRINT_TEST_KEEP_FIXTURES') != '1':
                shutil.rmtree(work)


class ManagedModuleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        except ImportError:
            raise unittest.SkipTest('Managed release acceptance requires the cryptography extra.')
        cls.key_class = Ed25519PrivateKey
        source = os.environ.get('BLUEPRINT_BLUESAI_SOURCE')
        if not source:
            raise unittest.SkipTest('Set BLUEPRINT_BLUESAI_SOURCE to the reviewed managed BlueSAI source.')
        cls.module_source = Path(source)
        if not (cls.module_source / 'src' / 'managed-runtime.js').is_file():
            raise AssertionError('BLUEPRINT_BLUESAI_SOURCE lacks the reviewed managed runtime.')
        node = os.environ.get('BLUEPRINT_NODE') or shutil.which('node')
        if not node:
            raise unittest.SkipTest('Set BLUEPRINT_NODE to Node 24.22+ (major 24).')
        cls.node = str(Path(node).resolve())
        environment = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
        version = subprocess.check_output([cls.node, '--version'], text=True, env=environment).strip()
        cls.node_version = version
        match = re.fullmatch(r'v(\d+)\.(\d+)\.(\d+)', version)
        if not match or not (24, 22, 0) <= tuple(map(int, match.groups())) < (25, 0, 0):
            raise unittest.SkipTest('Managed BlueSAI acceptance requires Node 24.22+ (major 24).')

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix='blueprint-module-runtime-'))
        self.worker = None
        self.controller = None
        self.errors = []

    def tearDown(self):
        if self.controller:
            self.controller.stop()
        if self.worker and self.worker.is_alive():
            self.worker.join(50)
        # Never remove data beneath an owned service whose shutdown is unconfirmed.
        if not (self.worker and self.worker.is_alive()) and os.environ.get('BLUEPRINT_TEST_KEEP_FIXTURES') != '1':
            shutil.rmtree(self.work)

    def _fixture_source(self):
        fixture = self.work / 'module-source'
        fixture.mkdir()
        tracked = _git(self.module_source, 'ls-files', '-z', '--cached').split('\0')
        names = set(tracked) | {'managed-component.json', 'src/managed-runtime.js', 'src/managed-control.js'}
        for name in sorted(names):
            if not name or not (name in {'package.json', 'agent.json', 'managed-component.json', 'LICENSE',
                                        'tools/release-privacy.js'} or
                                name.startswith(('src/', 'public/', 'integrations/blueprint/'))):
                continue
            original = self.module_source / name
            self.assertTrue(original.is_file(), 'Reviewed runtime source is incomplete: ' + name)
            self.assertFalse(original.is_symlink(), 'Runtime fixture cannot copy a source symlink.')
            target = fixture / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        _git(fixture, 'init', '--quiet')
        _git(fixture, 'config', 'core.autocrlf', 'false')
        _git(fixture, 'config', 'commit.gpgsign', 'false')
        _git(fixture, 'config', 'core.hooksPath', str(self.work / 'no-hooks'))
        _git(fixture, 'config', 'user.name', 'Synthetic Installer Test')
        _git(fixture, 'config', 'user.email', 'installer-test@example.invalid')
        remote = _git(self.module_source, 'remote', 'get-url', 'origin')
        _git(fixture, 'remote', 'add', 'origin', remote)
        _git(fixture, 'add', '--all')
        _git(fixture, 'commit', '--quiet', '-m', 'Synthetic reviewed module baseline')
        return fixture

    def _package_module(self, source, label, private, core_version):
        from tools.package_managed_release import build_component
        archive = self.work / (label + '.zip')
        manifest = self.work / (label + '.json')
        build_component(source, archive, manifest, private, 'synthetic-publisher', str(archive),
                        node_executable=self.node, platforms=[sys.platform],
                        requires={'blueprint-core': [core_version]})
        return json.loads(manifest.read_text(encoding='utf-8'))

    def _seed_module(self, source, data):
        data.mkdir()
        # Adoption preserves the original private credential; missing credentials
        # must never cause the managed runtime to reset an existing service.
        (data / 'admin-token').write_text(secrets.token_hex(32), encoding='utf-8')
        script = """
import {pathToFileURL} from 'node:url';
const {Store}=await import(pathToFileURL(process.argv[1]));
const store=new Store(process.argv[2]);
store.run('INSERT INTO knowledge VALUES (?,?,?,?,?,?,?)',
  'retained-fact','Synthetic hours','general','Synthetic opening hours',1,null,1700000000000);
store.run('INSERT INTO conversations(id,channel,address,name,state,updated_at) VALUES (?,?,?,?,?,?)',
  'retained-conversation','evolution','synthetic@example.invalid','Synthetic customer','human',1700000000000);
const unknown=store.addMessage('retained-conversation','staff','Do not replay this unknown outcome',
  {status:'uncertain',providerId:'synthetic-unknown-provider-id',providerInstance:'unconfigured-synthetic'});
store.run('INSERT INTO events VALUES (?,?)','retained-event',1700000000000);
store.close();
console.log(JSON.stringify({unknown}));
"""
        environment = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
        result = subprocess.run([self.node, '--input-type=module', '-e', script,
            str(source / 'src' / 'store.js'), str(data / 'service.sqlite')],
            check=True, text=True, capture_output=True, env=environment, timeout=30)
        return json.loads(result.stdout)['unknown']

    def _wait(self, root, predicate, label):
        deadline = time.monotonic() + 80
        while time.monotonic() < deadline:
            status = read_status(root)
            self.assertFalse(self.errors, str(self.errors))
            self.assertNotIn(status['phase'], {'recovery_required', 'prepare_failed'}, str(status))
            if predicate(status):
                return status
            time.sleep(.1)
        self.fail(label + ' timed out: ' + str(status))

    def _module_readback(self, data, port):
        client = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        address = f'http://127.0.0.1:{port}'
        # This token was created only for this synthetic fixture. Never print it.
        token = (data / 'admin-token').read_text(encoding='utf-8').strip()
        login = Request(address + '/api/login', data=json.dumps({'token': token}).encode(),
                        headers={'Content-Type': 'application/json', 'Origin': address})
        with client.open(login, timeout=5) as response:
            self.assertTrue(json.loads(response.read())['ok'])
        with client.open(address + '/api/state', timeout=5) as response:
            state = json.loads(response.read())
        with client.open(address + '/api/conversations/retained-conversation', timeout=5) as response:
            messages = json.loads(response.read())['messages']
        return state, messages

    def test_real_signed_module_update_keeps_core_and_private_data(self):
        from tools.package_managed_release import build
        from tools.package_release_catalog import build_catalog

        private_key = self.key_class.generate()
        private = self.work / 'synthetic-signing.key'
        private.write_bytes(base64.b64encode(private_key.private_bytes_raw()))
        keys = {'synthetic-publisher': base64.b64encode(private_key.public_key().public_bytes_raw()).decode()}
        archive, manifest = self.work / 'core.zip', self.work / 'core.json'
        build(archive, manifest, private, 'synthetic-publisher', str(archive),
              allow_working_tree=True, platforms=[sys.platform])
        core = json.loads(manifest.read_text(encoding='utf-8'))
        core_version = core['manifest']['version']

        source = self._fixture_source()
        old = self._package_module(source, 'module-old', private, core_version)
        old_revision = _git(source, 'rev-parse', 'HEAD')
        old_version = old['manifest']['version']
        base, preview = old_version.rsplit('-preview.', 1)
        new_version = base + '-preview.' + str(int(preview) + 1)
        for name in ('package.json', 'agent.json', 'managed-component.json',
                     'integrations/blueprint/agent.json', 'integrations/blueprint/package.json'):
            path = source / name
            value = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(value['version'], old_version)
            value['version'] = new_version
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        _git(source, 'add', '--all')
        _git(source, 'commit', '--quiet', '-m', 'Synthetic next module version')
        new_revision = _git(source, 'rev-parse', 'HEAD')
        self.assertNotEqual(old_revision, new_revision)
        new = self._package_module(source, 'module-new', private, core_version)
        self.assertEqual(old['manifest']['source_revision'], old_revision)
        self.assertEqual(new['manifest']['source_revision'], new_revision)

        root, data = self.work / 'installation', self.work / 'company-data'
        backups, recovery = self.work / 'backups', self.work / 'private-recovery'
        core_port, module_port = _free_port(), _free_port()
        while module_port == core_port:
            module_port = _free_port()
        initialize_installation(root, core, keys, data_dir=data, backup_dir=backups,
            recovery_dir=recovery, username='synthetic-owner', password='synthetic-password-012345', port=core_port)
        store = Store(data)
        item = store.add_file('synthetic-retained.txt', b'Synthetic retained attachment')
        old_agent = json.loads(_git(source, 'show', old_revision + ':integrations/blueprint/agent.json'))
        registered = Agents(store).register({'manifest': old_agent, 'source': {
            'repository': old['manifest']['source_repository'], 'revision': old_revision}})['agent']
        original_agent = json.dumps(registered, sort_keys=True)
        before = _business_snapshot(data)
        access_before = (data / 'access.json').read_bytes()
        with DataLease(data):
            core_checkpoint = create_checkpoint(_config(root), 'synthetic-module-baseline')

        module_data = self.work / 'customer-service-data'
        unknown_id = self._seed_module(source, module_data)
        module_access_before = (module_data / 'admin-token').read_bytes()
        config_file = self.work / 'private-module-config.json'
        config_file.write_text(json.dumps({'contract_version': 1, 'component_id': COMPONENT,
            'expected_agent_id': registered['id'], 'host': '127.0.0.1', 'port': module_port,
            'data_dir': str(module_data), 'initialize': True, 'existing_data': True}), encoding='utf-8')
        register_module(root, old, self.node, config_file)
        feed = self.work / 'signed-test-channel.json'
        installation_id = read_status(root)['installation_id']
        build_catalog([new], feed, private, 'synthetic-publisher', keys, channel='test',
                      audience=[installation_id], sequence=1, expires_at=int(time.time()) + 3600)
        configure_channel(root, str(feed), 'test')

        self.controller = Supervisor(root, startup_timeout=40, stop_timeout=35)
        def run():
            try:
                self.controller.run()
            except Exception as error:
                self.errors.append(repr(error))
        self.worker = threading.Thread(target=run)
        self.worker.start()
        try:
            ready = self._wait(root, lambda s: s['phase'] == 'ready' and s['can_apply'], 'Initial module readiness')
            initial = self.controller._get()
            core_pid = initial['child']['pid']
            module_pid = initial['module_children'][COMPONENT]['pid']
            self.assertEqual(self.controller.modules[COMPONENT].control()['version'], old_version)
            self.assertFalse(self.controller.modules[COMPONENT].control()['paused'])
            module_before, messages_before = self._module_readback(module_data, module_port)
            self.assertEqual(ready['plan'], [{'component_id': COMPONENT, 'from': old_version, 'to': new_version}])
            pending = request_apply(root, ready['prepared']['release_id'], ready['version'])
            complete = self._wait(root, lambda s: s['phase'] == 'complete', 'Module update')
            current = self.controller._get()
            self.assertEqual(current['child']['pid'], core_pid)
            self.assertNotEqual(current['module_children'][COMPONENT]['pid'], module_pid)
            self.assertEqual(self.controller._internal(current['child'])['version'], core_version)
            runtime = self.controller.modules[COMPONENT].control()
            self.assertEqual(runtime['version'], new_version)
            self.assertEqual(runtime['node_version'], self.node_version)
            self.assertTrue(runtime['ready'])
            self.assertFalse(runtime['paused'])
            self.assertEqual(runtime['expected_agent_id'], registered['id'])
            agent = self.controller._agent(COMPONENT)['agent']
            self.assertEqual(agent['id'], registered['id'])
            self.assertEqual(agent['manifest']['version'], new_version)
            self.assertEqual(agent['source']['revision'], new_revision)
            self.assertEqual(agent['settings'], registered['settings'])
            module_after, messages_after = self._module_readback(module_data, module_port)
            self.assertEqual(module_after['knowledge'], module_before['knowledge'])
            self.assertEqual(messages_after, messages_before)
            self.assertEqual(complete['components']['blueprint-core']['version'], core_version)
            self.assertEqual(complete['components'][COMPONENT]['version'], new_version)
            retry = request_apply(root, ready['prepared']['release_id'], ready['version'])
            self.assertEqual(retry['operation_id'], pending['operation_id'])
            self.assertTrue(retry['idempotent'])
            module_checkpoint = current['module_checkpoints'][COMPONENT]
            self.assertTrue(module_checkpoint['verified'])
            self.assertTrue(module_checkpoint['restore_verified'])
            self.assertFalse(module_checkpoint['off_host_backup_verified'])
            for name in ('archive', 'private_copy'):
                self.assertEqual(backup.digest(Path(module_checkpoint[name])), module_checkpoint['sha256'])
            with zipfile.ZipFile(module_checkpoint['archive']) as saved:
                self.assertIn('data/service.sqlite', saved.namelist())
                self.assertIn('runtime-config.json', saved.namelist())
        finally:
            self.controller.stop()
            self.worker.join(50)
            self.assertFalse(self.worker.is_alive(), 'Owned services did not stop gracefully.')
        self.assertFalse(self.errors, str(self.errors))
        self.assertFalse((module_data / 'service.lock').exists())
        self.assertEqual((module_data / 'admin-token').read_bytes(), module_access_before)
        self.assertEqual(_business_snapshot(data), before)
        self.assertEqual((data / 'access.json').read_bytes(), access_before)
        self.assertEqual(Store(data).get_file(item['id'])[0].read_bytes(), b'Synthetic retained attachment')
        self.assertEqual(Agents(Store(data)).list_agents()['total'], 1)
        with closing(sqlite3.connect(data / 'library.sqlite3')) as connection:
            old_snapshot = connection.execute("SELECT snapshot FROM agent_history WHERE entity='agent' AND record_id=? AND version=1",
                                              (registered['id'],)).fetchone()[0]
        self.assertEqual(json.dumps(json.loads(old_snapshot), sort_keys=True), original_agent)
        with closing(sqlite3.connect(module_data / 'service.sqlite')) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM messages').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT status,provider_id FROM messages WHERE id=?', (unknown_id,)).fetchone(),
                             ('uncertain', 'synthetic-unknown-provider-id'))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM events WHERE id=?', ('retained-event',)).fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.assertTrue(core_checkpoint['verified'])
        self.assertTrue(core_checkpoint['private_copy_readback_verified'])
        self.assertFalse(core_checkpoint['off_host_backup_verified'])
        self.assertEqual(backup.digest(Path(core_checkpoint['private_copy'])), core_checkpoint['sha256'])
        native_archive = backups / core_checkpoint['native_backup_id'] / 'backup.zip'
        self.assertEqual(backup.digest(native_archive), core_checkpoint['sha256'])
        backup.check(native_archive.parent)


if __name__ == '__main__':
    unittest.main()
