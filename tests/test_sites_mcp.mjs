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
test('guided acceptance uses existing API, resumes partial writes and keeps client identities separate', async () => {
  const records = new Map(), calls = [];
  let next = 1, failRead = true;
  const dispatch = async req => {
    assert.equal(req.headers.get('Verified-Test-User'), 'owner');
    const u = new URL(req.url), data = req.method === 'GET' ? null : await req.json();
    calls.push([req.method, u.pathname]);
    if (u.pathname === '/api/workdays' && req.method === 'GET') return Response.json({ workdays: [...records.values()].filter(r => r.source_key.includes(u.searchParams.get('q'))) });
    if (u.pathname === '/api/workdays' && req.method === 'POST') {
      const id = 'report-' + next++;
      const report = { ...data, id, version: 1, updated_at: '2026-10-21T12:00:00Z',
        entries: data.entries.map((e, i) => ({ ...e, id: id + '-e' + i, version: 1, done: false, url: '', tags: [], due_date: null })) };
      records.set(id, report); return Response.json({ workday: report }, { status: 201 });
    }
    if (u.pathname.startsWith('/api/todos/')) {
      const id = u.pathname.split('/').at(-1);
      const report = [...records.values()].find(r => r.entries.some(e => e.id === id));
      const todo = report.entries.find(e => e.id === id);
      assert.equal(data.expected_version, todo.version);
      todo.done = data.done; todo.version++; report.version++;
      return Response.json({ todo });
    }
    const report = records.get(u.pathname.split('/').at(-1));
    if (report && req.method === 'GET') {
      if (failRead) { failRead = false; return Response.json({ error: 'temporary read failure' }, { status: 503 }); }
      return Response.json({ workday: report });
    }
    if (report && req.method === 'PATCH') {
      assert.equal(data.expected_version, report.version);
      assert.deepEqual(data.entries.map(e => e.id), report.entries.map(e => e.id));
      assert.equal(report.entries[1].done, true);
      report.summary = data.summary; report.version++;
      return Response.json({ workday: report });
    }
    return Response.json({ error: 'missing' }, { status: 404 });
  };
  const handler = createMcpHandler({ tools, authorize, version: '2.3.0', dispatch });
  const args = { challenge: 'd'.repeat(32), client: 'Codex', date: '2026-10-21' };
  const call = async value => (await (await handler(request(rpc('verify_connection', value)), {})).json()).result;
  const first = await call(args);
  assert.equal(first.isError, true);
  assert.equal(records.size, 1); // First save succeeded, read failed; no completion claim.
  assert.equal([...records.values()][0].summary, 'blueprint-connection/1:pending');
  const resumed = await call(args);
  assert.equal(resumed.isError, false);
  assert.equal(resumed.structuredContent.verified, true);
  assert.equal(records.size, 1);
  const again = await call(args);
  assert.equal(again.structuredContent.workday_id, resumed.structuredContent.workday_id);
  assert.equal(records.size, 1);
  assert.equal((await call({ ...args, client: 'Claude Code' })).isError, false);
  assert.equal(records.size, 2);
  const before = calls.length;
  assert.equal((await call({ ...args, date: '2026-02-30' })).isError, true);
  assert.equal(calls.length, before);
  records.get(resumed.structuredContent.workday_id).summary = 'User edited';
  assert.equal((await call(args)).isError, true);
  assert.equal(records.get(resumed.structuredContent.workday_id).summary, 'User edited');
  const readonly = createMcpHandler({ tools, authorize, readOnly: true, version: '2.3.0', dispatch });
  assert.equal((await (await readonly(request(rpc('verify_connection', args)), {})).json()).result.isError, true);
  assert.equal(records.size, 2);
});
