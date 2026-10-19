# v1.0 驗證紀錄

驗證日期：2026-10-19。

## 已完成

- Python 3.12：14 個標準函式庫 unittest 全數通過。
- Node 24：7 個資料包測試全數通過。
- Chromium：1440 × 1000 桌面與 390 × 844 手機尺寸互動驗證。
- 首次開啟零卡片；建立文件夾時用途、標籤及內容均可留空。
- 圖示、名稱、標籤、搜尋、文字編輯、二進位檔案及 JSON 備份操作。
- 實際下載 ZIP、刪除卡片、重新載入、匯入 ZIP，文字與二進位內容一致。
- Python CLI → ZIP → 瀏覽器資料包元件 → ZIP → Python 解壓／CRC 校驗。
- 原有 AGENTS.md、身份及未知 metadata 欄位保留；同編號 UI 匯入會另存副本。
- 路徑穿越、損壞 ZIP、重複路徑、原有備份拒絕覆寫。
- 同時建立多個文件夾，父目錄索引不遺失項目。
- 儲存配額失敗時保留舊 localStorage，介面提示尚未保存。
- 手機尺寸無水平溢出；匯入文字以 textContent 顯示，不執行 HTML。

## 驗證邊界

- 單一 HTML 已透過本機 HTTP 服務測試，無外部網絡請求或 CDN 依賴。
  本環境的 Chromium 組織政策禁止 file://，因此未在此環境實測直接開本機 HTML。
- 手機驗證是 Chromium 手機尺寸模擬，並非實體 iPhone／Android 裝置測試。
- 未測試 Safari／Firefox；瀏覽器需要現代 JavaScript、Blob 及 localStorage。
- 壓縮 ZIP 匯入需要 DecompressionStream 的 deflate-raw 支援；程式自行匯出的
  ZIP 使用 store 格式，不需要此功能。
- 瀏覽器工作區限制約 2 MB；資料包元件上限 20 MB／5,000 檔。
- 沒有自動跨裝置同步、內置語言模型、Codex 連線或作業系統圖示設定。
- GitHub Pages workflow 只在手動觸發時部署；本次尚未發佈公開網站。

## 重跑

```sh
python3 -m unittest discover -s tests -v
node --test --test-isolation=none tests/test_packager.cjs
python3 tools/build_web.py
python3 blueprint.py serve --no-open
```
