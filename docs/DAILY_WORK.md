# 每日工作：說一句，留下有用的內容

[返回首頁](../README.md)

在每天使用的同一個 AI 對話完成工作後，讓 AI 整理值得保留的內容，存進 Blueprint Studio Backend。
工作台會分成 **成果、決定、參考資料、待辦**，方便之後找回。參考網址會加入資料收藏；待辦可以勾選完成。

## 接好 Codex，之後只說一句話

先按[後台安裝說明](LIBRARY.md)初始化公司資料庫。然後在工具目錄執行一次：

```sh
python3 blueprint.py workday-connect "../藍圖工作室 Blueprint Studio" --data "../藍圖公司資料"
```

程式會在私人工作室加入 `WORKDAY.md`、連接設定及一小段 Codex 指引，保留原有公司文件和自訂指引。
使用你實際的工作室及資料路徑；已有自訂同名文件時會拒絕覆寫。

在 Codex 開啟該工作室；必要時重新開啟對話，讓 Codex 讀到新增指引。每天完成工作後說：

> 整理今日工作，存入藍圖。保留成果、重要決定、值得收藏的資料及未完成待辦，略過閒聊和重複內容。

Codex 會整理目前可見、屬於今天的對話，經本機工具寫入公司資料庫。成功後可在「每日工作」查看。
這需要 Codex 可以執行本機指令並存取該私人資料目錄；本機連結使用檔案權限，不需將後台密碼貼到對話。

## Gemini、Claude 或沒有本機工具的 AI

1. 在後台「每日工作」開啟整理功能，複製 AI 指令。
2. 貼到你今天工作的 AI 對話，讓它輸出整理結果。
3. 將結果貼回後台，或匯入 JSON，預覽後可以改內容、調整類型或移除不需要的項目，再保存。

判斷哪些內容值得保存的是你正在使用的 AI。後台不會自行讀取其他平台的聊天，亦沒有在背景呼叫模型。
只保留可見對話有根據的內容，不把未完成工作寫成已完成，也不將整份原始對話、密碼或金鑰直接保存。

## 日期、重送及待辦

- 工作日期使用你的本地日期。同一天可保存不同對話的紀錄。
- 每個對話／工作日使用穩定的 `source_key`，不要只用日期。
- 同一日期、來源及識別碼的相同內容重送會去重；已勾選的待辦保持原狀。
- 同一識別碼但內容不同時會回傳衝突，先查看原紀錄，避免覆寫或誤建副本。
- 儲存前可編輯整理內容；目前儲存後支援修改待辦完成狀態，未提供整份紀錄覆寫或刪除。
- 文字整理不包含原始附件；程式碼或設計原檔仍需另外上傳或保留在自己的專案。

## Codex 可使用的指令

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
