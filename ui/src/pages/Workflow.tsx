import { useEffect, useState, type FormEvent } from "react";
import {
  Activity,
  ArrowLeft,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Code2,
  Copy,
  GitBranch,
  Link2,
  LockKeyhole,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  Trash2,
  Webhook,
  X,
  Zap,
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

type Node = {
  id: string;
  type: string;
  label: string;
  config: Data;
  position?: { x: number; y: number };
};
type Edge = { source: string; target: string; source_handle?: string };
const NODE_TYPES: {
  id: string;
  label: string;
  icon: typeof Zap;
  detail: string;
  category: string;
}[] = [
  {
    id: "set",
    label: "設定變數",
    icon: Code2,
    detail: "將輸入或前一步結果保存為變數",
    category: "資料",
  },
  {
    id: "condition",
    label: "條件分支",
    icon: GitBranch,
    detail: "按資料條件選擇下一步",
    category: "邏輯",
  },
  {
    id: "http",
    label: "HTTP 請求",
    icon: Link2,
    detail: "經已授權連接器呼叫業務 API",
    category: "整合",
  },
  {
    id: "agent",
    label: "外部 AI 員工",
    icon: Bot,
    detail: "向已接入的外部員工派發工作",
    category: "整合",
  },
  {
    id: "task",
    label: "建立任務",
    icon: CheckCircle2,
    detail: "建立可追蹤的工作待辦",
    category: "工作",
  },
  {
    id: "approval",
    label: "人工審批",
    icon: ShieldCheck,
    detail: "等待真人批准或拒絕後繼續",
    category: "工作",
  },
  {
    id: "delay",
    label: "等待",
    icon: Clock3,
    detail: "等待指定時間後繼續執行",
    category: "邏輯",
  },
  {
    id: "parallel",
    label: "平行分支",
    icon: GitBranch,
    detail: "同時啟動多條執行路徑",
    category: "邏輯",
  },
  {
    id: "merge",
    label: "合併分支",
    icon: GitBranch,
    detail: "合併前面的執行路徑",
    category: "邏輯",
  },
  {
    id: "foreach",
    label: "逐項處理",
    icon: RefreshCw,
    detail: "對列表內每個項目執行步驟",
    category: "邏輯",
  },
];
const initialNodes: Node[] = [
  { id: "start", type: "trigger", label: "工作流程開始", config: {} },
];
const parseJson = (value: string, label: string) => {
  try {
    return JSON.parse(value || "{}");
  } catch {
    throw new Error(`${label}必須為有效 JSON。`);
  }
};
const statusLabel = (value?: string) =>
  ({
    draft: "草稿",
    active: "已啟用",
    inactive: "已停用",
    queued: "排隊中",
    running: "執行中",
    waiting: "等待處理",
    completed: "已完成",
    failed: "失敗",
    cancelled: "已取消",
    cancel_requested: "取消請求中",
    waiting_approval: "等待審批",
    unknown: "結果待確認",
  })[value || ""] ||
  value ||
  "草稿";
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

function WorkflowList() {
  const resources = useResources("workflow"),
    [selected, setSelected] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [run, setRun] = useState<Resource | null>(null),
    [error, setError] = useState("");
  useOpenResource(["workflow"], setSelected, setError);
  if (selected)
    return (
      <WorkflowEditor
        key={selected === "new" ? "new" : selected.id}
        workflow={selected === "new" ? undefined : selected}
        onClose={() => {
          setSelected(null);
          void resources.refresh();
        }}
      />
    );
  return (
    <div className="business-page">
      <Header
        eyebrow="讓工作持續前進"
        title="工作流程"
        description="將人員、資料及系統連成可追蹤、可恢復的工作流程。"
        actions={
          <button className="button primary" onClick={() => setSelected("new")}>
            <Plus size={16} />
            建立工作流程
          </button>
        }
      />
      <Banner error={error || resources.error} />
      {!resources.items.length && !resources.loading ? (
        <Empty
          icon={<GitBranch size={32} />}
          title="讓重複工作有一致的處理方式"
          detail="設定觸發條件、操作步驟及審批規則。流程發佈後，每次執行都有完整記錄。"
          action={
            <button
              className="button primary"
              onClick={() => setSelected("new")}
            >
              <Plus size={16} />
              建立第一個工作流程
            </button>
          }
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>工作流程</th>
                <th>觸發方式</th>
                <th>狀態</th>
                <th>步驟</th>
                <th>最後更新</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {resources.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      className="business-cell-link"
                      onClick={() => setSelected(item)}
                    >
                      <span className="workflow-table-icon">
                        <GitBranch size={20} />
                      </span>
                      <span>
                        {item.name}
                        <small>{dataOf(item).description || "工作流程"}</small>
                      </span>
                    </button>
                  </td>
                  <td>
                    {(
                      {
                        manual: "手動啟動",
                        schedule: "定時排程",
                        event: "事件觸發",
                      } as Data
                    )[dataOf(item).trigger?.type] || "手動啟動"}
                  </td>
                  <td>
                    <span
                      className={`business-status ${dataOf(item).status === "active" || dataOf(item).active ? "completed" : "draft"}`}
                    >
                      {dataOf(item).active || dataOf(item).status === "active"
                        ? "已啟用"
                        : "草稿"}
                    </span>
                  </td>
                  <td>{(dataOf(item).nodes || []).length}</td>
                  <td className="muted">{time(item.updated_at)}</td>
                  <td>
                    <div className="business-actions">
                      <button
                        className="button secondary compact"
                        onClick={() => setRun(item)}
                      >
                        <Play size={14} />
                        執行
                      </button>
                      <button
                        className="icon-button"
                        title="編輯工作流程"
                        onClick={() => setSelected(item)}
                      >
                        <Settings2 size={16} />
                      </button>
                      <button
                        className="icon-button"
                        title="刪除工作流程"
                        onClick={() => setRemove(item)}
                      >
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void resources.refresh()}
        />
      )}
      {run && (
        <RunDialog
          workflow={run}
          onClose={() => setRun(null)}
          onDone={() => void resources.refresh()}
        />
      )}
    </div>
  );
}

function WorkflowEditor({
  workflow,
  onClose,
}: {
  workflow?: Resource;
  onClose: () => void;
}) {
  const [saved, setSaved] = useState<Resource | undefined>(workflow),
    [name, setName] = useState(workflow?.name || "未命名工作流程"),
    [description, setDescription] = useState(
      dataOf(workflow).description || "",
    ),
    [trigger, setTrigger] = useState<Data>(
      clone(
        dataOf(workflow).trigger || {
          type: "manual",
          timezone: "Asia/Hong_Kong",
        },
      ),
    ),
    [nodes, setNodes] = useState<Node[]>(
      clone(dataOf(workflow).nodes || initialNodes),
    ),
    [edges, setEdges] = useState<Edge[]>(clone(dataOf(workflow).edges || [])),
    [selected, setSelected] = useState("start");
  const [adding, setAdding] = useState(false),
    [edgeModal, setEdgeModal] = useState(false),
    [edgeDraft, setEdgeDraft] = useState<Edge>({
      source: "start",
      target: "",
      source_handle: "",
    }),
    [error, setError] = useState(""),
    [info, setInfo] = useState(""),
    [busy, setBusy] = useState(false),
    [run, setRun] = useState(false);
  const agents = useResources("agent"),
    connectors = useResources("connector");
  const selectedNode = nodes.find((node) => node.id === selected),
    typeDef = NODE_TYPES.find((t) => t.id === selectedNode?.type);
  function changeNode(patch: Partial<Node>) {
    setNodes(
      nodes.map((node) =>
        node.id === selected ? { ...node, ...patch } : node,
      ),
    );
  }
  function config(key: string, value: any) {
    if (selectedNode)
      changeNode({ config: { ...selectedNode.config, [key]: value } });
  }
  function addNode(type: string) {
    const definition = NODE_TYPES.find((t) => t.id === type)!;
    const id = `${type}_${Date.now().toString(36)}`;
    const baseConfig: Data =
      type === "task"
        ? { title: "跟進工作" }
        : type === "approval"
          ? { title: "請審批", message: "" }
          : type === "delay"
            ? { seconds: 60 }
            : type === "condition"
              ? { path: "input.value", operator: "eq", value: "" }
              : type === "set"
                ? { values: {} }
                : type === "http"
                  ? { method: "GET", path: "/", body: {}, idempotent: false }
                  : type === "agent"
                    ? { task: "", input: {} }
                    : type === "foreach"
                      ? { items: "${input.items}", steps: [] }
                      : {};
    setNodes([
      ...nodes,
      { id, type, label: definition.label, config: baseConfig },
    ]);
    if (nodes.length) {
      const source = selectedNode || nodes[nodes.length - 1];
      setEdges([
        ...edges,
        {
          source: source.id,
          target: id,
          ...(source.type === "condition" ? { source_handle: "true" } : {}),
        },
      ]);
    }
    setSelected(id);
    setAdding(false);
  }
  async function save() {
    setBusy(true);
    setError("");
    try {
      const data = { ...dataOf(saved), description, trigger, nodes, edges };
      const result = saved
        ? await updateResource(saved, data, name)
        : await createResource("workflow", name, data);
      setSaved(result);
      setInfo("草稿已儲存。已發佈及正在執行的版本不會因此改變。");
      return result;
    } catch (e) {
      setError(errorText(e));
      return undefined;
    } finally {
      setBusy(false);
    }
  }
  async function activate() {
    const item = await save();
    if (!item) return;
    setBusy(true);
    try {
      await api(`/workflows/${item.id}/activate`, {
        method: "POST",
        body: "{}",
      });
      const latest = await api<Resource>(`/resource/${item.id}`);
      setSaved(latest);
      setInfo("工作流程已驗證及啟用。");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const positions = new Map(
    nodes.map((node, i) => [
      node.id,
      node.position || { x: 110 + (i % 2 === 0 ? 0 : 35), y: 38 + i * 118 },
    ]),
  );
  return (
    <div className="workflow-editor">
      <div className="workflow-editor-top">
        <button
          className="icon-button"
          aria-label="返回工作流程"
          onClick={onClose}
        >
          <ArrowLeft size={20} />
        </button>
        <div className="workflow-name">
          <input
            aria-label="工作流程名稱"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <span>
            <span
              className={`business-status ${dataOf(saved).active ? "completed" : "draft"}`}
            >
              {dataOf(saved).active ? "已啟用" : "草稿"}
            </span>
            {saved && `第 ${saved.version} 版`}
          </span>
        </div>
        <div className="business-actions">
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void save()}
          >
            <Save size={16} />
            儲存草稿
          </button>
          {dataOf(saved).active && saved ? (
            <button
              className="button secondary"
              disabled={busy}
              onClick={async () => {
                try {
                  await api(`/workflows/${saved.id}/deactivate`, {
                    method: "POST",
                    body: "{}",
                  });
                  setSaved(await api(`/resource/${saved.id}`));
                  setInfo("已停用新觸發，現有執行實例會保留。");
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              <Pause size={16} />
              停用
            </button>
          ) : (
            <button
              className="button primary"
              disabled={busy || !name.trim()}
              onClick={() => void activate()}
            >
              <Zap size={16} />
              啟用流程
            </button>
          )}
          <button
            className="button secondary"
            disabled={!saved || busy}
            onClick={() => setRun(true)}
          >
            <Play size={16} />
            執行
          </button>
        </div>
      </div>
      <Banner
        error={error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      <div className="workflow-editor-layout">
        <aside className="workflow-palette">
          <h3>流程步驟</h3>
          <p>選擇步驟加入流程</p>
          {NODE_TYPES.map((type) => (
            <button key={type.id} onClick={() => addNode(type.id)}>
              <span className={`node-icon ${type.id}`}>
                <type.icon size={18} />
              </span>
              <span>
                <strong>{type.label}</strong>
                <small>{type.category}</small>
              </span>
              <Plus size={15} />
            </button>
          ))}
          <div className="workflow-palette-note">
            <LockKeyhole size={17} />
            <p>每一步都沿用執行身份的資料及工具權限。</p>
          </div>
        </aside>
        <div className="workflow-graph">
          <div className="workflow-graph-tools">
            <span>
              {nodes.length} 個步驟 · {edges.length} 條連線
            </span>
            <button
              className="button secondary compact"
              onClick={() => setEdgeModal(true)}
            >
              <Link2 size={15} />
              加入連線
            </button>
          </div>
          <div className="workflow-graph-scroll">
            <div
              className="workflow-graph-inner"
              style={{ minHeight: Math.max(480, nodes.length * 118 + 80) }}
            >
              <svg
                className="workflow-connections"
                width="100%"
                height={Math.max(480, nodes.length * 118 + 80)}
              >
                <defs>
                  <marker
                    id="workflow-arrow"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="5"
                    markerHeight="5"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="#96a3b3" />
                  </marker>
                </defs>
                {edges.map((edge, index) => {
                  const from = positions.get(edge.source),
                    to = positions.get(edge.target);
                  if (!from || !to) return null;
                  const x1 = from.x + 125,
                    y1 = from.y + 76,
                    x2 = to.x + 125,
                    y2 = to.y;
                  return (
                    <g key={index}>
                      <path
                        d={`M${x1},${y1} C${x1},${y1 + 28} ${x2},${y2 - 28} ${x2},${y2}`}
                        fill="none"
                        stroke={
                          edge.source_handle === "false" ? "#d79b7a" : "#96a3b3"
                        }
                        strokeWidth="1.5"
                        markerEnd="url(#workflow-arrow)"
                      />
                      {edge.source_handle && (
                        <text
                          x={(x1 + x2) / 2 + 10}
                          y={(y1 + y2) / 2}
                          fill="#69788d"
                          fontSize="11"
                        >
                          {edge.source_handle === "true" ? "符合" : "不符合"}
                        </text>
                      )}
                    </g>
                  );
                })}
              </svg>
              {nodes.map((node) => {
                const position = positions.get(node.id)!,
                  definition = NODE_TYPES.find((t) => t.id === node.type),
                  Icon = definition?.icon || Zap;
                return (
                  <button
                    key={node.id}
                    className={`workflow-node ${selected === node.id ? "selected" : ""}`}
                    style={{ left: position.x, top: position.y }}
                    onClick={() => setSelected(node.id)}
                    draggable
                    onDragEnd={(event) => {
                      if (event.clientX === 0 && event.clientY === 0) return;
                      const graph =
                        event.currentTarget.parentElement!.getBoundingClientRect();
                      const next = {
                        x: Math.max(10, event.clientX - graph.left - 100),
                        y: Math.max(10, event.clientY - graph.top - 30),
                      };
                      setNodes(
                        nodes.map((n) =>
                          n.id === node.id ? { ...n, position: next } : n,
                        ),
                      );
                    }}
                  >
                    <span className={`node-icon ${node.type}`}>
                      <Icon size={20} />
                    </span>
                    <span>
                      <small>
                        {node.type === "trigger"
                          ? "觸發器"
                          : definition?.label || node.type}
                      </small>
                      <strong>{node.label}</strong>
                    </span>
                    <ChevronRight size={15} />
                  </button>
                );
              })}
              <button
                className="workflow-add-step"
                style={{ top: nodes.length * 118 + 26, left: 208 }}
                onClick={() => setAdding(true)}
                aria-label="加入步驟"
              >
                <Plus size={20} />
              </button>
            </div>
          </div>
        </div>
        <aside className="workflow-inspector">
          {selectedNode && (
            <>
              <header>
                <span className={`node-icon ${selectedNode.type}`}>
                  {typeDef ? <typeDef.icon size={19} /> : <Zap size={19} />}
                </span>
                <div>
                  <h3>
                    {selectedNode.type === "trigger"
                      ? "設定觸發器"
                      : typeDef?.label || selectedNode.type}
                  </h3>
                  <small>{selectedNode.id}</small>
                </div>
              </header>
              <FormField label="步驟名稱">
                <input
                  value={selectedNode.label}
                  onChange={(e) => changeNode({ label: e.target.value })}
                />
              </FormField>
              {selectedNode.type === "trigger" ? (
                <>
                  <FormField label="觸發方式">
                    <select
                      value={trigger.type}
                      onChange={(e) =>
                        setTrigger({ ...trigger, type: e.target.value })
                      }
                    >
                      <option value="manual">手動啟動</option>
                      <option value="schedule">定時排程</option>
                      <option value="event">資源事件</option>
                    </select>
                  </FormField>
                  {trigger.type === "schedule" && (
                    <>
                      <FormField
                        label="Cron 排程"
                        hint="包含秒數，例如：0 0 9 * * *（每日 09:00）"
                      >
                        <input
                          value={trigger.cron || ""}
                          onChange={(e) =>
                            setTrigger({ ...trigger, cron: e.target.value })
                          }
                        />
                      </FormField>
                      <FormField label="時區">
                        <input
                          value={trigger.timezone || "Asia/Hong_Kong"}
                          onChange={(e) =>
                            setTrigger({ ...trigger, timezone: e.target.value })
                          }
                        />
                      </FormField>
                    </>
                  )}
                  {trigger.type === "event" && (
                    <FormField label="事件">
                      <select
                        value={trigger.event || "file.created"}
                        onChange={(e) =>
                          setTrigger({ ...trigger, event: e.target.value })
                        }
                      >
                        {[
                          "file.created",
                          "file.updated",
                          "submission.created",
                          "record.created",
                          "record.updated",
                          "task.completed",
                          "sign.completed",
                        ].map((value) => (
                          <option key={value}>{value}</option>
                        ))}
                      </select>
                    </FormField>
                  )}
                  <FormField label="流程說明">
                    <textarea
                      rows={3}
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                    />
                  </FormField>
                </>
              ) : (
                <NodeConfig
                  node={selectedNode}
                  config={config}
                  agents={agents.items}
                  connectors={connectors.items}
                />
              )}
              <div className="workflow-node-connections">
                <h4>下一步</h4>
                {edges
                  .filter((edge) => edge.source === selectedNode.id)
                  .map((edge, index) => (
                    <div key={index}>
                      <span>
                        {nodes.find((n) => n.id === edge.target)?.label ||
                          edge.target}
                        {edge.source_handle &&
                          ` · ${edge.source_handle === "true" ? "符合" : "不符合"}`}
                      </span>
                      <button
                        className="icon-button"
                        aria-label="移除連線"
                        onClick={() =>
                          setEdges(edges.filter((e) => e !== edge))
                        }
                      >
                        <X size={13} />
                      </button>
                    </div>
                  ))}
                <button
                  className="business-text-button"
                  onClick={() => {
                    setEdgeDraft({
                      source: selectedNode.id,
                      target: "",
                      source_handle:
                        selectedNode.type === "condition" ? "true" : "",
                    });
                    setEdgeModal(true);
                  }}
                >
                  <Plus size={14} />
                  連接步驟
                </button>
              </div>
              {selectedNode.type !== "trigger" && (
                <button
                  className="button danger"
                  onClick={() => {
                    setNodes(nodes.filter((n) => n.id !== selectedNode.id));
                    setEdges(
                      edges.filter(
                        (e) =>
                          e.source !== selectedNode.id &&
                          e.target !== selectedNode.id,
                      ),
                    );
                    setSelected("start");
                  }}
                >
                  <Trash2 size={15} />
                  移除步驟
                </button>
              )}
            </>
          )}
        </aside>
      </div>
      {adding && (
        <Modal title="加入流程步驟" onClose={() => setAdding(false)}>
          <div className="workflow-add-grid">
            {NODE_TYPES.map((type) => (
              <button key={type.id} onClick={() => addNode(type.id)}>
                <type.icon size={21} />
                <strong>{type.label}</strong>
                <small>{type.detail}</small>
              </button>
            ))}
          </div>
        </Modal>
      )}
      {edgeModal && (
        <Modal title="連接流程步驟" onClose={() => setEdgeModal(false)}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (edgeDraft.source === edgeDraft.target) {
                setError("步驟不可直接連接自己。");
                return;
              }
              const edge = {
                ...edgeDraft,
                source_handle:
                  nodes.find((node) => node.id === edgeDraft.source)?.type ===
                  "condition"
                    ? edgeDraft.source_handle || "true"
                    : undefined,
              };
              if (
                !edges.some(
                  (existing) =>
                    existing.source === edge.source &&
                    existing.target === edge.target &&
                    existing.source_handle === edge.source_handle,
                )
              )
                setEdges([...edges, edge]);
              setEdgeModal(false);
            }}
          >
            <FormField label="由">
              <select
                value={edgeDraft.source}
                onChange={(e) =>
                  setEdgeDraft({
                    ...edgeDraft,
                    source: e.target.value,
                    target:
                      edgeDraft.target === e.target.value
                        ? ""
                        : edgeDraft.target,
                    source_handle:
                      nodes.find((node) => node.id === e.target.value)?.type ===
                      "condition"
                        ? "true"
                        : "",
                  })
                }
              >
                {nodes.map((n) => (
                  <option value={n.id} key={n.id}>
                    {n.label}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField label="前往">
              <select
                required
                value={edgeDraft.target}
                onChange={(e) =>
                  setEdgeDraft({ ...edgeDraft, target: e.target.value })
                }
              >
                <option value="">選擇步驟</option>
                {nodes
                  .filter((n) => n.id !== edgeDraft.source)
                  .map((n) => (
                    <option value={n.id} key={n.id}>
                      {n.label}
                    </option>
                  ))}
              </select>
            </FormField>
            {nodes.find((n) => n.id === edgeDraft.source)?.type ===
              "condition" && (
              <FormField label="條件結果">
                <select
                  value={edgeDraft.source_handle}
                  onChange={(e) =>
                    setEdgeDraft({
                      ...edgeDraft,
                      source_handle: e.target.value,
                    })
                  }
                >
                  <option value="true">符合條件</option>
                  <option value="false">不符合條件</option>
                </select>
              </FormField>
            )}
            <div className="business-form-footer">
              <button className="button primary">建立連線</button>
            </div>
          </form>
        </Modal>
      )}
      {run && saved && (
        <RunDialog
          workflow={saved}
          onClose={() => setRun(false)}
          onDone={() => {}}
        />
      )}
    </div>
  );
}

function JsonInput({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: any;
  onChange: (value: any) => void;
  hint?: string;
}) {
  const [raw, setRaw] = useState(JSON.stringify(value || {}, null, 2)),
    [invalid, setInvalid] = useState(false);
  useEffect(() => {
    setRaw(JSON.stringify(value || {}, null, 2));
    setInvalid(false);
  }, [JSON.stringify(value)]);
  return (
    <FormField
      label={label}
      hint={invalid ? "JSON 格式未完成；修正後會自動更新。" : hint}
    >
      <textarea
        className={`business-code ${invalid ? "invalid" : ""}`}
        rows={5}
        value={raw}
        onChange={(e) => {
          setRaw(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setInvalid(false);
          } catch {
            setInvalid(true);
          }
        }}
      />
    </FormField>
  );
}

function NodeConfig({
  node,
  config,
  agents,
  connectors,
}: {
  node: Node;
  config: (key: string, value: any) => void;
  agents: Resource[];
  connectors: Resource[];
}) {
  const c = node.config;
  switch (node.type) {
    case "task":
      return (
        <>
          <FormField label="任務名稱">
            <input
              value={c.title || ""}
              onChange={(e) => config("title", e.target.value)}
            />
          </FormField>
          <FormField label="指派成員 ID">
            <input
              value={c.assignee_id || ""}
              onChange={(e) => config("assignee_id", e.target.value)}
              placeholder="可留空"
            />
          </FormField>
        </>
      );
    case "approval":
      return (
        <>
          <FormField label="審批標題">
            <input
              value={c.title || ""}
              onChange={(e) => config("title", e.target.value)}
            />
          </FormField>
          <FormField label="說明">
            <textarea
              rows={4}
              value={c.message || ""}
              onChange={(e) => config("message", e.target.value)}
            />
          </FormField>
          <p className="inspector-hint">流程會等待真人在執行記錄作出決定。</p>
        </>
      );
    case "delay":
      return (
        <FormField label="等待秒數" hint="最多 86,400 秒">
          <input
            type="number"
            min="0"
            max="86400"
            value={c.seconds ?? 60}
            onChange={(e) => config("seconds", Number(e.target.value))}
          />
        </FormField>
      );
    case "condition":
      return (
        <>
          <FormField label="資料路徑">
            <input
              value={c.path || ""}
              placeholder="input.amount"
              onChange={(e) => config("path", e.target.value)}
            />
          </FormField>
          <FormField label="比較方式">
            <select
              value={c.operator || "eq"}
              onChange={(e) => config("operator", e.target.value)}
            >
              {[
                ["eq", "等於"],
                ["ne", "不等於"],
                ["gt", "大於"],
                ["gte", "大於或等於"],
                ["lt", "小於"],
                ["lte", "小於或等於"],
                ["contains", "包含"],
                ["exists", "存在"],
              ].map(([id, label]) => (
                <option value={id} key={id}>
                  {label}
                </option>
              ))}
            </select>
          </FormField>
          <FormField label="比較值">
            <input
              value={
                typeof c.value === "object"
                  ? JSON.stringify(c.value)
                  : (c.value ?? "")
              }
              onChange={(e) => {
                let value: any = e.target.value;
                try {
                  value = JSON.parse(e.target.value);
                } catch {}
                config("value", value);
              }}
            />
          </FormField>
        </>
      );
    case "set":
      return (
        <JsonInput
          label="變數"
          value={c.values || {}}
          onChange={(value) => config("values", value)}
          hint={"可使用 ${input.field} 引用輸入資料。"}
        />
      );
    case "http":
      return (
        <>
          <FormField label="連接器">
            <select
              value={c.connector_id || ""}
              onChange={(e) => config("connector_id", e.target.value)}
            >
              <option value="">選擇已授權連接器</option>
              {connectors.map((a) => (
                <option value={a.id} key={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </FormField>
          <div className="business-form-grid">
            <FormField label="方法">
              <select
                value={c.method || "GET"}
                onChange={(e) => config("method", e.target.value)}
              >
                {["GET", "POST", "PUT", "PATCH", "DELETE"].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </FormField>
            <FormField label="路徑">
              <input
                value={c.path || "/"}
                onChange={(e) => config("path", e.target.value)}
              />
            </FormField>
          </div>
          <JsonInput
            label="請求內容"
            value={c.body || {}}
            onChange={(value) => config("body", value)}
          />
          <label className="business-check">
            <input
              type="checkbox"
              checked={!!c.idempotent}
              onChange={(e) => config("idempotent", e.target.checked)}
            />
            目標接口支援冪等重試
          </label>
        </>
      );
    case "agent":
      return (
        <>
          <FormField label="外部 AI 員工">
            <select
              value={c.agent_id || ""}
              onChange={(e) => config("agent_id", e.target.value)}
            >
              <option value="">選擇已接入員工</option>
              {agents.map((a) => (
                <option value={a.id} key={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </FormField>
          <FormField
            label="工作能力"
            hint="只能呼叫這位外部員工已宣告的能力。工作細節放在輸入資料。"
          >
            <select
              value={c.task || ""}
              onChange={(e) => config("task", e.target.value)}
            >
              <option value="">選擇能力</option>
              {(
                dataOf(agents.find((agent) => agent.id === c.agent_id))
                  .capabilities || []
              ).map((capability: string) => (
                <option key={capability}>{capability}</option>
              ))}
            </select>
          </FormField>
          <JsonInput
            label="輸入資料"
            value={c.input || {}}
            onChange={(value) => config("input", value)}
          />
        </>
      );
    case "foreach":
      return (
        <>
          <FormField label="列表來源">
            <input
              value={c.items || "${input.items}"}
              onChange={(e) => config("items", e.target.value)}
            />
          </FormField>
          <JsonInput
            label="逐項步驟定義"
            value={c.steps || []}
            onChange={(value) => config("steps", value)}
            hint="步驟使用相同的 id、type、label、config 結構。"
          />
        </>
      );
    default:
      return (
        <p className="inspector-hint">使用連線設定要執行的分支及合流位置。</p>
      );
  }
}

function RunDialog({
  workflow,
  onClose,
  onDone,
}: {
  workflow: Resource;
  onClose: () => void;
  onDone: () => void;
}) {
  const [input, setInput] = useState("{}"),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<Resource | null>(null);
  return (
    <Modal title={`執行「${workflow.name}」`} onClose={onClose}>
      {result ? (
        <>
          <div className="run-created">
            <CheckCircle2 size={32} />
            <h3>執行已建立</h3>
            <p>每一步結果會持久保存，可在「執行記錄」追蹤。</p>
            <code>{result.id}</code>
            <span className={`business-status ${dataOf(result).status}`}>
              {statusLabel(dataOf(result).status)}
            </span>
          </div>
          <div className="business-form-footer">
            <button className="button primary" onClick={onClose}>
              完成
            </button>
          </div>
        </>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            try {
              const value = parseJson(input, "輸入資料");
              const result = await api<Resource>(
                `/workflows/${workflow.id}/run`,
                {
                  method: "POST",
                  body: JSON.stringify({
                    input: value,
                    idempotency_key: crypto.randomUUID(),
                  }),
                },
              );
              setResult(result);
              onDone();
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <p>
            使用已發佈版本建立一次新的執行。未啟用的流程需要先完成驗證及啟用。
          </p>
          <FormField label="輸入資料（JSON）">
            <textarea
              className="business-code"
              rows={8}
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </FormField>
          <Banner error={error} />
          <div className="business-form-footer">
            <button
              className="button secondary"
              type="button"
              onClick={onClose}
            >
              取消
            </button>
            <button className="button primary" disabled={busy}>
              <Play size={16} />
              {busy ? "建立執行中…" : "開始執行"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

function RunsPage() {
  const runs = useResources("workflow_run"),
    [selected, setSelected] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [filter, setFilter] = useState("all");
  useOpenResource(["workflow_run", "run"], setSelected, setError);
  useEffect(() => {
    if (!selected) return;
    const timer = window.setInterval(async () => {
      try {
        setSelected(await api<Resource>(`/runs/${selected.id}`));
      } catch {}
    }, 4000);
    return () => window.clearInterval(timer);
  }, [selected?.id]);
  const filtered = runs.items.filter(
    (item) => filter === "all" || dataOf(item).status === filter,
  );
  return (
    <div className="business-page">
      <Header
        eyebrow="每一步都有記錄"
        title="執行記錄"
        description="追蹤流程進度、審批、輸入輸出及失敗原因。"
        actions={
          <button
            className="button secondary"
            onClick={() => void runs.refresh()}
          >
            <RefreshCw size={16} />
            重新整理
          </button>
        }
      />
      <Banner error={error || runs.error} />
      <div className="business-viewbar">
        <div className="business-view-tabs">
          {[
            ["all", "全部"],
            ["running", "執行中"],
            ["waiting", "等待"],
            ["completed", "已完成"],
            ["failed", "失敗"],
          ].map(([id, label]) => (
            <button
              className={filter === id ? "active" : ""}
              key={id}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="muted">{filtered.length} 次執行</span>
      </div>
      {!filtered.length && !runs.loading ? (
        <Empty
          icon={<Activity size={30} />}
          title="流程執行會顯示在這裏"
          detail="建立及啟用工作流程後，每次觸發都會保存進度及結果。"
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>執行</th>
                <th>狀態</th>
                <th>進度</th>
                <th>建立時間</th>
                <th>最後更新</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
                const steps = dataOf(item).steps || [];
                return (
                  <tr
                    key={item.id}
                    onClick={() => setSelected(item)}
                    className="clickable"
                  >
                    <td>
                      <button className="business-cell-link">
                        <Activity size={18} />
                        {item.name}
                        <small>{item.id.slice(0, 8)}</small>
                      </button>
                    </td>
                    <td>
                      <span
                        className={`business-status ${dataOf(item).status}`}
                      >
                        {statusLabel(dataOf(item).status)}
                      </span>
                    </td>
                    <td>
                      {
                        steps.filter((s: Data) => s.status === "completed")
                          .length
                      }{" "}
                      / {steps.length} 步
                    </td>
                    <td>{time(item.created_at)}</td>
                    <td>{time(item.updated_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {selected && (
        <Modal title={selected.name} onClose={() => setSelected(null)} wide>
          <div className="run-detail-summary">
            <span className={`business-status ${dataOf(selected).status}`}>
              {statusLabel(dataOf(selected).status)}
            </span>
            <code>{selected.id}</code>
            <span>{time(selected.created_at)}</span>
          </div>
          {dataOf(selected).error && (
            <Banner error={String(dataOf(selected).error)} />
          )}
          <div className="run-timeline">
            {(dataOf(selected).steps || []).map((step: Data, index: number) => (
              <section key={step.node_id || index}>
                <div className={`run-step-marker ${step.status}`}>
                  {step.status === "completed" ? (
                    <Check size={14} />
                  ) : (
                    index + 1
                  )}
                </div>
                <div className="run-step-content">
                  <header>
                    <strong>{step.label || step.node_id}</strong>
                    <span className={`business-status ${step.status}`}>
                      {statusLabel(step.status)}
                    </span>
                  </header>
                  {step.error && <Banner error={String(step.error)} />}
                  {step.output !== undefined && (
                    <details>
                      <summary>查看輸出</summary>
                      <pre>{JSON.stringify(step.output, null, 2)}</pre>
                    </details>
                  )}
                  {["waiting", "waiting_approval"].includes(step.status) && (
                    <div className="business-actions">
                      <button
                        className="button primary"
                        onClick={async () => {
                          try {
                            await api(`/runs/${selected.id}/approve`, {
                              method: "POST",
                              body: JSON.stringify({
                                node_id: step.node_id,
                                approved: true,
                              }),
                            });
                            setSelected(await api(`/runs/${selected.id}`));
                            void runs.refresh();
                          } catch (e) {
                            setError(errorText(e));
                          }
                        }}
                      >
                        批准
                      </button>
                      <button
                        className="button danger"
                        onClick={async () => {
                          try {
                            await api(`/runs/${selected.id}/approve`, {
                              method: "POST",
                              body: JSON.stringify({
                                node_id: step.node_id,
                                approved: false,
                              }),
                            });
                            setSelected(await api(`/runs/${selected.id}`));
                            void runs.refresh();
                          } catch (e) {
                            setError(errorText(e));
                          }
                        }}
                      >
                        拒絕
                      </button>
                    </div>
                  )}
                </div>
              </section>
            ))}
          </div>
          <details className="run-data">
            <summary>輸入及變數</summary>
            <pre>
              {JSON.stringify(
                {
                  input: dataOf(selected).input,
                  variables: dataOf(selected).variables,
                },
                null,
                2,
              )}
            </pre>
          </details>
        </Modal>
      )}
    </div>
  );
}

function IntegrationModal({
  item,
  kind,
  onClose,
  onDone,
}: {
  item?: Resource;
  kind: "agent" | "connector";
  onClose: () => void;
  onDone: () => void;
}) {
  const d = dataOf(item),
    [name, setName] = useState(item?.name || ""),
    [endpoint, setEndpoint] = useState(d.endpoint || ""),
    [hosts, setHosts] = useState<string>((d.allowed_hosts || []).join(", ")),
    [methods, setMethods] = useState<string[]>(
      d.allowed_methods || ["GET", "HEAD"],
    ),
    [pathPrefixes, setPathPrefixes] = useState<string>(
      (d.allowed_path_prefixes || ["/"]).join(", "),
    ),
    [capabilities, setCapabilities] = useState<string>(
      (d.capabilities || []).join(", "),
    ),
    [scopes, setScopes] = useState<string>(
      (d.scopes || ["content:read"]).join(", "),
    ),
    [secret, setSecret] = useState(""),
    [callbackSecret, setCallbackSecret] = useState(""),
    [privateNetwork, setPrivateNetwork] = useState(!!d.allow_private_network),
    [timeout, setTimeout] = useState(d.timeout_seconds || 30),
    [idempotent, setIdempotent] = useState(!!d.idempotent),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const url = new URL(endpoint);
      const data: Data = {
        ...d,
        endpoint,
        allowed_hosts: hosts
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        capabilities: capabilities
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        scopes: scopes
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        allow_private_network: privateNetwork,
        allowed_methods: methods,
        allowed_path_prefixes: pathPrefixes
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        timeout_seconds: Number(timeout),
        idempotent,
      };
      if (!data.allowed_hosts.length) data.allowed_hosts = [url.hostname];
      if (secret) data.secret = secret;
      if (callbackSecret) data.callback_secret = callbackSecret;
      if (item) await updateResource(item, data, name);
      else await createResource(kind, name, data);
      onDone();
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`${item ? "設定" : "接入"}${kind === "agent" ? "外部 AI 員工" : "連接器"}`}
      onClose={onClose}
      wide
    >
      <form onSubmit={submit}>
        <div className="integration-boundary">
          <ShieldCheck size={19} />
          <p>
            {kind === "agent"
              ? "這裏只註冊外部員工接口及授權範圍。員工程式由你或供應商獨立運行。"
              : "請求只會傳送到允許的主機。連接憑證在伺服器加密保存。"}
          </p>
        </div>
        <FormField label="名稱">
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <FormField label="服務端點">
          <input
            required
            type="url"
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            placeholder="https://worker.example.com"
          />
        </FormField>
        <FormField label="允許的主機" hint="以逗號分隔；留空使用端點主機。">
          <input value={hosts} onChange={(e) => setHosts(e.target.value)} />
        </FormField>
        {kind === "connector" && (
          <>
            <div className="business-label">允許的 HTTP 方法</div>
            <div className="business-inline-checks">
              {["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"].map(
                (method) => (
                  <label className="business-check" key={method}>
                    <input
                      type="checkbox"
                      checked={methods.includes(method)}
                      onChange={(e) =>
                        setMethods(
                          e.target.checked
                            ? [...methods, method]
                            : methods.filter((value) => value !== method),
                        )
                      }
                    />
                    {method}
                  </label>
                ),
              )}
            </div>
            <FormField
              label="允許的路徑前綴"
              hint="以逗號分隔；例如 /api/orders, /api/customers。"
            >
              <input
                value={pathPrefixes}
                onChange={(e) => setPathPrefixes(e.target.value)}
              />
            </FormField>
          </>
        )}
        <div className="business-form-grid">
          <FormField
            label="Bearer 憑證"
            hint={
              item
                ? "留空保留已保存憑證；儲存後不會再次顯示。"
                : "如服務需要認證，請填寫專用憑證。"
            }
          >
            <input
              type="password"
              autoComplete="new-password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
            />
          </FormField>
          <FormField label="Callback HMAC 密鑰" hint="用於驗證外部結果回報。">
            <input
              type="password"
              autoComplete="new-password"
              value={callbackSecret}
              onChange={(e) => setCallbackSecret(e.target.value)}
            />
          </FormField>
        </div>
        {kind === "agent" && (
          <FormField label="能力" hint="例如 summarize, extract, research">
            <input
              value={capabilities}
              onChange={(e) => setCapabilities(e.target.value)}
            />
          </FormField>
        )}
        <FormField
          label="接口範圍"
          hint="以逗號分隔；例如 content:read, business:write"
        >
          <input value={scopes} onChange={(e) => setScopes(e.target.value)} />
        </FormField>
        <FormField label="請求逾時（秒）">
          <input
            type="number"
            min="1"
            max="300"
            value={timeout}
            onChange={(e) => setTimeout(Number(e.target.value))}
          />
        </FormField>
        <label className="business-check">
          <input
            type="checkbox"
            checked={privateNetwork}
            onChange={(e) => setPrivateNetwork(e.target.checked)}
          />
          允許連接私人網絡（只供你信任的自架服務）
        </label>
        <label className="business-check">
          <input
            type="checkbox"
            checked={idempotent}
            onChange={(e) => setIdempotent(e.target.checked)}
          />
          目標服務支援相同 idempotency key 的安全重試
        </label>
        <Banner error={error} />
        <div className="business-form-footer">
          <button className="button secondary" type="button" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "儲存中…" : "儲存連接"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function IntegrationsPage({ kind }: { kind: "agent" | "connector" }) {
  const resources = useResources(kind),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [info, setInfo] = useState(""),
    [dispatch, setDispatch] = useState<Resource | null>(null),
    [task, setTask] = useState(""),
    [input, setInput] = useState("{}"),
    [busy, setBusy] = useState(false);
  const isAgent = kind === "agent";
  async function test(item: Resource) {
    setBusy(true);
    try {
      const result = await api<Data>(`/agents/${item.id}/test`, {
        method: "POST",
        body: "{}",
      });
      setInfo(result.message || `已收到服務回應：${JSON.stringify(result)}`);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow={isAgent ? "按需要接入你的團隊" : "安全連接業務系統"}
        title={isAgent ? "AI 員工接口" : "整合中心"}
        description={
          isAgent
            ? "集中管理外部員工的能力、資料範圍及工作回報。這裏不會自行建立或運行 AI 模型。"
            : "管理公司授權的 API 連接，供工作流程及外部服務使用。"
        }
        actions={
          <button className="button primary" onClick={() => setEdit("new")}>
            <Plus size={16} />
            {isAgent ? "接入 AI 員工" : "新增連接器"}
          </button>
        }
      />
      <Banner
        error={error || resources.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      {!resources.items.length && !resources.loading ? (
        <Empty
          icon={isAgent ? <Bot size={32} /> : <Link2 size={32} />}
          title={isAgent ? "準備好接入你的 AI 員工" : "將工作台連接到你的工具"}
          detail={
            isAgent
              ? "填寫外部服務端點及授權範圍。員工接入後，可以參與工作流程及保存結果。"
              : "新增一個可授權的 API 連接器，再在工作流程使用 HTTP 請求步驟。"
          }
          action={
            <button className="button primary" onClick={() => setEdit("new")}>
              <Plus size={16} />
              {isAgent ? "接入第一位員工" : "新增連接器"}
            </button>
          }
        />
      ) : (
        <div className="integration-card-grid">
          {resources.items.map((item) => (
            <article className="integration-card" key={item.id}>
              <header>
                <span className={`integration-card-icon ${kind}`}>
                  {isAgent ? <Bot size={25} /> : <Link2 size={25} />}
                </span>
                <button
                  className="icon-button"
                  title="設定"
                  onClick={() => setEdit(item)}
                >
                  <Settings2 size={17} />
                </button>
              </header>
              <h3>{item.name}</h3>
              <p className="integration-endpoint">{dataOf(item).endpoint}</p>
              <div className="integration-capabilities">
                {(dataOf(item).capabilities || dataOf(item).scopes || [])
                  .slice(0, 5)
                  .map((value: string) => (
                    <span className="business-tag" key={value}>
                      {value}
                    </span>
                  ))}
              </div>
              <div className="integration-security">
                <LockKeyhole size={14} />
                <span>
                  {(dataOf(item).allowed_hosts || []).length} 個允許主機 ·{" "}
                  {dataOf(item).timeout_seconds || 30} 秒逾時
                </span>
              </div>
              <footer>
                {isAgent && (
                  <>
                    <button
                      className="button secondary compact"
                      disabled={busy}
                      onClick={() => void test(item)}
                    >
                      測試連接
                    </button>
                    <button
                      className="button primary compact"
                      onClick={() => {
                        setDispatch(item);
                        setTask(dataOf(item).capabilities?.[0] || "");
                        setInput("{}");
                      }}
                    >
                      派發工作
                    </button>
                  </>
                )}
                <button
                  className="icon-button"
                  title="刪除連接"
                  onClick={() => setRemove(item)}
                >
                  <Trash2 size={16} />
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}
      <div className="mcp-connect-panel">
        <div>
          <Webhook size={22} />
          <h3>MCP 接口</h3>
        </div>
        <p>
          外部 AI
          工具可以透過同一套身份及權限存取工作台。請由管理員建立具有限定範圍的服務
          token。
        </p>
        <div className="mcp-endpoint">
          <code>{window.location.origin}/api/v1/mcp</code>
          <button
            className="icon-button"
            title="複製 MCP 網址"
            onClick={() => {
              void navigator.clipboard
                .writeText(`${window.location.origin}/api/v1/mcp`)
                .then(() => setInfo("已複製 MCP 網址。"))
                .catch((e) => setError(errorText(e)));
            }}
          >
            <Copy size={16} />
          </button>
        </div>
      </div>
      {edit && (
        <IntegrationModal
          kind={kind}
          item={edit === "new" ? undefined : edit}
          onClose={() => setEdit(null)}
          onDone={() => void resources.refresh()}
        />
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void resources.refresh()}
        />
      )}
      {dispatch && (
        <Modal
          title={`派發工作給 ${dispatch.name}`}
          onClose={() => setDispatch(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const result = await api<Data>(
                  `/agents/${dispatch.id}/dispatch`,
                  {
                    method: "POST",
                    body: JSON.stringify({
                      task,
                      input: parseJson(input, "輸入資料"),
                      idempotency_key: crypto.randomUUID(),
                    }),
                  },
                );
                setInfo(
                  `工作已排入佇列：${result.id || JSON.stringify(result)}`,
                );
                setDispatch(null);
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <FormField
              label="工作能力"
              hint="詳細指令及需要的資料放在下方輸入資料。"
            >
              <select
                required
                value={task}
                onChange={(e) => setTask(e.target.value)}
              >
                <option value="">選擇已宣告的能力</option>
                {(dataOf(dispatch).capabilities || []).map(
                  (capability: string) => (
                    <option key={capability}>{capability}</option>
                  ),
                )}
              </select>
            </FormField>
            <FormField label="輸入資料（JSON）">
              <textarea
                className="business-code"
                rows={6}
                value={input}
                onChange={(e) => setInput(e.target.value)}
              />
            </FormField>
            <Banner error={error} />
            <div className="business-form-footer">
              <button className="button primary" disabled={busy}>
                {busy ? "派發中…" : "派發工作"}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

export function WorkflowPage({ section }: { section: string }) {
  if (section === "agents") return <IntegrationsPage kind="agent" />;
  if (section === "connectors") return <IntegrationsPage kind="connector" />;
  if (section === "runs") return <RunsPage />;
  return <WorkflowList />;
}
