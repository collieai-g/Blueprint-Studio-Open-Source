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

EXCLUDED_PREFIXES = ('research/',)

REQUIRED = {'blueprint_library/runtime.py', 'integrations/sites/native-gateway.mjs', 'tools/build_sites_gateway.py', 'docs/SITES_NATIVE_GATEWAY.md', 'docs/AGENT_ROADMAP.md', 'blueprint_library/maintenance.py', 'blueprint_library/backup.py', 'tools/check_installation.py', 'integrations/mcp/maintenance-tools.json', 'docs/NATIVE_LIFECYCLE.md', 'tools/source_checkpoint.py', 'docs/SOURCE_CHECKPOINT.md', 'UPDATE_AI.txt', 'docs/UPDATE_ASSISTANT.md', 'library_web/update.js', 'tools/update_assistant.py', 'INSTALL_AI.txt', 'AI_CONNECT.md', 'START_HERE.md', 'AGENTS.md', 'blueprint.py',
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
            'templates/agent/agent.json', 'docs/OPERATING_MODEL.md'}


def build(output):
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    names = sorted(name for name in names if name and not name.startswith(EXCLUDED_PREFIXES))
    if not REQUIRED.issubset(names):
        raise ValueError('安裝來源不完整；先將正式公開來源加入 Git，不能產生部分安裝包。')
    files = {}
    for name in names:
        path = ROOT / name
        if (path.is_symlink() or not path.is_file() or path.resolve().is_relative_to(ROOT) is False
                or any(part.startswith('.env') and part != '.env.example' for part in path.relative_to(ROOT).parts)
                or path.name == 'access.json' or path.suffix in {'.zip', '.sqlite3', '.pyc'}):
            raise ValueError('安裝包包含不允許的來源檔案。')
        files[name] = path.read_bytes()
    manifest = {'product': 'Blueprint Studio Open Source', 'version': __version__,
                'entrypoint': 'INSTALL_AI.txt', 'ai_connection_entrypoint': 'AI_CONNECT.md', 'database_schema': SCHEMA_VERSION, 'private_data_included': False,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr('installer-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    return {'archive': str(output), 'version': __version__, 'files': len(files), 'entrypoint': 'INSTALL_AI.txt'}


def main():
    parser = argparse.ArgumentParser(description='建立完整 AI 引導安裝包，包含來源、左側 AI 接駁入口、規則及驗收')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output).expanduser().absolute()), ensure_ascii=False))


if __name__ == '__main__':
    main()
