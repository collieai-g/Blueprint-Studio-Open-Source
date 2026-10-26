# 首例：原生登入與內置瀏覽器表單相容性

這個案例把真實使用發現的問題，整理成可分享的通用修正與合成回歸。
公開內容不包含工作室資料、實際帳密、私人網址、登入階段或截圖。

## 兩個根因各自記錄

1. **原生 Basic 登入的客戶端相容性觀察。** 一次實測中，Chrome 能登入，
   內置瀏覽器提示立即關閉並回報 `ERR_INVALID_AUTH_CREDENTIALS`。
   這是有特定環境證據的觀察，不能推論所有版本的客戶端均不支援 Basic。
   原始環境及畫面證據只留在私人案例記錄；這個合成 fixture 不假裝重現它。
2. **新增表單接駁層引入的來源政策問題。** 登入頁設定
   `Referrer-Policy: no-referrer`，會令某些 HTML 導覽表單 POST 的
   `Origin` 成為 `null`。同源保護因而在帳密驗證前拒絕操作。
   這不是宣稱原版 v2.9.0 已有表單登入 bug。

修正只讓登入頁使用 `same-origin` 政策，讓瀏覽器送出真正的同源來源。
`null`、缺少或外來 Origin、無效 nonce 仍需拒絕；原版代理保留其原有政策。
參考：[Fetch 的 Origin 規則](https://fetch.spec.whatwg.org/#append-a-request-origin-header)、
[Referrer-Policy 對 Origin 的影響](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy#effect_on_the_origin_header)。

## 回歸分三種證據

| 證據 | 能證明甚麼 |
| --- | --- |
| Python 合成回歸 | 身份、nonce、來源、下載、期限及代理邊界 |
| 真正瀏覽器表單回歸 | 舊政策產生 null-origin 拒絕；修正政策可登入 |
| 私人實例驗收 | 當時指定客戶端、同一工作室及真實登入／保存結果 |

HTTP 測試自行填入正確 Origin，不能證明瀏覽器會送出同樣標頭。
這正是為何本例需要真正瀏覽器檢查；合成通過亦不代表私人站已更新。

## 啟動隔離 fixture

使用 Python 3.12，在本專案根目錄執行。先將 `PRIVATE_STATE` 設為程式與
所有 Git checkout 以外的新私人目錄，再執行：

```sh
python -X utf8 cases/blueprint-login-001/browser_fixture.py --state-dir "$PRIVATE_STATE"
```

Windows PowerShell 可使用已設定的 `$env:PRIVATE_STATE`：

```powershell
python -X utf8 cases/blueprint-login-001/browser_fixture.py --state-dir $env:PRIVATE_STATE
```

fixture 只分配隨機 loopback 連接埠，啟動一個虛構 Basic 後台及兩個登入入口。
`fixture.json` 記錄 broken／fixed URL。所有帳密都是程式內明確標示的虛構值，
沒有公司資料。觀察檔只保存來源類別、狀態碼與計數，不保存表單正文、
Authorization、cookie 或 session token。

## 在目前客戶端執行瀏覽器檢查

1. 先核對客戶端真正可用的 CUA 工具及網站授權。
2. 由 `fixture.json` 的實際 URL 建立 broken、fixed 兩個分頁；不要猜連接埠。
3. 在 CUA JavaScript 中匯入本例的 `browser-check.mjs`，呼叫
   `runBrowserRegression({ brokenTab, fixedTab })`。使用已取得的 Tab；
   模組不自行建立瀏覽器連接、讀取 cookie、使用 CDP 或直接呼叫網絡。
4. 保存回傳的檢查結果及必要截圖到私人證據目錄。加入目前輸入指紋、
   fixture 來源 SHA256、實際客戶端名稱及每份證據的 SHA256，再交給
   harness 的 `attest`。記錄是外部客戶端陳述，不會冒稱 runner 自己控制瀏覽器。

固定版另有一次獨立合成 HTTP probe，確保 `Origin:null` 被拒絕且不觸及後台。
輸出明確標記這個 probe 的機制，與真正瀏覽器導覽結果分開。

停止本次 fixture：

```sh
python -X utf8 cases/blueprint-login-001/browser_fixture.py --state-dir "$PRIVATE_STATE" --stop
```

等候 `fixture.json` 顯示 `stopped`；停止要求只處理本次程式自己建立的伺服器，
不停止其他程序，也不按程序名稱批次關閉服務。

## 上游交接

通用登入參考實作、回歸測試及整合條件見
`proposals/blueprint_native_login/UPSTREAM_HANDOFF.md`。
範本初始狀態為 `candidate_not_submitted`；本次提交的 PR／發行證據另記於實際 operation，不回寫共用案例作為日後所有實例的完成證明。
私人實例的部署狀態另外保存，不隨公開程式包自動變成完成。
