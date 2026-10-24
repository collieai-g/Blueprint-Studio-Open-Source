const test = require('node:test');
const assert = require('node:assert/strict');
const { verified, matches, prompt } = require('../library_web/connection.js');
const guide = require('../library_web/connection-guide.json');
const session = { challenge: 'a'.repeat(32), client: 'Codex', date: '2026-10-21' };
const report = { id: 'test-id', date: session.date, source: session.client, source_key: 'blueprint-connect:' + session.challenge,
  title: 'AI 接駁測試', summary: 'blueprint-connection/1:verified', entries: [
    { type: 'outcome', title: 'AI 接駁測試', content: session.challenge },
    { type: 'todo', title: '接駁測試：待辦寫入及更新', content: session.challenge, done: true }
  ] };
test('UI completion requires the matching persistent receipt and completed todo', () => {
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
test('native and Sites handoffs include the actual origin, client, challenge and distinct auth route', () => {
  for (const clientKey of Object.keys(guide.clients)) for (const route of ['native','sites']) {
    const text = prompt({ origin: 'https://own.example', route, clientKey, session: { ...session, client: guide.clients[clientKey].name }, guide });
    assert.ok(text.includes('https://own.example'));
    assert.ok(text.includes('blueprint_verify_connection'));
    assert.ok(text.includes(session.challenge));
    assert.ok(text.includes(route === 'sites' ? 'oauth_resource' : 'BLUEPRINT_BACKEND_PASSWORD'));
    assert.ok(text.includes('不重建或重設資料庫'));
    assert.ok(text.includes('保持代理與 TLS'));
    assert.ok(text.includes(guide.rules[0]));
  }
  assert.equal(prompt({ origin: 'https://user:secret@example.com', route: 'native', clientKey: 'codex', session, guide }), '');
  assert.equal(prompt({ origin: 'https://own.example', route: '', clientKey: 'codex', session, guide }), '');
});
test('new-chat handoffs retain the canonical entrypoint without freezing every workflow rule', () => {
  const current = { ...guide, rules: ['FIRST_CORE_RULE', 'FULL_AGENT_WORKFLOW_RULE'] };
  const text = prompt({ origin: 'https://own.example', route: 'native', clientKey: 'codex', session, guide: current });
  assert.ok(text.includes('blueprint_connection_guide'));
  assert.ok(text.includes('workflows'));
  assert.ok(text.includes('FIRST_CORE_RULE'));
  assert.ok(!text.includes('FULL_AGENT_WORKFLOW_RULE'));
  assert.ok(text.includes('不複製整份規則'));
  for (const name of ['daily', 'install', 'update', 'customization', 'agent', 'skill']) {
    assert.ok(guide.workflows[name].read.length > 0);
    for (const file of guide.workflows[name].read) {
      assert.ok(require('node:fs').existsSync(require('node:path').resolve(__dirname, '..', file)), file);
    }
  }
});
