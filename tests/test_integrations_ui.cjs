'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {COMMAND_STATES,fieldsFor,readPayload,approvedUrl,commandIntent,summary}=require('../library_web/integrations.js');
const schema={type:'object',properties:{reply:{type:'string',title:'回覆',maxLength:100},attempt:{type:'integer',minimum:0,maximum:3},reviewed:{type:'boolean'},channel:{type:'string',enum:['email','web']}},required:['reply','reviewed'],additionalProperties:false};
const action={name:'draft_reply',label:'準備回覆',input_schema:schema};
const integration={id:'integration-a',enabled:true,revoked:false,commands:[action]};

test('unknown and pending remain distinct from actual completion',()=>{
  assert.equal(COMMAND_STATES.pending,'待處理');
  assert.equal(COMMAND_STATES.unknown,'結果待查證');
  assert.equal(COMMAND_STATES.completed,'已完成');
  assert.equal(new Set(Object.values(COMMAND_STATES)).size,8);
});
test('primitive input fields retain typed values and reject invalid or undeclared data',()=>{
  assert.deepEqual(readPayload(schema,{reply:'請核對訂單',reviewed:false,attempt:'2',channel:'web',injected:'ignored'}),{reply:'請核對訂單',attempt:2,reviewed:false,channel:'web'});
  assert.throws(()=>readPayload(schema,{reply:' ',reviewed:true}),/回覆/);
  assert.throws(()=>readPayload(schema,{reply:'正常',reviewed:'true'}),/選擇/);
  for(const attempt of ['2.5','Infinity',true,'4'])assert.throws(()=>readPayload(schema,{reply:'正常',reviewed:false,attempt}),/數字/);
  assert.throws(()=>readPayload(schema,{reply:'正常',reviewed:false,channel:'unlisted'}),/選項/);
  assert.throws(()=>readPayload(schema,{reply:'x'.repeat(101),reviewed:false}),/回覆/);
  assert.throws(()=>readPayload(schema,{reply:'正常'}),/reviewed/);
});
test('remote or executable schema shapes cannot become form controls',()=>{
  for(const invalid of [
    {...schema,type:'array'},
    {...schema,additionalProperties:true},
    {...schema,properties:{payload:{$ref:'https://example.org/schema'}}},
    {...schema,properties:JSON.parse('{"__proto__":{"type":"string"}}')},
    {...schema,properties:{constructor:{type:'string'}}},
    {...schema,properties:{payload:{type:'object'}}},
    {...schema,properties:{payload:{type:'string',enum:[]}}},
    {...schema,required:['nonexistent']}
  ])assert.throws(()=>fieldsFor(invalid),/規格/);
});
test('form limits match the declared backend schema dialect',()=>{
  const properties=Object.fromEntries(Array.from({length:32},(_,i)=>['field.'+i,{type:'string'}]));
  const accepted={type:'object',properties,required:[],additionalProperties:false};
  assert.equal(fieldsFor(accepted).length,32);
  assert.throws(()=>fieldsFor({...accepted,properties:{...properties,extra:{type:'string'}}}),/規格/);
  const longName='a'.repeat(80);
  assert.equal(fieldsFor({...accepted,properties:{[longName]:{type:'string'}}})[0].key,longName);
  assert.throws(()=>fieldsFor({...accepted,properties:{[longName+'a']:{type:'string'}}}),/規格/);
  const options=Array.from({length:64},(_,i)=>String(i));
  assert.equal(fieldsFor({...accepted,properties:{choice:{type:'string',enum:options}}})[0].enum.length,64);
  assert.throws(()=>fieldsFor({...accepted,properties:{choice:{type:'string',enum:[...options,'extra']}}}),/規格/);
  assert.throws(()=>readPayload({type:'object',properties:{amount:{type:'number'}},required:[],additionalProperties:false},{amount:'9007199254740992'}),/數字/);
});
test('external case links require an exact approved HTTPS origin and no credentials',()=>{
  const origins=['https://cases.example.org'];
  assert.equal(approvedUrl('https://cases.example.org/case/123?view=timeline',origins),'https://cases.example.org/case/123?view=timeline');
  for(const value of ['https://cases.example.org.evil.test/case','http://cases.example.org/case','javascript:alert(1)','https://username:password@cases.example.org','https://cases.example.org:444/case','https://cases.example.org/\ncase','//cases.example.org/case'])assert.equal(approvedUrl(value,origins),'');
  assert.equal(approvedUrl('https://unapproved.example.org/case',origins),'');
});
test('command intentions preserve service-issued control conditions and exact retry key',()=>{
  const target={id:'record-1',integration_id:'integration-a',version:42,payload:{control_epoch:7,input_cursor:'message-5',expires_at:'2030-01-01T00:00:00Z'}};
  const result=commandIntent(integration,action,{reply:'準備草稿',reviewed:false},target,'ui-retry-key');
  assert.deepEqual(result,{integration_id:'integration-a',name:'draft_reply',payload:{reply:'準備草稿',reviewed:false},idempotency_key:'ui-retry-key',record_id:'record-1',control_epoch:7,input_cursor:'message-5',expires_at:'2030-01-01T00:00:00Z'});
  assert.ok(!('expected_version' in result));
  assert.ok(!('state' in result));
  assert.throws(()=>commandIntent({...integration,enabled:false},action,{},target,'retry'),/停用/);
  assert.throws(()=>commandIntent({...integration,revoked:true},action,{},target,'retry'),/撤銷/);
  assert.throws(()=>commandIntent(integration,action,{reply:'正常',reviewed:true},{...target,integration_id:'integration-b'},'retry'),/不屬於/);
  assert.throws(()=>commandIntent(integration,{...action,name:'arbitrary'},{},null,'retry'),/能力宣告/);
});
test('record previews stay bounded without interpreting source text as markup',()=>{
  const source='<script>fetch("https://untrusted.example.org")</script>';
  assert.equal(summary({text:source}),source);
  assert.equal(summary({content:'x'.repeat(2000)}).length,1200);
  assert.equal(summary({state:'received'}),'{\n  "state": "received"\n}');
});
