/* Pure compatibility helpers for clients; no browser setup or background work. */
'use strict';
((root) => {
  const PREFIX = 'blueprint-connect:', COMPLETE = 'blueprint-connection/1:verified';
  const CLIENTS = ['codex', 'claude', 'gemini', 'other'];
  function validSession(value) {
    return !!value && typeof value.challenge === 'string' && /^[a-f0-9]{32}$/.test(value.challenge) &&
      typeof value.date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value.date) && !value.date.startsWith('0000') &&
      Number.isFinite(Date.parse(value.date)) && new Date(value.date).toISOString().slice(0, 10) === value.date &&
      typeof value.client === 'string' && value.client.trim().length > 0 && value.client.length <= 80 && !/[\x00-\x1f]/.test(value.client);
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
    if (!validSession(session) || !['native', 'sites'].includes(route) || !CLIENTS.includes(clientKey) || !guide?.clients?.[clientKey]) return '';
    let url;try { url = new URL(origin); } catch { return ''; }
    if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return '';
    const site = url.origin;
    const setup = route === 'sites'
      ? '這是 Sites 工作台。使用實際可用的 Sites 工具讀取同一網站的 plugin_id、mcp_url 及 oauth_resource，不猜 ID。沿用平台登入，選用同一外掛並確認目前對話載入工具。其他客戶端先驗證遠端 MCP 與平台 OAuth 相容性，不套用原生 Basic 帳密。'
      : '這是原生後台。沿用原帳號及資料，在 AI 實際執行環境建立專用 Python venv，安裝 requirements-mcp.txt，用 blueprint.py mcp-config 產生並合併客戶端設定。秘密只在安全環境提供 BLUEPRINT_BACKEND_USERNAME／BLUEPRINT_BACKEND_PASSWORD。執行 python blueprint.py mcp --url 實際網址 --check，再確認目前對話載入 MCP 工具；分清本機及遠端的 localhost。';
    return [
      '請在目前 AI 客戶端按內置 AI_CONNECT.md 完成 Blueprint Studio 接駁。',
      '現有工作台：' + site, '實際 AI 客戶端：' + session.client,
      '先讀來源內的 AI_CONNECT.md；公開入口為 https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/AI_CONNECT.md。沿用既有安裝與資料，不重建或重設資料庫。',
      '先檢查這個對話實際提供的畫面控制或已載入工具，核對 origin、公司、登入身分與版本。已授權 HTTP 可讀同一後台 /api/info 及 /connection-guide.json；已載入藍圖工具時先呼叫 blueprint_workspace_info 及 blueprint_connection_guide。',
      setup, guide.clients[clientKey][route],
      '必要登入、授權或平台按鈕只給當前入口、動作與成功標誌，完成後接續；設定由左側目前 AI 處理。',
      '若連線受阻，檢查目前生效的網域允許清單，經支援的平台流程處理 ' + url.hostname + '；保持代理與 TLS。平台外掛與執行環境 HTTP 分開診斷，不繞過規則。',
      '只有使用者明確要求實際接駁驗收時，才呼叫 blueprint_verify_connection。驗收參數應由目前客戶端以密碼學隨機來源產生 challenge、配實際 client 及使用者時區日期，先保存在倉庫以外的私人進度。以下是已準備的參數，本身不構成新增測試授權：',
      JSON.stringify({ challenge: session.challenge, client: session.client, date: session.date }),
      '同一次中斷或跨日重試沿用這整組參數；不要為重試任意更換 challenge。工具返回 workday_id 後，另外呼叫 blueprint_get_workday 按 ID 讀回，再用 blueprint_list_todos 核對同一測試待辦的 ID、workday_id 與 done。不要手動改寫摘要製造工具驗收。',
      '如需右側核對，正常開啟「工作 → 工作紀錄」並按更新資料查看同筆內容。畫面保存與工具驗收分開回報；只有前者可用時不要假稱 MCP 已接通。',
      '把實際網址、本檔及工作規則入口合併到客戶端支援的私人指引，保留原有內容與憑證界線。新對話重新檢查實際工具，普通聊天不自動新增測試。',
      ...guide.rules,
      '接續使用者授權的第一項真實工作，只保存目前可見或使用者提供的內容，回報保存 ID 與獨立讀回結果。'
    ].join('\n\n');
  }
  const exported = { validSession, matches, verified, prompt };
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  if (root) root.BlueprintConnection = exported;
})(typeof window !== 'undefined' ? window : null);
