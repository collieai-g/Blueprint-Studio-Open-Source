# V2.4.2：跨環境保存候選來源，保持開源倉庫乾淨

V2.4.1 的接續 ZIP 仍留在原環境，換對話後可能無法取得。新增 `tools/source_checkpoint.py`：把經檢查的來源工作樹保存到另外核實的私人 GitHub 倉庫，再按確切 commit 下載及逐檔核對，才標記來源可接續。保留未提交修改、刪除效果及執行權限；不覆寫原分支或現有工作台。

本開源倉庫只提供通用程式、文件及虛構測試資料。工具拒絕把私人檢查點存入本倉庫、公開或未知可見性的倉庫。聊天、公司資料、附件、登入、平台設定、私人進度及完整備份不包含在 GitHub 來源檢查點內，繼續分開私人保存。

已有工作台使用 [UPDATE_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.2/UPDATE_AI.txt)；新工具可在獨立來源目錄執行，不必先升級私人正式站。[私人來源接續指南](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.2/docs/SOURCE_CHECKPOINT.md) 提供 gh 與 GitHub 外掛路線，以及候選仍只留舊環境時的處理方式。本機 ZIP 明確回報尚未完成跨環境交接。

保留 V2.4 的完整介面、白色淡方格風格及 schema 5。本版修正來源接續，沒有通用 Sites 自動部署、完整 R2 備份或自動載入外掛；來源恢復、正式資料備份、部署及 AI 寫入／讀回仍分開驗收。沒有改動任何私人正式 Site。

驗證：194 個 Python 測試、5 個 Node 測試檔及更新助手的真實 Chromium／HTTP 驗收通過。新增 GitHub 接續測試使用 API 替身及虛構來源，沒有上傳私人工作台。詳見 [驗證紀錄](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.4.2/VALIDATION.md)。
