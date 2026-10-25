"""Run separately with MCP SDK 2.3.0 to check both HTTP protocol generations."""
import asyncio
import json
from pathlib import Path
import subprocess

from mcp import Client


async def main():
    fixture = Path(__file__).with_name('mcp_http_fixture.mjs')
    process = subprocess.Popen(['node', str(fixture)], stdout=subprocess.PIPE, text=True)
    try:
        port = int(process.stdout.readline().strip())
        results = []
        for mode, expected in [('auto', '2026-07-28'), ('legacy', '2025-11-25')]:
            async with Client(f'http://127.0.0.1:{port}/mcp', mode=mode, read_timeout_seconds=10) as client:
                assert client.protocol_version == expected
                tools = await client.list_tools()
                assert len(tools.tools) == 42
                saved = await client.call_tool('blueprint_save_bookmark', {'title': mode, 'url': 'https://example.com/' + mode})
                assert not saved.is_error
                item = (saved.structured_content or json.loads(saved.content[0].text))['item']
                read = await client.call_tool('blueprint_get_item', {'id': item['id']})
                assert not read.is_error
                assert (read.structured_content or json.loads(read.content[0].text))['item'] == item
                results.append({'protocol': client.protocol_version, 'write_readback': 'passed'})
        print(json.dumps(results))
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()


if __name__ == '__main__': asyncio.run(main())
