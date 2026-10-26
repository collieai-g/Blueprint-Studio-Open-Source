/* Company dates and attendance use the same authenticated, versioned backend. */
'use strict';
((root)=>{
  const BASE='/api/studio/calendar',KINDS=['attendance','work_logs','todos','deadlines','workdays'];
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function validDate(value){
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value))return false;
    const [year,month,day]=value.split('-').map(Number),date=new Date(0);
    date.setUTCFullYear(year,month-1,day);date.setUTCHours(0,0,0,0);
    return year>=1&&date.getUTCFullYear()===year&&date.getUTCMonth()===month-1&&date.getUTCDate()===day;
  }
  function validMonth(value){return typeof value==='string'&&/^\d{4}-\d{2}$/.test(value)&&validDate(value+'-01');}
  function dateString(date){return String(date.getUTCFullYear()).padStart(4,'0')+'-'+String(date.getUTCMonth()+1).padStart(2,'0')+'-'+String(date.getUTCDate()).padStart(2,'0');}
  function shiftMonth(value,offset){
    if(!validMonth(value)||!Number.isSafeInteger(offset))throw new Error('月份不正確。');
    const [year,month]=value.split('-').map(Number),date=new Date(0);date.setUTCFullYear(year,month-1+offset,1);
    const result=dateString(date).slice(0,7);if(!validMonth(result))throw new Error('月份超出支援範圍。');return result;
  }
  function monthGrid(value){
    if(!validMonth(value))throw new Error('月份需要使用 YYYY-MM。');
    const [year,month]=value.split('-').map(Number),first=new Date(0);first.setUTCFullYear(year,month-1,1);first.setUTCHours(0,0,0,0);
    const last=new Date(first);last.setUTCMonth(month,0);
    const offset=(first.getUTCDay()+6)%7,count=Math.ceil((offset+last.getUTCDate())/7)*7;
    return Array.from({length:count},(_,index)=>{const date=new Date(first);date.setUTCDate(1-offset+index);return {date:dateString(date),day:date.getUTCDate(),inMonth:date.getUTCMonth()===month-1};});
  }
  function timeParts(instant,timezone){
    const parts=new Intl.DateTimeFormat('en-CA',{timeZone:timezone,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(instant);
    return Object.fromEntries(parts.filter(part=>part.type!=='literal').map(part=>[part.type,part.value]));
  }
  function localTimeInput(value,timezone){
    if(!value)return'';const instant=new Date(value);if(Number.isNaN(instant.getTime()))throw new Error('時間不正確。');
    const p=timeParts(instant,timezone),millis=instant.getUTCMilliseconds();
    return p.year+'-'+p.month+'-'+p.day+'T'+p.hour+':'+p.minute+':'+p.second+(millis?'.'+String(millis).padStart(3,'0'):'');
  }
  function localTimeToUTC(value,timezone,required=false){
    if(!value){if(required)throw new Error('請填寫開始時間。');return null;}
    const match=typeof value==='string'&&value.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,3}))?)?$/);
    if(!match||!validDate(match[1])||Number(match[2])>23||Number(match[3])>59||Number(match[4]||0)>59)throw new Error('時間不正確。');
    const [year,month,day]=match[1].split('-').map(Number),wall=new Date(0),millis=Number((match[5]||'').padEnd(3,'0'));
    wall.setUTCFullYear(year,month-1,day);wall.setUTCHours(Number(match[2]),Number(match[3]),Number(match[4]||0),millis);
    const target=wall.getTime(),offsets=new Set(),candidates=new Set();
    for(const hours of [-36,-24,-12,0,12,24,36]){
      const sample=new Date(target+hours*3600000),p=timeParts(sample,timezone),local=new Date(0);
      local.setUTCFullYear(Number(p.year),Number(p.month)-1,Number(p.day));local.setUTCHours(Number(p.hour),Number(p.minute),Number(p.second),sample.getUTCMilliseconds());
      offsets.add(local.getTime()-sample.getTime());
    }
    for(const offset of offsets){const instant=new Date(target-offset),p=timeParts(instant,timezone);if(p.year===String(year).padStart(4,'0')&&p.month===match[1].slice(5,7)&&p.day===match[1].slice(8,10)&&Number(p.hour)===Number(match[2])&&Number(p.minute)===Number(match[3])&&Number(p.second)===Number(match[4]||0))candidates.add(instant.toISOString());}
    if(!candidates.size)throw new Error('這個本地時間因時區或夏令時間變更而不存在，請重新選擇。');
    if(candidates.size>1)throw new Error('這個本地時間因夏令時間變更出現兩次，不能猜測時刻，請重新選擇。');
    return [...candidates][0];
  }
  const helpers={validDate,validMonth,shiftMonth,monthGrid,localTimeInput,localTimeToUTC};
  if(typeof module!=='undefined'&&module.exports){module.exports=helpers;return;}
  const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e;};
  const button=(text,run,cls='button secondary small')=>{const e=node('button',cls,text);e.type='button';e.addEventListener('click',()=>Promise.resolve().then(run).catch(error=>showError(error.message)));return e;};
  const key=()=>root.crypto?.randomUUID?.()||'calendar-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2);
  let current=null,scope='',meta=null,snapshot=null,ui={month:'',date:'',employee:'',type:''},generation=0,loading=false,saving=false,dialog=null,clockIntent=null,clockNeedsRead=false;
  const drafts=new Map(),operations=new Map();let shell=null;
  const request=(context,path,options={})=>context.app.api(BASE+path,{...options,headers:{...options.headers,'X-Blueprint-Account':encodeURIComponent(context.state.identity.username)}});
  const active=()=>!!current?.isCurrent();
  function showError(message){if(!active()||!shell)return;shell.error.textContent=message||'未能完成要求。';shell.error.hidden=false;}
  function clearError(){if(shell){shell.error.textContent='';shell.error.hidden=true;}}
  function heading(title,description,actions=[]){const h=node('header','studio-page-header'),copy=node('div'),controls=node('div','actions');copy.append(node('h1','',title),node('p','muted',description));controls.append(...actions);h.append(copy,controls);return h;}
  function field(label,name,{type='text',value='',required=false,options=null,rows=3}={}){
    const wrap=node('label','calendar-field'),title=node('span','',label),input=node(options?'select':type==='textarea'?'textarea':'input');input.name=name;
    if(options)options.forEach(([id,text])=>{const option=node('option','',text);option.value=id;input.append(option);});
    else if(type==='textarea')input.rows=rows;else input.type=type;
    input.required=required;if(type==='checkbox')input.checked=!!value;else input.value=value??'';
    if(type==='text')input.maxLength=1000;else if(type==='textarea')input.maxLength=8000;
    wrap.append(title,input);return {wrap,input};
  }
  function values(form){return Object.fromEntries([...form.querySelectorAll('[name]')].map(input=>[input.name,input.type==='checkbox'?input.checked:input.value]));}
  function employeeName(id){return meta?.employees.find(employee=>employee.id===id)?.name||'公司共同待辦';}
  function typeName(value){return meta?.employment_types.find(type=>type.value===value)?.label||value;}
  function displayTime(value){if(!value)return'尚未離班';try{return new Intl.DateTimeFormat('zh-HK',{timeZone:meta.timezone,month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}catch{return'時間格式無法讀取';}}
  function ensureMeta(value){if(!object(value)||!validDate(value.today)||typeof value.timezone!=='string'||!Array.isArray(value.employees)||!Array.isArray(value.employment_types))throw new Error('後台未提供有效的公司日曆設定。');return value;}
  function ensureMonth(value,month){const dates=new Set(monthGrid(month).filter(day=>day.inMonth).map(day=>day.date));if(!object(value)||value.month!==month||typeof value.timezone!=='string'||!validDate(value.today)||!Array.isArray(value.days)||value.days.length!==dates.size||new Set(value.days.map(day=>day.date)).size!==dates.size||value.days.some(day=>!dates.has(day.date)||KINDS.some(kind=>!Array.isArray(day[kind])))||!Array.isArray(value.undated_todos))throw new Error('後台未提供完整的月份資料，請重新讀取。');return value;}
  function updateMeta(value){meta=ensureMeta(value);if(!ui.month){ui.month=meta.today.slice(0,7);ui.date=meta.today;}if(!validMonth(ui.month))ui.month=meta.today.slice(0,7);if(!validDate(ui.date))ui.date=ui.month+'-01';}
  function filteredEmployees(){return (meta?.employees||[]).filter(employee=>!ui.type||employee.employment_type===ui.type);}
  function options(select,entries,value){select.replaceChildren(...entries.map(([id,label])=>{const option=node('option','',label);option.value=id;return option;}));select.value=entries.some(([id])=>id===value)?value:'';}
  function updateFilters(){
    shell.month.value=ui.month;
    options(shell.type,[['','所有員工類型'],...meta.employment_types.map(type=>[type.value,type.label])],ui.type);
    options(shell.employee,[[ '',meta.admin?'全部可見員工':'本人及公司待辦'],...filteredEmployees().map(employee=>[employee.id,employee.name+(employee.active?'':'（已停用）')])],ui.employee);
    ui.employee=shell.employee.value;shell.type.disabled=loading;shell.employee.disabled=loading;
  }
  function setLoading(value){loading=value;if(!shell)return;shell.workspace.setAttribute('aria-busy',String(value));shell.refresh.disabled=value||saving;if(value){shell.status.textContent='正在讀取公司日曆…';shell.workspace.dataset.calendarReady='false';}shell.type.disabled=value;shell.employee.disabled=value;}
  async function reload({readMeta=true}={}){
    const context=current,sequence=++generation,month=ui.month,employee=ui.employee,type=ui.type;
    if(!context?.isCurrent())return;clearError();setLoading(true);
    try{
      const query=new URLSearchParams({month});if(employee)query.set('employee_id',employee);if(type)query.set('employment_type',type);
      const [nextMeta,next]=await Promise.all([readMeta?request(context,'/meta'):Promise.resolve(meta),request(context,'/month?'+query)]);
      if(!context.isCurrent()||sequence!==generation)return;
      snapshot=ensureMonth(next,month);updateMeta({...nextMeta,timezone:snapshot.timezone,today:snapshot.today,...(object(snapshot.settings)?{settings:snapshot.settings,settings_version:snapshot.settings.version}:{}),...(Object.hasOwn(snapshot,'current_attendance')?{current_attendance:snapshot.current_attendance}:{}),...(Object.hasOwn(snapshot,'self_employee')?{self_employee:snapshot.self_employee}:{})});if(readMeta)clockNeedsRead=false;updateFilters();renderClock();renderMonth();renderAgenda();
      shell.status.textContent='已讀取 '+month+' · 時區 '+meta.timezone;
      shell.workspace.dataset.calendarReady='true';
    }catch(error){if(context.isCurrent()&&sequence===generation){showError(error.message);shell.status.textContent='讀取失敗，原資料未視為最新結果。';}}
    finally{if(context.isCurrent()&&sequence===generation)setLoading(false);}
  }
  function selectedDay(){return snapshot?.days.find(day=>day.date===ui.date)||{date:ui.date,...Object.fromEntries(KINDS.map(kind=>[kind,[]]))};}
  function renderClock(){
    shell.clockCopy.replaceChildren();const self=meta.self_employee,session=meta.current_attendance;
    shell.clockCopy.append(node('strong','',self?self.name+' · 我的出勤':'我的出勤'));
    shell.clockCopy.append(node('span','',!self?'尚未綁定本人的員工檔案。請由管理員核對登入與員工對應。':session?'到班 '+displayTime(session.started_at)+'；尚未離班。':!self.active?'本人的員工檔案已停用，仍可查看歷史紀錄。':'目前沒有未結束的出勤時段。'));
    shell.clock.textContent=clockIntent?'重試原'+(clockIntent.action==='clock_in'?'到班':'離班')+'請求':session?'離班':'到班';
    shell.clock.dataset.action=clockIntent?.action||(session?'clock_out':'clock_in');shell.clock.disabled=saving||clockNeedsRead||!self||(!self.active&&!session&&clockIntent?.action!=='clock_out');shell.refresh.disabled=loading||saving;
    shell.addLog.disabled=saving||(!meta.admin&&(!self||!self.active));
    shell.workspace.querySelectorAll('input[data-calendar-entry-id]').forEach(input=>{input.disabled=saving||input.dataset.calendarEditable!=='true';});
    shell.clockHint.textContent='只記錄本人；目前篩選不會改變打卡對象。公司時區：'+meta.timezone;
  }
  function renderMonth(){
    shell.grid.replaceChildren();const days=new Map((snapshot?.days||[]).map(day=>[day.date,day]));
    for(const date of monthGrid(ui.month)){
      const day=days.get(date.date),counts=[];
      if(day){if(day.attendance.length)counts.push('出勤 '+day.attendance.length);if(day.todos.length)counts.push('待辦 '+day.todos.length);if(day.work_logs.length+day.workdays.length)counts.push('紀錄 '+(day.work_logs.length+day.workdays.length));if(day.deadlines.length)counts.push('期限 '+day.deadlines.length);}
      const b=button('',()=>{if(date.date.slice(0,7)!==ui.month){ui.month=date.date.slice(0,7);ui.date=date.date;updateFilters();reload({readMeta:false});}else{ui.date=date.date;renderMonth();renderAgenda();}},'calendar-date');
      b.dataset.calendarDate=date.date;b.classList.toggle('outside',!date.inMonth);b.classList.toggle('today',date.date===meta.today);b.setAttribute('aria-pressed',String(ui.date===date.date));b.setAttribute('aria-label',date.date+(counts.length?'，'+counts.join('，'):'，沒有紀錄'));
      b.append(node('span','calendar-date-number',date.day));const lines=node('span','calendar-date-counts');counts.forEach(text=>lines.append(node('span','',text)));b.append(lines);shell.grid.append(b);
    }
    shell.monthTitle.textContent=ui.month.replace('-',' 年 ')+' 月';
    shell.monthNote.textContent='日期格顯示數量，選擇日期查看原紀錄。期限是完成日期，出勤與工作紀錄是已發生的事；沒有紀錄不代表缺勤。';
  }
  function sourceButton(entry){
    const report=entry.open_report||entry.workday_id;
    if(report)return button('查看原工作紀錄',()=>root.BlueprintWorkdays.openReport(report),'text-button');
    const route=typeof entry.route==='string'?entry.route.replace(/^#\//,''):'';
    if(/^record\/[A-Za-z0-9_-]+$/.test(route))return button('查看原紀錄',()=>current.navigate(route),'text-button');
    return null;
  }
  function eventCard(entry,kind){
    const card=node('article','calendar-event');card.dataset.calendarSourceKind=entry.source_kind||kind;card.dataset.calendarSourceId=entry.id||'';
    const employee=entry.employee?.name||employeeName(entry.employee_id),actions=node('div','calendar-actions');
    if(kind==='attendance'){
      const end=entry.segment_end||entry.ended_at;
      card.append(node('span','calendar-event-title',employee),node('p','',displayTime(entry.segment_start||entry.started_at)+' — '+(end?displayTime(end):'尚未離班')));
      card.append(node('small','',entry.open?'原出勤時段仍在進行；此處按日顯示，不把暫時計時作薪資工時。':Number.isFinite(entry.minutes)?'本日所屬時段 '+entry.minutes+' 分鐘':'出勤時段'));
      if(meta.admin)actions.append(button('更正時段',()=>editAttendance(entry)),button('更正紀錄',()=>showHistory('attendance',entry.id)));
    }else{
      if(kind==='todos'&&entry.source_kind==='calendar_entry'){
        const label=node('label','calendar-todo-label'+(entry.done?' done':'')),input=node('input');input.type='checkbox';input.checked=!!entry.done;input.dataset.calendarEntryId=entry.id;input.dataset.calendarEditable=String(canEditTodo(entry));input.disabled=saving||!canEditTodo(entry);
        input.addEventListener('change',()=>toggleTodo(entry,input));label.append(input,node('span','',entry.title||'待辦'));card.append(label);
      }else card.append(node('span','calendar-event-title',entry.title||entry.summary||'工作紀錄'));
      if(entry.employee_id!==undefined)card.append(node('small','',employee));
      if(entry.notes||entry.summary)card.append(node('p','',entry.notes||entry.summary));
      if(kind==='work_logs'&&entry.minutes!==null&&entry.minutes!==undefined)card.append(node('small','','工作時間 '+entry.minutes+' 分鐘'));
      if(entry.source_kind==='calendar_entry'&&(meta.admin||kind==='work_logs'&&entry.employee_id===meta.self_employee?.id))actions.append(button('修改',()=>editEntry(entry)));
      const original=sourceButton(entry);if(original)actions.append(original);
      if(entry.done&&!(kind==='todos'&&entry.source_kind==='calendar_entry'))card.append(node('small','','已完成'));
    }
    if(actions.childNodes.length)card.append(actions);return card;
  }
  function canEditTodo(entry){return meta.admin||!!meta.self_employee?.active&&entry.employee_id===meta.self_employee.id;}
  function renderAgenda(){
    const day=selectedDay();shell.agenda.dataset.date=ui.date;shell.agendaList.replaceChildren();shell.dayTitle.textContent=ui.date;
    const labels={attendance:'出勤時段',todos:'公司待辦',work_logs:'員工工作紀錄',deadlines:'原有工作期限',workdays:'原有工作紀錄'};
    let total=0;for(const kind of ['attendance','todos','work_logs','deadlines','workdays']){
      const entries=day[kind]||[];if(!entries.length)continue;total+=entries.length;
      const section=node('section','calendar-agenda-section');section.append(node('h3','',labels[kind]));entries.forEach(entry=>section.append(eventCard(entry,kind)));shell.agendaList.append(section);
    }
    if(!total)shell.agendaList.append(node('p','calendar-empty','這個日期沒有目前可查看的紀錄。'));
    shell.undated.replaceChildren();const entries=snapshot?.undated_todos||[];shell.undated.hidden=!entries.length;
    if(entries.length){shell.undated.append(node('summary','','未排期公司待辦 · '+entries.length));entries.forEach(entry=>shell.undated.append(eventCard(entry,'todos')));}
  }
  async function toggleTodo(entry,input){
    if(saving){input.checked=!!entry.done;return;}const context=current,done=input.checked;input.disabled=true;saving=true;renderClock();
    try{const saved=await request(context,'/entries/'+encodeURIComponent(entry.id),{method:'PATCH',body:{expected_version:entry.version,done}});const checked=(await request(context,'/entries/'+encodeURIComponent(entry.id))).entry;if(!checked||checked.id!==entry.id||checked.done!==done||checked.version<saved.entry.version)throw new Error('未能核對同一筆待辦結果，請重新讀取。');if(context.isCurrent())await reload();}
    catch(error){if(context.isCurrent()){input.checked=!!entry.done;showError(error.message+' 請先重新讀取待辦狀態。');}}
    finally{saving=false;if(context.isCurrent()){input.disabled=!canEditTodo(entry);renderClock();}}
  }
  function closeDialog(){if(dialog?.open)dialog.close();}
  function newDialog(title,kind,draftKey){
    if(dialog?.isConnected)dialog.remove();
    const context=current,element=node('dialog','calendar-dialog'),form=node('form');element.id='calendar-dialog';element.setAttribute('aria-labelledby','calendar-dialog-title');form.dataset.calendarForm=kind;
    const top=node('div','dialog-heading'),h=node('h2','',title);h.id='calendar-dialog-title';const close=button('關閉',()=>element.close(),'text-button');close.setAttribute('aria-label','關閉'+title);top.append(h,close);form.append(top);
    const content=node('div'),error=node('p','form-error'),status=node('p','calendar-form-hint'),latest=node('div','calendar-latest');error.hidden=true;error.setAttribute('role','alert');latest.hidden=true;
    const actions=node('div','dialog-actions'),cancel=button('取消',()=>element.close()),submit=node('button','button primary','保存');submit.type='submit';actions.append(cancel,submit);form.append(content,error,status,latest,actions);element.append(form);context.panel.append(element);dialog=element;
    const holder={context,scope,element,form,content,error,status,latest,actions,submit,draftKey:scope+'|'+draftKey,version:null,source:null,receipt:null,intent:null,uncertain:false,baseDisabled:new Map()};
    form.addEventListener('input',()=>drafts.set(holder.draftKey,values(form)));form.addEventListener('change',()=>drafts.set(holder.draftKey,values(form)));
    element.addEventListener('cancel',event=>{if(saving)event.preventDefault();});element.addEventListener('close',()=>{if(!saving)element.remove();});
    return holder;
  }
  function rememberOperation(holder){if(holder.intent||holder.receipt||holder.uncertain)operations.set(holder.draftKey,{intent:holder.intent,receipt:holder.receipt,uncertain:holder.uncertain,version:holder.version,source:holder.source});else operations.delete(holder.draftKey);}
  function unlockControls(holder){const locked=!!holder.intent||!!holder.receipt||holder.uncertain;for(const e of holder.form.querySelectorAll('input,textarea,select,button'))e.disabled=!!holder.baseDisabled.get(e)||(locked&&e.matches('input,textarea,select'));holder.submit.textContent=holder.receipt?'讀回原保存結果':holder.intent?'重試原請求':'保存';}
  function recoverOperation(holder){const pending=operations.get(holder.draftKey);if(pending){Object.assign(holder,pending);holder.status.textContent='上次原請求仍待核對，資料欄位暫時鎖定，避免換成另一項工作。';}unlockControls(holder);if(holder.uncertain)offerReconcile(holder);}
  function present(holder){holder.form.querySelectorAll('input,textarea,select,button').forEach(e=>holder.baseDisabled.set(e,e.disabled));recoverOperation(holder);current.app.openDialog(holder.element.id);}
  function restore(holder,initial){const saved=drafts.get(holder.draftKey);for(const [name,value]of Object.entries(saved||initial)){const input=holder.form.elements.namedItem(name);if(input){if(input.type==='checkbox')input.checked=!!value;else input.value=value??'';}}}
  async function verifyReceipt(holder,path,kind){
    const value=(await request(holder.context,path))[kind];if(!value||value.id!==holder.receipt.id||!Number.isSafeInteger(value.version)||value.version<holder.receipt.version)throw new Error('未能讀回同一筆保存結果。請保留內容，再按保存以重新核對。');
    const keys=['name','employment_type','department','username','active','title','date','notes','minutes','done','employee_id','started_at','ended_at'];
    const different=keys.filter(key=>Object.hasOwn(holder.intent||{},key)&&(/_at$/.test(key)?(value[key]===null||holder.intent[key]===null?value[key]!==holder.intent[key]:Date.parse(value[key])!==Date.parse(holder.intent[key])):value[key]!==holder.intent[key]));
    if(different.length){const error=new Error(value.version>holder.receipt.version?'原保存結果其後已有更新，請核對最新內容。':'保存回應與讀回內容不一致，請先核對，不能另建一份。');if(value.version>holder.receipt.version){error.status=409;error.acknowledged=value;}throw error;}return value;
  }
  function showLatest(holder,value){holder.latest.replaceChildren(node('p','','目前後台版本 '+value.version));for(const [key,label]of [['name','姓名'],['title','內容'],['employment_type','員工類型'],['department','部門'],['username','登入帳戶'],['date','日期'],['notes','說明'],['timezone','時區']])if(value[key]!==undefined)holder.latest.append(node('p','',label+'：'+(key==='employment_type'?typeName(value[key]):value[key]||'未填')));if(value.active!==undefined)holder.latest.append(node('p','','啟用狀態：'+(value.active?'啟用':'停用')));if(value.done!==undefined)holder.latest.append(node('p','','完成狀態：'+(value.done?'已完成':'未完成')));if(value.started_at)holder.latest.append(node('p','','出勤：'+displayTime(value.started_at)+' — '+displayTime(value.ended_at)));holder.latest.hidden=false;}
  function offerAcknowledged(holder,value){
    showLatest(holder,value);if(holder.latest.querySelector('[data-acknowledge]'))return;
    const accept=button('確認已查看最新結果',async()=>{operations.delete(holder.draftKey);drafts.delete(holder.draftKey);holder.intent=null;holder.receipt=null;holder.element.close();holder.element.remove();if(holder.context.isCurrent()){await reload();holder.context.app.notice('原請求已保存，其後的更新亦已讀回；沒有重送原請求。');}});accept.dataset.acknowledge='true';holder.latest.append(accept);
  }
  function bindSave(holder,prepare,send,readback){
    holder.form.addEventListener('submit',async event=>{
      event.preventDefault();if(saving||!holder.context.isCurrent())return;holder.error.hidden=true;
      if(holder.uncertain){holder.error.textContent='上次結果未能確認，請先重新核對，再重試原請求。';holder.error.hidden=false;return;}
      let body;try{body=holder.intent||prepare(values(holder.form));}catch(error){holder.error.textContent=error.message;holder.error.hidden=false;return;}
      saving=true;renderClock();holder.form.querySelectorAll('input,textarea,select,button').forEach(e=>{e.disabled=true;});
      try{
        if(!holder.receipt){holder.intent=body;rememberOperation(holder);holder.receipt=await send(body);rememberOperation(holder);}
        await readback();drafts.delete(holder.draftKey);operations.delete(holder.draftKey);holder.status.textContent='已保存並讀回核對。';
        holder.element.close();holder.element.remove();holder.receipt=null;holder.intent=null;
        if(holder.context.isCurrent())await reload();
      }catch(error){
        holder.error.textContent=error.message;holder.error.hidden=false;
        if(error.acknowledged&&holder.receipt)offerAcknowledged(holder,error.acknowledged);
        else if(error.status===409&&!holder.receipt)offerConflict(holder);
        else if(!holder.receipt&&!(error.status>=400&&error.status<500&&![408,429].includes(error.status)))offerReconcile(holder);
        else if(!holder.receipt)holder.intent=null;
        rememberOperation(holder);
      }finally{saving=false;unlockControls(holder);if(holder.context.isCurrent())renderClock();}
    });
  }
  function offerReconcile(holder){
    holder.uncertain=true;rememberOperation(holder);if(holder.actions.querySelector('[data-reconcile]'))return;
    const check=button('重新核對原請求',async()=>{holder.error.hidden=true;try{await request(holder.context,'/meta');await reload();holder.uncertain=false;rememberOperation(holder);holder.status.textContent='已重新讀取目前狀態。若尚未確認，可按保存重試相同請求；請勿另建紀錄。';check.remove();unlockControls(holder);}catch(error){holder.error.textContent=error.message;holder.error.hidden=false;}});check.dataset.reconcile='true';holder.actions.prepend(check);
    holder.status.textContent='請求結果未知，本頁保留內容及原請求識別碼。先核對，避免重複新增；不要重新載入瀏覽器或另建紀錄。';
  }
  function offerConflict(holder){
    holder.intent=null;rememberOperation(holder);if(!holder.source||holder.actions.querySelector('[data-conflict]'))return;
    const check=button('查看最新版本',async()=>{
      const latest=(await request(holder.context,holder.source.path))[holder.source.kind];if(!latest)throw new Error('未能讀取最新版本。');
      showLatest(holder,latest);
      holder.latest.append(button('核對後沿用我的修改',()=>{holder.version=latest.version;holder.source.value=latest;holder.latest.hidden=true;holder.error.hidden=true;holder.status.textContent='已選用最新版本作比對；請核對草稿後再保存。';check.remove();}));
    });check.dataset.conflict='true';holder.actions.prepend(check);
  }
  function employeeOptions({company=false,all=false}={}){return [...(company?[['','公司共同待辦']]:[]),...(meta.employees||[]).filter(e=>all||e.active).map(e=>[e.id,e.name+' · '+typeName(e.employment_type)])];}
  function editEntry(entry=null,kind='work_log'){
    kind=entry?.kind||kind;const isTodo=kind==='todo',h=newDialog(entry?'修改'+(isTodo?'公司待辦':'工作紀錄'):isTodo?'新增公司待辦':'新增工作紀錄',kind,kind+':'+(entry?.id||'new'));
    h.version=entry?.version;h.source=entry?{path:'/entries/'+encodeURIComponent(entry.id),kind:'entry',value:entry}:null;
    const selected=ui.employee||(isTodo?'':meta.self_employee?.id||''),employee=field(isTodo?'指派員工':'員工','employee_id',{options:employeeOptions({company:isTodo}),value:entry?.employee_id??selected,required:!isTodo});
    if(!meta.admin){employee.input.replaceChildren();if(meta.self_employee){const option=node('option','',meta.self_employee.name);option.value=meta.self_employee.id;employee.input.append(option);}employee.input.disabled=true;}
    if(entry)employee.input.disabled=true;
    const title=field(isTodo?'要完成甚麼':'工作內容','title',{value:entry?.title||'',required:true}),date=field(isTodo?'到期日期（可不填）':'工作日期','date',{type:'date',value:entry?.date??ui.date,required:!isTodo}),notes=field('說明','notes',{type:'textarea',value:entry?.notes||''});
    h.content.append(employee.wrap,title.wrap,date.wrap,notes.wrap);
    if(!isTodo){const minutes=field('工作時間（分鐘，可不填）','minutes',{type:'number',value:entry?.minutes??''});minutes.input.min='0';minutes.input.max='1440';minutes.input.step='1';h.content.append(minutes.wrap);}else h.content.append(field('已完成','done',{type:'checkbox',value:entry?.done||false}).wrap);
    restore(h,{employee_id:entry?.employee_id??selected});
    bindSave(h,v=>{const body={title:v.title.trim(),date:v.date,notes:v.notes.trim()};if(!body.title)throw new Error('請填寫工作內容。');if(isTodo)body.done=v.done;else body.minutes=v.minutes===''?null:Number(v.minutes);return entry?{...body,expected_version:h.version}:{...body,kind,employee_id:meta.admin?v.employee_id:meta.self_employee?.id||'',request_key:key()};},async body=>(await request(h.context,entry?'/entries/'+encodeURIComponent(entry.id):'/entries',{method:entry?'PATCH':'POST',body})).entry,()=>verifyReceipt(h,'/entries/'+encodeURIComponent(h.receipt.id),'entry'));
    present(h);
  }
  function manageEmployees(selected=''){
    if(!selected){const pending=[...operations].find(([id])=>id.startsWith(scope+'|employee:'));if(pending)selected=pending[1].source?.value?.id||'';}
    const h=newDialog('員工檔案','employee','employee:'+selected),choose=field('選擇員工','selected_employee',{options:[['','新增員工'],...employeeOptions({all:true})],value:selected});h.content.append(choose.wrap);
    const fields=node('div'),name=field('員工姓名','name',{required:true}),type=field('員工類型','employment_type',{options:meta.employment_types.map(t=>[t.value,t.label]),value:'other'}),department=field('部門（選填）','department'),account=field('登入帳戶（可不綁定）','username',{options:[['','沒有登入帳戶'],...(meta.accounts||[]).map(a=>[a.username,a.name||a.username])]}),enabled=field('啟用員工檔案','active',{type:'checkbox',value:true});
    fields.append(name.wrap,type.wrap,department.wrap,account.wrap,enabled.wrap,node('p','calendar-form-hint','員工可沒有登入帳戶；綁定後不能改派其他人。員工類型不會授予管理權限。'));h.content.append(fields);
    let employee=meta.employees.find(e=>e.id===selected)||null;
    function fill(value){employee=value;h.version=value?.version;h.source=value?{path:'/employees/'+encodeURIComponent(value.id),kind:'employee',value}:null;h.draftKey=h.scope+'|employee:'+(value?.id||'new');name.input.value=value?.name||'';type.input.value=value?.employment_type||'other';department.input.value=value?.department||'';account.input.value=value?.username||'';enabled.input.checked=value?.active??true;account.input.disabled=!!value?.username;h.baseDisabled.set(account.input,account.input.disabled);restore(h,{});}
    fill(employee);choose.input.addEventListener('change',()=>{if(h.intent||h.receipt||h.uncertain){choose.input.value=employee?.id||'';return;}fill(meta.employees.find(e=>e.id===choose.input.value)||null);h.error.hidden=true;h.latest.hidden=true;recoverOperation(h);});
    bindSave(h,v=>({name:v.name.trim(),employment_type:v.employment_type,department:v.department.trim(),username:employee?.username||v.username||'',active:v.active,...(employee?{expected_version:h.version}:{request_key:key()})}),async body=>(await request(h.context,employee?'/employees/'+encodeURIComponent(employee.id):'/employees',{method:employee?'PATCH':'POST',body})).employee,()=>verifyReceipt(h,'/employees/'+encodeURIComponent(h.receipt.id),'employee'));
    present(h);
  }
  async function editAttendance(entry=null){
    const context=current;if(entry)entry=(await request(context,'/attendance/'+encodeURIComponent(entry.id))).attendance;if(!context.isCurrent())return;
    const h=newDialog(entry?'更正出勤時段':'補登出勤時段',entry?'attendance':'attendance_manual','attendance:'+(entry?.id||'new'));
    h.version=entry?.version;h.source=entry?{path:'/attendance/'+encodeURIComponent(entry.id),kind:'attendance',value:entry}:null;
    const employee=field('員工','employee_id',{options:employeeOptions({all:!!entry}),value:entry?.employee_id||ui.employee||meta.self_employee?.id||'',required:true});employee.input.disabled=!!entry;
    const timezone=meta.timezone,started=field('開始時間（'+timezone+'）','started_at',{type:'datetime-local',value:localTimeInput(entry?.started_at,timezone),required:true}),ended=field('結束時間（'+timezone+'，可不填）','ended_at',{type:'datetime-local',value:localTimeInput(entry?.ended_at,timezone)}),reason=field(entry?'更正原因':'補登原因','reason',{type:'textarea',required:true});started.input.step='0.001';ended.input.step='0.001';
    h.content.append(employee.wrap,started.wrap,ended.wrap,reason.wrap,node('p','calendar-form-hint','按公司時區 '+timezone+' 輸入，保存時保留實際時刻。夏令時間重複或不存在的本地時間會要求重新核對。'+(entry?'原時段：'+displayTime(entry.started_at)+' — '+displayTime(entry.ended_at):'')));
    restore(h,{});
    bindSave(h,v=>({...(entry?{expected_version:h.version}:{employee_id:v.employee_id,request_key:key()}),started_at:localTimeToUTC(v.started_at,timezone,true),ended_at:localTimeToUTC(v.ended_at,timezone),reason:v.reason.trim()}),async body=>(await request(h.context,entry?'/attendance/'+encodeURIComponent(entry.id):'/attendance/manual',{method:entry?'PATCH':'POST',body})).attendance,()=>verifyReceipt(h,'/attendance/'+encodeURIComponent(h.receipt.id),'attendance'));
    present(h);
  }
  function editSettings(){
    const h=newDialog('公司日曆設定','settings','calendar-settings'),timezone=field('公司時區（IANA 名稱）','timezone',{value:meta.timezone,required:true});h.version=meta.settings?.version??meta.settings_version;h.source={path:'/meta',kind:'settings',value:meta.settings};
    h.content.append(timezone.wrap,node('p','calendar-form-hint','例如 Asia/Hong_Kong。修改會影響日曆日期及時間的顯示，不會改寫已保存的實際時刻。'));restore(h,{});
    bindSave(h,v=>({timezone:v.timezone.trim(),expected_version:h.version}),async body=>{const result=(await request(h.context,'/settings',{method:'PATCH',body})).settings;if(!result)throw new Error('回應未提供設定。');return {id:'settings',version:result.version,timezone:result.timezone};},async()=>{const checked=await request(h.context,'/meta');if(checked.timezone!==h.receipt.timezone||(checked.settings?.version??checked.settings_version)<h.receipt.version)throw new Error('未能核對公司時區設定。');return checked;});
    present(h);
  }
  async function showHistory(kind,id){const context=current,result=await request(context,'/history?'+new URLSearchParams({kind,id}));if(!context.isCurrent())return;const h=newDialog('更正紀錄','history','history:'+kind+':'+id);h.submit.remove();const list=node('div','calendar-history');for(const version of result.versions||[]){const card=node('article');card.append(node('strong','','版本 '+version.version),node('p','',version.change_reason||'原始紀錄'),node('p','',version.started_at?displayTime(version.started_at)+' — '+displayTime(version.ended_at):version.title||version.name||''),node('small','',version.actor||''));list.append(card);}if(!list.childNodes.length)list.append(node('p','calendar-empty','尚未有歷史紀錄。'));h.content.append(list);present(h);}
  async function clock(){
    if(saving||clockNeedsRead||!meta.self_employee||(!meta.self_employee.active&&!meta.current_attendance&&clockIntent?.action!=='clock_out'))return;const context=current,selfId=meta.self_employee.id;
    if(!clockIntent){const session=meta.current_attendance;clockIntent={action:session?'clock_out':'clock_in',request_key:key(),...(session?{attendance_id:session.id,expected_version:session.version}:{})};}
    saving=true;renderClock();clearError();
    try{
      const response=await request(context,'/attendance',{method:'POST',body:clockIntent}),attendance=response.attendance;
      if(!attendance?.id)throw new Error('後台未提供出勤編號。');const checked=(await request(context,'/attendance/'+encodeURIComponent(attendance.id))).attendance;
      if(!checked||checked.id!==attendance.id||checked.employee_id!==selfId||checked.version<attendance.version)throw new Error('未能讀回同一筆本人的出勤紀錄。');
      clockIntent=null;if(context.isCurrent()){await reload();context.app.notice('本人出勤已保存並讀回核對。');}
    }catch(error){if(context.isCurrent()){clockNeedsRead=true;showError(error.message+' 請先重新讀取目前狀態，再重試原請求。');if(error.status>=400&&error.status<500&&![408,429].includes(error.status))clockIntent=null;}}
    finally{saving=false;if(context.isCurrent())renderClock();}
  }
  function build(context){
    const workspace=node('section');workspace.id='calendar-workspace';workspace.dataset.calendarReady='false';
    const actions=[],addLog=button('新增工作紀錄',()=>editEntry(null,'work_log'));addLog.id='calendar-add-work-log';actions.push(addLog);
    if(meta.admin){const todo=button('新增公司待辦',()=>editEntry(null,'todo'));todo.id='calendar-add-todo';const employees=button('員工管理',()=>manageEmployees());employees.id='calendar-manage-employees';actions.push(todo,employees);}
    workspace.append(heading('公司日曆','按月份、日期和員工查看出勤、工作紀錄及待辦。',actions));
    const toolbar=node('div','calendar-toolbar'),filters=node('div','calendar-filters');
    const month=field('月份','month',{type:'month',value:ui.month}),employee=field('員工','employee_id',{options:[]}),type=field('員工類型','employment_type',{options:[]});month.input.id='calendar-month';employee.input.id='calendar-employee';type.input.id='calendar-employment-type';filters.append(month.wrap,employee.wrap,type.wrap);
    const nav=node('div','calendar-actions'),previous=button('上個月',()=>changeMonth(-1)),today=button('本月',()=>{ui.month=meta.today.slice(0,7);ui.date=meta.today;updateFilters();reload({readMeta:false});}),next=button('下個月',()=>changeMonth(1)),refresh=button('更新日曆',()=>reload());refresh.id='calendar-refresh';nav.append(previous,today,next,refresh);toolbar.append(filters,nav);workspace.append(toolbar);
    const error=node('p','form-error'),status=node('p','calendar-status');error.id='calendar-error';error.hidden=true;error.setAttribute('role','alert');status.setAttribute('role','status');workspace.append(error,status);
    const clockPanel=node('section','studio-detail calendar-clock-row');clockPanel.id='calendar-personal-clock';const clockCopy=node('div','calendar-clock-copy'),clockActions=node('div','calendar-clock-actions'),clockButton=button('到班',clock,'button primary');clockButton.id='calendar-clock';clockActions.append(clockButton);clockPanel.append(clockCopy,clockActions);const clockHint=node('p','calendar-read-time');clockPanel.append(clockHint);workspace.append(clockPanel);
    const columns=node('div','calendar-columns'),monthPanel=node('section','studio-detail'),monthHead=node('div','calendar-panel-heading'),monthTitle=node('h2'),weekdays=node('div','calendar-weekdays'),grid=node('div','calendar-month-grid'),monthNote=node('p','calendar-legend');monthPanel.id='calendar-month-panel';grid.id='calendar-month-grid';for(const day of ['一','二','三','四','五','六','日'])weekdays.append(node('span','','週'+day));monthHead.append(monthTitle);monthPanel.append(monthHead,weekdays,grid,monthNote);
    const agenda=node('section','studio-detail'),dayHead=node('div','calendar-panel-heading'),dayTitle=node('h2'),agendaList=node('div');agenda.id='calendar-day-agenda';dayHead.append(dayTitle);agenda.append(dayHead,agendaList);columns.append(monthPanel,agenda);workspace.append(columns);
    const undated=node('details','calendar-undated');undated.id='calendar-undated-todos';undated.hidden=true;workspace.append(undated);
    if(meta.admin){const advanced=node('details','calendar-undated'),controls=node('div','calendar-actions'),manual=button('補登出勤時段',()=>editAttendance()),settings=button('公司時區設定',editSettings);manual.id='calendar-add-manual-attendance';settings.id='calendar-settings';advanced.append(node('summary','','管理操作'),controls);controls.append(manual,settings);workspace.append(advanced);}
    context.beforeRender?.();context.panel.replaceChildren(workspace);shell={workspace,month:month.input,employee:employee.input,type:type.input,refresh,error,status,clock:clockButton,clockCopy,clockHint,addLog,grid,monthTitle,monthNote,agenda,dayTitle,agendaList,undated};
    month.input.addEventListener('change',()=>{if(!validMonth(month.input.value)){showError('請選擇有效月份。');return;}ui.month=month.input.value;ui.date=ui.month+'-01';reload({readMeta:false});});
    employee.input.addEventListener('change',()=>{ui.employee=employee.input.value;reload({readMeta:false});});
    type.input.addEventListener('change',()=>{ui.type=type.input.value;if(!filteredEmployees().some(e=>e.id===ui.employee))ui.employee='';updateFilters();reload({readMeta:false});});
    updateFilters();renderClock();context.onRendered?.();
  }
  function changeMonth(offset){ui.month=shiftMonth(ui.month,offset);ui.date=ui.month+'-01';updateFilters();return reload({readMeta:false});}
  async function mount(context){
    current=context;const nextScope=location.origin+'|'+context.state.identity.username;
    if(nextScope!==scope){scope=nextScope;ui={month:'',date:'',employee:'',type:''};drafts.clear();operations.clear();clockIntent=null;clockNeedsRead=false;snapshot=null;meta=null;}
    const sequence=++generation;
    if(context.state.capabilities?.company_calendar!==true){context.beforeRender?.();context.panel.replaceChildren(heading('公司日曆尚未提供','目前後台尚未支援公司日曆，請使用原有工作頁面。'));context.onRendered?.();return;}
    try{const value=await request(context,'/meta');if(!context.isCurrent()||sequence!==generation)return;updateMeta(value);build(context);await reload({readMeta:false});}
    catch(error){if(context.isCurrent()&&sequence===generation){context.beforeRender?.();context.panel.replaceChildren(heading('公司日曆未能載入',error.message,[button('重新讀取',()=>mount(context))]));context.onRendered?.();}}
  }
  root.BlueprintCalendar={...helpers,mount,isBusy:()=>saving||!!dialog?.isConnected&&dialog.open};
})(globalThis);
