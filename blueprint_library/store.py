"""Private, persistent company library metadata and immutable file storage.

Only standard-library modules are used. Classification is a transparent local
rule suggestion; this module never sends company files to a model or website.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from blueprint_folders.core import CATEGORIES

from .classify import classify_item, normalize_url
from .company import CompanyMixin
from .workdays import WorkdayMixin


MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_TEXT_SIZE = 200 * 1024
SCHEMA_VERSION = 4
_SOURCE_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_TYPES = {"website", "youtube", "code", "document", "image", "video", "other"}
_SORT_ORDERS = {
    "updated": "updated_at DESC,id",
    "created": "created_at DESC,id",
    "title": "title COLLATE NOCASE ASC,id",
}
_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".xml", ".html", ".htm", ".css", ".scss", ".js",
    ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".sh", ".sql", ".rs", ".go",
    ".java", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt", ".vue",
}


class LibraryError(ValueError):
    """A validation or missing-resource error safe to show to a client."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _text(value, label: str, maximum: int, *, empty: bool = True) -> str:
    if not isinstance(value, str):
        raise LibraryError(f"{label}必須係文字。")
    value = value.strip()
    if "\x00" in value or len(value) > maximum or (not empty and not value):
        raise LibraryError(f"{label}不可空白或超過 {maximum:,} 字。" if not empty else f"{label}最多 {maximum:,} 字，不能包含空字元。")
    return value


def _tags(value) -> list[str]:
    if not isinstance(value, list) or len(value) > 32:
        raise LibraryError("標籤必須係文字清單，最多 32 個。")
    result = []
    for tag in value:
        tag = _text(tag, "標籤", 100, empty=False)
        if tag not in result:
            result.append(tag)
    return result


def _category(value) -> str:
    if not isinstance(value, str) or value not in CATEGORIES:
        raise LibraryError("無效分類，請選擇現有圖示分類。")
    return value


def _payload(value, allowed: set[str]) -> dict:
    if not isinstance(value, dict):
        raise LibraryError("內容必須係 JSON 物件。")
    unknown = set(value) - allowed
    if unknown:
        raise LibraryError("包含不支援嘅欄位。")
    return value


def _boolean(value, label: str) -> bool:
    if not isinstance(value, bool):
        raise LibraryError(f"{label}必須係 true 或 false。")
    return value


def _identifier(value, label: str = "編號") -> str:
    return _text(value, label, 100, empty=False)


class Store(CompanyMixin, WorkdayMixin):
    """A SQLite-backed private library. Open connections per operation/thread."""

    def __init__(self, data_dir):
        try:
            original = Path(data_dir).expanduser()
            if original.is_symlink():
                raise LibraryError("資料目錄不能係符號連結。")
            self.data_dir = original.resolve()
        except (TypeError, OSError, RuntimeError) as exc:
            raise LibraryError("請提供有效嘅私人資料目錄。") from exc
        if (
            self.data_dir == _SOURCE_ROOT
            or _SOURCE_ROOT in self.data_dir.parents
            or self.data_dir in _SOURCE_ROOT.parents
        ):
            raise LibraryError("私人資料目錄必須放喺公開工具專案以外，兩者不能互相包含。")
        self.db_path = self.data_dir / "library.sqlite3"
        self.blobs_dir = self.data_dir / "blobs"
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.db_path.is_symlink() or (self.db_path.exists() and not self.db_path.is_file()):
                raise LibraryError("資料庫必須係正常檔案。")
            if self.blobs_dir.is_symlink() or (self.blobs_dir.exists() and not self.blobs_dir.is_dir()):
                raise LibraryError("檔案儲存位置必須係正常目錄。")
            self.blobs_dir.mkdir(exist_ok=True, mode=0o700)
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise LibraryError("無法開啟資料目錄或資料庫。", 500) from exc

    @contextmanager
    def _connection(self):
        if (self.data_dir.is_symlink() or self.data_dir.resolve() != self.data_dir
                or self.db_path.is_symlink() or self.blobs_dir.is_symlink()
                or self.blobs_dir.resolve() != self.blobs_dir):
            raise LibraryError("資料儲存位置不能改為符號連結。", 500)
        conn = None
        try:
            conn = sqlite3.connect(self.db_path, timeout=15)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        except sqlite3.Error as exc:
            raise LibraryError("資料庫操作失敗，請稍後重試。", 500) from exc
        finally:
            if conn is not None:
                conn.close()

    def _initialize(self):
        with self._connection() as conn:
            if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                raise LibraryError("此資料庫由較新版本建立，請先更新工具。", 500)
            conn.execute("PRAGMA journal_mode = WAL")
            # Serialize concurrent startup migrations and commit schema/version
            # together. Existing files, records and historical snapshots stay put.
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("PRAGMA user_version").fetchone()[0] > SCHEMA_VERSION:
                raise LibraryError("此資料庫由較新版本建立，請先更新工具。", 500)
            statements = ("""
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
                    icon TEXT NOT NULL, created_at TEXT NOT NULL
                )""", """
                CREATE TABLE IF NOT EXISTS items (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('file','link')),
                    title TEXT NOT NULL, notes TEXT NOT NULL, url TEXT NOT NULL,
                    collection_id TEXT REFERENCES collections(id), category TEXT NOT NULL,
                    tags TEXT NOT NULL, source_type TEXT NOT NULL, classification TEXT NOT NULL,
                    category_override INTEGER NOT NULL, tags_override INTEGER NOT NULL,
                    file_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, blob_name TEXT NOT NULL, extracted_text TEXT NOT NULL,
                    search_text TEXT NOT NULL, version INTEGER NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0, pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""", """
                CREATE TABLE IF NOT EXISTS history (
                    id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id),
                    version INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, snapshot TEXT NOT NULL,
                    UNIQUE(item_id, version)
                )""")
            for statement in statements:
                conn.execute(statement)
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(items)")}
            if "pinned" not in columns:
                conn.execute("ALTER TABLE items ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            for statement in (
                "CREATE INDEX IF NOT EXISTS items_collection ON items(collection_id, archived)",
                "CREATE INDEX IF NOT EXISTS items_category ON items(category, archived)",
                "CREATE INDEX IF NOT EXISTS items_updated ON items(updated_at DESC, id)",
                "CREATE INDEX IF NOT EXISTS items_pinned ON items(pinned, archived)",
            ):
                conn.execute(statement)
            self._initialize_workdays(conn)
            self._initialize_company(conn)
            conn.execute("PRAGMA user_version = 4")

    def _collection_id(self, conn, value):
        if value is None or value == "":
            return None
        value = _identifier(value, "收藏夾編號")
        if not conn.execute("SELECT 1 FROM collections WHERE id = ?", (value,)).fetchone():
            raise LibraryError("搵唔到指定收藏夾。", 404)
        return value

    def create_collection(self, payload) -> dict:
        payload = _payload(payload, {"name", "description", "icon"})
        name = _text(payload.get("name", ""), "收藏夾名稱", 120, empty=False)
        description = _text(payload.get("description", ""), "收藏夾說明", 8000)
        icon = _category(payload.get("icon", "general"))
        item = {"id": uuid.uuid4().hex, "name": name, "description": description,
                "icon": icon, "count": 0, "created_at": _now()}
        with self._connection() as conn:
            conn.execute("INSERT INTO collections(id,name,description,icon,created_at) VALUES(?,?,?,?,?)",
                         (item["id"], name, description, icon, item["created_at"]))
        return item

    def list_collections(self) -> list[dict]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("""
                SELECT c.*, COUNT(i.id) AS count FROM collections c
                LEFT JOIN items i ON i.collection_id=c.id AND i.archived=0
                GROUP BY c.id ORDER BY c.created_at,c.id
            """)]

    def _classify(self, item, category_override=False, tags_override=False):
        result = classify_item(
            name=item["file_name"] or item["title"], url=item["url"], mime=item["mime"],
            text="\n".join([item["title"], item["notes"], item["extracted_text"]]),
        )
        # Classification remains explainable and separate from user corrections.
        if not category_override:
            item["category"] = _category(result["category"])
        if not tags_override:
            item["tags"] = _tags(result["tags"])
        item["source_type"] = result["source_type"]
        item["classification"] = {
            **{key: value for key, value in result.items() if key not in {"category", "tags", "kind", "icon", "source_type"}},
            "suggested_category": result["category"], "suggested_tags": result["tags"],
            "overrides": {"category": bool(category_override), "tags": bool(tags_override)},
        }
        item["category_override"] = int(category_override)
        item["tags_override"] = int(tags_override)

    @staticmethod
    def _public(row, *, full=True):
        result = dict(row)
        for key in ("tags", "classification"):
            if isinstance(result[key], str):
                result[key] = json.loads(result[key])
        result["archived"] = bool(result["archived"])
        result["pinned"] = bool(result.get("pinned", False))
        result["icon"] = result["category"]
        for key in ("blob_name", "search_text", "category_override", "tags_override"):
            result.pop(key, None)
        if not full:
            result.pop("extracted_text", None)
        return result

    @staticmethod
    def _search_text(item):
        return "\n".join([item["title"], item["notes"], item["url"], item["file_name"],
                          " ".join(item["tags"]), item["extracted_text"]]).casefold()

    def _insert(self, conn, item, actor):
        item["search_text"] = self._search_text(item)
        columns = tuple(item)
        values = [_json(item[key]) if key in {"tags", "classification"} else item[key] for key in columns]
        # Column names come exclusively from this module, never from API input.
        conn.execute(f"INSERT INTO items({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)
        self._history(conn, item, "create", actor)
        return self._public(item)

    def _history(self, conn, item, action, actor):
        actor = _text(actor, "操作人", 120, empty=False)
        conn.execute("INSERT INTO history(id,item_id,version,action,actor,recorded_at,snapshot) VALUES(?,?,?,?,?,?,?)",
                     (uuid.uuid4().hex, item["id"], item["version"], action, actor, _now(), _json(self._public(item))))

    @staticmethod
    def _base_item(kind, title, notes, url, collection_id):
        now = _now()
        return dict(id=uuid.uuid4().hex, kind=kind, title=title, notes=notes, url=url,
                    collection_id=collection_id, category="general", tags=[], source_type="other",
                    classification={}, category_override=0, tags_override=0, file_name="", mime="",
                    size=0, sha256="", blob_name="", extracted_text="", version=1,
                    archived=0, pinned=0, created_at=now, updated_at=now)

    def create_item(self, payload, actor="owner") -> dict:
        payload = _payload(payload, {"kind", "title", "notes", "url", "collection_id", "category", "tags"})
        if payload.get("kind", "link") != "link":
            raise LibraryError("新增收藏只接受 link；檔案請使用上傳功能。")
        raw_url = _text(payload.get("url", ""), "網址", 8192, empty=False)
        try:
            url = normalize_url(raw_url)
        except ValueError as exc:
            raise LibraryError(str(exc)) from exc
        title = _text(payload.get("title", ""), "標題", 240) or url[:240]
        notes = _text(payload.get("notes", ""), "筆記", 20000)
        with self._connection() as conn:
            collection_id = self._collection_id(conn, payload.get("collection_id"))
            item = self._base_item("link", title, notes, url, collection_id)
            if "category" in payload:
                item["category"] = _category(payload["category"])
            if "tags" in payload:
                item["tags"] = _tags(payload["tags"])
            self._classify(item, "category" in payload, "tags" in payload)
            return self._insert(conn, item, actor)

    def _get_row(self, conn, item_id):
        item_id = _identifier(item_id)
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise LibraryError("搵唔到指定資料。", 404)
        return row

    def get_item(self, item_id) -> dict:
        with self._connection() as conn:
            return self._public(self._get_row(conn, item_id))

    def list_items(self, q="", kind="", category="", collection_id="", source_type="",
                   archived=False, limit=100, offset=0, pinned=None, unfiled=False,
                   sort="updated") -> dict:
        q = _text(q, "搜尋內容", 2000)
        archived = _boolean(archived, "封存狀態")
        unfiled = _boolean(unfiled, "未分類收藏夾篩選")
        if pinned is not None:
            pinned = _boolean(pinned, "常用狀態")
        if not isinstance(sort, str) or sort not in _SORT_ORDERS:
            raise LibraryError("排序只接受 updated、created 或 title。")
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or not 0 <= offset <= 1000000:
            raise LibraryError("每頁數量必須為 1 至 200，起始位置必須為非負整數。")
        for value, label in ((kind, "資料類型"), (category, "分類"), (source_type, "來源類型"), (collection_id, "收藏夾編號")):
            _text(value, label, 100)
        if kind and kind not in {"file", "link"}:
            raise LibraryError("無效資料類型。")
        if category:
            _category(category)
        if source_type and source_type not in _SOURCE_TYPES:
            raise LibraryError("無效來源類型。")
        terms = list(dict.fromkeys(q.casefold().split()))
        if len(terms) > 32:
            raise LibraryError("每次搜尋最多 32 個關鍵字。")
        where = ["archived=?"]
        args = [int(archived)]
        if pinned is not None:
            where.append("pinned=?")
            args.append(int(pinned))
        if unfiled:
            where.append("collection_id IS NULL")
        for column, value in (("kind", kind), ("category", category), ("collection_id", collection_id), ("source_type", source_type)):
            if value:
                where.append(column + "=?")
                args.append(value)
        for term in terms:
            where.append("instr(search_text, ?) > 0")
            args.append(term)
        clause = " AND ".join(where)
        with self._connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM items WHERE " + clause, args).fetchone()[0]
            # SQLite performs filtering and pagination, including Unicode casefold text.
            rows = conn.execute("SELECT * FROM items WHERE " + clause + " ORDER BY " + _SORT_ORDERS[sort] + " LIMIT ? OFFSET ?", [*args, limit, offset])
            return {"items": [self._public(row, full=False) for row in rows], "total": total}

    def update_item(self, item_id, payload, actor="owner") -> dict:
        payload = _payload(payload, {"title", "notes", "url", "collection_id", "category", "tags", "archived", "pinned", "reclassify", "expected_version"})
        expected = payload.get("expected_version")
        if type(expected) is not int or expected < 1:
            raise LibraryError("更新必須提供正確嘅 expected_version。")
        reclassify = _boolean(payload.get("reclassify", False), "重新分類")
        with self._connection() as conn:
            # A write transaction makes reading and checking a version atomic.
            conn.execute("BEGIN IMMEDIATE")
            item = dict(self._get_row(conn, item_id))
            if item["version"] != expected:
                raise LibraryError("資料已被其他操作更新，請重新讀取後再修改。", 409)
            item["tags"] = json.loads(item["tags"])
            item["classification"] = json.loads(item["classification"])
            for field, maximum in (("title", 240), ("notes", 20000)):
                if field in payload:
                    item[field] = _text(payload[field], "標題" if field == "title" else "筆記", maximum, empty=field != "title")
            if "url" in payload:
                if item["kind"] != "link":
                    raise LibraryError("檔案項目不能修改網址。")
                try:
                    item["url"] = normalize_url(_text(payload["url"], "網址", 8192, empty=False))
                except ValueError as exc:
                    raise LibraryError(str(exc)) from exc
            if "collection_id" in payload:
                item["collection_id"] = self._collection_id(conn, payload["collection_id"])
            if "archived" in payload:
                item["archived"] = int(_boolean(payload["archived"], "封存狀態"))
            if "pinned" in payload:
                item["pinned"] = int(_boolean(payload["pinned"], "常用狀態"))
            category_override = bool(item["category_override"]) and not reclassify
            tags_override = bool(item["tags_override"]) and not reclassify
            if "category" in payload:
                item["category"] = _category(payload["category"])
                category_override = True
            if "tags" in payload:
                item["tags"] = _tags(payload["tags"])
                tags_override = True
            self._classify(item, category_override, tags_override)
            item["version"] += 1
            item["updated_at"] = _now()
            item["search_text"] = self._search_text(item)
            columns = [key for key in item if key != "id"]
            values = [_json(item[key]) if key in {"tags", "classification"} else item[key] for key in columns]
            conn.execute("UPDATE items SET " + ",".join(key + "=?" for key in columns) + " WHERE id=?", [*values, item["id"]])
            self._history(conn, item, "reclassify" if reclassify else "update", actor)
            return self._public(item)

    def item_history(self, item_id) -> list[dict]:
        with self._connection() as conn:
            self._get_row(conn, item_id)
            result = []
            for row in conn.execute("SELECT * FROM history WHERE item_id=? ORDER BY version", (item_id,)):
                entry = dict(row)
                entry["snapshot"] = json.loads(entry["snapshot"])
                entry["snapshot"]["pinned"] = bool(entry["snapshot"].get("pinned", False))
                result.append(entry)
            return result

    def add_file(self, name, data: bytes, mime="", collection_id=None, tags=None, notes="", actor="owner") -> dict:
        name = _text(name, "檔案名稱", 255, empty=False)
        if name in {".", ".."} or re.search(r"[/\\\x00-\x1f\x7f]", name):
            raise LibraryError("請提供正常檔案名稱，不能包含路徑或控制字元。")
        if not isinstance(data, bytes):
            raise LibraryError("檔案內容必須係 bytes。")
        if len(data) > MAX_FILE_SIZE:
            raise LibraryError("每個檔案最多 20 MB。", 413)
        mime = _text(mime, "檔案格式", 200).split(";", 1)[0].strip().lower()
        if mime and not re.fullmatch(r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+", mime):
            raise LibraryError("無效檔案格式。")
        mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
        notes = _text(notes, "筆記", 20000)
        checked_tags = _tags(tags) if tags is not None else None
        item = self._base_item("file", name[:240], notes, "", None)
        item.update(file_name=name, mime=mime, size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                    blob_name=uuid.uuid4().hex)
        if len(data) <= MAX_TEXT_SIZE and (mime.startswith("text/") or Path(name).suffix.casefold() in _TEXT_SUFFIXES or mime in {"application/json", "application/xml", "application/javascript"}):
            try:
                content = data.decode("utf-8-sig")
                if "\x00" not in content:
                    item["extracted_text"] = content
            except UnicodeDecodeError:
                pass  # Binary/non-UTF8 files keep metadata; no OCR or PDF extraction.
        if checked_tags is not None:
            item["tags"] = checked_tags
        self._classify(item, False, checked_tags is not None)
        blob = self.blobs_dir / item["blob_name"]
        temporary = None
        try:
            with self._connection() as conn:
                item["collection_id"] = self._collection_id(conn, collection_id)
                with tempfile.NamedTemporaryFile(dir=self.blobs_dir, prefix=".upload-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, blob)
                temporary = None
                return self._insert(conn, item, actor)
        except OSError as exc:
            blob.unlink(missing_ok=True)
            raise LibraryError("無法保存原始檔案。", 500) from exc
        except LibraryError:
            blob.unlink(missing_ok=True)
            raise
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get_file(self, item_id) -> tuple[Path, str, str]:
        with self._connection() as conn:
            item = self._get_row(conn, item_id)
            if item["kind"] != "file":
                raise LibraryError("呢項資料唔係檔案。", 404)
            blob = self.blobs_dir / item["blob_name"]
            if not re.fullmatch(r"[0-9a-f]{32}", item["blob_name"]) or blob.is_symlink() or blob.resolve().parent != self.blobs_dir.resolve():
                raise LibraryError("檔案儲存位置無效。", 500)
            if not blob.is_file():
                raise LibraryError("原始檔案遺失，請從完整備份還原。", 404)
            return blob, item["file_name"], item["mime"]

    def stats(self) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute("""SELECT COUNT(*) AS all_items,
                COALESCE(SUM(archived=0),0) AS total,
                COALESCE(SUM(archived=0 AND kind='file'),0) AS files,
                COALESCE(SUM(archived=0 AND kind='link'),0) AS links,
                COALESCE(SUM(archived=0 AND source_type='youtube'),0) AS youtube,
                COALESCE(SUM(archived=0 AND pinned=1),0) AS pinned,
                COALESCE(SUM(archived=0 AND collection_id IS NULL),0) AS unfiled,
                COALESCE(SUM(archived=1),0) AS archived FROM items""").fetchone()
            result = dict(row)
            result["collections"] = conn.execute("SELECT COUNT(*) FROM collections").fetchone()[0]
            result.update(self._workday_stats(conn))
            return result

    def export_metadata(self) -> dict:
        with self._connection() as conn:
            conn.execute("BEGIN")
            items = [self._public(row) for row in conn.execute("SELECT * FROM items ORDER BY created_at,id")]
            collections = [dict(row) for row in conn.execute("SELECT c.*,COUNT(i.id) AS count FROM collections c LEFT JOIN items i ON i.collection_id=c.id AND i.archived=0 GROUP BY c.id ORDER BY c.created_at,c.id")]
            history = []
            for row in conn.execute("SELECT * FROM history ORDER BY recorded_at,id"):
                entry = dict(row)
                entry["snapshot"] = json.loads(entry["snapshot"])
                entry["snapshot"]["pinned"] = bool(entry["snapshot"].get("pinned", False))
                history.append(entry)
            return {"format": "blueprint-library-metadata", "version": 1, "exported_at": _now(),
                    "includes_file_bytes": False,
                    "backup_note": "此檔只包含資料索引、文字及修改紀錄。完整備份亦需保存私人資料目錄內的原始檔案。",
                    "items": items, "collections": collections, "history": history,
                    "workdays": self._export_workdays(conn),
                    "company_profile": self._company_profile(conn)}
