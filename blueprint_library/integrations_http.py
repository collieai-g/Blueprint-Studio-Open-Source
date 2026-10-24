"""Integration Contract 1.0 routes; bearer identities never enter other APIs."""
from __future__ import annotations

import re

from .integrations import Integrations
from .store import LibraryError

PREFIX = '/api/integrations/v1'
ERROR_CODES = {
    400: 'invalid_request', 401: 'unauthorized', 403: 'forbidden',
    404: 'not_found', 409: 'conflict', 410: 'cursor_gap',
    413: 'payload_too_large', 415: 'unsupported_media_type',
    422: 'invalid_data', 429: 'rate_limited',
    500: 'internal_error', 503: 'temporarily_unavailable',
}


def is_integration_path(path):
    return path == PREFIX or path.startswith(PREFIX + '/')


def error_body(message, status, code=None):
    return {'error': str(message), 'code': code or ERROR_CODES.get(status, 'invalid_request'),
            'retryable': status in {429, 500, 503}}


def handle(handler, method, path, query):
    if not is_integration_path(path):
        return False
    service = Integrations(handler.server.store, handler.identity,
                           integration_id=handler.identity.get('integration_id'))

    def send(value, status=200):
        handler._json(value, status)
        return True

    def select(name, default=''):
        values = query.get(name, [default])
        if len(values) != 1:
            raise LibraryError('查詢欄位不可重複。')
        return values[0]

    def pagination():
        try:
            return {'limit': int(select('limit', '50')), 'offset': int(select('offset', '0'))}
        except (ValueError, TypeError):
            raise LibraryError('分頁範圍不正確。') from None

    if path == PREFIX + '/capabilities' and method == 'GET':
        return send(service.capabilities())
    if path == PREFIX + '/registry':
        if method == 'GET':
            return send(service.list_integrations())
        if method == 'POST':
            return send(service.register(handler._body()), 201)
    match = re.fullmatch(re.escape(PREFIX) + r'/registry/([a-zA-Z0-9-]+)(/revoke)?', path)
    if match:
        integration_id, revoke = match.groups()
        if method == 'GET' and not revoke:
            return send({'integration': service.get_integration(integration_id)})
        if method == 'PATCH' and not revoke:
            return send({'integration': service.update_integration(integration_id, handler._body())})
        if method == 'POST' and revoke:
            return send({'integration': service.revoke(integration_id, handler._body())})
    if path == PREFIX + '/records':
        if method == 'GET':
            return send(service.list_records(integration_id=select('integration_id'),
                        record_type=select('record_type'), external_id=select('external_id'),
                        record_id=select('record_id'), **pagination()))
        if method == 'POST':
            result = service.write_record(handler._body())
            return send(result, 201 if result['created'] else 200)
    match = re.fullmatch(re.escape(PREFIX) + r'/records/([a-zA-Z0-9-]+)(/history)?', path)
    if match and method == 'GET':
        return send({'versions': service.history(match[1])} if match[2]
                    else {'record': service.get_record(match[1])})
    if path == PREFIX + '/commands':
        if method == 'GET':
            return send(service.list_commands(integration_id=select('integration_id'), **pagination()))
        if method == 'POST':
            result = service.create_command(handler._body())
            return send(result, 202 if result['created'] else 200)
    match = re.fullmatch(re.escape(PREFIX) + r'/commands/([a-zA-Z0-9-]+)', path)
    if match and method == 'GET':
        return send({'command': service.get_command(match[1])})
    if path == PREFIX + '/command-results' and method == 'POST':
        result = service.command_result(handler._body())
        return send(result, 201 if result['created'] else 200)
    if path == PREFIX + '/events' and method == 'GET':
        try:
            limit = int(select('limit', '50'))
        except ValueError:
            raise LibraryError('分頁範圍不正確。') from None
        return send(service.events(cursor=select('cursor'), limit=limit,
                                   integration_id=select('integration_id')))
    match = re.fullmatch(re.escape(PREFIX) + r'/resources/([a-z_]+)/([a-zA-Z0-9-]+)', path)
    if match and method == 'GET':
        return send(service.read_resource(match[1], match[2]))
    raise LibraryError('找不到此外部服務介面。', 404)
