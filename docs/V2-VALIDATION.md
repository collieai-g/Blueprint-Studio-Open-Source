# Rust v2 實際驗證紀錄

版本：`2.0.0-alpha.1`；日期：2026-10-20。以下是在可刪除測試資料完成嘅實際結果，不代表 Box 企業版、所有外部供應商或正式生產環境已驗收。

## 已通過

| 驗證 | 實際結果 |
| --- | --- |
| Rust | 71 項測試通過，包括真實 PostgreSQL HTTP、WOPI、工作流程、權限、SCIM、OIDC 及 adapter 協定測試；沒有略過需要資料庫嘅案例 |
| 發行建置 | `cargo build --release --locked` 通過；TypeScript strict 及 Vite production build 通過 |
| 真實瀏覽器 | 8 個 Playwright 案例通過，直接操作 Rust 後台；桌面／手機、成員／啟用／token、檔案／分享／收藏、17 MiB 中斷續傳、業務資料表、任務、表單、Yjs 協作、批准流程、DOCX、治理及個人工作台版面 |
| 白色格線工作台 | 真實資料摘要、逐張調整大小、滑鼠吸附、鍵盤、取消拖曳、重載保存、平板保留桌面欄寬、次序／隱藏／還原、損壞 localStorage 復原及手機無橫向溢出通過；完整 8 案例用時 34.9 秒，瀏覽器錯誤為零 |
| 本機原檔／S3 | 完整 HTTP 流程、不可變版本、ranges、匿名分享 DLP、write-only PAT、Object Lock／hold／retention、引用保護及真正清理原檔通過 |
| 備份還原 | 真實 `pg_dump`／`pg_restore` 9 組檢查通過；原檔、版本、歷史、身份、ACL、MFA 加密資料恢復，拒絕壞 checksum、覆寫備份及還原到已有公司 |
| 部署配置 | 隨機秘密、私人檔案權限、拒絕重設、簽署憑證、三個 Compose 組合檢查通過 |
| 容量 | 4 CPU／16 GiB、10,000 帳戶、200 sessions、40 metadata GET/s 跑 60 秒，p95 約 78 ms、零錯誤、800 個隔離檢查通過 |

可攜帶證據記錄使用嘅 binary hash、服務版本、方法及限制：

- [內容／儲存實測](../deploy/research/content-acceptance-2026-10-20.json)
- [備份／還原實測](../deploy/research/backup-restore-2026-10-20.json)
- [PGroonga、Gotenberg、Seaweed 引擎實測](../deploy/research/engine-smoke-2026-10-20.json)
- [容量方法及原始彙總](V2-PERFORMANCE.md)
- 白色格線工作台：[桌面](screenshots/workspace-grid-desktop.png)／[手機](screenshots/workspace-grid-mobile.png)／[編輯版面](screenshots/workspace-grid-editing.png)；使用獨立測試公司嘅可刪除設計預覽資料

## 重跑

只使用專用可刪除 PostgreSQL；HTTP 測試會建立隔離資料庫，需要 `CREATEDB`。正式公司不能用作測試環境。

```sh
npm ci --prefix ui
npm run build --prefix ui
cargo fmt --all -- --check
TEST_DATABASE_URL=postgresql://測試用戶:測試密碼@127.0.0.1:5432/測試庫 \
  cargo test --locked --all-targets -- --include-ignored --test-threads=1
cargo build --release --locked
node scripts/v2/check-deploy.mjs
```

內容及備份實測工具在 `tests/v2/`；各工具限定 loopback／可刪除環境並自行清理隔離資料庫。S3 測試仍會留下未到期 retention 物件，不能對正式 bucket 執行。請先讀取工具參數及相應證據範圍。

瀏覽器測試需要已初始化、獨立嘅測試公司、持續運作嘅 app／worker，以及本機 Chromium：

```sh
export BLUEPRINT_E2E_URL=http://127.0.0.1:8787
export BLUEPRINT_E2E_ALLOW_WRITES=1
export BLUEPRINT_E2E_EMAIL=測試管理員電郵
# 由自己嘅私人環境提供 BLUEPRINT_E2E_PASSWORD，不寫入 Git。
npm --prefix ui run test:e2e
```

沒有明確 write opt-in 或測試身份時，瀏覽器測試會略過。可用 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定 Chromium。預設不記錄會包含登入資料及一次性 token 嘅網絡 trace。

## 生產容器 CI

GitHub Actions 的 `container` job 實際建置完整 Dockerfile，再載入映像執行 `scripts/v2/check-container.py`。它使用獨立 PostgreSQL、私人網絡、隨機秘密及持久 volumes，驗證非 root app／worker、編譯網頁、首次設定、原檔上載／下載、真實工作處理、重啟持久化及映像內 `pg_dump` 備份。測試只公開 loopback 端口，最後移除自己建立的測試資源。

[本次 PR 的最新 CI 結果](https://github.com/collieai-g/blueprint-folder-agent/pull/1/checks) 是容器檢查的權威結果；基本 app 容器通過並不代表所有可選企業服務已完成配置及驗收。

## 實測界線

Google／Documenso adapter 及 OIDC 使用可控 HTTP provider，驗證真實請求、簽章、狀態、重送、衝突及失敗處理；沒有連接使用者嘅真實 Google／Microsoft、Documenso 或 Keycloak 帳戶。Collabora WOPI 協定及資料庫鎖定測試通過，沒有宣稱完成真正 Collabora 多人 Office 編輯。

繁中 OCR、ClamAV EICAR、真實收件人簽署、Windows／PowerShell及全套外部服務災難還原尚未完成實測。沒有配置嘅功能回報設定需要；安裝服務亦不等於端到端驗收。

本次 Clippy 執行完成，仍有可整理嘅程式風格提示。容量結果只涵蓋已記錄嘅 metadata 讀取，不能推論上載、OCR、Office、外部 AI、20 個背景工作的吞吐或生產 SLA。桌面同步與裝置信任客戶端屬後續版本。
