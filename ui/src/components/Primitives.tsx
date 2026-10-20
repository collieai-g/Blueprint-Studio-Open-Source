import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { AlertCircle, Check, ChevronRight, File, FileAudio, FileImage, FileSpreadsheet, FileText, FileVideo, Folder, Link2, LoaderCircle, X } from 'lucide-react';
import type { Resource } from '../api';
import { createPortal } from 'react-dom';

export function Modal({ title, subtitle, children, onClose, wide = false }: {
  title: string; subtitle?: string; children: ReactNode; onClose: () => void; wide?: boolean;
}) {
  const titleId = useId();
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const active = document.activeElement as HTMLElement | null;
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focus = ref.current?.querySelector<HTMLElement>('input,select,textarea,button');
    focus?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') closeRef.current();
      if (event.key !== 'Tab') return;
      const elements = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex="0"]');
      if (!elements?.length) return;
      const first = elements[0], last = elements[elements.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
    window.addEventListener('keydown', onKey);
    return () => { document.body.style.overflow = oldOverflow; window.removeEventListener('keydown', onKey); active?.focus(); };
  }, []);
  return <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className={`modal ${wide ? 'modal-wide' : ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} ref={ref}>
      <div className="modal-heading"><div><h2 id={titleId}>{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button className="icon-button" onClick={onClose} aria-label="關閉對話框"><X size={19} /></button></div>
      {children}
    </div>
  </div>;
}

export function ResourceIcon({ resource, size = 21 }: { resource: Pick<Resource, 'kind' | 'name' | 'data'>; size?: number }) {
  const mime = String(resource.data?.mime ?? resource.data?.mime_type ?? '');
  const ext = resource.name.split('.').pop()?.toLowerCase() ?? '';
  if (resource.kind === 'folder') return <span className="file-icon folder-icon"><Folder size={size} fill="currentColor" strokeWidth={1.3} /></span>;
  if (resource.kind === 'link') return <span className="file-icon link-icon"><Link2 size={size} /></span>;
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'heic'].includes(ext)) return <span className="file-icon image-icon"><FileImage size={size} /></span>;
  if (mime.startsWith('video/') || ['mp4', 'mov', 'mkv', 'webm'].includes(ext)) return <span className="file-icon video-icon"><FileVideo size={size} /></span>;
  if (mime.startsWith('audio/') || ['mp3', 'wav', 'aac', 'm4a'].includes(ext)) return <span className="file-icon video-icon"><FileAudio size={size} /></span>;
  if (['xlsx', 'xls', 'csv', 'ods'].includes(ext)) return <span className="file-icon sheet-icon"><FileSpreadsheet size={size} /></span>;
  if (['pdf', 'docx', 'doc', 'txt', 'md', 'odt'].includes(ext)) return <span className={`file-icon ${ext === 'pdf' ? 'pdf-icon' : 'document-icon'}`}><FileText size={size} /></span>;
  return <span className="file-icon"><File size={size} /></span>;
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return <div className="error-notice" role="alert"><AlertCircle size={18} /><span>{error instanceof Error ? error.message : String(error)}</span>{onRetry && <button onClick={onRetry}>重試</button>}</div>;
}

export function EmptyState({ icon, title, description, children }: { icon?: ReactNode; title: string; description?: string; children?: ReactNode }) {
  return <div className="empty-state"><div className="empty-state-art">{icon ?? <Folder size={36} strokeWidth={1.3} />}</div><h3>{title}</h3>{description && <p>{description}</p>}{children && <div className="empty-actions">{children}</div>}</div>;
}

export function Loading({ label = '正在載入…' }: { label?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle size={21} className="spin" /><span>{label}</span></div>;
}

export function Badge({ children, tone = '' }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone ? `badge-${tone}` : ''}`}>{children}</span>;
}

export function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  const text = name.trim().slice(0, 2).toUpperCase();
  return <span className={`avatar ${small ? 'avatar-small' : ''}`} aria-hidden="true">{text}</span>;
}

export function Crumbs({ items }: { items: { label: string; onClick?: () => void }[] }) {
  return <nav className="breadcrumbs" aria-label="所在位置">{items.map((item, index) => <span key={index}>{index > 0 && <ChevronRight size={13} />}{item.onClick ? <button onClick={item.onClick}>{item.label}</button> : <span aria-current={index === items.length - 1 ? 'page' : undefined}>{item.label}</span>}</span>)}</nav>;
}

export function CopyButton({ value, label = '複製' }: { value: string; label?: string }) {
  const copied = useRef<HTMLButtonElement>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      const element = copied.current;
      if (element) { element.dataset.copied = 'true'; setTimeout(() => { if (element.isConnected) delete element.dataset.copied; }, 2000); }
    } catch { /* The selectable value remains available when clipboard is unavailable. */ }
  }
  return <button type="button" className="button copy-button" ref={copied} onClick={copy}><span className="copy-normal">{label}</span><span className="copy-done"><Check size={14} /> 已複製</span></button>;
}

export function FloatingMenu({ anchor, children, onClose }: { anchor: HTMLElement; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [point, setPoint] = useState({ top: 0, left: 0 });
  useLayoutEffect(() => {
    const bounds = anchor.getBoundingClientRect();
    const height = ref.current?.offsetHeight ?? 320;
    const top = bounds.bottom + height + 10 <= window.innerHeight ? bounds.bottom + 5 : Math.max(8, bounds.top - height - 5);
    setPoint({ top, left: Math.max(8, Math.min(bounds.right - 184, window.innerWidth - 192)) });
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus();
  }, [anchor]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); onClose(); anchor.focus(); } };
    window.addEventListener('keydown', key); window.addEventListener('resize', onClose);
    return () => { window.removeEventListener('keydown', key); window.removeEventListener('resize', onClose); };
  }, [anchor, onClose]);
  return createPortal(<div ref={ref} className="dropdown-menu floating-menu" role="menu" style={{ position: 'fixed', left: point.left, top: point.top, right: 'auto', bottom: 'auto', maxHeight: 'calc(100dvh - 16px)', overflowY: 'auto' }} onClick={event => event.stopPropagation()}>{children}</div>, document.body);
}
