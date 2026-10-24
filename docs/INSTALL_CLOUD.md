# 雲端安裝：由帳戶設定到自己的完整工作台

V2.7.4 新安裝優先使用原生後台，先完成 [NATIVE_LIFECYCLE.md](NATIVE_LIFECYCLE.md) 的儲存、完整備份下載、還原、重啟保存及再次部署驗收。新容器資料放 `/data/library`，備份放 `/data/backups`，兩者都在持久磁碟；已有安裝保留原路徑。Sites 仍須另行實作及驗證備份還原，不能視為同等完成。

[安裝總入口](INSTALL.md) · [本機路線](INSTALL_LOCAL.md) · [後台功能與 API](LIBRARY.md)

這條路線適合希望在 ChatGPT／Codex 右側或一般瀏覽器，以固定網址使用工作台的人。
AI 應逐步完成可執行的設定，只有登入、驗證、必要授權及收費確認交由使用者操作。
每次交接都指出現在要開哪個頁面、填甚麼、完成後會見到甚麼，以及下一步。

完成路線是：**選雲端 → 檢查已有安裝 → GitHub 與完整程式碼 → Codex 雲端接入 → 選正式部署平台 → 開啟原版介面 → 驗證保存 → 接通 AI。**
已有的步驟直接接續。只想部署原生後台的人可以直接使用部署平台；Codex 環境是讓 AI 工作的工具，並非所有雲端主機的必要條件。

## C1. AI 先檢查，使用者只補充缺少資料

1. 沿用已選的雲端模式、工作室名稱及預算；名稱可以是個人名稱，其他公司資料選填。
2. 檢查目前對話可用的 GitHub、Codex 雲端、Sites／其他部署工具，以及已有的倉庫、環境和網站。
3. 先按 [任務選擇規則](INSTALL.md#先確定這次任務才讀取進度) 沿用本次目標。更新原站時，核對身份、來源、資料及登入後繼續原網站；不可用空白網站代替。使用者已明確選擇全新安裝、保留舊站時，另用新服務與持久儲存，原站不動，舊 Sites 的備份缺項不阻擋新原生安裝。
4. 記下目前完成階段及下一步，按 [安裝總入口](INSTALL.md) 在私人目錄維護 `INSTALL_PROGRESS.md`。這份檔案由安裝 AI 維護，並非 `setup` 自動產生；公司資料及部署秘密不提交至開源倉庫。

工具只可讀取而不可設定時，AI 先準備好可直接使用的設定內容，再引導使用者完成該一步；不能只說「你自行設定」。

## C2. GitHub 帳戶與完整程式碼

### 帳戶

已有 GitHub 帳戶便登入並沿用，**不需要為安裝重新開一個帳戶**。
沒有帳戶時，AI 帶使用者開啟 [GitHub 註冊](https://github.com/signup)，完成註冊及電郵驗證，然後回到安裝對話。
密碼、驗證碼及雙重驗證由使用者在 GitHub 頁面輸入；AI 在完成登入後核對已連接的帳戶。
參考：[GitHub 帳戶建立說明](https://docs.github.com/en/account-and-profile/how-tos/account-management/creating-an-account-on-github)。

### 程式碼倉庫

**GitHub 帳戶**是登入身份；**倉庫**是程式碼的位置。AI 應按用途選擇：

- 短期試用或直接使用未修改原版：可讀取 [Blueprint Studio Open Source](https://github.com/collieai-g/Blueprint-Studio-Open-Source)，不強制另建私人倉庫。
- 長期開發公司專用功能：在使用者帳戶建立／沿用已核實為 **Private** 的程式倉庫，例如 `blueprint-studio-private`，保存完整來源及公司自訂程式，保留 MIT 授權及上游來源版本。這是獨立私人副本；不要假設公開倉庫的 fork 可以改成私人。

建立私人倉庫前核對擁有者、名稱及可見性；已有同名倉庫先讀取內容、分支及 Git 狀態。
匯入時保留現有 README、提交及使用者修改，必要時在分支整合，不強制覆寫。
只搬程式、文件、測試及授權；公司資料庫、附件、私人安裝記錄與憑證繼續放在倉庫外。

上游發佈新版後，由 AI 在公司的私人程式倉庫整合指定正式版本、解決衝突、保留自訂改動並測試，再明確部署受影響服務。GitHub 提交不代表網站已更新，也沒有預設的上游自動同步或 GitHub 自動發佈至 Sites；部署證據須來自實際平台。日常報告、待辦、文件、收藏及公司資料經 API 保存；既有排版偏好與相容 Agent 登記／設定沿用原保存方式，均不需要 Git 提交或重新部署。見[日常操作與程式更新](OPERATING_MODEL.md)。

若 AI 工具不能建立倉庫，帶使用者開啟 [New repository](https://github.com/new)，核對 Owner、填入約定名稱並選 **Private**。
全新倉庫可保持空白以便 AI 匯入完整來源；已有內容則保留並整合。使用者建立後提供倉庫網址，AI 負責接著匯入、提交及核對遠端分支，不能停在空白倉庫。

新下載可由 AI 執行：

```sh
git clone https://github.com/collieai-g/Blueprint-Studio-Open-Source.git blueprint-folder-agent
cd blueprint-folder-agent
```

`blueprint-folder-agent` 在這裡是明確指定的本機資料夾名稱，避免倉庫改名後下一個指令找不到目錄。
已有 checkout 先檢查再更新，不重複 clone 到原有目錄。

**進入 Codex 環境設定前，AI 必須核實目標分支已包含完整程式：**

```text
blueprint.py
blueprint_folders/
blueprint_library/
library_web/
assets/icons/
company/Dockerfile
tests/
docs/
LICENSE
```

目標倉庫只有 README 時，先補齊並驗證程式碼；不能將空白倉庫交給下一個設定對話，亦不能為了通過檢查虛構 `package.json` 或套件依賴。

## C3. 接入 Codex 雲端工作環境

使用 Codex 雲端協助安裝時，由 AI 準備這一步。已有可用環境直接沿用；只連接本次工作需要的倉庫。
原生後台執行需要 Python 3.10+，沒有第三方 pip／npm 執行依賴；完整驗證亦使用 Node.js，版本參考倉庫 CI。

當目前工具不能替使用者建立環境時，按[官方流程](https://learn.chatgpt.com/docs/environments/cloud-environments#create-and-publish-an-environment)逐步操作：

1. 新對話選 **Work in → Cloud → Select environment → Create environment**；亦可從 **Settings → Codex Cloud → Environments** 進入。
2. 按提示連接 GitHub，授權剛才選定的倉庫，再選正確分支及 **Get started**。
3. 在設定對話貼上下面交接指令，由設定 AI 檢查及測試。
4. 檢視設定報告，保存設定並按 **Publish**；已有環境更新時按 **Republish**。環境供自己使用時保留 **Only me**。
5. 見到環境已發佈後，原 AI 提供不含秘密的安裝進度摘要，附實際倉庫版本、已有網站和下一步。從該環境開始工作對話，貼上摘要及 C4 的接續指令；新環境未必能讀取原來的私人檔案。

按鈕名稱以當前產品介面為準；AI 應查核使用者實際看到的入口，不要求重複建立已有環境。

交接時，AI 先把下列方括號換成已核實的值：

```text
請設定藍圖工作室的 Codex 雲端工作環境。
倉庫：[已包含完整程式碼的 owner/repository]
分支與來源版本：[實際分支及 commit]

先讀 AGENTS.md、README.md、docs/INSTALL.md 及 docs/INSTALL_CLOUD.md。
保留原有修改。這是 Python 標準庫後台，已有完整 library_web 介面。
依 .github/workflows/check.yml 檢查 Python、Node、測試及建置，
保存實際驗證過的 Install script 與 Start skill。
測試使用倉庫外的一次性資料；不把正式公司資料、管理員密碼、資料庫或
常駐服務放進可重用環境快照。完成測試後清理臨時服務和測試資料。
環境完成後交代實際倉庫版本、設定保存及 Publish 狀態。
下一步仍是正式網站部署，按 docs/INSTALL_CLOUD.md 的 C4 接續。
```

AI 執行的原生程式驗證與 CI 一致：

```sh
python3 -m unittest discover -s tests -v
node --test tests/test_packager.cjs
node --check library_web/app.js
node --check library_web/workdays.js
python3 tools/build_web.py
git diff --exit-code -- web/icons.js dist/index.html
```

HTTP 測試需要環境允許本機 loopback socket。遇到權限或網絡錯誤，按平台的環境設定修正並重試，記錄仍未完成的檢查，不能把測試失敗說成成功。
測試數量會隨版本改變，以實際結果為準。`tools/build_web.py` 建置的是保留的離線工具，原版公司後台由 `blueprint_library` 配合 `library_web` 提供。

## C4. 環境發佈後，繼續建立可用網站

**Codex 環境 Publish／Republish 的結果是 AI 可使用的工作環境。**
此時安裝狀態應顯示「環境已就緒，正式工作台待部署」。下一步要取得真正的後台網址及永久資料儲存。
雲端終端機印出的 `127.0.0.1` 屬於那部雲端機器；不能直接當作使用者電腦可開啟的網址。

新對話接續指令：

```text
請接續藍圖工作室雲端安裝。Codex 工作環境已發佈。
先讀本專案 docs/INSTALL.md、docs/INSTALL_CLOUD.md 及私人 INSTALL_PROGRESS.md。
核對現有倉庫、網站、版本與資料位置，保留全部已有內容。
帶我完成正式部署、原版介面、真實保存及 AI 連接。
已有平台選擇就沿用；沒有才解釋合適的選擇。
必須保留 library_web 原版版面、圖示、導覽、文字與完整功能。
請不要把安裝改成重新設計 UI，也不要交付只有示範資料的介面。
```

部署平台屬於**雲端路線內的選擇**。AI 先確認既有帳戶與偏好，只展開所選分支：

| 選擇 | 安裝方式 | 目前開源版狀態 |
| --- | --- | --- |
| Render／Railway／自己的主機 | 直接運行現有 Python 後台及原版介面 | 已提供 Docker 與部署設定 |
| Sites 入口＋原生後台 | 原生服務保存資料與備份，Sites 提供原版畫面及 MCP 入口 | 已提供接駁模組與建置工具；需完成兩邊部署、私人身份對應及驗收，見 [接駁指南](SITES_NATIVE_GATEWAY.md) |
| Codex／ChatGPT 的 Sites | 以 Sites 支援的執行架構接駁原版介面，提供持久儲存及登入 | 本倉庫尚未附可直接部署的 Sites 後台適配版本，需完成 C6 的接駁工作 |

使用者選 Sites 就沿用該選擇，先核對工具與適配進度；不能暗中切換平台。
沒有偏好而希望盡快使用現有程式，可介紹原生部署路線；平台費用及帳戶資格以當前頁面為準。
右側開啟網站的能力與網站的部署平台是分開的，AI 要實際確認該帳戶可在目標位置重新開啟同一個網址。

## C5. 原生雲端部署

### C5.1 準備私人設定

選定原生平台後，在工具目錄執行，例如：

```sh
python3 blueprint.py setup --company-name "我的工作室" --mode cloud --provider render --output "../我的工作室安裝"
```

Railway 使用 `--provider railway`，自己的主機使用 `--provider server`。
現有 `setup --provider` 只接受 `render`、`railway`、`server`；沒有 `sites` 選項。
`setup` 只產生私人公司資料與下一步指引，接著必須完成真正部署。
已有安裝設定時先核對並沿用，不覆寫既有私人計劃。

### C5.2 登入、設定並部署所選平台

AI 透過已連接的平台工具完成可執行部分。首次登入、身份驗證及未獲授權的費用由使用者在平台確認；AI 繼續核對結果。

**Render**

1. 登入自己的 Render 帳戶。使用原版時開啟[部署入口](https://render.com/deploy?repo=https://github.com/collieai-g/Blueprint-Studio-Open-Source)；使用私人修改版本時，由 AI 指向已確認的私人倉庫並接通 Render 的 GitHub 存取。
2. 讀取 `render.yaml`：Dockerfile 為 `company/Dockerfile`，單一服務、1 GB 永久磁碟掛載 `/data`、健康檢查 `/health`，模板 port 為 8787。核對頁面當時的服務及磁碟費用後建立。
3. 填入下表的公司名稱及管理員設定，等待部署完成。模板關閉自動部署，日後更新需操作新版本部署。
4. 取得服務實際 HTTPS 網址，繼續 C7。可參考[官方部署入口](https://render.com/docs/deploy-to-render)及[永久磁碟](https://render.com/docs/disks)。

**Railway**

1. 登入自己的帳戶，從選定 GitHub 倉庫建立服務；使用根目錄 `railway.json`，Dockerfile 為 `company/Dockerfile`。
2. 建立持久 Volume 並掛載 `/data`，填入下表變數。程式跟隨平台 `PORT`，未指定時用 8787；設定 HTTPS 網域的目標 port 與服務一致。
3. 如遇非 root 映像的 Volume 權限問題，依[Railway 官方 Volume 說明](https://docs.railway.com/volumes/reference)評估 `RAILWAY_RUN_UID=0` 後重部署；保留原 Volume。
4. 核對方案費用後完成部署，取得實際 HTTPS 網址，繼續 C7。SQLite 只使用單一服務實例。

**自己的雲端主機**

1. AI 核對已有主機、Docker、網域、備份及授權，將選定程式版本放到主機。
2. 在主機安全設定環境變數；可用私人 env 檔，避免將秘密提交 Git。於程式目錄使用 `docker compose --env-file /實際私人路徑/deployment.env up -d --build`，由 AI 先換成真實存在的路徑。
3. `compose.yaml` 將公司資料保存到 `company-data` volume；保留並記錄實際 volume 名稱。服務綁定主機 `127.0.0.1:8787`。
4. AI 設定 Caddy／Nginx、有效 TLS 及網域，把 HTTPS 代理至該 port，保留原始 Host。詳細設定見[後台部署說明](LIBRARY.md)。取得真實 HTTPS 網址後繼續 C7。

原生平台的設定值：

| 變數 | 填寫方式 |
| --- | --- |
| `BLUEPRINT_DATA_DIR` | 新安裝使用 `/data/library`；持久磁碟掛載 `/data`，已有安裝沿用原路徑 |
| `BLUEPRINT_BACKUP_DIR` | 新安裝使用 `/data/backups`，與資料分開並同樣持久保存 |
| `BLUEPRINT_COMPANY_NAME` | 工作室／公司名稱，可使用個人名稱 |
| `BLUEPRINT_COMPANY_INDUSTRY`、`BLUEPRINT_COMPANY_DESCRIPTION`、`BLUEPRINT_COMPANY_GOALS` | 選填，可登入後補充 |
| `BLUEPRINT_ADMIN_USERNAME` | 管理員名稱，例如 `admin` |
| `BLUEPRINT_ADMIN_PASSWORD` | 至少 12 字元，由使用者在平台秘密設定或安全終端機輸入 |

原生後台在瀏覽器顯示 HTTP Basic 登入提示，使用設定的管理員帳號及密碼。
公司資料與管理員只在首次建立；改環境變數不會重設已有帳戶。若已有資料，先核對原磁碟及登入方式，不能刪資料重新開始。

## C6. Sites 部署：保留原版畫面與完整功能

希望使用 Sites 入口而資料由原生後台管理時，改按 [SITES_NATIVE_GATEWAY.md](SITES_NATIVE_GATEWAY.md) 完成 C5 原生部署、身份對應與 Sites 接駁。這是另一種儲存架構；下列步驟專門處理資料放在 Sites D1／R2 的路線。已有舊資料不能自動切換架構。

這條原生接駁路線日後更新時，若只改原生後台或由它提供的介面，而且 Sites 接駁與工具定義保持相容，只需部署受影響的原生服務；若改動 Sites 接駁程式、工具定義或平台資產，須另按平台流程發佈**同一個 Site**。兩邊的來源版本與正式部署結果分開核對，不能由私人 GitHub commit 推斷 Sites 已更新。

Sites 分支由具備 Sites 工具的 AI 執行，先讀當時可用的官方 Sites 技能及部署規格。
這一節提供完整交付要求；**它不代表目前 Python 倉庫已能一鍵上傳到 Sites 運行。**
Sites 平台有自己的執行環境，不能將原生 Docker／SQLite 本機路徑直接當成它的正式儲存。

1. **核對帳戶及既有網站。** 已有同用途網站就沿用其網站 ID、網址、資料庫和存取設定；先檢查現有來源及部署版本。需要連接 Sites 時，由 AI 引導完成帳戶連接。新網站按使用者需要設定私人存取，不能自行公開公司工作台。
2. **檢查適配版本。** 如果來源已有經驗證的 Sites 版本，沿用該版本。只有 Python 原版時，AI 要明確列出並完成後台適配工作；不能宣稱執行 `setup` 已完成 Sites 安裝，也不能省略功能交付。
3. **鎖定原版介面。** 記錄來源 commit，直接沿用 `library_web/index.html`、`library_web/style.css`、`library_web/app.js`、`library_web/workdays.js`、`library_web/workspace.js`、`library_web/brand.svg` 及其引用圖示資源。核對檔案內容及實際桌面、窄欄和手機畫面。保留原有導覽、首頁、工作頁、文字及全部操作；不以重新生成的分頁、卡片或簡化清單代替。
4. **實作相容後台。** 依 [API 文件](LIBRARY.md)及原始程式完成登入、永久資料儲存、附件、資料隔離、版本檢查與歷史。使用當前 Sites 支援的資料庫及物件儲存；適配層要回應原版 UI 所需的路徑、欄位及錯誤格式。平台登入可由平台處理，原有工作介面保持一致。
5. **逐項驗證功能。** 完成[統一驗收](INSTALL.md#完整原版工作台驗收)列出的全部入口與操作，包括修改、封存、恢復、下載、AI 選取交接、每日紀錄、待辦及匯出。測試用資料與正式資料分開，不預填公司的虛構工作紀錄。
6. **正式部署並開啟。** 按 Sites 當前流程保存來源、建立版本、部署及核對完成狀態，使用工具實際回傳的正式網址。在使用者希望的右側或瀏覽器開啟；需要身份驗證時由使用者完成登入。
7. **接續 C7、C8。** 重新開啟網站、驗證同一筆資料及正式儲存後，再接通 AI。部署工具回報成功或本機測試通過，都不能代替正式登入後的操作驗證。

若有平台限制令某個原版功能不能使用，AI 必須列出未完成項並繼續處理；需要改動可見設計或功能時先說明具體影響，不能自行重新設計。
工具、登入或平台能力暫時欠缺時，保存實際進度並提供下一個可執行步驟，保持「未完成」狀態；臨時示範頁不作正式安裝交付。

## C7. 使用者開啟自己的完整工作台

AI 提供一個**已核對的實際網址**，帶使用者登入並看到原版畫面。
依[統一驗收](INSTALL.md)檢查所有入口，先完成一個最短日常操作：保存一筆工作、加入待辦、勾選完成，再重新開啟查回。

接著驗證同一網站在新登入工作階段仍可讀取原記錄，並在保留同一資料儲存的服務重啟／部署後再次查回。
AI 記錄網站、資料庫／磁碟及附件儲存的實際位置。生產網址受網絡或登入限制無法實測時，明確標示尚待使用者配合的驗證項。

## C8. 由左側 Codex 設定 AI 操作

由左側讀取 [AI_CONNECT.md](../AI_CONNECT.md)，核對 C7 已驗證的正式 origin、公司與身份，以及目前對話實際提供的工具。能控制同一個已登入工作台時，優先使用正式表單保存並重新開啟相同紀錄；僅有網站預覽不能當成畫面控制。

MCP 是可選路徑。選用時再按 [AI_CONNECTION.md](AI_CONNECTION.md) 接通：原生雲端以 stdio 橋接原 HTTPS 後台，Sites 使用同一網站的已驗證 HTTP MCP 外掛。客戶端產生 32 位十六進位 challenge，記錄實際 client 及有效 date，透過真正 `blueprint_verify_connection` 保存、讀回並更新待辦，再核對相同紀錄。重試沿用 challenge，不依賴右側接駁精靈，也不以畫面或直接 API 冒充 MCP 驗收。

以下是原有每日工作 CLI 綁定的補充，已有綁定者可沿用，畫面路徑不必使用。CLI 寫入及目前對話已載入 MCP 是不同證據。

**原生 Python 後台**：在 AI 執行環境的安全設定提供 `BLUEPRINT_BACKEND_USERNAME` 及 `BLUEPRINT_BACKEND_PASSWORD`。
確認後台的實際主機可被該環境存取，再使用：

```sh
python3 blueprint.py workday-connect "../我的 AI 工作室" --url "https://你的真實後台網址" --username-env BLUEPRINT_BACKEND_USERNAME --password-env BLUEPRINT_BACKEND_PASSWORD
```

AI 在執行前換成 C7 已驗證的網址及私人工作室路徑，讀取產生的 `WORKDAY.md`，實測保存與查詢。
設定只記秘密變數名稱；密碼不寫入指令、聊天、Git 或安裝進度檔。

**Sites 後台**：使用該網站實際支援的 AI 工具及授權方式。核對插件／工具已連接並載入目前對話，再實測讀寫。
Sites 登入與原生 HTTP Basic API 未必相同，不能直接套用 `workday-connect` 並宣稱成功。
沒有已驗證的相容 API 時，先使用網站提供的工具；尚未接通則保留 JSON 匯入路線並標記「AI 寫入待完成」。

兩種平台都要驗證：「今日收工」產生的記錄出現在同一個網站；AI 能查回同一 ID，更新保留歷史與已完成待辦。
只有網站可用而 AI 未接通時，分別交代兩項狀態，不另建本機資料庫冒充雲端保存。

## C9. 交付與日後更新

交付[安裝總入口](INSTALL.md)所列的完成資料，讓使用者知道日常開哪個網址、資料保存在哪裏、AI 是否接通，以及下次如何接續。

- **更新程式**：在選定來源保留修改並更新版本，再部署同一網站／服務，沿用資料儲存及網址。先備份，後核對原記錄。
- **更新 Codex 環境**：依官方設定流程保存及 Republish，供之後的新任務使用。這不會自動更新正式網站。
- **保存日常工作**：透過已驗證的工作台或 AI 連接寫入，不需要每次 Publish／Republish。

遇到問題時，AI 在原安裝進度上修正，列出「目前階段、已完成項、實際錯誤、下一個操作」，保留原始資料與完整介面。

## 由左側完成驗收與第一項工作

工作台可開啟後，按 [AI_CONNECT.md](../AI_CONNECT.md) 在左側完成能力核對、必要登入及所選路徑的真實保存／讀回，不再以右側精靈作設定入口。新總覽只顯示標題與「調整排版」，卡片按需要加入；側欄保留四個主入口與「設定」。最後完成第一項真實工作；換對話沿用私人進度，不重建工作台，網站部署成功與目前 AI 可操作分別記錄。
