const test = require('node:test');
const assert = require('node:assert/strict');
const { validSession, verified, matches, prompt, init } = require('../library_web/connection.js');
const guide = require('../library_web/connection-guide.json');
const session = { challenge: 'a'.repeat(32), client: 'Codex', date: '2026-10-21' };
const report = { id: 'test-id', date: session.date, source: session.client, source_key: 'blueprint-connect:' + session.challenge,
  title: 'AI 接駁測試', summary: 'blueprint-connection/1:verified', entries: [
    { type: 'outcome', title: 'AI 接駁測試', content: session.challenge },
    { type: 'todo', title: '接駁測試：待辦寫入及更新', content: session.challenge, done: true }
  ] };
test('receipt validation requires matching persistent evidence and a completed todo', () => {
  assert.equal(verified(report, session), true);
  for (const change of [{ source: 'Claude' }, { date: '2026-10-22' }, { source_key: 'another' },
    { summary: 'pending' }, { title: 'User work' }, { entries: [] },
    { entries: [report.entries[0], { ...report.entries[1], done: false }] },
    { entries: [{ ...report.entries[0], content: 'wrong' }, report.entries[1]] }]) {
    assert.equal(verified({ ...report, ...change }, session), false);
  }
  assert.equal(verified(null, session), false);
  assert.equal(matches(report, { ...session, challenge: 'b'.repeat(32) }), false);
});
test('client-led native and Sites handoffs retain origin, attempt and distinct authentication', () => {
  for (const clientKey of Object.keys(guide.clients)) for (const route of ['native','sites']) {
    const text = prompt({ origin: 'https://own.example', route, clientKey, session: { ...session, client: guide.clients[clientKey].name }, guide });
    assert.ok(text.includes('https://own.example'));
    assert.ok(text.includes('blueprint_verify_connection'));
    assert.ok(text.includes(session.challenge));
    assert.ok(text.includes(route === 'sites' ? 'oauth_resource' : 'BLUEPRINT_BACKEND_PASSWORD'));
    assert.ok(text.includes('不重建或重設資料庫'));
    assert.ok(text.includes('保持代理與 TLS'));
    assert.ok(text.includes(guide.rules[0]));
    assert.ok(text.includes('AI_CONNECT.md'));
    assert.ok(text.includes('blueprint_get_workday'));
    assert.ok(text.includes('blueprint_list_todos'));
    assert.ok(text.includes('本身不構成新增測試授權'));
    assert.ok(!text.includes('AI 接駁中心'));
    assert.ok(!text.includes('/blob/v2.'));
  }
  assert.equal(prompt({ origin: 'https://user:secret@example.com', route: 'native', clientKey: 'codex', session, guide }), '');
  assert.equal(prompt({ origin: 'https://own.example', route: '', clientKey: 'codex', session, guide }), '');
  assert.equal(prompt({ origin: 'not a URL', route: 'native', clientKey: 'codex', session, guide }), '');
  assert.ok(!prompt({ origin: 'https://own.example', route: 'native', clientKey: 'codex', session: { ...session, secret: 'must-not-leak' }, guide }).includes('must-not-leak'));
});
test('the built-in contract requires client-side nonce generation and explicit test authorization', () => {
  assert.equal(guide.entrypoint, 'AI_CONNECT.md');
  assert.equal(guide.setup_owner, 'active_ai_client');
  assert.equal(guide.client_acceptance.authorization, 'explicit_user_test_request');
  assert.equal(guide.client_acceptance.challenge.generator, 'client_csprng');
  assert.equal(guide.client_acceptance.challenge.bytes, 16);
  assert.equal(guide.client_acceptance.challenge.pattern, '^[a-f0-9]{32}$');
  assert.deepEqual(guide.client_acceptance.readback_tools, ['blueprint_get_workday', 'blueprint_list_todos']);
  assert.equal(validSession(session), true);
  for (const change of [{ challenge: '../data' }, { challenge: 'a'.repeat(31) }, { date: '2026-02-30' }, { date: '0000-01-01' }, { client: ' ' }, { client: 'client\nname' }]) {
    assert.equal(validSession({ ...session, ...change }), false);
  }
});
test('compatibility helpers load without a wizard, browser storage or background polling', () => {
  const fs = require('node:fs'), vm = require('node:vm');
  const fail = () => { throw new Error('Pure helpers must not start browser work'); };
  const context = { module: { exports: {} }, window: { addEventListener: fail }, document: new Proxy({}, { get: fail }), localStorage: new Proxy({}, { get: fail }), setInterval: fail, setTimeout: fail };
  vm.runInNewContext(fs.readFileSync(require.resolve('../library_web/connection.js'), 'utf8'), context);
  assert.equal(init, undefined);
  assert.equal(context.module.exports.verified(report, session), true);
  assert.equal(context.module.exports.init, undefined);
});
