# 換對話後接續同一份候選程式

本頁只負責來源交接。原任務是升級時，保存成功後必須接回 [完整更新路線](UPDATE_ROUTE.md)，保留原更新目標，繼續備份、部署及驗收。不要把保存子任務的「暫不部署」擴大成整個更新流程永遠不部署；使用者明確要求只保存時則遵守其要求。

2.4.1 的接續 ZIP 只留在當時環境。換環境後即使網絡恢復，新對話也未必能取得它。2.4.2 加入 `tools/source_checkpoint.py`，把**已檢查的私人來源**保存到獨立私人倉庫，再下載核對。它不部署網站，也不替代正式資料備份。

## 開源與私人資料分開

- 本開源倉庫只放通用程式、指南與虛構測試資料，永遠不可作私人檢查點的目的地。工具會拒絕本倉庫名稱，包括大小寫變體。
- 必須明確選擇另外的私人倉庫。工具不從目前 checkout 的 origin 推斷目的地，不建立倉庫、不改可見性、不修改預設分支。上傳前核實 GitHub 的私人狀態及寫入權限；公開、內部或未知可見性均停止。
- GitHub 檢查點只含來源工作樹，沒有公司資料、聊天、登入、附件、備份或 Git 歷史。`.openai` 等平台設定不會封裝。常見私人資料檔會被拒絕；AI 仍須檢查檔案內容及清單，檔名規則不是完整秘密掃描器。
- `INSTALL_PROGRESS.md`、完整接續包、資料庫及附件繼續放在原平台或另外的私人備份儲存。不能把整個更新目錄送到 GitHub，亦不能把來源檢查點稱為完整備份。

使用者只需貼更新指令，由 AI 完成以下操作。沒有合適私人儲存或權限時，保留現有工作，只說明缺少的能力；不能改用公開倉庫或要求把密碼貼到聊天。

## 舊環境：保存並讀回

先取得此版本工具；私人來源與工具 checkout 分開。以下使用示例路徑及虛構倉庫，由 AI 替換成實際已核對的位置。

```sh
python tools/source_checkpoint.py export \
  --project "/私人/原候選來源" --output "/私人/本次來源檢查點" \
  --repo "example/private-workspace" --url "https://原工作台網址" \
  --version "2.4.2" --role candidate
```

`--version` 是這份候選實際準備的目標版本，不能把舊的 V2.4 候選直接改稱 V2.4.2。保存舊正式來源時用 `--role baseline`。Sites 會讀原 `hosting.json` 的 Site ID；沒有檔案時用平台工具取得的 ID 傳入 `--site-id`。新目錄內只有 `source.zip` 與 `checkpoint.json`，包含來源 commit、未提交狀態、逐檔 SHA-256、執行權限與原網站身份。未提交新增／修改與刪除效果都會保留。

先檢查這份清單與來源，排除公司內容、硬編碼秘密或資料匯出。工具沒有讀取平台秘密、備份或聊天。容量為 32 MiB，超過時使用已核實的私人儲存，不得省略檔案卻聲稱完整。

已有 `gh` 登入時，由 AI 執行：

```sh
python tools/source_checkpoint.py save \
  --directory "/私人/本次來源檢查點" \
  --branch "blueprint-checkpoints/candidate-20261022-01"
```

工具先核對私人倉庫，再建立**全新分支**，根目錄只有來源 ZIP 與清單，不含可執行的 GitHub Actions。已有同名分支會停止，不強制覆寫。GitHub 若有外部監聽所有分支的自訂自動化，AI 應先核對並選擇專用私人倉庫，不能假定其他服務沒有觸發器。

保存後會從剛建立的完整 commit 重新下載，核對 Git blob、來源包及每個檔案，成功才產生 `remote-receipt.json`，狀態為 `remote_source_verified`。回報私人倉庫、分支與完整 commit；`production_status` 仍為 `not_verified`。若上傳成功而下載失敗，保留分支；下一步查回該分支的 commit 並重試下載，不重建候選或誤稱交接完成。

## 只有 GitHub 外掛時

CLI 使用現有 `gh` 認證，不會從外掛擷取憑證。沒有 `gh` 登入時，AI 可用已連接且具備下列能力的 GitHub 工具執行相同流程，不必請使用者重新登入另一條路線：

1. 用倉庫查詢核對**確切名稱、私人可見性與寫入權限**，拒絕本開源倉庫及所有公開倉庫。若名稱轉址，先核對新名稱，再重新 export。
2. `export` 後檢查兩個檔案。將 `source.zip` 以 base64 傳給 create_blob，`checkpoint.json` 以 UTF-8 或 base64 傳給 create_blob。不要在聊天／公開日誌印出內容。
3. create_tree 建立只含這兩個 blob 的新樹，**不要使用 base_tree**；兩者都是 `100644`。create_commit 可建立無父提交；工具必須填 parent 時用已核實的私人提交，仍指定這個只有兩檔的新樹。最後 create_branch 新增 `blueprint-checkpoints/...`，不能 update_ref 覆寫現有分支。
4. 再核對私人狀態、分支指向與確切 commit 的樹。從該 commit 下載兩個完整檔案至另一個私人目錄，核對 Git blob SHA；只看遠端檔名或寫入成功回應不夠。GitHub 外掛的 `fetch_blob`／一般 `fetch` 可能只接受 UTF-8，不能直接讀 ZIP；有 `fetch_file` 時用 `path=source.zip`、`ref=確切commit`、`encoding=base64`，解碼後按 Git blob 格式核對 SHA。回應若截斷就不能當作完整下載，不能以文字模式修補二進位檔案。
5. 執行下一節 `restore`，加 `--expected-manifest`，其值來自 export 輸出的 `manifest_sha256`。成功後在私人進度記錄這次遠端 commit 與讀回結果。不要手工偽造 CLI 的成功收據。

工具無法完整傳輸二進位檔案時，這條外掛路線未完成，改用已有認證的 Git／平台私人檔案儲存；沒有可用路線才請使用者完成一個明確的登入或轉交動作。不能把可讀聊天等同可下載舊環境檔案。

## 新環境：按確切提交取回

AI 先查私人進度、已知檢查點分支，核對同一網站並固定完整 commit。不要盲取「最新」分支，也不要使用主分支替代候選。

```sh
python tools/source_checkpoint.py fetch \
  --repo "example/private-workspace" --commit "已核對的40位commit" \
  --output "/私人/新環境下載" --url "https://原工作台網址" \
  --site-id "平台回傳的原SiteID"
```

原生服務不填 `--site-id`。工具核對私人狀態、Git blob 與清單，解到新的 `新環境下載-source` 目錄；不覆寫現有來源，不執行程式。還原的是完整來源工作樹，沒有 `.git` 歷史；原 commit 保留在清單供核對，不能聲稱新目錄的 Git HEAD 已是原 commit。

外掛已下載兩個檔案時：

```sh
python tools/source_checkpoint.py restore \
  --directory "/私人/外掛下載" --output "/私人/恢復候選" \
  --repo "example/private-workspace" --url "https://原工作台網址" \
  --site-id "平台回傳的原SiteID" --expected-manifest "原清單SHA256"
```

AI 從原平台重新取得同一 Site 的部署設定，核對資料庫、物件儲存及登入綁定，再把工作樹整合到隔離的原站 checkout。缺少舊的 `.openai` 設定不能另建 Site；原資料備份、私人進度和瀏覽器偏好另外取回。繼續 [UPGRADE.md](UPGRADE.md) 的完整備份與部署驗收，不能因來源已恢復就標記所有步驟完成。

## 已經卡在新環境怎樣辦

先找私人遠端檢查點、平台已保存版本及現有檔案。若候選只在舊環境且沒有跨環境取檔能力，新對話無法靠指令自動取得它。給使用者**一段貼回舊對話的指令**，由舊對話按本指南保存、讀回及提供 commit；新對話再直接讀私人倉庫，免除下載再上傳 ZIP。舊環境也失去時，才根據可還原來源重新準備候選，明確說明它是重新建立的版本。

網絡恢復後不要重複要求加入網域。附件備份、工具載入、正式部署及右側驗收仍須逐項完成；這個工具只修正來源無法接續的問題。

取得私人倉庫及 commit 後，下一個對話直接取回並接續完整更新；不用再叫使用者回舊對話保存同一份來源。若候選版本早於選定目標，先整合差異及驗證，再一次部署目標版本，不用連續部署每個中間版本。
