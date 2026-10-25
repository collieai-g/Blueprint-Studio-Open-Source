'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const layout=require('../library_web/page-layout.js');
const items=[{id:'title',label:'標題',type:'text',required:true},{id:'description',type:'text'},{id:'project-a',type:'card',min:{w:8,h:4},max:{w:30,h:24}},{id:'agent-a',type:'card'}];

test('each page preserves independent plain display text and unknown stored fields',()=>{
  const initial={version:1,extra:'keep',pages:{projects:{unknown:'preserve',cards:{future:{w:9}}},skills:{cards:{title:{text:'Skill'}}}}};
  const page=layout.normalize({cards:{title:{text:'<img src=x onerror=alert(1)>'}}},items);
  const saved=layout.storePage(initial,'projects',page);
  assert.equal(saved.extra,'keep');assert.equal(saved.pages.projects.unknown,'preserve');
  assert.deepEqual(saved.pages.projects.cards.future,{w:9});assert.equal(saved.pages.skills.cards.title.text,'Skill');
  assert.equal(saved.pages.projects.cards.title.text,'<img src=x onerror=alert(1)>');
  assert.equal(initial.pages.projects.cards.title,undefined);
});
test('normalization bounds dimensions, explicit page count and required content',()=>{
  const page=layout.normalize({pages:200,columns:1000,cards:{title:{visible:false,w:0,h:0,text:'x'.repeat(5000)},'project-a':{w:100,h:100,collapsed:true}}},items);
  assert.equal(page.pages,3);assert.equal(page.columns,96);assert.equal(page.cards.title.visible,true);
  assert.equal(page.cards.title.w,2);assert.equal(page.cards.title.h,1);assert.equal(page.cards.title.text.length,4000);
  assert.equal(page.cards['project-a'].w,30);assert.equal(page.cards['project-a'].h,24);assert.equal(page.cards['project-a'].collapsed,true);
});
test('different accounts and companies keep separate route preferences',()=>{
  const alice=layout.normalize({cards:{title:{text:'Alice 的公司'}}},items),bob=layout.normalize({cards:{title:{text:'Bob 的公司'}}},items);
  const first=layout.storePage({version:1,pages:{}},'projects',alice,'company-a:alice');
  const second=layout.storePage(first,'projects',bob,'company-a:bob');
  assert.equal(second.scopes['company-a:alice'].pages.projects.cards.title.text,'Alice 的公司');
  assert.equal(second.scopes['company-a:bob'].pages.projects.cards.title.text,'Bob 的公司');
  assert.deepEqual(second.pages,{});assert.equal(first.scopes['company-a:bob'],undefined);
});
test('unknown saved card IDs never become live widgets or overwrite registries',()=>{
  const page=layout.normalize({cards:{malicious:{visible:true},constructor:{visible:true}}},items);
  assert.deepEqual(Object.keys(page.cards),items.map(item=>item.id));
  for(const id of ['__proto__','constructor','prototype','space id'])assert.throws(()=>layout.normalize(null,[{id}]));
  assert.throws(()=>layout.normalize(null,[{id:'same'},{id:'same'}]));
  assert.throws(()=>layout.storePage({},'__proto__',page));
});
test('grid layout avoids overlaps and stays in explicit page boundaries',()=>{
  const page=layout.normalize(null,items),rects=layout.arrange(page,items,43);
  assert.ok(rects);
  for(const rect of Object.values(rects)){assert.ok(rect.x>=0&&rect.x+rect.w<=43);assert.ok(rect.y%48+rect.h<=48);}
  const entries=Object.entries(rects);
  for(const [i,[id,rect]]of entries.entries())for(const [otherId,other]of entries.slice(i+1))assert.equal(layout.overlaps(rect,other),false,id+' '+otherId);
  const tiny=[{id:'one',type:'card',min:{w:6,h:48},defaultSize:{w:6,h:48}},{id:'two',type:'card',min:{w:6,h:48},defaultSize:{w:6,h:48}}];
  const crowded=layout.normalize(null,tiny,6);assert.equal(layout.arrange(crowded,tiny,6),null);assert.equal(crowded.pages,1);
  crowded.pages=2;assert.ok(layout.arrange(crowded,tiny,6));
});
test('responsive display never mutates saved desktop geometry or text',()=>{
  const page=layout.normalize({active:true,cards:{title:{text:'我的工作'},'project-a':{w:28,h:12}}},items),before=JSON.stringify(page);
  const mobile=layout.fit(page,items,12);assert.ok(mobile);assert.equal(JSON.stringify(page),before);
  for(const rect of Object.values(mobile))assert.ok(rect.x+rect.w<=12);
  assert.equal(page.cards['project-a'].w,28);assert.equal(page.cards.title.text,'我的工作');
});
test('collapsed and hidden cards retain expanded geometry for later restoration',()=>{
  const page=layout.normalize({cards:{'project-a':{w:20,h:22,collapsed:true},'agent-a':{visible:false,w:19,h:18}}},items);
  const rects=layout.arrange(page,items,43);assert.equal(rects['project-a'].h,4);assert.equal(page.cards['project-a'].h,22);
  assert.equal(rects['agent-a'],undefined);assert.equal(page.cards['agent-a'].h,18);
});
test('malformed local preferences fall back without changing legacy overview key',()=>{
  for(const raw of ['{','null','[]','{"version":2,"pages":{}}'])assert.deepEqual(layout.decode(raw),{version:1,pages:{}});
  assert.equal(layout.KEY,'blueprint-page-layouts-v1');assert.notEqual(layout.KEY,'blueprint-workspace-v2');
  assert.equal(layout.normalize(null,items).active,false);
});
test('editing a partial synced page sends only actual changes and retains unmounted cards',()=>{
  const raw={cards:{title:{text:'原有標題'},'not-in-current-filter':{x:4,w:20,text:'另一項目'}}};
  const before=layout.normalize(raw,items),next=structuredClone(before);
  next.cards.title.text='新的標題';
  const changed=layout.editPresentation(raw,before,next);
  assert.deepEqual(changed,{cards:{title:{text:'新的標題'},'not-in-current-filter':{x:4,w:20,text:'另一項目'}}});
  assert.deepEqual(raw.cards.title,{text:'原有標題'});
  assert.equal(changed.pages,undefined);
  assert.equal(changed.cards['project-a'],undefined);
});
test('restoring display text removes the override without restoring an older title',()=>{
  const raw={version:1,cards:{title:{text:'自訂標題',x:2},future:{w:9,h:5}}};
  const before=layout.normalize(raw,items),next=structuredClone(before);delete next.cards.title.text;
  const changed=layout.editPresentation(raw,before,next);
  assert.deepEqual(changed.cards.title,{x:2});
  assert.deepEqual(changed.cards.future,{w:9,h:5});
});
test('remote empty page replaces its cache without reviving removed geometry or another account',()=>{
  const before=layout.storePage({version:1,pages:{}},'projects',{cards:{title:{text:'舊標題'}}},'company:alice');
  const withOther=layout.storePage(before,'projects',{cards:{title:{text:'Bob'}}},'company:bob');
  const result=layout.replacePage(withOther,'projects',{},'company:alice');
  assert.deepEqual(result.scopes['company:alice'].pages.projects,{});
  assert.equal(result.scopes['company:bob'].pages.projects.cards.title.text,'Bob');
  assert.equal(before.scopes['company:alice'].pages.projects.cards.title.text,'舊標題');
});
test('legacy page migration includes bounded presentation values only',()=>{
  const source=JSON.parse('{"version":1,"active":true,"pages":1,"record":{"title":"private"},"cards":{"title":{"text":"Title","record_id":"private","w":20},"future":{"x":1,"y":2,"visible":false},"__proto__":{"text":"unsafe"},"bad":{"w":10000,"text":"bad\\u0000text"}}}');
  const migrated=layout.presentationData(source);
  assert.deepEqual(migrated,{version:1,active:true,pages:1,cards:{title:{w:20,text:'Title'},future:{x:1,y:2,visible:false},bad:{}}});
  assert.equal(Object.hasOwn(migrated.cards,'__proto__'),false);
});
