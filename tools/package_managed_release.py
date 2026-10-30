#!/usr/bin/env python3
"""Build and sign immutable native-core packages for the managed installer."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders.path_safety import has_link
from blueprint_folders.console import configure_utf8_streams
from tools.release_policy import selected_names
from blueprint_library.installer_release import (InstallerReleaseError, canonical_json,
                                                file_digest, safe_name, validate_release,
                                                verify_prepared, verify_runtime)

# Exact reviewed inventory lives in tools/release_files.json.
REQUIRED = {'blueprint.py', 'blueprint_library/server.py', 'blueprint_library/store.py',
            'blueprint_folders/__init__.py', 'blueprint_folders/cli.py',
            'library_web/index.html', 'library_web/app.js',
            'blueprint_library/installer_release.py', 'blueprint_library/installer.py',
            'blueprint_library/installer_http.py', 'library_web/installer.js',
            'library_web/installer.css', 'requirements-installer.txt', 'tools/studio_installer.py',
            'blueprint_library/installer_catalog.py', 'blueprint_library/installer_modules.py',
            'blueprint_library/installer_backup.py', 'blueprint_library/installer_components.py',
            'blueprint_library/installer_preview.py', 'tools/dev_preview.py'}


def _external(path):
    path = Path(path).expanduser().absolute()
    if has_link(path):
        raise InstallerReleaseError('輸出路徑不能包含符號連結。')
    path = path.resolve()
    if path == ROOT or ROOT in path.parents or any((p / '.git').exists() for p in (path.parent, *path.parents)):
        raise InstallerReleaseError('發行產物與簽章私鑰必須放在來源倉庫以外。')
    return path


def generate_key(private_output, public_output, key_id):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_output = _external(private_output)
    public_output = _external(public_output)
    if private_output == public_output or private_output.exists() or public_output.exists():
        raise InstallerReleaseError('金鑰輸出已存在，不會覆寫。')
    if not isinstance(key_id, str) or not key_id or len(key_id) > 80:
        raise InstallerReleaseError('金鑰識別碼不正確。')
    key = Ed25519PrivateKey.generate()
    descriptor = os.open(private_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as output:
        output.write(base64.b64encode(key.private_bytes_raw()) + b'\n')
    with public_output.open('x', encoding='utf-8') as output:
        json.dump({key_id: base64.b64encode(key.public_key().public_bytes_raw()).decode('ascii')}, output)
        output.write('\n')
    return {'private_key_saved': True, 'trusted_keys': str(public_output), 'key_id': key_id}


def _selected_names(allow_working_tree):
    try:
        return selected_names(ROOT, 'managed-core',
                              allow_working_tree=allow_working_tree, required=REQUIRED)
    except (ValueError, OSError) as exc:
        raise InstallerReleaseError(str(exc)) from exc


def build(output, manifest_output, private_key, key_id, archive_url, *, allow_working_tree=False,
          platforms=None, notes='', redirect_hosts=None, requires=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from blueprint_folders import __version__
    from blueprint_library.store import SCHEMA_VERSION
    output = _external(output)
    manifest_output = _external(manifest_output)
    private_key = _external(private_key)
    if output == manifest_output or output.exists() or manifest_output.exists():
        raise InstallerReleaseError('發行輸出已存在，不會覆寫。')
    status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT).decode('utf-8')
    if status and not allow_working_tree:
        raise InstallerReleaseError('正式發行要求乾淨固定來源；本機候選需明確使用 --allow-working-tree。')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
    files = {name: (ROOT / name).read_bytes() for name in _selected_names(allow_working_tree)}
    inventory = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    source_tree = hashlib.sha256(canonical_json(inventory)).hexdigest()
    try:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key.read_bytes().strip(), validate=True))
    except (ValueError, OSError) as exc:
        raise InstallerReleaseError('無法讀取有效簽章私鑰。') from exc
    # Fixed metadata makes the same input tree produce the same archive bytes.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            member = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, value)
    manifest = {'contract_version': 1, 'component_id': 'blueprint-core', 'adapter': 'native-core',
                'version': __version__, 'source_revision': revision,
                'platforms': platforms or ['win32', 'linux', 'darwin'],
                'runtime': {'python_min': [3, 10], 'python_max_exclusive': [4, 0]},
                'archive': {'url': archive_url, 'sha256': file_digest(output), 'size': output.stat().st_size},
                'files': inventory, 'entrypoint': 'blueprint.py', 'database_schema': SCHEMA_VERSION,
                'candidate': bool(allow_working_tree), 'source_tree': source_tree, 'notes': notes}
    if redirect_hosts:
        manifest['archive']['redirect_hosts'] = redirect_hosts
    if requires is not None:
        manifest['requires'] = requires
    manifest = validate_release(manifest)
    envelope = {'manifest': manifest, 'key_id': key_id,
                'signature': base64.b64encode(key.sign(canonical_json(manifest))).decode('ascii')}
    with manifest_output.open('x', encoding='utf-8') as target:
        json.dump(envelope, target, ensure_ascii=False, indent=2)
        target.write('\n')
    return {'archive': str(output), 'manifest': str(manifest_output), 'version': __version__,
            'sha256': manifest['archive']['sha256'], 'source_tree': source_tree,
            'candidate': manifest['candidate'], 'files': len(files)}


def build_component(project_root, output, manifest_output, private_key, key_id, archive_url, *,
                    node_executable, allow_working_tree=False, platforms=None, notes='',
                    redirect_hosts=None, requires=None):
    """Package reviewed BlueSAI runtime files, never its private business data."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if not isinstance(requires, dict) or not requires.get('blueprint-core'):
        raise InstallerReleaseError('客服發行必須明確提供已驗證的工作室核心版本。')
    source_input = Path(project_root).expanduser().absolute()
    if has_link(source_input):
        raise InstallerReleaseError('模組維護來源不能包含符號連結。')
    source = source_input.resolve()
    if not source.is_dir():
        raise InstallerReleaseError('模組維護來源不存在或不是一般目錄。')
    paths = [_external(x) for x in (output, manifest_output, private_key)]
    output, manifest_output, private_key = paths
    if any(x == source or source in x.parents for x in paths):
        raise InstallerReleaseError('模組產物及簽章金鑰必須放在所有維護來源以外。')
    if output == manifest_output or output.exists() or manifest_output.exists():
        raise InstallerReleaseError('發行輸出已存在，不會覆寫。')
    git = lambda *args: subprocess.check_output(['git', *args], cwd=source).decode('utf-8').strip()
    if git('status', '--porcelain') and not allow_working_tree:
        raise InstallerReleaseError('正式模組發行要求乾淨固定來源；候選需明確選擇。')
    revision = git('rev-parse', 'HEAD')
    repository = git('remote', 'get-url', 'origin')
    if repository.endswith('.git'):
        repository = repository[:-4]
    try:
        descriptor = json.loads((source / 'managed-component.json').read_text(encoding='utf-8'))
        package = json.loads((source / 'package.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise InstallerReleaseError('模組缺少正式受管發行描述。') from exc
    required = {'component_id', 'adapter', 'version', 'database_schema', 'agent_manifest'}
    if (not isinstance(descriptor, dict) or set(descriptor) != required or
            descriptor['component_id'] != 'bluesai-cs-0001' or descriptor['adapter'] != 'bluesai-node' or
            descriptor['version'] != package.get('version')):
        raise InstallerReleaseError('模組來源身份或版本描述不相符。')
    safe_name(descriptor['agent_manifest'])
    tracked = set(filter(None, git('ls-files', '-z', '--cached').split('\0')))
    extras = {'managed-component.json', 'src/managed-runtime.js', 'src/managed-control.js'}
    if allow_working_tree:
        tracked |= set(git('ls-files', '-z', '--others', '--exclude-standard').split('\0')) & extras
    suffixes = {'.js', '.mjs', '.cjs', '.json', '.html', '.css', '.svg', '.png', '.webmanifest'}
    files = {}
    for name in sorted(tracked):
        if not name or not (name in {'package.json', 'agent.json', 'managed-component.json', 'LICENSE'} or
                (name.startswith(('src/', 'public/', 'integrations/blueprint/')) and Path(name).suffix in suffixes)):
            continue
        safe_name(name)
        path = source / name
        if (not path.is_file() or has_link(path) or not path.resolve().is_relative_to(source) or
                any(part.startswith('.') for part in name.split('/')) or
                path.name in {'access.json', 'installation.json', 'creds.json', 'auth.json'}):
            raise InstallerReleaseError('模組 runtime 含不允許的檔案。')
        files[name] = path.read_bytes()
    must_have = {'package.json', 'managed-component.json', 'src/managed-runtime.js', descriptor['agent_manifest']}
    if not must_have <= files.keys():
        raise InstallerReleaseError('模組 runtime 不完整，未建立部分套件。')
    # The source's existing privacy gate is evaluated only by the publisher,
    # using an explicitly configured executable; downloaded packages never run it.
    runtime = {'node_min': [24, 22, 0], 'node_max_exclusive': [25, 0, 0]}
    verify_runtime({'adapter': 'bluesai-node', 'runtime': runtime}, {'node': node_executable})
    gate = source / 'tools/release-privacy.js'
    if not gate.is_file() or has_link(gate):
        raise InstallerReleaseError('模組維護來源缺少公開發行私隱閘門。')
    script = ("import{readFileSync}from'node:fs';import{pathToFileURL}from'node:url';"
              "import{resolve}from'node:path';const{assertPublicReleaseFile}=await import(pathToFileURL(resolve('tools/release-privacy.js')));"
              "for(const name of JSON.parse(readFileSync(0,'utf8')))assertPublicReleaseFile(name,readFileSync(name));")
    clean_env = {k: v for k, v in os.environ.items() if k not in {'NODE_OPTIONS', 'NODE_PATH'}}
    try:
        subprocess.run([str(Path(node_executable).resolve()), '--input-type=module', '-e', script],
            cwd=source, input=json.dumps(list(files)), text=True, capture_output=True,
            check=True, timeout=60, env=clean_env, shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallerReleaseError('模組公開發行私隱閘門未通過，未產生套件。') from exc
    inventory = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    source_tree = hashlib.sha256(canonical_json(inventory)).hexdigest()
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            member = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            archive.writestr(member, value)
    manifest = {'contract_version': 2, 'component_id': descriptor['component_id'], 'adapter': descriptor['adapter'],
        'version': descriptor['version'], 'source_repository': repository, 'source_revision': revision,
        'platforms': platforms or [sys.platform], 'runtime': runtime,
        'archive': {'url': archive_url, 'sha256': file_digest(output), 'size': output.stat().st_size},
        'files': inventory, 'entrypoint': 'src/managed-runtime.js', 'database_schema': descriptor['database_schema'],
        'candidate': bool(allow_working_tree), 'source_tree': source_tree, 'notes': notes,
        'requires': requires or {}}
    if redirect_hosts:
        manifest['archive']['redirect_hosts'] = redirect_hosts
    manifest = validate_release(manifest)
    try:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key.read_bytes().strip(), validate=True))
    except (ValueError, OSError) as exc:
        raise InstallerReleaseError('無法讀取有效簽章私鑰。') from exc
    envelope = {'manifest': manifest, 'key_id': key_id,
                'signature': base64.b64encode(key.sign(canonical_json(manifest))).decode('ascii')}
    with manifest_output.open('x', encoding='utf-8') as target:
        json.dump(envelope, target, ensure_ascii=False, indent=2)
        target.write('\n')
    return {'archive': str(output), 'manifest': str(manifest_output), 'version': manifest['version'],
            'component_id': manifest['component_id'], 'sha256': manifest['archive']['sha256'],
            'source_tree': source_tree, 'candidate': manifest['candidate'], 'files': len(files)}


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='建立具有 Ed25519 簽章的受管工作室安裝套件')
    sub = parser.add_subparsers(dest='command', required=True)
    key = sub.add_parser('key-gen', help='在私人位置建立簽章金鑰；不顯示私鑰')
    key.add_argument('--private-output', required=True)
    key.add_argument('--public-output', required=True)
    key.add_argument('--key-id', required=True)
    for name in ('build', 'build-component'):
        package = sub.add_parser(name, help='建立固定的可驗證受管安裝包')
        package.add_argument('--output', required=True)
        package.add_argument('--manifest-out', required=True)
        package.add_argument('--private-key', required=True)
        package.add_argument('--key-id', required=True)
        package.add_argument('--archive-url', required=True)
        package.add_argument('--allow-working-tree', action='store_true', help='明確建立本機候選，記錄真實 HEAD 與來源指紋')
        package.add_argument('--platform', action='append', choices=['win32', 'linux', 'darwin'])
        package.add_argument('--notes', default='')
        package.add_argument('--requires-json', required=name == 'build-component', help='列出已驗證元件及明確版本的 JSON 檔')
        package.add_argument('--redirect-host', action='append', help='明確列出允許的 HTTPS 重新導向主機；最多三次轉向')
        if name == 'build-component':
            package.add_argument('--project-root', required=True)
            package.add_argument('--node', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'key-gen':
            result = generate_key(args.private_output, args.public_output, args.key_id)
        else:
            options = dict(allow_working_tree=args.allow_working_tree, platforms=args.platform,
                           notes=args.notes, redirect_hosts=args.redirect_host,
                           requires=json.loads(Path(args.requires_json).read_text(encoding='utf-8')) if args.requires_json else None)
            if args.command == 'build-component':
                result = build_component(args.project_root, args.output, args.manifest_out,
                    args.private_key, args.key_id, args.archive_url, node_executable=args.node, **options)
            else:
                result = build(args.output, args.manifest_out, args.private_key, args.key_id, args.archive_url, **options)
        print(json.dumps(result, ensure_ascii=False))
    except (InstallerReleaseError, ImportError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
