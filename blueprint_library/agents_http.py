"""Agent routes behind the workspace's existing identity and write guards."""
from __future__ import annotations

import re

from .agents import Agents
from .store import LibraryError


class _AgentRoutes:
    def __init__(self, handler, query):
        self.handler = handler
        self.query = query
        self.service = Agents(handler.server.store, handler.identity)

    def send(self, value, status=200):
        self.handler._json(value, status)
        return True

    def pagination(self):
        try:
            return {'limit': int(self.query.get('limit', ['50'])[0]),
                    'offset': int(self.query.get('offset', ['0'])[0])}
        except (ValueError, TypeError):
            raise LibraryError('分頁範圍不正確。') from None

    def collection(self, method, collection):
        service = self.service
        if method == 'GET':
            if collection == 'agents':
                return self.send(service.list_agents(**self.pagination()))
            return self.send(service.list_jobs(agent_id=self.query.get('agent_id', [''])[0],
                state=self.query.get('state', [''])[0], **self.pagination()))
        if method == 'POST':
            create = service.register if collection == 'agents' else service.create_job
            result = create(self.handler._body())
            return self.send(result, 201 if result['created'] else 200)
        return False

    def agent(self, method, record_id, part):
        service = self.service
        reads = {None: lambda: {'agent': service.get_agent(record_id)},
                 'history': lambda: {'versions': service.history('agent', record_id)}}
        if method == 'GET' and part in reads:
            return self.send(reads[part]())
        if method == 'PATCH' and not part:
            return self.send({'agent': service.update_agent(record_id, self.handler._body())})
        if method == 'POST' and part == 'actions':
            return self.send({'agent': service.agent_action(record_id, self.handler._body())})
        return False

    def job(self, method, record_id, part):
        service = self.service
        reads = {None: lambda: {'job': service.get_job(record_id)},
                 'context': lambda: service.context(record_id),
                 'history': lambda: {'versions': service.history('job', record_id)}}
        if method == 'GET' and part in reads:
            return self.send(reads[part]())
        if method == 'POST' and part == 'actions':
            return self.send({'job': service.job_action(record_id, self.handler._body())})
        return False


def handle(handler, method, path, query):
    prefixes = ('/api/studio/agents', '/api/studio/agent-jobs')
    if not any(path == prefix or path.startswith(prefix + '/') for prefix in prefixes):
        return False
    routes = _AgentRoutes(handler, query)
    if path in prefixes:
        if routes.collection(method, path.rsplit('/', 1)[1]):
            return True
    match = re.fullmatch(r'/api/studio/(agents|agent-jobs)/([a-zA-Z0-9-]+)(?:/(actions|context|history))?', path)
    if match:
        collection, record_id, part = match.groups()
        route = routes.agent if collection == 'agents' else routes.job
        if route(method, record_id, part):
            return True
    raise LibraryError('找不到此 Agent 功能。', 404)
