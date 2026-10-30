#!/usr/bin/env python3
"""Prepare or read back a public core update publication; never upload anything."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders.console import configure_utf8_streams
from blueprint_folders.path_safety import has_link
from blueprint_library.installer import read_feed
from blueprint_library.installer_catalog import verify_catalog
from blueprint_library.installer_release import (
    InstallerReleaseError, file_digest, prepare_release, verify_prepared, verify_release)
from tools.release_policy import external_output


def _catalog(source, keys, installation_id):
    if not isinstance(source, dict) or not isinstance(source.get('catalog'), dict):
        raise InstallerReleaseError('渠道封套格式不正確。')
    channel = source['catalog'].get('channel')
    if channel not in {'test', 'stable'}:
        raise InstallerReleaseError('公開發布工具只接受 test 或 stable 核心渠道。')
    checked = verify_catalog(source, keys, channel=channel, installation_id=installation_id)
    if checked['audience'] != ['*']:
        raise InstallerReleaseError('公開渠道只接受全體適用清單，不能發布私人工作室身份。')
    for envelope in checked['releases']:
        manifest = verify_release(envelope, keys)
        if manifest['adapter'] != 'native-core' or manifest.get('candidate'):
            raise InstallerReleaseError('公開發布只接受已固定來源的原生核心包。')
    return checked


def prepare_publication(catalog_path, keys, installation_id, artifacts, output,
                        *, repository, tag):
    """Copy unchanged accepted ZIPs and the original signed catalog to staging."""
    if (not isinstance(repository, str)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository)
            or not isinstance(tag, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', tag)):
        raise InstallerReleaseError('發布倉庫或 tag 不正確。')
    catalog_path = Path(catalog_path)
    if has_link(catalog_path):
        raise InstallerReleaseError('渠道來源不能經過連結。')
    raw = catalog_path.read_bytes()
    checked = _catalog(json.loads(raw), keys, installation_id)
    output = external_output(output, ROOT)
    if output.exists():
        raise InstallerReleaseError('發布準備目錄已存在，不會覆寫。')
    paths = [Path(path) for path in artifacts]
    if any(has_link(path) or not path.is_file() for path in paths):
        raise InstallerReleaseError('程式包來源不存在或經過連結。')
    by_name = {path.name: path for path in paths}
    if len(by_name) != len(paths) or len(paths) != len(checked['releases']):
        raise InstallerReleaseError('程式包數量或名稱不符。')
    accepted = []
    with tempfile.TemporaryDirectory(prefix='blueprint-publication-check-') as temp:
        for index, envelope in enumerate(checked['releases']):
            manifest = verify_release(envelope, keys)
            name = Path(urlsplit(manifest['archive']['url']).path).name
            expected_url = f'https://github.com/{repository}/releases/download/{tag}/{name}'
            if (manifest['archive']['url'] != expected_url or name not in by_name
                    or 'release-assets.githubusercontent.com' not in manifest['archive'].get('redirect_hosts', [])):
                raise InstallerReleaseError('程式包必須先固定最終 GitHub 下載網址及簽署轉址主機。')
            path = by_name[name]
            if (path.stat().st_size != manifest['archive']['size']
                    or file_digest(path) != manifest['archive']['sha256']):
                raise InstallerReleaseError('待發布 ZIP 與已簽署內容不符。')
            # Validate extraction, inventory and the real package version without
            # rewriting the signed archive URL or executing downloaded commands.
            from blueprint_library.installer_release import safe_name
            destination = Path(temp) / str(index)
            destination.mkdir()
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if (len(members) != len(manifest['files'])
                        or {member.filename for member in members} != set(manifest['files'])):
                    raise InstallerReleaseError('待發布 ZIP 完整清單不符。')
                from blueprint_library.installer_release import MAX_EXPANDED_BYTES, MAX_FILES
                if (len(members) > MAX_FILES or sum(member.file_size for member in members) > MAX_EXPANDED_BYTES
                        or len({member.filename.casefold() for member in members}) != len(members)):
                    raise InstallerReleaseError('待發布 ZIP 超過限制或名稱重複。')
                for member in members:
                    safe_name(member.filename)
                    if (((member.external_attr >> 16) & 0o170000) not in {0, 0o100000}
                            or member.flag_bits & 1):
                        raise InstallerReleaseError('待發布 ZIP 含連結或特殊檔案。')
                    target = destination / member.filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
            verify_prepared(destination, manifest)
            accepted.append({'name': name, 'sha256': file_digest(path), 'size': path.stat().st_size})
    (output / 'release-assets').mkdir(parents=True)
    (output / 'pages' / 'channels').mkdir(parents=True)
    for record in accepted:
        target = output / 'release-assets' / record['name']
        shutil.copyfile(by_name[record['name']], target)
        if file_digest(target) != record['sha256']:
            raise InstallerReleaseError('發布複本核對失敗，未上傳。')
    (output / 'pages' / 'channels' / (checked['channel'] + '.json')).write_bytes(raw)
    receipt = {'contract_version': 1, 'repository': repository, 'tag': tag,
               'channel': checked['channel'], 'catalog_id': checked['catalog_id'],
               'artifacts': accepted, 'uploaded': False}
    (output / 'publication.json').write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return receipt


def verify_hosted(feed_url, keys, installation_id, expected_catalog_id):
    """Read only: direct HTTPS feed and the actual signed ZIP redirect chain."""
    if urlsplit(feed_url).scheme != 'https':
        raise InstallerReleaseError('網上渠道驗收必須使用固定 HTTPS 網址。')
    checked = _catalog(read_feed(feed_url), keys, installation_id)
    if checked['catalog_id'] != expected_catalog_id:
        raise InstallerReleaseError('網上渠道不是已準備的同一份清單。')
    with tempfile.TemporaryDirectory(prefix='blueprint-hosted-update-check-') as temp:
        for envelope in checked['releases']:
            prepare_release(envelope, keys, Path(temp) / 'cache')
    return {'catalog_id': checked['catalog_id'], 'channel': checked['channel'],
            'releases': len(checked['releases']), 'download_verified': True}


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='準備或讀回公開更新來源；不建立倉庫、不上傳、不部署')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'verify-hosted'):
        command = commands.add_parser(name)
        command.add_argument('--trusted-keys', required=True)
        command.add_argument('--installation-id', required=True)
        if name == 'prepare':
            command.add_argument('--catalog', required=True)
            command.add_argument('--artifact', action='append', default=[])
            command.add_argument('--output', required=True)
            command.add_argument('--repository', required=True)
            command.add_argument('--tag', required=True)
        else:
            command.add_argument('--feed', required=True)
            command.add_argument('--catalog-id', required=True)
    args = parser.parse_args()
    try:
        keys = json.loads(Path(args.trusted_keys).read_text(encoding='utf-8'))
        if args.command == 'prepare':
            result = prepare_publication(args.catalog, keys, args.installation_id,
                args.artifact, args.output, repository=args.repository, tag=args.tag)
        else:
            result = verify_hosted(args.feed, keys, args.installation_id, args.catalog_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
