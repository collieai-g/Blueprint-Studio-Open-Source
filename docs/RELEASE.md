# V2.5.0：首次安裝就準備好下一次更新

原生本機／雲端後台新增真正可執行的備份及更新保護。首次啟動先保存資料庫、登入及原始附件，再還原副本核對。安裝 AI 另用 `tools/check_installation.py` 驗證正式備份下載、雜湊、隔離還原與原帳戶登入，並繼續完成重啟保存及 AI 寫入驗收。

程式內容改變時，啟動器先在副本試行遷移及核對舊資料，再更新正式資料。正式遷移失敗或中斷時，回復更新前資料、保留失敗副本並停止該份程式；部署流程再選回原程式或修正版。相同程式重新啟動不會重新初始化。資料鎖防止兩個原生服務同時使用同一目錄。

服務運行期間預設每日備份。擁有人可在「偏好設定 → 更新助手」建立及下載私人完整備份，或透過新增的 `blueprint_backup_status`、`blueprint_backup_create` 維護工具操作。原生版共有 32 個 MCP 工具，Sites 共通合約維持 30 個。保留原版介面、公司功能、淡格線風格及 schema 5。

- **首次安裝：** 下載本頁 `-installer.zip`，讓 AI 讀取 [INSTALL_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.0/INSTALL_AI.txt)。新安裝優先使用可持續運行的原生本機／雲端後台。
- **每次更新：** 沿用 [UPDATE_AI.txt](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.0/UPDATE_AI.txt)，保留原網址、登入、資料與儲存。已有容器若在 `/data` 直接存資料，須沿用原路徑及另設持久備份位置。
- **完整規則：** [安裝、備份及持續更新](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.0/docs/NATIVE_LIFECYCLE.md)。CLI 支援備份、完整性檢查、校驗 ZIP 及還原到新目錄，從不直接蓋過正式資料。

原生備份不等於異地備份；AI 仍須將已驗證副本另存到私人耐久位置。主機部署、授權、瀏覽器排版與目前對話工具載入分開驗收。Sites 的 D1／R2 及發佈仍需平台適配，本版沒有宣稱解決所有平台限制。未修改任何舊私人正式站，也未將私人資料放入開源倉庫。

驗證及執行平台限制見 [VALIDATION.md](https://github.com/collieai-g/Blueprint-Studio-Open-Source/blob/v2.5.0/VALIDATION.md)。
