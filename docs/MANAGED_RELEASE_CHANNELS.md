# 受管元件與發行渠道合約

[安裝器操作](MANAGED_INSTALLER.md) · [更新功能計劃](UPDATE_SYSTEM_DEVELOPMENT_PLAN.md)

工作室核心與客服使用各自的版本、不可變套件和已驗證依賴，由同一更新計劃協調。渠道目錄決定哪些發行適用於目前工作室；安裝器仍須驗證每個程式包及實際執行環境。建立或提升目錄只寫出檔案，不上傳、不推送，也不套用任何工作室。

## 元件發行描述

原有 `contract_version: 1` 的 `native-core` 描述繼續有效。客服使用 `contract_version: 2`，共同欄位仍包括固定來源、版本、平台、完整檔案清單、archive 大小／SHA-256 與簽章。

| 欄位 | 客服值及要求 |
| --- | --- |
| `component_id` | `bluesai-cs-0001` |
| `adapter` | `bluesai-node` |
| `version` | 與 `package.json` 及宿主 Agent 宣告一致的版本，允許測試渠道使用預覽版 |
| `source_repository`、`source_revision` | GitHub HTTPS 倉庫與真實完整 commit |
| `entrypoint` | `src/managed-runtime.js` |
| `runtime` | `node_min`、`node_max_exclusive` 的三段版本陣列；安裝器只使用已配置的實際 Node 執行器 |
| `database_schema` | 客服資料格式；與核心資料格式分開，不推定兩者數字相同 |
| `requires` | 必須明列已驗證的 `blueprint-core` 版本，其他相依元件亦列有限的明確版本 |

客服包內的 `managed-component.json` 包含且只包含 `component_id`、`adapter`、`version`、`database_schema`、`agent_manifest`。`agent_manifest` 指向同包內的 `integrations/blueprint/agent.json`；它的身份及版本須與程式包一致。產品根目錄的 `agent.json` 不是宿主登記宣告。

準備時核對已配置 Node 的路徑、實際版本及執行檔雜湊；不從 PATH 猜執行器，不接受 `.cmd` 或套件提供的驗證命令，不在下載內容內執行安裝腳本。發行者打包時則可用可信維護來源既有的私隱閘門檢查公開內容。

## 簽章與發行權限

程式包封套為 `{manifest, key_id, signature}`，渠道封套為 `{catalog, key_id, signature}`。兩者使用排序、UTF-8、無多餘空白的標準化 JSON 作 Ed25519 簽章。SHA-256 核對內容，不代替可信來源驗證。

既有 `key_id: base64公鑰` 配置保持相容。需要分開維護者權限時使用以下形式，示例不含任何私鑰：

```json
{
  "customer-service-publisher": {
    "public_key": "由可信渠道取得的base64公鑰",
    "components": ["bluesai-cs-0001"]
  },
  "channel-publisher": {
    "public_key": "由可信渠道取得的base64公鑰",
    "channels": ["test", "stable"],
    "components": ["blueprint-core", "bluesai-cs-0001"]
  }
}
```

受限元件金鑰沒有明列 `channels` 時不能簽署渠道目錄。每個目錄中的發行仍須由相應元件的可信金鑰簽署；目錄簽章不能替未信任的程式包授權。公開金鑰必須由維護者預先配置，下載包不能自行加入可信金鑰。

## 渠道目錄

已簽章的 `catalog` 必須含以下欄位：

| 欄位 | 行為 |
| --- | --- |
| `contract_version` | `1` |
| `channel` | `test`、`stable` 或 `private`，須與工作室配置相同 |
| `audience` | 工作室安裝身份清單；公開渠道可明確使用 `["*"]`，私人渠道必須指定工作室 |
| `sequence` | 正整數；拒絕倒退，同序號只允許完全相同內容的重讀 |
| `expires_at` | Unix 秒；過期目錄不能用來準備或套用 |
| `releases` | 已簽章的元件發行封套，最多 200 項 |
| `revoked` | 已撤回的完整發行識別碼清單 |

工作室私存已接受的序號及目錄識別，避免舊目錄重播。套用前重新驗證目錄、期限、適用身份及撤回狀態。目錄不包含公司正文、客戶對話或渠道登入；私人渠道的安裝身份和下載存取配置留在私人維護位置。

`stable` 不接受未提交候選或預覽版本。同一元件、同一版本、同一平台不能有兩份不同發行；已發布內容不覆寫，修正後使用新版本。

## 版本依賴與套用次序

`resolve_updates` 只對已安裝或明確選擇的授權元件提出更新，不因目錄多了一個 AI 就自行安裝。新功能需要新版核心時，將核心與模組組成同一計劃，依相依先後套用。缺少版本、未有授權、依賴循環或與其他已安裝模組衝突時，保留原版本並說明需要處理的條件。

每個計劃返回有序的發行清單、`plan_digest` 及完整 `final_versions`。相同版本意圖不因目錄欄位順序而改變。預覽版本按數字識別比較，例如 `preview.10` 晚於 `preview.9`；正式版本晚於同基礎號的預覽版。此計劃只選擇版本，不執行部署或宣稱跨程序可以原子提交。

## 建立客服包與渠道

以下路徑和版本只是示例，請換成實際已驗證的來源、私人位置與相容版本。私鑰和發行輸出在所有來源倉庫外保存。

先在私人檔案保存已驗證的依賴，例如 `{"blueprint-core":["2.10.0"]}`，再執行：

```powershell
python -X utf8 -B tools/package_managed_release.py build-component --project-root "D:\Sources\BlueSAI" --node "D:\Private\runtime\node.exe" --output "D:\Private\bluesai.zip" --manifest-out "D:\Private\bluesai-release.json" --private-key "D:\Private\publisher.key" --key-id publisher-1 --archive-url "https://updates.example.com/bluesai.zip" --requires-json "D:\Private\requires.json" --platform win32
```

正式包要求乾淨固定提交。本機候選可明確加 `--allow-working-tree`，保留真實 HEAD 和實際來源指紋；只在隔離測試使用。正式宿主登記的 manifest 必須與固定 commit 相符，不能以舊 commit 冒充未提交修改。

建立測試目錄：

```powershell
python -X utf8 -B tools/package_release_catalog.py build --channel test --audience "*" --sequence 1 --expires-at 1800000000 --release "D:\Private\bluesai-release.json" --output "D:\Private\test-feed.json" --private-key "D:\Private\publisher.key" --key-id publisher-1 --trusted-keys "D:\Private\trusted-keys.json"
```

範例 `expires-at` 必須改為實際將來時間。發行者完成該固定產物的測試後，將可正式發布的測試目錄提升到正式渠道：

```powershell
python -X utf8 -B tools/package_release_catalog.py promote --source "D:\Private\tested-feed.json" --installation-id studio-example --sequence 2 --expires-at 1800003600 --output "D:\Private\stable-feed.json" --private-key "D:\Private\publisher.key" --key-id publisher-1 --trusted-keys "D:\Private\trusted-keys.json"
```

提升保留相同發行封套、archive 雜湊及適用工作室名單，不重新建置程式，也不把指定客戶擴大成全部工作室。預覽版或開發候選仍會拒絕進入正式渠道；先完成正式版本的來源固定及測試。工具不會把「已建立目錄」記成客戶已安裝。
