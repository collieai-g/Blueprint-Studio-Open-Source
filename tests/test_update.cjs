const test = require('node:test');
const assert = require('node:assert/strict');
const { handoff, restore, validate, prompt } = require('../library_web/update.js');
function storage(initial = {}) {
  const data = { ...initial };
  return { data, getItem: key => data[key] ?? null, setItem: (key, value) => { data[key] = value; }, removeItem: key => { delete data[key]; } };
}
const site = 'https://studio.example';
test('handoff selects layout only and never exports credentials, connection challenges or company data', () => {
  const store = storage({ 'blueprint-workspace-v2': '{"pages":2}', 'blueprint-workspace-v2.7.3-backup': '{"layoutVersion":3,"cards":{}}', token: 'secret', 'blueprint-ai-connection-v1': '{"challenge":"private"}' });
  const data = handoff(site, { version: '2.4.0', company_name: 'Private company' }, store, 'sites');
  const text = JSON.stringify(data);
  for (const absent of ['secret', 'challenge', 'Private company']) assert.ok(!text.includes(absent));
  assert.equal(data.preferences['blueprint-workspace-v2'], '{"pages":2}');
  assert.equal(data.preferences['blueprint-workspace-v2.7.3-backup'], '{"layoutVersion":3,"cards":{}}');
  assert.ok(prompt(data).includes(site));
  assert.ok(prompt(data).includes('UPDATE_AI.txt'));
  assert.ok(prompt(data).includes('不要叫我重複加入'));
});
test('restore only accepts the original origin and known preference keys', () => {
  const original = storage({ 'blueprint-workspace-v2': '{"pages":2}', other: 'keep' });
  const data = handoff(site, {}, original, 'native');
  const destination = storage({ 'blueprint-workspace-v2': '{"pages":3}', other: 'keep' });
  assert.throws(() => restore(data, 'https://other.example', destination));
  assert.throws(() => validate({ ...data, preferences: { token: 'secret' } }, site));
  restore(data, site, destination);
  assert.equal(destination.data['blueprint-workspace-v2'], '{"pages":2}');
  assert.equal(destination.data.other, 'keep');
  const backup = Object.entries(destination.data).find(([key]) => key.includes(':before-restore:'))[1];
  assert.equal(JSON.parse(backup).preferences['blueprint-workspace-v2'], '{"pages":3}');
});
test('invalid URL and broken stored JSON do not produce a misleading handoff', () => {
  assert.throws(() => handoff('https://user:password@studio.example', {}, storage(), 'sites'));
  assert.throws(() => handoff(site, {}, storage({ 'blueprint-workspace-v2': 'broken' }), 'native'));
});
test('Sites gateway handoff preserves the real data backend without copying private server fields', () => {
  const data = handoff(site, { deployment_mode: 'sites_native', backend_origin: 'https://data.example',
    gateway_version: '1.0.0', version: '2.6.0', password: 'not-exported' }, storage(), 'sites');
  assert.equal(data.backend_origin, 'https://data.example');
  assert.ok(prompt(data).includes('SITES_NATIVE_GATEWAY.md'));
  assert.ok(prompt(data).includes('資料後台：https://data.example'));
  assert.ok(!JSON.stringify(data).includes('not-exported'));
  assert.throws(() => validate({ ...data, backend_origin: site }, site));
  assert.throws(() => validate({ ...data, backend_origin: 'https://user:secret@data.example' }, site));
  assert.throws(() => validate({ ...data, route: 'native' }, site));
});
