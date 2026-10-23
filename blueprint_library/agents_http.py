"""Agent routes behind the workspace's existing identity and write guards."""
from __future__ import annotations

import re

from .agents import Agents
from .store import LibraryError


def handle(handler, method, path, query):
    if not (path == '/api/studio/agents' or path.startswith('/api/studio/agents/') or
            path == '/api/studio/agent-jobs' or path.startswith('/api/studio/agent-jobs/')):
        return False
    service = Agents(handler.server.store, handler.identity)
    def send(value, status=200):
        handler._json(value, status)
        return True
    def pagination():
        try:
            return {'limit': int(query.get('limit', ['50'])[0]), 'offset': int(query.get('offset', ['0'])[0])}
        except (ValueError, TypeError):
            raise LibraryError('分頁範圍不正確。') from None
    if path == '/api/studio/agents':
        if method == 'GET':
            return send(service.list_agents(**pagination()))
        if method == 'POST':
            result = service.register(handler._body())
            return send(result, 201 if result['created'] else 200)
    if path == '/api/studio/agent-jobs':
        if method == 'GET':
            return send(service.list_jobs(agent_id=query.get('agent_id', [''])[0], state=query.get('state', [''])[0], **pagination()))
        if method == 'POST':
            result = service.create_job(handler._body())
            return send(result, 201 if result['created'] else 200)
    match = re.fullmatch(r'/api/studio/(agents|agent-jobs)/([a-zA-Z0-9-]+)(?:/(actions|context|history))?', path)
    if match:
        collection, record_id, part = match.groups()
        if collection == 'agents':
            if method == 'GET' and not part:
                return send({'agent': service.get_agent(record_id)})
            if method == 'GET' and part == 'history':
                return send({'versions': service.history('agent', record_id)})
            if method == 'PATCH' and not part:
                return send({'agent': service.update_agent(record_id, handler._body())})
            if method == 'POST' and part == 'actions':
                return send({'agent': service.agent_action(record_id, handler._body())})
        else:
            if method == 'GET' and not part:
                return send({'job': service.get_job(record_id)})
            if method == 'GET' and part == 'context':
                return send(service.context(record_id))
            if method == 'GET' and part == 'history':
                return send({'versions': service.history('job', record_id)})
            if method == 'POST' and part == 'actions':
                return send({'job': service.job_action(record_id, handler._body())})
    raise LibraryError('找不到此 Agent 功能。', 404)
