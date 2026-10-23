/* Scoped D1 snapshots. Platform identity and business permissions belong to the adapter. */
const copy = value => structuredClone(value);
const stamp = () => new Date().toISOString();
const key = (ns, id) => JSON.stringify([ns, id]);
const fail = (message, status = 400) => { throw Object.assign(new Error(message), { status }); };
function text(value, name) {
  if (typeof value !== 'string' || !value || value.length > 200 || value.includes('\0')) fail(`Invalid ${name}.`);
  return value;
}
function selection(value = {}) {
  const namespaces = new Set(), records = new Map(), histories = new Map();
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('A snapshot selection is required.');
  for (const field of ['namespaces', 'records', 'histories']) if (value[field] !== undefined && !Array.isArray(value[field])) fail('Snapshot selectors must be arrays.');
  for (const ns of value.namespaces || []) namespaces.add(text(ns, 'namespace'));
  for (const [field, target] of [['records', records], ['histories', histories]]) {
    for (const item of value[field] || []) {
      const ref = { ns: text(item?.ns, 'namespace'), id: text(item?.id, 'record ID') };
      target.set(key(ref.ns, ref.id), ref);
      if (field === 'histories') records.set(key(ref.ns, ref.id), ref);
    }
  }
  if (namespaces.size > 50 || records.size > 500 || histories.size > 100) fail('Snapshot selection is too large.');
  return { namespaces, records, histories };
}
function merge(left, right) {
  return { namespaces: new Set([...left.namespaces, ...right.namespaces]),
    records: new Map([...left.records, ...right.records]), histories: new Map([...left.histories, ...right.histories]) };
}
function predicate(selected, includeNamespaces = true) {
  const parts = [], values = [];
  if (includeNamespaces && selected.namespaces.size) {
    parts.push(`ns IN (${[...selected.namespaces].map(() => '?').join(',')})`);
    values.push(...selected.namespaces);
  }
  for (const ref of (includeNamespaces ? selected.records : selected.histories).values()) {
    parts.push('(ns=? AND id=?)'); values.push(ref.ns, ref.id);
  }
  return { sql: parts.length ? '(' + parts.join(' OR ') + ')' : '0', values };
}

class Snapshot {
  #rows; #history; #selected; #dependencies; #listed; #counts;
  constructor(rows, history, selected, dependencies = null, listed = null) {
    this.#rows = rows; this.#history = history; this.#selected = selected;
    this.#dependencies = dependencies; this.#listed = listed;
    this.#counts = new Map([...selected.namespaces].map(ns => [ns, [...rows.values()].filter(row => row.ns === ns).length]));
  }
  #allowed(ns, id) {
    if (!this.#selected.namespaces.has(ns) && !this.#selected.records.has(key(ns, id)))
      fail('Record was not included in the authorized snapshot.');
  }
  get(ns, id) {
    this.#allowed(ns, id);
    const row = this.#rows.get(key(ns, id));
    if (this.#dependencies && !this.#dependencies.has(key(ns, id))) this.#dependencies.set(key(ns, id), { ns, id, version: row?.version ?? null });
    return row ? copy(row.value) : null;
  }
  list(ns) {
    if (!this.#selected.namespaces.has(ns)) fail('Namespace was not included in the authorized snapshot.');
    const rows = [...this.#rows.values()].filter(row => row.ns === ns);
    if (this.#dependencies) for (const row of rows) this.get(ns, row.id);
    if (this.#listed && !this.#listed.has(ns)) this.#listed.set(ns, this.#counts.get(ns) || 0);
    return rows.map(row => copy(row.value));
  }
  history(ns, id) {
    if (!this.#selected.histories.has(key(ns, id))) fail('History was not included in the authorized snapshot.');
    if (!this.get(ns, id)) fail('The current record does not exist.', 404);
    return this.#history.filter(row => row.ns === ns && row.id === id).sort((a, b) => a.version - b.version)
      .map(row => ({ ...JSON.parse(row.value), action: row.action, actor: row.actor, recorded_at: row.time }));
  }
}

class Transaction extends Snapshot {
  #rows; #selected; #changed = new Map(); #history = []; #actor;
  constructor(rows, history, selected, actor, dependencies, listed) {
    super(rows, history, selected, dependencies, listed);
    this.#rows = rows; this.#selected = selected; this.#actor = actor;
  }
  create(ns, value, options = {}) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) fail('Record must be an object.');
    const id = text(value.id || crypto.randomUUID(), 'record ID');
    // Namespace-wide selection is needed to make source-key retries idempotent.
    if (options.sourceKey) {
      if (!this.#selected.namespaces.has(ns)) fail('Source-key creation requires a namespace snapshot.');
      const existing = this.list(ns).find(row => this.#rows.get(key(ns, row.id))?.source_key === options.sourceKey);
      if (existing) return { record: existing, created: false };
    }
    if (this.get(ns, id)) fail('Record already exists.', 409);
    const record = { ...copy(value), id, version: 1, created_at: value.created_at || stamp(), updated_at: stamp() };
    const row = { ns, id, value: record, version: 1, source_key: options.sourceKey ?? null };
    this.#rows.set(key(ns, id), row); this.#changed.set(key(ns, id), row);
    this.#history.push({ ns, id, version: 1, value: JSON.stringify(record), action: options.action || 'create', actor: this.#actor, time: stamp() });
    return { record: copy(record), created: true };
  }
  update(ns, id, patch, expectedVersion, options = {}) {
    const old = this.get(ns, id);
    if (!old) fail('Record does not exist.', 404);
    if (!Number.isSafeInteger(expectedVersion) || old.version !== expectedVersion) fail('Record version changed.', 409);
    for (const guard of options.guards || []) {
      const source = this.get(guard.ns, guard.id);
      if (!source || source.version !== guard.version) fail('Source version changed.', 409);
    }
    const changes = typeof patch === 'function' ? patch(copy(old)) : patch;
    if (!changes || typeof changes !== 'object' || Array.isArray(changes) || typeof changes.then === 'function') fail('Patch must be an object or synchronous function.');
    const record = { ...old, ...copy(changes), id, created_at: old.created_at, version: old.version + 1, updated_at: stamp() };
    const row = { ...this.#rows.get(key(ns, id)), value: record, version: record.version };
    this.#rows.set(key(ns, id), row); this.#changed.set(key(ns, id), row);
    this.#history.push({ ns, id, version: record.version, value: JSON.stringify(record), action: options.action || 'update', actor: this.#actor, time: stamp() });
    return copy(record);
  }
  history(ns, id) {
    return [...super.history(ns, id), ...this.#history.filter(row => row.ns === ns && row.id === id)
      .map(row => ({ ...JSON.parse(row.value), action: row.action, actor: row.actor, recorded_at: row.time }))];
  }
  async transaction(callback) {
    const rows = copy(this.#rows), changed = copy(this.#changed), history = copy(this.#history);
    try { return await callback(this); }
    catch (error) {
      this.#rows.clear(); for (const [id, row] of rows) this.#rows.set(id, row);
      this.#changed = changed; this.#history = history; throw error;
    }
  }
  changes() { return copy({ rows: [...this.#changed.values()], history: this.#history }); }
}

async function snapshot(binding, scope, selected) {
  const queries = [], add = (table, columns, part, history = false) => {
    const filter = predicate(part, !history);
    queries.push(binding.prepare(`SELECT ${columns} FROM ${table} WHERE scope=? AND ` + filter.sql).bind(scope, ...filter.values));
  };
  const base = { namespaces: selected.namespaces, records: new Map(), histories: new Map() };
  if (selected.namespaces.size) add('bp_records', 'scope,ns,id,value,version,source_key', base);
  // Keep each statement below D1's bind limit, while retaining one batch snapshot.
  const exact = [...selected.records].filter(([, ref]) => !selected.namespaces.has(ref.ns));
  for (let offset = 0; offset < exact.length; offset += 40) add('bp_records', 'scope,ns,id,value,version,source_key', { ...base, namespaces: new Set(), records: new Map(exact.slice(offset, offset + 40)) });
  const recordQueries = queries.length, histories = [...selected.histories];
  for (let offset = 0; offset < histories.length; offset += 40) add('bp_history', 'scope,ns,id,version,value,action,actor,time', { ...base, histories: new Map(histories.slice(offset, offset + 40)) }, true);
  // D1 batch executes the selected data and ACL/history queries in one transaction.
  const results = await binding.batch(queries), rows = new Map();
  for (const row of results.slice(0, recordQueries).flatMap(result => result.results || [])) {
    const value = JSON.parse(row.value);
    if (value.id !== row.id || value.version !== row.version) fail('Stored record metadata is inconsistent.', 500);
    rows.set(key(row.ns, row.id), { ...row, value });
  }
  return { rows, history: results.slice(recordQueries).flatMap(result => result.results || []) };
}
async function acquire(binding, settings) {
  const token = crypto.randomUUID();
  for (let attempt = 0; attempt < settings.attempts; attempt++) {
    const now = Date.now();
    const result = await binding.prepare('INSERT INTO bp_lease(id,token,expires) VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET token=excluded.token,expires=excluded.expires WHERE bp_lease.expires<? RETURNING token')
      .bind(token, now + settings.leaseMs, now).all();
    if (result.results?.[0]?.token === token) return token;
    if (attempt + 1 < settings.attempts) await new Promise(resolve => setTimeout(resolve, Math.min(250, 25 * 2 ** attempt)));
  }
  fail('Another write is in progress; read the current data before retrying.', 409);
}
function assertion(binding, sql, values) {
  // An invalid JSON expression aborts the entire D1 batch, including history.
  return binding.prepare(`SELECT CASE WHEN ${sql} THEN 1 ELSE json('blueprint store conflict') END AS valid`).bind(...values);
}

/** The caller must derive scope/actor from verified identity, never request input. */
export function createD1Store(binding, { scope, actor, access, authorize, attempts = 12, leaseMs = 120000 } = {}) {
  text(scope, 'scope'); text(actor, 'actor');
  if (!binding?.prepare || !binding?.batch || typeof authorize !== 'function') fail('An authenticated D1 adapter is required.');
  const required = selection(access);
  if (!required.namespaces.size && !required.records.size) fail('Declare the identity and permission snapshot.');
  if (!Number.isSafeInteger(attempts) || attempts < 1 || attempts > 20 || !Number.isSafeInteger(leaseMs) || leaseMs < 1000 || leaseMs > 120000) fail('Invalid write lease settings.');
  async function run(value, callback, writing) {
    if (typeof callback !== 'function') fail('A snapshot callback is required.');
    const selected = merge(required, selection(value)), dependencies = new Map(), listed = new Map();
    const token = writing ? await acquire(binding, { attempts, leaseMs }) : null;
    let committed = false;
    try {
      const data = await snapshot(binding, scope, selected);
      const view = writing ? new Transaction(data.rows, data.history, selected, actor, dependencies, listed) : new Snapshot(data.rows, data.history, selected);
      // The authorization view cannot mutate the staged transaction.
      const auth = new Snapshot(data.rows, data.history, selected, writing ? dependencies : null, writing ? listed : null);
      if (await authorize(auth, Object.freeze({ actor, scope, mode: writing ? 'write' : 'read' })) !== true) fail('Not authorized for this snapshot.', 403);
      const result = await callback(view);
      if (writing) {
        const changes = view.changes(), queries = [assertion(binding, 'EXISTS(SELECT 1 FROM bp_lease WHERE id=1 AND token=? AND expires>?)', [token, Date.now()])];
        for (const ref of dependencies.values()) queries.push(assertion(binding,
          ref.version === null ? 'NOT EXISTS(SELECT 1 FROM bp_records WHERE scope=? AND ns=? AND id=?)' : 'EXISTS(SELECT 1 FROM bp_records WHERE scope=? AND ns=? AND id=? AND version=?)',
          [scope, ref.ns, ref.id, ...(ref.version === null ? [] : [ref.version])]));
        for (const [ns, count] of listed) queries.push(assertion(binding, '(SELECT COUNT(*) FROM bp_records WHERE scope=? AND ns=?)=?', [scope, ns, count]));
        for (const row of changes.rows) {
          const original = dependencies.get(key(row.ns, row.id));
          if (original?.version === null) queries.push(binding.prepare('INSERT INTO bp_records(scope,ns,id,value,version,source_key) VALUES(?,?,?,?,?,?)').bind(scope, row.ns, row.id, JSON.stringify(row.value), row.version, row.source_key));
          else queries.push(binding.prepare('UPDATE bp_records SET value=?,version=?,source_key=? WHERE scope=? AND ns=? AND id=? AND version=?').bind(JSON.stringify(row.value), row.version, row.source_key, scope, row.ns, row.id, original.version));
        }
        for (const row of changes.history) queries.push(binding.prepare('INSERT INTO bp_history(scope,ns,id,version,value,action,actor,time) VALUES(?,?,?,?,?,?,?,?)').bind(scope, row.ns, row.id, row.version, row.value, row.action, row.actor, row.time));
        queries.push(binding.prepare('DELETE FROM bp_lease WHERE id=1 AND token=?').bind(token));
        try { await binding.batch(queries); }
        catch (error) {
          if (/malformed JSON|UNIQUE constraint|blueprint store conflict/i.test(String(error.message || error))) fail('Data or permissions changed; read the current data before retrying.', 409);
          throw error;
        }
        committed = true;
      }
      return result;
    } finally {
      if (token && !committed) await binding.prepare('DELETE FROM bp_lease WHERE id=1 AND token=?').bind(token).run();
    }
  }
  return Object.freeze({ read: (value, callback) => run(value, callback, false), write: (value, callback) => run(value, callback, true) });
}
