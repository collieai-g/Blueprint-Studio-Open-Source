#!/usr/bin/env python3
"""Versioned, read-only installation/update planning and validation receipts.

This helper never executes tests, packages, deployments or business writes. Its
receipts bind an operator's observed outcome to explicit inputs; they are not
proof of production acceptance or a substitute for the maintenance backup gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blueprint_library.lifecycle import (
    PHASES, ROUTES, SOURCE_FIELDS, digest, _text, provenance, plan,
)


def inputs(project, filenames):
    """Hash explicit relevant source files, including names/modes/deletions.

    The caller supplies the frozen test scope. No traversal of ignored files,
    credentials, repository history or private runtime data occurs.
    """
    root = Path(project).absolute()
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError('來源路徑不能包含符號連結。')
    root = root.resolve()
    if not root.is_dir():
        raise ValueError('來源目錄不存在。')
    if not isinstance(filenames, list) or not filenames or len(filenames) > 10000:
        raise ValueError('請列出本次檢查的來源檔案。')
    files, total = {}, 0
    for name in sorted(set(filenames)):
        _text(name, 1000)
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or name != relative.as_posix() or '.git' in relative.parts:
            raise ValueError('來源檔案路徑不正確。')
        current = root.joinpath(*relative.parts)
        if current.is_symlink() or any(parent.is_symlink() for parent in current.parents):
            raise ValueError('來源檔案不能包含符號連結。')
        if not current.exists():
            files[name] = {'deleted': True}
            continue
        if not current.is_file():
            raise ValueError('請指定檔案，不要指定目錄。')
        if current.stat().st_size + total > 256 * 1024 * 1024:
            raise ValueError('檢查輸入超過容量上限。')
        data = current.read_bytes()
        total += len(data)
        if total > 256 * 1024 * 1024:
            raise ValueError('檢查輸入超過容量上限。')
        files[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'executable': bool(current.stat().st_mode & 0o111)}
    return {'contract_version': 1, 'files': files, 'input_tree': digest(files)}


def validation_key(input_tree, commands, artifacts=None):
    if not isinstance(input_tree, str) or not re.fullmatch(r'[0-9a-f]{64}', input_tree):
        raise ValueError('檢查輸入需要完整 input_tree。')
    if not isinstance(commands, list) or not commands or len(commands) > 100:
        raise ValueError('檢查需要實際使用的命令參數清單。')
    for argv in commands:
        if not isinstance(argv, list) or not argv or len(argv) > 100:
            raise ValueError('每個命令需要參數陣列；不執行 shell 文字。')
        for arg in argv:
            _text(arg, 2000)
    artifacts = {} if artifacts is None else artifacts
    if not isinstance(artifacts, dict) or len(artifacts) > 100:
        raise ValueError('產物清單格式不正確。')
    for name, sha in artifacts.items():
        _text(name)
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            raise ValueError('產物需要完整 SHA256。')
    return digest({'contract_version': 1, 'input_tree': input_tree, 'commands': commands, 'artifacts': artifacts})


def reuse_check(receipt, input_tree, commands, artifacts=None):
    key = validation_key(input_tree, commands, artifacts)
    reusable = (isinstance(receipt, dict) and type(receipt.get('contract_version')) is int and
                receipt['contract_version'] == 1 and receipt.get('outcome') == 'passed' and receipt.get('validation_key') == key)
    return {'contract_version': 1, 'validation_key': key, 'reusable': reusable,
            'next': 'reuse_recorded_result' if reusable else 'run_affected_check', 'production_status': 'not_verified'}


def package_preflight(manifest, source):
    """Validate existing host contract without fetching or executing package code."""
    from blueprint_library.agents import validate_manifest, _source
    clean, pinned = validate_manifest(manifest), _source(source)
    return {'contract_version': 1, 'compatible': True, 'host_contract': 'blueprint-agent/1',
            'package': {'id': clean['id'], 'version': clean['version']}, 'source': pinned,
            'package_digest': digest({'manifest': clean, 'source': pinned}),
            'requires_host_deployment': False, 'registered': False, 'execution_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description='唯讀安裝更新計劃及檢查去重；不執行部署')
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('plan'); p.add_argument('--input', required=True)
    p = commands.add_parser('inputs'); p.add_argument('--project', required=True); p.add_argument('--file', action='append', required=True)
    p = commands.add_parser('preflight'); p.add_argument('--manifest', required=True); p.add_argument('--source', required=True)
    p = commands.add_parser('reuse'); p.add_argument('--receipt', required=True); p.add_argument('--input', required=True)
    args = parser.parse_args(argv)
    def read(filename):
        path = Path(filename)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('輸入 JSON 太大。')
        return json.loads(path.read_text(encoding='utf-8'))
    try:
        if args.command == 'plan': result = plan(read(args.input))
        elif args.command == 'inputs': result = inputs(args.project, args.file)
        elif args.command == 'preflight': result = package_preflight(read(args.manifest), read(args.source))
        else:
            value = read(args.input)
            result = reuse_check(read(args.receipt), value['input_tree'], value['commands'], value.get('artifacts'))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
