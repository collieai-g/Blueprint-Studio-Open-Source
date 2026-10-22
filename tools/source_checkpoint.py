#!/usr/bin/env python3
"""Keep reviewed source across environments, separately from production backups.

save/fetch use the user's existing gh authentication. export/restore also support
AI GitHub connectors. Never deploys, changes repo visibility, or executes source.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.update_assistant import git, hosting, now, origin, outside, path, read_json, write_json

PUBLIC_REPO = 'collieai-g/Blueprint-Studio-Open-Source'
LIMIT = 32 * 1024 * 1024
ARTIFACTS = {'checkpoint.json', 'source.zip'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def repository(value):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value or ''):
        raise ValueError('請提供獨立私人倉庫的 owner/repo。')
    if value.lower() == PUBLIC_REPO.lower():
        raise ValueError('開源倉庫不能保存任何私人來源或接續資料。')
    return value


def private_repo(info, requested, write=False):
    actual = repository(info.get('full_name') or info.get('repository_full_name'))
    if actual.lower() != repository(requested).lower():
        raise ValueError('倉庫名稱已變更；先由平台核對新名稱，再重新執行。')
    if not (info.get('private') is True or info.get('visibility') == 'private'):
        raise ValueError('只能使用已核實的私人倉庫；公開或未知可見性均停止。')
    if info.get('visibility') in {'public', 'internal'} or info.get('private') is False:
        raise ValueError('倉庫並非私人，未有上傳。')
    if write and info.get('permissions', {}).get('push') is not True:
        raise ValueError('沒有已核實的私人倉庫寫入權限。')
    return actual


def relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)
            or any(part in {'', '.', '..'} for part in value.split('/'))):
        raise ValueError('來源包含不安全路徑。')
    return Path(value)


def source_name(name):
    p = relative(name)
    parts = {part.lower() for part in p.parts}
    if parts & {'.git', '.openai', '.aws', '.ssh', '.codex', 'node_modules', '__pycache__'}:
        return False
    if (parts & {'data', 'uploads', 'attachments', 'backups', 'private-job'}
            or p.name.lower() in {'access.json', 'credentials.json', 'install_progress.md'}
            or p.name.lower().startswith(('.env', '.dev.vars')) and p.name != '.env.example'
            or p.suffix.lower() in {'.db', '.sqlite', '.sqlite3', '.bundle', '.zip', '.pem', '.key', '.p12', '.pyc'}):
        raise ValueError('來源混入私人資料、備份或登入檔；先分開保存，不能上傳。')
    return True


def export_source(project, output, repo, url, version, role='candidate', site_id=None):
    """Local only. AI reviews source before an explicit private-repository save."""
    repo = repository(repo)
    project, output = path(project), path(output)
    outside(output); outside(output, project)
    if output.exists():
        raise ValueError('檢查點目錄已存在，未覆寫。')
    if path(git(project, 'rev-parse', '--show-toplevel').decode().strip()) != project:
        raise ValueError('請指定原來源 Git 根目錄。')
    url = origin(url)
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or role not in {'baseline', 'candidate'}:
        raise ValueError('請提供確切版本及 baseline／candidate 用途。')
    recorded = hosting(project)
    if recorded and site_id and recorded != site_id:
        raise ValueError('原 Site ID 不符。')
    site_id = recorded or site_id
    if site_id and not re.fullmatch(r'[A-Za-z0-9_~-]{1,240}', site_id):
        raise ValueError('Site ID 格式不正確。')
    head = git(project, 'rev-parse', 'HEAD').decode().strip()
    names = sorted(set(filter(None, git(project, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0'))))
    files, omitted, total = {}, [], 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            if not source_name(name):
                omitted.append(name); continue
            current = path(project / name)
            if not current.exists():
                continue  # Retain tracked deletion effects in the complete tree.
            if not current.is_file():
                raise ValueError('子模組或特殊檔案須另行保存，不能標示來源已完整。')
            if total + current.stat().st_size > LIMIT:
                raise ValueError('來源超過 32 MiB；請使用已核實的私人儲存途徑。')
            data = current.read_bytes(); total += len(data)
            mode = 0o755 if current.stat().st_mode & 0o111 else 0o644
            entry = zipfile.ZipInfo(name); entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | mode) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
            files[name] = {'sha256': digest(data), 'mode': mode}
    if not files or total > LIMIT:
        raise ValueError('沒有可保存的來源或超過容量。')
    if head != git(project, 'rev-parse', 'HEAD').decode().strip():
        raise ValueError('保存期間提交已改變，請停止修改後重試。')
    current_names = set(filter(None, git(project, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')))
    current_names = {n for n in current_names if source_name(n) and path(project / n).exists()}
    if current_names != set(files):
        raise ValueError('保存期間來源清單已改變，請重試。')
    for name, entry in files.items():
        if digest(path(project / name).read_bytes()) != entry['sha256']:
            raise ValueError('保存期間來源內容已改變，請重試。')
    manifest = {'format': 'blueprint-source-checkpoint', 'format_version': 1,
                'created_at': now(), 'repository': repo, 'origin': url, 'site_id': site_id,
                'source_commit': head, 'source_dirty': bool(git(project, 'status', '--porcelain')),
                'target_version': version, 'role': role, 'files': files, 'omitted': omitted,
                'archive_sha256': digest(buffer.getvalue()), 'production_status': 'not_verified',
                'scope': 'source only; no Git history, platform configuration or production backup'}
    output.mkdir(parents=True, mode=0o700)
    (output / 'source.zip').write_bytes(buffer.getvalue()); (output / 'source.zip').chmod(0o600)
    write_json(output / 'checkpoint.json', manifest)
    return {'status': 'local_only', 'directory': str(output), 'repository': repo,
            'manifest_sha256': digest((output / 'checkpoint.json').read_bytes()),
            'files': len(files), 'production_status': 'not_verified'}


def validate(directory, repo, url, site_id=None, expected_manifest=None):
    directory = path(directory)
    manifest_bytes = (directory / 'checkpoint.json').read_bytes()
    if expected_manifest and digest(manifest_bytes) != expected_manifest:
        raise ValueError('遠端清單與本次保存的來源不同。')
    m = read_json(directory / 'checkpoint.json')
    if (m.get('format') != 'blueprint-source-checkpoint' or m.get('format_version') != 1
            or m.get('repository', '').lower() != repository(repo).lower()
            or m.get('origin') != origin(url) or m.get('site_id') != site_id
            or m.get('production_status') != 'not_verified'):
        raise ValueError('檢查點格式、私人倉庫或原工作台身份不符。')
    archive_file = path(directory / 'source.zip')
    if archive_file.stat().st_size > LIMIT:
        raise ValueError('來源包超過容量。')
    data = archive_file.read_bytes()
    if digest(data) != m.get('archive_sha256'):
        raise ValueError('來源包校驗失敗。')
    files = m.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('來源清單缺失。')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if (len(entries) != len(files) or set(archive.namelist()) != set(files)
                or sum(e.file_size for e in entries) > LIMIT):
            raise ValueError('來源清單、重複檔案或容量不符。')
        for e in entries:
            item = files[e.filename]
            if (not source_name(e.filename) or not isinstance(item, dict)
                    or item.get('mode') not in {0o644, 0o755}
                    or stat.S_IFMT(e.external_attr >> 16) != stat.S_IFREG
                    or digest(archive.read(e)) != item.get('sha256')):
                raise ValueError('來源路徑、類型或檔案校驗失敗。')
    return m


def restore(directory, output, repo, url, site_id=None, expected_manifest=None):
    output = path(output); outside(output); outside(output, path(directory))
    if output.exists():
        raise ValueError('還原目錄已存在，未覆寫。')
    m = validate(directory, repo, url, site_id, expected_manifest)
    output.mkdir(parents=True, mode=0o700)
    with zipfile.ZipFile(path(directory) / 'source.zip') as archive:
        for name, info in m['files'].items():
            target = output / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as stream:
                stream.write(archive.read(name))
            target.chmod(info['mode'])
    return {'status': 'source_restored_not_deployed', 'project': str(output),
            'source_commit': m['source_commit'], 'role': m['role'], 'target_version': m['target_version'],
            'site_id': m['site_id'], 'production_status': 'not_verified',
            'next': '由原平台取回部署設定及私人進度，核對完整資料備份；此目錄沒有原 Git 歷史，也未執行程式。'}


class GitHub:
    """No token handling: gh owns authentication; errors never echo API bodies."""
    def api(self, endpoint, body=None):
        args = ['gh', 'api', endpoint]
        if body is not None:
            args += ['--method', 'POST', '--input', '-']
        result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                                capture_output=True, text=True)
        if result.returncode:
            raise ValueError('GitHub 操作未完成。核對已連接帳戶或使用 AI GitHub 外掛；不要公開私人檔案。')
        return json.loads(result.stdout)


def download(api, repo, commit, destination):
    if not re.fullmatch(r'[0-9a-f]{40}', commit or ''):
        raise ValueError('請用已核對的完整遠端 commit，不能用會變動的分支代替。')
    root = f'repos/{repository(repo)}'
    c = api.api(f'{root}/git/commits/{commit}')
    if c.get('sha') != commit:
        raise ValueError('遠端提交不符。')
    tree = api.api(f'{root}/git/trees/{c["tree"]["sha"]}')
    entries = tree.get('tree', [])
    if (tree.get('truncated') or len(entries) != 2 or {e['path'] for e in entries} != ARTIFACTS
            or any(e.get('type') != 'blob' or e.get('mode') != '100644' for e in entries)):
        raise ValueError('遠端不是獨立來源檢查點。')
    destination = path(destination)
    if destination.exists():
        raise ValueError('下載目錄已存在，未覆寫。')
    payload = {}
    for e in entries:
        blob = api.api(f'{root}/git/blobs/{e["sha"]}')
        if blob.get('encoding') != 'base64' or blob.get('size', LIMIT + 1) > LIMIT:
            raise ValueError('遠端檔案格式或容量不符。')
        raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
        if (len(raw) != blob['size'] or len(raw) > LIMIT
                or hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() != e['sha']):
            raise ValueError('遠端 Git blob 校驗失敗。')
        payload[e['path']] = raw
    destination.mkdir(parents=True, mode=0o700)
    for name, data in payload.items():
        (destination / name).write_bytes(data); (destination / name).chmod(0o600)


def save(directory, branch, api=None):
    directory = path(directory); outside(directory)
    m = read_json(directory / 'checkpoint.json'); repo = repository(m['repository'])
    if not re.fullmatch(r'blueprint-checkpoints/[a-zA-Z0-9][a-zA-Z0-9_-]{0,100}', branch):
        raise ValueError('只可新增 blueprint-checkpoints/ 下的獨立分支；不可覆寫 main 或部署分支。')
    local_hash = digest((directory / 'checkpoint.json').read_bytes())
    validate(directory, repo, m['origin'], m['site_id'], local_hash)
    api = api or GitHub(); root = f'repos/{repo}'
    private_repo(api.api(root), repo, write=True)  # Before any upload.
    tree = []
    for name in sorted(ARTIFACTS):
        blob = api.api(f'{root}/git/blobs', {'encoding': 'base64',
                          'content': base64.b64encode((directory / name).read_bytes()).decode()})
        tree.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': blob['sha']})
    created_tree = api.api(f'{root}/git/trees', {'tree': tree})
    # Orphan commit: no unrelated private repo history and no executable workflows.
    commit = api.api(f'{root}/git/commits', {'message': 'Blueprint source checkpoint',
                      'tree': created_tree['sha'], 'parents': []})['sha']
    # POST only. Existing refs fail; no force push or default branch mutation.
    api.api(f'{root}/git/refs', {'ref': 'refs/heads/' + branch, 'sha': commit})
    private_repo(api.api(root), repo, write=True)
    ref = api.api(f'{root}/git/ref/heads/{branch}')
    if ref.get('object', {}).get('sha') != commit:
        raise ValueError('遠端分支未指向本次提交，未標記接續可用。')
    downloaded = directory / ('readback-' + commit)
    download(api, repo, commit, downloaded)
    validate(downloaded, repo, m['origin'], m['site_id'], local_hash)
    receipt = {'status': 'remote_source_verified', 'repository': repo, 'branch': branch,
               'commit': commit, 'manifest_sha256': local_hash, 'verified_at': now(),
               'production_status': 'not_verified', 'data_backup': 'not_included'}
    write_json(directory / 'remote-receipt.json', receipt)
    return receipt


def fetch(repo, commit, output, url, site_id=None, api=None):
    repo = repository(repo); output = path(output); outside(output)
    api = api or GitHub()
    private_repo(api.api(f'repos/{repo}'), repo)
    download(api, repo, commit, output)
    return restore(output, output.parent / (output.name + '-source'), repo, url, site_id)


def main(argv=None):
    p = argparse.ArgumentParser(description='私人來源跨環境接續；不部署、不保存公司資料到 GitHub')
    sub = p.add_subparsers(dest='command', required=True)
    e = sub.add_parser('export')
    for n in ['project', 'output', 'repo', 'url', 'version']: e.add_argument('--' + n, required=True)
    e.add_argument('--site-id'); e.add_argument('--role', choices=['baseline', 'candidate'], default='candidate')
    s = sub.add_parser('save'); s.add_argument('--directory', required=True); s.add_argument('--branch', required=True)
    r = sub.add_parser('restore')
    for n in ['directory', 'output', 'repo', 'url']: r.add_argument('--' + n, required=True)
    r.add_argument('--site-id'); r.add_argument('--expected-manifest')
    f = sub.add_parser('fetch')
    for n in ['repo', 'commit', 'output', 'url']: f.add_argument('--' + n, required=True)
    f.add_argument('--site-id')
    args = vars(p.parse_args(argv)); command = args.pop('command')
    try:
        result = {'export': export_source, 'save': save, 'restore': restore, 'fetch': fetch}[command](**args)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        # No raw command output, credential material or private source in errors.
        message = str(exc) if type(exc) is ValueError else '未完成。核對私人倉庫、原來源、身份及校驗清單；沒有部署或覆寫原服務。'
        print(json.dumps({'error': message}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
