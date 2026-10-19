# Blueprint Studio Backend｜公司文件與資料收藏

[返回首頁](../README.md)

**Blueprint Studio Backend｜藍圖工作室（後台）** 是為創業者而設、可逐步擴充的工作後台。
你可以為自己或公司部署獨立工作室。今版集中兩個功能：整理公司的文件，以及收藏網站／UI 參考和 YouTube 知識連結。
這是有登入及伺服器保存功能的後台，與舊版只保存在瀏覽器的資料包介面分開。
介面先提供可操作結構，視覺設計會按之後提供的方向調整。

## 可以做甚麼？

- 上傳文件：保留原檔，按名稱、檔案格式及可讀文字自動建議分類、標籤和統一圖示。
- 收藏資料：保存網站或 YouTube 網址、標題、筆記，加入自訂收藏夾。
- 整理與搜尋：用名稱、筆記、標籤、網址或可讀文字搜尋，按分類及收藏夾篩選。
- 隨時修正：手動修改標記、重新分類、封存及恢復資料；修改紀錄可透過 API 或匯出清單查閱。
- 持久保存：重啟服務後資料仍在。資料目錄與公開程式分開，更新工具不會重建資料庫。

自動分類目前使用本機規則，沒有接入語言模型或傳送文件給 AI 公司。
每項資料保留分類依據；手動修改後，一般編輯會保留你的標記。
只有明確按「重新自動分類」才重設分類及標籤。

## 在自己的電腦使用

需要 Python 3.10+，無需安裝額外套件。在工具目錄執行：

```sh
python3 blueprint.py library-init "../藍圖公司資料" --username admin
python3 blueprint.py library --data "../藍圖公司資料"
```

第一行會在終端機要求設定至少 12 字元的密碼，輸入不會顯示。
第二行啟動 `http://127.0.0.1:8787/`。用瀏覽器的登入提示輸入管理員帳號及密碼。
伺服器會保持運行；停止時在終端機按 Ctrl+C。

如環境使用 `python`，可將上述 `python3` 改為 `python`。
本機測試預設只監聽自己電腦，並不會自動產生外出可用的網址。
如由 Codex 代辦，讓你自己在本機輸入密碼；不要把私人密碼加入對話或 Git。

資料會放在：

```text
藍圖公司資料/
├── access.json       # 管理員密碼雜湊，沒有明文密碼
├── library.sqlite3   # 收藏、標記、筆記及修改紀錄
└── blobs/            # 原始上傳文件
```

資料目錄必須在公開工具目錄以外。現有登入設定不會被初始化指令覆寫。

## 放到自己的雲端主機

以下提供可自行部署的 Docker 設定；本專案並未因此自動上線到雲端。
需要可運行 Docker 的主機、持久儲存，以及提供 HTTPS 的反向代理。

1. 將工具程式下載到主機。
2. 把根目錄 `.env.example` 複製為 `.env`，自行設定管理員名稱及長密碼。
3. 在專案目錄執行：

```sh
docker compose up -d --build
```

Docker 將資料保存到 `company-data` volume，服務只綁定主機的 `127.0.0.1:8787`。
用 Caddy 或 Nginx 將自己的 HTTPS 網域代理到此服務，保留原始 Host header。
Basic 登入需要 HTTPS 才適合經互聯網使用。TLS 憑證及網域由你的主機設定提供。

首次啟動會初始化管理員；之後重啟保留原有設定。修改 `.env` 密碼不會重設既有帳號。
今版是一個公司管理員的資料庫；員工獨立帳號、角色及跨公司權限留待另行設計。
預覽／測試資料庫與實際公司資料應使用不同資料目錄或 volume。

## 更新與備份

更新 Git 下載的工具後，重新啟動後台即可使用新版：

```sh
git pull --ff-only
python3 blueprint.py library --data "../藍圖公司資料"
```

使用 Docker 則更新程式後重新執行 `docker compose up -d --build`，保留同一個 volume。
更新前先處理本機程式修改，並備份私人資料。

「匯出資料清單」包含筆記、標記、收藏夾、已抽取的可讀文字及修改紀錄，**不包含原始檔案附件**。
完整備份需先停止服務，再備份整個私人資料目錄或 Docker volume，包括 SQLite 相關檔案及 `blobs/`。
目前沒有一鍵匯入清單或備份還原介面。

## 自動分類與格式限制

- 每份上傳文件最多 20 MiB；空白文件亦可保存。
- UTF-8 文字／程式碼在不超過 200 KiB 時會抽取內容供搜尋與分類。
- PDF、Office、圖片、影片及較大文件保留原檔；目前按名稱及格式分類，未有 OCR 或全文抽取。
- 收藏網址不會自動抓取網頁、下載 YouTube 影片或讀取字幕。請在筆記記錄值得保留的內容。
- 規則未能判斷時使用通用分類，你可以手動改分類及標籤。
- 搜尋是關鍵字搜尋，並非語意搜尋；今版不會自動讀取各 AI 平台的聊天記錄。

## 日後 Agent 可使用的 API

後台提供同源 JSON API，便於日後開發接入。現時未連接 OpenAI、Gemini 或 Claude，
亦未提供 MCP server、Agent 獨立權限或 API token 管理。

| 操作 | API |
| --- | --- |
| 列出／新增收藏夾 | `GET /api/collections`、`POST /api/collections` |
| 搜尋／新增收藏 | `GET /api/items`、`POST /api/items` |
| 讀取／修改資料 | `GET /api/items/{id}`、`PATCH /api/items/{id}` |
| 查看修改紀錄 | `GET /api/items/{id}/history` |
| 上傳／下載文件 | `POST /api/files`、`GET /api/files/{id}` |
| 預覽規則分類 | `POST /api/classify` |
| 匯出 metadata | `GET /api/export` |

API 需要管理員 HTTP Basic 登入。寫入另外需要 `Content-Type: application/json`
及 `X-Blueprint-Request: 1`，跨來源寫入會被拒絕。
上傳使用 JSON `name`、`mime`、`content_base64`，可加 `notes`、`tags`、`collection_id`。
修改需要最新的 `expected_version`；版本不一致時回傳 409，避免覆蓋較新的內容。
搜尋支援 `q`、`kind`、`category`、`collection_id`、`source_type`、`archived`、`limit`、`offset`。

這些 API 是擴充基礎；接入外部 Agent 前應先加入專用的授權及資料範圍設計。
