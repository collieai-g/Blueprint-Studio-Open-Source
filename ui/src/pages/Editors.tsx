import { useCallback, useEffect, useRef, useState } from "react";
import * as Y from "yjs";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowLeft,
  ArrowUpRight,
  Bold,
  Check,
  ChevronDown,
  Circle,
  Code2,
  Download,
  FileText,
  Folder,
  Heading2,
  History,
  Image,
  Italic,
  LayoutGrid,
  Link2,
  List,
  ListChecks,
  Maximize,
  Minus,
  MousePointer2,
  Pencil,
  Plus,
  Quote,
  Redo2,
  Search,
  Square,
  StickyNote,
  Strikethrough,
  Trash2,
  Type,
  Undo2,
  WifiOff,
} from "lucide-react";
import { api, type Resource } from "../api";
import {
  Banner,
  ConfirmDelete,
  Empty,
  FormField,
  Header,
  Modal,
  createResource,
  dataOf,
  errorText,
  time,
  updateResource,
  useResources,
  useOpenResource,
  type Data,
} from "./Business";

const bytesToBase64 = (bytes: Uint8Array) => {
  let raw = "";
  for (let i = 0; i < bytes.length; i += 8192)
    raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(raw);
};
const base64ToBytes = (raw: string) =>
  Uint8Array.from(atob(raw), (char) => char.charCodeAt(0));

function useCollaborativeDocument(resource: Resource) {
  const [content, setContent] = useState(
      String(dataOf(resource).content || ""),
    ),
    [elements, setElements] = useState<Data[]>(dataOf(resource).elements || []),
    [connected, setConnected] = useState(false),
    [synced, setSynced] = useState(false),
    [error, setError] = useState(""),
    [version, setVersion] = useState(resource.version);
  const documentRef = useRef<Y.Doc | null>(null),
    socketRef = useRef<WebSocket | null>(null),
    pendingRef = useRef<Uint8Array[]>([]),
    undoRef = useRef<Y.UndoManager | null>(null);
  useEffect(() => {
    const doc = new Y.Doc(),
      text = doc.getText("content"),
      canvas = doc.getMap("canvas");
    documentRef.current = doc;
    undoRef.current = new Y.UndoManager([text, canvas], {
      trackedOrigins: new Set(["local"]),
    });
    let stopped = false,
      reconnect: number | undefined;
    const updateText = () => setContent(text.toString());
    const updateCanvas = () =>
      setElements(
        [...canvas.values()].filter(
          (value) => typeof value === "object",
        ) as Data[],
      );
    text.observe(updateText);
    canvas.observe(updateCanvas);
    doc.on("update", (update: Uint8Array, origin: any) => {
      if (origin === "remote") return;
      if (socketRef.current?.readyState === WebSocket.OPEN)
        socketRef.current.send(
          JSON.stringify({ type: "update", update: bytesToBase64(update) }),
        );
      else pendingRef.current.push(update);
    });
    const connect = () => {
      if (stopped) return;
      const socket = new WebSocket(
        `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/v1/collaboration/${resource.id}/ws`,
      );
      socketRef.current = socket;
      socket.onopen = () => {
        setConnected(true);
        setError("");
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          if (message.type === "error") {
            setError(message.message || message.error || "協同更新被拒絕。");
            return;
          }
          if (message.update)
            Y.applyUpdate(doc, base64ToBytes(message.update), "remote");
          if (message.version) setVersion(message.version);
          if (message.type === "sync") {
            setSynced(true);
            updateText();
            updateCanvas();
            const pending = pendingRef.current.splice(0);
            for (const update of pending)
              socket.send(
                JSON.stringify({
                  type: "update",
                  update: bytesToBase64(update),
                }),
              );
          }
        } catch (e) {
          setError(errorText(e));
        }
      };
      socket.onerror = () =>
        setError("暫時無法連接協同服務。系統會自動嘗試重新連接。");
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) reconnect = window.setTimeout(connect, 2500);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (reconnect) window.clearTimeout(reconnect);
      socketRef.current?.close();
      undoRef.current?.destroy();
      doc.destroy();
      documentRef.current = null;
      pendingRef.current = [];
    };
  }, [resource.id]);
  const replaceText = useCallback((value: string) => {
    const doc = documentRef.current;
    if (!doc) return;
    const text = doc.getText("content"),
      previous = text.toString();
    let prefix = 0;
    while (
      prefix < previous.length &&
      prefix < value.length &&
      previous[prefix] === value[prefix]
    )
      prefix++;
    let suffix = 0;
    while (
      suffix < previous.length - prefix &&
      suffix < value.length - prefix &&
      previous[previous.length - suffix - 1] ===
        value[value.length - suffix - 1]
    )
      suffix++;
    doc.transact(() => {
      text.delete(prefix, previous.length - prefix - suffix);
      text.insert(prefix, value.slice(prefix, value.length - suffix));
    }, "local");
  }, []);
  const setElement = useCallback((element: Data) => {
    const doc = documentRef.current;
    if (doc)
      doc.transact(
        () => doc.getMap("canvas").set(String(element.id), element),
        "local",
      );
  }, []);
  const removeElement = useCallback((id: string) => {
    const doc = documentRef.current;
    if (doc) doc.transact(() => doc.getMap("canvas").delete(id), "local");
  }, []);
  const restoreElements = useCallback((next: Data[]) => {
    const doc = documentRef.current;
    if (doc)
      doc.transact(() => {
        const map = doc.getMap("canvas");
        for (const key of [...map.keys()]) map.delete(key);
        for (const element of next) map.set(String(element.id), element);
      }, "local");
  }, []);
  return {
    content,
    elements,
    connected,
    synced,
    error,
    version,
    replaceText,
    setElement,
    removeElement,
    restoreElements,
    undo: () => undoRef.current?.undo(),
    redo: () => undoRef.current?.redo(),
  };
}

function RenameModal({
  item,
  onClose,
  onDone,
}: {
  item: Resource;
  onClose: () => void;
  onDone: (resource: Resource) => void;
}) {
  const [name, setName] = useState(item.name),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal title="重新命名" onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            const latest = await api<Resource>(`/resource/${item.id}`);
            const result = await updateResource(latest, dataOf(latest), name);
            onDone(result);
            onClose();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <FormField label="名稱">
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <Banner error={error} />
        <div className="business-form-footer">
          <button type="button" className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy}>
            儲存
          </button>
        </div>
      </form>
    </Modal>
  );
}

function HistoryModal({
  resource,
  onClose,
  onRestore,
}: {
  resource: Resource;
  onClose: () => void;
  onRestore: (snapshot: Data) => void;
}) {
  const [items, setItems] = useState<Data[]>([]),
    [selected, setSelected] = useState<Data | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    void api<Data[]>(`/resource/${resource.id}/history`)
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  }, [resource.id]);
  const snapshot = selected?.snapshot || selected?.resource || selected;
  return (
    <Modal title="版本歷史" onClose={onClose} wide>
      <Banner error={error} />
      <div className="editor-history">
        <aside>
          {items.map((item, index) => {
            const value = item.snapshot || item.resource || item;
            return (
              <button
                key={item.id || index}
                className={selected === item ? "active" : ""}
                onClick={() => setSelected(item)}
              >
                <strong>
                  第 {value.version || item.version || index + 1} 版
                </strong>
                <small>{time(item.created_at || value.updated_at)}</small>
              </button>
            );
          })}
          {!items.length && !error && <p className="muted">未有較早版本。</p>}
        </aside>
        <div>
          {snapshot ? (
            <>
              <h3>{snapshot.name || resource.name}</h3>
              <pre className="editor-history-content">
                {snapshot.data?.content ||
                  JSON.stringify(
                    snapshot.data?.elements || snapshot.data,
                    null,
                    2,
                  )}
              </pre>
              <button
                className="button primary"
                onClick={() => {
                  onRestore(snapshot);
                  onClose();
                }}
              >
                以此內容建立新版本
              </button>
            </>
          ) : (
            <p className="muted">選擇版本查看內容。</p>
          )}
        </div>
      </div>
    </Modal>
  );
}

function MarkdownView({ content }: { content: string }) {
  return (
    <div className="note-preview">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function NoteEditor({
  initial,
  onClose,
}: {
  initial: Resource;
  onClose: () => void;
}) {
  const [resource, setResource] = useState(initial),
    collaborative = useCollaborativeDocument(resource),
    [mode, setMode] = useState("edit"),
    [rename, setRename] = useState(false),
    [history, setHistory] = useState(false),
    editor = useRef<HTMLTextAreaElement>(null);
  function format(before: string, after = "", placeholder = "文字") {
    const area = editor.current;
    if (!area) return;
    const start = area.selectionStart,
      end = area.selectionEnd,
      value = collaborative.content;
    collaborative.replaceText(
      value.slice(0, start) +
        before +
        (value.slice(start, end) || placeholder) +
        after +
        value.slice(end),
    );
    requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(
        start + before.length,
        start + before.length + (end - start || placeholder.length),
      );
    });
  }
  return (
    <div className="note-editor">
      <header className="document-editor-header">
        <button className="icon-button" title="返回筆記" onClick={onClose}>
          <ArrowLeft size={20} />
        </button>
        <FileText size={21} className="note-document-icon" />
        <button className="document-title" onClick={() => setRename(true)}>
          {resource.name}
          <ChevronDown size={14} />
        </button>
        <div
          className={`collaboration-status ${collaborative.connected ? "connected" : ""}`}
        >
          {collaborative.connected ? (
            <>
              <Check size={14} />
              已連接 · 第 {collaborative.version} 版
            </>
          ) : (
            <>
              <WifiOff size={14} />
              正在重新連接
            </>
          )}
        </div>
        <div className="business-actions">
          <button
            className={`button secondary compact ${mode === "preview" ? "active" : ""}`}
            onClick={() => setMode(mode === "edit" ? "preview" : "edit")}
          >
            {mode === "edit" ? "閱讀模式" : "編輯模式"}
          </button>
          <button
            className="button secondary compact"
            onClick={() => setHistory(true)}
          >
            <History size={16} />
            版本歷史
          </button>
          <button
            className="icon-button"
            title="下載 Markdown"
            onClick={() => {
              const blob = new Blob([collaborative.content], {
                type: "text/markdown;charset=utf-8",
              });
              const url = URL.createObjectURL(blob),
                anchor = document.createElement("a");
              anchor.href = url;
              anchor.download = `${resource.name}.md`;
              anchor.click();
              URL.revokeObjectURL(url);
            }}
          >
            <Download size={18} />
          </button>
        </div>
      </header>
      <Banner error={collaborative.error} />
      <div className="note-format-toolbar">
        <div>
          <button
            className="icon-button"
            title="復原"
            onClick={collaborative.undo}
          >
            <Undo2 size={17} />
          </button>
          <button
            className="icon-button"
            title="重做"
            onClick={collaborative.redo}
          >
            <Redo2 size={17} />
          </button>
        </div>
        <span className="toolbar-divider" />
        <div>
          <button
            className="icon-button"
            title="標題"
            onClick={() => format("## ", "", "標題")}
          >
            <Heading2 size={18} />
          </button>
          <button
            className="icon-button"
            title="粗體"
            onClick={() => format("**", "**")}
          >
            <Bold size={17} />
          </button>
          <button
            className="icon-button"
            title="斜體"
            onClick={() => format("_", "_")}
          >
            <Italic size={17} />
          </button>
          <button
            className="icon-button"
            title="刪除線"
            onClick={() => format("~~", "~~")}
          >
            <Strikethrough size={17} />
          </button>
        </div>
        <span className="toolbar-divider" />
        <div>
          <button
            className="icon-button"
            title="清單"
            onClick={() => format("- ", "", "項目")}
          >
            <List size={18} />
          </button>
          <button
            className="icon-button"
            title="待辦清單"
            onClick={() => format("- [ ] ", "", "待辦")}
          >
            <ListChecks size={18} />
          </button>
          <button
            className="icon-button"
            title="引用"
            onClick={() => format("> ", "", "引用內容")}
          >
            <Quote size={17} />
          </button>
        </div>
        <span className="toolbar-divider" />
        <div>
          <button
            className="icon-button"
            title="連結"
            onClick={() => format("[", "](https://example.com)", "連結文字")}
          >
            <Link2 size={17} />
          </button>
          <button
            className="icon-button"
            title="程式碼"
            onClick={() => format("`", "`", "code")}
          >
            <Code2 size={18} />
          </button>
          <button
            className="icon-button"
            title="表格"
            onClick={() =>
              format(
                "\n| 欄位一 | 欄位二 |\n| --- | --- |\n| ",
                " | 內容 |\n",
                "內容",
              )
            }
          >
            <LayoutGrid size={17} />
          </button>
          <button
            className="icon-button"
            title="插入圖片網址"
            onClick={() =>
              format("![", "](https://example.com/image.png)", "圖片說明")
            }
          >
            <Image size={17} />
          </button>
        </div>
        <span className="note-format-label">Markdown</span>
      </div>
      <main className="note-paper">
        <h1 onClick={() => setRename(true)}>{resource.name}</h1>
        {mode === "edit" ? (
          <textarea
            ref={editor}
            aria-label="筆記內容"
            className="note-content-input"
            spellCheck={false}
            disabled={!collaborative.synced}
            value={collaborative.content}
            onChange={(e) => collaborative.replaceText(e.target.value)}
            placeholder={
              collaborative.synced
                ? "在這裏開始寫筆記…\n\n內容會經協同服務自動保存，亦可使用 Markdown 格式。"
                : "正在同步文件…"
            }
          />
        ) : (
          <MarkdownView content={collaborative.content} />
        )}
      </main>
      <footer className="note-statusbar">
        <span>{collaborative.content.length.toLocaleString()} 個字元</span>
        <span>
          {collaborative.connected
            ? "協同編輯已連接，更新自動保存"
            : "連接恢復後將同步暫存更新"}
        </span>
      </footer>
      {rename && (
        <RenameModal
          item={resource}
          onClose={() => setRename(false)}
          onDone={setResource}
        />
      )}
      {history && (
        <HistoryModal
          resource={resource}
          onClose={() => setHistory(false)}
          onRestore={(snapshot) =>
            collaborative.replaceText(String(snapshot.data?.content || ""))
          }
        />
      )}
    </div>
  );
}

const COLORS = [
  "#fff2ad",
  "#dff2e4",
  "#e0edff",
  "#f0e2ff",
  "#ffe0d8",
  "#ffffff",
];
type CanvasElement = Data & {
  id: string;
  type: string;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  text?: string;
};
function CanvasEditor({
  initial,
  onClose,
}: {
  initial: Resource;
  onClose: () => void;
}) {
  const [resource, setResource] = useState(initial),
    collaborative = useCollaborativeDocument(resource),
    [tool, setTool] = useState("select"),
    [selected, setSelected] = useState<string | null>(null),
    [color, setColor] = useState(COLORS[0]),
    [zoom, setZoom] = useState(1),
    [rename, setRename] = useState(false),
    [history, setHistory] = useState(false),
    [editing, setEditing] = useState<CanvasElement | null>(null),
    [draftText, setDraftText] = useState("");
  const [drawing, setDrawing] = useState<CanvasElement | null>(null),
    dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null),
    connectorRef = useRef<string | null>(null),
    boardRef = useRef<SVGSVGElement>(null);
  const elements = collaborative.elements as CanvasElement[];
  const coords = (event: React.PointerEvent<SVGElement>) => {
    const box = boardRef.current!.getBoundingClientRect();
    return {
      x: (event.clientX - box.left) / zoom,
      y: (event.clientY - box.top) / zoom,
    };
  };
  function selectElement(
    event: React.PointerEvent<SVGGElement>,
    element: CanvasElement,
  ) {
    event.stopPropagation();
    if (!collaborative.synced) return;
    if (tool === "connect") {
      if (connectorRef.current && connectorRef.current !== element.id) {
        collaborative.setElement({
          id: crypto.randomUUID(),
          type: "connector",
          source: connectorRef.current,
          target: element.id,
          x: 0,
          y: 0,
          width: 0,
          height: 0,
          color: "#748297",
        });
        connectorRef.current = null;
        setTool("select");
      } else {
        connectorRef.current = element.id;
        setSelected(element.id);
      }
      return;
    }
    setSelected(element.id);
    if (tool === "select") {
      const p = coords(event);
      dragRef.current = {
        id: element.id,
        dx: p.x - element.x,
        dy: p.y - element.y,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    }
  }
  function onBoardDown(event: React.PointerEvent<SVGSVGElement>) {
    if (!collaborative.synced) return;
    const p = coords(event);
    if (tool === "select") {
      setSelected(null);
      return;
    }
    if (tool === "connect") {
      connectorRef.current = null;
      setSelected(null);
      return;
    }
    const element: CanvasElement = {
      id: crypto.randomUUID(),
      type: tool,
      x: p.x,
      y: p.y,
      width: tool === "text" ? 240 : tool === "sticky" ? 190 : 180,
      height: tool === "text" ? 52 : tool === "sticky" ? 150 : 110,
      color: tool === "pen" ? "#384659" : color,
      text: "",
    };
    if (tool === "pen") {
      element.points = [[0, 0]];
      setDrawing(element);
      event.currentTarget.setPointerCapture(event.pointerId);
    } else {
      collaborative.setElement(element);
      setSelected(element.id);
      if (tool === "text" || tool === "sticky") {
        setEditing(element);
        setDraftText("");
      }
      setTool("select");
    }
  }
  function onMove(event: React.PointerEvent<SVGSVGElement>) {
    const p = coords(event);
    if (drawing) {
      setDrawing({
        ...drawing,
        points: [...(drawing.points || []), [p.x - drawing.x, p.y - drawing.y]],
      });
      return;
    }
    if (!dragRef.current) return;
    const element = elements.find((e) => e.id === dragRef.current!.id);
    if (element)
      collaborative.setElement({
        ...element,
        x: Math.max(0, p.x - dragRef.current.dx),
        y: Math.max(0, p.y - dragRef.current.dy),
      });
  }
  function renderElement(element: CanvasElement, temporary = false) {
    if (element.type === "connector") {
      const from = elements.find((e) => e.id === element.source),
        to = elements.find((e) => e.id === element.target);
      return from && to ? (
        <line
          key={element.id}
          x1={from.x + from.width / 2}
          y1={from.y + from.height / 2}
          x2={to.x + to.width / 2}
          y2={to.y + to.height / 2}
          stroke={element.color}
          strokeWidth={2}
          markerEnd="url(#canvas-arrow)"
          onClick={(event) => {
            event.stopPropagation();
            setSelected(element.id);
          }}
          className={selected === element.id ? "selected-connector" : ""}
        />
      ) : null;
    }
    return (
      <g
        key={element.id}
        transform={`translate(${element.x},${element.y})`}
        onPointerDown={(event) => selectElement(event, element)}
        onDoubleClick={(event) => {
          event.stopPropagation();
          if (element.type !== "pen") {
            setEditing(element);
            setDraftText(element.text || "");
          }
        }}
        className={temporary ? "" : "canvas-object"}
      >
        {element.type === "pen" ? (
          <polyline
            points={(element.points || [])
              .map((p: number[]) => p.join(","))
              .join(" ")}
            fill="none"
            stroke={element.color}
            strokeWidth={3}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ) : element.type === "ellipse" ? (
          <ellipse
            cx={element.width / 2}
            cy={element.height / 2}
            rx={element.width / 2}
            ry={element.height / 2}
            fill={element.color}
            stroke="#919aaa"
            strokeWidth={1.4}
          />
        ) : element.type === "text" ? null : (
          <rect
            width={element.width}
            height={element.height}
            rx={element.type === "sticky" ? 2 : 8}
            fill={element.color}
            stroke={element.type === "sticky" ? "#00000010" : "#919aaa"}
            strokeWidth={1.3}
            style={
              element.type === "sticky"
                ? { filter: "drop-shadow(0 3px 4px #0000000d)" }
                : undefined
            }
          />
        )}
        {element.type !== "pen" && (
          <foreignObject
            x={12}
            y={element.type === "text" ? 0 : 12}
            width={element.width - 24}
            height={element.height - 12}
          >
            <div
              className={`canvas-object-text ${element.type === "text" ? "free-text" : ""}`}
            >
              {element.text ||
                (element.type === "sticky" ? "連按兩下輸入內容" : "")}
            </div>
          </foreignObject>
        )}
        {selected === element.id && (
          <rect
            x={-5}
            y={-5}
            width={element.width + 10}
            height={element.height + 10}
            fill="none"
            stroke="#176cf2"
            strokeWidth={1.3}
            strokeDasharray="4 2"
            pointerEvents="none"
          />
        )}
      </g>
    );
  }
  return (
    <div className="canvas-editor">
      <header className="document-editor-header">
        <button className="icon-button" onClick={onClose} title="返回白板">
          <ArrowLeft size={20} />
        </button>
        <LayoutGrid size={21} className="canvas-document-icon" />
        <button className="document-title" onClick={() => setRename(true)}>
          {resource.name}
          <ChevronDown size={14} />
        </button>
        <div
          className={`collaboration-status ${collaborative.connected ? "connected" : ""}`}
        >
          {collaborative.connected ? (
            <>
              <Check size={14} />
              已同步
            </>
          ) : (
            <>
              <WifiOff size={14} />
              重新連接中
            </>
          )}
        </div>
        <div className="business-actions">
          <button
            className="button secondary compact"
            onClick={() => setHistory(true)}
          >
            <History size={16} />
            版本歷史
          </button>
          <button
            className="button secondary compact"
            onClick={() => {
              if (!boardRef.current) return;
              const data = new XMLSerializer().serializeToString(
                  boardRef.current,
                ),
                blob = new Blob([data], { type: "image/svg+xml" }),
                url = URL.createObjectURL(blob),
                a = document.createElement("a");
              a.href = url;
              a.download = `${resource.name}.svg`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            <Download size={16} />
            匯出 SVG
          </button>
        </div>
      </header>
      <Banner error={collaborative.error} />
      <div className="canvas-workspace">
        <div className="canvas-toolbar">
          {[
            ["select", MousePointer2, "選取"],
            ["sticky", StickyNote, "便利貼"],
            ["rectangle", Square, "矩形"],
            ["ellipse", Circle, "圓形"],
            ["text", Type, "文字"],
            ["pen", Pencil, "畫筆"],
            ["connect", Link2, "連接線"],
          ].map(([id, Icon, title]) => {
            const ToolIcon = Icon as typeof Pencil;
            return (
              <button
                key={String(id)}
                className={tool === id ? "active" : ""}
                onClick={() => {
                  setTool(String(id));
                  connectorRef.current = null;
                }}
                title={String(title)}
                aria-label={String(title)}
              >
                <ToolIcon size={20} />
              </button>
            );
          })}
          <span className="toolbar-divider" />
          <button title="復原" onClick={collaborative.undo}>
            <Undo2 size={19} />
          </button>
          <button title="重做" onClick={collaborative.redo}>
            <Redo2 size={19} />
          </button>
        </div>
        <div className="canvas-colors">
          {COLORS.map((c) => (
            <button
              key={c}
              aria-label={`顏色 ${c}`}
              className={color === c ? "selected" : ""}
              style={{ background: c }}
              onClick={() => {
                setColor(c);
                const element = elements.find((e) => e.id === selected);
                if (element) collaborative.setElement({ ...element, color: c });
              }}
            />
          ))}
        </div>
        {selected && (
          <button
            className="canvas-delete button danger compact"
            onClick={() => {
              collaborative.removeElement(selected);
              elements
                .filter(
                  (e) =>
                    e.type === "connector" &&
                    (e.source === selected || e.target === selected),
                )
                .forEach((e) => collaborative.removeElement(e.id));
              setSelected(null);
            }}
          >
            <Trash2 size={16} />
            刪除
          </button>
        )}
        <div className="canvas-board-scroll">
          <svg
            ref={boardRef}
            className="canvas-board"
            width={1800 * zoom}
            height={1200 * zoom}
            viewBox="0 0 1800 1200"
            onPointerDown={onBoardDown}
            onPointerMove={onMove}
            onPointerUp={() => {
              if (drawing) {
                collaborative.setElement(drawing);
                setDrawing(null);
              }
              dragRef.current = null;
            }}
            style={{ cursor: tool === "select" ? "default" : "crosshair" }}
          >
            <defs>
              <pattern
                id="canvas-dots"
                width="24"
                height="24"
                patternUnits="userSpaceOnUse"
              >
                <circle cx="1" cy="1" r="1" fill="#cdd4dd" />
              </pattern>
              <marker
                id="canvas-arrow"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="6"
                markerHeight="6"
                orient="auto"
              >
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#748297" />
              </marker>
            </defs>
            <rect width="1800" height="1200" fill="url(#canvas-dots)" />
            {elements
              .filter((e) => e.type === "connector")
              .map((e) => renderElement(e))}
            {elements
              .filter((e) => e.type !== "connector")
              .map((e) => renderElement(e))}
            {drawing && renderElement(drawing, true)}
          </svg>
        </div>
        <div className="canvas-help">
          {!collaborative.synced
            ? "正在同步白板…"
            : tool === "connect"
              ? connectorRef.current
                ? "選擇另一個物件完成連接"
                : "依次選擇兩個物件建立連線"
              : "雙擊編輯文字 · 拖動移動物件"}
        </div>
        <div className="canvas-zoom">
          <button
            aria-label="縮小"
            onClick={() => setZoom(Math.max(0.4, zoom - 0.1))}
          >
            <Minus size={17} />
          </button>
          <span>{Math.round(zoom * 100)}%</span>
          <button
            aria-label="放大"
            onClick={() => setZoom(Math.min(1.6, zoom + 0.1))}
          >
            <Plus size={17} />
          </button>
          <button aria-label="重設縮放" onClick={() => setZoom(1)}>
            <Maximize size={16} />
          </button>
        </div>
      </div>
      {editing && (
        <Modal title="編輯物件文字" onClose={() => setEditing(null)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const latest =
                elements.find((element) => element.id === editing.id) ||
                editing;
              collaborative.setElement({ ...latest, text: draftText });
              setEditing(null);
            }}
          >
            <textarea
              autoFocus
              rows={6}
              value={draftText}
              onChange={(e) => setDraftText(e.target.value)}
            />
            <div className="business-form-footer">
              <button className="button primary">儲存</button>
            </div>
          </form>
        </Modal>
      )}
      {rename && (
        <RenameModal
          item={resource}
          onClose={() => setRename(false)}
          onDone={setResource}
        />
      )}
      {history && (
        <HistoryModal
          resource={resource}
          onClose={() => setHistory(false)}
          onRestore={(snapshot) =>
            collaborative.restoreElements(snapshot.data?.elements || [])
          }
        />
      )}
    </div>
  );
}

function DocumentsList({ kind }: { kind: "note" | "canvas" }) {
  const resources = useResources(kind),
    [selected, setSelected] = useState<Resource | null>(null),
    [creating, setCreating] = useState(false),
    [name, setName] = useState(""),
    [query, setQuery] = useState(""),
    [remove, setRemove] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  useOpenResource([kind], setSelected, setError);
  if (selected)
    return kind === "note" ? (
      <NoteEditor
        key={selected.id}
        initial={selected}
        onClose={() => {
          setSelected(null);
          void resources.refresh();
        }}
      />
    ) : (
      <CanvasEditor
        key={selected.id}
        initial={selected}
        onClose={() => {
          setSelected(null);
          void resources.refresh();
        }}
      />
    );
  const isNote = kind === "note",
    items = resources.items.filter((item) =>
      item.name.toLowerCase().includes(query.toLowerCase()),
    );
  return (
    <div className="business-page">
      <Header
        eyebrow={isNote ? "一起思考，留下共識" : "從想法到共同方向"}
        title={isNote ? "筆記" : "白板"}
        description={
          isNote
            ? "在同一份文件協同編輯會議記錄、工作說明及想法。"
            : "使用便利貼、圖形及連接線，一起整理想法和規劃。"
        }
        actions={
          <button
            className="button primary"
            onClick={() => {
              setCreating(true);
              setName("");
            }}
          >
            <Plus size={16} />
            {isNote ? "新增筆記" : "新增白板"}
          </button>
        }
      />
      <Banner error={error || resources.error} />
      <div className="business-filterbar">
        <div className="business-search">
          <Search size={17} />
          <input
            placeholder={isNote ? "搜尋筆記" : "搜尋白板"}
            aria-label="搜尋"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <span className="muted">{items.length} 個項目</span>
      </div>
      {!items.length && !resources.loading ? (
        <Empty
          icon={isNote ? <FileText size={32} /> : <LayoutGrid size={32} />}
          title={isNote ? "建立團隊的共同筆記" : "為你的想法留一個空間"}
          detail={
            isNote
              ? "內容自動保存，保留修改歷史；已授權成員可以同時編輯。"
              : "真正可保存的協作白板，與可執行工作流程分開管理。"
          }
          action={
            <button
              className="button primary"
              onClick={() => setCreating(true)}
            >
              <Plus size={16} />
              {isNote ? "建立第一份筆記" : "建立第一個白板"}
            </button>
          }
        />
      ) : (
        <div className="document-card-grid">
          {items.map((item) => (
            <article className={`document-card ${kind}`} key={item.id}>
              <button
                className="document-card-preview"
                onClick={() => setSelected(item)}
              >
                {isNote ? (
                  <>
                    <h4>{item.name}</h4>
                    <div>
                      {String(dataOf(item).content || "空白筆記")
                        .split("\n")
                        .slice(0, 7)
                        .map((line, index) => (
                          <p key={index}>{line || "\u00a0"}</p>
                        ))}
                    </div>
                  </>
                ) : (
                  <svg viewBox="0 0 300 150">
                    <defs>
                      <pattern
                        id={`dots-${item.id}`}
                        width="14"
                        height="14"
                        patternUnits="userSpaceOnUse"
                      >
                        <circle cx="1" cy="1" r=".5" fill="#c9d2de" />
                      </pattern>
                    </defs>
                    <rect
                      width="300"
                      height="150"
                      fill={`url(#dots-${item.id})`}
                    />
                    {(dataOf(item).elements || [])
                      .filter(
                        (element: Data) =>
                          !["connector", "pen"].includes(element.type),
                      )
                      .slice(0, 20)
                      .map((element: Data) => (
                        <g
                          key={element.id}
                          transform={`translate(${element.x / 5},${element.y / 5})`}
                        >
                          <rect
                            width={element.width / 5}
                            height={element.height / 5}
                            fill={element.color || "#fff2ad"}
                            rx="1"
                          />
                          <text x="3" y="9" fontSize="5" fill="#536174">
                            {String(element.text || "").slice(0, 22)}
                          </text>
                        </g>
                      ))}
                  </svg>
                )}
              </button>
              <footer>
                <div>
                  {isNote ? <FileText size={19} /> : <LayoutGrid size={19} />}
                  <span>
                    <button onClick={() => setSelected(item)}>
                      {item.name}
                    </button>
                    <small>更新於 {time(item.updated_at)}</small>
                  </span>
                </div>
                <button
                  className="icon-button"
                  title="刪除"
                  onClick={() => setRemove(item)}
                >
                  <Trash2 size={16} />
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}
      {creating && (
        <Modal
          title={isNote ? "新增筆記" : "新增白板"}
          onClose={() => setCreating(false)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const result = await createResource(
                  kind,
                  name,
                  isNote ? { content: "" } : { elements: [] },
                );
                setSelected(result);
                setCreating(false);
                void resources.refresh();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <FormField label="名稱">
              <input
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={isNote ? "例如：專案會議記錄" : "例如：新服務構思"}
              />
            </FormField>
            <Banner error={error} />
            <div className="business-form-footer">
              <button className="button primary" disabled={busy}>
                {busy ? "建立中…" : "建立"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void resources.refresh()}
        />
      )}
    </div>
  );
}

function HubsPage() {
  const hubs = useResources("hub"),
    files = useResources("file"),
    folders = useResources("folder"),
    notes = useResources("note"),
    [selected, setSelected] = useState<Resource | null>(null),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [name, setName] = useState(""),
    [description, setDescription] = useState(""),
    [ids, setIds] = useState<string[]>([]),
    [remove, setRemove] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [query, setQuery] = useState("");
  const contents = [...files.items, ...folders.items, ...notes.items];
  useOpenResource(["hub"], setSelected, setError);
  function openEdit(item: Resource | "new") {
    setEdit(item);
    setName(item === "new" ? "" : item.name);
    setDescription(item === "new" ? "" : dataOf(item).description || "");
    setIds(item === "new" ? [] : dataOf(item).resource_ids || []);
    setQuery("");
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="讓重要內容更容易找到"
        title={selected?.name || "知識入口"}
        description={
          selected
            ? dataOf(selected).description
            : "按部門、專案或主題，整理公司需要持續查閱的內容。"
        }
        actions={
          <>
            {selected && (
              <button
                className="button secondary"
                onClick={() => setSelected(null)}
              >
                <ArrowLeft size={16} />
                所有入口
              </button>
            )}
            <button
              className="button primary"
              onClick={() => openEdit(selected || "new")}
            >
              {selected ? <Pencil size={16} /> : <Plus size={16} />}
              {selected ? "編輯入口" : "建立入口"}
            </button>
          </>
        }
      />
      <Banner error={error || hubs.error} />
      {selected ? (
        <>
          <div className="hub-cover">
            <div className="hub-cover-pattern" />
            <div>
              <span>公司知識入口</span>
              <h2>{selected.name}</h2>
              <p>{dataOf(selected).description}</p>
            </div>
          </div>
          <div className="hub-section-header">
            <h3>精選內容</h3>
            <span className="muted">只顯示你有權限存取的項目</span>
          </div>
          <div className="hub-content-grid">
            {(dataOf(selected).resource_ids || []).map((id: string) => {
              const item = contents.find((resource) => resource.id === id);
              return item ? (
                <article className="hub-content-card" key={id}>
                  <div className={`hub-content-icon ${item.kind}`}>
                    {item.kind === "folder" ? (
                      <Folder size={26} />
                    ) : (
                      <FileText size={26} />
                    )}
                  </div>
                  <h3>{item.name}</h3>
                  <small>
                    {item.kind === "folder"
                      ? "資料夾"
                      : item.kind === "note"
                        ? "筆記"
                        : dataOf(item).mime || "文件"}
                  </small>
                  {item.kind === "file" ? (
                    <a
                      className="business-text-button"
                      href={`/api/v1/files/${item.id}/preview`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      開啟預覽
                      <ArrowUpRight size={15} />
                    </a>
                  ) : (
                    <p className="hub-note-excerpt">
                      {dataOf(item).content?.slice(0, 180) || "已加入知識入口"}
                    </p>
                  )}
                </article>
              ) : (
                <article className="hub-content-card unavailable" key={id}>
                  <LockIcon />
                  <h3>未能存取此內容</h3>
                  <p>內容可能已移除，或你未有存取權限。</p>
                </article>
              );
            })}
          </div>
          {!(dataOf(selected).resource_ids || []).length && (
            <Empty
              title="為入口加入有用內容"
              detail="挑選公司文件、資料夾及筆記，組織成容易查閱的知識入口。"
              action={
                <button
                  className="button primary"
                  onClick={() => openEdit(selected)}
                >
                  加入內容
                </button>
              }
            />
          )}
        </>
      ) : !hubs.items.length && !hubs.loading ? (
        <Empty
          icon={<LayoutGrid size={32} />}
          title="一個入口，找到團隊需要的內容"
          detail="內容仍保留在原本位置；入口只整理引用，原有權限繼續適用。"
          action={
            <button className="button primary" onClick={() => openEdit("new")}>
              建立知識入口
            </button>
          }
        />
      ) : (
        <div className="hub-card-grid">
          {hubs.items.map((item) => (
            <article className="hub-card" key={item.id}>
              <button
                className="hub-card-cover"
                onClick={() => setSelected(item)}
              >
                <LayoutGrid size={34} />
                <ArrowUpRight size={20} />
              </button>
              <div>
                <h3>
                  <button onClick={() => setSelected(item)}>{item.name}</button>
                </h3>
                <p>{dataOf(item).description || "公司知識入口"}</p>
                <footer>
                  <span>{(dataOf(item).resource_ids || []).length} 個內容</span>
                  <button
                    className="icon-button"
                    title="刪除入口"
                    onClick={() => setRemove(item)}
                  >
                    <Trash2 size={15} />
                  </button>
                </footer>
              </div>
            </article>
          ))}
        </div>
      )}
      {edit && (
        <Modal
          title={edit === "new" ? "建立知識入口" : "編輯知識入口"}
          onClose={() => setEdit(null)}
          wide
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const data = {
                  ...(edit === "new" ? {} : dataOf(edit)),
                  description,
                  resource_ids: ids,
                };
                const result =
                  edit === "new"
                    ? await createResource("hub", name, data)
                    : await updateResource(edit, data, name);
                if (selected) setSelected(result);
                setEdit(null);
                void hubs.refresh();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <FormField label="名稱">
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </FormField>
            <FormField label="說明">
              <textarea
                rows={3}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </FormField>
            <div className="hub-picker-header">
              <strong>選擇內容</strong>
              <span>{ids.length} 個已選</span>
            </div>
            <div className="business-search">
              <Search size={16} />
              <input
                aria-label="搜尋內容"
                placeholder="搜尋文件、資料夾及筆記"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <div className="hub-content-picker">
              {contents
                .filter((item) =>
                  item.name.toLowerCase().includes(query.toLowerCase()),
                )
                .map((item) => (
                  <label className="hub-picker-row" key={item.id}>
                    <input
                      type="checkbox"
                      checked={ids.includes(item.id)}
                      onChange={(e) =>
                        setIds(
                          e.target.checked
                            ? [...ids, item.id]
                            : ids.filter((id) => id !== item.id),
                        )
                      }
                    />
                    {item.kind === "folder" ? (
                      <Folder size={19} />
                    ) : (
                      <FileText size={19} />
                    )}
                    <span>
                      {item.name}
                      <small>{item.kind}</small>
                    </span>
                  </label>
                ))}
              {!contents.length && (
                <p className="muted">先上傳文件或建立筆記，再加入知識入口。</p>
              )}
            </div>
            <Banner error={error} />
            <div className="business-form-footer">
              <button
                type="button"
                className="button secondary"
                onClick={() => setEdit(null)}
              >
                取消
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? "儲存中…" : "儲存入口"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void hubs.refresh()}
        />
      )}
    </div>
  );
}
function LockIcon() {
  return (
    <span className="hub-content-icon">
      <FileText size={25} />
    </span>
  );
}

export function EditorPage({ section }: { section: string }) {
  if (section === "canvas") return <DocumentsList kind="canvas" />;
  if (section === "hubs") return <HubsPage />;
  return <DocumentsList kind="note" />;
}
