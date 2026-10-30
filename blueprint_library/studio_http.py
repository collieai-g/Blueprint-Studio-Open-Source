"""Workspace HTTP dispatch, behind the server's existing identity/write gates."""
from __future__ import annotations

from html import escape
import json
import re
from urllib.parse import quote

from .studio import Studio
from .store import LibraryError


def _record_download(record):
    if record['kind']=='quote':
        d=record['data'];rows=''.join('<tr><td>'+escape(v['description'])+'</td><td>'+str(v['quantity'])+'</td><td>'+escape(v['unit_price'])+'</td></tr>' for v in d.get('lines',[]))
        content=f'<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>{escape(record["title"])}</title><h1>{escape(record["title"])}</h1><p>{escape(d.get("company",""))} · {escape(d.get("contact",""))}</p><p>狀態：{escape(d.get("state","draft"))}。此檔案不會自動發送或簽署。</p><table><thead><tr><th>項目</th><th>數量</th><th>單價</th></tr></thead><tbody>{rows}</tbody></table><p>總額：{escape(d.get("currency","HKD"))} {escape(d.get("amount","0.00"))}</p><p>有效至：{escape(d.get("expires",""))}</p><pre>{escape(d.get("terms",""))}</pre></html>'
        data=content.encode();mime='text/html; charset=utf-8';filename='blueprint-quote.html'
    else:
        data=('# '+record['title']+'\n\n'+record['data'].get('content',json.dumps(record['data'],ensure_ascii=False,indent=2))).encode();mime='text/markdown; charset=utf-8';filename='blueprint-result.md'
    return data, mime, filename


class _StudioRoutes:
    def __init__(self, handler, studio, query):
        self.handler = handler
        self.studio = studio
        self.query = query

    def send(self, value, status=200):
        self.handler._json(value, status)
        return True

    def presentation(self, method):
        expected = self.handler.headers.get('X-Blueprint-Account')
        if expected is not None and expected != quote(self.studio.actor, safe="~()*!.'-_"):
            raise LibraryError('登入帳戶已改變，請重新載入工作台後再同步版面。', 403)
        query = self.query
        if set(query) - {'key', 'since_version'} or any(len(values) != 1 for values in query.values()):
            raise LibraryError('版面查詢參數不正確。')
        if method == 'PATCH':
            return self.send(self.studio.save_presentation(self.handler._body()))
        if method == 'GET':
            since = query.get('since_version', [None])[0]
            if since is not None:
                if not re.fullmatch(r'[0-9]{1,16}', since):
                    raise LibraryError('版面版本需要有效的非負整數。')
                since = int(since)
            return self.send(self.studio.presentation(key=query.get('key', [None])[0], since_version=since))
        return False

    def collection(self, method, path):
        studio, query = self.studio, self.query
        reads = {
            '/api/studio/state': studio.state,
            '/api/studio/summary': lambda: studio.summary(query.get('month', [''])[0] or None),
            '/api/studio/preferences': studio.preferences,
            '/api/studio/users': lambda: {'users': studio.users()},
            '/api/studio/records': self.list_records,
        }
        if method == 'GET' and path in reads:
            return self.send(reads[path]())
        if method == 'PATCH' and path == '/api/studio/preferences':
            return self.send(studio.save_preferences(self.handler._body()))
        if method != 'POST':
            return False
        if path == '/api/studio/users':
            return self.send({'user': studio.save_user(self.handler._body())})
        if path == '/api/studio/templates':
            return self.send(studio.create_specialist(self.handler._body().get('template_id')), 201)
        if path == '/api/studio/financial-report':
            return self.send(studio.financial_report(self.handler._body().get('month', '')), 201)
        if path == '/api/studio/records':
            result = studio.create(self.handler._body())
            return self.send(result, 201 if result['created'] else 200)
        return False

    def list_records(self):
        query = self.query
        return self.studio.list(kind=query.get('kind', [''])[0], q=query.get('q', [''])[0],
            archived=query.get('archived', ['0'])[0] == '1', limit=int(query.get('limit', ['50'])[0]),
            offset=int(query.get('offset', ['0'])[0]), category=query.get('category', [''])[0],
            relation=query.get('relation', [''])[0])

    def record(self, method, record_id, part):
        studio = self.studio
        reads = {
            None: lambda: {'record': studio.get(record_id)},
            'history': lambda: {'versions': studio.history(record_id)},
            'context': lambda: studio.context(record_id),
            'similar': lambda: studio.similar(record_id),
        }
        if method == 'GET' and part in reads:
            return self.send(reads[part]())
        if method == 'GET' and part == 'download':
            data, mime, filename = _record_download(studio.get(record_id))
            self.handler._headers(200, mime, len(data),
                {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(filename)})
            self.handler.wfile.write(data)
            return True
        if method == 'PATCH' and not part:
            return self.send({'record': studio.update(record_id, self.handler._body())})
        if method == 'POST' and part == 'actions':
            return self.send(studio.action(record_id, self.handler._body()))
        return False

    def media(self, item_id):
        if not self.studio.resource_allowed('item', item_id):
            raise LibraryError('你的帳戶沒有這項權限。', 403)
        file, name, mime = self.studio.store.get_file(item_id)
        if mime not in {'image/png', 'image/jpeg', 'image/webp'}:
            raise LibraryError('這個檔案不是可預覽的截圖。')
        data = file.read_bytes()
        self.handler._headers(200, mime, len(data))
        self.handler.wfile.write(data)
        return True


def handle(handler, method, path, query):
    if not path.startswith('/api/studio/'):
        return False
    from .calendar_http import handle as calendar_route
    from .agents_http import handle as agent_route
    if calendar_route(handler, method, path, query) or agent_route(handler, method, path, query):
        return True
    routes = _StudioRoutes(handler, Studio(handler.server.store, handler.identity), query)
    if path == '/api/studio/presentation':
        if routes.presentation(method):
            return True
    elif routes.collection(method, path):
        return True
    match = re.fullmatch(r'/api/studio/records/([a-zA-Z0-9-]+)(?:/(history|context|similar|actions|download))?', path)
    if match and routes.record(method, *match.groups()):
        return True
    match = re.fullmatch(r'/api/studio/media/([a-zA-Z0-9-]+)', path)
    if match and method == 'GET':
        return routes.media(match.group(1))
    raise LibraryError('找不到此工作台功能。', 404)
