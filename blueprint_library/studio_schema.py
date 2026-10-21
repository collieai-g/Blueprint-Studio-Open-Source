"""Shared V2.4 record definitions. Labels are also used by the original web UI."""
from __future__ import annotations


def field(label, field_type='text', **options):
    return {'label': label, 'type': field_type, **options}


def choice(label, values, **options):
    return field(label, 'select', options=[{'value': key, 'label': name} for key, name in values], **options)


REFERENCE_KINDS = ['item', 'workday', 'todo', 'project', 'inbox', 'command', 'task', 'lead', 'quote', 'customer', 'feedback', 'supplier', 'invoice', 'cost', 'specialist', 'conversation', 'scenario', 'integration', 'topic', 'inspiration', 'report']
STAGES = [('planning', '規劃'), ('development', '開發'), ('testing', '測試'), ('live', '上線'), ('completed', '完成')]
INBOX_STATES = [('pending', '待你處理'), ('processing', 'AI 處理中'), ('processed', 'AI 已處理'), ('decision', '待你決定')]
CATEGORIES = [('web', '網站與排版'), ('components', '介面組件'), ('ai', 'AI 介面'), ('brand', '品牌與宣傳'), ('business', '營運與商業模式'), ('learning', '學習與參考')]
SPECIALIST_STATES = [('unconnected', '未接入'), ('configuring', '設定中'), ('testing', '測試中'), ('live', '上線'), ('attention', '需要留意'), ('paused', '暫停')]
CURRENCIES = [(v, v) for v in ['HKD', 'USD', 'CNY', 'EUR', 'GBP', 'SGD']]
COMMON_CONTACT = {'company': field('公司名稱'), 'contact': field('聯絡人'), 'email': field('電郵', 'email'), 'phone': field('電話'), 'website': field('公開網站', 'url'), 'industry': field('行業')}
SCHEMAS = {
    'project': {'label': '項目', 'fields': {
        'stage': choice('階段', STAGES, default='planning'), 'progress': field('進度百分比', 'integer', min=0, max=100, default=0),
        'owner': field('負責人'), 'due_date': field('預計完成日期', 'date'), 'summary': field('項目說明', 'textarea'),
        'milestones': field('里程碑', 'checklist'), 'specialist_id': field('上線後交給哪位專員', 'reference', kind='specialist')}},
    'inbox': {'label': '收件匣內容', 'fields': {
        'source': field('來源', required=True, default='手動加入'), 'state': choice('處理狀態', INBOX_STATES, default='pending'),
        'content': field('內容', 'textarea'), 'project_id': field('所屬項目', 'reference', kind='project'), 'sender': field('寄件人或來源名稱')}},
    'command': {'label': '指令', 'fields': {
        'instruction': field('想完成甚麼', 'textarea', required=True), 'state': choice('進度', [('awaiting_ai','等待已連接的 AI'),('planned','已拆解任務'),('needs_confirmation','待你確認'),('completed','已完成'),('failed','未能完成')], default='awaiting_ai', readonly=True),
        'specialist_id': field('指定專員', 'reference', kind='specialist'), 'topic_id': field('只使用這個主題的內容', 'reference', kind='topic'),
        'tasks': field('工作步驟', 'checklist'), 'result': field('結果', 'textarea', readonly=True), 'purpose': field('用途', readonly=True)}},
    'task': {'label': '待辦', 'fields': {
        'content': field('說明', 'textarea'), 'owner': field('負責人'), 'due_date': field('到期日期', 'date'),
        'done': field('已完成', 'boolean', default=False), 'project_id': field('所屬項目', 'reference', kind='project')}},
    'lead': {'label': '潛在客戶', 'fields': {**COMMON_CONTACT,
        'size': field('公司規模'), 'needs': field('需要甚麼服務', 'textarea'), 'source_url': field('公開資料來源', 'url'),
        'follow_up': field('下次跟進', 'date'), 'notes': field('跟進紀錄', 'textarea'),
        'state': choice('狀態', [('new','待確認'),('contacted','已聯絡'),('qualified','可跟進'),('converted','已轉成客戶'),('closed','暫不跟進')], default='new')}},
    'quote': {'label': '報價與合約', 'fields': {**COMMON_CONTACT,
        'needs': field('服務需要', 'textarea'),
        'currency': choice('貨幣', CURRENCIES, default='HKD'), 'lines': field('報價項目', 'line_items'), 'amount': field('總額', 'money', readonly=True),
        'expires': field('報價有效至', 'date'), 'terms': field('報價及合約條款', 'textarea'),
        'state': choice('狀態', [('draft','草稿'),('pending','待確認'),('approved','已確認草稿'),('sent','已自行發送'),('signed','已簽署'),('invoiced','已建立發票'),('declined','不採用')], default='draft', readonly=True),
        'signed_date': field('簽署日期', 'date', readonly=True)}},
    'customer': {'label': '客戶檔案', 'fields': {**COMMON_CONTACT, 'needs': field('服務需要', 'textarea'), 'notes': field('客戶備註', 'textarea')}},
    'feedback': {'label': '反饋與客訴', 'fields': {
        'customer_id': field('客戶', 'reference', kind='customer'), 'source': field('來源', default='手動加入'),
        'content': field('內容', 'textarea', required=True), 'owner': field('負責人'),
        'state': choice('狀態', [('new','新收到'),('handling','處理中'),('resolved','已解決')], default='new'),
        'resolution': field('處理方法', 'textarea'), 'resolved_at': field('解決時間', readonly=True)}},
    'member': {'label': '團隊成員', 'restricted': True, 'fields': {
        'email': field('聯絡電郵', 'email'), 'job': field('工作職責'), 'notes': field('人事備註', 'textarea'),
        'username': field('登入名稱', readonly=True), 'work_logs': field('工時紀錄', 'work_logs')}},
    'supplier': {'label': '供應商與合作夥伴', 'fields': {**COMMON_CONTACT, 'payment_terms': field('付款條款', 'textarea'), 'notes': field('合作內容', 'textarea')}},
    'invoice': {'label': '發票', 'restricted': True, 'fields': {
        'customer_id': field('客戶', 'reference', kind='customer'), 'company': field('客戶公司'), 'contact': field('聯絡人'),
        'currency': choice('貨幣', CURRENCIES, default='HKD'), 'amount': field('應收金額', 'money', default='0.00'),
        'paid': field('已收金額', 'money', default='0.00', readonly=True), 'issued': field('發票日期', 'date'), 'due_date': field('付款期限', 'date'),
        'notes': field('備註', 'textarea'), 'payments': field('收款紀錄', 'payments', readonly=True)}},
    'cost': {'label': '雲端與 AI 成本', 'restricted': True, 'fields': {
        'currency': choice('貨幣', CURRENCIES, default='HKD'), 'amount': field('費用', 'money', default='0.00'),
        'date': field('日期', 'date', required=True), 'category': choice('類別', [('server','伺服器'),('model','模型 API'),('plugin','外掛'),('other','其他')], default='model'),
        'integration_id': field('所屬接入', 'reference', kind='integration'), 'specialist_id': field('所屬專員', 'reference', kind='specialist'),
        'source': field('帳單或用量來源'), 'notes': field('備註', 'textarea')}},
    'integration': {'label': '接入設定', 'restricted': True, 'fields': {
        'category': choice('類別', [('model','模型 API'),('connector','外掛與連接器'),('skill','技能庫'),('channel','渠道帳戶')], default='model'),
        'provider': field('服務名稱'), 'endpoint': field('服務網址', 'url'), 'secret_env': field('伺服器憑證設定名稱'),
        'enabled': field('允許使用', 'boolean', default=False), 'currency': choice('費用貨幣', CURRENCIES, default='HKD'),
        'monthly_limit': field('每月上限（0 表示不設）', 'money', default='0.00'), 'notes': field('接入說明', 'textarea'),
        'budget_paused': field('已達用量上限', 'boolean', readonly=True, default=False)}},
    'specialist': {'label': 'AI 專員', 'fields': {
        'role': field('角色', 'textarea'), 'tone': field('語氣'), 'goal': field('目標', 'textarea'),
        'knowledge': field('可以引用的知識', 'textarea'), 'channel_id': field('渠道', 'reference', kind='integration', category='channel'),
        'skill_ids': field('技能', 'references', kind='integration', category='skill'),
        'model_id': field('主要模型', 'reference', kind='integration', category='model'), 'fallback_id': field('後備模型', 'reference', kind='integration', category='model'),
        'rules': field('規則', 'textarea'), 'handoff': field('何時交給真人', 'textarea'),
        'state': choice('狀態', SPECIALIST_STATES, default='unconnected', readonly=True), 'attention': field('需要留意的事', readonly=True)}},
    'conversation': {'label': 'AI 對話紀錄', 'fields': {
        'specialist_id': field('專員', 'reference', kind='specialist', required=True), 'source': field('真實來源', required=True),
        'content': field('對話及結果', 'textarea'), 'outcome': choice('處理結果', [('automatic','自動處理'),('human','需要人手'),('error','出錯')], default='human'),
        'occurred_at': field('發生日期', 'date', required=True), 'test_only': field('只是測試紀錄', 'boolean', default=True)}},
    'scenario': {'label': '情景測試', 'fields': {
        'specialist_id': field('專員', 'reference', kind='specialist', required=True), 'input': field('測試問題', 'textarea', required=True),
        'expected': field('應有的表現', 'textarea'), 'actual': field('實際結果', 'textarea'),
        'source': field('測試工具或來源'), 'result': choice('結果', [('pending','未執行'),('passed','通過'),('failed','未通過')], default='pending')}},
    'topic': {'label': '自訂主題', 'fields': {
        'description': field('主題說明', 'textarea'), 'icon': field('圖示', default='folder'), 'color': field('顏色', 'color', default='#7b8793'),
        'order': field('排序', 'integer', min=0, max=10000, default=0)}},
    'inspiration': {'label': '靈感', 'fields': {
        'item_id': field('原始收藏', 'reference', kind='item', required=True), 'note': field('它好在哪裏', 'textarea'),
        'categories': field('分類', 'choices', options=[{'value': k, 'label': v} for k,v in CATEGORIES]),
        'tags': field('標籤', 'strings'), 'ai_summary': field('AI 摘要', 'textarea'), 'summary_source': field('摘要來源'),
        'screenshot_id': field('截圖檔案', 'reference', kind='item'), 'capture_state': choice('截圖狀態', [('pending','等待截圖工具'),('ready','已有截圖'),('failed','擷取失敗')], default='pending', readonly=True)}},
    'report': {'label': '報告與成果', 'fields': {'content': field('內容', 'textarea', required=True), 'source': field('來源', required=True), 'project_id': field('所屬項目', 'reference', kind='project')}},
}
SPECIALISTS = [('website','網站客服 AI','回答網站訪客、收集查詢'), ('whatsapp','WhatsApp 客服 AI','處理查詢和預約'), ('email','電郵 AI','分類來信、起草回覆'), ('video','宣傳影片 AI','根據品牌和靈感準備影片')]
TEMPLATES = [
    {'id':'booking','title':'預約排程 AI','role':'整理預約要求、提供可選時段','rules':'改動預約前先請使用者確認。'},
    {'id':'social','title':'社交媒體貼文 AI','role':'根據品牌資料準備貼文草稿','rules':'對外發佈前先取得確認。'},
    {'id':'quote','title':'報價助理 AI','role':'根據已確認的價目表準備報價','rules':'不可自行承諾價錢或發送報價。'},
    {'id':'knowledge','title':'內部知識問答 AI','role':'根據獲授權的公司資料回答問題','rules':'沒有資料時清楚說明，不猜測。'},
    {'id':'meeting','title':'會議紀錄 AI','role':'整理提供的會議內容與待辦','rules':'只使用已提供的內容，保留來源。'},
]
