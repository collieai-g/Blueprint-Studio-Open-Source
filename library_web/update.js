/* Same-site update handoff and browser preference backup; no pretend deployment. */
'use strict';
((root) => {
  const KEYS = ['blueprint-workspace-v2', 'blueprint-workspace-v2.2-backup', 'blueprint-workspace-v2.7.3-backup', 'blueprint-workspace-v2.8-backup', 'blueprint-backend-display-v1', 'blueprint-page-layouts-v1', 'blueprint-page-layouts-v1-backup'];
  const PIN_PREFIX='blueprint-overview-cards-v1:';
  const PAGE_KEYS=['blueprint-page-layouts-v1','blueprint-page-layouts-v1-backup'];
  const WORKSPACE_KEYS=['blueprint-workspace-v2','blueprint-workspace-v2.2-backup','blueprint-workspace-v2.7.3-backup','blueprint-workspace-v2.8-backup'];
  const CORE_CARDS=['heading','description','date','files','links','youtube','unfiled','workdays','todos','recent','projects','inbox','ai-agents','ai-skills','commands'];
  const BACKUP = 'blueprint-update-browser-backup-v1';
  const REPOSITORY = 'https://github.com/collieai-g/Blueprint-Studio-Open-Source';
  function origin(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) throw new Error('工作台網址不正確。');
    return url.origin;
  }
  function pinScope(key,site){
    if(typeof key!=='string'||key.length>1200||!key.startsWith(PIN_PREFIX))return null;
    try{
      const parts=key.slice(PIN_PREFIX.length).split('|').map(decodeURIComponent);
      if(parts.length!==3||origin(parts[0])!==origin(site)||parts[0]!==origin(site)||parts.some(value=>/[\x00-\x1f\x7f]/.test(value)))return null;
      return {company:parts[1],username:parts[2]};
    }catch{return null;}
  }
  function preferenceKey(key,site){return KEYS.includes(key)||pinScope(key,site)!==null;}
  const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function identityScope(site,identity){return typeof identity?.username==='string'&&identity.username&&identity.username.length<=80&&!/[\x00-\x1f\x7f]/.test(identity.username)?origin(site)+'|'+identity.username:'';}
  function scopedDocument(value,scope){
    if(!object(value))return value;
    const selected=object(value.scopes)&&object(value.scopes[scope])?value.scopes[scope]:{};
    return {version:1,pages:{},...(scope?{scopes:{[scope]:selected}}:{})};
  }
  function selectedWorkspace(value,scope,allowed){
    if(!object(value))return value;
    const selected=object(value.scopes)?scope&&object(value.scopes[scope])?value.scopes[scope]:{}:value;
    const result={};
    for(const field of ['version','layoutVersion','mode','pages','columns'])if(selected[field]!==undefined)result[field]=selected[field];
    if(object(selected.cards))result.cards=Object.fromEntries(Object.entries(selected.cards).filter(([id])=>allowed.has(id)));
    if(Array.isArray(selected.visible))result.visible=selected.visible.filter(id=>allowed.has(id));
    result.texts=scope&&object(value.scopes)&&object(value.scopes[scope])&&object(selected.texts)?selected.texts:{};
    return scope?{...result,scopes:{[scope]:result}}:result;
  }
  function storedKeys(storage){
    if(Number.isInteger(storage.length)&&typeof storage.key==='function')return Array.from({length:Math.min(storage.length,2000)},(_,index)=>storage.key(index));
    return storage.data&&typeof storage.data==='object'?Object.keys(storage.data):Object.keys(storage);
  }
  function preferenceValue(key,value,site){
    if(value===null)return;
    if(typeof value!=='string'||value.length>1024*1024)throw new Error('排版備份格式不正確。');
    const parsed=JSON.parse(value);
    if(pinScope(key,site)){
      if(!parsed||parsed.version!==1||!Array.isArray(parsed.refs)||parsed.refs.length>24||Object.keys(parsed).some(name=>!['version','refs'].includes(name)))throw new Error('總覽卡片備份格式不正確。');
      const routes=['projects','inbox','agents','skills','command'];
      for(const ref of parsed.refs){
        if(!ref||typeof ref!=='object'||Array.isArray(ref))throw new Error('總覽卡片備份格式不正確。');
        const fields=Object.keys(ref);
        if(ref.kind==='route'){
          if(fields.some(name=>!['kind','route'].includes(name))||!routes.includes(ref.route))throw new Error('總覽卡片備份格式不正確。');
        }else if(!['studio_record','agent','agent_job','skill','workday','item'].includes(ref.kind)||fields.some(name=>!['kind','id'].includes(name))||typeof ref.id!=='string'||!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$/.test(ref.id)||(ref.kind==='skill'&&!['video','general'].includes(ref.id)))throw new Error('總覽卡片備份格式不正確。');
      }
    }
  }
  function handoff(site, info, storage, route) {
    const preferences = {};
    const scope=identityScope(site,info.account);
    const names=[...KEYS,...storedKeys(storage).filter(key=>{
      const pin=pinScope(key,site);return scope&&pin&&pin.username===info.account.username;
    })];
    const allowedCards=new Set(CORE_CARDS);
    for(const key of names.filter(name=>pinScope(name,site))){
      const value=storage.getItem(key);preferenceValue(key,value,site);
      if(value!==null)for(const ref of JSON.parse(value).refs)if(ref.kind!=='route')allowedCards.add('pin-'+ref.kind+'-'+ref.id);
    }
    for (const key of new Set(names)) {
      let value = storage.getItem(key);
      preferenceValue(key,value,site);
      if(value!==null&&PAGE_KEYS.includes(key))value=scope?JSON.stringify(scopedDocument(JSON.parse(value),scope)):null;
      if(value!==null&&WORKSPACE_KEYS.includes(key)){
        const parsed=JSON.parse(value);
        const privateFields=object(parsed)&&(object(parsed.scopes)||object(parsed.texts)||Object.keys(parsed.cards||{}).some(id=>!allowedCards.has(id))||(Array.isArray(parsed.visible)&&parsed.visible.some(id=>!allowedCards.has(id))));
        if(privateFields)value=JSON.stringify(selectedWorkspace(parsed,scope,allowedCards));
      }
      if(value!==null&&key==='blueprint-backend-display-v1'){
        const display=JSON.parse(value),selected={};
        if(['grid','list'].includes(display?.layout))selected.layout=display.layout;
        if(['updated','created','title'].includes(display?.sort))selected.sort=display.sort;
        value=JSON.stringify(selected);
      }
      preferences[key] = value;
    }
    const deployment = info.deployment_mode === 'sites_native' ? {
      deployment_mode: 'sites_native', backend_origin: origin(info.backend_origin),
      gateway_version: String(info.gateway_version || '待核對').slice(0, 80)
    } : {};
    const result = { format: 'blueprint-update-handoff', format_version: 1, origin: origin(site),
      version: String(info.version || '待核對').slice(0, 80),
      route: ['native', 'sites'].includes(route) ? route : 'unknown',
      ...deployment,
      ...(scope?{account_scope:scope}:{}),
      created_at: new Date().toISOString(), preferences,
      scope: '瀏覽器排版接續檔：只有目前登入帳戶的排版與顯示偏好；不包含公司資料、附件或登入。' };
    return validate(result, site);
  }
  function validate(data, site) {
    if (!data || data.format !== 'blueprint-update-handoff' || data.format_version !== 1 || data.origin !== origin(site) ||
        !data.preferences || typeof data.preferences !== 'object' || Array.isArray(data.preferences) ||
        Object.keys(data.preferences).some(key => !preferenceKey(key,site))) throw new Error('請使用同一個工作台的排版備份。');
    if(data.account_scope!==undefined&&(typeof data.account_scope!=='string'||!data.account_scope.startsWith(origin(site)+'|')||!data.account_scope.slice(origin(site).length+1)||data.account_scope.length>origin(site).length+81||/[\x00-\x1f\x7f]/.test(data.account_scope)))throw new Error('排版備份的帳戶不正確。');
    for (const [key,value] of Object.entries(data.preferences))preferenceValue(key,value,site);
    for(const [key,value] of Object.entries(data.preferences))if(value!==null){
      const scoped=pinScope(key,site),parsed=JSON.parse(value);
      if(scoped&&(!data.account_scope||data.account_scope!==origin(site)+'|'+scoped.username))throw new Error('總覽卡片備份的帳戶不正確。');
      if((PAGE_KEYS.includes(key)||WORKSPACE_KEYS.includes(key))&&object(parsed)&&object(parsed.scopes)&&Object.keys(parsed.scopes).some(scope=>scope!==data.account_scope))throw new Error('排版備份包含其他帳戶的內容。');
      if(PAGE_KEYS.includes(key)&&(!data.account_scope||Object.keys(parsed.pages||{}).length))throw new Error('頁面排版備份缺少正確帳戶。');
    }
    if (data.deployment_mode !== undefined && (data.deployment_mode !== 'sites_native' || data.route !== 'sites' ||
        origin(data.backend_origin) !== data.backend_origin || !data.backend_origin.startsWith('https://') ||
        data.backend_origin === data.origin)) throw new Error('請核對 Sites 入口與獨立原生後台，保留 Sites 接駁方式。');
    return data;
  }
  function restore(data, site, storage, identity) {
    validate(data, site);
    const scope=identityScope(site,identity);
    if(data.account_scope&&scope!==data.account_scope)throw new Error('請使用建立此排版備份的同一帳戶。');
    const before = {};
    for (const key of Object.keys(data.preferences)) before[key] = storage.getItem(key);
    // Keep the current arrangement before replacing it, including on repeated restores.
    const privateBefore=scope?handoff(site,{account:identity,version:data.version},storage,data.route):{...data,preferences:before};
    storage.setItem(backupKey(data)+':before-restore:'+Date.now(),JSON.stringify(privateBefore));
    const set = (key, value) => value === null ? storage.removeItem(key) : storage.setItem(key, value);
    try {
      for (const [key, value] of Object.entries(data.preferences)){
        if(value!==null&&scope&&(PAGE_KEYS.includes(key)||WORKSPACE_KEYS.includes(key))){
          let current={};try{current=JSON.parse(storage.getItem(key)||'{}');}catch{}
          const incoming=JSON.parse(value),selected=incoming.scopes?.[scope]||(WORKSPACE_KEYS.includes(key)?incoming:{});
          const merged={...(object(current)?current:{}),version:incoming.version||1,
            scopes:{...(object(current?.scopes)?current.scopes:{}),[scope]:selected}};
          set(key,JSON.stringify(merged));
        }else if(value===null&&scope&&(PAGE_KEYS.includes(key)||WORKSPACE_KEYS.includes(key))){
          let current={};try{current=JSON.parse(storage.getItem(key)||'{}');}catch{}
          const cleared=PAGE_KEYS.includes(key)?{pages:{}}:{version:1,layoutVersion:4,cards:{},visible:['heading'],texts:{}};
          set(key,JSON.stringify({...(object(current)?current:{}),version:1,
            scopes:{...(object(current?.scopes)?current.scopes:{}),[scope]:cleared}}));
        }else set(key, value);
      }
    } catch (error) {
      for (const [key, value] of Object.entries(before)) set(key, value);
      throw error;
    }
  }
  const copy=value=>JSON.parse(JSON.stringify(value));
  const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
  const equal=(a,b)=>a===b||(Array.isArray(a)&&Array.isArray(b)?a.length===b.length&&a.every((value,index)=>equal(value,b[index])):
    object(a)&&object(b)&&Object.keys(a).length===Object.keys(b).length&&Object.keys(a).every(key=>own(b,key)&&equal(a[key],b[key])));
  const safeId=(value,limit)=>typeof value==='string'&&value.length<=limit&&/^[A-Za-z0-9][A-Za-z0-9:_.\/-]*$/.test(value)&&!value.split(/[:.\/-]/).some(part=>['__proto__','constructor','prototype'].includes(part));
  const widgetId=value=>typeof value==='string'&&(CORE_CARDS.includes(value)||/^pin-(?:studio_record|agent|agent_job|skill|workday|item)-[A-Za-z0-9_.-]{1,160}$/.test(value));
  function presentationValue(key,value){
    if(!object(value))throw new Error('排版備份缺少有效的頁面設定。');
    const overview=key==='overview',result={},fields=overview?['version','layoutVersion','mode','pages','columns','visible','texts']:['version','active','pages','columns'];
    for(const field of fields)if(own(value,field))result[field]=copy(value[field]);
    const integer=(number,min,max)=>Number.isInteger(number)&&number>=min&&number<=max;
    if(own(result,'version')&&result.version!==1||own(result,'pages')&&!integer(result.pages,1,3)||own(result,'columns')&&!integer(result.columns,6,96)||own(result,'active')&&typeof result.active!=='boolean')throw new Error('排版備份的頁數或尺寸不正確。');
    if(overview){
      // Older browser-only layouts use the current UI's migration behavior.
      if(result.layoutVersion!==4){result.layoutVersion=4;result.visible=['heading'];}
      if(own(result,'mode')&&!['ai','manual'].includes(result.mode))throw new Error('排版備份的操作模式不正確。');
      if(own(result,'visible')&&(!Array.isArray(result.visible)||result.visible.length>40||new Set(result.visible).size!==result.visible.length||result.visible.some(id=>!widgetId(id))))throw new Error('排版備份包含不支援的總覽卡片。');
      if(own(result,'texts')){
        if(!object(result.texts)||Object.keys(result.texts).some(id=>!['heading','description','date'].includes(id)))throw new Error('排版備份的顯示文字不正確。');
        for(const text of Object.values(result.texts))if(typeof text!=='string'||text.length>2000||text.includes('\x00'))throw new Error('排版備份的顯示文字不正確。');
      }
    }
    if(own(value,'cards')){
      if(!object(value.cards)||Object.keys(value.cards).length>(overview?40:200))throw new Error('排版備份包含過多卡片。');
      result.cards={};
      for(const [id,card]of Object.entries(value.cards)){
        if(!(overview?widgetId(id):safeId(id,240))||!object(card))throw new Error('排版備份的卡片識別碼不正確。');
        const selected={};
        for(const [field,min,max]of [['x',0,95],['y',0,143],['w',1,96],['h',1,48]])if(own(card,field)){
          if(!integer(card[field],min,max))throw new Error('排版備份的卡片尺寸不正確。');selected[field]=card[field];
        }
        for(const field of overview?['collapsed']:['collapsed','visible'])if(own(card,field)){
          if(typeof card[field]!=='boolean')throw new Error('排版備份的卡片設定不正確。');selected[field]=card[field];
        }
        if(!overview&&own(card,'text')){
          if(typeof card.text!=='string'||card.text.length>4000||card.text.includes('\x00'))throw new Error('排版備份的顯示文字不正確。');selected.text=card.text;
        }
        result.cards[id]=selected;
      }
    }
    return result;
  }
  function presentationDocuments(data,site,identity,existing={}){
    validate(data,site);
    const scope=identityScope(site,identity);
    if(!scope||data.account_scope&&scope!==data.account_scope)throw new Error('請使用建立此排版備份的同一帳戶。');
    const documents={},preferences=data.preferences;
    if(own(preferences,'blueprint-workspace-v2')){
      const value=preferences['blueprint-workspace-v2'];
      if(value===null)documents.overview={};
      else{const parsed=JSON.parse(value),selected=object(parsed?.scopes)?parsed.scopes[scope]||{}:parsed;
        documents.overview=presentationValue('overview',selected);}
    }
    if(own(preferences,'blueprint-page-layouts-v1')){
      const value=preferences['blueprint-page-layouts-v1'],parsed=value===null?{}:JSON.parse(value);
      const pages=parsed?.scopes?.[scope]?.pages||{};
      if(!object(pages)||Object.keys(pages).length>256)throw new Error('排版備份的頁面清單不正確。');
      for(const key of Object.keys(existing))if(key.startsWith('page:')&&safeId(key.slice(5),300))documents[key]={};
      for(const [route,page]of Object.entries(pages)){
        if(!safeId(route,300))throw new Error('排版備份的頁面識別碼不正確。');
        documents['page:'+route]=presentationValue('page:'+route,page);
      }
    }
    const pins=Object.entries(preferences).filter(([key])=>pinScope(key,site)?.username===identity.username);
    if(pins.length){
      const selected=pins.map(([,value])=>value===null?{version:1,refs:[]}:JSON.parse(value));
      if(selected.some(value=>!equal(value,selected[0])))throw new Error('排版備份包含不同公司的總覽卡片，請使用目前工作室的單一備份。');
      documents.pins=copy(selected[0]);
    }else if(data.account_scope===scope&&KEYS.every(key=>own(preferences,key)))documents.pins={version:1,refs:[]};
    return documents;
  }
  async function restorePresentation(data,site,storage,identity,sync){
    // Validate everything before changing local preferences or queuing server writes.
    const initial=presentationDocuments(data,site,identity);
    if(!Object.keys(initial).length||!sync||sync.status().state==='unsupported'){
      restore(data,site,storage,identity);return {synced:false,localOnly:true};
    }
    const ready=await sync.pull({full:true}),status=sync.status();
    if(status.state==='unsupported'){
      restore(data,site,storage,identity);return {synced:false,localOnly:true};
    }
    if(['unauthorized','diverged','stopped'].includes(status.state))throw new Error(status.message||'請先恢復帳戶版面連接，再還原排版。');
    const previous=sync.getDocuments(),documents=presentationDocuments(data,site,identity,previous),keys=Object.keys(documents);
    const scope=identityScope(site,identity),pendingKey=BACKUP+':pending-restore:'+encodeURIComponent(scope);
    storage.setItem(backupKey(data)+':before-cloud-restore:'+Date.now(),JSON.stringify({account_scope:scope,complete:ready,documents:previous}));
    restore(data,site,storage,identity);
    storage.setItem(pendingKey,JSON.stringify(data));
    // This is an explicit replacement. Base each change on the current effective
    // document; concurrent server changes still meet the normal CAS/conflict path.
    for(const key of keys)sync.update(key,documents[key],{base:sync.get(key)});
    if(!ready)throw new Error('已保留這份還原排版，但尚未讀取完整帳戶設定。請連線後再次按還原，完成同步。');
    await sync.flush();
    if(!(await sync.pull({full:true})))throw new Error('已保留這份還原排版，但尚未確認帳戶同步。請連線後再次按還原。');
    const latest=sync.status();
    if(keys.some(key=>latest.pending.includes(key)||latest.conflicts.some(item=>item.key===key)||!equal(sync.getDocument(key)?.data,documents[key])))throw new Error('排版還原尚未全部同步；修改與備份已保留，請先處理操作列的同步提示，再次按還原。');
    storage.removeItem(pendingKey);return {synced:true,localOnly:false};
  }
  // A fingerprint describes active preferences, not archive bytes or export time.
  const canonical=value=>Array.isArray(value)?value.map(canonical):object(value)?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
  const cryptography=()=>root?.crypto||(typeof require==='function'?require('node:crypto').webcrypto:null);
  async function digest(value){
    const crypto=cryptography();if(!crypto?.subtle)throw new Error('目前瀏覽器未提供安全雜湊，請使用 HTTPS 工作台。');
    return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',value)),byte=>byte.toString(16).padStart(2,'0')).join('');
  }
  function backupKey(data){return BACKUP+':'+encodeURIComponent(data.account_scope||data.origin+'|anonymous');}
  async function fingerprint(data){
    validate(data,data.origin);
    if(!data.account_scope)throw new Error('請先登入，再核對瀏覽器排版。');
    const preferences={};
    for(const [key,value]of Object.entries(data.preferences))if(['blueprint-workspace-v2','blueprint-page-layouts-v1','blueprint-backend-display-v1'].includes(key)||pinScope(key,data.origin)){
      const parsed=value===null?null:JSON.parse(value);
      preferences[key]=object(parsed)&&object(parsed.scopes)?parsed.scopes[data.account_scope]||{}:parsed;
    }
    return digest(new TextEncoder().encode(JSON.stringify(canonical({origin:data.origin,account_scope:data.account_scope,format_version:data.format_version,preferences}))));
  }
  async function layoutSummary(data){return {origin:data.origin,account_scope:data.account_scope,format_version:data.format_version,preference_sha256:await fingerprint(data)};}
  function prompt(data) {
    validate(data, data.origin);
    return [
      '請使用藍圖工作台更新助手，將我的原工作台更新至最新正式版本，保留原網址、資料、登入及自訂內容。',
      '現有工作台：' + data.origin,
      '目前後台回報版本：' + data.version + '；安裝方式：' + data.route,
      ...(data.deployment_mode === 'sites_native' ? [
        '這是 Sites 入口配原生後台；資料後台：' + data.backend_origin + '；接駁版本：' + data.gateway_version,
        '先讀 docs/SITES_NATIVE_GATEWAY.md。備份及更新資料後台，保留原生持久磁碟；只有接駁程式或工具合約改變才更新 Sites。不要把這個入口當成 D1／R2 公司資料庫，也不要改回舊 Sites。能力檢查與正式備份、部署驗收分開核對。'
      ] : []),
      '先讀 ' + REPOSITORY + '/blob/main/UPDATE_AI.txt 及 docs/UPDATE_ASSISTANT.md；選定確切正式版本。',
      '沿用已有私人接續包與安裝紀錄；不要重新開始或另建網站。由你執行工具、保存來源與備份、核對環境並完成原站升級。',
      '開源倉庫只保存通用程式，不能放入我的私人來源、資料、設定、聊天或接續包。跨環境前按 docs/SOURCE_CHECKPOINT.md，把已檢查的候選來源保存至獨立私人倉庫，下載核對後才標示可接續；目前環境內的 ZIP 不等於已交接。',
      '先讀共用安裝更新工作紀錄，沿用 operation_id、plan_digest 及已完成證據；結果不明先讀回。相同 coverage 且完整性、還原、私人副本及保留政策仍有效時，沿用原備份 ID；中段比較使用唯讀指紋，不重造完整包。',
      '瀏覽器排版接續檔只在排版遷移、首次保護自訂內容或跨環境時匯出。同登入範圍與偏好雜湊的已核對私人檔案可以沿用；純後台或日常資料保存不下載排版。公司資料、附件及登入另按既定政策保護。',
      '先檢查現在對話實際載入的工具。平台外掛與執行環境 HTTP 分別診斷；網域設定已發佈但未套用時，不要叫我重複加入或反覆安裝外掛。',
      '只有需要我本人登入、授權或按平台按鈕時才交代一個明確動作及入口。完成後繼續，直到正式版本、資料保留及左側寫入／右側讀回都已驗證。'
    ].join('\n\n');
  }
  function init({ api, openDialog, notice }) {
    const $ = id => document.getElementById(id);
    let current=null,info=null,maintenance=null,summary=null,generation=0,refreshGeneration=0,restoring=false;
    const error=message=>{$('update-error').textContent=message||'';$('update-error').hidden=!message;};
    const maintenanceApi=(path,options={})=>api(path,{...options,headers:{...(options.headers||{}),'X-Blueprint-Account':encodeURIComponent(info?.account?.username||'')}});
    const savedJSON=key=>{try{return JSON.parse(localStorage.getItem(key)||'null');}catch{return null;}};
    function backupStatus(status){
      maintenance=status;$('native-backup').hidden=!status?.supported;if(!status?.supported)return;
      const last=status.last_backup,protection=status.protection||{},valid=last?.restore_verified&&/^\d{8}T\d{6}Z-[0-9a-f]{12}$/.test(last.id);
      const protectedData=protection.current_coverage_matches&&protection.platform_verified&&protection.restore_verified&&protection.retention_valid;
      $('native-backup-create').disabled=!!status.busy||status.verification_current===false;
      $('native-backup-create').hidden=!!protectedData;
      $('native-backup-download').hidden=!valid;
      $('native-backup-verify-details').hidden=!valid||!Number.isInteger(last.size)||!/^[0-9a-f]{64}$/.test(last.sha256);
      if(valid)$('native-backup-download').href='/api/maintenance/backups/'+last.id;
      const next=protection.reusable?'可重用。完整性、還原及目前帳戶的私人副本核對均有紀錄；變更前再核對資料指紋。':protectedData?'私人副本待核對。平台完整備份已通過還原測試；下載同一份檔案並讀回核對即可。':valid?'平台已驗證最近備份。'+(status.coverage?'目前資料或保留條件已改變，請保護目前資料。':'請由 AI 核對此版本的保護條件。'):'待建立。請先建立並驗證完整資料備份。';
      $('native-backup-status').textContent=status.busy?'正在備份及測試還原，請稍候。':status.verification_current===false?'正在核對目前資料與已有保護…':status.error||next;
      $('native-backup-verify').disabled=!!status.busy;
    }
    async function readBackup(request){
      const status=await maintenanceApi('/api/maintenance');if(request!==generation)return null;backupStatus(status);return status;
    }
    async function pollBackup(request){
      if(request!==generation||!$('update-dialog').open)return;
      try{const status=await readBackup(request);if(status?.busy)setTimeout(()=>pollBackup(request),1500);}
      catch(e){if(request===generation)error('未能核對備份：'+e.message+'。請先重新核對同一個工作，毋須重建。');}
    }
    function baseline(){
      if(!current)return null;
      const scoped=savedJSON(backupKey(current));if(scoped)return scoped;
      const legacy=savedJSON(BACKUP);
      // Retain old backups, but never offer another account's global baseline.
      try{return legacy?.account_scope===current.account_scope?validate(legacy,location.origin):null;}catch{return null;}
    }
    function layoutStatus(){
      const receipt=summary&&savedJSON(backupKey(current)+':layout-receipt');
      const verified=receipt?.account_scope===summary?.account_scope&&receipt?.preference_sha256===summary?.preference_sha256;
      $('update-backup-status').textContent=verified?'已有相同排版雜湊的私人檔案核對紀錄。接續時確認該檔案仍可讀，無需再次匯出。':'只在排版遷移、首次保護自訂內容或換環境時，才需要瀏覽器排版接續檔。';
      $('update-layout-fingerprint').textContent=summary?'目前排版 SHA256：'+summary.preference_sha256:'';
      $('update-restore-local').disabled=!baseline();
    }
    async function refresh(){
      const refresh=++refreshGeneration,request=generation;
      current=handoff(location.origin,info,localStorage,$('update-route').value);const value=current;
      const next=await layoutSummary(value);if(request!==generation||refresh!==refreshGeneration)return;
      summary=next;$('update-prompt').value=prompt(value)+'\n\n瀏覽器排版摘要（不含正文）：'+JSON.stringify(summary);
      $('update-copy').disabled=$('update-download').disabled=false;layoutStatus();
    }
    async function open(){
      const request=++generation;current=info=summary=null;
      $('update-copy').disabled=$('update-download').disabled=true;$('update-prompt').value='';
      $('update-backend').textContent='正在讀取目前工作台…';$('update-backup-status').textContent='';$('native-backup').hidden=true;error('');openDialog('update-dialog');
      try{
        const result=await api('/api/info');if(request!==generation)return;info=result;backupStatus(info.maintenance);
        $('update-backend').textContent=location.origin+' · 目前版本 '+(info.version||'待核對');
        const saved=savedJSON('blueprint-ai-connection-v1')||{},route=info.connection_mode||saved.route;
        $('update-route').value=['native','sites'].includes(route)?route:'unknown';await refresh();
        if(request===generation&&info.maintenance?.supported)await readBackup(request);
      }catch(e){if(request===generation)error('未能完整準備更新資料：'+e.message);}
    }
    document.querySelectorAll('[data-update-open]').forEach(button=>button.addEventListener('click',open));
    $('native-backup-refresh').addEventListener('click',async()=>{try{error('');await readBackup(generation);}catch(e){error(e.message);}});
    $('native-backup-create').addEventListener('click',async()=>{
      if(!current)return;const request=generation;$('native-backup-create').disabled=true;
      try{
        // Read unknown results before retrying the same persisted operation.
        const status=await readBackup(request);if(!status||request!==generation)return;
        let body={};
        if(status.maintenance_contract==='blueprint-maintenance/2'){
          const plan_digest=await digest(new TextEncoder().encode(JSON.stringify({origin:current.origin,coverage:status.coverage,purpose:'manual-backup-v2'})));
          const key=backupKey(current)+':pending-backup',prior=savedJSON(key);
          body=prior?.plan_digest===plan_digest?prior:{contract_version:2,operation_id:'browser-'+Array.from(cryptography().getRandomValues(new Uint8Array(16)),byte=>byte.toString(16).padStart(2,'0')).join(''),plan_digest};
          if(request!==generation)return;
          localStorage.setItem(key,JSON.stringify(body));
        }
        await maintenanceApi('/api/maintenance/backup',{method:'POST',body});if(request===generation)await pollBackup(request);
      }catch(e){if(request===generation){$('native-backup-create').disabled=false;error(e.message+'。請先重新核對；重試會沿用原工作編號。');}}
    });
    $('native-backup-verify').addEventListener('click',async()=>{
      const request=generation,last=maintenance?.last_backup;
      try{
        const file=$('native-backup-file').files[0],reference=$('native-backup-reference').value.trim();
        if(!file||!last)throw new Error('請選擇已另存的完整備份。');if(!reference)throw new Error('請填寫私人檔案的保存位置或引用。');
        if(file.size!==last.size||await digest(await file.arrayBuffer())!==last.sha256)throw new Error('檔案大小或 SHA256 不符；請核對同一個備份編號。');
        if(request!==generation)return;
        await maintenanceApi('/api/maintenance/backups/'+last.id+'/private-copy',{method:'POST',body:{file_reference:reference,size:file.size,sha256:last.sha256,readback_verified:true}});
        if(request===generation){await readBackup(request);if(request!==generation)return;$('native-backup-verify-details').open=false;error('');notice('已讀回私人檔案並保存核對收據。');}
      }catch(e){if(request===generation)error('未能核對私人副本：'+e.message);}
    });
    $('update-route').addEventListener('change',()=>{if(info)refresh().catch(e=>error(e.message));});
    $('update-copy').addEventListener('click',async()=>{
      if(!current)return;
      try{await navigator.clipboard.writeText($('update-prompt').value);notice('已複製更新指令，貼給你使用的 AI。');}
      catch{$('update-prompt').closest('details').open=true;$('update-prompt').focus();$('update-prompt').select();error('請按 Ctrl / ⌘ C 複製已選取的更新指令。');}
    });
    $('update-download').addEventListener('click',async()=>{
      if(!current)return;const request=generation;
      try{
        await refresh();if(request!==generation)return;const key=backupKey(current);if(!localStorage.getItem(key))localStorage.setItem(key,JSON.stringify(current));
        const url=URL.createObjectURL(new Blob([JSON.stringify(current,null,2)],{type:'application/json'}));
        const link=document.createElement('a');link.href=url;link.download='blueprint-browser-layout-handoff.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        notice('已開始下載瀏覽器排版接續檔。選擇已保存的檔案讀回後，才會記錄私人副本核對。');layoutStatus();
      }catch(e){error(e.message);}
    });
    $('update-layout-verify').addEventListener('click',async()=>{
      const request=generation;
      try{
        const file=$('update-layout-file').files[0];if(!file||file.size>2*1024*1024)throw new Error('請選擇有效的瀏覽器排版接續檔。');
        const data=validate(JSON.parse(await file.text()),location.origin);if(request!==generation)return;await refresh();if(request!==generation)return;
        const check=await layoutSummary(data);if(check.account_scope!==current.account_scope||check.preference_sha256!==summary.preference_sha256)throw new Error('檔案與目前登入帳戶或排版不同。');
        const receipt={...check,file_reference:file.name,size:file.size,sha256:await digest(await file.arrayBuffer()),readback_verified:true};
        if(request!==generation)return;
        localStorage.setItem(backupKey(current)+':layout-receipt',JSON.stringify(receipt));$('update-layout-file').closest('details').open=false;layoutStatus();error('');
      }catch(e){if(request===generation)error('未能核對排版接續檔：'+e.message);}
    });
    $('update-restore').addEventListener('click', async () => {
      if(restoring)return;
      restoring=true;$('update-restore').disabled=$('update-restore-local').disabled=true;
      try {
        const file = $('update-backup-file').files[0];
        if (!file) throw new Error('請先選擇之前下載的瀏覽器排版接續檔。');
        if (file.size > 2 * 1024 * 1024) throw new Error('瀏覽器排版接續檔過大。');
        const currentInfo=await api('/api/info');
        await restorePresentation(JSON.parse(await file.text()), location.origin, localStorage,currentInfo.account,root.BlueprintStudio?.presentation());
        location.reload();
      } catch (e) { error('未能還原排版：' + e.message); }
      finally{restoring=false;$('update-restore').disabled=$('update-restore-local').disabled=false;}
    });
    $('update-restore-local').addEventListener('click', async () => {
      if(restoring)return;
      restoring=true;$('update-restore').disabled=$('update-restore-local').disabled=true;
      try {
        const saved = baseline();
        if (!saved) throw new Error('這個瀏覽器尚未保存更新前排版。');
        const currentInfo=await api('/api/info');
        await restorePresentation(saved, location.origin, localStorage,currentInfo.account,root.BlueprintStudio?.presentation());
        location.reload();
      } catch (e) { error('未能還原排版：' + e.message); }
      finally{restoring=false;$('update-restore').disabled=$('update-restore-local').disabled=false;}
    });
    $('update-dialog').addEventListener('close', () => { ++generation; });
  }
  const exported = { handoff, validate, restore, presentationDocuments, restorePresentation, fingerprint, layoutSummary, backupKey, prompt, init };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root) root.BlueprintUpdate = exported;
})(typeof window !== 'undefined' ? window : null);
