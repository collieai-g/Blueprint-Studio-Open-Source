// Isolated integration test: platform identity is a test fixture, never a login implementation.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const config = JSON.parse(Buffer.concat(chunks).toString());
const site = 'https://gateway.example', backend = 'https://backend.example';
const realFetch = globalThis.fetch;
// Production always requires HTTPS. Only this fixture maps the fixed test origin to loopback.
globalThis.fetch = (url, options) => {
  assert.equal(new URL(url).origin, backend);
  return realFetch(config.local_backend + new URL(url).pathname + new URL(url).search, options);
};
const { default: worker } = await import(pathToFileURL(config.worker));
const env = { BLUEPRINT_SITE_ORIGIN: site, BLUEPRINT_BACKEND_ORIGIN: backend,
  BLUEPRINT_SITE_ACCOUNTS: JSON.stringify(config.accounts) };
const request = (path, method = 'GET', data, user = 'owner-id') => worker.fetch(new Request(site + path, {
  method, headers: { 'oai-authenticated-user-id': user, Origin: site,
    ...(data === undefined ? {} : { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1' }) },
  ...(data === undefined ? {} : { body: JSON.stringify(data) })
}), env);
const api = async (path, method, data, user) => {
  const response = await request(path, method, data, user);
  assert.ok(response.ok, 'API failed: ' + response.status + ' ' + path);
  return response.json();
};
const mcp = async (method, params, user) => {
  const response = await api('/mcp', 'POST', { jsonrpc: '2.0', id: 1, method, params }, user);
  assert.equal(response.error, undefined);
  return response.result;
};
const tool = async (name, args = {}) => {
  const result = await mcp('tools/call', { name: 'blueprint_' + name, arguments: args });
  assert.equal(result.isError, false, JSON.stringify(result));
  return result.structuredContent;
};
const info = await api('/api/info');
assert.equal(info.deployment_mode, 'sites_native');
assert.equal(info.maintenance.supported, true);
const page = await request('/');
const pageSha = createHash('sha256').update(Buffer.from(await page.arrayBuffer())).digest('hex');
assert.equal((await mcp('tools/list', {})).tools.length, 32);
const saved = await tool('save_bookmark', { title: '跨入口資料', url: 'https://example.com/saved' });
assert.equal((await api('/api/items/' + saved.item.id)).item.title, '跨入口資料');
const uploaded = await api('/api/files', 'POST', { name: 'retain.txt', content_base64: Buffer.from('original attachment').toString('base64') });
assert.equal(await (await request('/api/files/' + uploaded.item.id)).text(), 'original attachment');
const evidence = await tool('verify_connection', { challenge: 'f'.repeat(32), client: 'Gateway integration test', date: '2026-10-22' });
assert.equal((await api('/api/workdays/' + evidence.workday_id)).workday.entries[1].done, true);
assert.equal((await request('/api/maintenance', 'GET', undefined, 'member-id')).status, 403);
const denied = await mcp('tools/call', { name: 'blueprint_backup_create', arguments: {} }, 'member-id');
assert.equal(denied.isError, true);
const previous = (await api('/api/maintenance')).last_backup.id;
await tool('backup_create');
let status;
for (let attempt = 0; attempt < 150; attempt++) {
  status = await api('/api/maintenance');
  if (!status.busy && status.last_backup.id !== previous) break;
  await new Promise(resolve => setTimeout(resolve, 25));
}
assert.notEqual(status.last_backup.id, previous);
assert.equal(status.last_backup.restore_verified, true);
const response = await request('/api/maintenance/backups/' + status.last_backup.id);
assert.equal(response.status, 200);
const bytes = Buffer.from(await response.arrayBuffer());
await writeFile(config.archive, bytes, { flag: 'wx', mode: 0o600 });
console.log(JSON.stringify({ item_id: saved.item.id, file_id: uploaded.item.id,
  workday_id: evidence.workday_id, page_sha256: pageSha,
  archive_sha256: createHash('sha256').update(bytes).digest('hex') }));
