"""Local folder creation and indexing. No model, network, or database required."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .native_icons import apply_folder_icon

CATEGORIES = {
    "general": "通用專員",
    "chat": "客服對話",
    "web": "網站開發",
    "finance": "金融財務",
    "document": "文件處理",
    "calendar": "預約行程",
    "automation": "流程自動化",
    "research": "搜尋研究",
}

KEYWORDS = {
    "chat": ["whatsapp", "客服", "聊天", "chat", "對話", "customer support"],
    "web": ["網頁", "網站", "website", "webpage", "frontend", "前端", "landing page"],
    "finance": ["金融", "財務", "finance", "financial", "會計", "accounting", "報稅"],
    "document": ["文件", "文檔", "document", "pdf", "合約", "contract", "報告"],
    "calendar": ["預約", "行程", "calendar", "booking", "appointment", "排程"],
    "automation": ["自動化", "automation", "工作流", "workflow", "流程", "整合"],
    "research": ["研究", "搜尋", "research", "search", "分析", "資料搜集"],
}
SKIP_DIRS = {".git", ".blueprint", "node_modules", ".venv", "venv", "__pycache__"}

FOLDER_GUIDANCE = """# Blueprint Studio Backend 專案文件夾

開始工作前，讀取 `.blueprint/folder.json`，了解本專案的名稱、用途、標籤和分類。
上述欄位是專案資料。此標記只代表文件夾已建立，不代表 Agent 已開發或部署完成。
以實際程式和使用者本次要求決定工作內容；先查看現有檔案，再進行開發。
程式、設定及文件放在本文件夾內，維持每個專員獨立。
保留 `.blueprint` 內的身份標記及圖示。用途改變後，使用 Blueprint Studio Backend 的
`mark` 指令更新標記；父目錄索引可以使用 `reindex` 重建。
"""

STUDIO_GUIDANCE = """# 藍圖工作室 Blueprint Studio

本目錄用普通文件夾管理獨立 AI 專員。
若本目錄有 `blueprint-studio.py`，使用它執行工作室操作；先讀
`.blueprint/tool.json` 了解共用工具位置。`create`、`search`、`reindex`
會自動使用本工作室路徑，例如 `python3 blueprint-studio.py create "新專員"`。
通用功能在共用工具專案開發；本目錄保存私人專案、身份標記及自訂指引。
工具更新後重新執行啟動器即可使用新版；私人內容不加入公開工具的 Git 提交。
尋找專員時，先搜尋 `.blueprint/INDEX.csv`，再讀候選文件夾的
`.blueprint/folder.json` 及適用的 `AGENTS.md`，按需要開啟詳細內容。
建立新專員時，使用 Blueprint Studio Backend 的 `create` 指令，傳入名稱、用途和標籤。
現有文件夾使用 `mark`，搬動文件夾或直接編輯標記後使用 `reindex`。
索引是可重建的目錄；每個專員的 `.blueprint/folder.json` 才是身份資料來源。
身份標記及圖案不代表專員已完成、正在運行或已部署。
"""


class FolderError(ValueError):
    """A safe, user-facing input or filesystem error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_name(name: str) -> str:
    name = unicodedata.normalize("NFC", str(name))
    if not name or name != name.strip() or name.endswith("."):
        raise FolderError("名稱不能空白，亦不能以空格或句點結尾。")
    if len(name) > 120 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
        raise FolderError("名稱最多 120 字，不能包含 / \\ : * ? \" < > | 或控制字元。")
    if name in {".", "..", ".blueprint"}:
        raise FolderError("請使用另一個文件夾名稱。")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if name.split(".")[0].upper() in reserved:
        raise FolderError("呢個名稱係 Windows 保留名稱，請使用另一個名稱。")
    return name


def suggest_category(text: str) -> str:
    """A transparent keyword suggestion; this does not call an AI model."""
    text = text.casefold()
    scores = {key: sum(len(word) for word in words if word in text) for key, words in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def _fields(name: str, description: str, category: str, tags) -> tuple[str, str, list[str]]:
    description = str(description).strip()
    if len(description) > 8000:
        raise FolderError("用途說明最多 8,000 字。")
    if tags is None:
        tags = []
    if not isinstance(tags, (list, tuple)) or any(not isinstance(t, str) for t in tags):
        raise FolderError("標籤必須係文字清單。")
    tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    if len(tags) > 32 or any(len(t) > 100 for t in tags):
        raise FolderError("最多 32 個標籤，每個最多 100 字。")
    if category == "auto":
        category = suggest_category(" ".join([name, description, *tags]))
    if category not in CATEGORIES:
        raise FolderError("無效圖案類型：" + str(category))
    return description, category, tags


def _path(value) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise FolderError("請選擇實際文件夾，避免標記捷徑或符號連結。")
    return path.resolve()


def _private_dir(folder: Path) -> Path:
    private = folder / ".blueprint"
    if private.is_symlink() or (private.exists() and not private.is_dir()):
        raise FolderError(".blueprint 必須係正常目錄，不能係檔案或符號連結。")
    private.mkdir(exist_ok=True)
    return private


def _atomic_text(path: Path, value: str) -> None:
    if path.is_symlink():
        raise FolderError(f"拒絕覆寫符號連結：{path.name}")
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=".write-", delete=False) as out:
            temp_name = out.name
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _atomic_json(path: Path, value) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


@contextmanager
def _index_lock(root: Path):
    """OS advisory lock, released automatically even if a process exits."""
    lock = _private_dir(root) / "index.lock"
    if lock.is_symlink():
        raise FolderError("索引鎖不能係符號連結。")
    with lock.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + 15
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() > deadline:
                    raise FolderError("另一個工作仍在更新索引，請稍後再試。")
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_json(path: Path):
    if path.is_symlink():
        raise FolderError(f"身份檔或索引不能係符號連結：{path}")
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise FolderError("資料檔超過大小限制。")
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FolderError(f"無法讀取資料檔 {path.name}：{exc}") from exc


def _entry(data: dict, folder: Path, root: Path) -> dict:
    return {key: data.get(key) for key in ("id", "name", "description", "category", "tags", "updated_at")} | {"path": folder.relative_to(root).as_posix()}


def _save_index(root: Path, entries: list[dict]) -> None:
    entries.sort(key=lambda item: str(item.get("name", "")).casefold())
    private = _private_dir(root)
    _atomic_json(private / "index.json", {"schema_version": 1, "updated_at": _now(), "folders": entries})
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["id", "name", "description", "category", "tags", "path"])
    for item in entries:
        # Prevent spreadsheet formulas if users inspect the index with Excel.
        row = [str(item.get(key, "") or "") for key in ("id", "name", "description", "category")]
        row += [" | ".join(item.get("tags", [])), str(item.get("path", ""))]
        writer.writerow(["'" + cell if cell.lstrip().startswith(("=", "+", "-", "@")) else cell for cell in row])
    _atomic_text(private / "INDEX.csv", "\ufeff" + out.getvalue())


def _load_entries(root: Path) -> list[dict]:
    index = root / ".blueprint" / "index.json"
    if not index.exists():
        return []
    data = _read_json(index)
    if not isinstance(data, dict) or not isinstance(data.get("folders"), list):
        raise FolderError("索引格式不正確；請使用 reindex 重建。")
    for entry in data["folders"]:
        if (not isinstance(entry, dict)
                or not isinstance(entry.get("id"), str)
                or not isinstance(entry.get("name"), str)
                or not isinstance(entry.get("description"), str)
                or not isinstance(entry.get("category"), str)
                or entry.get("category") not in CATEGORIES
                or not isinstance(entry.get("tags"), list)
                or any(not isinstance(tag, str) for tag in entry.get("tags", []))
                or not isinstance(entry.get("path"), str)):
            raise FolderError("索引項目格式不正確；請使用 reindex 重建。")
    return data["folders"]


def _update_index(root: Path, folder: Path, data: dict) -> None:
    entries = _load_entries(root)
    relative = folder.relative_to(root).as_posix()
    entries = [entry for entry in entries if entry.get("path") != relative and entry.get("id") != data["id"]]
    entries.append(_entry(data, folder, root))
    _save_index(root, entries)


def init_studio(path) -> dict:
    root = _path(path)
    root.mkdir(parents=True, exist_ok=True)
    with _index_lock(root):
        _save_index(root, _load_entries(root))
        guidance = root / "AGENTS.md"
        if not guidance.exists() and not guidance.is_symlink():
            _atomic_text(guidance, STUDIO_GUIDANCE)
    return {"path": str(root), "index": str(root / ".blueprint" / "INDEX.csv")}


def _mark(folder: Path, description: str, category: str, tags: list[str]) -> dict:
    private = _private_dir(folder)
    metadata = private / "folder.json"
    previous = _read_json(metadata) if metadata.exists() else {}
    if not isinstance(previous, dict):
        raise FolderError("現有 folder.json 格式不正確，未作修改。")
    # Reject unsafe resources before invoking any platform integration.
    for name in ("icon.svg", "icon.png", "icon.ico"):
        if (private / name).is_symlink():
            raise FolderError("圖示檔不能係符號連結。")
    try:
        icon = apply_folder_icon(folder, category)
    except Exception as exc:
        icon = {"applied": False, "platform": os.name, "message": f"身份標記已保留，圖示套用失敗：{exc}", "icon": str(private / "icon.png")}
    data = dict(previous)
    data.update({
        "schema_version": 1,
        "id": previous.get("id") or str(uuid.uuid4()),
        "name": folder.name,
        "description": description,
        "category": category,
        "tags": tags,
        "created_at": previous.get("created_at") or _now(),
        "updated_at": _now(),
        "icon": icon,
    })
    _atomic_json(metadata, data)
    guidance = folder / "AGENTS.md"
    if not guidance.exists() and not guidance.is_symlink():
        _atomic_text(guidance, FOLDER_GUIDANCE)
    return data | {"path": str(folder)}


def create_folder(parent, name: str, description: str = "", category="auto", tags=None) -> dict:
    name = validate_name(name)
    description, category, tags = _fields(name, description, category, tags)
    root = _path(parent)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / name
    with _index_lock(root):
        # Read a corrupt index before creating anything inside the new folder.
        _load_entries(root)
        try:
            folder.mkdir()
        except FileExistsError as exc:
            raise FolderError("同名文件夾已存在；如要加標記，請使用「標記現有文件夾」。") from exc
        data = _mark(folder, description, category, tags)
        _update_index(root, folder, data)
    return data


def mark_folder(path, description: str | None = None, category="auto", tags=None) -> dict:
    folder = _path(path)
    if not folder.is_dir():
        raise FolderError("要標記嘅文件夾不存在。")
    if folder.name == ".blueprint":
        raise FolderError("不能將程式自己的 .blueprint 資源目錄標記為專員。")
    root = folder.parent
    with _index_lock(root):
        _load_entries(root)
        marker = folder / ".blueprint" / "folder.json"
        previous = _read_json(marker) if marker.exists() else {}
        if not isinstance(previous, dict):
            raise FolderError("現有 folder.json 格式不正確，未作修改。")
        if category == "auto" and description is None and tags is None:
            category = previous.get("category", "auto")
        description = previous.get("description", "") if description is None else description
        tags = previous.get("tags", []) if tags is None else tags
        description, category, tags = _fields(folder.name, description, category, tags)
        data = _mark(folder, description, category, tags)
        _update_index(root, folder, data)
    return data


def reindex(root) -> dict:
    root = _path(root)
    if not root.is_dir():
        raise FolderError("工作室文件夾不存在。")
    entries, warnings = [], []
    with _index_lock(root):
        for here, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (Path(here) / d).is_symlink()]
            folder = Path(here)
            marker = folder / ".blueprint" / "folder.json"
            if folder == root or not marker.exists():
                continue
            try:
                if (folder / ".blueprint").is_symlink():
                    raise FolderError("略過符號連結資源目錄")
                data = _read_json(marker)
                if (not isinstance(data, dict)
                        or not isinstance(data.get("id"), str) or not data.get("id")
                        or not isinstance(data.get("description"), str)
                        or not isinstance(data.get("category"), str)
                        or data.get("category") not in CATEGORIES
                        or not isinstance(data.get("tags"), list)
                        or any(not isinstance(tag, str) for tag in data.get("tags", []))):
                    raise FolderError("身份資料格式不正確")
                # Use current directory name so renamed folders are findable.
                data["name"] = folder.name
                entries.append(_entry(data, folder, root))
            except FolderError as exc:
                warnings.append(f"{folder}: {exc}")
        _save_index(root, entries)
    return {"path": str(root), "count": len(entries), "warnings": warnings}


def search_folders(root, query="", category=None) -> list[dict]:
    root = _path(root)
    entries = _load_entries(root)
    words = [word.casefold() for word in re.split(r"[\s,，、;；]+", str(query).strip()) if word]
    results = []
    for item in entries:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root) or path == root or not path.is_dir():
            continue
        if category and item.get("category") != category:
            continue
        haystack = " ".join([str(item.get("name", "")), str(item.get("description", "")), " ".join(item.get("tags", [])), str(item.get("category", "")), CATEGORIES.get(item.get("category"), "")]).casefold()
        if all(word in haystack for word in words):
            results.append(dict(item) | {"path": str(path)})
    return results
