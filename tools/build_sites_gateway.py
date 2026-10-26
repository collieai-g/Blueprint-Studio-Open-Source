#!/usr/bin/env python3
"""Build one dependency-free Worker for the official Sites Worker ESM starter.

Only Blueprint source and public tool schemas enter the result. No Site ID,
credentials, company data or platform settings are included.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.update_assistant import path, outside


def build(output):
    output = path(output)
    outside(output)
    sources = []
    for name, remove in [
        ('integrations/mcp/connection.mjs', None),
        ('integrations/mcp/sites.mjs', "import { verifyConnection } from './connection.mjs';\n"),
        ('integrations/sites/native-gateway.mjs', "import { createMcpHandler } from '../mcp/sites.mjs';\n"),
    ]:
        content = (ROOT / name).read_text(encoding='utf-8')
        if remove:
            if content.count(remove) != 1:
                raise ValueError('接駁來源結構已改變；請先更新建置工具。')
            content = content.replace(remove, '')
        sources.append(content)
    # Each module keeps its own lexical scope. No general-purpose source rewriting.
    entry = (
        '// Generated from Blueprint source; private configuration comes from Sites secrets.\n'
        'const verifyConnection = (() => {\n' + sources[0].replace('export ', '') + '\nreturn verifyConnection; })();\n'
        'const createMcpHandler = (() => {\n' + sources[1].replace('export ', '') + '\nreturn createMcpHandler; })();\n'
        'const createNativeGateway = (() => {\n' + sources[2].replace('export ', '') + '\nreturn createNativeGateway; })();\n'
        'const tools = ' + json.dumps(json.loads((ROOT / 'integrations/mcp/tools.json').read_text())['tools'], ensure_ascii=False) + ';\n'
        'const maintenanceTools = ' + json.dumps(json.loads((ROOT / 'integrations/mcp/maintenance-tools.json').read_text())['tools'], ensure_ascii=False) + ';\n'
        'const calendarTools = ' + json.dumps(json.loads((ROOT / 'integrations/mcp/calendar-tools.json').read_text())['tools'], ensure_ascii=False) + ';\n'
        'export default { fetch: createNativeGateway({ tools, maintenanceTools, calendarTools }) };\n'
    )
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with output.open('x', encoding='utf-8') as stream:
        stream.write(entry)
    return {'file': str(output), 'sha256': hashlib.sha256(entry.encode()).hexdigest(),
            'deployment': 'not_performed', 'requires': 'Sites verified identity, native backend and private account mapping'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='建立 Sites 至原生後台接駁程式；不部署或移轉舊資料')
    parser.add_argument('--output', required=True)
    print(json.dumps(build(parser.parse_args().output), ensure_ascii=False))
