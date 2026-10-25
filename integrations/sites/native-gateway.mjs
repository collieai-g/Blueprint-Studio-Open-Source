/* Original Blueprint adapter. Deploy ONLY behind Sites' verified identity gate.
 * UI, MCP and maintenance share one native backend; no D1/R2 or local data copy.
 * A raw public Worker must not trust these headers without its own identity gate.
 */
import { createMcpHandler } from '../mcp/sites.mjs';

export const GATEWAY_VERSION = '1.0.0';
const CONTRACT = 'blueprint-native/1';
const JSON_LIMIT = 512 * 1024;
const FILE_LIMIT = Math.floor(20 * 1024 * 1024 * 4 / 3) + 128 * 1024;
const ASSETS = new Set(['/', '/index.html', '/AI_CONNECT.md', '/app.js', '/workdays.js', '/workspace.js', '/presentation-sync.js', '/page-layout.js', '/page-layout.css', '/overview-cards.js',
  '/brand.svg', '/style.css', '/connection.js', '/update.js', '/connection-guide.json',
  '/studio.js', '/studio.css', '/agents.js', '/agents.css', '/integrations.js', '/integrations.css', '/skills.js', '/skills.css', '/navigation.js', '/manifest.webmanifest', '/service-worker.js']);
const failure = (status, message) => { throw Object.assign(new Error(message), { status, safe: true }); };
const jsonResponse = (value, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' }
});

function httpsOrigin(value) {
  try {
    if (typeof value !== 'string' || /[\s\x00-\x1f]/.test(value)) throw new Error();
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash ||
        url.pathname !== '/') throw new Error();
    return url.origin;
  } catch { failure(503, '接駁設定未完成：需要不含帳密的 HTTPS 根網址。'); }
}

function identity(request) {
  const user = request.headers.get('oai-authenticated-user-id');
  if (!user || user.length > 240 || /[\s\x00-\x1f]/.test(user)) failure(401, '請先使用 Sites 登入。');
  return user;
}

function accountFor(env, user) {
  let accounts;
  try { accounts = JSON.parse(env.BLUEPRINT_SITE_ACCOUNTS || ''); }
  catch { failure(503, '尚未設定工作台帳戶接駁。'); }
  if (!accounts || Array.isArray(accounts) || typeof accounts !== 'object') failure(503, '帳戶接駁設定格式不正確。');
  if (!Object.hasOwn(accounts, user)) failure(403, '你的 Sites 帳戶尚未獲准使用這個工作台。');
  const account = accounts[user];
  if (!account || typeof account !== 'object' || Array.isArray(account) ||
      Object.keys(account).some(key => !['username', 'password', 'read_only'].includes(key)) ||
      typeof account.username !== 'string' || !account.username || account.username.length > 128 ||
      /[:\x00-\x1f\x7f]/.test(account.username) || typeof account.password !== 'string' ||
      account.password.length < 12 || account.password.length > 1024 ||
      (account.read_only !== undefined && typeof account.read_only !== 'boolean')) {
    failure(503, '帳戶接駁設定格式不正確。');
  }
  return account;
}

async function limitedBody(request, limit) {
  const declared = request.headers.get('Content-Length');
  if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) failure(413, '內容超出大小上限。');
  const reader = request.body?.getReader();
  if (!reader) failure(400, '請提供 JSON 內容。');
  const chunks = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) { await reader.cancel(); failure(413, '內容超出大小上限。'); }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const body = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  return body;
}

function basic(account) {
  const bytes = new TextEncoder().encode(account.username + ':' + account.password);
  let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
  return 'Basic ' + btoa(binary);
}

function guardOrigin(request, origin) {
  const supplied = request.headers.get('Origin');
  if ((supplied && supplied !== origin) || request.headers.get('Sec-Fetch-Site') === 'cross-site') {
    failure(403, '不允許跨網站操作。');
  }
}

/** fetchImpl is injected only for tests; runtime destinations come from secrets. */
export function createNativeGateway({ tools, maintenanceTools, fetchImpl = fetch }) {
  return async function handle(request, env) {
    try {
      const url = new URL(request.url), site = httpsOrigin(env.BLUEPRINT_SITE_ORIGIN);
      if (url.origin !== site) failure(403, '網站入口與設定不符。');
      const user = identity(request);
      guardOrigin(request, site);
      if (url.pathname === '/_blueprint/identity' && request.method === 'GET') {
        return jsonResponse({ site_user_id: user, scope: 'this_site_only' });
      }
      const account = accountFor(env, user), backend = httpsOrigin(env.BLUEPRINT_BACKEND_ORIGIN);
      if (backend === site) failure(503, '原生後台與 Sites 入口不能是同一網址。');
      if (request.url.length > 8192) failure(414, '網址過長。');
      const isMcp = url.pathname === '/mcp';
      const isApi = url.pathname.startsWith('/api/');
      const isStatus = url.pathname === '/_blueprint/status';
      const isAsset = ASSETS.has(url.pathname) || /^\/icons\/[a-z]+\.svg$/.test(url.pathname);
      if (!(isMcp || isApi || isStatus || isAsset)) failure(404, '找不到此內容。');
      if (!['GET', 'POST', 'PATCH'].includes(request.method) ||
          ((isAsset || isStatus) && request.method !== 'GET') || (isMcp && request.method !== 'POST')) {
        failure(405, '此入口不支援這個操作。');
      }
      if (isApi && request.method !== 'GET') {
        if (account.read_only) failure(403, '這個連接只可讀取。');
        if (request.headers.get('X-Blueprint-Request') !== '1') failure(403, '請使用工作台或已授權工具寫入。');
      }
      if (request.method !== 'GET' && request.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase() !== 'application/json') {
        failure(415, '需要 application/json。');
      }

      // No caller headers (cookies, identity, authorization, forwarding) cross this boundary.
      // Never follow redirects with a backend credential. Never retry writes automatically.
      const upstream = async (path, method = 'GET', body) => {
        const headers = { Authorization: basic(account), Accept: '*/*', 'Accept-Encoding': 'identity' };
        if (body !== undefined) {
          headers['Content-Type'] = 'application/json';
          headers['X-Blueprint-Request'] = '1';
          headers['Content-Length'] = String(body.byteLength);
        }
        let response;
        try {
          response = await fetchImpl(backend + path, { method, headers, body,
            redirect: 'manual', signal: AbortSignal.timeout(30000) });
        } catch { failure(502, '未能連接原生後台；未確認保存，請先讀回資料再決定是否重試。'); }
        if (response.status >= 300 && response.status < 400) {
          await response.body?.cancel(); failure(502, '後台要求轉往其他網址，接駁已停止。');
        }
        if (response.status === 401) {
          await response.body?.cancel(); failure(502, '後台帳戶接駁未通過，請核對伺服器的私人設定。');
        }
        return response;
      };
      // Recheck on every request: a backend upgrade cannot leave stale write permission.
      const probe = await upstream('/api/runtime');
      let contract;
      try {
        if (probe.ok) contract = JSON.parse(new TextDecoder().decode(await limitedBody(probe, 16384)));
        else await probe.body?.cancel();
      }
      catch { contract = null; }
      if (!contract || contract.api_contract !== CONTRACT || contract.storage !== 'native' ||
          contract.maintenance_available !== true) {
        failure(503, '原生後台版本或備份功能未就緒；請先完成後台安裝及還原驗證。');
      }
      if (isStatus) return jsonResponse({ deployment_mode: 'sites_native', gateway_version: GATEWAY_VERSION,
        backend_origin: backend, backend: contract, chat_tools_verified: false,
        note: '能力檢查不代表已完成正式備份、異地保存或目前對話讀寫驗收。' });

      const proxy = async inner => {
        const target = new URL(inner.url);
        const body = inner.method === 'GET' ? undefined : await limitedBody(inner,
          target.pathname === '/api/files' ? FILE_LIMIT : JSON_LIMIT);
        const response = await upstream(target.pathname + target.search, inner.method, body);
        // Cookies, challenges, redirects and transport headers are never forwarded.
        const headers = new Headers({ 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
          'Referrer-Policy': 'no-referrer' });
        for (const key of ['Content-Type', 'Content-Disposition', 'Content-Security-Policy', 'X-Frame-Options', 'Retry-After']) {
          if (response.headers.has(key)) headers.set(key, response.headers.get(key));
        }
        if (target.pathname === '/api/info' && response.ok) {
          const info = JSON.parse(new TextDecoder().decode(await limitedBody(response, 1024 * 1024)));
          return jsonResponse({ ...info, connection_mode: 'sites', deployment_mode: 'sites_native',
            backend_origin: backend, gateway_version: GATEWAY_VERSION, api_contract: CONTRACT });
        }
        return new Response(response.body, { status: response.status, headers });
      };
      if (isMcp) {
        // Bound before passing to the shared parser; it must not buffer an unlimited body.
        const body = await limitedBody(request, JSON_LIMIT);
        const bounded = new Request(request.url, { method: 'POST', headers: request.headers, body });
        const mcp = createMcpHandler({ tools: [...tools, ...maintenanceTools],
          version: GATEWAY_VERSION, readOnly: account.read_only === true,
          authorize: () => {}, // Identity, account mapping and contract checked above for this request.
          dispatch: proxy });
        return await mcp(bounded, env);
      }
      return await proxy(request);
    } catch (error) {
      return jsonResponse({ error: error.safe ? error.message : '接駁未能完成，請檢查私人設定及後台狀態。' }, error.safe ? error.status : 502);
    }
  };
}
