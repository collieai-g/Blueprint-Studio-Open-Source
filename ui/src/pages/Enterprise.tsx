import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Activity,
  Archive,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  ChevronRight,
  Download,
  FileCheck2,
  FileSignature,
  FileText,
  Folder,
  LockKeyhole,
  Plus,
  RefreshCw,
  Search,
  Send,
  Shield,
  ShieldAlert,
  ShieldCheck,
  X,
} from "lucide-react";
import { api, type Resource } from "../api";
import {
  Banner,
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

function FilePicker({
  items,
  value,
  onChange,
  multiple = false,
}: {
  items: Resource[];
  value: string[];
  onChange: (ids: string[]) => void;
  multiple?: boolean;
}) {
  const [query, setQuery] = useState("");
  return (
    <div className="enterprise-file-picker">
      <div className="business-search">
        <Search size={16} />
        <input
          placeholder="搜尋文件或資料夾"
          aria-label="搜尋選取文件"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      <div>
        {items
          .filter((item) =>
            item.name.toLowerCase().includes(query.toLowerCase()),
          )
          .map((item) => (
            <label key={item.id}>
              <input
                type={multiple ? "checkbox" : "radio"}
                name="resource-pick"
                checked={value.includes(item.id)}
                onChange={(e) =>
                  onChange(
                    multiple
                      ? e.target.checked
                        ? [...value, item.id]
                        : value.filter((id) => id !== item.id)
                      : [item.id],
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
                <small>
                  {item.kind === "folder"
                    ? "資料夾"
                    : dataOf(item).mime || "文件"}
                </small>
              </span>
            </label>
          ))}
        {!items.length && <p className="muted">先上傳文件，再使用此功能。</p>}
      </div>
    </div>
  );
}

function SignaturePage() {
  const [items, setItems] = useState<Resource[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [creating, setCreating] = useState(false),
    [name, setName] = useState(""),
    [fileId, setFileId] = useState<string[]>([]),
    [recipients, setRecipients] = useState<
      { name: string; email: string; role: string }[]
    >([{ name: "", email: "", role: "SIGNER" }]),
    [busy, setBusy] = useState(false),
    [selected, setSelected] = useState<Resource | null>(null),
    [filter, setFilter] = useState("all"),
    [info, setInfo] = useState("");
  const files = useResources("file");
  useOpenResource(["sign_request"], setSelected, setError);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api<Resource[]>("/sign/requests"));
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const labels: Data = {
    DRAFT: "草稿",
    PENDING: "等待簽署",
    COMPLETED: "已完成",
    REJECTED: "已拒絕",
    EXPIRED: "已到期",
    draft: "草稿",
    pending: "等待簽署",
    completed: "已完成",
  };
  const status = (item: Resource) =>
    String(dataOf(item).status || dataOf(item).provider_status || "draft");
  const filtered = items.filter(
    (item) => filter === "all" || status(item).toLowerCase() === filter,
  );
  return (
    <div className="business-page">
      <Header
        eyebrow="由文件到完成簽署"
        title="電子簽署"
        description="發出簽署請求、追蹤收件人進度，保存完成文件及技術證據。"
        actions={
          <>
            <button className="button secondary" onClick={() => void refresh()}>
              <RefreshCw size={16} />
              重新整理
            </button>
            <button
              className="button primary"
              onClick={() => {
                setCreating(true);
                setName("");
                setFileId([]);
                setRecipients([{ name: "", email: "", role: "SIGNER" }]);
              }}
            >
              <Plus size={16} />
              建立簽署請求
            </button>
          </>
        }
      />
      <Banner
        error={error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      <div className="enterprise-service-note">
        <FileSignature size={21} />
        <div>
          <strong>連接自行託管的簽署服務</strong>
          <p>
            使用 Documenso Community Edition
            API。未完成配置時，請求不會假裝已寄出。自簽憑證提供完整性驗證，公共受信憑證可另行接駁。
          </p>
        </div>
      </div>
      <div className="business-viewbar">
        <div className="business-view-tabs">
          {[
            ["all", "全部請求"],
            ["pending", "等待簽署"],
            ["completed", "已完成"],
            ["draft", "草稿"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={filter === id ? "active" : ""}
              onClick={() => setFilter(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="muted">{filtered.length} 個請求</span>
      </div>
      {loading ? (
        <div className="business-loading">正在載入簽署請求…</div>
      ) : !filtered.length ? (
        <Empty
          icon={<FileSignature size={32} />}
          title="讓簽署流程清晰可追蹤"
          detail="選擇公司文件及收件人，在已配置的自架服務建立真正的簽署請求。"
          action={
            <button
              className="button primary"
              onClick={() => setCreating(true)}
            >
              建立簽署請求
            </button>
          }
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>文件及請求</th>
                <th>狀態</th>
                <th>收件人</th>
                <th>建立時間</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      className="business-cell-link"
                      onClick={() => setSelected(item)}
                    >
                      <FileSignature size={20} />
                      <span>
                        {item.name}
                        <small>{item.id.slice(0, 8)}</small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span
                      className={`business-status ${status(item).toLowerCase()}`}
                    >
                      {labels[status(item)] || status(item)}
                    </span>
                  </td>
                  <td>
                    {(dataOf(item).recipients || [])
                      .map((r: Data) => r.name || r.email)
                      .join("、") || "—"}
                  </td>
                  <td>{time(item.created_at)}</td>
                  <td>
                    <button
                      className="button secondary compact"
                      onClick={async () => {
                        try {
                          setSelected(
                            await api<Resource>(`/sign/requests/${item.id}`),
                          );
                        } catch (e) {
                          setError(errorText(e));
                        }
                      }}
                    >
                      檢視詳情
                      <ChevronRight size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && (
        <Modal title="建立簽署請求" onClose={() => setCreating(false)} wide>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!fileId[0]) {
                setError("請選擇需要簽署的文件。");
                return;
              }
              setBusy(true);
              try {
                const result = await api<Resource>("/sign/requests", {
                  method: "POST",
                  body: JSON.stringify({
                    name,
                    file_id: fileId[0],
                    recipients,
                  }),
                });
                setCreating(false);
                setSelected(result);
                setInfo("已建立簽署請求，正準備送出。請重新整理詳情查看狀態。");
                void refresh();
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <FormField label="請求名稱">
              <input
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：服務協議簽署"
              />
            </FormField>
            <div className="business-label">選擇文件</div>
            <FilePicker
              items={files.items}
              value={fileId}
              onChange={setFileId}
            />
            <div className="recipient-list-header">
              <strong>簽署人</strong>
              <button
                type="button"
                className="business-text-button"
                onClick={() =>
                  setRecipients([
                    ...recipients,
                    { name: "", email: "", role: "SIGNER" },
                  ])
                }
              >
                <Plus size={15} />
                新增簽署人
              </button>
            </div>
            {recipients.map((recipient, index) => (
              <div className="signature-recipient" key={index}>
                <span>{index + 1}</span>
                <input
                  required
                  aria-label="簽署人姓名"
                  placeholder="姓名"
                  value={recipient.name}
                  onChange={(e) =>
                    setRecipients(
                      recipients.map((r, i) =>
                        i === index ? { ...r, name: e.target.value } : r,
                      ),
                    )
                  }
                />
                <input
                  required
                  type="email"
                  aria-label="簽署人電郵"
                  placeholder="email@example.com"
                  value={recipient.email}
                  onChange={(e) =>
                    setRecipients(
                      recipients.map((r, i) =>
                        i === index ? { ...r, email: e.target.value } : r,
                      ),
                    )
                  }
                />
                {recipients.length > 1 && (
                  <button
                    type="button"
                    className="icon-button"
                    title="移除簽署人"
                    onClick={() =>
                      setRecipients(recipients.filter((_, i) => i !== index))
                    }
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            ))}
            <Banner error={error} />
            <div className="business-form-footer">
              <button
                className="button secondary"
                type="button"
                onClick={() => setCreating(false)}
              >
                取消
              </button>
              <button
                className="button primary"
                disabled={busy || !fileId.length}
              >
                <Send size={16} />
                {busy ? "建立中…" : "建立簽署請求"}
              </button>
            </div>
          </form>
        </Modal>
      )}
      {selected && (
        <Modal title={selected.name} onClose={() => setSelected(null)} wide>
          <div className="signature-detail">
            <span
              className={`business-status ${status(selected).toLowerCase()}`}
            >
              {labels[status(selected)] || status(selected)}
            </span>
            <p>建立於 {time(selected.created_at)}</p>
            <div className="business-actions">
              <button
                className="button secondary compact"
                onClick={async () => {
                  try {
                    await api(`/sign/requests/${selected.id}/refresh`, {
                      method: "POST",
                      body: "{}",
                    });
                    setInfo("已安排更新服務狀態，請稍後重新開啟詳情。");
                  } catch (e) {
                    setError(errorText(e));
                  }
                }}
              >
                <RefreshCw size={15} />
                向簽署服務更新狀態
              </button>
              {!["completed", "COMPLETED", "cancelled", "CANCELLED"].includes(
                status(selected),
              ) && (
                <button
                  className="button danger compact"
                  onClick={async () => {
                    try {
                      const result = await api<Resource>(
                        `/sign/requests/${selected.id}/cancel`,
                        { method: "POST", body: "{}" },
                      );
                      setSelected(result);
                      void refresh();
                    } catch (e) {
                      setError(errorText(e));
                    }
                  }}
                >
                  取消請求
                </button>
              )}
            </div>
            <div className="signature-detail-recipients">
              {(
                dataOf(selected).provider_state?.recipients ||
                dataOf(selected).recipients ||
                []
              ).map((recipient: Data, index: number) => (
                <div key={index}>
                  <span className="signature-avatar">
                    {String(recipient.name || recipient.email)
                      .charAt(0)
                      .toUpperCase()}
                  </span>
                  <div>
                    <strong>{recipient.name}</strong>
                    <span>{recipient.email}</span>
                  </div>
                  <span className="business-status">
                    {recipient.signingStatus || recipient.status || "待處理"}
                  </span>
                </div>
              ))}
            </div>
            {["completed", "COMPLETED"].includes(status(selected)) && (
              <div className="business-actions">
                <a
                  className="button primary"
                  href={`/api/v1/sign/requests/${selected.id}/download`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={16} />
                  下載完成文件
                </a>
                <a
                  className="button secondary"
                  href={`/api/v1/sign/requests/${selected.id}/evidence`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <FileCheck2 size={16} />
                  簽署證據
                </a>
              </div>
            )}
            <details>
              <summary>服務記錄</summary>
              <pre className="business-code">
                {JSON.stringify(
                  dataOf(selected).provider_response ||
                    dataOf(selected).evidence ||
                    dataOf(selected),
                  null,
                  2,
                )}
              </pre>
            </details>
          </div>
        </Modal>
      )}
    </div>
  );
}

function DocumentGenerationPage() {
  const templates = useResources("document_template"),
    [templateId, setTemplateId] = useState(""),
    [name, setName] = useState(""),
    [template, setTemplate] = useState(""),
    [values, setValues] = useState("{}"),
    [format, setFormat] = useState("docx"),
    [error, setError] = useState(""),
    [info, setInfo] = useState(""),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<Resource | null>(null);
  function loadTemplate(id: string) {
    setTemplateId(id);
    const item = templates.items.find((t) => t.id === id);
    if (item) {
      setTemplate(dataOf(item).template || "");
      setValues(JSON.stringify(dataOf(item).default_values || {}, null, 2));
      if (!name) setName(item.name);
    }
  }
  async function saveTemplate() {
    setBusy(true);
    try {
      const payload = JSON.parse(values || "{}");
      const item = templates.items.find((t) => t.id === templateId);
      const data = { template, default_values: payload };
      const saved = item
        ? await updateResource(item, data, name || item.name)
        : await createResource("document_template", name || "文件範本", data);
      setTemplateId(saved.id);
      await templates.refresh();
      setInfo("範本已儲存，可在之後重用。");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="由資料產生正式文件"
        title="文件生成"
        description="以範本、變數、條件及清單生成可下載的 DOCX 或 PDF。"
        actions={
          <button
            className="button secondary"
            disabled={!template.trim() || busy}
            onClick={() => void saveTemplate()}
          >
            <Plus size={16} />
            儲存為範本
          </button>
        }
      />
      <Banner
        error={error || templates.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setResult(null);
          try {
            const result = await api<Resource>("/documents/generate", {
              method: "POST",
              body: JSON.stringify({
                name,
                template,
                values: JSON.parse(values || "{}"),
                format,
              }),
            });
            setResult(result);
            setInfo("文件已生成並保存到文件庫。");
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="docgen-layout">
          <section className="docgen-main panel">
            <div className="docgen-section-header">
              <FileText size={19} />
              <h3>文件範本</h3>
              <select
                value={templateId}
                onChange={(e) => loadTemplate(e.target.value)}
              >
                <option value="">建立新範本</option>
                {templates.items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </div>
            <FormField label="文件名稱">
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="例如：專案服務建議書"
              />
            </FormField>
            <FormField
              label="範本內容"
              hint={
                "使用 {{ company }} 插入欄位；{% for item in items %}…{% endfor %} 建立清單。"
              }
            >
              <textarea
                required
                className="docgen-template-input business-code"
                rows={18}
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
                placeholder={
                  "{{ title }}\n\n致 {{ client_name }}：\n\n{{ introduction }}\n\n{% for item in services %}\n• {{ item.name }}：{{ item.description }}\n{% endfor %}"
                }
              />
            </FormField>
          </section>
          <aside className="docgen-sidebar">
            <div className="panel">
              <h3>輸入資料</h3>
              <FormField label="變數（JSON）">
                <textarea
                  className="business-code"
                  rows={12}
                  value={values}
                  onChange={(e) => setValues(e.target.value)}
                />
              </FormField>
              <FormField label="輸出格式">
                <select
                  value={format}
                  onChange={(e) => setFormat(e.target.value)}
                >
                  <option value="docx">Word 文件（.docx）</option>
                  <option value="pdf">PDF 文件（需轉檔服務）</option>
                </select>
              </FormField>
              <button
                className="button primary"
                disabled={busy || !template.trim()}
              >
                <FileCheck2 size={17} />
                {busy ? "正在生成…" : "生成文件"}
              </button>
            </div>
            {result && (
              <div className="docgen-result">
                <Check size={24} />
                <h3>文件已準備好</h3>
                <p>{result.name}</p>
                <a
                  className="button primary"
                  href={`/api/v1/files/${result.id}/download`}
                >
                  <Download size={16} />
                  下載文件
                </a>
                <a
                  className="business-text-button"
                  target="_blank"
                  rel="noreferrer"
                  href={`/api/v1/files/${result.id}/preview`}
                >
                  開啟預覽
                  <ArrowUpRight size={14} />
                </a>
              </div>
            )}
          </aside>
        </div>
      </form>
    </div>
  );
}

function PolicyModal({
  item,
  kind,
  onClose,
  onDone,
  targets,
}: {
  item?: Resource;
  kind: string;
  onClose: () => void;
  onDone: () => void;
  targets: Resource[];
}) {
  const d = dataOf(item),
    [name, setName] = useState(item?.name || ""),
    [ids, setIds] = useState<string[]>(d.resource_ids || []),
    [all, setAll] = useState(!!d.all_resources),
    [classification, setClassification] = useState(
      d.classification || "restricted",
    ),
    [patterns, setPatterns] = useState<string>((d.patterns || []).join("\n")),
    [blocked, setBlocked] = useState<string[]>(d.blocked_actions || ["share"]),
    [retainUntil, setRetainUntil] = useState(
      d.retain_until
        ? new Date(d.retain_until)
            .toLocaleString("sv-SE")
            .slice(0, 16)
            .replace(" ", "T")
        : "",
    ),
    [active, setActive] = useState(d.active !== false),
    [watermark, setWatermark] = useState(!!d.watermark),
    [preventPrint, setPreventPrint] = useState(!!d.prevent_print),
    [groups, setGroups] = useState<string>((d.allowed_groups || []).join(", ")),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const title: Data = {
    classification: "分類及資料防護規則",
    retention: "保存政策",
    legal_hold: "法務保全",
    archive: "企業歸檔",
  };
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (!all && !ids.length) throw new Error("請選擇適用文件或資料夾。");
      const data: Data = {
        ...d,
        active,
        resource_ids: ids,
        all_resources: all,
      };
      if (kind === "classification")
        Object.assign(data, {
          classification,
          patterns: patterns
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
          blocked_actions: blocked,
          watermark,
          prevent_print: preventPrint,
          allowed_groups: groups
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
        });
      if (kind === "retention")
        data.retain_until = new Date(retainUntil).toISOString();
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
      title={`${item ? "設定" : "建立"}${title[kind] || "政策"}`}
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
          />
        </FormField>
        {kind === "classification" && (
          <>
            <FormField label="資料分類">
              <select
                value={classification}
                onChange={(e) => setClassification(e.target.value)}
              >
                <option value="public">公開</option>
                <option value="internal">內部</option>
                <option value="confidential">機密</option>
                <option value="restricted">嚴格限制</option>
              </select>
            </FormField>
            <FormField
              label="內容比對規則"
              hint="每行一個正則表達式；留空時按適用範圍直接套用。"
            >
              <textarea
                rows={3}
                className="business-code"
                value={patterns}
                onChange={(e) => setPatterns(e.target.value)}
              />
            </FormField>
            <div className="business-label">限制操作</div>
            <div className="business-inline-checks">
              {[
                ["download", "下載"],
                ["share", "分享"],
                ["preview", "預覽"],
                ["copy", "複製"],
              ].map(([id, label]) => (
                <label className="business-check" key={id}>
                  <input
                    type="checkbox"
                    checked={blocked.includes(id)}
                    onChange={(e) =>
                      setBlocked(
                        e.target.checked
                          ? [...blocked, id]
                          : blocked.filter((value) => value !== id),
                      )
                    }
                  />
                  {label}
                </label>
              ))}
            </div>
            <label className="business-check">
              <input
                type="checkbox"
                checked={watermark}
                onChange={(e) => setWatermark(e.target.checked)}
              />
              在受控預覽加入使用者及時間水印（需要轉檔服務）
            </label>
            <label className="business-check">
              <input
                type="checkbox"
                checked={preventPrint}
                onChange={(e) => setPreventPrint(e.target.checked)}
              />
              限制受控預覽列印
            </label>
            <FormField
              label="允許例外群組"
              hint="以逗號分隔群組 ID；留空不設定群組例外。"
            >
              <input
                value={groups}
                onChange={(e) => setGroups(e.target.value)}
              />
            </FormField>
          </>
        )}
        {kind === "retention" && (
          <>
            <FormField label="至少保存至">
              <input
                type="datetime-local"
                required
                value={retainUntil}
                onChange={(e) => setRetainUntil(e.target.value)}
              />
            </FormField>
            <div className="enterprise-policy-warning">
              <LockKeyhole size={17} />
              生效後不可縮短保存期限或移除適用範圍。
            </div>
          </>
        )}
        {kind === "legal_hold" && (
          <p className="enterprise-policy-warning">
            <ShieldCheck size={17} />
            保全期間阻止永久刪除，檔案及版本仍受原有存取權限控制。
          </p>
        )}
        {kind === "archive" && (
          <p className="enterprise-policy-warning">
            <Archive size={17} />
            歸檔會撤銷原有分享連結及一般協作存取。停用歸檔後可還原存取路徑，保存及保全政策繼續適用。
          </p>
        )}
        {kind !== "archive" && (
          <label className="business-check">
            <input
              type="checkbox"
              checked={all}
              onChange={(e) => setAll(e.target.checked)}
            />
            套用所有公司資料
          </label>
        )}
        {!all && (
          <>
            <div className="business-label">適用文件及資料夾</div>
            <FilePicker
              multiple
              items={targets}
              value={ids}
              onChange={setIds}
            />
          </>
        )}
        <label className="business-check">
          <input
            type="checkbox"
            checked={active}
            onChange={(e) => setActive(e.target.checked)}
          />
          啟用政策
        </label>
        <Banner error={error} />
        <div className="business-form-footer">
          <button type="button" className="button secondary" onClick={onClose}>
            取消
          </button>
          <button className="button primary" disabled={busy}>
            {busy ? "儲存中…" : "儲存政策"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function GovernancePage({
  initialKind = "retention",
}: {
  initialKind?: string;
}) {
  const [kind, setKind] = useState(initialKind),
    resources = useResources(kind),
    files = useResources("file"),
    folders = useResources("folder"),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [error, setError] = useState(""),
    [info, setInfo] = useState("");
  useEffect(() => {
    setKind(initialKind);
  }, [initialKind]);
  const archiveOnly = initialKind === "archive",
    title = archiveOnly ? "企業歸檔" : "資料治理";
  async function toggle(item: Resource) {
    try {
      await updateResource(item, {
        ...dataOf(item),
        active: dataOf(item).active === false,
      });
      setInfo(
        dataOf(item).active === false
          ? "政策已啟用。"
          : kind === "archive"
            ? "歸檔已停用。原檔、版本及其他保存政策保留。"
            : "政策已停用。",
      );
      void resources.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="保留公司需要的證據"
        title={title}
        description={
          archiveOnly
            ? "管理員集中保存不再日常協作的內容，保留版本及政策。"
            : "設定資料保存期限、法務保全及永久刪除限制。"
        }
        actions={
          <button className="button primary" onClick={() => setEdit("new")}>
            <Plus size={16} />
            {kind === "retention"
              ? "建立保存政策"
              : kind === "legal_hold"
                ? "建立法務保全"
                : kind === "archive"
                  ? "建立歸檔"
                  : "建立防護規則"}
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
      {!archiveOnly && (
        <div className="business-viewbar">
          <div className="business-view-tabs">
            {[
              ["retention", "保存政策"],
              ["legal_hold", "法務保全"],
              ["classification", "資料防護"],
            ].map(([id, label]) => (
              <button
                className={kind === id ? "active" : ""}
                key={id}
                onClick={() => setKind(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}
      {!resources.items.length && !resources.loading ? (
        <Empty
          icon={archiveOnly ? <Archive size={32} /> : <ShieldCheck size={32} />}
          title={
            archiveOnly ? "為長期內容建立受控歸檔" : "讓資料生命週期有清楚規則"
          }
          detail={
            archiveOnly
              ? "歸檔保留內容及版本，並收緊日常分享及協作存取。"
              : "政策會由後端操作及儲存層執行；設定不能只停留在介面。"
          }
          action={
            <button className="button primary" onClick={() => setEdit("new")}>
              建立{archiveOnly ? "歸檔" : "政策"}
            </button>
          }
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>政策名稱</th>
                <th>狀態</th>
                <th>適用範圍</th>
                <th>{kind === "retention" ? "保存期限" : "最後更新"}</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {resources.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      className="business-cell-link"
                      onClick={() => setEdit(item)}
                    >
                      {archiveOnly ? (
                        <Archive size={19} />
                      ) : (
                        <ShieldCheck size={19} />
                      )}
                      {item.name}
                    </button>
                  </td>
                  <td>
                    <span
                      className={`business-status ${dataOf(item).active !== false ? "completed" : "draft"}`}
                    >
                      {dataOf(item).active !== false ? "生效中" : "已停用"}
                    </span>
                  </td>
                  <td>
                    {dataOf(item).all_resources
                      ? "所有公司資料"
                      : `${(dataOf(item).resource_ids || []).length} 個文件／資料夾`}
                  </td>
                  <td>
                    {time(
                      kind === "retention"
                        ? dataOf(item).retain_until
                        : item.updated_at,
                    )}
                  </td>
                  <td>
                    <div className="business-actions">
                      <button
                        className="button secondary compact"
                        onClick={() => setEdit(item)}
                      >
                        設定
                      </button>
                      {kind !== "retention" && (
                        <button
                          className="business-text-button"
                          onClick={() => void toggle(item)}
                        >
                          {dataOf(item).active !== false
                            ? kind === "archive"
                              ? "還原歸檔"
                              : "停用"
                            : "啟用"}
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {edit && (
        <PolicyModal
          kind={kind}
          item={edit === "new" ? undefined : edit}
          onClose={() => setEdit(null)}
          onDone={() => void resources.refresh()}
          targets={[...folders.items, ...files.items]}
        />
      )}
    </div>
  );
}

function SecurityPage() {
  const alerts = useResources("alert"),
    classifications = useResources("classification"),
    files = useResources("file"),
    folders = useResources("folder"),
    [settings, setSettings] = useState<Data>({}),
    [tab, setTab] = useState("overview"),
    [edit, setEdit] = useState<Resource | "new" | null>(null),
    [error, setError] = useState(""),
    [selected, setSelected] = useState<Resource | null>(null),
    [info, setInfo] = useState("");
  useEffect(() => {
    void api<Data>("/settings")
      .then(setSettings)
      .catch((e) => setError(errorText(e)));
  }, []);
  const quarantined = files.items.filter(
      (item) =>
        dataOf(item).quarantined ||
        dataOf(item).scan?.status === "infected" ||
        dataOf(item).scan_status === "infected",
    ),
    activeAlerts = alerts.items.filter(
      (item) => dataOf(item).status !== "resolved",
    );
  return (
    <div className="business-page">
      <Header
        eyebrow="公司資料的保護中心"
        title="安全中心"
        description="檢視資料防護、掃描及異常警報，所有狀態均來自實際處理記錄。"
        actions={
          <button
            className="button secondary"
            onClick={() => {
              void alerts.refresh();
              void classifications.refresh();
              void files.refresh();
            }}
          >
            <RefreshCw size={16} />
            重新整理
          </button>
        }
      />
      <Banner
        error={error || alerts.error || classifications.error}
        info={info}
        onClose={() => {
          setError("");
          setInfo("");
        }}
      />
      <div className="business-viewbar">
        <div className="business-view-tabs">
          {[
            ["overview", "概覽"],
            ["rules", "資料防護規則"],
            ["alerts", "安全警報"],
            ["quarantine", "隔離檔案"],
          ].map(([id, label]) => (
            <button
              className={tab === id ? "active" : ""}
              key={id}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "rules" && (
          <button
            className="button primary compact"
            onClick={() => setEdit("new")}
          >
            <Plus size={15} />
            新增規則
          </button>
        )}
      </div>
      {tab === "overview" ? (
        <>
          <div className="security-summary-grid">
            <div>
              <span className="security-summary-icon blue">
                <ShieldCheck size={23} />
              </span>
              <strong>
                {
                  classifications.items.filter(
                    (item) => dataOf(item).active !== false,
                  ).length
                }
              </strong>
              <span>生效中的防護規則</span>
            </div>
            <div>
              <span className="security-summary-icon amber">
                <ShieldAlert size={23} />
              </span>
              <strong>{activeAlerts.length}</strong>
              <span>待處理安全警報</span>
            </div>
            <div>
              <span className="security-summary-icon red">
                <LockKeyhole size={23} />
              </span>
              <strong>{quarantined.length}</strong>
              <span>隔離中的檔案</span>
            </div>
          </div>
          <div className="enterprise-service-note">
            <Shield size={23} />
            <div>
              <strong>真實狀態，清楚界線</strong>
              <p>
                掃描未完成或引擎不可用不會顯示為「安全」。網頁版執行資料存取及分享政策，桌面裝置信任元件在下一期提供。
              </p>
            </div>
          </div>
          <h3 className="enterprise-section-title">已配置的自架服務</h3>
          <div className="service-status-grid">
            {Object.entries(settings.services || {}).map(([key, service]) => {
              const value = service as any,
                configured =
                  typeof value === "boolean"
                    ? value
                    : typeof value === "string"
                      ? !!value
                      : !!value?.configured;
              return (
                <div className="service-status-card" key={key}>
                  <span
                    className={`service-status-dot ${configured ? "configured" : ""}`}
                  />
                  <div>
                    <strong>{key}</strong>
                    <small>
                      {configured ? "已配置；服務執行結果另行驗證" : "未配置"}
                    </small>
                  </div>
                </div>
              );
            })}
            {!Object.keys(settings.services || {}).length && (
              <p className="muted">
                服務設定由部署環境管理。請按安裝文件配置掃描、轉檔、簽署及身份服務。
              </p>
            )}
          </div>
        </>
      ) : tab === "rules" ? (
        !classifications.items.length ? (
          <Empty
            icon={<ShieldCheck size={32} />}
            title="設定適合公司的資料防護"
            detail="按檔案範圍及內容規則，限制分享、下載、複製或預覽。"
            action={
              <button className="button primary" onClick={() => setEdit("new")}>
                建立防護規則
              </button>
            }
          />
        ) : (
          <div className="policy-card-grid">
            {classifications.items.map((item) => (
              <article className="policy-card" key={item.id}>
                <header>
                  <ShieldCheck size={23} />
                  <span
                    className={`business-status ${dataOf(item).active !== false ? "completed" : "draft"}`}
                  >
                    {dataOf(item).active !== false ? "生效中" : "已停用"}
                  </span>
                </header>
                <h3>{item.name}</h3>
                <p>
                  {dataOf(item).classification || "內部"} ·{" "}
                  {dataOf(item).all_resources
                    ? "所有資料"
                    : `${(dataOf(item).resource_ids || []).length} 個適用項目`}
                </p>
                <div className="integration-capabilities">
                  {(dataOf(item).blocked_actions || []).map(
                    (action: string) => (
                      <span className="business-tag" key={action}>
                        限制
                        {(
                          {
                            download: "下載",
                            share: "分享",
                            preview: "預覽",
                            copy: "複製",
                          } as Data
                        )[action] || action}
                      </span>
                    ),
                  )}
                </div>
                <footer>
                  <button
                    className="button secondary compact"
                    onClick={() => setEdit(item)}
                  >
                    編輯規則
                  </button>
                </footer>
              </article>
            ))}
          </div>
        )
      ) : tab === "alerts" ? (
        !alerts.items.length ? (
          <Empty
            icon={<ShieldCheck size={32} />}
            title="未有安全警報"
            detail="有實際政策命中、掃描結果或異常活動時，警報會顯示在這裏。"
          />
        ) : (
          <div className="business-data-grid">
            <table>
              <thead>
                <tr>
                  <th>警報</th>
                  <th>嚴重程度</th>
                  <th>狀態</th>
                  <th>時間</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {alerts.items.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <button
                        className="business-cell-link"
                        onClick={() => setSelected(item)}
                      >
                        <ShieldAlert size={19} />
                        {item.name}
                      </button>
                    </td>
                    <td>
                      <span className="business-status failed">
                        {dataOf(item).severity || "需要檢視"}
                      </span>
                    </td>
                    <td>
                      {dataOf(item).status === "resolved" ? "已處理" : "待處理"}
                    </td>
                    <td>{time(item.created_at)}</td>
                    <td>
                      {dataOf(item).status !== "resolved" && (
                        <button
                          className="button secondary compact"
                          onClick={async () => {
                            try {
                              await updateResource(item, {
                                ...dataOf(item),
                                status: "resolved",
                                resolved_at: new Date().toISOString(),
                              });
                              setInfo("已標記警報為已處理。");
                              void alerts.refresh();
                            } catch (e) {
                              setError(errorText(e));
                            }
                          }}
                        >
                          標記已處理
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : !quarantined.length ? (
        <Empty
          icon={<FileCheck2 size={32} />}
          title="未有隔離檔案"
          detail="此列表只顯示實際被掃描或安全政策隔離的檔案，未掃描不代表已驗證安全。"
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>檔案</th>
                <th>掃描結果</th>
                <th>最後更新</th>
              </tr>
            </thead>
            <tbody>
              {quarantined.map((item) => (
                <tr key={item.id}>
                  <td>{item.name}</td>
                  <td>
                    {dataOf(item).scan?.verdict ||
                      dataOf(item).scan?.status ||
                      dataOf(item).scan_status ||
                      "已隔離"}
                  </td>
                  <td>{time(item.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {edit && (
        <PolicyModal
          kind="classification"
          item={edit === "new" ? undefined : edit}
          onClose={() => setEdit(null)}
          onDone={() => void classifications.refresh()}
          targets={[...folders.items, ...files.items]}
        />
      )}
      {selected && (
        <Modal title={selected.name} onClose={() => setSelected(null)}>
          <p>{time(selected.created_at)}</p>
          <pre className="business-code">
            {JSON.stringify(dataOf(selected), null, 2)}
          </pre>
        </Modal>
      )}
    </div>
  );
}

function ReportsPage() {
  const [items, setItems] = useState<Data[]>([]),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const value = await api<Data[] | Data>("/audit");
      setItems(Array.isArray(value) ? value : value.items || []);
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  const filtered = items.filter((item) =>
    JSON.stringify(item).toLowerCase().includes(query.toLowerCase()),
  );
  function exportReport() {
    const blob = new Blob(
        [
          JSON.stringify(
            {
              exported_at: new Date().toISOString(),
              filter: query,
              entries: filtered,
            },
            null,
            2,
          ),
        ],
        { type: "application/json;charset=utf-8" },
      ),
      url = URL.createObjectURL(blob),
      anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `blueprint-audit-${new Date().toISOString().slice(0, 10)}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className="business-page">
      <Header
        eyebrow="可追溯的公司運作"
        title="報告與操作記錄"
        description="檢視真實操作事件，按需要篩選及匯出。"
        actions={
          <>
            <button className="button secondary" onClick={() => void refresh()}>
              <RefreshCw size={16} />
              重新整理
            </button>
            <button
              className="button primary"
              disabled={!items.length}
              onClick={exportReport}
            >
              <ArrowDownToLine size={16} />
              匯出報告
            </button>
          </>
        }
      />
      <Banner error={error} />
      <div className="business-filterbar">
        <div className="business-search">
          <Search size={17} />
          <input
            aria-label="篩選操作記錄"
            placeholder="搜尋事件、使用者或資源…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <span className="muted">{filtered.length} 筆事件</span>
      </div>
      {loading ? (
        <div className="business-loading">正在讀取操作記錄…</div>
      ) : !filtered.length ? (
        <Empty
          icon={<Activity size={32} />}
          title="操作事件會顯示在這裏"
          detail="資料建立、修改、分享、權限及工作流程等操作會留下可追查記錄。"
        />
      ) : (
        <div className="business-data-grid">
          <table>
            <thead>
              <tr>
                <th>時間</th>
                <th>事件</th>
                <th>操作者</th>
                <th>資源</th>
                <th>詳情</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, index) => (
                <tr key={item.id || index}>
                  <td>{time(item.created_at)}</td>
                  <td>
                    <span className="audit-event">
                      <Activity size={15} />
                      {item.action}
                    </span>
                  </td>
                  <td>
                    {item.actor_name ||
                      item.actor_email ||
                      item.actor_id ||
                      "系統"}
                  </td>
                  <td>
                    <code>{item.resource_id?.slice(0, 8) || "—"}</code>
                  </td>
                  <td>
                    <details>
                      <summary>檢視</summary>
                      <pre className="business-code">
                        {JSON.stringify(item.details || {}, null, 2)}
                      </pre>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function EnterprisePage({ section }: { section: string }) {
  if (section === "sign") return <SignaturePage />;
  if (section === "docgen") return <DocumentGenerationPage />;
  if (section === "security") return <SecurityPage />;
  if (section === "archives") return <GovernancePage initialKind="archive" />;
  if (section === "retention") return <GovernancePage />;
  return <ReportsPage />;
}
