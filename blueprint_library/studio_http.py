"""HTTP routes for the V2.4 workspace, behind the existing identity gate."""
from __future__ import annotations

from html import escape
import json
import re
from urllib.parse import quote

from .studio import Studio
from .store import LibraryError


def handle(handler, method, path, query):
    if not path.startswith('/api/studio/'):
        return False
    studio=Studio(handler.server.store,handler.identity)
    def send(value,status=200):handler._json(value,status);return True
    def body():return handler._body()
    if path=='/api/studio/state' and method=='GET':return send(studio.state())
    if path=='/api/studio/summary' and method=='GET':return send(studio.summary(query.get('month',[''])[0] or None))
    if path=='/api/studio/preferences':
        if method=='GET':return send(studio.preferences())
        if method=='PATCH':return send(studio.save_preferences(body()))
    if path=='/api/studio/users':
        if method=='GET':return send({'users':studio.users()})
        if method=='POST':return send({'user':studio.save_user(body())})
    if path=='/api/studio/templates' and method=='POST':return send(studio.create_specialist(body().get('template_id')),201)
    if path=='/api/studio/financial-report' and method=='POST':return send(studio.financial_report(body().get('month','')),201)
    if path=='/api/studio/records':
        if method=='GET':
            return send(studio.list(kind=query.get('kind',[''])[0],q=query.get('q',[''])[0],archived=query.get('archived',['0'])[0]=='1',limit=int(query.get('limit',['50'])[0]),offset=int(query.get('offset',['0'])[0]),category=query.get('category',[''])[0],relation=query.get('relation',[''])[0]))
        if method=='POST':
            result=studio.create(body());return send(result,201 if result['created'] else 200)
    match=re.fullmatch(r'/api/studio/records/([a-zA-Z0-9-]+)(?:/(history|context|similar|actions|download))?',path)
    if match:
        record_id,part=match.groups()
        if method=='GET':
            if part=='history':return send({'versions':studio.history(record_id)})
            if part=='context':return send(studio.context(record_id))
            if part=='similar':return send(studio.similar(record_id))
            if not part:return send({'record':studio.get(record_id)})
            if part=='download':
                record=studio.get(record_id)
                if record['kind']=='quote':
                    d=record['data'];rows=''.join('<tr><td>'+escape(v['description'])+'</td><td>'+str(v['quantity'])+'</td><td>'+escape(v['unit_price'])+'</td></tr>' for v in d.get('lines',[]))
                    content=f'<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>{escape(record["title"])}</title><h1>{escape(record["title"])}</h1><p>{escape(d.get("company",""))} · {escape(d.get("contact",""))}</p><p>狀態：{escape(d.get("state","draft"))}。此檔案不會自動發送或簽署。</p><table><thead><tr><th>項目</th><th>數量</th><th>單價</th></tr></thead><tbody>{rows}</tbody></table><p>總額：{escape(d.get("currency","HKD"))} {escape(d.get("amount","0.00"))}</p><p>有效至：{escape(d.get("expires",""))}</p><pre>{escape(d.get("terms",""))}</pre></html>'
                    data=content.encode();mime='text/html; charset=utf-8';filename='blueprint-quote.html'
                else:
                    data=('# '+record['title']+'\n\n'+record['data'].get('content',json.dumps(record['data'],ensure_ascii=False,indent=2))).encode();mime='text/markdown; charset=utf-8';filename='blueprint-result.md'
                handler._headers(200,mime,len(data),{'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename)});handler.wfile.write(data);return True
        if method=='PATCH' and not part:return send({'record':studio.update(record_id,body())})
        if method=='POST' and part=='actions':return send(studio.action(record_id,body()))
    match=re.fullmatch(r'/api/studio/media/([a-zA-Z0-9-]+)',path)
    if match and method=='GET':
        item_id=match.group(1)
        if not studio.resource_allowed('item',item_id):raise LibraryError('你的帳戶沒有這項權限。',403)
        file,name,mime=studio.store.get_file(item_id)
        if mime not in {'image/png','image/jpeg','image/webp'}:raise LibraryError('這個檔案不是可預覽的截圖。')
        data=file.read_bytes();handler._headers(200,mime,len(data));handler.wfile.write(data);return True
    raise LibraryError('找不到此工作台功能。',404)
