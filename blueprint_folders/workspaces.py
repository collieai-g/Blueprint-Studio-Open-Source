"""Bind a private studio to one shared, independently updated tool checkout."""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .core import FolderError, _atomic_json, _atomic_text, _path, _read_json, init_studio


LAUNCHER_NAME = "blueprint-studio.py"

# Keep this independent of the engine and its location. Updating the checkout
# changes the code used by this launcher without copying code into the studio.
_LAUNCHER_SOURCE = '''#!/usr/bin/env python3
"""Blueprint Folder Agent private studio launcher. Configuration: .blueprint/tool.json."""

import json
from pathlib import Path
import subprocess
import sys


def main():
    studio = Path(__file__).resolve().parent
    arguments = sys.argv[1:]
    if not arguments:
        print("藍圖專員工作室：" + str(studio))
        print("使用方法：python blueprint-studio.py <指令> [參數]")
        print("create 名稱、search [關鍵字]、reindex：自動使用本工作室。")
        print("mark 路徑、inspect 路徑、pack 路徑 --output 路徑：以本工作室為起點。")
        print("其他指令：--version、--help、gui、serve。")
        return 0

    config_path = studio / ".blueprint" / "tool.json"
    try:
        if config_path.parent.is_symlink() or config_path.is_symlink():
            raise ValueError("工具設定不能係符號連結。")
        if config_path.stat().st_size > 1024 * 1024:
            raise ValueError("工具設定檔過大。")
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        tool_value = config.get("tool_path") if isinstance(config, dict) else None
        if not isinstance(tool_value, str) or not tool_value or not Path(tool_value).is_absolute():
            raise ValueError("工具路徑必須係完整路徑。")
        tool_root = Path(tool_value).resolve()
        if studio == tool_root or studio in tool_root.parents or tool_root in studio.parents:
            raise ValueError("工作室與工具必須係分開嘅目錄。")
        entrypoint = tool_root / "blueprint.py"
        if not tool_root.is_dir() or not entrypoint.is_file():
            raise ValueError("搵唔到共用工具 blueprint.py。")
    except (OSError, ValueError) as exc:
        print("未能使用工作室連結：" + str(exc), file=sys.stderr)
        print('請用工具目錄嘅 blueprint.py link "' + str(studio) + '" 重新連結。', file=sys.stderr)
        return 2

    if arguments[0] in {"create", "search", "reindex"}:
        arguments.insert(1, str(studio))
    try:
        return subprocess.call([sys.executable, str(entrypoint), *arguments], cwd=studio)
    except OSError as exc:
        print("未能啟動共用工具：" + str(exc), file=sys.stderr)
        print('請用工具目錄嘅 blueprint.py link "' + str(studio) + '" 重新連結。', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _tool_root() -> Path:
    return Path(__file__).resolve().parents[1]


def link_workspace(path) -> dict:
    """Create a private launcher while retaining studio data and local guidance."""
    workspace = _path(path)
    tool_root = _tool_root().resolve()
    if workspace == tool_root or workspace in tool_root.parents or tool_root in workspace.parents:
        raise FolderError("工作室與工具目錄必須分開，亦不能互相包含；請使用並排嘅文件夾。")
    if workspace.exists() and not workspace.is_dir():
        raise FolderError("工作室路徑必須係文件夾。")
    if not (tool_root / "blueprint.py").is_file():
        raise FolderError("共用工具缺少 blueprint.py，未有建立連結。")

    private = workspace / ".blueprint"
    config_path = private / "tool.json"
    launcher = workspace / LAUNCHER_NAME
    if private.is_symlink() or (private.exists() and not private.is_dir()):
        raise FolderError(".blueprint 必須係正常目錄，不能係檔案或符號連結。")
    if config_path.is_symlink():
        raise FolderError("工具設定不能係符號連結。")
    try:
        config = _read_json(config_path) if config_path.exists() else {}
    except UnicodeError as exc:
        raise FolderError("現有 tool.json 並非有效文字，未有修改。") from exc
    if not isinstance(config, dict):
        raise FolderError("現有 tool.json 格式不正確，未有修改。")
    if launcher.is_symlink():
        raise FolderError("工作室啟動器不能係符號連結。")
    if launcher.exists():
        try:
            existing = launcher.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise FolderError("現有 blueprint-studio.py 不能讀取，未有覆寫。") from exc
        if existing != _LAUNCHER_SOURCE:
            raise FolderError("已有其他 blueprint-studio.py，請先重新命名；程式唔會覆寫。")

    init_studio(workspace)
    config = dict(config)
    config.update({"schema_version": 1, "tool_path": str(tool_root), "linked_version": __version__})
    _atomic_json(config_path, config)
    if not launcher.exists():
        _atomic_text(launcher, _LAUNCHER_SOURCE)
    return {"path": str(workspace), "tool_path": str(tool_root), "launcher": str(launcher), "version": __version__}
