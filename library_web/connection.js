/* Connection guidance and evidence from the same backend; never a fake toggle. */
'use strict';
((root) => {
  const KEY = 'blueprint-ai-connection-v1', PREFIX = 'blueprint-connect:', COMPLETE = 'blueprint-connection/1:verified';
  const CLIENTS = ['codex', 'claude', 'gemini', 'other'];
  function validSession(value) {
    return !!value && /^[a-f0-9]{32}$/.test(value.challenge) &&
      /^\d{4}-\d{2}-\d{2}$/.test(value.date) && typeof value.client === 'string' &&
      value.client.trim().length > 0 && value.client.length <= 80 && !/[\x00-\x1f]/.test(value.client);
  }
  function matches(report, session) {
    return validSession(session) && !!report && report.source_key === PREFIX + session.challenge &&
      report.source === session.client && report.date === session.date;
  }
  function verified(report, session) {
    const e = report?.entries;
    return matches(report, session) && report.title === 'AI 接駁測試' && report.summary === COMPLETE &&
      Array.isArray(e) && e.length === 2 && e[0].type === 'outcome' && e[0].title === 'AI 接駁測試' &&
      e[1].type === 'todo' && e[1].title === '接駁測試：待辦寫入及更新' && e[1].done === true &&
      e.every(entry => entry.content === session.challenge);
  }
  function prompt({ origin, route, clientKey, session, guide }) {
    if (!validSession(session) || !['native', 'sites'].includes(route) || !CLIENTS.includes(clientKey)) return '';
    const url = new URL(origin);
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return '';
    const site = url.origin;
    const setup = route === 'sites'
      ? '這是 Sites 工作台。先用 Sites get_site(include_mcp_connection=true) 讀取同一網站真正的 plugin_id、mcp_url 及 oauth_resource；不可猜 ID。沿用平台登入，選用該外掛並確認目前對話載入工具。其他客戶端先驗證遠端 MCP 及平台 OAuth 相容；不能套用原生 Basic 帳密。'
      : '這是原生後台。沿用原帳號，在 AI 實際執行環境建立專用 Python venv 並安裝 requirements-mcp.txt，以 blueprint.py mcp-config 產生及合併客戶端設定。秘密只在安全環境填入 BLUEPRINT_BACKEND_USERNAME／BLUEPRINT_BACKEND_PASSWORD。先執行 mcp --url 實際網址 --check，再載入目前對話的 MCP 工具。本機與遠端的 localhost 必須分清。';
    return [
      '請帶我完成 Blueprint Studio Open Source V2.2 的 AI 接駁，直到真正在同一個工作台使用。',
      '現有工作台：' + site, 'AI 客戶端：' + session.client,
      '先讀 https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.2.0/docs/CONNECT.md 及 docs/AI_CONNECTION.md。',
      '先檢查已有安裝與資料；未到 2.2.0 時先按 docs/UPGRADE.md 備份並升級同一網站，不重建或重設資料庫。完整保留原版介面。',
      setup, guide.clients[clientKey][route],
      '需要我登入、授權或按平台按鈕時，只交代當前入口、動作及成功標誌，完成後繼續。已安裝不代表對話工具已載入。',
      '若雲端直接連線受阻，查看目前生效的網域允許清單，經平台設定加入 ' + url.hostname + '；保持代理與 TLS。平台外掛與執行環境 HTTP 分開診斷，不繞過規則。',
      '工具可用後，呼叫 blueprint_workspace_info 比較網址及公司，呼叫 blueprint_connection_guide，遵循內置工作規則。',
      '我授權新增一筆明確標示的接駁測試及測試待辦。呼叫 blueprint_verify_connection，參數完整如下：',
      JSON.stringify(session), '中斷時重試同一組參數，不任意換 challenge。此工具會保存、讀回、更新測試待辦再讀回。回報返回的 workday_id。',
      '提醒我回到工作台「AI 接駁中心」確認同一筆紀錄。只有工具讀寫及右側顯示均驗證才算完成；不能在瀏覽器或直接 API 手工製造驗收成功紀錄。',
      '將本工作台地址、客戶端設定位置及以下規則寫進此客戶端支援的私人專案指引，保留原有指引；換對話亦要先讀取。無法載入指引時交付接續文字，不假設記憶存在。',
      ...guide.rules,
      '驗收後帶我完成第一項真實工作：整理文件、保存連結或建立工作及待辦。只保存我提供的內容，回報保存位置、ID 及讀回結果。'
    ].join('\n\n');
  }
  function init({ api, openDialog, notice, setView }) {
    const $ = id => document.getElementById(id);
    let saved = {}, guide, backend, request = 0, checking = false;
    try { saved = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch { /* Optional browser progress. */ }
    if (!saved || typeof saved !== 'object' || Array.isArray(saved)) saved = {};
    if (!saved.sessions || typeof saved.sessions !== 'object' || Array.isArray(saved.sessions)) saved.sessions = {};
    $('connection-client').value = CLIENTS.includes(saved.clientKey) ? saved.clientKey : 'codex';
    $('connection-route').value = ['native', 'sites'].includes(saved.route) ? saved.route : '';
    $('connection-other').value = typeof saved.other === 'string' ? saved.other.slice(0, 80) : '';
    const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(saved)); } catch { /* Current-session use still works. */ } };
    const clientName = () => $('connection-client').value === 'other' ? $('connection-other').value.trim() : guide?.clients[$('connection-client').value].name;
    const sessionKey = () => $('connection-route').value + ':' + $('connection-client').value + ':' + clientName();
    const session = () => {
      const key = sessionKey(), existing = saved.sessions[key];
      if (validSession(existing) && existing.client === clientName()) return existing;
      if (!guide || !clientName() || !['native', 'sites'].includes($('connection-route').value)) return null;
      const bytes = new Uint8Array(16); crypto.getRandomValues(bytes);
      const now = new Date(), day = [now.getFullYear(), String(now.getMonth() + 1).padStart(2, '0'), String(now.getDate()).padStart(2, '0')].join('-');
      const value = { challenge: [...bytes].map(b => b.toString(16).padStart(2, '0')).join(''), client: clientName(), date: day };
      saved.sessions[key] = value; persist(); return value;
    };
    const error = text => { $('connection-error').hidden = !text; $('connection-error').textContent = text || ''; };
    function render(report) {
      const s = session(), complete = verified(report, s);
      const labels = [
        backend ? '工作台可讀取：已確認' : '工作台可讀取：等待檢查',
        matches(report, s) ? '接駁測試紀錄：已寫入同一後台' : '接駁測試紀錄：等待 AI 寫入',
        complete ? '工具讀回及待辦更新：已完成本次驗收' : '工具讀回及待辦更新：尚未完成',
        complete ? '右側工作台：已讀到相同驗收結果' : '右側工作台：等待驗收結果'
      ];
      $('connection-progress').replaceChildren(...labels.map((text, i) => {
        const li = document.createElement('li'); li.textContent = text;
        li.classList.toggle('complete', i === 0 ? !!backend : i === 1 ? matches(report, s) : complete); return li;
      }));
      $('connection-workdays').hidden = !report;
      $('connection-evidence').textContent = report ? '來源：' + report.source + ' · 紀錄 ID：' + report.id + ' · 紀錄更新：' + report.updated_at : '目前未收到這組測試的完成結果。複製指令不會自動變成已接通。';
      $('connection-banner-status').textContent = complete ? clientName() + ' 已完成一次讀寫驗收；開始工作或連接另一個 AI。' : '讓 AI 帶你完成接駁，將對話中的成果保存到這裏。';
    }
    function help() {
      if (!guide) return;
      const issue = guide.troubleshooting[$('connection-problem').value];
      $('connection-help-text').textContent = issue.action + ($('connection-problem').value === 'network' ? ' 本工作台網域：' + location.hostname : '');
    }
    function update() {
      ++request;
      saved.clientKey = $('connection-client').value; saved.route = $('connection-route').value; saved.other = $('connection-other').value;
      persist(); $('connection-other-field').hidden = saved.clientKey !== 'other';
      const s = session();
      $('connection-client-help').textContent = !saved.route ? '選擇現有工作台的安裝方式，AI 會沿用你的網站及資料。' : saved.route === 'sites' ? 'AI 會沿用這個網站的登入，帶你選用工作台外掛。需要登入或授權時，按它提供的目前一步完成。' : 'AI 會幫你準備所選客戶端的連接設定。你只需貼上下一步指令，並在需要時完成安全登入。';
      $('connection-prompt').value = guide && s ? prompt({ origin: location.origin, route: saved.route, clientKey: saved.clientKey, session: s, guide }) : '';
      $('connection-copy').disabled = $('connection-download').disabled = !$('connection-prompt').value;
      $('connection-retest').disabled = !s;
      $('connection-copy-status').textContent = '';
      error(''); render(null); help();
    }
    async function check() {
      if (checking || !guide) return;
      checking = true;
      const current = ++request, s = session();
      $('connection-check').disabled = true;
      try {
        const info = await api('/api/info');
        let report;
        if (s) {
          const list = await api('/api/workdays?q=' + encodeURIComponent(PREFIX + s.challenge) + '&limit=100');
          const found = list.workdays.find(r => matches(r, s));
          if (found) report = (await api('/api/workdays/' + encodeURIComponent(found.id))).workday;
        }
        if (current !== request) return;
        backend = info;
        $('connection-backend').textContent = (info.company_name || '目前工作台') + ' · ' + location.origin + ' · 後台 ' + (info.version || '版本待確認');
        error(''); render(report);
      } catch (e) {
        if (current !== request) return;
        backend = null; render(null); error('未能確認目前連接：' + e.message + ' 請按下方相應卡點繼續。');
      } finally { checking = false; $('connection-check').disabled = false; }
    }
    async function open() {
      ++request; guide = null; backend = null; render(null); error('');
      $('connection-backend').textContent = '正在核對這個工作台…';
      $('connection-copy').disabled = $('connection-download').disabled = true;
      openDialog('connection-dialog');
      try {
        guide = await api('/connection-guide.json');
        if (guide.format !== 1 || !Array.isArray(guide.rules) || !guide.clients) throw new Error('接駁指引格式不相容，請完整升級網站。');
        backend = await api('/api/info');
        if (!$('connection-route').value && backend.connection_mode === 'native') $('connection-route').value = 'native';
        update(); await check();
      } catch (e) {
        ++request; guide = null; backend = null; render(null); $('connection-prompt').value = '';
        $('connection-copy').disabled = $('connection-download').disabled = true;
        error('接駁中心未完整載入：' + e.message + ' 請按 docs/UPGRADE.md 更新同一網站全部 V2.2 資產，再重開接駁中心。');
      }
    }
    async function copy(text) {
      if (!text) return;
      try { await navigator.clipboard.writeText(text); $('connection-copy-status').textContent = '已複製，貼到你選擇的 AI；完成登入後請回來查看結果。'; }
      catch { $('connection-prompt').value = text; $('connection-prompt').closest('details').open = true; $('connection-prompt').focus(); $('connection-prompt').select(); $('connection-copy-status').textContent = '請按 Ctrl / ⌘ C 複製已選取內容。'; }
    }
    document.querySelectorAll('[data-connection-open]').forEach(button => button.addEventListener('click', open));
    ['connection-client', 'connection-route', 'connection-other'].forEach(id => $(id).addEventListener('change', update));
    $('connection-copy').addEventListener('click', () => copy($('connection-prompt').value));
    $('connection-download').addEventListener('click', () => {
      const url = URL.createObjectURL(new Blob([$('connection-prompt').value], { type: 'text/plain;charset=utf-8' }));
      const link = document.createElement('a'); link.href = url; link.download = 'blueprint-ai-connect.txt'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    $('connection-check').addEventListener('click', check);
    $('connection-retest').addEventListener('click', () => { delete saved.sessions[sessionKey()]; update(); notice('已建立新的測試指令；貼給 AI 後會新增一筆接駁測試。'); });
    $('connection-workdays').addEventListener('click', () => { $('connection-dialog').close(); setView('workdays'); });
    $('connection-problem').addEventListener('change', help);
    $('connection-help-copy').addEventListener('click', () => copy('請先處理藍圖工作台接駁卡點：' + $('connection-help-text').textContent + '\n\n' + $('connection-prompt').value));
    document.querySelectorAll('[data-connection-example]').forEach(button => button.addEventListener('click', () => copy(button.dataset.connectionExample + '\n工作台：' + location.origin + '\n先確認目前工具連接，讀取 blueprint_connection_guide。')));
    $('connection-dialog').addEventListener('close', () => { ++request; });
    setInterval(() => { if ($('connection-dialog').open && !document.hidden && guide) check(); }, 5000);
    render(null);
  }
  const exported = { validSession, matches, verified, prompt, init };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root) root.BlueprintConnection = exported;
})(typeof window !== 'undefined' ? window : null);
