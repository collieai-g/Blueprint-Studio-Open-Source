/* Same-site update handoff and browser preference backup; no pretend deployment. */
'use strict';
((root) => {
  const KEYS = ['blueprint-workspace-v2', 'blueprint-workspace-v2.2-backup', 'blueprint-workspace-v2.7.3-backup', 'blueprint-backend-display-v1'];
  const BACKUP = 'blueprint-update-browser-backup-v1';
  const REPOSITORY = 'https://github.com/collieai-g/Blueprint-Studio-Open-Source';
  function origin(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) throw new Error('工作台網址不正確。');
    return url.origin;
  }
  function handoff(site, info, storage, route) {
    const preferences = {};
    for (const key of KEYS) {
      const value = storage.getItem(key);
      if (value !== null) JSON.parse(value);
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
      created_at: new Date().toISOString(), preferences,
      scope: '只有目前瀏覽器的排版與顯示偏好；不包含伺服器資料、附件或登入。' };
    return validate(result, site);
  }
  function validate(data, site) {
    if (!data || data.format !== 'blueprint-update-handoff' || data.format_version !== 1 || data.origin !== origin(site) ||
        !data.preferences || typeof data.preferences !== 'object' || Array.isArray(data.preferences) ||
        Object.keys(data.preferences).some(key => !KEYS.includes(key))) throw new Error('請使用同一個工作台的排版備份。');
    for (const value of Object.values(data.preferences)) {
      if (value !== null) {
        if (typeof value !== 'string' || value.length > 1024 * 1024) throw new Error('排版備份格式不正確。');
        JSON.parse(value);
      }
    }
    if (data.deployment_mode !== undefined && (data.deployment_mode !== 'sites_native' || data.route !== 'sites' ||
        origin(data.backend_origin) !== data.backend_origin || !data.backend_origin.startsWith('https://') ||
        data.backend_origin === data.origin)) throw new Error('請核對 Sites 入口與獨立原生後台，保留 Sites 接駁方式。');
    return data;
  }
  function restore(data, site, storage) {
    validate(data, site);
    const before = {};
    for (const key of Object.keys(data.preferences)) before[key] = storage.getItem(key);
    // Keep the current arrangement before replacing it, including on repeated restores.
    storage.setItem(BACKUP + ':before-restore:' + Date.now(), JSON.stringify({ ...data, preferences: before }));
    const set = (key, value) => value === null ? storage.removeItem(key) : storage.setItem(key, value);
    try {
      for (const [key, value] of Object.entries(data.preferences)) set(key, value);
    } catch (error) {
      for (const [key, value] of Object.entries(before)) set(key, value);
      throw error;
    }
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
    let current = null, info = null, generation = 0;
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
      try {
        const file = $('update-backup-file').files[0];
        if (!file) throw new Error('請先選擇之前下載的排版接續資料。');
        if (file.size > 2 * 1024 * 1024) throw new Error('排版接續資料過大。');
        restore(JSON.parse(await file.text()), location.origin, localStorage);
        location.reload();
      } catch (e) { error('未能還原排版：' + e.message); }
    });
    $('update-restore-local').addEventListener('click', () => {
      try {
        const saved = localStorage.getItem(BACKUP);
        if (!saved) throw new Error('這個瀏覽器尚未保存更新前排版。');
        restore(JSON.parse(saved), location.origin, localStorage);
        location.reload();
      } catch (e) { error('未能還原排版：' + e.message); }
    });
    $('update-dialog').addEventListener('close', () => { ++generation; });
  }
  const exported = { handoff, validate, restore, prompt, init };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root) root.BlueprintUpdate = exported;
})(typeof window !== 'undefined' ? window : null);
