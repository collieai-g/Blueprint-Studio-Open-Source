'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {KEY,MAX_PINS,reference,widgetId,normalize,scopeKey,recordView,agentView,readReference}=require('../library_web/overview-cards.js');

test('pin references retain IDs only and never store card contents or labels',()=>{
  const value=reference({kind:'studio_record',id:'record-1',label:'Private company name',content:'Sensitive report',payload:{secret:true},route:'javascript:alert(1)'});
  assert.deepEqual(value,{kind:'studio_record',id:'record-1'});
  const saved=normalize({version:1,refs:[value,value,{kind:'route',route:'agents',label:'Secret'},{kind:'skill',id:'video'}]});
  assert.deepEqual(saved.refs,[value,{kind:'route',route:'agents'},{kind:'skill',id:'video'}]);
  assert.ok(!JSON.stringify(saved).includes('Private'));
  assert.equal(widgetId({kind:'route',route:'agents'}),'ai-agents');
});
test('source kinds, identifiers and routes cannot introduce arbitrary requests',()=>{
  for(const value of [{kind:'file',id:'1'},{kind:'studio_record',id:'../private'},{kind:'agent',id:'a?token=secret'},{kind:'route',route:'https://external.example'},{kind:'route',route:'account'},{kind:'skill',id:'unknown'},{kind:'agent_job',id:''},null])assert.throws(()=>reference(value));
  const many=normalize({version:1,refs:Array.from({length:MAX_PINS+5},(_,index)=>({kind:'studio_record',id:'record-'+index}))});
  assert.equal(many.refs.length,MAX_PINS);
  assert.deepEqual(normalize({version:99,refs:[{kind:'agent',id:'one'}]}).refs,[]);
});
test('pin storage separates authenticated accounts and backend identities',()=>{
  const first=scopeKey({origin:'https://company.example',identity:{username:'owner'},companyId:'company-1'});
  assert.ok(first.startsWith(KEY+':'));
  assert.notEqual(first,scopeKey({origin:'https://company.example',identity:{username:'staff'},companyId:'company-1'}));
  assert.notEqual(first,scopeKey({origin:'https://company.example',identity:{username:'owner'},companyId:'company-2'}));
  assert.notEqual(first,scopeKey({origin:'https://another.example',identity:{username:'owner'},companyId:'company-1'}));
});
test('pinned records fetch current authorized contents on every read',async()=>{
  let version=1,calls=[];
  const api=async path=>{calls.push(path);return {record:{id:'report-1',title:'Version '+version,kind:'report',version,data:{content:'Current '+version}}};};
  const ref={kind:'studio_record',id:'report-1'};
  assert.equal((await readReference(api,ref)).summary,'Current 1');
  version=2;assert.equal((await readReference(api,ref)).title,'Version 2');
  assert.deepEqual(calls,['/api/studio/records/report-1','/api/studio/records/report-1']);
  await assert.rejects(()=>readReference(async()=>{throw Object.assign(new Error('Permission revoked'),{status:403});},ref),/Permission revoked/);
});
test('daily work and library cards preserve source IDs and todo follows its workday',async()=>{
  assert.deepEqual(reference({kind:'todo',id:'todo-1',workday_id:'day-1'}),{kind:'workday',id:'day-1'});
  const requests=[];
  const api=async path=>{requests.push(path);return path.includes('/workdays/')?{workday:{id:'day-1',title:'Daily work',summary:'Verified summary',date:'2026-10-24',version:2}}:{item:{id:'file-1',title:'Source file',notes:'Current notes',kind:'file',version:3}};};
  const day=await readReference(api,{kind:'workday',id:'day-1'}),item=await readReference(api,{kind:'item',id:'file-1'});
  assert.equal(day.kind,'workday');assert.equal(day.version,2);assert.equal(day.summary,'Verified summary');
  assert.equal(item.kind,'item');assert.equal(item.version,3);
  assert.deepEqual(requests,['/api/workdays/day-1','/api/items/file-1']);
});
test('bounded views use plain text and never claim a registered Agent is running',()=>{
  const value=recordView({id:'record-1',title:'<script>alert(1)</script>',data:{content:'x'.repeat(1000)}});
  assert.equal(value.title,'<script>alert(1)</script>');assert.equal(value.summary.length,500);
  const agent=agentView({id:'agent-1',manifest:{name:'Helper'},enabled:true,connection_state:'connected'});
  assert.match(agent.state,/目前 AI 對話/);assert.ok(!agent.state.includes('在線'));
  assert.equal(agentView({id:'agent-1',manifest:{name:'Helper'},enabled:false}).state,'已停用');
});
test('optional modules use only bounded existing data APIs',async()=>{
  const paths=[];
  const api=async path=>{paths.push(path);return path.includes('/agents?')?{agents:[],total:0}:{records:[],total:0};};
  for(const route of ['projects','inbox','agents','command'])assert.deepEqual((await readReference(api,{kind:'route',route})).items,[]);
  assert.deepEqual(paths,['/api/studio/records?kind=project&limit=5&offset=0','/api/studio/records?kind=inbox&limit=5&offset=0','/api/studio/agents?limit=5&offset=0','/api/studio/records?kind=command&limit=5&offset=0']);
  assert.equal((await readReference(api,{kind:'route',route:'skills'})).items.length,2);
  assert.equal(paths.length,4);
});
