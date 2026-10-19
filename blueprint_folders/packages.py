"""Export an on-disk folder without omitting its source files."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
import tempfile
import zipfile

from .core import FolderError, _path, _read_json, SKIP_DIRS

MAX_BYTES = 20 * 1024 * 1024


def pack_folder(path, output) -> dict:
    folder = _path(path)
    if (folder / ".blueprint").is_symlink():
        raise FolderError("資源目錄不能係符號連結。")
    metadata = folder / ".blueprint" / "folder.json"
    if not metadata.is_file():
        raise FolderError("文件夾未有身份標記，請先使用 mark。")
    _read_json(metadata)
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FolderError("輸出檔已存在，請用另一個名稱，避免覆寫備份。")
    if output.parent.resolve().is_relative_to(folder):
        raise FolderError("ZIP 請儲存在專員文件夾以外。")
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = []
    total = 0
    for here, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if d not in (SKIP_DIRS - {".blueprint"}) and not (Path(here) / d).is_symlink()]
        for name in files:
            source = Path(here) / name
            if source.is_symlink():
                raise FolderError(f"資料包不能包含符號連結：{source.relative_to(folder)}")
            relative = source.relative_to(folder)
            if relative.parts[0] == ".blueprint" and (name == "index.lock" or name.startswith(".write-")):
                continue
            total += source.stat().st_size
            if total > MAX_BYTES or len(sources) >= 4999:
                raise FolderError("瀏覽器資料包最多支援 20 MB／5,000 個檔案。")
            sources.append((source, (Path(folder.name) / relative).as_posix()))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".package-", delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, name in sources:
                archive.write(source, name)
        # Exclusive creation works on NTFS, APFS and removable FAT/exFAT drives.
        # Never overwrite an existing backup or follow a competing symlink.
        with output.open("xb") as target, temporary.open("rb") as source:
            shutil.copyfileobj(source, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return {"path": str(output), "files": len(sources), "uncompressed_bytes": total}
