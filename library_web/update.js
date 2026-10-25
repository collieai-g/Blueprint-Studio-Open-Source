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
    const selected=scope&&object(value.scopes)&&object(value.scopes[scope])?value.scopes[scope]:value;
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
      scope: '只有目前瀏覽器的排版與顯示偏好；不包含伺服器資料、附件或登入。' };
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
    storage.setItem(BACKUP + ':before-restore:' + Date.now(), JSON.stringify({ ...data, preferences: before }));
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
    storage.setItem(BACKUP+':before-cloud-restore:'+Date.now(),JSON.stringify({account_scope:scope,complete:ready,documents:previous}));
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
      '瀏覽器排版另行保存；伺服器資料庫、附件與登入仍須在私人儲存完整備份，不包含在來源檢查點內。由你使用可用工具接續；需要我本人操作時只交代當前一步。',
      '先檢查現在對話實際載入的工具。平台外掛與執行環境 HTTP 分別診斷；網域設定已發佈但未套用時，不要叫我重複加入或反覆安裝外掛。',
      '只有需要我本人登入、授權或按平台按鈕時才交代一個明確動作及入口。完成後繼續，直到正式版本、資料保留及左側寫入／右側讀回都已驗證。'
    ].join('\n\n');
  }
  function init({ api, openDialog, notice }) {
    const $ = id => document.getElementById(id);
    let current = null, info = null, generation = 0,restoring=false;
    const error = message => { $('update-error').textContent = message || ''; $('update-error').hidden = !message; };
    function backupStatus(status) {
      $('native-backup').hidden = !status?.supported;
      if (!status?.supported) return;
      const last = status.last_backup;
      $('native-backup-create').disabled = !!status.busy;
      $('native-backup-status').textContent = status.busy ? '正在備份及測試還原，請稍候。' : (status.error ||
        (last?.restore_verified ? '最近完整備份已通過還原測試：' + new Date(last.created_at * 1000).toLocaleString() + '。請下載另一份私人保存。' : '尚未完成完整備份驗證。'));
      const valid = last?.restore_verified && /^\d{8}T\d{6}Z-[0-9a-f]{12}$/.test(last.id);
      $('native-backup-download').hidden = !valid;
      if (valid) $('native-backup-download').href = '/api/maintenance/backups/' + last.id;
    }
    async function pollBackup(request) {
      if (request !== generation || !$('update-dialog').open) return;
      try {
        const status = await api('/api/maintenance');
        if (request !== generation) return;
        backupStatus(status);
        if (status.busy) setTimeout(() => pollBackup(request), 1500);
      } catch (e) { if (request === generation) error('未能核對備份：' + e.message); }
    }
    function refresh() {
      current = handoff(location.origin, info, localStorage, $('update-route').value);
      $('update-prompt').value = prompt(current);
      $('update-copy').disabled = $('update-download').disabled = false;
    }
    async function open() {
      const request = ++generation;
      current = info = null;
      $('update-copy').disabled = $('update-download').disabled = true;
      $('update-prompt').value = '';
      $('update-backend').textContent = '正在讀取目前工作台…';
      $('update-backup-status').textContent = '';
      $('native-backup').hidden = true;
      error('');
      openDialog('update-dialog');
      try {
        const result = await api('/api/info');
        if (request !== generation) return;
        info = result;
        backupStatus(info.maintenance);
        $('update-backend').textContent = location.origin + ' · 目前版本 ' + (info.version || '待核對');
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem('blueprint-ai-connection-v1') || '{}'); } catch { /* Optional choice only. */ }
        const route = info.connection_mode || saved?.route;
        $('update-route').value = ['native', 'sites'].includes(route) ? route : 'unknown';
        refresh();
        const existing = localStorage.getItem(BACKUP);
        if (!existing) localStorage.setItem(BACKUP, JSON.stringify(current));
        else validate(JSON.parse(existing), location.origin);
        $('update-backup-status').textContent = '已在此瀏覽器保留一份更新前排版。下載接續資料可另存目前排版，方便換環境接續。';
      } catch (e) {
        if (request !== generation) return;
        error('未能完整準備更新資料：' + e.message);
      }
    }
    document.querySelectorAll('[data-update-open]').forEach(button => button.addEventListener('click', open));
    $('native-backup-create').addEventListener('click', async () => {
      const request = generation;
      $('native-backup-create').disabled = true;
      try {
        await api('/api/maintenance/backup', { method: 'POST', body: {} });
        if (request === generation) { backupStatus({ supported: true, busy: true }); pollBackup(request); }
      } catch (e) { if (request === generation) { $('native-backup-create').disabled = false; error(e.message); } }
    });
    $('update-route').addEventListener('change', () => { if (info) { try { refresh(); error(''); } catch (e) { error(e.message); } } });
    $('update-copy').addEventListener('click', async () => {
      if (!current) return;
      try { await navigator.clipboard.writeText($('update-prompt').value); notice('已複製更新指令，貼給你使用的 AI。'); }
      catch {
        $('update-prompt').closest('details').open = true;
        $('update-prompt').focus(); $('update-prompt').select();
        error('請按 Ctrl / ⌘ C 複製已選取的更新指令。');
      }
    });
    $('update-download').addEventListener('click', () => {
      if (!current) return;
      const url = URL.createObjectURL(new Blob([JSON.stringify(current, null, 2)], { type: 'application/json' }));
      const link = document.createElement('a'); link.href = url; link.download = 'blueprint-update-handoff.json'; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    $('update-restore').addEventListener('click', async () => {
      if(restoring)return;
      restoring=true;$('update-restore').disabled=$('update-restore-local').disabled=true;
      try {
        const file = $('update-backup-file').files[0];
        if (!file) throw new Error('請先選擇之前下載的排版接續資料。');
        if (file.size > 2 * 1024 * 1024) throw new Error('排版接續資料過大。');
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
        const saved = localStorage.getItem(BACKUP);
        if (!saved) throw new Error('這個瀏覽器尚未保存更新前排版。');
        const currentInfo=await api('/api/info');
        await restorePresentation(JSON.parse(saved), location.origin, localStorage,currentInfo.account,root.BlueprintStudio?.presentation());
        location.reload();
      } catch (e) { error('未能還原排版：' + e.message); }
      finally{restoring=false;$('update-restore').disabled=$('update-restore-local').disabled=false;}
    });
    $('update-dialog').addEventListener('close', () => { ++generation; });
  }
  const exported = { handoff, validate, restore, presentationDocuments, restorePresentation, prompt, init };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root) root.BlueprintUpdate = exported;
})(typeof window !== 'undefined' ? window : null);
