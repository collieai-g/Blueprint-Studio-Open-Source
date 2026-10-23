#!/usr/bin/env python3
"""Package only the V2 workbench assets for an existing compatible deployment."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders import __version__
from blueprint_library.store import SCHEMA_VERSION
FILES = ['UPDATE_AI.txt', 'docs/UPDATE_ASSISTANT.md', 'library_web/update.js', 'library_web/index.html', 'library_web/style.css', 'library_web/app.js',
         'library_web/workdays.js', 'library_web/workspace.js', 'library_web/brand.svg',
         'library_web/navigation.js', 'library_web/studio.js', 'library_web/studio.css',
         'library_web/agents.js', 'library_web/agents.css', 'docs/AGENT_CONTRACT.md',
         'library_web/manifest.webmanifest', 'library_web/service-worker.js',
         'docs/V2.4.md', 'docs/STUDIO_API.md', 'library_web/connection.js', 'library_web/connection-guide.json',
         'integrations/mcp/tools.json', 'integrations/mcp/sites.mjs', 'integrations/mcp/connection.mjs',
         'docs/CONNECT.md', 'docs/AI_CONNECTION.md', 'docs/OPERATING_MODEL.md', 'docs/UPGRADE.md', 'docs/LAYOUT.md']


def main():
    parser = argparse.ArgumentParser(description='建立 V2 介面更新包；不包含私人資料或重新建立後台')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().absolute()
    paths = FILES + sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / 'assets/icons').glob('*.svg'))
    manifest = {'product': 'Blueprint Studio Open Source', 'version': __version__, 'database_schema': SCHEMA_VERSION,
                'database_migration': 'Required: additive schema 6 Agent tables; this UI package does not perform backend migration', 'requires': 'V2.6 Agent API (docs/AGENT_CONTRACT.md) plus the existing authenticated Blueprint Studio API. UI-only upgrade is insufficient. Sites backend adaptation is not included.',
                'mcp_adapter': 'Integrate with the existing verified platform identity and API; see docs/AI_CONNECTION.md.',
                'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in paths: archive.write(ROOT / name, name)
        archive.writestr('workbench-release.json', json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'archive': str(output), 'version': __version__, 'files': len(paths)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
