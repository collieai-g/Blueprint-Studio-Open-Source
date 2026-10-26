# 架構研究筆記

這個目錄保存維護者閱讀外部項目後的比較與取捨，讓參考來源可以追溯。內容與藍圖的產品程式、安裝指南及使用介面分開。

- [FastGPT 架構研究](fastgpt-architecture.md)：服務分工、更新、資料遷移、工具權限及 Agent 執行方式。
- [Codex 專用工作台的產品參考](codex-workbench-reference.md)：從 Zendesk 官方文件借鑑工作物件、上下文與專業流程，支持本版[產品定位與開發指示](../docs/HARNESS_ENGINEERING.md)。
- [Harness engineering 研究](harness-engineering.md)：官方一手來源與藍圖原碼的比較，支持[下一版自動檢查與測試候選](../docs/HARNESS_VALIDATION_NEXT.md)。

研究筆記沒有引入參考項目的程式、圖片、商標或執行依賴，也不代表兩個產品已連接。首次安裝與日後更新使用藍圖自己的入口及原生後台；客服 Agent 的實作範圍以 [藍圖開發路線](../docs/AGENT_ROADMAP.md) 為準。

此目錄保留在開源倉庫供閱讀，`tools/package_installer.py`、`.gitattributes` 與 `.dockerignore` 分別將它排除於安裝包、Git 來源封存及 Docker 建置內容之外。Git clone 仍會取得完整倉庫及研究筆記，但執行藍圖不會載入它們。不得在此保存公司資料、私人對話、部署設定或備份。
