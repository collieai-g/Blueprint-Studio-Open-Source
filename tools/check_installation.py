#!/usr/bin/env python3
"""Exercise a real native installation's backup download and isolated restore.

Credentials come only from the existing backend environment variables. This
does not publish, reset a database, or claim that the chat's MCP tools are loaded.
"""
import argparse
import hashlib
import re
import uuid
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprint_library import backup
from blueprint_library.maintenance import atomic_json
from blueprint_library.remote import WorkdayRemote, _NoRedirect
from blueprint_library.server import make_server
from blueprint_library.store import LibraryError
from blueprint_folders import __version__


def _verify_legacy(url, output):
    output = backup.safe_path(output); backup.external(output)
    if output.exists():
        raise ValueError('請使用新的私人驗收目錄。')
    remote = WorkdayRemote({'url': url})
    info = remote._request('GET', '/api/info')
    if info.get('version') != __version__:
        raise ValueError('請使用與正式後台相同版本的驗收工具。')
    status = remote._request('GET', '/api/maintenance')
    if not status.get('supported') or status.get('status') != 'ready':
        raise ValueError('正式後台尚未具備原生備份能力。')
    before = (status.get('last_backup') or {}).get('id')
    remote._request('POST', '/api/maintenance/backup', {})
    deadline = time.monotonic() + 600
    while True:
        if time.monotonic() >= deadline:
            raise ValueError('備份尚未完成；請沿用後台進度核對，不要重設資料。')
        try:
            status = remote._request('GET', '/api/maintenance')
        except LibraryError as exc:
            # A large backup pauses HTTP admissions. Retry only reads: never
            # queue another backup after a timeout or turn it into success.
            if exc.status != 502:
                raise
            time.sleep(1)
            continue
        receipt = status.get('last_backup') or {}
        if status.get('error') and not status.get('busy'):
            raise ValueError('正式備份未完成，請核對服務儲存空間及權限。')
        if receipt.get('id') and receipt['id'] != before and not status.get('busy'):
            break
        time.sleep(1)
    import re
    if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', receipt['id']):
        raise ValueError('備份識別碼無效。')
    output.mkdir(parents=True, mode=0o700)
    archive = output / 'backup.zip'
    request = Request(remote.url + '/api/maintenance/backups/' + receipt['id'],
                      headers={'Authorization': remote._authorization()})
    with build_opener(_NoRedirect()).open(request, timeout=120) as response, archive.open('xb') as stream:
        archive.chmod(0o600)
        if response.headers.get_content_type() != 'application/zip':
            raise ValueError('伺服器未回傳備份封包。')
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 8 * 1024**3:
                raise ValueError('備份超過下載上限；請使用主機備份路線。')
            stream.write(chunk)
    unpacked = output / 'snapshot'
    backup.unpack(archive, unpacked, receipt['sha256'])
    restored = output / 'restore-check'
    backup.restore(unpacked, restored)
    server = make_server(restored)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        restored_remote = WorkdayRemote({'url': 'http://127.0.0.1:' + str(server.server_port)})
        restored_info = restored_remote._request('GET', '/api/info')
        if restored_info.get('company_profile') != info.get('company_profile'):
            raise ValueError('還原後公司設定與驗收開始時不同，請在停止修改後重試。')
        backup.verify(restored, unpacked)
    finally:
        server.shutdown(); server.server_close(); worker.join()
    shutil.rmtree(restored)
    result = {'status': 'backup_download_and_restore_verified', 'url': remote.url,
              'version': info['version'], 'snapshot_id': receipt['id'], 'sha256': receipt['sha256'],
              'authenticated_restore': True, 'persistent_restart_verified': False,
              'ai_connection_verified': False, 'off_host_durable_copy_verified': False}
    atomic_json(output / 'verification.json', result)
    return result



def _download(remote, receipt, archive):
    """Read back a known file first; retain failed attempts without overwriting."""
    if archive.exists():
        if (not archive.is_symlink() and archive.stat().st_size == receipt['size']
                and backup.digest(archive) == receipt['sha256']):
            return
        archive.rename(archive.with_name('backup.invalid-' + uuid.uuid4().hex + '.zip'))
    # A previous process may have completed the bytes but stopped before the
    # final rename. Read the same private attempt before downloading it again.
    for candidate in sorted(archive.parent.glob('backup.partial-*.zip'))[:1000]:
        if (not candidate.is_symlink() and candidate.is_file()
                and candidate.stat().st_size == receipt['size']
                and backup.digest(candidate) == receipt['sha256']):
            candidate.rename(archive)
            return
    partial = archive.with_name('backup.partial-' + uuid.uuid4().hex + '.zip')
    request = Request(remote.url + '/api/maintenance/backups/' + receipt['id'],
                      headers={'Authorization': remote._authorization()})
    with build_opener(_NoRedirect()).open(request, timeout=120) as response, partial.open('xb') as stream:
        partial.chmod(0o600)
        if response.headers.get_content_type() != 'application/zip':
            raise ValueError('伺服器未回傳備份封包。')
        size = 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > 8 * 1024**3:
                raise ValueError('備份超過下載上限；請使用主機備份路線。')
            stream.write(chunk)
    if partial.stat().st_size != receipt['size'] or backup.digest(partial) != receipt['sha256']:
        raise ValueError('私人下載未通過大小與 SHA256 核對；保留檔案供診斷，重試同一備份 ID。')
    partial.rename(archive)


def _authenticated_restore(snapshot, output, info):
    restored = output / 'restore-check'
    if restored.exists():
        restored.rename(output / ('restore-interrupted-' + uuid.uuid4().hex))
    backup.restore(snapshot, restored)
    server = make_server(restored)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        restored_remote = WorkdayRemote({'url': 'http://127.0.0.1:' + str(server.server_port)})
        restored_info = restored_remote._request('GET', '/api/info')
        if restored_info.get('company_profile') != info.get('company_profile'):
            raise ValueError('還原後公司設定與驗收開始時不同，請在停止修改後重試。')
        backup.verify(restored, snapshot)
    finally:
        server.shutdown(); server.server_close(); worker.join()
    shutil.rmtree(restored)


def verify_installation(url, output):
    """Resume native v2 acceptance; legacy hosts keep their original semantics."""
    output = backup.safe_path(output); backup.external(output)
    remote = WorkdayRemote({'url': url})
    info = remote._request('GET', '/api/info')
    if info.get('version') != __version__:
        raise ValueError('請使用與正式後台相同版本的驗收工具。')
    status = remote._request('GET', '/api/maintenance')
    if not status.get('supported') or status.get('status') != 'ready':
        raise ValueError('正式後台尚未具備原生備份能力。')
    if status.get('maintenance_contract') != 'blueprint-maintenance/2':
        return _verify_legacy(url, output)
    account = info.get('account', {}).get('username')
    if not isinstance(account, str) or not account:
        raise ValueError('正式後台未回報目前登入身份，不能接續私人驗收。')
    coverage = status.get('coverage')
    if not isinstance(coverage, dict) or not re.fullmatch('[0-9a-f]{64}', coverage.get('digest', '')):
        raise ValueError('正式後台未回報一致資料指紋。')
    progress_path = output / 'installation-check.json'
    backup.safe_path(progress_path)
    if output.exists():
        if not progress_path.is_file():
            raise ValueError('私人目錄缺少原驗收進度；不會覆寫已有內容。')
        progress = json.loads(progress_path.read_text())
        if (progress.get('format') != 'blueprint-installation-check/2' or progress.get('url') != remote.url
                or progress.get('account') != account or progress.get('version') != info['version']):
            raise ValueError('私人驗收目錄屬於另一個服務、登入身份或程式版本。')
        if progress.get('coverage') != coverage:
            raise ValueError('正式資料已改變；原驗收證據保留，請為新資料狀態使用新的私人驗收目錄。')
        if (not re.fullmatch(r'installation-[0-9a-f]{32}', progress.get('operation_id', ''))
                or not re.fullmatch('[0-9a-f]{64}', progress.get('plan_digest', ''))):
            raise ValueError('私人驗收工作識別碼或計劃指紋無效。')
    else:
        output.mkdir(parents=True, mode=0o700)
        plan = {'url': remote.url, 'account': account, 'version': info['version'],
                'coverage': coverage, 'purpose': 'installation-acceptance-v2'}
        progress = {'format': 'blueprint-installation-check/2', **plan,
                    'operation_id': 'installation-' + uuid.uuid4().hex,
                    'plan_digest': hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
                    'stage': 'select_backup'}
        atomic_json(progress_path, progress)
    def save(stage):
        progress['stage'] = stage; atomic_json(progress_path, progress)
    if not progress.get('backup_id'):
        last = status.get('last_backup') or {}
        protection = status.get('protection') or {}
        if (progress['stage'] == 'select_backup' and last.get('platform_verified')
                and last.get('restore_verified') and protection.get('current_coverage_matches')
                and protection.get('retention_valid')):
            progress['backup_id'] = last['id']; save('download')
        else:
            # Persist before a mutation. On retry, read this exact request before
            # deciding whether the same idempotent request still needs submitting.
            save('request_backup')
            try:
                response = remote._request('GET', '/api/maintenance', query={'operation_id': progress['operation_id']})
                request = response['backup_request']
            except LibraryError as exc:
                if exc.status != 404:
                    raise
                try:
                    request = remote._request('POST', '/api/maintenance/backup', {
                        'contract_version': 2, 'operation_id': progress['operation_id'],
                        'plan_digest': progress['plan_digest']})
                except LibraryError as write_error:
                    if write_error.status != 502:
                        raise
                    request = remote._request('GET', '/api/maintenance',
                                              query={'operation_id': progress['operation_id']})['backup_request']
            deadline = time.monotonic() + 600
            while request.get('status') != 'complete':
                if request.get('plan_digest') != progress['plan_digest']:
                    raise ValueError('原備份工作結果未知或計劃不符；先核對同 ID，不建立另一份備份。')
                if request.get('status') == 'unknown':
                    # The worker may have saved a complete archive before losing
                    # its final journal write. Read that exact ID; never create a
                    # replacement merely because the request outcome is unknown.
                    known = remote._request('GET', '/api/maintenance/backups/' + request['backup_id'] + '/metadata')
                    if (known.get('platform_verified') and known.get('restore_verified')
                            and known.get('coverage') == coverage):
                        request = {**request, 'status': 'complete'}
                        break
                    raise ValueError('原備份工作結果未知；同 ID 未有完整證據，不建立另一份備份。')
                if time.monotonic() >= deadline:
                    raise ValueError('備份尚未完成；沿用此私人目錄及工作識別碼重試。')
                time.sleep(1)
                try:
                    request = remote._request('GET', '/api/maintenance',
                                              query={'operation_id': progress['operation_id']})['backup_request']
                except LibraryError as exc:
                    if exc.status != 502:
                        raise
            if request.get('plan_digest') != progress['plan_digest']:
                raise ValueError('原備份工作使用另一份計劃；先讀回原紀錄。')
            progress['backup_id'] = request['backup_id']; save('download')
    name = progress['backup_id']
    if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{12}', name):
        raise ValueError('備份識別碼無效。')
    receipt = remote._request('GET', '/api/maintenance/backups/' + name + '/metadata')
    if (receipt.get('id') != name or receipt.get('coverage') != coverage or not receipt.get('platform_verified')
            or not receipt.get('restore_verified') or type(receipt.get('size')) is not int
            or not re.fullmatch('[0-9a-f]{64}', receipt.get('sha256', ''))):
        raise ValueError('所選備份未完整覆蓋目前資料，不能當作新的驗收。')
    age = time.time() - receipt.get('created_at', 0)
    if not 0 <= age < status.get('backup_interval_seconds', 0):
        raise ValueError('原備份已超出保留政策；證據仍保留，請為新的保護週期使用新的私人驗收目錄。')
    archive = output / 'backup.zip'
    _download(remote, receipt, archive)
    snapshot = output / 'snapshot'
    if snapshot.exists():
        try:
            backup.check(snapshot)
            if backup.coverage(snapshot / 'data')['digest'] != coverage['digest']:
                raise ValueError('指紋不同。')
        except (OSError, ValueError):
            snapshot.rename(output / ('snapshot-invalid-' + uuid.uuid4().hex))
    if not snapshot.exists():
        backup.unpack(archive, snapshot, receipt['sha256'])
    result_path = output / 'verification.json'
    backup.safe_path(result_path)
    previous = json.loads(result_path.read_text()) if result_path.is_file() else None
    reusable = bool(previous and previous.get('url') == remote.url
                    and previous.get('version') == info['version'] and previous.get('coverage') == coverage
                    and previous.get('snapshot_id') == name and previous.get('sha256') == receipt['sha256']
                    and previous.get('account') == account and previous.get('authenticated_restore') is True)
    if not reusable:
        save('authenticated_restore')
        _authenticated_restore(snapshot, output, info)
    fresh = remote._request('GET', '/api/maintenance')
    if fresh.get('coverage') != coverage:
        raise ValueError('驗收期間正式資料有變動；保留現有下載及證據，不宣稱新狀態已通過。')
    result = previous if reusable else {
        'status': 'backup_download_and_restore_verified', 'url': remote.url,
        'version': info['version'], 'account': account, 'coverage': coverage,
        'snapshot_id': name, 'sha256': receipt['sha256'], 'size': receipt['size'], 'verified_at': time.time(),
        'authenticated_restore': True, 'persistent_restart_verified': False,
        'ai_connection_verified': False, 'off_host_durable_copy_verified': False}
    # Save actual restore evidence before sending the client attestation; an
    # ambiguous network result must not force another download or restore.
    atomic_json(result_path, result); save('private_copy_receipt')
    remote._request('POST', '/api/maintenance/backups/' + name + '/private-copy', {
        'file_reference': str(archive), 'size': receipt['size'],
        'sha256': receipt['sha256'], 'readback_verified': True})
    save('complete')
    return {**result, 'reused_verification': reusable}

def main():
    parser = argparse.ArgumentParser(description='驗證正式工作台可備份、下載及還原；帳密只讀取安全環境設定')
    parser.add_argument('--url', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_installation(args.url, args.output), ensure_ascii=False, indent=2))
        return 0
    except Exception:
        print(json.dumps({'error': '驗收未完成；請核對版本、擁有人登入、正式維護狀態、網絡及私人路徑。未改寫正式工作資料。'}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
