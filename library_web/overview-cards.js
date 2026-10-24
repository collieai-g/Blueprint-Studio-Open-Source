/* Overview cards retain references only; each visit reads current authorized data. */
'use strict';
((root)=>{
  const KEY='blueprint-overview-cards-v1',MAX_PINS=24;
  const MODULES={
    projects:{label:'工作項目',route:'projects',kind:'project'},
    inbox:{label:'收件匣',route:'inbox',kind:'inbox'},
    'ai-agents':{label:'Agent 工作台',route:'agents',collection:'agents'},
    'ai-skills':{label:'Skill 與交接',route:'skills',collection:'skills'},
    commands:{label:'工作指令',route:'command',kind:'command'}
  };
  const ROUTES=Object.fromEntries(Object.entries(MODULES).map(([id,value])=>[value.route,id]));
  const SKILLS={video:'個人化影片設計',general:'一般專業工作'};
  const ID=/^[A-Za-z0-9][A-Za-z0-9_-]{0,159}$/;
  const STATES={awaiting_codex:'等待目前 AI 對話處理',in_progress:'處理中',completed:'已完成',failed:'處理失敗',cancelled:'已取消',pending:'待處理',accepted:'已接收',running:'執行中',unknown:'結果待查證',draft:'草稿'};
  const text=(value,max=500)=>typeof value==='string'?value.slice(0,max):'';
  function reference(value){
    if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('卡片來源格式不正確。');
    if(value.kind==='route'){
      if(!Object.hasOwn(ROUTES,value.route))throw new Error('這個頁面未提供總覽卡片。');
      return {kind:'route',route:value.route};
    }
    if(value.kind==='todo'&&typeof value.workday_id==='string')return reference({kind:'workday',id:value.workday_id});
    if(!['studio_record','agent','agent_job','skill','workday','item'].includes(value.kind)||typeof value.id!=='string'||!ID.test(value.id))throw new Error('卡片來源格式不正確。');
    if(value.kind==='skill'&&!Object.hasOwn(SKILLS,value.id))throw new Error('找不到這份內置 Skill。');
    return {kind:value.kind,id:value.id};
  }
  function widgetId(value){
    const ref=reference(value);
    return ref.kind==='route'?ROUTES[ref.route]:'pin-'+ref.kind+'-'+ref.id;
  }
  function normalize(value){
    const refs=[],seen=new Set();
    if(value?.version===1&&Array.isArray(value.refs))for(const source of value.refs){
      try{const ref=reference(source),id=widgetId(ref);if(!seen.has(id)&&refs.length<MAX_PINS){seen.add(id);refs.push(ref);}}catch{/* Unsupported saved sources never become requests. */}
    }
    return {version:1,refs};
  }
  function scopeKey({origin='',identity,companyId=''}={}){
    const username=typeof identity==='string'?identity:identity?.username||'';
    return KEY+':'+[origin,companyId,username].map(value=>encodeURIComponent(String(value))).join('|');
  }
  function recordView(record){
    if(!record||typeof record.id!=='string'||!ID.test(record.id))throw new Error('未能讀取這張卡片。');
    const data=record.data||{};
    const summary=text(data.note||data.description||data.instruction||data.summary||data.content||data.company||data.role);
    const state=typeof data.state==='string'?STATES[data.state]||data.state:record.kind==='task'?(data.done?'已完成':'待完成'):'';
    return {title:text(record.title,240)||'工作內容',summary,state,route:'record/'+record.id,id:record.id,version:record.version};
  }
  function agentView(agent){
    if(!agent||typeof agent.id!=='string'||!ID.test(agent.id))throw new Error('未能讀取這張 Agent 卡片。');
    return {title:text(agent.manifest?.name,240)||'Agent',summary:text(agent.manifest?.description),
      state:agent.enabled===false?'已停用':agent.connection_state==='connected'?'已連接接口，工作由目前 AI 對話執行':'已加入，尚未連接',
      route:'agent/'+agent.id,id:agent.id,version:agent.version};
  }
  function jobView(job){
    if(!job||typeof job.id!=='string'||!ID.test(job.id))throw new Error('未能讀取這張工作卡片。');
    return {title:text(job.title,240)||'Agent 工作',summary:text(job.error||job.result),state:STATES[job.state]||text(job.state,80),route:'agent-job/'+job.id,id:job.id,version:job.version};
  }
  function skillView(id){return {title:SKILLS[id],summary:'準備專業規則及選定素材，交給目前使用的 AI。',state:'內置 Skill',route:'skills',id};}
  function workdayView(workday){
    if(!workday||typeof workday.id!=='string'||!ID.test(workday.id))throw new Error('未能讀取這份工作紀錄。');
    return {title:text(workday.title,240)||text(workday.date,10)+' 工作紀錄',summary:text(workday.summary),state:text(workday.date,10),route:'overview/workdays',kind:'workday',id:workday.id,version:workday.version};
  }
  function itemView(item){
    if(!item||typeof item.id!=='string'||!ID.test(item.id))throw new Error('未能讀取這份資料。');
    return {title:text(item.title||item.file_name,240)||'資料',summary:text(item.notes||item.extracted_text),state:item.archived?'已封存':item.kind==='file'?'文件':'收藏連結',route:'documents',kind:'item',id:item.id,version:item.version};
  }
  async function readReference(api,value){
    const ref=reference(value);
    if(ref.kind==='studio_record')return recordView((await api('/api/studio/records/'+encodeURIComponent(ref.id))).record);
    if(ref.kind==='agent')return agentView((await api('/api/studio/agents/'+encodeURIComponent(ref.id))).agent);
    if(ref.kind==='agent_job')return jobView((await api('/api/studio/agent-jobs/'+encodeURIComponent(ref.id))).job);
    if(ref.kind==='workday')return workdayView((await api('/api/workdays/'+encodeURIComponent(ref.id))).workday);
    if(ref.kind==='item')return itemView((await api('/api/items/'+encodeURIComponent(ref.id))).item);
    if(ref.kind==='skill')return skillView(ref.id);
    const module=MODULES[ROUTES[ref.route]];
    if(module.collection==='skills')return {title:module.label,route:module.route,items:Object.keys(SKILLS).map(skillView)};
    if(module.collection==='agents'){
      const data=await api('/api/studio/agents?limit=5&offset=0');
      return {title:module.label,route:module.route,total:data.total,items:(data.agents||[]).map(agentView)};
    }
    const data=await api('/api/studio/records?'+new URLSearchParams({kind:module.kind,limit:5,offset:0}));
    return {title:module.label,route:module.route,total:data.total,items:(data.records||[]).map(recordView)};
  }
  const helpers={KEY,MAX_PINS,MODULES,reference,widgetId,normalize,scopeKey,recordView,agentView,jobView,workdayView,itemView,readReference};
  if(typeof module!=='undefined'&&module.exports){module.exports=helpers;return;}
  let dependencies=null,storageKey='',saved={version:1,refs:[]},generation=0,subscribed=false;
  const widgets=new Map();
  const node=(tag,cls,copy)=>{const result=document.createElement(tag);if(cls)result.className=cls;if(copy!==undefined)result.textContent=String(copy);return result;};
  const button=(label,work,cls='text-button')=>{const result=node('button',cls,label);result.type='button';result.addEventListener('click',()=>Promise.resolve().then(work).catch(error=>dependencies?.notice?.(error.message,true)));return result;};
  function overviewVisible(){const panel=document.getElementById('dashboard-panel');return !!panel&&!panel.hidden;}
  function save(){
    try{localStorage.setItem(storageKey,JSON.stringify(normalize(saved)));}
    catch{throw new Error('這個瀏覽器無法保存總覽卡片。');}
  }
  async function removeReference(ref){
    const id=widgetId(ref);
    saved.refs=saved.refs.filter(source=>widgetId(source)!==id);save();
    if(root.BlueprintWorkspace.unregisterWidget)root.BlueprintWorkspace.unregisterWidget(id);else root.BlueprintWorkspace.setVisible(id,false);
    widgets.get(id)?.node.remove();widgets.delete(id);generation++;await refresh();
    dependencies.notice?.('已從總覽收起，原有資料仍然保留。');
  }
  function install(ref,label){
    const id=widgetId(ref);if(widgets.has(id))return widgets.get(id);
    const module=ref.kind==='route'?MODULES[id]:null;
    const article=node('article','workspace-widget');article.dataset.widget=id;article.dataset.overviewReference=ref.kind;article.hidden=true;
    const surface=node('div','widget-surface'),heading=node('header','widget-heading'),title=node('h3','',module?.label||text(label,240)||'已加入的卡片');
    const collapse=button('−',()=>{},'widget-collapse');collapse.dataset.boardAction='collapse';collapse.setAttribute('aria-expanded','true');collapse.setAttribute('aria-label','收起'+title.textContent);
    heading.append(title,collapse);
    if(!module){const remove=button('移除',()=>removeReference(ref),'text-button');remove.setAttribute('aria-label','從總覽移除此卡片');heading.append(remove);}
    const content=node('div','widget-content'),open=button('查看原頁面',()=>openReference(ref),'widget-open text-button');
    content.append(node('p','muted','在總覽開啟此卡片時讀取目前資料。'));
    surface.append(heading,content,open);article.append(surface);
    document.getElementById('workspace-board').append(article);
    const entry={id,ref,node:article,title,content};widgets.set(id,entry);
    root.BlueprintWorkspace.registerWidget({id,label:module?.label||text(label,240)||'已加入的卡片',node:article,defaults:[0,4,18,10]});
    return entry;
  }
  function renderItem(item,showTitle=true){
    const row=node('div','work-dashboard-entry');
    if(showTitle)row.append(button(item.title,()=>item.kind==='workday'&&dependencies.openWorkday?dependencies.openWorkday(item.id):item.kind==='item'&&dependencies.openItem?dependencies.openItem(item.id):dependencies.navigate(item.route),'work-title-button'));
    if(item.state)row.append(node('p','muted',item.state));
    if(item.summary)row.append(node('p','work-summary',item.summary));
    return row;
  }
  function openReference(ref){
    if(ref.kind==='workday')return dependencies.openWorkday?dependencies.openWorkday(ref.id):dependencies.navigate('overview/workdays');
    if(ref.kind==='item')return dependencies.openItem?dependencies.openItem(ref.id):dependencies.navigate('documents');
    return dependencies.navigate(ref.kind==='route'?ref.route:ref.kind==='studio_record'?'record/'+ref.id:ref.kind==='agent'?'agent/'+ref.id:ref.kind==='agent_job'?'agent-job/'+ref.id:'skills');
  }
  async function load(entry,run){
    entry.content.replaceChildren(node('p','muted','正在讀取目前資料…'));
    if(entry.ref.kind!=='route')entry.title.textContent='已加入的卡片';
    try{
      const result=await readReference(dependencies.api,entry.ref);
      if(run!==generation||!widgets.has(entry.id))return;
      entry.title.textContent=result.title;
      if(Array.isArray(result.items)){
        entry.content.replaceChildren(...result.items.map(item=>renderItem(item)));
        if(!result.items.length)entry.content.append(node('p','muted','目前沒有可顯示的內容。'));
        if(typeof result.total==='number')entry.content.append(node('p','muted','共 '+result.total+' 項'));
      }else entry.content.replaceChildren(renderItem(result,false));
    }catch(error){
      if(run!==generation||!widgets.has(entry.id))return;
      entry.title.textContent=entry.ref.kind==='route'?MODULES[entry.id].label:'已加入的卡片';
      entry.content.replaceChildren(node('p','muted',error.status===403||error.status===404?'來源已移除或目前沒有讀取權限。':'暫時未能讀取來源，請使用操作選單更新資料。'));
    }
  }
  async function refresh(ids){
    if(!dependencies||!overviewVisible())return;
    const run=++generation;
    const selected=[...widgets.values()].filter(entry=>(!ids||ids.includes(entry.id))&&root.BlueprintWorkspace.isVisible(entry.id));
    await Promise.all(selected.map(entry=>load(entry,run)));
  }
  async function pin(value){
    if(!dependencies)throw new Error('總覽尚未準備好。');
    const ref=reference(value),id=widgetId(ref),exists=saved.refs.some(source=>widgetId(source)===id);
    if(!exists){if(saved.refs.length>=MAX_PINS)throw new Error('最多加入 24 張跨頁卡片。請先從總覽收起不需要的卡片。');saved.refs.push(ref);save();}
    install(ref,value?.label);
    if(root.BlueprintWorkspace.setVisible(id,true)===false)throw new Error('總覽目前的空間不足。請先到總覽縮小卡片或增加一頁，再加入此卡片。');
    if(overviewVisible())await refresh();
    dependencies.notice?.('已加入總覽，可在總覽調整位置與大小。');
    return {id,reference:ref,created:!exists};
  }
  function init(options){
    dependencies=options;generation++;
    root.BlueprintWorkspace.setScope?.(options.identity?.username?location.origin+'|'+options.identity.username:'');
    const nextKey=scopeKey({origin:location.origin,identity:options.identity,companyId:options.companyId||''});
    if(storageKey&&storageKey!==nextKey){for(const entry of widgets.values()){root.BlueprintWorkspace.unregisterWidget?.(entry.id);entry.node.remove();}widgets.clear();}
    storageKey=nextKey;
    try{saved=normalize(JSON.parse(localStorage.getItem(storageKey)||'null'));}catch{saved={version:1,refs:[]};}
    for(const module of Object.values(MODULES))install({kind:'route',route:module.route});
    for(const ref of saved.refs)install(ref);
    if(!subscribed){
      subscribed=true;
      root.addEventListener('blueprint:overview-visibility',event=>{if(event.detail?.visible&&widgets.has(event.detail.id))refresh();});
    }
  }
  root.BlueprintOverviewCards={...helpers,init,pin,refresh,remove:removeReference};
})(globalThis);
