# 本機安裝：由新對話到自己的原版工作台

[返回安裝總覽](INSTALL.md) · [雲端安裝](INSTALL_CLOUD.md)

選擇本機後，程式及私人資料保存在你指定的電腦。這條路線不需要建立 GitHub 帳戶、
Codex 雲端環境或租用網站主機。AI 逐步處理檢查、下載、設定及驗證；需要你操作時，
只交代當前一步，例如選擇資料位置或在本機安全輸入密碼。

## L1. 確認 AI 正在操作你的電腦

AI 先檢查實際執行位置、作業系統、Python、已有工具及私人資料路徑。若目前是
Codex 雲端工作環境，要先引導你切換到自己電腦上的 Codex／具備本機工具的 AI，
或把已整理好的步驟交給你在本機終端機執行。雲端環境裏的 `127.0.0.1` 不是你電腦的地址。

使用已有工作室時，先找出原有程式目錄、資料目錄、啟動方式及運行中的服務，直接接續。
保留自訂程式、公司資料、帳號和工作紀錄；遇到已有檔案或不同設定時，先核對用途，
不要另建空資料庫來繞過衝突。

新安裝只需要你確認工作室／公司名稱及存放位置；行業、簡介及目標可留待介面內補充。
以下以同一個父目錄下的兩個資料夾示範，AI 應換成你確認的實際位置：

```text
你選擇的資料夾/
├── blueprint-folder-agent/   # 公開工具程式
└── blueprint-private/        # 你自己的設定、資料及 AI 工作室
```

**本步完成：** 確認指令會在目標電腦執行，並記下兩個實際絕對路徑。

## L2. 準備 Python 及完整程式

在 Windows PowerShell 檢查：

```powershell
py -3 --version
```

在 macOS／Linux 終端機檢查：

```sh
python3 --version
```

需要 Python 3.10 或以上。若未安裝，AI 按作業系統引導你使用
[Python 官方下載](https://www.python.org/downloads/)或該系統的套件管理器，安裝後重新開啟終端機再驗證。
若只有 `python` 指令而版本符合，之後統一改用 `python`。正常運行後台不需要額外 pip 或 npm 套件；
Node.js 是開發檢查使用的工具，不是本機使用前提。

有 Git 時，在你選擇的父目錄執行：

```sh
git clone https://github.com/collieai-g/Blueprint-Studio-Open-Source.git blueprint-folder-agent
cd blueprint-folder-agent
```

沒有 Git 時，下載[完整 ZIP](https://github.com/collieai-g/Blueprint-Studio-Open-Source/archive/refs/heads/main.zip)，
解壓後進入包含 `blueprint.py` 的資料夾。保留完整目錄，不只下載 README 或 HTML。
已有 checkout 時先檢查 Git 狀態及版本，不重複 clone 或覆寫本機修改。

AI 要確認 `blueprint.py`、`blueprint_library/`、`library_web/` 及 `assets/icons/` 都存在，
再繼續設定。這些檔案已包含原版後台介面，安裝時直接沿用。

**本步完成：** Python 可以執行，完整程式已在指定位置。

## L3. 準備私人設定

以下指令一律從包含 `blueprint.py` 的程式目錄執行。

Windows PowerShell：

```powershell
py -3 blueprint.py setup --company-name "我的工作室" --mode local --output "../blueprint-private"
```

macOS／Linux：

```sh
python3 blueprint.py setup --company-name "我的工作室" --mode local --output "../blueprint-private"
```

AI 用你確認的名稱替換「我的工作室」。公司名稱含特殊字元時，按目前終端機正確傳遞參數，
不要直接拼接成可執行指令。可選欄位為 `--industry`、`--description`、`--goals`。

`setup` 產生 `company-profile.json`、`setup.json`、`START_HERE.md` 及無密碼的
`deployment.env.example`。`START_HERE.md` 記錄實際位置，但產生的指令採用 POSIX shell 引號。
Windows 請使用本頁的 PowerShell 範例，由 AI 按實際路徑轉換參數及引號；路徑含單引號等特殊字元時，
不能只將 `python3` 改為 `py -3` 後照貼產生的指令。
這一步只準備安裝計劃，AI 必須接續初始化及啟動，不能停在「已產生檔案」。

保留已有計劃；若工具回報內容不同，核對原設定及實際資料後接續，不刪除檔案重做。

**本步完成：** 私人設定已建立，工作台尚未啟動。

## L4. 建立管理員並啟動原版後台

新資料目錄只初始化一次。Windows PowerShell：

```powershell
py -3 blueprint.py library-init "../blueprint-private/data" --username admin --company-file "../blueprint-private/company-profile.json"
py -3 blueprint.py library --data "../blueprint-private/data"
```

macOS／Linux：

```sh
python3 blueprint.py library-init "../blueprint-private/data" --username admin --company-file "../blueprint-private/company-profile.json"
python3 blueprint.py library --data "../blueprint-private/data"
```

先完成第一行，再執行第二行。初始化會要求你在終端機輸入及確認至少 12 字元的管理員密碼，
輸入時不顯示內容。密碼不要貼到聊天或寫入 Git。已有 `access.json` 及資料庫時，
沿用原路徑和管理員，跳過 `library-init`。

若 AI 的執行工具沒有互動輸入能力，將這一行交給你在本機終端機完成，完成後由 AI 接續；
亦可使用已配置的安全環境變數 `BLUEPRINT_ADMIN_PASSWORD`。AI 不應啟動無法讓你輸入的密碼提示、
代設固定示範密碼，或要求你在對話中提供密碼。

第二行會持續運行。瀏覽器通常會自動打開；沒有自動開啟時，使用服務輸出的實際地址，
預設為 `http://127.0.0.1:8787/`。用瀏覽器彈出的 HTTP Basic 登入視窗輸入帳號 `admin`
及剛才設定的密碼；原生版本不另外提供 HTML 登入頁。

後台首頁會直接載入 `library_web/` 的原版介面。`gui`、`serve`、直接執行沒有子指令的
`blueprint.py`，以及 `dist/index.html` 都屬於舊版離線工具入口；它們不會啟動這個公司後台。
不要用新生成的介面或示範頁代替原版工作台。

**本步完成：** 你可以在自己電腦的瀏覽器登入原版工作台。保持服務終端機運行。

## L5. 保存一次，重新啟動後再查回

AI 按[安裝總覽的完整驗收要求](INSTALL.md)核對原版導覽及所有既有功能，並帶你完成：

1. 開啟公司設定，確認工作室名稱；其他欄位按需要補充。
2. 加入一個網站收藏及一份小文件，確認搜尋、下載與原檔內容正常。
3. 在「每日工作」使用 AI 整理的有效 JSON 預覽及保存一筆簡短紀錄，並勾選其中一個待辦。
   AI 可按[每日工作格式](DAILY_WORK.md)準備內容，讓你確認後匯入。
4. 重新整理頁面，查回同一筆紀錄及待辦完成狀態。
5. 在服務終端機按 `Ctrl+C`，重新執行同一條 `library --data` 指令，再登入查回資料及附件。

AI 同時驗證未登入不能讀取公司資料。沒有實際執行的檢查要標為待驗證，不能以程式測試結果
替代你目前這個安裝的登入及保存結果。驗收資料只放入已確認的私人後台，清楚標記測試用途。

**本步完成：** 本機工作台已可日常使用，重啟後資料仍在。

## L6. 連接所用 AI 的 MCP 工具

依 [AI_CONNECTION.md 的原生路線](AI_CONNECTION.md#1-原生後台codexclaude-codegemini-cli-共用-mcp)，安裝專用 MCP 環境，使用 `mcp-config` 產生 Codex／Claude／Gemini 設定，並完成寫入及讀回驗收。這條路線提供文件、收藏、工作及待辦的完整工具；不是只在對話貼提示詞。

以下 `workday-connect` 是保留的命令列每日工作路線，可供已有終端機綁定的使用者沿用，不能代替 MCP 外掛接駁。

保持後台服務運行，另開一個終端機並進入同一程式目錄。
Windows PowerShell：

```powershell
py -3 blueprint.py workday-connect "../blueprint-private/藍圖工作室 Blueprint Studio" --data "../blueprint-private/data"
py -3 blueprint.py workday-list --workspace "../blueprint-private/藍圖工作室 Blueprint Studio"
```

macOS／Linux：

```sh
python3 blueprint.py workday-connect "../blueprint-private/藍圖工作室 Blueprint Studio" --data "../blueprint-private/data"
python3 blueprint.py workday-list --workspace "../blueprint-private/藍圖工作室 Blueprint Studio"
```

讓 AI 讀取產生的私人 `WORKDAY.md`。試講「今日收工」，由 AI 整理目前可見或你提供的工作，
透過已有連線保存；在瀏覽器確認同一筆紀錄出現，再由 AI 查回。
已有工作室綁定時先核對其指向，保留自訂指引及資料。

本機連線需要 AI 有權存取這部電腦的同一個資料目錄。Codex 雲端或普通聊天無法僅憑
`127.0.0.1` 或本機檔案路徑接上你的電腦；此時先使用後台 JSON 匯入，清楚標記「AI 直接寫入未接通」。
需要跨裝置直接使用時，按[雲端路線](INSTALL_CLOUD.md)規劃可登入的正式服務及資料遷移，
不要在另一個環境新建資料庫並宣稱已連到原有後台。

## L7. 下次開啟、更新及完成交付

下次使用時，在原程式目錄執行以下一行，再開啟服務輸出的地址；不需要重新初始化。

Windows PowerShell：

```powershell
py -3 blueprint.py library --data "../blueprint-private/data"
```

macOS／Linux：

```sh
python3 blueprint.py library --data "../blueprint-private/data"
```

電腦關機、休眠或關閉服務終端機後，工作台可能暫停；資料仍在原私人目錄。
需要自動啟動時，AI 再按你使用的作業系統設定並驗證服務啟動方式。
更新程式前處理已有修改並備份資料；更新後沿用相同資料路徑，見[更新與備份](LIBRARY.md#更新與備份)。

AI 最後在私人目錄的 `INSTALL_PROGRESS.md` 記錄：實際程式及資料絕對路徑、Python 指令、
工作台地址、下次啟動指令、驗收結果、AI 工作室位置和未完成事項。不要記錄密碼。
交付時分別說明「本機工作台可用」及「AI 直接保存已驗證／待接通」。

## 卡住時，接續目前步驟

| 現象 | AI 要檢查及處理 |
| --- | --- |
| 找不到 Python | 完成 L2 安裝，重新開終端機並確認實際可用指令。 |
| 已有設定或資料目錄 | 查閱原計劃與資料位置，保留資料並跳過已完成初始化。 |
| 密碼提示無法輸入 | 回到 L4，由你在本機互動終端機輸入，或使用已配置的安全秘密設定。 |
| 地址無法開啟 | 確認服務仍在運行，瀏覽器與服務位於同一部電腦，並核對實際 port。 |
| 8787 已被使用 | 先檢查是否已有藍圖服務；若由其他程式使用，可加 `--port 8788` 啟動，記下新地址。 |
| 登入後仍出現 401 | 核對原管理員及瀏覽器快取的 Basic 登入資料；不要重新建立空資料庫。 |
| 打開後像另一個舊工具 | 確認使用 `library --data` 及其 HTTP 地址，回到 L4 開啟原版後台。 |
| AI 說保存成功但介面找不到 | 核對 AI 與介面的資料路徑，查回相同紀錄識別碼，再修正綁定。 |

每次修正後接續原進度；只有真實啟動、登入、原版功能及重啟保存驗證通過，才標記本機安裝完成。

## V2.2：由介面接續到真正使用

工作台可開啟後，立即進入內置「AI 接駁中心」，依 [CONNECT.md](CONNECT.md) 選客戶端、取得接駁指令、完成必要登入／工具載入，再呼叫 blueprint_verify_connection。等右側讀到同一筆驗收結果，最後帶使用者完成第一項真實工作。遇到卡點，使用中心的對應處理指令；換對話沿用私人進度及同一測試參數，不重建工作台。
