import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowRight, ArrowUpRight, Bot, CalendarDays, Check, CheckCheck, FileText, Files, GitBranch, LayoutGrid, ListTodo, LoaderCircle, Network, RefreshCw, SlidersHorizontal, StickyNote } from 'lucide-react';
import { RRule } from 'rrule';
import { extractList, formatBytes, formatDate, listResources, type Actor, type Resource } from '../api';
import { ResourceIcon } from '../components/Primitives';
import { WorkspaceGrid, type WorkspaceWidget } from '../components/WorkspaceGrid';
import '../overview.css';

type SourceId = 'file' | 'task' | 'agent' | 'event' | 'workflow' | 'note';
type Source = { items: Resource[]; loading: boolean; error: string | null };
const sources: { id: SourceId; module: string }[] = [
  { id: 'file', module: 'files' }, { id: 'task', module: 'tasks' },
  { id: 'agent', module: 'agents' }, { id: 'event', module: 'calendar' },
  { id: 'workflow', module: 'workflows' }, { id: 'note', module: 'notes' },
];

function ModuleLink({ href, children = '查看全部' }: { href: string; children?: ReactNode }) {
  return <a className="overview-module-link" href={`#${href}`}>{children}<ArrowUpRight size={14} /></a>;
}

function CardState({ source, children, empty, icon, detail, href, onRetry }: {
  source: Source; children: ReactNode; empty: string; icon: ReactNode; detail: string; href: string; onRetry: () => void;
}) {
  if (source.loading) return <div className="overview-card-state" role="status"><LoaderCircle className="spin" size={22} /><p>正在載入…</p></div>;
  if (source.error) return <div className="overview-card-state overview-card-error" role="status"><span className="overview-empty-icon">{icon}</span><h3>暫時未能載入</h3><p>{source.error}</p><button className="overview-inline-button" onClick={onRetry}><RefreshCw size={13} />重新載入</button></div>;
  if (!source.items.length) return <div className="overview-card-state"><span className="overview-empty-icon">{icon}</span><h3>{empty}</h3><p>{detail}</p><ModuleLink href={href}>前往設定</ModuleLink></div>;
  return children;
}

function openResource(item: Resource, route: string) {
  if (item.kind === 'file') sessionStorage.setItem('blueprint-open-file', item.id);
  else sessionStorage.setItem('blueprint-open-resource', item.id);
  location.hash = route;
}

function nextEvent(item: Resource, now: Date): Date | null {
  const start = new Date(item.data.start);
  if (!Number.isFinite(start.getTime())) return null;
  if (start >= now) return start;
  if (!item.data.rrule) return null;
  try {
    const zone = item.data.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(start).map(part => [part.type, part.value]));
    const dtstart = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second)));
    return new RRule({ ...RRule.parseString(String(item.data.rrule).replace(/^RRULE:/, '')), dtstart, tzid: zone }).after(now, true);
  } catch { return null; }
}

export function OverviewPage({ actor, companyName, moduleEnabled }: { actor: Actor; companyName: string; moduleEnabled: (key: string) => boolean }) {
  const [editing, setEditing] = useState(false);
  const [data, setData] = useState<Partial<Record<SourceId, Source>>>({});
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const generation = useRef(0);
  const enabledKey = sources.filter(source => moduleEnabled(source.module)).map(source => source.id).join(',');
  const load = useCallback(async () => {
    const current = ++generation.current;
    const ids = enabledKey.split(',').filter(Boolean) as SourceId[];
    setData(old => Object.fromEntries(ids.map(id => [id, { items: old[id]?.items ?? [], loading: true, error: null }])));
    const results = await Promise.allSettled(ids.map(id => listResources(id)));
    if (generation.current !== current) return;
    setData(Object.fromEntries(ids.map((id, index) => {
      const result = results[index];
      return [id, result.status === 'fulfilled'
        ? { items: extractList<Resource>(result.value), loading: false, error: null }
        : { items: [], loading: false, error: result.reason instanceof Error ? result.reason.message : '連線暫時中斷，請再試一次。' }];
    })));
    setUpdatedAt(new Date());
  }, [enabledKey, actor.id]);
  useEffect(() => { void load(); return () => { generation.current++; }; }, [load]);
  useEffect(() => { const refresh = () => { void load(); }; window.addEventListener('blueprint:changed', refresh); return () => window.removeEventListener('blueprint:changed', refresh); }, [load]);
  const state = (id: SourceId): Source => data[id] ?? { items: [], loading: true, error: null };
  const files = [...state('file').items].filter(item => !item.data.archived).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const tasks = state('task').items.filter(item => item.data.status !== 'completed').sort((a, b) => String(a.data.due_at ?? '9999').localeCompare(String(b.data.due_at ?? '9999')));
  const completed = state('task').items.filter(item => item.data.status === 'completed').length;
  const notes = [...state('note').items].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const events = useMemo(() => {
    const now = updatedAt ?? new Date();
    return (data.event?.items ?? []).flatMap(item => { const start = nextEvent(item, now); return start ? [{ item, start }] : []; }).sort((a, b) => a.start.getTime() - b.start.getTime());
  }, [data.event, updatedAt]);
  const busy = Object.values(data).some(source => source?.loading);
  const now = new Date();
  const date = now.toLocaleDateString('zh-HK', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' });
  const widgets: WorkspaceWidget[] = [];
  if (moduleEnabled('files')) widgets.push({
    id: 'files', title: '最近的文件', eyebrow: 'CONTENT', icon: <Files size={17} />, action: <ModuleLink href="files" />, defaultWidth: 8, defaultHeight: 6,
    children: <CardState source={{ ...state('file'), items: files }} icon={<Files size={23} />} empty="為你的工作放入第一份內容" detail="文件、提案與靈感，都可以由這裡開始。" href="files" onRetry={() => { void load(); }}>
      <div className="overview-file-panel"><div className="overview-file-head" aria-hidden="true"><span>名稱</span><span>檔案大小</span><span>最後更新</span></div>
      <div className="overview-file-list">{files.slice(0, 6).map(item => <button className="overview-file-row" key={item.id} onClick={() => openResource(item, `files/${item.parent_id ?? ''}`)}><span className="overview-file-name"><ResourceIcon resource={item} size={28} /><span><strong>{item.name}</strong><small>{item.data.mime ?? item.name.split('.').pop()?.toUpperCase() ?? '文件'}</small></span></span><span className="overview-file-size">{formatBytes(Number(item.data.size ?? item.data.size_bytes ?? 0))}</span><span className="overview-file-date">{formatDate(item.updated_at, true)}</span></button>)}</div>
      <div className="overview-card-foot"><span>依最近更新排列</span><ModuleLink href="files">開啟文件庫</ModuleLink></div></div>
    </CardState>,
  });
  if (moduleEnabled('tasks')) widgets.push({
    id: 'tasks', title: '待辦事項', eyebrow: 'YOUR NEXT STEP', icon: <ListTodo size={17} />, action: <ModuleLink href="tasks" />, defaultWidth: 4, defaultHeight: 6,
    children: <CardState source={{ ...state('task'), items: tasks }} icon={<CheckCheck size={23} />} empty={state('task').items.length ? '目前的任務都已完成' : '下一步，從一項小事開始'} detail={state('task').items.length ? '到待辦事項查看已完成的工作，或安排下一項。' : '建立任務、設定期限，讓工作穩步向前。'} href="tasks" onRetry={() => { void load(); }}>
      <div className="overview-task-summary"><strong>{tasks.length}<span>待完成</span></strong><span><Check size={13} />{completed} 項已完成</span></div>
      <div className="overview-task-list">{tasks.slice(0, 5).map(item => <button className="overview-task-row" key={item.id} onClick={() => openResource(item, 'tasks')}><span className={`overview-task-marker ${item.data.status === 'in_progress' ? 'is-progress' : ''}`} /><span><strong>{item.name}</strong><small>{item.data.due_at ? `期限 ${formatDate(item.data.due_at)}` : '未設定期限'}</small></span><ArrowUpRight size={14} /></button>)}</div>
    </CardState>,
  });
  if (moduleEnabled('agents')) widgets.push({
    id: 'agents', title: 'AI 員工', eyebrow: 'YOUR CONNECTED TEAM', icon: <Bot size={17} />, action: <ModuleLink href="agents">管理</ModuleLink>, defaultWidth: 4, defaultHeight: 5,
    children: <CardState source={state('agent')} icon={<Network size={23} />} empty="讓你的 AI 團隊接入工作" detail="連接員工接口，為每位員工設定清晰的能力與權限。" href="agents" onRetry={() => { void load(); }}>
      <div className="overview-agent-summary"><span className="overview-large-icon"><Bot size={25} /></span><div><strong>{state('agent').items.length}<span>個已登記接口</span></strong><p>接入資料與權限集中管理</p></div></div>
      <div className="overview-compact-list">{state('agent').items.slice(0, 3).map(item => <button key={item.id} className="overview-compact-row" onClick={() => openResource(item, 'agents')}><span className="overview-identity-icon">{item.name.slice(0, 1)}</span><strong>{item.name}</strong><ArrowUpRight size={13} /></button>)}</div>
      <p className="overview-small-note">接口登記不代表服務目前在線。</p>
    </CardState>,
  });
  if (moduleEnabled('calendar')) widgets.push({
    id: 'calendar', title: '即將開始', eyebrow: 'ON THE CALENDAR', icon: <CalendarDays size={17} />, action: <ModuleLink href="calendar" />, defaultWidth: 4, defaultHeight: 5,
    children: <CardState source={{ ...state('event'), items: events.map(event => event.item) }} icon={<CalendarDays size={23} />} empty="為下一個重要時刻留個位置" detail="接下來的會議、交付與日程，會排列在這裡。" href="calendar" onRetry={() => { void load(); }}>
      <div className="overview-agenda">{events.slice(0, 4).map(({ item, start }) => <button className="overview-agenda-row" key={item.id} onClick={() => openResource(item, 'calendar')}><span className="overview-date-tile"><small>{start.toLocaleDateString('zh-HK', { month: 'short' })}</small><strong>{start.getDate()}</strong></span><span><strong>{item.name}</strong><small>{item.data.all_day ? '全日' : start.toLocaleTimeString('zh-HK', { hour: '2-digit', minute: '2-digit' })}{item.data.location ? ` · ${item.data.location}` : ''}</small></span></button>)}</div>
    </CardState>,
  });
  if (moduleEnabled('workflows')) widgets.push({
    id: 'workflows', title: '工作流程', eyebrow: 'MAKE WORK FLOW', icon: <GitBranch size={17} />, action: <ModuleLink href="workflows" />, defaultWidth: 4, defaultHeight: 5,
    children: <CardState source={state('workflow')} icon={<GitBranch size={23} />} empty="把重複的工作交給流程" detail="連接步驟、設定審批，讓每次執行都有跡可循。" href="workflows" onRetry={() => { void load(); }}>
      <div className="overview-workflow-list">{state('workflow').items.slice(0, 4).map(item => <button className="overview-workflow-row" key={item.id} onClick={() => openResource(item, 'workflows')}><span className="overview-flow-icon"><GitBranch size={17} /></span><span><strong>{item.name}</strong><small>{Array.isArray(item.data.nodes) ? `${item.data.nodes.length} 個步驟` : '工作流程'} · {item.data.active || item.data.status === 'active' ? '已啟用' : '草稿'}</small></span><ArrowUpRight size={13} /></button>)}</div>
      <div className="overview-card-foot"><ModuleLink href="runs">查看執行記錄</ModuleLink></div>
    </CardState>,
  });
  if (moduleEnabled('notes')) widgets.push({
    id: 'notes', title: '知識與筆記', eyebrow: 'ROOM FOR IDEAS', icon: <StickyNote size={17} />, action: <ModuleLink href="notes" />, defaultWidth: 12, defaultHeight: 4,
    children: <CardState source={state('note')} icon={<StickyNote size={23} />} empty="讓想法有一個可以繼續的地方" detail="整理決策、工作筆記與團隊共識，隨時回來接續。" href="notes" onRetry={() => { void load(); }}>
      <div className="overview-notes-list">{notes.slice(0, 4).map(item => <button className="overview-note-row" key={item.id} onClick={() => openResource(item, 'notes')}><span className="overview-note-icon"><FileText size={21} /></span><strong>{item.name}</strong><small>{formatDate(item.updated_at)} 更新</small><ArrowUpRight size={14} /></button>)}</div>
    </CardState>,
  });
  return <div className="overview-page">
    <div className="overview-breadcrumb"><span>{companyName}</span><span>/</span><span>工作總覽</span></div>
    <header className="overview-header"><div><div className="overview-eyebrow"><span />YOUR WORK, IN PLACE.</div><h1>工作總覽<span className="overview-heading-dot" aria-hidden="true">.</span></h1><p>{actor.name || '你好'}，讓今天的工作，各就其位。</p></div><div className="overview-header-side"><time dateTime={now.toISOString().slice(0, 10)}>{date}</time><div className="overview-actions"><button className="overview-refresh" onClick={() => { void load(); }} disabled={busy} aria-label="重新整理工作總覽"><RefreshCw size={15} className={busy ? 'spin' : ''} /></button><button className={`button overview-edit-button ${editing ? 'primary' : ''}`} onClick={() => setEditing(value => !value)}>{editing ? <Check size={15} /> : <SlidersHorizontal size={15} />}{editing ? '完成編輯' : '編輯版面'}</button></div></div></header>
    <div className="overview-canvas-bar"><div><LayoutGrid size={14} /><span>我的工作台</span><span className="overview-canvas-divider" /><small>{editing ? '調整格位，讓版面配合你的工作方式。' : '把重要的事，放在眼前。'}</small></div><span className="overview-canvas-status">{busy ? '正在更新' : updatedAt ? `更新於 ${updatedAt.toLocaleTimeString('zh-HK', { hour: '2-digit', minute: '2-digit' })}` : '準備載入'}<span /></span></div>
    {widgets.length ? <WorkspaceGrid storageKey={`blueprint-v2-workspace-layout-${actor.id}`} widgets={widgets} editing={editing} onEditingChange={setEditing} /> : <div className="overview-no-modules"><LayoutGrid size={28} /><h2>為你的工作台啟用工具</h2><p>目前的模組已停用。啟用需要的模組後，工作卡片會顯示在這裡。</p>{['owner', 'admin'].includes(actor.role) && <ModuleLink href="settings">管理模組</ModuleLink>}</div>}
    <div className="overview-bottom-note"><span><span className="overview-note-square" />留白，給下一個好想法。</span><a href="#files">回到工作<ArrowRight size={13} /></a></div>
  </div>;
}
