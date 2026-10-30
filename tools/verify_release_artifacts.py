#!/usr/bin/env python3
"""Accept the exact installer/workbench ZIPs before an explicit publication."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders import __version__
from blueprint_folders.console import configure_utf8_streams
from blueprint_folders.path_safety import has_link
from blueprint_library.store import SCHEMA_VERSION
from tools.release_policy import external_output, read_policy, verify_archive


def accept_artifacts(installer, workbench, *, root=None):
    root = Path(root or ROOT)
    policy = read_policy(root)
    results = []
    for kind, source, manifest_name in [
            ('installer', installer, 'installer-manifest.json'),
            ('workbench', workbench, 'workbench-release.json')]:
        source = Path(source)
        if has_link(source):
            raise ValueError('發行驗收不能經過連結。')
        manifest = verify_archive(source, manifest_name)
        expected = {name for name, groups in policy.items() if kind in groups}
        if (set(manifest['files']) != expected or manifest.get('version') != __version__
                or manifest.get('database_schema') != SCHEMA_VERSION):
            raise ValueError('發行包與被驗證來源的版本或精確清單不符。')
        for name, checksum in manifest['files'].items():
            path = root / name
            if has_link(path) or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
                raise ValueError('發行包與被驗證來源內容不符。')
        results.append({'kind': kind, 'name': source.name,
                        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                        'size': source.stat().st_size, 'files': len(expected)})
    if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root):
        raise ValueError('正式產物驗收要求乾淨固定來源，不能以舊提交冒充未提交改動。')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    return {'contract_version': 1, 'source_revision': revision,
            'version': __version__, 'artifacts': results, 'accepted': True}


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='驗收固定發行產物，不重建、不上傳')
    parser.add_argument('--installer', required=True)
    parser.add_argument('--workbench', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = accept_artifacts(args.installer, args.workbench)
    with external_output(args.output, ROOT).open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write('\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
