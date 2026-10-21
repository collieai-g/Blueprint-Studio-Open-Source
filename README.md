# Blueprint Studio Open Source

**藍圖工作室開源｜你公司的專屬後台。**

為管理公司大小事務而設，把每天的工作持續整理成公司的記憶。
說一句「今日收工」，由你接入的 AI 整合成果、決定、參考與待辦，留下有用內容，逐步累積及修整。
目前已提供公司設定、文件整理、資料收藏及每日工作；其他功能可按需要擴充。

[下載工具](https://github.com/collieai-g/Blueprint-Studio-Open-Source/archive/refs/heads/main.zip) · [安裝指引](docs/INSTALL.md) · [更新紀錄](CHANGELOG.md)

## 貼給你的 AI，開始安裝

適用於具備專案、終端機或部署工具權限的 AI Agent，例如 Codex、Claude Code、Gemini CLI。
一般聊天 AI 亦可按同一份指引，帶你逐步操作。

```text
請幫我安裝 Blueprint Studio Open Source，建立我自己的完整原版藍圖工作台。
公開工具：
https://github.com/collieai-g/Blueprint-Studio-Open-Source

先讀 README.md、docs/INSTALL.md 及 AGENTS.md，檢查現有安裝和工具權限，保留已有資料、工作室及工具修改。先讓我選本機或雲端，已有選擇就沿用；再問工作室／公司名稱，行業、簡介及目標可以留空。每次帶我完成目前一步，不要只交付計劃就停止。

本機：跟 docs/INSTALL_LOCAL.md，在我自己的電腦檢查 Python、下載完整程式、準備私人資料與管理員，使用 library 指令啟動原版介面，完成登入及重啟保存驗證。辨認遠端 Codex 與我的電腦，不能把遠端 localhost 當成本機入口。

雲端：跟 docs/INSTALL_CLOUD.md，協助沿用或註冊所需帳戶；直接部署原版可沿用公開來源，需要私人修改時準備完整的私人 GitHub 倉庫，使用 Codex 雲端時協助授權及接入環境。需要我登入、驗證或按 Publish 時，給我確切下一步。環境 Publish／Republish 後繼續正式網站部署，直到有 HTTPS 工作台及持久儲存。按我的選擇使用 Render、Railway、自己的主機或 Sites。Sites 目前需要實際後台適配，公開倉庫未提供一鍵 Sites 部署；不能把 Python Docker 當作可直接部署到 Sites。

完整沿用 library_web/ 和 assets/icons/ 的原版介面、導覽及所有既有功能，不要另生成排版、簡化功能或用臨時示範代替。平台適配以後台、登入和儲存為主。依 docs/INSTALL.md 逐項驗收原版功能，保存一筆工作及待辦並重啟後查回，再交付真實網址、資料位置和下次開啟方法；未驗證部分明確標示。

公司資料及安裝計劃放到程式專案以外的私人位置，由你維護 INSTALL_PROGRESS.md，換對話也能接續。setup 只產生計劃，已有公司資料不可重設。有工具就完成已授權的工作；普通聊天沒有工具時提供當前一步操作及有效公司 JSON 交接。涉及未授權收費先核對費用；密碼和金鑰只在平台秘密設定或本機安全輸入，不貼聊天。

工作台可用後連結我的 AI，實際保存並讀回同一個後台，分別回報工作台及 AI 連接狀態。以後我說『今日收工』，請整理目前可見或我提供的對話，只保存有用成果、已確認決定、參考及未完成待辦，去重及精煉，沿用來源識別；修改使用版本檢查並保留可恢復歷史，不用隨機識別繞過衝突。不保存秘密或整段原始聊天，不聲稱能讀取其他平台不可見對話。目前沒有可用連線時，只輸出可匯入 JSON，不聲稱已儲存。先做好公司設定、文件整理、收藏及每日工作，不增加未要求的公司模組。
```

## 兩條完整安裝路線

先選本機或雲端，再由 AI 逐步帶到原版工作台可用。已有帳戶、環境或網站會接續使用。

| 模式 | 逐步指南 | 安裝結果 |
| --- | --- | --- |
| **本機** | [Windows／macOS／Linux](docs/INSTALL_LOCAL.md)：Python → 完整程式 → 私人資料及登入 → 原版介面 → AI | 在自己的電腦啟動及使用，資料保存在私人目錄 |
| **雲端** | [GitHub／Codex／正式部署](docs/INSTALL_CLOUD.md)：帳戶與程式 → Codex 接入 → 部署選擇 → 原版介面及永久儲存 → AI | 可以登入的 HTTPS 工作台，按需要在 ChatGPT 右側開啟 |

雲端指南包含 Render、Railway、自己的主機及 Sites。原生平台已有部署設定；Sites 需完成後台適配，目前沒有現成的一鍵 Sites 安裝版本。所有路線保留原版介面及全部既有功能。

[安裝總覽與完整驗收](docs/INSTALL.md)亦包含中途換對話的接續方法。公司私人資料獨立保存，正式主機費用以所選平台確認為準。

## 每日一句「今日收工」

接好後，對正在使用的 AI 說：

> 今日收工，幫我整合今日工作，更新公司後台。

AI 會保留有用成果、重要決定、設計／學習參考及下一步；參考網址加入收藏，待辦可以勾選完成。
同一份工作可以合併新內容、修整或移除雜訊，修改歷史及完成進度保留。

AI 整理目前可見或你明確提供的對話；工具不會自行讀取各平台未提供的聊天。
沒有工具權限的 AI 可輸出整理結果，貼回後台預覽、修改及保存。

[每日工作與 AI 連接說明](docs/DAILY_WORK.md)

## 目前可以做甚麼？

- **公司設定**：名稱、行業、簡介與目標，保存在自己的後台。
- **文件整理**：保存原檔、分類、標籤及統一線條圖示。
- **資料收藏**：保存網站、UI 參考、YouTube 與學習筆記。
- **工作管理**：每日紀錄、版本歷史、待辦、星號及快捷搜尋。

![公司後台介面](artifacts/backend-desktop.png)

以上使用示範資料。[手機介面](artifacts/backend-mobile.png) · [公司設定](artifacts/backend-company.png) · [每日工作示例](artifacts/backend-workday.png)。
分類目前使用本機規則；收藏網址未有自動讀取網頁全文或影片字幕。

## 更新工具

在共用工具目錄檢查修改後執行 `git pull --ff-only`，再重新啟動本機服務或重新部署雲端服務。
保留原本資料路徑／雲端磁碟，公司內容可繼續使用。「今日收工」更新的是工作資料。

舊版文件夾與離線資料包工具保留，見[詳細用法](docs/USAGE.md)。
後台儲存、備份及自行部署見[公司後台說明](docs/LIBRARY.md)。

## 開源與參與

目前版本 **1.5.0**。程式碼、文件及自製圖示採用 [MIT 授權](LICENSE)，可以使用、修改及分享。
歡迎提交 [Issues](https://github.com/collieai-g/Blueprint-Studio-Open-Source/issues) 或 Pull Request。
[更新紀錄](CHANGELOG.md) · [驗證紀錄](VALIDATION.md)
