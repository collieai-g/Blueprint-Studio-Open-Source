/* V2.4 company navigation and real record workflows, using the original visual language. */
'use strict';
((root)=>{
  const NAV=root.BlueprintNavigation, $=id=>document.getElementById(id);
  const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e;};
  const button=(label,run,cls='button secondary small')=>{const e=node('button',cls,label);e.type='button';e.addEventListener('click',()=>Promise.resolve().then(run).catch(error=>app.notice(error.message,true)));return e;};
  let app,state,route='overview',activeTab='',request=0,editing=null,actionSubmit=null,activeRecord=null,navigating=false,ready=false,initialized=false;
  let choices=[],searchText='',pageOffset=0,lastNav='',dragTopic=null;
  const pageNames={leads:'潛在客戶分析',quotes:'智能報價單與合約流水線',customers:'現有客戶檔案庫',feedback:'客戶反饋與客訴追蹤表',suppliers:'供應商與合作夥伴',revenue:'公司營收與發票',costs:'雲端伺服器與 AI 算力成本'};
  const icons={project:'checklist',inbox:'inbox',command:'automation',task:'checklist',lead:'search',quote:'document',customer:'folder',feedback:'chat',member:'settings',supplier:'link',invoice:'document',cost:'automation',specialist:'chat',conversation:'chat',scenario:'checklist',topic:'folder',inspiration:'bookmark',report:'document'};
  const admin=()=>['owner','admin'].includes(state?.identity.role);
  const api=(path,options)=>app.api('/api/studio'+path,options);
  const recordUrl=id=>'/records/'+encodeURIComponent(id);
  const catalog=(kind)=>state.catalog.filter(r=>r.kind===kind&&!r.archived);
  const schema=kind=>state.schemas[kind];
  function definition(value=route){
    if(NAV.pages[value])return NAV.pages[value];
    if(value.startsWith('specialist/'))return {...NAV.pages['specialist/specialist-website'],route:value,label:catalog('specialist').find(r=>r.id===value.split('/')[1])?.title||'AI 專員'};
    if(value.startsWith('topic/'))return {...NAV.pages.topics,route:value,label:catalog('topic').find(r=>r.id===value.split('/')[1])?.title||'自訂主題'};
    if(value.startsWith('record/')){
      const parent={project:'projects',inbox:'inbox',command:'command',task:'overview',lead:'leads',quote:'quotes',customer:'customers',feedback:'feedback',member:'team',supplier:'suppliers',invoice:'revenue',cost:'costs',integration:'connections',specialist:'specialist/'+activeRecord?.id,topic:'topics',inspiration:'inspirations',report:'documents',conversation:'specialist/'+activeRecord?.data.specialist_id,scenario:'specialist/'+activeRecord?.data.specialist_id}[activeRecord?.kind]||'inbox';
      return {...definition(parent),route:value,label:activeRecord?.title||'工作內容',parentRoute:parent};
    }
    if(value.startsWith('overview/'))return {...NAV.pages.overview,route:value};
    return {route:value,label:{archived:'已封存',pinned:'釘選',account:'帳戶'}[value]||'工作內容',description:'',group:''};
  }
  function showDialog(id){app.openDialog(id);}
  function closeDialog(id){$(id).close();}
  function errorAt(id,error){$(id).hidden=!error;$(id).textContent=error?.message||'';}
  async function savePrefs(changes){
    const result=await api('/preferences',{method:'PATCH',body:{expected_version:state.preferences.version,data:changes}});
    state.preferences=result;renderNavigation(true);return result;
  }
  function routeButton(value,label,icon){
    const e=button('',()=>navigate(value),'nav-item');e.dataset.studioRoute=value;e.title=label;e.append(app.icon(icon||'folder'),node('span','studio-nav-label',label));
    if(value==='overview')e.dataset.view='dashboard';if(value==='documents')e.dataset.view='files';
    e.classList.toggle('active',value===route||value===definition().parentRoute||(value==='overview'&&route.startsWith('overview/')));
    if(e.classList.contains('active'))e.setAttribute('aria-current','page');return e;
  }
  function renderNavigation(force=false){
    const preferences=state.preferences.data;
    document.body.classList.toggle('studio-narrow',!!preferences.narrow);
    const signature=JSON.stringify([route,state.catalog.map(r=>[r.id,r.version,r.title,r.visibility]),preferences,state.summary.counts]);
    if(!force&&lastNav===signature)return;lastNav=signature;
    const holder=$('studio-navigation');holder.replaceChildren();
    NAV.groups.forEach(group=>{
      if(preferences.hidden_groups?.includes(group.id)&&definition().group!==group.id)return;
      const section=node('section','studio-nav-group'),toggle=button('',()=>savePrefs({groups:preferences.groups.includes(group.id)?preferences.groups.filter(g=>g!==group.id):[...preferences.groups,group.id]}),'studio-group-button');
      toggle.dataset.group=group.id;toggle.title=group.label;toggle.setAttribute('aria-expanded',String(preferences.groups.includes(group.id)));
      toggle.append(app.icon(group.icon),node('span','studio-nav-label',group.label),node('span','studio-group-arrow',preferences.groups.includes(group.id)?'−':'+'));section.append(toggle);
      const children=node('div','studio-nav-children');children.hidden=!preferences.groups.includes(group.id);
      const items=[...group.items];
      if(group.id==='specialists'){
        const extra=catalog('specialist').filter(r=>!['specialist-website','specialist-whatsapp','specialist-email','specialist-video'].includes(r.id));
        items.splice(4,0,...extra.map(r=>['specialist/'+r.id,r.title,'chat','專員設定','specialist']));
      }
      if(group.id==='topics'){
        const topics=catalog('topic').sort((a,b)=>(a.data.order||0)-(b.data.order||0)||a.created_at.localeCompare(b.created_at));
        items.unshift(...topics.slice(0,5).map(r=>['topic/'+r.id,r.title,r.data.icon||'folder','主題內容','topic']));
      }
      items.forEach(entry=>{
        if(!Array.isArray(entry)){if(entry.access!=='admin'||admin())children.append(node('p','studio-nav-heading',entry.heading));return;}
        const [value,label,icon,,kind,access]=entry;if(access==='admin'&&!admin())return;
        if(value==='templates'&&!admin())return;
        const e=routeButton(value,label,icon);
        if(kind==='specialist'&&value.startsWith('specialist/')){
          const specialist=catalog('specialist').find(r=>r.id===value.split('/')[1]);
          const status=specialist?.data.state||'unconnected',dot=node('span','specialist-dot');dot.dataset.state=status;
          dot.title=statusLabel('specialist','state',status);dot.setAttribute('aria-label',dot.title);e.append(dot);
        }
        const count={inbox:state.summary.counts.inbox,feedback:state.summary.counts.feedback,quotes:state.summary.counts.quotes}[value];
        if(count)e.append(node('span','nav-count studio-count',count));
        if(value.startsWith('topic/')){
          const topic=catalog('topic').find(r=>r.id===value.split('/')[1]);if(topic)e.style.setProperty('--topic-color',topic.data.color||'#7b8793');
          e.draggable=true;e.addEventListener('dragstart',()=>{dragTopic=value.split('/')[1];});e.addEventListener('dragover',event=>event.preventDefault());
          e.addEventListener('drop',async event=>{event.preventDefault();if(dragTopic)await reorderTopic(dragTopic,value.split('/')[1]);dragTopic=null;});
        }
        children.append(e);
      });
      if(group.id==='topics')children.append(button('＋ 新主題',()=>editRecord('topic'),'nav-item studio-new-topic'));
      section.append(children);holder.append(section);
    });
    const pins=$('studio-pins');pins.replaceChildren();const savedPins=preferences.pins||[];
    if(savedPins.length||Number(document.querySelector('[data-count=pinned]')?.textContent)){
      pins.append(node('p','nav-label','釘選'));
      savedPins.forEach(pin=>pins.append(routeButton(pin.route||'record/'+pin.id,pin.title,'star')));
      pins.append(routeButton('pinned','查看釘選內容','star'));
    }
  }
  async function reorderTopic(from,to){
    const topics=catalog('topic').sort((a,b)=>(a.data.order||0)-(b.data.order||0)||a.created_at.localeCompare(b.created_at));
    const index=topics.findIndex(r=>r.id===from),target=topics.findIndex(r=>r.id===to);if(index<0||target<0||index===target)return;
    topics.splice(target,0,topics.splice(index,1)[0]);
    for(let i=0;i<topics.length;i++)if(topics[i].data.order!==i)await api(recordUrl(topics[i].id),{method:'PATCH',body:{expected_version:topics[i].version,data:{order:i}}});
    await refresh();
  }
  function statusLabel(kind,key,value){return schema(kind)?.fields[key]?.options?.find(o=>o.value===value)?.label??(value===true?'是':value===false?'否':String(value??''));}
  function summaryText(record){
    const d=record.data,kind=record.kind;
    if(kind==='project')return `${statusLabel(kind,'stage',d.stage)} · ${d.progress||0}%${d.owner?' · '+d.owner:''}`;
    if(kind==='invoice')return `${d.currency} ${d.amount} · 已收 ${d.paid||'0.00'}${d.due_date?' · '+d.due_date+' 到期':''}`;
    if(kind==='cost')return `${d.currency} ${d.amount} · ${d.date||''}`;
    if(kind==='specialist')return statusLabel(kind,'state',d.state||'unconnected');
    if(kind==='integration')return d.budget_paused?'已達上限，工作台已暫停':d.enabled?'已登記，執行服務未接入':'未啟用';
    if(kind==='task')return `${d.done?'已完成':'待完成'}${d.due_date?' · '+d.due_date:''}`;
    if(d.state)return statusLabel(kind,'state',d.state);
    return d.note||d.description||d.content||d.company||d.role||d.summary||d.source||'查看內容';
  }
  function badges(record){const wrap=node('div','studio-badges');if(record.data.state)wrap.append(node('span','badge',statusLabel(record.kind,'state',record.data.state)));if(record.visibility==='admin')wrap.append(node('span','badge','管理員可見'));return wrap;}
  function recordCard(record){
    const card=node('article','studio-card');card.dataset.recordId=record.id;
    if(record.kind==='inspiration'&&record.data.screenshot_id){const image=node('img','inspiration-preview');image.src='/api/studio/media/'+encodeURIComponent(record.data.screenshot_id);image.alt=record.title+'的截圖';image.loading='lazy';card.append(image);}
    const heading=button(record.title,()=>openRecord(record.id),'studio-record-title');card.append(heading,badges(record),node('p','studio-record-summary',summaryText(record)));
    if(record.kind==='project'){
      const progress=node('progress');progress.max=100;progress.value=record.data.progress||0;progress.setAttribute('aria-label','項目進度');card.append(progress);
      const done=(record.data.milestones||[]).filter(m=>m.done).length;card.append(node('small','muted',`${done} / ${(record.data.milestones||[]).length} 個里程碑完成`));
    }
    if(record.kind==='inbox')card.append(node('small','muted','來源：'+record.data.source));
    const actions=node('div','studio-card-actions');
    if(record.archived)actions.append(button('還原',()=>patch(record,{archived:false})));
    else if(record.kind==='task')actions.append(button(record.data.done?'設為未完成':'完成',()=>patch(record,{data:{done:!record.data.done}})));
    else actions.append(button('查看',()=>openRecord(record.id)));
    if(!record.archived&&['inbox','inspiration','report','feedback'].includes(record.kind))actions.append(button('轉成待辦',()=>run(record,'to_task')));
    card.append(actions);return card;
  }
  function empty(copy,action){const e=node('div','studio-empty');e.append(app.icon('folder'),node('h3','',copy),node('p','muted','新增內容，或交給已連接的 AI 整理。'));if(action)e.append(action);return e;}
  function header(title,description,actions=[]){
    const e=node('div','studio-page-header'),copy=node('div');copy.append(node('h1','',title),node('p','muted',description));e.append(copy);
    const buttons=node('div','actions');actions.forEach(a=>buttons.append(a));e.append(buttons);return e;
  }
  function tabs(labels,onChange,selected){const e=node('div','studio-tabs');labels.forEach(([key,label])=>{const b=button(label,()=>onChange(key),'studio-tab');b.classList.toggle('active',key===selected);b.setAttribute('aria-pressed',String(key===selected));e.append(b);});return e;}
  function hideLegacy(){app.hideLegacy();$('view-heading').hidden=true;$('dashboard-panel').hidden=true;$('workdays-panel').hidden=true;$('library-panel').hidden=true;$('studio-view-tabs').hidden=true;$('studio-panel').hidden=false;}
  async function navigate(value,{replace=false}={}){
    if(!ready)return;
    if(value.startsWith('collection/'))value='topic/'+value.split('/')[1];
    value=NAV.aliases[value]||value;
    activeRecord=value.startsWith('record/')?(await api(recordUrl(value.split('/')[1]))).record:null;
    const page=definition(value);if(page.access==='admin'&&!admin())throw new Error('你的帳戶沒有這項權限。');
    route=value;activeTab='';pageOffset=0;searchText='';
    if(page.group&&!state.preferences.data.groups.includes(page.group)){state.preferences.data.groups=[...state.preferences.data.groups,page.group];await savePrefs({groups:state.preferences.data.groups});}
    const url='#/'+value;if(location.hash!==url)history[replace?'replaceState':'pushState'](null,'',url);
    $('breadcrumb-current').textContent=page.label;app.closeSidebar();renderNavigation(true);
    await renderPage();
  }
  async function renderPage(){
    const seq=++request;
    if(route==='overview'||route==='overview/workdays'||route==='overview/todos'||route==='documents'){
      navigating=true;app.setView(route==='documents'?'files':route.endsWith('/workdays')?'workdays':route.endsWith('/todos')?'todos':'dashboard');navigating=false;
      $('studio-panel').hidden=true;
      const bar=$('studio-view-tabs');bar.hidden=false;bar.replaceChildren();
      if(route.startsWith('overview'))bar.append(tabs([['overview','今日總覽'],['overview/workdays','每日工作紀錄'],['overview/todos','待辦']],v=>navigate(v),route));
      else bar.append(tabs([['documents','文件'],['reports','報告與交付物']],v=>v==='documents'?navigate(v):navigate('reports'),'documents'));
      if(route==='overview/todos'){const wrap=node('div','studio-inline-tasks');const data=await api('/records?kind=task&limit=100');if(seq!==request)return;wrap.append(header('其他待辦','由收件匣、靈感和項目加入。',[button('新增待辦',()=>editRecord('task'))]),...data.records.map(recordCard));bar.append(wrap);}
      renderOverview();return;
    }
    hideLegacy();const panel=$('studio-panel');panel.replaceChildren(node('p','muted','正在讀取…'));
    if(route.startsWith('specialist/'))return specialistPage(route.split('/')[1],seq);
    if(route.startsWith('topic/'))return topicPage(route.split('/')[1],seq);
    if(route.startsWith('record/'))return detailPage(route.split('/')[1],seq);
    if(route==='templates')return templatePage();
    if(route==='connections')return connectionPage(seq);
    if(route==='account')return accountPage();
    if(route==='command')return commandPage(seq);
    if(route==='pinned')return pinnedPage();
    if(route==='team')return teamPage(seq);
    const page=definition();let kind=page.kind;
    if(route==='reports')kind='report';if(route==='archived')kind='';
    if(!kind&&route!=='archived'){panel.replaceChildren(empty('找不到這個頁面。',button('返回今日總覽',()=>navigate('overview'))));return;}
    const category=route.startsWith('inspirations/')?route.split('/')[1]:'';
    await listPage({kind,category,archived:route==='archived',title:pageNames[route]||page.label,description:page.description,seq});
  }
  async function listPage({kind,category='',archived=false,title,description,seq,extra=[]}){
    const data=await api('/records?'+new URLSearchParams({kind,category,archived:archived?'1':'0',q:searchText,offset:pageOffset,limit:30}));if(seq!==request)return;
    const panel=$('studio-panel'),actions=[];
    if(!archived){actions.push(button('交給 AI',()=>openCommand('請幫我整理'+title+'。')));actions.push(button('新增'+(schema(kind)?.label||'內容'),()=>kind==='inspiration'?addInspiration():editRecord(kind)));}
    actions.push(button('釘選頁面',()=>pinPage()));
    panel.replaceChildren(header(title,description,actions));extra.forEach(n=>panel.append(n));
    if(route==='reports')panel.append(tabs([['documents','文件'],['reports','報告與交付物']],v=>navigate(v),'reports'));
    if(route==='inspirations/learning')panel.append(button('查看原有影片與收藏',()=>openLegacyLibrary('links'),'text-button'));
    if(route==='archived')panel.append(button('查看原有封存文件與收藏',()=>openLegacyLibrary('archived'),'text-button'));
    if(['revenue','costs'].includes(route))panel.append(financeSummary());
    const search=node('input','studio-search');search.type='search';search.placeholder='搜尋這個頁面…';search.value=searchText;search.setAttribute('aria-label','搜尋這個頁面');
    const filter=node('form','studio-filter');filter.append(search);const submit=node('button','button secondary small','搜尋');submit.type='submit';filter.append(submit);filter.addEventListener('submit',event=>{event.preventDefault();searchText=search.value;pageOffset=0;listPage({kind,category,archived,title,description,seq:++request,extra}).catch(e=>app.notice(e.message,true));});panel.append(filter);
    const grid=node('div','studio-grid');data.records.forEach(r=>grid.append(recordCard(r)));panel.append(data.records.length?grid:empty(archived?'沒有已封存的內容。':'這裏還沒有內容。'));
    const pagination=node('div','pagination');if(pageOffset)pagination.append(button('上一頁',()=>{pageOffset=Math.max(0,pageOffset-30);return listPage({kind,category,archived,title,description,seq:++request,extra});}));pagination.append(node('span','muted',`共 ${data.total} 項`));if(pageOffset+30<data.total)pagination.append(button('下一頁',()=>{pageOffset+=30;return listPage({kind,category,archived,title,description,seq:++request,extra});}));panel.append(pagination);
  }
  function openLegacyLibrary(view){navigating=true;app.setView(view);navigating=false;$('studio-panel').hidden=true;$('studio-view-tabs').hidden=false;$('studio-view-tabs').replaceChildren(button('返回 '+definition().label,()=>renderPage(),'text-button'));}
  function financeSummary(){
    const wrap=node('div','studio-finance-summary');wrap.append(node('p','muted','本月數字按貨幣分開，沒有換算匯率。'));
    Object.entries(state.summary.finance||{}).forEach(([currency,total])=>{const row=node('div','studio-summary-row');row.append(node('strong','',currency),node('span','',`實收 ${total.received}`),node('span','',`應收未收 ${total.outstanding}`),node('span','',`成本 ${total.cost}`));wrap.append(row);});
    wrap.append(button('建立本月報告',async()=>{const result=await api('/financial-report',{method:'POST',body:{month:state.summary.month}});await refresh();await openRecord(result.record.id);}));return wrap;
  }
  async function patch(record,body){await api(recordUrl(record.id),{method:'PATCH',body:{expected_version:record.version,...body}});await refresh();await renderPage();}
  async function run(record,action,args={}){const result=await api(recordUrl(record.id)+'/actions',{method:'POST',body:{expected_version:record.version,action,arguments:args}});await refresh();if(action==='ask_ai'){const command=result.created.find(r=>r.kind==='command');if(command)await handoff(command);}else{app.notice('已保存。');await renderPage();}return result;}
  async function openRecord(id){await navigate('record/'+id);}
  function referenceLabel(ref){return choices.find(r=>r.kind===ref.kind&&r.id===ref.id)?.title||state.catalog.find(r=>r.kind===ref.kind&&r.id===ref.id)?.title||ref.id;}
  function fieldDisplay(record){
    const wrap=node('dl','studio-fields-display');
    Object.entries(schema(record.kind)?.fields||{}).forEach(([key,spec])=>{
      const value=record.data[key];if(value===undefined||value===''||Array.isArray(value)&&!value.length)return;
      wrap.append(node('dt','',spec.label));const content=node('dd');
      if(spec.type==='checklist')value.forEach((item,index)=>{const label=node('label','studio-check');const input=node('input');input.type='checkbox';input.checked=item.done;input.addEventListener('change',()=>patch(record,{data:{[key]:value.map((v,i)=>i===index?{...v,done:input.checked}:v)}}).catch(e=>app.notice(e.message,true)));label.append(input,node('span','',item.title));content.append(label);});
      else if(spec.type==='line_items')value.forEach(line=>content.append(node('p','',`${line.description} · ${line.quantity} × ${line.unit_price}`)));
      else if(spec.type==='work_logs')value.forEach(log=>content.append(node('p','',`${log.date} · ${log.minutes} 分鐘 · ${log.notes}`)));
      else if(spec.type==='payments')value.forEach(log=>content.append(node('p','',`${log.date} · ${log.amount} · ${log.note||''}`)));
      else if(spec.type==='choices')content.textContent=value.map(v=>statusLabel(record.kind,key,v)).join('、');
      else if(spec.type==='reference')content.append(button(referenceLabel({kind:spec.kind,id:value}),()=>activateReference({kind:spec.kind,id:value}),'text-button'));
      else if(spec.type==='references')value.forEach(id=>content.append(button(referenceLabel({kind:spec.kind,id}),()=>activateReference({kind:spec.kind,id}),'text-button')));
      else if(spec.type==='url'&&value){const a=node('a','text-button',value);a.href=value;a.target='_blank';a.rel='noopener noreferrer';content.append(a);}
      else content.textContent=spec.type==='select'||spec.type==='boolean'?statusLabel(record.kind,key,value):Array.isArray(value)?value.join('、'):String(value);
      wrap.append(content);
    });return wrap;
  }
  async function detailPage(id,seq){
    const [{record},context]=await Promise.all([api(recordUrl(id)),api(recordUrl(id)+'/context')]);if(seq!==request)return;activeRecord=record;
    const panel=$('studio-panel'),actions=[button('修改',()=>editRecord(record.kind,record)),button('交給 AI',()=>run(record,'ask_ai')),button('釘選',()=>pinRecord(record))];
    if(record.archived)actions.push(button('還原',()=>patch(record,{archived:false})));else actions.push(button('封存',()=>patch(record,{archived:true})));
    panel.replaceChildren(header(record.title,schema(record.kind)?.label||'內容',actions),badges(record));
    if(record.kind==='inbox'){
      const bar=node('div','actions');bar.append(button('歸入項目',()=>editRecord('inbox',record)),button('轉成待辦',()=>run(record,'to_task')));panel.append(bar);
    }
    if(record.kind==='inspiration'){
      const bar=node('div','actions');bar.append(button('找相似',async()=>{const result=await api(recordUrl(id)+'/similar');const section=node('section','studio-related');section.append(node('h2','','相似靈感'),node('p','muted',result.note),...result.matches.map(m=>recordCard(m.record)));panel.append(section);}),button('轉成待辦',()=>run(record,'to_task')),button('擷取截圖與摘要',()=>run(record,'ask_ai',{instruction:'使用真實瀏覽工具讀取來源，擷取整頁截圖並整理摘要。先確認可以使用的工具，沒有連接時清楚交代。'})));panel.append(bar);
      if(record.data.screenshot_id){const image=node('img','studio-full-preview');image.src='/api/studio/media/'+record.data.screenshot_id;image.alt=record.title;panel.append(image);}else panel.append(node('p','studio-callout','尚未有截圖。收藏已保存，截圖與摘要等待具備瀏覽工具的 AI 處理。'));
    }
    if(record.kind==='lead')panel.append(button('建立報價草稿',async()=>{const result=await run(record,'create_quote');await openRecord(result.created[0].id);}));
    if(record.kind==='quote')panel.append(quoteActions(record));
    if(record.kind==='invoice')panel.append(button('記錄收款',()=>actionDialog('記錄收款','只記錄你已收到的款項，不會向客戶收費。',[
      ['amount',{label:'收到金額',type:'money',required:true}],['date',{label:'收款日期',type:'date',default:new Date().toLocaleDateString('en-CA'),required:true}],['note',{label:'備註',type:'text'}]
    ],values=>run(record,'record_payment',values))));
    if(record.kind==='command'){
      panel.append(node('p','studio-callout',record.data.state==='awaiting_ai'?'指令已保存。需要已連接的 AI 讀取這筆指令，才會開始處理。':'結果來自已記錄的處理來源；對外動作仍需另外接入並確認。'),button('複製給 AI',()=>handoff(record)));
      if(record.data.state==='needs_confirmation'&&admin())panel.append(button('確認草稿',()=>actionDialog('確認草稿','確認後只記錄你的決定，不會發送電郵、WhatsApp、報價或付款。',[],()=>run(record,'approve_draft',{confirmed:true}))));
    }
    if(['report','quote','invoice'].includes(record.kind)){const download=node('a','button secondary small','下載內容');download.href='/api/studio'+recordUrl(id)+'/download';download.download='';panel.append(download);}
    panel.append(fieldDisplay(record));
    if(context.sources.length){const related=node('section','studio-related');related.append(node('h2','','相關內容'));context.sources.forEach(source=>related.append(button(source.title||source.id,()=>activateReference(source),'studio-related-link')));panel.append(related);}
    panel.append(button('查看修改紀錄',async()=>{const result=await api(recordUrl(id)+'/history');const history=node('section','studio-history');history.append(node('h2','','修改紀錄'));result.versions.forEach(v=>{const entry=node('details');entry.append(node('summary','',`版本 ${v.version} · ${v.updated_at} · ${v.actor}`),fieldDisplay(v));history.append(entry);});panel.append(history);}));
  }
  function quoteActions(record){
    const actions=node('div','actions'),state=record.data.state||'draft';
    const transitions={draft:['pending','提交確認'],pending:['approved','確認草稿'],approved:['sent','記錄已自行發送'],sent:['signed','記錄已簽署']};
    if(transitions[state]&&(state==='draft'||admin())){const [next,label]=transitions[state];actions.append(button(label,()=>actionDialog(label,'這個動作只更新紀錄，不會自動發送文件或代你簽署。',next==='signed'?[['date',{label:'簽署日期',type:'date',default:stateDate(),required:true}]]:[],values=>run(record,'quote_state',{state:next,confirmed:true,...values}))));}
    if(['pending','approved','declined'].includes(state))actions.append(button('退回草稿',()=>run(record,'quote_state',{state:'draft'})));
    if(state==='signed'&&admin())actions.append(button('建立客戶檔案與發票',()=>actionDialog('建立客戶檔案與發票','沿用這份報價的聯絡人、金額和貨幣，不用重填。',[['due_date',{label:'付款期限',type:'date'}]],values=>run(record,'create_invoice',values))));
    return actions;
  }
  const stateDate=()=>new Date().toLocaleDateString('en-CA');
  async function specialistPage(id,seq){
    const {record}=await api(recordUrl(id));if(seq!==request)return;activeRecord=record;
    const panel=$('studio-panel');panel.replaceChildren(header(record.title,'先設定角色與規則，再接入實際服務。',[button('釘選',()=>pinRecord(record)),...(admin()?[button(record.data.state==='paused'?'繼續設定':'暫停',()=>run(record,'specialist_state',{state:record.data.state==='paused'?'configuring':'paused'}))]:[])]));
    const body=node('div','studio-tab-content');const views=[['overview','總覽'],['settings','設定'],['conversations','對話紀錄'],['tests','測試'],['metrics','數據']];
    const select=async key=>{
      activeTab=key;bar.replaceWith(makeBar());body.replaceChildren();
      if(key==='overview'){
        body.append(node('p','studio-callout',statusLabel('specialist','state',record.data.state||'unconnected')+'。目前是專員設定空間，尚未接入對外執行服務。'),node('h2','',record.data.role||'先寫下這位專員的工作。'),node('p','muted',record.data.goal||'設定目標、知識、渠道、技能、模型和交接規則。'),...(admin()?[button('設定七個部分',()=>editRecord('specialist',record))]:[]));
      }else if(key==='settings'){
        const labels=[['身分與目標','名稱、角色、語氣與目標'],['知識','可以引用的文件與說明'],['渠道','在哪裏與客戶對話'],['技能','從接入管理選擇可用能力'],['模型','主要模型與後備模型'],['規則與交接','限制、確認要求和轉交真人的時機'],['紀錄與數據','從真實對話及測試取得結果']];
        const grid=node('div','studio-grid');labels.forEach(([title,copy])=>{const box=node('section','studio-card');box.append(node('h3','',title),node('p','muted',copy));grid.append(box);});body.append(grid,...(admin()?[button('修改設定',()=>editRecord('specialist',record))]:[]),fieldDisplay(record));
      }else if(key==='metrics'){
        const metric=state.summary.specialists.find(v=>v.id===id);body.append(node('p','studio-callout','只計算實際匯入、並標示為正式的對話。情景測試不計入正式數據。'));
        if(!metric?.conversations)body.append(empty('尚未有正式對話紀錄。'));else{const row=node('div','studio-summary-row');[['今日對話',metric.conversations],['自動處理',metric.automatic_ratio+'%'],['需要人手',metric.human],['錯誤',metric.errors]].forEach(([label,value])=>row.append(node('p','',label+'：'+value)));body.append(row);}
      }else{
        const kind=key==='tests'?'scenario':'conversation';const result=await api('/records?'+new URLSearchParams({kind,relation:id,limit:100}));
        if(activeTab!==key)return;
        body.append(node('p','studio-callout',key==='tests'?'可先保存測試問題及預期表現。沒有模型接入時，不會生成模擬回覆。':'目前未接入渠道。可透過已授權工具匯入真實對話，並保留來源。'),button(key==='tests'?'新增情景':'新增真實紀錄',()=>editRecord(kind,null,{specialist_id:id})),...(result.records.length?result.records.map(recordCard):[empty(key==='tests'?'還沒有情景測試。':'未接入，尚未收到對話。')]));
      }
    };
    let bar;function makeBar(){bar=tabs(views,select,activeTab||'overview');return bar;}panel.append(makeBar(),body);await select(activeTab||'overview');
  }
  function templatePage(){const panel=$('studio-panel');panel.replaceChildren(header('從範本新增','範本只帶入角色和規則，其他設定由你決定。'));const grid=node('div','studio-grid');state.templates.forEach(template=>{const card=node('article','studio-card');card.append(node('h2','',template.title),node('p','muted',template.role),node('p','',template.rules),button('使用範本',async()=>{const result=await api('/templates',{method:'POST',body:{template_id:template.id}});await refresh();await navigate('specialist/'+result.record.id);}));grid.append(card);});panel.append(grid);}
  async function connectionPage(seq){
    const panel=$('studio-panel');panel.replaceChildren(header('雲端與外部接入','統一管理專員可以使用的能力。',[button('連接左側 AI',()=>document.querySelector('[data-connection-open]').click()),button('新增接入',()=>editRecord('integration'))]));
    const body=node('div'),views=[['model','模型 API'],['connector','外掛與連接器'],['skill','技能庫'],['channel','渠道帳戶'],['usage','用量與上限']];let bar;
    const select=key=>{activeTab=key;bar.replaceWith(makeBar());body.replaceChildren(node('p','studio-callout','這裏保存接入設定。API 金鑰只在伺服器環境或平台秘密設定中填寫；此頁只記錄設定名稱。本版未提供專員執行器，登記後不會自行呼叫模型或對外發送。'));
      const records=catalog('integration').filter(r=>key==='usage'||r.data.category===key);const grid=node('div','studio-grid');
      records.forEach(record=>{const card=recordCard(record);const users=catalog('specialist').filter(s=>[s.data.model_id,s.data.fallback_id,s.data.channel_id,...(s.data.skill_ids||[])].includes(record.id));card.append(node('p','muted','使用專員：'+(users.map(s=>s.title).join('、')||'尚未分配')),node('p','muted',`本月已記錄：${record.data.currency||'HKD'} ${record.month_usage||'0.00'} · 上限 ${record.data.monthly_limit==='0.00'?'未設定':record.data.monthly_limit||'未設定'}`),node('p','muted',record.credential_configured?'伺服器已有對應憑證設定。':'伺服器尚未提供對應憑證。'));
        card.append(button(record.data.enabled?'停用':'啟用設定',()=>record.data.budget_paused?run(record,'resume_budget'):patch(record,{data:{enabled:!record.data.enabled}})),button('記錄實際用量',()=>actionDialog('記錄實際用量','使用服務帳單或量測資料，不要填入估計值。',[['amount',{label:'實際費用',type:'money',required:true}],['date',{label:'日期',type:'date',default:stateDate(),required:true}],['source',{label:'來源',type:'text',required:true}],['source_key',{label:'帳單或紀錄識別碼',type:'text',required:true}]],values=>run(record,'record_usage',values))));grid.append(card);});body.append(records.length?grid:empty('尚未登記這類接入。',button('新增接入',()=>editRecord('integration',null,{category:key==='usage'?'model':key}))));
      if(key==='usage')body.append(button('查看公司成本',()=>navigate('costs')));
    };
    function makeBar(){bar=tabs(views,select,activeTab||'model');return bar;}panel.append(makeBar(),body);if(seq===request)select(activeTab||'model');
  }
  async function topicPage(id,seq){
    const context=await api(recordUrl(id)+'/context');if(seq!==request)return;const record=context.record;activeRecord=record;
    const panel=$('studio-panel');panel.replaceChildren(header(record.title,record.data.description||'把同一個主題的資料連在一起。',[button('加入或整理內容',()=>editRecord('topic',record)),button('交給 AI',()=>run(record,'ask_ai',{instruction:'只根據這個主題內的資料，整理重點和下一步。'})),button('釘選',()=>pinRecord(record))]));
    const grid=node('div','studio-grid');context.sources.forEach(source=>{if(source.data)grid.append(recordCard(source));else{const card=node('article','studio-card');card.append(button(source.title||source.id,()=>activateReference(source),'studio-record-title'),node('p','muted',source.content||'查看原始資料'));grid.append(card);}});panel.append(context.sources.length?grid:empty('這個主題還沒有連結內容。'));
    panel.append(button('新增相關待辦',()=>editRecord('task',null,{},[{kind:'topic',id}])));
    const related=await api('/records?'+new URLSearchParams({kind:'task',relation:id,limit:100}));if(seq!==request)return;if(related.records.length){panel.append(node('h2','','相關待辦'));related.records.forEach(r=>panel.append(recordCard(r)));}
  }
  async function commandPage(seq){
    const panel=$('studio-panel');const data=await api('/records?kind=command&limit=50');if(seq!==request)return;
    panel.replaceChildren(header('指揮中樞','寫下一句指令，保留過程，讓結果回到收件匣。',[button('新增指令',()=>openCommand()),button('釘選頁面',()=>pinPage())]),node('p','studio-callout','指令會交給你已連接的 AI 客戶端。專員未接入時，工作台會保留待處理狀態。對外發送和收費只會先產生待確認的草稿。'));
    const examples=node('div','actions');['整理本月的財務報告','總結本星期的客訴','整理本月靈感，提出三個值得嘗試的做法'].forEach(text=>examples.append(button(text,()=>openCommand(text))));panel.append(examples);
    const grid=node('div','studio-grid');data.records.forEach(r=>grid.append(recordCard(r)));panel.append(data.records.length?grid:empty('還沒有指令紀錄。'));
  }
  async function pinnedPage(){
    const panel=$('studio-panel');panel.replaceChildren(header('釘選','把常用頁面和內容放在手邊。'));
    for(const pin of state.preferences.data.pins||[]){const row=node('div','studio-summary-row');row.append(button(pin.title,()=>navigate(pin.route||'record/'+pin.id),'text-button'),button('取消釘選',()=>savePrefs({pins:state.preferences.data.pins.filter(p=>p!==pin)}).then(pinnedPage)));panel.append(row);}
    panel.append(button('查看原有星號收藏',()=>openLegacyLibrary('pinned')));
  }
  async function pinPage(){const prefs=state.preferences.data;if(prefs.pins.some(p=>p.route===route)){app.notice('這個頁面已釘選。');return;}await savePrefs({pins:[...prefs.pins,{route,title:definition().label}]});app.notice('已釘選頁面。');}
  async function pinRecord(record){const prefs=state.preferences.data;if(prefs.pins.some(p=>p.id===record.id)){app.notice('這項內容已釘選。');return;}await savePrefs({pins:[...prefs.pins,{kind:record.kind,id:record.id,title:record.title}]});app.notice('已釘選內容。');}
  async function activateReference(ref){
    if(ref.kind==='item')return app.openDetail(ref.id);
    if(ref.kind==='workday')return root.BlueprintWorkdays?.openReport(ref.id);
    if(ref.kind==='todo'){navigate('overview/todos');return;}
    if(ref.kind==='specialist')return navigate('specialist/'+ref.id);
    if(ref.kind==='topic')return navigate('topic/'+ref.id);
    return openRecord(ref.id);
  }
  async function loadChoices(){
    const [records,items,workdays,todos]=await Promise.all([api('/records?limit=100'),app.api('/api/items?limit=200'),app.api('/api/workdays?limit=50'),app.api('/api/todos?limit=50&done=all')]);
    choices=[...records.records,...state.catalog,...items.items.map(r=>({...r,kind:'item'})),...workdays.workdays.map(r=>({...r,kind:'workday'})),...todos.todos.map(r=>({...r,kind:'todo'}))].filter((r,i,all)=>all.findIndex(v=>v.kind===r.kind&&v.id===r.id)===i);
  }
  function fieldControl(key,spec,value){
    const grouped=['checklist','line_items','work_logs'].includes(spec.type);
    const label=node(grouped?'fieldset':'label','field');label.append(node(grouped?'legend':'span','',spec.label));let input;
    if(['checklist','line_items','work_logs'].includes(spec.type)){
      const wrap=node('div','studio-row-editor');wrap.dataset.field=key;wrap.dataset.type=spec.type;
      const list=node('div','studio-row-list');wrap.append(list);
      const addRow=(data={})=>{
        const row=node('div','studio-edit-row');row.dataset.row='1';
        const fields=spec.type==='checklist'?[['done','checkbox','完成'],['title','text','內容']]:spec.type==='line_items'?[['description','text','項目'],['quantity','number','數量'],['unit_price','number','單價']]:[['date','date','日期'],['minutes','number','分鐘'],['notes','text','備註']];
        fields.forEach(([name,type,title])=>{const part=node('input');part.type=type;part.dataset.part=name;part.setAttribute('aria-label',title);part.placeholder=title;
          if(type==='checkbox')part.checked=!!data[name];else part.value=data[name]??(name==='quantity'?1:'');
          if(type==='number'){part.min=name==='quantity'?1:0;part.step=name==='unit_price'?'.01':'1';}if(name!=='notes'&&type!=='checkbox')part.required=true;row.append(part);
        });row.append(button('移除',()=>row.remove(),'text-button'));list.append(row);
      };
      (value||[]).forEach(addRow);wrap.append(button('＋ 加入一項',()=>addRow(),'text-button'));label.append(wrap);return label;
    }
    if(['select','reference','references','choices'].includes(spec.type)){
      input=node('select');input.multiple=['references','choices'].includes(spec.type);if(input.multiple)input.size=4;
      let options=spec.options||choices.filter(r=>r.kind===spec.kind&&(!spec.category||r.data?.category===spec.category)).map(r=>({value:r.id,label:r.title}));
      const selected=Array.isArray(value)?value:value?[value]:[];
      selected.forEach(v=>{if(!options.some(o=>o.value===v))options=[...options,{value:v,label:'已連結：'+v}];});
      if(!input.multiple&&!spec.required&&spec.type==='reference'){const o=node('option','','未選擇');o.value='';input.append(o);}
      options.forEach(option=>{const o=node('option','',option.label);o.value=option.value;o.selected=selected.includes(option.value);input.append(o);});
    }else if(spec.type==='textarea'||spec.type==='strings'){
      input=node('textarea');input.rows=spec.type==='textarea'?3:2;input.value=Array.isArray(value)?value.join('、'):value||'';
      if(spec.type==='strings')input.placeholder='用逗號或頓號分隔。';
    }else{
      input=node('input');input.type=spec.type==='boolean'?'checkbox':spec.type==='money'||spec.type==='integer'?'number':['date','email','url','color','password'].includes(spec.type)?spec.type:'text';
      if(spec.type==='boolean')input.checked=!!value;else input.value=value??spec.default??'';
      if(spec.type==='integer'){input.step='1';input.min=spec.min??0;input.max=spec.max??10000;}
      if(spec.type==='money'){input.step='.01';input.min='0';}
      if(input.type==='password')input.autocomplete='new-password';
    }
    input.dataset.field=key;input.dataset.type=spec.type;input.required=!!spec.required;label.append(input);
    if(key==='secret_env')label.append(node('span','muted','例如 BLUEPRINT_OPENAI_KEY。請勿在這裏填入金鑰。'));
    return label;
  }
  function readFields(container){
    const result={};container.querySelectorAll('[data-field]').forEach(input=>{
      const type=input.dataset.type,key=input.dataset.field;
      if(['checklist','line_items','work_logs'].includes(type)){
        result[key]=[...input.querySelectorAll('[data-row]')].map(row=>Object.fromEntries([...row.querySelectorAll('[data-part]')].map(part=>[part.dataset.part,part.type==='checkbox'?part.checked:['quantity','minutes'].includes(part.dataset.part)?Number(part.value):part.value])));return;
      }
      result[key]=type==='boolean'?input.checked:type==='integer'?Number(input.value):['references','choices'].includes(type)?[...input.selectedOptions].map(o=>o.value):type==='strings'?input.value.split(/[,，、\n]/).map(v=>v.trim()).filter(Boolean):input.value;
    });return result;
  }
  async function editRecord(kind,record=null,initial={},refs=[]){
    await loadChoices();const definition=schema(kind);if(!definition)throw new Error('你的帳戶不能使用這類內容。');
    editing={kind,record,initial,refs};$('studio-editor-title').textContent=(record?'修改':'新增')+definition.label;$('studio-record-title').value=record?.title||'';$('studio-fields').replaceChildren();
    Object.entries(definition.fields).forEach(([key,spec])=>{if(!spec.readonly)$('studio-fields').append(fieldControl(key,spec,record?.data[key]??initial[key]??spec.default));});
    const related=$('studio-reference-fields');related.replaceChildren(node('p','muted','相關內容以連結加入，不會複製原資料。'));
    const select=node('select');select.multiple=true;select.size=5;select.id='studio-refs';select.setAttribute('aria-label','相關內容');
    const selected=record?.refs||refs;
    const options=[...choices.filter(r=>r.id!==record?.id),...selected.filter(ref=>!choices.some(r=>r.kind===ref.kind&&r.id===ref.id)).map(ref=>({...ref,title:ref.id}))];
    const appendOption=(r,checked=false)=>{const value=JSON.stringify({kind:r.kind,id:r.id});if([...select.options].some(o=>o.value===value))return;const o=node('option','',`${schema(r.kind)?.label||{workday:'每日工作',todo:'待辦'}[r.kind]||'文件'} · ${r.title}`);o.value=value;o.selected=checked;select.append(o);};
    options.forEach(r=>appendOption(r,selected.some(ref=>ref.kind===r.kind&&ref.id===r.id)));
    const query=node('input');query.type='search';query.placeholder='搜尋較早的內容…';query.setAttribute('aria-label','搜尋相關內容');
    related.append(query,button('搜尋相關內容',async()=>{
      const term=encodeURIComponent(query.value.trim());if(!term)return;
      const [records,items,workdays,todos]=await Promise.all([api('/records?q='+term+'&limit=100'),app.api('/api/items?q='+term+'&limit=100'),app.api('/api/workdays?q='+term+'&limit=50'),app.api('/api/todos?q='+term+'&done=all&limit=50')]);
      const found=[...records.records,...items.items.map(r=>({...r,kind:'item'})),...workdays.workdays.map(r=>({...r,kind:'workday'})),...todos.todos.map(r=>({...r,kind:'todo'}))];
      [...select.options].filter(o=>!o.selected).forEach(o=>o.remove());found.filter(r=>r.id!==record?.id).forEach(r=>appendOption(r));
      app.notice(`找到 ${found.length} 項候選內容；已選取的關聯會保留。`);
    }),select);
    errorAt('studio-form-error',null);showDialog('studio-editor');
  }
  async function busy(form,fn,errorId){
    if(form.dataset.busy)return;form.dataset.busy='1';const buttons=[...form.querySelectorAll('button')];buttons.forEach(b=>b.disabled=true);errorAt(errorId,null);
    try{await fn();}catch(error){errorAt(errorId,error);}finally{delete form.dataset.busy;buttons.forEach(b=>b.disabled=false);}
  }
  async function actionDialog(title,copy,fields,submit){
    await loadChoices();$('studio-action-title').textContent=title;$('studio-action-copy').textContent=copy;$('studio-action-fields').replaceChildren(...fields.map(([key,spec])=>fieldControl(key,spec,spec.default)));actionSubmit=submit;errorAt('studio-action-error',null);showDialog('studio-action-dialog');
  }
  function openCommand(text=''){$('studio-command-input').value=text;errorAt('studio-command-error',null);showDialog('studio-command-dialog');}
  async function handoff(record){
    const context=await api(recordUrl(record.id)+'/context');
    $('studio-handoff-text').value=`請處理我在藍圖工作台保存的指令。\n後台：${location.origin}\n指令編號：${record.id}\n\n先呼叫 blueprint_connection_guide 和 blueprint_studio_state，確認同一公司與可用工具。\n用 blueprint_studio_get 讀取此編號，再用 blueprint_studio_context 取得獲授權的相關資料。\n若指令限定主題，只使用該主題的資料。必要時用 blueprint_studio_list 找出其他已獲授權的公司資料；不要猜測未讀取的網頁或對話。\n用 blueprint_studio_action 的 plan_command 保存工作步驟，再以目前版本執行 complete_command；arguments_json 包含 result、真實 source 及 external_action。涉及發送或收費時 external_action 必須為 true，只保存草稿，等我確認。\n完成後讀回結果，確認同一收件匣已有內容。未接入工具時先協助連接，不要聲稱已執行。\n\n原指令：${context.record.data.instruction||record.title}`;
    $('studio-handoff-status').textContent='';showDialog('studio-handoff');
  }
  function addInspiration(url='',title=''){
    app.openAdd('link');$('link-url').value=url;$('link-title').value=title|| (url?new URL(url).hostname:'');
  }
  async function teamPage(seq){
    const [users,records]=await Promise.all([api('/users'),api('/records?kind=member&limit=100')]);if(seq!==request)return;
    const panel=$('studio-panel');panel.replaceChildren(header('團隊與權限','人事內容只供老闆和管理員查看。',[button('新增成員檔案',()=>editRecord('member')),button('新增登入帳戶',()=>userForm())]));
    panel.append(node('p','studio-callout','老闆保留原登入。管理員可管理公司資料；員工不能查看財務、人事、接入憑證設定或受保護的附件。停用帳戶會阻止下一次要求。'));
    const list=node('div','studio-grid');users.users.forEach(user=>{const card=node('article','studio-card');card.append(node('h3','',user.name),node('p','muted',`${user.username} · ${user.role==='admin'?'管理員':'員工'} · ${user.enabled?'可登入':'已停用'}`),button('修改登入權限',()=>userForm(user)));list.append(card);});panel.append(list,node('h2','','成員檔案與工時'));records.records.forEach(r=>panel.append(recordCard(r)));
  }
  async function userForm(user){
    const fields=[['username',{label:'登入名稱',type:'text',required:true,default:user?.username||''}],['name',{label:'顯示名稱',type:'text',required:true,default:user?.name||''}],['role',{label:'角色',type:'select',options:(state.identity.role==='owner'?[['member','員工'],['admin','管理員']]:[['member','員工']]).map(([value,label])=>({value,label})),default:user?.role||'member'}],['password',{label:user?'新密碼（不改請留空）':'密碼（至少 12 個字元）',type:'password',required:!user}],['enabled',{label:'允許登入',type:'boolean',default:user?!!user.enabled:true}]];
    await actionDialog(user?'修改登入帳戶':'新增登入帳戶','密碼只送到後台作安全雜湊，不會儲存在瀏覽器。',fields,async values=>{await api('/users',{method:'POST',body:{...values,expected_version:user?.version||0}});await renderPage();});
    if(user)$('studio-action-fields').querySelector('[data-field=username]').readOnly=true;
  }
  function accountPage(){
    const panel=$('studio-panel');panel.replaceChildren(header('帳戶','目前使用的工作空間與登入資料。'),node('p','',`使用者：${state.identity.name||state.identity.username}`),node('p','',`登入名稱：${state.identity.username}`),node('p','',`角色：${{owner:'老闆',admin:'管理員',member:'員工'}[state.identity.role]}`),node('p','muted',`工作台版本：V2.4 · 後台 ${document.getElementById('version').title}`),node('p','muted','原生後台使用瀏覽器的 HTTP 登入。需要切換登入帳戶時，請開啟新的私人瀏覽視窗，再輸入另一個帳戶。'));
    if(admin())panel.append(button('管理團隊登入',()=>navigate('team')));
  }
  function preferencesPanel(){
    let holder=$('studio-preferences');if(!holder){holder=node('section','studio-preferences');holder.id='studio-preferences';$('settings-dialog').append(holder);}
    holder.replaceChildren(node('h3','','工作台顯示'),node('p','muted','這些設定會按登入帳戶保存。介面使用繁體中文，沿用目前的白色風格。'));
    [['narrow','使用窄側欄'],['notifications','顯示工作台提醒'],['monthly_review','每月建立一次靈感回顧指令']].forEach(([key,title])=>{
      const label=node('label','studio-check'),input=node('input');input.type='checkbox';input.checked=!!state.preferences.data[key];input.addEventListener('change',()=>savePrefs({[key]:input.checked}).catch(e=>app.notice(e.message,true)));label.append(input,node('span','',title));holder.append(label);
    });
    holder.append(node('p','muted','選擇側欄顯示的主列。當前頁面所屬主列仍會顯示。'));
    NAV.groups.forEach(group=>{const label=node('label','studio-check'),input=node('input');input.type='checkbox';input.checked=!state.preferences.data.hidden_groups.includes(group.id);input.addEventListener('change',()=>savePrefs({hidden_groups:input.checked?state.preferences.data.hidden_groups.filter(g=>g!==group.id):[...state.preferences.data.hidden_groups,group.id]}).catch(e=>app.notice(e.message,true)));label.append(input,node('span','',group.label));holder.append(label);});
  }
  async function companySwitcher(){
    const fields=[['name',{label:'工作空間名稱',type:'text',required:true}],['url',{label:'已有工作台網址',type:'url',required:true}]];
    await actionDialog('公司與工作空間','每個工作空間沿用自己的登入和資料。加入網址只建立切換入口，不會複製公司資料。',fields,values=>savePrefs({workspaces:[...state.preferences.data.workspaces,values]}));
    const holder=$('studio-action-fields');const list=node('div','studio-workspaces');list.append(node('p','muted','目前：'+$('company-name').textContent));
    state.preferences.data.workspaces.forEach(workspace=>{const a=node('a','studio-related-link',workspace.name);a.href=workspace.url;a.rel='noreferrer';list.append(a);});holder.prepend(list);
  }
  async function renderOverview(){
    if(!state)return;
    const holder=$('studio-focus');if(holder){holder.replaceChildren();
      for(const ref of state.preferences.data.focus||[]){const label=referenceLabel(ref);holder.append(button(label,()=>activateReference(ref),'studio-focus-line'));}
      if(!state.preferences.data.focus?.length)holder.append(node('p','muted','選擇最多三件今天要完成的事。'));
      holder.append(button('設定今日焦點',()=>editFocus(),'text-button'));
    }
    const reminders=$('studio-reminders');if(reminders){reminders.replaceChildren();if(state.preferences.data.notifications&&state.summary.reminders.length){reminders.append(node('h4','','公司提醒'));state.summary.reminders.slice(0,3).forEach(r=>reminders.append(button(r.reason+' · '+r.title,()=>openRecord(r.id),'studio-focus-line')));}}
    const metrics=state.summary.specialists.reduce((all,m)=>({conversations:all.conversations+m.conversations,automatic:all.automatic+m.automatic,human:all.human+m.human,errors:all.errors+m.errors}),{conversations:0,automatic:0,human:0,errors:0});
    $('studio-specialist-summary').textContent=metrics.conversations?`今日 ${metrics.conversations} 則 · 自動 ${metrics.automatic} · 人手 ${metrics.human} · 出錯 ${metrics.errors}`:`${state.summary.specialists.length} 位專員 · 尚無正式對話`;
    const tasks=await api('/records?kind=task&limit=100');const holderTasks=$('studio-dashboard-tasks');holderTasks.replaceChildren();tasks.records.filter(r=>!r.data.done).slice(0,5).forEach(r=>{const label=node('label','studio-check'),input=node('input');input.type='checkbox';input.addEventListener('change',()=>patch(r,{data:{done:true}}).catch(e=>app.notice(e.message,true)));label.append(input,node('span','',r.title));holderTasks.append(label);});
  }
  async function editFocus(){
    await loadChoices();await actionDialog('今日焦點','選擇最多三件今天要完成的事。',[],()=>{
      const selected=[...$('studio-focus-options').selectedOptions].map(o=>JSON.parse(o.value));if(selected.length>3)throw new Error('今日焦點最多三件。');return savePrefs({focus:selected}).then(renderOverview);
    });
    const select=node('select');select.id='studio-focus-options';select.multiple=true;select.size=8;select.setAttribute('aria-label','今日焦點');choices.filter(r=>['task','project','inbox'].includes(r.kind)).forEach(r=>{const o=node('option','',r.title);o.value=JSON.stringify({kind:r.kind,id:r.id});o.selected=state.preferences.data.focus?.some(ref=>ref.kind===r.kind&&ref.id===r.id);select.append(o);});$('studio-action-fields').append(select);
  }
  async function refresh(){
    if(!ready)return;const next=await api('/state');if(state.preferences.version>next.preferences.version)next.preferences=state.preferences;state=next;renderNavigation();preferencesPanel();await renderOverview();
  }
  async function search(query){const result=await api('/records?'+new URLSearchParams({q:query,limit:12}));return result;}
  function legacyView(view,collectionId){
    if(!ready||navigating)return;$('studio-panel').hidden=true;
    const value=view==='collection'?'topic/'+collectionId:NAV.aliases[view];if(!value)return;
    route=value;history.replaceState(null,'','#/'+value);renderNavigation(true);
    if(['dashboard','workdays','todos','files'].includes(view))renderPage().catch(e=>app.notice(e.message,true));
    else if(['links','youtube','unfiled','collection'].includes(view))navigate(value,{replace:true}).catch(e=>app.notice(e.message,true));
  }
  async function init(context){
    app=context;
    try{state=await api('/state');}catch(error){$('legacy-navigation').hidden=false;document.querySelector('.legacy-main-nav').classList.add('main-nav');app.notice('V2.4 工作功能未能載入。請先確認同一後台已完整升級。'+error.message,true);return;}
    ready=true;$('legacy-navigation').hidden=true;$('studio-sidebar-tools').hidden=false;$('studio-nav-scroll').hidden=false;$('studio-account').hidden=false;$('studio-mobile-command').hidden=false;
    const top=$('studio-sidebar-tools');top.append(button('搜尋全部 ⌘K',()=>app.openCommand(),'nav-item'),button('指揮中樞 ⌘J',()=>openCommand(),'nav-item'),button('＋ 新增',()=>quickAdd(),'nav-item'),button('收窄側欄',()=>savePrefs({narrow:!state.preferences.data.narrow}),'studio-narrow-toggle'));
    const company=document.querySelector('.workspace-label');company.tabIndex=0;company.setAttribute('role','button');company.setAttribute('aria-label','切換公司與工作空間');company.addEventListener('click',companySwitcher);company.addEventListener('keydown',event=>{if(['Enter',' '].includes(event.key)){event.preventDefault();companySwitcher();}});
    const bottom=document.querySelector('.sidebar-bottom');bottom.prepend(routeButton('archived','已封存','archive'));
    $('studio-account').addEventListener('click',()=>navigate('account'));
    $('studio-mobile-command').addEventListener('click',()=>openCommand());
    document.querySelectorAll('[data-studio-close]').forEach(b=>b.addEventListener('click',()=>b.closest('dialog').close()));
    $('studio-form').addEventListener('submit',event=>{event.preventDefault();busy(event.currentTarget,async()=>{
      const data=readFields($('studio-fields')),refs=[...$('studio-refs').selectedOptions].map(o=>JSON.parse(o.value));
      const payload={title:$('studio-record-title').value,data,refs};
      const result=await api(editing.record?recordUrl(editing.record.id):'/records',{method:editing.record?'PATCH':'POST',body:editing.record?{...payload,expected_version:editing.record.version}:{...payload,kind:editing.kind}});
      await refresh();if(result.record.kind==='topic')await navigate('topic/'+result.record.id);else await openRecord(result.record.id);closeDialog('studio-editor');
    },'studio-form-error');});
    $('studio-action-form').addEventListener('submit',event=>{event.preventDefault();busy(event.currentTarget,async()=>{await actionSubmit(readFields($('studio-action-fields')));closeDialog('studio-action-dialog');await refresh();},'studio-action-error');});
    $('studio-command-form').addEventListener('submit',event=>{event.preventDefault();busy(event.currentTarget,async()=>{
      const instruction=$('studio-command-input').value;const result=await api('/records',{method:'POST',body:{kind:'command',title:instruction.slice(0,80),data:{instruction}}});closeDialog('studio-command-dialog');await refresh();await openRecord(result.record.id);await handoff(result.record);
    },'studio-command-error');});
    $('studio-handoff-copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('studio-handoff-text').value);$('studio-handoff-status').textContent='已複製。';}catch{$('studio-handoff-text').select();$('studio-handoff-status').textContent='請複製已選取的文字。';}});
    window.addEventListener('popstate',()=>navigate(location.hash.replace(/^#\/?/,'')||'overview',{replace:true}).catch(e=>app.notice(e.message,true)));
    document.addEventListener('keydown',event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==='j'){event.preventDefault();openCommand();}});
    preferencesPanel();await loadChoices();
    const params=new URLSearchParams(location.search),old=params.get('view');let initial=location.hash.replace(/^#\/?/,'')||old||'overview';if(old==='collection'&&params.get('collection_id'))initial='topic/'+params.get('collection_id');
    await navigate(initial,{replace:true});
    const share=params.get('share_url')||params.get('share_text');if(share){try{const url=new URL(share);if(['http:','https:'].includes(url.protocol))addInspiration(url.href,params.get('share_title')||'');}catch{app.notice('分享的內容沒有有效網址。',true);}}
    if('serviceWorker' in navigator&&location.protocol==='https:')navigator.serviceWorker.register('/service-worker.js').catch(()=>{});
    initialized=true;
  }
  async function quickAdd(){
    await actionDialog('新增內容','選擇要加入甚麼。',[],()=>{});$('studio-action-submit').hidden=true;
    const options=[['task','待辦'],['project','項目'],['inspiration','靈感'],['customer','客戶'],['topic','主題'],...(admin()?[['specialist','AI 專員']]:[])];
    options.forEach(([kind,label])=>$('studio-action-fields').append(button(label,()=>{closeDialog('studio-action-dialog');$('studio-action-submit').hidden=false;return kind==='inspiration'?addInspiration():kind==='specialist'?navigate('templates'):editRecord(kind);})));$('studio-action-dialog').addEventListener('close',()=>{$('studio-action-submit').hidden=false;},{once:true});
  }
  root.BlueprintStudio={init,refresh,navigate,legacyView,search,openRecord,openCommand,isReady:()=>initialized,refreshVisible:async()=>{if(!ready||app.isBusy())return;await refresh();if(!route.startsWith('overview')&&route!=='documents')await renderPage();}};
})(globalThis);
