"""Calendar routes behind the existing authenticated Studio HTTP entrypoint."""
from __future__ import annotations

import re
from urllib.parse import quote

from .calendar import Calendar
from .store import LibraryError

PREFIX = '/api/studio/calendar'


def handle(handler, method, path, query):
    if path != PREFIX and not path.startswith(PREFIX + '/'):
        return False
    service = Calendar(handler.server.store, handler.identity)
    expected_account = handler.headers.get('X-Blueprint-Account')
    # This startup-account assertion only rejects stale browser tabs; it never
    # establishes identity. MCP clients may omit it and use normal authentication.
    if expected_account is not None and expected_account != quote(service.actor, safe="~()*!.'-_"):
        raise LibraryError('登入帳戶已改變，請重新載入工作台後再操作日曆。', 403)

    def send(value, status=200):
        handler._json(value, status)
        return True

    def parameters(allowed, required=()):
        if set(query) - set(allowed) or set(required) - set(query) or any(len(values) != 1 for values in query.values()):
            raise LibraryError('日曆查詢參數不正確。')
        return {key: values[0] for key, values in query.items()}

    if method == 'GET' and path == PREFIX + '/meta':
        parameters(())
        return send(service.meta())
    if method == 'GET' and path == PREFIX + '/month':
        values = parameters({'month', 'employee_id', 'employment_type'}, {'month'})
        return send(service.month(**values))
    if method == 'GET' and path == PREFIX + '/history':
        values = parameters({'kind', 'id'}, {'kind', 'id'})
        return send(service.history(values['kind'], values['id']))
    parameters(())
    if path == PREFIX + '/settings' and method == 'PATCH':
        return send(service.save_settings(handler._body()))
    if path == PREFIX + '/employees' and method == 'POST':
        result = service.create_employee(handler._body())
        return send(result, 201 if result['created'] else 200)
    if path == PREFIX + '/entries' and method == 'POST':
        result = service.create_entry(handler._body())
        return send(result, 201 if result['created'] else 200)
    if path == PREFIX + '/attendance/manual' and method == 'POST':
        result = service.manual_attendance(handler._body())
        return send(result, 201 if result['created'] else 200)
    if path == PREFIX + '/attendance' and method == 'POST':
        result = service.attendance_action(handler._body())
        return send(result, 201 if result['created'] else 200)
    match = re.fullmatch(re.escape(PREFIX) + r'/(employees|entries|attendance)/([a-zA-Z0-9-]{1,80})', path)
    if match:
        collection, identifier = match.groups()
        noun = {'employees': 'employee', 'entries': 'entry', 'attendance': 'attendance'}[collection]
        if method == 'GET':
            return send(getattr(service, 'get_' + noun)(identifier))
        if method == 'PATCH':
            return send(getattr(service, 'update_' + noun)(identifier, handler._body()))
    raise LibraryError('找不到這項日曆功能。', 404)
