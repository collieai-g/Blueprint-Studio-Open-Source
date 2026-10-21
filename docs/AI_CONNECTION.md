# 不同 AI 都能讀寫同一個藍圖工作台

[安裝指南](INSTALL.md) · [保留資料升級](UPGRADE.md) · [AI 工作方式](AI_WORKSPACE.md)

**V2.3（2.3.0）沿用真正的 MCP 工具層及內置接駁中心。** 模型理解你的要求，由 AI 客戶端呼叫已授權工具，工具將內容存入原來的後台；右側 V2 工作台再同步顯示結果。不是把對話文字自動當成資料，也不是只供 Codex 使用。

```text
Codex／Claude／Gemini／其他支援 MCP 的 AI 客戶端
                     ↓ 已授權 MCP 工具
               同一個藍圖工作室 API
                     ↓
          原有公司資料庫、附件、待辦及歷史
                     ↓
                 右側工作台
```

模型名稱不決定連接能力：必須使用可安裝 MCP 工具的 AI 客戶端及允許該功能的帳戶。普通聊天頁若沒有工具功能，不能僅靠一段提示詞取得寫入權限。原生版與 Sites 有不同登入方式，按以下路線接駁。

## V2.3 先從內置接駁中心開始

安裝完成後開啟工作台「AI 接駁中心」，按 [CONNECT.md](CONNECT.md) 由選擇客戶端、複製接駁指令、處理卡點一路完成驗收及第一項真實工作。下方技術步驟由安裝 AI 執行，不要求使用者自行拼接設定。

新增 blueprint_connection_guide 直接提供工作台共用規則、各客戶端設定及卡點處理；原生 MCP 初始化亦載入同一份規則。每個新對話先讀取，客戶端支援的私人專案指引亦應記下這一步。
新增 blueprint_verify_connection 以接駁中心提供的 challenge、client、date 在原後台做真實保存、讀回及測試待辦更新。它不會修改正常工作紀錄；只讀連接不能執行。右側獨立讀回同一筆驗收紀錄才顯示完成。21 個工具的完整參數由 tools/list 提供。

## 1. 原生後台：Codex、Claude Code、Gemini CLI 共用 MCP

適用於本機 Python、Docker、Render／Railway 等原生 API。後台先按安裝指南啟動；MCP 程式在 AI 客戶端能執行 Python 的環境運行，透過 HTTP／HTTPS 寫入**已存在的後台**。

### 安裝工具依賴

在完整來源目錄建立專用虛擬環境，避免改動其他程式的 Python 套件：

```sh
python3 -m venv ../blueprint-mcp-env
../blueprint-mcp-env/bin/python -m pip install -r requirements-mcp.txt
```

Windows PowerShell：

```powershell
py -3 -m venv ../blueprint-mcp-env
../blueprint-mcp-env/Scripts/python.exe -m pip install -r requirements-mcp.txt
```

後台本身仍只需 Python 標準函式庫；這份依賴只供 AI 工具橋接使用。目前固定 MCP Python SDK 1.29.0，使用標準 stdio 傳輸及 2025 年協定握手。支援此協定或可回退至此協定的客戶端可使用；不用各模型的 API key，也不會額外呼叫模型。

### 準備原帳號並檢查連接

在 AI 客戶端的安全環境設定 `BLUEPRINT_BACKEND_USERNAME` 及 `BLUEPRINT_BACKEND_PASSWORD`，沿用已有後台帳號。不要把密碼放進聊天、命令列參數、來源或公開設定檔。

AI 用虛擬環境 Python 的**絕對路徑**及已確認網址執行：

```sh
/實際位置/blueprint-mcp-env/bin/python /實際來源/blueprint.py mcp --url http://127.0.0.1:8787 --check
```

雲端改用實際 HTTPS 網址。`127.0.0.1` 只代表這個 AI 執行環境自己的電腦；不能拿遠端 Codex 的 localhost 當成使用者電腦。`--check` 只讀取真實公司名稱、版本、統計及連接地址，尚未證明寫入成功。登入失敗、重新導向或錯誤地址會停止，不會另建空資料庫。

### 產生所用 AI 的設定

使用同一個虛擬環境 Python，按客戶端選擇：

```sh
/實際位置/blueprint-mcp-env/bin/python /實際來源/blueprint.py mcp-config --client codex --url https://你的後台網址
/實際位置/blueprint-mcp-env/bin/python /實際來源/blueprint.py mcp-config --client claude --url https://你的後台網址
/實際位置/blueprint-mcp-env/bin/python /實際來源/blueprint.py mcp-config --client gemini --url https://你的後台網址
```

只執行你使用的那一條。指令會產生實際 Python／程式路徑及秘密變數**參照**，不包含密碼。AI 將輸出合併到原設定，保留其他外掛，不直接覆蓋整個檔案：

| 客戶端 | 合併位置／驗證方式 |
| --- | --- |
| Codex CLI／相容桌面環境 | `~/.codex/config.toml` 的 `mcp_servers.blueprint`；用 `codex mcp list` 檢查，並確認工具已載入目前對話 |
| Claude Code | 專案私人 `.mcp.json` 的 `mcpServers.blueprint`；在 `/mcp` 檢查並完成所需授權 |
| Gemini CLI | `~/.gemini/settings.json` 的 `mcpServers.blueprint`；保留其他設定，重開／重新連接後檢查工具 |
| 其他支援 stdio MCP 的客戶端 | 使用產生設定內的 `command`、`args`；按該客戶端的安全秘密管理方式傳入兩個環境變數 |

Codex 的 `env_vars` 及 Claude／Gemini 的環境參照必須由啟動該客戶端的環境或其秘密設定供應；從桌面圖示啟動時不一定繼承終端機的變數。不要把 `${...}` 當成真正密碼，也不要假設所有客戶端的設定格式相同。

設定已存在就沿用。新增或更新後，按客戶端能力重新連接或開始新對話，直到能列出 `blueprint_workspace_info` 等工具。只想讀資料時，可在啟動參數加 `--read-only`；此模式同時隱藏並拒絕寫入工具。

## 2. Sites：安裝、授權、載入是三個步驟

Sites 使用平台登入及原網站的 `/mcp`，不能把原生 Basic 帳密橋接硬套到 Sites。

1. AI 讀現有 Site 的來源與資料，透過 `get_site(include_mcp_connection=true)` 取得該網站**實際回傳**的 `mcp_url`、`oauth_resource` 及 `plugin_id`，不可猜 ID。
2. 核對 `/mcp` 的工具涵蓋文件、收藏、工作、待辦及公司資料。舊 Site 只有部分工具時，依 [Sites 適配方法](#sites-開發者接駁)在同一網站加入完整工具；保留登入、使用者隔離、資料庫與附件。
3. ChatGPT／Codex 的 Sites 外掛使用回傳的 `plugin_id` 安裝或連接。若平台回報 **already installed**，不要反覆安裝；檢查該外掛是否在**目前對話**可用及是否需要重新登入。
4. 在目前對話的外掛／工具選擇中選用該工作台，必要時以選用該外掛的新對話重新載入。AI 必須看到真正的讀寫工具；只有右側網頁、外掛卡片或「已安裝」標籤都未完成。
5. Claude、Gemini 或其他支援遠端 MCP／相容 OAuth 的客戶端，使用同一 `mcp_url` 及平台要求的 `oauth_resource`，由使用者完成平台登入。每個客戶端分開授權；不能複製 ChatGPT 的 session cookie 或使用者身份標頭。
6. 每個客戶端都完成下一節的寫入與讀回驗收。平台若不接受該客戶端的 OAuth 註冊或回調地址，回報具體不相容環節；不能取消登入保護來令它「可用」。可以選擇原生後台及上面的 stdio 路線。

### Sites 開發者接駁

開源包包含 `integrations/mcp/tools.json`（21 個共用工具定義）、`integrations/mcp/sites.mjs`（HTTP MCP 適配器）及同目錄 `connection.mjs`（驗收流程）。適配器需要現有網站 API 和平台驗證身份，不是獨立登入服務或一鍵 Sites 部署。

```js
import { createMcpHandler } from './mcp/sites.mjs';
import catalog from './mcp/tools.json' with { type: 'json' };

const mcp = createMcpHandler({
  tools: catalog.tools,
  version: '2.3.0',
  authorize: (request, env) => requireVerifiedPlatformUser(request, env),
  dispatch: (request, env) => existingAuthenticatedApi.fetch(request, env)
});
// 在原網站 /mcp 的分支呼叫 mcp(request, env)。
// authorize 及 dispatch 由原網站提供，不能以模型傳入的 user_id 代替身份驗證。
```

內部請求沿用同一個已驗證身份，轉交原 `/api/*`，因此使用相同資料及版本檢查；不新建資料表。來源須完整打包共用工具定義及適配器。保留舊工具名稱／schema 的相容別名，或明確刷新原外掛工具定義，避免舊對話仍呼叫已移除的工具。依目前平台流程發佈同一 Site，然後重新連接並讀回。

適配器提供傳統 `initialize` 及新版 `server/discover`，以 JSON 回應工具呼叫；沒有進度串流或資源訂閱。直接暴露在沒有身份閘道的服務時，不能信任請求自行帶入的身份標頭。

## 3. 每個 AI 都要完成的驗收

先在工作台內置接駁中心取得本次 challenge、client、date，呼叫 blueprint_verify_connection，按返回 ID 讀回並等待工作台顯示同一結果；詳見 [CONNECT.md](CONNECT.md)。此工具自動完成測試紀錄及待辦驗收，然後再以第一項真實工作驗證日常使用。

用一項使用者已要求保存的真實內容驗收，或清楚命名測試資料，不混入虛構公司工作。

1. 列出目前對話真正可呼叫的工具，確認有讀取及寫入工具。
2. 呼叫 `blueprint_workspace_info`，核對公司及後台網址。舊 Sites 名稱可能為 `blueprint_overview`，以實際 schema 為準。
3. 用 `blueprint_save_bookmark` 或 `blueprint_save_workday` 保存；來源填真正的 AI 名稱，保留穩定來源 key。
4. 取得實際紀錄 ID，再用 `blueprint_get_item` 或 `blueprint_get_workday` 讀回，核對內容。
5. 右側同一網站出現資料；V2 可見且未編輯時約 10 秒自動更新。
6. 修改一筆待辦、讀回狀態，確認版本衝突會停止；修訂工作後已完成待辦及歷史仍然保留。

交付須分別回報：**網站可用／外掛已安裝／目前對話工具已載入／已驗證讀取／已驗證寫入與讀回**。任何一步未通過就指出具體卡點，不能只因網頁打開而報「AI 已接通」。

## 工具範圍與日常指令

21 個工具包括後台身份與狀態、公司資料讀寫、收藏集讀寫、文件／收藏搜尋與讀取、文字文件保存、附件上載、網站與 YouTube 收藏、版本化修改與歷史、工作紀錄保存／修整／歷史，以及待辦讀寫。完整參數由 `tools/list` 提供，不要求使用者手填 JSON。

MCP 附件上載上限為 300 KiB，工具請求總量最多 512 KiB；較大附件沿用工作台原有的上載按鍵。文字文件按 UTF-8 保存；工具不接受任意本機檔案路徑，AI 只傳遞使用者已授權的檔案內容。原檔保持不可變。

你可以在任何已接通的客戶端說：

> 將這份內容整理成文件存入藍圖工作室，加入合適收藏集，讀回確認。
>
> 保存這個 YouTube 連結，記下我提供的學習重點。
>
> 整理今日成果同下一步，更新藍圖工作室，保留已完成待辦。

以上是相容設定及協定路線；自動測試使用官方 MCP SDK 的獨立客戶端連線，不等同已代每位使用者在三個產品內完成帳戶授權。

客戶端設定依 [Codex MCP 文件](https://developers.openai.com/codex/mcp/)、[Claude Code MCP 文件](https://code.claude.com/docs/en/mcp)及 [Gemini CLI MCP 文件](https://geminicli.com/docs/tools/mcp-server/)核對。協定採用 [MCP 官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk)。
