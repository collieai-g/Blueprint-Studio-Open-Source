# 外部服務接駁指引

Blueprint Integration Contract 1.0 讓獨立服務保存工作紀錄、讀取明確授權的
資料、取得使用者指令及回報實際結果。第一版只實作原生 Python 後台；
Sites 自身後台及 gateway 不會因這次更新自動具備此介面。
原生 runtime 的能力識別碼為 `blueprint-integration/1.0`，HTTP 資料欄位
`schema_version` 與 `contract_version` 使用 `"1.0"`。

這不是新的 AI 引擎，也不是 `blueprint-agent/1` 的替代品。現有
`connected_client` 工作仍由活躍 AI 對話處理；外部服務自行維護自己的
程序、憑證、發送佇列及執行規則。藍圖不載入 manifest 指定的程式碼，
也不替服務呼叫模型、DOT、WhatsApp 或對外發送。

## 開發範圍與第一個交接

本版本維護通用介面。既有客服程式庫
[BlueSAi-Cs-0001](https://github.com/collieai-g/BlueSAi-Cs-0001) 保持獨立，
其 `blueprint-client` 應固定本倉庫 `contracts/integration-v1` 的 commit
及檔案摘要，使用相同虛構樣本跑測試。不要共享 SQLite，不要直接寫對方
資料表，也不要把共用外觀或登記成功當作資料互通的證據。

第一版原生後台每個私人資料目錄代表一家公司。公司身份由已驗證的後台
及其 Store 決定；每個外部部署再綁定一個 `integration_id`。請求不能自行
聲稱 `tenant_id`，也不能用另一個實例的 ID 越權。這不是一個 Store 內的
多租戶平台。不同公司的安裝使用不同資料目錄、網址及專用服務憑證。

## 註冊與身份

1. 從已確認的同一後台 origin，使用現有擁有人／管理員身份建立
   `POST /api/integrations/v1/registry`。請求結構見
   [虛構 manifest](../contracts/integration-v1/fixtures/manifest.json)。
2. 仔細選擇 `record_types`、`commands`、`resource_refs` 及
   `allowed_origins`。能力只是允許的宣告，不代表服務已健康運行。
3. 把回傳的一次性 `token` 放進外部服務的秘密儲存，使用
   `Authorization: Bearer …`。後台只保存摘要，之後讀取登記資料不會再次
   顯示 token。不要把 token、管理員密碼或公司資料放入來源倉庫。
4. 外部服務讀取 `GET /api/integrations/v1/capabilities`，核對當前合約及
   有效範圍。Bearer 只適用此接駁命名空間，不能讀取一般資料 API、搜尋、
   匯出或維護備份。註冊、修改範圍、發出公司操作指令及正式知識發布使用
   工作台使用者身份；不能把擁有人身份交給外部服務。
5. 使用 `PATCH /registry/ID` 及剛讀取的 `expected_version` 修改範圍或
   暫停。`POST /registry/ID/revoke` 撤銷專用憑證。每次讀取、重試及交付都
   重新檢查當前授權，撤銷後不能沿用舊的成功回執繼續讀資料。

所有寫入沿用 `Content-Type: application/json` 及
`X-Blueprint-Request: 1`。有瀏覽器 `Origin` 時必須與 Host 相符。
服務應使用固定且已驗證的 HTTPS origin，保持 TLS 驗證及平台代理設定；
不要跟隨帶憑證的重新導向。只准本機隔離測試使用 HTTP loopback。

動作 schema 只支援有限的物件及 `string`、`integer`、`number`、`boolean`
欄位、標題、長度、選項與必填設定，不支援遠端 `$ref`、script、HTML 或
iframe。外部個案連結只可使用已核准的 HTTPS origin，不能含登入資料。

## 寫入與讀回紀錄

外部服務用 `POST /records` 提交 `thread`、`message`、`work_item`、
`artifact`、`note` 或 `status`。每份請求包括 `schema_version: "1.0"`、
外部原始 ID、事件 ID、`source_revision`、來源、原始時間、payload 及
`idempotency_key`。後台另外保存接收時間、平台 ID、版本及同步位置。
完整請求見 [record.json](../contracts/integration-v1/fixtures/record.json)。

回應的 `record_id`、`version` 及 `cursor` 是持久保存回執。再用
`GET /records/ID` 讀回同一份資料，核對來源 ID 與 payload；不要只檢查
HTTP 成功。修改既有紀錄須附剛讀取的 `expected_version`，並提高來源
`source_revision`。這兩個版本各自代表不同的權威；客服 `control_epoch`
及 `input_cursor` 又是另一組執行條件。

相同整合、操作及 idempotency key 的相同內容回傳原回執；相同鍵代表
不同內容會回報衝突，不會覆寫。紀錄、歷史、事件及去重回執在同一交易
提交，並行重試也不會新增副本。收到 409 時保留未保存內容並重新讀取。
外部訊息是追加的來源證據，不把後來的修訂當成原文悄悄改寫。

同步一則訊息、註解或外部訂單狀態不會建立對客發送指令。物流狀態只是
帶有來源的資料，不能憑一段客服備註斷言物流系統已完成變更。

## 持久指令與實際結果

使用者在「工作 → 外部工作」選擇已註冊服務宣告的動作，或由受支援的
已驗證 HTTP 客戶端建立 `POST /commands`。後台記錄真實操作者，驗證
動作、輸入與目標紀錄，回傳 `pending`。此時畫面只表示待處理。

外部服務從增量事件取得指令，把指令及自己的游標在本地同一交易保存
之後，再用 `POST /command-results` 回報 `accepted`。執行前仍須重新
核對本地控制權、最新輸入、期限及其他條件；平台的版本不能代替這些
檢查。取得指令不會將它刪除，也不會自行改成成功。

每次結果保留原 `command_id`、事件 ID、時間及實際證據。結果可包括
`accepted`、`running`、`completed`、`rejected`、`expired`、`failed` 或
`unknown`。後台保留每次結果，防止較舊或不合法的轉移把現況退回。
`unknown` 表示仍未查明，不能當作失敗後盲目重發。先查證原操作，再以
非空的 `payload.evidence` 查證結果轉為 `completed`、`failed` 或
`rejected`；新的執行須是重新授權的指令。

| 目前狀態 | 可接受的下一個結果 |
| --- | --- |
| pending | accepted、running、completed、rejected、expired、failed、unknown |
| accepted | running、completed、rejected、expired、failed、unknown |
| running | completed、failed、unknown |
| unknown | completed、failed、rejected，必須有查證證據 |
| completed、rejected、expired、failed | 不再改寫，沿原 key 重試可讀回原回執 |

外部讀取指令時，`deliverable: true` 才代表目前仍待交付；它不授權跳過
服務自身檢查。過期、被拒絕或授權失效的意圖會隱去操作 payload 及
控制條件，管理員仍保留原始紀錄供核對。讀回快照的交付資格可以隨當前
授權改變，持久結果及事件 ID 不會因此重新產生。

「我接手」須等待服務真的切換控制權並回報後才顯示完成。結果應保存
已生效的 `control_epoch` 及已提交的 `inflight` 項目。平台不能替已提交
的外部訊息承諾撤回，也不能將 HTTP 接收、發送、送達及已讀混成同一
狀態。背景服務停止時，已保存資料仍可閱讀，指令仍保留其實際狀態。

## 增量事件、游標及還原

外部服務持續讀取 `GET /events?cursor=…&limit=…`，不依賴使用者開啟
瀏覽器。回應包含 `events`、新的 `cursor` 及 `has_more`。有下一頁時
繼續取得；沒有新事件時保持最後游標。工作台介面本身不為同步整頁刷新，
不監看左側對話，也不清除未送出的文字。

游標是不透明的值，綁定 Store、整合實例及不可變事件錨點。客戶端不能
自行產生、解析或把其他公司的游標搬過來。外部服務應把收到的事件與
最後游標原子保存；處理失敗時沿用原游標重讀，依事件 ID 去重。

第一版保留事件，不做自動裁剪；沒有 webhook 或持久資料保留期限管理。
未來通知機制也只提示有新資料，仍以游標讀回為準。來源範圍、公司權限、
操作者狀態及期限在交付時再查，不能把建立指令時有權限當成永久授權。

還原較舊備份後，序號可能重用，但事件錨點不同。未知、未來或分歧的
游標須明確回報 `cursor_gap`，不能靜默從最新位置跳過。遇到缺口時：

1. 暫停會產生外部副作用的重試，保留原游標、收件匣及已執行證據。
2. 核對兩邊備份時間與目前身份，列出當前紀錄及待處理指令，依來源 ID、
   command ID、版本及本地執行帳本核對。
3. 由管理員確認重新同步範圍後，以空游標從保留起點重播，依事件 ID
   去重。不可因重播而再執行已完成或已過期的指令。
4. 分別保存新游標並核對讀回。第一版提供明確缺口及重播介面，沒有自動
   合併兩邊獨立還原歷史的工具。

## 核准公司知識

`knowledge` 是公司使用者維護的正式政策紀錄。外部服務可在授權範圍內
閱讀，不能用自己的 bearer 發布、批准或撤銷。管理員使用與其他紀錄
相同的 `POST /records` envelope，payload 為 `title`、`content`、
`state: "approved" | "revoked"`、`effective_at` 及可選 `expires_at`；
更新仍須平台版本及更高來源版本。見
[knowledge.json](../contracts/integration-v1/fixtures/knowledge.json)。

只有目前正式版本已批准、已生效且未過期時，外部服務才可讀到內容。
未生效、撤銷或過期版本回傳狀態而隱去正文；歷史及事件不能繞過當前
批准。服務應保存快照版本及期限，對客使用前重新核對有效性，不能把
曾經讀到的內容永久當成現行政策。

現有客服本地知識先備份、列出來源 ID、批准與期限，經擁有人核對後再
匯入。連接模式以藍圖正式版本為準；外部候選修改可另存 `note` 或
`work_item` 供人審閱，不能自動覆寫正式政策。第一版未提供批量匯入、
完整審閱流程或兩邊衝突自動合併。獨立客服模式與連接模式必須明確選擇。

`resource_refs` 另外授予已選定原生資料的讀取，使用
`GET /resources/KIND/ID`。第一版 KIND 只支援 `item` 與 `report`；上傳
文件沿用 `item`，不使用另一個 `document` 種類。每次讀取仍查當前引用
及原生權限；它不是整間公司的通用搜尋、附件下載或資料匯出權限。

## 錯誤與限制

錯誤一律為 `{error, code, retryable}`。未授權 401、越權 403、找不到
404、版本／去重內容衝突 409、游標缺口 410、內容過大 413、媒體類型
錯誤 415 各自處理。429 或 503 才按伺服器指示退避重試；寫入是否已
接收不明時沿用原 key 核對，不換 key 產生第二個操作。完整格式及樣本
見 [OpenAPI](../contracts/integration-v1/openapi.json)。

分頁每次最多 100 項，個別文字、schema 與輸入亦受伺服器限制。第一版
沒有完整按服務計費或配額系統。不要把契約列出的錯誤格式解讀成已實作
所有未來營運能力。正式環境仍需 HTTPS、可持久儲存、合適容量與備份。

## 隔離驗收、更新與回復

在來源目錄執行 `python examples/integration_service.py --self-test`。
範例建立來源倉庫以外的暫時私人目錄和本機真實 HTTP 後台，用分開的
管理員與 bearer 驗證虛構收件、重試去重、讀回、pending 接管、外部
accepted／completed、游標續讀與最終結果。它不會登入正式公司、不會
發送訊息，結束後移除測試資料。這是軟件接駁驗收，不是 WhatsApp、DOT
或真實客服部署驗收。

`python -m unittest discover -s tests -v` 會執行共用樣本、隔離及回歸
測試。服務團隊可把同一 fixture 加入自己的 client CI；schema 除了語法
驗證，也須測試相同鍵不同內容、並行寫入、權限撤銷、來源版本、重啟及
還原分歧。部署前仍須兩個獨立服務的聯合驗收。

此版本以新增 schema 7 資料表保存整合、歷史、指令及事件。沿用
[原生生命週期](NATIVE_LIFECYCLE.md) 與 [更新流程](UPGRADE.md)：正式更新
先完整備份、隔離還原及演練，透過既有 Maintenance 啟動遷移，保留舊
資料、附件、帳號及憑證。新表與 schema 版本一起提交；失敗不能留下
半套遷移。不能把資料庫版本號調低來讓舊程式開啟。

回復到舊版程式須配對更新前的完整備份，先另存更新後新增資料及結果
證據，再還原到新的私人目錄；不要直接覆寫正式資料。兩邊各自還原後
必須按照上面的缺口流程核對，過期指令不能因回復而重新生效。這份
指引與原始碼更新不會自行部署私人工作室或更改網站權限。

完整備份亦包含服務憑證摘要及當時的 enabled／revoked 狀態。還原到撤銷
以前的備份，可能令已撤銷的舊 token 再次有效。第一版沒有自動隔離或
備份以外的撤銷帳本；在恢復服務對外存取前，管理員須核對並重新套用
後來的停用／撤銷，或撤銷還原出的相關憑證並重新註冊，再更新外部服務
的秘密設定。不要把資料還原成功當作目前憑證狀態已安全延續的證明。
