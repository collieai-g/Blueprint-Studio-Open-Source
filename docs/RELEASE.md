# V2.7.1：四個入口，左右協作與 Skill 交接

左側 Codex 理解要求並使用真正可用的工具執行；右側藍圖準備選定材料、私人工作說明並保存成果。專業方法可以用 Skill 重用，例如影片設計不用先建立另一個常駐 AI 服務。

- 側欄保留總覽、工作、AI、資料，收起重複及細分入口；原資料、權限和舊連結保留。
- 閒置及切回視窗不再重建畫面。按「更新資料」查看外部保存，工作台內保存即時同步。
- 修復舊版回退、較慢請求覆蓋新頁面、失效起始連結及保存後不同步等問題。
- AI 的「Skill 與交接」可準備要求、選定素材、編輯可重用規則、複製／下載 SKILL.md 及私人交接單，並將交接單保存為同一後台的報告後讀回，沿用選定來源的權限。
- [左右協作流程](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.7.1/docs/LEFT_RIGHT_WORKFLOW.md)說明畫面控制、工具及回存；[影片 Skill 範本](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.7.1/templates/skills/personal-video/README.md)提供可重用製作方法。

畫面控制只在目前客戶端真正提供並授權時可用。右側可見、Skill 已下載、外掛已安裝、目前工具可用與影片已製作分開驗證。工作台不自動啟動 Codex、安裝外掛、呼叫影片模型或執行背景工作。MCP 是另一條相容路線，原有身份、版本及來源權限檢查保持不變。

本修正版另補齊 Docker 映像內的共用安裝指引檔案，並以容器中的 HTTP 讀回驗證。

本版不新增資料庫 schema；沿用 schema 6 與 40 個共通／42 個原生 MCP 工具。安裝使用完整 installer.zip；workbench.zip 需整合到現有相容後台，不是獨立後台。GitHub 發布不會自動更新既有私人工作台，請沿用更新入口、原網址、資料與可還原備份。
