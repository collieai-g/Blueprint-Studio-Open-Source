'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {normalize,arrange,fit,preset,overlaps,IDS,TEXT_IDS,PAGE_ROWS,MAX_PAGES}=require('../library_web/workspace.js');
const all=changes=>normalize({version:1,layoutVersion:4,cards:{},visible:IDS,...changes});
function check(layout,cols,pages,visible=IDS){
  assert.ok(layout);assert.deepEqual(Object.keys(layout).sort(),[...visible].sort());
  const rects=Object.values(layout);
  rects.forEach((r,i)=>{
    assert.ok(Object.values(r).every(Number.isInteger));assert.ok(r.x>=0&&r.x+r.w<=cols);
    assert.ok(r.y>=0&&r.y+r.h<=pages*PAGE_ROWS);assert.ok(r.y%PAGE_ROWS+r.h<=PAGE_ROWS);
    assert.ok(r.w*r.h>=2);
    rects.slice(i+1).forEach(other=>assert.equal(overlaps(r,other),false));
  });
}
test('fresh and earlier layouts show only the overview heading without rewriting saved geometry',()=>{
  assert.deepEqual(normalize(null).visible,['heading']);
  const old={version:1,layoutVersion:3,mode:'manual',pages:3,columns:43,cards:{heading:{x:2,y:1,w:16,h:3},files:{x:5,y:14,w:12,h:7,collapsed:true},todos:{x:0,y:100,w:18,h:20},connection:{x:20,y:2,w:14,h:5}}};
  const original=JSON.stringify(old),v=normalize(old);
  assert.equal(JSON.stringify(old),original);assert.equal(v.mode,'manual');assert.equal(v.pages,3);
  assert.deepEqual(v.visible,['heading']);assert.equal(v.cards.heading.x,2);assert.equal(v.cards.heading.y,1);
  assert.equal(v.cards.files.y,14);assert.equal(v.cards.files.h,7);assert.equal(v.cards.files.collapsed,true);
  assert.equal(v.cards.todos.y,100);assert.ok(!('connection' in v.cards));
  check(fit(v,43),43,3,['heading']);assert.deepEqual(normalize(JSON.parse(JSON.stringify(v))),v);
});
test('visibility is opt-in, deduplicated and limited to supported modules; retired setup cannot return',()=>{
  const v=all({visible:['files','files','connection','intro-title','intro-description','unknown']});
  assert.deepEqual(v.visible,['heading','files']);assert.equal(Object.keys(v.cards).length,IDS.length);
  for(const retired of ['connection','intro-title','intro-description'])assert.ok(!IDS.includes(retired)&&!(retired in v.cards));
  const original=structuredClone(v.cards.files);v.visible=['heading'];check(fit(v,43),43,1,['heading']);
  v.visible.push('files');assert.deepEqual(v.cards.files,original);check(fit(v,43),43,1,v.visible);
  assert.deepEqual(normalize({version:99,layoutVersion:4,visible:['files'],cards:{}}).visible,['heading']);
});
test('corrupt geometry is bounded without exposing hidden modules',()=>{
  const v=all({mode:'bad',pages:999,columns:Infinity,cards:{heading:{w:1,h:1,x:-5},files:{w:1e6,h:-4,y:Infinity},unknown:{w:1}}});
  assert.equal(v.mode,'ai');assert.equal(v.pages,MAX_PAGES);assert.equal(v.cards.heading.w,2);assert.equal(v.cards.files.w,96);assert.equal(v.cards.files.h,4);
  for(const cols of [6,13,43,96])check(fit(v,cols),cols,3);
});
test('two- and four-cell text keeps exact geometry',()=>{
  for(const id of TEXT_IDS)for(const [w,h] of [[2,1],[1,2],[2,2]]){
    const v=all();v.cards[id]={x:0,y:0,w,h,collapsed:false};const r=arrange(v,43,id);
    check(r,43,1);assert.equal(r[id].w,w);assert.equal(r[id].h,h);
  }
});
test('responsive viewing and presets preserve visibility and never overwrite preference geometry',()=>{
  for(const visible of [['heading'],['heading','files','todos'],IDS]){
    const v=all({visible}),before=JSON.stringify(v);
    for(const cols of [6,7,13,20,30,43,48,96]){
      check(fit(v,cols),cols,1,visible);
      for(const name of ['balanced','focus','compact']){
        const p=preset(name,cols,v.mode,v.pages,visible);assert.deepEqual(p.visible,visible);check(arrange(p,cols),cols,1,visible);
      }
    }
    assert.equal(JSON.stringify(v),before);
  }
});
test('crowded edits are rejected while hidden modules consume no space',()=>{
  const v=all();Object.assign(v.cards.files,{x:0,y:0,w:43,h:48});
  assert.equal(arrange(v,43,'files'),null);assert.equal(v.pages,1);check(fit(v,43),43,1);
  v.visible=['heading'];check(arrange(v,43),43,1,['heading']);
  v.pages=3;Object.assign(v.cards.heading,{x:0,y:47,w:2,h:2});
  const layout=arrange(v,43,'heading');check(layout,43,3,['heading']);assert.equal(layout.heading.y,46);
});
test('collapse and visibility round-trips keep expanded height and requested page',()=>{
  const v=all({pages:3,visible:['heading','todos']});v.cards.todos={x:0,y:96,w:12,h:20,collapsed:true};
  const r=arrange(v,43,'todos');assert.equal(r.todos.y,96);assert.equal(r.todos.h,4);check(r,43,3,v.visible);
  v.visible=['heading'];const reload=normalize(JSON.parse(JSON.stringify(v)));assert.equal(reload.cards.todos.h,20);
  reload.visible.push('todos');reload.cards.todos.collapsed=false;
  assert.equal(arrange(reload,43,'todos').todos.h,20);assert.equal(arrange(reload,43,'todos').todos.y,96);
});
test('bounded fallback retains all explicitly selected modules in fragmented imported layouts',()=>{
  let seed=20261021;const next=max=>{seed=(seed*1664525+1013904223)>>>0;return seed%max;};
  for(let n=0;n<100;n++){
    const v=all();IDS.forEach(id=>Object.assign(v.cards[id],{x:next(96),y:next(144),w:1+next(96),h:1+next(48)}));
    const bounded=normalize(v),before=JSON.stringify(bounded);
    for(const cols of [6,13,43])check(fit(bounded,cols),cols,1);
    assert.equal(JSON.stringify(bounded),before);
  }
});
