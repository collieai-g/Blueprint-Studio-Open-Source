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
