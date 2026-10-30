#!/usr/bin/env python3
"""Create and supervise a private managed Blueprint installation."""
import argparse
import getpass
import json
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders.console import configure_utf8_streams


def main(argv=None):
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='藍圖工作室受管安裝器：固定套件、背景準備與安全換版')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init', help='接入已簽章核心套件；資料與憑證留在私人位置')
    init.add_argument('--root', required=True)
    init.add_argument('--release', required=True, help='本機或 HTTPS 已簽章 release JSON')
    init.add_argument('--trusted-keys', required=True, help='私人配置的 key_id 至 base64 公鑰 JSON')
    init.add_argument('--data', required=True)
    init.add_argument('--backups', required=True)
    init.add_argument('--recovery-dir')
    init.add_argument('--existing-data', action='store_true', help='接入已停止的原資料；保留登入與所有記錄')
    init.add_argument('--existing-backups', action='store_true', help='沿用已核對的原生備份身份；須同時指定 --existing-data')
    init.add_argument('--username', default='admin')
    init.add_argument('--host', default='127.0.0.1', choices=['127.0.0.1'])
    init.add_argument('--port', type=int, default=8787)
    init.add_argument('--feed', help='固定的 HTTPS 或本機更新目錄')
    init.add_argument('--channel', choices=['test', 'stable', 'private'], default='stable')
    module = commands.add_parser('module-add', help='綁定客服固定版本及原有 Agent ID')
    module.add_argument('--root', required=True)
    module.add_argument('--release', required=True)
    module.add_argument('--node', required=True)
    module.add_argument('--config', required=True, help='私人客服設定；秘密不放參數')
    channel = commands.add_parser('channel-set', help='在停止的受管安裝綁定可信發行渠道')
    channel.add_argument('--root', required=True)
    channel.add_argument('--feed', required=True)
    channel.add_argument('--channel', choices=['test', 'stable', 'private'], required=True)
    for name in ('run', 'status', 'check', 'apply'):
        command = commands.add_parser(name)
        command.add_argument('--root', required=True)
        if name == 'apply':
            command.add_argument('--release-id', required=True)
            command.add_argument('--expected-version', type=int, required=True)
            command.add_argument('--work-saved', action='store_true', help='本人確認受影響的分頁已保存並關閉')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init' and args.existing_backups and not args.existing_data:
            raise ValueError('--existing-backups 必須同時指定 --existing-data。')
        if args.command == 'init' and sys.platform == 'win32':
            from blueprint_library.installer_bootstrap import ensure_runtime
            runtime = ensure_runtime(args.root, protected=[args.data, args.backups,
                args.recovery_dir or str(Path(args.root).with_name(Path(args.root).name + '-recovery'))])
            if runtime is not None and Path(sys.prefix).resolve() != runtime.parent.parent.resolve():
                # Keep stdin for the password prompt; dependency progress is stderr.
                return subprocess.call([str(runtime), '-I', '-B', str(Path(__file__).resolve()),
                                        *(sys.argv[1:] if argv is None else argv)])
        from blueprint_library.installer import (Supervisor, initialize_installation, read_feed,
                                                 read_status, request_apply, request_check)
        from blueprint_library.installer_components import register_module, configure_channel
        if args.command == 'init':
            if args.existing_backups and not args.existing_data:
                raise ValueError('--existing-backups 必須同時指定 --existing-data。')
            password = None
            if not args.existing_data:
                password = getpass.getpass('設定管理員密碼（至少 12 字元）：')
                if password != getpass.getpass('再次輸入密碼：'):
                    raise ValueError('兩次密碼不相同。')
            result = initialize_installation(args.root, read_feed(args.release),
                       json.loads(Path(args.trusted_keys).read_text(encoding='utf-8')),
                       data_dir=args.data, backup_dir=args.backups, recovery_dir=args.recovery_dir,
                       existing_data=args.existing_data, existing_backups=args.existing_backups,
                       username=args.username, password=password,
                       host=args.host, port=args.port, feed=args.feed, channel=args.channel)
        elif args.command == 'module-add':
            result = register_module(args.root, read_feed(args.release), args.node, args.config)
        elif args.command == 'channel-set':
            result = configure_channel(args.root, args.feed, args.channel)
        elif args.command == 'run':
            supervisor = Supervisor(args.root)
            signal.signal(signal.SIGTERM, lambda *_: supervisor.stop())
            try:
                supervisor.run()
            except KeyboardInterrupt:
                supervisor.stop()
            return 0
        elif args.command == 'status':
            result = read_status(args.root)
        elif args.command == 'check':
            result = request_check(args.root)
        else:
            if not args.work_saved:
                raise ValueError('請先保存並關閉受影響分頁，再以 --work-saved 確認套用。')
            result = request_apply(args.root, args.release_id, args.expected_version)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
