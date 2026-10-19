"""AI tools save curated work to an explicit local or cloud company backend."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from .integration import connect_workday, existing_workday_data, get_workday_binding, resolve_workday_data
from .remote import DEFAULT_PASSWORD_ENV, DEFAULT_USERNAME_ENV, WorkdayRemote
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
        return connect_workday(args.path, getattr(args, "data", None),
                               url=getattr(args, "url", None),
                               username_env=getattr(args, "username_env", DEFAULT_USERNAME_ENV),
                               password_env=getattr(args, "password_env", DEFAULT_PASSWORD_ENV))
    if getattr(args, "data", None):
        store = Store(existing_workday_data(args.data))
        remote = None
    else:
        binding = get_workday_binding(args.workspace)
        remote = WorkdayRemote(binding) if binding["mode"] == "cloud" else None
        store = None if remote is not None else Store(resolve_workday_data(args.workspace))
    if args.command == "workday-save":
        payload = _read_result(args.input)
        result = remote.save(payload) if remote else store.import_workday(payload, actor="local-ai")
        return _save_summary(result)
    if args.command == "workday-revise":
        payload = _read_result(args.input)
        result = remote.revise(args.id, payload) if remote else store.update_workday(args.id, payload, actor="local-ai")
        return _save_summary(result, revision=True)
    if args.command == "workday-list":
        return remote.list(q=args.query, date=args.date) if remote else store.list_workdays(q=args.query, date=args.date)
    if args.command == "workday-show":
        return remote.show(args.id) if remote else {"workday": store.get_workday(args.id)}
    if args.command == "workday-history":
        return remote.history(args.id) if remote else {"versions": store.item_workday_history(args.id)}
    raise LibraryError("不支援的每日工作指令。")


def _save_summary(result, *, revision=False):
    workday = result.get("workday")
    if (not isinstance(workday, dict) or not isinstance(workday.get("entries"), list)
            or any(not isinstance(workday.get(key), str) for key in ("id", "date", "title"))):
        raise LibraryError("後台儲存回覆格式無效，請先查詢紀錄確認結果。", 502)
    summary = {"id": workday["id"], "date": workday["date"], "title": workday["title"],
               "entry_count": len(workday["entries"])}
    keys = ("updated",) if revision else ("created", "duplicate")
    for key in keys:
        if not isinstance(result.get(key), bool):
            raise LibraryError("後台儲存回覆格式無效，請先查詢紀錄確認結果。", 502)
        summary[key] = result[key]
    return summary
