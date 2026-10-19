"""Container entrypoint; initialize only an empty, mounted private data directory."""

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blueprint_library.server import initialize_library, serve_library


def main():
    data = Path(os.environ.get("BLUEPRINT_DATA_DIR", "/data"))
    if not (data / "access.json").exists():
        password = os.environ.get("BLUEPRINT_ADMIN_PASSWORD", "")
        if len(password) < 12:
            raise SystemExit("首次啟動請設定 BLUEPRINT_ADMIN_PASSWORD（最少 12 字元）；資料會保存於持久資料目錄。")
        initialize_library(data, os.environ.get("BLUEPRINT_ADMIN_USERNAME", "admin"), password)
    serve_library(data, host="0.0.0.0", port=8787, open_browser=False)


if __name__ == "__main__":
    main()
