"""Explicit local AI handoff; this module never reads chats or calls a model."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import sqlite3
import stat
import tempfile

from .store import LibraryError, Store


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = "blueprint-workday-link"
_START = b"<!-- BLUEPRINT-WORKDAY:START -->"
_END = b"<!-- BLUEPRINT-WORKDAY:END -->"
_BLOCK = (
    "<!-- BLUEPRINT-WORKDAY:START -->\n"
    "## 藍圖每日工作紀錄\n\n"
    "使用者要求「整理今日工作，存入藍圖」或同義指令時，先讀取本目錄的 `WORKDAY.md`。\n"
    "只整理目前可見對話的有用成果、確認決定、參考及未完成待辦；按指引儲存，勿自行讀取其他聊天。\n"
    "<!-- BLUEPRINT-WORKDAY:END -->"
).encode("utf-8")


def _day(today=None) -> str:
    if today is None:
        # The product's default locale is Hong Kong; the instruction still asks
        # the acting AI to use the user's own date if their locale differs.
        today = datetime.now(timezone(timedelta(hours=8))).date()
    if isinstance(today, datetime):
        today = today.date()
    if isinstance(today, date):
        return today.isoformat()
    if not isinstance(today, str):
        raise LibraryError("工作日期需要 YYYY-MM-DD。")
    try:
        parsed = date.fromisoformat(today)
    except ValueError as exc:
        raise LibraryError("工作日期需要 YYYY-MM-DD。") from exc
    if parsed.isoformat() != today:
        raise LibraryError("工作日期需要 YYYY-MM-DD。")
    return today


def workday_example(today=None) -> dict:
    """Return an explicitly labelled format example, not a claimed work log."""
    day = _day(today)
    return {
        "date": day,
        "source": "Codex",
        "source_key": "example-chat:" + day,
        "title": "工作紀錄格式示範",
        "summary": "以下只是格式示範，並非實際工作紀錄；請以目前對話的真實內容替換。",
        "entries": [
            {"type": "outcome", "title": "示範：已完成成果",
             "content": "填入本次對話確實完成的有用成果。", "tags": ["示範"]},
            {"type": "decision", "title": "示範：確認決定",
             "content": "填入使用者已確認的決定，排除已否決的方案。", "tags": ["示範"]},
            {"type": "reference", "title": "示範：設計或學習參考",
             "content": "填入本次對話實際提供的參考；未讀過的資料不能聲稱已閱讀。", "tags": ["示範"]},
            {"type": "todo", "title": "示範：具體未完成工作",
             "content": "填入一項可執行的下一步；沒有確定期限時省略 due_date。", "tags": ["示範"]},
        ],
    }


def workday_instruction(today=None) -> str:
    """Instructions usable in the current chat, including a browser fallback."""
    day = _day(today)
    example = json.dumps(workday_example(day), ensure_ascii=False, indent=2)
    return (
        "請整理目前這個聊天中，屬於使用者本地今天的有用工作內容，存入藍圖每日工作紀錄。\n"
        f"日期參考：{day}（預設香港）；以使用者本地日期為準，不能把伺服器 UTC 當作本地日期。\n\n"
        "只使用你目前可見的這個對話，不讀取其他平台或其他聊天。保留真正完成的成果、"
        "已確認的決定、有用的設計／學習參考，以及具體而未完成的待辦。"
        "去掉閒聊、重複內容、已否決方案和密碼、金鑰等秘密。不得杜撰工作、決定、"
        "資源或閱讀經驗；若看不到日期歸屬或完成證據，說明限制，不當作今天已完成。"
        "沒有可保存內容時直接說明，不為湊數建立紀錄。\n\n"
        "輸出一個 JSON 物件，只有 date、source、source_key、title、summary、entries。"
        "source 填目前實際使用的 AI 工具名稱。source_key 必須是本聊天的穩定識別加本地日期，"
        "例如 stable-chat-key:YYYY-MM-DD；不能只用日期，不能採用下列示範 key。"
        "沿用已儲存的 key；不知既有 key 時，先查該日紀錄再核對來源。"
        "entries 為 1 至 80 項，總文字最多 200,000 字，每項只用 type、title、content、"
        "url、tags、due_date；type 只可為 outcome、decision、reference、todo。"
        "tags 為文字清單；url 可省略，提供時必須是對話中已有的 HTTP(S) 網址。"
        "due_date 可省略，只可用於 todo，而且必須是已確定的 YYYY-MM-DD。"
        "不要加 format、version、完成狀態或其他欄位。\n\n"
        "本機直存需要先做一次 workday-connect 連結工作室和已初始化的私人資料庫，"
        "並讓目前的 AI 工具有本機指令權限；依設定文件完成這一次連結，之後遵循工作室 WORKDAY.md。"
        "使用者這次說『整理今日工作，存入藍圖』已授權儲存整理後的紀錄，毋須每天再要求確認。"
        "同一日期、來源、source_key 和相同內容重送只會回傳現有紀錄；"
        "內容不同會回 409，須告知使用者並檢查既有紀錄，不可用隨機新 key 繞過衝突。\n\n"
        "若目前沒有本機連結／工具權限，請只輸出可貼入後台的有效 JSON，"
        "讓使用者在每日工作頁預覽、編輯後匯入；不要聲稱已儲存或已連接 AI。"
        "不要要求使用者把後台帳號、密碼或 API 金鑰貼到對話。\n\n"
        "以下只是格式示範，所有示範內容及 key 都要替換成實際資料：\n"
        f"```json\n{example}\n```\n"
    )


def _safe_path(value, label) -> Path:
    """Reject symlinks in every supplied path component before resolving it."""
    try:
        if isinstance(value, str) and (not value.strip() or any(ord(char) < 32 for char in value)):
            raise ValueError
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        current = Path(path.anchor)
        for component in path.parts[1:]:
            current = current / component
            if current.is_symlink():
                raise LibraryError(f"{label}不能包含符號連結。")
        return path.resolve()
    except (TypeError, ValueError, OSError, RuntimeError) as exc:
        if isinstance(exc, LibraryError):
            raise
        raise LibraryError(f"請提供有效的{label}。") from exc


def _separate(path: Path, root: Path, label: str):
    if path == root or root in path.parents or path in root.parents:
        raise LibraryError(f"{label}必須放在公開工具專案以外，兩者不能互相包含。")


def _ordinary_file(path: Path, label: str, *, required=False):
    _safe_path(path, label)
    if path.exists() and not path.is_file():
        raise LibraryError(f"{label}必須是正常檔案。", 409)
    if required and not path.is_file():
        raise LibraryError(f"找不到{label}，請先初始化資料庫或修復連結。")


def _library_path(data_dir, tool_root=None) -> Path:
    data = _safe_path(data_dir, "私人資料目錄")
    _separate(data, _SOURCE_ROOT, "私人資料目錄")
    if tool_root is not None:
        _separate(data, tool_root, "私人資料目錄")
    if not data.is_dir():
        raise LibraryError("找不到私人資料目錄；請先執行 library-init，未有建立空資料庫。")
    db = data / "library.sqlite3"
    _ordinary_file(db, "library.sqlite3", required=True)
    _ordinary_file(data / "access.json", "access.json", required=True)
    # A typo pointing to an empty SQLite file must not be initialized by Store.
    # Open read-only first; do not read or copy credentials into the binding.
    try:
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"collections", "items", "history"} <= tables:
                raise LibraryError("此位置不是已初始化的藍圖資料庫，未有建立或覆寫資料。")
    except sqlite3.Error as exc:
        raise LibraryError("無法讀取既有資料庫，請先檢查私人資料位置。") from exc
    Store(data)  # Existing library only: enforce its version and storage guards.
    return data


def existing_workday_data(data_dir) -> Path:
    """Validate an already initialized private library without creating one."""
    return _library_path(data_dir)


def _read_binding(config_path: Path) -> dict | None:
    _ordinary_file(config_path, "workday.json")
    if not config_path.exists():
        return None
    try:
        if config_path.stat().st_size > 1024 * 1024:
            raise ValueError
        config = json.loads(config_path.read_bytes().decode("utf-8-sig"))
        if (not isinstance(config, dict) or config.get("schema") != _SCHEMA
                or type(config.get("version")) is not int or config["version"] != 1):
            raise ValueError
        for key in ("data_path", "tool_path"):
            if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
                raise ValueError
        return config
    except (OSError, ValueError, UnicodeError) as exc:
        raise LibraryError("已有自訂或無法辨認的 workday.json，未有覆寫。", 409) from exc


def _guide(workspace: Path, tool_root: Path) -> bytes:
    command = "python3 " + shlex.quote(str(tool_root / "blueprint.py"))
    workspace_arg = " --workspace " + shlex.quote(str(workspace))
    # Keep this template deterministic so only exact known generated files can
    # be updated after moving the shared tool checkout.
    return (
        "# 藍圖每日工作紀錄｜本機連結\n\n"
        "此檔由 workday-connect 管理；私人補充請放在其他文件，避免重新連結時產生衝突。\n"
        "使用者說「整理今日工作，存入藍圖」或同義指令時，按以下流程執行。"
        "這次自然語言要求已授權儲存整理結果，毋須每天再要求確認。\n\n"
        "只整理目前可見、屬於使用者本地當天的對話：真正完成的成果、已確認決定、"
        "有用的設計／學習參考，以及具體未完成的待辦。去掉閒聊、重複、被否決方案及秘密。"
        "不得虛構工作、完成狀態、資料來源或閱讀經驗；看不到日期或證據時說明限制。"
        "不得自行讀取其他聊天或聲稱能讀取所有平台對話；沒有有用內容時不建立紀錄。\n\n"
        "## 儲存方式\n\n"
        "準備一個 JSON 物件，date 是使用者本地 YYYY-MM-DD，source 是實際 AI 工具名稱（例如 Codex），"
        "source_key 是本聊天的穩定識別加日期（stable-chat-key:YYYY-MM-DD），不能只用日期。"
        "若已有儲存紀錄便沿用 key；不清楚時先查詢該日並核對來源。title 及 summary 為精簡文字。\n"
        "entries 是 1 至 80 項有用記錄，總文字最多 200,000 字。每項包含 type、title、content、tags；"
        "type 只接受 outcome、decision、reference、todo。可省略 url（已有的 HTTP(S) 參考網址）；"
        "可省略 due_date（只用於 todo，且必須是已確認的 YYYY-MM-DD）。"
        "不要加 format、version、完成狀態或其他欄位。沒有秘密進入 JSON，也不把全部原始對話直接存入。\n\n"
        "查詢當日既有紀錄（將日期替換為使用者的本地日期）：\n\n"
        f"```sh\n{command} workday-list{workspace_arg} --date YYYY-MM-DD\n```\n\n"
        "透過執行工具把 JSON 作為標準輸入，執行以下指令。"
        "不要把 JSON 插進 shell 字串，也不要加入密碼、金鑰或登入設定；本機連結不需傳送帳密。\n\n"
        f"```sh\n{command} workday-save{workspace_arg} --input -\n```\n\n"
        "同一日期、來源及 source_key 的相同內容重送會去重。不同內容回 409 時，"
        "須告知使用者並檢查現有紀錄，不可隨機改 key 繞過衝突或覆寫。"
        "只有指令回覆成功才說已儲存；失敗須如實說明。\n\n"
        "若無本機工具權限，輸出有效 JSON 供後台每日工作頁預覽、編輯及匯入，"
        "不得聲稱已儲存。只有目前 AI 已安全設定 API 連線時，才可改用 POST /api/workdays，"
        "沿用既有 Basic 驗證、application/json 及 X-Blueprint-Request: 1；"
        "不要要求把帳密貼到對話。此整合沒有上傳附件、呼叫模型或自動讀取聊天。\n"
    ).encode("utf-8")


def _agents_content(existing: bytes) -> bytes:
    if _START in existing or _END in existing:
        if existing.count(_START) != 1 or existing.count(_END) != 1:
            raise LibraryError("AGENTS.md 的每日工作管理區塊有衝突，未有修改。", 409)
        start = existing.index(_START)
        end = existing.index(_END) + len(_END)
        block = existing[start:end]
        if block not in {_BLOCK, _BLOCK.replace(b"\n", b"\r\n")}:
            raise LibraryError("AGENTS.md 的每日工作管理區塊已自訂，未有覆寫。", 409)
        return existing
    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    block = _BLOCK.replace(b"\n", newline)
    separator = b"" if not existing or existing.endswith(newline * 2) else (
        newline if existing.endswith(newline) else newline * 2)
    return existing + separator + block + newline


def _write_bytes(path: Path, value: bytes):
    _ordinary_file(path, path.name)
    if path.exists() and path.read_bytes() == value:
        return
    temporary = None
    try:
        previous_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".workday-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        if previous_mode is not None:
            temporary.chmod(previous_mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def connect_workday(workspace, data_dir, tool_root=None) -> dict:
    """Bind a private workspace without replacing its own code or guidance."""
    workspace = _safe_path(workspace, "工作室路徑")
    tool = _safe_path(_SOURCE_ROOT if tool_root is None else tool_root, "工具路徑")
    _separate(workspace, _SOURCE_ROOT, "工作室")
    _separate(workspace, tool, "工作室")
    if workspace.exists() and not workspace.is_dir():
        raise LibraryError("工作室路徑必須是正常目錄。", 409)
    _ordinary_file(tool / "blueprint.py", "blueprint.py", required=True)
    private = _safe_path(workspace / ".blueprint", ".blueprint")
    if private.exists() and not private.is_dir():
        raise LibraryError(".blueprint 必須是正常目錄。", 409)
    config_path = private / "workday.json"
    guide_path = workspace / "WORKDAY.md"
    agents_path = workspace / "AGENTS.md"
    config = _read_binding(config_path)
    _ordinary_file(guide_path, "WORKDAY.md")
    _ordinary_file(agents_path, "AGENTS.md")
    guide = _guide(workspace, tool)
    known_guides = {guide}
    if config is not None:
        known_guides.add(_guide(workspace, Path(config["tool_path"])))
    if guide_path.exists() and guide_path.read_bytes() not in known_guides:
        raise LibraryError("已有自訂 WORKDAY.md，未有覆寫；請先自行保留或重新命名。", 409)
    agents = _agents_content(agents_path.read_bytes() if agents_path.exists() else b"")
    data = _library_path(data_dir, tool)
    updated = dict(config or {})
    updated.update(schema=_SCHEMA, version=1, data_path=str(data), tool_path=str(tool))
    encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        private.mkdir(exist_ok=True, mode=0o700)
        _write_bytes(guide_path, guide)
        _write_bytes(agents_path, agents)
        _write_bytes(config_path, encoded)
    except OSError as exc:
        raise LibraryError("未能寫入工作室的每日工作連結，請檢查路徑及權限。", 500) from exc
    return {"workspace_path": str(workspace), "guide_path": str(guide_path),
            "config_path": str(config_path), "data_path": str(data)}


def resolve_workday_data(workspace) -> Path:
    """Resolve an existing explicit binding; never initialize a missing library."""
    workspace = _safe_path(workspace, "工作室路徑")
    _separate(workspace, _SOURCE_ROOT, "工作室")
    config = _read_binding(workspace / ".blueprint" / "workday.json")
    if config is None:
        raise LibraryError("此工作室未連結每日工作紀錄，請先執行 workday-connect。")
    tool = _safe_path(config["tool_path"], "工具路徑")
    _separate(workspace, tool, "工作室")
    return _library_path(config["data_path"], tool)
