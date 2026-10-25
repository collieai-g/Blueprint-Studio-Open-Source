/* Per-page presentation preferences. Existing records and overview storage stay separate. */
'use strict';
((root)=>{
  const KEY='blueprint-page-layouts-v1',BACKUP_KEY=KEY+'-backup',UNIT=24,PAGE_ROWS=48,MAX_PAGES=3;
  const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
  const clamp=(value,fallback,min,max)=>Number.isInteger(value)?Math.max(min,Math.min(max,value)):fallback;
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const safeId=value=>typeof value==='string'&&/^[A-Za-z0-9][A-Za-z0-9:_.\/-]{0,239}$/.test(value)&&!['__proto__','prototype','constructor'].includes(value);
  const clone=value=>JSON.parse(JSON.stringify(value));
  function specs(items){
    if(!Array.isArray(items)||items.length>200)throw new Error('頁面項目超出可調整範圍。');
    const found=new Set();
    return items.map(item=>{
      if(!object(item)||!safeId(item.id)||found.has(item.id))throw new Error('頁面項目需要不同的有效識別碼。');
      found.add(item.id);const text=item.type==='text';
      const minimum={w:clamp(item.min?.w,text?2:8,1,96),h:clamp(item.min?.h,text?1:4,1,PAGE_ROWS)};
      if(text&&minimum.w*minimum.h<2)minimum.w=2;
      const maximum={w:clamp(item.max?.w,96,minimum.w,96),h:clamp(item.max?.h,text?12:PAGE_ROWS,minimum.h,PAGE_ROWS)};
      return {...item,type:text?'text':'card',label:String(item.label||item.id).slice(0,200),min:minimum,max:maximum};
    });
  }
  function normalize(raw,items,columns=43){
    const catalog=specs(items),source=object(raw)?raw:{},cards={};
    const saved=object(source.cards)?source.cards:{};
    let nextX=0,nextY=0,rowHeight=0;
    for(const item of catalog){
      const value=object(saved[item.id])?saved[item.id]:{},text=item.type==='text';
      const width=Math.min(columns,clamp(item.defaultSize?.w,text?columns:20,item.min.w,item.max.w));
      const height=clamp(item.defaultSize?.h,text?2:10,item.min.h,item.max.h);
      if(text||nextX+width>columns){if(nextX)nextY+=rowHeight+1;nextX=0;rowHeight=0;}
      const card={x:clamp(value.x,nextX,0,95),y:clamp(value.y,nextY,0,PAGE_ROWS*MAX_PAGES-1),
        w:clamp(value.w,width,item.min.w,item.max.w),h:clamp(value.h,height,item.min.h,item.max.h),
        visible:item.required===true||(typeof value.visible==='boolean'?value.visible:item.visible!==false),
        collapsed:item.type==='card'&&value.collapsed===true};
      if(typeof value.text==='string')card.text=value.text.slice(0,4000);
      cards[item.id]=card;nextX+=width+1;rowHeight=Math.max(rowHeight,height);
      if(text){nextY+=height+1;nextX=0;rowHeight=0;}
    }
    return {version:1,active:source.active===true,pages:clamp(source.pages,1,1,MAX_PAGES),
      columns:clamp(source.columns,columns,6,96),cards};
  }
  const overlaps=(a,b)=>a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y;
  function space(rect,placed,columns,pages){
    const rows=pages*PAGE_ROWS;
    if(rect.w>columns||rect.h>PAGE_ROWS)return null;
    const desired={...rect,x:Math.max(0,Math.min(columns-rect.w,rect.x)),y:Math.max(0,Math.min(rows-rect.h,rect.y))};
    desired.y=Math.floor(desired.y/PAGE_ROWS)*PAGE_ROWS+Math.min(desired.y%PAGE_ROWS,PAGE_ROWS-desired.h);
    const free=value=>!placed.some(other=>overlaps(value,other));
    if(free(desired))return desired;
    // Exact cell alignment; moving one item places remaining items into free cells.
    for(let y=0;y<=rows-rect.h;y++){
      if(y%PAGE_ROWS+rect.h>PAGE_ROWS)continue;
      for(let x=0;x<=columns-rect.w;x++){const candidate={...rect,x,y};if(free(candidate))return candidate;}
    }
    return null;
  }
  function arrange(value,items,columns=value.columns,priority){
    const catalog=specs(items),cols=clamp(Math.floor(columns),43,6,96),placed=[],layout={};
    const visible=catalog.filter(item=>value.cards[item.id]?.visible);
    if(priority){const index=visible.findIndex(item=>item.id===priority);if(index>0)visible.unshift(...visible.splice(index,1));}
    for(const item of visible){
      const saved=value.cards[item.id],rect=space({x:saved.x,y:saved.y,w:Math.min(cols,saved.w),h:saved.collapsed?item.min.h:saved.h},placed,cols,value.pages);
      if(!rect)return null;placed.push(rect);layout[item.id]=rect;
    }
    return layout;
  }
  function fit(value,items,columns){
    const cols=clamp(Math.floor(columns),43,6,96);
    if(cols>=value.columns)return arrange(value,items,cols);
    const display=clone(value),catalog=specs(items);
    if(cols<24){
      let y=0;
      for(const item of catalog){const card=display.cards[item.id];if(!card?.visible)continue;
        const h=item.type==='text'?Math.max(item.min.h,Math.min(card.h,3)):Math.max(item.min.h,Math.min(card.h,12));
        if(y%PAGE_ROWS+h>PAGE_ROWS)y=Math.ceil(y/PAGE_ROWS)*PAGE_ROWS;
        Object.assign(card,{x:0,y,w:cols,h});y+=h+1;
      }
    }else for(const item of catalog){const card=display.cards[item.id];
      Object.assign(card,{x:Math.round(card.x*cols/value.columns),w:Math.min(cols,Math.max(item.min.w,Math.round(card.w*cols/value.columns)))});
    }
    let layout=arrange(display,catalog,cols);
    if(layout)return layout;
    // A dense mobile view can use minimum sizes without writing desktop geometry.
    for(const item of catalog)Object.assign(display.cards[item.id],{x:0,y:0,w:Math.min(cols,item.type==='text'?cols:item.min.w),h:item.min.h});
    return arrange(display,catalog,cols);
  }
  function decode(raw){
    try{const value=JSON.parse(typeof raw==='string'&&raw.length<=2*1024*1024?raw:'null');
      return object(value)&&value.version===1&&object(value.pages)?value:{version:1,pages:{}};
    }catch{return {version:1,pages:{}};}
  }
  function storePage(document,route,preferences,scope=''){
    if(typeof route!=='string'||!route||route.length>300||['__proto__','constructor','prototype'].includes(route))throw new Error('頁面識別碼無效。');
    const original=object(document)?document:{version:1,pages:{}};
    if(scope){
      if(typeof scope!=='string'||scope.length>500||['__proto__','prototype','constructor'].includes(scope))throw new Error('版面身份識別碼無效。');
      const scopes=object(original.scopes)?original.scopes:{},current=own(scopes,scope)&&object(scopes[scope])?scopes[scope]:{};
      const scoped=storePage(current,route,preferences);
      return {...original,version:1,pages:object(original.pages)?original.pages:{},scopes:{...scopes,[scope]:scoped}};
    }
    const pages=object(original.pages)?original.pages:{};
    const prior=own(pages,route)&&object(pages[route])?pages[route]:{};
    return {...original,version:1,pages:{...pages,[route]:{...prior,...preferences,cards:{...(object(prior.cards)?prior.cards:{}),...preferences.cards}}}};
  }
  // Copy only presentation fields. A page can retain cards which are not mounted
  // in the current filter, without copying records or old browser-only metadata.
  function presentationData(value){
    const source=object(value)?value:{},result={};
    if(source.version===1)result.version=1;
    if(typeof source.active==='boolean')result.active=source.active;
    if(Number.isInteger(source.pages)&&source.pages>=1&&source.pages<=MAX_PAGES)result.pages=source.pages;
    if(Number.isInteger(source.columns)&&source.columns>=6&&source.columns<=96)result.columns=source.columns;
    if(object(source.cards)){
      result.cards={};
      for(const [id,card]of Object.entries(source.cards).slice(0,200)){
        if(!safeId(id)||!object(card))continue;
        const copy={};
        for(const [field,min,max]of [['x',0,95],['y',0,PAGE_ROWS*MAX_PAGES-1],['w',1,96],['h',1,PAGE_ROWS]]){
          if(Number.isInteger(card[field])&&card[field]>=min&&card[field]<=max)copy[field]=card[field];
        }
        for(const field of ['visible','collapsed'])if(typeof card[field]==='boolean')copy[field]=card[field];
        if(typeof card.text==='string'&&card.text.length<=4000&&!card.text.includes('\x00'))copy.text=card.text;
        result.cards[id]=copy;
      }
    }
    return result;
  }
  function editPresentation(previous,before,next){
    if(!object(previous))return presentationData(next);
    const result=presentationData(previous),same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
    for(const field of ['version','active','pages','columns'])if(!same(before[field],next[field])){
      if(own(next,field))result[field]=next[field];else delete result[field];
    }
    for(const id of new Set([...Object.keys(before.cards||{}),...Object.keys(next.cards||{})])){
      if(!safeId(id))continue;
      const old=before.cards?.[id]||{},current=next.cards?.[id]||{};
      for(const field of ['x','y','w','h','visible','collapsed','text'])if(!same(old[field],current[field])){
        result.cards??={};result.cards[id]??={};
        if(own(current,field))result.cards[id][field]=current[field];else delete result.cards[id][field];
      }
    }
    return result;
  }
  function replacePage(document,route,preferences,scope=''){
    const result=storePage(document,route,preferences,scope),target=scope?result.scopes[scope]:result;
    target.pages[route]=clone(preferences);return result;
  }
  const active=new WeakMap();
  function mount(options){
    const {route,container,toolbarContainer,onPin,isPinned,scope='',sync}=options;
    if(!container||typeof document==='undefined')return null;
    active.get(container)?.destroy();
    const catalog=specs(options.items||[]).filter(item=>item.node?.nodeType===1);
    if(!catalog.length)return null;
    // Each node has one layout owner; nested containers remain native controls.
    for(const item of catalog)if(catalog.some(other=>other!==item&&other.node.contains(item.node)))throw new Error('調整項目不能同時包含另一個調整項目。');
    const element=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;};
    const button=(label,run,cls='button secondary small')=>{const control=element('button',cls,label);control.type='button';control.addEventListener('click',()=>Promise.resolve().then(run).catch(error=>say(error.message)));return control;};
    let alive=true,editing=false,pointer=null,wrapped=false,rectangles={},selected=catalog[0].id,raw=null,saved,width=0,resizeObserver,unsubscribe,pendingRemote=false;
    try{raw=localStorage.getItem(KEY);}catch{}
    saved=decode(raw);const scoped=scope&&object(saved.scopes)&&own(saved.scopes,scope)&&object(saved.scopes[scope])?saved.scopes[scope]:scope?{pages:{}}:saved;
    const syncKey='page:'+route,localPage=object(scoped.pages)&&own(scoped.pages,route)&&object(scoped.pages[route])?scoped.pages[route]:undefined;
    const initial=sync?.get(syncKey);
    let documentData=object(initial)?presentationData(initial):localPage?presentationData(localPage):undefined;
    let preferences=normalize(documentData,catalog),displayBaseline=clone(preferences);
    const originals=new Map(),wrappers=new Map(),placeholders=new Map(),hiddenParents=new Map();
    const toolbar=element('div','page-layout-toolbar'),edit=button('調整版面',()=>setEditing(!editing));
    edit.dataset.pageLayoutEdit=route;edit.setAttribute('aria-pressed','false');toolbar.append(edit);
    const editor=element('section','page-layout-editor');editor.hidden=true;editor.setAttribute('aria-label','本頁版面設定');
    const board=element('div','page-layout-board');board.dataset.pageLayoutRoute=route;
    const announcement=element('p','page-layout-announcement');announcement.setAttribute('role','status');announcement.setAttribute('aria-live','polite');
    const say=text=>{if(alive)announcement.textContent=text;options.announce?.(text);};
    const pending=element('div','page-layout-announcement');pending.hidden=true;
    pending.append(element('span','','其他位置已更新版面；完成編輯後會套用。 '),button('套用新版面',()=>applyPending(true)));
    if(toolbarContainer)toolbarContainer.append(toolbar);else container.prepend(toolbar);
    container.prepend(editor);container.append(announcement,pending);
    const select=element('select'),widthInput=element('input'),heightInput=element('input'),pageSelect=element('select'),textInput=element('textarea');
    widthInput.type=heightInput.type='number';textInput.rows=3;textInput.maxLength=4000;textInput.dataset.pageLayoutText=route;
    const field=(label,control)=>{const result=element('label','page-layout-field');result.append(element('span','',label),control);return result;};
    const textField=field('顯示文字（只改本頁）',textInput),visibleFields=element('div','page-layout-visible');
    const visibility=element('fieldset');visibility.append(element('legend','','顯示的內容'),visibleFields);
    const restoreText=button('還原原文字',()=>{const item=catalog.find(item=>item.id===selected);delete preferences.cards[selected].text;applyText(item);persist();inspect();});
    textField.append(restoreText);
    const pin=button('加入總覽',()=>pinItem(catalog.find(item=>item.id===selected)));
    const sizeFields=element('div','page-layout-size-fields');
    sizeFields.append(field('調整項目',select),field('寬度（格）',widthInput),field('高度（格）',heightInput),field('所在頁',pageSelect),button('套用大小',()=>{
      if(widthInput.reportValidity()&&heightInput.reportValidity())change(selected,{w:Number(widthInput.value),h:Number(heightInput.value),collapsed:false});
    }),pin);
    const addPage=button('增加一頁',()=>{if(preferences.pages>=MAX_PAGES)return;preferences.pages++;preferences.active=true;persist();render();say('已增加一頁。');});
    const reset=button('還原本頁排列',()=>{const before=preferences,next=normalize(null,catalog,columns());next.pages=before.pages;next.active=true;
      for(const item of catalog){next.cards[item.id].visible=before.cards[item.id].visible;if(own(before.cards[item.id],'text'))next.cards[item.id].text=before.cards[item.id].text;}
      const layout=arrange(next,catalog,columns());if(!layout){say('這裏的空間不足，請先減少內容或增加一頁。');return;}
      preferences=next;render(layout);persist();say('已還原本頁排列，顯示文字與公司資料仍然保留。');});
    editor.append(element('p','muted',sync&&sync.status?.().state!=='unsupported'?'拖動或調整卡片；版面與顯示文字會同步到同一帳戶，進度可在操作列查看。':'拖動或調整卡片；此後台未提供排版同步，設定保存在目前瀏覽器。'),visibility,sizeFields,textField,addPage,reset);
    function columns(){return clamp(Math.floor((board.clientWidth||container.clientWidth||1032)/UNIT),43,6,96);}
    function cache(value){
      try{
        if(raw&&!localStorage.getItem(BACKUP_KEY))localStorage.setItem(BACKUP_KEY,raw);
        const latest=decode(localStorage.getItem(KEY));saved=(sync?replacePage:storePage)(latest,route,value,scope);localStorage.setItem(KEY,JSON.stringify(saved));
      }catch{say('這個瀏覽器無法保存版面，公司資料不受影響。');}
    }
    function persist(){
      const previous=documentData,next=editPresentation(previous,displayBaseline,preferences);
      documentData=clone(next);displayBaseline=clone(preferences);cache(next);
      if(sync)try{sync.update(syncKey,next,{base:previous});}catch(error){say(error.message);}
    }
    const drafts=new Set();
    function focusedField(){const current=document.activeElement;return !!(current&&container.contains(current)&&current.matches('input,textarea,select,[contenteditable="true"]'));}
    function busyForSync(){return editing||!!pointer||focusedField()||[...drafts].some(input=>input.isConnected)||options.isBusy?.();}
    function applyRemote(value){
      if(!alive||!object(value))return;
      documentData=presentationData(value);preferences=normalize(documentData,catalog,columns());displayBaseline=clone(preferences);
      pendingRemote=false;pending.hidden=true;cache(documentData);
      if(preferences.active)wrap();else unwrap();render();
    }
    function applyPending(force=false){
      if(!pendingRemote||!alive)return;
      if(editing||pointer){say('請先完成目前的版面調整。');return;}
      if(!force&&busyForSync())return;
      const latest=sync?.get(syncKey);if(object(latest))applyRemote(latest);
    }
    function receive(value,meta={}){
      if(!alive||meta.source==='local'||!object(value)||JSON.stringify(value)===JSON.stringify(documentData))return;
      if(busyForSync()){pendingRemote=true;pending.hidden=false;return;}
      applyRemote(value);
    }
    const rememberDraft=event=>{if(!editor.contains(event.target)&&event.target.matches('input,textarea,select,[contenteditable="true"]'))drafts.add(event.target);};
    const afterFocus=()=>Promise.resolve().then(()=>applyPending());
    container.addEventListener('input',rememberDraft);container.addEventListener('change',rememberDraft);container.addEventListener('focusout',afterFocus);
    function target(item){return item.textNode||(item.type==='text'?item.node:null);}
    function applyText(item){const text=target(item);if(!text)return;
      if(!originals.has(item.id))originals.set(item.id,text.textContent);
      text.textContent=own(preferences.cards[item.id],'text')?preferences.cards[item.id].text:originals.get(item.id);
    }
    function labelControls(item,wrapper){
      const controls=element('div','page-layout-controls'),move=button('⠿',()=>{},'page-layout-move'),resize=button('⌟',()=>{},'page-layout-resize');
      move.dataset.pageLayoutAction='move';resize.dataset.pageLayoutAction='resize';
      move.setAttribute('aria-label','移動：'+item.label+'，可使用方向鍵');resize.setAttribute('aria-label','調整大小：'+item.label+'，可使用方向鍵');
      controls.append(move,element('span','page-layout-label',item.label));
      if(item.type==='card')controls.append(button('收合／展開',()=>change(item.id,{collapsed:!preferences.cards[item.id].collapsed}),'page-layout-collapse'));
      if(item.pin&&onPin){const pinControl=button('加入總覽',()=>pinItem(item),'page-layout-pin');controls.append(pinControl);}
      wrapper.append(controls,resize);
    }
    function wrap(){
      if(wrapped)return;wrapped=true;
      container.insertBefore(board,announcement);
      for(const item of catalog){
        const placeholder=document.createComment('page-layout:'+item.id);item.node.before(placeholder);placeholders.set(item.id,placeholder);
        const parent=item.node.parentElement,wrapper=element('section','page-layout-item '+(item.type==='text'?'page-layout-text':'page-layout-card'));
        wrapper.dataset.pageLayoutId=item.id;wrapper.setAttribute('aria-label',item.label);
        const content=element('div','page-layout-content');content.append(item.node);wrapper.append(content);labelControls(item,wrapper);board.append(wrapper);wrappers.set(item.id,wrapper);
        if(parent!==container&&parent&&![...parent.children].length){hiddenParents.set(parent,parent.hidden);parent.hidden=true;}
      }
    }
    function unwrap(){
      if(!wrapped)return;
      for(const item of catalog){const placeholder=placeholders.get(item.id);if(placeholder?.parentNode)placeholder.replaceWith(item.node);}
      for(const [parent,hidden]of hiddenParents)parent.hidden=hidden;
      placeholders.clear();hiddenParents.clear();wrappers.clear();board.remove();wrapped=false;
    }
    function inspect(){
      const item=catalog.find(value=>value.id===selected)||catalog[0],card=preferences.cards[item.id],rect=rectangles[item.id]||card;selected=item.id;
      select.replaceChildren(...catalog.map(value=>{const option=element('option','',value.label);option.value=value.id;return option;}));select.value=selected;
      widthInput.min=item.min.w;widthInput.max=Math.min(columns(),item.max.w);widthInput.value=rect.w;
      heightInput.min=item.min.h;heightInput.max=item.max.h;heightInput.value=card.h;
      pageSelect.replaceChildren(...Array.from({length:preferences.pages},(_,index)=>{const option=element('option','','第 '+(index+1)+' 頁');option.value=index;return option;}));
      pageSelect.value=Math.min(preferences.pages-1,Math.floor(rect.y/PAGE_ROWS));
      textField.hidden=!target(item);if(document.activeElement!==textInput)textInput.value=own(card,'text')?card.text:originals.get(item.id)||target(item)?.textContent||'';
      pin.hidden=!item.pin||!onPin;pin.textContent=item.pin&&isPinned?.(item.pin)?'已加入總覽':'加入總覽';pin.disabled=!!(item.pin&&isPinned?.(item.pin));
      addPage.disabled=preferences.pages>=MAX_PAGES;
      for(const toggle of visibleFields.querySelectorAll('input'))toggle.checked=preferences.cards[toggle.dataset.pageLayoutVisible].visible;
    }
    function render(layout){
      if(!alive)return;
      for(const item of catalog)applyText(item);
      if(!wrapped){inspect();return;}
      rectangles=layout||fit(preferences,catalog,columns());
      if(!rectangles){board.classList.add('page-layout-flow');rectangles={};say('目前畫面空間不足，內容以順序顯示；已保存的排列不會改動。');}
      else board.classList.remove('page-layout-flow');
      const height=editing?preferences.pages*PAGE_ROWS:Math.max(0,...Object.values(rectangles).map(rect=>rect.y+rect.h));
      board.style.gridTemplateColumns='repeat('+columns()+', '+UNIT+'px)';board.style.minHeight=height*UNIT+'px';
      for(const item of catalog){const wrapper=wrappers.get(item.id),card=preferences.cards[item.id],rect=rectangles[item.id];
        wrapper.hidden=!card.visible;wrapper.classList.toggle('page-layout-selected',editing&&selected===item.id);wrapper.classList.toggle('page-layout-collapsed',card.collapsed);
        const content=wrapper.querySelector('.page-layout-content');content.hidden=card.collapsed;
        if(rect){wrapper.style.gridColumn=(rect.x+1)+' / span '+rect.w;wrapper.style.gridRow=(rect.y+1)+' / span '+rect.h;}
        else{wrapper.style.removeProperty('grid-column');wrapper.style.removeProperty('grid-row');}
      }
      inspect();
    }
    function adopt(layout=rectangles){
      for(const [id,rect] of Object.entries(layout)){Object.assign(preferences.cards[id],{x:rect.x,y:rect.y,w:rect.w});if(!preferences.cards[id].collapsed)preferences.cards[id].h=rect.h;}
      preferences.columns=columns();
    }
    function change(id,changes){
      const item=catalog.find(value=>value.id===id);if(!item)return false;
      const before=clone(preferences);adopt();Object.assign(preferences.cards[id],changes);preferences=normalize(preferences,catalog,columns());preferences.active=true;
      if(item.required)preferences.cards[id].visible=true;
      const layout=arrange(preferences,catalog,columns(),id);
      if(!layout){
        if(changes.visible===false){render();persist();say('已隱藏項目，資料仍然保留；可繼續減少內容或增加一頁。');return true;}
        preferences=before;render();say('這裏的空間不足，請縮小項目或增加一頁。');return false;
      }
      render(layout);adopt(layout);persist();say('本頁版面已保存。');return true;
    }
    function setEditing(value){
      if(!alive)return;
      if(pointer)endPointer(true);
      editing=!!value;
      if(editing){wrap();preferences.active=true;}
      editor.hidden=!editing;board.classList.toggle('page-layout-editing',editing);edit.textContent=editing?'完成調整':'調整版面';edit.setAttribute('aria-pressed',String(editing));render();
      // Entering edit mode is not a save; resizing the viewport is never a save.
      if(!editing)applyPending();
    }
    async function pinItem(item){
      if(!item?.pin||!onPin||isPinned?.(item.pin))return;
      await onPin(item.pin);if(alive){inspect();say('已加入總覽。');}
    }
    for(const item of catalog){
      applyText(item);const label=element('label'),toggle=element('input');toggle.type='checkbox';toggle.disabled=item.required===true;toggle.dataset.pageLayoutVisible=item.id;
      toggle.addEventListener('change',()=>change(item.id,{visible:toggle.checked}));label.append(toggle,document.createTextNode(item.label));visibleFields.append(label);
    }
    select.addEventListener('change',()=>{selected=select.value;render();});
    pageSelect.addEventListener('change',()=>{const card=rectangles[selected]||preferences.cards[selected];change(selected,{y:Number(pageSelect.value)*PAGE_ROWS+card.y%PAGE_ROWS});});
    textInput.addEventListener('input',()=>{preferences.cards[selected].text=textInput.value;applyText(catalog.find(item=>item.id===selected));persist();});
    board.addEventListener('click',event=>{const wrapper=event.target.closest('[data-page-layout-id]');if(editing&&wrapper){selected=wrapper.dataset.pageLayoutId;inspect();for(const [id,value]of wrappers)value.classList.toggle('page-layout-selected',id===selected);}});
    function preview(event){
      if(!pointer)return;
      const dx=Math.round((event.clientX-pointer.x)/UNIT),dy=Math.round((event.clientY+root.scrollY-pointer.y)/UNIT),item=catalog.find(item=>item.id===pointer.id);
      preferences=clone(pointer.base);const card=preferences.cards[item.id],rect=pointer.rect;
      if(pointer.action==='resize')Object.assign(card,{w:clamp(rect.w+dx,rect.w,item.min.w,Math.min(columns(),item.max.w)),h:clamp(rect.h+dy,rect.h,item.min.h,item.max.h),collapsed:false});
      else Object.assign(card,{x:Math.max(0,Math.min(columns()-rect.w,rect.x+dx)),y:Math.max(0,Math.min(preferences.pages*PAGE_ROWS-rect.h,rect.y+dy))});
      const layout=arrange(preferences,catalog,columns(),pointer.id);
      if(layout){pointer.last=clone(preferences);pointer.layout=layout;render(layout);}else preferences=clone(pointer.last);
    }
    function endPointer(cancel){if(!pointer)return;const done=pointer;pointer=null;preferences=cancel?done.before:done.last;board.classList.remove('page-layout-adjusting');
      if(cancel)render();else{render(done.layout);adopt(done.layout);persist();say('本頁版面已保存。');}}
    board.addEventListener('pointerdown',event=>{
      const control=event.target.closest('[data-page-layout-action]');if(!editing||pointer||event.button!==0||!control)return;
      const id=control.closest('[data-page-layout-id]').dataset.pageLayoutId,rect=rectangles[id];if(!rect)return;
      selected=id;const before=clone(preferences);adopt();pointer={id,action:control.dataset.pageLayoutAction,x:event.clientX,y:event.clientY+root.scrollY,before,base:clone(preferences),last:clone(preferences),layout:rectangles,rect:{...rect}};
      control.setPointerCapture(event.pointerId);event.preventDefault();board.classList.add('page-layout-adjusting');inspect();
    });
    board.addEventListener('pointermove',preview);board.addEventListener('pointerup',()=>endPointer(false));board.addEventListener('pointercancel',()=>endPointer(true));board.addEventListener('lostpointercapture',()=>endPointer(true));
    const escape=event=>{if(event.key==='Escape'&&pointer){event.preventDefault();endPointer(true);}};document.addEventListener('keydown',escape);
    board.addEventListener('keydown',event=>{
      const control=event.target.closest('[data-page-layout-action]');if(!editing||!control||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();const id=control.closest('[data-page-layout-id]').dataset.pageLayoutId,rect=rectangles[id];if(!rect)return;selected=id;
      const dx=event.key==='ArrowLeft'?-1:event.key==='ArrowRight'?1:0,dy=event.key==='ArrowUp'?-1:event.key==='ArrowDown'?1:0;
      change(id,control.dataset.pageLayoutAction==='resize'?{w:rect.w+dx,h:rect.h+dy,collapsed:false}:{x:rect.x+dx,y:rect.y+dy});
    });
    const resize=()=>{if(!alive)return;const next=container.clientWidth;if(next===width)return;width=next;if(pointer)endPointer(true);render();};
    if(typeof ResizeObserver!=='undefined'){resizeObserver=new ResizeObserver(resize);resizeObserver.observe(container);}else root.addEventListener('resize',resize);
    const controller={setEditing,render,isBusy:()=>alive&&(editing||!!pointer),getPreferences:()=>clone(preferences),destroy(){
      if(!alive)return;alive=false;pointer=null;unsubscribe?.();resizeObserver?.disconnect();root.removeEventListener('resize',resize);document.removeEventListener('keydown',escape);
      container.removeEventListener('input',rememberDraft);container.removeEventListener('change',rememberDraft);container.removeEventListener('focusout',afterFocus);
      for(const item of catalog){const placeholder=placeholders.get(item.id);if(placeholder?.parentNode){placeholder.replaceWith(item.node);}if(originals.has(item.id)&&target(item))target(item).textContent=originals.get(item.id);}
      for(const [parent,hidden]of hiddenParents)parent.hidden=hidden;
      toolbar.remove();editor.remove();board.remove();announcement.remove();pending.remove();if(active.get(container)===controller)active.delete(container);
    }};
    active.set(container,controller);if(preferences.active)wrap();render();
    if(sync){
      if(!object(initial)&&localPage)sync.adopt(syncKey,presentationData(localPage));
      unsubscribe=sync.subscribe(syncKey,receive);
    }
    return controller;
  }
  const api={KEY,BACKUP_KEY,UNIT,PAGE_ROWS,MAX_PAGES,specs,normalize,arrange,fit,overlaps,decode,storePage,presentationData,editPresentation,replacePage,mount};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.BlueprintPageLayout=api;
})(globalThis);
