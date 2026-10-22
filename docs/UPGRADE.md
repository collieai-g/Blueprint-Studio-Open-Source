# 升級到 V2.4，同時保留原有工作資料

若本次已明確選擇另裝全新原生工作台、舊站保留不動，改按 [任務選擇規則](INSTALL.md#先確定這次任務才讀取進度) 使用安裝入口；不要把這份舊站升級清單套到獨立新安裝。以下備份要求仍適用於更新原站及需要保留的舊資料。

**V2.5 原生安裝改按 [NATIVE_LIFECYCLE.md](NATIVE_LIFECYCLE.md) 接續。** 本頁保留舊版遷移與 Sites 適配細節。新版增加自動備份、隔離還原測試及啟動前資料遷移保護，不新增公司資料 schema。舊版沒有維護 API 時仍先完成原資料完整備份。

[返回首頁](../README.md) · [安裝指南](INSTALL.md) · [AI 工作方式](AI_WORKSPACE.md)

[完整更新路線](UPDATE_ROUTE.md) 定義從候選取回到原站部署、右側驗收與 AI 接駁的連續步驟。保存候選不等於部署；已有私人檢查點後直接接續，不重複交接。

目前 V2.4 的修訂版是 **2.4.2**。從 2.4.0 升至 2.4.2 不新增資料表或 schema，新增更新助手及接續工具。原生資料庫由 schema 4 升至 **schema 5**，在同一交易新增公司紀錄、歷史、帳戶、偏好和權限表；保留原表、ID、待辦完成狀態、登入及附件。新增五組側欄與公司工作流程，沿用原品牌與 V2.3 排版。完整範圍及尚未接入的服務見 [V2.4.md](V2.4.md)。

更新的是程式與畫面。已有公司資料不重新初始化；新的預設介面不預填示範工作。卡片排列與輸入偏好保存在同一網站的目前瀏覽器，舊有卡片／清單及排序偏好仍沿用。

GitHub 發佈新版後，已安裝的私人工作台不會自動改版。你向該工作台的 AI 發出升級指令，AI 便按此流程完成備份、整合、部署與驗證。日後升級可說「按官方升級指南，把我的現有工作台更新到最新穩定版，保留全部資料及自訂內容」；AI 先讀該版更新及遷移要求，再選定確切版本，不盲目覆蓋。

2.4.1 加入 [更新助手](UPDATE_ASSISTANT.md)；2.4.2 再加入 [私人來源檢查點](SOURCE_CHECKPOINT.md)，修正本機接續包沒有實際交接的缺口。使用者可直接貼 [UPDATE_AI.txt](../UPDATE_AI.txt)；舊工作台沒有更新助手入口也可開始，不必先安裝新版。它協助執行下列流程，不代替正式備份、Sites 後台適配或平台授權。

## 把這段指令貼到已安裝工作台的 AI

```text
請將我現有的藍圖工作室工作台升級到 Blueprint Studio Open Source V2.4（2.4.2）。
上游：https://github.com/collieai-g/Blueprint-Studio-Open-Source

先讀新版 docs/UPGRADE.md、docs/AI_CONNECTION.md、docs/AI_WORKSPACE.md 及 AGENTS.md。
公開開源倉庫只放通用程式，不得保存我的私人來源、聊天、資料、登入設定或接續包。換環境前按 docs/SOURCE_CHECKPOINT.md 將候選來源保存到另外核實的私人倉庫，下載核對後再交接；公司資料及完整備份另外私人保存。
檢查我目前使用的是本機、原生雲端還是 Sites，確認現有網址、程式來源、版本、
資料庫、附件儲存、登入、AI 連接及公司自訂內容。沿用現有網站及資料。
先保存舊程式版本與完整私人備份，再更新同一個工作台，不重新執行 library-init、
不建立新網站或空資料庫，不覆寫公司設定、工作紀錄、附件、待辦進度、歷史及憑證。

原生版：用新版 tools/upgrade_workspace.py 備份及驗證；保持原資料路徑／磁碟。
Sites：用新版 tools/package_workbench.py 產生完整介面包，整合到現有 Site 的來源；
保留原有登入、資料庫、附件及 AI 工具適配。新增 workspace.js、brand.svg、connection.js 和 connection-guide.json 的服務路徑，
先按 docs/STUDIO_API.md 在原 Site 實作完整 /api/studio/*、持久資料及角色檢查，
再連同 navigation.js、studio.js、studio.css、manifest.webmanifest、service-worker.js 一併部署。
UI 包不包含 Sites 新後台，不能宣稱單換前端便完成升級。不要以 Python／SQLite 覆蓋 Sites 後台。
補齊真正的 MCP 外掛寫入層，支援我所用的 Codex、Claude、Gemini 或其他相容客戶端。
原生版安裝 requirements-mcp.txt，配置 mcp／mcp-config 指向原後台；Sites 整合 integrations/mcp
到原本已驗證身份的 API，保留舊工具相容性並刷新外掛。確認目前對話載入寫入工具，
不能把「已安裝」當成「能寫入」。開啟 AI 接駁中心，使用它的測試參數呼叫 blueprint_verify_connection；我授權建立一筆明確標示的接駁測試及測試待辦。
每個新對話讀 blueprint_connection_guide，並將後台地址及如何讀規則合併到客戶端私人指引。
按 docs/CONNECT.md 處理登入、工具載入或網絡卡點，帶我完成同一紀錄的右側顯示及第一項真實工作。
保留我的自訂內容；遇到衝突逐項整合，不能強制 reset、刪 volume 或重建資料。

按 docs/LAYOUT.md 驗證標題／副標題／日期無框、兩格與四格文字、卡片貼齊背景格線、
拖動／縮放／收起、鍵盤操作、明確按鈕加頁且最多三頁，以及重新開啟後保留排列；
驗證左側 AI 保存後右側工作台自動更新，編輯中的草稿不被刷新覆寫。
確認升級前的紀錄 ID、公司設定、附件、歷史與已完成待辦仍在，AI 能讀寫同一個後台。
完成後交付原網址、新版本、資料保留結果、備份位置及回復舊版的方法。
遇到工具或登入限制，完成可執行部分並指出確切下一步，不聲稱未驗證的升級已完成。
```

## 沿用 V2.3 排版與舊版相容

見 [排版指南](LAYOUT.md)。保持同一網站、瀏覽器及 `blueprint-workspace-v2` 儲存鍵。舊六張卡片會放到標題下方，優先保留大小、相對位置、收起狀態及輸入方式；重疊或超出頁面時按格調整。首次保存新版排版前，將舊值保留到 `blueprint-workspace-v2.2-backup`，不覆寫已存在的備份。單純開啟或縮窄視窗不會改寫排版。升級前亦應由使用者在原瀏覽器私人備份此鍵，伺服器備份不包含瀏覽器設定。

需要回復 V2.2 排版時，先另存目前的新版排版，再在原瀏覽器把備份鍵的值寫回原鍵並重新開啟舊版。這只回復排版，不更改公司資料。

## 沿用 V2.2 的內置接駁流程

V2.2 起提供 AI 接駁中心、共用接入及工作規則、兩個新 MCP 工具、六種卡點處理及第一項工作引導。升級後必須開啟中心，使用本次指令完成工具驗收與畫面確認，詳見 [CONNECT.md](CONNECT.md)。接駁測試仍是明確標示的新工作紀錄；公司功能另有 schema 5 的新增表。

V2.2 起需要靜態資產 /connection.js 及 /connection-guide.json；Sites MCP 適配器新增同目錄 connection.mjs。必須同時更新網站、工具層及外掛定義，不能只換前端。原生 MCP 必須執行新版本來源；既有資料路徑及客戶端其他設定不變。

## 1. 先辨認現有安裝

AI 先讀原 `INSTALL_PROGRESS.md` 或既有部署設定，核對實際使用中的服務。記下舊 commit、部署版本、網址、資料目錄／磁碟、附件儲存及工具連接；記錄不包含密碼或 token。

- 本機／Docker／Render／Railway：使用原生 Python API 和外部 SQLite 資料目錄。
- Sites：使用現有 Site ID、目前來源、平台資料庫、物件儲存及網站自己的登入／MCP 工具。Sites 的「部署版本 1／2」是平台序號，不等同程式版本 1.5.0／2.4.2。
- 私人來源有修改：先保留提交及現有修改，再在分支整合所選上游版本。不要用下載 ZIP 直接蓋過整個私人專案。

AI 先在獨立目錄取得 V2 工具，再備份舊資料。尚未更新舊 checkout 時，其內可能沒有升級工具。

```sh
git clone --branch v2.4.2 https://github.com/collieai-g/Blueprint-Studio-Open-Source.git blueprint-v2-source
```

若有舊版 1.0–1.4，先核對原版的遷移與備份要求；不要套用只適合 schema 4 的「無資料遷移」結論。V2 的既有 Store 仍保留早期版本遷移程式。

## 2. 原生後台：完整備份

安排短暫停止原服務，暫停 AI 寫入；保持資料路徑及磁碟。用 V2 工具對**原來的資料**備份：

```sh
python3 blueprint-v2-source/tools/upgrade_workspace.py snapshot --data "/實際原資料目錄" --output "/私人備份/升級V2前" --service-stopped
```

AI 在執行前換成核實過的路徑。Windows 使用 `py -3` 及正確的 PowerShell 路徑引號。

備份包含 SQLite 一致快照、原 `access.json` 登入設定、全部附件及資料目錄其他檔案；輸出目錄必須是全新的私人目錄。工具不會初始化或覆寫來源，不會自動停止服務，`--service-stopped` 表示操作者已確認服務停止。失敗時保留原資料，使用新的備份目錄排查後重試。

Docker／平台磁碟的實際目錄必須在執行工具的主機可讀；不能拿 AI 自己的空目錄代替。無法直接讀取平台磁碟時，先使用平台支援的完整磁碟備份，或在維護環境執行工具。單獨 `/api/export` 的 JSON 不包含附件及登入設定，不能當作完整備份。

## 3. 原生後台：更新同一個服務

先核對 Git 狀態及自訂修改。乾淨、直接跟隨官方來源的安裝可將舊 checkout 快進到已選 V2 commit；私人副本由 AI 在分支整合並測試，保留原有設定。不要強制覆寫本機修改。

- **本機：** 更新程式，使用原來的 `library --data "/原資料目錄"` 啟動。
- **Docker／自己的主機：** 更新來源及映像，重建同一服務，保留原 Compose project 和實際 volume 名稱。禁止 `docker compose down -v` 或新建空 volume。
- **Render／Railway：** 部署同一個服務的新來源版本，沿用原磁碟掛載、環境秘密及網址。不要建立另一個服務後聲稱資料已遷移。

V2 不需要重設公司資料或帳號，不要重跑 `library-init`。更新環境變數的密碼也不會改變已有帳號。

在停止寫入的驗證時段，對升級後同一資料目錄執行：

```sh
python3 blueprint-v2-source/tools/upgrade_workspace.py verify --data "/實際原資料目錄" --snapshot "/私人備份/升級V2前"
```

驗證逐一比對原有資料列、紀錄版本、已完成待辦、公司設定、附件及登入檔案；容許新增資料，但原紀錄改動會回報未通過。因此先完成保留資料驗證，再恢復日常工作及做新的操作測試。

## 4. Sites：在原網站疊加新版介面

Sites 升級由有 Sites 工具的 AI 在原工作台對話執行。先讀當前 Sites 官方技能，沿用既有 Site ID 和來源版本，確認網站的正式儲存與授權。這份更新包不會替你建立或更換 Sites 後台。

1. 保存目前 Site 來源 commit 及可回復的部署版本；使用平台備份方式保存資料庫、附件與必要設定，備份保持私人。不要把另一個人的工作台或公司資料複製入開源版。
2. 在 V2 來源目錄產生可驗證的介面包：

   ```sh
   python3 tools/package_workbench.py --output /私人交接位置/blueprint-workbench-v2.zip
   ```

3. 更新包包含 `library_web/` 全部 V2 必要檔案、圖示及 SHA-256 清單。AI 比對清單後，整合到原 Site 服務靜態資源的位置；保留 Sites 自己的伺服器、登入、資料庫 binding、附件及 AI 工具。
4. 確保 `/connection.js`、`/connection-guide.json`、`/workspace.js`、`/brand.svg`、`/app.js`、`/workdays.js`、`/style.css` 及 `/icons/*.svg` 由原網站提供。V2.4.2 亦需提供 `/update.js`，更新助手入口位於偏好設定。V2.4 必須新增 `/navigation.js`、`/studio.js`、`/studio.css`、`/manifest.webmanifest` 和 `/service-worker.js`，並按 [STUDIO_API.md](STUDIO_API.md) 在原儲存實作完整 `/api/studio/*`、角色保護及交易。這個後台移植不包含在 UI 包中；原有 API 仍保留。MCP 適配器按 [AI_CONNECTION.md](AI_CONNECTION.md) 接到原已驗證身份的 API，重新連接外掛並驗證工具。介面定期讀取 `/api/info`、`/api/collections`、資料與工作紀錄，沿用同一個正式資料庫，新增公司功能的資料表，不能另開空資料庫。保留原有存取限制。
5. 依原 Site 現有 API 測試與本指南驗收；如有平台適配，對照回應結構，不能省略功能或將 SQLite 程式直接放入 Sites。
6. 按 Sites 流程提交完整來源、部署至**同一網站**，核對正式網址與使用者登入後結果。更新本機程式／Codex 環境不會自動更新正式 Site。

如果需要由左側 AI 填寫公司資料、文件、收藏或待辦，沿用並補齊該 Site 已有工具。插件已建立不代表目前對話已載入；必須實測一筆資料在同一網站出現並讀回。

## 5. 升級驗收與回復

- 登入同一網址，看見 V2.4 五組側欄、白色工作台及原品牌標誌。
- 實測收件匣轉待辦、項目保存、主題關聯、報價至發票流程；員工不能透過直接 API 讀取財務與人事。
- MCP 能探索 30 個工具，實測 studio_create → studio_get 同一 ID，並確認原生 schema_version=5。
- 原公司設定、紀錄 ID、歷史版本、星號、收藏集、待辦完成狀態及附件仍然存在。
- 「調整排版」可以按格拖動及調整大小，亦可收起／展開；重開同一瀏覽器保留排列。手機可操作。
- 舊版卡片／清單及排序偏好保留；排列沿用 `blueprint-workspace-v2` 鍵，首次保存前備份舊值，不清空舊儲存。
- AI 透過既有 API／工具保存後，工作台在可見且沒有編輯草稿時約 10 秒內更新；失敗會顯示狀態。輸入中的表單、選取資料及卡片調整期間暫停同步。
- 搜尋、必要上載、下載、勾選待辦和修改仍可用；備用操作及偏好設定可展開手動入口。
- 重啟服務／重新部署後再次驗證資料，更新私人安裝進度記錄。

出現程式問題時先停止寫入。**V2.3 及以前的程式不能直接開啟 schema 5 資料庫**，會拒絕讀取；不能只退回程式版本或手動降低 PRAGMA。

回復前先另存目前完整 schema 5 資料和升級後新增工作，再恢復升級前的完整私人快照與匹配的舊程式，使用原路徑／服務／磁碟。升級後新內容不會自動出現在舊快照，要在保留的新版副本中核對及匯出，不能直接覆蓋丟棄。平台移植遵守相應平台的資料回復步驟。
