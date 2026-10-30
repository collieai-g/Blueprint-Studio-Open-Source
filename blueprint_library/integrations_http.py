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


class _IntegrationRoutes:
    def __init__(self, handler, query):
        self.handler = handler
        self.query = query
        self.service = Integrations(handler.server.store, handler.identity,
                                    integration_id=handler.identity.get('integration_id'))

    def send(self, value, status=200):
        self.handler._json(value, status)
        return True

    def select(self, name, default=''):
        values = self.query.get(name, [default])
        if len(values) != 1:
            raise LibraryError('查詢欄位不可重複。')
        return values[0]

    def pagination(self):
        try:
            return {'limit': int(self.select('limit', '50')), 'offset': int(self.select('offset', '0'))}
        except (ValueError, TypeError):
            raise LibraryError('分頁範圍不正確。') from None

    def registry(self, method, path):
        if path == PREFIX + '/capabilities' and method == 'GET':
            return self.send(self.service.capabilities())
        if path == PREFIX + '/registry':
            if method == 'GET':
                return self.send(self.service.list_integrations())
            if method == 'POST':
                return self.send(self.service.register(self.handler._body()), 201)
        match = re.fullmatch(re.escape(PREFIX) + r'/registry/([a-zA-Z0-9-]+)(/revoke)?', path)
        if match:
            integration_id, revoke = match.groups()
            if method == 'GET' and not revoke:
                return self.send({'integration': self.service.get_integration(integration_id)})
            if method == 'PATCH' and not revoke:
                return self.send({'integration': self.service.update_integration(integration_id, self.handler._body())})
            if method == 'POST' and revoke:
                return self.send({'integration': self.service.revoke(integration_id, self.handler._body())})
        return False

    def records(self, method, path):
        if path == PREFIX + '/records':
            if method == 'GET':
                return self.send(self.service.list_records(integration_id=self.select('integration_id'),
                            record_type=self.select('record_type'), external_id=self.select('external_id'),
                            record_id=self.select('record_id'), **self.pagination()))
            if method == 'POST':
                result = self.service.write_record(self.handler._body())
                return self.send(result, 201 if result['created'] else 200)
        match = re.fullmatch(re.escape(PREFIX) + r'/records/([a-zA-Z0-9-]+)(/history)?', path)
        if match and method == 'GET':
            return self.send({'versions': self.service.history(match[1])} if match[2]
                        else {'record': self.service.get_record(match[1])})
        return False

    def commands(self, method, path):
        if path == PREFIX + '/commands':
            if method == 'GET':
                return self.send(self.service.list_commands(integration_id=self.select('integration_id'), **self.pagination()))
            if method == 'POST':
                result = self.service.create_command(self.handler._body())
                return self.send(result, 202 if result['created'] else 200)
        match = re.fullmatch(re.escape(PREFIX) + r'/commands/([a-zA-Z0-9-]+)', path)
        if match and method == 'GET':
            return self.send({'command': self.service.get_command(match[1])})
        if path == PREFIX + '/command-results' and method == 'POST':
            result = self.service.command_result(self.handler._body())
            return self.send(result, 201 if result['created'] else 200)
        return False

    def events_resources(self, method, path):
        if path == PREFIX + '/events' and method == 'GET':
            try:
                limit = int(self.select('limit', '50'))
            except ValueError:
                raise LibraryError('分頁範圍不正確。') from None
            return self.send(self.service.events(cursor=self.select('cursor'), limit=limit,
                                       integration_id=self.select('integration_id')))
        match = re.fullmatch(re.escape(PREFIX) + r'/resources/([a-z_]+)/([a-zA-Z0-9-]+)', path)
        if match and method == 'GET':
            return self.send(self.service.read_resource(match[1], match[2]))
        return False

def handle(handler, method, path, query):
    if not is_integration_path(path):
        return False
    routes = _IntegrationRoutes(handler, query)
    for route in (routes.registry, routes.records, routes.commands, routes.events_resources):
        if route(method, path):
            return True
    raise LibraryError('找不到此外部服務介面。', 404)
