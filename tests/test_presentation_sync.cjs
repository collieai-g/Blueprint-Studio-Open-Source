'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const sync=require('../library_web/presentation-sync.js');
const clone=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
const failure=status=>Object.assign(new Error('fixture failure'),{status});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function memory(){const data=new Map();return {getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key),data};}
function backend(initial={}){
  const documents={},calls=[];let version=0,online=true;
  for(const [key,data]of Object.entries(initial))documents[key]={version:1,data:clone(data)},version++;
  function snapshot(){return {version,documents:clone(documents)};}
  function change(key,data){documents[key]={version:(documents[key]?.version||0)+1,data:clone(data)};version++;}
  async function api(path,options={}){
    calls.push({path,options:clone(options)});if(!online)throw failure(503);
    if(options.method==='PATCH'){
      const {key,expected_version,data}=options.body;
      if(expected_version!==(documents[key]?.version||0))throw failure(409);
      change(key,data);return {version,documents:{[key]:clone(documents[key])}};
    }
    const since=new URL(path,'http://test').searchParams.get('since_version');
    return since!==null&&Number(since)===version?{version,unchanged:true}:snapshot();
  }
  return {api,change,snapshot,calls,get: key=>clone(documents[key]),offline:value=>{online=!value;},restore:value=>{for(const key of Object.keys(documents))delete documents[key];Object.assign(documents,clone(value.documents));version=value.version;}};
}
function client(t,server,options={}){const value=sync.create({api:server.api,scope:'https://company.test|alice',storage:memory(),pollMs:0,debounceMs:60000,...options});t.after(()=>value.stop());return value;}

test('three-way merge preserves disjoint edits, deletions and literal display text',()=>{
  const before={cards:{title:{text:'old',x:0},other:{w:10}},texts:{description:'remove'}};
  const local={cards:{title:{text:'<script>literal</script>',x:0},other:{w:10}},texts:{}};
  const remote={cards:{title:{text:'old',x:8},other:{w:14}},texts:{description:'remove'}};
  assert.deepEqual(sync.merge(before,local,remote),{data:{cards:{title:{text:'<script>literal</script>',x:8},other:{w:14}},texts:{}},conflicts:[]});
  assert.equal(before.cards.title.text,'old');
  assert.deepEqual(sync.merge({x:0},{x:1},{x:2}).conflicts,[['x']]);
  assert.deepEqual(sync.merge({visible:['a']},{visible:['a','b']},{visible:['a','c']}).conflicts,[['visible']]);
});
test('read and subscriptions are immutable and receiving remote changes never echoes writes',async t=>{
  const server=backend({overview:{cards:{heading:{x:0}}}}),c=client(t,server);await c.start();
  const updates=[];c.subscribe('overview',(data,meta)=>{updates.push({data:clone(data),meta});data.cards.heading.x=999;});
  const copy=c.get('overview');copy.cards.heading.x=111;
  server.change('overview',{cards:{heading:{x:8}}});await c.pull();
  assert.equal(c.get('overview').cards.heading.x,8);assert.equal(updates.at(-1).meta.source,'remote');
  assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,0);assert.equal(c.status().state,'synced');
});
test('adoption imports an absent document only after support is confirmed',async t=>{
  const server=backend({overview:{text:'cloud'}}),c=client(t,server);
  assert.equal(c.adopt('overview',{text:'old cache'}),false);await c.start();
  assert.equal(c.adopt('overview',{text:'old cache'}),false);
  assert.equal(c.adopt('page:projects',{cards:{title:{text:'my projects'}}}),true);
  assert.equal(c.adopt('page:projects',{cards:{title:{text:'older'}}}),false);
  await c.flush();assert.equal(server.get('overview').data.text,'cloud');assert.equal(server.get('page:projects').data.cards.title.text,'my projects');
});
test('two clients merge independent updates after compare-and-swap conflict',async t=>{
  const initial={cards:{heading:{x:0,text:'title'},other:{w:10}}},server=backend({overview:initial});
  const a=client(t,server),b=client(t,server);await Promise.all([a.start(),b.start()]);
  a.update('overview',{...initial,cards:{...initial.cards,heading:{x:6,text:'title'}}});await a.flush();
  b.update('overview',{...initial,cards:{...initial.cards,other:{w:18}}});await b.flush();
  assert.deepEqual(server.get('overview').data,{cards:{heading:{x:6,text:'title'},other:{w:18}}});
  assert.equal(b.status().state,'synced');await a.pull();assert.deepEqual(a.get('overview'),b.get('overview'));
});
test('same-field concurrent change is retained as conflict until explicitly resolved',async t=>{
  const server=backend({overview:{text:'original'}}),a=client(t,server),b=client(t,server);
  await Promise.all([a.start(),b.start()]);a.update('overview',{text:'from Codex'});await a.flush();
  b.update('overview',{text:'from browser'});await b.flush();
  assert.equal(b.status().state,'conflict');assert.equal(b.get('overview').text,'from browser');
  assert.equal(server.get('overview').data.text,'from Codex');assert.deepEqual(b.status().conflicts,[{key:'overview',paths:[['text']]}]);
  await b.resolve('overview','local');assert.equal(server.get('overview').data.text,'from browser');assert.equal(b.status().state,'synced');
});
test('an editor with deferred remote rendering supplies its visible base to avoid stale overwrites',async t=>{
  const original={text:'old',x:0},server=backend({overview:original}),c=client(t,server);await c.start();
  server.change('overview',{text:'remote',x:0});await c.pull();
  c.update('overview',{text:'old',x:5},{base:original});await c.flush();
  assert.deepEqual(server.get('overview').data,{text:'remote',x:5});
  const displayed=clone(server.get('overview').data);server.change('overview',{text:'new remote',x:5});await c.pull();
  c.update('overview',{text:'my typing',x:5},{base:displayed});await c.flush();
  assert.equal(c.status().state,'conflict');assert.equal(server.get('overview').data.text,'new remote');
  await c.resolve('overview','remote');assert.deepEqual(c.get('overview'),{text:'new remote',x:5});
});
test('offline draft survives reload and is reconciled before retry without leaking across account scopes',async t=>{
  const store=memory(),server=backend({overview:{text:'original',x:0}}),c=client(t,server,{storage:store});await c.start();
  server.offline(true);c.update('overview',{text:'offline',x:0});assert.equal(await c.flush(),false);assert.equal(c.status().state,'offline');c.stop();
  server.offline(false);server.change('overview',{text:'original',x:4});
  const bob=client(t,server,{storage:store,scope:'https://company.test|bob'});assert.equal(bob.get('overview'),undefined);
  const restored=client(t,server,{storage:store});assert.equal(restored.get('overview').text,'offline');await restored.start();await restored.flush();
  assert.deepEqual(server.get('overview').data,{text:'offline',x:4});assert.equal(store.getItem(restored.storageKey),null);
});
test('new local input during an in-flight write is saved afterwards with the acknowledged base',async t=>{
  const server=backend({overview:{text:'a',x:0}}),gate=deferred();let first=true;
  const c=client(t,server,{api:async(path,options)=>{if(options?.method==='PATCH'&&first){first=false;await gate.promise;}return server.api(path,options);}});await c.start();
  c.update('overview',{text:'b',x:0});const saving=c.flush();await tick();
  c.update('overview',{text:'c',x:0},{base:{text:'b',x:0}});gate.resolve();await saving;
  assert.deepEqual(server.get('overview').data,{text:'c',x:0});assert.equal(c.status().state,'synced');
  assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,2);
});
test('GET observing own saved write before PATCH response does not create a conflict',async t=>{
  const server=backend({overview:{text:'a',x:0}}),ack=deferred(),saved=deferred();let first=true;
  const c=client(t,server,{api:async(path,options)=>{const result=await server.api(path,options);if(options?.method==='PATCH'&&first){first=false;saved.resolve();await ack.promise;}return result;}});await c.start();
  c.update('overview',{text:'b',x:0});const saving=c.flush();await saved.promise;
  c.update('overview',{text:'c',x:0},{base:{text:'b',x:0}});await c.pull();assert.equal(c.status().conflicts.length,0);
  ack.resolve();await saving;assert.equal(server.get('overview').data.text,'c');
});
test('partial PATCH response cannot advance the full GET cursor past unrelated writes',async t=>{
  const server=backend({overview:{text:'a'},'page:projects':{text:'old'}}),c=client(t,server);await c.start();
  server.change('page:projects',{text:'remote'});c.update('overview',{text:'local'});await c.flush();
  assert.equal(c.get('page:projects').text,'old');await c.pull();
  assert.equal(c.get('page:projects').text,'remote');
  assert.equal(server.calls.filter(call=>!call.options.method).at(-1).path,'/api/studio/presentation?since_version=2');
});
test('an older GET response cannot regress a document acknowledged by a later PATCH',async t=>{
  const server=backend({overview:{text:'original'}}),gate=deferred();let hold=false;
  const c=client(t,server,{api:async(path,options)=>{const result=await server.api(path,options);if(hold&&!options?.method){hold=false;await gate.promise;}return result;}});await c.start();
  server.change('overview',{text:'external'});hold=true;const reading=c.pull();await tick();
  // This update was based on the actually visible original, so after 409 it must conflict.
  c.update('overview',{text:'local'});const saving=c.flush();gate.resolve();await reading;await saving;
  assert.equal(c.status().state,'conflict');assert.equal(c.getDocument('overview').data.text,'external');
});
test('stopped clients ignore late network responses and do not notify a new account',async t=>{
  const server=backend({overview:{text:'private'}}),gate=deferred();const events=[];
  const c=client(t,server,{api:async(path,options)=>{await gate.promise;return server.api(path,options);},onChange:(...args)=>events.push(args)});
  const boot=c.start();c.stop();gate.resolve();await boot;assert.equal(c.get('overview'),undefined);assert.equal(events.length,0);assert.equal(c.status().state,'stopped');
});
test('unsupported or unauthorized backends preserve local drafts and never claim cloud sync',async t=>{
  for(const [code,state]of [[404,'unsupported'],[401,'unauthorized'],[403,'unauthorized']]){
    let calls=0;const c=client(t,{}, {api:async()=>{calls++;throw failure(code);}});
    assert.equal(await c.start(),false);assert.equal(c.status().state,state);c.update('overview',{text:'local'});
    assert.equal(c.status().state,state);assert.equal(c.get('overview').text,'local');assert.equal(await c.pull(),false);assert.equal(calls,1);
    assert.equal(c.adopt('pins',{version:1,refs:[]}),false);
  }
});
test('visible-only timer probes presentation endpoint and never refreshes company data',async t=>{
  const queue=[],server=backend(),c=client(t,server,{pollMs:2000,isVisible:()=>false,setTimeout:(run,ms)=>{queue.push({run,ms});return queue.length;},clearTimeout:()=>{}});
  await c.start();assert.equal(queue[0].ms,2000);await queue.shift().run();assert.equal(server.calls.length,1);
  assert.equal(queue.length,1);assert.equal(server.calls[0].path,sync.ENDPOINT);
});
test('malformed or prototype-bearing input is never submitted',async t=>{
  const server=backend(),c=client(t,server);await c.start();
  assert.throws(()=>c.update('page:__proto__',{}));assert.throws(()=>c.update('overview',JSON.parse('{"__proto__":{"polluted":true}}')));
  assert.throws(()=>c.update('overview',{value:NaN}));assert.throws(()=>c.update('overview',[]));assert.equal({}.polluted,undefined);
  assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,0);
});
test('a delayed full GET retains newer PATCH acknowledgments while adding unseen documents',async t=>{
  const server=backend({overview:{text:'initial'}}),gate=deferred();let hold=false;
  const c=client(t,server,{api:async(path,options)=>{const result=await server.api(path,options);if(hold&&!options?.method){hold=false;await gate.promise;}return result;}});await c.start();
  server.change('pins',{version:1,refs:[]});hold=true;const reading=c.pull();await tick();
  c.update('overview',{text:'new'});await c.flush();assert.equal(c.getDocument('overview').version,2);
  gate.resolve();await reading;assert.equal(c.get('overview').text,'new');assert.deepEqual(c.get('pins'),{version:1,refs:[]});
  await c.pull();assert.equal(c.get('overview').text,'new');
});
test('a failed write whose result was saved is resolved by readback without a duplicate write',async t=>{
  const server=backend({overview:{text:'original'}});let lose=true;
  const c=client(t,server,{api:async(path,options)=>{const result=await server.api(path,options);if(lose&&options?.method==='PATCH'){lose=false;throw failure(503);}return result;}});await c.start();
  c.update('overview',{text:'saved despite timeout'});await c.flush();assert.equal(c.status().state,'offline');
  await c.pull();assert.equal(c.status().state,'synced');assert.equal(c.status().pending.length,0);
  assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,1);
});
test('initial offline editing with a local layout base does not invent a missing-server conflict',async t=>{
  const server=backend(),c=client(t,server);server.offline(true);await c.start();
  c.update('overview',{text:'changed'},{base:{text:'local old'}});assert.equal(c.status().conflicts.length,0);
  server.offline(false);await c.pull();await c.flush();assert.equal(server.get('overview').data.text,'changed');
});
test('every request is bound to the initial account including Unicode usernames',async t=>{
  const server=backend({overview:{text:'old'}}),c=client(t,server,{username:'使用者甲'});await c.start();
  c.update('overview',{text:'new'});await c.flush();await c.pull();
  assert.ok(server.calls.every(call=>call.options.headers['X-Blueprint-Account']===encodeURIComponent('使用者甲')));
  assert.throws(()=>client(t,server,{username:'bad\nheader'}));
});
test('a restored lower revision freezes writes, preserves drafts and requires explicit reconciliation',async t=>{
  const server=backend({overview:{text:'first'}}),old=server.snapshot(),c=client(t,server);await c.start();
  server.change('overview',{text:'second'});await c.pull();c.update('overview',{text:'my unsaved change'});
  server.restore(old);assert.equal(await c.pull(),false);assert.equal(c.status().state,'diverged');assert.equal(c.status().diverged,true);
  assert.equal(c.get('overview').text,'my unsaved change');assert.equal(await c.flush(),false);assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,0);
  assert.equal(await c.reconcile(),true);assert.equal(c.status().state,'conflict');assert.deepEqual(c.status().conflicts[0].paths,[[]]);
  await c.resolve('overview','local');assert.equal(server.get('overview').data.text,'my unsaved change');
});
test('a reused revision with different content is detected by full read rather than labelled offline',async t=>{
  const server=backend({overview:{text:'first'}}),c=client(t,server);await c.start();
  server.restore({version:1,documents:{overview:{version:1,data:{text:'different history'}}}});
  assert.equal(await c.pull({full:true}),false);assert.equal(c.status().state,'diverged');assert.equal(c.get('overview').text,'first');
  await c.reconcile();assert.equal(c.get('overview').text,'different history');assert.equal(c.status().state,'synced');
});
test('reloading a draft against a restored backend cannot bypass divergence protection',async t=>{
  const store=memory(),server=backend({overview:{text:'first'}}),old=server.snapshot(),c=client(t,server,{storage:store});await c.start();
  server.change('overview',{text:'second'});await c.pull();c.update('overview',{text:'unsaved'});c.stop();server.restore(old);
  const restored=client(t,server,{storage:store});assert.equal(await restored.start(),false);assert.equal(restored.status().state,'diverged');
  assert.equal(restored.get('overview').text,'unsaved');assert.equal(await restored.flush(),false);
});
test('explicit reconciliation emits default data for removed documents without writing it back',async t=>{
  const server=backend({overview:{text:'first'},'page:projects':{text:'project'}}),c=client(t,server);await c.start();const seen=[];
  c.subscribe('page:projects',(data,meta)=>seen.push({data,meta}));server.restore({version:0,documents:{}});
  await c.pull();await c.reconcile();assert.deepEqual(seen.at(-1).data,{});assert.equal(seen.at(-1).meta.source,'resolved');
  assert.equal(c.get('page:projects'),undefined);assert.equal(server.calls.filter(call=>call.options.method==='PATCH').length,0);
});
test('the full supported document count and restored pending draft count are accepted',async t=>{
  const initial=Object.fromEntries(Array.from({length:256},(_,i)=>['page:test'+i,{text:String(i)}])),server=backend(initial),store=memory(),c=client(t,server,{storage:store});
  await c.start();assert.equal(Object.keys(c.getDocuments()).length,256);const docs=c.getDocuments();docs['page:test0'].data.text='not live';assert.equal(c.get('page:test0').text,'0');
  for(let i=0;i<256;i++)c.update('page:test'+i,{text:'pending'+i});c.stop();
  const restored=client(t,server,{storage:store});assert.equal(restored.status().pending.length,256);await restored.start();assert.equal(restored.get('page:test255').text,'pending255');
});
test('a duplicated tab uses a separate draft writer while reload recovers that tab only',async t=>{
  const server=backend({overview:{text:'initial'}}),store=memory(),session=memory();
  const first=client(t,server,{storage:store,sessionStorage:session,navigationType:'navigate'});await first.start();first.update('overview',{text:'first pending'});
  const duplicate=client(t,server,{storage:store,sessionStorage:session,navigationType:'navigate'});assert.notEqual(duplicate.storageKey,first.storageKey);await duplicate.start();duplicate.update('overview',{text:'second pending'});duplicate.stop();
  const reloaded=client(t,server,{storage:store,sessionStorage:session,navigationType:'reload'});assert.equal(reloaded.storageKey,duplicate.storageKey);assert.equal(reloaded.get('overview').text,'second pending');
  assert.equal(JSON.parse(store.getItem(first.storageKey)).entries[0].data.text,'first pending');
});
test('blocked localStorage does not prevent boot or cloud saves',async t=>{
  const vm=require('node:vm'),fs=require('node:fs'),context={module:{exports:{}},setTimeout,clearTimeout};
  Object.defineProperty(context,'localStorage',{get(){throw new Error('blocked');}});
  vm.runInNewContext(fs.readFileSync(require.resolve('../library_web/presentation-sync.js'),'utf8'),context);
  const server=backend({overview:{text:'old'}}),c=context.module.exports.create({api:server.api,scope:'test|alice',pollMs:0,debounceMs:60000});t.after(()=>c.stop());
  await c.start();assert.equal(c.status().storageError,true);c.update('overview',{text:'cloud saved'});await c.flush();assert.equal(server.get('overview').data.text,'cloud saved');assert.equal(c.status().storageError,true);
});
test('offline initial legacy adoption is deferred until a successful read and never replaces cloud data',async t=>{
  const server=backend(),c=client(t,server);server.offline(true);await c.start();
  assert.equal(c.adopt('overview',{text:'legacy overview'}),false);assert.equal(c.adopt('page:projects',{text:'legacy page'}),false);
  server.offline(false);server.change('overview',{text:'from another device'});await c.pull();await c.flush();
  assert.equal(server.get('overview').data.text,'from another device');assert.equal(server.get('page:projects').data.text,'legacy page');
});
test('invalid document writes remain blocked across polling and reload until edited or explicitly retried',async t=>{
  const server=backend({overview:{text:'original'}}),store=memory();let writes=0,reject=true;
  const api=async(path,options)=>{if(options?.method==='PATCH'){writes++;if(reject)throw failure(413);}return server.api(path,options);};
  const c=client(t,server,{api,storage:store});await c.start();c.update('overview',{text:'too large'});await c.flush();
  assert.equal(c.status().state,'error');assert.deepEqual(c.status().errors,[{key:'overview',status:413}]);
  for(let i=0;i<3;i++){await c.pull();await c.flush();}assert.equal(writes,1);c.stop();
  const restored=client(t,server,{api,storage:store});await restored.start();await restored.flush();assert.equal(writes,1);assert.equal(restored.status().state,'error');
  reject=false;restored.update('overview',{text:'fixed'});await restored.flush();assert.equal(writes,2);assert.equal(server.get('overview').data.text,'fixed');
});
test('explicit retry rechecks authentication and saves corrected drafts without an automatic unauthorized loop',async t=>{
  const server=backend({pins:{version:1,refs:[]}});let allowed=false,writes=0;
  const api=async(path,options)=>{if(options?.method==='PATCH'){writes++;if(!allowed)throw failure(403);}return server.api(path,options);};
  const c=client(t,server,{api,username:'alice'});await c.start();c.update('pins',{version:1,refs:[{kind:'item',id:'revoked'}]});await c.flush();
  assert.equal(c.status().state,'unauthorized');await c.pull();assert.equal(writes,1);
  c.update('pins',{version:1,refs:[{kind:'item',id:'allowed'}]});allowed=true;assert.equal(await c.retry(),true);
  assert.equal(c.status().state,'synced');assert.equal(writes,2);assert.equal(server.get('pins').data.refs[0].id,'allowed');
});
test('an explicitly retried backend upgrade can resume formerly unsupported local drafts',async t=>{
  const server=backend();let supported=false;const c=client(t,server,{api:(path,options)=>supported?server.api(path,options):Promise.reject(failure(404))});
  await c.start();c.update('overview',{text:'kept locally'});assert.equal(c.status().state,'unsupported');supported=true;
  assert.equal(await c.retry(),true);assert.equal(server.get('overview').data.text,'kept locally');assert.equal(c.status().state,'synced');
});
