import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createD1Store} from '../integrations/sites/d1-store.mjs';

// SQLite executes the same SQL and atomic batches as the D1 binding contract.
function fixture() {
  const sql = new DatabaseSync(':memory:'), queries = [];
  sql.exec(`CREATE TABLE bp_records(scope TEXT,ns TEXT,id TEXT,value TEXT,version INTEGER,source_key TEXT,
    PRIMARY KEY(scope,ns,id),UNIQUE(scope,ns,source_key));
    CREATE TABLE bp_history(scope TEXT,ns TEXT,id TEXT,version INTEGER,value TEXT,action TEXT,actor TEXT,time TEXT,
    PRIMARY KEY(scope,ns,id,version));
    CREATE TABLE bp_lease(id INTEGER PRIMARY KEY,token TEXT,expires INTEGER);`);
  class Statement {
    constructor(query, values = []) { this.query = query; this.values = values; }
    bind(...values) { return new Statement(this.query, values); }
    execute() {
      queries.push({sql:this.query, values:this.values});
      const statement = sql.prepare(this.query);
      if (/^\s*(SELECT|INSERT.*RETURNING)/is.test(this.query)) return {results:statement.all(...this.values)};
      return {results:[], meta:statement.run(...this.values)};
    }
    async all() { return this.execute(); }
    async run() { return this.execute(); }
  }
  const binding = {prepare:query=>new Statement(query), async batch(statements) {
    sql.exec('BEGIN IMMEDIATE');
    try { const result = statements.map(statement=>statement.execute()); sql.exec('COMMIT'); return result; }
    catch(error) { sql.exec('ROLLBACK'); throw error; }
  }};
  function seed(ns, id, value = {}, scope = 'live') {
    const record = {id,version:1,...value};
    sql.prepare('INSERT INTO bp_records VALUES(?,?,?,?,?,NULL)').run(scope,ns,id,JSON.stringify(record),record.version);
    return record;
  }
  seed('users','owner',{role:'owner',enabled:true}); seed('acl','items:item',{allowed:true});
  const settings = {scope:'live',actor:'owner',access:{records:[{ns:'users',id:'owner'},{ns:'acl',id:'items:item'}]},
    authorize(snapshot,{actor}) { return snapshot.get('users',actor)?.enabled === true && snapshot.get('acl','items:item')?.allowed === true; }};
  return {sql,queries,binding,seed,settings,store:createD1Store(binding,settings)};
}
const status = expected => error => error.status === expected;

test('reads use selected rows and selected history in a single snapshot, without a lease', async () => {
  const f=fixture();f.seed('items','item',{title:'visible'});f.seed('items','other',{title:'not selected'});f.seed('items','private',{},'another-company');
  f.sql.prepare('INSERT INTO bp_history VALUES(?,?,?,?,?,?,?,?)').run('live','items','item',1,JSON.stringify({id:'item',version:1,title:'before'}),'create','owner','2026-01-01');
  const value=await f.store.read({records:[{ns:'items',id:'item'}]},snapshot=>snapshot.get('items','item'));
  assert.equal(value.title,'visible');
  assert.equal(f.queries.length,1);assert.doesNotMatch(f.queries[0].sql,/bp_history|bp_lease/);
  assert.ok(f.queries[0].values.includes('item'));assert.ok(!f.queries[0].values.includes('another-company'));
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_lease').get().n,0);
  f.queries.length=0;
  const history=await f.store.read({histories:[{ns:'items',id:'item'}]},snapshot=>snapshot.history('items','item'));
  assert.equal(history[0].title,'before');assert.equal(f.queries.length,2);
  assert.match(f.queries[1].sql,/scope=\?/);assert.match(f.queries[1].sql,/ns=\? AND id=\?/);
});

test('snapshots fail closed for undeclared records, namespace lists, and history', async () => {
  const f=fixture();f.seed('items','item');
  await f.store.read({records:[{ns:'items',id:'item'}]},snapshot=>{
    assert.throws(()=>snapshot.get('items','other'),status(400));
    assert.throws(()=>snapshot.list('items'),status(400));
    assert.throws(()=>snapshot.history('items','item'),status(400));
    assert.equal(snapshot.create,undefined);
    const value=snapshot.get('items','item');value.version=99;
    assert.equal(snapshot.get('items','item').version,1);
  });
});

test('authorization is mandatory, fails closed, and cannot mutate returned records', async () => {
  const f=fixture();
  assert.throws(()=>createD1Store(f.binding,{...f.settings,authorize:undefined}),status(400));
  assert.throws(()=>createD1Store(f.binding,{...f.settings,access:{}}),status(400));
  const denied=createD1Store(f.binding,{...f.settings,authorize:()=>undefined});
  await assert.rejects(denied.read({},()=>assert.fail('callback must not run')),status(403));
  await assert.rejects(denied.write({},()=>assert.fail('callback must not run')),status(403));
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_lease').get().n,0);
});

test('history includes the current record for authorization and rejects missing current data', async () => {
  const f=fixture();f.seed('items','item',{visibility:'admin'});
  const history={histories:[{ns:'items',id:'item'}]};
  const restricted=createD1Store(f.binding,{...f.settings,authorize(snapshot) {
    return snapshot.get('items','item')?.visibility !== 'admin';
  }});
  await assert.rejects(restricted.read(history,snapshot=>snapshot.history('items','item')),status(403));
  await assert.rejects(f.store.read({histories:[{ns:'items',id:'missing'}]},snapshot=>snapshot.history('items','missing')),status(404));
});

test('large exact selections keep statement bind counts bounded in one batch', async () => {
  const f=fixture(),records=[];
  for(let i=0;i<100;i++){f.seed('items','item-'+i);records.push({ns:'items',id:'item-'+i});}
  let batches=0;const original=f.binding.batch;
  f.binding.batch=async statements=>{batches++;return original(statements);};
  await f.store.read({records},snapshot=>assert.equal(snapshot.get('items','item-99').id,'item-99'));
  assert.equal(batches,1);assert.ok(f.queries.every(query=>query.values.length<=81));
});

test('pure reads proceed while a writer holds the lease, and concurrent writers conflict', async () => {
  const f=fixture();f.seed('items','item',{title:'before'});
  let continueWrite,writerStarted;
  const blocked=new Promise(resolve=>{continueWrite=resolve;});
  const started=new Promise(resolve=>{writerStarted=resolve;});
  const writing=f.store.write({records:[{ns:'items',id:'item'}]},async tx=>{
    tx.update('items','item',{title:'after'},1);writerStarted();await blocked;
  });
  await started;f.queries.length=0;
  const read=await f.store.read({records:[{ns:'items',id:'item'}]},snapshot=>snapshot.get('items','item'));
  assert.equal(read.title,'before');assert.ok(f.queries.every(q=>!q.sql.includes('bp_lease')));
  const second=createD1Store(f.binding,{...f.settings,attempts:1});
  await assert.rejects(second.write({namespaces:['items']},()=>{}),status(409));
  continueWrite();await writing;
  assert.equal(JSON.parse(f.sql.prepare("SELECT value FROM bp_records WHERE ns='items'").get().value).title,'after');
});

test('writes preserve versions, source-key idempotency, staged updates, and atomic history', async () => {
  const f=fixture();
  const selection={namespaces:['items'],histories:[{ns:'items',id:'item'}]};
  await f.store.write(selection,tx=>{
    const first=tx.create('items',{id:'item',title:'new'},{sourceKey:'stable'});
    assert.equal(first.created,true);assert.equal(tx.create('items',{id:'different'},{sourceKey:'stable'}).created,false);
    tx.update('items','item',{title:'updated'},1);
    assert.equal(tx.list('items').length,1);assert.equal(tx.history('items','item').length,2);
    const changes=tx.changes();changes.rows[0].value.title='tampered';
    assert.equal(tx.get('items','item').title,'updated');
  });
  assert.equal(f.sql.prepare("SELECT version FROM bp_records WHERE ns='items'").get().version,2);
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_history').get().n,2);
  await assert.rejects(f.store.write(selection,tx=>tx.update('items','item',{title:'stale'},1)),status(409));
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_history').get().n,2);
});

test('reference and ACL versions are checked in SQL even when those records are unchanged', async () => {
  for (const changed of ['source','acl']) {
    const f=fixture();f.seed('items','item',{title:'before'});f.seed('sources','source');
    await assert.rejects(f.store.write({records:[{ns:'items',id:'item'},{ns:'sources',id:'source'}]},tx=>{
      tx.update('items','item',{title:'after'},1,{guards:[{ns:'sources',id:'source',version:1}]});
      const ns=changed==='acl'?'acl':'sources',id=changed==='acl'?'items:item':'source';
      f.sql.prepare('UPDATE bp_records SET version=2,value=? WHERE ns=? AND id=?').run(JSON.stringify({id,version:2,allowed:false}),ns,id);
    }),status(409));
    assert.equal(f.sql.prepare("SELECT version FROM bp_records WHERE ns='items'").get().version,1);
    assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_history').get().n,0);
    assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_lease').get().n,0);
  }
});

test('failure midway through a commit rolls back record and history changes together', async () => {
  const f=fixture();f.seed('items','item',{title:'before'});
  f.sql.exec("CREATE TRIGGER reject_history BEFORE INSERT ON bp_history BEGIN SELECT RAISE(ABORT,'history unavailable'); END");
  await assert.rejects(f.store.write({records:[{ns:'items',id:'item'}]},tx=>tx.update('items','item',{title:'after'},1)),/history unavailable/);
  assert.equal(JSON.parse(f.sql.prepare("SELECT value FROM bp_records WHERE ns='items'").get().value).title,'before');
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_lease').get().n,0);
});

test('nested rollback preserves the outer staged transaction and history', async () => {
  const f=fixture();f.seed('items','item',{title:'before'});
  await f.store.write({namespaces:['items']},async tx=>{
    await assert.rejects(tx.transaction(inner=>{inner.update('items','item',{title:'discard'},1);throw new Error('discard');}),/discard/);
    assert.equal(tx.get('items','item').title,'before');tx.update('items','item',{title:'outer'},1);
  });
  assert.equal(f.sql.prepare('SELECT COUNT(*) AS n FROM bp_history').get().n,1);
  assert.equal(JSON.parse(f.sql.prepare("SELECT value FROM bp_records WHERE ns='items'").get().value).title,'outer');
});
