import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowUpDown, Check, ChevronDown, ChevronRight, Clock3, Download, Ellipsis, ExternalLink, FilePlus2, FileText, FolderInput, FolderPlus, Grid2X2, History, Info, Link2, List, LockKeyhole, MessageSquare, Plus, RefreshCw, Search, Share2, ShieldCheck, Star, Trash2, Upload, X } from 'lucide-react';
import { api, apiUrl, ApiError, canEditResource, canManageResource, createResource, deleteResource, downloadFile, extractList, formatBytes, formatDate, listResources, safeExternalUrl, updateResource, type Actor, type Resource } from '../api';
import { Avatar, Badge, CopyButton, Crumbs, EmptyState, ErrorNotice, FloatingMenu, Loading, Modal, ResourceIcon } from '../components/Primitives';

type Toast = (message: string, tone?: 'success' | 'error') => void;
type UploadState = { name: string; progress: number; error?: string };
type Dialog = { type: string; resource?: Resource } | null;

function isFolder(resource: Resource) { return resource.kind === 'folder'; }
function fileSize(resource: Resource) { return resource.data?.size ?? resource.data?.size_bytes ?? 0; }
function officeFile(resource: Resource) { return resource.kind === 'file' && ['docx', 'xlsx', 'pptx', 'odt', 'ods', 'odp'].includes(resource.name.split('.').pop()?.toLowerCase() ?? ''); }
async function openOffice(resource: Resource, actor: Actor, toast: Toast) {
  const target = `blueprint-office-${Date.now()}`;
  const popup = window.open('', target);
  if (!popup) { toast('請允許此工作空間開啟新視窗，再重試。', 'error'); return; }
  popup.opener = null; popup.document.title = '正在開啟 Office'; popup.document.body.textContent = '正在連接你的 Office 協作服務…';
  try {
    const result = await api<any>(`/office/${resource.id}/open`, { method: 'POST', body: JSON.stringify({ mode: canEditResource(resource, actor) ? 'edit' : 'view' }) });
    const action = new URL(result.action_url, location.origin);
    if (!['http:', 'https:'].includes(action.protocol)) throw new Error('Office 服務回傳無效的開啟網址。');
    const form = document.createElement('form'); form.method = 'POST'; form.action = action.href; form.target = target;
    for (const [name, value] of Object.entries(result.fields ?? {})) { const field = document.createElement('input'); field.type = 'hidden'; field.name = name; field.value = String(value); form.appendChild(field); }
    document.body.appendChild(form); form.submit(); form.remove();
  } catch (e) { popup.close(); toast(e instanceof Error ? e.message : String(e), 'error'); }
}
function fileState(resource: Resource) {
  if (resource.data?.quarantined || resource.data?.scan_status === 'infected' || resource.data?.scan?.verdict === 'infected' || resource.data?.scan?.status === 'infected') return { label: '已隔離', tone: 'red' };
  if (resource.data?.archived) return { label: '已歸檔', tone: '' };
  if (resource.data?.legal_hold) return { label: '法務保全', tone: 'purple' };
  if (resource.data?.locked || resource.data?.lock?.locked) return { label: '已鎖定', tone: 'yellow' };
  if (resource.data?.scan_status === 'pending' || resource.data?.scan_status === 'queued' || resource.data?.scan?.status === 'pending') return { label: '掃描中', tone: 'yellow' };
  return null;
}

export function ContentPage({ section, folderId, collectionId, actor, toast, onCollectionsChanged, collectionsEnabled = true }: {
  section: string; folderId?: string; collectionId?: string; actor: Actor; toast: Toast; onCollectionsChanged: () => void; collectionsEnabled?: boolean;
}) {
  const [items, setItems] = useState<Resource[]>([]);
  const [folders, setFolders] = useState<Resource[]>([]);
  const [collections, setCollections] = useState<Resource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');
  const [kindFilter, setKindFilter] = useState('all');
  const [sort, setSort] = useState('updated');
  const [view, setView] = useState(() => localStorage.getItem('blueprint-v2-content-view') ?? 'list');
  const [dialog, setDialog] = useState<Dialog>(null);
  const [preview, setPreview] = useState<Resource | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const [menuAnchor, setMenuAnchor] = useState<HTMLElement | null>(null);
  const [newMenu, setNewMenu] = useState(false);
  const [uploads, setUploads] = useState<UploadState[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const lastIndex = useRef<number>(-1);
  const isTrash = section === 'trash';
  const isAdmin = ['owner', 'admin'].includes(actor.role);

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true); setError(null);
    try {
      const [files, dirs, links, groups] = await Promise.all([
        isTrash ? api<Resource[]>('/trash') : listResources('file'),
        listResources('folder'), listResources('link'), collectionsEnabled ? listResources('collection').catch(e => { if (e instanceof ApiError && e.status === 403) return []; throw e; }) : Promise.resolve([]),
      ]);
      setFolders(extractList(dirs)); setCollections(extractList(groups));
      setItems(isTrash ? extractList(files) : [...extractList<Resource>(dirs), ...extractList<Resource>(files), ...extractList<Resource>(links)]);
    } catch (e) { setError(e); }
    finally { if (!silent) setLoading(false); }
  }, [isTrash, collectionsEnabled]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!items.some(item => ['pending', 'queued', 'running'].includes(item.data.scan?.status) || ['queued', 'running'].includes(item.data.processing?.status))) return;
    const timer = setInterval(() => { void load(true); }, 5000); return () => clearInterval(timer);
  }, [items, load]);
  useEffect(() => {
    let active = true;
    const openById = async (id: string) => { try { const resource = await api<Resource>(`/resource/${id}`); if (active) { setPreview(resource); sessionStorage.removeItem('blueprint-open-file'); } } catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); } };
    const listener = (event: Event) => { void openById((event as CustomEvent<string>).detail); };
    window.addEventListener('blueprint:open-file', listener);
    const pending = sessionStorage.getItem('blueprint-open-file'); if (pending) void openById(pending);
    return () => { active = false; window.removeEventListener('blueprint:open-file', listener); };
  }, [toast, section, folderId, collectionId]);
  useEffect(() => { setSelected(new Set()); setQuery(''); setPreview(null); }, [section, folderId, collectionId]);
  useEffect(() => { localStorage.setItem('blueprint-v2-content-view', view); }, [view]);
  useEffect(() => { const close = () => { setMenu(null); setNewMenu(false); }; document.addEventListener('click', close); return () => document.removeEventListener('click', close); }, []);
  const collection = collections.find(item => item.id === collectionId);
  const favorites = collections.find(item => item.data.system === 'favorites' && item.owner_id === actor.id);
  const favoriteIds: string[] = favorites?.data.item_ids ?? [];
  const currentFolder = folders.find(item => item.id === folderId);
  const mayCreate = actor.role !== 'viewer' && (!currentFolder || canEditResource(currentFolder, actor));
  const rows = useMemo(() => {
    const ids: string[] = collection?.data.item_ids ?? [];
    return items.filter(item => {
      if (isTrash) return true;
      if (collectionId) return ids.includes(item.id);
      if (section === 'favorites') return favoriteIds.includes(item.id);
      if (section === 'links') return item.kind === 'link';
      if (section === 'recent') return true;
      return (item.parent_id ?? null) === (folderId ?? null) && !item.data.archived;
    }).filter(item => (kindFilter === 'all' || item.kind === kindFilter) && `${item.name} ${item.data.description ?? ''} ${(item.data.tags ?? []).join(' ')}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())).sort((a, b) => {
      if (sort === 'name') return a.name.localeCompare(b.name, 'zh-HK');
      if (sort === 'size') return fileSize(b) - fileSize(a);
      if (isFolder(a) !== isFolder(b) && section !== 'recent') return isFolder(a) ? -1 : 1;
      return b.updated_at.localeCompare(a.updated_at);
    });
  }, [items, collection, collectionId, section, folderId, query, kindFilter, sort, isTrash, favorites]);
  const chosen = items.filter(item => selected.has(item.id));
  const recent = items.filter(item => !isFolder(item) && !item.data.archived).sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 4);
  const crumbs = useMemo(() => {
    const result: Resource[] = []; let current = currentFolder; const seen = new Set<string>();
    while (current && !seen.has(current.id)) { seen.add(current.id); result.unshift(current); current = folders.find(item => item.id === current?.parent_id); }
    return result;
  }, [currentFolder, folders]);
  const title = collection?.name ?? currentFolder?.name ?? ({ favorites: '星號收藏', recent: '最近更新', links: '資料收藏', trash: '垃圾桶' }[section] ?? '文件');

  function toggle(resource: Resource, index: number, shift = false) {
    setSelected(old => {
      const next = new Set(old);
      if (shift && lastIndex.current >= 0) for (let i = Math.min(index, lastIndex.current); i <= Math.max(index, lastIndex.current); i++) next.add(rows[i].id);
      else if (next.has(resource.id)) next.delete(resource.id); else next.add(resource.id);
      return next;
    });
    lastIndex.current = index;
  }
  function open(item: Resource) {
    if (isFolder(item)) location.hash = `files/${item.id}`;
    else if (item.kind === 'link') { const url = safeExternalUrl(item.data.url); if (url) window.open(url, '_blank', 'noopener,noreferrer'); else toast('連結必須是有效的 HTTP 或 HTTPS 網址。', 'error'); }
    else setPreview(item);
  }
  useEffect(() => { if (dialog) setMenu(null); }, [dialog]);
  async function afterMutation(message?: string) { setDialog(null); setMenu(null); setSelected(new Set()); await load(); if (preview) { try { setPreview(await api<Resource>(`/resource/${preview.id}`)); } catch { setPreview(null); } } onCollectionsChanged(); if (message) toast(message); }
  async function pin(item: Resource) {
    try {
      const remove = favoriteIds.includes(item.id);
      const item_ids = remove ? favoriteIds.filter(id => id !== item.id) : [...favoriteIds, item.id];
      const updated = favorites ? await updateResource(favorites, { data: { ...favorites.data, item_ids } }) : await createResource({ kind: 'collection', name: '星號收藏', visibility: 'private', data: { system: 'favorites', item_ids } });
      setCollections(old => [...old.filter(value => value.id !== updated.id), updated]); onCollectionsChanged(); toast(remove ? '已移除星號' : '已加入星號收藏');
    }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); }
  }
  async function upload(fileList: FileList | File[], versionFor?: Resource) {
    const files = Array.from(fileList); if (!files.length) return;
    if (uploading) { toast('目前有文件正在上傳，請等待完成後再加入。', 'error'); return; }
    setUploading(true);
    setUploads(files.map(file => ({ name: file.name, progress: 0 })));
    const pendingKey = `blueprint-v2-pending-uploads-${actor.id}`;
    function readPending(): Record<string, any> { try { return JSON.parse(localStorage.getItem(pendingKey) ?? '{}'); } catch { return {}; } }
    function persist(signature: string, value?: any) { const pending = readPending(); if (value) pending[signature] = value; else delete pending[signature]; localStorage.setItem(pendingKey, JSON.stringify(pending)); }
    function progress(index: number, value: number, error?: string) { setUploads(old => old.map((item, i) => i === index ? { ...item, progress: value, ...(error ? { error } : {}) } : item)); }
    for (let index = 0; index < files.length; index++) {
      const file = files[index];
      try {
        if (versionFor) {
          const form = new FormData(); form.append('file', file); form.append('expected_version', String(versionFor.version));
          await api(`/files/${versionFor.id}/versions`, { method: 'POST', body: form }); progress(index, 100); continue;
        }
        const signature = `${file.name}:${file.size}:${file.lastModified}:${folderId ?? ''}`;
        const pending = readPending()[signature]; let session: any;
        if (pending?.id) {
          try { session = await api(`/uploads/${pending.id}`); }
          catch (e: any) { if ([404, 410, 409].includes(e.status)) persist(signature); else throw e; }
        }
        if (!session || ['failed', 'aborted'].includes(session.status)) {
          session = await api('/uploads', { method: 'POST', body: JSON.stringify({ name: file.name, mime: file.type || 'application/octet-stream', size: file.size, parent_id: folderId ?? null, visibility: 'company' }) });
          persist(signature, { id: session.id, name: file.name, size: file.size, lastModified: file.lastModified });
        }
        let offset = Number(session.offset ?? 0);
        const chunkBytes = Math.min(Number(session.max_chunk_bytes ?? 8 * 1024 * 1024), 8 * 1024 * 1024);
        let conflicts = 0;
        progress(index, file.size ? Math.round(offset / file.size * 98) : 98);
        while (offset < file.size) {
          const chunk = file.slice(offset, Math.min(offset + chunkBytes, file.size));
          const chunkStart = offset;
          try {
            const result = await new Promise<any>((resolve, reject) => {
              const xhr = new XMLHttpRequest(); xhr.open('PATCH', apiUrl(`/uploads/${session.id}`)); xhr.withCredentials = true;
              xhr.setRequestHeader('X-Blueprint-Request', '1'); xhr.setRequestHeader('Content-Type', 'application/octet-stream'); xhr.setRequestHeader('Upload-Offset', String(chunkStart));
              xhr.upload.onprogress = event => { if (event.lengthComputable) progress(index, Math.round((chunkStart + event.loaded) / file.size * 98)); };
              xhr.onload = () => { let value: any = {}; try { value = JSON.parse(xhr.responseText); } catch { /* body is optional */ } if (xhr.status >= 200 && xhr.status < 300) resolve(value); else reject(Object.assign(new Error(value.error?.message ?? `上傳失敗（${xhr.status}）`), { status: xhr.status })); };
              xhr.onerror = () => reject(new Error('網絡連線中斷。重新選取相同文件即可從已保存的位置繼續。'));
              xhr.send(chunk);
            });
            offset = Number(result.offset ?? chunkStart + chunk.size); conflicts = 0;
          } catch (e: any) {
            if (e.status === 409 && conflicts++ < 3) { const latest = await api<any>(`/uploads/${session.id}`); offset = Number(latest.offset ?? 0); }
            else throw e;
          }
        }
        await api(`/uploads/${session.id}/complete`, { method: 'POST', body: '{}' }); persist(signature); progress(index, 100);
      } catch (e) { progress(index, 0, e instanceof Error ? e.message : String(e)); }
    }
    await load();
    if (versionFor) { try { setPreview(await api<Resource>(`/resource/${versionFor.id}`)); } catch { /* load will show changes */ } }
    setUploading(false);
  }
  async function removeFromCollection(item: Resource) {
    if (!collection) return;
    try { await updateResource(collection, { data: { ...collection.data, item_ids: (collection.data.item_ids ?? []).filter((id: string) => id !== item.id) } }); await afterMutation('已從集合移除，原件保持不變'); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), 'error'); }
  }
  function rowMenu(item: Resource) {
    if (!menuAnchor) return null;
    return <FloatingMenu anchor={menuAnchor} onClose={() => setMenu(null)}>
      {!isTrash && <button onClick={() => open(item)}><Info size={15} />{isFolder(item) ? '開啟資料夾' : '開啟預覽'}</button>}
      {!isTrash && <>{officeFile(item) && <button onClick={() => { void openOffice(item, actor, toast); setMenu(null); }}><ExternalLink size={15} />以 Office {canEditResource(item, actor) ? '編輯' : '開啟'}</button>}{canManageResource(item, actor) && <button onClick={() => setDialog({ type: 'share', resource: item })}><Share2 size={15} />分享</button>}{canEditResource(item, actor) && <><button onClick={() => setDialog({ type: 'rename', resource: item })}><FileText size={15} />重新命名</button><button onClick={() => setDialog({ type: 'move', resource: item })}><FolderInput size={15} />移動或複製</button></>}<button disabled={!collectionsEnabled} onClick={() => setDialog({ type: 'collection', resource: item })}><Star size={15} />加入集合</button><button disabled={!collectionsEnabled} onClick={() => { void pin(item); setMenu(null); }}><Star size={15} />{favoriteIds.includes(item.id) ? '移除星號' : '加入星號'}</button>
        {item.kind === 'file' && <button onClick={() => { void downloadFile(`/files/${item.id}/download`, item.name).catch(e => toast(e.message, 'error')); setMenu(null); }}><Download size={15} />下載</button>}
        {collection && <button onClick={() => { void removeFromCollection(item); }}><X size={15} />從此集合移除</button>}
        {isAdmin && item.kind === 'file' && <button onClick={() => setDialog({ type: 'governance', resource: item })}><ShieldCheck size={15} />保存及安全政策</button>}
        {canEditResource(item, actor) && <><div className="menu-divider" /><button className="danger-text" onClick={() => setDialog({ type: 'delete', resource: item })}><Trash2 size={15} />移至垃圾桶</button></>}</>}
      {isTrash && <><button onClick={() => { void api(`/resource/${item.id}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: item.version }) }).then(() => afterMutation('已還原資料')).catch(e => toast(e.message, 'error')); }}><History size={15} />還原</button>{isAdmin && <button className="danger-text" onClick={() => setDialog({ type: 'purge', resource: item })}><Trash2 size={15} />永久刪除</button>}</>}
    </FloatingMenu>;
  }
  return <div className={`content-page ${dragging ? 'is-dragging' : ''}`} onDragOver={event => { if (!isTrash && mayCreate && !uploading) { event.preventDefault(); setDragging(true); } }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setDragging(false); }} onDrop={event => { event.preventDefault(); setDragging(false); if (!isTrash && mayCreate && !uploading) void upload(event.dataTransfer.files); }}>
    <Crumbs items={[{ label: '工作空間', onClick: () => { location.hash = 'files'; } }, ...crumbs.map((item, i) => ({ label: item.name, onClick: i < crumbs.length - 1 ? () => { location.hash = `files/${item.id}`; } : undefined })), ...(!crumbs.length ? [{ label: title }] : [])]} />
    <div className="page-header"><div><h1>{title}</h1><p>{isTrash ? '還原誤刪的資料，或查看保留中的內容。' : collection ? '將重要內容放在一起，原始文件及權限保持不變。' : folderId ? '在這裡整理內容，與團隊一起完成工作。' : '讓內容、知識和團隊在這裡連結。'}</p></div>
      {!isTrash && <div className="page-actions"><button className="button" onClick={() => inputRef.current?.click()} disabled={!mayCreate || uploading}><Upload size={16} />上傳</button><div className="menu-anchor"><button className="button primary" onClick={event => { event.stopPropagation(); setNewMenu(!newMenu); }}><Plus size={17} />新增<ChevronDown size={14} /></button>{newMenu && <div className="dropdown-menu" onClick={event => event.stopPropagation()}><button disabled={!mayCreate} onClick={() => { setDialog({ type: 'folder' }); setNewMenu(false); }}><FolderPlus size={16} />資料夾</button><button disabled={!mayCreate} onClick={() => { setDialog({ type: 'link' }); setNewMenu(false); }}><Link2 size={16} />連結收藏</button><button disabled={!collectionsEnabled} onClick={() => { setDialog({ type: 'newcollection' }); setNewMenu(false); }}><Star size={16} />個人集合</button><div className="menu-divider" /><button onClick={() => { location.hash = 'notes'; }}><FilePlus2 size={16} />筆記</button></div>}</div></div>}
    </div>
    <input ref={inputRef} type="file" multiple className="sr-only" onChange={event => { if (event.target.files) void upload(event.target.files); event.target.value = ''; }} />
    {section === 'files' && !folderId && !collectionId && recent.length > 0 && !query && <section className="recent-section"><div className="section-caption"><h2>最近更新</h2><button onClick={() => { location.hash = 'recent'; }}>查看全部<ChevronRight size={14} /></button></div><div className="recent-grid">{recent.map(item => <button className="recent-card" key={item.id} onClick={() => open(item)}><div className={`recent-thumbnail ${item.kind === 'link' ? 'thumbnail-link' : ''}`}><ResourceIcon resource={item} size={35} /><span className="recent-file-extension">{item.kind === 'link' ? 'LINK' : item.name.split('.').pop()?.toUpperCase().slice(0, 6)}</span></div><div className="recent-card-details"><strong>{item.name}</strong><span>{folders.find(folder => folder.id === item.parent_id)?.name ?? '文件'} <i>·</i> {formatDate(item.updated_at, true)}</span></div></button>)}</div></section>}
    <section className="file-section">
      <div className="content-toolbar"><div className="toolbar-left">{selected.size ? <><button className="icon-button" onClick={() => setSelected(new Set())} aria-label="取消選取"><X size={17} /></button><strong className="selection-count">已選取 {selected.size} 項</strong>{!isTrash && <><button className="button toolbar-button" disabled={chosen.some(item => !canEditResource(item, actor))} onClick={() => setDialog({ type: 'move' })}><FolderInput size={16} /><span>移動</span></button><button className="button toolbar-button" disabled={!collectionsEnabled} onClick={() => setDialog({ type: 'collection' })}><Star size={16} /><span>加入集合</span></button><button className="icon-button danger-text" disabled={chosen.some(item => !canEditResource(item, actor))} onClick={() => setDialog({ type: 'delete' })} aria-label="刪除選取項目"><Trash2 size={17} /></button></>}</> : <><h2>{folderId ? '此資料夾的內容' : collectionId ? '集合內容' : '所有內容'}</h2><span className="item-count">{rows.length}</span></>}</div>
        <div className="toolbar-right"><button className="icon-button content-refresh" onClick={() => { void load(); }} aria-label="重新整理內容"><RefreshCw size={14} /></button><div className="inline-search"><Search size={15} /><input aria-label="篩選目前內容" placeholder="搜尋目前內容" value={query} onChange={event => setQuery(event.target.value)} />{query && <button className="icon-button" onClick={() => setQuery('')} aria-label="清除篩選"><X size={13} /></button>}</div><select className="filter-select" value={kindFilter} onChange={event => setKindFilter(event.target.value)} aria-label="內容類型"><option value="all">所有類型</option><option value="folder">資料夾</option><option value="file">文件</option><option value="link">連結</option></select><div className="view-toggle" aria-label="顯示方式"><button className={view === 'list' ? 'active' : ''} onClick={() => setView('list')} aria-label="列表檢視" aria-pressed={view === 'list'}><List size={17} /></button><button className={view === 'grid' ? 'active' : ''} onClick={() => setView('grid')} aria-label="卡片檢視" aria-pressed={view === 'grid'}><Grid2X2 size={16} /></button></div></div>
      </div>
      <ErrorNotice error={error} onRetry={() => { void load(); }} />
      {loading ? <Loading label="正在載入你的內容…" /> : rows.length === 0 ? <EmptyState icon={isTrash ? <Trash2 size={37} strokeWidth={1.3} /> : query ? <Search size={37} strokeWidth={1.3} /> : <FolderPlus size={39} strokeWidth={1.2} />} title={query ? '沒有符合的內容' : isTrash ? '垃圾桶是空的' : collection ? '讓重要內容聚在一起' : '你的下一個好點子，從這裡開始'} description={query ? '試試其他關鍵字，或調整內容類型。' : isTrash ? '移至垃圾桶的內容會出現在這裡。' : collection ? '在文件的選單中選擇「加入集合」，建立你的個人內容入口。' : '上傳第一份文件，或建立資料夾，為公司的工作建立清晰的起點。'}>{!query && !isTrash && !collection && mayCreate && <><button className="button primary" onClick={() => inputRef.current?.click()}><Upload size={16} />上傳文件</button><button className="button" onClick={() => setDialog({ type: 'folder' })}>建立資料夾</button></>}</EmptyState> : view === 'list' ? <div className="table-wrap"><table className="file-table"><thead><tr><th className="check-cell"><input type="checkbox" aria-label="選取全部內容" checked={rows.length > 0 && rows.every(row => selected.has(row.id))} onChange={event => setSelected(event.target.checked ? new Set(rows.map(item => item.id)) : new Set())} /></th><th><button className="sort-heading" onClick={() => setSort(sort === 'name' ? 'updated' : 'name')}>名稱<ArrowUpDown size={12} /></button></th><th className="hide-medium"><button className="sort-heading" onClick={() => setSort('updated')}>更新時間<ChevronDown size={12} /></button></th><th className="hide-medium">存取範圍</th><th className="hide-small"><button className="sort-heading" onClick={() => setSort('size')}>大小<ChevronDown size={12} /></button></th><th className="row-actions-cell" /></tr></thead><tbody>{rows.map((item, index) => <tr key={item.id} className={`${selected.has(item.id) ? 'selected' : ''} ${preview?.id === item.id ? 'previewed' : ''}`} onDoubleClick={() => { if (!isTrash) open(item); }}>
        <td className="check-cell"><input type="checkbox" aria-label={`選取 ${item.name}`} checked={selected.has(item.id)} onChange={() => toggle(item, index)} onClick={event => { if (event.shiftKey) { event.preventDefault(); toggle(item, index, true); } }} /></td><td><div className="file-name-cell"><button className="resource-open" disabled={isTrash} onClick={() => open(item)}><ResourceIcon resource={item} /><span><strong>{item.name}</strong>{item.kind === 'folder' ? <small>資料夾</small> : item.data.description ? <small>{item.data.description}</small> : null}</span></button>{favoriteIds.includes(item.id) && <Star size={13} className="pinned-star" fill="currentColor" />}{fileState(item) && <Badge tone={fileState(item)!.tone}>{fileState(item)!.label}</Badge>}</div></td>
        <td className="hide-medium date-cell">{formatDate(item.updated_at)}<span>修訂 {item.version}</span></td><td className="hide-medium"><span className={`access-pill ${item.visibility === 'private' ? 'access-private' : ''}`}>{item.visibility === 'private' ? <LockKeyhole size={11} /> : <ShieldCheck size={11} />}{item.visibility === 'private' ? '私人' : '公司'}</span></td><td className="hide-small size-cell">{isFolder(item) ? '—' : item.kind === 'link' ? '連結' : formatBytes(fileSize(item))}</td><td className="row-actions-cell"><div className="row-actions">{!isTrash && canManageResource(item, actor) && <button className="icon-button row-share" onClick={() => setDialog({ type: 'share', resource: item })} aria-label={`分享 ${item.name}`}><Share2 size={16} /></button>}<div className="menu-anchor"><button className={`icon-button ${menu === item.id ? 'active' : ''}`} onClick={event => { event.stopPropagation(); setMenuAnchor(event.currentTarget); setMenu(menu === item.id ? null : item.id); }} aria-label={`${item.name} 的更多操作`} aria-expanded={menu === item.id}><Ellipsis size={19} /></button>{menu === item.id && rowMenu(item)}</div></div></td>
      </tr>)}</tbody></table></div> : <div className="file-grid">{rows.map((item, index) => <article key={item.id} className={`file-card ${selected.has(item.id) ? 'selected' : ''}`}><div className="card-top"><input type="checkbox" aria-label={`選取 ${item.name}`} checked={selected.has(item.id)} onChange={() => toggle(item, index)} /><div className="menu-anchor"><button className="icon-button" onClick={event => { event.stopPropagation(); setMenuAnchor(event.currentTarget); setMenu(menu === item.id ? null : item.id); }} aria-label={`${item.name} 的更多操作`}><Ellipsis size={19} /></button>{menu === item.id && rowMenu(item)}</div></div><button className="card-open" disabled={isTrash} onClick={() => open(item)}><div className="card-preview"><ResourceIcon resource={item} size={48} /></div><strong>{item.name}</strong><span>{isFolder(item) ? '資料夾' : formatBytes(fileSize(item))}<i>·</i>{formatDate(item.updated_at, true)}</span></button></article>)}</div>}
      {!loading && rows.length > 0 && <div className="list-footer"><span>{rows.length} 項內容</span><span><ShieldCheck size={13} /> 內容依你的存取權限顯示</span></div>}
    </section>
    {dragging && <div className="drop-overlay"><Upload size={40} /><h2>將文件放在這裡</h2><p>上傳至 {currentFolder?.name ?? '文件'}</p></div>}
    {uploads.length > 0 && <div className="upload-panel"><div className="upload-panel-heading"><strong>{uploads.every(item => item.progress === 100 || item.error) ? '上傳結果' : '正在上傳'}</strong><button className="icon-button" aria-label="關閉上傳結果" onClick={() => setUploads([])}><X size={17} /></button></div>{uploads.map((item, index) => <div className="upload-item" key={index}><FileText size={18} /><div><strong>{item.name}</strong>{item.error ? <small className="danger-text">{item.error}</small> : <div className="progress-track"><span style={{ width: `${item.progress}%` }} /></div>}</div>{item.progress === 100 ? <Check size={17} className="success-text" /> : <small>{item.progress}%</small>}</div>)}</div>}
    {dialog && <ContentDialog dialog={dialog} selected={chosen} folders={folders} collections={collections.filter(item => item.data.system !== 'favorites')} folderId={folderId} onClose={() => setDialog(null)} onDone={afterMutation} toast={toast} />}
    {preview && <PreviewPanel resource={preview} actor={actor} onClose={() => { setPreview(null); void load(true); }} onShare={() => setDialog({ type: 'share', resource: preview })} onUploadVersion={file => { void upload([file], preview); }} onUpdated={updated => { setPreview(updated); setItems(old => old.map(item => item.id === updated.id ? updated : item)); }} toast={toast} />}
  </div>;
}

function ContentDialog({ dialog, selected, folders, collections, folderId, onClose, onDone, toast }: {
  dialog: NonNullable<Dialog>; selected: Resource[]; folders: Resource[]; collections: Resource[]; folderId?: string; onClose: () => void; onDone: (message?: string) => Promise<void>; toast: Toast;
}) {
  const [resource, setResource] = useState(dialog.resource);
  const [refreshing, setRefreshing] = useState(Boolean(dialog.resource));
  const refreshResource = useCallback(async () => {
    if (!dialog.resource) return;
    setRefreshing(true);
    try { const latest = dialog.resource.deleted_at ? extractList<Resource>(await api('/trash')).find(item => item.id === dialog.resource!.id) : await api<Resource>(`/resource/${dialog.resource.id}`); if (!latest) throw new Error('找不到這項內容，請重新整理。'); setResource(latest); setError(null); }
    catch (e) { setError(e); }
    finally { setRefreshing(false); }
  }, [dialog.resource?.id]);
  useEffect(() => { void refreshResource(); }, [refreshResource]);
  const targets = resource ? [resource] : selected;
  const [name, setName] = useState(resource?.name ?? '');
  const [url, setUrl] = useState('');
  const [description, setDescription] = useState('');
  const [destination, setDestination] = useState(folderId ?? '');
  const [operation, setOperation] = useState('move');
  const [collectionId, setCollectionId] = useState(collections[0]?.id ?? '');
  const [access, setAccess] = useState('invited');
  const [password, setPassword] = useState('');
  const [expires, setExpires] = useState('');
  const [allowDownload, setAllowDownload] = useState(true);
  const [visibility, setVisibility] = useState(resource?.visibility ?? 'company');
  const [shareUrl, setShareUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [legalHold, setLegalHold] = useState(Boolean(resource?.data.legal_hold));
  const [retainUntil, setRetainUntil] = useState(String(resource?.data.retain_until ?? '').slice(0, 10));
  const [archived, setArchived] = useState(Boolean(resource?.data.archived));
  const [userId, setUserId] = useState('');
  const [aclRole, setAclRole] = useState('viewer');
  const [users, setUsers] = useState<Actor[]>([]);
  const [existingShares, setExistingShares] = useState<any[]>([]);
  const loadShares = useCallback(async () => {
    if (!resource) return;
    try { setExistingShares(extractList(await api(`/shares?resource_id=${resource.id}`))); }
    catch (e) { setError(e); }
  }, [resource?.id]);
  useEffect(() => { if (dialog.type === 'share') { void api('/users').then(value => setUsers(extractList<Actor>(value))).catch(() => {}); void loadShares(); } }, [dialog.type, loadShares]);
  const titles: Record<string, string> = { folder: '新增資料夾', link: '收藏連結', newcollection: '建立個人集合', rename: '重新命名', move: '移動或複製內容', collection: '加入個人集合', share: '分享內容', delete: '移至垃圾桶', purge: '永久刪除內容', governance: '保存及安全政策' };
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try {
      if (dialog.type === 'folder' || dialog.type === 'link' || dialog.type === 'newcollection') {
        const kind = dialog.type === 'newcollection' ? 'collection' : dialog.type;
        if (kind === 'link' && !safeExternalUrl(url)) throw new Error('請輸入有效的 HTTP 或 HTTPS 網址，不包括帳戶密碼。');
        await createResource({ kind, name: name.trim(), parent_id: kind === 'collection' ? null : folderId ?? null, visibility: kind === 'collection' ? 'private' : visibility, data: { ...(kind === 'link' ? { url } : {}), ...(kind === 'collection' ? { item_ids: [] } : {}), description } });
        await onDone(kind === 'collection' ? '已建立個人集合' : kind === 'folder' ? '已建立資料夾' : '已收藏連結');
      } else if (dialog.type === 'rename' && resource) { await updateResource(resource, { name: name.trim() }); await onDone('名稱已更新'); }
      else if (dialog.type === 'delete') { for (const item of targets) await deleteResource(item); await onDone(`已將 ${targets.length} 項內容移至垃圾桶`); }
      else if (dialog.type === 'purge' && resource) { await api(`/resource/${resource.id}/purge?expected_version=${resource.version}`, { method: 'DELETE' }); await onDone('內容已永久刪除'); }
      else if (dialog.type === 'move') {
        for (const item of targets) {
          if (destination === item.id) throw new Error('不能將資料夾移入自己。');
          if (operation === 'copy') {
            await api(`/files/${item.id}/copy`, { method: 'POST', body: JSON.stringify({ parent_id: destination || null, name: item.name }) });
          } else await api(`/files/${item.id}/move`, { method: 'POST', body: JSON.stringify({ parent_id: destination || null, expected_version: item.version }) });
        }
        await onDone(operation === 'copy' ? '已複製內容' : '已移動內容');
      } else if (dialog.type === 'collection') {
        const group = collections.find(item => item.id === collectionId); if (!group) throw new Error('請先建立及選擇一個集合。');
        await updateResource(group, { data: { ...group.data, item_ids: [...new Set([...(group.data.item_ids ?? []), ...targets.map(item => item.id)])] } });
        await onDone('已加入個人集合');
      } else if (dialog.type === 'share' && resource) {
        const result = await api<any>('/shares', { method: 'POST', body: JSON.stringify({ resource_id: resource.id, access, allow_download: allowDownload, ...(password ? { password } : {}), ...(expires ? { expires_at: new Date(`${expires}T23:59:59`).toISOString() } : {}) }) });
        const returnedUrl = result.url ?? result.share?.url;
        if (!returnedUrl) throw new Error('分享已建立，但伺服器未回傳連結。請檢查分享設定。');
        setShareUrl(new URL(returnedUrl, location.origin).href); await loadShares(); toast('分享連結已建立');
      } else if (dialog.type === 'governance' && resource) {
        await api(`/files/${resource.id}/governance`, { method: 'POST', body: JSON.stringify({ expected_version: resource.version, legal_hold: legalHold, archived, retain_until: retainUntil ? new Date(`${retainUntil}T23:59:59`).toISOString() : null }) });
        await onDone('保存及安全政策已更新');
      }
    } catch (e) { setError(e); }
    finally { setBusy(false); }
  }
  async function grant() {
    if (!resource || !userId) return; setBusy(true); setError(null);
    try { await updateResource(resource, { acl: { ...resource.acl, [`user:${userId}`]: aclRole } }); toast('協作權限已更新'); await onDone(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function revokeShare(id: string) {
    setBusy(true); setError(null);
    try { await api(`/shares/${id}`, { method: 'DELETE' }); await loadShares(); toast('分享連結已撤銷'); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <Modal title={titles[dialog.type] ?? '內容設定'} subtitle={resource?.name ?? (targets.length > 0 ? `已選取 ${targets.length} 項內容` : undefined)} onClose={onClose}><form onSubmit={submit}>
    <div className="modal-body">
      {['folder', 'link', 'newcollection', 'rename'].includes(dialog.type) && <label className="field"><span>名稱</span><input value={name} onChange={event => setName(event.target.value)} required maxLength={255} placeholder={dialog.type === 'folder' ? '例如：品牌與設計' : dialog.type === 'newcollection' ? '例如：我的重要參考' : ''} /></label>}
      {dialog.type === 'link' && <label className="field"><span>網址</span><input type="url" value={url} onChange={event => setUrl(event.target.value)} required placeholder="https://" /></label>}
      {['folder', 'link', 'newcollection'].includes(dialog.type) && <label className="field"><span>說明 <small>選填</small></span><textarea value={description} onChange={event => setDescription(event.target.value)} rows={3} /></label>}
      {['folder', 'link'].includes(dialog.type) && <label className="field"><span>存取範圍</span><select value={visibility} onChange={event => setVisibility(event.target.value as 'private' | 'company')}><option value="company">公司成員</option><option value="private">私人，只限自己及獲授權協作者</option></select></label>}
      {dialog.type === 'newcollection' && <div className="subtle-notice"><Star size={16} />集合只供你個人使用；加入內容不會搬移原件或改變權限。</div>}
      {dialog.type === 'move' && <><div className="segmented"><button type="button" className={operation === 'move' ? 'active' : ''} onClick={() => setOperation('move')}>移動</button><button type="button" className={operation === 'copy' ? 'active' : ''} onClick={() => setOperation('copy')}>複製</button></div><label className="field"><span>目的地</span><select value={destination} onChange={event => setDestination(event.target.value)}><option value="">文件根目錄</option>{folders.filter(folder => !targets.some(item => item.id === folder.id)).map(folder => <option value={folder.id} key={folder.id}>{folder.name}</option>)}</select></label><div className="subtle-notice"><FolderInput size={17} />目的地的存取權限會由後台驗證。</div></>}
      {dialog.type === 'collection' && (collections.length ? <label className="field"><span>選擇集合</span><select value={collectionId} onChange={event => setCollectionId(event.target.value)}>{collections.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label> : <p className="muted">你還沒有個人集合。先在「新增」選單建立集合，再加入內容。</p>)}
      {['delete', 'purge'].includes(dialog.type) && <><div className="confirm-art"><Trash2 size={26} /></div><p>{dialog.type === 'purge' ? '永久刪除' : '將'} {resource ? `「${resource.name}」` : `${targets.length} 項內容`}{dialog.type === 'purge' ? '？' : ' 移至垃圾桶？'}</p><p className="muted">{dialog.type === 'purge' ? '永久刪除無法還原。未再被版本或上傳引用的實體檔案，會在至少 24 小時寬限期後清理；保存期限及法務保全仍會阻止刪除。' : '內容可在垃圾桶還原。保存政策及法務保全仍然生效。'}</p></>}
      {dialog.type === 'share' && <><div className="share-resource"><ResourceIcon resource={resource!} size={24} /><strong>{resource?.name}</strong><Badge>{resource?.visibility === 'private' ? '私人' : '公司'}</Badge></div><label className="field"><span>誰可以開啟連結？</span><select value={access} onChange={event => { setAccess(event.target.value); setShareUrl(''); }}><option value="invited">已獲邀的協作者</option><option value="company">公司成員</option><option value="anyone">持有連結的任何人</option></select></label><div className="form-grid"><label className="field"><span>到期日期 <small>選填</small></span><input type="date" value={expires} onChange={event => setExpires(event.target.value)} min={new Date().toLocaleDateString('en-CA')} /></label><label className="field"><span>連結密碼 <small>選填</small></span><input type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="new-password" /></label></div><label className="checkbox-label"><input type="checkbox" checked={allowDownload} onChange={event => setAllowDownload(event.target.checked)} />允許下載</label>{shareUrl && <div className="copy-row"><input aria-label="分享連結" value={shareUrl} readOnly onFocus={event => event.target.select()} /><CopyButton value={shareUrl} /></div>}
        {existingShares.length > 0 && <div className="existing-shares"><h3>已建立的連結</h3>{existingShares.map(item => <div className="existing-share-row" key={item.id}><Link2 size={14} /><span><strong>{({ anyone: '任何持有連結的人', company: '公司成員', invited: '已獲邀協作者' } as Record<string, string>)[item.access] ?? item.access}</strong><small>{item.revoked ? '已撤銷' : item.expires_at ? `有效至 ${formatDate(item.expires_at)}` : '未設定期限'}{item.password_protected ? ' · 密碼保護' : ''}</small></span>{!item.revoked && <button type="button" className="button small" onClick={() => { void revokeShare(item.id); }} disabled={busy}>撤銷</button>}</div>)}</div>}
        {users.length > 0 && <div className="share-people"><h3>加入協作者</h3><div className="form-grid"><label className="field"><span>成員</span><select value={userId} onChange={event => setUserId(event.target.value)}><option value="">選擇成員</option>{users.map(user => <option key={user.id} value={user.id}>{user.name || user.email}</option>)}</select></label><label className="field"><span>權限</span><select value={aclRole} onChange={event => setAclRole(event.target.value)}><option value="viewer">檢視者</option><option value="editor">編輯者</option><option value="uploader">上傳者</option></select></label></div><button type="button" className="button" onClick={() => { void grant(); }} disabled={!userId || busy}>授予存取權限</button></div>}</>}
      {dialog.type === 'governance' && <><label className="field"><span>保留至</span><input type="date" value={retainUntil} onChange={event => setRetainUntil(event.target.value)} /></label><label className="checkbox-label"><input type="checkbox" checked={legalHold} onChange={event => setLegalHold(event.target.checked)} />啟用法務保全，禁止永久刪除</label><label className="checkbox-label"><input type="checkbox" checked={archived} onChange={event => setArchived(event.target.checked)} />移至企業歸檔，限制一般存取及編輯</label><div className="subtle-notice"><ShieldCheck size={17} />後台會驗證保存政策。保留期限不能以一般刪除操作繞過。</div></>}
      <ErrorNotice error={error} onRetry={error instanceof ApiError && error.status === 409 ? () => { void refreshResource(); } : undefined} />
    </div><div className="modal-footer"><button type="button" className="button" onClick={onClose}>{shareUrl ? '完成' : '取消'}</button><button className={`button ${['delete', 'purge'].includes(dialog.type) ? 'danger' : 'primary'}`} disabled={busy || refreshing || (dialog.type === 'collection' && !collections.length)}>{busy ? '處理中…' : dialog.type === 'purge' ? '永久刪除' : dialog.type === 'delete' ? '移至垃圾桶' : dialog.type === 'share' ? shareUrl ? '建立新連結' : '建立分享連結' : dialog.type === 'move' ? operation === 'copy' ? '複製' : '移動' : '儲存'}</button></div>
  </form></Modal>;
}

function PreviewPanel({ resource, actor, onClose, onShare, onUploadVersion, onUpdated, toast }: {
  resource: Resource; actor: Actor; onClose: () => void; onShare: () => void; onUploadVersion: (file: File) => void; onUpdated: (resource: Resource) => void; toast: Toast;
}) {
  const [tab, setTab] = useState('details');
  const [comments, setComments] = useState<any[]>([]);
  const [history, setHistory] = useState<any[]>([]);
  const [comment, setComment] = useState('');
  const [description, setDescription] = useState(resource.data.description ?? '');
  const [tags, setTags] = useState((resource.data.tags ?? []).join(', '));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const versionInput = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const oldOverflow = document.body.style.overflow;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    document.body.style.overflow = 'hidden';
    panelRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (document.querySelector('.modal-backdrop')) return;
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); }
      if (event.key !== 'Tab') return;
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea,select,a[href],[tabindex="0"]');
      if (!focusable?.length) return;
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', keydown);
    return () => { document.body.style.overflow = oldOverflow; window.removeEventListener('keydown', keydown); if (previouslyFocused?.isConnected) previouslyFocused.focus(); };
  }, []);
  const canWrite = canEditResource(resource, actor);
  const loadActivity = useCallback(async () => {
    try {
      const [events, versions, current] = await Promise.all([
        resource.kind === 'file' ? api<any>(`/files/${resource.id}/comments`) : Promise.resolve([]),
        api<any>(resource.kind === 'file' ? `/files/${resource.id}/versions` : `/resource/${resource.id}/history`),
        api<Resource>(`/resource/${resource.id}`),
      ]);
      onUpdated(current);
      setComments(extractList(events?.comments ?? events)); setHistory(extractList(versions?.versions ?? versions));
    } catch (e) { setError(e); }
  }, [resource.id, resource.kind]);
  useEffect(() => { setTab('details'); setDescription(resource.data.description ?? ''); setTags((resource.data.tags ?? []).join(', ')); setError(null); void loadActivity(); }, [resource.id, loadActivity]);
  async function saveDetails() {
    setBusy(true); setError(null);
    try { const next = await updateResource(resource, { data: { ...resource.data, description, tags: tags.split(',').map((value: string) => value.trim()).filter(Boolean) } }); onUpdated(next); toast('文件資料已更新'); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function addComment(event: React.FormEvent) {
    event.preventDefault(); if (!comment.trim()) return; setBusy(true); setError(null);
    try { await api(`/files/${resource.id}/comments`, { method: 'POST', body: JSON.stringify({ body: comment.trim() }) }); setComment(''); await loadActivity(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function lock() {
    setBusy(true); setError(null);
    try { await api(`/files/${resource.id}/lock`, { method: 'POST', body: JSON.stringify({ locked: !(resource.data.locked || resource.data.lock?.locked), minutes: 60 }) }); onUpdated(await api<Resource>(`/resource/${resource.id}`)); toast('鎖定狀態已更新'); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function restoreVersion(version: number) {
    setBusy(true); setError(null);
    try { await api(`/files/${resource.id}/versions/${version}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: resource.version }) }); onUpdated(await api<Resource>(`/resource/${resource.id}`)); await loadActivity(); toast('已建立還原版本'); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <div className="preview-backdrop"><section ref={panelRef} className="preview-dialog" role="dialog" aria-modal="true" aria-label={`預覽 ${resource.name}`}><header className="preview-header"><button className="icon-button" onClick={onClose} aria-label="返回文件"><ArrowLeft size={20} /></button><ResourceIcon resource={resource} /><div className="preview-title"><h2>{resource.name}</h2><span>{formatBytes(fileSize(resource))} · 版本 {resource.data.current_version ?? resource.version}</span></div><div className="preview-actions">{officeFile(resource) && <button className="button" onClick={() => { void openOffice(resource, actor, toast); }}><ExternalLink size={16} /><span>Office</span></button>}<button className="button" onClick={() => { void downloadFile(`/files/${resource.id}/download`, resource.name).catch(e => toast(e.message, 'error')); }}><Download size={16} /><span>下載</span></button><button className="button primary" onClick={onShare} disabled={!canManageResource(resource, actor)}><Share2 size={16} /><span>分享</span></button><button className="icon-button" onClick={onClose} aria-label="關閉預覽"><X size={20} /></button></div></header>
    <div className="preview-layout"><div className="preview-stage"><FilePreview resource={resource} /></div><aside className="preview-sidebar"><div className="preview-tabs"><button className={tab === 'details' ? 'active' : ''} onClick={() => setTab('details')}><Info size={16} />詳細資料</button><button className={tab === 'activity' ? 'active' : ''} onClick={() => setTab('activity')}><MessageSquare size={16} />活動</button><button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}><History size={16} />版本</button></div><div className="preview-sidebar-body"><ErrorNotice error={error} />
      {tab === 'details' && <><h3>文件資料</h3><dl className="details-list"><div><dt>名稱</dt><dd>{resource.name}</dd></div><div><dt>建立</dt><dd>{formatDate(resource.created_at)}</dd></div><div><dt>更新</dt><dd>{formatDate(resource.updated_at)}</dd></div><div><dt>存取</dt><dd>{resource.visibility === 'private' ? '私人' : '公司成員'}</dd></div><div><dt>檔案大小</dt><dd>{formatBytes(fileSize(resource))}</dd></div></dl>{fileState(resource) && <Badge tone={fileState(resource)!.tone}>{fileState(resource)!.label}</Badge>}<div className="preview-fields"><label className="field"><span>說明</span><textarea value={description} onChange={event => setDescription(event.target.value)} rows={4} readOnly={!canWrite} placeholder="加入文件說明…" /></label><label className="field"><span>標籤</span><input value={tags} onChange={event => setTags(event.target.value)} readOnly={!canWrite} placeholder="以逗號分隔標籤" /></label>{canWrite && <button className="button" onClick={() => { void saveDetails(); }} disabled={busy}>儲存資料</button>}</div>{resource.kind === 'file' && canWrite && <div className="preview-section"><h3>編輯控制</h3><button className="button" onClick={() => { void lock(); }} disabled={busy}><LockKeyhole size={15} />{resource.data.locked || resource.data.lock?.locked ? '解除鎖定' : '鎖定一小時'}</button><p className="muted">鎖定狀態及權限會由後台驗證。</p></div>}</>}
      {tab === 'activity' && <><h3>團隊討論</h3>{comments.length ? <div className="comments-list">{comments.map((item, index) => <article key={item.id ?? index}><Avatar name={item.data?.author_name ?? item.author_name ?? item.name ?? '成員'} small /><div><strong>{item.data?.author_name ?? item.author_name ?? '公司成員'}</strong><span>{formatDate(item.created_at)}</span><p>{item.body ?? item.data?.body}</p></div></article>)}</div> : <div className="sidebar-empty"><MessageSquare size={26} /><p>還沒有留言</p><span>留下背景、意見或下一步，讓工作更清晰。</span></div>}{resource.kind === 'file' && <form className="comment-form" onSubmit={addComment}><textarea aria-label="新增留言" placeholder="寫下你的留言…" value={comment} onChange={event => setComment(event.target.value)} rows={3} required /><button className="button primary" disabled={busy || !comment.trim()}>發佈留言</button></form>}</>}
      {tab === 'history' && <><div className="section-caption"><h3>版本歷史</h3>{resource.kind === 'file' && canWrite && <button onClick={() => versionInput.current?.click()}><Upload size={14} />上傳新版本</button>}</div><input className="sr-only" type="file" ref={versionInput} onChange={event => { const file = event.target.files?.[0]; if (file) onUploadVersion(file); event.target.value = ''; }} />{history.length ? <div className="version-list">{history.map((item, index) => { const number = item.number ?? item.file_version ?? item.version ?? index + 1; return <article key={item.id ?? number}><div className="version-marker"><History size={16} /></div><div><strong>版本 {number}</strong><span>{formatDate(item.created_at)}</span>{item.size != null && <small>{formatBytes(item.size)}</small>}<div className="version-actions">{resource.kind === 'file' && <><button onClick={() => { void downloadFile(`/files/${resource.id}/versions/${number}/download`, resource.name).catch(e => toast(e.message, 'error')); }}><ArrowDownToLine size={13} />下載</button>{canWrite && <button onClick={() => { void restoreVersion(number); }} disabled={busy}>還原此版本</button>}</>}</div></div></article>; })}</div> : <div className="sidebar-empty"><Clock3 size={26} /><p>尚未有較早版本</p><span>文件更新後會在這裡保留歷史。</span></div>}</>}
    </div></aside></div></section></div>;
}

function FilePreview({ resource }: { resource: Resource }) {
  const [preview, setPreview] = useState<{ url: string; type: string; text?: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [retry, setRetry] = useState(0);
  const [policy, setPolicy] = useState<{ watermark?: boolean; prevent_print?: boolean; watermark_label?: string | null }>({});
  useEffect(() => {
    let active = true, objectUrl = ''; const controller = new AbortController();
    setPreview(null); setLoading(true); setError(null); setPolicy({});
    async function load() {
      try {
        const rules = await api<any>(`/files/${resource.id}/preview-policy`);
        if (active) setPolicy(rules);
        const response = await fetch(apiUrl(`/files/${resource.id}/preview`), { credentials: 'same-origin', signal: controller.signal });
        if (!response.ok || response.headers.get('Content-Type')?.includes('application/json')) {
          const value = await response.json().catch(() => ({}));
          throw new Error(value.error?.message ?? value.message ?? (response.status === 202 ? '預覽正在準備，請稍後重新載入。' : '目前未有可用預覽，你仍可按權限下載原檔。'));
        }
        const blob = await response.blob(); const type = blob.type || 'application/octet-stream';
        if (type.startsWith('text/') && !type.includes('html')) {
          const text = await blob.text(); if (active) setPreview({ url: '', type, text });
        } else { objectUrl = URL.createObjectURL(blob); if (active) setPreview({ url: objectUrl, type }); }
      } catch (e) { if (active && !controller.signal.aborted) setError(e); }
      finally { if (active) setLoading(false); }
    }
    void load(); return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [resource.id, resource.version, retry]);
  if (loading) return <Loading label="正在準備預覽…" />;
  if (error) return <div className="preview-unavailable"><ResourceIcon resource={resource} size={60} /><h3>預覽尚未可用</h3><p>{error instanceof Error ? error.message : String(error)}</p><button className="button" onClick={() => setRetry(value => value + 1)}><RefreshCw size={15} />重新載入預覽</button></div>;
  if (!preview) return null;
  let body;
  if (preview.text != null) body = <pre className="text-preview">{preview.text}</pre>;
  else if (preview.type.startsWith('image/')) body = <img className="image-preview" src={preview.url} alt={resource.name} />;
  else if (preview.type.startsWith('video/')) body = <video className="media-preview" src={preview.url} controls controlsList={policy.prevent_print ? 'nodownload' : undefined} />;
  else if (preview.type.startsWith('audio/')) body = <div className="audio-preview"><ResourceIcon resource={resource} size={70} /><h3>{resource.name}</h3><audio src={preview.url} controls /></div>;
  else body = <iframe className="document-preview" title={`預覽 ${resource.name}`} src={`${preview.url}${policy.prevent_print ? '#toolbar=0' : ''}`} sandbox="allow-same-origin" />;
  return <div className={`preview-renderer ${policy.prevent_print ? 'preview-no-print' : ''}`}>{body}{policy.watermark && policy.watermark_label && !preview.type.includes('pdf') && <div className="preview-watermark" aria-hidden="true">{Array.from({ length: 6 }, (_, index) => <span key={index}>{policy.watermark_label}</span>)}</div>}{policy.prevent_print && <span className="preview-policy-label"><LockKeyhole size={12} />此預覽限制列印</span>}</div>;
}
