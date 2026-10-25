/* Account presentation API for an EXISTING Sites adapter.
 * The host must supply its verified identity and current resource permissions.
 * This module does not create a Site/database, trust browser identity headers,
 * or migrate a private installation. D1 storage requires the existing
 * studio_preferences(username PRIMARY KEY, version, data) schema.
 */
const MAX_DOCUMENT = 128 * 1024, MAX_STATE = 512 * 1024, MAX_DOCUMENTS = 256;
const encoder = new TextEncoder();
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const own = (value, key) => Object.hasOwn(value, key);
const clone = value => JSON.parse(JSON.stringify(value));
const equal = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, index) => equal(item, b[index]))) ||
  (object(a) && object(b) && Object.keys(a).length === Object.keys(b).length && Object.keys(a).every(key => own(b, key) && equal(a[key], b[key])));
const fail = (status, message) => { throw Object.assign(new Error(message), { status, safe: true }); };
const bad = () => fail(400, '排版設定格式不正確。');
const count = value => [...value].length;
const size = value => encoder.encode(JSON.stringify(value)).length;
const safeName = (value, maximum) => typeof value === 'string' && count(value) <= maximum &&
  /^[A-Za-z0-9][A-Za-z0-9:_.\/-]*$/.test(value) &&
  !value.split(/[:./-]/).some(part => ['__proto__', 'constructor', 'prototype'].includes(part));
const text = (value, maximum) => typeof value === 'string' && count(value) <= maximum && !value.includes('\0') && !/[\uD800-\uDFFF]/u.test(value);
const integer = (value, minimum, maximum) => Number.isSafeInteger(value) && value >= minimum && value <= maximum;
const core = new Set(['heading', 'description', 'date', 'files', 'links', 'youtube', 'unfiled', 'workdays', 'todos', 'recent', 'projects', 'inbox', 'ai-agents', 'ai-skills', 'commands']);
const overviewId = value => typeof value === 'string' && (core.has(value) || /^pin-(studio_record|agent|agent_job|skill|workday|item)-[A-Za-z0-9_.-]{1,160}$/.test(value));
const routes = new Set(['projects', 'inbox', 'agents', 'skills', 'command']);
const kinds = new Set(['studio_record', 'agent', 'agent_job', 'skill', 'workday', 'item']);

export function presentationKey(key) {
  if (key === 'overview' || key === 'pins' || (typeof key === 'string' && key.startsWith('page:') && safeName(key.slice(5), 300))) return key;
  bad();
}

function fields(value, allowed) { if (!object(value) || Object.keys(value).some(key => !allowed.includes(key))) bad(); }
function common(value) {
  if (own(value, 'version') && value.version !== 1) bad();
  if (own(value, 'pages') && !integer(value.pages, 1, 3)) bad();
  if (own(value, 'columns') && !integer(value.columns, 6, 96)) bad();
}
export function presentationReference(ref) {
  if (!object(ref)) bad();
  if (ref.kind === 'route') { fields(ref, ['kind', 'route']); if (!routes.has(ref.route)) bad(); }
  else {
    fields(ref, ['kind', 'id']);
    if (!kinds.has(ref.kind) || typeof ref.id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/.test(ref.id)) bad();
    if (ref.kind === 'skill' && !['video', 'general'].includes(ref.id)) bad();
  }
  return ref;
}
export function validatePresentation(key, value) {
  presentationKey(key);
  if (key === 'pins') {
    fields(value, ['version', 'refs']); common(value);
    if (own(value, 'refs')) {
      if (!Array.isArray(value.refs) || value.refs.length > 24) bad();
      const seen = new Set();
      for (const ref of value.refs) { presentationReference(ref); const id = `${ref.kind}:${ref.id || ref.route}`; if (seen.has(id)) bad(); seen.add(id); }
    }
  } else {
    const overview = key === 'overview';
    fields(value, overview ? ['version', 'layoutVersion', 'visible', 'mode', 'pages', 'columns', 'cards', 'texts'] : ['version', 'active', 'pages', 'columns', 'cards']);
    common(value);
    if (overview) {
      if (own(value, 'layoutVersion') && value.layoutVersion !== 4) bad();
      if (own(value, 'mode') && !['ai', 'manual'].includes(value.mode)) bad();
      if (own(value, 'visible') && (!Array.isArray(value.visible) || value.visible.length > 40 || value.visible.some(id => !overviewId(id)) || new Set(value.visible).size !== value.visible.length)) bad();
      if (own(value, 'texts')) { fields(value.texts, ['heading', 'description', 'date']); if (Object.values(value.texts).some(copy => !text(copy, 2000))) bad(); }
    } else if (own(value, 'active') && typeof value.active !== 'boolean') bad();
    if (own(value, 'cards')) {
      if (!object(value.cards) || Object.keys(value.cards).length > (overview ? 40 : 200)) bad();
      for (const [id, card] of Object.entries(value.cards)) {
        if (!(overview ? overviewId(id) : safeName(id, 240))) bad();
        fields(card, overview ? ['x', 'y', 'w', 'h', 'collapsed'] : ['x', 'y', 'w', 'h', 'visible', 'collapsed', 'text']);
        for (const [field, min, max] of [['x', 0, 95], ['y', 0, 143], ['w', 1, 96], ['h', 1, 48]]) if (own(card, field) && !integer(card[field], min, max)) bad();
        for (const field of ['collapsed', 'visible']) if (own(card, field) && typeof card[field] !== 'boolean') bad();
        if (own(card, 'text') && !text(card.text, 4000)) bad();
      }
    }
  }
  if (size(value) > MAX_DOCUMENT) fail(413, '這份排版設定超出大小上限。');
  return clone(value);
}

function stateOf(value) {
  if (value === undefined) return { version: 0, documents: {} };
  if (!object(value) || !integer(value.version, 0, Number.MAX_SAFE_INTEGER) || !object(value.documents) || Object.keys(value.documents).length > MAX_DOCUMENTS || size(value) > MAX_STATE) fail(503, '已保存的排版設定需要檢查。');
  for (const [key, doc] of Object.entries(value.documents)) {
    if (!object(doc) || !integer(doc.version, 1, Number.MAX_SAFE_INTEGER) || !object(doc.data)) fail(503, '已保存的排版設定需要檢查。');
    try { validatePresentation(key, doc.data); } catch { fail(503, '已保存的排版設定需要檢查。'); }
  }
  return clone(value);
}
function username(identity) {
  if (!identity || !text(identity.username, 240) || !identity.username || /[\x00-\x1f\x7f]/.test(identity.username)) fail(401, '請先登入工作台。');
  return identity.username;
}

/** D1 uses a full-row comparison to protect regular preferences and presentation.
 * All regular-preference writers must use saveOrdinaryPreferences below (or an
 * equivalent atomic merge/CAS) so they preserve the latest reserved field.
 */
export function createD1PresentationStorage(db, { defaultPreferences = () => ({
  groups: ['work', 'specialists'], hidden_groups: [], narrow: false, notifications: true,
  pins: [], workspaces: [], focus: [], monthly_review: false
}) } = {}) {
  if (!db || typeof db.prepare !== 'function') throw new Error('Existing D1 binding required.');
  if (typeof defaultPreferences !== 'function') throw new Error('Provide the existing host preference defaults.');
  return {
    async read(account) {
      const row = await db.prepare('SELECT version,data FROM studio_preferences WHERE username=?').bind(account).first();
      if (!row) {
        const defaults = defaultPreferences(account);
        if (!object(defaults) || own(defaults, '_presentation')) fail(503, '偏好設定預設值需要檢查。');
        return { token: null, version: 0, data: clone(defaults) };
      }
      let data; try { data = JSON.parse(row.data); } catch { fail(503, '偏好設定需要檢查。'); }
      if (!object(data) || !integer(row.version, 0, Number.MAX_SAFE_INTEGER)) fail(503, '偏好設定需要檢查。');
      return { token: { version: row.version, data: row.data }, version: row.version, data };
    },
    async compareAndSwap(account, row, data, version = row.version) {
      const result = row.token === null ?
        await db.prepare('INSERT OR IGNORE INTO studio_preferences(username,version,data) VALUES(?,?,?)').bind(account, version, JSON.stringify(data)).run() :
        await db.prepare('UPDATE studio_preferences SET version=?,data=? WHERE username=? AND version=? AND data=?').bind(version, JSON.stringify(data), account, row.token.version, row.token.data).run();
      return result?.meta?.changes === 1;
    }
  };
}

/** Normal preferences must not expose the reserved presentation document. */
export async function readOrdinaryPreferences(storage, account) {
  username({ username: account });
  const row = await storage.read(account), data = clone(row.data);
  delete data._presentation;
  return { version: row.version, data };
}

/** Supply already validated COMPLETE regular preferences; this preserves the
 * latest presentation even if a browser writes while settings are being saved.
 */
export async function saveOrdinaryPreferences(storage, account, expectedVersion, values) {
  if (!integer(expectedVersion, 0, Number.MAX_SAFE_INTEGER) || !object(values) || own(values, '_presentation')) bad();
  username({ username: account });
  for (let attempt = 0; attempt < 8; attempt++) {
    const row = await storage.read(account);
    if (row.version !== expectedVersion) fail(409, '偏好設定已有更新，請重新讀取。');
    const data = { ...clone(values), ...(own(row.data, '_presentation') ? { _presentation: row.data._presentation } : {}) };
    if (await storage.compareAndSwap(account, row, data, expectedVersion + 1)) return { version: expectedVersion + 1, data: clone(values) };
  }
  fail(503, '設定暫時忙碌，請稍後重試。');
}

/** authorize(request, env) must verify the host session and return its own
 * {username, readOnly?}; never derive this from request JSON/query parameters.
 * authorizeReference(identity, ref, env) must recheck current source permissions.
 * Return null for other routes, so a host can compose its existing API.
 */
export function createPresentationHandler({ storage, authorize, authorizeReference }) {
  if (!storage?.read || !storage?.compareAndSwap || typeof authorize !== 'function' || typeof authorizeReference !== 'function') throw new Error('Verified identity, storage CAS and resource checks are required.');
  const json = (value, status = 200) => Response.json(value, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
  return async function handle(request, env) {
    const url = new URL(request.url);
    if (url.pathname !== '/api/studio/presentation') return null;
    try {
      let identity; try { identity = await authorize(request, env); } catch { fail(401, '請先登入工作台。'); }
      const account = username(identity);
      // A tab binds its local cache/drafts to the identity seen at startup.
      // Cookies can change in another tab; never read/write that new account
      // using the old tab's cached presentation. This header is an assertion,
      // not an identity source and cannot grant access to another account.
      const expectedAccount = request.headers.get('X-Blueprint-Account');
      if (expectedAccount !== null && expectedAccount !== encodeURIComponent(account)) fail(403, '登入帳戶已有變更，請重新載入工作台。');
      if (!['GET', 'PATCH'].includes(request.method)) fail(405, '此介面只支援讀取及修改。');
      const origin = request.headers.get('Origin');
      if ((origin && origin !== url.origin) || request.headers.get('Sec-Fetch-Site') === 'cross-site') fail(403, '不允許跨網站操作。');
      if (request.method === 'GET') {
        if ([...url.searchParams.keys()].some(key => !['key', 'since_version'].includes(key)) || [...new Set(url.searchParams.keys())].some(key => url.searchParams.getAll(key).length !== 1)) bad();
        const key = url.searchParams.has('key') ? presentationKey(url.searchParams.get('key')) : null;
        const since = url.searchParams.get('since_version');
        if (since !== null && (!/^\d+$/.test(since) || !integer(Number(since), 0, Number.MAX_SAFE_INTEGER))) bad();
        const state = stateOf((await storage.read(account)).data._presentation);
        // Stable account-owned references contain no source snapshot. Reading
        // their actual content must still use the host's current permission gate.
        if (!key && since !== null && Number(since) === state.version) return json({ version: state.version, unchanged: true });
        const documents = key ? (own(state.documents, key) ? { [key]: state.documents[key] } : {}) : state.documents;
        return json({ version: state.version, documents });
      }
      if (identity.readOnly) fail(403, '此連接只可讀取。');
      if (url.search) bad();
      if (request.headers.get('X-Blueprint-Request') !== '1') fail(403, '請使用工作台或已授權工具修改。');
      if (request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') fail(415, '需要 application/json。');
      const declared = request.headers.get('Content-Length');
      if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > MAX_DOCUMENT + 4096)) fail(413, '排版設定超出大小上限。');
      const reader = request.body?.getReader(); if (!reader) bad();
      const chunks = []; let bytes = 0;
      try { while (true) { const part = await reader.read(); if (part.done) break; bytes += part.value.byteLength; if (bytes > MAX_DOCUMENT + 4096) { await reader.cancel(); fail(413, '排版設定超出大小上限。'); } chunks.push(part.value); } } finally { reader.releaseLock(); }
      const all = new Uint8Array(bytes); let offset = 0; for (const part of chunks) { all.set(part, offset); offset += part.length; }
      let body; try { body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(all)); } catch { bad(); }
      fields(body, ['key', 'expected_version', 'data']);
      if (Object.keys(body).length !== 3 || !integer(body.expected_version, 0, Number.MAX_SAFE_INTEGER)) bad();
      const key = presentationKey(body.key), data = validatePresentation(key, body.data);
      for (let attempt = 0; attempt < 8; attempt++) {
        const row = await storage.read(account), state = stateOf(row.data._presentation);
        const current = own(state.documents, key) ? state.documents[key] : null;
        if ((current?.version || 0) !== body.expected_version) fail(409, '這份排版已有更新，請重新讀取後合併修改。');
        if (key === 'pins') for (const ref of data.refs || []) if (await authorizeReference(identity, ref, env) !== true) fail(403, '這份排版包含目前未獲准使用的來源。');
        if (current && equal(current.data, data)) return json({ version: state.version, documents: { [key]: current } });
        if (state.version === Number.MAX_SAFE_INTEGER || body.expected_version === Number.MAX_SAFE_INTEGER) fail(409, '排版版本已達上限。');
        const doc = { version: body.expected_version + 1, data };
        const next = { version: state.version + 1, documents: { ...state.documents, [key]: doc } };
        if (Object.keys(next.documents).length > MAX_DOCUMENTS || size(next) > MAX_STATE) fail(413, '帳戶排版設定超出大小上限。');
        if (await storage.compareAndSwap(account, row, { ...row.data, _presentation: next })) return json({ version: next.version, documents: { [key]: doc } });
      }
      fail(503, '排版設定暫時忙碌，請稍後重試。');
    } catch (error) { return json({ error: error.safe ? error.message : '暫時未能讀寫排版設定。' }, error.safe ? error.status : 503); }
  };
}
