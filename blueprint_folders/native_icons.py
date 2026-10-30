"""Portable artwork for folder agents, without changing OS folder settings.

The application displays the same bundled SVG/PNG artwork on a computer or a
phone. Runtime dependencies are limited to the Python standard library.
"""

from __future__ import annotations

from blueprint_folders.path_safety import has_link

from pathlib import Path
import shutil


ICON_KINDS = ("general", "chat", "web", "finance", "document", "calendar", "automation", "research")
ASSETS = Path(__file__).resolve().parents[1] / "assets" / "icons"


def apply_folder_icon(folder: Path, kind: str = "general") -> dict:
    """Save SVG/PNG artwork under `.blueprint/` for the portable interface.

    `applied` deliberately remains false: no native OS setting is modified.
    If a previous local image differs, retain its first version in `backups/`.
    Unknown kinds use the neutral, purpose-free general icon.
    """
    result = {
        "applied": False,
        "platform": "portable",
        "message": "",
        "icon": "",
    }
    try:
        folder = Path(folder).expanduser()
        if has_link(folder):
            raise ValueError("指定位置不能包含符號連結或重新解析點。")
        folder = folder.resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("指定位置並非文件夾。")
        kind = kind if kind in ICON_KINDS else "general"
        directory = folder / ".blueprint"
        if has_link(directory):
            raise ValueError("資源目錄不能係符號連結。")
        directory.mkdir(exist_ok=True)
        for extension in ("svg", "png"):
            destination = directory / f"icon.{extension}"
            if has_link(destination):
                raise ValueError("圖示檔不能係符號連結。")
            data = (ASSETS / f"{kind}.{extension}").read_bytes()
            if destination.exists():
                if destination.read_bytes() == data:
                    continue
                backup = directory / "backups" / f"icon.{extension}"
                if has_link(backup.parent) or has_link(backup):
                    raise ValueError("圖示備份位置不能係符號連結。")
                if not backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
            destination.write_bytes(data)
        result["message"] = "圖示已保存，可於文件夾 Agent 介面顯示"
        result["icon"] = ".blueprint/icon.png"
    except (OSError, ValueError) as error:
        result["message"] = str(error)
    return result
