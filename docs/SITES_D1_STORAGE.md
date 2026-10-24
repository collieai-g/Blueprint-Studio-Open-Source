# Sites D1：分開查閱與保存

[平台 API 規格](STUDIO_API.md) · [Sites 原生入口](SITES_NATIVE_GATEWAY.md)

`integrations/sites/d1-store.mjs` 是供已有 D1 後台使用的共用儲存模組。
它不建立 Site、不提供登入，也不是完整的 Sites 後台。採用原生後台的
安裝不需要這個模組。把檔案加入開源倉庫不會自動更新任何已部署工作台。

## 運作方式

- 純查閱使用 `read(selection, callback)`，不取得寫入鎖、不執行保存。
- 查詢只包含宣告的 namespace 或紀錄 ID；歷史只在明確選取時讀取。
- 身份、權限、當前資料及選定歷史在同一個 D1 `batch` 交易讀取，避免
  分開取得不同版本的資料和權限。大量 ID 會拆成有界查詢，仍使用同一批次。
- callback 取得資料副本。讀取未宣告的紀錄、整個 namespace 或歷史會失敗，
  不會自動補讀其他公司資料。
- `write(selection, callback)` 才取得現有寫入租約。只保存改動紀錄及新增
  歷史，不逐筆比較整個資料庫。紀錄、來源版本、已讀權限及租約會在 SQL
  提交時再次檢查；失敗會回滾同一批次的紀錄和歷史。

## 接駁範例

下面的 `verifiedIdentity`、`requireCompanyMember` 及 `canReadItem` 必須由
實際平台身份驗證及原有後台權限檢查提供。`scope`、`actor`、namespace 和
權限來源由伺服器決定，不接受模型或使用者任意指定公司、身份或角色。

```js
import { createD1Store } from './d1-store.mjs';

const actor = verifiedIdentity.id;
const requestedId = validateItemId(requestedItemId);
const store = createD1Store(env.DB, {
  scope: verifiedIdentity.companyScope,
  actor,
  access: {
    records: [
      { ns: 'users', id: actor },
      { ns: 'acl', id: 'item:' + requestedId }
    ]
  },
  authorize(snapshot, context) {
    const user = snapshot.get('users', context.actor);
    const acl = snapshot.get('acl', 'item:' + requestedId);
    const item = snapshot.get('items', requestedId);
    requireCompanyMember(user, context.scope);
    return canReadItem(user, item, acl, context.mode);
  }
});

const selection = { records: [{ ns: 'items', id: requestedId }] };
const item = await store.read(selection, snapshot => snapshot.get('items', requestedId));

// 修改時仍檢查剛讀到的版本；authorize 必須同時確認寫入權限。
const updated = await store.write(selection, tx =>
  tx.update('items', requestedId, { title: requestedTitle }, item.version)
);
```

權限檢查必須回傳明確的 `true`；沒有設定 `authorize` 或沒有宣告身份及權限
快照，模組會拒絕啟動。讀取歷史時，將 `{ns,id}` 放入 `histories`；模組亦會
讀取當前紀錄。`authorize` 須根據當前紀錄、相關來源和現在的權限決定是否
可查閱歷史，不能沿用建立歷史時的舊權限。當前紀錄不存在時拒絕讀取歷史。

`namespaces: ['items']` 表示此批次確實需要完整的 items namespace。
列表頁應先按平台現有查詢和授權邏輯選出所需 ID，使用 `records` 選取；只有
確實需要整類資料的操作才選整個 namespace。包含關聯來源時，一併宣告來源
及其 ACL，讓所有判斷使用同一份快照。模組不會代替應用程式推斷業務權限。

## 保存及來源保護

`tx.create(ns, value, {sourceKey})` 回傳 `{record, created}`。使用 source key
去重時，須選取整個 namespace，才能在同一快照找回既有紀錄。
`tx.update(ns, id, patch, expectedVersion, {guards})` 回傳更新後的紀錄；
patch 可為物件或同步函式。來源保護使用 `guards: [{ns,id,version}]`，例如
Agent 工作引用的文件。模組會在保存時再次執行來源版本 SQL 條件，即使來源
本身沒有被修改亦一樣。所有修改者及維護程序必須共用寫入租約規格。

`tx.transaction(callback)` 提供同一請求內的暫存回滾，不是另一個資料庫
交易。callback 完成後才原子保存外層變更和歷史。callback 必須等待自己
啟動的工作；不要在回傳後繼續修改交易，也不要在持鎖期間呼叫模型或外部
網絡服務。平台須保留完整非同步保存或釋放租約的生命週期；瀏覽器中斷
不應取消伺服器的清理工作。

沿用以下既有表合約，不重建正式資料：

| 表 | 必要欄位與約束 |
| --- | --- |
| `bp_records` | `scope, ns, id, value, version, source_key`；主鍵 `(scope,ns,id)`；source key 在同 scope／namespace 唯一 |
| `bp_history` | `scope, ns, id, version, value, action, actor, time`；`(scope,ns,id,version)` 唯一 |
| `bp_lease` | `id, token, expires`；`id=1` 為原有全域寫入租約，expires 使用毫秒 |

紀錄 JSON 中的 `id` 和 `version` 必須與表欄位一致。既有表若需要新增索引，
由平台以可回復的資料遷移處理；模組不會自動建立表、重設版本或清除歷史。

## 更新既有後台

1. 先保留完整可還原備份，確認現有表、平台身份與公司隔離方式。
2. 把首次身份／公司初始化、收藏同步、月度提醒等會寫入資料的工作抽成
   明確的保存交易。不要在 `read` callback 內偷偷建立紀錄。
3. 逐條列出真正純讀操作與其身份、ACL、資料依賴，再接入 `read`。
   不能只看 HTTP 方法：MCP 讀取工具亦使用 POST，現有 GET 可能仍有初始化。
4. 保存操作接入 `write`，保留原有角色、引用權限、版本和 Agent 工作驗證。
   備份仍須使用經驗證的一致完整快照；不能把頁面選取結果當作完整備份。
5. 在隔離環境驗證並行查閱、保存衝突、來源權限變動、歷史、首次啟動及還原，
   再部署同一服務。開源倉庫更新與實際測試工作室更新分開核對。

本機驗證使用 Node 24 的 SQLite，執行實際 SQL 及原子交易：

```sh
node --test tests/test_sites_d1_store.mjs
```

這些測試驗證儲存邊界，不能代表已測量正式 D1 網絡耗時或完成平台部署。
