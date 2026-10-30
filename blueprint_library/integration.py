"""Explicit local AI handoff; this module never reads chats or calls a model."""

from __future__ import annotations

from blueprint_folders.path_safety import has_link

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
from .remote import DEFAULT_PASSWORD_ENV, DEFAULT_USERNAME_ENV, backend_url, environment_name


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = "blueprint-workday-link"
_START = b"<!-- BLUEPRINT-WORKDAY:START -->"
_END = b"<!-- BLUEPRINT-WORKDAY:END -->"
_LEGACY_BLOCK = (
    "<!-- BLUEPRINT-WORKDAY:START -->\n"
    "## 藍圖每日工作紀錄\n\n"
    "使用者要求「整理今日工作，存入藍圖」或同義指令時，先讀取本目錄的 `WORKDAY.md`。\n"
    "只整理目前可見對話的有用成果、確認決定、參考及未完成待辦；按指引儲存，勿自行讀取其他聊天。\n"
    "<!-- BLUEPRINT-WORKDAY:END -->"
).encode("utf-8")
_BLOCK = (
    "<!-- BLUEPRINT-WORKDAY:START -->\n"
    "## 藍圖每日工作紀錄\n\n"
    "Codex、Claude Code、Gemini CLI 或其他具備工具權限的 AI：使用者說「今日收工」、"
    "「整理今日工作，存入藍圖」或同義指令時，先讀取本目錄的 `WORKDAY.md`。\n"
    "只整理目前可見或使用者明確提供的對話；保留有用成果、確認決定、參考及未完成待辦，勿自行讀取其他聊天。\n"
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
        "source": "AI 工具名稱",
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
        "今日收工：請整理目前可見或我明確提供的對話中，屬於使用者本地今天的有用工作內容，存入藍圖每日工作紀錄。\n"
        f"日期參考：{day}（預設香港）；以使用者本地日期為準，不能把伺服器 UTC 當作本地日期。\n\n"
        "適用於 Codex、Claude Code、Gemini CLI 及其他具備工具權限的 AI。"
        "只使用目前可見或使用者明確提供的內容，不讀取其他平台或其他聊天。保留真正完成的成果、"
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
        "直存需要先做一次 workday-connect，把私人工作室連到雲端後台網址或既有本機資料庫，"
        "並讓目前 AI 有執行工具權限；依設定文件完成這一次連結，之後遵循工作室 WORKDAY.md。"
        "雲端連線只在安全執行環境設定帳密變數，連結檔只保存變數名稱。"
        "使用者這次說『今日收工』或『整理今日工作，存入藍圖』已授權儲存整理後的紀錄，毋須每天再要求確認。"
        "同一日期、來源、source_key 和相同內容重送只會回傳現有紀錄；"
        "已有紀錄時先讀取內容，再合併新的有用成果；保留既有有用內容、待辦的 id 及目前完成狀態，"
        "只移除明顯雜訊或使用者要求刪除的項目。修改使用 workday-revise，JSON 為 expected_version、"
        "可選的 title/summary，以及完整新 entries 清單（既有項目須保留 id）；"
        "不能修改 date/source/source_key 或自行重設待辦完成狀態。"
        "版本或內容衝突回 409 時，須告知使用者並重新讀取既有紀錄，不可用隨機新 key 繞過衝突。"
        "每日工作歷史保留；整理日報不會自行刪除原始附件或書籤。\n\n"
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
            if has_link(current):
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
        if not isinstance(config.get("tool_path"), str) or not Path(config["tool_path"]).is_absolute():
            raise ValueError
        mode = config.get("mode", "local")
        if mode == "local":
            if not isinstance(config.get("data_path"), str) or not Path(config["data_path"]).is_absolute():
                raise ValueError
        elif mode == "cloud":
            if "data_path" in config:
                raise ValueError
            backend_url(config.get("url"))
            environment_name(config.get("username_env", DEFAULT_USERNAME_ENV))
            environment_name(config.get("password_env", DEFAULT_PASSWORD_ENV))
        else:
            raise ValueError
        return config
    except (OSError, ValueError, UnicodeError) as exc:
        raise LibraryError("已有自訂或無法辨認的 workday.json，未有覆寫。", 409) from exc


def _legacy_guide(workspace: Path, tool_root: Path) -> bytes:
    """Frozen v1.4 template: recognize it without accepting a customized file."""
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


def _guide(workspace: Path, tool_root: Path) -> bytes:
    command = "python3 " + shlex.quote(str(tool_root / "blueprint.py"))
    workspace_arg = " --workspace " + shlex.quote(str(workspace))
    return (
        "# 藍圖每日工作紀錄｜通用 AI 連結\n\n"
        "此檔由 workday-connect 管理；私人補充請放在其他文件，避免重新連結時產生衝突。\n"
        "適用於 Codex、Claude Code、Gemini CLI，以及其他可以讀取本文件及執行工具的 AI。"
        "使用者說「今日收工」、「整理今日工作，存入藍圖」或同義指令時，按以下流程執行。"
        "這次自然語言要求已授權儲存整理結果，毋須每天再要求確認。\n\n"
        "只整理目前可見或使用者明確提供、屬於使用者本地當天的對話：真正完成的成果、"
        "已確認決定、有用的設計／學習參考，以及具體未完成的待辦。"
        "去掉閒聊、重複、被否決方案及秘密；先按使用者要求刪改雜訊，再保存有用內容。"
        "不得虛構工作、完成狀態、資料來源或閱讀經驗；看不到日期或證據時說明限制。"
        "不得自行讀取其他聊天或聲稱能讀取所有平台對話；沒有有用內容時不建立紀錄。\n\n"
        "## 整理格式\n\n"
        "準備一個 JSON 物件，只用 date、source、source_key、title、summary、entries。"
        "date 是使用者本地 YYYY-MM-DD，source 是實際 AI 工具名稱，source_key 是本聊天的穩定識別加日期"
        "（stable-chat-key:YYYY-MM-DD），不能只用日期。已有紀錄便沿用 key；不清楚時先查詢當日並核對來源。"
        "entries 是 1 至 80 項，總文字最多 200,000 字，每項包含 type、title、content、tags；"
        "type 只接受 outcome、decision、reference、todo。可省略 url（已提供的 HTTP(S) 參考網址）；"
        "可省略 due_date（只用於 todo，且必須是已確認的 YYYY-MM-DD）。"
        "不要加 format、version、完成狀態或其他欄位；不要保存全部原始對話或秘密。\n\n"
        "## 先檢查既有紀錄\n\n"
        f"```sh\n{command} workday-list{workspace_arg} --date YYYY-MM-DD\n```\n\n"
        "將日期替換為使用者的本地日期。需要完整內容時用 workday-show 加紀錄編號。"
        "相同日期、來源及 source_key 的相同內容重送會去重；內容不同回 409 時，"
        "先告知使用者並讀取原有紀錄，不可隨機改 key 繞過。\n\n"
        f"```sh\n{command} workday-show{workspace_arg} RECORD_ID\n```\n\n"
        "若已有本聊天當日紀錄，先讀完整內容再合併新成果，保留既有有用內容及待辦 id；"
        "只刪除明顯雜訊或使用者要求移除的項目。後台會保留原有待辦完成狀態及歷史版本。"
        "修改 JSON 只用 expected_version（剛讀取的正整數版本）、可選 title/summary、"
        "entries（完整新清單，格式與新增相同，保留項目須帶原 id，新項目不帶 id）。"
        "不要提交 done 或自行重設完成進度；不能修改 date/source/source_key。\n\n"
        f"```sh\n{command} workday-revise{workspace_arg} RECORD_ID --input -\n```\n\n"
        "同樣透過標準輸入送 JSON。遇到 409，重新讀取再核對，不可用隨機 key 繞過。"
        "只修改整理紀錄，不會默默刪除原始附件或書籤。需要查看歷史可用 workday-history 加紀錄編號。\n\n"
        "## 儲存\n\n"
        "透過執行工具把 JSON 作為標準輸入，執行以下指令；不要把 JSON 插進 shell 字串：\n\n"
        f"```sh\n{command} workday-save{workspace_arg} --input -\n```\n\n"
        "指令會讀取 .blueprint/workday.json：local 模式使用已初始化的私人資料庫；"
        "cloud 模式使用後台 HTTPS API，從 username_env/password_env 指定的安全環境變數讀取帳密，"
        "不會寫入另一份本機資料庫。連結檔及本文件只包含環境變數名稱，不包含帳密。"
        "缺少安全環境設定時如實說明，不要求使用者把帳密貼到聊天，也不把值寫進工作室文件或記錄。"
        "只有指令回覆成功才說已儲存；失敗須如實說明。\n\n"
        "若沒有本機工具或安全連線權限，輸出有效 JSON 供後台每日工作頁預覽、編輯及匯入，"
        "不得聲稱已儲存。此整合沒有上傳附件、呼叫模型或自動讀取聊天。\n"
    ).encode("utf-8")


def _agents_content(existing: bytes) -> bytes:
    if _START in existing or _END in existing:
        if existing.count(_START) != 1 or existing.count(_END) != 1:
            raise LibraryError("AGENTS.md 的每日工作管理區塊有衝突，未有修改。", 409)
        start = existing.index(_START)
        end = existing.index(_END) + len(_END)
        block = existing[start:end]
        known = {_BLOCK, _BLOCK.replace(b"\n", b"\r\n"),
                 _LEGACY_BLOCK, _LEGACY_BLOCK.replace(b"\n", b"\r\n")}
        if block not in known:
            raise LibraryError("AGENTS.md 的每日工作管理區塊已自訂，未有覆寫。", 409)
        newline = b"\r\n" if b"\r\n" in block else b"\n"
        return existing[:start] + _BLOCK.replace(b"\n", newline) + existing[end:]
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


def connect_workday(workspace, data_dir=None, tool_root=None, *, url=None,
                    username_env=DEFAULT_USERNAME_ENV, password_env=DEFAULT_PASSWORD_ENV) -> dict:
    """Bind a private workspace without replacing its own code or guidance."""
    if (data_dir is None) == (url is None):
        raise LibraryError("每日工作連結需要選擇本機資料目錄或雲端網址其中一項。")
    remote_url = backend_url(url) if url is not None else None
    if remote_url is not None:
        username_env = environment_name(username_env)
        password_env = environment_name(password_env)
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
    known_guides = {guide, _legacy_guide(workspace, tool)}
    if config is not None:
        known_guides.add(_guide(workspace, Path(config["tool_path"])))
        known_guides.add(_legacy_guide(workspace, Path(config["tool_path"])))
    if guide_path.exists() and guide_path.read_bytes() not in known_guides:
        raise LibraryError("已有自訂 WORKDAY.md，未有覆寫；請先自行保留或重新命名。", 409)
    agents = _agents_content(agents_path.read_bytes() if agents_path.exists() else b"")
    data = _library_path(data_dir, tool) if remote_url is None else None
    updated = dict(config or {})
    updated.update(schema=_SCHEMA, version=1, tool_path=str(tool))
    if data is not None:
        for field in ("url", "username_env", "password_env"):
            updated.pop(field, None)
        updated.update(mode="local", data_path=str(data))
    else:
        updated.pop("data_path", None)
        updated.update(mode="cloud", url=remote_url, username_env=username_env, password_env=password_env)
    encoded = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
        private.mkdir(exist_ok=True, mode=0o700)
        _write_bytes(guide_path, guide)
        _write_bytes(agents_path, agents)
        # Keep the owner's formatting and line endings when the effective
        # binding has not changed; reconnect is a readback, not a rewrite.
        if config != updated:
            _write_bytes(config_path, encoded)
    except OSError as exc:
        raise LibraryError("未能寫入工作室的每日工作連結，請檢查路徑及權限。", 500) from exc
    result = {"workspace_path": str(workspace), "guide_path": str(guide_path), "config_path": str(config_path)}
    result.update({"data_path": str(data)} if data is not None else {"mode": "cloud", "url": remote_url})
    return result


def get_workday_binding(workspace) -> dict:
    """Validate a private binding without reading credentials or contacting it."""
    workspace = _safe_path(workspace, "工作室路徑")
    _separate(workspace, _SOURCE_ROOT, "工作室")
    config = _read_binding(workspace / ".blueprint" / "workday.json")
    if config is None:
        raise LibraryError("此工作室未連結每日工作紀錄，請先執行 workday-connect。")
    tool = _safe_path(config["tool_path"], "工具路徑")
    _separate(workspace, tool, "工作室")
    result = dict(config)
    result["mode"] = result.get("mode", "local")
    if result["mode"] == "cloud":
        result["url"] = backend_url(result["url"])
        result.setdefault("username_env", DEFAULT_USERNAME_ENV)
        result.setdefault("password_env", DEFAULT_PASSWORD_ENV)
    return result


def resolve_workday_data(workspace) -> Path:
    """Resolve a local binding; cloud must never create or select a local DB."""
    config = get_workday_binding(workspace)
    if config["mode"] == "cloud":
        raise LibraryError("此工作室連結雲端後台，請使用支援雲端的 workday-save/list/show；不會改用本機資料庫。")
    tool = _safe_path(config["tool_path"], "工具路徑")
    return _library_path(config["data_path"], tool)
