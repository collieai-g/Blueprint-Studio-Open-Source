/* Account-scoped presentation sync. Only this endpoint is polled; company views stay live. */
'use strict';
((root)=>{
  const ENDPOINT='/api/studio/presentation',DRAFT_KEY='blueprint-presentation-drafts-v1',MISSING=Symbol('missing');
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
  const forbidden=key=>['__proto__','prototype','constructor'].includes(key);
  const validKey=key=>typeof key==='string'&&(key==='overview'||key==='pins'||(/^page:[A-Za-z0-9][A-Za-z0-9:_.\/-]{0,299}$/.test(key)&&!forbidden(key.slice(5))));
  const copy=value=>value===undefined?undefined:JSON.parse(JSON.stringify(value));
  function equal(a,b){
    if(a===b)return true;
    if(Array.isArray(a)&&Array.isArray(b))return a.length===b.length&&a.every((value,index)=>equal(value,b[index]));
    if(object(a)&&object(b)){const keys=Object.keys(a);return keys.length===Object.keys(b).length&&keys.every(key=>own(b,key)&&equal(a[key],b[key]));}
    return false;
  }
  function safeData(value,depth=0){
    if(depth>16)return false;
    if(value===null||typeof value==='string'||typeof value==='boolean')return true;
    if(typeof value==='number')return Number.isFinite(value);
    if(Array.isArray(value))return value.length<=500&&value.every(item=>safeData(item,depth+1));
    return object(value)&&Object.keys(value).length<=500&&Object.entries(value).every(([key,item])=>!forbidden(key)&&safeData(item,depth+1));
  }
  function documentData(value){
    if(!object(value)||!safeData(value)||JSON.stringify(value).length>512*1024)throw new Error('版面設定格式不正確或超出大小限制。');
    return copy(value);
  }
  // Missing keys are meaningful deletions. Arrays are atomic selections, not index patches.
  function merge(base,local,remote){
    const conflicts=[];
    function visit(before,mine,theirs,path){
      if(equal(mine,before))return theirs;
      if(equal(theirs,before)||equal(mine,theirs))return mine;
      if(object(mine)&&object(theirs)&&(object(before)||before===MISSING)){
        const result={},keys=new Set([...Object.keys(before===MISSING?{}:before),...Object.keys(mine),...Object.keys(theirs)]);
        for(const key of keys){
          if(forbidden(key))continue;
          const next=visit(before!==MISSING&&own(before,key)?before[key]:MISSING,own(mine,key)?mine[key]:MISSING,own(theirs,key)?theirs[key]:MISSING,[...path,key]);
          if(next!==MISSING)result[key]=next;
        }
        return result;
      }
      conflicts.push(path);return mine;
    }
    const data=visit(base===undefined?MISSING:base,local===undefined?MISSING:local,remote===undefined?MISSING:remote,[]);
    return {data:data===MISSING?undefined:copy(data),conflicts};
  }
  function at(value,path){for(const key of path){if(!object(value)&&!Array.isArray(value)||!own(value,key))return MISSING;value=value[key];}return value;}
  const uniquePaths=paths=>[...new Map(paths.map(path=>[JSON.stringify(path),path])).values()];
  const messages={starting:'正在讀取帳戶版面…',synced:'版面已同步',pending:'版面等待同步…',saving:'正在同步版面…',offline:'暫時未能連接；版面修改保留在此裝置，連線後重試。',conflict:'另一處亦修改了相同設定，請選擇保留哪個版本。',unsupported:'此後台尚未支援版面同步；修改只保存在此瀏覽器。',unauthorized:'登入或權限已失效；版面修改保留在此裝置，請重新登入。',error:'版面未能同步；修改已保留，請檢查後重試。',diverged:'後台版面版本與此裝置不一致，可能已還原備份。修改已保留，請重新核對版本後選擇保留的內容。',stopped:'版面同步已停止'};
  function create(options){
    if(typeof options?.api!=='function'||typeof options.scope!=='string'||!options.scope||options.scope.length>1600)throw new Error('版面同步需要已登入帳戶。');
    const {api,scope}=options;let storage=options.storage;
    if(storage===undefined){try{storage=root.localStorage;}catch{storage=null;}}
    const username=options.username;
    if(username!==undefined&&(typeof username!=='string'||!username||username.length>128||/[\x00-\x1f\x7f]/.test(username)))throw new Error('版面同步需要有效的登入帳戶。');
    const headers=username?{'X-Blueprint-Account':encodeURIComponent(username)}:{};
    const timer=options.setTimeout||root.setTimeout.bind(root),clear=options.clearTimeout||root.clearTimeout.bind(root);
    const visible=options.isVisible||(()=>!root.document||root.document.visibilityState!=='hidden');
    const pollMs=options.pollMs??2000,debounceMs=options.debounceMs??300;
    let clientId=options.clientId||'default';
    if(!options.clientId){try{
      const session=options.sessionStorage??root.sessionStorage;
      if(session){
        const name=DRAFT_KEY+':client:'+encodeURIComponent(scope);
        const navigationType=options.navigationType??root.performance?.getEntriesByType?.('navigation')?.[0]?.type;
        // A duplicated tab inherits sessionStorage but is a new navigation. It must
        // not overwrite the original tab's offline draft. Reload restores its own ID.
        const reuse=navigationType===undefined||['reload','back_forward'].includes(navigationType);
        clientId=(reuse&&session.getItem(name))||root.crypto?.randomUUID?.()||Math.random().toString(36).slice(2);session.setItem(name,clientId);
      }
    }catch{clientId=root.crypto?.randomUUID?.()||Math.random().toString(36).slice(2);/* Keep blocked-session tabs from overwriting each other. */}}
    const storageKey=DRAFT_KEY+':'+encodeURIComponent(scope)+':'+encodeURIComponent(clientId);
    const remote=new Map(),pending=new Map(),listeners=new Map(),adoptions=new Map();
    let running=true,initialized=false,supported=null,cursor=-1,state='starting',storageError=storage===null,sequence=0,diverged=false;
    let pollTimer=null,writeTimer=null,reading=null,writing=null,flight=null,readCount=0;
    function effective(key){return pending.get(key)?.data??remote.get(key)?.data;}
    function status(){return {state,message:messages[state],pending:[...pending.keys()],conflicts:[...pending].filter(([,entry])=>entry.conflicts.length).map(([key,entry])=>({key,paths:copy(entry.conflicts)})),errors:[...pending].filter(([,entry])=>entry.errorStatus).map(([key,entry])=>({key,status:entry.errorStatus})),storageError,diverged};}
    function report(next){
      if(!running&&next!=='stopped')return;
      if(next)state=next;
      else if(diverged)state='diverged';
      else if([...pending.values()].some(entry=>entry.conflicts.length))state='conflict';
      else if([...pending.values()].some(entry=>entry.errorStatus))state='error';
      else if(writing)state='saving';else if(!initialized)state='starting';else state=pending.size?'pending':'synced';
      options.onStatus?.(status());
    }
    function emit(key,source){
      const data=source==='resolved'&&effective(key)===undefined?{}:copy(effective(key)),meta={source,version:remote.get(key)?.version||0,conflict:!!pending.get(key)?.conflicts.length};
      for(const listener of listeners.get(key)||[])listener(copy(data),meta);
      options.onChange?.(key,copy(data),meta);
    }
    function remember(){
      try{
        if(pending.size)storage?.setItem(storageKey,JSON.stringify({version:1,scope,entries:[...pending].map(([key,entry])=>({key,base:entry.base??null,baseVersion:entry.baseVersion,data:entry.data,conflicts:entry.conflicts,errorStatus:entry.errorStatus||null}))}));
        else storage?.removeItem(storageKey);
        storageError=!storage;
      }catch{storageError=true;}
    }
    try{
      const raw=storage?.getItem(storageKey),saved=raw&&raw.length<2*1024*1024?JSON.parse(raw):null;
      if(saved?.version===1&&saved.scope===scope&&Array.isArray(saved.entries)&&saved.entries.length<=256)for(const entry of saved.entries){
        if(!validKey(entry.key)||!Number.isSafeInteger(entry.baseVersion)||entry.baseVersion<0)continue;
        try{pending.set(entry.key,{base:entry.base===null?undefined:documentData(entry.base),baseVersion:entry.baseVersion,data:documentData(entry.data),conflicts:Array.isArray(entry.conflicts)?entry.conflicts.filter(path=>Array.isArray(path)&&path.length<=16&&path.every(part=>typeof part==='string'&&!forbidden(part))):[],errorStatus:Number.isInteger(entry.errorStatus)&&entry.errorStatus>=400&&entry.errorStatus<500?entry.errorStatus:undefined,seq:++sequence});}catch{/* Ignore a malformed local draft, never submit it. */}
      }
    }catch{/* Existing layout caches remain untouched. */}
    function rebase(key,incoming,source='remote'){
      const known=remote.get(key);
      if(known&&incoming.version<known.version)return;
      if(known&&incoming.version===known.version&&!equal(incoming.data,known.data))throw divergenceError();
      const before=copy(effective(key)),entry=pending.get(key);
      remote.set(key,copy(incoming));
      if(entry){
        // A GET may observe our own write before its PATCH response arrives.
        const self=flight?.key===key&&incoming.version===flight.baseVersion+1&&equal(incoming.data,flight.data);
        const result=merge(self?flight.data:entry.base,entry.data,incoming.data);
        entry.base=copy(incoming.data);entry.baseVersion=incoming.version;entry.data=result.data;
        entry.conflicts=uniquePaths([...entry.conflicts,...result.conflicts]).filter(path=>!equal(at(entry.data,path),at(incoming.data,path)));
        if(equal(entry.data,incoming.data))pending.delete(key);
      }
      if(!equal(before,effective(key)))emit(key,source);
    }
    function validateResponse(value){
      if(!object(value)||!Number.isSafeInteger(value.version)||value.version<0)throw new Error('後台未提供有效版面版本。');
      if(value.unchanged===true)return value;
      if(!object(value.documents)||Object.keys(value.documents).length>256)throw new Error('後台未提供有效版面設定。');
      for(const [key,doc]of Object.entries(value.documents)){
        if(!validKey(key)||!object(doc)||!Number.isSafeInteger(doc.version)||doc.version<1)throw new Error('後台未提供有效版面設定。');
        documentData(doc.data);
      }
      return value;
    }
    function divergenceError(){return Object.assign(new Error(messages.diverged),{code:'presentation_divergence',status:409});}
    function accept(value,full,baseline=remote){
      const response=validateResponse(value);
      if(full&&response.version<cursor)throw divergenceError();
      if(full&&!response.unchanged)for(const [key,doc]of baseline){
        if(!own(response.documents,key)||response.documents[key].version<doc.version)throw divergenceError();
      }
      if(!response.unchanged)for(const [key,doc]of Object.entries(response.documents)){
        const known=remote.get(key),draft=pending.get(key);
        if(known&&doc.version===known.version&&!equal(doc.data,known.data))throw divergenceError();
        if(full&&!initialized&&draft&&(doc.version<draft.baseVersion||(doc.version===draft.baseVersion&&!equal(doc.data,draft.base))))throw divergenceError();
      }
      if(full&&!initialized&&!response.unchanged)for(const [key,draft]of pending){
        if(draft.baseVersion>0&&!own(response.documents,key))throw divergenceError();
      }
      if(!response.unchanged)for(const [key,doc]of Object.entries(response.documents))rebase(key,doc);
      if(full)cursor=response.version;
      remember();return true;
    }
    function fail(error){
      const code=error?.status;
      if(error?.code==='presentation_divergence'){diverged=true;report('diverged');cancelTimers();}
      else if(code===401||code===403){supported=false;report('unauthorized');cancelTimers();}
      else if(code===404||code===405||code===501){supported=false;report('unsupported');cancelTimers();}
      else report(code&&code<500?'error':'offline');
    }
    function cancelTimers(){if(pollTimer!==null)clear(pollTimer);if(writeTimer!==null)clear(writeTimer);pollTimer=writeTimer=null;}
    function schedulePoll(){
      if(!running||diverged||supported===false||pollMs<=0||pollTimer!==null)return;
      pollTimer=timer(async()=>{pollTimer=null;if(visible())await pull();schedulePoll();},pollMs);
    }
    function scheduleWrite(){
      if(!running||diverged||!initialized||supported!==true||writeTimer!==null)return;
      if(![...pending.values()].some(entry=>!entry.conflicts.length&&!entry.errorStatus))return;
      writeTimer=timer(()=>{writeTimer=null;flush();},debounceMs);
    }
    async function pull({full=false}={}){
      if(!running||diverged||supported===false)return false;
      if(reading)return reading;
      const baseline=new Map(remote);readCount++;
      reading=(async()=>{
        try{
          // Periodic full reads also detect restored histories with a reused revision.
          const response=await api(ENDPOINT+(cursor>=0&&!full&&readCount%15!==0?'?since_version='+cursor:''),{headers});
          if(!running)return false;
          accept(response,true,baseline);initialized=true;supported=true;
          for(const [key,data]of adoptions){adoptions.delete(key);if(!remote.has(key)&&!pending.has(key))update(key,data);}
          report();scheduleWrite();return true;
        }catch(error){if(running)fail(error);return false;}
        finally{reading=null;if(running)schedulePoll();}
      })();
      return reading;
    }
    function update(key,value,change={}){
      if(!running)throw new Error('版面同步已停止。');
      if(!validKey(key))throw new Error('版面識別碼不正確。');
      const requested=documentData(value),previous=pending.get(key),known=remote.get(key),current=effective(key);
      const result=own(change,'base')&&current!==undefined?merge(change.base,requested,current):{data:requested,conflicts:[]};
      const conflicts=uniquePaths([...(previous?.conflicts||[]),...result.conflicts]);
      if(equal(result.data,known?.data)){pending.delete(key);}
      else pending.set(key,{base:copy(previous?previous.base:known?.data),baseVersion:previous?.baseVersion??known?.version??0,data:result.data,conflicts,seq:++sequence});
      remember();emit(key,'local');
      if(!diverged&&supported!==false&&!['offline','error'].includes(state))report();else options.onStatus?.(status());
      scheduleWrite();return copy(effective(key));
    }
    function adopt(key,value){
      if(!validKey(key))throw new Error('版面識別碼不正確。');
      if(!running||diverged||supported===false||remote.has(key)||pending.has(key))return false;
      const data=documentData(value);
      if(!initialized||supported!==true){if(adoptions.size<256)adoptions.set(key,data);return false;}
      update(key,data);return true;
    }
    async function flush(){
      if(writeTimer!==null){clear(writeTimer);writeTimer=null;}
      if(!running||diverged||supported===false)return false;
      if(!initialized&&!(await pull()))return false;
      if(writing)return writing;
      writing=(async()=>{
        let passes=0;
        while(running&&!diverged&&supported===true&&passes++<64){
          const item=[...pending].find(([,entry])=>!entry.conflicts.length&&!entry.errorStatus);
          if(!item)break;
          const [key,entry]=item,sent={key,baseVersion:entry.baseVersion,data:copy(entry.data),seq:entry.seq};flight=sent;report('saving');
          try{
            const response=validateResponse(await api(ENDPOINT,{method:'PATCH',headers,body:{key,expected_version:sent.baseVersion,data:sent.data}}));
            if(!running||diverged)return false;
            const ack=response.documents?.[key];
            if(!ack||ack.version<sent.baseVersion+1)throw new Error('後台未確認保存的版面。');
            const current=pending.get(key),latest=remote.get(key);
            if(current){
              if(current.seq===sent.seq&&equal(current.data,sent.data))pending.delete(key);
              else{
                const target=latest&&latest.version>ack.version?latest:ack,result=merge(sent.data,current.data,target.data);
                current.base=copy(target.data);current.baseVersion=target.version;current.data=result.data;
                current.conflicts=uniquePaths([...current.conflicts,...result.conflicts]).filter(path=>!equal(at(current.data,path),at(target.data,path)));
                if(equal(current.data,target.data))pending.delete(key);
              }
            }
            accept(response,false);emit(key,'remote');remember();flight=null;
          }catch(error){
            flight=null;if(!running)return false;
            if(error.code==='presentation_divergence'){fail(error);return false;}
            if(error.status===409){
              // A full read retains unrelated concurrent documents and finds actual conflicts.
              const refreshed=await pull();if(!refreshed)return false;
              continue;
            }
            if(error.status>=400&&error.status<500&&![401,403,404,405].includes(error.status)){
              const rejected=pending.get(key);if(rejected&&rejected.seq===sent.seq)rejected.errorStatus=error.status;remember();
            }
            fail(error);return false;
          }
        }
        return !pending.size;
      })();
      const work=writing;
      try{return await work;}
      finally{if(writing===work)writing=null;flight=null;if(running&&!diverged&&!['offline','error','unsupported','unauthorized'].includes(state)){report();scheduleWrite();}}
    }
    async function retry(){
      if(!running||diverged)return false;
      supported=null;for(const entry of pending.values())delete entry.errorStatus;remember();
      if(!(await pull({full:true})))return false;return flush();
    }
    async function reconcile(){
      if(!running)return false;
      try{
        if(writing)await writing;
        const response=validateResponse(await api(ENDPOINT,{headers}));if(!running)return false;
        if(response.unchanged)throw new Error('請重新讀取完整版面設定。');
        const keys=new Set([...remote.keys(),...pending.keys(),...Object.keys(response.documents)]);
        adoptions.clear();remote.clear();for(const [key,doc]of Object.entries(response.documents))remote.set(key,copy(doc));
        for(const [key,entry]of pending){const known=remote.get(key);
          if(equal(entry.data,known?.data)){pending.delete(key);continue;}
          entry.base=copy(known?.data);entry.baseVersion=known?.version||0;entry.conflicts=[[]];
        }
        cursor=response.version;initialized=true;supported=true;diverged=false;remember();
        for(const key of keys)emit(key,'resolved');report();schedulePoll();return true;
      }catch(error){if(running)fail(error);return false;}
    }
    async function resolve(key,choice){
      if(!running||!pending.has(key))return false;
      if(!['local','remote'].includes(choice))throw new Error('請選擇使用這裏或其他位置的版本。');
      if(!(await pull()))return false;
      const entry=pending.get(key),known=remote.get(key);
      if(!entry)return true;
      if(choice==='remote')pending.delete(key);
      else{entry.base=copy(known?.data);entry.baseVersion=known?.version||0;entry.conflicts=[];delete entry.errorStatus;entry.seq=++sequence;}
      remember();emit(key,'resolved');report();
      return choice==='local'?flush():true;
    }
    const controller={
      start:async()=>{report();const result=await pull();schedulePoll();return result;},
      get:key=>copy(effective(key)),getDocument:key=>copy(remote.get(key)),getDocuments:()=>copy(Object.fromEntries(remote)),update,adopt,pull,flush,resolve,reconcile,retry,status,
      subscribe(key,listener){
        if(!validKey(key)||typeof listener!=='function')throw new Error('版面訂閱格式不正確。');
        if(!listeners.has(key))listeners.set(key,new Set());listeners.get(key).add(listener);
        if(effective(key)!==undefined)listener(copy(effective(key)),{source:'remote',version:remote.get(key)?.version||0,conflict:!!pending.get(key)?.conflicts.length});
        return ()=>listeners.get(key)?.delete(listener);
      },
      stop(){if(!running)return;running=false;cancelTimers();listeners.clear();report('stopped');},
      storageKey
    };
    return controller;
  }
  const exports={create,merge,equal,validKey,DRAFT_KEY,ENDPOINT};
  if(typeof module!=='undefined'&&module.exports)module.exports=exports;else root.BlueprintPresentation=exports;
})(globalThis);
