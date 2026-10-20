# Box 公開證據 REA 與藍圖功能對照

調查基準：2026-10-20。目標為登入後內容工作台、企業管理功能及公開 API，而非只參考行銷首頁。此研究使用官方公開文件、示範、截圖、公開 UI 原始碼及 OpenAPI；**未登入企業帳戶、未安裝原生 App、未逐個按鍵實測付費介面**。

以下係重建規格底稿，不是「已完成 Box 全產品複製」證明。藍圖實作狀態以程式、整合測試及介面驗收記錄為準。

## 證據邊界

- **D：官方文件支持**：官方列出操作、用途、限制或權限。
- **P：公開原始碼／示範支持**：可確認部分元件、互動及資料結構；不推論完整私有後端。
- **B：藍圖產品決定**：為獨立自架後台設計的行為，不冒稱 Box 原有能力。
- **U：未實測／版本有差異**：保留到差異清單，不以推論補成已驗證事實。

固定公開原始碼版本：

- [Box UI Elements `63094918`](https://github.com/box/box-ui-elements/tree/6309491838185107607fa74435a4aa66d8306c47)。其特定 Box SDK 授權不作為藍圖獨立產品的程式依賴。
- [Box OpenAPI `a4f3e23d`](https://github.com/box/box-openapi/tree/a4f3e23d5bd01d9f4398bc1a764d410df3e607a7)。此 repo 為 Apache-2.0；公開 schema 不代表無須授權可呼叫真實 API。
- [UI 元件展示](https://opensource.box.com/box-ui-elements/)、[快速示範](https://www.box.com/resources/quick-demos)、[完整產品示範目錄](https://www.box.com/events/live-demos)。展示可能只提供預設路徑，部分完整 Explorer stories 仍需要 Box token。

## 1. 導覽、文件、收藏

| ID | 入口／操作 | 已確認用途與狀態影響 | 藍圖驗收重點 | 證據 |
| --- | --- | --- | --- | --- |
| NAV-01 | Files；Folder Tree | 開啟有權內容，樹狀跳轉 | 列表、麵包屑及深層路徑一致；每次讀取有授權 | D [基本操作](https://support.box.com/hc/en-us/articles/360044196373-The-Basics-of-Box) |
| NAV-02 | 摺疊／展開側欄；More | 收起次要入口；收起時仍可存取集合 | 鍵盤、手機及窄螢幕能到達所有已啟用頁面 | D [新版導覽](https://support.box.com/hc/en-us/articles/43938600160019-A-modernized-web-experience-to-streamline-your-workflow) |
| NAV-03 | Recent Files；See All | 個人最近使用項目及原資料夾位置 | 只記錄實際存取且仍有權的內容 | D 同上 |
| NAV-04 | 頭像 → Account Settings → Notifications → Save | 個人通知偏好；部分資料夾可覆寫 | 偏好保存；通知事件不等同寄信成功 | D 基本操作 |
| FILE-01 | New → Folder；Name；Invite；Permission；Create | 建資料夾及選擇協作者角色 | 名稱／父層權限／邀請角色由伺服器驗證 | D 基本操作 |
| FILE-02 | Upload；拖放；取消／重試 | 加入原檔；上傳狀態 | 真實 bytes、失敗恢復、hash；不以假進度代替上傳 | D/P [Explorer](https://developer.box.com/guides/embed/ui-elements/explorer) |
| FILE-03 | 名稱／空白列；Checkbox；Shift／Ctrl 選取；Esc | 開啟項目和選取項目分開 | 開啟不誤觸批次動作；手機有選取模式 | D [列表／格視圖](https://support.box.com/hc/en-us/articles/10117683547539-List-view-and-grid-view-enhancements) |
| FILE-04 | List／Grid；排序；欄位 | 改變瀏覽呈現 | 偏好保存，篩選及排序實際作用於資料 | D 同上 |
| FILE-05 | …／右鍵 → Move or Copy；目的地；確認 | 移動／複製單項或多項 | 驗證目的地與繼承權限，批次結果可追查 | D [移動複製](https://support.box.com/hc/en-us/articles/360044196593-Moving-and-Copying-Files-Folders) |
| FILE-06 | Rename；Save／Cancel；Delete | 修改名稱或移入 Trash | 樂觀版本衝突、失敗不遺失名稱、刪除可還原 | P [公開 UI 元件](https://github.com/box/box-ui-elements) |
| FILE-07 | Lock；Duration；Disable Download；Unlock／Request Unlock | 控制編輯／下載期間，避免衝突 | 期間到期及角色檢查；不只顯示鎖頭 | D [鎖定](https://support.box.com/hc/en-us/articles/360043697174-Locking-Unlocking-Files) |
| FILE-08 | Upload New Version | 同一資源加入不可變原檔版本 | 版本、hash、掃描及預覽分別綁定，舊版仍可追查 | D [版本](https://support.box.com/hc/en-us/articles/360043697054-Accessing-Version-History) |
| COL-01 | Collections → +；Name；Create | 建私人引用集合 | 不搬原件，不授予新讀取權 | D [Collections](https://support.box.com/hc/en-us/articles/360048730094-Using-Collections) |
| COL-02 | Star；集合 Checkbox；拖放入集合 | 收藏及一物件加入多集合 | 集合是引用，移除引用不刪原件 | D 同上 |
| COL-03 | 集合 … → Rename／Delete；Make Homepage | 改私人組織及首頁偏好 | 不影響其他成員；無權原件不洩露 metadata | D 同上 |
| SEARCH-01 | 搜尋框；建議；Enter／See All | 搜尋文件、資料夾及支援內容 | 權限先行，結果不能包含不可讀節錄 | D [搜尋](https://support.box.com/hc/en-us/articles/360043696314-Search-for-Files-Folders-and-Content) |
| SEARCH-02 | 類型／位置／更新日／擁有人／標籤／metadata 篩選 | 收窄查詢 | 條件交由後端執行；不可只改顯示籤 | D [搜尋增強](https://support.box.com/hc/en-us/articles/31514489688851-Filters-and-quick-search-enhancements) |

新版官方文件將 All Files 改為 Files，移除左側獨立 Recents；Canvas、Integrations、Synced、Trash 收入 More。各租戶 rollout 及功能入口可能不同，不混用舊藍色側欄畫面作當前精確基準。

## 2. 預覽、分享、協作、版本

| ID | 入口／操作 | 已確認用途與狀態影響 | 藍圖驗收重點 | 證據 |
| --- | --- | --- | --- | --- |
| PRE-01 | 檔名／父資料夾／返回 | 預覽及定位原件 | 預覽綁定版本；返回保留位置 | D [Preview](https://support.box.com/hc/en-us/articles/360044195013-Introducing-Box-Preview) |
| PRE-02 | Zoom；Rotate；Fullscreen；左右切換 | 格式對應的瀏覽控制 | 不支援格式有真實說明；切換仍受權限 | D 同上 |
| PRE-03 | Open；Download；Share；… | 本機編輯、下載、分享及管理 | 不靠隱藏按鈕代替伺服器授權 | D/P 同上及 Explorer |
| PRE-04 | Activity／Details／Metadata | 切換討論、屬性、分類及自訂欄位 | 讀寫權限分開，編輯保存及歷史可驗證 | D 同上 |
| PRE-05 | AI；選來源；輸入／送出 | 文件問答／分析 | 藍圖只調用已接外部員工；保留來源版本、狀態及結果 | D [Using AI](https://support.box.com/hc/en-us/articles/50474599955219-Using-Box-AI)／B 外部接口 |
| SHARE-01 | Copy Shared Link；Share；Enable Link；Copy | 建立／啟用連結及複製 | 建立分享有真正 token、scope、期限及撤銷 | D [分享連結](https://support.box.com/hc/en-us/articles/360043697094-Creating-Shared-Links) |
| SHARE-02 | People with link／company／invited | 設定連結讀取對象 | 公司範圍需要登入；公開分享不等同持續協作者角色 | D 同上 |
| SHARE-03 | Link Settings；Password；Expiry；View／Download／Edit | 限制連結使用 | 每次存取、下載及寫入都重新檢查 | D 同上 |
| SHARE-04 | Invite People；角色；Manage Collaborators | 加人、改角色及移除持續授權 | 撤銷後現成連結及直接 API 均不能越權 | D [角色](https://support.box.com/hc/en-us/articles/360044196413-Understanding-Collaborator-Permission-Levels) |
| COLLAB-01 | Comment；Reply；@mention | 內容討論及指定成員通知 | 對象必須有權；通知失敗可追查 | D [留言任務](https://support.box.com/hc/en-us/articles/360044193333-Replace-Email-Chains-with-Comments-and-Tasks) |
| COLLAB-02 | Add Task；Type；Assignee；Message；Due；Create | 一般／審批任務 | 真實指派、期限與狀態，不能指派無權人員 | D 同上 |
| COLLAB-03 | One assignee required；Complete／Approve／Reject | 任一／全部完成及批准／拒絕 | 保存決策人、時間、規則與完成條件 | D 同上 |
| COLLAB-04 | Modify／Delete Task；Task Center | 修改未完成任務及統一待辦 | 依建立者／角色限制修改，已處理紀錄保留 | D [Task Center](https://support.box.com/hc/en-us/articles/360044195793-Using-the-Box-Task-Center) |
| VERSION-01 | Version History；Preview／Download | 開啟個別版本 | 讀取舊原檔而非當前版本替身 | D [版本歷史](https://support.box.com/hc/en-us/articles/360043697054-Accessing-Version-History) |
| VERSION-02 | Make Current；Delete／Restore version | 晉升、刪除及還原版本 | 檢查角色、保存政策及歷史 | D 同上 |
| TRASH-01 | Trash；搜尋；Owned／Deleted 篩選 | 查看可見已刪內容 | 不洩露其他人不可見資料 | D [Trash](https://support.box.com/hc/en-us/articles/360044196093-Managing-Trash) |
| TRASH-02 | Restore／Delete；Restore All／Delete All | 還原及永久刪除 | 批次範圍明示；hold／retention 不可繞過 | D 同上 |

## 3. Hubs、Notes、Canvas、業務工作台

| ID | 入口／操作 | 用途與必要行為 | 藍圖決定／驗收 | 證據 |
| --- | --- | --- | --- | --- |
| HUB-01 | New Hub；Add Content；Add to Hub；選 section | 整理既有文件／資料夾／連結 | 引用不複製原件；排序、分區及預覽保存 | D [Hubs 內容](https://support.box.com/hc/en-us/articles/25870093558675-Adding-Content-to-Box-Hubs) |
| HUB-02 | Share；Shared with；Manage All | 發佈知識入口 | 明示底層內容授權影響；入口權限不代替文件權限 | D [分享 Hub](https://support.box.com/hc/en-us/articles/33749892767891-Sharing-a-Hub) |
| NOTE-01 | New；Title；模板；自動儲存 | 建筆記、模板及標題 | 真實保存狀態、斷線重連、修訂版本 | D [Notes](https://support.box.com/hc/en-us/sections/21356720224275-Creating-and-Editing-Box-Notes) |
| NOTE-02 | 標題／清單／連結／引用／圖片／表格／程式碼／目錄 | 富文字編輯 | 支援內容重開一致，安全渲染及權限驗證 | D 同上 |
| NOTE-03 | Undo／Redo；游標；變更提示；註解；分享 | 即時協同編輯 | 兩人同改收斂；唯讀不可寫；資料不互相覆蓋 | D [協作](https://support.box.com/hc/en-us/articles/360043694494-Tracking-Changes-and-Recent-Activity-in-a-Box-Note) |
| NOTE-04 | Mermaid；表格設定；章節錨點／摺疊 | 文件內結構及圖表 | 圖表內容可編輯、重開及匯出；不當作業務資料表 | D [新版 Notes](https://support.box.com/hc/en-us/articles/48275136146707-New-Box-Notes-features-enhanced-tables-Mermaid-diagrams-and-more) |
| CANVAS-01 | New／模板；Select／Pan；縮放 | 白板建立及瀏覽 | 真實畫布座標、狀態保存及權限 | D [Canvas](https://support.box.com/hc/en-us/articles/48218095373971-Getting-Started-with-Box-Canvas) |
| CANVAS-02 | Sticky；文字；形狀；畫筆／橡皮；圖片；連線 | 白板內容 | 移動物件連線跟隨；多人更新保持；白板不自動執行流程 | D 同上 |
| APP-01 | New App；Page；Section；Grid／List；Add Blocks | 無程式工作台編排 | 自訂頁面、區塊、資料表視圖及篩選實際保存 | D [建立 App](https://support.box.com/hc/en-us/articles/35839726700435-Creating-an-App) |
| APP-02 | View 設定；Filters／Columns；Save／Save as New | 保存業務視圖 | 後端查詢、個人／共用範圍及底層資料權限 | D [Views](https://support.box.com/hc/en-us/articles/37315568510483-Configuring-View-in-Box-Apps) |
| APP-03 | Inline edit；批次 metadata；排序；List／Grid | 編輯與組織結構資料 | 型別校驗、版本衝突、原子寫入及歷史 | D 同上 |
| DB-01 | 新資料表／欄位／關聯／公式 | 藍圖通用公司資料庫 | 不是 Box Notes 普通表格；資料表及記錄有穩定 ID | B 使用者確認 |
| DB-02 | 列表／看板／日曆／圖表／表單 | 可配置公司工作台 | 同一資料來源、多視圖一致；保存篩選及欄位設定 | B 使用者確認 |
| DAY-01 | 新日報／編輯／待辦／完成／修訂歷史 | 保存成果、決定、參考及行動 | 真實工作來源；修改保留進度及歷史 | B 原有後台用途 |
| CAL-01 | 個人／公司日程；重複事件；提醒；ICS | 公司行事曆 | UTC＋IANA 時區、重複及例外、權限與提醒狀態 | B 使用者確認 |
| CAL-02 | Google／Microsoft Connect；Sync；Disconnect | 外部日曆 | 真實 OAuth；增量、刪除、衝突及撤銷；沒有 token 不顯示已連接 | B [Google](https://developers.google.com/workspace/calendar/api/guides/sync)、[Microsoft](https://learn.microsoft.com/en-us/graph/delta-query-events) |

## 4. 工作流、表單、文件生成、外部 AI

Box Automate 是現時流程參考，Relay 只補充舊有行為；不要只還原舊 Relay。[差異來源](https://support.box.com/hc/en-us/articles/51191572681107-Differences-Between-Box-Automate-and-Box-Relay)

| ID | 入口／操作 | 用途與必要行為 | 藍圖驗收 | 證據 |
| --- | --- | --- | --- | --- |
| FLOW-01 | New；Name；拖入 trigger；+ step | 建立流程圖 | 每個節點有可執行配置，未完整草稿可保存 | D [建立流程](https://support.box.com/hc/en-us/articles/50925837290131-Creating-workflows-in-Box-Automate) |
| FLOW-02 | 文件／表單／任務／簽署／metadata／手動／排程觸發 | 開始一次流程 | 真實事件、去重及有權來源 | D 同上 |
| FLOW-03 | Condition；Parallel／Merge；For Each；Go Back；Variables | 分支、循環及資料流 | 有界循環、變數校驗、確定合流及執行紀錄 | D 同上 |
| FLOW-04 | 文件動作；任務／批准；HTTP；通知；外部 AI；生成／簽署 | 執行實際步驟 | 讀寫與外部結果可追查；錯誤不跳成成功 | D 同上／B 外部 AI 協定 |
| FLOW-05 | Save；Activate；Deactivate；Share；Tracking | 發布、權限及追蹤 | 新版本不改舊執行；停用不等同取消已啟動任務 | D [流程管理](https://support.box.com/hc/en-us/articles/51191347003795-Managing-Workflows) |
| FLOW-06 | Connectors；Add；Enable／Disable；權限 | 連接外部服務 | 憑證不回傳；伺服器限制目標及操作；重試須有冪等條件 | D [自訂連接器](https://support.box.com/hc/en-us/articles/51207924089363-Using-Box-Custom-Connectors) |
| FORM-01 | New；欄位拖放；必填／格式；條件 All／Any | 自訂收集表單 | Server 驗證、附件限制、條件控制及型別 | D [Forms](https://support.box.com/hc/en-us/articles/32564947075859-Creating-a-Form) |
| FORM-02 | Preview；Activate／Deactivate；Share；期限／登入 | 測試及正式發佈 | 預覽提交不污染真實紀錄；失效連結拒絕提交 | D 同上 |
| FORM-03 | Submissions；開啟；附件；刪除 | 處理提交 | 一次提交只觸發一次工作流；刪提交不誤刪共用附件 | D [管理](https://support.box.com/hc/en-us/articles/38605008751251-Managing-Box-Forms) |
| DOC-01 | New Template；上傳；欄位／表格／條件／圖片 | 定義文件模板 | 真正產生 DOCX，錯誤指出欄位；輸出可用 Office 開啟 | D [Doc Gen](https://support.box.com/hc/en-us/articles/36587626631827-Box-Doc-Gen-building-blocks) |
| DOC-02 | Generate；表單／JSON；選保存位置 | 生成文件 | 鎖定輸入、原檔及結果、PDF 轉換狀態 | D [生成](https://support.box.com/hc/en-us/articles/48579824629011-Generating-Documents-Using-Box-Web-App) |
| EXT-01 | 抽取配置；欄位／指令；Keep／Override；來源；Activate | 將內容抽成資料 | 藍圖交給外部員工，校驗實際回傳型別與來源；不內建虛構 AI | D [Extract](https://support.box.com/hc/en-us/articles/48156443597331-Using-Box-Extract)／B |
| EXT-02 | Run History；人工修正；停用 | 追查及覆核抽取 | 保留原結果與人類修訂；重送 callback 去重 | D [Extract 管理](https://support.box.com/hc/en-us/articles/48037016473235-Managing-Custom-Extract-Agents-in-Box-Extract) |
| AI-01 | 來源選取；Prompt；History；Copy；引用 | AI 工作入口 | 外部員工真實連接、有限資料範圍、引用及狀態 | D [AI](https://support.box.com/hc/en-us/articles/50474599955219-Using-Box-AI)／B |
| AI-02 | 新外部員工／能力／權限／測試／派工／停用 | 接入日後開發的 AI 員工 | 服務身份、scope、冪等鍵、期限、簽署 callback、取消確認 | B 使用者明確排除員工本身實作 |
| API-01 | REST／OpenAPI；MCP；Webhooks | 程式接入 | 與真人相同授權；重送去重；秘密不洩露 | D [Box MCP](https://developer.box.com/guides/box-mcp)／B 藍圖協定 |

## 5. 電子簽署、企業管理、安全及歸檔

| ID | 入口／操作 | 用途與必要行為 | 藍圖驗收 | 證據 |
| --- | --- | --- | --- | --- |
| SIGN-01 | Sign；Sent／Inbox／Templates；Filter | 找到請求及待處理文件 | 真實 provider 狀態；時間、對象、結果可追查 | D [Sign 入門](https://support.box.com/hc/en-us/articles/48217136567571-Getting-Started-with-Box-Sign) |
| SIGN-02 | New Template；文件；收件人；欄位；次序／平行；Send | 建及送出簽署請求 | 使用免費 CE API，郵件／連結實際可用；回報發送錯誤 | D 同上／B Documenso CE |
| SIGN-03 | Sign／Approve；Reminder；Completed PDF／Signing Log | 簽署及證據 | PDF 密碼學完整性、時間、認證及事件記錄；重送不重複 | D [Signing Log](https://support.box.com/hc/en-us/articles/4404095202579-Viewing-the-Signing-Log) |
| ADMIN-01 | Users／Groups；Invite；角色；停用；Reports | 公司成員及治理 | 停用身份撤銷 session／token；群組及資源 ACL 實際執行 | D [Admin](https://support.box.com/hc/en-us/articles/360043695714-Admin-Console-Guide) |
| ADMIN-02 | SSO／MFA；應用允許；API tokens | 企業身份及整合控制 | 服務身份與真人分開，受控 scope、撤銷及稽核 | D [SSO](https://support.box.com/hc/en-us/articles/360043696514-Setting-Up-Single-Sign-On-SSO-for-Your-Organization)／B SCIM |
| DLP-01 | Classification；自動分類；政策 | 偵測敏感資料並限制使用 | 標籤、命中原因、後端分享／下載／整合限制 | D [Shield 分類](https://support.box.com/hc/en-us/articles/360052481814-Automated-classification-in-Box-Shield) |
| DLP-02 | Smart Access；Watermark；限制動作 | 依分類保護內容 | 資源版本、身份及動作政策；水印輸出而非純裝飾 | D [Smart Access](https://support.box.com/hc/en-us/articles/7711416297747-About-Smart-Access) |
| SEC-01 | Malware rule；警告；詳細結果；Override | 掃描及隔離惡意內容 | 原檔版本與掃描判定綁定；EICAR測試；引擎失效不得標為安全 | D [Malware](https://support.box.com/hc/en-us/articles/46976541627411-Malicious-Content-Detection-Rule) |
| SEC-02 | 異常下載規則；警報；詳細事件 | 識別異常行為 | 可重現規則／統計、IP／對象／時間及管理處理；偵測與封鎖分開 | D [Anomalous Download](https://support.box.com/hc/en-us/articles/46976557112851-Anomalous-Download-Detection-Rule) |
| GOV-01 | Retention；期限；範圍；處置 | 保存內容及版本 | 最遲到期政策生效；移動及 Trash 不解除保存 | D [Retention](https://support.box.com/hc/en-us/articles/360043694374-About-Retention-and-Retention-Policies) |
| GOV-02 | Legal Hold；範圍；解除 | 防止指定內容最終刪除 | 儲存層版本保留；解除後仍尊重其他保存期 | D [Legal Hold](https://support.box.com/hc/en-us/articles/360044193773-About-Legal-Hold-Policies) |
| ARC-01 | Admin → Archives；Create；Archive files | 管理員歸檔保全 | 所有版本保存；撤除分享／協作；限制一般修改 | D [Archive](https://support.box.com/hc/en-us/articles/40530659112211-Archiving-Files-and-Folders) |
| ARC-02 | Archive Search；Restore；Delete Empty Archive | 查找及還原 | 原／新位置還原、權限重檢；有內容不能刪 archive | D [還原歸檔](https://support.box.com/hc/en-us/articles/40530832275731-Restoring-Archived-Content-and-Deleting-Archives) |
| DEVICE-01 | Drive／Tools；Device Trust／EDR | OS檔案整合及可信裝置狀態 | 網頁版後續：需要真實Windows／macOS元件、可信證據及獨立驗收 | D [Box Drive](https://www.box.com/resources/downloads)／[EDR](https://support.box.com/hc/en-us/articles/29997961590291-Box-Shield-is-integrating-with-CrowdStrike-to-deliver-enhanced-endpoint-protection) |

## 6. 保留的差異與未決證據

- **介面版本**：公開元件、示範與租戶 rollout 未必同版。現有規格不聲稱包含所有 A/B 介面、方案及角色的每一粒按鍵。
- **Hubs 權限**：Box 分享 Hub 可連帶提供相關內容 Viewer 權限；藍圖必須明示並驗證此影響，不能隱藏授權擴大。
- **AI API 差異**：新 AI Home 管理文件指新建／升級 Agent 不支援 API，不能由舊 AI Studio API 推論全部新 Agent 可外部控制。藍圖採自己的外部員工協定。
- **Forms 方案差異**：Automate 矩陣列 Enterprise Advanced，建立流程文件部分內容列 Advanced Plus；這不影響藍圖免費功能，但不能當作已核實的單一 Box 方案規則。
- **Extract 格式差異**：新版 Using 頁支援更多格式，Managing 頁仍有 PDF／根資料夾描述；來源版本不同，未實測。
- **桌面差異**：新舊 Windows Drive 文件對個別檔案離線、Store app 等描述不同；後續需固定客戶端版本。Device Trust 與 Device Pinning 亦非同一能力。
- **專有能力**：不由公開 UI 推論 Box 私有掃描模型、法規認證、後端實作、內部風險分數或任意格式的完整支援。
- **視覺目標**：九成相似以固定公開參考頁面、版面、間距、控制位置及互動對照；不是已量測的產品百分比。
- **整合市場**：藍圖交付可運行的整合框架及已列連接器，不把 Box 1,500+ 第三方應用生態視為已逐一移植。

## 7. 每項操作的完成標準

1. 介面可到達，欄位／鍵盤／窄螢幕狀態可用。
2. 真正呼叫後端，具有同一套身份及資源權限。
3. 成功、失敗、取消、衝突及服務未配置有明確結果。
4. 資料、版本、附件及操作歷史重啟後保持。
5. 有與風險相稱的測試；涉及外部服務者用真實接駁驗證後才列完成。
6. 研究證據及藍圖設計決定有來源／版本，未知項沒有被改寫成已觀察事實。

## 8. 已執行 REA CLI：公開發行包靜態證據

本次實際使用 **REA CLI 6.7.0** 分析 npm 正式發行嘅 **`box-ui-elements@27.0.0`**。下載包及完整分析紀錄保存在工作環境研究目錄；解包暫存於分析後清理。沒有將 Box 程式加入藍圖產品原始碼或執行。

- 壓縮包：16,418,471 bytes；npm SHA-512 integrity 已比對。
- 壓縮包 SHA-256：`afeccffd7132b003ba579140156ca3fcdc836f2fb9aa23d165cb8213ffbc9d2e`。
- REA extracted-artifact SHA-256：`8f39b61c4c80690f66d62ef5456b9f3703199e2ab15c96be9b3b17f06694d638`。
- Evidence ID：`ev_77aeca41487116c1361142480155fc30c69fbcfb238755d97ea10d4783ad6f31`。
- 針對六個已發行 JavaScript 檔案：ContentExplorer、RenameDialog、DeleteConfirmationDialog、ShareDialog、ContentPreview、ContentUploader。
- 工具統計：解析 6 個檔案、18,936 AST 節點、0 parse failure、396 個靜態 findings。Findings 數目不等於功能或按鈕數目。
- 完整 artifact inventory 包含 10,157 個相關檔案；其餘 10,151 個沒有進入本次選定源碼分析。

[可攜帶證據摘要及逐檔 hash](../deploy/research/box-ui-elements-27.0.0.json) 保留工具身份、來源、範圍、限制及完整輸出 hash。

此證據確認發行元件包含獨立 rename／delete／share dialog、loading 與 callback 邊界、Explorer 功能旗標，以及 preview／uploader 入口；原始碼交叉閱讀支持功能對照表。**靜態關係不是執行觀察**，不證明 Box 後端已執行任何動作。

REA 明確回報 application graph 為 partial；semantic graph 亦為 partial，達到本次設定的節點／unknown 上限，存在 dynamic call 及未選檔案的未知關係。沒有將未知關係當成不存在，也不稱為全 SDK 或整個 Box SaaS 已完成分析。
