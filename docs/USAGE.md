# 詳細使用說明

[返回專案首頁](../README.md) · [更新紀錄](../CHANGELOG.md)

一個普通文件夾，一張 AI 可讀身份卡，一套統一圖示。

Blueprint Folder Agent is a local-first folder marker and portable workspace
for use with Codex. Start with an empty folder and develop its purpose over time.

文件夾可以完全空白起步，無需預先設定工作目的。你與 Codex 溝通後，
再逐步加入內容、用途及標籤。這個小程式負責保存及展示資料，推理和開發由 Codex 提供；
程式不內置語言模型、不需要 API 金鑰、不連接雲端服務。

![桌面介面示例：文件夾卡片、統一圖示及可編輯身份標記](../artifacts/desktop.png)

上圖為示範資料；首次開啟的工作區是空白的。

## 公開工具與自己的工作室一齊更新

需要長期用 Codex 管理 AI 程式專案時，建議保留一份共用工具，
再建立一個獨立的私人工作室。工作室透過小型啟動檔使用同一份工具，
無需為自己另複製一套程式或維護第二個 fork。

```text
你的電腦/
├── blueprint-folder-agent/       # 公開工具，通用功能在這裏更新
└── 藍圖專員工作室/               # 自己的專案、標記、工作指引
    ├── blueprint-studio.py       # 連結共用工具的啟動檔
    ├── AGENTS.md                 # 可自行補充的 Codex 工作指引
    ├── .blueprint/tool.json      # 本機工具位置
    └── WhatsApp客服/             # 個別專案的程式碼及身份卡
```

需要 Git 和 Python 3.10+。在你準備存放兩個文件夾的位置執行：

```sh
git clone https://github.com/collieai-g/blueprint-folder-agent.git
python3 blueprint-folder-agent/blueprint.py link "藍圖專員工作室"
python3 "藍圖專員工作室/blueprint-studio.py" create "第一個專員"
python3 "藍圖專員工作室/blueprint-studio.py" --version
```

在 Codex 開啟「藍圖專員工作室」作為自己的工作專案。之後可直接說：

> 請先讀 AGENTS.md、.blueprint/tool.json 及工作室清單，使用 blueprint-studio.py
> 管理這個工作室。按我們的對話更新專案身份標記；通用工具功能到連結的工具專案
> 開發，私人程式碼及指引保存在本工作室。

在工作室內，指令可以省略工作室路徑：

```sh
python3 blueprint-studio.py create "WhatsApp客服" --category chat --tag 開發中
python3 blueprint-studio.py mark "WhatsApp客服" --description "企業客服回覆" --tag WhatsApp
python3 blueprint-studio.py search "WhatsApp"
python3 blueprint-studio.py reindex
```

| 你更新的內容 | 如何在兩邊生效 |
| --- | --- |
| 在本機共用工具開發通用功能 | 測試後提交、推送到公開 GitHub；自己的啟動檔已使用這份程式 |
| GitHub 上已有新功能 | 本機拉取新版；工作室下次執行就使用新版 |
| 自己的專案、標記或 Codex 指引 | 保存在工作室，可持續修改；與公開工具分開管理 |

拉取公開更新，在存放兩個文件夾的父目錄執行：

```sh
git -C blueprint-folder-agent pull --ff-only
python3 "藍圖專員工作室/blueprint-studio.py" --version
```

工具在同一路徑更新後，啟動檔會使用更新的程式；已啟動的本機服務請重新啟動。
如有本機工具修改，先讓 Codex 檢查 Git 狀態並處理提交／分支，再拉取更新。
工作室內的自訂指引及程式碼會保留。更新工具不會自動修改各個 AI 專案的程式碼，
日後如功能需要改資料格式，應另外提供遷移步驟。

`link` 亦可連結既有工作室，保留現有 `AGENTS.md`、專案及身份資料。
工作室與工具目錄必須分開。搬動共用工具，或改用新版 ZIP 解壓後的新位置，
請用新位置的 `blueprint.py link "工作室路徑"` 重新連結；本機路徑需在各裝置分別設定。
此功能不會自動拉取 GitHub 或跨裝置同步私人內容。

啟動檔的 `gui`／`serve` 仍開啟下述瀏覽器資料包介面，並不直接讀寫工作室檔案。
瀏覽器介面更新前請匯出 JSON／ZIP 備份；使用固定的 localhost 網址及同一瀏覽器，
避免因下載路徑或網址改變而開啟另一個儲存空間。

## 直接使用

下載並用瀏覽器開啟 **`dist/index.html`**。它是包含全部程式碼與圖示的單一 HTML，
無需安裝 npm 或 Python，也沒有 CDN 依賴。

1. 按「建立空白文件夾」，輸入名稱；用途及標籤可留空。
2. 選擇圖示，之後隨時編輯標記或加入小型文字檔案。
3. 下載 ZIP 資料包。解壓後，就是有身份標記的實際文件夾。
4. 在 Codex 開啟該文件夾，請它讀取身份卡及現有內容，再按照你的要求開發。
5. 將更新後的文件夾重新封裝為 ZIP，匯入介面繼續整理。

桌面及手機使用同一個 responsive 介面。手機建議透過 HTTPS 靜態網站開啟；
部分手機檔案預覽器不會執行本機 HTML。瀏覽器內的卡片保存在當前瀏覽器，
不會直接更改電腦／手機檔案系統，也不會自動跨裝置同步。
要轉移或備份，請下載 ZIP 或匯出 JSON；清除瀏覽器資料會清除本機清單。

圖示由本程式統一顯示，亦保存在資料包內。系統的檔案總管可能繼續使用系統圖示；
本程式不修改作業系統設定。

## 配合 Codex 建立實際文件夾

這部分需要 Python 3.10+，全部使用標準函式庫，**不用 pip install**。
在本專案目錄執行（你的環境可能用 `python` 或 `python3`）：

```sh
python3 blueprint.py init "/你的路徑/藍圖專員工作室"
python3 blueprint.py create "/你的路徑/藍圖專員工作室" "第一個文件夾"
```

第二行可以不填用途。之後再逐步補充：

```sh
python3 blueprint.py mark "/你的路徑/藍圖專員工作室/第一個文件夾" --description "協助整理客服回覆" --category chat --tag WhatsApp --tag 客服
python3 blueprint.py search "/你的路徑/藍圖專員工作室" "客服"
python3 blueprint.py inspect "/你的路徑/藍圖專員工作室/第一個文件夾"
```

可用圖案：`general`、`chat`、`web`、`finance`、`document`、`calendar`、`automation`、`research`。
`auto` 只按本機關鍵字建議圖案，並不是 AI 模型分類。空白名稱以外的用途資料可以留空。

可直接對 Codex 說：

> 請用這個專案的 blueprint.py，在我指定的工作室目錄建立一個空白文件夾。
> 先不要預設用途。之後每次開始工作，先讀 .blueprint/folder.json 及現有檔案；
> 按我們的對話逐步補充用途與標籤，保留既有身份編號及內容。

程式會建立：

```text
第一個文件夾/
├── AGENTS.md                 # Codex 工作指引；原有檔案不會被覆寫
└── .blueprint/
    ├── folder.json           # 名稱、身份、用途、標籤、圖案類型
    ├── icon.svg
    └── icon.png
```

父目錄的 `.blueprint/index.json` 和 `INDEX.csv` 會同步更新。
搬動或重新命名文件夾後，執行 `python3 blueprint.py reindex "工作室路徑"` 重建清單。

## 把 Codex 更新帶回介面

```sh
python3 blueprint.py pack "/你的路徑/藍圖專員工作室/第一個文件夾" --output "/另一個路徑/第一個文件夾.zip"
```

然後在瀏覽器版匯入 ZIP。會保留標記、文字、二進位檔案及既有 `AGENTS.md`。
封裝會略過 `.git`、`node_modules`、虛擬環境與 Python 快取；ZIP 上限 20 MB／5,000 檔。
瀏覽器本機保存的容量較小，適合標記及小型內容；大型程式庫請繼續保存在電腦或 GitHub。
封裝前請自行檢查內容，程式不會自動辨認或刪除你的憑證檔案。

## GitHub 與手機網址

GitHub 專案：[collieai-g/blueprint-folder-agent](https://github.com/collieai-g/blueprint-folder-agent)。
可直接[下載完整程式碼 ZIP](https://github.com/collieai-g/blueprint-folder-agent/archive/refs/heads/main.zip)。
把 `dist/` 目錄部署到任何靜態網站服務，就能在手機瀏覽器使用。
專案附有手動執行的 GitHub Pages workflow：先在 repository 的 Settings → Pages
選擇 GitHub Actions，再於 Actions 執行「Publish folder app」。這會公開瀏覽器介面；
文件夾內容仍只保存在使用者瀏覽器，程式沒有上傳內容的 API。
GitHub Pages 是否可用取決於 repository 可見度、帳戶方案及設定。

GitHub 雲端的檔案不會由本程式自動同步。要與 Codex 交換工作成果，使用資料包或 Git。

## 開發及驗證

```sh
python3 tools/build_web.py
python3 -m unittest discover -s tests -v
node --test tests/test_packager.cjs
python3 blueprint.py serve --no-open
```

圖示已包含在下載包內。只有重新製作圖示才需要 Pillow：
`python3 tools/build_icons.py`。

本程式沒有自動部署或執行其他 Agent 的功能；身份標記只記錄文件夾資料。

## 開源授權

本專案的程式碼、文件及自製圖示採用 [MIT License](../LICENSE)，
Copyright (c) 2026 collieai-g。可以商業使用、修改及再發布，請保留授權及版權聲明。

Python CLI 使用標準函式庫；瀏覽器版沒有第三方執行依賴。
圖示由 `tools/build_icons.py` 的繪圖指令產生，沒有附帶第三方圖示套件。
Pillow 只用於重新產生圖示，沒有隨程式封裝；自行安裝的開發工具依其各自授權使用。
