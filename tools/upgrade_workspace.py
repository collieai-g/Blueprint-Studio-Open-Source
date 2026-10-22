#!/usr/bin/env python3
"""Backward-compatible native backup, verification and safe restore commands."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blueprint_library.backup import safe_path, snapshot, verify, check, restore, unpack
from blueprint_library.maintenance import DataLease


def main(argv=None):
    parser = argparse.ArgumentParser(description='完整私人備份、還原演練及資料保留核對')
    commands = parser.add_subparsers(dest='command', required=True)
    save = commands.add_parser('snapshot')
    save.add_argument('--data', required=True)
    save.add_argument('--output', required=True)
    save.add_argument('--service-stopped', action='store_true', required=True)
    validate = commands.add_parser('verify')
    validate.add_argument('--data', required=True)
    validate.add_argument('--snapshot', required=True)
    inspect = commands.add_parser('check')
    inspect.add_argument('--snapshot', required=True)
    recover = commands.add_parser('restore')
    recover.add_argument('--snapshot', required=True)
    recover.add_argument('--output', required=True)
    imported = commands.add_parser('unpack')
    imported.add_argument('--archive', required=True)
    imported.add_argument('--sha256', required=True)
    imported.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'unpack':
            result = unpack(safe_path(args.archive), safe_path(args.output), args.sha256)
        elif args.command == 'check':
            result = check(safe_path(args.snapshot))
        elif args.command == 'restore':
            result = restore(safe_path(args.snapshot), safe_path(args.output))
        else:
            data = safe_path(args.data)
            with DataLease(data):
                result = snapshot(data, safe_path(args.output)) if args.command == 'snapshot' else verify(data, safe_path(args.snapshot))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, zipfile.BadZipFile):
        print(json.dumps({'error': '未完成。請核對備份完整性、私人路徑及服務是否已停止；不會覆寫已有還原目錄。'}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
