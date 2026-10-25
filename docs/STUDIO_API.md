# V2.4 公司 API 與平台適配

原生實作：`blueprint_library/studio.py`、`studio_schema.py`、`studio_http.py`。公司介面使用 `navigation.js`、`studio.js`、`studio.css`；Skill 工作台另需一併提供 `skills.js`、`skills.css`。V2.6 Agent 介面使用 `agents.js`、`agents.css`。更新時連同 `index.html` 部署同一份來源的資產；舊 API 保留。

**Sites MCP 轉接器不是公司資料後台。** `integrations/mcp/sites.mjs` 轉交以下請求，不建立資料表或登入。原 Site 須在原正式儲存實作相容 API，才可升級；不能開另一個 SQLite 檔案冒充原 Site。

## 身份與資料

先通過原平台身份驗證，伺服器推導 `username/name/role`，不接受前端自稱角色。角色 `owner/admin/member`；財務、人事及接入限 owner/admin，只有 owner 管理 admin。停用帳戶立即拒絕下一請求。原生 owner 沿用 `access.json`，新帳戶用加鹽 PBKDF2，不回傳雜湊。

新增六表：`studio_records/history/users/preferences/meta/resource_acl`（完整表名均以 `studio_` 開頭）。原生 schema 4 → 5 在同一交易建立；保留原表、ID、檔案及登入。Sites 使用原資料庫等效交易與遷移，不以 Python SQL 覆蓋平台模型。

紀錄：`id/kind/title/data/refs/visibility/version/archived/created_at/updated_at/actor`。`refs=[{kind,id}]`，不複製內容；`source_key` 伺服器去重。以 `studio_schema.py` 為欄位／選項／限制的完整來源，`state` 只回傳角色可用定義。

修改帶 `expected_version`，原子比對並新增歷史，衝突 409。錯誤沿用 `{error: message}`，不回傳 SQL 或秘密。URL 僅 HTTP/S、不含帳密。金額為兩位十進位字串，不用浮點加總、不混算貨幣。

## 路由

| 方法／路徑 | 回應或用途 |
| --- | --- |
| GET `/api/studio/state` | identity、schemas、catalog、preferences、summary、templates、capabilities、schema_version |
| GET `/api/studio/summary?month=YYYY-MM` | 真實正式對話、提醒、分貨幣財務小計；員工 finance=null |
| GET/PATCH `/api/studio/preferences` | `{version,data}`；PATCH `{expected_version,data}`，按身份保存 |
| GET/PATCH `/api/studio/presentation` | 帳戶版面文件、各自版本及增量檢查；見 [版面同步](PRESENTATION_SYNC.md) |
| GET/POST `/api/studio/records` | GET kind/q/category/relation/archived/limit/offset → `{records,total,offset,limit}`；POST → `{record,created}` |
| GET/PATCH `/api/studio/records/ID` | `{record}`；POST 可帶 kind/title/data/refs/visibility/source_key；PATCH 不改 kind 或自行升權 |
| GET `/api/studio/records/ID/history` | `{versions}`，最近 100 版本，先檢查目前權限 |
| GET `/api/studio/records/ID/context` | `{record,sources,scope,rules}`，僅獲授權選定來源；主題不混入其他公司資料 |
| GET `/api/studio/records/ID/similar` | 標籤／文字相似結果，明示 method |
| POST `/api/studio/records/ID/actions` | `{expected_version,action,arguments}` → `{record,created,external_action_executed:false}` |
| GET `/api/studio/records/ID/download` | 報價 HTML／內容 Markdown 附件，跳脫文字並檢查權限 |
| GET `/api/studio/media/ITEM_ID` | 原附件庫 PNG/JPEG/WebP，先檢查原檔權限 |
| POST `/api/studio/templates` | `{template_id}` → 新專員 `{record,created}` |
| GET/POST `/api/studio/users` | 人員帳戶清單／新增修改，不回傳密碼或雜湊 |
| POST `/api/studio/financial-report` | `{month}` → 真實計算月報，管理員限定 |

清單預設 50、最多 100 筆，前端分頁；單筆最多 100 關聯。catalog 供選單／專員／主題使用，不預填假公司資料。

## 流程與平台驗收

MCP 動作：`to_task/ask_ai/create_quote/create_invoice/plan_command/complete_command/record_usage`。人工動作 `quote_state/record_payment/approve_draft/specialist_state/resume_budget` 由登入介面送出，仍受伺服器角色與狀態檢查。

報價按順序前進；發送與簽署需 `confirmed:true`，只記錄外部完成的事。確認內容不能直接改寫，建立發票須先簽署，在同一交易帶入客戶和金額。任何路徑均不會自動外發、代簽或付款。

私密內容及其附件／工作資料必須連舊 API、搜尋、歷史、下載、匯出、交接、MCP 一同保護。關聯到私密資料會收緊為管理員可見，不因移除關聯自動公開。沒有實際執行器時 `specialist_runtime/external_sending/server_capture/model_inference` 為 false；無真實結果及來源不可標測試通過。

先備份原正式資料，再對照 `tests/test_studio.py` 及 `tests/check_studio_browser.py` 驗證：原 ID／附件、總覽／工作／AI／資料四個主入口及保留路由、命令與收件匣、報價發票、金額去重、版本衝突、角色越權、舊附件路徑、主題範圍、專員空狀態、手機、草稿保留。

UI 包有 SHA-256 清單但沒有 Sites 後台。API 不相容時保留原資料，以同一個載入／錯誤畫面顯示原因及重試操作；不要靜默切換舊版介面，也不應把升級標為完成。完整原生部署用 installer 包及 Python／Docker 路線。

## V2.6 Agent 共用操作層

新增 `/api/studio/agents` 與 `/api/studio/agent-jobs`，詳見 [Agent 接駁規格](AGENT_CONTRACT.md)。UI 和 MCP 共用這些路由、身份及版本檢查。Agent 登記與工作使用獨立資料表，schema 5 → 6 追加建立；原公司紀錄、附件及登入不變，備份涵蓋全部新表。舊版程式不能開啟 schema 6，回復時須使用相容程式及完整備份。

`state` 新增 `agent_workbench: true` 及 `agent_contract: blueprint-agent/1`；`specialist_runtime` 和自動外發仍為 false。`/api/runtime` 的 `agent_handoff: connected_client` 表示由已連接 AI 客戶端交接工作，並非常駐執行器或目前對話已連接的證明。


## Skill 工作台與畫面操作

Skill 工作台整理可重用指引及單次工作說明，沿用既有資料清單與檔案保存服務，不建立另一份公司資料庫。選定來源只匯出編號、名稱及版本等交接資訊；原始附件須由獲授權的客戶端另外讀取。保存草稿後按相同資料編號讀回，不能把下載本機檔案當成後台保存或 Skill 安裝成功。

瀏覽器控制可使用已實作的正式欄位及按鈕。這條路徑與 MCP 共用後台身份、版本、權限及歷史；控制畫面不會賦予額外權限，也不取代尚未提供的工作生命週期操作。完整分工見[左右協作流程](LEFT_RIGHT_WORKFLOW.md)。

一般畫面不定時刷新，也不在 focus、visibilitychange 或瀏覽器歷史還原時更新公司資料。工作台內保存後更新相應結果；外部修改由使用者按「更新資料」讀取。非同步請求返回後，仍需核對目前頁面及編輯狀態，避免較早請求覆蓋新選擇或草稿。靜態介面資產應使用一致來源並正確處理快取，避免交錯使用不同部署的程式。

## Sites／Workers 請求生命週期

自行把公司後台移植到有請求取消機制的 Workers 平台時，若一次請求會取得資料租約或鎖，需讓整個受保護交易持續至提交或清理結束。使用者關閉或切換畫面，不應令尚未完成的鎖清理隨前景請求一起被取消。

在平台提供執行上下文時，可保留同一個交易 Promise：

```js
function retainProtectedRequest(execution, run) {
  const task = Promise.resolve().then(run);
  execution.waitUntil(task.then(() => undefined, () => undefined));
  return task;
}

const response = await retainProtectedRequest(ctx, () => handleProtectedRequest(request, env));
```

`handleProtectedRequest` 仍負責原有身份、資料、提交及 `finally` 清理。需在等待交易前登記保留工作，涵蓋取得鎖、讀取、寫入、提交及錯誤清理；一般 HTTP、MCP 和隔離還原入口若共用受保護交易，都應套用相同生命週期。前景等待的是同一個 Promise，照常回傳原本成功或錯誤；上例只忽略供 `waitUntil` 持有的分支拒絕，不把失敗回報為成功。

保留工作受平台持續時間限制，不是任意長時間的背景執行器。不要為避免衝突而縮短或任意刪除仍有效的鎖，也不要移除版本檢查。這是自行移植 Workers 後台的指引，不表示原生 Python 伺服器需要 `waitUntil`，也不提供通用 D1／R2 完整後台。

平台適配驗收應覆蓋：取得鎖後延遲回應、前景中斷、成功提交、失敗清理、MCP 及隔離還原只讀保護。確認清理完成後下一項請求可正常取得資料，錯誤與資料內容不被改寫。本機中止測試可驗證生命週期登記及清理，平台實際取消行為仍需在正式執行環境核對。


## V2.7.4 左側 AI 接駁入口

`AI_CONNECT.md` 為客戶端閱讀的共用入口，連同已驗證身份下的 `/api/info`、`/connection-guide.json` 及現有 MCP 描述核對 origin、身份、規則與工具。它不授予控制權，也不自行寫入測試。MCP 驗收由實際客戶端產生並私人保存 challenge／client／date，透過真正工具保存，再另行讀回核對。

右側不載入接駁精靈，正常 AI 區不列模型／外部接入登記；舊 `#/connections` 管理員唯讀頁面保留已有紀錄與權限。工具 API、來源保護和 schema 6 不因導覽簡化而移除或放寬。

## V2.8.1 帳戶版面同步

`state.capabilities.presentation_sync: true` 表示後台提供版面 API。總覽、每頁排版、純文字顯示覆寫及總覽來源引用按已登入身份保存，使用獨立文件版本作原子比對。資料放在既有 `studio_preferences.data` 的保留欄位 `_presentation`，schema 仍為 7；普通偏好路由不暴露或接受此欄位，修改普通設定須保留最新的版面資料。

可見網頁約每兩秒只檢查版面版本；公司紀錄不會因此自動重載。瀏覽器保留未同步修改，衝突需明確處理；原有表單節點和草稿仍然保留。完整 HTTP、MCP、Sites 適配與驗收規格見 [PRESENTATION_SYNC.md](PRESENTATION_SYNC.md)。
