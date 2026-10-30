# 固定產物與更新渠道發布

[受管安裝器](MANAGED_INSTALLER.md) · [渠道合約](MANAGED_RELEASE_CHANNELS.md) · [開發驗收](DEVELOPMENT_ACCEPTANCE.md)

來源通過、產物通過、網上發布與客戶安裝是四個獨立結果。本文件與工具不建立 GitHub 倉庫、不啟用 Pages，也不更新任何工作室。首次網上發布及真正 HTTPS 下載驗收須在獲授權的發布階段完成。

## 只建立一次，再發布相同產物

1. 完成來源修正及必要回歸，固定乾淨 commit。檢查 `tools/release_files.json`：每個被追蹤檔案必須明確分類；空陣列表示不進產品包。新檔案不會因所在目錄或副檔名而自動獲准發布。
2. 使用最終、固定的 GitHub Releases URL 建立並簽署程式包，例如 `https://github.com/OWNER/Blueprint-Studio-Updates/releases/download/core-vVERSION/core.zip`。網址、大小、SHA-256 與精確轉址主機 `release-assets.githubusercontent.com` 都在簽署描述內。不能在驗收後把本機路徑替換成網上網址。
3. 在隔離合成工作室驗收這份固定 ZIP 及描述，完成相容性、重啟、保留與失敗處理檢查。正式版本亦可先放在 `test`；開發候選／預覽版不能提升成 `stable`。
4. 建立已簽署渠道清單。提升渠道只重新簽署清單，保持所有元件封套及 ZIP 位元組不變；不重建、不覆寫同一版本。
5. 先發布 ZIP，再發布指向它的清單；下載來源未完成時不讓工作室看見新清單。

簽署私鑰、私人安裝身份及完整驗收進度保存在所有來源倉庫之外。公開 Pages／Releases 路線只接受 `audience: ["*"]` 的通用核心清單；即使渠道名為 `test` 或 `stable`，帶指定工作室身份的清單亦會被公開發布工具拒絕，不能放入公開發布倉庫。指定工作室清單保留在本機或私人維護位置，網上交付須先完成有身份驗證的儲存及客戶端能力。`private` 的適用身份不是下載存取控制。

## 本機準備網上交付檔案

以下全部是示例；`OWNER`、版本、身份及位置必須換成已選定的值。所有輸出使用未存在的 Git 外路徑。

```powershell
python tools/prepare_update_publication.py prepare --catalog "D:\Private\test.json" --trusted-keys "D:\Private\trusted-keys.json" --installation-id synthetic-acceptance --artifact "D:\Private\core.zip" --output "D:\Private\publication-vVERSION" --repository OWNER/Blueprint-Studio-Updates --tag core-vVERSION
```

工具驗證渠道及元件簽章、最終 GitHub URL、轉址主機、ZIP 雜湊、檔案清單及真正版本，然後原樣複製到：

| 輸出 | 下一階段用途 |
| --- | --- |
| `release-assets/core.zip` | 作為固定 tag 的 Release asset 上傳，不能覆寫 |
| `pages/channels/test.json` | 在獨立發布倉庫提供固定 HTTPS 清單 |
| `publication.json` | 本機核對收據；`uploaded: false` 明確表示尚未上傳 |

現有主倉庫的 Pages 工作流程發布舊版離線介面，不改作更新清單。獨立發布倉庫可採用 `templates/update-distribution/pages.yml`，將準備輸出的 `pages/` 放在該倉庫同名目錄；此範本本身不會啟用任何服務。

## 網上讀回驗收

Feed 必須是直接回應 JSON 的固定 HTTPS URL，不能依賴重新導向。ZIP 只允許簽署描述列出的 HTTPS 轉址主機。完成獲授權發布後，可執行只讀驗收：

```powershell
python tools/prepare_update_publication.py verify-hosted --feed "https://OWNER.github.io/Blueprint-Studio-Updates/channels/test.json" --trusted-keys "D:\Private\trusted-keys.json" --installation-id synthetic-acceptance --catalog-id CATALOG_SHA256
```

這會讀回相同渠道識別及每份真正 ZIP，沿用安裝器的下載、簽章、解壓及 runtime 預檢；不安裝、不啟動候選，也不寫入工作室。下載失敗保留未驗證狀態。

## 一般安裝包與 CI

[CI](../.github/workflows/check.yml)是必要檢查的正本：Ubuntu／Windows、Python 3.10／3.12、Node 24、合約、瀏覽器、MCP 及容器檢查按工作分組執行。Windows 保留原來系統 locale，UTF-8 由產品入口處理。

一般安裝包和介面包使用同一明確檔案政策。`tools/verify_release_artifacts.py` 會檢查交付 ZIP 的精確清單、內容、版本及合約 SHA256SUMS 與被驗來源一致。一般 main／PR 與手動檢查通過後都會建立及驗收產物；只有明確 tag 或啟用手動發布選項才進入發布工作。產物工作建立並驗收一次，發布工作下載同一份產物，重查收據及雜湊後上傳，最後讀回下載檔再核對；不在發布工作重建。已有版本一律拒絕覆寫。

受管已簽署核心包仍須使用上述專用發布準備及合成驗收流程；一般 source installer ZIP 不會被冒充成受管更新 ZIP。
