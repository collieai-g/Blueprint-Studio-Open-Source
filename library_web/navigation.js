/* Four primary entries; existing URLs and access rules remain available. */
'use strict';
((root)=>{
  const groups=[
    {id:'overview',label:'總覽',icon:'dashboard',route:'overview',items:[
      ['overview','總覽','dashboard','查看及安排公司的工作。']]},
    {id:'work',label:'工作',icon:'checklist',route:'projects',items:[
      ['projects','項目','checklist','查看階段、里程碑及相關成果。','project'],
      ['inbox','收件匣','inbox','集中處理新收到的內容。','inbox'],
      ['overview/todos','待辦','checklist','查看及完成接下來要做的事。'],
      ['overview/workdays','工作紀錄','calendar','找回每天的成果、決定和下一步。']]},
    {id:'specialists',label:'AI',icon:'chat',route:'skills',items:[
      ['skills','Skill 與交接','document','準備專業指引與指定素材，交給目前使用的 AI。'],
      ['agents','Agent 工作台','automation','管理獨立 Agent，將工作交給目前使用的 AI。'],
      ['command','工作指令','automation','保存工作要求及處理結果。','command']]},
    {id:'inspiration',label:'資料',icon:'folder',route:'documents',items:[
      ['documents','文件','folder','找回文件、報告和項目交付物。'],
      ['inspirations','收藏','bookmark','保存網站、影片及其他參考資料。','inspiration'],
      ['topics','主題','folder-plus','用連結整理資料，不需要複製。','topic'],
      ['pinned','釘選','star','找回常用的頁面、文件及收藏。'],
      ['archived','已封存','archive','已封存的內容仍可查看及還原。']]}
  ];
  const pages={};
  const add=(group,entries)=>entries.forEach(([route,label,icon,description,kind,access])=>{
    pages[route]={route,label,icon,description,kind,access,group};
  });
  groups.forEach(group=>add(group.id,group.items));

  // Retain records, deep links and permissions without filling the primary menu
  // with preset channels, collection categories and company management modules.
  add('specialists',[
    ['connections','舊接入紀錄','link','查看以往保存的接入設定。','integration','admin'],
    ['specialists','專員設定','chat','集中查看專員的角色、知識及規則。','specialist'],
    ['specialist/specialist-website','網站客服 AI','chat','設定網站查詢的處理方式。','specialist'],
    ['specialist/specialist-whatsapp','WhatsApp 客服 AI','chat','設定查詢及預約的處理方式。','specialist'],
    ['specialist/specialist-email','電郵 AI','inbox','設定來信分類和回覆規則。','specialist'],
    ['specialist/specialist-video','宣傳影片 AI','play','準備影片目標、品牌資料和規則。','specialist'],
    ['templates','從範本新增','plus','選擇一個角色，建立自己的專員。',undefined,'admin']
  ]);
  add('work',[
      ['leads','潛在客戶','search','記錄公開商業資料和跟進進度。','lead'],
      ['quotes','報價與合約','document','由草稿、確認、簽署到建立發票。','quote'],
      ['customers','客戶檔案庫','folder','把聯絡、項目和往來內容放在一起。','customer'],
      ['feedback','反饋與客訴','chat','跟進每則反饋，記下處理方法。','feedback'],
      ['team','團隊與權限','settings','管理成員、登入權限和工時。','member','admin'],
      ['suppliers','供應商與夥伴','link','記錄合作內容、合約和付款條款。','supplier'],
      ['revenue','營收與發票','finance','查看收入、發票和應收未收。','invoice','admin'],
      ['costs','雲端與 AI 成本','finance','按貨幣、接入和專員核對費用。','cost','admin']
  ]);
  add('inspiration',[
      ['reports','報告與交付物','document','找回已保存的報告及工作成果。','report'],
      ['inspirations/web','網站與排版','web','整頁網站、排版與畫廊。','inspiration'],
      ['inspirations/components','介面組件','grid','卡片、按鈕、表單和導覽。','inspiration'],
      ['inspirations/ai','AI 介面','chat','對話、語音和 AI 工作介面。','inspiration'],
      ['inspirations/brand','品牌與宣傳','star','標誌、配色、字體和宣傳參考。','inspiration'],
      ['inspirations/business','營運與商業模式','automation','定價、流程和商業做法。','inspiration'],
      ['inspirations/learning','學習與參考','play','文章、教學、影片與資料收藏。','inspiration']
  ]);
  const aliases={dashboard:'overview',workdays:'overview/workdays',todos:'overview/todos',files:'documents',links:'inspirations/learning',youtube:'inspirations/learning',unfiled:'inbox',pinned:'pinned',archived:'archived'};
  const api={groups,pages,aliases};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.BlueprintNavigation=api;
})(globalThis);
