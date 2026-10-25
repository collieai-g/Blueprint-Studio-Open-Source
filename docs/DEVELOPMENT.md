# 本機開發與回饋修正

[返回首頁](../README.md) · [貢獻入口](../CONTRIBUTING.md) · [安裝與更新流程](INSTALL_UPDATE_FLOW.md) · [日常運作方式](OPERATING_MODEL.md)

[驗收規則](DEVELOPMENT_ACCEPTANCE.md) · [私隱規則](PRIVACY.md) · [分批計劃](DEVELOPMENT_PLAN.md)

這份指南說明如何在本機修改 Blueprint 或獨立 Agent，再把可共用的修正交回正確的程式倉庫。先用合成資料重現問題，保留來源版本與測試結果，再提出可審閱的 Pull Request（PR，合併請求）。

閱讀本頁、下載來源或建立 Git 分支都不會建立工作台。只有明確要安裝獨立測試工作台時，才按[本機安裝](INSTALL_LOCAL.md)及[原生生命週期](NATIVE_LIFECYCLE.md)另選服務、port、私人資料與備份位置；保留已有服務。更新原站則按[更新路線](UPDATE_ROUTE.md)，不要因開發測試而重跑初始化。

## 先分清這次要做甚麼

| 目的 | 使用甚麼流程 |
| --- | --- |
| 保存文件、工作、待辦，修改商戶資料、Agent 實例設定或個人排版 | 在原工作台用已有畫面或獲授權工具保存，檢查版本並讀回；不需要 Git 提交 |
| 改寫可重用的 Agent 指引、manifest、程式或測試 | 在該 Agent 的來源目錄開分支；套件新版本與實例設定分開處理 |
| 修正 Blueprint 共用介面、API、權限、備份或接駁合約 | 在 Blueprint 來源開分支，重現問題並執行相關測試 |
| 把乾淨修正交回開源倉庫 | 按 `source_release` 流程準備來源、文件、相關測試與 PR；不連接私人工作台保存進度 |

任務分流及檢查範圍以[共用流程](INSTALL_UPDATE_FLOW.md)為準。本頁不增加另一套安裝、備份或部署要求。普通設定改動不會自動變成程式開發；改動程式也不代表獲准更新任何既有服務。

## Git 在本機記錄程式，GitHub 用來合作

Git 可以在沒有 GitHub 帳戶及網絡的情況下記錄本機版本。GitHub 是託管與合作平台：需要分享來源、提出 PR 或保存遠端程式檢查點時才使用。本機安裝及一般程式編輯不強制建立 GitHub 倉庫。

首次取得來源可依[本機安裝的來源準備](INSTALL_LOCAL.md#l2-準備-python-及完整程式)下載完整 ZIP，或使用 Git。ZIP 可以閱讀與測試，但沒有 Git 歷史；要開分支及提出 PR，另取乾淨的 Git checkout，將已審核的通用修正移入，不覆蓋原目錄。

以下 Git 命令只適用於 Git checkout。已有 checkout 時，先在該程式目錄檢查：

```sh
git status --short
git log -1 --format="%H"
```

第一行列出未提交修改，第二行讀取完整 commit。若已有修改，先確認它們屬於哪項工作；不要用強制重設、覆蓋或刪除來取得乾淨畫面。開始一項獨立修正時，可建立本機分支：

```sh
git switch -c codex/short-description
```

把 `short-description` 換成簡短用途。若分支已存在，先核對其內容再接續，不重建或強制覆寫。

## 程式目錄與私人執行資料分開

以下只是目錄安排示例，不是要求建立這些目錄：

```text
開發位置/
├── blueprint-source/       # Blueprint 程式與通用測試
├── agent-source/           # 獨立 Agent 程式、manifest 與通用測試
└── blueprint-dev-private/  # 獨立測試資料、備份、設定及私人進度
```

私人執行資料放在所有來源倉庫以外，包括私人 GitHub 倉庫。資料庫、原始附件、帳戶與權限對應、Agent 實例設定、訓練內容、工作輸入與結果、執行歷史、完整備份、瀏覽器接續檔及秘密均不提交。Git 的程式修改歷史與工作台的業務歷史是兩回事。

測試使用虛構名稱、文件及訊息。需要示範設定時，只提交不含有效值的範例；不要先提交真實憑證，再打算於下一個 commit 刪除。`.gitignore` 只是防誤加的輔助，不能代替提交前檢查。

本機啟動不會自動取得舊雲端資料。Sites D1／R2 備份、原生 SQLite 資料及平台身份各有格式；搬遷需要另外授權、欄位與引用對應及完整驗證。私人 Sites 的客服卡片或儲存適配，也不能假定可直接複製到原生後台。

## 記下三層來源，再定位問題

在私人開發紀錄保存實際組合；分享時只保留可公開的來源：

| 層 | 應記錄的版本與歸屬 |
| --- | --- |
| Blueprint 宿主 | tag、完整 commit、原生或其他儲存模式；共用介面、資料、權限及維護功能通常在此修正 |
| 獨立 Agent | 自己的倉庫、完整 commit、套件版本及合約版本；專業規則、渠道或該 Agent 的程式在其倉庫修正 |
| 平台適配器（如有） | 自己的來源 commit／版本及支援範圍；平台登入、儲存及專用畫面的問題先在這層核對 |

「開源最新版」、「本機候選」及「正式服務正在運行的版本」分別記錄。若修正同時影響宿主及 Agent，兩個 PR 各自列出相容版本與先後次序，不能用其中一個倉庫測試通過代替另一個。

先找最小重現：使用一份合成輸入，列出操作、預期結果及實際結果。例如某平台適配器的讀取排隊，須先確認是否只在該適配器發生，不能直接當成原生 Python 後台的問題。只貼必要的已去敏錯誤及來源位置，不貼整份公司匯出、聊天或原始平台回應。

## 本機開發 Agent 的現有界線

[Agent 規格 v1](AGENT_CONTRACT.md)及[開發範本](../templates/agent/README.md)已提供宣告、設定、操作及持久工作流程。可以先在本機編輯及執行單元／隔離整合測試；測試夾具中的合成來源只是測試資料，不能當成正式登記證據。

目前正常登記仍要求 GitHub HTTPS 倉庫網址與完整 40 位 commit，不接受本機檔案路徑或浮動的 `main`。後台驗證格式，不替開發者向 GitHub 核實內容；應先核對 manifest 確實來自該 commit。要走現有正式登記流程，可將乾淨套件來源保存到自己的公開或私人 GitHub 倉庫，再固定已核對的提交；公司資料不隨之上傳。未推送的新修改可以繼續本機開發，但不能以虛構遠端版本宣稱已完成固定來源登記。

已有合適來源時，可從 Blueprint 根目錄預檢套件；路徑須換成實際位置：

```sh
python tools/lifecycle_plan.py preflight --manifest "../agent-source/agent.json" --source "../blueprint-dev-private/agent-source.json"
```

`agent-source.json` 使用已核對的 `{repository, revision}`。`preflight` 只驗現有宣告及來源格式，不下載、執行或登記套件，也不證明模型或渠道已連接。現有 `connected_client` 工作由目前 AI 對話處理；任意 Node／Python 程式、WhatsApp 服務及無人值守執行器有自己的執行需求，須依該 Agent 的實際文件另行驗證。

## 在分支修正，只驗受影響的範圍

先讀本倉庫 `AGENTS.md` 及相關模組規範。保留原 ID、歷史、附件、登入、權限及未保存草稿；重現問題後，再做能解決原因的修正。

公開來源貢獻沿用 `source_release`。需要可接續計劃時，將下列 JSON 放在倉庫以外的私人開發紀錄；把已核對的公開來源證據補入 `source`，不要填猜測的 commit：

```json
{
  "contract_version": 1,
  "task": "source_release",
  "target_services": [],
  "protected_services": [],
  "source": {},
  "layout_change": false
}
```

```sh
python tools/lifecycle_plan.py plan --input "../blueprint-dev-private/source-release-plan.json"
```

計劃不執行測試或部署。相關輸入及命令的雜湊、檢查收據與重用規則見[生命週期工具合約](LIFECYCLE_API.md)；已完成且輸入未變的檢查直接沿用，不能手填通過來代替實際結果。

從程式根目錄執行與改動相符的檢查。下表是選擇示例，不是每次全部執行：

| 改動 | 檢查示例 |
| --- | --- |
| 純文件 | 核對相對連結、現有命令／欄位及 `git diff --check`；不因此初始化資料庫或跑部署驗收 |
| 生命週期計劃工具 | `python -m unittest discover -s tests -p "test_lifecycle_plan.py" -v` |
| Agent 合約或工作流程 | `python -m unittest discover -s tests -p "test_agents*.py" -v`，再按受影響入口補 API／MCP 或瀏覽器驗證 |
| 逐頁版面演算法 | `node --test tests/test_page_layout.cjs`，涉及互動再按相關指南做瀏覽器驗證 |

以上 `python` 須使用 Python 3.10+；Windows 可按已有環境改為 `py -3`，macOS／Linux 可用 `python3`。Node 檢查沿用 CI 使用的 Node 24；不要為了開發去覆寫已有系統 runtime。修改 web 來源時依 `AGENTS.md` 執行 `python tools/build_web.py` 並核對產物。完整回歸及執行環境以 [CI 設定](../.github/workflows/check.yml)為準，根據風險選擇，不把文件編輯擴大成私人站更新。

需要驗證實際安裝或資料遷移時，按原指南使用已選定的獨立測試服務。從乾淨的確切 commit 或發行物重現，避免只在有未追蹤檔、舊設定或私人資料的目錄成功。原生、Sites 及其他適配器分別列出已驗與未驗平台；模擬渠道測試不代表已對真實客戶發送。

## 檢查提交，再提出 PR

準備分享前，在來源目錄先看差異：

```sh
git status --short
git diff --check
git diff --stat
git diff
```

逐一檢查新增檔與修改，排除私人資料及無關檔案。只加入本次修正的明確檔案；以下以一份文件為例，請換成實際清單：

```sh
git add -- docs/DEVELOPMENT.md
git diff --cached --stat
git diff --cached
git commit -m "docs: explain local development workflow"
```

本機 commit 尚未傳到 GitHub。要提出 PR，先核對目的倉庫及寫入權限；沒有上游寫入權限時，可在 GitHub 建立自己的 fork。確認 `origin` 是本次預期且有權寫入的程式倉庫後，才推送分支：

```sh
git push --set-upstream origin codex/short-description
```

推送前再次確認目的倉庫可見性及內容範圍。公開修正只含乾淨通用來源；不要將整個私人工作區或公司客製來源推到公開 fork。依 GitHub 顯示的分支建立 PR，選對宿主或 Agent 倉庫，寫明：

- 哪個具體操作出錯，修正後應有甚麼行為。
- 使用的來源版本、最小合成重現及修正範圍。
- 真正執行的相關檢查及結果；未驗的平台或限制另列。
- 若跨倉庫或更改合約，列出配對 PR、相容版本及必要更新次序。

PR 是可審閱的修正，不是發版或部署成功。收到回饋後在同一分支修正，因新變更受影響的檢查才重跑。

## 合併、發版與服務更新分別確認

維護者合併 PR 後，依目前 [CI 發行設定](../.github/workflows/check.yml)處理版本、檢查及發行物，並核對[發行說明](RELEASE.md)。本機工作台不會因 GitHub 推送、PR 合併或上游發版自動更新；獨立 Agent 也有自己的版本及發行流程。

使用者另行要求更新已安裝服務時，才帶著確切版本按原更新流程整合、備份、部署及驗收。公開來源貢獻完成時回報 PR 與實際檢查即可，不為了宣稱完成而建立測試工作台、變更私人資料或部署私人站。
