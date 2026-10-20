import { useEffect, useState } from 'react';
import { ChevronRight, Download, Folder, LockKeyhole, ShieldCheck } from 'lucide-react';
import { api, apiUrl, downloadFile, extractList, formatBytes, safeExternalUrl, type Resource } from '../api';
import { ErrorNotice, Loading, ResourceIcon } from './Primitives';

export function PublicShare({ token }: { token: string }) {
  const [value, setValue] = useState<any>(null);
  const [password, setPassword] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  async function load() {
    setLoading(true); setError(null);
    try { setValue(await api(`/public/shares/${encodeURIComponent(token)}`, { headers: password ? { 'X-Share-Password': password } : {} })); }
    catch (e) { setError(e); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, [token]); // Password is explicitly submitted.
  const resource = (value?.resource ?? value?.item ?? value) as Resource | null;
  async function download() {
    setBusy(true); setError(null);
    try { await downloadFile(`/public/shares/${token}/download`, resource?.name, password ? { 'X-Share-Password': password } : undefined); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <div className="public-page"><a className="auth-brand" href="/"><span className="brand-mark">b</span><span>藍圖工作室</span></a><main className="public-share-card">
    <div className="public-share-top"><ShieldCheck size={19} /><span>安全分享</span></div>
    {loading ? <Loading /> : resource?.name ? <><div className="share-file-art"><ResourceIcon resource={resource} size={54} /></div><h1>{resource.name}</h1><p className="muted">{formatBytes(resource.data?.size ?? resource.data?.size_bytes ?? 0)} · 由藍圖工作室分享</p>
      {resource.data?.description && <p>{resource.data.description}</p>}
      {resource.kind === 'folder' ? <SharedFolder token={token} root={resource} password={password} allowDownload={value?.allow_download === true} /> : resource.kind === 'file' ? <><PublicPreview token={token} resource={resource} password={password} />{(value?.allow_download ?? value?.share?.allow_download ?? true) && <button className="button primary" onClick={download} disabled={busy}><Download size={17} />{busy ? '正在準備下載…' : '下載檔案'}</button>}</> : resource.kind === 'link' && safeExternalUrl(resource.data.url) ? <a className="button primary" href={safeExternalUrl(resource.data.url)!} target="_blank" rel="noopener noreferrer">開啟收藏連結</a> : null}
      {value?.expires_at && <p className="muted">連結有效至 {new Date(value.expires_at).toLocaleString('zh-HK')}</p>}
    </> : <><div className="share-file-art"><LockKeyhole size={46} strokeWidth={1.4} /></div><h1>受保護的分享連結</h1><p className="muted">如連結設有密碼，請輸入分享者提供的密碼。</p><form onSubmit={event => { event.preventDefault(); void load(); }} className="public-password"><label className="field"><span>分享密碼</span><input type="password" autoComplete="off" value={password} onChange={event => setPassword(event.target.value)} /></label><button className="button primary">開啟分享</button></form><a href={`/?returnTo=${encodeURIComponent(`/share/${token}`)}`}>使用公司帳戶登入</a></>}
    <ErrorNotice error={error} />
  </main><footer>你的內容，由你掌握。</footer></div>;
}

function SharedFolder({ token, root, password, allowDownload }: { token: string; root: Resource; password: string; allowDownload: boolean }) {
  const [path, setPath] = useState<Resource[]>([]);
  const [items, setItems] = useState<Resource[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const current = path.at(-1) ?? root;
  useEffect(() => {
    let active = true; setLoading(true); setError(null);
    void api<any>(`/public/shares/${token}/items${current.id !== root.id ? `?parent_id=${current.id}` : ''}`, { headers: password ? { 'X-Share-Password': password } : {} }).then(value => { if (active) setItems(extractList(value)); }).catch(e => { if (active) setError(e); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [current.id, root.id, token, password]);
  async function download(item: Resource) { setError(null); try { await downloadFile(`/public/shares/${token}/files/${item.id}/download`, item.name, password ? { 'X-Share-Password': password } : undefined); } catch (e) { setError(e); } }
  return <section className="shared-folder"><nav className="shared-folder-crumbs" aria-label="分享資料夾路徑"><button onClick={() => setPath([])}>{root.name}</button>{path.map((item, index) => <span key={item.id}><ChevronRight size={11} /><button onClick={() => setPath(path.slice(0, index + 1))}>{item.name}</button></span>)}</nav><ErrorNotice error={error} />{loading ? <Loading /> : items.length ? <div className="shared-folder-items">{items.map(item => <article key={item.id}><ResourceIcon resource={item} /><div>{item.kind === 'folder' ? <button onClick={() => setPath([...path, item])}>{item.name}</button> : <strong>{item.name}</strong>}<span>{item.kind === 'folder' ? '資料夾' : formatBytes(item.data?.size ?? 0)}</span></div>{item.kind === 'folder' ? <button className="icon-button" onClick={() => setPath([...path, item])} aria-label={`開啟 ${item.name}`}><ChevronRight size={16} /></button> : item.kind === 'file' && allowDownload ? <button className="icon-button" onClick={() => { void download(item); }} aria-label={`下載 ${item.name}`}><Download size={16} /></button> : null}</article>)}</div> : <div className="shared-folder-empty"><Folder size={29} /><p>此資料夾目前沒有可分享的內容。</p></div>}</section>;
}

function PublicPreview({ token, resource, password }: { token: string; resource: Resource; password: string }) {
  const [preview, setPreview] = useState<{ url: string; type: string; text?: string; restricted: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true, objectUrl = ''; const controller = new AbortController();
    setLoading(true); setError(null);
    async function load() {
      try {
        const response = await fetch(apiUrl(`/public/shares/${token}/preview`), { credentials: 'same-origin', headers: password ? { 'X-Share-Password': password } : {}, signal: controller.signal });
        if (!response.ok || response.headers.get('Content-Type')?.includes('application/json')) { const value = await response.json().catch(() => ({})); throw new Error(value.error?.message ?? '此文件目前未有可用預覽。'); }
        const blob = await response.blob(); const type = blob.type; const restricted = response.headers.get('X-Blueprint-Print-Restricted') === '1';
        if (type.startsWith('text/') && !type.includes('html')) { const text = await blob.text(); if (active) setPreview({ url: '', type, text, restricted }); }
        else { objectUrl = URL.createObjectURL(blob); if (active) setPreview({ url: objectUrl, type, restricted }); }
      } catch (e) { if (active && !controller.signal.aborted) setError(e instanceof Error ? e.message : String(e)); }
      finally { if (active) setLoading(false); }
    }
    void load(); return () => { active = false; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [token, resource.id, password]);
  if (loading) return <Loading label="正在準備分享預覽…" />;
  if (error) return <p className="public-preview-error muted">{error}</p>;
  if (!preview) return null;
  return <div className={`public-preview ${preview.restricted ? 'preview-no-print' : ''}`}>{preview.text != null ? <pre>{preview.text}</pre> : preview.type.startsWith('image/') ? <img src={preview.url} alt={resource.name} /> : preview.type.startsWith('audio/') ? <audio src={preview.url} controls /> : preview.type.startsWith('video/') ? <video src={preview.url} controls /> : <iframe title={`分享預覽 ${resource.name}`} src={`${preview.url}${preview.restricted ? '#toolbar=0' : ''}`} sandbox="allow-same-origin" />}</div>;
}
