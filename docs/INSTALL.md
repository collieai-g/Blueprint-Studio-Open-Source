# 通用 AI 安裝指引

[返回首頁](../README.md)

這是給 AI Agent 及使用者共同跟進的原生工具包安裝流程。
目標是建立一個真正可登入、能保存公司工作的專屬後台。
有讀檔、終端機、瀏覽器或部署工具的 AI 可代辦相應步驟；純聊天 AI 逐步指導使用者操作。

## 1. 先了解公司與使用方式

先問一組簡單問題：公司／創業名稱、行業、公司簡介、目前目標。名稱必填，其餘可以留空。
再問使用 **雲端（預設）** 或 **本機**；不要將雲端默認理解為已接好某個供應商。

雲端再了解已有帳戶、偏好平台及預算。沒有偏好可介紹 Render 的部署入口；亦支援 Railway 和自己的主機。
讓使用者在供應商頁面核對當前費用。未獲授權的收費資源先確認；已有授權沿用。
密碼及金鑰由使用者在平台秘密設定、安全環境或終端機輸入，不放進聊天或公開 Git。

## 2. 下載工具並準備私人設定

確認 Git 及 Python 3.10+。已有安裝先檢查來源、Git 狀態、服務及資料位置，保留現有修改，不重建舊資料庫。
新安裝可下載 ZIP 或：

```sh
git clone https://github.com/collieai-g/blueprint-folder-agent.git
cd blueprint-folder-agent
```

依剛才收集的資料執行，例如：

```sh
python3 blueprint.py setup --company-name "我的公司" --industry "設計服務" --mode cloud --provider render --output "../我的公司後台設定"
```

`setup` 在互動終端機會詢問名稱、基本資料及模式；非互動 AI 工具用參數提供答案，模式預設 cloud。
可加 `--description`、`--goals`、`--username`；本機改為 `--mode local`。

私人目錄包含：

```text
我的公司後台設定/
├── company-profile.json       # 公司基本資料
├── setup.json                 # 選定模式，狀態仍為待部署／待啟動
├── deployment.env.example     # 沒有密碼的環境設定範本
└── START_HERE.md               # 按模式產生的下一步
```

準備這些檔案後，**繼續完成所選模式及驗證**。不要在產生計劃後宣稱安裝完成。

## 雲端使用

公司資料使用 SQLite 及原檔，需一個運行服務、一個永久磁碟，以及 HTTPS。
程式與資料分開存放；維持單一服務實例，重部署沿用同一磁碟。

### Render

1. 由使用者登入／建立自己的 Render 帳戶。
2. 開啟[專案部署入口](https://render.com/deploy?repo=https://github.com/collieai-g/blueprint-folder-agent)，檢查 `render.yaml`。
3. 模板使用 Docker、單一付費運行實例及 1 GB 永久磁碟 `/data`；核對平台顯示的實際費用後才確認建立。
4. 在平台填公司名稱、管理員帳號及長密碼。其他公司欄位可從私人範本加到環境變數：

| 變數 | 內容 |
| --- | --- |
| `BLUEPRINT_COMPANY_NAME` | 公司名稱 |
| `BLUEPRINT_COMPANY_INDUSTRY` | 行業，選填 |
| `BLUEPRINT_COMPANY_DESCRIPTION` | 公司簡介，選填 |
| `BLUEPRINT_COMPANY_GOALS` | 目前目標，選填 |
| `BLUEPRINT_ADMIN_USERNAME` | 管理員名稱 |
| `BLUEPRINT_ADMIN_PASSWORD` | 至少 12 字元，只在秘密設定填寫 |

5. 確認 `BLUEPRINT_DATA_DIR=/data`、磁碟確實掛載 `/data`；Dockerfile 是 `company/Dockerfile`，監聽 `PORT`（模板為 8787）。
6. 等待服務建立，開啟供應商實際分配的 HTTPS 網址，登入並核對公司資料；未填完整可在後台「公司設定」補充。
7. 完成下方保存及重啟驗證，再接上 AI 工作室。

Render 的部署按鈕需要登入及確認；永久磁碟需要付費服務，不能以臨時檔案系統代替。
參考：[官方部署入口說明](https://render.com/docs/deploy-to-render)、[永久磁碟](https://render.com/docs/disks)。
模板關閉自動部署；更新工具時在服務操作新版本部署，保留原磁碟。

### Railway

1. 登入自己的帳戶，從公開 repo 建立服務，使用本專案 `railway.json`；Dockerfile 指向 `company/Dockerfile`。
2. **建立 Volume 掛載 `/data`**，設定 `BLUEPRINT_DATA_DIR=/data`，填入上表公司與管理員變數。
3. 程式會跟隨平台 `PORT`，沒有指定時用 8787；公開網域的目標 port 必須一致，開啟 HTTPS。
4. 本映像預設使用非 root UID；若 Volume 出現權限錯誤，按[Railway 官方說明](https://docs.railway.com/volumes/reference)設定 `RAILWAY_RUN_UID=0` 後重部署。不要刪除原 Volume。
5. 核對費用、開啟真實網址，執行登入及重啟保存驗證。維持單一服務，SQLite 不配多副本。

### 自己的雲端主機

用 `compose.yaml` 啟動服務，`company-data` volume 保存資料；Caddy／Nginx 將 HTTPS 網域代理到主機 `127.0.0.1:8787`。
代理保留原始 Host。具體指令見[後台部署說明](LIBRARY.md)。

### 雲端 AI 寫入

有可用網址之後，連接私人工作室：

```sh
python3 blueprint.py workday-connect "../我的 AI 工作室" --url "https://你的真實後台網址" --username-env BLUEPRINT_BACKEND_USERNAME --password-env BLUEPRINT_BACKEND_PASSWORD
```

在 AI 工具的安全環境設定這兩個變數，值為後台管理員帳號及密碼。設定檔只記變數名稱，不保存密碼。
AI 工具需要可存取網址及該安全環境；沒有這些權限時，使用後台 JSON 匯入。

**雲端連結經已登入的 HTTPS API 寫入同一個後台；不能另建本機資料庫冒充雲端保存。**

## 本機使用

跟隨私人 `START_HERE.md` 的實際路徑。例如：

```sh
python3 blueprint.py library-init "../我的公司資料" --username admin --company-file "../我的公司後台設定/company-profile.json"
python3 blueprint.py workday-connect "../我的 AI 工作室" --data "../我的公司資料"
python3 blueprint.py library --data "../我的公司資料"
```

第一步由使用者在終端機輸入密碼；AI 自動化亦可從安全環境讀取 `BLUEPRINT_ADMIN_PASSWORD`，不要放進聊天。
已有登入設定時不要重做初始化；核對原路徑並接續啟動。

開啟程式輸出的真實地址，通常是 `http://127.0.0.1:8787`。保持服務運行，這個地址只代表該部電腦。
Windows 若使用 Python launcher，可將 `python3` 換成 `py -3`。

## 3. 驗證完成再交付

AI 跟使用者逐步驗證：

1. 開啟真實地址，確認未登入不能讀公司資料，使用管理員登入。
2. 核對公司名稱、行業、簡介及目標。
3. 保存一筆真實而簡短的工作紀錄，加入待辦並勾選完成。
4. 用已連接 AI 的查詢或後台搜尋找回同一筆紀錄。
5. 重啟服務／重新部署，保留原磁碟或本機路徑；查回同一紀錄、公司設定及待辦完成狀態。
6. 交付實際網址、私人資料位置／磁碟、工作室路徑，以及任何仍需使用者操作的事項。

失敗時保留原資料，依錯誤修正；未完成要說明實際停在哪一步。
沒有真實網址、登入及保存驗證，不可聲稱「已部署成功」。

## 4. 每日使用及更新

接好後說「今日收工」，AI 按工作室 `WORKDAY.md` 整理目前可見或明確提供的對話。
有用內容按成果、決定、參考及待辦分配；可合併及修整已有紀錄，保留版本歷史及完成進度。
跨對話資料要由使用者提供，或經另行授權的工具讀取，不代表能自動讀各家 AI 所有聊天。

「今日收工」更新公司工作資料；升級程式則更新公開工具、重新啟動／部署，兩者都保留私人資料。
詳細格式、修整及歷史見[每日工作](DAILY_WORK.md)。
