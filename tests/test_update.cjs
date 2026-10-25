const test = require('node:test');
const assert = require('node:assert/strict');
const { handoff, restore, validate, prompt, presentationDocuments, restorePresentation } = require('../library_web/update.js');
const presentation=require('../library_web/presentation-sync.js');
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
test('personalized page layouts and current-account source references survive handoff',()=>{
  const key='blueprint-overview-cards-v1:'+encodeURIComponent(site)+'||owner';
  const other='blueprint-overview-cards-v1:'+encodeURIComponent(site)+'||staff';
  const scope=site+'|owner',otherScope=site+'|staff';
  const own={pages:{projects:{cards:{title:{text:'我的項目'}}}}};
  const layouts=JSON.stringify({version:1,pages:{},scopes:{[scope]:own,[otherScope]:{pages:{agents:{cards:{title:{text:'另一位同事的私人字句'}}}}}}});
  const refs=JSON.stringify({version:1,refs:[{kind:'studio_record',id:'report-1'},{kind:'route',route:'agents'}]});
  const store=storage({'blueprint-page-layouts-v1':layouts,[key]:refs,[other]:refs,access_token:'private'});
  const data=handoff(site,{account:{username:'owner'}},store,'native');
  const selected=JSON.parse(data.preferences['blueprint-page-layouts-v1']);
  assert.deepEqual(selected,{version:1,pages:{},scopes:{[scope]:own}});
  assert.ok(!JSON.stringify(data).includes('另一位同事'));
  assert.equal(data.preferences[key],refs);assert.ok(!(other in data.preferences));assert.ok(!('access_token' in data.preferences));
  const foreign={pages:{inbox:{cards:{title:{text:'保留別人的排版'}}}}};
  const destination=storage({'blueprint-page-layouts-v1':JSON.stringify({version:1,pages:{},keep:'metadata',scopes:{[otherScope]:foreign}})});
  assert.throws(()=>restore(data,site,destination,{username:'staff'}),/同一帳戶/);
  assert.throws(()=>restore(data,site,destination),/同一帳戶/);
  restore(data,site,destination,{username:'owner'});
  assert.equal(destination.data[key],refs);
  const restored=JSON.parse(destination.data['blueprint-page-layouts-v1']);
  assert.deepEqual(restored.scopes[scope],own);assert.deepEqual(restored.scopes[otherScope],foreign);assert.equal(restored.keep,'metadata');
});
test('scoped reference backups reject foreign origins, arbitrary values and payloads',()=>{
  const key='blueprint-overview-cards-v1:'+encodeURIComponent(site)+'||owner';
  const data=handoff(site,{},storage(),'native');
  for(const preferences of [
    {'blueprint-overview-cards-v1:token':'"secret"'},
    {['blueprint-overview-cards-v1:'+encodeURIComponent('https://other.example')+'||owner']:'{"version":1,"refs":[]}'},
    {[key]:JSON.stringify({version:1,refs:[{kind:'studio_record',id:'r1',payload:'private content'}]})},
    {[key]:JSON.stringify({version:1,refs:[{kind:'route',route:'account'}]})},
    {[key]:JSON.stringify({version:1,refs:[{kind:'agent',id:'../private'}]})}
  ])assert.throws(()=>validate({...data,preferences},site));
});
test('all shared layout backups export only authenticated display text and declared card references',()=>{
  const scope=site+'|owner',other=site+'|staff';
  const pinKey='blueprint-overview-cards-v1:'+encodeURIComponent(site)+'||owner';
  const workspace={version:1,layoutVersion:4,visible:['heading','pin-studio_record-other-private'],
    cards:{heading:{w:16,h:3},'pin-studio_record-other-private':{w:20,h:12}},texts:{heading:'Other private heading'},
    scopes:{[scope]:{version:1,layoutVersion:4,visible:['heading','pin-studio_record-own','pin-studio_record-other-private'],
      cards:{heading:{w:16,h:3},'pin-studio_record-own':{w:18,h:10},'pin-studio_record-other-private':{w:20,h:12}},texts:{heading:'Owner heading'}},
      [other]:{texts:{heading:'Other private heading'},cards:{'pin-studio_record-other-private':{w:20,h:12}}}}};
  const pages={version:1,pages:{privateLegacy:{cards:{text:{text:'Legacy other private'}}}},
    scopes:{[scope]:{pages:{projects:{cards:{title:{text:'Owner page'}}}}},[other]:{pages:{agents:{cards:{title:{text:'Other private page'}}}}}}};
  const store=storage({'blueprint-workspace-v2':JSON.stringify(workspace),'blueprint-workspace-v2.8-backup':JSON.stringify(workspace),
    'blueprint-page-layouts-v1':JSON.stringify(pages),'blueprint-page-layouts-v1-backup':JSON.stringify(pages),
    [pinKey]:JSON.stringify({version:1,refs:[{kind:'studio_record',id:'own'}]})});
  const data=handoff(site,{account:{username:'owner'}},store,'native');
  const text=JSON.stringify(data);
  for(const forbidden of ['Other private','other-private','Legacy other private'])assert.ok(!text.includes(forbidden),forbidden);
  assert.ok(text.includes('Owner heading'));assert.ok(text.includes('Owner page'));
  const anonymous=handoff(site,{},store,'native');
  assert.ok(!(pinKey in anonymous.preferences));assert.equal(anonymous.preferences['blueprint-page-layouts-v1'],null);
  assert.ok(!JSON.stringify(anonymous).includes('Owner heading'));
});
test('restoring an empty account layout resets only that account and retains other users',()=>{
  const scope=site+'|owner',other=site+'|staff';
  const data=handoff(site,{account:{username:'owner'}},storage(),'native');
  const destination=storage({'blueprint-page-layouts-v1':JSON.stringify({version:1,pages:{},scopes:{[scope]:{pages:{projects:{cards:{title:{text:'newer'}}}}},[other]:{pages:{skills:{active:true}}}}}),
    'blueprint-workspace-v2':JSON.stringify({version:1,scopes:{[scope]:{texts:{heading:'newer'}},[other]:{texts:{heading:'keep'}}}})});
  restore(data,site,destination,{username:'owner'});
  const pages=JSON.parse(destination.data['blueprint-page-layouts-v1']),workspace=JSON.parse(destination.data['blueprint-workspace-v2']);
  assert.deepEqual(pages.scopes[scope],{pages:{}});assert.deepEqual(pages.scopes[other],{pages:{skills:{active:true}}});
  assert.deepEqual(workspace.scopes[scope].texts,{});assert.equal(workspace.scopes[other].texts.heading,'keep');
});
function accountBackup(preferences){return {format:'blueprint-update-handoff',format_version:1,origin:site,account_scope:site+'|owner',preferences};}
function presentationBackend(initial={}){
  let revision=Object.keys(initial).length,online=true,beforeWrite=null;
  const documents=Object.fromEntries(Object.entries(initial).map(([key,data])=>[key,{version:1,data:structuredClone(data)}]));
  const calls=[];
  return {documents,calls,setOnline:value=>{online=value;},beforeWrite:value=>{beforeWrite=value;},
    async api(path,options={}){
      if(!online)throw Object.assign(new Error('offline'),{status:503});
      if(options.method==='PATCH'){
        const body=options.body;calls.push(structuredClone(body));
        if(beforeWrite){const run=beforeWrite;beforeWrite=null;run(documents);revision++;}
        if((documents[body.key]?.version||0)!==body.expected_version)throw Object.assign(new Error('conflict'),{status:409});
        documents[body.key]={version:body.expected_version+1,data:structuredClone(body.data)};revision++;
        return {version:revision,documents:{[body.key]:structuredClone(documents[body.key])}};
      }
      return {version:revision,documents:structuredClone(documents)};
    }};
}
test('restore extracts primary account documents and resets pages absent from a complete page snapshot',()=>{
  const scope=site+'|owner';
  const data=accountBackup({
    'blueprint-workspace-v2':JSON.stringify({version:1,scopes:{[scope]:{version:1,layoutVersion:4,texts:{heading:'Restored'}}}}),
    'blueprint-workspace-v2.8-backup':JSON.stringify({version:1,scopes:{[scope]:{texts:{heading:'Do not publish this backup'}}}}),
    'blueprint-page-layouts-v1':JSON.stringify({version:1,pages:{},scopes:{[scope]:{pages:{projects:{cards:{title:{text:'Projects'}}}}}}}),
    'blueprint-page-layouts-v1-backup':JSON.stringify({version:1,pages:{},scopes:{[scope]:{pages:{agents:{cards:{title:{text:'Historical only'}}}}}}})
  });
  const docs=presentationDocuments(data,site,{username:'owner'},{'page:old':{version:1,data:{active:true}},overview:{version:1,data:{texts:{heading:'Newer'}}}});
  assert.deepEqual(docs.overview,{version:1,layoutVersion:4,texts:{heading:'Restored'}});
  assert.deepEqual(docs['page:projects'],{cards:{title:{text:'Projects'}}});
  assert.deepEqual(docs['page:old'],{});assert.equal(docs['page:agents'],undefined);
  assert.equal(docs.pins,undefined);
  const emptySnapshot=handoff(site,{account:{username:'owner'}},storage(),'native');
  assert.deepEqual(presentationDocuments(emptySnapshot,site,{username:'owner'}).pins,{version:1,refs:[]});
  assert.throws(()=>presentationDocuments(data,site,{username:'staff'}),/同一帳戶/);
  const historical=accountBackup({'blueprint-page-layouts-v1-backup':data.preferences['blueprint-page-layouts-v1-backup']});
  assert.deepEqual(presentationDocuments(historical,site,{username:'owner'},{'page:old':{version:1,data:{}}}),{});
});
test('restoring account layout updates authoritative server documents before reporting success',async()=>{
  const backend=presentationBackend({overview:{texts:{heading:'Current'}},'page:extra':{cards:{title:{text:'Extra'}}},pins:{version:1,refs:[{kind:'route',route:'agents'}]}});
  const local=storage(),sync=presentation.create({api:backend.api,scope:site+'|owner',username:'owner',storage:local,clientId:'restore',pollMs:0,debounceMs:100000});
  try{
    await sync.start();
    const pinKey='blueprint-overview-cards-v1:'+encodeURIComponent(site)+'||owner';
    const data=accountBackup({'blueprint-workspace-v2':JSON.stringify({version:1,layoutVersion:4,texts:{heading:'Backup heading'}}),'blueprint-page-layouts-v1':null,[pinKey]:null});
    const result=await restorePresentation(data,site,local,{username:'owner'},sync);
    assert.equal(result.synced,true);
    assert.equal(backend.documents.overview.data.texts.heading,'Backup heading');
    assert.deepEqual(backend.documents['page:extra'].data,{});
    assert.deepEqual(backend.documents.pins.data,{version:1,refs:[]});
    assert.ok(Object.keys(local.data).some(key=>key.includes(':before-cloud-restore:')));
    assert.ok(Object.keys(local.data).some(key=>key.includes(':before-restore:')));
    assert.ok(!Object.keys(local.data).some(key=>key.includes(':pending-restore:')));
    assert.ok(backend.calls.every(call=>call.expected_version>=1));
  }finally{sync.stop();}
});
test('offline restore retains its selected backup and pending edits without claiming synchronized success',async()=>{
  const backend=presentationBackend({overview:{texts:{heading:'Current'}}}),local=storage();
  const sync=presentation.create({api:backend.api,scope:site+'|owner',storage:local,clientId:'offline',pollMs:0,debounceMs:100000});
  try{
    await sync.start();backend.setOnline(false);
    const data=accountBackup({'blueprint-workspace-v2':JSON.stringify({version:1,layoutVersion:4,texts:{heading:'Offline restore'}})});
    await assert.rejects(restorePresentation(data,site,local,{username:'owner'},sync),/尚未讀取完整帳戶設定/);
    assert.equal(backend.documents.overview.data.texts.heading,'Current');
    assert.equal(sync.get('overview').texts.heading,'Offline restore');
    assert.ok(Object.keys(local.data).some(key=>key.includes(':pending-restore:')));
    assert.equal(JSON.parse(local.data['blueprint-workspace-v2']).scopes[site+'|owner'].texts.heading,'Offline restore');
    backend.setOnline(true);
    assert.equal((await restorePresentation(data,site,local,{username:'owner'},sync)).synced,true);
    assert.equal(backend.documents.overview.data.texts.heading,'Offline restore');
  }finally{sync.stop();}
});
test('concurrent same-field changes during restore are preserved as an explicit conflict',async()=>{
  const backend=presentationBackend({overview:{version:1,layoutVersion:4,texts:{heading:'Current'}}}),local=storage();
  const sync=presentation.create({api:backend.api,scope:site+'|owner',storage:local,clientId:'conflict',pollMs:0,debounceMs:100000});
  try{
    await sync.start();backend.beforeWrite(documents=>{documents.overview={version:2,data:{version:1,layoutVersion:4,texts:{heading:'Other device'}}};});
    const data=accountBackup({'blueprint-workspace-v2':JSON.stringify({version:1,layoutVersion:4,texts:{heading:'Restored heading'}})});
    await assert.rejects(restorePresentation(data,site,local,{username:'owner'},sync),/尚未全部同步/);
    assert.equal(backend.documents.overview.data.texts.heading,'Other device');
    assert.equal(sync.get('overview').texts.heading,'Restored heading');
    assert.equal(sync.status().conflicts[0].key,'overview');
    assert.ok(Object.keys(local.data).some(key=>key.includes(':pending-restore:')));
  }finally{sync.stop();}
});
test('unsupported backends retain local restore and never submit cloud changes',async()=>{
  const local=storage(),data=accountBackup({'blueprint-workspace-v2':null});let called=false;
  const result=await restorePresentation(data,site,local,{username:'owner'},{status:()=>({state:'unsupported'}),pull:()=>{called=true;}});
  assert.deepEqual(result,{synced:false,localOnly:true});assert.equal(called,false);
  assert.deepEqual(JSON.parse(local.data['blueprint-workspace-v2']).scopes[site+'|owner'].texts,{});
});
