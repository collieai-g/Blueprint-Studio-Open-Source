import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { createD1PresentationStorage, createPresentationHandler, readOrdinaryPreferences, saveOrdinaryPreferences, validatePresentation } from '../integrations/sites/presentation.mjs';

// Exercise the adapter's SQL against actual SQLite, shaped like D1's bindings.
function database() {
  const sqlite = new DatabaseSync(':memory:');
  sqlite.exec('CREATE TABLE studio_preferences(username TEXT PRIMARY KEY,version INTEGER NOT NULL,data TEXT NOT NULL)');
  const db = { prepare(sql) { return { bind(...args) { return {
    async first() { return sqlite.prepare(sql).get(...args) || null; },
    async run() { const result = sqlite.prepare(sql).run(...args); return { meta: { changes: result.changes } }; }
  }; } }; } };
  return { sqlite, storage: createD1PresentationStorage(db) };
}
const request = (method = 'GET', body, account = 'owner', query = '', headers = {}) => new Request('https://studio.example/api/studio/presentation' + query, {
  method, headers: { 'Content-Type': 'application/json', 'X-Blueprint-Request': '1', ...(account ? { 'Test-Session': account } : {}), ...headers },
  ...(body === undefined ? {} : { body: JSON.stringify(body) })
});
const authorize = req => {
  const username = req.headers.get('Test-Session');
  if (!username) throw new Error('Not authenticated');
  return { username, readOnly: username === 'reader' };
};
const read = async (handler, account = 'owner', query = '') => (await handler(request('GET', undefined, account, query), {})).json();
const patch = async (handler, key, expected_version, data, account = 'owner') => handler(request('PATCH', { key, expected_version, data }, account), {});

test('two independent clients and MCP-compatible writes share durable account-scoped layouts', async () => {
  const { sqlite, storage } = database();
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    assert.deepEqual(await read(handler), { version: 0, documents: {} });
    const data = { version: 1, layoutVersion: 4, texts: { heading: '我的工作台' }, cards: { heading: { x: 2, y: 1, w: 16, h: 3 } } };
    assert.equal((await patch(handler, 'overview', 0, data)).status, 200);
    const reopened = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    const result = await read(reopened);
    assert.equal(result.documents.overview.version, 1);
    assert.deepEqual(result.documents.overview.data, data);
    assert.deepEqual(await (await patch(handler, 'overview', 1, data)).json(), result);
    const ordinary = await readOrdinaryPreferences(storage, 'owner');
    assert.equal(ordinary.version, 0);
    assert.equal(ordinary.data.notifications, true);
    assert.equal(Object.hasOwn(ordinary.data, '_presentation'), false);
    assert.deepEqual(await read(handler, 'owner', '?since_version=1'), { version: 1, unchanged: true });
    assert.deepEqual(await read(handler, 'owner', '?key=overview&since_version=1'), result);
    assert.deepEqual(await read(handler, 'member'), { version: 0, documents: {} });
    assert.equal((await patch(handler, 'overview', 0, { texts: { heading: 'STALE' } })).status, 409);
    assert.deepEqual((await read(handler)).documents.overview.data, data);
  } finally { sqlite.close(); }
});

test('simultaneous writes merge different documents and conflict on the same document', async () => {
  const { sqlite, storage } = database();
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    const different = await Promise.all([
      patch(handler, 'overview', 0, { texts: { heading: 'A' } }),
      patch(handler, 'page:projects', 0, { active: true, cards: { heading: { text: 'B' } } })
    ]);
    assert.deepEqual(different.map(result => result.status), [200, 200]);
    assert.equal(Object.keys((await read(handler)).documents).length, 2);
    const same = await Promise.all([
      patch(handler, 'overview', 1, { texts: { heading: 'C' } }),
      patch(handler, 'overview', 1, { texts: { heading: 'D' } })
    ]);
    assert.deepEqual(same.map(result => result.status).sort(), [200, 409]);
    assert.equal((await read(handler)).documents.overview.version, 2);
  } finally { sqlite.close(); }
});

test('a tab bound to one account cannot read or write after its session changes accounts', async () => {
  const { sqlite, storage } = database();
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    await patch(handler, 'overview', 0, { texts: { heading: 'Owner layout' } }, 'owner');
    await patch(handler, 'overview', 0, { texts: { heading: 'Member layout' } }, 'member');
    const beforeOwner = await read(handler, 'owner'), beforeMember = await read(handler, 'member');
    const data = { key: 'overview', expected_version: 1, data: { texts: { heading: 'Old-tab draft' } } };
    for (const [session, bound] of [['member', 'owner'], ['owner', 'member']]) {
      for (const method of ['GET', 'PATCH']) {
        const result = await handler(request(method, method === 'PATCH' ? data : undefined, session, '', { 'X-Blueprint-Account': bound }), {});
        assert.equal(result.status, 403);
        assert.equal(JSON.stringify(await result.json()).includes('Member layout'), false);
      }
    }
    assert.deepEqual(await read(handler, 'owner'), beforeOwner);
    assert.deepEqual(await read(handler, 'member'), beforeMember);
    const matched = await handler(request('GET', undefined, 'member', '', { 'X-Blueprint-Account': 'member' }), {});
    assert.equal(matched.status, 200);
    assert.deepEqual(await matched.json(), beforeMember);
    const saved = await handler(request('PATCH', data, 'owner', '', { 'X-Blueprint-Account': 'owner' }), {});
    assert.equal(saved.status, 200);
    assert.equal((await read(handler, 'owner')).documents.overview.data.texts.heading, 'Old-tab draft');
    assert.deepEqual(await read(handler, 'member'), beforeMember);
  } finally { sqlite.close(); }
});

test('account binding uses canonical URI encoding for Unicode usernames', async () => {
  const { sqlite, storage } = database();
  try {
    const account = '測試管理員+香港', encoded = encodeURIComponent(account);
    const handler = createPresentationHandler({ storage, authorize: () => ({ username: account }), authorizeReference: () => true });
    const data = { key: 'overview', expected_version: 0, data: { texts: { heading: 'Unicode account layout' } } };
    const saved = await handler(request('PATCH', data, 'verified', '', { 'X-Blueprint-Account': encoded }), {});
    assert.equal(saved.status, 200);
    const loaded = await handler(request('GET', undefined, 'verified', '', { 'X-Blueprint-Account': encoded }), {});
    assert.equal(loaded.status, 200);
    assert.equal((await loaded.json()).documents.overview.data.texts.heading, 'Unicode account layout');
    for (const wrong of [encoded.toLowerCase(), encodeURIComponent(encoded), encodeURIComponent('另一個帳戶')]) {
      assert.equal((await handler(request('GET', undefined, 'verified', '', { 'X-Blueprint-Account': wrong }), {})).status, 403);
      assert.equal((await handler(request('PATCH', { ...data, expected_version: 1 }, 'verified', '', { 'X-Blueprint-Account': wrong }), {})).status, 403);
    }
    assert.equal((await storage.read(account)).data._presentation.documents.overview.version, 1);
  } finally { sqlite.close(); }
});

test('regular preference writes cannot clobber a concurrently saved layout and vice versa', async () => {
  const { sqlite, storage } = database();
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    const [settings, layout] = await Promise.all([
      saveOrdinaryPreferences(storage, 'owner', 0, { notifications: false, pins: [] }),
      patch(handler, 'overview', 0, { texts: { heading: 'Saved while settings changed' } })
    ]);
    assert.equal(settings.version, 1); assert.equal(layout.status, 200);
    const row = await storage.read('owner');
    assert.equal(row.version, 1); assert.equal(row.data.notifications, false);
    assert.equal(row.data._presentation.documents.overview.version, 1);
    await saveOrdinaryPreferences(storage, 'owner', 1, { notifications: true, pins: [] });
    assert.equal((await read(handler)).documents.overview.data.texts.heading, 'Saved while settings changed');
    await assert.rejects(() => saveOrdinaryPreferences(storage, 'owner', 1, { notifications: false }), error => error.status === 409);
    await assert.rejects(() => saveOrdinaryPreferences(storage, 'owner', 2, { _presentation: {} }), error => error.status === 400);
  } finally { sqlite.close(); }
});

test('auth, same-origin, write-marker and field validation fail before mutation', async () => {
  const { sqlite, storage } = database();
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => true });
    const data = { key: 'overview', expected_version: 0, data: { texts: { heading: 'Good' } } };
    for (const [req, status] of [
      [request('GET', undefined, null), 401],
      [request('PATCH', data, 'reader'), 403],
      [request('PATCH', data, 'owner', '', { Origin: 'https://other.example' }), 403],
      [request('PATCH', data, 'owner', '', { 'X-Blueprint-Request': '' }), 403],
      [request('PATCH', data, 'owner', '', { 'Content-Type': 'text/plain' }), 415],
      [request('PATCH', { ...data, username: 'other' }), 400],
      [request('GET', undefined, 'owner', '?username=other'), 400],
      [request('GET', undefined, 'owner', '?since_version=-1'), 400],
      [request('GET', undefined, 'owner', '?key=overview&key=pins'), 400],
      [request('PATCH', { ...data, key: 'page:__proto__/test' }), 400],
      [request('PATCH', { ...data, expected_version: false }), 400],
      [request('PATCH', { ...data, data: { cards: { heading: { x: false } } } }), 400],
      [request('PATCH', { ...data, data: { texts: { heading: 'x'.repeat(2001) } } }), 400],
      [request('PATCH', { ...data, data: { texts: { heading: 'a\0b' } } }), 400],
      [request('PATCH', { ...data, data: { scripts: ['bad'] } }), 400]
    ]) assert.equal((await handler(req, {})).status, status);
    assert.deepEqual(await read(handler), { version: 0, documents: {} });
  } finally { sqlite.close(); }
});

test('stored stable references survive revocation but new pins require current source permission', async () => {
  const { sqlite, storage } = database(); let allowed = true;
  try {
    const handler = createPresentationHandler({ storage, authorize, authorizeReference: () => allowed });
    assert.equal((await patch(handler, 'pins', 0, { version: 1, refs: [{ kind: 'studio_record', id: 'project-1' }] })).status, 200);
    assert.equal((await patch(handler, 'overview', 0, { visible: ['heading', 'pin-studio_record-project-1'], cards: { 'pin-studio_record-project-1': { x: 2, y: 3 } } })).status, 200);
    allowed = false;
    const revoked = await read(handler, 'owner', '?since_version=2');
    assert.deepEqual(revoked, { version: 2, unchanged: true });
    assert.deepEqual((await read(handler, 'owner', '?key=pins')).documents.pins.data.refs, [{ kind: 'studio_record', id: 'project-1' }]);
    assert.equal((await patch(handler, 'pins', 1, { refs: [{ kind: 'item', id: 'private-item' }] })).status, 403);
  } finally { sqlite.close(); }
});

test('presentation schema accepts literal text but rejects code-bearing or unbounded documents', () => {
  assert.deepEqual(validatePresentation('page:projects', { cards: { title: { text: '<img src=x onerror=alert(1)>' } } }), { cards: { title: { text: '<img src=x onerror=alert(1)>' } } });
  for (const [key, data] of [
    ['pins', { refs: [{ kind: 'item', id: 'one', title: 'Private cached content' }] }],
    ['pins', { refs: [{ kind: 'route', route: 'account' }] }],
    ['overview', { visible: ['heading', 'heading'] }],
    ['page:projects', { cards: { constructor: { x: 1 } } }],
    ['page:projects', { cards: { title: { html: '<b>script</b>' } } }],
    ['overview', { columns: 200 }],
    ['overview', { version: true }]
  ]) assert.throws(() => validatePresentation(key, data), error => error.status === 400);
  const cards = Object.fromEntries(Array.from({ length: 200 }, (_, i) => ['text-' + i, { text: 'x'.repeat(4000) }]));
  assert.throws(() => validatePresentation('page:projects', { cards }), error => error.status === 413);
});
