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
FILES = ['docs/INSTALL_UPDATE_FLOW.md', 'docs/LIFECYCLE_API.md', 'docs/MAINTENANCE_API.md', 'AI_CONNECT.md', 'UPDATE_AI.txt', 'docs/UPDATE_ASSISTANT.md', 'library_web/update.js', 'library_web/index.html', 'library_web/style.css', 'library_web/app.js',
         'library_web/workdays.js', 'library_web/workspace.js', 'library_web/page-layout.js', 'library_web/page-layout.css', 'library_web/overview-cards.js', 'library_web/presentation-sync.js', 'library_web/brand.svg',
         'library_web/navigation.js', 'library_web/studio.js', 'library_web/studio.css',
         'library_web/agents.js', 'library_web/agents.css', 'docs/AGENT_CONTRACT.md',
         'library_web/integrations.js', 'library_web/integrations.css', 'docs/INTEGRATION_GUIDE.md',
         'library_web/skills.js', 'library_web/skills.css', 'docs/LEFT_RIGHT_WORKFLOW.md',
         'docs/USER_GUIDE.md', 'artifacts/codex-blueprint-workflow.svg',
         'artifacts/codex-blueprint-workspace-redraw.png',
         'templates/skills/personal-video/SKILL.md', 'templates/skills/personal-video/README.md',
         'library_web/manifest.webmanifest', 'library_web/service-worker.js',
         'docs/V2.4.md', 'docs/STUDIO_API.md', 'library_web/connection.js', 'library_web/connection-guide.json',
         'integrations/mcp/tools.json', 'integrations/mcp/sites.mjs', 'integrations/mcp/connection.mjs',
         'integrations/sites/presentation.mjs', 'docs/PRESENTATION_SYNC.md',
         'docs/CONNECT.md', 'docs/AI_CONNECTION.md', 'docs/OPERATING_MODEL.md', 'docs/UPGRADE.md', 'docs/LAYOUT.md']


def main():
    parser = argparse.ArgumentParser(description='建立 V2 介面更新包；不包含私人資料或重新建立後台')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).expanduser().absolute()
    paths = FILES + sorted(path.relative_to(ROOT).as_posix() for path in (ROOT / 'assets/icons').glob('*.svg'))
    manifest = {'product': 'Blueprint Studio Open Source', 'version': __version__, 'database_schema': SCHEMA_VERSION,
                'database_migration': 'Native schema 7 adds integration registry, records, commands, events and history. This UI package cannot migrate backend data; use the full native source with maintenance backup and rehearsal.',
                'requires': 'V2.9 native managed backend for maintenance/2 coverage, operation and private-copy tools; legacy backup clients remain compatible. V2.8.1 native backend or authenticated Sites presentation adapter for account layout sync. V2.8 native backend for Integration Contract 1.0; existing Agent API remains blueprint-agent/1. UI-only upgrade is insufficient. Sites presentation reference adapter requires host integration; full Sites backend adaptation is not included.',
                'integration_contract': '1.0', 'integration_backend': 'native',
                'ai_connection_entrypoint': 'AI_CONNECT.md', 'mcp_adapter': 'Integrate with the existing verified platform identity and API; see docs/AI_CONNECTION.md.',
                'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in paths: archive.write(ROOT / name, name)
        archive.writestr('workbench-release.json', json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({'archive': str(output), 'version': __version__, 'files': len(paths)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
