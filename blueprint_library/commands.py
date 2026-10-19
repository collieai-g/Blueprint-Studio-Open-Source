"""Local AI tools save curated work without putting private records in Git."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .integration import connect_workday, existing_workday_data, resolve_workday_data
from .store import LibraryError, Store


MAX_INPUT_BYTES = 512 * 1024


def _read_result(filename):
    if filename == "-":
        stream = getattr(sys.stdin, "buffer", sys.stdin)
        raw = stream.read(MAX_INPUT_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
    else:
        path = Path(filename).expanduser()
        if not path.is_file():
            raise LibraryError("請提供整理結果的 JSON 檔案。")
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise LibraryError("整理結果最多 512 KiB，請只保留值得保存的內容。", 413)
    try:
        result = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, ValueError) as exc:
        raise LibraryError("請提供有效的 UTF-8 JSON 整理結果。") from exc
    if not isinstance(result, dict):
        raise LibraryError("整理結果必須是 JSON 物件。")
    return result


def run_workday_command(args):
    if args.command == "workday-connect":
        return connect_workday(args.path, args.data)
    data_path = (existing_workday_data(args.data) if args.data
                 else resolve_workday_data(args.workspace))
    store = Store(data_path)
    if args.command == "workday-save":
        result = store.import_workday(_read_result(args.input), actor="local-ai")
        workday = result["workday"]
        # Logs should confirm a save without repeating private conversation text.
        return {"id": workday["id"], "date": workday["date"], "title": workday["title"],
                "entry_count": len(workday["entries"]), "created": result["created"],
                "duplicate": result["duplicate"]}
    if args.command == "workday-list":
        return store.list_workdays(q=args.query, date=args.date)
    if args.command == "workday-show":
        return {"workday": store.get_workday(args.id)}
    raise LibraryError("不支援的每日工作指令。")
