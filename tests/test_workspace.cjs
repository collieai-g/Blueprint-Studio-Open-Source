'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {normalize,arrange,fit,preset,overlaps,IDS,TEXT_IDS,PAGE_ROWS,MAX_PAGES}=require('../library_web/workspace.js');
function check(layout,cols,pages){
  assert.ok(layout);assert.equal(Object.keys(layout).length,IDS.length);
  const rects=Object.values(layout);
  rects.forEach((r,i)=>{
    assert.ok(Object.values(r).every(Number.isInteger));assert.ok(r.x>=0&&r.x+r.w<=cols);
    assert.ok(r.y>=0&&r.y+r.h<=pages*PAGE_ROWS);assert.ok(r.y%PAGE_ROWS+r.h<=PAGE_ROWS);
    assert.ok(r.w*r.h>=2);
    rects.slice(i+1).forEach(other=>assert.equal(overlaps(r,other),false));
  });
}
test('old card layout migrates once; mode, expanded size and old objects survive',()=>{
  const old={version:1,mode:'manual',cards:{files:{x:5,y:4,w:12,h:7,collapsed:true},todos:{x:0,y:80,w:18,h:20}}};
  const original=JSON.stringify(old),v=normalize(old);
  assert.equal(JSON.stringify(old),original);assert.equal(v.mode,'manual');assert.equal(v.cards.files.y,17);
  assert.equal(v.cards.files.h,7);assert.equal(v.cards.files.collapsed,true);assert.equal(v.pages,3);
  assert.deepEqual(normalize(JSON.parse(JSON.stringify(v))),v);check(fit(v,43),43,3);
});
test('corrupt geometry is bounded, unknown modules ignored and every module retained',()=>{
  const v=normalize({version:1,layoutVersion:3,mode:'bad',pages:999,columns:Infinity,cards:{heading:{w:1,h:1,x:-5},files:{w:1e6,h:-4,y:Infinity},unknown:{w:1}}});
  assert.equal(v.mode,'ai');assert.equal(v.pages,MAX_PAGES);assert.equal(v.cards.heading.w,2);assert.equal(v.cards.files.w,96);assert.equal(v.cards.files.h,4);
  assert.equal(Object.keys(v.cards).length,IDS.length);assert.deepEqual(normalize(null),normalize({version:99}));
  for(const cols of [6,13,43,96])check(fit(v,cols),cols,3);
});
test('two- and four-cell text has exact geometry, including a vertical two-cell label',()=>{
  for(const id of TEXT_IDS)for(const [w,h] of [[2,1],[1,2],[2,2]]){
    const v=normalize(null);v.cards[id]={x:0,y:0,w,h,collapsed:false};const r=arrange(v,43,id);
    check(r,43,1);assert.equal(r[id].w,w);assert.equal(r[id].h,h);
  }
});
test('responsive layouts and all presets fit without changing saved preferences',()=>{
  const v=normalize(null),before=JSON.stringify(v);
  for(const cols of [6,7,13,20,30,43,48,96]){
    check(fit(v,cols),cols,1);
    for(const name of ['balanced','focus','compact'])check(arrange(preset(name,cols),cols),cols,1);
  }
  assert.equal(JSON.stringify(v),before);
});
test('crowded edits are rejected; fitting never silently adds pages or crosses page edges',()=>{
  const v=normalize(null);Object.assign(v.cards.files,{x:0,y:0,w:43,h:48});
  assert.equal(arrange(v,43,'files'),null);assert.equal(v.pages,1);check(fit(v,43),43,1);
  v.pages=3;Object.assign(v.cards.heading,{x:0,y:47,w:2,h:2});
  const layout=arrange(v,43,'heading');check(layout,43,3);assert.equal(layout.heading.y,46);
});
test('collapse keeps expanded height for reload and moves preserve requested page',()=>{
  const v=normalize({pages:3});v.cards.todos={x:0,y:96,w:12,h:20,collapsed:true};
  const r=arrange(v,43,'todos');assert.equal(r.todos.y,96);assert.equal(r.todos.h,4);check(r,43,3);
  const reload=normalize(JSON.parse(JSON.stringify(v)));reload.cards.todos.collapsed=false;
  assert.equal(arrange(reload,43,'todos').todos.h,20);
});

test('bounded fallback retains every module for fragmented imported layouts',()=>{
  let seed=20261021;
  const next=max=>{seed=(seed*1664525+1013904223)>>>0;return seed%max;};
  for(let n=0;n<100;n++){
    const v=normalize(null);
    IDS.forEach(id=>Object.assign(v.cards[id],{x:next(96),y:next(144),w:1+next(96),h:1+next(48)}));
    const bounded=normalize(v),before=JSON.stringify(bounded);
    for(const cols of [6,13,43])check(fit(bounded,cols),cols,1);
    assert.equal(JSON.stringify(bounded),before);
  }
});
