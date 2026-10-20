import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowUpRight, Check, CheckCheck, Download, ExternalLink, FileText, KeyRound, Link2, ListTodo, LoaderCircle, MessageSquareText, Plus, RefreshCw, ShieldCheck, ShieldOff, TerminalSquare } from 'lucide-react';
import { api, extractList, formatDate, listResources, type Actor, type Resource } from '../api';
import { Badge, CopyButton, ErrorNotice, Loading, Modal } from '../components/Primitives';
import '../codex.css';

type ConnectionToken = { id: string; user_id: string; name: string; scopes: string[]; created_at: string; expires_at?: string | null; revoked_at?: string | null; last_used_at?: string | null };
type RecentState = { items: Resource[]; errors: string[] };
const sourceLabels: Record<string, string> = { note: '筆記', workday: '每日工作', task: '待辦事項' };

const starterPrompt = `請透過已連接的 Blueprint MCP，協助我管理這間公司的工作。

先讀取 blueprint_workspace_info，確認公司、時區、可用模組及工具；每次涉及人物、客戶或項目，使用 blueprint_context 查閱我有權讀取的真實紀錄，引用來源名稱、ID 及版本。同名、資料不足或關係不清時先問我，不要推測公司歷史。

當我描述一項工作，按背景提出少量必要問題，保存我的明確回答。日期使用公司的時區；「聽日」等相對日期須轉成明確日期，欠缺時間時向我詢問。

將確認內容分別保存為筆記、任務、日程及每日工作，預設私人可見，保留共同 activity_id、來源引用及問題／答案。筆記正文放在 data.content，並可用 data.blueprint_activity 保存 activity_id、source_refs、questions_answers。每一項寫入都以實際回傳的 ID 及版面連結回報；部分失敗要列明，不要聲稱全部完成。重新執行前先檢查既有結果，避免重複建立。

需要外部 AI 員工時，先檢查已登記能力及可分享來源，再使用派工工具，追查實際執行結果；未接入員工時說明尚未配置。不要把規劃、排隊或已送出請求當成工作已完成。

現在請先檢查連接，告訴我可以開始使用哪些功能。`;

function openResult(item: Resource) {
  sessionStorage.setItem('blueprint-open-resource', item.id);
  location.hash = ({ note: 'notes', workday: 'workdays', task: 'tasks' } as Record<string, string>)[item.kind] ?? 'overview';
}

export function CodexPage({ actor }: { actor: Actor }) {
  const [tokens, setTokens] = useState<ConnectionToken[]>([]);
  const [recent, setRecent] = useState<RecentState>({ items: [], errors: [] });
  const [loading, setLoading] = useState(true);
  const [tokenError, setTokenError] = useState<unknown>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('我的工作台');
  const [businessWrites, setBusinessWrites] = useState(true);
  const [execution, setExecution] = useState(false);
  const [newToken, setNewToken] = useState('');
  const [dialogError, setDialogError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);
  const [downloaded, setDownloaded] = useState(false);
  const generation = useRef(0);
  const readOnly = actor.role === 'viewer' || actor.role === 'guest';
  const endpoint = `${location.origin}/api/v1/mcp`;

  const load = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true); setTokenError(null);
    const kinds = ['note', 'workday', 'task'];
    const [credentialResult, ...contentResults] = await Promise.allSettled([api<ConnectionToken[]>('/tokens'), ...kinds.map(kind => listResources(kind))]);
    if (generation.current !== current) return;
    if (credentialResult.status === 'fulfilled') setTokens(extractList<ConnectionToken>(credentialResult.value).filter(item => item.user_id === actor.id && item.name.startsWith('Codex · ')));
    else { setTokens([]); setTokenError(credentialResult.reason); }
    const items: Resource[] = [], errors: string[] = [];
    contentResults.forEach((result, index) => {
      if (result.status === 'fulfilled') items.push(...extractList<Resource>(result.value));
      else errors.push(`${sourceLabels[kinds[index]]}：${result.reason instanceof Error ? result.reason.message : '暫時未能載入'}`);
    });
    setRecent({ items: items.sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 8), errors });
    setLoading(false);
  }, [actor.id]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load]);
  useEffect(() => { const changed = () => { void load(); }; window.addEventListener('blueprint:changed', changed); return () => window.removeEventListener('blueprint:changed', changed); }, [load]);

  function openDialog() {
    setName('我的工作台'); setBusinessWrites(!readOnly); setExecution(false);
    setNewToken(''); setDownloaded(false); setDialogError(null); setDialogOpen(true);
  }
  function closeDialog() { setDialogOpen(false); setNewToken(''); setDialogError(null); setDownloaded(false); }
  async function createToken(event: FormEvent) {
    event.preventDefault(); if (busy || newToken) return;
    setBusy(true); setDialogError(null);
    const scopes = ['content:read', 'business:read', 'integrations:read', 'workflows:read'];
    if (businessWrites && !readOnly) scopes.push('content:write', 'business:write');
    if (execution && !readOnly) scopes.push('workflows:execute', 'integrations:execute');
    try {
      const result = await api<{ token: string }>('/tokens', { method: 'POST', body: JSON.stringify({ name: `Codex · ${name.trim()}`, scopes, expires_at: new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).toISOString() }) });
      if (!result.token) throw new Error('伺服器未回傳連接金鑰。');
      setNewToken(result.token); await load();
    } catch (error) { setDialogError(error); } finally { setBusy(false); }
  }
  function downloadToken() {
    if (!newToken) return;
    const url = URL.createObjectURL(new Blob([`${newToken}\n`], { type: 'application/octet-stream' }));
    const link = document.createElement('a'); link.href = url; link.download = 'codex-token';
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setDownloaded(true);
  }
  async function revoke(item: ConnectionToken) {
    setRevoking(item.id); setTokenError(null);
    try { await api(`/tokens/${item.id}`, { method: 'DELETE' }); await load(); }
    catch (error) { setTokenError(error); } finally { setRevoking(null); }
  }

  return <div className="codex-page">
    <div className="codex-eyebrow"><span />YOUR INTELLIGENCE, CONNECTED.</div>
    <header className="codex-page-header"><div><h1>Codex 協作</h1><p>繼續在 Codex 對話，讓公司的資料與工作，在這裡有序累積。</p></div><button className="icon-button" aria-label="重新整理 Codex 協作" disabled={loading} onClick={() => { void load(); }}><RefreshCw size={18} className={loading ? 'spin' : ''} /></button></header>
    <div className="codex-flow" aria-label="協作流程"><span><MessageSquareText size={17} />在 Codex 講工作</span><i>→</i><span><Link2 size={17} />查背景、釐清問題</span><i>→</i><span><CheckCheck size={17} />成果回到工作台</span></div>
    <div className="codex-layout">
      <section className="codex-panel codex-setup-panel" aria-labelledby="codex-setup-title"><header><span className="codex-panel-icon"><TerminalSquare size={19} /></span><div><span className="codex-small-label">GET CONNECTED</span><h2 id="codex-setup-title">連接你的 Codex</h2></div></header>
        <ol className="codex-steps"><li><span>01</span><div><h3>啟動藍圖，登入自己的帳戶</h3><p>使用已安裝的公司後台，Codex 會沿用這個帳戶被授予的資料範圍。</p></div></li><li><span>02</span><div><h3>建立專用連接金鑰</h3><p>在右方建立金鑰，下載 <code>codex-token</code>，移到安裝時的私人設定目錄。</p></div></li><li><span>03</span><div><h3>在程式目錄執行連接工具</h3><div className="codex-command"><small>macOS / Linux</small><code>./scripts/v2/connect-codex.sh --private-dir ../blueprint-private</code></div><div className="codex-command"><small>Windows · PowerShell 7.2+</small><code>./scripts/v2/connect-codex.ps1 -PrivateDirectory ../blueprint-private</code></div><p>使用自己的私人目錄位置；連接工具把金鑰留在私人檔案，設定中只引用檔案位置。</p></div></li><li><span>04</span><div><h3>重新載入 MCP，開始一次對話</h3><p>在 Codex 重新載入連接或開啟新對話，再貼上下方開始指示。連接成功由 Codex 實際讀取工具確認。</p></div></li></ol>
        <div className="codex-endpoint"><span>MCP 接口</span><code>{endpoint}</code><CopyButton value={endpoint} label="複製網址" /></div>
      </section>
      <section className="codex-panel codex-credentials-panel" aria-labelledby="codex-credentials-title"><header><span className="codex-panel-icon"><KeyRound size={19} /></span><div><span className="codex-small-label">YOUR CONNECTIONS</span><h2 id="codex-credentials-title">我的連接金鑰</h2></div><button className="button small" onClick={openDialog}><Plus size={14} />建立連接金鑰</button></header>
        <p className="codex-panel-intro">每個連接獨立授權，有效期 90 日，可以隨時撤銷。</p><ErrorNotice error={tokenError} onRetry={() => { void load(); }} />
        {loading ? <Loading label="正在讀取連接資料…" /> : tokens.length ? <div className="codex-token-list">{tokens.map(item => {
          const expired = !!item.expires_at && new Date(item.expires_at).getTime() <= Date.now();
          return <article key={item.id} className="codex-token-card" data-token-id={item.id}><div className="codex-token-top"><strong>{item.name}</strong><Badge tone={item.revoked_at || expired ? '' : 'blue'}>{item.revoked_at ? '已撤銷' : expired ? '已到期' : '有效'}</Badge></div><div className="codex-token-metadata"><span>最近使用</span><strong>{item.last_used_at ? formatDate(item.last_used_at) : '尚無使用紀錄'}</strong><span>到期</span><strong>{formatDate(item.expires_at)}</strong></div><div className="codex-token-footer"><small>{item.scopes.includes('business:write') ? '可整理及儲存工作' : '唯讀資料'}{item.scopes.includes('workflows:execute') ? ' · 可執行工作流程' : ''}</small>{!item.revoked_at && <button className="codex-revoke" disabled={revoking === item.id} onClick={() => { void revoke(item); }} aria-label={`撤銷 ${item.name}`}>{revoking === item.id ? <LoaderCircle size={13} className="spin" /> : <ShieldOff size={13} />}撤銷</button>}</div></article>;
        })}</div> : !tokenError && <div className="codex-card-empty"><ShieldCheck size={27} /><h3>為 Codex 開一扇合適的門</h3><p>建立專用金鑰後，這裡會保留它的權限、期限和最近使用時間。</p></div>}
        <p className="codex-usage-note">「最近使用」是金鑰的實際請求紀錄，並不代表 Codex 目前在線。</p>
      </section>
      <section className="codex-panel codex-prompt-panel" aria-labelledby="codex-prompt-title"><header><span className="codex-panel-icon"><MessageSquareText size={19} /></span><div><span className="codex-small-label">A GOOD FIRST CONVERSATION</span><h2 id="codex-prompt-title">把這段指示交給 Codex</h2></div><CopyButton value={starterPrompt} label="複製開始指示" /></header><p className="codex-panel-intro">由真實公司資料開始，讓每次對話留下可追溯的成果。</p><textarea className="codex-starter-prompt" aria-label="Codex 開始指示" readOnly value={starterPrompt} rows={12} onFocus={event => event.currentTarget.select()} /><div className="codex-example"><span>例如</span><p>「我聽日會同裝修公司黃老細見面，幫我查返項目背景，再問我需要準備啲乜。」</p></div></section>
      <section className="codex-panel codex-results-panel" aria-labelledby="codex-results-title"><header><span className="codex-panel-icon"><FileText size={19} /></span><div><span className="codex-small-label">WORK, KEPT IN PLACE</span><h2 id="codex-results-title">最近保存的工作</h2></div><a href="#workdays" className="codex-module-link">每日工作<ArrowUpRight size={14} /></a></header><p className="codex-panel-intro">你與 AI 保存的筆記、待辦及工作紀錄，共用相同的資料與權限。</p>
        {recent.errors.length > 0 && <div className="codex-content-errors" role="status">{recent.errors.map(error => <p key={error}>{error}</p>)}</div>}
        {loading ? <Loading label="正在讀取工作內容…" /> : recent.items.length ? <div className="codex-result-list">{recent.items.map(item => <button className="codex-result-row" key={item.id} onClick={() => openResult(item)}><span className="codex-result-icon">{item.kind === 'task' ? <ListTodo size={18} /> : <FileText size={18} />}</span><span><strong>{item.name}</strong><small>{sourceLabels[item.kind]} · {formatDate(item.updated_at)}</small></span><ExternalLink size={14} /></button>)}</div> : !recent.errors.length && <div className="codex-card-empty"><CheckCheck size={27} /><h3>第一份成果，會從一次對話開始</h3><p>請 Codex 整理並保存工作後，回來重新整理，即可查看實際內容。</p></div>}
      </section>
    </div>
    {dialogOpen && <Modal title={newToken ? 'Codex 連接金鑰已建立' : '建立 Codex 連接金鑰'} subtitle={newToken ? '下載後放入安裝時的私人設定目錄。' : '選擇這個 Codex 連接可以使用的能力。'} onClose={closeDialog}><form onSubmit={createToken}><div className="modal-body">
      {newToken ? <><div className="codex-secret-ready"><span><Check size={21} /></span><div><h3>連接憑證準備好了</h3><p>金鑰只會在這次建立後提供；關閉後將無法再次下載。</p></div></div><label className="field"><span>連接金鑰</span><input type="password" readOnly autoComplete="off" aria-label="新建立的 Codex 連接金鑰" value={newToken} /></label><button className="button primary codex-download-token" type="button" onClick={downloadToken}>{downloaded ? <Check size={16} /> : <Download size={16} />}{downloaded ? '再次下載 codex-token' : '下載 codex-token'}</button><p className="codex-secret-note">將檔案移到 <code>blueprint-private/codex-token</code>。這份檔案是存取憑證，請放在自己的私人設定目錄，不要上載至 GitHub 或貼到對話。</p></> : <><label className="field"><span>連接名稱</span><input value={name} onChange={event => setName(event.target.value)} required maxLength={120} placeholder="例如：我的電腦" /></label><div className="codex-base-scopes"><ShieldCheck size={16} /><span>可讀取目前帳戶有權存取的公司內容、業務資料及流程設定。</span></div><label className="checkbox-label"><input type="checkbox" checked={businessWrites && !readOnly} disabled={readOnly} onChange={event => setBusinessWrites(event.target.checked)} />允許整理及儲存工作</label><label className="checkbox-label"><input type="checkbox" checked={execution && !readOnly} disabled={readOnly} onChange={event => setExecution(event.target.checked)} />允許執行工作流程及向 AI 員工派工</label>{readOnly && <p className="codex-secret-note">目前帳戶使用唯讀連接；實際資料仍受帳戶角色及內容權限限制。</p>}<p className="codex-secret-note">有效期為建立起計 90 日。這個連接不包含管理員權限。</p></>}
      <ErrorNotice error={dialogError} /></div><div className="modal-footer"><button className="button" type="button" onClick={closeDialog}>{newToken ? '完成並關閉' : '取消'}</button>{!newToken && <button className="button primary" disabled={busy || !name.trim()}>{busy ? <LoaderCircle size={15} className="spin" /> : <KeyRound size={15} />}{busy ? '建立中…' : '建立金鑰'}</button>}</div></form></Modal>}
  </div>;
}
