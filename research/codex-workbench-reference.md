# Codex 專用工作台的產品參考

[產品定位與開發指示](../docs/HARNESS_ENGINEERING.md) · [左右協作流程](../docs/LEFT_RIGHT_WORKFLOW.md)

藍圖的產品選擇，是以 Codex 作主要理解與執行環境，將自身開發集中在企業材料、專業做法、工作物件、介面與成果的持續管理。本筆記使用 Zendesk 官方文件，研究成熟企業工作台如何組織工作；具體運用方式是對藍圖的設計推論，不表示兩個產品已整合。

查閱日期：2026-10-26。功能及配置可能隨方案、管理員設定與產品更新而異。本文不比較模型排名，也不推斷 Zendesk 的基礎模型來源。

## 1 以正在處理的工作為中心

[About the Zendesk Agent Workspace](https://support.zendesk.com/hc/en-us/articles/4408821259930-About-the-Zendesk-Agent-Workspace) 描述在同一 ticket 介面處理多渠道對話，並結合客戶歷史、知識與相關工作能力。

可借鑑的原則是：讓使用者處理一件事時，取得與這件事相關的材料和操作。藍圖可把「一段影片」或「一個品牌企劃」作為工作對象，再連接其材料、要求及成果，不必將所有專業工作強行轉成客服 ticket。

## 2 介面提供本次工作所需上下文

[Using the context panel](https://support.zendesk.com/hc/en-us/articles/4408836526362-Using-the-context-panel) 說明相關歷史、物件、知識、協作、審批、任務與 apps 可在 context panel 呈現；可用內容取決於配置。

對藍圖的啟發是按當前工作提供材料、狀態及適用操作。影片工作需要看素材與成品，研究工作需要看來源與結論；共用底層可以維持一致，具體畫面則按工作形成。右側選定的資料須經明確交接或授權讀取，才能成為左側 Codex 的本次上下文。

## 3 先定義業務物件和關係

[Understanding custom objects](https://support.zendesk.com/hc/en-us/articles/5914453843994-Understanding-custom-objects) 介紹自訂物件的 schema、欄位、權限與 records，可用來表達產品、合約、資產或事件等業務資料。定義關係與關聯具體紀錄是不同工作。

對藍圖而言，應先回答素材屬於哪個項目、哪份要求產生哪個成果、使用者目前選定哪個版本，再設計畫面。可先沿用已有文件、項目、報告與工作紀錄；這個原則不要求立即新增通用物件引擎。

## 4 分開工作條件 知識 方法和操作

[Turning on and configuring auto assist](https://support.zendesk.com/hc/en-us/articles/8013454025114-Turning-on-and-configuring-auto-assist) 分別配置使用群組、適用工作條件、知識來源，以及 procedures 和 actions。

藍圖可借用這種分工：本次要求描述要做甚麼，選定資料提供事實，Skill 提供專業方法，工具提供實際能力。Codex 將它們組合成當次工作，藍圖承接材料及成果。新增專業模式時先確定缺少哪一部分，再決定需要新欄位、新 Skill、工具接駁還是產品程式。

## 5 參考的範圍

[Zendesk demo 頁](https://www.zendesk.hk/demo/) 可作產品示範入口；本次可讀內容主要是示範及試用連結，未能讀取嵌入式互動示範的畫面。上述具體比較以已讀取的官方支援文件為依據。

Zendesk 的文件本身包含 AI、知識、應用及可配置流程，不能由「企業客服軟件」一個名稱推斷其全部能力。藍圖選擇的設計焦點，是保留 Codex 原有的主要工作對話和執行環境，另外提供可開源、可持續累積公司資產的工作台。這是產品分工選擇，不是功能排名結論。

本版把上述原則用於產品與介面設計。自動檢查、測試與 AI 行為評估的研究另見[下一版工程候選](../docs/HARNESS_VALIDATION_NEXT.md)。
