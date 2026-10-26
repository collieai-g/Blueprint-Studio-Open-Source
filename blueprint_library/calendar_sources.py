"""Read existing company deadlines and workdays without copying their records.

Legacy owners and report authors are free text, not verified employee identities.
Every result therefore stays company-scoped and unassigned.  The calendar caller
decides whether those company results belong in a requested employee view.
"""
from __future__ import annotations

from datetime import date
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .store import LibraryError
from .studio_schema import SCHEMAS
from .workdays import _date


def _range_day(value, label):
    if type(value) is date:
        value = value.isoformat()
    return _date(value, label=label)


def _event(row, source_kind, event_type, day, **extra):
    return {
        'type': event_type, 'date': day, 'title': row['title'],
        'id': row['id'], 'source_kind': source_kind, 'version': row['version'],
        'event_key': f"legacy:{source_kind}:{row['id']}",
        'scope': 'company', 'assignment': 'unassigned', **extra,
    }


def aggregate(studio, conn, start_date: str | date, end_date: str | date,
              timezone: str) -> list[dict]:
    """Return all authorized sources in [start_date, end_date), without writes.

    Dates in these sources are already civil YYYY-MM-DD dates.  They do not
    represent midnight UTC and must not move to another day in the selected
    timezone.  The zone is validated for the same contract as timed events.
    The caller supplies the connection and any read transaction it needs.
    """
    start = _range_day(start_date, '開始日期')
    end = _range_day(end_date, '結束日期')
    if start >= end:
        raise LibraryError('結束日期必須在開始日期之後。')
    if not isinstance(timezone, str) or not timezone:
        raise LibraryError('請提供有效的 IANA 時區。')
    try:
        ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise LibraryError('請提供有效的 IANA 時區。') from None

    events = []
    records = conn.execute("""
        SELECT id,kind,title,data,version,visibility,
               json_extract(data,'$.due_date') AS due_date
        FROM studio_records
        WHERE kind IN ('task','project') AND archived=0
          AND json_extract(data,'$.due_date')>=?
          AND json_extract(data,'$.due_date')<?
          AND (? OR visibility='team')
        """, (start, end, int(studio.admin)))
    for row in records:
        kind = row['kind']
        if SCHEMAS[kind].get('restricted') and not studio.admin:
            continue
        data = json.loads(row['data'])
        extra = {'route': 'record/' + row['id']}
        if kind == 'task':
            extra['done'] = bool(data.get('done', False))
        events.append(_event(row, kind, 'deadline', row['due_date'], **extra))

    for row in conn.execute("""
        SELECT id,date,title,version FROM workdays WHERE date>=? AND date<?
        """, (start, end)):
        if studio.resource_allowed('workday', row['id'], conn):
            events.append(_event(row, 'workday', 'workday', row['date'],
                                 route='overview/workdays', open_report=row['id'],
                                 workday_id=row['id']))

    # A todo belongs on its due date even when its parent report is outside the
    # requested month.  Undated todos are not invented calendar deadlines.
    for row in conn.execute("""
        SELECT e.id,e.workday_id,e.title,e.due_date,e.done,e.version
        FROM workday_entries e JOIN workdays w ON w.id=e.workday_id
        WHERE e.type='todo' AND e.due_date>=? AND e.due_date<?
        """, (start, end)):
        if studio.resource_allowed('todo', row['id'], conn):
            events.append(_event(row, 'todo', 'deadline', row['due_date'],
                                 done=bool(row['done']), route='overview/workdays',
                                 open_report=row['workday_id'],
                                 workday_id=row['workday_id']))

    return sorted(events, key=lambda event: (
        event['date'], event['type'], event['source_kind'], event['id']))
