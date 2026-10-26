// Isolated gateway integration fixture. These identities and outputs are test data.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createNativeGateway } from '../integrations/sites/native-gateway.mjs';

const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const config = JSON.parse(Buffer.concat(chunks).toString());
const { tools } = JSON.parse(await readFile(new URL('../integrations/mcp/tools.json', import.meta.url)));
const { tools: maintenanceTools } = JSON.parse(await readFile(new URL('../integrations/mcp/maintenance-tools.json', import.meta.url)));
const { tools: calendarTools } = JSON.parse(await readFile(new URL('../integrations/mcp/calendar-tools.json', import.meta.url)));
const site = 'https://agent-workspace.example', backend = 'https://agent-backend.example';
const gateway = createNativeGateway({ tools, maintenanceTools, calendarTools, fetchImpl: (url, options) => {
  assert.equal(new URL(url).origin, backend);
  return fetch(config.backend + new URL(url).pathname + new URL(url).search, options);
} });
const env = { BLUEPRINT_SITE_ORIGIN: site, BLUEPRINT_BACKEND_ORIGIN: backend,
  BLUEPRINT_SITE_ACCOUNTS: JSON.stringify({ 'test-owner': { username: config.username, password: config.password } }) };
async function request(path, method = 'GET', data) {
  return gateway(new Request(site + path, { method,
    headers: { 'oai-authenticated-user-id': 'test-owner', Origin: site,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1' }) },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }) }), env);
}
async function tool(name, args) {
  const response = await request('/mcp', 'POST', { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'blueprint_' + name, arguments: args } });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.result.isError, false, JSON.stringify(payload));
  return payload.result.structuredContent;
}
const original = (await tool('agent_get', { id: config.agent_id })).agent;
const updated = (await tool('agent_update', { id: original.id, expected_version: original.version,
  settings_json: JSON.stringify({ language: 'English' }) })).agent;
assert.equal(updated.settings.language, 'English');
const context = await tool('agent_job_context', { id: config.job_id });
assert.equal(context.agent.settings.language, '繁體中文'); // Queued work pins its settings.
assert.equal(context.context_digest, config.context_digest);
const saved = await tool('agent_job_action', { id: config.job_id, expected_version: config.version,
  action: 'complete', arguments_json: JSON.stringify({ result: config.result, source: config.source,
    source_refs: config.refs, context_digest: context.context_digest }) });
const visible = await (await request('/api/studio/agent-jobs/' + config.job_id)).json();
assert.equal(visible.job.state, 'completed');
assert.equal(visible.job.result, config.result);
assert.equal(visible.job.version, saved.job.version);
assert.equal((await request('/api/studio/agents/' + original.id, 'PATCH', {
  expected_version: original.version, settings: { language: 'stale' }
})).status, 409);
console.log(JSON.stringify({ job: visible.job, agent: updated }));
