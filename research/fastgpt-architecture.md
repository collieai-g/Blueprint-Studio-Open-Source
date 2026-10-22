# 參考 FastGPT：把可持續更新及 Agent 能力放在同一個後台

本頁只供維護者研究及追溯參考來源，獨立於藍圖產品文件。安裝包、Git 來源封存及 Docker 建置內容均排除此研究目錄；安裝、更新及運行藍圖不需要 FastGPT 帳戶、套件或服務。

研究來源固定於 FastGPT commit [`9fd604ab860a5cc566f7450ead9fc64dcbc555f1`](https://github.com/labring/FastGPT/tree/9fd604ab860a5cc566f7450ead9fc64dcbc555f1)，研究日期 2026-10-22。另核對 [v4.17.2 發佈說明](https://github.com/labring/FastGPT/releases/tag/v4.17.2)。以下是架構比較及藍圖自己的實作安排，並非宣稱 FastGPT 能替 Sites 解鎖平台權限。

## 學到甚麼，以及怎樣取捨

| 讀過的實作 | 底層做法 | 藍圖採用的方式 |
| --- | --- | --- |
| [部署範本](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/deploy/version/main/docker-compose.template.yml) | 應用、工具服務及資料儲存分工；資料保存在獨立 volume；設定傳入服務 | 原生後台持有唯一公司資料，Sites 作入口與身份接駁。保留輕量 Python／SQLite，暫不引入 MongoDB、Redis、向量庫及整套服務。 |
| [升級說明](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/document/content/self-host/upgrading/upgrade-instruction.mdx)與 v4.17.2 release | 更新執行程式與處理舊資料是不同工作；部分遷移有順序及執行前提 | 沿用藍圖已實作的備份、隔離演練及啟動前遷移；新增獨立 API 合約，避免介面、MCP 及後台被迫使用相同版本號。 |
| [遷移註冊表](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/projects/app/src/migration/registry.ts)及[執行器](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/projects/app/src/migration/runner.ts) | 穩定任務 ID、依賴、進度、檢查點及執行權；分開阻擋啟動與可稍後處理的工作 | 未來 Agent 任務使用可保存及接續的狀態；核心資料遷移失敗仍停止啟動，摘要或索引失敗不應阻塞整個工作台。並非把備份失敗改成忽略。 |
| [Agent loop 入口](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/packages/service/core/workflow/dispatch/ai/agentLoopCore/application/run.ts)及[runtime 組裝](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/packages/service/core/workflow/dispatch/ai/agentLoopCore/application/runtime/createRuntime.ts) | 模型循環、工具執行、用量及呈現事件有明確邊界 | 規劃獨立模型介面、任務執行器及工具登記。原版 UI 顯示工作狀態，業務資料仍透過既有 API。 |
| [工具目錄](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/packages/service/core/workflow/dispatch/ai/agent/adapter/toolCatalog.ts)及[等待使用者](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/packages/service/core/workflow/dispatch/ai/agentLoopCore/adapter/interactive/ask.ts) | 業務工具與計劃／詢問工具分開；等待回答是可呈現的執行狀態 | 客服先產生有來源的草稿；需要人工決定時保存進度並等待，回覆後接續。模型不能自行寫入「已批准」。 |
| [MCP 身份解析](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/projects/app/src/service/support/mcp/auth.ts) | 工具呼叫需要明確的有效成員及團隊範圍 | Sites 身份以伺服器私人對應表連到實際後台帳戶。UI、MCP、下載與備份共用原生權限，沒有預設管理員回退。 |

FastGPT 的部署範本與執行器服務較多，適合它的產品範圍。藍圖現階段先保留單一資料服務，避免增加使用者的安裝與維護負擔。FastGPT 的[授權](https://github.com/labring/FastGPT/blob/9fd604ab860a5cc566f7450ead9fc64dcbc555f1/LICENSE)含附加條款；本次沒有搬入其程式、圖片、商標或介面，也沒有新增 FastGPT 執行依賴。

## 本次實作

- `GET /api/runtime`：登入後可讀取 API 合約、資料格式及維護能力。產品版本與合約版本分開；沒有 Agent 執行器時明確回報 `not_configured`。
- `integrations/sites/native-gateway.mjs`：Sites 驗證身份後，原版介面、附件、備份與 32 個 MCP 工具通往同一個原生後台。沒有 D1／R2 公司資料副本。
- `tools/build_sites_gateway.py`：從藍圖來源產生單一 Worker，供官方 Sites Worker ESM 流程封裝；不包含秘密或 Site ID。
- 更新助手保留入口與資料後台兩個網址，避免下一次更新又錯誤等待 Sites 的 D1／R2 備份。
- 測試涵蓋真實原生 API 的工具寫入、畫面讀回、附件、角色限制、完整備份下載與隔離還原；平台驗證身份使用隔離 fixture，不把它當作正式 Sites 登入驗收。

這些變更準備了接駁程式，沒有替使用者購買主機、改動舊站、搬遷資料或完成正式發佈。正式安裝依 [Sites 與原生後台接駁](../docs/SITES_NATIVE_GATEWAY.md) 逐項驗收。Agent 的後續範圍、成功條件及未實作部分見 [Agent 架構與客服路線](../docs/AGENT_ROADMAP.md)。
