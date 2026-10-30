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
async function request(path, method = 'GET', data, environment = env) {
  return gateway(new Request(site + path, { method,
    headers: { 'oai-authenticated-user-id': 'test-owner', Origin: site,
      ...(data === undefined ? {} : { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1' }) },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }) }), environment);
}
async function toolResult(name, args, environment = env) {
  const response = await request('/mcp', 'POST', { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'blueprint_' + name, arguments: args } }, environment);
  assert.equal(response.status, 200);
  const payload = await response.json();
  return payload.result;
}
async function tool(name, args) {
  const payload = { result: await toolResult(name, args) };
  assert.equal(payload.result.isError, false, JSON.stringify(payload));
  return payload.result.structuredContent;
}
const original = (await tool('agent_get', { id: config.agent_id })).agent;
if (config.mode === 'upgrade') {
  const beforeJob = (await tool('agent_job_get', { id: config.job_id })).job;
  const readonly = { ...env, BLUEPRINT_SITE_ACCOUNTS: JSON.stringify({
    'test-owner': { username: config.username, password: config.password, read_only: true }
  }) };
  const rejected = await toolResult('agent_update', config.upgrade, readonly);
  assert.equal(rejected.isError, true);
  assert.equal(JSON.parse(rejected.content[0].text).status, 403);
  for (const field of ['manifest_json', 'source_json', 'upgrade_key']) {
    const args = { ...config.upgrade }; delete args[field];
    const failed = await toolResult('agent_update', args);
    assert.equal(failed.isError, true);
    assert.equal(JSON.parse(failed.content[0].text).status, 400);
  }
  for (const upgrade_key of ['', '../different', 'x'.repeat(101)]) {
    const failed = await toolResult('agent_update', { ...config.upgrade, upgrade_key });
    assert.equal(failed.isError, true);
    assert.equal(JSON.parse(failed.content[0].text).status, 400);
  }
  const upgraded = (await tool('agent_update', config.upgrade)).agent;
  assert.equal(upgraded.id, original.id);
  assert.equal(upgraded.created_at, original.created_at);
  assert.equal(upgraded.enabled, original.enabled);
  assert.equal(upgraded.connection_state, original.connection_state);
  assert.equal(upgraded.version, original.version + 1);
  assert.deepEqual((await tool('agent_get', { id: original.id })).agent, upgraded);
  assert.deepEqual((await tool('agent_job_get', { id: config.job_id })).job, beforeJob);
  const current = (await tool('agent_update', { id: original.id, expected_version: upgraded.version,
    settings_json: JSON.stringify({ language: 'English', concise: false }) })).agent;
  assert.deepEqual((await tool('agent_update', config.upgrade)).agent, upgraded);
  assert.deepEqual((await tool('agent_update', { ...config.upgrade, expected_version: current.version })).agent, upgraded);
  assert.deepEqual((await tool('agent_get', { id: original.id })).agent, current);
  const conflicting = await toolResult('agent_update', { ...config.upgrade,
    settings_json: JSON.stringify({ concise: false }) });
  assert.equal(conflicting.isError, true);
  assert.equal(JSON.parse(conflicting.content[0].text).status, 409);
  const stale = await toolResult('agent_update', { ...config.upgrade, upgrade_key: 'sites-stale-upgrade' });
  assert.equal(stale.isError, true);
  assert.equal(JSON.parse(stale.content[0].text).status, 409);
  assert.equal((await tool('agents_list', {})).total, 1);
  assert.deepEqual((await tool('agent_job_get', { id: config.job_id })).job, beforeJob);
  console.log(JSON.stringify({ agent: current, upgraded }));
} else {
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
}
