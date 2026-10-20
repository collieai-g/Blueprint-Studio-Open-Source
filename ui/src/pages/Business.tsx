import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  ArrowDownToLine,
  ArrowUpRight,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Database,
  ExternalLink,
  FileText,
  LayoutGrid,
  List,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Table2,
  Trash2,
  X,
} from "lucide-react";
import { api, type Resource } from "../api";
import { RRule } from "rrule";
import "../business.css";

export type Data = Record<string, any>;
export type Field = {
  id: string;
  name: string;
  type: string;
  required?: boolean;
  options?: string[];
  relation_table?: string;
  multiple?: boolean;
  formula?: string;
};
export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
export const day = (date: string | Date = new Date()) =>
  new Date(date).toLocaleDateString("en-CA");
export const time = (date?: string) =>
  date
    ? new Date(date).toLocaleString("zh-HK", {
        dateStyle: "medium",
        timeStyle: "short",
      })
    : "—";
export const dataOf = (resource?: Resource | null): Data =>
  (resource?.data || {}) as Data;
export const resourceName = (kind: string) =>
  ({
    table: "資料表",
    task: "任務",
    workday: "每日工作",
    event: "日程",
    form: "表單",
    note: "筆記",
    canvas: "白板",
    hub: "知識入口",
    workflow: "工作流程",
    agent: "外部 AI 員工",
    connector: "連接器",
  })[kind] || "項目";

export function useResources(kind: string) {
  const [items, setItems] = useState<Resource[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api<Resource[]>(`/resources/${kind}`));
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, [kind]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return { items, loading, error, setItems, setError, refresh };
}
export function useOpenResource(
  kinds: string[],
  onOpen: (resource: Resource) => void | Promise<void>,
  onError?: (message: string) => void,
) {
  const callback = useRef(onOpen),
    failure = useRef(onError);
  callback.current = onOpen;
  failure.current = onError;
  const key = kinds.join(",");
  useEffect(() => {
    let active = true,
      pending = "";
    async function open(id: string, kind?: string) {
      if (!id || pending === id || (kind && !kinds.includes(kind))) return;
      pending = id;
      try {
        const item = await api<Resource>(`/resource/${encodeURIComponent(id)}`);
        if (!active || !kinds.includes(item.kind)) return;
        if (sessionStorage.getItem("blueprint-open-resource") === id)
          sessionStorage.removeItem("blueprint-open-resource");
        await callback.current(item);
      } catch (error) {
        if (active) failure.current?.(errorText(error));
      } finally {
        pending = "";
      }
    }
    const listener = (event: Event) => {
      const detail = (event as CustomEvent<{ id: string; kind?: string }>)
        .detail;
      if (detail?.id) void open(detail.id, detail.kind);
    };
    const id = sessionStorage.getItem("blueprint-open-resource");
    if (id) void open(id);
    window.addEventListener("blueprint:open-resource", listener);
    return () => {
      active = false;
      window.removeEventListener("blueprint:open-resource", listener);
    };
  }, [key]);
}
export const createResource = (
  kind: string,
  name: string,
  data: Data,
  visibility = "company",
) =>
  api<Resource>("/resources", {
    method: "POST",
    body: JSON.stringify({ kind, name, data, visibility }),
  });
export const updateResource = (item: Resource, data: Data, name = item.name) =>
  api<Resource>(`/resource/${item.id}`, {
    method: "PATCH",
    body: JSON.stringify({ name, data, expected_version: item.version }),
  });
export const deleteResource = (item: Resource) =>
  api(`/resource/${item.id}?expected_version=${item.version}`, {
    method: "DELETE",
  });

export function Banner({
  error,
  info,
  onClose,
}: {
  error?: string;
  info?: string;
  onClose?: () => void;
}) {
  if (!error && !info) return null;
  return (
    <div
      className={`business-banner ${error ? "is-error" : "is-info"}`}
      role={error ? "alert" : "status"}
    >
      <span>{error || info}</span>
      {onClose && (
        <button className="icon-button" onClick={onClose} aria-label="關閉訊息">
          <X size={15} />
        </button>
      )}
    </div>
  );
}
export function Header({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="business-heading">
      <div>
        {eyebrow && <div className="business-eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="business-actions">{actions}</div>
    </div>
  );
}
export function Empty({
  icon,
  title,
  detail,
  action,
}: {
  icon?: ReactNode;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="business-empty">
      <div className="business-empty-icon">
        {icon || <Database size={30} />}
      </div>
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </div>
  );
}
export function Modal({
  title,
  children,
  onClose,
  footer,
  wide,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  wide?: boolean;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        className={`modal business-modal ${wide ? "business-modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="關閉">
            <X size={20} />
          </button>
        </header>
        <div className="business-modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  );
}
export function FormField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="business-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function ConfirmDelete({
  item,
  onClose,
  onDone,
}: {
  item: Resource;
  onClose: () => void;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal
      title={`刪除「${item.name}」？`}
      onClose={onClose}
      footer={
        <>
          <button className="button secondary" onClick={onClose}>
            取消
          </button>
          <button
            className="button danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await deleteResource(item);
                onDone();
                onClose();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "處理中…" : "移至垃圾桶"}
          </button>
        </>
      }
    >
      <p>項目會移至垃圾桶。保留政策及法務保全仍然適用。</p>
      <Banner error={error} />
    </Modal>
  );
}
export const FIELD_TYPES: [string, string][] = [
  ["text", "文字"],
  ["long_text", "長文字"],
  ["number", "數字"],
  ["currency", "金額"],
  ["checkbox", "核取方塊"],
  ["date", "日期"],
  ["datetime", "日期及時間"],
  ["select", "單一選項"],
  ["multiselect", "多個選項"],
  ["email", "電郵"],
  ["url", "網址"],
  ["user", "成員"],
  ["file", "文件"],
  ["relation", "關聯資料"],
  ["formula", "公式"],
];

function FieldsEditor({
  fields,
  onChange,
}: {
  fields: Field[];
  onChange: (fields: Field[]) => void;
}) {
  const [tables, setTables] = useState<Resource[]>([]),
    [referenceError, setReferenceError] = useState("");
  const needsTables = fields.some((field) => field.type === "relation");
  useEffect(() => {
    if (!needsTables) return;
    let active = true;
    api<Resource[]>("/resources/table")
      .then((items) => {
        if (active) {
          setTables(items);
          setReferenceError("");
        }
      })
      .catch((error) => {
        if (active) setReferenceError(errorText(error));
      });
    return () => {
      active = false;
    };
  }, [needsTables]);
  const edit = (index: number, patch: Partial<Field>) =>
    onChange(
      fields.map((field, n) => (n === index ? { ...field, ...patch } : field)),
    );
  return (
    <div className="schema-editor">
      {fields.map((field, index) => (
        <div className="schema-field" key={index}>
          <div className="schema-field-top">
            <span className="schema-number">{index + 1}</span>
            <input
              aria-label="欄位名稱"
              value={field.name}
              placeholder="欄位名稱"
              onChange={(e) => edit(index, { name: e.target.value })}
            />
            <select
              aria-label="欄位類型"
              value={field.type}
              onChange={(e) => edit(index, { type: e.target.value })}
            >
              {FIELD_TYPES.map(([type, label]) => (
                <option key={type} value={type}>
                  {label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="icon-button"
              aria-label="移除欄位"
              onClick={() => onChange(fields.filter((_, n) => n !== index))}
            >
              <Trash2 size={16} />
            </button>
          </div>
          <div className="schema-field-options">
            <input
              aria-label="欄位識別碼"
              value={field.id}
              placeholder="field_id"
              onChange={(e) =>
                edit(index, {
                  id: e.target.value.replace(/[^a-zA-Z0-9_]/g, "_"),
                })
              }
            />
            <label className="business-check">
              <input
                type="checkbox"
                checked={!!field.required}
                onChange={(e) => edit(index, { required: e.target.checked })}
              />
              必填
            </label>
          </div>
          {["select", "multiselect"].includes(field.type) && (
            <input
              aria-label="選項"
              placeholder="選項，以逗號分隔"
              value={(field.options || []).join(", ")}
              onChange={(e) =>
                edit(index, {
                  options: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          )}
          {field.type === "relation" && (
            <select
              aria-label="關聯資料表"
              required
              value={field.relation_table || ""}
              onChange={(e) => edit(index, { relation_table: e.target.value })}
            >
              <option value="">選擇關聯資料表…</option>
              {field.relation_table &&
                !tables.some((table) => table.id === field.relation_table) && (
                  <option value={field.relation_table}>
                    目前資料表（未能載入）
                  </option>
                )}
              {tables.map((table) => (
                <option key={table.id} value={table.id}>
                  {table.name}
                </option>
              ))}
            </select>
          )}
          {["file", "user", "relation"].includes(field.type) && (
            <label className="business-check">
              <input
                type="checkbox"
                checked={!!field.multiple}
                onChange={(e) => edit(index, { multiple: e.target.checked })}
              />
              容許選擇多項
            </label>
          )}
          {field.type === "formula" && (
            <input
              aria-label="公式"
              placeholder="例如 quantity * amount"
              value={field.formula || ""}
              onChange={(e) => edit(index, { formula: e.target.value })}
            />
          )}
        </div>
      ))}
      {referenceError && <Banner error={referenceError} />}
      <button
        className="button secondary"
        type="button"
        onClick={() =>
          onChange([
            ...fields,
            { id: `field_${fields.length + 1}`, name: "", type: "text" },
          ])
        }
      >
        <Plus size={16} />
        新增欄位
      </button>
    </div>
  );
}

function ConditionsEditor({
  fields,
  rules,
  onChange,
}: {
  fields: Field[];
  rules: Data[];
  onChange: (rules: Data[]) => void;
}) {
  const update = (index: number, patch: Data) =>
    onChange(
      rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)),
    );
  const compare = (value: string) => {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  };
  return (
    <section className="conditions-editor">
      <div className="conditions-header">
        <h3>條件邏輯</h3>
        <button
          type="button"
          className="business-text-button"
          disabled={fields.length < 2}
          onClick={() =>
            onChange([
              ...rules,
              {
                target: fields[1]?.id || "",
                action: "show",
                mode: "all",
                when: [
                  { field: fields[0]?.id || "", operator: "eq", value: "" },
                ],
              },
            ])
          }
        >
          <Plus size={14} />
          加入條件
        </button>
      </div>
      {!rules.length && <p>按其他欄位的答案顯示、隱藏或鎖定欄位。</p>}
      {rules.map((rule, index) => (
        <div className="condition-rule" key={index}>
          <div className="condition-rule-target">
            <select
              aria-label="條件動作"
              value={rule.action}
              onChange={(e) => update(index, { action: e.target.value })}
            >
              <option value="show">顯示</option>
              <option value="hide">隱藏</option>
              <option value="enable">啟用</option>
              <option value="disable">停用</option>
            </select>
            <select
              aria-label="條件目標欄位"
              value={rule.target}
              onChange={(e) => update(index, { target: e.target.value })}
            >
              {fields.map((field) => (
                <option key={field.id} value={field.id}>
                  {field.name}
                </option>
              ))}
            </select>
            <span>當</span>
            <select
              aria-label="條件組合"
              value={rule.mode || "all"}
              onChange={(e) => update(index, { mode: e.target.value })}
            >
              <option value="all">全部符合</option>
              <option value="any">任一符合</option>
            </select>
            <button
              type="button"
              className="icon-button"
              title="刪除條件"
              onClick={() => onChange(rules.filter((_, i) => i !== index))}
            >
              <Trash2 size={14} />
            </button>
          </div>
          {(rule.when || []).map((test: Data, testIndex: number) => {
            const change = (patch: Data) =>
              update(index, {
                when: rule.when.map((value: Data, i: number) =>
                  i === testIndex ? { ...value, ...patch } : value,
                ),
              });
            return (
              <div className="condition-comparison" key={testIndex}>
                <select
                  aria-label="比較欄位"
                  value={test.field}
                  onChange={(e) => change({ field: e.target.value })}
                >
                  {fields
                    .filter((field) => field.id !== rule.target)
                    .map((field) => (
                      <option key={field.id} value={field.id}>
                        {field.name}
                      </option>
                    ))}
                </select>
                <select
                  aria-label="比較方式"
                  value={test.operator}
                  onChange={(e) => change({ operator: e.target.value })}
                >
                  {[
                    ["eq", "等於"],
                    ["ne", "不等於"],
                    ["contains", "包含"],
                    ["starts_with", "開頭為"],
                    ["ends_with", "結尾為"],
                    ["empty", "空白"],
                    ["not_empty", "非空白"],
                    ["gt", "大於"],
                    ["gte", "大於或等於"],
                    ["lt", "小於"],
                    ["lte", "小於或等於"],
                  ].map(([id, name]) => (
                    <option key={id} value={id}>
                      {name}
                    </option>
                  ))}
                </select>
                <input
                  aria-label="比較值"
                  disabled={["empty", "not_empty"].includes(test.operator)}
                  value={
                    typeof test.value === "object"
                      ? JSON.stringify(test.value)
                      : String(test.value ?? "")
                  }
                  onChange={(e) => change({ value: compare(e.target.value) })}
                />
                {rule.when.length > 1 && (
                  <button
                    type="button"
                    className="icon-button"
                    title="移除比較"
                    onClick={() =>
                      update(index, {
                        when: rule.when.filter(
                          (_: Data, i: number) => i !== testIndex,
                        ),
                      })
                    }
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            );
          })}
          <button
            type="button"
            className="business-text-button"
            onClick={() =>
              update(index, {
                when: [
                  ...rule.when,
                  {
                    field:
                      fields.find((field) => field.id !== rule.target)?.id ||
                      "",
                    operator: "eq",
                    value: "",
                  },
                ],
              })
            }
          >
            <Plus size={12} />
            增加比較
          </button>
        </div>
      ))}
    </section>
  );
}

function formFieldState(form: Data, values: Data, id: string) {
  let hidden = false,
    disabled = false;
  const compare = (test: Data) => {
    const value = values[test.field],
      target = test.value;
    switch (test.operator || "eq") {
      case "eq":
        return JSON.stringify(value) === JSON.stringify(target);
      case "ne":
        return JSON.stringify(value) !== JSON.stringify(target);
      case "empty":
        return (
          value == null ||
          value === "" ||
          (Array.isArray(value) && value.length === 0)
        );
      case "not_empty":
        return !(
          value == null ||
          value === "" ||
          (Array.isArray(value) && value.length === 0)
        );
      case "contains":
        return Array.isArray(value)
          ? value.includes(target)
          : String(value ?? "").includes(String(target));
      case "starts_with":
        return String(value ?? "").startsWith(String(target));
      case "ends_with":
        return String(value ?? "").endsWith(String(target));
      case "gt":
        return Number(value) > Number(target);
      case "gte":
        return Number(value) >= Number(target);
      case "lt":
        return Number(value) < Number(target);
      case "lte":
        return Number(value) <= Number(target);
      default:
        return false;
    }
  };
  for (const rule of (form.conditions || []).filter(
    (value: Data) => value.target === id,
  )) {
    const tests: Data[] = rule.when || [],
      matches =
        rule.mode === "any" ? tests.some(compare) : tests.every(compare);
    if (rule.action === "show") hidden = !matches;
    if (rule.action === "hide") hidden = matches;
    if (rule.action === "enable") disabled = !matches;
    if (rule.action === "disable") disabled = matches;
  }
  return { hidden, disabled };
}
function activeFormValues(form: Data, values: Data) {
  return Object.fromEntries(
    Object.entries(values).filter(([id]) => {
      const state = formFieldState(form, values, id);
      return !state.hidden && !state.disabled;
    }),
  );
}

function SchemaModal({
  item,
  kind,
  onClose,
  onDone,
}: {
  item?: Resource;
  kind: "table" | "form";
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(item?.name || ""),
    [description, setDescription] = useState(dataOf(item).description || ""),
    [fields, setFields] = useState<Field[]>(
      dataOf(item).fields || [
        { id: "name", name: "名稱", type: "text", required: true },
      ],
    );
  const [access, setAccess] = useState(dataOf(item).access || "company"),
    [tableId, setTableId] = useState<string>(dataOf(item).table_id || ""),
    [conditions, setConditions] = useState<Data[]>(
      dataOf(item).conditions || [],
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const linkedTables = useResources("table");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (!fields.length || fields.some((f) => !f.name.trim() || !f.id.trim()))
        throw new Error("請填寫每個欄位嘅名稱及識別碼。");
      if (new Set(fields.map((f) => f.id)).size !== fields.length)
        throw new Error("欄位識別碼不可重複。");
      const data = {
        ...dataOf(item),
        description,
        fields,
        ...(kind === "form"
          ? {
              access,
              submit_label: "提交",
              table_id: tableId || null,
              conditions,
            }
          : {}),
      };
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
      title={`${item ? "設定" : "建立"}${resourceName(kind)}`}
      onClose={onClose}
      wide
    >
      <form onSubmit={submit}>
        <FormField label="名稱">
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={kind === "table" ? "例如：客戶資料" : "例如：合作查詢"}
          />
        </FormField>
        <FormField label="說明">
          <textarea
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormField>
        {kind === "form" && (
          <FormField label="提交對象">
            <select value={access} onChange={(e) => setAccess(e.target.value)}>
              <option value="company">已登入公司成員</option>
              <option value="public">任何持有連結嘅人</option>
            </select>
          </FormField>
        )}
        {kind === "form" && (
          <FormField
            label="連接業務資料表"
            hint="正式提交後會建立對應記錄。選擇資料表會使用相同的欄位結構。"
          >
            <select
              value={tableId}
              onChange={(e) => {
                setTableId(e.target.value);
                const table = linkedTables.items.find(
                  (t) => t.id === e.target.value,
                );
                if (table) setFields(dataOf(table).fields || []);
              }}
            >
              <option value="">只保存提交記錄</option>
              {linkedTables.items.map((table) => (
                <option key={table.id} value={table.id}>
                  {table.name}
                </option>
              ))}
            </select>
          </FormField>
        )}
        <div className="business-label">欄位結構</div>
        <FieldsEditor fields={fields} onChange={setFields} />
        {kind === "form" && (
          <ConditionsEditor
            fields={fields}
            rules={conditions}
            onChange={setConditions}
          />
        )}
        <Banner error={error} />
        <div className="business-form-footer">
          <button className="button secondary" type="button" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy || !name.trim()}>
            {busy ? "儲存中…" : "儲存"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ReferenceInput({
  field,
  value,
  onChange,
}: {
  field: Field;
  value: any;
  onChange: (value: any) => void;
}) {
  const [options, setOptions] = useState<
      { id: string; name: string; active?: boolean }[]
    >([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    const route =
      field.type === "user"
        ? "/users"
        : field.type === "file"
          ? "/resources/file"
          : field.relation_table
            ? `/tables/${field.relation_table}/records`
            : "";
    if (!route) {
      setLoading(false);
      return;
    }
    setLoading(true);
    api<{ id: string; name: string; active?: boolean }[]>(route)
      .then((items) => {
        if (active) {
          setOptions(items.filter((item) => item.active !== false));
          setError("");
        }
      })
      .catch((failure) => {
        if (active) setError(errorText(failure));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [field.type, field.relation_table]);
  const selected: string[] = field.multiple
    ? Array.isArray(value)
      ? value
      : []
    : value
      ? [value]
      : [];
  const choices = [
    ...options,
    ...selected
      .filter((id) => !options.some((item) => item.id === id))
      .map((id) => ({ id, name: `未能載入的項目 · ${id.slice(0, 8)}` })),
  ];
  return (
    <div className="reference-input">
      <select
        aria-label={field.name}
        required={field.required}
        multiple={field.multiple}
        size={
          field.multiple ? Math.min(Math.max(choices.length, 3), 6) : undefined
        }
        disabled={loading}
        value={field.multiple ? selected : value || ""}
        onChange={(event) =>
          onChange(
            field.multiple
              ? Array.from(event.target.selectedOptions).map(
                  (option) => option.value,
                )
              : event.target.value,
          )
        }
      >
        {!field.multiple && (
          <option value="">{loading ? "載入選項中…" : "選擇…"}</option>
        )}
        {choices.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
      {field.multiple && (
        <small className="muted">按住 Ctrl 或 Command 可選擇多項。</small>
      )}
      {!loading && !choices.length && (
        <small className="muted">暫時未有可選擇的項目。</small>
      )}
      {error && <small className="danger-text">{error}</small>}
    </div>
  );
}

function ValueInput({
  field,
  value,
  onChange,
  formFile = false,
}: {
  field: Field;
  value: any;
  onChange: (value: any) => void;
  formFile?: boolean;
}) {
  const [fileError, setFileError] = useState("");
  if (field.type === "file" && formFile)
    return (
      <div className="form-attachment-input">
        <input
          type="file"
          multiple={field.multiple}
          required={field.required && !value}
          onChange={async (e) => {
            const files = Array.from(e.target.files || []);
            setFileError("");
            try {
              const uploads = await Promise.all(
                files.map(
                  (file) =>
                    new Promise<Data>((resolve, reject) => {
                      if (file.size > 20 * 1024 * 1024) {
                        reject(new Error("每個附件最多 20 MiB。"));
                        return;
                      }
                      const reader = new FileReader();
                      reader.onload = () =>
                        resolve({
                          name: file.name,
                          mime: file.type || "application/octet-stream",
                          content_base64: String(reader.result).split(",")[1],
                        });
                      reader.onerror = () =>
                        reject(new Error("未能讀取附件。"));
                      reader.readAsDataURL(file);
                    }),
                ),
              );
              onChange(field.multiple ? uploads : uploads[0]);
            } catch (error) {
              setFileError(errorText(error));
              e.target.value = "";
            }
          }}
        />
        {value && (
          <span className="form-attachment-names">
            {(Array.isArray(value) ? value : [value])
              .map((file: Data) => file.name)
              .join("、")}
          </span>
        )}
        {fileError && <small className="danger-text">{fileError}</small>}
      </div>
    );
  if (field.type === "formula")
    return <div className="formula-value">由伺服器按公式計算</div>;
  if (["user", "file", "relation"].includes(field.type))
    return <ReferenceInput field={field} value={value} onChange={onChange} />;
  if (field.type === "checkbox")
    return (
      <label className="business-check">
        <input
          type="checkbox"
          checked={!!value}
          onChange={(e) => onChange(e.target.checked)}
        />
        已選取
      </label>
    );
  if (field.type === "select")
    return (
      <select
        required={field.required}
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">選擇…</option>
        {field.options?.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    );
  if (field.type === "multiselect")
    return (
      <div className="business-option-list">
        {field.options?.map((o) => (
          <label className="business-check" key={o}>
            <input
              type="checkbox"
              checked={Array.isArray(value) && value.includes(o)}
              onChange={(e) =>
                onChange(
                  e.target.checked
                    ? [...(value || []), o]
                    : (value || []).filter((s: string) => s !== o),
                )
              }
            />
            {o}
          </label>
        ))}
      </div>
    );
  if (field.type === "long_text")
    return (
      <textarea
        rows={3}
        required={field.required}
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  const inputType =
    (
      {
        number: "number",
        currency: "number",
        date: "date",
        datetime: "datetime-local",
        email: "email",
        url: "url",
      } as Data
    )[field.type] || "text";
  return (
    <input
      type={inputType}
      step={inputType === "number" ? "any" : undefined}
      required={field.required}
      value={
        field.type === "datetime" && value
          ? new Date(value)
              .toLocaleString("sv-SE")
              .slice(0, 16)
              .replace(" ", "T")
          : (value ?? "")
      }
      onChange={(e) =>
        onChange(
          inputType === "number"
            ? e.target.value === ""
              ? null
              : Number(e.target.value)
            : field.type === "datetime" && e.target.value
              ? new Date(e.target.value).toISOString()
              : e.target.value,
        )
      }
    />
  );
}
export const displayValue = (value: any) =>
  value === true
    ? "✓"
    : value === false || value == null || value === ""
      ? "—"
      : Array.isArray(value)
        ? value.join("、")
        : typeof value === "object"
          ? JSON.stringify(value)
          : String(value);

function RecordModal({
  table,
  item,
  onClose,
  onDone,
}: {
  table: Resource;
  item?: Resource;
  onClose: () => void;
  onDone: () => void;
}) {
  const fields: Field[] = dataOf(table).fields || [],
    [values, setValues] = useState<Data>(dataOf(item).values || {}),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const name = String(
        values[fields.find((f) => f.type === "text")?.id || "name"] ||
          item?.name ||
          "未命名記錄",
      );
      if (item)
        await updateResource(
          item,
          { ...dataOf(item), table_id: table.id, values },
          name,
        );
      else
        await api(`/tables/${table.id}/records`, {
          method: "POST",
          body: JSON.stringify({ name, values }),
        });
      onDone();
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={item ? "編輯記錄" : "新增記錄"} onClose={onClose}>
      <form onSubmit={submit}>
        {fields.map((field) => (
          <FormField
            key={field.id}
            label={field.name + (field.required ? " *" : "")}
            hint={field.type === "formula" ? field.formula : undefined}
          >
            <ValueInput
              field={field}
              value={values[field.id]}
              onChange={(value) => setValues({ ...values, [field.id]: value })}
            />
          </FormField>
        ))}
        <Banner error={error} />
        <div className="business-form-footer">
          <button type="button" className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "儲存中…" : "儲存記錄"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function TablesPage() {
  const tables = useResources("table"),
    [selected, setSelected] = useState<Resource | null>(null),
    [records, setRecords] = useState<Resource[]>([]),
    [view, setView] = useState("list"),
    [query, setQuery] = useState("");
  const [schema, setSchema] = useState<Resource | "new" | null>(null),
    [record, setRecord] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [error, setError] = useState(""),
    [info, setInfo] = useState(""),
    [busy, setBusy] = useState(false);
  useOpenResource(
    ["table", "record"],
    async (item) => {
      if (item.kind === "table") setSelected(item);
      else {
        const table = await api<Resource>(`/resource/${dataOf(item).table_id}`);
        setSelected(table);
        setRecord(item);
      }
    },
    setError,
  );
  useEffect(() => {
    const preferred = dataOf(selected).default_view || "list";
    const initial = preferred === "board" ? "kanban" : preferred;
    setView(
      ["list", "kanban", "calendar", "chart"].includes(initial)
        ? initial
        : "list",
    );
    const saved = (dataOf(selected).views || []).find(
      (item: Data) => item.type === preferred || item.id === preferred,
    );
    setQuery(saved?.query || "");
    setInfo("");
  }, [selected?.id]);
  const refreshRecords = useCallback(async () => {
    if (!selected) return;
    setBusy(true);
    try {
      setRecords(await api<Resource[]>(`/tables/${selected.id}/records`));
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, [selected?.id, selected?.version]);
  useEffect(() => {
    void refreshRecords();
  }, [refreshRecords]);
  useEffect(() => {
    if (selected) {
      const latest = tables.items.find((i) => i.id === selected.id);
      if (latest) setSelected(latest);
    }
  }, [tables.items]);
  const filtered = records.filter((r) =>
    `${r.name} ${JSON.stringify(r.data)}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const fields: Field[] = dataOf(selected).fields || [],
    status = fields.find((f) => f.type === "select"),
    date = fields.find((f) => f.type === "date" || f.type === "datetime"),
    numeric = fields.find((f) => f.type === "number" || f.type === "currency");
  async function saveView() {
    if (!selected) return;
    try {
      const type = view === "kanban" ? "board" : view;
      const next = await updateResource(selected, {
        ...dataOf(selected),
        default_view: type,
        views: [
          ...(dataOf(selected).views || []).filter(
            (v: Data) => v.type !== view && v.type !== type,
          ),
          {
            id: view,
            name: {
              list: "列表",
              kanban: "看板",
              calendar: "日曆",
              chart: "圖表",
            }[view],
            type,
            query,
          },
        ],
      });
      setSelected(next);
      setInfo("視圖及搜尋條件已保存，下次開啟會自動套用。");
      void tables.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function moveRecord(row: Resource, column: string) {
    if (!status) return;
    const value = column === "未分類" ? null : column;
    if ((dataOf(row).values?.[status.id] || null) === value) return;
    try {
      const updated = await updateResource(row, {
        ...dataOf(row),
        values: { ...dataOf(row).values, [status.id]: value },
      });
      setRecords((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      setError("");
    } catch (failure) {
      setError(errorText(failure));
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="業務資料"
        title={selected?.name || "資料庫"}
        description={
          selected
            ? dataOf(selected).description || "將資料、文件及工作流程連在一起。"
            : "為公司建立適合自己工作方式嘅資料庫。"
        }
        actions={
          <>
            {selected && (
              <button
                className="button secondary"
                onClick={() => setSelected(null)}
              >
                <ChevronLeft size={16} />
                所有資料表
              </button>
            )}
            <button
              className="button primary"
              onClick={() => (selected ? setRecord("new") : setSchema("new"))}
            >
              <Plus size={16} />
              {selected ? "新增記錄" : "建立資料表"}
            </button>
          </>
        }
      />
      <Banner
        error={error || tables.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      {!selected ? (
        <>
          {tables.loading && (
            <div className="business-loading">正在載入資料表…</div>
          )}
          {!tables.loading && !tables.items.length ? (
            <Empty
              icon={<Database size={30} />}
              title="將公司資料整理成一個工作空間"
              detail="由客戶名單、專案或資產清單開始，自行設定欄位及關聯。"
              action={
                <button
                  className="button primary"
                  onClick={() => setSchema("new")}
                >
                  <Plus size={16} />
                  建立第一個資料表
                </button>
              }
            />
          ) : (
            <div className="resource-card-grid">
              {tables.items.map((item) => (
                <button
                  className="resource-card"
                  key={item.id}
                  onClick={() => setSelected(item)}
                >
                  <div className="resource-card-icon">
                    <Table2 size={27} />
                  </div>
                  <h3>{item.name}</h3>
                  <p>
                    {dataOf(item).description ||
                      `${(dataOf(item).fields || []).length} 個欄位`}
                  </p>
                  <footer>
                    <span>{time(item.updated_at)}</span>
                    <ArrowUpRight size={17} />
                  </footer>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          <div className="business-viewbar">
            <div className="business-view-tabs">
              {[
                ["list", List, "列表"],
                ["kanban", Columns3, "看板"],
                ["calendar", CalendarDays, "日曆"],
                ["chart", LayoutGrid, "圖表"],
              ].map(([id, Icon, label]) => {
                const ViewIcon = Icon as typeof List;
                return (
                  <button
                    key={String(id)}
                    className={view === id ? "active" : ""}
                    onClick={() => {
                      setView(String(id));
                      const type = id === "kanban" ? "board" : id;
                      const saved = (dataOf(selected).views || []).find(
                        (item: Data) => item.type === type || item.type === id,
                      );
                      setQuery(saved?.query || "");
                    }}
                  >
                    <ViewIcon size={16} />
                    {String(label)}
                  </button>
                );
              })}
            </div>
            <div className="business-actions">
              <button
                className="button secondary compact"
                onClick={() => void saveView()}
              >
                保存視圖
              </button>
              <button
                className="icon-button"
                title="欄位設定"
                onClick={() => setSchema(selected)}
              >
                <Settings2 size={18} />
              </button>
              <button
                className="icon-button"
                title="重新整理"
                onClick={() => void refreshRecords()}
              >
                <RefreshCw size={17} />
              </button>
              <button
                className="icon-button"
                title="刪除資料表"
                onClick={() => setRemove(selected)}
              >
                <Trash2 size={17} />
              </button>
            </div>
          </div>
          <div className="business-filterbar">
            <div className="business-search">
              <Search size={17} />
              <input
                aria-label="搜尋記錄"
                placeholder="搜尋記錄…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
            <span className="muted">{filtered.length} 筆記錄</span>
          </div>
          {busy ? (
            <div className="business-loading">載入記錄中…</div>
          ) : !records.length ? (
            <Empty
              title="資料表已準備好"
              detail="新增第一筆記錄，開始使用列表、看板及日曆視圖。"
              action={
                <button
                  className="button primary"
                  onClick={() => setRecord("new")}
                >
                  <Plus size={16} />
                  新增記錄
                </button>
              }
            />
          ) : view === "list" ? (
            <div className="business-data-grid">
              <table>
                <thead>
                  <tr>
                    {fields.map((f) => (
                      <th key={f.id}>
                        <span className="column-type">
                          {f.type === "number" || f.type === "currency"
                            ? "#"
                            : f.type === "date"
                              ? "◷"
                              : "Aa"}
                        </span>
                        {f.name}
                      </th>
                    ))}
                    <th aria-label="操作" />
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row) => (
                    <tr key={row.id} onDoubleClick={() => setRecord(row)}>
                      {fields.map((f, i) => (
                        <td key={f.id}>
                          {i === 0 ? (
                            <button
                              className="business-cell-link"
                              onClick={() => setRecord(row)}
                            >
                              {displayValue(dataOf(row).values?.[f.id])}
                            </button>
                          ) : f.type === "select" ? (
                            <span className="business-tag">
                              {displayValue(dataOf(row).values?.[f.id])}
                            </span>
                          ) : (
                            displayValue(dataOf(row).values?.[f.id])
                          )}
                        </td>
                      ))}
                      <td>
                        <button
                          className="icon-button"
                          title="刪除記錄"
                          onClick={() => setRemove(row)}
                        >
                          <Trash2 size={15} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : view === "kanban" ? (
            status ? (
              <div className="business-kanban">
                {[...(status.options || []), "未分類"].map((column) => {
                  const rows = filtered.filter(
                    (r) =>
                      (dataOf(r).values?.[status.id] || "未分類") === column,
                  );
                  return (
                    <section
                      className="kanban-column"
                      key={column}
                      onDragOver={(event) => {
                        event.preventDefault();
                        event.dataTransfer.dropEffect = "move";
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        const row = records.find(
                          (item) =>
                            item.id ===
                            event.dataTransfer.getData(
                              "application/x-blueprint-record",
                            ),
                        );
                        if (row) void moveRecord(row, column);
                      }}
                    >
                      <header>
                        <span className="kanban-dot" />
                        {column}
                        <small>{rows.length}</small>
                      </header>
                      {rows.map((row) => (
                        <button
                          className="kanban-card"
                          key={row.id}
                          draggable
                          onDragStart={(event) => {
                            event.dataTransfer.setData(
                              "application/x-blueprint-record",
                              row.id,
                            );
                            event.dataTransfer.effectAllowed = "move";
                          }}
                          onClick={() => setRecord(row)}
                        >
                          <strong>{row.name}</strong>
                          {fields
                            .filter((f) => f.id !== status.id)
                            .slice(1, 3)
                            .map((f) => (
                              <span key={f.id}>
                                {f.name}：
                                {displayValue(dataOf(row).values?.[f.id])}
                              </span>
                            ))}
                        </button>
                      ))}
                      <button
                        className="kanban-add"
                        onClick={() => setRecord("new")}
                      >
                        <Plus size={15} />
                        新增記錄
                      </button>
                    </section>
                  );
                })}
              </div>
            ) : (
              <Empty
                title="加入選項欄位以使用看板"
                detail="看板會按第一個單一選項欄位分組。"
                action={
                  <button
                    className="button secondary"
                    onClick={() => setSchema(selected)}
                  >
                    設定欄位
                  </button>
                }
              />
            )
          ) : view === "calendar" ? (
            date ? (
              <MiniCalendar
                items={filtered.map((r) => ({
                  id: r.id,
                  name: r.name,
                  start: dataOf(r).values?.[date.id],
                  item: r,
                }))}
                onItem={(r) => setRecord(r.item)}
              />
            ) : (
              <Empty
                title="加入日期欄位以使用日曆"
                detail="日曆會按第一個日期欄位顯示記錄。"
                action={
                  <button
                    className="button secondary"
                    onClick={() => setSchema(selected)}
                  >
                    設定欄位
                  </button>
                }
              />
            )
          ) : (
            <div className="business-chart-panel">
              <h3>{numeric ? `${numeric.name}分佈` : "記錄分佈"}</h3>
              {filtered.slice(0, 20).map((row) => {
                const value = numeric
                    ? Number(dataOf(row).values?.[numeric.id] || 0)
                    : 1,
                  max = Math.max(
                    1,
                    ...filtered.map((r) =>
                      numeric ? Number(dataOf(r).values?.[numeric.id] || 0) : 1,
                    ),
                  );
                return (
                  <div className="business-chart-row" key={row.id}>
                    <button onClick={() => setRecord(row)}>{row.name}</button>
                    <div>
                      <span
                        style={{
                          width: `${Math.max(1, (value / max) * 100)}%`,
                        }}
                      />
                    </div>
                    <strong>{value.toLocaleString()}</strong>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
      {schema && (
        <SchemaModal
          key={schema === "new" ? "new" : schema.id}
          kind="table"
          item={schema === "new" ? undefined : schema}
          onClose={() => setSchema(null)}
          onDone={() => void tables.refresh()}
        />
      )}
      {selected && record && (
        <RecordModal
          key={record === "new" ? "new" : record.id}
          table={selected}
          item={record === "new" ? undefined : record}
          onClose={() => setRecord(null)}
          onDone={() => void refreshRecords()}
        />
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => {
            if (remove.kind === "table") {
              setSelected(null);
              void tables.refresh();
            } else void refreshRecords();
          }}
        />
      )}
    </div>
  );
}

export function MiniCalendar({
  items,
  onItem,
  onDay,
}: {
  items: {
    id: string;
    name: string;
    start?: string;
    rrule?: string;
    timezone?: string;
    item: any;
  }[];
  onItem: (item: any) => void;
  onDay?: (date: string) => void;
}) {
  const [cursor, setCursor] = useState(new Date()),
    year = cursor.getFullYear(),
    month = cursor.getMonth(),
    first = new Date(year, month, 1),
    offset = first.getDay(),
    start = new Date(year, month, 1 - offset);
  const cells = Array.from(
    { length: 42 },
    (_, i) =>
      new Date(start.getFullYear(), start.getMonth(), start.getDate() + i),
  );
  const expanded = useMemo(() => {
    const lower = new Date(year, month, 1 - offset),
      upper = new Date(year, month, 43 - offset);
    return items.flatMap((item) => {
      if (!item.start || !item.rrule) return [item];
      try {
        const zone =
          item.timezone ||
          Intl.DateTimeFormat().resolvedOptions().timeZone ||
          "UTC";
        const parts = Object.fromEntries(
          new Intl.DateTimeFormat("en-GB", {
            timeZone: zone,
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hourCycle: "h23",
          })
            .formatToParts(new Date(item.start))
            .map((part) => [part.type, part.value]),
        );
        const dtstart = new Date(
          Date.UTC(
            Number(parts.year),
            Number(parts.month) - 1,
            Number(parts.day),
            Number(parts.hour),
            Number(parts.minute),
            Number(parts.second),
          ),
        );
        const rule = new RRule({
          ...RRule.parseString(item.rrule.replace(/^RRULE:/, "")),
          dtstart,
          tzid: zone,
        });
        return rule
          .between(lower, upper, true, (_, index) => index < 1000)
          .map((date) => ({
            ...item,
            id: `${item.id}:${date.toISOString()}`,
            start: date.toISOString(),
          }));
      } catch {
        return [item];
      }
    });
  }, [items, year, month, offset]);
  const keyOf = (date: Date) =>
    `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  return (
    <div className="business-calendar">
      <header>
        <h2>
          {year} 年 {month + 1} 月
        </h2>
        <div className="business-actions">
          <button
            className="button secondary compact"
            onClick={() => setCursor(new Date())}
          >
            今天
          </button>
          <button
            className="icon-button"
            aria-label="上個月"
            onClick={() => setCursor(new Date(year, month - 1, 1))}
          >
            <ChevronLeft size={19} />
          </button>
          <button
            className="icon-button"
            aria-label="下個月"
            onClick={() => setCursor(new Date(year, month + 1, 1))}
          >
            <ChevronRight size={19} />
          </button>
        </div>
      </header>
      <div className="calendar-weekdays">
        {["日", "一", "二", "三", "四", "五", "六"].map((d) => (
          <span key={d}>星期{d}</span>
        ))}
      </div>
      <div className="calendar-cells">
        {cells.map((date) => {
          const key = keyOf(date),
            today = key === keyOf(new Date()),
            rows = expanded.filter(
              (item) =>
                item.start &&
                (/^\d{4}-\d{2}-\d{2}$/.test(item.start)
                  ? item.start
                  : keyOf(new Date(item.start))) === key,
            );
          return (
            <div
              className={`calendar-cell ${date.getMonth() !== month ? "outside" : ""}`}
              key={key}
            >
              <button
                className={`calendar-date ${today ? "today" : ""}`}
                onClick={() => onDay?.(key)}
              >
                {date.getDate()}
              </button>
              {rows.slice(0, 4).map((item) => (
                <button
                  className="calendar-event"
                  key={item.id}
                  onClick={() => onItem(item)}
                >
                  {item.name}
                </button>
              ))}
              {rows.length > 4 && (
                <span className="calendar-more">另外 {rows.length - 4} 項</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TaskModal({
  item,
  onClose,
  onDone,
}: {
  item?: Resource;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(item?.name || ""),
    [description, setDescription] = useState(dataOf(item).description || ""),
    [status, setStatus] = useState(dataOf(item).status || "open"),
    [due, setDue] = useState(
      dataOf(item).due_at
        ? new Date(dataOf(item).due_at)
            .toLocaleString("sv-SE")
            .slice(0, 16)
            .replace(" ", "T")
        : "",
    ),
    [assignees, setAssignees] = useState<string[]>(
      dataOf(item).assignees || [],
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title={item ? "編輯任務" : "新增任務"} onClose={onClose}>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            const data = {
              ...dataOf(item),
              description,
              status,
              assignees,
              due_at: due ? new Date(due).toISOString() : null,
            };
            if (item) await updateResource(item, data, name);
            else await createResource("task", name, data);
            onDone();
            onClose();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <FormField label="任務名稱">
          <input
            autoFocus
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </FormField>
        <FormField label="說明">
          <textarea
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormField>
        <div className="business-form-grid">
          <FormField label="狀態">
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="open">待處理</option>
              <option value="in_progress">進行中</option>
              <option value="completed">已完成</option>
            </select>
          </FormField>
          <FormField label="到期時間">
            <input
              type="datetime-local"
              value={due}
              onChange={(e) => setDue(e.target.value)}
            />
          </FormField>
        </div>
        <FormField label="負責人">
          <ReferenceInput
            field={{
              id: "assignees",
              name: "負責人",
              type: "user",
              multiple: true,
            }}
            value={assignees}
            onChange={setAssignees}
          />
        </FormField>
        <Banner error={error} />
        <div className="business-form-footer">
          <button type="button" className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "儲存中…" : "儲存"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function TasksPage() {
  const tasks = useResources("task"),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [filter, setFilter] = useState("all"),
    [query, setQuery] = useState("");
  useOpenResource(["task"], setEdit, tasks.setError);
  const items = tasks.items.filter(
    (t) =>
      (filter === "all" || dataOf(t).status === filter) &&
      t.name.toLowerCase().includes(query.toLowerCase()),
  );
  async function complete(item: Resource) {
    try {
      await updateResource(item, {
        ...dataOf(item),
        status: dataOf(item).status === "completed" ? "open" : "completed",
      });
      void tasks.refresh();
    } catch (e) {
      tasks.setError(errorText(e));
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="一起完成工作"
        title="任務"
        description="集中處理跟進事項、工作期限及團隊待辦。"
        actions={
          <button className="button primary" onClick={() => setEdit("new")}>
            <Plus size={16} />
            新增任務
          </button>
        }
      />
      <Banner error={tasks.error} />
      <div className="business-viewbar">
        <div className="business-view-tabs">
          {[
            ["all", "全部"],
            ["open", "待處理"],
            ["in_progress", "進行中"],
            ["completed", "已完成"],
          ].map(([id, label]) => (
            <button
              className={filter === id ? "active" : ""}
              key={id}
              onClick={() => setFilter(id)}
            >
              {label}
              <span>
                {
                  tasks.items.filter(
                    (t) => id === "all" || dataOf(t).status === id,
                  ).length
                }
              </span>
            </button>
          ))}
        </div>
        <div className="business-search">
          <Search size={16} />
          <input
            aria-label="搜尋任務"
            placeholder="搜尋任務"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      {tasks.loading ? (
        <div className="business-loading">正在載入任務…</div>
      ) : !items.length ? (
        <Empty
          icon={<Check size={30} />}
          title={
            filter === "completed"
              ? "完成嘅任務會顯示喺呢度"
              : "清楚下一步，工作更有方向"
          }
          detail="新增任務並設定期限。完成後可以隨時查返記錄。"
          action={
            <button className="button primary" onClick={() => setEdit("new")}>
              <Plus size={16} />
              新增任務
            </button>
          }
        />
      ) : (
        <div className="task-list">
          {items.map((item) => (
            <div
              className={`task-row ${dataOf(item).status === "completed" ? "completed" : ""}`}
              key={item.id}
            >
              <button
                aria-label={
                  dataOf(item).status === "completed"
                    ? "重新開啟任務"
                    : "完成任務"
                }
                className="task-checkbox"
                onClick={() => void complete(item)}
              >
                {dataOf(item).status === "completed" && <Check size={14} />}
              </button>
              <button className="task-main" onClick={() => setEdit(item)}>
                <strong>{item.name}</strong>
                <span>{dataOf(item).description || "沒有補充說明"}</span>
              </button>
              <span className={`business-status ${dataOf(item).status}`}>
                {(
                  {
                    open: "待處理",
                    in_progress: "進行中",
                    completed: "已完成",
                  } as Data
                )[dataOf(item).status] || dataOf(item).status}
              </span>
              <span className="task-due">
                {dataOf(item).due_at ? time(dataOf(item).due_at) : "未設期限"}
              </span>
              <button
                className="icon-button"
                onClick={() => setRemove(item)}
                aria-label="刪除任務"
              >
                <Trash2 size={16} />
              </button>
            </div>
          ))}
        </div>
      )}
      {edit && (
        <TaskModal
          key={edit === "new" ? "new" : edit.id}
          item={edit === "new" ? undefined : edit}
          onClose={() => setEdit(null)}
          onDone={() => void tasks.refresh()}
        />
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void tasks.refresh()}
        />
      )}
    </div>
  );
}

function WorkdaysPage() {
  const records = useResources("workday"),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [date, setDate] = useState(day()),
    [name, setName] = useState(""),
    [draft, setDraft] = useState<Data>({}),
    [error, setError] = useState(""),
    [pending, setPending] = useState<string[]>([]),
    [busy, setBusy] = useState(false);
  async function toggleTodo(item: Resource, index: number, completed: boolean) {
    const todos = dataOf(item).todos.map((todo: any, i: number) =>
      i === index
        ? typeof todo === "string"
          ? { text: todo, completed }
          : { ...todo, completed }
        : todo,
    );
    const optimistic = { ...item, data: { ...dataOf(item), todos } };
    records.setItems((items) =>
      items.map((record) => (record.id === item.id ? optimistic : record)),
    );
    setPending((items) => [...items, item.id]);
    try {
      const saved = await updateResource(item, optimistic.data);
      records.setItems((items) =>
        items.map((record) => (record.id === item.id ? saved : record)),
      );
    } catch (failure) {
      records.setItems((items) =>
        items.map((record) => (record.id === item.id ? item : record)),
      );
      records.setError(errorText(failure));
    } finally {
      setPending((items) => items.filter((id) => id !== item.id));
    }
  }
  function open(item: Resource | "new") {
    setEdit(item);
    setDate(item === "new" ? day() : dataOf(item).date || day());
    setName(item === "new" ? "每日工作" : item.name);
    const d = item === "new" ? {} : dataOf(item);
    setDraft(
      Object.fromEntries(
        ["outcomes", "decisions", "references", "todos"].map((k) => [
          k,
          (d[k] || [])
            .map((v: any) =>
              typeof v === "string"
                ? v
                : v.text || v.title || JSON.stringify(v),
            )
            .join("\n"),
        ]),
      ),
    );
    setError("");
  }
  useOpenResource(["workday"], open, setError);
  return (
    <div className="business-page">
      <Header
        eyebrow="公司嘅工作記憶"
        title="每日工作"
        description="留低成果、決定同下一步，讓每一日嘅工作持續累積。"
        actions={
          <button className="button primary" onClick={() => open("new")}>
            <Plus size={16} />
            記錄今日工作
          </button>
        }
      />
      <Banner error={records.error} />
      {!records.items.length && !records.loading ? (
        <Empty
          icon={<FileText size={30} />}
          title="記錄值得留下嘅工作"
          detail="將每日成果、決定、參考同待辦整理成可追溯嘅公司記錄。外部 AI 亦可經 API 保存。"
          action={
            <button className="button primary" onClick={() => open("new")}>
              建立工作記錄
            </button>
          }
        />
      ) : (
        <div className="workday-grid">
          {[...records.items]
            .sort((a, b) =>
              String(dataOf(b).date).localeCompare(String(dataOf(a).date)),
            )
            .map((item) => (
              <article className="workday-card" key={item.id}>
                <header>
                  <div className="workday-date">
                    <span>
                      {new Date(
                        dataOf(item).date || item.created_at,
                      ).toLocaleDateString("zh-HK", { month: "short" })}
                    </span>
                    <strong>
                      {new Date(dataOf(item).date || item.created_at).getDate()}
                    </strong>
                  </div>
                  <div>
                    <h3>
                      <button onClick={() => open(item)}>{item.name}</button>
                    </h3>
                    <small>
                      {dataOf(item).date} · 第 {item.version} 版
                    </small>
                  </div>
                  <button
                    className="icon-button"
                    aria-label="刪除工作記錄"
                    onClick={() => setRemove(item)}
                  >
                    <Trash2 size={15} />
                  </button>
                </header>
                {[
                  ["outcomes", "成果"],
                  ["decisions", "決定"],
                  ["todos", "下一步"],
                ].map(([key, title]) => (
                  <section key={key}>
                    <h4>{title}</h4>
                    {dataOf(item)[key]?.length ? (
                      <ul>
                        {dataOf(item)
                          [key].slice(0, 3)
                          .map((entry: any, i: number) => (
                            <li
                              key={
                                typeof entry === "object" ? entry.id || i : i
                              }
                              className={
                                key === "todos" ? "workday-todo" : undefined
                              }
                            >
                              {key === "todos" && (
                                <input
                                  aria-label={`完成待辦：${typeof entry === "string" ? entry : entry.text || entry.title}`}
                                  type="checkbox"
                                  checked={
                                    typeof entry === "object" &&
                                    !!entry.completed
                                  }
                                  disabled={pending.includes(item.id)}
                                  onChange={(e) =>
                                    void toggleTodo(item, i, e.target.checked)
                                  }
                                />
                              )}
                              <span
                                className={
                                  key === "todos" && entry.completed
                                    ? "completed"
                                    : undefined
                                }
                              >
                                {typeof entry === "string"
                                  ? entry
                                  : entry.text ||
                                    entry.title ||
                                    JSON.stringify(entry)}
                              </span>
                            </li>
                          ))}
                      </ul>
                    ) : (
                      <p className="muted">未有記錄</p>
                    )}
                  </section>
                ))}
                <button
                  className="business-text-button"
                  onClick={() => open(item)}
                >
                  檢視及編輯
                  <ArrowUpRight size={15} />
                </button>
              </article>
            ))}
        </div>
      )}
      {edit && (
        <Modal
          title={edit === "new" ? "記錄每日工作" : "編輯工作記錄"}
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
                  date,
                  ...Object.fromEntries(
                    ["outcomes", "decisions", "references", "todos"].map(
                      (k) => [
                        k,
                        String(draft[k] || "")
                          .split("\n")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      ],
                    ),
                  ),
                };
                if (edit === "new") await createResource("workday", name, data);
                else await updateResource(edit, data, name);
                void records.refresh();
                setEdit(null);
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <div className="business-form-grid">
              <FormField label="標題">
                <input
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </FormField>
              <FormField label="工作日期">
                <input
                  type="date"
                  required
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                />
              </FormField>
            </div>
            <div className="business-form-grid">
              {[
                ["outcomes", "成果"],
                ["decisions", "重要決定"],
                ["references", "參考資料"],
                ["todos", "待辦及下一步"],
              ].map(([key, title]) => (
                <FormField key={key} label={title} hint="每行一項">
                  <textarea
                    rows={5}
                    value={draft[key] || ""}
                    onChange={(e) =>
                      setDraft({ ...draft, [key]: e.target.value })
                    }
                  />
                </FormField>
              ))}
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
                {busy ? "儲存中…" : "儲存工作記錄"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void records.refresh()}
        />
      )}
    </div>
  );
}

function CalendarPage() {
  const events = useResources("event"),
    [connections, setConnections] = useState<Data[]>([]),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [draft, setDraft] = useState<Data>({}),
    [error, setError] = useState(""),
    [info, setInfo] = useState(""),
    [busy, setBusy] = useState(false),
    [remove, setRemove] = useState<Resource | null>(null),
    [disconnect, setDisconnect] = useState<Data | null>(null),
    [view, setView] = useState("month");
  const loadConnections = useCallback(async () => {
    try {
      setConnections(await api<Data[]>("/calendar/connections"));
    } catch (e) {
      setError(errorText(e));
    }
  }, []);
  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);
  function open(item: Resource | "new", date = day()) {
    setEdit(item);
    setDraft(
      item === "new"
        ? {
            name: "",
            start: `${date}T09:00`,
            end: `${date}T10:00`,
            timezone:
              Intl.DateTimeFormat().resolvedOptions().timeZone ||
              "Asia/Hong_Kong",
            all_day: false,
            description: "",
            location: "",
            rrule: "",
          }
        : {
            ...dataOf(item),
            name: item.name,
            start: new Date(dataOf(item).start)
              .toLocaleString("sv-SE")
              .slice(0, 16)
              .replace(" ", "T"),
            end: new Date(dataOf(item).end)
              .toLocaleString("sv-SE")
              .slice(0, 16)
              .replace(" ", "T"),
          },
    );
    setError("");
  }
  useOpenResource(["event"], open, setError);
  async function connect(provider: string) {
    setBusy(true);
    setError("");
    try {
      const result = await api<Data>("/calendar/connections", {
        method: "POST",
        body: JSON.stringify({ provider }),
      });
      if (result.authorization_url)
        window.location.assign(result.authorization_url);
      else {
        setInfo(result.message || "連接設定已儲存。");
        void loadConnections();
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="時間與計劃"
        title="日曆"
        description="安排公司日程，連接個人及外部工作日曆。"
        actions={
          <>
            <a
              className="button secondary"
              href="/api/v1/calendar/ics"
              download
            >
              <ArrowDownToLine size={16} />
              匯出 ICS
            </a>
            <button className="button primary" onClick={() => open("new")}>
              <Plus size={16} />
              新增日程
            </button>
          </>
        }
      />
      <Banner
        error={error || events.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      <div className="calendar-layout">
        <div>
          <div className="business-viewbar">
            <div className="business-view-tabs">
              <button
                className={view === "month" ? "active" : ""}
                onClick={() => setView("month")}
              >
                <CalendarDays size={16} />
                月份
              </button>
              <button
                className={view === "list" ? "active" : ""}
                onClick={() => setView("list")}
              >
                <List size={16} />
                日程列表
              </button>
            </div>
            <button
              className="icon-button"
              title="重新整理"
              onClick={() => void events.refresh()}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          {view === "month" ? (
            <MiniCalendar
              items={events.items.map((item) => ({
                id: item.id,
                name: item.name,
                start: dataOf(item).start,
                rrule: dataOf(item).rrule,
                timezone: dataOf(item).timezone,
                item,
              }))}
              onItem={(event) => open(event.item)}
              onDay={(date) => open("new", date)}
            />
          ) : (
            <div className="calendar-agenda">
              {[...events.items]
                .sort((a, b) =>
                  String(dataOf(a).start).localeCompare(
                    String(dataOf(b).start),
                  ),
                )
                .map((item) => (
                  <button
                    className="agenda-row"
                    key={item.id}
                    onClick={() => open(item)}
                  >
                    <span className="agenda-dot" />
                    <div>
                      <strong>{item.name}</strong>
                      <span>{dataOf(item).location || "未設地點"}</span>
                    </div>
                    <time>
                      {time(dataOf(item).start)} —{" "}
                      {new Date(dataOf(item).end).toLocaleTimeString("zh-HK", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  </button>
                ))}
              {!events.items.length && (
                <Empty
                  title="未有日程"
                  detail="新增公司活動、會議或個人安排。"
                />
              )}
            </div>
          )}
        </div>
        <aside className="calendar-connections">
          <h3>連接嘅日曆</h3>
          <p>授權後可同步外部日程。憑證由管理員配置並保存在伺服器。</p>
          {connections.map((connection) => (
            <div className="calendar-connection" key={connection.id}>
              <span className={`provider-mark ${connection.provider}`}>
                {connection.provider === "google" ? "G" : "M"}
              </span>
              <div>
                <strong>
                  {connection.provider === "google"
                    ? "Google Calendar"
                    : "Microsoft Outlook"}
                </strong>
                <small>
                  {connection.status || "等待授權"}
                  {connection.last_sync_at &&
                    ` · ${time(connection.last_sync_at)}`}
                </small>
              </div>
              <button
                className="icon-button"
                title="同步日曆"
                onClick={async () => {
                  try {
                    await api(`/calendar/connections/${connection.id}/sync`, {
                      method: "POST",
                      body: "{}",
                    });
                    setInfo("同步工作已排入佇列。");
                    void loadConnections();
                  } catch (e) {
                    setError(errorText(e));
                  }
                }}
              >
                <RefreshCw size={15} />
              </button>
              <button
                className="icon-button"
                title="解除日曆連接"
                onClick={() => setDisconnect(connection)}
              >
                <X size={15} />
              </button>
            </div>
          ))}
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void connect("google")}
          >
            <span className="provider-mark google">G</span>連接 Google
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void connect("microsoft")}
          >
            <span className="provider-mark microsoft">M</span>連接 Microsoft
          </button>
        </aside>
      </div>
      {edit && (
        <Modal
          title={edit === "new" ? "新增日程" : "編輯日程"}
          onClose={() => setEdit(null)}
        >
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              try {
                const data: Data = {
                  ...draft,
                  start: new Date(draft.start).toISOString(),
                  end: new Date(draft.end).toISOString(),
                };
                delete data.name;
                if (edit === "new")
                  await createResource("event", draft.name, data);
                else await updateResource(edit, data, draft.name);
                setEdit(null);
                void events.refresh();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {edit !== "new" && draft.sync_conflict && (
              <div className="business-conflict-notice" role="alert">
                <strong>日曆同步有衝突</strong>
                <p>
                  此日程在本地及外部日曆都有修改。選擇要保留的版本後再同步。
                </p>
                <small>{draft.sync_conflict.reason || "外部版本已變更"}</small>
                <div className="business-actions">
                  {(["local", "remote"] as const).map((resolution) => (
                    <button
                      key={resolution}
                      type="button"
                      className="button secondary"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true);
                        setError("");
                        try {
                          await api(`/calendar/events/${edit.id}/resolve`, {
                            method: "POST",
                            body: JSON.stringify({
                              resolution,
                              expected_version: edit.version,
                            }),
                          });
                          setInfo(
                            resolution === "local"
                              ? "已保留本地版本，外部同步工作已排入佇列。"
                              : "已採用外部日曆版本。",
                          );
                          setEdit(null);
                          void events.refresh();
                        } catch (failure) {
                          setError(errorText(failure));
                        } finally {
                          setBusy(false);
                        }
                      }}
                    >
                      {resolution === "local" ? "保留本地日程" : "採用外部日程"}
                    </button>
                  ))}
                </div>
              </div>
            )}
            <FormField label="名稱">
              <input
                autoFocus
                required
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
            </FormField>
            <div className="business-form-grid">
              <FormField label="開始">
                <input
                  type="datetime-local"
                  required
                  value={draft.start}
                  onChange={(e) =>
                    setDraft({ ...draft, start: e.target.value })
                  }
                />
              </FormField>
              <FormField label="結束">
                <input
                  type="datetime-local"
                  required
                  value={draft.end}
                  onChange={(e) => setDraft({ ...draft, end: e.target.value })}
                />
              </FormField>
            </div>
            <div className="business-form-grid">
              <FormField label="地點">
                <input
                  value={draft.location || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, location: e.target.value })
                  }
                />
              </FormField>
              <FormField label="重複">
                <select
                  value={draft.rrule || ""}
                  onChange={(e) =>
                    setDraft({ ...draft, rrule: e.target.value })
                  }
                >
                  <option value="">不重複</option>
                  <option value="FREQ=DAILY">每日</option>
                  <option value="FREQ=WEEKLY">每週</option>
                  <option value="FREQ=MONTHLY">每月</option>
                  <option value="FREQ=YEARLY">每年</option>
                </select>
              </FormField>
            </div>
            <FormField label="說明">
              <textarea
                rows={3}
                value={draft.description || ""}
                onChange={(e) =>
                  setDraft({ ...draft, description: e.target.value })
                }
              />
            </FormField>
            <label className="business-check">
              <input
                type="checkbox"
                checked={draft.all_day || false}
                onChange={(e) =>
                  setDraft({ ...draft, all_day: e.target.checked })
                }
              />
              全日活動
            </label>
            <Banner error={error} />
            <div className="business-form-footer">
              {edit !== "new" && (
                <button
                  type="button"
                  className="button danger"
                  onClick={() => {
                    setRemove(edit);
                    setEdit(null);
                  }}
                >
                  刪除
                </button>
              )}
              <button
                type="button"
                className="button secondary"
                onClick={() => setEdit(null)}
              >
                取消
              </button>
              <button className="button primary" disabled={busy}>
                {busy ? "儲存中…" : "儲存日程"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void events.refresh()}
        />
      )}
      {disconnect && (
        <Modal title="解除日曆連接" onClose={() => setDisconnect(null)}>
          <p>解除後不再同步此帳戶。已匯入的日程會保留在工作空間。</p>
          <Banner error={error} />
          <div className="business-form-footer">
            <button
              className="button secondary"
              onClick={() => setDisconnect(null)}
            >
              取消
            </button>
            <button
              className="button danger"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await api(
                    `/calendar/connections/${disconnect.id}/disconnect`,
                    { method: "POST", body: "{}" },
                  );
                  setDisconnect(null);
                  setInfo("日曆連接已解除。");
                  void loadConnections();
                } catch (failure) {
                  setError(errorText(failure));
                } finally {
                  setBusy(false);
                }
              }}
            >
              解除連接
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function FormsPage() {
  const forms = useResources("form"),
    submissions = useResources("submission"),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [remove, setRemove] = useState<Resource | null>(null),
    [selected, setSelected] = useState<Resource | null>(null),
    [info, setInfo] = useState(""),
    [error, setError] = useState("");
  useOpenResource(["form"], setEdit, setError);
  async function publish(item: Resource) {
    try {
      const result = await api<Data>(`/forms/${item.id}/publish`, {
        method: "POST",
        body: JSON.stringify({ published: !dataOf(item).published }),
      });
      if (result.url && !dataOf(item).published)
        setInfo(`表單已發佈：${result.url}`);
      void forms.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="收集資料，啟動工作"
        title="表單"
        description="建立公開或內部表單，將提交資料接入公司工作流程。"
        actions={
          <button className="button primary" onClick={() => setEdit("new")}>
            <Plus size={16} />
            建立表單
          </button>
        }
      />
      <Banner
        error={error || forms.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      {!forms.items.length && !forms.loading ? (
        <Empty
          icon={<FileText size={30} />}
          title="由一份表單開始"
          detail="收集客戶查詢、申請或內部需求，使用同一套欄位驗證及權限。"
          action={
            <button className="button primary" onClick={() => setEdit("new")}>
              建立第一份表單
            </button>
          }
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>名稱</th>
                <th>狀態</th>
                <th>對象</th>
                <th>提交</th>
                <th>最後更新</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {forms.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      className="business-cell-link"
                      onClick={() => setEdit(item)}
                    >
                      <FileText size={18} />
                      {item.name}
                    </button>
                  </td>
                  <td>
                    <span
                      className={`business-status ${dataOf(item).published ? "completed" : "draft"}`}
                    >
                      {dataOf(item).published ? "已發佈" : "草稿"}
                    </span>
                  </td>
                  <td>
                    {dataOf(item).access === "public" ? "公開連結" : "公司成員"}
                  </td>
                  <td>
                    <button
                      className="business-text-button"
                      onClick={() => setSelected(item)}
                    >
                      {
                        submissions.items.filter(
                          (s) => dataOf(s).form_id === item.id,
                        ).length
                      }{" "}
                      筆提交
                    </button>
                  </td>
                  <td className="muted">{time(item.updated_at)}</td>
                  <td>
                    <div className="business-actions">
                      <button
                        className="button secondary compact"
                        onClick={() => void publish(item)}
                      >
                        {dataOf(item).published ? "停用" : "發佈"}
                      </button>
                      {dataOf(item).published && (
                        <a
                          className="icon-button"
                          title="開啟表單"
                          target="_blank"
                          rel="noreferrer"
                          href={dataOf(item).public_url || `/forms/${item.id}`}
                        >
                          <ExternalLink size={16} />
                        </a>
                      )}
                      <button
                        className="icon-button"
                        title="編輯表單"
                        onClick={() => setEdit(item)}
                      >
                        <Settings2 size={16} />
                      </button>
                      <button
                        className="icon-button"
                        title="刪除表單"
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
      {edit && (
        <SchemaModal
          key={edit === "new" ? "new" : edit.id}
          kind="form"
          item={edit === "new" ? undefined : edit}
          onClose={() => setEdit(null)}
          onDone={() => void forms.refresh()}
        />
      )}
      {remove && (
        <ConfirmDelete
          item={remove}
          onClose={() => setRemove(null)}
          onDone={() => void forms.refresh()}
        />
      )}
      {selected && (
        <Modal
          title={`${selected.name} · 提交記錄`}
          onClose={() => setSelected(null)}
          wide
        >
          {submissions.items.filter((s) => dataOf(s).form_id === selected.id)
            .length ? (
            submissions.items
              .filter((s) => dataOf(s).form_id === selected.id)
              .map((item) => (
                <div className="submission-card" key={item.id}>
                  <header>
                    <strong>{item.name}</strong>
                    <time>{time(item.created_at)}</time>
                  </header>
                  {Object.entries(dataOf(item).values || {}).map(
                    ([key, value]) => (
                      <p key={key}>
                        <span>
                          {(dataOf(selected).fields || []).find(
                            (f: Field) => f.id === key,
                          )?.name || key}
                        </span>
                        <strong>{displayValue(value)}</strong>
                      </p>
                    ),
                  )}
                </div>
              ))
          ) : (
            <Empty
              title="未有提交記錄"
              detail="表單發佈並收到正式提交後，結果會顯示喺呢度。"
            />
          )}
        </Modal>
      )}
    </div>
  );
}

export function PublicFormPage({ id }: { id: string }) {
  const [form, setForm] = useState<Resource | null>(null),
    [values, setValues] = useState<Data>({}),
    [error, setError] = useState(""),
    [done, setDone] = useState(false),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    void api<Resource>(`/public/forms/${id}${window.location.search}`)
      .then(setForm)
      .catch(async (first) => {
        try {
          const resource = await api<Resource>(`/resource/${id}`);
          if (resource.kind !== "form" || !dataOf(resource).published)
            throw first;
          setForm(resource);
        } catch {
          setError(errorText(first));
        }
      });
  }, [id]);
  return (
    <main className="public-form-page">
      <div className="public-form-brand">
        <Database size={22} />
        藍圖工作室
      </div>
      <section className="public-form-card">
        {done ? (
          <Empty
            icon={<Check size={32} />}
            title="提交成功"
            detail="你嘅資料已安全保存。"
          />
        ) : form ? (
          <>
            <h1>{form.name}</h1>
            <p>{dataOf(form).description}</p>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                try {
                  await api(
                    dataOf(form).access === "company"
                      ? `/forms/${id}/submit`
                      : `/public/forms/${id}${window.location.search}`,
                    {
                      method: "POST",
                      body: JSON.stringify({
                        values: activeFormValues(dataOf(form), values),
                        submission_key: crypto.randomUUID(),
                      }),
                    },
                  );
                  setDone(true);
                } catch (e) {
                  setError(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {(dataOf(form).fields || []).map((field: Field) => {
                const state = formFieldState(dataOf(form), values, field.id);
                if (state.hidden) return null;
                return (
                  <fieldset
                    key={field.id}
                    disabled={state.disabled}
                    className={state.disabled ? "form-field-disabled" : ""}
                  >
                    <FormField
                      label={
                        field.name +
                        (field.required && !state.disabled ? " *" : "")
                      }
                    >
                      <ValueInput
                        formFile
                        field={field}
                        value={values[field.id]}
                        onChange={(value) =>
                          setValues({ ...values, [field.id]: value })
                        }
                      />
                    </FormField>
                  </fieldset>
                );
              })}
              <Banner error={error} />
              <button className="button primary" disabled={busy}>
                {busy ? "提交中…" : dataOf(form).submit_label || "提交"}
              </button>
            </form>
          </>
        ) : (
          <>
            <Banner error={error} info={error ? "" : "正在載入表單…"} />
            {error && (
              <a className="button secondary" href="/">
                返回工作空間登入
              </a>
            )}
          </>
        )}
      </section>
      <small>由藍圖工作室後台提供</small>
    </main>
  );
}

export function BusinessPage({ section }: { section: string }) {
  if (section === "tasks") return <TasksPage />;
  if (section === "workdays") return <WorkdaysPage />;
  if (section === "calendar") return <CalendarPage />;
  if (section === "forms") return <FormsPage />;
  return <TablesPage />;
}
