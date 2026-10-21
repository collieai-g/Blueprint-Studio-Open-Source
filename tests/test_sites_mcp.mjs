import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createMcpHandler } from '../integrations/mcp/sites.mjs';
const { tools } = JSON.parse(await readFile(new URL('../integrations/mcp/tools.json', import.meta.url)));
const request = (message, authorized = true) => new Request('https://workspace.example/mcp', { method: 'POST',
  headers: { 'Content-Type': 'application/json', ...(authorized ? { 'Verified-Test-User': 'owner' } : {}) }, body: JSON.stringify(message) });
const authorize = req => { if (!req.headers.has('Verified-Test-User')) throw new Error('Denied'); };
const rpc = (name, args) => ({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'blueprint_' + name, arguments: args } });

test('platform authentication is required before tool discovery or mutation', async () => {
  let writes = 0;
  const handler = createMcpHandler({ tools, authorize, version: 'test', dispatch: () => { writes++; } });
  for (const message of [{ jsonrpc: '2.0', id: 1, method: 'tools/list' }, rpc('save_bookmark', { title: 'A', url: 'https://example.com' })]) {
    assert.equal((await handler(request(message, false), {})).status, 401);
  }
  assert.equal(writes, 0);
});

test('tool writes use the same authenticated API and preserve conflict responses', async () => {
  const seen = [];
  const handler = createMcpHandler({ tools, authorize, version: 'test', dispatch: async req => {
    seen.push({ path: new URL(req.url).pathname, user: req.headers.get('Verified-Test-User'), marker: req.headers.get('X-Blueprint-Request'), data: await req.json() });
    return new Response(JSON.stringify({ error: 'private database details' }), { status: 409 });
  } });
  const result = await (await handler(request(rpc('update_todo', { id: 'todo-1', expected_version: 3, done: true })), {})).json();
  assert.equal(result.result.isError, true);
  assert.equal(seen[0].path, '/api/todos/todo-1');
  assert.equal(seen[0].user, 'owner'); assert.equal(seen[0].marker, '1');
  assert.deepEqual(seen[0].data, { expected_version: 3, done: true });
  assert.equal(JSON.stringify(result).includes('private database details'), false);
});

test('text files preserve UTF-8 and writes cannot redirect to another backend', async () => {
  const seen = [];
  const handler = createMcpHandler({ tools, authorize, version: 'test', dispatch: async req => {
    seen.push({ url: req.url, data: await req.json() });
    return new Response(JSON.stringify({ item: { id: 'file-1', version: 1 } }));
  } });
  const result = await (await handler(request(rpc('save_text_file', { name: '中文.md', text: '真實成果' })), {})).json();
  assert.equal(result.result.structuredContent.item.id, 'file-1');
  assert.equal(seen[0].url, 'https://workspace.example/api/files');
  assert.equal(Buffer.from(seen[0].data.content_base64, 'base64').toString(), '真實成果');
  for (const message of [rpc('get_item', { id: '../secrets' }), rpc('save_text_file', { name: 'file', text: 'x', url: 'https://evil.example' })]) {
    assert.equal((await (await handler(request(message), {})).json()).result.isError, true);
  }
  assert.equal(seen.length, 1);
});

test('read-only connections cannot call hidden mutations; notifications never write', async () => {
  let writes = 0;
  const handler = createMcpHandler({ tools, authorize, readOnly: true, version: 'test', dispatch: () => { writes++; } });
  const list = await (await handler(request({ jsonrpc: '2.0', id: 1, method: 'tools/list' }), {})).json();
  assert.ok(list.result.tools.every(tool => tool.annotations.readOnlyHint && !tool.route));
  assert.equal((await (await handler(request(rpc('save_bookmark', { title: 'x', url: 'https://example.com' })), {})).json()).result.isError, true);
  const notification = rpc('save_bookmark', { title: 'x', url: 'https://example.com' }); delete notification.id;
  assert.equal((await handler(request(notification), {})).status, 202);
  assert.equal(writes, 0);
});
