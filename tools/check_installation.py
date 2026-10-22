#!/usr/bin/env python3
"""Exercise a real native installation's backup download and isolated restore.

Credentials come only from the existing backend environment variables. This
does not publish, reset a database, or claim that the chat's MCP tools are loaded.
"""
import argparse
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


def verify_installation(url, output):
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
