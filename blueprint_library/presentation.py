"""Versioned, account-owned presentation documents shared by HTTP and AI clients.

These documents contain layout geometry, display aliases and stable references,
never a second copy of company records. They live inside the existing account
preferences row so full backups include them without a database migration.
"""
from __future__ import annotations

import json
import re

from .store import LibraryError


MAX_DOCUMENTS = 256
MAX_DOCUMENT_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_VERSION = 9007199254740991
RESERVED = '_presentation'
UNSAFE = {'__proto__', 'constructor', 'prototype'}
CORE_WIDGETS = {'heading', 'description', 'date', 'files', 'links', 'youtube',
                'unfiled', 'workdays', 'todos', 'recent', 'projects', 'inbox',
                'ai-agents', 'ai-skills', 'commands'}
PIN_KINDS = {'studio_record', 'agent', 'agent_job', 'skill', 'workday', 'item'}
PIN_ROUTES = {'projects', 'inbox', 'agents', 'skills', 'command'}
COORDINATES = {'x': (0, 95), 'y': (0, 143), 'w': (1, 96), 'h': (1, 48)}


def _error(message='版面設定格式不正確。', status=400):
    raise LibraryError(message, status)


def _object(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        _error()


def _integer(value, minimum=0, maximum=MAX_VERSION):
    if type(value) is not int or not minimum <= value <= maximum:
        _error('版面設定需要有效的整數及範圍。')


def _text(value, maximum):
    if not isinstance(value, str) or len(value) > maximum or '\x00' in value:
        _error('顯示文字超出長度或格式限制。')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        _error('顯示文字包含無效字元。')


def _safe_id(value, maximum):
    return (isinstance(value, str) and len(value) <= maximum and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:_.\/-]*', value) is not None and
            not set(re.split(r'[:./-]', value)).intersection(UNSAFE))


def validate_key(value):
    if value in ('overview', 'pins'):
        return value
    if isinstance(value, str) and value.startswith('page:') and _safe_id(value[5:], 300):
        return value
    _error('找不到這個版面識別碼。')


def _widget_id(value):
    return isinstance(value, str) and (value in CORE_WIDGETS or re.fullmatch(
        r'pin-(?:studio_record|agent|agent_job|skill|workday|item)-[A-Za-z0-9_.-]{1,160}', value))


def _pin(ref):
    if not isinstance(ref, dict):
        _error('總覽卡片來源格式不正確。')
    if ref.get('kind') == 'route':
        if (set(ref) != {'kind', 'route'} or not isinstance(ref.get('route'), str) or
                ref['route'] not in PIN_ROUTES):
            _error('總覽卡片來源格式不正確。')
    elif (set(ref) != {'kind', 'id'} or not isinstance(ref.get('kind'), str) or
          ref['kind'] not in PIN_KINDS or not isinstance(ref.get('id'), str) or
          not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', ref['id']) or
          ref['kind'] == 'skill' and ref['id'] not in {'video', 'general'}):
        _error('總覽卡片來源格式不正確。')


def _encoded(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        _error()


def _validate_pins(data):
    _object(data, {'version', 'refs'})
    refs = data.get('refs', [])
    if not isinstance(refs, list) or len(refs) > 24:
        _error('最多加入 24 張跨頁卡片。')
    seen = set()
    for ref in refs:
        _pin(ref)
        marker = (ref['kind'], ref.get('id', ref.get('route')))
        if marker in seen:
            _error('總覽卡片來源不能重複。')
        seen.add(marker)


def _validate_overview(data):
    if 'layoutVersion' in data:
        _integer(data['layoutVersion'], 4, 4)
    if 'mode' in data and data['mode'] not in ('ai', 'manual'):
        _error()
    if 'visible' in data:
        visible = data['visible']
        if not isinstance(visible, list) or len(visible) > 40 or any((not _widget_id(value) for value in visible)) or (len(set(visible)) != len(visible)):
            _error('總覽顯示項目不正確。')
    if 'texts' in data:
        _object(data['texts'], {'heading', 'description', 'date'})
        for text in data['texts'].values():
            _text(text, 2000)


def _validate_card(card, overview):
    _object(card, set(COORDINATES) | {'collapsed'} | (set() if overview else {'visible', 'text'}))
    for name, (minimum, maximum) in COORDINATES.items():
        if name in card:
            _integer(card[name], minimum, maximum)
    for name in ('visible', 'collapsed'):
        if name in card and type(card[name]) is not bool:
            _error()
    if 'text' in card:
        _text(card['text'], 4000)


def _validate_cards(cards, overview):
    if not isinstance(cards, dict) or len(cards) > (40 if overview else 200):
        _error('版面卡片超出數量限制。')
    for card_id, card in cards.items():
        if not (_widget_id(card_id) if overview else _safe_id(card_id, 240)):
            _error('版面卡片識別碼不正確。')
        _validate_card(card, overview)


def _validate_layout(key, data):
    overview = key == 'overview'
    allowed = {'version', 'pages', 'columns', 'cards'} | ({'layoutVersion', 'visible', 'mode', 'texts'} if overview else {'active'})
    _object(data, allowed)
    if 'pages' in data:
        _integer(data['pages'], 1, 3)
    if 'columns' in data:
        _integer(data['columns'], 6, 96)
    if overview:
        _validate_overview(data)
    elif 'active' in data and type(data['active']) is not bool:
        _error()
    _validate_cards(data.get('cards', {}), overview)


def validate_document(key, data):
    """Validate known fields without inserting defaults or changing literal text.

        A document replaces its previous value. Optional fields permit a client to
        reset to UI defaults; callers preserving other settings must read and merge.
        """
    validate_key(key)
    if key == 'pins':
        _validate_pins(data)
    else:
        _validate_layout(key, data)
    if 'version' in data:
        _integer(data['version'], 1, 1)
    if len(_encoded(data).encode('utf-8')) > MAX_DOCUMENT_BYTES:
        _error('本頁版面設定超出儲存上限。', 413)
    return data



def _read(conn, username):
    row = conn.execute('SELECT version,data FROM studio_preferences WHERE username=?', (username,)).fetchone()
    preferences = json.loads(row['data']) if row else None
    state = preferences.get(RESERVED) if preferences else None
    return row, preferences, state or {'version': 0, 'documents': {}}


def read(studio, *, key=None, since_version=None):
    if key is not None:
        validate_key(key)
    if since_version is not None:
        _integer(since_version)
    with studio.store._connection() as conn:
        _, _, state = _read(conn, studio.actor)
    if key is None and since_version == state['version']:
        return {'version': state['version'], 'unchanged': True}
    documents = state['documents']
    return {'version': state['version'], 'documents': documents if key is None else (
        {key: documents[key]} if key in documents else {})}


def _authorize_pin(studio, conn, ref):
    kind = ref['kind']
    if kind in {'route', 'skill'}:
        return
    if kind == 'studio_record':
        studio._get(conn, ref['id'])
    elif kind in {'item', 'workday'}:
        studio._reference(conn, ref)
    else:
        from .agents import Agents
        agents = Agents(studio.store, studio.identity)
        if kind == 'agent':
            agents._get_agent(conn, ref['id'])
        else:
            agents._get_job(conn, ref['id'])


def save(studio, payload):
    _object(payload, {'key', 'expected_version', 'data'})
    if set(payload) != {'key', 'expected_version', 'data'}:
        _error()
    key = validate_key(payload['key'])
    _integer(payload['expected_version'])
    data = validate_document(key, payload['data'])
    from .studio import default_preferences
    with studio.store._connection() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row, preferences, state = _read(conn, studio.actor)
        previous = state['documents'].get(key)
        version = previous['version'] if previous else 0
        if payload['expected_version'] != version:
            _error('版面已有其他更新，請先讀取並合併你的修改。', 409)
        if key == 'pins':
            for ref in data.get('refs', []):
                _authorize_pin(studio, conn, ref)
        if previous and previous['data'] == data:
            return {'version': state['version'], 'documents': {key: previous}}
        document = {'version': version + 1, 'data': data}
        state = {'version': state['version'] + 1, 'documents': {**state['documents'], key: document}}
        if len(state['documents']) > MAX_DOCUMENTS or len(_encoded(state).encode('utf-8')) > MAX_TOTAL_BYTES:
            _error('帳戶版面設定超出儲存上限。', 413)
        values = {**(preferences if preferences is not None else default_preferences()), RESERVED: state}
        conn.execute('''INSERT INTO studio_preferences VALUES(?,?,?)
            ON CONFLICT(username) DO UPDATE SET data=excluded.data''',
                     (studio.actor, row['version'] if row else 0, _encoded(values)))
        return {'version': state['version'], 'documents': {key: document}}
