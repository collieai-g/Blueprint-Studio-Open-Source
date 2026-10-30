# 原生工作室受管安裝器

## 本批來源交付與保留合約

正式可用範圍以每個 adapter 的固定產物驗收為準；本批首先交付原生核心、相同 schema
的更新保護。下文 BlueSAI adapter 是已有來源能力，必須配對其獨立來源及環境完成驗收，
不能只因清單或程式存在，就宣稱客服、網站平台或真實渠道已部署。

首次 Windows x64 CPython 安裝沿用 `tools/studio_installer.py init`，先解析參數，
在 `<程式目錄>-python` 建立專用環境，按 `tools/runtime_lock.json` 的版本與 wheel
SHA-256 準備直接和間接依賴，再核對簽章及時區能力，最後才詢問密碼及初始化資料。
目前鎖檔涵蓋 Python 3.10–3.14；其他平台／版本沿用已準備的獨立環境路線。
失敗時保留有身份標記的環境供重試，不初始化工作室；不改系統 Python、不安裝編譯工具。
已受管的工作室保留原執行器與 prefix，不能藉重試更換其環境。啟動政策維持明確 `run`，
未新增 Windows 服務或登入自啟。

已發布 ZIP 及元件簽署描述不可覆寫。正式候選先包含最終固定 HTTPS 下載網址，再測試該
產物；提升 `test` 至 `stable` 只重簽渠道清單，不改 ZIP、來源 pin、雜湊或元件封套。
本機路徑的開發候選不能直接改名成正式發行。網上發布來源另按發布流程設定，
本批來源修改沒有建立更新網站或更新任何既有工作室。

更新準備及套用前核對現有程式清單；若被客製修改，保留原程式並阻擋套用，先整合來源。
公司資料、附件、權限、普通設定及帳戶版面留在原私人資料域；不以官方預設覆蓋自訂值。
未保存／待同步內容或版面衝突須先處理；既有可恢復草稿依原帳戶恢復，不能跨帳戶套用。
不能安全恢復的表單內容及未上傳檔案必須先保存。將來經批准改版可提供版本化遷移，
這份保留合約不凍結舊按鈕或幾何位置。

[更新功能計劃](UPDATE_SYSTEM_DEVELOPMENT_PLAN.md) · [元件與發行渠道](MANAGED_RELEASE_CHANNELS.md) · [原生生命週期](NATIVE_LIFECYCLE.md) · [更新助手](UPDATE_ASSISTANT.md)

受管安裝器讓每個工作室使用獨立、固定且經簽章驗證的程式目錄。v2 以已簽章渠道選擇核心與已接入客服的相容版本，在背景準備完整計劃；擁有人從左下角選擇套用後，協調受影響元件暫停、排空、保護資料及切換。一般原生啟動方式及原更新助手仍可使用。本輪只修改公開來源與合成測試，沒有部署現有工作室。

## 已支援的範圍

- 新工作室安裝，或明確接入已停止、schema 相符的既有工作室；保留登入及資料。
- 每工作室獨立的程式、資料、備份、私人恢復副本及操作紀錄，可共用主機。
- Ed25519 簽章、固定 commit／候選來源指紋、ZIP 完整清單與 SHA-256、平台及現有 Python 檢查。
- 本機簽章包及 HTTPS 下載、背景準備、帳戶權限、重複套用去重及版本衝突檢查；完整依賴選擇與按依賴次序套用。
- `test`／`stable`／`private` 已簽章渠道、工作室適用身份、序號、到期及撤回檢查，以及元件／渠道簽章權限。
- 原生核心及 BlueSAI 客服核心各自的相同 schema 更新、專用啟動握手及只停止自己擁有的服務。
- 同 Agent ID 登記及實際客服程序分別換版、讀回；只更新客服時保留核心程序。
- 核心沿用原生備份／還原／私人副本收據去重；客服資料域正常停止後離線備份、SQLite 完整性及隔離還原核對。
- 左下角狀態、套用／稍後及手動重新載入。此頁曾編輯內容時，先保存並重新開啟，再套用；也要保存並關閉其他工作分頁。

目前套用 adapter 為 `native-core` 與指定元件 `bluesai-cs-0001` 的 `bluesai-node`，只支援 loopback 原生服務。核心目前為 schema 8，客服為 schema 7；相同 schema 是各元件升級前後須相同，不是要求兩個資料域使用同一數字。跨 schema 遷移、通用客製模組、雲端平台與 Sites 自動套用尚未支援；Sites 不能使用本原生安裝路線。

安裝器使用首次接入時固定的 Python 執行環境，之後的控制工具亦須使用相同執行器。客服由操作者明確提供 Node `>=24.22.0 <25` 的執行檔；準備時核對路徑、實際版本及雜湊，不從 PATH 猜測或由發行包提供命令。兩者均不自動下載 runtime 或改寫系統環境。原生核心業務 API 仍為標準庫；簽章驗證額外需要獨立環境內的 `requirements-installer.txt`，其中亦為 Windows 固定 IANA 時區資料依賴。準備時驗證現有環境可讀取時區，不把只有來源 ZIP 的狀態稱為更新就緒。

核心更新會暫停已綁定客服的宿主 Agent 新工作及其 runtime；只有未完成宿主工作處理妥當後才換版。客服獨立更新只暫停該 Agent 入口與客服程序。其他未接入暫停協定的已連接 Agent、未完成工作或有效整合會阻擋核心更新，不藉此停用它們或改變授權。

獨立 WhatsApp 橋接不在本 adapter 的更新範圍。只有已明確配置且本身有持久 callback outbox 的橋接，才可用 `retry_503` 在核心暫停期間保留事件並重試；其他來源使用 `manual_pause`，須先安排暫停，不能承諾零停機或已保存尚未收到的來訊。更新不啟用模型、不執行推理或測試發送，也不自動恢復須重新確認的 grant。

## 發行者建立固定套件

以下路徑是示例，換成實際選定且位於所有來源倉庫以外的私人位置。每份輸出使用新檔名；工具不覆寫已有金鑰或發行物。正式發布先在乾淨固定提交完成測試，簽章包須與被測產物一致。

在獨立 Python 環境安裝簽章依賴：

```powershell
python -m pip install -r requirements-installer.txt
```

只有發行者建立簽章私鑰，客戶不需要取得私鑰：

```powershell
python -X utf8 -B tools/package_managed_release.py key-gen --key-id publisher-1 --private-output "D:\Blueprint-Private\publisher.key" --public-output "D:\Blueprint-Private\trusted-keys.json"
```

建立原生工作室包及已簽章發行描述：

```powershell
python -X utf8 -B tools/package_managed_release.py build --output "D:\Blueprint-Private\core.zip" --manifest-out "D:\Blueprint-Private\release.json" --private-key "D:\Blueprint-Private\publisher.key" --key-id publisher-1 --archive-url "https://updates.example.com/core.zip" --platform win32 --notes "工作室功能改善"
```

本機候選可明確加上 `--allow-working-tree`，並把 `--archive-url` 設為該私人 ZIP 的絕對路徑。候選記錄真實 HEAD 和實際產物來源指紋，不冒充該 commit 已包含未提交工作。候選來源僅包含允許的產品路徑與已知安裝器新增檔，不將任意未追蹤檔案收入套件。

直接下載位置使用 HTTPS。若發行主機會重新導向，須以 `--redirect-host` 明確列出並簽署允許的最終主機，例如 GitHub Release 的 `release-assets.githubusercontent.com`；每一跳仍須 HTTPS 且符合精確主機清單。缺少這項宣告就拒絕重新導向。

金鑰輪換採明確更新各工作室的可信公鑰配置；不接受由下載包自行附上的公鑰取代原信任。簽章工具不會自動推送、發版或上傳金鑰。

客服包由已核對的獨立 BlueSAI 維護來源建立。先在私人 `requires.json` 列出已驗證的完整版本，例如 `{"blueprint-core":["2.10.0"]}`：

```powershell
python -X utf8 -B tools/package_managed_release.py build-component --project-root "D:\Sources\BlueSAI" --node "D:\Blueprint-Private\runtimes\node.exe" --output "D:\Blueprint-Private\bluesai.zip" --manifest-out "D:\Blueprint-Private\bluesai-release.json" --private-key "D:\Blueprint-Private\publisher.key" --key-id publisher-1 --archive-url "https://updates.example.com/bluesai.zip" --requires-json "D:\Blueprint-Private\requires.json" --platform win32
```

這會核對 `managed-component.json`、套件及宿主 manifest 身份／版本，並在發行者的可信來源執行既有公開內容私隱閘門。客戶準備下載包時不執行包內驗證腳本。建立／提升已簽章渠道的命令見[渠道合約](MANAGED_RELEASE_CHANNELS.md#建立客服包與渠道)；工具只建立檔案，不上傳或發布。

## 擁有人安裝或接入工作室

先核對發行者提供的可信公鑰，再選擇互不包含的安裝、資料、備份及恢復副本目錄。使用持久私人位置，保護目錄存取權。安裝器拒絕以來源倉庫或已有的非空白資料冒充新安裝。

```powershell
python -X utf8 -B tools/studio_installer.py init --root "D:\Blueprint-Private\studio-a-program" --release "D:\Blueprint-Private\release.json" --trusted-keys "D:\Blueprint-Private\trusted-keys.json" --data "D:\Blueprint-Private\studio-a-data" --backups "D:\Blueprint-Private\studio-a-backups" --recovery-dir "D:\Blueprint-Private\studio-a-recovery" --username owner --port 8787 --feed "https://updates.example.com/stable-feed.json" --channel stable
```

新安裝透過本機隱藏輸入設定密碼，密碼不放參數或輸出。接入原工作室時先停止原服務，使用原資料目錄並加 `--existing-data`；不重新建立帳號或換資料庫。接入的第一份程式包及實際資料 schema 須相符，較舊資料先走既有升級與保護路線。

如需沿用原生備份位置，另明確加上 `--existing-backups`，且必須同時使用 `--existing-data`。安裝器要求原 `installation.json` 的資料路徑、service ID、schema 及完成狀態一致，拒絕未知／未完成備份、遷移或損壞收據；不改寫原生身份或歷史。普通新安裝仍要求空白備份及恢復位置，不能把任意非空資料夾當成已驗證備份。

開發預覽使用[專用接駁入口](DEVELOPMENT.md#在同一預覽試用安裝器)：`tools/dev_preview.py --resume ... --enable-installer ... --channel test`。它保留原預覽身份和資料，並在後續同一 `--resume` 接續固定候選。更新狀態 API 在真正套件程序內核對自己安裝目錄；僅允許其實際 `<program>/releases/<64hex>` 的那一個 program 容器，其他資料／備份／恢復位置仍不得包含來源。預覽使用動態埠及「藍圖工作室開源開發預覽」名稱；網址以當次真實程序讀回為準。

開發候選及預覽版使用 `--channel test` 與對應測試目錄，不能放入 `stable`。`status` 可在本機讀回 `installation_id`，供私人渠道的 `audience` 綁定使用。

接入客服時，先確認宿主已有的原 Agent ID、獨立服務埠和私人核心資料。停止監督程序後，以私人設定檔綁定；`module-add` 不建立第二張卡片：

```powershell
python -X utf8 -B tools/studio_installer.py module-add --root "D:\Blueprint-Private\studio-a-program" --release "D:\Blueprint-Private\bluesai-release.json" --node "D:\Blueprint-Private\runtimes\node.exe" --config "D:\Blueprint-Private\studio-a-bluesai.json"
```

設定的必要欄位為 `contract_version:1`、`component_id:"bluesai-cs-0001"`、原 `expected_agent_id`、獨立絕對 `data_dir`、`host:"127.0.0.1"` 及 `port`。新的空白核心目錄才使用 `initialize:true`；首次接入已有客服資料使用 `existing_data:true`、`initialize:false`，保留原資料及 `admin-token`。`ingress_policy` 明確選 `manual_pause` 或經核對的 `retry_503`。模型／渠道秘密由此資料域內的私人秘密檔提供，不放參數或發行包；完整可用欄位依 BlueSAI 來源的 `docs/MANAGED_UPDATE.md`。安裝器保存每次啟動的固定設定副本；已開始初始化後資料庫或憑證缺失便停止，不重建空白服務。

啟動監督程序：

```powershell
python -X utf8 -B tools/studio_installer.py run --root "D:\Blueprint-Private\studio-a-program"
```

程序啟動固定的 `blueprint.py library` 並保持監督；已綁定客服以 `src/managed-runtime.js --config` 獨立啟動，必定先暫停。核對原 Agent ID、同 ID 套件升級與獨立狀態讀回後才恢復入口，並核對真實程序版本／schema。關閉監督程序時只要求本身擁有的工作室與客服安全停止；不按程序名稱批量中止其他工作室。長期運行時由已選主機的受支援服務管理方式啟動此命令；此工具不註冊系統服務、不購買主機或改變其他服務。

既有未接入安裝器的工作室會回報 `supported: false`，繼續使用原更新助手，不顯示虛假的一鍵更新能力。

## 發行目錄與更新操作

`--feed` 可指定 HTTPS JSON 或明確選定的本機 JSON。v2 的渠道目錄使用已簽章 `{catalog,key_id,signature}`，內含各元件自己的已簽章發行描述。只對已安裝／已明確綁定元件解出相容更新，不因目錄新增另一個 AI 便自行安裝。依賴是已驗證的明確版本清單；缺失、循環、未授權或與已安裝元件衝突時拒絕準備，不以最高版本號強行組合。完整計劃有固定摘要、最終版本組合與依賴先後次序。

渠道支援 `test`、`stable` 及 `private`。可信金鑰可限制元件與渠道；目錄另核對 `audience`、遞增 `sequence`、未過期 `expires_at` 與 `revoked` 清單。私人渠道不能使用萬用 audience；這是目錄適用性控制，尚不是帶登入的私人下載儲存服務。`stable` 拒絕開發候選和預覽版本；從測試提升正式渠道時沿用相同發行封套與 archive 雜湊，不重新建置。沒有渠道配置的舊核心單元安裝保留原有個別簽章描述相容路線；多元件更新須使用簽章渠道。

擁有人可在監督程序停止後重新綁定來源與渠道：

```powershell
python -X utf8 -B tools/studio_installer.py channel-set --root "D:\Blueprint-Private\studio-a-program" --feed "https://updates.example.com/test-feed.json" --channel test
```

監督程序定期檢查發行 metadata，介面約每十五秒讀取本工作室的安裝狀態；兩者均不讀取公司內容。檢查或下載失敗保持目前服務；已準備包在啟動與套用前重驗。套用前重新查目錄，移除候選或簽章描述已改變時不繼續。

擁有人也可使用本機控制工具：

```powershell
python -X utf8 -B tools/studio_installer.py status --root "D:\Blueprint-Private\studio-a-program"
python -X utf8 -B tools/studio_installer.py check --root "D:\Blueprint-Private\studio-a-program"
python -X utf8 -B tools/studio_installer.py apply --root "D:\Blueprint-Private\studio-a-program" --release-id "從狀態讀回的完整識別碼" --expected-version 3 --work-saved
```

`--expected-version` 使用剛讀回的整數；`--work-saved` 是操作者對已保存並關閉相關分頁的確認，不是後台讀取所有瀏覽器草稿的證明。套用期間暫停新的公司寫入，現有 HTTP 請求排空後才取得資料 lease 及一致備份。

## HTTP 合約

| 入口 | 權限及內容 |
| --- | --- |
| `GET /api/installer` | 擁有人／管理員讀取去敏狀態；不包含路徑、token 或公司資料 |
| `POST /api/installer/check` | 擁有人，正文 `{}`；只檢查已綁定目錄 |
| `POST /api/installer/apply` | 擁有人，`{release_id, expected_version, drafts_saved: true}`；只套用已準備候選 |

寫入沿用原登入、`X-Blueprint-Request: 1` 與同來源檢查，並必須帶啟動時帳戶的 `X-Blueprint-Account`。請求不能指定下載網址、目標資料路徑或程序命令。

監督握手為 loopback 限定、每次啟動隨機 token 的內部接口，不是瀏覽器或 MCP 的新工具。握手核對程序 ID、發行識別、真正版本及 schema；公開健康頁不能代替這項身份核對。

## 資料保護與未確認結果

核心先安全停止自己擁有的服務並取得原生資料 lease，再沿用 `Maintenance` 的一致快照、獨立還原、完整性與私人副本收據。資料覆蓋、保留期限及還原證據仍有效時重用原生備份；同一操作的保護收據須讀回核對，不能因網絡重試再造一份。私人 ZIP 副本須實際保存及核對雜湊，這個本機私人副本不宣稱為異地備份。

v2 將安裝器收據接回核心既有的 snapshot／restore／client-copy 紀錄；核心啟動可按相同覆蓋與保留政策重用，不另維護一套核心備份格式。資料變動、證據失效或私人副本不符時，仍須重新保護或停止核對，不能一律跳過。

客服只在受管程序正常退出並釋放 `service.lock` 後，保護自己的完整核心資料目錄及固定私人設定。先複製原檔與 WAL 至隔離位置，再用 SQLite backup／integrity check 產生一致資料庫，建立 ZIP、隔離還原檔案核對及另一私人副本。相同檔案／設定覆蓋、24 小時內及兩份 ZIP 雜湊均有效時可重用；原操作結果未知時不覆寫。核心備份不涵蓋客服，客服核心備份也不涵蓋獨立橋接資料與手機登入；後者須另有自己的保護安排。

新版啟動失敗、程序中斷或資料可能已被新版寫入時，保留實際資料、候選及檢查點，回報 `recovery_required`。多元件操作保存已完成步驟、實際觀察版本及 runtime／登記各自結果，不假稱跨程序原子完成。部分成功或未知結果尚無自動復原／接續引擎；重新 `run` 會拒絕盲目重啟，須先核對原操作，不能自動恢復接收、重送訊息、直接還原舊資料或降低維護版本。

自動化發行測試在合成環境完成。客戶套用不建立測試工作、不發送訊息，也不自動授予 AI／渠道新權限。
