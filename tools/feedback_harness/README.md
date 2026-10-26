# 共用實測回饋 harness MVP

版本：0.1.0。Python 3.12 標準庫；瀏覽器回歸由目前客戶端的正式 CUA 工具執行。

在 Blueprint 倉庫內，先進入 `tools/feedback_harness` 再執行本頁命令。案例中的 v2.9.0 commit 是歷史問題基線，不代表目前 main 或最新正式版；`BLUEPRINT_SOURCE` 須是該固定基線的獨立乾淨 checkout。未提供時只記錄生命週期參考。此工具隨來源提供，不由原生服務載入或自動啟動。

這是一套小型開發工具：把實際問題整理成可重現案例，執行回歸，讀回私人證據，產生可審閱的上游交接包。它不會自動監察對話、操作工作室、推送 GitHub 或部署服務。

## 三個位置

| 位置 | 內容 |
| --- | --- |
| 本專案 | 共用 runner、通用合約、項目接入設定、合成案例及參考修正 |
| 項目來源 | 項目原有程式、既有測試與生命週期工具；以明確 project root 接入 |
| 倉庫外的私人 state | operation、執行收據、實例對應、瀏覽器截圖及其他私人證據 |

公開包只採用明確 allowlist。私人 state 必須在所有來源／Git checkout 以外；不要將真實工作室資料、登入、路徑、備份或截圖搬進本專案。

## 第一個案例

[`blueprint-login-001`](cases/blueprint-login-001/README.md) 分開記錄兩個問題：

1. 原生 HTTP Basic 登入曾在一次內置瀏覽器安裝中失敗，而 Chrome 成功。這是歷史相容性觀察，不能推論所有客戶端版本都不支援 Basic。
2. 本機新增登入接駁的 `no-referrer` 政策令 HTML 表單 POST 送出 `Origin:null`，被同源保護拒絕。這是可用假資料重現的接駁程式缺陷，參考修正只將登入頁改用 `same-origin`，仍拒絕缺少、null 及外來 Origin。

[`proposals/blueprint_native_login`](proposals/blueprint_native_login/UPSTREAM_HANDOFF.md) 提供通用參考接駁與回歸測試。它沒有改動原版工作台，不會代替上游審查、正式發行或實例更新。

## 執行一次完整回饋流程

先把 `PRIVATE_STATE` 設為倉庫以外的私人目錄，把 `BLUEPRINT_SOURCE` 設為已核對的固定版本 Blueprint 來源。以下變數值由本機提供，範例不保存實例資料。

```powershell
python -X utf8 harness.py plan --adapter adapters/blueprint-studio.json --case cases/blueprint-login-001/case.json --state $env:PRIVATE_STATE --operation blueprint-login-001 --upstream-source $env:BLUEPRINT_SOURCE
python -X utf8 harness.py run --state $env:PRIVATE_STATE --check regression
python -X utf8 harness.py status --state $env:PRIVATE_STATE
```

`plan` 引用該固定來源原有的 `tools/lifecycle_plan.py` 唯讀 `source_release` 計劃與 digest。沒有提供上游來源時只記錄參考，不能聲稱已執行原生流程。harness 沒有另一套備份或部署引擎。

`run` 使用 manifest 明確列出的 argv，以 `shell=False` 執行；綁定程式輸入、命令、runtime 與結果。只保存 return code、輸出長度及 SHA256，不保存子程序輸出正文或環境秘密。相同已通過輸入可以重用；程式、測試、命令或 runtime 改變便失效。中斷留下未知結果時先核對，再明確接續。

時間上限管理 runner 直接建立的檢查程序。會開啟子服務的測試須自行負責正常停止；本例 fixture 有獨立的停止入口，只處理自己建立的伺服器。

原有單元測試不能取代真正瀏覽器提交表單。依[案例說明](cases/blueprint-login-001/README.md)啟動隔離 fixture，再由目前客戶端執行 `browser-check.mjs`，將結果、來源雜湊及截圖保存在私人 state 外置證據目錄。之後交給 runner 讀回：

```powershell
python -X utf8 harness.py attest --state $env:PRIVATE_STATE --kind browser_form_regression --evidence $env:BROWSER_EVIDENCE
python -X utf8 harness.py status --state $env:PRIVATE_STATE
python -X utf8 harness.py bundle --state $env:PRIVATE_STATE --output $env:HANDOFF_ZIP
```

外部瀏覽器證據標示為客戶端陳述。runner 會核對目前 input tree、fixture 來源及檔案 SHA256，但不會因此宣稱自己控制過瀏覽器，亦不會把歷史證據當成新的正式登入驗收。

`bundle` 需要回歸及必要瀏覽器證據都有效。它只打包已審閱通用檔案，附內容清單及 SHA256；未通過、輸入過期、私人路徑或禁止檔案會阻止打包。掃描屬防呆，不能取代公開前的人手內容審查。

## 接入另一個項目

共用核心只維護一份。新增薄的項目 manifest、案例及所需檢查；不把整套 runner 複製進各倉庫。

- 以 `plan --project-root` 指向本次明確選定的項目來源，檢查命令及宣告輸入相對該 root 執行。
- `--adapter`、`--case` 及 `bundle_files` 亦相對該 project root；共用 `harness.py` 仍從原位置執行，不需要複製 engine。另一個項目只保存自己的薄設定、案例及欲分享的通用修正。
- 不需要瀏覽器的案例可使用空 `required_attestations`，不要求 fixture。
- 未接入生命週期執行器的項目使用 `lifecycle.kind: reference-only`，沿用原項目流程；不假稱已部署。
- 在項目自己的 `AGENTS.md` 加入本 harness 指引及 manifest 的入口，保留原有規則。實際私人路徑與權限留在該實例外置 state。
- 日常文件、待辦及工作保存繼續走工作台原有資料操作，不為每次日常任務執行 Git、完整備份或部署。

## 完成狀態

本機回歸、上游交接包、GitHub PR、正式發行及私人實例更新分開記錄。`handoff_ready` 只代表本機候選可供審閱；本 MVP 不執行後三項。

## 驗證共用程式

```powershell
python -X utf8 -m unittest discover -s tests -v
```

全部測試使用合成資料與獨立 loopback ports，不讀公司資料，也不連正在使用的工作室。
