# 公司工作台介面增補與精簡的參考

[介面方案](../docs/COMPANY_INTERFACE_PLAN.md) · [產品定位](../docs/HARNESS_ENGINEERING.md)

查閱日期：2026-10-26。比較目的為日常公司介面的資訊安排、內容增補與刪減；參考不代表藍圖已實作 Zendesk 的功能，也不將客服企業產品的完整流程搬進小型工作台。

## 原碼中的現況

- [navigation.js](../library_web/navigation.js) 已採四個主入口，並將舊專員、客戶、財務與接駁等許多頁面移出日常導航。精簡提案要針對仍可見的重複概念，不把已收起功能再次列為改善成果。
- [studio.js](../library_web/studio.js) 在待辦頁另列 studio task 為「其他待辦」；文件與報告已有相鄰視圖。把來源模型藏在詳情，比新增另一個待辦模組更適合日常操作。
- [studio.py](../blueprint_library/studio.py) 已以同 ID 橋接 collection／topic，並從原 item 產生收件匣及靈感引用。同一原材料可以有不同工作角色，統一展示不等於刪除這些角色與歷史。
- [studio_schema.py](../blueprint_library/studio_schema.py) 的 member 工時包含日期、分鐘及備註，沒有到班／離班時間；project、task 及 workday 已有不同日期欄位。真正打卡需要補持久行為；月曆則可先聚合已有日期。
- [classify.py](../blueprint_library/classify.py) 明確不抓取網址、不呼叫模型；[store.py](../blueprint_library/store.py) 僅對小型 UTF-8 文字／程式檔抽字，未對 PDF、Office、圖片或影片作全面抽取。[資料夾工具](../blueprint_folders/core.py) 也是依已有中繼資料分類。自動標籤不能表達內容已被完整理解。

原碼基線為 `f90c0d47fcdd93a09d70b1cc7482792b7b1259b3`，上述為靜態盤點，並非本次正式工作台驗收。

## Zendesk 官方來源及設計推論

| 已讀官方來源 | 可支持的產品事實 | 對藍圖的設計推論 |
| --- | --- | --- |
| [Creating views](https://support.zendesk.com/hc/en-us/articles/4408888828570-Creating-views-to-build-customized-lists-of-tickets) | 以條件建立工作清單，選擇欄位、分組與排序，並控制適用範圍 | 今日、本週、按項目及月曆可以是同一批原紀錄的視圖，保留原 ID 與權限 |
| [Knowledge content organization](https://support.zendesk.com/hc/en-us/articles/4408845897370-Organizing-knowledge-base-content-in-categories-and-sections) | 知識內容有 category、section、article 結構；單一 category 時可省去該閱讀層 | 資料入口保持淺層，沒有內容或只有一個選項時不強迫多走一頁；主題與權限分開 |
| [Context panel](https://support.zendesk.com/hc/en-us/articles/4408836526362-Using-the-context-panel) | 在當前工作旁提供相關內容與可配置能力 | 清單保持簡短，來源、備註、歷史及次要操作按需要展開 |
| [Custom objects](https://support.zendesk.com/hc/en-us/articles/5914453843994-Understanding-custom-objects) | 業務物件有自己的欄位、權限及紀錄關係 | 先釐清工作、材料和成果的關係，優先沿用已有物件，不立即開發通用物件引擎 |

這些原則支持「同一份資料，多種需要時才出現的查看方式」。它不要求藍圖採用 ticket 為所有工作的模型，也不表示只改前端便能完成跨模型搜尋或日期聚合。

## 打卡與外部月曆的功能邊界

[Starting and ending your day in the Zendesk WFM time tracker](https://support.zendesk.com/hc/en-us/articles/6443354661402-Starting-and-ending-your-day-in-the-Zendesk-WFM-time-tracker) 描述 WFM 的開始／結束工作操作。它是 WFM 功能，不應描述成所有基本工作台預設都有的個人打卡。

[Google Calendar for Zendesk WFM integration](https://support.zendesk.com/hc/en-us/articles/9118977786522-Activating-and-deactivating-the-Google-Calendar-for-Zendesk-WFM-integration) 屬相應整合，啟用後仍有使用者自己的連接步驟。這不能作為藍圖已具備 Google Calendar 同步的證據。

藍圖的建議範圍先限於個人明確到班／離班及現有工作日期呈現，不照搬完整排班、薪酬、背景活動監測或外部同步。一般成果紀錄、截止日與會議時段各有不同意義，介面不能混為一談。

## 設計交付的範圍

對照結果用於[介面增補與精簡方案](../docs/COMPANY_INTERFACE_PLAN.md)及合成互動概念稿，沒有修改公司後台、讀取私人材料或接入任何 Zendesk 帳戶。實際程式開發仍需按提案的資料來源、保存及權限範圍逐批完成。
