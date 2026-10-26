# 藍圖 Harness engineering 研究依據

[設計與開發次序](../docs/HARNESS_ENGINEERING.md) · [左右協作](../docs/LEFT_RIGHT_WORKFLOW.md) · [運作分工](../docs/OPERATING_MODEL.md)

藍圖需要研究的工程主體，是人、當前 AI 客戶端、選定材料、正式操作與私人持久成果之間的協作。以下比較官方原始文件及藍圖現有實作，選取可支援這個運作方式的原則。外部範例不是藍圖的依賴，也不代表已安裝其 SDK、模型或 tracing 服務。

研究日期為 2026-10-26。藍圖研究基線為 `41898c987fdc858b4ae6ebb6437bc9565e902c0a`。外部引用固定到實際閱讀的來源 commit；snapshot 的日期不當作文件首次發布日期。MCP 比較採 2025-11-25 規格，不宣稱它永遠是最新版本。

## R1 用功能驗收與明確狀態承接長任務

來源為 Anthropic 官方 `claude-quickstarts`：

- [Autonomous Coding Agent Demo](https://github.com/anthropics/claude-quickstarts/blob/930ad10bb2e21a9ef97fe1a88d2037e18a24465c/autonomous-coding/README.md)
- [Initializer prompt](https://github.com/anthropics/claude-quickstarts/blob/930ad10bb2e21a9ef97fe1a88d2037e18a24465c/autonomous-coding/prompts/initializer_prompt.md)
- [Coding prompt](https://github.com/anthropics/claude-quickstarts/blob/930ad10bb2e21a9ef97fe1a88d2037e18a24465c/autonomous-coding/prompts/coding_prompt.md)

範例將 initializer 與後續 coding agent 分開。前者根據規格建立 `feature_list.json`、啟動方式與結構；後者在 fresh context 先讀功能清單、進度與 Git 歷史，選一項功能處理，驗證後才記錄通過。功能描述、步驟及 `passes` 狀態，讓下一次執行可以重新判斷哪些工作尚未完成。

**藍圖採用的原則：** 將完整左右協作場景變成可驗收的功能單位；每次接續重新核對工作與來源狀態。程式開發進度與產品日常工作進度分開，前者的通用規格與合成測試可入 Git，後者留在私人後台。

**需要調整的地方：** 範例的固定功能配額、自動連續執行、模型 SDK、全面 UI 驗證及全目錄加入 Git，不適合作為藍圖預設要求。藍圖已有 Python、Node、合約與瀏覽器測試，應按風險選擇。驗收規格可以經審閱修訂，不能因範例限制修改功能清單而禁止合法需求變更。

## R2 評估資料與評分器也需要版本和驗證

來源為 OpenAI 官方 `evals`：[Building an eval](https://github.com/openai/evals/blob/8eac7a7de5215c907fbddc30efdaf316913eccdd/docs/build-eval.md)。

文件將 eval 分為 dataset 與 eval class，使用參考答案或 rubric 定義正確方向，並以 split／version 區分評估。模型評分亦需以人提供的 labels 做 meta-evaluation；評估應圍繞一致的用例或失敗模式，並人工抽查。

**藍圖採用的原則：** 一個 eval 需要包含初始工作台狀態、左側要求、允許來源、能力條件、預期產物及副作用、禁止行為與評分規則。比較不同 AI 時固定案例版本及能力條件。資料庫狀態、權限、ID 和去重由程式驗；來源忠實度與需求理解用 rubric 及人工校準。

**適用限制：** 模型評審不能證明保存成功、訊息送達或授權正確。原文件的 CLI 及模型例子不作本專案安裝指令，亦不為採用評估原則新增 OpenAI Evals 依賴。

## R3 對話記憶與工作狀態需要分開

來源為 OpenAI 官方 `openai-agents-python`：

- [Sessions](https://github.com/openai/openai-agents-python/blob/0dc4cf152bff1f50bdc0e41f28808563406545b2/docs/sessions/index.md)
- [Running agents](https://github.com/openai/openai-agents-python/blob/0dc4cf152bff1f50bdc0e41f28808563406545b2/docs/running_agents.md)
- [Human in the loop](https://github.com/openai/openai-agents-python/blob/0dc4cf152bff1f50bdc0e41f28808563406545b2/docs/human_in_the_loop.md)

Session 管理 run 之間的 conversation history；`RunState` 可保存執行狀態並接續暫停的 run。Human-in-the-loop 則保留待處理工具呼叫、interruption 和授權決定，再恢復相應執行。授權沿用具有具體作用域，不能只因工具名稱一樣便跨主體套用。

**藍圖採用的原則：** 左側可以有自己的 session，但接續公司工作以右側保存的工作編號、來源版本、設定快照、實際成果及未完成事項核對。人修改方向後重新對齊；已授權範圍內繼續處理，避免每一步重複詢問。跨 AI 以明確交接處理，不假設可以搬移另一個客戶端的內部 session。

**適用限制：** SDK 的 `RunState` 不等於 Blueprint job contract 或 MCP 標準。藍圖無需引入 SDK 即可採用狀態分離原則；現有 `started_by_client` 只是來源名稱，不是上述執行狀態或已認證租約。

## R4 觀測整項工作並限制收集內容

來源為 OpenAI 官方 Agents SDK：[Tracing](https://github.com/openai/openai-agents-python/blob/0dc4cf152bff1f50bdc0e41f28808563406545b2/docs/tracing.md)。

文件以 trace 表示 workflow，以 span 表示模型、工具、handoff、guardrail 或自訂事件，並可用 trace／group 識別連結多次 run。該 snapshot 的 tracing 預設啟用，`trace_include_sensitive_data` 預設為 true；generation／function span 可能包含輸入輸出，文件提供關閉、自訂 processor 及遮蔽方式。

**藍圖採用的原則：** 以一項協作工作或 operation 連結操作、版本、路徑、狀態及讀回證據。公開自動測試只用合成資料；私人診斷預設最少中繼資料，不自動匯出原始對話或完整工具內容。

**適用限制：** 採用 workflow 觀測概念，不表示啟用該 SDK 的預設上傳或採用其服務。工作引用本身也可能敏感；公開記錄另用合成 ID。內部推理、token、原始截圖及私人網址不作必要觀測欄位。

## R5 多 AI 相容以協商版本與能力為基礎

來源為 MCP 官方 [Lifecycle 規格 2025-11-25](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/c254060614b5a7d5f40ecbab6234e47c8d74cd5a/docs/specification/2025-11-25/basic/lifecycle.mdx)。

這個版本的初始化協商 `protocolVersion`、client／server capabilities 及 implementation details，再進入可操作階段；只使用協商成功的能力。不支持回應版本時不應繼續。HTTP 後續請求帶相應協定版本。規格也討論每個請求的 timeout 及最大等待限制。

**藍圖採用的原則：** 能力矩陣記錄客戶端與版本、傳輸、實際協商協定、當前工具、授權及任務證據。正確降級是服務的一部分，例如缺媒體工具就交付腳本並保留成片待辦。

**適用限制：** 藍圖已有新舊 MCP 互通實作，應按其實際 adapter 行為及測試確認，不把單一版規格全部條文直接當成現況。MCP 初始化不證明 Skill 已讀到、右側分頁可控制或影片可生成；timeout／取消也不證明底層副作用未發生。

## R6 上下文交付與執行工具是不同合約

來源為 MCP 官方同版規格：

- [Tools](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/c254060614b5a7d5f40ecbab6234e47c8d74cd5a/docs/specification/2025-11-25/server/tools.mdx)
- [Resources](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/c254060614b5a7d5f40ecbab6234e47c8d74cd5a/docs/specification/2025-11-25/server/resources.mdx)

Resources 表達由應用提供的上下文，例子包括 UI 選擇、搜尋及篩選。Tools 表達模型可以呼叫的操作，以工具清單發現輸入 schema；可有輸出 schema 及 structured content。協定錯誤與工具執行錯誤是兩層，後者可回 `isError` 及可修正訊息。工具 annotations 也不能一概視為可信。

**藍圖採用的原則：** 右側交接要證明「交付甚麼材料與版本」，左側工具要證明「執行甚麼操作、前置條件、錯誤及保存結果」。提供檔名不等於 AI 已取得全文，返回成功文字不等於成果已持久化。

**適用限制：** 不要求將現有資料工具全面重構成 MCP resources。MCP 本身不提供業務 exactly-once、冪等、來源語意正確或公司角色權限；這些仍由藍圖與外部服務的合約保證及測試。

## R7 傳輸認證與公司資料授權分開核對

來源為 MCP 官方 [Authorization 規格 2025-11-25](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/c254060614b5a7d5f40ecbab6234e47c8d74cd5a/docs/specification/2025-11-25/basic/authorization.mdx)。

該認證規範針對 HTTP 傳輸；stdio 有不同的環境憑證路徑。HTTP server 要驗證 token 的目標資源，不能接受發給其他資源的 token，也不能把收到的 MCP token 原樣傳給 downstream API。Protected Resource Metadata 支持發現授權資訊。

**藍圖採用的原則：** 各客戶端及接駁路線綁定實際身份與 grant；登入後，清單、來源、結果、歷史與修改仍各自檢查業務權限。獨立服務使用 integration 專用憑證，與 owner／admin session 分離。

**適用限制：** 不把 Sites OAuth 套用到所有 native stdio 客戶端，也不以登入成功推斷有權讀全公司。認證路線及 Integration Contract 的實作範圍要按部署核對。

## 藍圖原碼盤點如何支持設計

| 發現 | 基線來源 | 設計取捨 |
| --- | --- | --- |
| 服務已以左右循環表達 | [LEFT_RIGHT_WORKFLOW](../docs/LEFT_RIGHT_WORKFLOW.md)、[OPERATING_MODEL](../docs/OPERATING_MODEL.md) | 以完整工作為工程單位，沿用左側執行及右側持久環境 |
| Skill 與私人要求已有分離 | [skills.js](../library_web/skills.js)、[交接測試](../tests/test_skill_handoff.cjs) | 延伸既有交接，不新增背景模型執行器 |
| 交接中改要求、來源驗證及同 ID 讀回已有瀏覽器覆蓋 | [check_skills_browser](../tests/check_skills_browser.py) | 將既有機械檢查映射至工作場景，補真正 AI 行為 eval |
| 已有固定工作快照及來源摘要 | [agents.py](../blueprint_library/agents.py)、[Agent 測試](../tests/test_agents.py) | 保留 CAS、digest 及客戶端名稱規則；未來跨 AI 接管另立合約 |
| 自報來源名稱只做字串一致比較 | [agents.py](../blueprint_library/agents.py) 的 `start`／`complete` | 不把它稱為 session 認證或執行租約 |
| UI、工具、版面與業務資料有明確分工 | [workspace.js](../library_web/workspace.js)、[presentation-sync.js](../library_web/presentation-sync.js) | 同一後台、保留草稿；版面同步不擴大成業務資料輪詢 |
| 外部常駐服務另有原生限定合約 | [INTEGRATION_GUIDE](../docs/INTEGRATION_GUIDE.md)、[integrations.py](../blueprint_library/integrations.py) | 不與 `connected_client` 混同，不假設所有 Sites 部署支持 |
| CI 已涵蓋多種工程層 | [check.yml](../.github/workflows/check.yml) | 優先補場景對照、語意品質及實際客戶端證據，避免重建一般 CI |

## 研究邊界與後續驗證

以上原碼盤點以基線靜態閱讀為準，外部原則以實際讀取的官方 GitHub 文件為準；沒有將未取得正文的官方博客當作已讀證據。實際 AI 客戶端、私人部署、媒體工具及外部渠道的能力，仍需按設計的 C／D 證據層分別驗收。

最先應驗證的假設是：共用場景、明確持久結果與同一 rubric，能否更快找出左右協作失敗的元件。先記錄目前結果，再以具體修正比較；不由外部範例推斷藍圖已具備長駐執行、自動接管或通用跨平台 session。
