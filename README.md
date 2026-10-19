# Blueprint Studio Backend

**藍圖工作室（後台）｜為創業者而設，可逐步擴充的工作後台。**

整理公司文件、收藏設計參考與學習資料，記下每日成果和下一步。
你可以為自己或公司部署獨立工作室。現階段提供文件整理、資料收藏與每日工作，
之後再逐步加入其他核心功能。

[下載工具](https://github.com/collieai-g/blueprint-folder-agent/archive/refs/heads/main.zip) · [詳細用法](docs/USAGE.md) · [更新紀錄](CHANGELOG.md)

## 公司文件與資料收藏

有登入及資料庫保存的公司後台：

1. **文件整理**：上傳原檔，自動建議分類、標籤及統一圖示，之後可以手動修正。
2. **資料收藏**：保存網站設計、UI 參考及 YouTube 連結，加上筆記並隨時搜尋。
3. **每日工作**：保存 AI 整理好的成果、決定、參考及待辦，隨時查看及勾選完成。

工作台集中顯示最近資料；側欄可切換文件、收藏、YouTube、星號及待整理。
支援卡片／清單、排序與快捷搜尋（`⌘K`／`Ctrl+K`）。
選取資料後按「交給 AI」，即可複製或下載工作交接，交給 Codex、Gemini 或 Claude 使用。
文件夾採用無填色線條圖示，分類符號只保留少量點綴。

![公司後台介面](artifacts/backend-desktop.png)

上圖使用示範資料；新工作室保持空白。[手機介面](artifacts/backend-mobile.png) · [每日工作示例](artifacts/backend-workday.png)。

需要 Python 3.10+，無需額外套件。在下載後的工具目錄執行：

```sh
python3 blueprint.py library-init "../藍圖公司資料" --username admin
python3 blueprint.py library --data "../藍圖公司資料"
```

第一行會讓你設定密碼；第二行開啟本機後台，用剛設定的帳號登入。
公司資料獨立保存，重啟服務後仍可使用。自動分類目前使用本機規則，未連接 AI 模型。
收藏網址會保留你填寫的內容，未有自動讀取網頁全文或影片字幕。

[公司後台使用及雲端部署說明](docs/LIBRARY.md)。
外出使用需要將服務部署到自己的雲端主機；下載工具不會自動產生雲端網址。

## 每日用一句話保存工作

讓 Codex 在本機工作室完成一次連結：

```sh
python3 blueprint.py workday-connect "../藍圖工作室 Blueprint Studio" --data "../藍圖公司資料"
```

在 Codex 開啟這個工作室，完成一天工作後說：

> 整理今日工作，存入藍圖。保留成果、重要決定、值得收藏的資料及未完成待辦，略過閒聊和重複內容。

Codex 會按工作室指引整理目前可見的對話，透過本機工具儲存。
其他 AI 可使用後台「每日工作」的複製指令，產生整理結果，再貼回預覽及保存。
資料庫會去重，已完成的待辦不會因重送而重設。工具不會自行讀取其他平台的聊天。

[每日工作與 Codex 連接說明](docs/DAILY_WORK.md)

## 文件夾工具：交給 Codex 安裝

在你電腦上的 Codex 開啟一個可存放專案的位置，貼上以下整段文字：

```text
請幫我安裝 Blueprint Studio Backend，並建立我專用的「藍圖工作室 Blueprint Studio」。
工具網址：https://github.com/collieai-g/blueprint-folder-agent

1. 檢查 Git 及 Python 3.10+ 是否可用；缺少時說明需要準備甚麼。
2. 在本機可寫入的位置，用 git clone 下載工具；已有版本則先檢查來源及狀態，保留現有修改。
3. 閱讀 README.md 及 AGENTS.md，使用 blueprint.py 的 link 指令，
   在工具文件夾旁邊建立「藍圖工作室 Blueprint Studio」，連結這份共用工具。
4. 工作室先保持空白，不預設用途、不建立任何專員；已有內容則保留。
5. 驗證工作室的 blueprint-studio.py 能顯示版本及搜尋清單。
6. 告訴我工作室的完整路徑，方便我在 Codex 加入這個專案。

私人專案及指引保存在工作室；通用工具功能在公開工具專案更新。
如果只能操作雲端環境，請說明實際位置，不要聲稱已安裝到我的電腦。
```

完成後，把「藍圖工作室 Blueprint Studio」加入 Codex 作為專案。之後可以直接說：

> 幫我建立一個叫「WhatsApp 客服」的專案文件夾，加入客服及開發中標籤。
> 先記錄用途，程式功能之後再逐步開發。

## 你的內容放在哪裏？

```text
你的電腦/
├── blueprint-folder-agent/  ← 可公開分享及更新的工具
└── 藍圖工作室 Blueprint Studio/         ← 自己的程式碼、標記及工作指引
```

兩個文件夾分開存放，工作室使用同一份共用工具。私人內容由你自行保存及備份。
工具本身不內置 AI 模型，也不會自動執行或部署其他 Agent。

## 如何更新？

專案沿用原有 GitHub 連結及指令，現有工作室路徑及公司資料可以繼續使用。

用 Git 下載的版本，在工具文件夾內執行：

```sh
git pull --ff-only
```

更新後，工作室下次執行就使用新版功能；已開啟的服務請重新啟動。
你可以繼續修改工作室內的專案及指引。工具不會自動拉取 GitHub，亦不會跨裝置同步私人內容。
如有本機修改，先讓 Codex 檢查 Git 狀態再更新。ZIP 版本的更新方法見 [詳細用法](docs/USAGE.md)。

## 想先試介面？

下載工具 ZIP，解壓後用瀏覽器開啟 `dist/index.html`，即可建立帶圖示及標記的資料包。

![介面示例](artifacts/desktop.png)

上圖是示範資料，首次使用是空白的。介面內容保存在目前瀏覽器，
透過 ZIP／JSON 與本機文件夾交換；它不會直接修改本機工作室或系統文件夾圖示。

## 開源與參與

目前功能版本：**1.4.0**。每次更新可查看 [更新紀錄](CHANGELOG.md)。

程式碼、文件及自製圖示採用 [MIT 授權](LICENSE)。可以使用、修改及分享，請保留授權及版權聲明。
歡迎透過 [Issues](https://github.com/collieai-g/blueprint-folder-agent/issues) 提出建議，或提交 Pull Request 改善工具。

更多操作見 [詳細使用說明](docs/USAGE.md)，測試資料見 [驗證紀錄](VALIDATION.md)。
