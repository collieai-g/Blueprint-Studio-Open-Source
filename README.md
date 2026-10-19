# 藍圖文件夾 Agent

**為你的 AI 程式專案加入身份卡，方便你與 Codex 整理、搜尋及繼續開發。**

每個文件夾可以記錄名稱、用途、標籤，並選用同一風格的圖示。
你可以從空白開始，之後透過對話慢慢補充內容。
Codex 負責理解及開發，這個工具負責保存標記，無需 API 金鑰。

[下載工具](https://github.com/collieai-g/blueprint-folder-agent/archive/refs/heads/main.zip) · [詳細用法](docs/USAGE.md) · [更新紀錄](CHANGELOG.md)

## 安裝：交給 Codex 處理

在你電腦上的 Codex 開啟一個可存放專案的位置，貼上以下整段文字：

```text
請幫我安裝 Blueprint Folder Agent，並建立我專用的「藍圖專員工作室」。
工具網址：https://github.com/collieai-g/blueprint-folder-agent

1. 檢查 Git 及 Python 3.10+ 是否可用；缺少時說明需要準備甚麼。
2. 在本機可寫入的位置，用 git clone 下載工具；已有版本則先檢查來源及狀態，保留現有修改。
3. 閱讀 README.md 及 AGENTS.md，使用 blueprint.py 的 link 指令，
   在工具文件夾旁邊建立「藍圖專員工作室」，連結這份共用工具。
4. 工作室先保持空白，不預設用途、不建立任何專員；已有內容則保留。
5. 驗證工作室的 blueprint-studio.py 能顯示版本及搜尋清單。
6. 告訴我工作室的完整路徑，方便我在 Codex 加入這個專案。

私人專案及指引保存在工作室；通用工具功能在公開工具專案更新。
如果只能操作雲端環境，請說明實際位置，不要聲稱已安裝到我的電腦。
```

完成後，把「藍圖專員工作室」加入 Codex 作為專案。之後可以直接說：

> 幫我建立一個叫「WhatsApp 客服」的專案文件夾，加入客服及開發中標籤。
> 先記錄用途，程式功能之後再逐步開發。

## 你的內容放在哪裏？

```text
你的電腦/
├── blueprint-folder-agent/  ← 可公開分享及更新的工具
└── 藍圖專員工作室/         ← 自己的程式碼、標記及工作指引
```

兩個文件夾分開存放，工作室使用同一份共用工具。私人內容由你自行保存及備份。
工具本身不內置 AI 模型，也不會自動執行或部署其他 Agent。

## 如何更新？

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

目前功能版本：**1.1.0**。每次更新可查看 [更新紀錄](CHANGELOG.md)。

程式碼、文件及自製圖示採用 [MIT 授權](LICENSE)。可以使用、修改及分享，請保留授權及版權聲明。
歡迎透過 [Issues](https://github.com/collieai-g/blueprint-folder-agent/issues) 提出建議，或提交 Pull Request 改善工具。

更多操作見 [詳細使用說明](docs/USAGE.md)，測試資料見 [驗證紀錄](VALIDATION.md)。
