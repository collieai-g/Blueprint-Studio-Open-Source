/* Grid preferences are local to this browser, separate from company data. */
'use strict';
((root) => {
  const KEY = 'blueprint-workspace-v2', BACKUP_KEY = 'blueprint-workspace-v2.2-backup';
  const UNIT = 24, PAGE_ROWS = 48, MAX_PAGES = 3;
  const TEXT_IDS = ['heading', 'description', 'date', 'intro-title', 'intro-description'];
  const LABELS = { heading: '工作台標題', description: '工作台副標題', date: '日期', 'intro-title': '介紹標題', 'intro-description': '介紹說明', connection: '連接 AI', files: '文件庫', links: '資料收藏', youtube: 'YouTube 學習', unfiled: '待整理', workdays: '最近工作', todos: '待辦事項', recent: '最近更新' };
  const DEFAULTS = { heading: [0,0,16,3], description: [0,3,24,1], date: [32,1,11,2],
    'intro-title': [0,6,27,2], 'intro-description': [0,8,27,2], connection: [29,6,14,5],
    files: [0,13,10,7], links: [11,13,10,7], youtube: [22,13,10,7], unfiled: [33,13,10,7],
    workdays: [0,21,21,10], todos: [22,21,21,10], recent: [0,32,43,14] };
  const IDS = Object.keys(DEFAULTS);
  const isText = id => TEXT_IDS.includes(id);
  const minimum = id => isText(id) ? { w: 1, h: 1 } : { w: 6, h: id === 'recent' ? 6 : 4 };
  const integer = (v, fallback, min, max) => Number.isInteger(v) ? Math.max(min, Math.min(max, v)) : fallback;
  function normalize(value) {
    const valid = value?.version === 1 && value.cards && typeof value.cards === 'object';
    const legacy = valid && value.layoutVersion !== 3;
    const source = valid ? value.cards : {};
    let pages = integer(value?.pages, 1, 1, MAX_PAGES);
    const cards = {};
    IDS.forEach(id => {
      const [x,y,w,h] = DEFAULTS[id], s = source[id] || {}, min = minimum(id);
      const card = { x: integer(s.x, x, 0, 95), y: integer(s.y, y, 0, PAGE_ROWS * MAX_PAGES - 1),
        w: integer(s.w, w, min.w, 96), h: integer(s.h, h, min.h, PAGE_ROWS), collapsed: !isText(id) && s.collapsed === true };
      if (card.w * card.h < 2) card.w = 2;
      if (legacy && source[id]) {
        card.y = Math.min(PAGE_ROWS * MAX_PAGES - card.h, card.y + 13);
        pages = Math.min(MAX_PAGES, Math.max(pages, Math.ceil((card.y + card.h) / PAGE_ROWS)));
      }
      cards[id] = card;
    });
    return { version: 1, layoutVersion: 3, mode: value?.mode === 'manual' ? 'manual' : 'ai',
      pages, columns: integer(value?.columns, 43, 6, 96), cards };
  }
  const overlaps = (a,b) => a.x < b.x+b.w && a.x+a.w > b.x && a.y < b.y+b.h && a.y+a.h > b.y;
  function findSpace(rect, placed, cols, pages) {
    const fits = r => !placed.some(other => overlaps(r, other));
    const wanted = { ...rect, x: Math.min(cols-rect.w, Math.max(0,rect.x)), y: Math.min(pages*PAGE_ROWS-rect.h,Math.max(0,rect.y)) };
    wanted.y = Math.floor(wanted.y/PAGE_ROWS)*PAGE_ROWS + Math.min(wanted.y%PAGE_ROWS, PAGE_ROWS-wanted.h);
    if (fits(wanted)) return wanted;
    let best = null, distance = Infinity;
    for (let y=0; y<=pages*PAGE_ROWS-rect.h; y++) {
      if (y%PAGE_ROWS+rect.h>PAGE_ROWS) continue;
      for (let x=0; x<=cols-rect.w; x++) {
        const d = Math.abs(y-wanted.y)+Math.abs(x-wanted.x);
        if (d >= distance) continue;
        const r = { x,y,w:rect.w,h:rect.h };
        if (fits(r)) { best=r; distance=d; }
      }
    }
    return best;
  }
  function arrange(value, columns, priority) {
    const cols = integer(Math.floor(columns), 6, 6, 96), placed=[], result={};
    const ids = IDS.includes(priority) ? [priority,...IDS.filter(id=>id!==priority)] : IDS;
    const responsive = cols < value.columns;
    for (const id of ids) {
      const s=value.cards[id], min=minimum(id);
      let w=Math.min(cols,s.w), h=s.collapsed ? min.h : s.h;
      if (responsive) {
        w=Math.min(cols,Math.max(min.w,Math.round(s.w*cols/value.columns)));
        h=Math.max(min.h, Math.min(h, Math.ceil(h*Math.max(.5,cols/value.columns))));
        if (w*h<2) w=2;
      }
      const rect=findSpace({ x:responsive?Math.round(s.x*cols/value.columns):s.x,y:s.y,w,h },placed,cols,value.pages);
      if (!rect) return null; // A crowded edit is rejected, never hides or deletes a module.
      placed.push(rect);result[id]=rect;
    }
    return result;
  }
  function fit(value, columns) {
    if (columns < 24 && columns < value.columns) {
      // A narrow viewport gets a calm reading order; saved desktop geometry stays intact.
      const mobile=normalize(value), half=Math.floor((columns-1)/2), paired=columns>=13;
      const positions={heading:[0,0,Math.max(2,columns-5),2],date:[columns-4,0,4,2],
        description:[0,3,columns,1], 'intro-title':[0,5,columns,2], 'intro-description':[0,7,columns,2],
        connection:[0,9,columns,4],workdays:[0,paired?26:34,columns,paired?5:4],
        todos:[0,paired?32:38,columns,paired?5:4],recent:[0,paired?38:42,columns,paired?10:6]};
      ['files','links','youtube','unfiled'].forEach((id,i)=>{
        positions[id]=paired?[i%2*(half+1),14+Math.floor(i/2)*6,half,5]:[0,14+i*5,columns,4];
      });
      IDS.forEach(id=>{
        const [x,y,w,h]=positions[id],saved=value.cards[id],page=Math.min(value.pages-1,Math.floor(saved.y/PAGE_ROWS));
        const tiny=isText(id)&&saved.w<=4;
        Object.assign(mobile.cards[id],{x,y:page*PAGE_ROWS+y,w:tiny?saved.w:w,h:tiny?saved.h:h});
      });
      mobile.columns=columns;
      const layout=arrange(mobile,columns);if(layout)return layout;
    }
    const normal=arrange(value,columns); if(normal) return normal;
    // Responsive/legacy fallback: reduce only the display, never saved preferences.
    const compact=normalize(value);
    IDS.forEach(id=> { const min=minimum(id); Object.assign(compact.cards[id],{x:0,y:0,w:isText(id)?Math.min(columns,Math.max(2,compact.cards[id].w)):min.w,h:min.h}); });
    return arrange(compact,columns);
  }
  function preset(name, columns=43, mode='ai', pages=1) {
    const value=normalize({mode,pages,columns});
    if(name==='focus') {
      Object.assign(value.cards.todos,{x:0,y:13,w:27,h:17});
      Object.assign(value.cards.workdays,{x:28,y:13,w:15,h:17});
      ['files','links','youtube','unfiled'].forEach((id,i)=>Object.assign(value.cards[id],{x:i*11,y:31,w:10,h:4}));
      Object.assign(value.cards.recent,{y:36,h:10});
    } else if(name==='compact') {
      ['files','links','youtube','unfiled'].forEach(id=>Object.assign(value.cards[id],{y:12,h:4}));
      ['workdays','todos'].forEach(id=>Object.assign(value.cards[id],{y:17,h:8}));
      Object.assign(value.cards.recent,{y:26,h:12});
    }
    value.columns=43;
    const rects=fit(value,columns);
    IDS.forEach(id=>Object.assign(value.cards[id],rects[id]));value.columns=columns;
    return value;
  }
  function start({refresh,isBusy}) {
    const $=id=>document.getElementById(id), board=$('workspace-board'); if(!board) return;
    let raw=null,saved;
    try { raw=localStorage.getItem(KEY);saved=JSON.parse(raw||'null'); } catch { /* Safe defaults. */ }
    let preferences=normalize(saved),editing=false,pointer=null,rectangles={},selected='heading',syncing=false,scrollFrame;
    const widgets=new Map([...board.querySelectorAll('[data-widget]')].map(node=>[node.dataset.widget,node]));
    const status=$('workspace-sync'), columns=()=>integer(Math.floor(board.clientWidth/UNIT),6,6,96);
    const announce=text=>{$('board-announcement').textContent=text;};
    const persist=()=>{
      try {
        if(raw && saved?.layoutVersion!==3 && !localStorage.getItem(BACKUP_KEY)) localStorage.setItem(BACKUP_KEY,raw);
        localStorage.setItem(KEY,JSON.stringify(preferences));
      } catch { announce('這個瀏覽器無法保存排版。公司資料不受影響。'); }
    };
    function applyMode() { document.body.dataset.entryMode=preferences.mode;$('settings-entry-mode').value=preferences.mode; }
    // Text remains plain text outside edit mode; controls do not occupy grid cells.
    widgets.forEach((node,id)=>{
      for(const action of ['move','resize']) if(!node.querySelector(`[data-board-action="${action}"]`)) {
        const button=document.createElement('button');button.type='button';button.className=`widget-${action==='move'?'grip':'resize'}`;
        button.dataset.boardAction=action;button.textContent=action==='move'?'⠿':'⌟';
        button.setAttribute('aria-label',`${action==='move'?'移動':'調整大小'}：${LABELS[id]}。可使用方向鍵。`);node.append(button);
      }
      if(isText(id)) node.querySelector('.text-content').title=node.querySelector('.text-content').textContent;
      const option=document.createElement('option');option.value=id;option.textContent=LABELS[id];$('board-selected').append(option);
    });
    function updateDate() {
      const now=new Date(),date=$('board-date');date.dateTime=`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
      date.textContent=rectangles.date?.w<=4?`${now.getMonth()+1}/${now.getDate()}`:now.toLocaleDateString('zh-HK',{month:'long',day:'numeric',weekday:'short'});
      date.parentElement.title=now.toLocaleDateString('zh-HK',{year:'numeric',month:'long',day:'numeric',weekday:'long'});
    }
    function inspector() {
      const r=rectangles[selected];if(!r)return;
      $('board-selected').value=selected;
      $('board-width').value=r.w;$('board-width').min=minimum(selected).w;$('board-width').max=columns();
      $('board-height').value=r.h;$('board-height').min=minimum(selected).h;
      $('text-size-options').hidden=!isText(selected);
      $('board-page').replaceChildren(...Array.from({length:preferences.pages},(_,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`第 ${i+1} 頁`;return o;}));
      $('board-page').value=Math.floor(r.y/PAGE_ROWS);
    }
    function render(layout) {
      if(!board.clientWidth)return;
      rectangles=layout||fit(preferences,columns());
      widgets.forEach((node,id)=>{
        const r=rectangles[id],card=preferences.cards[id];
        node.style.gridColumn=`${r.x+1} / span ${r.w}`;node.style.gridRow=`${r.y+1} / span ${r.h}`;
        node.classList.toggle('collapsed',card.collapsed);node.classList.toggle('small-widget',r.h<=4);node.classList.toggle('short-widget',r.h<=5);
        node.classList.toggle('compact-text',isText(id)&&(r.w<=4||r.h===1));node.classList.toggle('selected',editing&&selected===id);
        const toggle=node.querySelector('[data-board-action="collapse"]');
        if(toggle){toggle.textContent=card.collapsed?'+':'−';toggle.setAttribute('aria-expanded',String(!card.collapsed));toggle.setAttribute('aria-label',`${card.collapsed?'展開':'收起'}${LABELS[id]}`);node.querySelector('.widget-content').hidden=card.collapsed;}
        const size=node.querySelector('.widget-size');if(size)size.textContent=`${r.w} × ${r.h}`;
      });
      board.style.gridTemplateColumns=`repeat(${columns()}, ${UNIT}px)`;
      board.style.height=`${preferences.pages*PAGE_ROWS*UNIT}px`;
      board.style.setProperty('--page-height',`${PAGE_ROWS*UNIT}px`);
      const main=document.querySelector('.app-main'),a=main.getBoundingClientRect(),b=board.getBoundingClientRect();
      main.style.backgroundPosition=`${b.left-a.left}px ${b.top-a.top}px`;
      $('board-page-count').textContent=`共 ${preferences.pages} 頁`;
      $('add-board-page').disabled=preferences.pages>=MAX_PAGES;
      $('board-extend-label').textContent=preferences.pages>=MAX_PAGES?'已達三頁上限。':'需要更多空間嗎？';
      updateDate();inspector();
    }
    function adopt() { IDS.forEach(id=>{const r=rectangles[id],card=preferences.cards[id];Object.assign(card,{x:r.x,y:r.y,w:r.w});if(!card.collapsed)card.h=r.h;});preferences.columns=columns(); }
    function change(id,changes) {
      const before=structuredClone(preferences);adopt();Object.assign(preferences.cards[id],changes);
      preferences=normalize(preferences);const layout=arrange(preferences,columns(),id);
      if(!layout){preferences=before;announce('這裏的空間不足。請縮小項目，或在底部增加一頁。');render();return false;}
      render(layout);adopt();persist();render(layout);announce('排版已保存。');return true;
    }
    function select(id){selected=id;widgets.forEach((node,key)=>node.classList.toggle('selected',editing&&key===id));inspector();}
    $('edit-board').addEventListener('click',event=>{
      if(pointer)endPointer(true);
      editing=!editing;board.classList.toggle('editing',editing);$('board-editor').hidden=!editing;$('reset-board').hidden=!editing;
      event.currentTarget.setAttribute('aria-pressed',String(editing));event.currentTarget.textContent=editing?'完成調整':'調整排版';render();
    });
    $('reset-board').addEventListener('click',()=>{preferences=preset('balanced',columns(),preferences.mode,preferences.pages);persist();render();announce('已還原排版。頁數和公司資料保持不變。');});
    $('apply-board-preset').addEventListener('click',()=>{preferences=preset($('board-preset').value,columns(),preferences.mode,preferences.pages);persist();render();announce('已套用排版。公司資料保持不變。');});
    $('board-selected').addEventListener('change',event=>select(event.target.value));
    $('apply-board-size').addEventListener('click',()=>{
      if(!$('board-width').reportValidity()||!$('board-height').reportValidity())return;
      change(selected,{w:Number($('board-width').value),h:Number($('board-height').value),collapsed:false});
    });
    document.querySelectorAll('[data-text-size]').forEach(button=>button.addEventListener('click',()=>{if(isText(selected))change(selected,{w:2,h:button.dataset.textSize==='2'?1:2});}));
    $('board-page').addEventListener('change',event=>change(selected,{y:Number(event.target.value)*PAGE_ROWS+rectangles[selected].y%PAGE_ROWS}));
    $('add-board-page').addEventListener('click',()=>{
      if(preferences.pages>=MAX_PAGES)return;preferences.pages++;persist();render();announce(`已增加第 ${preferences.pages} 頁。`);
    });
    $('settings-entry-mode').addEventListener('change',event=>{preferences.mode=event.target.value==='manual'?'manual':'ai';applyMode();persist();});
    board.addEventListener('click',event=>{
      const node=event.target.closest('[data-widget]');if(!node)return;const id=node.dataset.widget;if(editing)select(id);
      const action=event.target.closest('[data-board-action]')?.dataset.boardAction;
      if(action==='collapse')change(id,{collapsed:!preferences.cards[id].collapsed});
      if(editing&&['smaller','larger'].includes(action)){const n=action==='larger'?2:-2;change(id,{w:rectangles[id].w+n,h:rectangles[id].h+n,collapsed:false});}
    });
    function preview(clientX,clientY) {
      if(!pointer)return;
      const dx=Math.round((clientX-pointer.x)/UNIT),dy=Math.round((clientY+window.scrollY-pointer.y)/UNIT),r=pointer.rect;
      preferences=structuredClone(pointer.base);const card=preferences.cards[pointer.id],min=minimum(pointer.id);
      if(pointer.action==='resize')Object.assign(card,{w:Math.max(min.w,Math.min(columns(),r.w+dx)),h:Math.max(min.h,Math.min(PAGE_ROWS,r.h+dy)),collapsed:false});
      else Object.assign(card,{x:Math.max(0,Math.min(columns()-r.w,r.x+dx)),y:Math.max(0,Math.min(preferences.pages*PAGE_ROWS-r.h,r.y+dy))});
      if(card.w*card.h<2)card.w=2;
      const layout=arrange(preferences,columns(),pointer.id);
      if(layout){pointer.last=structuredClone(preferences);pointer.layout=layout;render(layout);}
      else preferences=structuredClone(pointer.last);
    }
    function autoscroll(){
      if(!pointer)return;
      const y=pointer.clientY,delta=y<70?-12:y>window.innerHeight-70?12:0;
      if(delta){window.scrollBy(0,delta);preview(pointer.clientX,y);}
      scrollFrame=requestAnimationFrame(autoscroll);
    }
    board.addEventListener('pointerdown',event=>{
      const control=event.target.closest('[data-board-action]');
      if(!editing||pointer||event.button!==0||!control||!['move','resize'].includes(control.dataset.boardAction))return;
      const id=control.closest('[data-widget]').dataset.widget;select(id);
      const before=structuredClone(preferences);adopt();
      pointer={id,control,action:control.dataset.boardAction,x:event.clientX,y:event.clientY+window.scrollY,clientX:event.clientX,clientY:event.clientY,before,base:structuredClone(preferences),last:structuredClone(preferences),layout:rectangles,rect:{...rectangles[id]}};
      control.setPointerCapture(event.pointerId);event.preventDefault();board.classList.add('adjusting');scrollFrame=requestAnimationFrame(autoscroll);
    });
    board.addEventListener('pointermove',event=>{if(!pointer)return;pointer.clientX=event.clientX;pointer.clientY=event.clientY;preview(event.clientX,event.clientY);});
    function endPointer(cancel){
      if(!pointer)return;cancelAnimationFrame(scrollFrame);
      const done=pointer;pointer=null;board.classList.remove('adjusting');
      preferences=cancel?done.before:done.last;
      if(cancel)render();else{render(done.layout);adopt();persist();render(done.layout);announce('排版已保存。');}
    }
    board.addEventListener('pointerup',()=>endPointer(false));board.addEventListener('pointercancel',()=>endPointer(true));board.addEventListener('lostpointercapture',()=>endPointer(true));
    document.addEventListener('keydown',event=>{if(event.key==='Escape'&&pointer){event.preventDefault();endPointer(true);}});
    board.addEventListener('keydown',event=>{
      const control=event.target.closest('[data-board-action]');
      if(!editing||!control||!['move','resize'].includes(control.dataset.boardAction)||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const id=control.closest('[data-widget]').dataset.widget,r=rectangles[id];select(id);
      const dx=event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:0,dy=event.key==='ArrowUp'?-1:event.key==='ArrowDown'?1:0;
      change(id,control.dataset.boardAction==='resize'?{w:r.w+dx,h:r.h+dy,collapsed:false}:{x:r.x+dx,y:r.y+dy});
    });
    async function sync(){
      if(syncing||pointer||document.hidden||document.querySelector('dialog[open]')||isBusy()||editing){status.textContent='請先完成編輯，再按更新資料';return;}
      syncing=true;
      try{if(await refresh()===false)return;status.textContent=`資料已更新 · ${new Date().toLocaleTimeString('zh-HK',{hour:'2-digit',minute:'2-digit'})}`;status.dataset.state='ready';updateDate();}
      catch{status.textContent='暫時無法更新，請再按更新資料';status.dataset.state='error';}finally{syncing=false;}
    }
    // Reading, returning to the tab and restoring browser history never replace the page.
    $('refresh-workspace').addEventListener('click',sync);
    window.addEventListener('pagehide',()=>{if(pointer)endPointer(true);});
    new ResizeObserver(()=>{if(pointer)endPointer(true);render();}).observe(board);
    applyMode();render();status.textContent='資料已載入 · 按更新資料查看最新內容';status.dataset.state='ready';
  }
  const api={normalize,arrange,fit,preset,overlaps,start,KEY,BACKUP_KEY,IDS,TEXT_IDS,UNIT,PAGE_ROWS,MAX_PAGES,minimum};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.BlueprintWorkspace=api;
})(globalThis);
