/* External records and durable command intentions. No service runtime or polling. */
'use strict';
((root)=>{
  const COMMAND_STATES={pending:'待處理',accepted:'已接收',running:'執行中',completed:'已完成',rejected:'已拒絕',expired:'已過期',failed:'執行失敗',unknown:'結果待查證'};
  const RECORD_TYPES={thread:'會話',message:'訊息',work_item:'工作',artifact:'成果',note:'備註',status:'外部狀態',knowledge:'公司知識'};
  const unsafeKeys=new Set(['__proto__','prototype','constructor']);
  const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
  function fieldsFor(schema){
    if(!schema||schema.type!=='object'||schema.additionalProperties!==false||!schema.properties||typeof schema.properties!=='object'||Array.isArray(schema.properties))throw new Error('這項操作的欄位規格不受支援。');
    const fields=Object.entries(schema.properties),required=schema.required||[];
    if(fields.length>32||!Array.isArray(required)||required.some(key=>!own(schema.properties,key)))throw new Error('這項操作的欄位規格不受支援。');
    return fields.map(([key,spec])=>{
      if(unsafeKeys.has(key)||!(/^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$/).test(key)||!spec||!['string','integer','number','boolean'].includes(spec.type)||own(spec,'$ref'))throw new Error('這項操作的欄位規格不受支援。');
      if(spec.enum&&(!Array.isArray(spec.enum)||!spec.enum.length||spec.enum.length>64))throw new Error('這項操作的選項規格不受支援。');
      return {key,...spec,required:required.includes(key)};
    });
  }
  function readPayload(schema,values){
    const result={};
    for(const spec of fieldsFor(schema)){
      let value=values[spec.key];
      if(value===undefined||value===null||(value===''&&spec.type!=='string')){
        if(spec.required)throw new Error('請填寫「'+(spec.title||spec.key)+'」。');
        continue;
      }
      if(spec.type==='string'){
        if(typeof value!=='string'||value.length>(spec.maxLength??40000)||value.includes('\x00')||(spec.required&&!value.trim()))throw new Error('請核對「'+(spec.title||spec.key)+'」的內容。');
        if(value===''&&!spec.required)continue;
      }else if(spec.type==='boolean'){
        if(typeof value!=='boolean')throw new Error('請核對「'+(spec.title||spec.key)+'」的選擇。');
      }else{
        if(typeof value!=='number'&&typeof value!=='string')throw new Error('請填寫有效數字。');
        if(typeof value==='string'&&!value.trim())throw new Error('請填寫有效數字。');
        value=Number(value);
        if(!Number.isFinite(value)||Math.abs(value)>Number.MAX_SAFE_INTEGER||(spec.type==='integer'&&!Number.isSafeInteger(value))||(spec.minimum!==undefined&&value<spec.minimum)||(spec.maximum!==undefined&&value>spec.maximum))throw new Error('請核對「'+(spec.title||spec.key)+'」的數字。');
      }
      if(spec.enum&&!spec.enum.some(option=>option===value))throw new Error('請使用「'+(spec.title||spec.key)+'」提供的選項。');
      result[spec.key]=value;
    }
    return result;
  }
  function approvedUrl(value,origins=[]){
    if(typeof value!=='string'||/[\x00-\x20\x7f]/.test(value))return '';
    try{const url=new URL(value);return url.protocol==='https:'&&!url.username&&!url.password&&origins.includes(url.origin)?url.href:'';}catch{return '';}
  }
  function commandIntent(integration,action,values,target,idempotencyKey){
    if(!integration.enabled||integration.revoked)throw new Error('這項服務已停用或撤銷，不能建立新指令。');
    if(!integration.commands.some(value=>value.name===action.name))throw new Error('這項操作已不在服務的能力宣告內。');
    if(typeof idempotencyKey!=='string'||!idempotencyKey)throw new Error('未能準備指令識別碼，請重新載入。');
    const result={integration_id:integration.id,name:action.name,payload:readPayload(action.input_schema,values),idempotency_key:idempotencyKey};
    if(target){
      if(target.integration_id!==integration.id)throw new Error('指定紀錄不屬於這項服務。');
      result.record_id=target.id||target.record_id;
      // These are service-issued conditions, never a platform version substitute.
      for(const key of ['control_epoch','input_cursor','expires_at'])if(own(target.payload||{},key))result[key]=target.payload[key];
    }
    return result;
  }
  function summary(payload){
    for(const key of ['text','content','summary','title','detail','body'])if(typeof payload?.[key]==='string')return payload[key].slice(0,1200);
    return JSON.stringify(payload||{},null,2).slice(0,1200);
  }
  const helpers={COMMAND_STATES,fieldsFor,readPayload,approvedUrl,commandIntent,summary};
  if(typeof module!=='undefined'&&module.exports){module.exports=helpers;return;}
  const node=(tag,cls,text)=>{const value=document.createElement(tag);if(cls)value.className=cls;if(text!==undefined)value.textContent=String(text);return value;};
  const dateLabel=value=>value&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString('zh-HK',{hour12:false}):value||'尚無紀錄';
  const drafts=new Map();
  let current,dirty=false,submitting=false,loadRequest=0,recordOffset=0,commandOffset=0;
  const api=(path,options)=>current.app.api('/api/integrations/v1'+path,options);
  const button=(label,run,cls='button secondary small')=>{const control=node('button',cls,label);control.type='button';control.addEventListener('click',()=>Promise.resolve().then(run).catch(error=>current.app.notice(error.message,true)));return control;};
  function heading(title,description,actions=[]){const holder=node('header','studio-page-header'),text=node('div'),controls=node('div','actions');text.append(node('h1','',title),node('p','muted',description));controls.append(...actions);holder.append(text,controls);return holder;}
  function status(value){const badge=node('span','badge integration-status',COMMAND_STATES[value]||value);badge.dataset.commandState=value;return badge;}
  function jsonDetails(title,value){const details=node('details','integration-evidence');details.append(node('summary','',title),node('pre','integration-json',JSON.stringify(value,null,2)));return details;}
  function recordCard(record,integration){
    const card=node('article','studio-card integration-record');card.dataset.integrationRecordId=record.id||record.record_id;
    card.append(node('h3','',RECORD_TYPES[record.record_type]||record.record_type),node('p','integration-copy',summary(record.payload)));
    card.append(node('p','muted','來源：'+record.source+' · '+dateLabel(record.occurred_at)),node('p','muted small-text','收到：'+dateLabel(record.received_at)+' · 版本 '+record.version+' · 來源版本 '+record.source_revision));
    if(record.record_type==='knowledge'){
      const state=record.payload?.status||record.payload?.state;
      card.append(node('p','integration-knowledge','公司知識狀態：'+({approved:'已批准',revoked:'已撤銷',candidate:'待審閱',draft:'草稿'}[state]||state||'未提供')));
      if(record.payload?.expires_at)card.append(node('p','muted','有效期至：'+dateLabel(record.payload.expires_at)));
    }
    const linkUrl=approvedUrl(record.payload?.url||record.payload?.external_url,integration.allowed_origins);
    if(linkUrl){const link=node('a','text-button','開啟外部個案');link.href=linkUrl;link.target='_blank';link.rel='noopener noreferrer';card.append(link);}
    card.append(jsonDetails('來源資料與識別碼',{record_id:record.id||record.record_id,external_id:record.external_id,event_id:record.event_id,payload:record.payload}));
    return card;
  }
  function commandCard(command,integration){
    const card=node('article','studio-card integration-command');card.dataset.commandId=command.id||command.command_id;
    card.append(node('h3','',integration.commands.find(value=>value.name===command.name)?.label||command.name),status(command.state));
    card.append(node('p','muted','提出者：'+command.actor+' · '+dateLabel(command.created_at)));
    if(command.state==='pending')card.append(node('p','integration-copy','指令已保存，等待外部服務回報。'));
    if(command.state==='unknown')card.append(node('p','integration-copy','外部服務尚未確認實際結果，請先查證。'));
    if(command.expires_at)card.append(node('p','muted','執行期限：'+dateLabel(command.expires_at)));
    card.append(jsonDetails('原始操作要求',{command_id:command.id||command.command_id,payload:command.payload,record_id:command.record_id,control_epoch:command.control_epoch,input_cursor:command.input_cursor}));
    if(command.results?.length){
      const history=node('ol','integration-results');
      command.results.forEach(result=>{const item=node('li');item.append(status(result.status),node('time','muted',dateLabel(result.occurred_at||result.received_at)),jsonDetails('外部服務回報',result.payload||{}));history.append(item);});
      card.append(history);
    }
    return card;
  }
  function pagination(data,offset,change){
    const holder=node('div','pagination');
    if(offset)holder.append(button('上一頁',()=>change(Math.max(0,offset-30))));
    holder.append(node('span','muted',`共 ${data.total||0} 項`));
    if(offset+30<(data.total||0))holder.append(button('下一頁',()=>change(offset+30)));
    return holder;
  }
  async function refreshEvidence(integration,holder,meta,{notifyLayout=true}={}){
    const context=current,request=++loadRequest;
    const [records,commands]=await Promise.all([api('/records?'+new URLSearchParams({integration_id:integration.id,limit:30,offset:recordOffset})),api('/commands?'+new URLSearchParams({integration_id:integration.id,limit:30,offset:commandOffset}))]);
    if(!context.isCurrent()||request!==loadRequest)return;
    if(notifyLayout)context.beforeRender?.();
    holder.replaceChildren();
    const timeline=node('section','integration-timeline'),results=node('section','integration-command-list');
    timeline.append(node('h2','','外部紀錄'));
    if(records.records.length)records.records.forEach(record=>timeline.append(recordCard(record,integration)));
    else timeline.append(node('p','muted','尚未收到外部紀錄。'));
    timeline.append(pagination(records,recordOffset,next=>{recordOffset=next;return refreshEvidence(integration,holder,meta);}));
    results.append(node('h2','','操作指令及結果'));
    if(commands.commands.length)commands.commands.forEach(command=>results.append(commandCard(command,integration)));
    else results.append(node('p','muted','尚未提出操作指令。'));
    results.append(pagination(commands,commandOffset,next=>{commandOffset=next;return refreshEvidence(integration,holder,meta);}));
    holder.append(timeline,results);
    meta.textContent='已讀取 '+dateLabel(new Date().toISOString())+'；外部資料以各筆「收到」時間為準。';
    if(notifyLayout&&context.isCurrent())context.onRendered?.();
    return records.records;
  }
  function inputFields(action,draft){
    const holder=node('div','integration-fields');
    for(const spec of fieldsFor(action.input_schema)){
      const label=node('label','field');label.append(node('span','',spec.title||spec.key));let input;
      if(spec.enum){
        input=node('select');if(!spec.required){const option=node('option','','未選擇');option.value='';input.append(option);}
        spec.enum.forEach((value,index)=>{const option=node('option','',String(value));option.value=String(index);input.append(option);});
        input.dataset.integrationEnum='true';
        const value=draft.values[spec.key];if(value!==undefined)input.value=String(spec.enum.findIndex(option=>option===value));
      }else if(spec.type==='boolean'){input=node('input');input.type='checkbox';input.checked=draft.values[spec.key]===true;}
      else if(spec.type==='string'){input=node('textarea');input.rows=3;input.maxLength=spec.maxLength??40000;input.value=draft.values[spec.key]??'';}
      else{input=node('input');input.type='number';input.step=spec.type==='integer'?'1':'any';if(spec.minimum!==undefined)input.min=spec.minimum;if(spec.maximum!==undefined)input.max=spec.maximum;input.value=draft.values[spec.key]??'';}
      input.name=spec.key;input.dataset.integrationField=spec.key;input.required=spec.required&&spec.type!=='boolean';label.append(input);holder.append(label);
    }
    return holder;
  }
  function formValues(holder,action){
    const values={};
    for(const input of holder.querySelectorAll('[data-integration-field]')){
      const key=input.dataset.integrationField,spec=action.input_schema.properties[key];
      values[key]=input.dataset.integrationEnum?(input.value===''?undefined:spec.enum[Number(input.value)]):spec.type==='boolean'?input.checked:input.value;
    }
    return values;
  }
  function randomKey(){
    if(!root.crypto?.getRandomValues)throw new Error('目前瀏覽器無法建立指令識別碼，請使用安全連線重新開啟。');
    return 'ui-'+Array.from(root.crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
  }
  function composer(integration,records,onSaved){
    const context=current,requestApi=(path,options)=>context.app.api('/api/integrations/v1'+path,options);
    const section=node('section','studio-detail integration-compose');section.append(node('h2','','提出操作指令'));
    if(!integration.enabled||integration.revoked){section.append(node('p','muted','這項服務已停用或撤銷，已保存紀錄仍可查看。'));return section;}
    if(!integration.commands.length){section.append(node('p','muted','這項服務沒有已核准的操作能力。'));return section;}
    const draft=drafts.get(integration.id)||{name:integration.commands[0].name,values:{},record_id:'',key:'',modified:false};drafts.set(integration.id,draft);dirty=!!draft.modified;
    let action=integration.commands.find(value=>value.name===draft.name)||integration.commands[0];draft.name=action.name;
    const form=node('form','integration-form');form.dataset.integrationForm='true';
    const actionLabel=node('label','field');actionLabel.append(node('span','','操作'));const actionSelect=node('select');actionSelect.name='command_name';integration.commands.forEach(item=>{const option=node('option','',item.label||item.name);option.value=item.name;actionSelect.append(option);});actionSelect.value=action.name;actionLabel.append(actionSelect);
    const targetLabel=node('label','field');targetLabel.append(node('span','','指定紀錄'));const targetSelect=node('select');targetSelect.name='record_id';const noTarget=node('option','','不指定');noTarget.value='';targetSelect.append(noTarget);
    records.forEach(record=>{const option=node('option','',(RECORD_TYPES[record.record_type]||record.record_type)+' · '+(record.external_id||record.id));option.value=record.id||record.record_id;targetSelect.append(option);});targetSelect.value=draft.record_id;targetLabel.append(targetSelect);
    let fields=inputFields(action,draft);const conditions=node('p','integration-conditions muted');
    const updateConditions=()=>{const target=records.find(record=>(record.id||record.record_id)===draft.record_id);conditions.textContent=target&&['control_epoch','input_cursor','expires_at'].some(key=>own(target.payload||{},key))?'會一併提交外部服務提供的控制版本、訊息位置或執行期限。':'';conditions.hidden=!conditions.textContent;};updateConditions();
    const error=node('p','form-error');error.hidden=true;error.setAttribute('role','alert');
    const submit=node('button','button primary small','保存指令');submit.type='submit';
    form.append(actionLabel,targetLabel,conditions,fields,node('p','muted','保存後顯示待處理，實際結果由外部服務回報。'),error,submit);
    const remember=()=>{draft.values=formValues(fields,action);draft.record_id=targetSelect.value;draft.key='';draft.modified=true;dirty=true;};
    fields.addEventListener('input',remember);fields.addEventListener('change',remember);
    targetSelect.addEventListener('change',()=>{remember();updateConditions();});
    actionSelect.addEventListener('change',()=>{
      action=integration.commands.find(value=>value.name===actionSelect.value);draft.name=action.name;draft.values={};draft.key='';draft.modified=true;dirty=true;
      const replacement=inputFields(action,draft);fields.replaceWith(replacement);fields=replacement;fields.addEventListener('input',remember);fields.addEventListener('change',remember);
    });
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(submitting||!context.isCurrent())return;submitting=true;error.hidden=true;submit.disabled=true;
      try{
        draft.values=formValues(fields,action);draft.record_id=targetSelect.value;draft.key=draft.key||randomKey();
        const submittedKey=draft.key;
        const target=records.find(record=>(record.id||record.record_id)===draft.record_id);
        if(draft.record_id&&!target)throw new Error('指定紀錄已不在這一頁，請重新選擇。');
        const result=await requestApi('/commands',{method:'POST',body:commandIntent(integration,action,draft.values,target,draft.key)});
        const commandId=result.command_id||result.command?.id||result.command?.command_id;
        if(!commandId)throw new Error('指令回應缺少識別碼，請保留內容並重試核對。');
        const saved=(await requestApi('/commands/'+encodeURIComponent(commandId))).command;
        if(!saved||(saved.id||saved.command_id)!==commandId||saved.integration_id!==integration.id)throw new Error('未能讀回同一筆指令，請保留內容並重試核對。');
        const preservedDraft=drafts.get(integration.id)===draft&&draft.key!==submittedKey;
        if(drafts.get(integration.id)===draft&&!preservedDraft)drafts.delete(integration.id);
        if(!context.isCurrent())return;
        dirty=preservedDraft;
        try{await onSaved(saved,{preserveDraft:preservedDraft});if(context.isCurrent())context.app.notice('指令已保存並讀回，請查看外部服務的後續回報。');}
        catch(problem){if(context.isCurrent())context.app.notice('指令已保存並讀回；未能更新列表：'+problem.message,true);}
      }catch(problem){if(context.isCurrent()){error.textContent=problem.message;error.hidden=false;}}
      finally{submitting=false;submit.disabled=false;}
    });
    section.append(form);return section;
  }
  async function workspace(){
    const context=current,data=await api('/registry');if(!context.isCurrent())return;
    const registered=data.integrations||[],panel=context.panel;
    if(!registered.length){context.beforeRender?.();panel.replaceChildren(heading('外部工作','外部服務提交的公司紀錄及操作結果。'),node('p','studio-empty','尚未登記外部服務。'));if(context.isCurrent())context.onRendered?.();return;}
    const selected=context.route.split('/')[1],integration=registered.find(value=>value.id===selected)||registered[0];
    const shell=node('div');shell.id='integration-workspace';shell.dataset.integrationId=integration.id;
    context.beforeRender?.();panel.replaceChildren(heading('外部工作','查看外部紀錄、提出操作指令及核對實際結果。'),shell);
    const selectLabel=node('label','field integration-select');selectLabel.append(node('span','','外部服務'));const select=node('select');select.name='integration_id';registered.forEach(item=>{const option=node('option','',item.name);option.value=item.id;select.append(option);});select.value=integration.id;select.addEventListener('change',()=>context.navigate('external-work/'+select.value).catch(error=>context.app.notice(error.message,true)));selectLabel.append(select);shell.append(selectLabel);
    const meta=node('p','muted integration-read-time'),evidence=node('div','integration-evidence-columns');
    const refresh=button('更新外部紀錄',async()=>{const scroll=window.scrollY;await refreshEvidence(integration,evidence,meta);window.scrollTo({top:scroll});});
    shell.append(node('p','studio-scope-note',(integration.revoked?'已撤銷':integration.enabled?'已登記':'已停用')+' · 服務版本 '+integration.app_version+' · 登記時間 '+dateLabel(integration.created_at)),refresh,meta);
    const composeSlot=node('div');shell.append(composeSlot,evidence);
    const records=await refreshEvidence(integration,evidence,meta,{notifyLayout:false});if(!context.isCurrent())return;
    const afterSave=async(command,{preserveDraft=false}={})=>{
      if(!context.isCurrent())return;
      context.beforeRender?.();
      try{
        if(!preserveDraft)composeSlot.replaceChildren(composer(integration,records||[],afterSave));
        commandOffset=0;await refreshEvidence(integration,evidence,meta,{notifyLayout:false});
      }finally{if(context.isCurrent())context.onRendered?.();}
      if(!context.isCurrent())return;
      evidence.querySelector('[data-command-id="'+CSS.escape(command.id||command.command_id)+'"]')?.scrollIntoView({block:'nearest'});
    };
    composeSlot.append(composer(integration,records||[],afterSave));
    shell.dataset.integrationsReady='true';
    if(context.isCurrent())context.onRendered?.();
  }
  async function mount(context){
    current=context;dirty=false;recordOffset=0;commandOffset=0;loadRequest++;
    if(!context.isCurrent())return;
    if(!context.state.capabilities?.integration_v1){context.beforeRender?.();context.panel.replaceChildren(heading('外部工作尚未提供','目前帳戶或後台未提供外部工作功能。'));if(context.isCurrent())context.onRendered?.();return;}
    try{await workspace();}catch(error){if(context.isCurrent()){context.beforeRender?.();context.panel.replaceChildren(heading('外部工作未能載入',error.message,[button('重新讀取',()=>mount(context))]));if(context.isCurrent())context.onRendered?.();}}
  }
  root.BlueprintIntegrations={...helpers,mount,isBusy:()=>dirty||submitting||!!document.activeElement?.closest('[data-integration-form]')};
})(globalThis);
