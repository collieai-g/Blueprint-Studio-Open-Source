# 給左側 AI 的接駁指引

這是 Blueprint 內置的 AI 接駁入口，由目前的 Codex 或相容 AI 客戶端執行。使用者留在左側交代要求；右側藍圖工作台顯示公司工作、資料及成果。接駁設定不需要右側精靈或另一個 AI 服務。

已有工作台可從**已確認的同一 origin**，以既有登入讀取 `GET /AI_CONNECT.md` 取得該部署內置的本檔；`GET /connection-guide.json` 提供對應的機器可讀規則。這兩個入口都沿用後台身分驗證，不把公開倉庫的文件當成登入或寫入證明。

適用於已有工作台的接駁與驗收。相關文件先沿用已固定來源內的版本；以下連結提供公開倉庫閱讀入口。首次安裝先按 [INSTALL_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/INSTALL_AI.txt)，程式更新按 [UPDATE_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/UPDATE_AI.txt)。沿用已選網址、公司、帳號與資料，不為接駁重新安裝或建立空資料庫。

## 1. 先核對目前能力與目標

1. 檢查**這個對話**實際可呼叫的工具、讀檔與執行能力。工具已安裝、網頁可見、舊對話曾成功，都不能代替目前的能力檢查。
2. 優先沿用已有私人安裝進度，確認工作台的實際 origin、公司名稱及登入身分。不要猜 Site ID、網址、資料路徑或帳密；不要把某個執行環境的 `localhost` 當成使用者電腦。
3. 已載入藍圖工具時，呼叫 `blueprint_workspace_info`，核對 `connected_backend_url`、公司、身分及版本；再呼叫 `blueprint_connection_guide`。其 HTTP 對應入口是同一個已驗證身分後台的 `GET /api/info` 與 `GET /connection-guide.json`。
4. 用已授權的 HTTP 能力讀取這兩個端點時，保留既有登入、代理與 TLS，不把憑證放進網址、對話、來源倉庫或輸出。公開倉庫的 JSON 是產品規則；實際已登入後台的回應才反映該工作台部署的能力。
5. 目前只有真實畫面控制工具時，核對正式頁面的網址與公司，再按要求操作可見表單。工作台內的保存與重新開啟可驗證畫面操作；這與 MCP 工具驗收分開回報。看得到右側分頁不代表具有控制權。

沒有畫面控制，也沒有載入可寫入的藍圖工具時，先完成客戶端設定；只有一般聊天能力時交付可用內容，明確說明尚未保存。不要假稱已控制工作台。

## 2. 在目前 AI 客戶端完成設定

| 實際部署 | 客戶端應做甚麼 |
| --- | --- |
| 原生本機／雲端 | 使用該版本來源及專用 Python 環境，按 `python blueprint.py mcp-config --client codex --url 實際網址` 產生設定；Claude、Gemini 使用對應 `--client`。合併到客戶端支援的位置，保留原有設定，執行 `python blueprint.py mcp --url 實際網址 --check` 後確認目前對話真的載入工具。 |
| Sites 自身後台 | 由可用的 Sites 工具讀取同一網站的 `plugin_id`、`mcp_url` 與 `oauth_resource`，沿用平台登入並選用正確外掛。其他客戶端先核對遠端 MCP／OAuth 相容性，不套用原生帳密。 |
| Sites 入口配原生後台 | 沿用 Sites 的登入及工具入口，由既有 gateway 連接原生後台；不向瀏覽器或 AI 對話暴露原生後台憑證。依實際回應確認模式。 |

原生秘密只存在客戶端安全環境的 `BLUEPRINT_BACKEND_USERNAME`／`BLUEPRINT_BACKEND_PASSWORD` 或平台秘密設定。外掛載入、平台登入、HTTP 能力與目前對話的工具是分開的狀態。

遇到登入或平台按鈕必須本人操作時，只說明當前入口、動作與成功標誌，完成後接續。網絡受阻按已生效的允許清單及支援的平台流程處理；保持代理與 TLS，不繞過限制。完整設定參考 [AI_CONNECTION.md](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/docs/AI_CONNECTION.md)。

## 3. 只有獲明確授權才建立測試

只閱讀本檔、查詢設定或一般聊天，不授權新增測試紀錄。使用者明確要求「接駁並實際驗證讀寫」時，可以建立一筆明確標示的測試工作紀錄及測試待辦；不要夾帶正式公司資料。

由**目前 AI 客戶端**準備以下參數，不從右側畫面取得：

| 參數 | 規則 |
| --- | --- |
| `challenge` | 使用客戶端真正的密碼學隨機來源產生 16 bytes，再編碼為 32 位小寫十六進位字串。Python 可用 `secrets.token_hex(16)`；JavaScript 可用 `crypto.getRandomValues(new Uint8Array(16))` 後逐 byte 轉 hex。不可使用固定範例、時間戳或自行聲稱隨機。 |
| `client` | 實際執行這次操作的客戶端名稱，例如 Codex。不可把其他 AI 冒充成 Codex。 |
| `date` | 使用者時區的實際日期，格式 `YYYY-MM-DD`。 |

開始呼叫前，將 origin、公司／非秘密身分識別、這三個參數及進度保存在**來源倉庫以外**的私人可接續位置。不要保存密碼、token 或整段對話。若客戶端沒有可用的隨機或私人保存能力，先說明確切缺項；不可製造成功紀錄。

同一次驗收中斷、回應遺失或跨日重試，沿用同一組 `challenge`、`client`、`date`；先找回既有參數，避免重複資料。新的工作台、客戶端或另一次明確驗收才建立新組合。使用者已修改舊測試時保留修改；只有要求新測試才另開一次，不用換 nonce 逃避錯誤。

## 4. 用真正工具驗證並另行讀回

1. 再核對 origin、公司、登入與目前工具。不要將驗收寫到另一個後台。
2. 呼叫真正載入的 `blueprint_verify_connection`，傳入剛保存的 `challenge`、`client`、`date`。工具名稱可能有客戶端前綴，應使用實際列出的同一工具。
3. 此工具會在同一個已驗證身分的後台建立或沿用 `AI 接駁測試`，以 `blueprint-connect:` 加 nonce 作來源識別碼，另行讀回、更新測試待辦，再讀回 `blueprint-connection/1:verified`。工具回報錯誤時不宣稱成功，也不手動修改欄位製造驗收。
4. 取得 `workday_id` 後，**另外呼叫** `blueprint_get_workday` 按該 ID 讀回。核對公司、日期、實際客戶端、來源識別碼、標題、摘要及 nonce；其中兩項必須分別是測試成果與測試待辦。
5. 再以 `blueprint_list_todos` 查詢該 nonce、`done: "all"`，按待辦 ID 和 `workday_id` 核對同一項測試待辦確實已完成。不要只相信保存回應或一個 `verified` 布林值。
6. 如使用者要查看右側，正常開啟「工作 → 工作紀錄」，按相同日期、來源及內容核對該筆紀錄；外部寫入後可按「更新資料」。右側沒有接駁精靈，也不會監看左側對話。

只有畫面操作而沒有這個驗收工具時，完成獲授權的正式表單保存，再重新開啟同一份資料核對，回報「畫面保存與讀回已驗證」。不要呼叫任意 API 或手動建立上述摘要來冒充 MCP 驗收。

## 5. 接續日常工作

回報實際工作台、驗收路線、紀錄 ID、獨立讀回與待辦結果，分清楚「客戶端設定完成」、「目前工具可讀寫」及「畫面已核對」。每個新對話重新檢查實際工具與登入，不自動新增測試。

只把實際網址、如何讀取本檔與 `/connection-guide.json`、已確認的客戶端設定位置合併到客戶端支援的私人指引，保留原有內容。日常任務使用實際可用的畫面或工具，不需要每次更新 GitHub 或重新部署。接著完成使用者授權的第一項真實工作，保存並讀回同一筆資料。
