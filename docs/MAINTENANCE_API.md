# 安裝更新與備份合約

原生受管理後台及 Sites-to-native 接駁層支援 `blueprint-maintenance/2`。這是加法擴充，沒有變更公司 schema 7 或共用資料工具。所有 `/api/maintenance` 端點沿用原身份驗證，只有 `owner` 可用；寫入仍需 `Content-Type: application/json`、`X-Blueprint-Request: 1` 及同源限制。一般成員與管理員不能取得完整備份。

瀏覽器可帶 `X-Blueprint-Account: encodeURIComponent(已登入用戶名)` 固定目前分頁的預期帳戶；實際登入變更時為 409，這個標頭只作防誤寫檢查，不能代替身份。正常 `/api/info` 只提供輕量摘要，`verification_current=false`；完整 coverage 及可重用判定須由明確的維護查詢取得。

舊客戶端 `POST /api/maintenance/backup {}` 保持原有行為。舊備份沒有 coverage 或私人副本收據時，不能直接列為可重用。其他 Sites 儲存適配器須自行實作本合約，不能只換 UI 就聲稱具備原生備份能力。

## 同一備份只建立一次

```json
{
  "contract_version": 2,
  "operation_id": "synthetic-upgrade-backup-before",
  "plan_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

以上為合成參數，真正 `plan_digest` 應使用本次已固定計劃的 SHA256。`operation_id` 是 1–100 位英數、點、底線或連字號，首位必須英數；digest 為 64 位小寫十六進位。兩者必須同時提供。

首次 `POST /api/maintenance/backup` 先持久記錄備份 ID，再排入備份工作。相同 operation/digest 重試回傳同 ID；不同 digest 為 409。結果不明先 `GET /api/maintenance?operation_id=原工作識別碼` 取得 `backup_request`，核對同一備份 ID，不能改 ID 再造一份。查詢不存在的 operation 為 404。中斷後無法證明完成時回報 `unknown`，需要核對現存產物，不自動重新打包。202 只代表已接受。

`GET /api/maintenance` 回報目前一致 coverage、最近備份與 `protection`：

| 狀態 | 下一個缺項 |
| --- | --- |
| `pending` | 建立並驗證完整平台備份 |
| `platform_verified` | 目前 coverage 或保留期限不符，更新相應保護 |
| `private_copy_pending` | 平台備份與隔離還原已驗證，仍需私人副本讀回 |
| `reusable` | 目前資料、服務範圍、schema、完整性、還原、帳戶收據及保留期限均符合 |

coverage 在一致資料讀取下涵蓋完整資料列、業務歷史、身份與權限、schema、附件清單及實際內容 SHA256。備份時間、隔離演練副本、安裝更新工作收據不計入。正文或附件改變，即使數量相同也失效。部署或遷移前重新讀取；這個狀態不是部署鎖，也不代表未來不會有其他寫入。

## 取得及核對同一份私人檔案

1. `GET /api/maintenance/backups/:id/metadata` 回傳現有 ZIP 的 ID、大小、SHA256、coverage、還原證據與受保護同源 `download_path`。
2. 使用同一登入的 `GET` 或 `HEAD /api/maintenance/backups/:id`；沒有公開網址、token 或身份繞過。瀏覽器先監聽下載事件，再按一次連結。結果不明先核對同 ID 的既有檔案，只補缺失下載。
3. 客戶端實際讀回私人檔案，核對大小與 SHA256 後，`POST /api/maintenance/backups/:id/private-copy`：

```json
{
  "file_reference": "private-backup-before.zip",
  "size": 12345,
  "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "readback_verified": true
}
```

以上大小與雜湊是合成例子，正式請求必須與該 ID 實際檔案相符。`file_reference` 是私人的檔案標籤或引用，不要填帳密、token 或公開連結。後台核對對應 ZIP 的大小與雜湊，並按已驗證帳戶保存 `client_attestation`；它不能直接查看客戶端檔案系統，也不證明異地耐久儲存。另一個登入帳戶看不到這份收據。平台備份完整、隔離還原成功、客戶端私人讀回是三份不同證據。

## 可接續工作紀錄

`GET /api/maintenance/operations` 列出目前擁有人帳戶的紀錄；加 `?id=工作識別碼` 或使用 `/operations/:id` 讀回一筆。`POST /api/maintenance/operations` 建立或更新：

```json
{
  "contract_version": 1,
  "operation_id": "synthetic-source-release",
  "plan_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "request_id": "discover-1",
  "expected_version": 0,
  "changes": {
    "stage": "discover",
    "change_type": "source_release",
    "target_services": [],
    "protected_services": [],
    "layout_change": false,
    "sources": {},
    "evidence": {},
    "next_step": "核對來源及受影響測試。"
  }
}
```

紀錄位於備份私人目錄的 `operations.json`，不進公司資料 coverage，也不進來源倉庫。建立使用 `expected_version=0`；之後使用讀回的版本。相同 `request_id` 及完全相同請求先回傳原結果，即使目前版本已前進亦不重做。重用 ID 配不同請求、不同計劃或過期版本均為 409。完成紀錄不可重新開啟。每筆最多 128 個請求，每份工作證據最多 32 KiB；整個私人紀錄最多 16 MiB，超出時停止而不覆寫。

階段依次為 `discover → plan → backup_gate → apply → publish_if_needed → verify → complete`。本次不需要的階段記錄 `not_required` 證據後前進，不能省略紀錄而跳階段。`waiting_user` 保存 `resume_stage`；`next_step` 必須寫出一個本人動作，完成後接回原階段或下一階段。來源發行不指定私人部署服務，亦不要求私人資料備份。新安裝用獨立目標及 `protected_services` 保存不可變更的舊服務。

`change_type` 沿用 [共用流程](INSTALL_UPDATE_FLOW.md) 的 `source_release`、`daily_data`、`compatible_package`、`ui`、`persistence`、`schema_identity`、`fresh_install`。工具根據同一個流程定義產生 `verification_plan`；它只是應驗清單，沒有執行部署或宣稱驗收完成。工作類型、目標服務、受保護服務與排版範圍固定後不可覆寫。

`sources` 使用共用計劃的 `source` 欄位：`upstream_commit`、`adapter_version`、`private_commit`、`platform_commit`、`artifact_sha256`、`saved_version`、`product_version`。commit 使用完整 40 位雜湊。平台序號另放選取的 `evidence`，不當作產品版本。證據只保存受限的 JSON 事實、引用及結果，拒絕明顯憑證欄位；操作者仍須檢查正文，不能把完整平台回應、公司內容或秘密搬進紀錄。

## MCP 對應

原生及 gateway 共有 42 個共用工具，加 6 個維護工具，共 48 個。實際載入數目仍以當前對話為準，數目不是成功寫入證據。

| 工具 | 用途 |
| --- | --- |
| `blueprint_backup_status` | 讀取 coverage、備份與目前帳戶收據；可加 `operation_id` 讀回同一備份請求 |
| `blueprint_backup_create` | 舊空參數或 v2 同 ID 建立 |
| `blueprint_backup_artifact` | 取得既有備份 metadata 及受保護下載路徑 |
| `blueprint_backup_private_copy` | 保存已讀回檔案的客戶端聲明 |
| `blueprint_operation_read` | 按 ID 讀回或列出目前帳戶工作 |
| `blueprint_operation_write` | `changes_json` 物件文字轉成上述 HTTP `changes`，沿用 CAS 及重試語義 |

讀取工具可出現在 read-only 連接；建立、收據及工作寫入不提供。工具不把私人 ZIP 內容回傳聊天、不下載至未知位置、不執行部署。只有瀏覽器下載能力時，沿用一次受保護同源下載並核對檔案。
