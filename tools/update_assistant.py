#!/usr/bin/env python3
"""Private, resumable upgrade preparation. Never deploys or resets a workspace.

No network calls, shell interpolation, secrets discovery, or service initialization.
Runtime observations come from the installer AI's current platform tools.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders import __version__

MAX_BYTES = 128 * 1024 * 1024
PREFERENCES = {'blueprint-workspace-v2', 'blueprint-workspace-v2.2-backup', 'blueprint-workspace-v2.7.3-backup', 'blueprint-backend-display-v1'}


def now():
    return datetime.now(timezone.utc).isoformat()


def path(value):
    result = Path(value).expanduser().absolute()
    if any(p.is_symlink() for p in (result, *result.parents)):
        raise ValueError('路徑不能包含符號連結。')
    return result.resolve()


def outside(directory, source=ROOT):
    if directory == source or source in directory.parents or directory in source.parents:
        raise ValueError('接續資料必須保存在程式目錄之外的私人位置。')


def origin(value):
    if not isinstance(value, str) or re.search(r'[\s\x00-\x1f\x7f]', value):
        raise ValueError('請提供沒有帳密的原工作台網址。')
    url = urlsplit(value)
    if (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in {'', '/'}):
        raise ValueError('請提供原工作台的根網址，不要加入帳密或查詢參數。')
    _ = url.port
    return f'{url.scheme}://{url.netloc.lower()}'


def read_json(filename):
    filename = path(filename)
    if filename.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('JSON 太大；請只保存所需檢查結果。')
    value = json.loads(filename.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('JSON 必須是物件。')
    return value


def write_json(filename, value):
    filename = path(filename)
    fd, temporary = tempfile.mkstemp(dir=filename.parent, prefix='.update-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def checksum(filename):
    digest = hashlib.sha256()
    with filename.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(project, *args):
    # Keep credentials and Git diagnostics out of reports and chat output.
    return subprocess.check_output(['git', '-C', str(project), *args], stderr=subprocess.DEVNULL)


def hosting(project):
    filename = project / '.openai/hosting.json'
    if not filename.exists():
        return None
    data = read_json(filename)
    ident = data.get('project_id')
    if not isinstance(ident, str) or not ident or len(ident) > 240 or re.search(r'\s', ident):
        raise ValueError('原 hosting.json 沒有有效 Site ID；先由 Sites 工具核對，不能猜測。')
    return ident


def capture_source(project, output):
    if path(git(project, 'rev-parse', '--show-toplevel').decode().strip()) != project:
        raise ValueError('請指定原 Git 專案的根目錄。')
    output.mkdir(mode=0o700)
    head = git(project, 'rev-parse', 'HEAD').decode().strip()
    git(project, 'bundle', 'create', str(output / 'source.bundle'), '--all')
    git(project, 'bundle', 'verify', str(output / 'source.bundle'))
    names = git(project, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    total, count, captured = 0, 0, {}
    with zipfile.ZipFile(output / 'working-tree.zip', 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(filter(None, names))):
            relative = safe_name(name)
            current = path(project / relative)
            if project not in current.parents:
                raise ValueError('來源路徑超出原專案。')
            if not current.exists():  # Tracked deletions remain absent in this tree snapshot.
                continue
            if not current.is_file():
                raise ValueError('來源包含子模組或特殊檔案，請先保存其獨立來源。')
            if (current.name == 'access.json' or current.name.startswith('.env') and current.name != '.env.example'
                    or current.suffix in {'.sqlite3', '.pyc'}):
                raise ValueError('來源包含登入／資料檔，請先分開私人資料與程式。')
            total += current.stat().st_size
            if total > MAX_BYTES:
                raise ValueError('來源超過接續包容量，請使用原平台的私人來源備份。')
            archive.write(current, relative.as_posix())
            captured[name] = checksum(current)
            count += 1
    if head != git(project, 'rev-parse', 'HEAD').decode().strip():
        raise ValueError('保存期間來源提交已變更，請在停止修改後建立新的接續目錄。')
    for filename in output.iterdir():
        filename.chmod(0o600)
    with zipfile.ZipFile(output / 'working-tree.zip') as archive:
        for name, digest in captured.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest or checksum(path(project / name)) != digest:
                raise ValueError('保存期間來源檔案已變更，請停止修改後重試。')
    result = {'commit': head,
              'dirty': bool(git(project, 'status', '--porcelain')), 'files': count,
              'captured_at': now(), 'scope': 'Git history and current non-ignored source; not a production data backup',
              'sha256': {p.name: checksum(p) for p in output.iterdir()}}
    write_json(output / 'source.json', result)
    return result


def handoff_data(filename):
    data = read_json(filename)
    if data.get('format') != 'blueprint-update-handoff' or data.get('format_version') != 1:
        raise ValueError('不支援的工作台接續資料。')
    preferences = data.get('preferences', {})
    if not isinstance(preferences, dict) or set(preferences) - PREFERENCES:
        raise ValueError('排版備份包含不支援的設定。')
    for value in preferences.values():
        if value is not None:
            if not isinstance(value, str):
                raise ValueError('排版備份格式不正確。')
            json.loads(value)
    deployment = {}
    if data.get('deployment_mode') is not None:
        backend = origin(data.get('backend_origin'))
        if (data['deployment_mode'] != 'sites_native' or data.get('route') != 'sites'
                or not backend.startswith('https://') or backend == origin(data['origin'])):
            raise ValueError('Sites 與原生後台接駁資料不一致。')
        deployment = {'deployment_mode': 'sites_native', 'backend_origin': backend,
                      'gateway_version': str(data.get('gateway_version', 'unknown'))[:80]}
    return {'format': data['format'], 'format_version': 1, 'origin': origin(data['origin']),
            'version': str(data.get('version', 'unknown'))[:80],
            'route': data.get('route') if data.get('route') in {'native', 'sites'} else 'unknown',
            'preferences': preferences, **deployment}


def prepare(project, output, url=None, handoff=None, site_id=None):
    project, output = path(project), path(output)
    outside(output); outside(output, project)
    browser = handoff_data(handoff) if handoff else None
    url = origin(url or (browser or {}).get('origin'))
    if browser and browser['origin'] != url:
        raise ValueError('瀏覽器備份與指定工作台不同，未有合併。')
    recorded_id = hosting(project)
    if site_id and recorded_id and site_id != recorded_id:
        raise ValueError('Site ID 與原專案不同，未建立另一個工作台。')
    site_id = recorded_id or site_id
    if site_id and (not isinstance(site_id, str) or len(site_id) > 240 or re.search(r'\s', site_id)):
        raise ValueError('請逐字沿用 Sites 回傳的 Site ID。')
    route = 'sites' if site_id else 'native' if (project / 'blueprint.py').is_file() else 'unknown'
    if browser and browser['route'] != 'unknown' and route != browser['route']:
        raise ValueError('來源與工作台安裝方式不一致，請先核對原專案與 Site ID。')
    if output.exists():
        job = load_job(output)
        if (job['project'], job['origin'], job['site_id']) != (str(project), url, site_id):
            raise ValueError('這個接續目錄屬於另一個專案；未覆寫。')
        if browser and (job.get('deployment_mode'), job.get('backend_origin')) != (
                browser.get('deployment_mode'), browser.get('backend_origin')):
            raise ValueError('接續紀錄的資料後台已改變，請先核對，不自動合併。')
        return {'status': 'resumed', 'job': str(output), 'target_version': job['target_version']}
    output.mkdir(parents=True, mode=0o700)
    # A failed capture is retained for diagnosis, never labelled as a finished job.
    source = capture_source(project, output / 'before-source')
    if browser:
        write_json(output / 'browser-preferences.json', browser)
    job = {'format': 'blueprint-update-job', 'format_version': 1, 'created_at': now(),
           'origin': url, 'route': route, 'site_id': site_id, 'hosting_file': recorded_id is not None, 'project': str(project),
           'target_version': __version__, 'baseline_commit': source['commit'],
           'production_status': 'not_verified', 'source_backup': 'before-source',
           'database_backup': None, 'data_path': None}
    if browser and browser.get('deployment_mode') == 'sites_native':
        job.update({key: browser[key] for key in ('deployment_mode', 'backend_origin', 'gateway_version')})
    write_json(output / 'job.json', job)
    return {'status': 'prepared', 'job': str(output), 'route': route, 'source_saved': True,
            'target_version': job['target_version'], 'production_status': 'not_verified'}


def load_job(directory):
    directory = path(directory); outside(directory)
    data = read_json(directory / 'job.json')
    if data.get('format') != 'blueprint-update-job' or data.get('format_version') != 1:
        raise ValueError('不支援的接續紀錄。')
    return data


def assess(job, observations):
    """Report separate capabilities. No supplied observation can mark deployment complete."""
    result = {'checked_at': now(), 'origin': job['origin'], 'target_version': job['target_version'],
              'production_status': 'not_verified', 'checks': []}
    def add(code, state, message):
        result['checks'].append({'code': code, 'state': state, 'message': message})
    env = observations.get('environment') or {}
    if not isinstance(env, dict):
        raise ValueError('環境觀察格式不正確。')
    policy = env.get('network_policy') or {}
    expected = observations.get('expected_config_version')
    if expected and expected != env.get('source_config_version_id'):
        add('network', 'blocked', '已發佈的設定尚未套用到目前環境。保留接續包，在使用該設定的環境繼續；不要重複加入網域。')
    elif env.get('observations_current') is not True or policy.get('state') != 'enforced':
        add('network', 'unknown', '尚未取得目前環境的生效網絡設定。由 AI 重新讀平台狀態；不要把設定草稿當成已生效。')
    elif policy.get('mode') == 'unrestricted':
        add('network', 'policy_allows', '目前網絡政策允許連線；仍需實測登入及讀取。')
    elif policy.get('mode') == 'restricted' and isinstance(policy.get('effective_allowed_hosts'), list):
        host = urlsplit(job['origin']).hostname
        allowed = any(isinstance(pattern, str) and (host == pattern.lower() or
                      pattern.startswith('*.') and host.endswith(pattern[1:].lower()))
                      for pattern in policy['effective_allowed_hosts'])
        add('network', 'policy_allows' if allowed else 'blocked',
            '目前網絡政策包含工作台網域；仍需實測登入及讀取。' if allowed else
            '目前環境不允許 ' + host + '。核對已發佈設定與目前環境；由平台套用設定，保持代理及 TLS。')
    else:
        add('network', 'unknown', '沒有完整的生效網域清單，不能推斷可連線。')
    loaded = observations.get('tools', [])
    if not isinstance(loaded, list) or not all(isinstance(tool, str) for tool in loaded):
        raise ValueError('tools 必須是目前對話實際載入的工具名稱陣列。')
    expected_tools = [t['name'] for t in read_json(ROOT / 'integrations/mcp/tools.json')['tools']]
    missing = [name for name in expected_tools if not any(t == name or t.endswith('__' + name) for t in loaded)]
    count = len(expected_tools) - len(missing)
    if count == 0:
        add('ai_tools', 'missing', '目前對話沒有藍圖工具。先檢查對話的工具選擇／登入；已安裝時不要反覆安裝。')
    elif missing:
        add('ai_tools', 'outdated_or_partial', f'目前對話已載入 {count} 個藍圖工具，仍缺 {len(missing)} 個。核對正式後台版本與外掛定義；舊版可能需要先升級再刷新工具。')
    else:
        add('ai_tools', 'loaded', '目前對話已載入全部藍圖工具；仍需實際寫入、讀回及畫面確認。')
    result['missing_tools'] = missing
    if job['route'] == 'sites':
        site = observations.get('site') or {}
        matches = site.get('id') == job['site_id'] and site.get('current_live_url', '').rstrip('/') == job['origin']
        add('site_identity', 'matched' if matches else 'unknown',
            'Sites 回傳同一個 Site ID 與正式網址。' if matches else '由 AI 讀取原 Site，核對 ID 與正式網址，不能另建網站。')
        publisher = observations.get('publisher') or {}
        add('publisher', 'reported_available' if publisher.get('available') is True else 'unknown',
            'AI 回報有可用發佈方式；正式儲存版本及部署仍待執行。' if publisher.get('available') is True else
            '先讀目前 Sites 技能，確認可用的來源推送、儲存版本及部署方式。缺少舊腳本不代表必須重建網站。')
    if job.get('deployment_mode') == 'sites_native':
        add('data_backend', 'not_verified', '這是 Sites 入口配原生後台：' + job['backend_origin'] +
            '。按 docs/SITES_NATIVE_GATEWAY.md 核對 /_blueprint/status、原生服務與持久磁碟；備份及更新在該後台執行，不等待 Sites D1／R2 快照。瀏覽器交接本身不證明綁定正確。')
    add('production_backup', 'not_verified', '程式／排版接續包不等於正式資料備份。由 AI 完成原資料庫、附件及必要設定的備份與還原核對。')
    add('production_acceptance', 'not_verified', '部署後核對同一網址、舊資料、版本及真正工具寫入／讀回；本機測試不代表正式站已更新。')
    return result


def check(directory, observations):
    directory = path(directory)
    result = assess(load_job(directory), read_json(observations))
    # Save only selected diagnoses. Raw platform responses may contain credentials.
    write_json(directory / 'checks.json', result)
    return result


def native_snapshot(directory, data):
    from tools.upgrade_workspace import snapshot
    directory, data = path(directory), path(data)
    job = load_job(directory)
    if job['route'] != 'native':
        raise ValueError('Sites 必須使用原平台儲存的備份途徑，不能拿本機空資料庫代替。')
    outside(directory, data); outside(data, path(job['project']))
    if job.get('database_backup'):
        raise ValueError('已有升級前備份，請沿用並驗證；未覆寫。')
    result = snapshot(data, directory / 'database-before')
    job['database_backup'] = 'database-before'; job['data_path'] = str(data)
    write_json(directory / 'job.json', job)
    return result


def safe_name(value):
    relative = Path(value)
    if (not value or relative.is_absolute() or '..' in relative.parts or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('封裝內有不安全路徑。')
    return relative


def pack(directory, output, project=None):
    directory, output = path(directory), path(output)
    job = load_job(directory)
    outside(output, directory); outside(output)
    source = path(project or job['project'])
    outside(directory, source); outside(output, source)
    recorded_id = hosting(source)
    if ((recorded_id is not None or job.get('hosting_file')) and recorded_id != job['site_id']
            or not recorded_id and job['site_id'] and str(source) != job['project']):
        raise ValueError('接續來源的 Site ID 已變更，請先核對原工作台。')
    # Keep every checkpoint; never replace the only copy of a prepared candidate.
    name = 'checkpoint-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    capture_source(source, directory / name)
    files, total = {}, 0
    for current in sorted(directory.rglob('*')):
        current = path(current)
        if not current.is_file():
            continue
        if current.name == 'resume-manifest.json' and current.parent == directory:
            continue
        total += current.stat().st_size
        if total > MAX_BYTES:
            raise ValueError('接續資料超過 128 MiB，請保留私人備份位置並使用平台的檔案交接方式。')
        files[current.relative_to(directory).as_posix()] = checksum(current)
    manifest = {'format': 'blueprint-update-resume', 'format_version': 1, 'created_at': now(),
                'checkpoint': name, 'private': True, 'production_status': 'not_verified', 'files': files}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open('xb') as stream:
        output.chmod(0o600)
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name in files:
                archive.write(directory / name, name)
            archive.writestr('resume-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    return {'status': 'private_resume_package_created', 'archive': str(output),
            'files': len(files), 'sha256': checksum(output), 'production_status': 'not_verified',
            'cross_environment_ready': False,
            'next': 'ZIP 仍只在目前環境。先在獨立私人位置保存並實際取回核對；來源可用 source_checkpoint.py，完整資料備份另存。'}


def unpack(archive_path, output):
    archive_path, output = path(archive_path), path(output)
    outside(output)
    if output.exists():
        raise ValueError('接續目錄已存在，未有覆寫。')
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
            raise ValueError('接續包包含重複檔案或超過容量。')
        for name in names:
            safe_name(name)
        manifest = json.loads(archive.read('resume-manifest.json'))
        if manifest.get('format') != 'blueprint-update-resume' or manifest.get('format_version') != 1:
            raise ValueError('不支援的接續包。')
        files = manifest.get('files')
        if not isinstance(files, dict) or set(files) != set(names) - {'resume-manifest.json'}:
            raise ValueError('接續包清單與檔案不一致。')
        for name, digest in files.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError('接續包檔案校驗失敗，未有還原。')
        output.mkdir(parents=True, mode=0o700)
        for name in names:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with target.open('xb') as stream:
                target.chmod(0o600); stream.write(archive.read(name))
    return {'status': 'resume_package_verified', 'job': str(output),
            'checkpoint': manifest['checkpoint'], 'production_status': 'not_verified',
            'next': '讀 job.json、checks.json 及最新 checkpoint；核對新環境，沿用原 Site 與資料。'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='減少重複操作：保存來源、檢查環境並接續同一工作台升級')
    commands = parser.add_subparsers(dest='command', required=True)
    start = commands.add_parser('prepare')
    start.add_argument('--project', required=True); start.add_argument('--output', required=True)
    start.add_argument('--url'); start.add_argument('--handoff'); start.add_argument('--site-id')
    inspect = commands.add_parser('check')
    inspect.add_argument('--job', required=True); inspect.add_argument('--observations', required=True)
    save = commands.add_parser('snapshot')
    save.add_argument('--job', required=True); save.add_argument('--data', required=True)
    save.add_argument('--service-stopped', action='store_true', required=True)
    bundle = commands.add_parser('pack')
    bundle.add_argument('--job', required=True); bundle.add_argument('--output', required=True)
    bundle.add_argument('--project', help='接續後的新來源路徑；必須屬於原 Site')
    restore = commands.add_parser('unpack')
    restore.add_argument('--archive', required=True); restore.add_argument('--output', required=True)
    args = vars(parser.parse_args(argv)); command = args.pop('command')
    try:
        if command == 'prepare':
            result = prepare(**args)
        elif command == 'check':
            result = check(args['job'], args['observations'])
        elif command == 'snapshot':
            result = native_snapshot(args['job'], args['data'])
        elif command == 'pack':
            result = pack(args['job'], args['output'], args['project'])
        else:
            result = unpack(args['archive'], args['output'])
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        message = str(exc) if type(exc) is ValueError else '未完成。請核對原來源、私人路徑及輸入格式；原服務與資料沒有被本工具覆寫。'
        print(json.dumps({'error': message}, ensure_ascii=False), file=sys.stderr); return 2


if __name__ == '__main__':
    raise SystemExit(main())
