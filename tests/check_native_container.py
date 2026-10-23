"""Exercise the production container with synthetic private data only.

Build first: docker build -f company/Dockerfile -t blueprint-native-test .
Uses only the local Docker daemon; test-owned resources are removed on exit.
"""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprint_folders import __version__
from blueprint_library.mcp_server import Bridge
from blueprint_library.remote import WorkdayRemote, _NoRedirect
from tools.check_installation import verify_installation

test_env = dict(os.environ)
for key in ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH']:
    test_env.pop(key, None)
name = 'blueprint-lifecycle-test-' + secrets.token_hex(6)
volume = name + '-data'
test_env.update(BLUEPRINT_ADMIN_USERNAME='test-owner', BLUEPRINT_ADMIN_PASSWORD=secrets.token_urlsafe(24))
os.environ['BLUEPRINT_BACKEND_USERNAME'] = test_env['BLUEPRINT_ADMIN_USERNAME']
os.environ['BLUEPRINT_BACKEND_PASSWORD'] = test_env['BLUEPRINT_ADMIN_PASSWORD']

def docker(*args):
    return subprocess.check_output(['docker', '--host=unix:///var/run/docker.sock', *args], env=test_env, text=True).strip()

def ready(remote):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        try:
            info = remote._request('GET', '/api/info')
            assert info['version'] == __version__
            assert info['maintenance']['status'] == 'ready'
            return info
        except Exception:
            time.sleep(.25)
    raise AssertionError('Container did not become ready')

try:
    docker('volume', 'create', volume)
    with socket.socket() as port_check:
        port_check.bind(('127.0.0.1', 0))
        port = port_check.getsockname()[1]
    docker('run', '-d', '--name', name, '-p', f'127.0.0.1:{port}:8787', '-v', volume + ':/data',
           '-e', 'BLUEPRINT_ADMIN_USERNAME', '-e', 'BLUEPRINT_ADMIN_PASSWORD',
           '-e', 'BLUEPRINT_DATA_DIR=/data/library', '-e', 'BLUEPRINT_BACKUP_DIR=/data/backups',
           '-e', 'BLUEPRINT_COMPANY_NAME=Container lifecycle test', os.environ.get('BLUEPRINT_TEST_IMAGE', 'blueprint-native-test'))
    address = docker('port', name, '8787/tcp')
    remote = WorkdayRemote({'url': 'http://' + address})
    ready(remote)
    assert docker('exec', name, 'id', '-u') == '10001'
    bridge = Bridge(remote.url)
    assert len(bridge.tools()) == 42
    created = bridge.call('blueprint_save_text_file', {'name':'retained.txt', 'text':'Original attachment after restart'})
    assert 'item' in created, tuple(created)
    item = created['item']
    before = bridge.call('blueprint_get_item', {'id': item['id']})
    with tempfile.TemporaryDirectory(prefix='blueprint-container-acceptance-') as temporary:
        receipt = verify_installation(remote.url, Path(temporary) / 'download')
        assert receipt['authenticated_restore']
    old_backup = bridge.call('blueprint_backup_status', {})['last_backup']['id']
    docker('stop', '--time', '20', name)
    docker('start', name)
    ready(remote)
    assert bridge.call('blueprint_get_item', {'id': item['id']}) == before
    assert bridge.call('blueprint_backup_status', {})['last_backup']['id'] == old_backup
    request = Request(remote.url + '/api/files/' + item['id'], headers={'Authorization': remote._authorization()})
    with build_opener(_NoRedirect()).open(request, timeout=10) as response:
        assert response.read() == b'Original attachment after restart'
    print(json.dumps({'container_build':True, 'non_root':True, 'fresh_initialization':True,
                      'complete_download_authenticated_restore':True, 'same_url_volume_restart':True,
                      'original_id_attachment_login_preserved':True, 'native_tools':42}))
except Exception:
    print(docker('logs', name))
    raise
finally:
    subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'rm', '-f', name], env=test_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(['docker', '--host=unix:///var/run/docker.sock', 'volume', 'rm', volume], env=test_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
