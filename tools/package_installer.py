#!/usr/bin/env python3
"""Build a complete AI installer from tracked product source, excluding research."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_folders import __version__
from blueprint_library.store import SCHEMA_VERSION
from blueprint_folders.console import configure_utf8_streams
from tools.release_policy import selected_names, external_output, write_archive

EXCLUDED_PREFIXES = ('research/',)

MANAGED_INSTALLER_FILES = {'blueprint_library/installer.py', 'blueprint_library/installer_release.py',
    'blueprint_library/installer_catalog.py', 'blueprint_library/installer_components.py',
    'blueprint_library/installer_modules.py', 'blueprint_library/installer_backup.py',
    'blueprint_library/installer_preview.py', 'tools/dev_preview.py',
    'blueprint_library/installer_http.py', 'tools/studio_installer.py', 'tools/package_managed_release.py',
    'tools/package_release_catalog.py', 'docs/MANAGED_RELEASE_CHANNELS.md',
    'requirements-installer.txt', 'library_web/installer.js', 'library_web/installer.css',
    'docs/MANAGED_INSTALLER.md'}

REQUIRED = {'docs/INSTALL_UPDATE_FLOW.md', 'docs/LIFECYCLE_API.md', 'tools/lifecycle_plan.py', 'blueprint_library/operations.py', 'blueprint_library/lifecycle.py', 'docs/MAINTENANCE_API.md', 'blueprint_library/runtime.py', 'integrations/sites/native-gateway.mjs', 'tools/build_sites_gateway.py', 'docs/SITES_NATIVE_GATEWAY.md', 'docs/AGENT_ROADMAP.md', 'blueprint_library/maintenance.py', 'blueprint_library/backup.py', 'tools/check_installation.py', 'integrations/mcp/maintenance-tools.json', 'docs/NATIVE_LIFECYCLE.md', 'tools/source_checkpoint.py', 'docs/SOURCE_CHECKPOINT.md', 'UPDATE_AI.txt', 'docs/UPDATE_ASSISTANT.md', 'library_web/update.js', 'tools/update_assistant.py', 'INSTALL_AI.txt', 'AI_CONNECT.md', 'START_HERE.md', 'AGENTS.md', 'blueprint.py',
            'library_web/connection.js', 'library_web/connection-guide.json',
            'blueprint_library/connection.py', 'integrations/mcp/connection.mjs',
            'integrations/mcp/tools.json', 'docs/CONNECT.md', 'docs/UPGRADE.md',
            'requirements-mcp.txt', 'company/Dockerfile', 'library_web/navigation.js',
            'library_web/studio.js', 'library_web/studio.css', 'blueprint_library/studio.py',
            'blueprint_library/studio_schema.py', 'blueprint_library/studio_http.py', 'docs/V2.4.md',
            'blueprint_library/agents.py', 'blueprint_library/agents_http.py',
            'library_web/agents.js', 'library_web/agents.css', 'docs/AGENT_CONTRACT.md',
            'library_web/skills.js', 'library_web/skills.css', 'docs/LEFT_RIGHT_WORKFLOW.md',
            'docs/USER_GUIDE.md', 'artifacts/codex-blueprint-workflow.svg',
            'artifacts/codex-blueprint-workspace-redraw.png',
            'templates/skills/personal-video/SKILL.md', 'templates/skills/personal-video/README.md',
            'templates/agent/agent.json', 'docs/OPERATING_MODEL.md',
            'blueprint_library/integrations.py', 'blueprint_library/integrations_http.py',
            'library_web/integrations.js', 'library_web/integrations.css',
            'blueprint_library/calendar.py', 'blueprint_library/calendar_http.py', 'blueprint_library/calendar_sources.py',
            'library_web/calendar.js', 'library_web/calendar.css', 'docs/COMPANY_CALENDAR.md', 'integrations/mcp/calendar-tools.json',
            'library_web/page-layout.js', 'library_web/page-layout.css', 'library_web/overview-cards.js', 'library_web/presentation-sync.js',
            'contracts/integration-v1/openapi.json', 'contracts/integration-v1/README.md',
            'contracts/integration-v1/SHA256SUMS',
            'contracts/integration-v1/schemas/manifest.schema.json',
            'contracts/integration-v1/schemas/record-write.schema.json',
            'contracts/integration-v1/schemas/command-write.schema.json',
            'contracts/integration-v1/schemas/command-result.schema.json',
            'contracts/integration-v1/schemas/knowledge-payload.schema.json',
            'contracts/integration-v1/schemas/error.schema.json',
            'contracts/integration-v1/fixtures/manifest.json',
            'contracts/integration-v1/fixtures/record.json',
            'contracts/integration-v1/fixtures/command.json',
            'contracts/integration-v1/fixtures/command-results.json',
            'docs/INTEGRATION_GUIDE.md',
            'docs/PRESENTATION_SYNC.md', 'blueprint_library/presentation.py', 'integrations/sites/presentation.mjs',
            'examples/integration_service.py'}


def build(output):
    output = external_output(output, ROOT)
    names = selected_names(ROOT, 'installer', required=REQUIRED | MANAGED_INSTALLER_FILES)
    files = {name: (ROOT / name).read_bytes() for name in names}
    manifest = {'product': 'Blueprint Studio Open Source', 'version': __version__,
                'entrypoint': 'INSTALL_AI.txt', 'ai_connection_entrypoint': 'AI_CONNECT.md', 'database_schema': SCHEMA_VERSION, 'private_data_included': False,
                'integration_contract': '1.0', 'integration_backend': 'native',
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    write_archive(output, files, 'installer-manifest.json', manifest)
    return {'archive': str(output), 'version': __version__, 'files': len(files), 'entrypoint': 'INSTALL_AI.txt'}


def main():
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description='建立完整 AI 引導安裝包，包含來源、左側 AI 接駁入口、規則及驗收')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output).expanduser().absolute()), ensure_ascii=False))


if __name__ == '__main__':
    main()
