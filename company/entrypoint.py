"""Container entrypoint; initialize only an empty, mounted private data directory."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blueprint_library.server import initialize_library, serve_library


def cloud_settings(environ):
    """Validate runtime settings before creating any private files."""
    raw_port = environ.get("PORT", "8787")
    try:
        port = int(raw_port)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ValueError("PORT 必須是 1 至 65535 的整數。") from exc
    profile = None
    if environ.get("BLUEPRINT_COMPANY_NAME", "").strip():
        profile = {key: environ.get("BLUEPRINT_COMPANY_" + key.upper(), "")
                   for key in ("name", "industry", "description", "goals")}
        from blueprint_library.company import _fields
        _fields(profile, versioned=False)
    return port, profile


def main():
    port, profile = cloud_settings(os.environ)
    data = Path(os.environ.get("BLUEPRINT_DATA_DIR", "/data/library"))
    if data == Path('/data/library') and (Path('/data/access.json').exists() or Path('/data/library.sqlite3').exists()):
        raise SystemExit('找到舊 /data 資料。請沿用 BLUEPRINT_DATA_DIR=/data，並設定另一個持久備份位置；不會建立空白工作台。')
    if not (data / "access.json").exists():
        if data.exists() and any(data.iterdir()):
            raise SystemExit('資料目錄已有內容但缺少登入設定；請先修復，不會重新初始化。')
        password = os.environ.get("BLUEPRINT_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise SystemExit("首次啟動請設定 BLUEPRINT_ADMIN_PASSWORD（最少 12 字元）；資料會保存於持久資料目錄。")
        initialize_library(data, os.environ.get("BLUEPRINT_ADMIN_USERNAME", "admin"), password,
                           company_profile=profile)
    # Existing data is opened only by the guarded startup, after its backup.
    serve_library(data, host="0.0.0.0", port=port, open_browser=False)


if __name__ == "__main__":
    main()
