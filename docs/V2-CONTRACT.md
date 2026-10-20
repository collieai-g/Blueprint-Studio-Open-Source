# Rust v2 架構與接口契約

每次安裝服務一間公司或個人。Rust 負責身份、資料、權限、工作流程、REST／MCP、CLI 及背景工作；React／TypeScript 提供網頁。PostgreSQL 保存公司資料、版本、操作紀錄及工作狀態；原檔保存在外置私人目錄或自選 S3。這是全新安裝，沒有舊 Python／SQLite 相容執行層。

## 接口來源

登入後讀取 `GET /api/v1/openapi.json`，取得實際路由、請求／回應 schema、權限、上載、WOPI、WebSocket 及 callback 協定。原始契約在 `src/openapi.rs`；測試檢查每個已公開路由都有記錄，所有 schema 引用可解析。

- 首次設定：`GET /api/v1/status`、`POST /api/v1/setup`，之後使用 `/login`、`/me`、`/logout`。
- 通用內容：`/resources/{kind}`、`/resources`、`/resource/{id}`、history、trash、restore、purge、search。
- 檔案：串流／分段上載、不可變版本、範圍下載、預覽、分享、移動／複製、鎖定、治理及延後收集。
- 業務：資料表／記錄／視圖、表單、日曆、DOCX／PDF、簽署、Notes／Canvas 協作。
- 自動化：工作流程發布／執行／批准、外部員工能力與派工、HTTP 連接器、簽署回報及 MCP。
- 管理：使用者／群組、tokens、公司模組、audit、jobs、SCIM Users／Groups。

瀏覽器使用 HttpOnly session cookie；JSON 寫入附 `X-Blueprint-Request: 1`。API 使用 `Authorization: Bearer …`。公開表單、分享、OAuth、簽署／worker callback 及 WOPI 各自驗證 capability、狀態或簽署，不能沿用一般登入假設。錯誤為 `{error:{code,message}}`。

## 資料與授權

Resource 格式為 `{id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at}`。UUID 為穩定識別；時間使用 RFC3339 UTC。公司日曆同時保存 IANA 時區。更新需要 `expected_version`，衝突回傳 409。

ACL 使用 `user:<uuid>`／`group:<uuid>`，角色為 viewer／editor／uploader。公司可見性提供非訪客的讀取權；編輯仍需要擁有權或 editor 授權。每層父資源權限持續有效。個人 Collections 只有本人可讀，不搬動或授予原件權限；已刪資源不經一般讀取接口返回。

PAT 另受 namespace scope、可選資源 ID、期限及撤銷限制；資源範圍不會變成公司管理權。排隊工作保存發起身份及 token 約束，執行時重新驗證帳戶、角色、群組及 token。不同 token 的冪等鍵分開，工作接口不洩露其他 token 的執行內容。

群組成員以 `users.groups` 為唯一權威；SCIM 操作將群組資料、成員及歷史原子提交。停用使用者同時撤銷 sessions、tokens 及未完成邀請。OIDC 驗證 state／PKCE、RS256 簽章、issuer／audience／expiry／nonce／subject，並綁定已核准帳戶與 provider subject。

列表一次最多返回 5,000 個有權資源；搜尋最多 500 個結果，trash 最多 1,000 個。限制前先按候選 ACL 篩選，再檢查父層，避免其他部門新資料擠走可讀內容。大型資料匯出應使用完整備份，不將這些列表當作無限量匯出接口。

## Codex 作為公司智能入口

第一個對話模式留在使用者已有嘅 Codex，藍圖透過 MCP 提供資料及動作，網頁顯示持久結果。`blueprint_workspace_info` 返回公司時區、當地時間、可讀模組及操作 playbook；`blueprint_context` 返回來源 UUID／版本、遮罩資料及資料表真實關聯嘅雙向一層展開。同名候選不自動合併，總證據最多 50、關聯候選最多 200；單筆 data 超過 64 KiB 明確標記省略，由 `blueprint_get` 讀取，context 不攜帶 CRDT 傳輸狀態。

Codex 按來源提問，使用現有 CRUD 保存回答、筆記、任務、日程及每日工作，以共同 activity ID 連結。每次寫入分開提交，部分失敗需分開回報。MCP 更新筆記正文會保留真正 Yjs 狀態及版本衝突檢查。`blueprint_agent_dispatch` 共用原有持久派工路徑，返回 run／job 真實回執；後續讀取仍隔離 token 身份。

`mcp-stdio` CLI 不載入資料庫配置，將標準輸入／輸出 JSON-RPC 轉送至已運行後台；日誌只用 stderr。Codex 設定引用本機 wrapper 同私人目錄，憑證由私人 `codex-token` 讀取。新連接需在實際客戶端刷新／發現工具；目前對話無法熱載入時，Codex 亦可用既有終端機呼叫同一 MCP bridge。完整操作見 [Codex playbook](CODEX-WORKSPACE.md)。

## 持久化與外部動作

Resource 及其歷史／audit 在交易中保存；資料異動事件使用同一交易的持久 outbox。工作以 PostgreSQL 排隊、租約、heartbeat 及重啟恢復運作。工作流程使用發布時的固定版本，人工批准、條件及外部回報不依賴單一 HTTP 請求持續在線。

未知結果的外部寫入不會盲目重送。外部員工由使用者另外開發；後台校驗能力、來源、scope、冪等鍵及簽署 callback，不產生虛構 AI 結果。連接器限制方法、路徑及主機，預設拒絕私網目標和轉址。

原檔版本使用 SHA-256 及不可變儲存引用；掃描、預覽及抽字分別記錄真實結果。保留／hold／archive／分類政策在伺服器執行。永久刪除記錄待收集物件，至少等 24 小時並重查所有內容／歷史引用及 provider hold；共享原檔引用存在時不刪 bytes。

可選引擎未配置或不可用時回報設定需要／失敗，不冒稱已掃描、已簽署、已同步或已連接。秘密使用持久 master key 加密，讀取接口及日誌不返回秘密。實際驗證與限制見 [驗證紀錄](V2-VALIDATION.md) 及 [整合依賴](V2-INTEGRATIONS.md)。
