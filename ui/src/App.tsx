import { Component, Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Activity as ActivityIcon, Archive, Bell, Blocks, BookOpen, CalendarDays, ChartNoAxesCombined, CheckCheck, ChevronDown, ChevronRight, CircleHelp, FileClock, Files, Folder, Globe2, LayoutDashboard, ListTodo, LoaderCircle, LogOut, Menu, Network, PenLine, Search, Settings, ShieldCheck, Star, StickyNote, Trash2, Users, Workflow, X } from 'lucide-react';
import { api, deleteResource, extractList, listResources, safeExternalUrl, updateResource, type Actor, type Resource } from './api';
import { Avatar, EmptyState, ErrorNotice, Loading, Modal, ResourceIcon } from './components/Primitives';
import { PublicShare } from './components/PublicShare';
import { ContentPage } from './pages/Content';
import { AdminPage } from './pages/Admin';
const BusinessPage = lazy(() => import('./pages/Business').then(module => ({ default: module.BusinessPage })));
const PublicFormPage = lazy(() => import('./pages/Business').then(module => ({ default: module.PublicFormPage })));
const WorkflowPage = lazy(() => import('./pages/Workflow').then(module => ({ default: module.WorkflowPage })));
const EditorPage = lazy(() => import('./pages/Editors').then(module => ({ default: module.EditorPage })));
const EnterprisePage = lazy(() => import('./pages/Enterprise').then(module => ({ default: module.EnterprisePage })));
const OverviewPage = lazy(() => import('./pages/Overview').then(module => ({ default: module.OverviewPage })));

type ToastMessage = { id: number; text: string; tone: 'success' | 'error' };
type Status = { initialized: boolean; company_name: string; version: string; setup_available?: boolean; sso_available?: boolean };
const businessSections = ['tables', 'tasks', 'workdays', 'calendar', 'forms'];
const workflowSections = ['workflows', 'agents', 'connectors', 'runs'];
const editorSections = ['notes', 'canvas', 'hubs'];
const enterpriseSections = ['sign', 'docgen', 'security', 'retention', 'archives', 'reports'];
const adminSections = ['settings', 'users', 'tokens', 'audit', 'jobs', 'account'];

class ErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null as string | null };
  static getDerivedStateFromError(error: Error) { return { error: error.message }; }
  render() {
    if (this.state.error) return <div className="app-error"><div className="brand-mark">b</div><h1>工作台遇到問題</h1><p>{this.state.error}</p><button className="button primary" onClick={() => location.reload()}>重新載入</button></div>;
    return this.props.children;
  }
}

export default function App() {
  const shareMatch = location.pathname.match(/^\/share\/([^/]+)\/?$/);
  const formMatch = location.pathname.match(/^\/(?:forms|form)\/([^/]+)\/?$/);
  if (location.pathname === '/activate') return <ErrorBoundary><ActivationScreen /></ErrorBoundary>;
  if (shareMatch) return <ErrorBoundary><PublicShare token={shareMatch[1]} /></ErrorBoundary>;
  if (formMatch) return <ErrorBoundary><Suspense fallback={<Loading />}><PublicFormPage id={formMatch[1]} /></Suspense></ErrorBoundary>;
  return <ErrorBoundary><Workspace /></ErrorBoundary>;
}

function Workspace() {
  const [status, setStatus] = useState<Status | null>(null);
  const [actor, setActor] = useState<Actor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [route, setRoute] = useState(location.hash.slice(1) || 'overview');
  const [sidebar, setSidebar] = useState(false);
  const [collectionsOpen, setCollectionsOpen] = useState(true);
  const [folderTreeOpen, setFolderTreeOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [collections, setCollections] = useState<Resource[]>([]);
  const [folders, setFolders] = useState<Resource[]>([]);
  const [notifications, setNotifications] = useState<Resource[]>([]);
  const [settings, setSettings] = useState<any>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [helpOpen, setHelpOpen] = useState(false);
  const [collectionDialog, setCollectionDialog] = useState<Resource | null>(null);
  const [collectionName, setCollectionName] = useState('');
  const [collectionError, setCollectionError] = useState<unknown>(null);
  const [collectionBusy, setCollectionBusy] = useState(false);
  const toastId = useRef(0);
  const section = route.split('/')[0];
  const isAdmin = !!actor && ['admin', 'owner'].includes(actor.role);
  const companyName = settings?.company?.name ?? settings?.company?.company_name ?? status?.company_name ?? '藍圖工作室';
  const toast = useCallback((text: string, tone: 'success' | 'error' = 'success') => {
    const id = ++toastId.current; setToasts(old => [...old.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts(old => old.filter(item => item.id !== id)), 5500);
  }, []);
  const init = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const result = await api<Status>('/status'); setStatus(result);
      if (result.initialized) {
        try { const me = await api<any>('/me'); setActor(me.user ?? me); }
        catch (e: any) { if (e.status === 401) setActor(null); else throw e; }
      }
    } catch (e) { setError(e); }
    finally { setLoading(false); }
  }, []);
  const refreshNavigation = useCallback(async () => {
    if (!actor) return;
    const results = await Promise.allSettled([listResources('collection'), listResources('folder'), api('/settings'), listResources('notification')]);
    if (results[0].status === 'fulfilled') setCollections(extractList<Resource>(results[0].value).filter(item => item.data.system !== 'favorites'));
    if (results[1].status === 'fulfilled') setFolders(extractList(results[1].value));
    if (results[2].status === 'fulfilled') setSettings(results[2].value);
    if (results[3].status === 'fulfilled') setNotifications(extractList(results[3].value));
  }, [actor]);
  useEffect(() => { void init(); }, [init]);
  useEffect(() => { void refreshNavigation(); }, [refreshNavigation]);
  useEffect(() => {
    const hash = () => { setRoute(location.hash.slice(1) || 'overview'); setSidebar(false); setProfileOpen(false); };
    window.addEventListener('hashchange', hash); return () => window.removeEventListener('hashchange', hash);
  }, []);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(value => !value); }
      if (event.key === 'Escape') { setSidebar(false); setProfileOpen(false); }
    };
    window.addEventListener('keydown', keyboard); return () => window.removeEventListener('keydown', keyboard);
  }, []);
  useEffect(() => { const changed = () => { void refreshNavigation(); }; window.addEventListener('blueprint:changed', changed); return () => window.removeEventListener('blueprint:changed', changed); }, [refreshNavigation]);
  function navigate(value: string) { location.hash = value; setSidebar(false); }
  function moduleEnabled(key: string) {
    const map: Record<string, string> = { files: 'files', favorites: 'collections', recent: 'files', links: 'files', trash: 'files', collections: 'collections', tables: 'databases', tasks: 'tasks', workdays: 'workdays', calendar: 'calendar', forms: 'forms', workflows: 'workflows', agents: 'integrations', connectors: 'integrations', notes: 'notes', canvas: 'canvas', hubs: 'knowledge', sign: 'sign', docgen: 'documents', security: 'security', retention: 'governance', archives: 'governance', reports: 'governance', runs: 'workflows' };
    return settings?.modules?.[map[key] ?? key] !== false;
  }
  async function logout() {
    try { await api('/logout', { method: 'POST', body: '{}' }); setActor(null); setProfileOpen(false); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); }
  }
  async function markNotificationsRead() {
    try { await Promise.all(notifications.filter(item => !item.data.read).map(item => updateResource(item, { data: { ...item.data, read: true } }))); await refreshNavigation(); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); }
  }
  async function renameCollection(event: React.FormEvent) {
    event.preventDefault(); if (!collectionDialog) return; setCollectionBusy(true); setCollectionError(null);
    try { await updateResource(collectionDialog, { name: collectionName.trim() }); setCollectionDialog(null); await refreshNavigation(); toast('集合名稱已更新'); }
    catch (e) { setCollectionError(e); } finally { setCollectionBusy(false); }
  }
  async function removeCollection() {
    if (!collectionDialog) return; setCollectionBusy(true); setCollectionError(null);
    try { await deleteResource(collectionDialog); setCollectionDialog(null); await refreshNavigation(); toast('集合已移至垃圾桶，原始文件保持不變'); if (route === `collections/${collectionDialog.id}`) navigate('files'); }
    catch (e) { setCollectionError(e); } finally { setCollectionBusy(false); }
  }
  if (loading) return <div className="boot-screen"><span className="brand-mark">b</span><Loading label="正在開啟工作空間…" /></div>;
  if (error && !status) return <div className="boot-screen"><span className="brand-mark">b</span><h2>暫時未能連接工作空間</h2><ErrorNotice error={error} onRetry={() => { void init(); }} /></div>;
  if (!actor) return <AuthScreen status={status} onAuthenticated={async user => { setActor(user); setStatus(old => ({ initialized: true, company_name: old?.company_name ?? '藍圖工作室', version: old?.version ?? '2.0.0' })); }} />;
  const navGroups = [
    [{ key: 'overview', label: '工作總覽', icon: LayoutDashboard }, { key: 'files', label: '文件', icon: Files }, { key: 'favorites', label: '星號收藏', icon: Star }],
    [{ key: 'hubs', label: '知識入口', icon: BookOpen }, { key: 'notes', label: '筆記', icon: StickyNote }, { key: 'canvas', label: '白板', icon: PenLine }],
    [{ key: 'tables', label: '業務資料庫', icon: LayoutDashboard }, { key: 'tasks', label: '待辦事項', icon: ListTodo }, { key: 'calendar', label: '日程', icon: CalendarDays }, { key: 'workdays', label: '每日工作', icon: FileClock }],
    [{ key: 'forms', label: '表單', icon: Blocks }, { key: 'workflows', label: '工作流程', icon: Workflow }, { key: 'sign', label: '電子簽署', icon: PenLine }, { key: 'agents', label: 'AI 員工接入', icon: Network }],
  ];
  let page: ReactNode;
  if (!moduleEnabled(section) && !adminSections.includes(section)) page = <div className="page-content"><EmptyState icon={<Blocks size={36} />} title="這個模組已停用" description="現有資料仍然保留。管理員可以在模組設定中重新啟用。">{isAdmin && <button className="button primary" onClick={() => navigate('settings')}>前往模組設定</button>}</EmptyState></div>;
  else if (section === 'overview') page = <OverviewPage actor={actor} companyName={companyName} moduleEnabled={moduleEnabled} />;
  else if (businessSections.includes(section)) page = <BusinessPage key={section} section={section} />;
  else if (workflowSections.includes(section)) page = <WorkflowPage key={section} section={section} />;
  else if (editorSections.includes(section)) page = <EditorPage key={section} section={section} />;
  else if (enterpriseSections.includes(section)) page = <EnterprisePage key={section} section={section} />;
  else if (adminSections.includes(section)) page = <AdminPage section={section} actor={actor} settings={settings} onSettingsChanged={() => { void refreshNavigation(); }} toast={toast} />;
  else page = <ContentPage key={section} section={section} folderId={section === 'files' ? route.split('/')[1] : undefined} collectionId={section === 'collections' ? route.split('/')[1] : undefined} actor={actor} toast={toast} collectionsEnabled={moduleEnabled('collections')} onCollectionsChanged={() => { void refreshNavigation(); }} />;
  return <div className="workspace-shell">
    {sidebar && <button className="sidebar-scrim" onClick={() => setSidebar(false)} aria-label="關閉導覽" />}
    <aside className={`sidebar ${sidebar ? 'sidebar-open' : ''}`}><a className="workspace-brand" href="#overview"><span className="brand-mark">b<span /></span><span className="brand-wordmark">藍圖<span>WORKSPACE</span></span></a>
      <button className="company-switcher" onClick={() => navigate(isAdmin ? 'settings' : 'account')}><span className="company-avatar">{companyName.slice(0, 1)}</span><span><strong>{companyName}</strong><small>公司工作空間</small></span><Settings size={14} /></button>
      <nav className="main-navigation" aria-label="主要導覽">{navGroups.map((group, index) => <div className="nav-group" key={index}>{group.filter(item => moduleEnabled(item.key)).map(item => { const Icon = item.icon; return <a key={item.key} href={`#${item.key}`} className={`nav-item ${section === item.key || (item.key === 'files' && section === 'recent') ? 'active' : ''}`} aria-current={section === item.key ? 'page' : undefined}><Icon size={18} strokeWidth={1.7} /><span>{item.label}</span>{item.key === 'tasks' && <span className="nav-end-dot" />}</a>; })}
        {index === 0 && <>{folders.length > 0 && <><button className="nav-item nav-expand" onClick={() => setFolderTreeOpen(!folderTreeOpen)} aria-expanded={folderTreeOpen}><Folder size={18} strokeWidth={1.7} /><span>資料夾樹</span><ChevronDown size={13} className={folderTreeOpen ? '' : 'rotate-closed'} /></button>{folderTreeOpen && <div className="folder-navigation">{folders.filter(folder => !folder.parent_id).map(folder => <FolderBranch key={folder.id} folder={folder} folders={folders} activeRoute={route} />)}</div>}</>}<button disabled={!moduleEnabled('collections')} className={`nav-item nav-expand ${section === 'collections' ? 'active' : ''}`} onClick={() => setCollectionsOpen(!collectionsOpen)} aria-expanded={collectionsOpen}><Folder size={18} strokeWidth={1.7} /><span>個人集合</span><ChevronDown size={13} className={collectionsOpen ? '' : 'rotate-closed'} /></button>{moduleEnabled('collections') && collectionsOpen && <div className="collection-navigation">{collections.length ? collections.map(item => <div className="collection-nav-row" key={item.id}><a href={`#collections/${item.id}`} className={route === `collections/${item.id}` ? 'active' : ''}><span className="collection-dot" />{item.name}</a><button className="icon-button" aria-label={`重新命名 ${item.name}`} onClick={() => { setCollectionDialog(item); setCollectionName(item.name); }}><PenLine size={11} /></button></div>) : <span className="nav-empty">在文件中建立你的第一個集合</span>}</div>}</>}
      </div>)}
      <div className="nav-group"><button className="nav-item nav-expand" onClick={() => setMoreOpen(!moreOpen)} aria-expanded={moreOpen}><ChevronRight size={18} className={moreOpen ? 'rotate-open' : ''} /><span>更多工具</span></button>{moreOpen && <div className="more-navigation">{[{ key: 'links', label: '連結收藏', icon: Globe2 }, { key: 'connectors', label: '整合連接器', icon: Network }, { key: 'docgen', label: '文件生成', icon: Files }, { key: 'archives', label: '企業歸檔', icon: Archive }, { key: 'security', label: '內容安全', icon: ShieldCheck }, { key: 'retention', label: '保存政策', icon: FileClock }, { key: 'reports', label: '報告', icon: ChartNoAxesCombined }, { key: 'runs', label: '流程執行', icon: ActivityIcon }, { key: 'trash', label: '垃圾桶', icon: Trash2 }].filter(item => moduleEnabled(item.key)).map(item => { const Icon = item.icon; return <a key={item.key} href={`#${item.key}`} className={`nav-item ${section === item.key ? 'active' : ''}`}><Icon size={17} /><span>{item.label}</span></a>; })}</div>}</div>
      </nav><div className="sidebar-footer">{isAdmin && <a href="#settings" className={`nav-item ${adminSections.includes(section) ? 'active' : ''}`}><Settings size={17} /><span>管理工作空間</span><span className="admin-label">管理員</span></a>}<button className="sidebar-profile" onClick={() => navigate('account')}><Avatar name={actor.name || actor.email} /><span><strong>{actor.name || actor.email}</strong><small>{actor.email}</small></span><ChevronRight size={15} /></button></div>
    </aside>
    <div className="workspace-main"><header className="topbar"><button className="icon-button mobile-menu" onClick={() => setSidebar(true)} aria-label="開啟導覽"><Menu size={21} /></button><button className="global-search" onClick={() => setSearchOpen(true)}><Search size={18} /><span>搜尋你的內容、資料及工作…</span><kbd>⌘ K</kbd></button><div className="topbar-actions"><span className="workspace-status"><span />私人工作空間</span><button className="icon-button topbar-help" onClick={() => setHelpOpen(true)} aria-label="使用說明"><CircleHelp size={19} /></button><button className="icon-button notification-button" onClick={() => { setNotificationOpen(true); void refreshNavigation(); }} aria-label="通知"><Bell size={19} />{notifications.some(item => !item.data.read) && <i />}</button><div className="menu-anchor"><button className="profile-trigger" onClick={() => setProfileOpen(!profileOpen)} aria-label="帳戶選單" aria-expanded={profileOpen}><Avatar name={actor.name || actor.email} small /></button>{profileOpen && <div className="dropdown-menu profile-menu"><div className="profile-menu-details"><strong>{actor.name}</strong><span>{actor.email}</span></div><button onClick={() => navigate('account')}><Users size={15} />我的帳戶</button>{isAdmin && <button onClick={() => navigate('settings')}><Settings size={15} />工作空間設定</button>}<div className="menu-divider" /><button onClick={() => { void logout(); }}><LogOut size={15} />登出</button></div>}</div></div></header>
      <main className="workspace-content" id="main-content"><Suspense fallback={<Loading label="正在載入工作台…" />}>{page}</Suspense></main><footer className="workspace-footer"><span>Blueprint Workspace</span><span>開源 · 自行託管 · 由你掌握</span></footer>
    </div>
    <div className="toast-stack" aria-live="polite">{toasts.map(message => <div key={message.id} className={`toast toast-${message.tone}`} role={message.tone === 'error' ? 'alert' : 'status'}><CheckCheck size={17} /><span>{message.text}</span><button onClick={() => setToasts(old => old.filter(item => item.id !== message.id))} aria-label="關閉通知"><X size={14} /></button></div>)}</div>
    {searchOpen && <GlobalSearch onClose={() => setSearchOpen(false)} folders={folders} />}
    {notificationOpen && <Modal title="通知" subtitle="團隊更新及需要你處理的事項。" onClose={() => setNotificationOpen(false)}><div className="modal-body">{notifications.length ? <><button className="button" onClick={() => { void markNotificationsRead(); }}><CheckCheck size={16} />全部標為已讀</button><div className="notification-list">{notifications.map(item => <article key={item.id} className={item.data.read ? '' : 'unread'}><span className="notification-dot" /><div><strong>{item.name}</strong><p>{item.data.body ?? item.data.message}</p><small>{new Date(item.created_at).toLocaleString('zh-HK')}</small>{item.data.url && <button onClick={() => { navigate(item.data.url.replace(/^#/, '')); setNotificationOpen(false); }}>查看事項</button>}</div></article>)}</div></> : <EmptyState icon={<Bell size={32} />} title="目前沒有新通知" description="有新的留言、任務或工作流程更新時，會在這裡通知你。" />}</div></Modal>}
    {helpOpen && <Modal title="讓工作有條理地向前" subtitle="內容、資料和流程，共用一個工作空間。" onClose={() => setHelpOpen(false)}><div className="modal-body help-content"><div><Files size={21} /><section><h3>整理公司的內容</h3><p>上傳文件、建立資料夾、收藏連結。使用星號及個人集合，快速找回重要資料。</p></section></div><div><LayoutDashboard size={21} /><section><h3>建立適合你的工作台</h3><p>在業務資料庫定義欄位，配合表單、日曆、工作流及知識入口管理日常工作。</p></section></div><div><Network size={21} /><section><h3>連接外部 AI 員工</h3><p>為你自行開發的員工設定接口與權限，查看派工結果及操作紀錄。</p></section></div><div><ShieldCheck size={21} /><section><h3>按需要啟用服務</h3><p>簽署、文件轉檔、掃描及日曆整合使用你設定的服務。管理員可在設定查看連接狀態。</p></section></div><p className="help-shortcut">快速搜尋 <kbd>⌘ / Ctrl</kbd> + <kbd>K</kbd></p></div></Modal>}
    {collectionDialog && <Modal title="重新命名集合" onClose={() => setCollectionDialog(null)}><form onSubmit={renameCollection}><div className="modal-body"><label className="field"><span>集合名稱</span><input value={collectionName} onChange={event => setCollectionName(event.target.value)} required maxLength={255} /></label><ErrorNotice error={collectionError} /></div><div className="modal-footer"><button type="button" className="button danger-text" onClick={() => { void removeCollection(); }} disabled={collectionBusy}>刪除此集合</button><button type="button" className="button" onClick={() => setCollectionDialog(null)}>取消</button><button className="button primary" disabled={collectionBusy}>儲存</button></div></form></Modal>}
  </div>;
}

function FolderBranch({ folder, folders, activeRoute, depth = 0 }: { folder: Resource; folders: Resource[]; activeRoute: string; depth?: number }) {
  const [open, setOpen] = useState(false);
  const children = folders.filter(item => item.parent_id === folder.id && item.id !== folder.id);
  if (depth > 12) return null;
  return <div className="folder-branch"><div className="folder-branch-row"><button className={`folder-toggle ${children.length ? '' : 'invisible'}`} onClick={() => setOpen(!open)} aria-label={`${open ? '收合' : '展開'} ${folder.name}`} aria-expanded={open}><ChevronRight size={11} className={open ? 'rotate-open' : ''} /></button><a className={activeRoute === `files/${folder.id}` ? 'active' : ''} href={`#files/${folder.id}`}><Folder size={12} />{folder.name}</a></div>{open && children.length > 0 && <div className="folder-branch-children">{children.map(item => <FolderBranch key={item.id} folder={item} folders={folders} activeRoute={activeRoute} depth={depth + 1} />)}</div>}</div>;
}

function AuthScreen({ status, onAuthenticated }: { status: Status | null; onAuthenticated: (actor: Actor) => void }) {
  const setup = status ? !status.initialized : false;
  const [name, setName] = useState('');
  const [company, setCompany] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [token, setToken] = useState(new URLSearchParams(location.search).get('setup_token') ?? '');
  const [otp, setOtp] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      if (setup) await api('/setup', { method: 'POST', body: JSON.stringify({ token, company_name: company, name, email, password }) });
      const result = await api<any>('/login', { method: 'POST', body: JSON.stringify({ email, password, ...(otp ? { otp } : {}) }) });
      const me = result.user ?? result.actor ?? await api('/me'); onAuthenticated(me.user ?? me);
      const returnTo = new URLSearchParams(location.search).get('returnTo');
      if (returnTo?.startsWith('/share/')) location.href = returnTo;
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }
  return <div className="auth-page"><div className="auth-visual"><a className="auth-brand" href="/"><span className="brand-mark">b</span><span>藍圖工作室</span></a><div className="auth-visual-content"><span className="auth-eyebrow">YOUR WORK, CONNECTED.</span><h1>公司的每一份成果，<br />都有清晰的位置。</h1><p>讓文件、知識、資料與工作流程，<br />成為團隊共同向前的基礎。</p><div className="auth-illustration" aria-hidden="true"><div className="illustration-line line-one" /><div className="illustration-line line-two" /><div className="illustration-card illustration-file"><Files size={25} /><span>內容與知識</span><i /><i /></div><div className="illustration-card illustration-process"><Workflow size={24} /><span>工作流程</span><div><b /><em /><b /><em /><b /></div></div><div className="illustration-card illustration-data"><ChartNoAxesCombined size={24} /><span>公司資料</span><div><i /><i /><i /><i /><i /></div></div><div className="illustration-center"><span className="brand-mark">b</span></div></div></div><div className="auth-visual-footer"><ShieldCheck size={15} />開源軟件 · 自行託管 · 內容由你掌握</div></div>
    <main className="auth-main"><div className="auth-card"><span className="auth-tag">BLUEPRINT WORKSPACE</span><h2>{setup ? '建立你的公司工作空間' : '歡迎回來'}</h2><p>{setup ? '幾項基本設定，為公司的工作建立新起點。' : `登入 ${status?.company_name ?? '你的工作空間'}，繼續今天的工作。`}</p><form onSubmit={submit}>
      {setup && <><label className="field"><span>公司或工作空間名稱</span><input value={company} onChange={event => setCompany(event.target.value)} placeholder="例如：藍圖工作室" required maxLength={120} /></label><label className="field"><span>你的名稱</span><input value={name} onChange={event => setName(event.target.value)} required autoComplete="name" /></label></>}
      <label className="field"><span>電郵地址</span><input type="email" value={email} onChange={event => setEmail(event.target.value)} required autoComplete="username" placeholder="you@company.com" /></label><label className="field"><span>密碼</span><input type="password" value={password} onChange={event => setPassword(event.target.value)} required minLength={setup ? 12 : undefined} autoComplete={setup ? 'new-password' : 'current-password'} placeholder={setup ? '最少 12 個字元' : '輸入你的密碼'} /></label>
      {setup ? <label className="field"><span>安裝驗證碼</span><input type="password" value={token} onChange={event => setToken(event.target.value)} required autoComplete="off" /><small>使用部署時設定的 BLUEPRINT_SETUP_TOKEN。</small></label> : <details className="auth-otp"><summary>已啟用雙重驗證？</summary><label className="field"><span>驗證器代碼</span><input inputMode="numeric" pattern="[0-9]{6}" value={otp} onChange={event => setOtp(event.target.value)} autoComplete="one-time-code" placeholder="6 位數字" /></label></details>}
      {setup && status?.setup_available === false && <div className="subtle-notice">部署尚未設定安裝驗證碼。請由伺服器管理員設定 BLUEPRINT_SETUP_TOKEN，或使用 init 管理指令。</div>}{!setup && new URLSearchParams(location.search).has('password_changed') && <div className="success-notice">密碼已更新，請使用新密碼登入。</div>}<ErrorNotice error={error} /><button className="button primary auth-submit" disabled={busy || setup && status?.setup_available === false}>{busy ? <LoaderCircle size={17} className="spin" /> : null}{busy ? '正在連接…' : setup ? '建立工作空間' : '登入工作空間'}<ChevronRight size={16} /></button>
    </form>{!setup && status?.sso_available && <a className="button auth-sso" href="/api/v1/auth/sso/start"><ShieldCheck size={16} />使用公司單一登入</a>}<div className="auth-note"><LockKeyholeIcon />{setup ? '帳戶及公司資料儲存在你部署的後台。' : '請向你的公司管理員索取帳戶或存取權限。'}</div></div><footer>Blueprint Workspace · {status?.version ?? '2.0'}</footer></main>
  </div>;
}

function LockKeyholeIcon() { return <ShieldCheck size={15} />; }

function ActivationScreen() {
  const [token] = useState(new URLSearchParams(location.search).get('token') ?? '');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(null);
    if (!token) { setError(new Error('啟用連結缺少驗證碼，請向管理員索取有效連結。')); return; }
    if (password !== confirm) { setError(new Error('兩次輸入的密碼不一致。')); return; }
    setBusy(true);
    try { await api('/activate', { method: 'POST', body: JSON.stringify({ token, password }) }); location.href = '/'; }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <div className="public-page"><a className="auth-brand" href="/"><span className="brand-mark">b</span><span>藍圖工作室</span></a><main className="public-share-card activation-card"><div className="public-share-top"><ShieldCheck size={18} />公司成員邀請</div><h1>開啟你的工作空間</h1><p className="muted">設定登入密碼，完成帳戶啟用。</p><form onSubmit={submit} style={{ textAlign: 'left', marginTop: 25 }}><label className="field"><span>登入密碼</span><input type="password" minLength={12} value={password} onChange={event => setPassword(event.target.value)} required autoComplete="new-password" placeholder="最少 12 個字元" /></label><label className="field"><span>確認密碼</span><input type="password" minLength={12} value={confirm} onChange={event => setConfirm(event.target.value)} required autoComplete="new-password" /></label><ErrorNotice error={error} /><button className="button primary auth-submit" disabled={busy}>{busy ? '啟用中…' : '啟用帳戶'}</button></form></main></div>;
}

function GlobalSearch({ onClose, folders }: { onClose: () => void; folders: Resource[] }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!query.trim()) { setItems([]); setError(null); return; }
    let current = true;
    const timeout = setTimeout(() => {
      setLoading(true); setError(null);
      void api<Resource[]>(`/search?q=${encodeURIComponent(query)}`).then(value => { if (current) setItems(extractList<Resource>(value).filter(item => ['file', 'folder', 'link', 'collection', 'note', 'canvas', 'hub', 'table', 'record', 'event', 'task', 'workday', 'form', 'workflow', 'run', 'workflow_run', 'sign_request'].includes(item.kind))); }).catch(e => { if (current) setError(e); }).finally(() => { if (current) setLoading(false); });
    }, 240);
    return () => { current = false; clearTimeout(timeout); };
  }, [query]);
  function open(item: Resource) {
    const map: Record<string, string> = { file: `files/${item.parent_id ?? ''}`, folder: `files/${item.id}`, collection: `collections/${item.id}`, note: 'notes', canvas: 'canvas', hub: 'hubs', table: 'tables', record: 'tables', event: 'calendar', task: 'tasks', workday: 'workdays', form: 'forms', workflow: 'workflows', agent: 'agents', connector: 'connectors', run: 'runs', workflow_run: 'runs', sign_request: 'sign' };
    if (item.kind === 'link') { const url = safeExternalUrl(item.data.url); if (url) { window.open(url, '_blank', 'noopener,noreferrer'); onClose(); } else setError(new Error('連結必須是有效的 HTTP 或 HTTPS 網址。')); return; }
    location.hash = map[item.kind] ?? 'files';
    if (!['file', 'folder', 'link', 'collection'].includes(item.kind)) { sessionStorage.setItem('blueprint-open-resource', item.id); window.dispatchEvent(new CustomEvent('blueprint:open-resource', { detail: { id: item.id, kind: item.kind } })); }
    if (item.kind === 'file') { sessionStorage.setItem('blueprint-open-file', item.id); window.dispatchEvent(new CustomEvent('blueprint:open-file', { detail: item.id })); }
    onClose();
  }
  return <Modal title="搜尋工作空間" onClose={onClose} wide><div className="search-modal-input"><Search size={20} /><input autoFocus value={query} onChange={event => setQuery(event.target.value)} placeholder="搜尋文件、筆記、資料、任務…" aria-label="全域搜尋" /><kbd>ESC</kbd></div><div className="search-modal-results"><ErrorNotice error={error} />{loading ? <Loading label="正在搜尋…" /> : !query.trim() ? <div className="search-start"><Search size={29} /><p>輸入關鍵字，連結你的內容與工作。</p><span>結果只包含你有權限存取的內容。</span></div> : items.length ? <><div className="search-result-caption">找到 {items.length} 項內容</div>{items.map(item => <button className="search-result" key={item.id} onClick={() => open(item)}><ResourceIcon resource={item} /><div><strong>{item.name}</strong><span>{folders.find(folder => folder.id === item.parent_id)?.name ?? ({ task: '待辦事項', note: '筆記', table: '資料庫', workday: '每日工作', event: '日程' }[item.kind] ?? '工作空間')}</span></div><ChevronRight size={15} /></button>)}</> : <EmptyState title="沒有符合的搜尋結果" description="試試較短的關鍵字，或確認你已獲得內容的存取權限。" />}</div></Modal>;
}
