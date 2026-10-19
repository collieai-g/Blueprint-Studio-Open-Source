# 今日收工：讓工作持續累積

[返回首頁](../README.md)

在每天使用的同一個 AI 對話完成工作後，讓 AI 整理值得保留的內容，存進 Blueprint Studio Backend。
工作台會分成 **成果、決定、參考資料、待辦**，方便之後找回。參考網址會加入資料收藏；待辦可以勾選完成。

## 接好你的 AI，之後只說一句話

先按[後台安裝說明](LIBRARY.md)初始化公司資料庫。然後在工具目錄執行一次：

```sh
python3 blueprint.py workday-connect "../藍圖工作室 Blueprint Studio" --data "../藍圖公司資料"
```

程式會在私人工作室加入 `WORKDAY.md`、連接設定及一小段 AI 指引，保留原有公司文件和自訂指引。
使用你實際的工作室及資料路徑；已有自訂同名文件時會拒絕覆寫。

讓具備工具權限的 AI（例如 Codex、Claude Code、Gemini CLI）讀取該工作室的 `AGENTS.md` 及 `WORKDAY.md`。必要時重新開啟對話。每天完成工作後說：

> 今日收工，幫我整合今日工作，更新公司後台。

AI 會整理目前可見或你提供、屬於今天的對話。連接本機時寫入私人資料庫；連接雲端時經 HTTPS API 寫入同一後台。成功後可在「每日工作」查看。
這需要 AI 有實際工具及資料權限；雲端與本機的首次接駁見[安裝指引](INSTALL.md)。純聊天 AI 可按下節輸出整理結果。

## 沒有工具權限的聊天 AI

1. 在後台「每日工作」開啟整理功能，複製 AI 指令。
2. 貼到你今天工作的 AI 對話，讓它輸出整理結果。
3. 將結果貼回後台，或匯入 JSON，預覽後可以改內容、調整類型或移除不需要的項目，再保存。

判斷哪些內容值得保存的是你正在使用的 AI。後台不會自行讀取其他平台的聊天，亦沒有在背景呼叫模型。
只保留可見對話有根據的內容，不把未完成工作寫成已完成，也不將整份原始對話、密碼或金鑰直接保存。

## 日期、重送及待辦

- 工作日期使用你的本地日期。同一天可保存不同對話的紀錄。
- 每個對話／工作日使用穩定的 `source_key`，不要只用日期。
- 同一日期、來源及識別碼的相同內容重送會去重；已勾選的待辦保持原狀。
- 同一識別碼但內容不同時，先查看原紀錄，再合併及修整同一份紀錄，不改成隨機識別碼。
- 儲存前可編輯；已存紀錄可用「修整紀錄」合併新內容、修正及移除雜訊，保留版本歷史。已完成待辦保留原項目 ID，完成狀態不重設。
- 移除紀錄中的參考項目，不會刪除原有資料收藏或原始檔案。
- 文字整理不包含原始附件；程式碼或設計原檔仍需另外上傳或保留在自己的專案。

## 有工具權限的 AI 可使用的指令

把整理好的 JSON 透過標準輸入送入：

```sh
python3 blueprint.py workday-save --workspace "../藍圖工作室 Blueprint Studio" --input -
```

亦可讀取 JSON 檔案，或明確指定已初始化的 `--data` 目錄：

```sh
python3 blueprint.py workday-save --data "../藍圖公司資料" --input "/私人位置/今日整理.json"
python3 blueprint.py workday-list --workspace "../藍圖工作室 Blueprint Studio" --date 2026-10-19
python3 blueprint.py workday-show 紀錄編號 --workspace "../藍圖工作室 Blueprint Studio"
```

不要把私人整理結果放進公開工具 repository。指令成功回傳紀錄編號後，才算保存完成。

## 整理結果格式

以下只是格式示例，請替換成實際日期、來源及工作內容：

```json
{
  "date": "2026-10-19",
  "source": "Codex",
  "source_key": "my-chat-unique-id:2026-10-19",
  "title": "工作室介面設計",
  "summary": "完成圖示方向，留下下一步工作。",
  "entries": [
    {"type": "outcome", "title": "完成文件夾線條圖示", "content": "保留簡單外框及分類符號。"},
    {"type": "decision", "title": "採用無填色外框", "tags": ["設計方向"]},
    {"type": "reference", "title": "設計參考", "url": "https://example.com/design"},
    {"type": "todo", "title": "檢查手機版圖示", "due_date": "2026-10-20"}
  ]
}
```

每份最多 80 項、200,000 字元，HTTP／CLI JSON 輸入最多 512 KiB；摘要及單項說明最多 8,000 字。
`source` 最多 80 字，`source_key` 最多 200 字，標題最多 240 字。
每項最多 16 個標籤；`due_date` 只適用於待辦，沒有確定日期可省略。

已安全設定 API 連線的工具可改用 `POST /api/workdays`，需要既有的管理員登入、
`Content-Type: application/json` 及 `X-Blueprint-Request: 1`。
`GET /api/workdays` 支援 `q`、`date`、`limit`、`offset`；
`GET /api/todos` 另有 `done=0`（未完成）、`1`（已完成）、`all`。
日期篩選指工作紀錄日期；待辦更新使用 `PATCH /api/todos/{id}`，包含 `done` 及最新 `expected_version`。

## 修整已有工作紀錄

先讀取原紀錄，保留仍有用的項目及其 `id`，合併後續成果。
修整 JSON 包含 `expected_version`、可選 `title`／`summary`，以及完整的新 `entries` 清單。
保留項目帶原 `id`，新增項目省略 `id`；不再需要的項目從清單移除，歷史保留原內容。
日期、來源及來源識別碼保持不變。

```sh
python3 blueprint.py workday-show 紀錄編號 --workspace "../我的 AI 工作室"
python3 blueprint.py workday-revise 紀錄編號 --workspace "../我的 AI 工作室" --input "/私人位置/修整.json"
python3 blueprint.py workday-history 紀錄編號 --workspace "../我的 AI 工作室"
```

修整使用最新報告版本；待辦勾選也會更新報告版本，過期修改回傳 409。
遇到衝突先重新讀取及合併，不覆蓋別處更新。瀏覽器亦可在報告內開啟修整及歷史紀錄。
歷史可查閱及匯出；目前沒有一鍵回復整份舊版本，需按舊內容建立新的修整版本。

雲端綁定沿用相同指令，經 HTTPS API 寫入：

```sh
python3 blueprint.py workday-connect "../我的 AI 工作室" --url "https://你的真實後台網址"
```

預設從安全環境的 `BLUEPRINT_BACKEND_USERNAME`／`BLUEPRINT_BACKEND_PASSWORD` 讀取帳密，
可用 `--username-env`／`--password-env` 指定其他變數名稱。設定檔不保存帳密值。
修整 API 是 `PATCH /api/workdays/{id}`，歷史是 `GET /api/workdays/{id}/history`。
