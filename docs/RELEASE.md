# V2.5.1：全新安裝與舊站更新分開處理

之前的安裝入口寫着「發現已有工作台就轉去更新」，可能把已選擇全新安裝的使用者帶回舊 Sites 的備份流程。本版優先遵從使用者的任務選擇：明確另裝原生工作台時，保留舊站不動，另用獨立服務、資料、備份及私人進度。

- **全新安裝：** 使用 [INSTALL_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.1/INSTALL_AI.txt)。即使有舊站，已明確選擇另裝時，也不要求先完成舊站 D1／R2 備份、候選還原或封裝。新站不會自動取得舊資料。
- **更新原站：** 使用 [UPDATE_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.1/UPDATE_AI.txt)。仍須保留原資料並完成可還原備份，不能因遇到阻礙而擅自換成空白新站。
- **搬遷舊資料：** 另外驗證匯出、附件及身份；不能將獨立新安裝說成資料已搬遷。

雙語 README、生成的安裝指令、AGENTS、開始使用、本機／雲端及更新指南已同步。[任務選擇規則](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.1/docs/INSTALL.md#先確定這次任務才讀取進度)說明新舊服務隔離及換對話接續方式。

保留 V2.5.0 的原版介面、schema 5、32 個原生 MCP 工具及備份維護功能。新安裝仍須完成自己的備份下載、還原登入、重啟保存與 AI 寫入驗收。本版沒有部署或改動私人工作台，也沒有新增 Sites 的 D1／R2 備份能力。

下載 `-installer.zip` 作完整安裝；`-workbench.zip` 只是介面更新包。[驗證紀錄](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.1/VALIDATION.md)列明測試與限制。
