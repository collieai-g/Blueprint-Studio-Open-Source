# 共用安裝更新計劃與檢查去重

`tools/lifecycle_plan.py` 提供唯讀 `contract_version: 1` 計劃。CLI 與擁有人工作紀錄共用 `blueprint_library/lifecycle.py` 的任務、階段及核對定義；API 使用 `change_type`／`sources`，分別對應計劃的 `task`／`source`。工具不會執行套件、測試、資料寫入或部署，亦不會代替正式備份及驗收。私人計劃及檢查收據留在來源倉庫以外；公開來源只提供通用合約及合成測試。

先按本次任務選 `task`：

| task | 必要核對 | 部署及備份 |
| --- | --- | --- |
| `source_release` | 來源、受影響測試、發行物 | 不讀寫私人站，不作私人備份或部署 |
| `daily_data` | 權限、版本、同 ID 讀回 | 不作 Git 提交或部署 |
| `compatible_package` | 固定來源、既有套件合約、登記讀回 | 按原保留政策沿用保護，不部署宿主 |
| `ui` | UI、資產、可及性、舊資料唯讀核對 | 只部署 `target_services`，不固定重做持久層重啟 |
| `persistence` | 權限、CAS、重試、回退、一次 AI 閉環、重啟 | 當前資料保護及受影響服務 |
| `schema_identity` | 當前一致備份、私人副本、遷移、還原、舊資料及附件 | 核心保護不可省略 |
| `fresh_install` | 獨立服務、儲存、備份還原、重啟、AI 及 UI 驗收 | 保護列於 `protected_services` 的舊站，毋須先備份舊站 |

所有計劃使用 `discover → plan → backup_gate → apply → publish_if_needed → verify → complete`，需要本人操作時記錄 `waiting_user`。不適用的階段記錄跳過原因，不能假稱做過部署。`plan_digest` 不含時間或自身提交；已固定的目標來源、範圍或政策改變時，使用新計劃。執行中取得的產物雜湊、平台 saved version 及驗收結果，補入原工作紀錄的 `sources`／`evidence`，不因此重設 digest 或重新發佈。真正執行進度、CAS 及重試沿用擁有人維護 API 的工作紀錄。

以下全部是合成例子；由 AI 用實際來源證據及私人檔案替換：

```json
{"contract_version":1,"task":"ui","target_services":["native"],"layout_change":true,"source":{"upstream_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","adapter_version":"1.0.0","private_commit":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","platform_commit":"cccccccccccccccccccccccccccccccccccccccc","product_version":"2.8.1","saved_version":"opaque-platform-version"}}
```

```sh
python tools/lifecycle_plan.py plan --input /private/update/plan-input.json
python tools/lifecycle_plan.py inputs --project /private/source --file library_web/update.js --file tests/test_update.cjs
python tools/lifecycle_plan.py preflight --manifest /private/package/agent.json --source /private/package/source.json
```

`layout_change` 只在涉及安裝排版、首次保護自訂內容或跨環境接續時要求偏好雜湊及必要基準；來源發行和日常資料任務不因此取得瀏覽器正文。套件預檢重用 `blueprint-agent/1` 的完整宣告驗證，`source.json` 為 `{repository, revision}`，revision 必須是完整 commit。通過只代表相容宣告，未下載或執行套件，亦未登記或接通模型。

檢查先凍結明確的相關檔案清單，`inputs` 按路徑、內容、執行位及刪除計算 `input_tree`。測試分工各自完成後主線才做正式 build／回歸；只停止自己建立的 PID／session。清單須涵蓋測試程式、相依模組、設定及實際工具版本等會影響結果的輸入，不能只列改動檔案而漏掉相依項。

檢查收據記錄 `contract_version: 1`、實際 `outcome: passed` 及 `validation_key`。key 由 `validation_key(input_tree, commands, artifacts)` 計算，`commands` 使用實際 argv 陣列，`artifacts` 是產物名稱至 SHA256 的對應；只在相關輸入、命令及產物全部未變時重用。工具不執行或證明這項檢查，操作者仍須保存真實結果。輸入 JSON 為 `{input_tree, commands, artifacts}`，可用以下命令比較；輸出永遠保留 `production_status: not_verified`：

```sh
python tools/lifecycle_plan.py reuse --receipt /private/update/check-receipt.json --input /private/update/check-input.json
```

公開 upstream commit、adapter 版本、私人 commit、平台 commit、產物 SHA256、平台 saved version 與產品版本各自保存，不互相代替。提交雜湊不要寫回同一提交的自身來源檔；GitHub 來源發行不會部署私人工作台。Sites 打包及發佈仍須沿用目前官方 source helper、精確已推送 commit 和版本規則。
