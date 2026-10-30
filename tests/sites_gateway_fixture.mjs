// Isolated integration test: platform identity is a test fixture, never a login implementation.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import { request as httpRequest } from 'node:http';
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const config = JSON.parse(Buffer.concat(chunks).toString());
const site = 'https://gateway.example', backend = 'https://backend.example';
// Buffer only this loopback test transport. Undici can assert on an HTTP/1.0
// close while an unconsumed denial body is paused. Every API still reaches the
// real authenticated Python server; production gateway fetch is unchanged.
const loopbackFetch = (url, options = {}) => new Promise((resolve, reject) => {
  const outgoing = httpRequest(url, {method: options.method || 'GET',
    headers: Object.fromEntries(new Headers(options.headers)), signal: options.signal}, incoming => {
    const chunks = [];
    incoming.on('data', chunk => chunks.push(chunk));
    incoming.on('error', reject);
    incoming.on('end', () => {
      const status = incoming.statusCode;
      const headers = new Headers();
      for (const [name, value] of Object.entries(incoming.headers)) {
        if (Array.isArray(value)) for (const entry of value) headers.append(name, entry);
        else if (value !== undefined) headers.set(name, value);
      }
      resolve(new Response([204, 205, 304].includes(status) ? null : Buffer.concat(chunks), {status, headers}));
    });
  });
  outgoing.on('error', reject);
  outgoing.end(options.body);
});
// Production always requires HTTPS. Only this fixture maps the fixed test origin to loopback.
globalThis.fetch = (url, options) => {
  assert.equal(new URL(url).origin, backend);
  return loopbackFetch(config.local_backend + new URL(url).pathname + new URL(url).search, options);
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
assert.equal((await mcp('tools/list', {})).tools.length, 52);
await tool('calendar_meta');
const calendarArgs = { kind: 'todo', title: '跨入口月曆待辦', date: '2026-10-26', employee_id: '',
  request_key: 'gateway-calendar-synthetic-1' };
const calendarCreated = await tool('calendar_entry_create', calendarArgs);
assert.equal((await tool('calendar_entry_create', calendarArgs)).entry.id, calendarCreated.entry.id);
const calendarMonth = await tool('calendar_month', { month: '2026-10' });
assert.equal(calendarMonth.days.flatMap(day => day.todos).filter(entry => entry.id === calendarCreated.entry.id).length, 1);
const calendarSaved = await tool('calendar_entry_update', { id: calendarCreated.entry.id,
  expected_version: calendarCreated.entry.version, done: true });
assert.equal((await api('/api/studio/calendar/entries/' + calendarCreated.entry.id)).entry.version, calendarSaved.entry.version);
assert.equal((await tool('calendar_month', { month: '2026-10' })).days
  .flatMap(day => day.todos).find(entry => entry.id === calendarCreated.entry.id).done, true);
const saved = await tool('save_bookmark', { title: '跨入口資料', url: 'https://example.com/saved' });
assert.equal((await api('/api/items/' + saved.item.id)).item.title, '跨入口資料');
const uploaded = await api('/api/files', 'POST', { name: 'retain.txt', content_base64: Buffer.from('original attachment').toString('base64') });
assert.equal(await (await request('/api/files/' + uploaded.item.id)).text(), 'original attachment');
// Both entrypoints operate on one persisted Agent job; the text below is fixture output.
const manifest = { contract_version: 1, id: 'gateway-summary', name: '摘要測試 Agent',
  version: '1.0.0', adapter: 'connected_client', instructions: '只整理使用者指定的資料。',
  settings: [], operations: [{ id: 'summarize', name: '整理摘要', instructions: '保留來源。',
    input_fields: [] }], ui: { layout: 'standard', result_label: '摘要' } };
const { agent: registered } = await api('/api/studio/agents', 'POST', { manifest,
  source: { repository: 'https://github.com/example/summary', revision: 'a'.repeat(40) } });
const { agent: connected } = await tool('agent_action', { id: registered.id,
  expected_version: registered.version, action: 'connect', arguments_json: '{}' });
const refs = [{ kind: 'item', id: saved.item.id }];
const { job: queued } = await api('/api/studio/agent-jobs', 'POST', { agent_id: connected.id,
  operation_id: 'summarize', title: '雙入口摘要測試', inputs: {}, refs, source_key: 'gateway-summary-1' });
assert.equal(queued.state, 'awaiting_codex');
const context = await tool('agent_job_context', { id: queued.id });
const { job: started } = await tool('agent_job_action', { id: queued.id, expected_version: queued.version,
  action: 'start', arguments_json: JSON.stringify({ source: 'Gateway integration fixture', context_digest: context.context_digest }) });
const { job: completed } = await tool('agent_job_action', { id: queued.id, expected_version: started.version,
  action: 'complete', arguments_json: JSON.stringify({ result: '測試結果：已保存跨入口資料。',
    source: 'Gateway integration fixture', source_refs: refs, context_digest: context.context_digest }) });
assert.equal(completed.state, 'completed');
assert.equal((await api('/api/studio/agent-jobs/' + queued.id)).job.result, completed.result);
for (const asset of ['/agents.js', '/agents.css']) assert.equal((await request(asset)).status, 200);
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
  agent_id: connected.id, agent_job_id: completed.id,
  calendar_entry_id: calendarSaved.entry.id,
  workday_id: evidence.workday_id, page_sha256: pageSha,
  archive_sha256: createHash('sha256').update(bytes).digest('hex') }));
