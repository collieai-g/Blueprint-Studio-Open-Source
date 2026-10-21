# V2.4.1：減少更新時的重複操作

新增「偏好設定 → 更新助手」：自動讀取原工作台網址與版本、保留瀏覽器排版，提供更新指令、私人接續資料及同站排版還原，沿用 V2.4 的白色淡方格風格。

新增 tools/update_assistant.py：保存 Git 歷史與未提交來源、分開核對生效網絡設定與已載入工具、執行原生資料快照、封裝候選程式與接續進度，並在新環境校驗後還原。已發佈設定未套用、外掛未載入與部分舊工具會分別說明，避免重複要求相同操作。

已有工作台把 [UPDATE_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.1/UPDATE_AI.txt) 交給 AI；舊版沒有更新助手入口亦可開始。完整步驟見 [更新助手指南](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.1/docs/UPDATE_ASSISTANT.md)。

版本號更新至主分支並通過檢查後，GitHub Actions 自動建立正式安裝包及介面包；不改寫已有版本的下載。私人工作台仍須按原平台部署，公開發佈不會自行更新私人網站。

此版不新增資料庫 schema，沿用 schema 5。V2.4.0 以前的升級仍按 [UPGRADE.md](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.1/docs/UPGRADE.md) 完整備份。來源／排版接續包不等於完整正式備份，Sites 仍須原後台適配、資料與附件備份及正式發佈；未提供通用 Sites 自動部署或自動授權。

本版以原生 HTTP 與臨時私人資料驗證，沒有改動你的正式私人網站。詳見 [驗證紀錄](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.1/VALIDATION.md)。
