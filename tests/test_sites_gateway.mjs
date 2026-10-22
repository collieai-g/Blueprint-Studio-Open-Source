import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createNativeGateway } from '../integrations/sites/native-gateway.mjs';
const { tools } = JSON.parse(await readFile(new URL('../integrations/mcp/tools.json', import.meta.url)));
const { tools: maintenanceTools } = JSON.parse(await readFile(new URL('../integrations/mcp/maintenance-tools.json', import.meta.url)));
const env = {
  BLUEPRINT_SITE_ORIGIN: 'https://desk.example', BLUEPRINT_BACKEND_ORIGIN: 'https://data.example',
  BLUEPRINT_SITE_ACCOUNTS: JSON.stringify({ alice: { username: 'owner', password: 'test-owner-secret-only' },
    bob: { username: 'member', password: 'test-member-secret-only' },
    reader: { username: 'member', password: 'test-member-secret-only', read_only: true } })
};
const contract = { api_contract: 'blueprint-native/1', product_version: '2.6.0',
  storage: 'native', database_schema: 5, maintenance_available: true };
const req = (path, { user = 'alice', method = 'GET', body, headers = {} } = {}) => new Request(env.BLUEPRINT_SITE_ORIGIN + path, {
  method, headers: { ...(user ? { 'oai-authenticated-user-id': user } : {}),
    ...(body === undefined ? {} : { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1' }), ...headers },
  ...(body === undefined ? {} : { body: typeof body === 'string' ? body : JSON.stringify(body) })
});
const rpc = (name, arguments_ = {}) => ({ jsonrpc: '2.0', id: 1, method: 'tools/call',
  params: { name: 'blueprint_' + name, arguments: arguments_ } });
function fixture(respond = () => Response.json({ ok: true }), capabilities = contract) {
  const calls = [];
  const gateway = createNativeGateway({ tools, maintenanceTools, fetchImpl: async (url, options) => {
    calls.push({ url, ...options });
    return new URL(url).pathname === '/api/runtime' ? Response.json(capabilities) : respond(url, options);
  } });
  return { calls, gateway, handle: (request, config = env) => gateway(request, config) };
}

test('unmapped, anonymous and cross-origin users cannot reach data or discover tools', async () => {
  const f = fixture();
  for (const path of ['/', '/api/info', '/mcp']) {
    for (const [user, status] of [[null, 401], ['unknown', 403]]) {
      const request = req(path, { user, ...(path === '/mcp' ? { method: 'POST', body: { jsonrpc: '2.0', id: 1, method: 'tools/list' } } : {}) });
      assert.equal((await f.handle(request)).status, status);
    }
  }
  assert.equal((await f.handle(req('/api/info', { headers: { Origin: 'https://other.example' } }))).status, 403);
  assert.equal((await f.handle(req('/api/info', { headers: { 'Sec-Fetch-Site': 'cross-site' } }))).status, 403);
  assert.equal(f.calls.length, 0);
  const own = await (await f.handle(req('/_blueprint/identity', { user: 'new-user' }), {})).json();
  assert.ok(own.error); // A mismatched or unconfigured origin never supplies an identity.
  assert.equal((await (await f.handle(req('/_blueprint/identity', { user: 'new-user' }))).json()).site_user_id, 'new-user');
  assert.equal(f.calls.length, 0);
});

test('browser and MCP choose the same mapped account without forwarding caller credentials', async () => {
  const f = fixture(() => Response.json({ item: { id: 'item-1' } }));
  await f.handle(req('/api/items/item-1', { user: 'bob', headers: { Authorization: 'Basic attacker', Cookie: 'private-cookie', 'X-Forwarded-Host': 'evil.example' } }));
  const answer = await (await f.handle(req('/mcp', { user: 'bob', method: 'POST', body: rpc('get_item', { id: 'item-1' }) }))).json();
  assert.equal(answer.result.isError, false);
  for (const call of f.calls) {
    assert.equal(call.redirect, 'manual');
    assert.equal(Buffer.from(call.headers.Authorization.slice(6), 'base64').toString(), 'member:test-member-secret-only');
    for (const key of ['Cookie', 'oai-authenticated-user-id', 'Origin', 'X-Forwarded-Host']) assert.equal(call.headers[key], undefined);
    assert.ok(call.url.startsWith('https://data.example/'));
  }
  const write = await f.handle(req('/api/items', { method: 'POST', body: { title: '實際內容' }, headers: { Origin: env.BLUEPRINT_SITE_ORIGIN } }));
  assert.equal(write.status, 200);
  assert.equal(f.calls.at(-1).headers['X-Blueprint-Request'], '1');
  assert.equal(JSON.parse(new TextDecoder().decode(f.calls.at(-1).body)).title, '實際內容');
  const unsafe = req('/api/items', { method: 'POST', body: '{}' }); unsafe.headers.delete('X-Blueprint-Request');
  const before = f.calls.length;
  assert.equal((await f.handle(unsafe)).status, 403);
  assert.equal(f.calls.length, before);
});

test('unsupported backend or missing maintenance blocks mutations before they happen', async () => {
  for (const changes of [{ api_contract: 'unknown/2' }, { storage: 'sites' }, { maintenance_available: false }]) {
    const f = fixture(undefined, { ...contract, ...changes });
    assert.equal((await f.handle(req('/api/items', { method: 'POST', body: {} }))).status, 503);
    assert.deepEqual(f.calls.map(c => new URL(c.url).pathname), ['/api/runtime']);
  }
  const f = fixture();
  for (const bad of ['http://data.example', 'https://user:secret@data.example', 'https://data.example/path', env.BLUEPRINT_SITE_ORIGIN]) {
    assert.equal((await f.handle(req('/api/info'), { ...env, BLUEPRINT_BACKEND_ORIGIN: bad })).status, 503);
  }
  assert.equal(f.calls.length, 0);
  const compatible = fixture(undefined, { ...contract, product_version: '3.0.0', database_schema: 6 });
  assert.equal((await compatible.handle(req('/api/info'))).status, 200);
  // Storage migrations belong to the native service; a compatible API needs no gateway redeploy.
});

test('backend errors and redirects never leak credentials or replay a write', async () => {
  for (const response of [() => new Response('secret', { status: 307, headers: { Location: 'https://evil.example' } }),
    () => new Response('password details', { status: 401, headers: { 'WWW-Authenticate': 'Basic private' } }),
    () => { throw new Error('sensitive-debug'); }]) {
    const f = fixture(response);
    const result = await f.handle(req('/api/items', { method: 'POST', body: { title: 'only-once' } }));
    assert.equal(result.status, 502);
    assert.equal(result.headers.get('WWW-Authenticate'), null);
    const text = await result.text();
    for (const secret of ['sensitive-debug', 'password details', 'evil.example', 'test-owner-secret-only']) assert.ok(!text.includes(secret));
    assert.equal(f.calls.filter(c => c.method === 'POST').length, 1);
  }
});

test('read-only mapping blocks both browser writes and MCP maintenance calls', async () => {
  const f = fixture();
  assert.equal((await f.handle(req('/api/maintenance/backup', { user: 'reader', method: 'POST', body: {} }))).status, 403);
  const list = await (await f.handle(req('/mcp', { user: 'reader', method: 'POST', body: { jsonrpc: '2.0', id: 1, method: 'tools/list' } }))).json();
  assert.ok(list.result.tools.every(tool => tool.annotations.readOnlyHint));
  const write = await (await f.handle(req('/mcp', { user: 'reader', method: 'POST', body: rpc('backup_create') }))).json();
  assert.equal(write.result.isError, true);
  assert.ok(f.calls.every(c => c.method === 'GET'));
  const member = fixture(() => Response.json({ error: 'forbidden' }, { status: 403 }));
  assert.equal((await member.handle(req('/api/maintenance', { user: 'bob' }))).status, 403);
});

test('original assets and attachment streams pass through; runtime facts stay separate', async () => {
  const bytes = new Uint8Array([0, 1, 127, 255]);
  const f = fixture(url => new URL(url).pathname === '/api/info'
    ? Response.json({ company_name: 'Test', version: '9.1.0', connection_mode: 'native', maintenance: { supported: true } })
    : new Response(bytes, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment', 'Set-Cookie': 'never=forward', 'Content-Security-Policy': "default-src 'self'" } }));
  for (const path of ['/app.js', '/api/files/test-file', '/api/maintenance/backups/test']) {
    const response = await f.handle(req(path));
    assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
    assert.equal(response.headers.get('Set-Cookie'), null);
    assert.equal(response.headers.get('Content-Disposition'), 'attachment');
  }
  const info = await (await f.handle(req('/api/info'))).json();
  assert.equal(info.version, '9.1.0');
  assert.equal(info.gateway_version, '1.0.0');
  assert.equal(info.connection_mode, 'sites');
  assert.equal(info.deployment_mode, 'sites_native');
  const status = await (await f.handle(req('/_blueprint/status'))).json();
  assert.equal(status.chat_tools_verified, false);
  assert.equal(status.backend_origin, env.BLUEPRINT_BACKEND_ORIGIN);
});

test('bounded bodies and route checks reject oversized or unsupported requests', async () => {
  const f = fixture();
  assert.equal((await f.handle(req('/api/items', { method: 'DELETE' }))).status, 405);
  assert.equal((await f.handle(req('/unrelated'))).status, 404);
  assert.equal(f.calls.length, 0);
  const large = req('/mcp', { method: 'POST', body: 'x'.repeat(512 * 1024 + 1) });
  assert.equal((await f.handle(large)).status, 413);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 0);
});
