import { useCallback, useEffect, useState } from 'react';
import { Activity, ArrowUpRight, Blocks, Building2, Check, ChevronRight, CircleHelp, Database, FileClock, HardDrive, KeyRound, LockKeyhole, Network, Plus, RefreshCw, Search, Settings, ShieldCheck, ShieldOff, Trash2, Users } from 'lucide-react';
import { api, createResource, deleteResource, extractList, formatDate, listResources, type Actor, type Resource } from '../api';
import { Avatar, Badge, CopyButton, EmptyState, ErrorNotice, Loading, Modal } from '../components/Primitives';

type Toast = (message: string, tone?: 'success' | 'error') => void;
const moduleNames: Record<string, [string, string]> = {
  files: ['文件與分享', '文件、資料夾、版本、分享及垃圾桶。'],
  collections: ['個人收藏', '星號及個人內容集合。'],
  knowledge: ['知識入口', '為公司、部門及專案整理可分享的內容入口。'],
  notes: ['協作筆記', '在共同文件中整理想法、記錄會議及保存知識。'],
  canvas: ['協作白板', '以便利貼、圖形及連接線整理思路。'],
  databases: ['業務資料庫', '自訂欄位、關聯、視圖及公司工作台。'],
  tasks: ['任務及審批', '指派工作、追蹤期限與管理批准流程。'],
  workdays: ['每日工作', '保存成果、決定、參考及下一步。'],
  calendar: ['日程', '管理事件、提醒及外部日曆同步。'],
  forms: ['表單', '收集資料、附件及啟動後續工作。'],
  workflows: ['工作流程', '設定事件、條件、步驟及人工審批。'],
  integrations: ['整合及外部 AI', '連接 API、外部員工、MCP 及 webhook。'],
  sign: ['電子簽署', '管理簽署請求、模板、收件人及證據。'],
  documents: ['文件生成', '將結構資料轉換成 DOCX 或 PDF 文件。'],
  security: ['內容安全', '資料分級、防外洩規則及安全警報。'],
  governance: ['企業治理', '歸檔、保存期限及法務保全。'],
};
const serviceInfo: Record<string, [string, string, string]> = {
  gotenberg: ['文件預覽', 'Gotenberg', 'BLUEPRINT_GOTENBERG_URL'],
  tika: ['全文抽取', 'Apache Tika', 'BLUEPRINT_TIKA_URL'],
  clamav: ['惡意檔案掃描', 'ClamAV', 'BLUEPRINT_CLAMAV_URL'],
  documenso: ['電子簽署', 'Documenso CE', 'BLUEPRINT_DOCUMENSO_URL'],
  collabora: ['Office 協作', 'Collabora', 'BLUEPRINT_COLLABORA_URL'],
  keycloak: ['企業身份', 'Keycloak', 'BLUEPRINT_KEYCLOAK_URL'],
  smtp: ['郵件服務', 'SMTP', 'BLUEPRINT_SMTP_URL'],
  s3: ['物件儲存', 'S3 / SeaweedFS', 'BLUEPRINT_S3_ENDPOINT'],
  google_calendar: ['Google 日曆', 'Google Calendar', 'BLUEPRINT_GOOGLE_CLIENT_ID'],
  microsoft_calendar: ['Microsoft 日曆', 'Microsoft Graph', 'BLUEPRINT_MICROSOFT_CLIENT_ID'],
};

export function AdminPage({ section, actor, settings, onSettingsChanged, toast }: { section: string; actor: Actor; settings: any; onSettingsChanged: () => void; toast: Toast }) {
  const admin = ['admin', 'owner'].includes(actor.role);
  const title = ({ settings: '工作空間設定', users: '成員及群組', tokens: '服務身份及 API 金鑰', audit: '操作紀錄', jobs: '背景工作', account: '我的帳戶' } as Record<string, string>)[section] ?? '管理工作空間';
  if (!admin && section !== 'account') return <div className="page-content"><EmptyState icon={<LockKeyhole size={34} />} title="需要管理員權限" description="請聯絡公司管理員，取得這個功能的存取權限。" /></div>;
  return <div className="admin-page page-content"><div className="breadcrumbs"><span>工作空間<ChevronRight size={13} />管理</span></div><div className="page-header"><div><h1>{title}</h1><p>{section === 'account' ? '管理你的身份及登入安全。' : '讓公司的內容、成員和服務，以清晰的方式運作。'}</p></div><Badge tone="blue">{section === 'account' ? '個人設定' : '管理員'}</Badge></div>
    {admin && <nav className="admin-tabs" aria-label="管理設定"><a href="#settings" className={section === 'settings' ? 'active' : ''}><Settings size={15} />一般設定</a><a href="#users" className={section === 'users' ? 'active' : ''}><Users size={15} />成員及群組</a><a href="#tokens" className={section === 'tokens' ? 'active' : ''}><KeyRound size={15} />API 金鑰</a><a href="#audit" className={section === 'audit' ? 'active' : ''}><FileClock size={15} />操作紀錄</a><a href="#jobs" className={section === 'jobs' ? 'active' : ''}><Activity size={15} />背景工作</a></nav>}
    {section === 'settings' && <SettingsView settings={settings} onSaved={onSettingsChanged} toast={toast} />}
    {section === 'users' && <UsersView actor={actor} toast={toast} />}
    {section === 'tokens' && <TokensView toast={toast} />}
    {section === 'audit' && <AuditView />}
    {section === 'jobs' && <JobsView toast={toast} />}
    {section === 'account' && <AccountView actor={actor} toast={toast} />}
  </div>;
}

function SettingsView({ settings, onSaved, toast }: { settings: any; onSaved: () => void; toast: Toast }) {
  const [tab, setTab] = useState('company');
  const [company, setCompany] = useState<any>({ name: '', industry: '', description: '', timezone: 'Asia/Hong_Kong', language: 'zh-Hant-HK' });
  const [modules, setModules] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { if (settings) { setCompany({ timezone: 'Asia/Hong_Kong', language: 'zh-Hant-HK', ...settings.company }); setModules(Object.fromEntries(Object.keys(moduleNames).map(key => [key, settings.modules?.[key] !== false]))); } }, [settings]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try { await api('/settings', { method: 'PATCH', body: JSON.stringify(tab === 'modules' ? { modules } : { company }) }); onSaved(); toast('工作空間設定已儲存'); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  const services = settings?.services ?? {};
  return <div className="settings-layout"><aside className="settings-subnav"><button className={tab === 'company' ? 'active' : ''} onClick={() => setTab('company')}><Building2 size={17} />公司資料</button><button className={tab === 'modules' ? 'active' : ''} onClick={() => setTab('modules')}><Blocks size={17} />功能模組</button><button className={tab === 'services' ? 'active' : ''} onClick={() => setTab('services')}><Network size={17} />服務連接</button><button className={tab === 'storage' ? 'active' : ''} onClick={() => setTab('storage')}><HardDrive size={17} />資料與部署</button></aside>
    <div className="settings-main"><ErrorNotice error={error} />{tab === 'company' && <form className="panel settings-panel" onSubmit={save}><div className="panel-heading"><h2>公司資料</h2><p>這些資料會用於工作空間名稱及公司資訊。</p></div><div className="panel-body"><label className="field"><span>公司或工作空間名稱</span><input value={company.name ?? ''} onChange={event => setCompany({ ...company, name: event.target.value })} required maxLength={120} /></label><label className="field"><span>行業 <small>選填</small></span><input value={company.industry ?? ''} onChange={event => setCompany({ ...company, industry: event.target.value })} placeholder="例如：設計、顧問、零售" /></label><label className="field"><span>公司簡介 <small>選填</small></span><textarea rows={4} value={company.description ?? ''} onChange={event => setCompany({ ...company, description: event.target.value })} /></label><label className="field"><span>目前目標 <small>選填</small></span><textarea rows={3} value={company.goals ?? ''} onChange={event => setCompany({ ...company, goals: event.target.value })} /></label><div className="form-grid"><label className="field"><span>預設時區</span><select value={company.timezone ?? 'Asia/Hong_Kong'} onChange={event => setCompany({ ...company, timezone: event.target.value })}>{['Asia/Hong_Kong', 'Asia/Taipei', 'Asia/Shanghai', 'Asia/Singapore', 'Asia/Tokyo', 'Europe/London', 'America/New_York', 'America/Los_Angeles', 'UTC'].map(zone => <option key={zone}>{zone}</option>)}</select></label><label className="field"><span>介面語言</span><select value={company.language ?? 'zh-Hant-HK'} onChange={event => setCompany({ ...company, language: event.target.value })}><option value="zh-Hant-HK">繁體中文（香港）</option></select></label></div></div><div className="panel-footer"><span className="muted">設定由公司工作空間保存。</span><button className="button primary" disabled={busy}>{busy ? '儲存中…' : '儲存變更'}</button></div></form>}
    {tab === 'modules' && <form className="panel" onSubmit={save}><div className="panel-heading"><h2>按公司的需要配置功能</h2><p>停用模組會阻止新操作，既有資料及歷史仍然保留。保存政策會繼續生效。</p></div><div className="module-list">{Object.entries(moduleNames).map(([key, [name, description]]) => <label className="module-row" key={key}><div className="module-row-icon"><Blocks size={19} /></div><span><strong>{name}</strong><small>{description}</small></span><input type="checkbox" role="switch" checked={modules[key] !== false} onChange={event => setModules({ ...modules, [key]: event.target.checked })} /><span className="switch-visual" aria-hidden="true" /></label>)}</div><div className="panel-footer"><span className="muted">後台會驗證模組及依賴。</span><button className="button primary" disabled={busy}>{busy ? '儲存中…' : '儲存模組設定'}</button></div></form>}
    {tab === 'services' && <><div className="panel-heading standalone"><h2>你掌握每一個服務連接</h2><p>免費自架服務由部署設定連接。未設定的服務不會顯示為已啟用。</p></div><div className="service-grid">{Object.entries(serviceInfo).map(([key, [name, engine, variable]]) => { const state = services[key]; const configured = state === true || typeof state === 'string' && !!state || state?.configured === true; const ready = state?.healthy === true || state?.available === true; return <article className="panel service-card" key={key}><div className="service-card-top"><span className="service-icon"><Network size={21} /></span><Badge tone={ready ? 'green' : configured ? 'blue' : ''}>{ready ? '服務就緒' : configured ? '已設定' : '尚未設定'}</Badge></div><h3>{name}</h3><p>{engine}</p><div className="service-config-note"><span>部署環境變數</span><code>{variable}</code></div>{state?.error && <p className="danger-text">{state.error}</p>}{key === 'google_calendar' || key === 'microsoft_calendar' ? <a className="text-link" href="#calendar">管理日曆授權<ArrowUpRight size={13} /></a> : null}</article>; })}</div><div className="subtle-notice"><ShieldCheck size={17} />金鑰及密碼由伺服器的私有部署設定保存，這裡只顯示服務狀態。</div></>}
    {tab === 'storage' && <div className="panel settings-panel"><div className="panel-heading"><h2>資料與部署</h2><p>一間公司，一個獨立工作空間。你可以在本機或自己的雲端託管。</p></div><div className="panel-body"><div className="storage-fact"><Database size={21} /><div><strong>PostgreSQL</strong><p>帳戶、資料、權限、工作流程及操作紀錄。</p></div><Badge tone="blue">持久保存</Badge></div><div className="storage-fact"><HardDrive size={21} /><div><strong>{services.s3?.configured || services.s3 === true ? 'S3 相容物件儲存' : '私人持久儲存'}</strong><p>原始文件與衍生預覽分開保存。</p></div></div><div className="storage-fact"><ShieldCheck size={21} /><div><strong>備份與還原</strong><p>由伺服器管理工具執行完整備份及還原，涵蓋資料庫與原始附件。</p></div></div><div className="subtle-notice"><CircleHelp size={17} />公開程式碼更新不應取代你的私人資料目錄。正式更新前，先驗證可還原的完整備份。</div></div></div>}
    </div></div>;
}

function UsersView({ actor, toast }: { actor: Actor; toast: Toast }) {
  const [users, setUsers] = useState<Actor[]>([]);
  const [groups, setGroups] = useState<Resource[]>([]);
  const [tab, setTab] = useState('users');
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState<'user' | 'group' | null>(null);
  const [editing, setEditing] = useState<Actor | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('member');
  const [memberships, setMemberships] = useState<string[]>([]);
  const [activationUrl, setActivationUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try { const [members, teams] = await Promise.all([api('/users'), listResources('group')]); setUsers(extractList(members)); setGroups(extractList(teams)); }
    catch (e) { setError(e); } finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  function openUser(user?: Actor) { setEditing(user ?? null); setName(user?.name ?? ''); setEmail(user?.email ?? ''); setRole(user?.role ?? 'member'); setMemberships(user?.groups ?? []); setPassword(''); setActivationUrl(''); setDialog('user'); setError(null); }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      if (dialog === 'group') { await createResource({ kind: 'group', name, visibility: 'company', data: {} }); setDialog(null); toast('群組已建立'); }
      else {
        const result = await api<any>(editing ? `/users/${editing.id}` : '/users', { method: editing ? 'PATCH' : 'POST', body: JSON.stringify({ name, ...(!editing ? { email } : {}), role, groups: memberships, ...(!editing && password ? { password } : {}) }) });
        if (result.activation_url || result.invitation_url || result.invite_url) setActivationUrl(new URL(result.activation_url ?? result.invitation_url ?? result.invite_url, location.origin).href);
        else setDialog(null);
        toast(editing ? '成員資料已更新' : '已建立成員帳戶');
      }
      await load();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function toggleUser(user: Actor) {
    try { await api(`/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ active: user.active === false }) }); await load(); toast(user.active === false ? '已啟用帳戶' : '已停用帳戶'); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); }
  }
  return <><div className="management-toolbar"><div className="segmented"><button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}>成員 <span>{users.length}</span></button><button className={tab === 'groups' ? 'active' : ''} onClick={() => setTab('groups')}>群組 <span>{groups.length}</span></button></div><div className="toolbar-right"><div className="inline-search"><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋成員或群組" aria-label="搜尋成員或群組" /></div><button className="icon-button" aria-label="重新整理成員及群組" onClick={() => { void load(); }} disabled={loading}><RefreshCw size={16} /></button><button className="button primary" onClick={() => { if (tab === 'users') openUser(); else { setName(''); setDialog('group'); } }}><Plus size={16} />{tab === 'users' ? '新增成員' : '建立群組'}</button></div></div><ErrorNotice error={!dialog ? error : null} onRetry={() => { void load(); }} />
    {loading ? <Loading /> : tab === 'users' ? <div className="panel table-wrap"><table className="data-table"><thead><tr><th>成員</th><th>角色</th><th>群組</th><th>狀態</th><th /></tr></thead><tbody>{users.filter(user => `${user.name} ${user.email}`.toLowerCase().includes(query.toLowerCase())).map(user => <tr key={user.id}><td><div className="member-cell"><Avatar name={user.name || user.email} /><span><strong>{user.name || user.email}{user.id === actor.id && <small>你</small>}</strong><span>{user.email}</span></span></div></td><td><Badge tone={user.role === 'owner' || user.role === 'admin' ? 'blue' : ''}>{({ owner: '擁有者', admin: '管理員', member: '成員', viewer: '檢視者', guest: '外部協作者' } as Record<string, string>)[user.role] ?? user.role}</Badge></td><td><div className="tag-list">{user.groups?.map(id => <span key={id}>{groups.find(group => group.id === id)?.name ?? id.slice(0, 8)}</span>)}</div></td><td><span className={`status-dot ${user.active === false ? 'status-inactive' : ''}`} />{user.active === false ? '已停用' : '使用中'}</td><td><div className="table-row-buttons"><button className="button small" onClick={() => openUser(user)}>編輯</button>{user.id !== actor.id && <button className="button small" onClick={() => { void toggleUser(user); }}>{user.active === false ? '啟用' : '停用'}</button>}</div></td></tr>)}</tbody></table></div> : groups.length ? <div className="group-grid">{groups.filter(group => group.name.toLowerCase().includes(query.toLowerCase())).map(group => <article className="panel group-card" key={group.id}><span className="group-icon"><Users size={24} /></span><h3>{group.name}</h3><p>{users.filter(user => user.groups?.includes(group.id)).length} 位成員</p><small className="muted">在成員設定中加入或移除群組。</small><button className="button small" onClick={() => { void deleteResource(group).then(load).then(() => toast('群組已刪除')).catch(e => toast(e.message, 'error')); }}><Trash2 size={13} />刪除群組</button></article>)}</div> : <div className="panel"><EmptyState icon={<Users size={35} />} title="以群組管理團隊" description="建立部門或專案群組，再為成員設定一致的存取權限。"><button className="button primary" onClick={() => { setName(''); setDialog('group'); }}>建立第一個群組</button></EmptyState></div>}
    {dialog && <Modal title={dialog === 'group' ? '建立群組' : editing ? '編輯成員' : '新增公司成員'} onClose={() => setDialog(null)}><form onSubmit={save}><div className="modal-body">{activationUrl ? <><div className="success-notice"><Check size={18} />成員已建立，請將啟用連結交給對方。</div><label className="field"><span>啟用連結</span><input value={activationUrl} readOnly onFocus={event => event.target.select()} /></label><CopyButton value={activationUrl} /></> : <><label className="field"><span>{dialog === 'group' ? '群組名稱' : '名稱'}</span><input value={name} onChange={event => setName(event.target.value)} required /></label>{dialog === 'user' && <><label className="field"><span>電郵地址</span><input type="email" value={email} onChange={event => setEmail(event.target.value)} required disabled={!!editing} /></label><label className="field"><span>角色</span><select value={role} onChange={event => setRole(event.target.value)}><option value="member">成員</option><option value="viewer">檢視者</option><option value="guest">外部協作者</option><option value="admin">管理員</option>{editing?.role === 'owner' && <option value="owner">擁有者</option>}</select></label>{!editing && <label className="field"><span>初始密碼 <small>留空產生啟用連結</small></span><input type="password" minLength={12} value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" placeholder="最少 12 個字元" /></label>}{groups.length > 0 && <fieldset className="field"><legend>所屬群組</legend><div className="checkbox-grid">{groups.map(group => <label className="checkbox-label" key={group.id}><input type="checkbox" checked={memberships.includes(group.id)} onChange={event => setMemberships(event.target.checked ? [...memberships, group.id] : memberships.filter(id => id !== group.id))} />{group.name}</label>)}</div></fieldset>}</>}</>}<ErrorNotice error={error} /></div><div className="modal-footer"><button className="button" type="button" onClick={() => setDialog(null)}>{activationUrl ? '完成' : '取消'}</button>{!activationUrl && <button className="button primary" disabled={busy}>{busy ? '儲存中…' : '儲存成員資料'}</button>}</div></form></Modal>}
  </>;
}

function TokensView({ toast }: { toast: Toast }) {
  const [tokens, setTokens] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState(false);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState(['content:read']);
  const [resources, setResources] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const scopeOptions = ['content:read', 'content:write', 'business:read', 'business:write', 'workflows:read', 'workflows:write', 'workflows:execute', 'integrations:read', 'integrations:write', 'admin:read'];
  const load = useCallback(async () => { setLoading(true); try { setTokens(extractList(await api('/tokens'))); } catch (e) { setError(e); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  async function create(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try { const result = await api<any>('/tokens', { method: 'POST', body: JSON.stringify({ name, scopes, ...(resources.trim() ? { resource_ids: resources.split(/[\s,]+/).filter(Boolean) } : {}) }) }); if (!result.token) throw new Error('伺服器未回傳新金鑰。'); setToken(result.token); await load(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function revoke(item: any) { try { await api(`/tokens/${item.id}`, { method: 'DELETE' }); await load(); toast('金鑰已撤銷'); } catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); } }
  return <><div className="management-toolbar"><p className="muted">讓外部工具及 AI 員工以獨立權限接入，隨時撤銷。</p><button className="button primary" onClick={() => { setDialog(true); setName(''); setToken(''); setError(null); }}><Plus size={16} />建立 API 金鑰</button></div><ErrorNotice error={!dialog ? error : null} />{loading ? <Loading /> : tokens.length ? <div className="panel table-wrap"><table className="data-table"><thead><tr><th>名稱</th><th>權限範圍</th><th>建立時間</th><th>狀態</th><th /></tr></thead><tbody>{tokens.map(item => <tr key={item.id}><td><div className="name-with-icon"><KeyRound size={17} /><strong>{item.name}</strong></div></td><td><div className="tag-list">{(item.scopes ?? []).map((scope: string) => <span key={scope}>{scope}</span>)}</div></td><td>{formatDate(item.created_at)}</td><td><Badge tone={item.revoked_at ? '' : 'green'}>{item.revoked_at ? '已撤銷' : '有效'}</Badge></td><td>{!item.revoked_at && <button className="button small danger-text" onClick={() => { void revoke(item); }}><ShieldOff size={13} />撤銷</button>}</td></tr>)}</tbody></table></div> : <div className="panel"><EmptyState icon={<KeyRound size={34} />} title="為每個工具建立獨立金鑰" description="設定最少需要的權限與內容範圍，讓每次外部操作都可追查。"><button className="button primary" onClick={() => { setDialog(true); setToken(''); setError(null); }}>建立第一個金鑰</button></EmptyState></div>}
    {dialog && <Modal title={token ? 'API 金鑰已建立' : '建立 API 金鑰'} onClose={() => { setDialog(false); setToken(''); }}><form onSubmit={create}><div className="modal-body">{token ? <><div className="subtle-notice"><KeyRound size={17} />金鑰只會在建立時完整顯示。請保存到工具的私有設定。</div><textarea className="token-output" rows={4} value={token} readOnly aria-label="新建立的 API 金鑰" onFocus={event => event.target.select()} /><CopyButton value={token} /><p className="muted">已建立：{name}</p></> : <><label className="field"><span>用途名稱</span><input value={name} onChange={event => setName(event.target.value)} required placeholder="例如：公司研究員" /></label><fieldset className="field"><legend>允許的範圍</legend><div className="checkbox-grid">{scopeOptions.map(scope => <label className="checkbox-label" key={scope}><input type="checkbox" checked={scopes.includes(scope)} onChange={event => setScopes(event.target.checked ? [...scopes, scope] : scopes.filter(value => value !== scope))} /><code>{scope}</code></label>)}</div></fieldset><label className="field"><span>指定內容 ID <small>選填</small></span><textarea value={resources} onChange={event => setResources(event.target.value)} rows={3} placeholder="每行一個內容 UUID；留空仍受身份及範圍權限限制" /></label></>}<ErrorNotice error={error} /></div><div className="modal-footer"><button type="button" className="button" onClick={() => { setDialog(false); setToken(''); }}>{token ? '完成並關閉' : '取消'}</button>{!token && <button className="button primary" disabled={busy || !scopes.length}>{busy ? '建立中…' : '建立金鑰'}</button>}</div></form></Modal>}
  </>;
}

function AuditView() {
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [detail, setDetail] = useState<any>(null);
  const load = useCallback(async () => { setLoading(true); setError(null); try { setItems(extractList(await api('/audit'))); } catch (e) { setError(e); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  const rows = items.filter(item => JSON.stringify(item).toLowerCase().includes(query.toLowerCase()));
  return <><div className="management-toolbar"><div className="inline-search"><Search size={15} /><input aria-label="搜尋操作紀錄" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋操作、成員或內容 ID" /></div><button className="button" onClick={() => { void load(); }}><RefreshCw size={15} />重新整理</button></div><ErrorNotice error={error} />{loading ? <Loading /> : rows.length ? <div className="panel table-wrap"><table className="data-table audit-table"><thead><tr><th>時間</th><th>操作</th><th>執行者</th><th>內容</th><th /></tr></thead><tbody>{rows.map((item, index) => <tr key={item.id ?? index}><td>{formatDate(item.created_at)}</td><td><code className="action-code">{item.action}</code></td><td>{item.actor_name ?? item.actor_email ?? item.actor_id?.slice(0, 8) ?? '系統'}</td><td><span className="mono muted">{item.resource_id?.slice(0, 13) ?? '—'}</span></td><td><button className="button small" onClick={() => setDetail(item)}>查看詳情</button></td></tr>)}</tbody></table><div className="list-footer">最近 {rows.length} 項操作紀錄</div></div> : <div className="panel"><EmptyState icon={<FileClock size={34} />} title={query ? '沒有符合的操作紀錄' : '操作紀錄會在這裡累積'} description="公司成員及外部工具的操作均會留下可追查的紀錄。" /></div>}{detail && <Modal title="操作詳情" subtitle={detail.action} onClose={() => setDetail(null)}><div className="modal-body"><pre className="json-view">{JSON.stringify(detail, null, 2)}</pre></div></Modal>}</>;
}

function JobsView({ toast }: { toast: Toast }) {
  const [jobs, setJobs] = useState<any[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');
  const [detail, setDetail] = useState<any>(null);
  const load = useCallback(async () => { setLoading(true); setError(null); try { setJobs(extractList(await api('/jobs'))); } catch (e) { setError(e); } finally { setLoading(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  const labels: Record<string, string> = { queued: '等候執行', running: '執行中', waiting: '等待處理', completed: '已完成', failed: '失敗', cancel_requested: '正要求取消', cancelled: '已取消' };
  async function cancel(job: any) { try { await api(`/jobs/${job.id}/cancel`, { method: 'POST', body: '{}' }); await load(); toast('已發出取消請求'); } catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); } }
  return <><div className="management-toolbar"><div className="segmented">{[['all', '全部'], ['running', '執行中'], ['failed', '需要處理'], ['completed', '已完成']].map(([value, label]) => <button key={value} className={filter === value ? 'active' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div><button className="button" onClick={() => { void load(); }}><RefreshCw size={15} />重新整理</button></div><ErrorNotice error={error} />{loading ? <Loading /> : jobs.filter(job => filter === 'all' || job.status === filter).length ? <div className="panel table-wrap"><table className="data-table"><thead><tr><th>工作</th><th>狀態</th><th>嘗試次數</th><th>更新時間</th><th /></tr></thead><tbody>{jobs.filter(job => filter === 'all' || job.status === filter).map(job => <tr key={job.id}><td><strong>{job.kind}</strong><small className="table-subtitle mono">{job.id.slice(0, 12)}</small></td><td><Badge tone={job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : job.status === 'running' ? 'blue' : ''}>{labels[job.status] ?? job.status}</Badge></td><td>{job.attempts ?? 0}</td><td>{formatDate(job.updated_at)}</td><td><div className="table-row-buttons"><button className="button small" onClick={() => setDetail(job)}>詳情</button>{['queued', 'running', 'waiting'].includes(job.status) && <button className="button small" onClick={() => { void cancel(job); }}>取消</button>}</div></td></tr>)}</tbody></table></div> : <div className="panel"><EmptyState icon={<Activity size={34} />} title="這裡暫時沒有背景工作" description="文件處理、同步及工作流程執行後，可以在這裡查看狀態。" /></div>}{detail && <Modal title="背景工作詳情" subtitle={detail.kind} onClose={() => setDetail(null)} wide><div className="modal-body"><Badge tone={detail.status === 'failed' ? 'red' : 'blue'}>{labels[detail.status] ?? detail.status}</Badge>{detail.error && <ErrorNotice error={detail.error} />}<pre className="json-view">{JSON.stringify(detail, null, 2)}</pre></div></Modal>}</>;
}

function AccountView({ actor, toast }: { actor: Actor; toast: Toast }) {
  const [password, setPassword] = useState('');
  const [nextPassword, setNextPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [mfa, setMfa] = useState<any>(null);
  const [code, setCode] = useState('');
  const [mfaDialog, setMfaDialog] = useState(false);
  useEffect(() => { void api<any[]>('/users').then(value => { const me = extractList<any>(value).find(user => user.id === actor.id); if (me?.mfa_enabled) setMfa({ enabled: true }); }).catch(() => {}); }, [actor.id]);
  async function changePassword(event: React.FormEvent) {
    event.preventDefault(); setError(null);
    if (nextPassword !== confirmPassword) { setError(new Error('兩次輸入的新密碼不一致。')); return; }
    setBusy(true);
    try { const result = await api<any>('/auth/password', { method: 'POST', body: JSON.stringify({ current_password: password, password: nextPassword }) }); setPassword(''); setNextPassword(''); setConfirmPassword(''); toast('密碼已更新'); if (result.sign_in_required) location.href = '/?password_changed=1'; }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function setupMfa() { setBusy(true); setError(null); try { setMfa(await api('/auth/mfa/setup', { method: 'POST', body: '{}' })); setMfaDialog(true); } catch (e) { setError(e); } finally { setBusy(false); } }
  async function confirmMfa(event: React.FormEvent) { event.preventDefault(); setBusy(true); setError(null); try { await api('/auth/mfa/confirm', { method: 'POST', body: JSON.stringify({ token: mfa?.token, code }) }); setMfaDialog(false); setMfa({ enabled: true }); toast('雙重驗證已啟用'); } catch (e) { setError(e); } finally { setBusy(false); } }
  return <div className="account-layout"><section className="panel account-profile"><Avatar name={actor.name || actor.email} /><h2>{actor.name}</h2><p>{actor.email}</p><Badge tone="blue">{actor.role}</Badge><dl className="details-list"><div><dt>帳戶 ID</dt><dd className="mono">{actor.id.slice(0, 12)}</dd></div><div><dt>存取範圍</dt><dd>目前公司工作空間</dd></div></dl></section><div><form className="panel settings-panel" onSubmit={changePassword}><div className="panel-heading"><h2>登入密碼</h2><p>使用至少 12 個字元，為帳戶設定獨立密碼。</p></div><div className="panel-body"><label className="field"><span>目前密碼</span><input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required /></label><label className="field"><span>新密碼</span><input type="password" minLength={12} value={nextPassword} onChange={event => setNextPassword(event.target.value)} autoComplete="new-password" required /></label><label className="field"><span>確認新密碼</span><input type="password" minLength={12} value={confirmPassword} onChange={event => setConfirmPassword(event.target.value)} autoComplete="new-password" required /></label><ErrorNotice error={!mfaDialog ? error : null} /></div><div className="panel-footer"><span /><button className="button primary" disabled={busy}>更新密碼</button></div></form><section className="panel mfa-panel"><div><ShieldCheck size={24} /><h2>雙重驗證</h2><p>以驗證器產生一次性代碼，在密碼之外多一層保護。</p></div>{mfa?.enabled ? <Badge tone="green">已啟用</Badge> : <button className="button" onClick={() => { void setupMfa(); }} disabled={busy}>設定驗證器</button>}</section></div>{mfaDialog && <Modal title="設定雙重驗證" onClose={() => setMfaDialog(false)}><form onSubmit={confirmMfa}><div className="modal-body"><p className="muted">將以下設定加入你的驗證器，再輸入 6 位數字代碼完成設定。</p>{mfa?.qr_code && <img className="mfa-qr" src={mfa.qr_code} alt="驗證器設定 QR code" />}<label className="field"><span>設定金鑰</span><input readOnly value={mfa?.secret ?? mfa?.uri ?? ''} onFocus={event => event.target.select()} /></label>{mfa?.uri && <CopyButton value={mfa.uri} label="複製設定網址" />}<label className="field"><span>驗證代碼</span><input inputMode="numeric" pattern="[0-9]{6}" value={code} onChange={event => setCode(event.target.value)} required autoComplete="one-time-code" /></label><ErrorNotice error={error} /></div><div className="modal-footer"><button className="button" type="button" onClick={() => setMfaDialog(false)}>取消</button><button className="button primary" disabled={busy}>啟用雙重驗證</button></div></form></Modal>}</div>;
}
