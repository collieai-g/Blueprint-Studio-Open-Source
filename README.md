<p align="center">
  <a href="https://blueprint-agent-studio.onrender.com/">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="assets/brand/mark-light.svg">
      <img src="assets/brand/mark-ink.svg" width="112" height="112" alt="Blueprint Studio 官方標誌">
    </picture>
  </a>
</p>

<h1 align="center">Blueprint Studio Open Source</h1>

<p align="center"><strong>藍圖工作室開源 · 讓 AI 協助工作，讓公司資料有序保存。</strong></p>

<p align="center">
  <strong>繁體中文</strong> · <a href="README.en.md">English</a>
</p>

<p align="center">
  <a href="https://blueprint-agent-studio.onrender.com/">官方網站</a> ·
  <a href="https://github.com/collieai-g/Blueprint-Studio-Open-Source/releases/latest">下載最新正式版</a> ·
  <a href="#first-install">首次安裝</a> ·
  <a href="#keep-updated">更新工作台</a>
</p>

藍圖工作室是一個為公司日常管理而設的開源 AI 工作台。你向已接通的 AI 說明要做甚麼，AI 就可以把工作成果、待辦、文件及客戶資料放到合適的位置。你在工作台查看、搜尋和跟進，逐步累積公司的工作紀錄。

支援左右分屏的 AI 軟件，可以在左側對話、右側查看工作台；也可以在另一個瀏覽器分頁使用。**AI 負責理解和整理，工作台負責保存和呈現。**

開發中的新路線：[Sites 入口配原生後台](docs/SITES_NATIVE_GATEWAY.md)，讓公司資料與備份獨立管理，保留原版畫面及 AI 工具入口。已提供接駁來源與隔離測試，正式主機及平台接駁仍需安裝驗收。[藍圖客服 Agent 開發路線](docs/AGENT_ROADMAP.md)列出已實作與後續工作；目前沒有自動客服執行器。

## 兩個指令，從第一次安裝用到每次更新

**V2.5：首次安裝就準備好下次更新。** 原生本機／雲端後台會在首次啟動及程式更新前完整備份，測試還原與資料遷移；服務運行時定期備份。擁有人可在更新助手下載包含資料庫、登入及原始附件的私人 ZIP。安裝 AI 會驗證下載、還原登入、重啟保存及下一次部署途徑。[查看完整流程與適用範圍](docs/NATIVE_LIFECYCLE.md)。

**可以，兩個固定入口就能涵蓋首次安裝，以及日後多次更新。** 下方指令不寫死版本號；AI 每次先查核最新正式發行版，再固定本次使用的版本。你不需要每次重新尋找一段升級指令。

AI 會按所用平台完成能執行的操作；需要你本人登入、授權或確認費用時，再帶你做當前一步。能否自動完成，取決於該 AI 軟件實際提供的工具。詳細步驟由 AI 閱讀，不需要你逐份研究。

<a id="first-install"></a>
<a id="貼給你的-ai開始安裝"></a>

### 1. 第一次安裝

開啟一個有安裝或部署工具的 AI 對話，複製以下整段：

```text
請幫我首次安裝 Blueprint Studio Open Source，並用繁體中文逐步帶我完成。
先讀取並遵循最新安裝入口：
https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/INSTALL_AI.txt

核實最新正式版本，固定本次 tag 與 commit。先檢查這個 AI 平台的實際工具，再帶我選本機或雲端；已有選擇便沿用。
請完成原版介面、登入、持久儲存、AI 真正寫入與讀回，並在工作台核對相同結果，再帶我完成第一項工作。需要我操作時，只給當前一步的入口、動作及成功標誌。
私人資料及安裝進度保存在公開倉庫以外。先沿用我已選定的目標：若我明確要求全新安裝並保留舊站，使用獨立服務及資料位置，舊站不動；不要因找到舊站而轉去更新，也不要以舊站備份作為新安裝前置條件。只有我要更新原工作台時，才改按 UPDATE_AI.txt 保留資料更新。
```

本機安裝會在你自己的電腦使用；雲端安裝會提供可以登入的網站。AI 會沿用已有帳戶和資源，再按需要帶你完成設定。

已有舊站但決定另外安裝時，在這段指令加上「全新安裝原生工作台，保留舊站不動」。AI 會使用獨立資料及新服務；舊資料留在舊站，不會自動搬過來。新站安裝仍須驗證自己的備份、還原與 AI 接駁。

<a id="keep-updated"></a>

### 2. 之後每次更新

把以下整段貼給管理你現有工作台的 AI。**每次有新版本，都用同一段。**

```text
請幫我把現有 Blueprint Studio Open Source 工作台更新至最新正式版本。
先讀取並遵循最新更新入口：
https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/main/UPDATE_AI.txt

核實並固定本次目標 tag 與 commit，沿用原網址、登入、資料、附件及自訂內容。尋找已有的私人進度與來源檢查點，直接接續。
先核對完整且可還原的備份，再整合新版、部署原服務、核對舊資料與新版畫面，最後驗證目前 AI 真正寫入及讀回同一筆結果。不要停在只保存程式或下載安裝包。
所有私人來源、資料、設定及備份都保存在公開倉庫以外。缺少必要能力時，說清楚具體缺項與下一步，分開回報網站更新和 AI 接駁的狀態。
```

更新 GitHub 上的程式，不會自動更新你正在使用的工作台；第二段指令會帶 AI 接續到原服務的部署和驗收。已是最新正式版時，先核對版本與運作，不必重裝。更新中途換對話，也沿用原目標及已完成進度。

若 AI 無法讀取 GitHub，從[最新正式發行版](https://github.com/collieai-g/Blueprint-Studio-Open-Source/releases/latest)下載檔名以 `-installer.zip` 結尾的完整安裝包，交給 AI 讀取包內的 `INSTALL_AI.txt` 或 `UPDATE_AI.txt`。需要讀取最新入口但無法連線時，AI 應說明限制；介面包 `-workbench.zip` 只包含介面，不能代替完整後台。

## 一個工作台，看清公司的日常

![藍圖工作台：白色淡格線背景、公司導覽及項目進度](artifacts/studio-v2.4-projects.png)

*原版工作台的項目頁，圖中為測試資料。介面目前使用繁體中文。[查看手機介面](artifacts/studio-v2.4-mobile.png)。*

| 區域 | 用來做甚麼 |
| --- | --- |
| **工作項目** | 查看今天的重點、收件匣、待辦及項目進度，保存文件與工作成果。 |
| **AI 專員** | 設定專員的角色、知識、規則及工作範本。對外回覆或自動執行仍需接入實際服務。 |
| **公司** | 整理潛在客戶、報價、合約、發票、收款、成本、團隊權限及合作夥伴。 |
| **靈感庫** | 收藏網站、YouTube 連結、參考圖片與學習筆記，連到相關工作。 |
| **自訂主題** | 把同一主題的項目、文件和收藏放在一起查看，沿用原資料。 |

白色與淺灰色保留閱讀空間，淡方格讓版面容易對齊。總覽中的文字與卡片可沿方格移動、調整大小和收合；標題、說明及日期使用無框文字。需要更多空間時，可向下增加版面，最多三頁。搜尋、上載和勾選待辦等必要操作仍然保留。

[完整功能與實際完成範圍](docs/V2.4.md) · [個人排版方式](docs/LAYOUT.md)

## 說明工作，讓 AI 幫你整理

接通後，你可以向 AI 說：

> 把剛才確認的工作拆成三項待辦，放到這個項目。

> 把這個參考網址加入靈感庫，並連到網站改版項目。

> 今日收工，整理今天的成果、決定和未完成工作，保存到公司後台。

AI 根據你的要求，整理目前看得到或你提供的內容，透過已授權工具保存，再讀回核對；工作台顯示同一份資料。一般對話不會自動全部存入，AI 也不會自行取得其他平台的私人聊天。

工作台內置 **AI 接駁中心**，帶你核對登入、工具載入、實際寫入和讀回。看得到網頁、裝了外掛，與「目前對話已能寫入」是不同狀態；接駁中心會以真正的測試結果核對。

「今日收工」整理的是工作資料；更新程式版本使用上方第二段指令。

## 可以使用哪些 AI？

本項目以 ChatGPT／Codex 的使用情境為起點，接駁方式不綁定單一模型。是否能安裝和操作，主要取決於**承載模型的軟件及其工具權限**。

| 使用方式 | 需要具備的能力 |
| --- | --- |
| **ChatGPT／Codex** | 安裝時需要程式執行或部署工具；日常寫入需要載入並授權藍圖工具。內置 Sites 的路線還需要完成該平台的後台適配。 |
| **Claude、Gemini 等** | 本項目提供 Claude Code、Gemini CLI 等相容 MCP 客戶端的接駁方式。能否代為安裝，仍視該產品的執行與部署工具而定。 |
| **Grok 及其他 AI** | 如果所用產品支援相容工具接駁，並具備安裝所需能力，可以按同一流程接入；目前未逐一驗證所有產品。 |
| **只有聊天或網頁預覽** | 可以解釋步驟或展示畫面；完整工作台還需要可運行的後台、登入，以及長期保存資料和附件的空間。 |

MCP 是讓 AI 呼叫工作台工具的共同接駁方式。本項目提供 30 個共通 MCP 工具；原生 V2.5 另有兩個擁有人備份工具，共 32 個。支援的客戶端可以連到同一個已授權後台。**有類似 Sites 的功能，是一種可能的部署入口；是否能完整安裝，仍需核對後台、儲存、登入和工具接駁。** 也可以把後台部署在外部主機，再由 AI 連接。

目前提供可直接運行的 Python 本機／雲端後台，以及 Docker、Render、Railway 等部署指引。Sites 提供原版介面、工具合約及後台適配規格，尚無通用的一鍵完整安裝版本。AI 專員自動回覆、網站擷取及影片處理等外部服務，也需要另行接入；下載介面不代表這些服務已在運行。

[AI 接駁說明](docs/AI_CONNECTION.md) · [本機安裝](docs/INSTALL_LOCAL.md) · [雲端安裝](docs/INSTALL_CLOUD.md)

## 你的公司資料，留在自己的工作台

這個公開倉庫只保存可重用的程式與文件。公司資料、附件、帳密、聊天、私人工作台來源及備份，存放在另外的私人位置。

每次更新沿用原工作台，先備份，再整合、部署及核對。需要跨對話保存私人候選程式時，使用另外核實的私人倉庫；來源檢查點與完整資料備份分開保存。[完整更新路線](docs/UPDATE_ROUTE.md)說明從取回程式到正式使用的每一步。

## 文件與參與

| 你想了解 | 文件 |
| --- | --- |
| 安裝及第一次使用 | [開始使用](START_HERE.md) · [完整安裝指南](docs/INSTALL.md) |
| 接通 AI 及保存每日工作 | [接駁中心](docs/CONNECT.md) · [每日工作](docs/DAILY_WORK.md) |
| 更新、備份及回復 | [更新路線](docs/UPDATE_ROUTE.md) · [升級指南](docs/UPGRADE.md) |
| 自行部署或開發 | [後台說明](docs/LIBRARY.md) · [平台適配規格](docs/STUDIO_API.md) · [文件夾工具](docs/USAGE.md) |
| 版本與驗證 | [正式發行版](https://github.com/collieai-g/Blueprint-Studio-Open-Source/releases/latest) · [更新紀錄](CHANGELOG.md) · [驗證紀錄](VALIDATION.md) |

**關於 Blueprint Studio** — 從企業的日常工作出發，把 AI 帶入實際工作。本開源項目提供可自行安裝及擴充的公司工作台；[官方網站](https://blueprint-agent-studio.onrender.com/)介紹品牌與客製化服務方向。首頁沿用官方標誌。

README 提供繁體中文與英文；詳細指南及工作台介面目前以繁體中文為主。

採用 [MIT 授權](LICENSE)。歡迎透過 [Issues](https://github.com/collieai-g/Blueprint-Studio-Open-Source/issues) 或 Pull Request 提出問題與改進；分享範例時請使用測試資料。
