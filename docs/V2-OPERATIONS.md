# Rust v2 安裝、服務接駁與備份

本文件適用於 `rewrite/rust-workspace-v2`。這是全新安裝路線，沒有讀取或重建舊 SQLite／私人工作室資料的步驟。

## 第一次啟動

可以直接使用新嘅 [單一安裝入口及 Codex 連接流程](../INSTALL.md)。在另一個雲端項目建立第一間真實公司，請複製 [第一位使用者交接指示](../FIRST-USER.md)。以下保留逐步部署命令，方便維護及排查。

需要 Docker Engine／Docker Desktop、Compose v2。macOS／Linux 設定腳本使用 Bash 和 OpenSSL；Windows 使用 PowerShell 7。

```sh
git clone https://github.com/collieai-g/blueprint-folder-agent.git
cd blueprint-folder-agent
git switch rewrite/rust-workspace-v2
./scripts/v2/configure.sh ../blueprint-private
./scripts/v2/stack.sh ../blueprint-private basic up -d --build
./scripts/v2/stack.sh ../blueprint-private basic ps
```

Windows：

```powershell
git clone https://github.com/collieai-g/blueprint-folder-agent.git
Set-Location blueprint-folder-agent
git switch rewrite/rust-workspace-v2
./scripts/v2/configure.ps1 -PrivateDirectory ../blueprint-private
./scripts/v2/stack.ps1 -PrivateDirectory ../blueprint-private -Profile basic up -d --build
```

開啟 `http://127.0.0.1:8787`。在自己電腦開啟私人 `.env`，取出 `BLUEPRINT_SETUP_TOKEN`，在首次設定頁輸入公司名稱、管理員姓名、電郵及自己的密碼。不要把 `.env`、密碼、setup token 或 API token 貼到聊天或 Git。

設定腳本只在新目錄建立隨機秘密；已有 `.env` 時會停止，不會重設帳號或金鑰。首次管理員建立完成後，再次設定不能覆寫既有公司；setup token 仍只保存在私人 `.env`。

基本服務包含 Rust app、Rust worker、PostgreSQL／PGroonga。原檔與衍生檔在持久 `company-data` volume；公司內容在 PostgreSQL。私人目錄保存配置、加密金鑰、S3 身份及加密簽署憑證，並不包含所有 Docker volumes。

## 個人工作台版面

登入後預設開啟「工作總覽」。白色格線工作台顯示目前帳戶有權存取、已啟用模組嘅真實文件、待辦、AI 員工登記、日程、工作流程及筆記；卡片可以直接開啟對應內容。左側導覽繼續提供完整模組操作。

選擇「編輯版面」，拖曳卡片右下角吸附格位，或使用闊度／高度選單。把手支援方向鍵，Shift 每次調整兩格，Esc 取消未完成嘅拖曳。亦可向前／向後移動、隱藏卡片、重新加入或還原預設版面；隱藏只影響工作台，沒有刪除業務資料。

版面按帳戶保存在目前瀏覽器 localStorage；沒有跨裝置同步，清除網站資料會移除個人版面。瀏覽器拒絕儲存時會顯示提示。手機採用單欄版面及大小選單，同時保留桌面格數設定。

## 三種安裝組合

| Profile | 額外服務 | 使用方式 |
| --- | --- | --- |
| `basic` | 無 | 文件、公司、資料庫及工作流程基礎；可逐項設定外部服務 |
| `documents` | Gotenberg、Tika／Tesseract、ClamAV | Office 轉 PDF、文字／繁簡中文 OCR、掃描 |
| `enterprise` | 文件組合、SeaweedFS、Documenso CE、Keycloak、Collabora | 自架簽署、物件儲存、企業身份、Office 編輯引擎 |

```sh
./scripts/v2/stack.sh ../blueprint-private documents up -d --build
# 或
./scripts/v2/stack.sh ../blueprint-private enterprise up -d --build
```

組合只代表容器與服務位置已配置。管理員仍需完成對應帳戶、API token、OAuth、網域、郵件及權限設定。端點存在不代表連接已驗證；以介面顯示的實際狀態和功能測試為準。

若基本服務已存在而後來啟用企業組合，初始 PostgreSQL script 不會重新執行。需由資料庫管理員建立 `documenso`／`keycloak` 獨立 role 和 database，使用 `.env` 中相應密碼；不要刪除既有 volume 來觸發初始化。

## 電子簽署

1. 啟動企業組合，在本機開啟 `http://127.0.0.1:3001`，建立自己的 Documenso CE 帳戶。
2. 為郵件設定真實 SMTP，或依 CE 提供方式取得簽署連結；未寄出的郵件不能當作已送達。
3. 在 CE 建立 API token，放入私人 `.env` 的 `BLUEPRINT_DOCUMENSO_API_TOKEN`；重新建立 app／worker。
4. Webhook 共享秘密使用 `BLUEPRINT_DOCUMENSO_WEBHOOK_SECRET`；依後台接口文件設定實際 callback URL。
5. 以測試文件完成建立、送出、簽署、PDF 及證據下載，再列為已驗證。

免費預設採用本機生成的加密 `.p12`。它提供 PDF 完整性簽章，不會自然取得 Adobe 公共信任。可以換成使用者自己的憑證；公共 CA／可信時間戳服務屬可選配置。不要遺失 `.p12`、其密碼及資料庫備份。

簽署請求及追蹤在藍圖介面，收件人使用自己託管的 CE 簽署頁。此組合沒有使用付費 embedded editor／enterprise license 功能。

## 物件儲存與保存政策

企業組合使用內部 `http://seaweed:8333`。S3 管理憑證由設定腳本生成；不公開 S3、filer 或 master 端口。

Seaweed 每個 bucket collection 需要多個 volume slots。預設 `BLUEPRINT_SEAWEED_MAX_VOLUMES=0` 依磁碟計算上限，`BLUEPRINT_SEAWEED_VOLUME_SIZE_MB=1024`；小磁碟反覆建立測試 bucket 可能先耗盡 slots 而出現 `No writable volumes`。可刪除的多 bucket 測試使用過 `64`／`32` 組合；正式配置須依檔案量、bucket 數及實際剩餘磁碟調整，這不是已驗證的生產容量保證。

Object Lock 必須在 bucket 建立時啟用，並驗證版本、retention 及 legal hold 的刪除語義。普通 S3 bucket 或本機檔案目錄不能當作已提供儲存層 WORM。部署者應在使用正式保存政策前，測試保留期間刪除被拒、重啟後保持，以及解除 hold 後仍遵守未到期 retention。

分段上載有效期為 24 小時。worker 每 30 秒批次清理已過期 staging；正在 finalizing 嘅 session 另保留至少 5 分鐘，避免打斷仍在進行嘅儲存提交。有效上載及已完成原檔不在此清理範圍。

永久刪除先移除可見 metadata，再交由 worker 收集原檔／衍生檔；至少保留 24 小時寬限期，並重新檢查現有版本、歷史引用及上傳保護。管理員可用 `GET /api/v1/content/gc` 查看狀態，`POST /api/v1/content/gc` 啟動處理。S3 只刪除有明確 version receipt 的版本，仍遵守 provider hold／retention；舊預覽缺少 receipt 時會阻擋並等待覆核。更改 storage endpoint 或 data directory 後，舊位置的清理記錄保留給操作員處理，不會猜測新位置並刪檔。worker 必須持續運作，才會完成延後收集。

## Google／Microsoft 日曆

在自己管理的 Google Cloud／Microsoft Entra 登記 OAuth application，設定 client ID／secret。Redirect URL 為：

```text
https://你的後台網域/api/v1/public/calendar/oauth/google
https://你的後台網域/api/v1/public/calendar/oauth/microsoft
```

公開部署需使用實際 HTTPS origin。本機測試是否允許 loopback redirect 取決於所登記 application 類型及 provider 規則。每位使用者自行授權自己的日曆；沒有 provider 設定時，內置日曆仍可運行。

## 企業身份及 Office

Keycloak 在本機 `http://127.0.0.1:8081`；管理員密碼在私人 `.env`。建立 `blueprint` realm 及 OIDC client `blueprint`，啟用 standard authorization-code flow、client authentication 及 PKCE S256。有效 redirect URI 為 `<BLUEPRINT_PUBLIC_URL>/api/v1/auth/sso/callback`；將 client secret 放入 `BLUEPRINT_OIDC_CLIENT_SECRET`。公開 issuer 是 `<BLUEPRINT_KEYCLOAK_PUBLIC_URL>/realms/blueprint`，容器內連接則使用 `http://keycloak:8080`，兩者不可混作瀏覽器網址。

先在藍圖建立並啟用對應公司帳戶，並讓身份供應商確認其 email；SSO 不會自行註冊任意外部帳戶。未啟用邀請及已停用帳戶不能藉 SSO 登入。同一 issuer／subject 首次綁定後，不會隨 email 變更切換成其他使用者。群組實際授權以藍圖目前成員設定為準，可透過 SCIM 管理；部署 Keycloak 不會自動建立角色或群組映射。部署容器不等於已有真實 Keycloak／SAML／MFA 完整驗收。

Collabora 在本機 `http://127.0.0.1:9980`；`BLUEPRINT_COLLABORA_WOPI_ORIGIN` 必須匹配 Collabora 實際可連接的藍圖 WOPI origin。正式網站、iframe、反向代理及 WOPI allowlist 必須一致，不能靠 iframe 就稱為完成協同編輯。

## 公開 HTTPS

基本服務只綁定主機 loopback。公開前，將私人 `.env` 的 `BLUEPRINT_PUBLIC_URL` 設為真實 HTTPS origin，`BLUEPRINT_SITE` 設為自己的網域；DNS 指向主機，再啟用 Caddy：

```sh
./scripts/v2/stack.sh ../blueprint-private basic --profile https up -d --build
```

Caddy 只代理藍圖主站。Documenso、Keycloak、Collabora 若需外部存取，應各配置自己的 HTTPS 虛擬主機，更新 public URL 並保留正確 forwarded headers；不要將這些容器的管理端口直接暴露在公網。

## 備份、還原及更新

應同時保存：

- PostgreSQL 全部相關資料庫及角色。
- `company-data` 原檔、版本及預覽；企業模式另外保存 `object-data`。
- 私人 `.env`、master key、簽署 `.p12`、S3 身份及必要 HTTPS／身份服務配置。

`blueprint-backend backup <path>`／`restore <path>` 處理藍圖核心資料；選用外部服務的資料及憑證必須另行備份。正式還原前先在隔離安裝驗證，不要直接覆蓋仍在使用的公司。

容器中的 `/backups` 使用獨立持久 volume，避免非 root 程序無法寫入主機的私人目錄。在維護時段先停止 app／worker，避免資料庫與附件在複製期間改動；以一次性容器建立核心快照，再複製到私人備份目錄。名稱須使用新的空路徑：

```sh
./scripts/v2/stack.sh ../blueprint-private basic stop -t 180 app worker
./scripts/v2/stack.sh ../blueprint-private basic run --rm app backup /backups/core-20261020
./scripts/v2/stack.sh ../blueprint-private basic cp app:/backups/core-20261020 ../blueprint-private/backups/
./scripts/v2/stack.sh ../blueprint-private basic up -d app worker
```

還原時，將快照複製到全新安裝的 app 容器 `/backups/`，停止 app／worker，以 `stack.sh … basic run --rm app restore /backups/快照名稱` 執行一次性還原，然後啟動 app／worker 並驗證。正式資料、master key 及外部服務備份必須屬於同一組快照。

2026-10-20 的[本機核心還原驗證](../deploy/research/backup-restore-2026-10-20.json) 已執行真實 PostgreSQL 17.11 `pg_dump`／`pg_restore`，通過原檔及版本 hash、公司／筆記／歷史、私人及 viewer ACL、加密 MFA 及 TOTP 驗證，並拒絕錯誤 checksum、覆寫快照及非空安裝還原。此紀錄不包含外部 S3、Documenso、Keycloak、Collabora 或所有 Docker volumes 的災難恢復。

更新先備份，再固定新 image／Git 版本及重新建置。不要執行 `docker compose down -v` 作更新或修復步驟；該指令會刪除持久 volumes。不要變更 `BLUEPRINT_MASTER_KEY` 來嘗試修復登入；它影響已保存秘密的解密。

## Managed cloud 建置

`stack.sh` 偵測到 `CODEX_PROXY_CERT` 時，加入 `deploy/compose.proxy-ca.yaml`，以 BuildKit secret 將 CA 交給 npm／Cargo／apt。此 CA 只存在於建置步驟，不會寫入最終映像。一般自行部署不需要此 override。

對外網絡仍受執行環境政策限制；必須讓所選 registry／套件來源及實際使用的 provider 可達，不能以停用 TLS 驗證解決問題。
