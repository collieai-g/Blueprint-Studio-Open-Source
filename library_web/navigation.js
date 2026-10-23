/* One definition for sidebar labels, groups, routes and access rules. */
'use strict';
((root)=>{
  const groups=[
    {id:'work',label:'工作項目',icon:'dashboard',open:true,items:[
      ['overview','今日總覽','dashboard','今天要做甚麼，工作進展如何。'],
      ['inbox','收件匣','inbox','集中處理新收到的內容。','inbox'],
      ['command','指揮中樞','automation','寫下指令，交給已連接的 AI 處理。','command'],
      ['projects','項目進度','checklist','查看階段、里程碑及相關成果。','project'],
      ['documents','文件與成果','folder','找回文件、報告和項目交付物。']]},
    {id:'specialists',label:'AI 專員',icon:'chat',open:true,items:[
      ['agents','Agent 工作台','automation','管理獨立 Agent，將工作交給目前使用的 Codex。'],
      ['specialist/specialist-website','網站客服 AI','chat','設定網站查詢的處理方式。','specialist'],
      ['specialist/specialist-whatsapp','WhatsApp 客服 AI','chat','設定查詢及預約的處理方式。','specialist'],
      ['specialist/specialist-email','電郵 AI','inbox','設定來信分類和回覆規則。','specialist'],
      ['specialist/specialist-video','宣傳影片 AI','play','準備影片目標、品牌資料和規則。','specialist'],
      ['templates','從範本新增','plus','選擇一個角色，建立自己的專員。'],
      ['connections','雲端與外部接入','link','登記模型、外掛、技能和渠道。','integration','admin']]},
    {id:'company',label:'公司',icon:'folder',items:[
      {heading:'客戶'},
      ['leads','潛在客戶','search','記錄公開商業資料和跟進進度。','lead'],
      ['quotes','報價與合約','document','由草稿、確認、簽署到建立發票。','quote'],
      ['customers','客戶檔案庫','folder','把聯絡、項目和往來內容放在一起。','customer'],
      ['feedback','反饋與客訴','chat','跟進每則反饋，記下處理方法。','feedback'],
      {heading:'營運'},
      ['team','團隊與權限','settings','管理成員、登入權限和工時。','member','admin'],
      ['suppliers','供應商與夥伴','link','記錄合作內容、合約和付款條款。','supplier'],
      {heading:'財務',access:'admin'},
      ['revenue','營收與發票','finance','查看收入、發票和應收未收。','invoice','admin'],
      ['costs','雲端與 AI 成本','finance','按貨幣、接入和專員核對費用。','cost','admin']]},
    {id:'inspiration',label:'靈感庫',icon:'bookmark',items:[
      ['inspirations','全部靈感','bookmark','收藏有用的想法，連到正在做的工作。','inspiration'],
      ['inspirations/web','網站與排版','web','整頁網站、排版與畫廊。','inspiration'],
      ['inspirations/components','介面組件','grid','卡片、按鈕、表單和導覽。','inspiration'],
      ['inspirations/ai','AI 介面','chat','對話、語音和 AI 工作介面。','inspiration'],
      ['inspirations/brand','品牌與宣傳','star','標誌、配色、字體和宣傳參考。','inspiration'],
      ['inspirations/business','營運與商業模式','automation','定價、流程和商業做法。','inspiration'],
      ['inspirations/learning','學習與參考','play','文章、教學、影片與資料收藏。','inspiration']]},
    {id:'topics',label:'自訂主題',icon:'folder-plus',items:[['topics','全部主題','folder','用連結整理資料，不需要複製。','topic']]}
  ];
  const pages=Object.fromEntries(groups.flatMap(group=>group.items.filter(Array.isArray).map(([route,label,icon,description,kind,access])=>[route,{route,label,icon,description,kind,access,group:group.id}])));
  const aliases={dashboard:'overview',workdays:'overview/workdays',todos:'overview/todos',files:'documents',links:'inspirations/learning',youtube:'inspirations/learning',unfiled:'inbox',pinned:'pinned',archived:'archived'};
  const api={groups,pages,aliases};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.BlueprintNavigation=api;
})(globalThis);
