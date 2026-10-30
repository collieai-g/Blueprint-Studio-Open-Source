#!/usr/bin/env python3
"""Create signed immutable test/stable/private channels; never upload or deploy."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.installer_release import InstallerReleaseError, canonical_json
from blueprint_library.installer_catalog import CHANNELS, verify_catalog
from tools.package_managed_release import _external
from blueprint_folders.console import configure_utf8_streams


def build_catalog(releases, output, private_key, key_id, trusted_keys, *, channel,
                  audience, sequence, expires_at, revoked=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    output, private_key = _external(output), _external(private_key)
    if output.exists() or output == private_key:
        raise InstallerReleaseError('渠道輸出已存在，不會覆寫。')
    try:
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key.read_bytes().strip(), validate=True))
    except (ValueError, OSError) as exc:
        raise InstallerReleaseError('無法讀取有效渠道簽章私鑰。') from exc
    catalog = {'contract_version': 1, 'channel': channel, 'audience': audience,
               'sequence': sequence, 'expires_at': expires_at, 'releases': releases,
               'revoked': revoked or []}
    envelope = {'catalog': catalog, 'key_id': key_id,
                'signature': base64.b64encode(key.sign(canonical_json(catalog))).decode('ascii')}
    verified = verify_catalog(envelope, trusted_keys, channel=channel,
                   installation_id=next((x for x in audience if x != '*'), 'publisher-verification'))
    with output.open('x', encoding='utf-8') as stream:
        json.dump(envelope, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'output': str(output), 'channel': channel, 'sequence': sequence,
            'catalog_id': verified['catalog_id'], 'releases': len(releases)}


def promote_catalog(source, output, private_key, key_id, trusted_keys, *, installation_id,
                    sequence, expires_at):
    """Promote the unchanged tested release envelopes, never rebuild their ZIPs."""
    checked = verify_catalog(source, trusted_keys, channel='test', installation_id=installation_id)
    return build_catalog(checked['releases'], output, private_key, key_id, trusted_keys,
        channel='stable', audience=checked['audience'], sequence=sequence, expires_at=expires_at,
        revoked=checked['revoked'])


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='建立及提升已簽章更新渠道；不執行上傳或部署')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('build', 'promote'):
        command = commands.add_parser(name)
        command.add_argument('--output', required=True)
        command.add_argument('--private-key', required=True)
        command.add_argument('--key-id', required=True)
        command.add_argument('--trusted-keys', required=True)
        command.add_argument('--sequence', type=int, required=True)
        command.add_argument('--expires-at', type=int, required=True, help='到期時間 Unix 秒')
        if name == 'build':
            command.add_argument('--channel', required=True, choices=sorted(CHANNELS))
            command.add_argument('--audience', action='append', required=True)
            command.add_argument('--release', action='append', default=[])
            command.add_argument('--revoke', action='append', default=[])
        else:
            command.add_argument('--source', required=True)
            command.add_argument('--installation-id', required=True)
    args = parser.parse_args()
    try:
        keys = json.loads(Path(args.trusted_keys).read_text(encoding='utf-8'))
        options = dict(sequence=args.sequence, expires_at=args.expires_at)
        if args.command == 'build':
            result = build_catalog([json.loads(Path(p).read_text(encoding='utf-8')) for p in args.release],
                args.output, args.private_key, args.key_id, keys, channel=args.channel,
                audience=args.audience, revoked=args.revoke, **options)
        else:
            result = promote_catalog(json.loads(Path(args.source).read_text(encoding='utf-8')),
                args.output, args.private_key, args.key_id, keys, installation_id=args.installation_id, **options)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (InstallerReleaseError, OSError, ValueError, ImportError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
