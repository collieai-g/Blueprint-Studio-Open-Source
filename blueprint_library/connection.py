"""Real, repeatable connection acceptance using the existing authenticated API.

No new database or migration. One explicitly labelled workday per client attempt.
"""
from datetime import date as calendar_date
import re

from .store import LibraryError

PREFIX = 'blueprint-connect:'
INITIAL = 'blueprint-connection/1:pending'
COMPLETE = 'blueprint-connection/1:verified'
TITLE = 'AI 接駁測試'
TODO = '接駁測試：待辦寫入及更新'


def _connection_arguments(args):
    challenge, client, day = args['challenge'], args['client'], args['date']
    if (not re.fullmatch(r'[a-f0-9]{32}', challenge) or not client.strip()
            or any(ord(c) < 32 for c in client) or len(client) > 80):
        raise LibraryError('接駁測試參數無效。')
    try:
        if calendar_date.fromisoformat(day).isoformat() != day:
            raise ValueError
    except ValueError:
        raise LibraryError('測試日期需要有效 YYYY-MM-DD。') from None
    return challenge, client, day


def _find_or_create_report(request, challenge, client, day, key):
    matches = request('GET', '/api/workdays', query={'q': key, 'limit': 100})['workdays']
    matches = [r for r in matches if r['source_key'] == key and r['source'] == client and r['date'] == day]
    if len(matches) > 1:
        raise LibraryError('找到多筆接駁紀錄，請檢查原資料，不自動覆寫。', 409)
    if matches:
        report_id = matches[0]['id']
    else:
        result = request('POST', '/api/workdays', payload={
            'date': day, 'source': client, 'source_key': key, 'title': TITLE, 'summary': INITIAL,
            'entries': [{'type': 'outcome', 'title': TITLE, 'content': challenge},
                        {'type': 'todo', 'title': TODO, 'content': challenge}]
        })
        report_id = result['workday']['id']
    return report_id


def _check_report(report, challenge, client, day, key):
    # A separate GET proves readback; never trust the save response alone.
    entries = report['entries']
    if (report['source_key'] != key or report['source'] != client or report['date'] != day
            or report['title'] != TITLE or report['summary'] not in {INITIAL, COMPLETE}
            or len(entries) != 2 or entries[0]['type'] != 'outcome' or entries[0]['title'] != TITLE
            or entries[1]['type'] != 'todo' or entries[1]['title'] != TODO
            or any(e['content'] != challenge for e in entries)):
        raise LibraryError('測試紀錄已被修改，未覆寫；請在目前 AI 對話核對，只有要求另一次驗收才建立新的測試。', 409)


def _complete_report(request, report_id, report):
    patch_entries = [{k: e[k] for k in ('id', 'type', 'title', 'content', 'url', 'tags')}
                     | ({'due_date': e['due_date']} if e['type'] == 'todo' else {}) for e in report['entries']]
    request('PATCH', '/api/workdays/' + report_id,
            payload={'expected_version': report['version'], 'summary': COMPLETE, 'entries': patch_entries})
    report = request('GET', '/api/workdays/' + report_id)['workday']
    if report['summary'] != COMPLETE or not report['entries'][1]['done']:
        raise LibraryError('驗收結果未能讀回。', 502)
    return report


def verify_connection(request, args):
    challenge, client, day = _connection_arguments(args)
    key = PREFIX + challenge
    report_id = _find_or_create_report(request, challenge, client, day, key)
    # Every write has an independent readback; retries keep the same identity.
    report = request('GET', '/api/workdays/' + report_id)['workday']
    _check_report(report, challenge, client, day, key)
    todo = report['entries'][1]
    request('PATCH', '/api/todos/' + todo['id'],
            payload={'expected_version': todo['version'], 'done': True})
    report = request('GET', '/api/workdays/' + report_id)['workday']
    if not report['entries'][1]['done']:
        raise LibraryError('待辦更新未能讀回，驗收未完成。', 502)
    if report['summary'] not in {INITIAL, COMPLETE}:
        raise LibraryError('測試紀錄已被修改，未覆寫。', 409)
    report = _complete_report(request, report_id, report)
    return {'verified': True, 'client': client, 'challenge': challenge,
            'workday_id': report_id, 'checked_at': report['updated_at'],
            'next_step': '在目前 AI 對話按 workday_id 另行讀回紀錄，並按待辦 ID 核對測試待辦已完成；可到工作台「工作 → 工作紀錄」查看同一筆。新對話須重新檢查實際工具。',
            'workday': report}
