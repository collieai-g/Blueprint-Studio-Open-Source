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
from blueprint_folders.console import configure_utf8_streams
from tools.release_policy import selected_names, external_output, write_archive
FILES = ['docs/INSTALL_UPDATE_FLOW.md', 'docs/LIFECYCLE_API.md', 'docs/MAINTENANCE_API.md', 'AI_CONNECT.md', 'UPDATE_AI.txt', 'docs/UPDATE_ASSISTANT.md', 'library_web/update.js', 'library_web/index.html', 'library_web/style.css', 'library_web/app.js',
         'library_web/installer.js', 'library_web/installer.css', 'docs/MANAGED_INSTALLER.md',
         'library_web/workdays.js', 'library_web/workspace.js', 'library_web/page-layout.js', 'library_web/page-layout.css', 'library_web/overview-cards.js', 'library_web/presentation-sync.js', 'library_web/brand.svg',
         'library_web/navigation.js', 'library_web/studio.js', 'library_web/studio.css',
         'library_web/agents.js', 'library_web/agents.css', 'docs/AGENT_CONTRACT.md',
         'library_web/integrations.js', 'library_web/integrations.css', 'docs/INTEGRATION_GUIDE.md',
         'library_web/calendar.js', 'library_web/calendar.css', 'docs/COMPANY_CALENDAR.md',
         'library_web/skills.js', 'library_web/skills.css', 'docs/LEFT_RIGHT_WORKFLOW.md',
         'docs/USER_GUIDE.md', 'artifacts/codex-blueprint-workflow.svg',
         'artifacts/codex-blueprint-workspace-redraw.png',
         'templates/skills/personal-video/SKILL.md', 'templates/skills/personal-video/README.md',
         'library_web/manifest.webmanifest', 'library_web/service-worker.js',
         'docs/V2.4.md', 'docs/STUDIO_API.md', 'library_web/connection.js', 'library_web/connection-guide.json',
         'integrations/mcp/tools.json', 'integrations/mcp/sites.mjs', 'integrations/mcp/connection.mjs',
         'integrations/sites/presentation.mjs', 'docs/PRESENTATION_SYNC.md',
         'docs/CONNECT.md', 'docs/AI_CONNECTION.md', 'docs/OPERATING_MODEL.md', 'docs/UPGRADE.md', 'docs/LAYOUT.md']


def build(output):
    output = external_output(output, ROOT)
    paths = selected_names(ROOT, 'workbench', required=FILES)
    files = {name: (ROOT / name).read_bytes() for name in paths}
    manifest = {'product': 'Blueprint Studio Open Source', 'version': __version__, 'database_schema': SCHEMA_VERSION,
                'database_migration': 'Native schema 8 adds company calendar employees, attendance, work logs, todos and audit history; schema 7 integration data is retained. This UI package cannot migrate backend data; use the full native source with maintenance backup and rehearsal.',
                'requires': 'Calendar requires native schema 8 and blueprint-calendar/1; pure Sites needs its own compatible adapter. V2.9 native managed backend for maintenance/2 coverage, operation and private-copy tools; legacy backup clients remain compatible. V2.8.1 native backend or authenticated Sites presentation adapter for account layout sync. V2.8 native backend for Integration Contract 1.0; existing Agent API remains blueprint-agent/1. UI-only upgrade is insufficient. Sites presentation reference adapter requires host integration; full Sites backend adaptation is not included.',
                'integration_contract': '1.0', 'integration_backend': 'native',
                'ai_connection_entrypoint': 'AI_CONNECT.md', 'mcp_adapter': 'Integrate with the existing verified platform identity and API; see docs/AI_CONNECTION.md.',
                'files': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}}
    write_archive(output, files, 'workbench-release.json', manifest)
    return {'archive': str(output), 'version': __version__, 'files': len(paths)}


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='建立 V2 介面更新包；不包含私人資料或重新建立後台')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
