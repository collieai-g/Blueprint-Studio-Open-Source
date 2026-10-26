# 參與開發與回報問題

先讀 [AGENTS.md](AGENTS.md) 及[本機開發與回饋指南](docs/DEVELOPMENT.md)。日常保存資料、修改已有設定及調整排版，使用工作台原有功能；只有通用程式、文件或測試修正才進入本倉庫的貢獻流程。

1. 記下 Blueprint、獨立 Agent 及平台適配器的實際來源版本，先用合成資料重現。
2. 判斷問題歸屬：Blueprint 共用功能在此提出；獨立 Agent 或私人平台適配問題，先到相應來源核對。
3. 在分支完成範圍清楚的修正，執行相關檢查，檢查暫存差異後提出 PR。說明觸發操作、修正後行為、驗證及仍有的限制。

需要可接續的實測回饋時，可選用 [共用 feedback harness](tools/feedback_harness/README.md)。首例涵蓋登入接駁的合成重現、回歸及去敏交接；私人證據留在所有來源倉庫以外，推送及 PR 仍由使用者明確授權。

程式、通用文件與測試可使用 Git；公司資料、附件、Agent 實例設定、工作結果與歷史、憑證、備份及私人進度保存在所有來源倉庫以外。回報時只提供必要的去敏資訊，不上傳完整公司資料或原始聊天。發現秘密外洩或存取控制漏洞時，先避免在公開議題貼出有效憑證、私人內容或可直接存取私人服務的資訊。

純文件改動先核對連結、命令、範本格式及 `git diff --check`，再依 `AGENTS.md`、[驗收規則](docs/DEVELOPMENT_ACCEPTANCE.md)與修改風險選擇必要檢查。程式測試與環境見 [CI](.github/workflows/check.yml)；公開來源貢獻沿用 [source_release 流程](docs/INSTALL_UPDATE_FLOW.md)，不要求建立工作台、備份私人站或部署服務。

[私隱規則](docs/PRIVACY.md)列出可公開內容與提交前核對方式；[分批計劃](docs/DEVELOPMENT_PLAN.md)分清本批文件交付與尚待評估的效能、診斷及本機開發能力。回報問題使用 [Bug 表單](.github/ISSUE_TEMPLATE/bug_report.yml)，功能建議使用 [Feature 表單](.github/ISSUE_TEMPLATE/feature_request.yml)，提出修正沿用 [PR 範本](.github/pull_request_template.md)。

GitHub PR、開源發版與私人服務部署是不同階段。需要安裝或更新時，另按[安裝指南](docs/INSTALL.md)或[更新路線](docs/UPDATE_ROUTE.md)執行，保留已有資料及服務。
