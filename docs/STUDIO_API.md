# V2.4 公司 API 與平台適配

原生實作：`blueprint_library/studio.py`、`studio_schema.py`、`studio_http.py`。前端新增 `navigation.js`、`studio.js`、`studio.css`。舊 API 保留。

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

先備份原正式資料，再對照 `tests/test_studio.py` 及 `tests/check_studio_browser.py` 驗證：原 ID／附件、五組頁面、命令與收件匣、報價發票、金額去重、版本衝突、角色越權、舊附件路徑、主題範圍、專員空狀態、手機、草稿保留。

UI 包有 SHA-256 清單但沒有 Sites 後台。API 不相容時保留舊功能並顯示載入失敗，不應把升級標為完成。完整原生部署用 installer 包及 Python／Docker 路線。

## V2.6 Agent 共用操作層

新增 `/api/studio/agents` 與 `/api/studio/agent-jobs`，詳見 [Agent 接駁規格](AGENT_CONTRACT.md)。UI 和 MCP 共用這些路由、身份及版本檢查。Agent 登記與工作使用獨立資料表，schema 5 → 6 追加建立；原公司紀錄、附件及登入不變，備份涵蓋全部新表。舊版程式不能開啟 schema 6，回復時須使用相容程式及完整備份。

`state` 新增 `agent_workbench: true` 及 `agent_contract: blueprint-agent/1`；`specialist_runtime` 和自動外發仍為 false。`/api/runtime` 的 `agent_handoff: connected_client` 表示由已連接 AI 客戶端交接工作，並非常駐執行器或目前對話已連接的證明。
