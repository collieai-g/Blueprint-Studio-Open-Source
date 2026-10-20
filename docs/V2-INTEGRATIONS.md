# Rust v2 整合依賴與驗收

每項整合分為「程式接口存在」「已配置」「可連接」「端到端驗證」四個狀態。設定 URL 或啟動容器，不等於通過最後兩項。

## 固定依賴

以下映像 tag 在 2026-10-20 以 registry manifest 查到；tag 固定後仍應在正式發行保存 image digest、SBOM 及更新紀錄。完整容器執行／負載能力另行驗證。

| 引擎 | 映像／版本 | 自架用途與來源 |
| --- | --- | --- |
| Rust build | `rust:1.99.1-slim-bookworm` | 編譯藍圖核心 |
| Node build | `node:24.19.0-bookworm-slim` | 編譯 TypeScript 介面 |
| PostgreSQL／PGroonga | `groonga/pgroonga:4.1.0-alpine-17` | [官方版本表](https://github.com/pgroonga/docker)；中英全文搜尋 |
| Runtime／PG client | `postgres:17.11-bookworm` | pg_dump／pg_restore 17 client；本映像不運行 PostgreSQL server |
| SeaweedFS | `chrislusf/seaweedfs:4.48` | [Apache-2.0](https://github.com/seaweedfs/seaweedfs)，S3／版本／Object Lock |
| Gotenberg | `gotenberg/gotenberg:8.38.1` | [MIT](https://github.com/gotenberg/gotenberg)，LibreOffice PDF 轉換 |
| Tika | `apache/tika:3.2.3.0-full`，加繁簡中文字庫 | [Apache-2.0](https://tika.apache.org/)，抽字及 metadata |
| ClamAV | `clamav/clamav:1.5.4` | [GPL-2.0](https://docs.clamav.net/)，ClamD／病毒庫更新 |
| Documenso CE | `ghcr.io/documenso/documenso:v2.21.0` | [AGPL-3.0 CE](https://docs.documenso.com/docs/policies/community-edition)，免費 API／簽署服務 |
| Keycloak | `quay.io/keycloak/keycloak:26.8.1` | [Apache-2.0](https://github.com/keycloak/keycloak)，OIDC／SAML／MFA |
| Collabora CODE | `collabora/code:25.04.7.2.1` | [開源 Office 協作](https://www.collaboraonline.com/code/)，WOPI |
| Caddy | `caddy:2.10.2-alpine` | [Apache-2.0](https://github.com/caddyserver/caddy)，HTTPS reverse proxy |

Tesseract、PDF.js 等實際組件亦須在發行 SBOM 列出。不同語言嘅開源處理引擎是可替換依賴；藍圖業務後端本身使用 Rust。

## 本次實際執行紀錄

2026-10-20 在獨立測試容器及可刪除資料完成：

- PostgreSQL／PGroonga：真實啟動，建立 `pgroonga` extension、獨立 Documenso／Keycloak 資料庫，重啟後仍存在。
- Gotenberg 8.38.1：真實 DOCX 經 LibreOffice 轉 PDF，`pdftotext` 讀到指定內容；PDF stamp 路由回傳 200，產出不同 hash 的 PDF，抽取到原文及浮水印文字。`stampOptions` 的 `points`、`rotation`、`opacity` 須用 JSON 字串。
- SeaweedFS 4.48：使用真實 AWS4 簽署請求建立 Object Lock bucket、啟用版本、上傳及讀回兩個版本；governance retention 及 legal hold 分別令永久刪除回傳 403；解除 hold 成功；服務重啟後 Object Lock 及版本列表仍存在。
- Rust／S3：[實際應用程式測試](../deploy/research/content-acceptance-2026-10-20.json) 通過原檔／舊版／range、真實 version receipt、複製引用的 hold 聯集與解除、provider compliance-retention 刪除拒絕，以及 GC 在最後引用移除後刪除明確版本。GC 測試只在其可刪除資料庫提前寬限及 pin 到期時間，沒有冒稱等待完整 24 小時。
- 本機核心備份／還原：[九項實際驗證](../deploy/research/backup-restore-2026-10-20.json) 通過，包括真實 `pg_dump`／`pg_restore`、兩個原檔版本及 hash、公司／筆記／歷史、私人 ACL 拒絕、master key 解密及 TOTP 登入；拒絕覆寫快照、checksum 不符及還原到非空安裝。
- Metadata 讀取容量：[60 秒實測](V2-PERFORMANCE.md) 使用 4 CPU／16 GiB、10,000 合成帳戶、200 sessions、12,200 metadata rows，40 GET/s 下完成 2,400 請求；排程至完成 p95 78.088 ms，0 HTTP／ACL 錯誤。沒有實際檔案流量、OCR、掃描、Office 或背景工作吞吐測試。

各紀錄只證明其列明場景。Tika full 映像解壓曾因本次 32 GiB 環境磁碟空間耗盡中止；本次未完成繁中 OCR、ClamAV、Documenso、Keycloak、Collabora、Google／Microsoft 真實帳戶端到端測試。Windows 腳本未在 PowerShell 執行。完整發布映像由 GitHub Actions 實際建置，再以獨立 app／worker／PostgreSQL 驗證首次啟動、處理、重啟及備份；以 [PR 的 container job](https://github.com/collieai-g/blueprint-folder-agent/pull/1/checks) 最新結果為準。外部處理引擎負載及包含物件儲存／選用服務的整套恢復未驗證。

應用程式另有真實 PostgreSQL 配合本機 provider mock 的協定測試：OIDC 使用執行時生成的 RSA key、實際 RS256 token、PKCE、公開／內部 issuer 路徑、claims 拒絕及帳戶撤銷；安全回歸涵蓋停用邀請、SCIM 交易回滾、模組關閉及 Trash 權限。Documenso／Google／WOPI 的本機協定測試與真實部署服務、實際帳戶測試分開，不把 mock 通過當作供應商端驗收。

[可攜帶測試結果](../deploy/research/engine-smoke-2026-10-20.json) 保存實際狀態碼及 PDF hash。`scripts/v2/check-s3.mjs` 可在**可刪除的測試 S3** 重跑版本／hold／retention 測試：設定 `BLUEPRINT_ENGINE_TEST_DISPOSABLE=1`、`BLUEPRINT_ENGINE_TEST_S3_ENDPOINT`，以及 `BLUEPRINT_ENGINE_TEST_S3_ACCESS_KEY`／`BLUEPRINT_ENGINE_TEST_S3_SECRET_KEY`，或以 `BLUEPRINT_ENGINE_TEST_S3_CREDENTIALS_FILE` 指定 Seaweed 身份 JSON。它會建立隨機測試 bucket 並留下保留一小時的物件；不應對正式儲存執行。可用 `BLUEPRINT_ENGINE_TEST_RESULT_FILE` 保存結果；重啟測試服務後，以 `node scripts/v2/check-s3.mjs --recheck 結果檔` 驗證保留設定及舊版本。

## 接駁契約

| 接駁 | 設定 | 完整驗證 |
| --- | --- | --- |
| 轉 PDF | `BLUEPRINT_GOTENBERG_URL` | 真實 DOCX／XLSX／PPTX 轉換；壞檔／密碼檔／逾時回報失敗 |
| 抽字／OCR | `BLUEPRINT_TIKA_URL` | 繁中掃描 PDF、Office 原檔 hash 不變；搜尋抽取結果；未識別內容不可亂填 |
| 防毒 | `BLUEPRINT_CLAMAV_URL=tcp://clamav:3310` | EICAR 隔離；引擎失聯保持未掃描／失敗；新版本重新掃描 |
| S3／保存 | S3 endpoint、bucket、region、access／secret key | Object Lock 建 bucket、版本讀取、hold／retention 阻止永久刪除、重啟保持 |
| 簽署 | Documenso URL／API token／webhook secret | 免費 CE API 建 envelope、distribute、簽署、下載 signed PDF 及 audit log；webhook 重送去重 |
| 企業身份 | Keycloak URL、issuer、client及群組配置 | 真實 OIDC／SAML 登入、MFA、停用成員、群組撤銷及 token 撤銷 |
| Office 編輯 | Collabora public URL／WOPI origin | 兩帳戶同時編輯、權限拒絕、版本寫回、重新連線 |
| 日曆 | Google／Microsoft OAuth client | 授權、首次／增量同步、重複事件、刪除、衝突、游標過期及撤銷 |
| AI 員工 | 自己嘅 worker endpoint、憑證、能力及資料範圍 | 真實派工、progress／result callback、去重、逾時／取消及權限限制 |

Documenso v2 使用 envelope API；舊 document API 不能與新接口混用。其 webhook 使用指定 secret header，不能自行假設為 HMAC。藍圖對外自己的 worker webhook 可使用自己的簽署協定。

## 已知能力邊界

- ClamAV 提供真正可用掃描，並不等同 Box 專有多引擎 Reputation／Deep Scan。
- 自簽 PDF 可驗完整性；公共 CA、可信時間戳及特殊身份驗證是額外配置。
- Documenso CE API 與收件人簽署頁可用；免費組合不依賴商業 embedded editor／white-label／enterprise license。
- Collabora 開源版本沒有商業 SLA；版本固定、容量與協作恢復需要自行驗證。
- 網頁無法可靠讀取遠端裝置防毒、磁碟加密及 EDR 狀態。Device Trust／同步／本機開啟需要後續桌面元件。
- 本機 volume 不提供儲存層 WORM；只有通過 object-store retention／hold 測試後才可宣稱該能力已啟用。
- 原生第三方 App 市場不等於藍圖已逐一實作；整合中心提供接口及已驗證嘅連接器。

## 容量驗收基線

初始目標為 10,000 帳戶、200 活躍使用者、20 背景任務。[已執行 metadata 場景](V2-PERFORMANCE.md) 通過其明確門檻；200 sessions 不等於 200 個同時忙碌請求，20 背景任務吞吐亦未因此獲驗證。每次報告列出 CPU／RAM、資料量、索引、服務版本及測試方法；轉檔、OCR、掃描、原檔流量分開記錄。沒有跑過該測試，不可由帳戶數推斷生產 SLA。
