"""Explicit daily work summaries and todos, without importing raw conversations.

The Store supplies the SQLite connection and bookmark helpers. Imports are
idempotent; completed todo state never changes when a client retries a report.
"""

from __future__ import annotations

from datetime import date as calendar_date
import hashlib
import json
import re
import uuid

from .classify import normalize_url


_ENTRY_TYPES = {"outcome", "decision", "reference", "todo"}
_MAX_REPORT_CHARS = 200_000
_ENTRY_FIELDS = ("type", "title", "content", "url", "tags", "due_date")


def _date(value, *, label="日期", optional=False):
    from .store import LibraryError

    if optional and value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise LibraryError(f"{label}必須使用 YYYY-MM-DD 格式。")
    try:
        calendar_date.fromisoformat(value)
    except ValueError as exc:
        raise LibraryError(f"{label}無效。") from exc
    return value


def _entries(entries, *, allow_ids=False):
    from .store import LibraryError, _identifier, _payload, _tags, _text

    if not isinstance(entries, list) or not 1 <= len(entries) <= 80:
        raise LibraryError("每份工作紀錄需要 1 至 80 項內容。")
    validated = []
    seen_ids = set()
    for entry in entries:
        entry = _payload(entry, set(_ENTRY_FIELDS) | ({"id"} if allow_ids else set()))
        kind = entry.get("type")
        if not isinstance(kind, str) or kind not in _ENTRY_TYPES:
            raise LibraryError("內容類型只接受 outcome、decision、reference 或 todo。")
        item_title = _text(entry.get("title", ""), "內容標題", 240, empty=False)
        content = _text(entry.get("content", ""), "內容說明", 8000)
        raw_url = _text(entry.get("url", ""), "參考網址", 8192)
        try:
            url = normalize_url(raw_url) if raw_url else ""
        except ValueError as exc:
            raise LibraryError(str(exc)) from exc
        raw_tags = entry.get("tags", [])
        if not isinstance(raw_tags, list) or len(raw_tags) > 16:
            raise LibraryError("每項內容最多 16 個文字標籤。")
        tags = _tags(raw_tags)
        if "due_date" in entry and kind != "todo":
            raise LibraryError("只有待辦事項可以設定到期日期。")
        due_date = _date(entry.get("due_date"), label="到期日期", optional=True)
        normalized = {"type": kind, "title": item_title, "content": content,
                      "url": url, "tags": tags, "due_date": due_date}
        if "id" in entry:
            entry_id = _identifier(entry["id"], "內容編號")
            if entry_id in seen_ids:
                raise LibraryError("同一份工作紀錄不能重複使用內容編號。")
            seen_ids.add(entry_id)
            normalized["id"] = entry_id
        validated.append(normalized)
    return validated


def _report_hash(report):
    from .store import LibraryError

    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical) > _MAX_REPORT_CHARS:
        raise LibraryError("每份工作紀錄最多 200,000 字元。", 413)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _report(payload):
    from .store import _payload, _text

    payload = _payload(payload, {"date", "source", "source_key", "title", "summary", "entries"})
    day = _date(payload.get("date"))
    source = _text(payload.get("source", "Codex"), "工作來源", 80, empty=False)
    source_key = _text(payload.get("source_key", ""), "來源識別碼", 200, empty=False)
    title = _text(payload.get("title", ""), "工作紀錄標題", 240) or f"{day} 工作紀錄"
    summary = _text(payload.get("summary", ""), "工作摘要", 8000)
    result = {"date": day, "source": source, "source_key": source_key, "title": title,
              "summary": summary, "entries": _entries(payload.get("entries"))}
    return result, _report_hash(result)


def _report_text(report):
    context = "\n".join(report[name] for name in ("date", "source", "source_key", "title", "summary"))
    search = "\n".join([context, *("\n".join([entry["title"], entry["content"], entry["url"], " ".join(entry["tags"])])
                                  for entry in report["entries"])])
    return context.casefold(), search.casefold()


def _query(q, day, limit, offset, maximum):
    from .store import LibraryError, _text

    q = _text(q, "搜尋內容", 2000)
    if day != "":
        day = _date(day)
    elif not isinstance(day, str):
        raise LibraryError("日期篩選必須係文字。")
    if type(limit) is not int or not 1 <= limit <= maximum or type(offset) is not int or not 0 <= offset <= 1_000_000:
        raise LibraryError(f"每頁數量必須為 1 至 {maximum}，起始位置必須為非負整數。")
    terms = list(dict.fromkeys(q.casefold().split()))
    if len(terms) > 32:
        raise LibraryError("每次搜尋最多 32 個關鍵字。")
    return terms, day


def _entry_public(row, *, context=False):
    result = dict(row)
    if isinstance(result["tags"], str):
        result["tags"] = json.loads(result["tags"])
    result["done"] = bool(result["done"])
    result.pop("position", None)
    result.pop("search_text", None)
    if not context:
        result.pop("workday_id", None)
    return result


class WorkdayMixin:
    """Persistence operations mixed into Store; no independent storage paths."""

    @staticmethod
    def _initialize_workdays(conn):
        for sql in ("""
            CREATE TABLE IF NOT EXISTS workdays (
                id TEXT PRIMARY KEY, date TEXT NOT NULL, source TEXT NOT NULL,
                source_key TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
                payload_hash TEXT NOT NULL, search_text TEXT NOT NULL,
                context_text TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                UNIQUE(date, source, source_key)
            )""", """
            CREATE TABLE IF NOT EXISTS workday_entries (
                id TEXT PRIMARY KEY, workday_id TEXT NOT NULL REFERENCES workdays(id),
                position INTEGER NOT NULL,
                type TEXT NOT NULL CHECK(type IN ('outcome','decision','reference','todo')),
                title TEXT NOT NULL, content TEXT NOT NULL, url TEXT NOT NULL,
                tags TEXT NOT NULL, due_date TEXT, done INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,
                library_item_id TEXT REFERENCES items(id), search_text TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
                created_by TEXT NOT NULL, updated_by TEXT NOT NULL,
                UNIQUE(workday_id, position)
            )""",
            "CREATE INDEX IF NOT EXISTS workdays_date ON workdays(date DESC,created_at DESC,id)",
            "CREATE INDEX IF NOT EXISTS workday_entries_order ON workday_entries(workday_id,position)",
            "CREATE INDEX IF NOT EXISTS workday_entries_todos ON workday_entries(type,done,workday_id)",
            "CREATE INDEX IF NOT EXISTS items_active_url ON items(url,kind,archived)",
        ):
            conn.execute(sql)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(workdays)")}
        if "version" not in columns:
            conn.execute("ALTER TABLE workdays ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
        conn.execute("""CREATE TABLE IF NOT EXISTS workday_revisions (
            workday_id TEXT NOT NULL REFERENCES workdays(id), version INTEGER NOT NULL,
            action TEXT NOT NULL, actor TEXT NOT NULL, recorded_at TEXT NOT NULL,
            snapshot TEXT NOT NULL, PRIMARY KEY(workday_id,version)
        )""")

    @staticmethod
    def _workday_row(conn, workday_id):
        from .store import LibraryError, _identifier

        workday_id = _identifier(workday_id, "工作紀錄編號")
        row = conn.execute("SELECT * FROM workdays WHERE id=?", (workday_id,)).fetchone()
        if row is None:
            raise LibraryError("搵唔到指定工作紀錄。", 404)
        return row

    def _workday_full(self, conn, workday_id):
        result = dict(self._workday_row(conn, workday_id))
        for name in ("payload_hash", "search_text", "context_text"):
            result.pop(name, None)
        result["entries"] = [_entry_public(row) for row in conn.execute(
            "SELECT * FROM workday_entries WHERE workday_id=? ORDER BY position", (workday_id,))]
        return result

    def _workday_revision(self, conn, workday_id, action, actor, recorded_at):
        from .store import _json

        snapshot = self._workday_full(conn, workday_id)
        conn.execute("""INSERT OR IGNORE INTO workday_revisions
            (workday_id,version,action,actor,recorded_at,snapshot) VALUES(?,?,?,?,?,?)""",
                     (workday_id, snapshot["version"], action, actor, recorded_at, _json(snapshot)))

    def _ensure_workday_revision(self, conn, workday):
        # Old databases have no historical snapshots. Preserve their exact
        # current state before the first versioned edit, without changing rows
        # during migration or assigning a fictional creation history.
        self._workday_revision(conn, workday["id"], "legacy", workday["updated_by"], workday["updated_at"])

    @staticmethod
    def _workday_history(conn, workday_id):
        result = []
        for row in conn.execute("""SELECT version,action,actor,recorded_at,snapshot
                FROM workday_revisions WHERE workday_id=? ORDER BY version""", (workday_id,)):
            entry = dict(row)
            entry["snapshot"] = json.loads(entry["snapshot"])
            result.append(entry)
        return result

    def item_workday_history(self, workday_id):
        with self._connection() as conn:
            conn.execute("BEGIN")
            self._workday_row(conn, workday_id)
            return self._workday_history(conn, workday_id)

    def _workday_reference(self, conn, entry, actor):
        if entry["type"] != "reference" or not entry["url"]:
            return None
        existing = conn.execute("""SELECT id FROM items WHERE kind='link' AND url=? AND archived=0
                                   ORDER BY created_at,id LIMIT 1""", (entry["url"],)).fetchone()
        if existing is not None:
            return existing["id"]
        item = self._base_item("link", entry["title"], entry["content"], entry["url"], None)
        if entry["tags"]:
            item["tags"] = entry["tags"]
        self._classify(item, False, bool(entry["tags"]))
        return self._insert(conn, item, actor)["id"]

    @staticmethod
    def _entry_row(workday_id, position, entry, actor, now, library_item_id):
        return {"id": uuid.uuid4().hex, "workday_id": workday_id, "position": position,
                **{key: entry[key] for key in _ENTRY_FIELDS}, "done": 0, "version": 1,
                "library_item_id": library_item_id,
                "search_text": "\n".join([entry["title"], entry["content"], entry["url"], " ".join(entry["tags"])]).casefold(),
                "created_at": now, "updated_at": now, "completed_at": None,
                "created_by": actor, "updated_by": actor}

    @staticmethod
    def _insert_workday_entry(conn, entry):
        from .store import _json

        columns = tuple(entry)
        values = [_json(entry[key]) if key == "tags" else entry[key] for key in columns]
        conn.execute("INSERT INTO workday_entries(" + ",".join(columns) + ") VALUES("
                     + ",".join("?" for _ in columns) + ")", values)

    def import_workday(self, payload, actor="owner", member_access=False) -> dict:
        from .store import LibraryError, _now, _text

        report, payload_hash = _report(payload)
        actor = _text(actor, "操作人", 120, empty=False)
        context, search_text = _report_text(report)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT id,payload_hash FROM workdays WHERE date=? AND source=? AND source_key=?",
                                    (report["date"], report["source"], report["source_key"])).fetchone()
            if existing is not None:
                if member_access and conn.execute("SELECT 1 FROM studio_resource_acl WHERE kind='workday' AND resource_id=?", (existing['id'],)).fetchone():
                    raise LibraryError('你的帳戶沒有這項權限。', 403)
                if existing["payload_hash"] != payload_hash:
                    raise LibraryError("同一來源識別碼已有不同內容，未有覆寫。請先查看原有紀錄；不同工作可另設來源識別碼。", 409)
                return {"workday": self._workday_full(conn, existing["id"]), "created": False, "duplicate": True}
            now = _now()
            workday_id = uuid.uuid4().hex
            conn.execute("""INSERT INTO workdays(id,date,source,source_key,title,summary,payload_hash,
                search_text,context_text,created_at,updated_at,created_by,updated_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (workday_id, report["date"], report["source"], report["source_key"], report["title"],
                          report["summary"], payload_hash, search_text, context, now, now, actor, actor))
            for position, entry in enumerate(report["entries"]):
                library_item_id = self._workday_reference(conn, entry, actor)
                self._insert_workday_entry(conn, self._entry_row(workday_id, position, entry, actor, now, library_item_id))
            self._workday_revision(conn, workday_id, "create", actor, now)
            return {"workday": self._workday_full(conn, workday_id), "created": True, "duplicate": False}

    def update_workday(self, workday_id, payload, actor="owner") -> dict:
        from .store import LibraryError, _identifier, _json, _now, _payload, _text

        workday_id = _identifier(workday_id, "工作紀錄編號")
        payload = _payload(payload, {"expected_version", "title", "summary", "entries"})
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 1:
            raise LibraryError("更新必須提供正確嘅 expected_version。")
        entries = _entries(payload.get("entries"), allow_ids=True)
        actor = _text(actor, "操作人", 120, empty=False)
        title = _text(payload["title"], "工作紀錄標題", 240) if "title" in payload else None
        summary = _text(payload["summary"], "工作摘要", 8000) if "summary" in payload else None
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._workday_full(conn, workday_id)
            if current["version"] != expected:
                raise LibraryError("工作紀錄已被其他操作更新，請重新讀取後再整理。", 409)
            existing = {entry["id"]: entry for entry in current["entries"]}
            if any(entry.get("id") not in existing for entry in entries if "id" in entry):
                raise LibraryError("內容編號不屬於此工作紀錄，未有修改任何內容。")
            report = {name: current[name] for name in ("date", "source", "source_key", "title", "summary")}
            if title is not None:
                report["title"] = title or f"{current['date']} 工作紀錄"
            if summary is not None:
                report["summary"] = summary
            report["entries"] = [{name: entry[name] for name in _ENTRY_FIELDS} for entry in entries]
            payload_hash = _report_hash(report)
            unchanged = (
                report["title"] == current["title"] and report["summary"] == current["summary"]
                and len(entries) == len(current["entries"])
                and all(entry.get("id") == saved["id"]
                        and all(entry[name] == saved[name] for name in _ENTRY_FIELDS)
                        for entry, saved in zip(entries, current["entries"]))
            )
            if unchanged:
                return {"workday": current, "updated": False}

            # Validate all entries and ownership before taking a snapshot or
            # creating bookmarks. Every subsequent write shares this transaction.
            self._ensure_workday_revision(conn, current)
            now = _now()
            context, search_text = _report_text(report)
            desired_rows = []
            for position, entry in enumerate(entries):
                library_item_id = self._workday_reference(conn, entry, actor)
                old = existing.get(entry.get("id"))
                row = self._entry_row(workday_id, position, entry, actor, now, library_item_id)
                if old is not None:
                    changed = any(entry[name] != old[name] for name in _ENTRY_FIELDS)
                    changed = changed or library_item_id != old["library_item_id"]
                    row.update(id=old["id"], created_at=old["created_at"], created_by=old["created_by"],
                               version=old["version"] + int(changed),
                               updated_at=now if changed else old["updated_at"],
                               updated_by=actor if changed else old["updated_by"])
                    if old["type"] == "todo" and entry["type"] == "todo":
                        row.update(done=int(old["done"]), completed_at=old["completed_at"])
                desired_rows.append(row)
            desired_ids = {row["id"] for row in desired_rows}
            # Temporary negative positions avoid UNIQUE(workday_id, position)
            # collisions when retained entries move; their IDs remain stable.
            conn.execute("UPDATE workday_entries SET position=-position-1 WHERE workday_id=?", (workday_id,))
            for entry_id in existing:
                if entry_id not in desired_ids:
                    conn.execute("DELETE FROM workday_entries WHERE id=? AND workday_id=?", (entry_id, workday_id))
            for row in desired_rows:
                if row["id"] not in existing:
                    self._insert_workday_entry(conn, row)
                else:
                    columns = tuple(key for key in row if key not in {"id", "workday_id", "created_at", "created_by"})
                    values = [_json(row[key]) if key == "tags" else row[key] for key in columns]
                    conn.execute("UPDATE workday_entries SET " + ",".join(key + "=?" for key in columns)
                                 + " WHERE id=? AND workday_id=?", [*values, row["id"], workday_id])
            conn.execute("""UPDATE workdays SET title=?,summary=?,payload_hash=?,search_text=?,context_text=?,
                         updated_at=?,updated_by=?,version=version+1 WHERE id=?""",
                         (report["title"], report["summary"], payload_hash, search_text, context, now, actor, workday_id))
            self._workday_revision(conn, workday_id, "update", actor, now)
            return {"workday": self._workday_full(conn, workday_id), "updated": True}

    def get_workday(self, workday_id) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            return self._workday_full(conn, workday_id)

    def list_workdays(self, q="", date="", limit=30, offset=0, member_access=False) -> dict:
        terms, day = _query(q, date, limit, offset, 100)
        where, args = [], []
        if member_access: where.append("NOT EXISTS(SELECT 1 FROM studio_resource_acl a WHERE a.kind='workday' AND a.resource_id=w.id)")
        if day:
            where.append("w.date=?")
            args.append(day)
        for term in terms:
            where.append("instr(w.search_text,?)>0")
            args.append(term)
        clause = " AND ".join(where) or "1=1"
        with self._connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT COUNT(*) FROM workdays w WHERE " + clause, args).fetchone()[0]
            rows = conn.execute("""SELECT w.id,w.date,w.source,w.source_key,w.title,w.summary,w.created_at,w.updated_at,w.version,
                w.created_by,w.updated_by,COUNT(e.id) AS entry_count,
                COALESCE(SUM(e.type='outcome'),0) AS count_outcome,
                COALESCE(SUM(e.type='decision'),0) AS count_decision,
                COALESCE(SUM(e.type='reference'),0) AS count_reference,
                COALESCE(SUM(e.type='todo'),0) AS count_todo,
                COALESCE(SUM(e.type='todo' AND e.done=0),0) AS open_todos
                FROM workdays w LEFT JOIN workday_entries e ON e.workday_id=w.id WHERE """ + clause + """
                GROUP BY w.id ORDER BY w.date DESC,w.created_at DESC,w.id LIMIT ? OFFSET ?""", [*args, limit, offset])
            workdays = []
            for row in rows:
                record = dict(row)
                record["counts"] = {name: record.pop("count_" + name) for name in ("outcome", "decision", "reference", "todo")}
                workdays.append(record)
            return {"workdays": workdays, "total": total}

    def list_todos(self, done=None, date="", q="", limit=100, offset=0, member_access=False) -> dict:
        from .store import _boolean

        terms, day = _query(q, date, limit, offset, 200)
        where, args = ["e.type='todo'"], []
        if member_access: where.append("NOT EXISTS(SELECT 1 FROM studio_resource_acl a WHERE (a.kind='workday' AND a.resource_id=w.id) OR (a.kind='todo' AND a.resource_id=e.id))")
        if done is not None:
            where.append("e.done=?")
            args.append(int(_boolean(done, "完成狀態")))
        if day:
            where.append("w.date=?")
            args.append(day)
        for term in terms:
            where.append("instr(e.search_text || char(10) || w.context_text,?)>0")
            args.append(term)
        clause = " AND ".join(where)
        with self._connection() as conn:
            conn.execute("BEGIN")
            total = conn.execute("SELECT COUNT(*) FROM workday_entries e JOIN workdays w ON w.id=e.workday_id WHERE " + clause, args).fetchone()[0]
            rows = conn.execute("""SELECT e.*,w.title AS workday_title,w.date,w.source
                FROM workday_entries e JOIN workdays w ON w.id=e.workday_id WHERE """ + clause + """
                ORDER BY (e.due_date IS NULL),e.due_date,w.date DESC,e.position,e.id LIMIT ? OFFSET ?""", [*args, limit, offset])
            return {"todos": [_entry_public(row, context=True) for row in rows], "total": total}

    def update_todo(self, entry_id, payload, actor="owner") -> dict:
        from .store import LibraryError, _boolean, _identifier, _now, _payload, _text

        entry_id = _identifier(entry_id, "待辦編號")
        payload = _payload(payload, {"expected_version", "done"})
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 1:
            raise LibraryError("更新必須提供正確嘅 expected_version。")
        done = _boolean(payload.get("done"), "完成狀態")
        actor = _text(actor, "操作人", 120, empty=False)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM workday_entries WHERE id=?", (entry_id,)).fetchone()
            if row is None:
                raise LibraryError("搵唔到指定待辦事項。", 404)
            if row["type"] != "todo":
                raise LibraryError("只有待辦事項可以更新完成狀態。")
            if row["version"] != expected:
                raise LibraryError("待辦已被其他操作更新，請重新讀取後再修改。", 409)
            self._ensure_workday_revision(conn, self._workday_full(conn, row["workday_id"]))
            now = _now()
            completed_at = (row["completed_at"] or now) if done else None
            conn.execute("UPDATE workday_entries SET done=?,version=version+1,updated_at=?,completed_at=?,updated_by=? WHERE id=?",
                         (int(done), now, completed_at, actor, entry_id))
            conn.execute("UPDATE workdays SET updated_at=?,updated_by=?,version=version+1 WHERE id=?", (now, actor, row["workday_id"]))
            self._workday_revision(conn, row["workday_id"], "todo", actor, now)
            result = conn.execute("""SELECT e.*,w.title AS workday_title,w.date,w.source
                FROM workday_entries e JOIN workdays w ON w.id=e.workday_id WHERE e.id=?""", (entry_id,)).fetchone()
            return _entry_public(result, context=True)

    @staticmethod
    def _workday_stats(conn, member_access=False):
        row = conn.execute("""SELECT
            COALESCE(SUM(type='todo' AND done=0),0) AS todos_open,
            COALESCE(SUM(type='todo' AND done=1),0) AS todos_done FROM workday_entries e WHERE (?=0 OR NOT EXISTS(SELECT 1 FROM studio_resource_acl a WHERE (a.kind='workday' AND a.resource_id=e.workday_id) OR (a.kind='todo' AND a.resource_id=e.id)))""", (int(member_access),)).fetchone()
        return {"workdays": conn.execute("SELECT COUNT(*) FROM workdays w WHERE (?=0 OR NOT EXISTS(SELECT 1 FROM studio_resource_acl a WHERE a.kind='workday' AND a.resource_id=w.id))", (int(member_access),)).fetchone()[0], **dict(row)}

    def workday_stats(self) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            return self._workday_stats(conn)

    def _export_workdays(self, conn):
        reports = []
        for row in conn.execute("SELECT id FROM workdays ORDER BY date,created_at,id"):
            report = self._workday_full(conn, row["id"])
            report["revisions"] = self._workday_history(conn, row["id"])
            reports.append(report)
        return reports
