import { useEffect, useId, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, Check, Grip, Plus, RotateCcw, X } from 'lucide-react';
import '../workspace-grid.css';

export interface WorkspaceWidget {
  id: string;
  title: string;
  eyebrow?: string;
  icon?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  defaultWidth: number;
  defaultHeight: number;
}

type LayoutItem = { id: string; width: number; height: number; hidden: boolean };
type Props = {
  storageKey: string;
  widgets: WorkspaceWidget[];
  editing: boolean;
  onEditingChange: (value: boolean) => void;
};
type ResizeSession = {
  id: string;
  pointerId: number;
  startX: number;
  startY: number;
  width: number;
  storedWidth: number;
  height: number;
  columns: number;
  columnStep: number;
  rowStep: number;
  moved: boolean;
  nextWidth: number;
  nextHeight: number;
};

const widths = Array.from({ length: 10 }, (_, index) => index + 3);
const heights = Array.from({ length: 9 }, (_, index) => index + 4);
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, Math.round(value)));
const defaultItem = (widget: WorkspaceWidget): LayoutItem => ({
  id: widget.id, width: clamp(widget.defaultWidth, 3, 12), height: clamp(widget.defaultHeight, 4, 12), hidden: false,
});

function readLayout(key: string, widgets: WorkspaceWidget[]): { items: LayoutItem[]; status: string } {
  const defaults = widgets.map(defaultItem);
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { items: defaults, status: '版面會儲存於此瀏覽器' };
    if (raw.length > 50_000) throw new Error('Invalid layout');
    const stored: unknown = JSON.parse(raw);
    if (!stored || typeof stored !== 'object' || !('version' in stored) || stored.version !== 1 || !('items' in stored) || !Array.isArray(stored.items)) throw new Error('Invalid layout');
    const seen = new Set<string>();
    const items: LayoutItem[] = [];
    for (const item of stored.items.slice(0, 200)) {
      if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !item.id || item.id.length > 120 || seen.has(item.id) || typeof item.width !== 'number' || !Number.isFinite(item.width) || typeof item.height !== 'number' || !Number.isFinite(item.height)) continue;
      seen.add(item.id);
      items.push({ id: item.id, width: clamp(item.width, 3, 12), height: clamp(item.height, 4, 12), hidden: item.hidden === true });
    }
    for (const item of defaults) if (!seen.has(item.id)) items.push(item);
    return { items, status: '版面已儲存於此瀏覽器' };
  } catch {
    return { items: defaults, status: '無法讀取已儲存的版面，現正使用預設版面' };
  }
}

/** The keyed state keeps one account's local layout out of another account's view. */
export function WorkspaceGrid(props: Props) {
  return <WorkspaceGridState key={props.storageKey} {...props} />;
}

function WorkspaceGridState({ storageKey, widgets, editing, onEditingChange }: Props) {
  const [initial] = useState(() => readLayout(storageKey, widgets));
  const [layout, setLayout] = useState(initial.items);
  const [status, setStatus] = useState(initial.status);
  const [preview, setPreview] = useState<{ id: string; width: number; height: number } | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);
  const resizeRef = useRef<ResizeSession | null>(null);
  const titlePrefix = useId();
  useEffect(() => {
    const cancel = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape' && resizeRef.current) {
        event.preventDefault();
        resizeRef.current = null;
        setPreview(null);
      }
    };
    window.addEventListener('keydown', cancel);
    return () => { window.removeEventListener('keydown', cancel); resizeRef.current = null; };
  }, []);
  useEffect(() => {
    if (!editing) { resizeRef.current = null; setPreview(null); }
  }, [editing]);
  const widgetMap = useMemo(() => new Map(widgets.map(widget => [widget.id, widget])), [widgets]);
  // Keep temporarily disabled modules in saved state so enabling them restores their cards.
  const items = useMemo(() => [...layout, ...widgets.filter(widget => !layout.some(item => item.id === widget.id)).map(defaultItem)], [layout, widgets]);
  const visible = items.filter(item => !item.hidden && widgetMap.has(item.id));
  const hidden = items.filter(item => item.hidden && widgetMap.has(item.id));

  function commit(next: LayoutItem[]) {
    setLayout(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify({ version: 1, items: next }));
      setStatus('版面已儲存於此瀏覽器');
    } catch {
      setStatus('瀏覽器未能儲存版面；目前調整只會保留至離開此頁');
    }
  }
  function update(id: string, patch: Partial<Omit<LayoutItem, 'id'>>) {
    commit(items.map(item => item.id === id ? { ...item, ...patch } : item));
  }
  function move(id: string, direction: -1 | 1) {
    const current = visible.findIndex(item => item.id === id);
    const target = visible[current + direction];
    if (!target) return;
    const next = [...items];
    const left = next.findIndex(item => item.id === id);
    const right = next.findIndex(item => item.id === target.id);
    [next[left], next[right]] = [next[right], next[left]];
    commit(next);
  }
  function cancelResize() {
    resizeRef.current = null;
    setPreview(null);
  }
  function startResize(event: PointerEvent<HTMLButtonElement>, item: LayoutItem) {
    if (!editing || event.button !== 0 || !gridRef.current) return;
    const style = getComputedStyle(gridRef.current);
    const columns = Number.parseInt(style.getPropertyValue('--workspace-grid-columns')) || 12;
    if (columns < 3) return;
    const gap = Number.parseFloat(style.columnGap) || 16;
    const row = Number.parseFloat(style.gridAutoRows) || 40;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    resizeRef.current = {
      id: item.id, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
      width: Math.min(item.width, columns), storedWidth: item.width, height: item.height, columns,
      columnStep: (gridRef.current.getBoundingClientRect().width + gap) / columns,
      rowStep: row + (Number.parseFloat(style.rowGap) || 16), moved: false,
      nextWidth: item.width, nextHeight: item.height,
    };
  }
  function continueResize(event: PointerEvent<HTMLButtonElement>) {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    const dx = Math.round((event.clientX - session.startX) / session.columnStep);
    const dy = Math.round((event.clientY - session.startY) / session.rowStep);
    if (!dx && !dy && !session.moved) return;
    session.moved = true;
    const visualWidth = clamp(session.width + dx, 3, session.columns);
    // A tablet displays a wide desktop card in six columns. Changing only its
    // height must not silently replace the saved desktop width with six.
    session.nextWidth = visualWidth === session.width ? session.storedWidth : visualWidth;
    session.nextHeight = clamp(session.height + dy, 4, 12);
    setPreview({ id: session.id, width: session.nextWidth, height: session.nextHeight });
  }
  function finishResize(event: PointerEvent<HTMLButtonElement>) {
    const session = resizeRef.current;
    if (!session || session.pointerId !== event.pointerId) return;
    if (session.moved) update(session.id, { width: session.nextWidth, height: session.nextHeight });
    cancelResize();
  }
  function keyboardResize(event: KeyboardEvent<HTMLButtonElement>, item: LayoutItem) {
    if (event.key === 'Escape') { cancelResize(); return; }
    const step = event.shiftKey ? 2 : 1;
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const delta = arrows[event.key];
    if (!delta) return;
    event.preventDefault(); event.stopPropagation();
    update(item.id, { width: clamp(item.width + delta[0], 3, 12), height: clamp(item.height + delta[1], 4, 12) });
  }

  return <section className={`workspace-grid-section ${editing ? 'is-editing' : ''}`} aria-label="工作台卡片版面" onKeyDownCapture={event => { if (event.key === 'Escape' && resizeRef.current) { event.preventDefault(); cancelResize(); } }}>
    {editing && <div className="workspace-layout-toolbar">
      <div className="workspace-layout-hint"><span className="workspace-layout-symbol"><Grip size={16} /></span><div><strong>為你的工作，留出合適的位置</strong><span>拖曳卡片右下角調整大小，或選擇格數。</span></div></div>
      <div className="workspace-layout-actions">
        <label className="workspace-add-widget"><Plus size={15} /><span className="sr-only">加入卡片</span><select aria-label="加入卡片" value="" disabled={!hidden.length} onChange={event => { if (event.target.value) update(event.target.value, { hidden: false }); }}><option value="">{hidden.length ? '加入卡片' : '所有卡片已顯示'}</option>{hidden.map(item => <option key={item.id} value={item.id}>{widgetMap.get(item.id)!.title}</option>)}</select></label>
        <button className="button workspace-reset-layout" type="button" onClick={() => { cancelResize(); commit(widgets.map(defaultItem)); }}><RotateCcw size={14} /><span>還原版面</span></button>
        <button className="button primary workspace-finish-layout" type="button" onClick={() => { cancelResize(); onEditingChange(false); }}><Check size={15} />完成</button>
      </div>
    </div>}
    <div ref={gridRef} className={`workspace-widget-grid ${preview ? 'is-resizing' : ''}`}>
      {visible.map((item, index) => {
        const widget = widgetMap.get(item.id)!;
        const dimensions = preview?.id === item.id ? preview : item;
        const style = { '--widget-width': dimensions.width, '--widget-tablet-width': Math.min(dimensions.width, 6), '--widget-height': dimensions.height } as CSSProperties;
        const titleId = `${titlePrefix}-${item.id}`;
        return <article className={`workspace-widget ${preview?.id === item.id ? 'is-resizing' : ''}`} key={item.id} data-widget-id={item.id} data-width={dimensions.width} data-height={dimensions.height} style={style} aria-labelledby={titleId}>
          {editing && <div className="workspace-widget-editor">
            <span className="workspace-widget-size" aria-label={`${dimensions.width} 欄、${dimensions.height} 格高`}>{dimensions.width} <span>×</span> {dimensions.height}</span>
            <div className="workspace-widget-position"><button type="button" className="icon-button" aria-label={`將 ${widget.title} 向前移動`} disabled={index === 0} onClick={() => move(item.id, -1)}><ArrowUp size={14} /></button><button type="button" className="icon-button" aria-label={`將 ${widget.title} 向後移動`} disabled={index === visible.length - 1} onClick={() => move(item.id, 1)}><ArrowDown size={14} /></button><button type="button" className="icon-button" aria-label={`隱藏 ${widget.title}`} onClick={() => update(item.id, { hidden: true })}><X size={14} /></button></div>
          </div>}
          <header className="workspace-widget-header"><div className="workspace-widget-heading">{widget.icon && <span className="workspace-widget-icon" aria-hidden="true">{widget.icon}</span>}<div>{widget.eyebrow && <span className="workspace-widget-eyebrow">{widget.eyebrow}</span>}<h2 id={titleId}>{widget.title}</h2></div></div>{!editing && widget.action && <div className="workspace-widget-action">{widget.action}</div>}</header>
          <div className="workspace-widget-body">{widget.children}</div>
          {editing && <><div className="workspace-widget-dimensions"><label><span>闊度</span><select aria-label={`${widget.title} 的闊度`} value={item.width} onChange={event => update(item.id, { width: Number(event.target.value) })}>{widths.map(width => <option key={width} value={width}>{width} 欄</option>)}</select></label><label><span>高度</span><select aria-label={`${widget.title} 的高度`} value={item.height} onChange={event => update(item.id, { height: Number(event.target.value) })}>{heights.map(height => <option key={height} value={height}>{height} 格</option>)}</select></label></div><button type="button" className="workspace-widget-resize" aria-label={`調整 ${widget.title} 大小`} aria-describedby={`${titlePrefix}-instructions`} title="拖曳調整大小；方向鍵逐格調整，Shift 加倍，Esc 取消拖曳" onPointerDown={event => startResize(event, item)} onPointerMove={continueResize} onPointerUp={finishResize} onPointerCancel={cancelResize} onLostPointerCapture={cancelResize} onKeyDown={event => keyboardResize(event, item)}><svg viewBox="0 0 20 20" width="20" height="20" aria-hidden="true"><path d="M7 15 15 7M11 15l4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg></button></>}
        </article>;
      })}
    </div>
    {!visible.length && <div className="workspace-grid-empty"><Grip size={24} /><h2>為工作台加入第一張卡片</h2><p>選擇你需要的內容，再安排每一張卡片的大小。</p>{!editing && <button type="button" className="button" onClick={() => onEditingChange(true)}><Plus size={15} />加入卡片</button>}</div>}
    {editing && <div className="workspace-grid-footnote"><p id={`${titlePrefix}-instructions`}>調整把手支援方向鍵；按住 Shift 每次調整兩格。手機上可使用格數選單。</p><span role="status">{status}</span></div>}
  </section>;
}
