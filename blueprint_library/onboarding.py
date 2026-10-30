"""Prepare a private, reviewable setup plan without deploying or creating data.

The installer AI follows the plan using tools it actually has. This module only
writes company input and deployment guidance; it never creates credentials, an
empty library, a remote account, or a pretend connected backend.
"""

from __future__ import annotations

from blueprint_folders.path_safety import has_link

import json
import os
from pathlib import Path
import re
import shlex

from .company import _fields as _company_fields
from .store import LibraryError, SCHEMA_VERSION


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_PROVIDERS = {"render", "railway", "server"}
_REPOSITORY = "https://github.com/collieai-g/Blueprint-Studio-Open-Source"
_RENDER_LAUNCH = "https://render.com/deploy?repo=" + _REPOSITORY
_WORKSPACE_NAME = "藍圖工作室 Blueprint Studio"


def installation_instruction() -> str:
    """Read the canonical prompt shipped in every complete installation."""
    return (_SOURCE_ROOT / "INSTALL_AI.txt").read_text(encoding="utf-8")


def _safe_path(value, label: str) -> Path:
    """Check supplied components before resolving, including dangling symlinks."""
    try:
        if isinstance(value, str) and not value.strip():
            raise ValueError
        path = Path(value).expanduser()
        if any(ord(char) < 32 or ord(char) == 127 for char in str(path)):
            raise ValueError
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


def _outside_source(path: Path, label: str):
    if path == _SOURCE_ROOT or _SOURCE_ROOT in path.parents or path in _SOURCE_ROOT.parents:
        raise LibraryError(f"{label}必須放在公開工具專案以外，兩者不能互相包含。")


def _company(value) -> dict:
    # Shared validation checks the exact four fields, lengths, types and NULs.
    fields, _ = _company_fields(value, versioned=False)
    return fields


def load_company_file(path) -> dict:
    """Load a private company JSON file without opening or creating a library."""
    target = _safe_path(path, "公司資料檔案")
    _outside_source(target, "公司資料檔案")
    try:
        if not target.is_file() or target.stat().st_size > 64 * 1024:
            raise ValueError
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise LibraryError("無法讀取公司 JSON；請使用不超過 64 KiB 的正常 UTF-8 檔案。") from exc
    return _company(payload)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _command(*parts) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _code(command: str) -> str:
    # Keep legal backticks in a user-chosen path from ending a Markdown fence.
    longest = max((len(run) for run in re.findall(r"`+", command)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}sh\n{command}\n{fence}\n"


def _environment(company: dict, username: str) -> bytes:
    values = {
        "BLUEPRINT_DATA_DIR": "/data/library",
        "BLUEPRINT_BACKUP_DIR": "/data/backups",
        "BLUEPRINT_ADMIN_USERNAME": username,
        "BLUEPRINT_COMPANY_NAME": company["name"],
        "BLUEPRINT_COMPANY_INDUSTRY": company.get("industry", ""),
        "BLUEPRINT_COMPANY_DESCRIPTION": company.get("description", ""),
        "BLUEPRINT_COMPANY_GOALS": company.get("goals", ""),
    }
    lines = [
        "# 私人部署範本，請勿提交 Git。此檔沒有密碼或 API 金鑰。",
        "# 下列文字採 dotenv 的 JSON 引號表示；在平台表單填入實際值，不連引號貼上。",
        "# 公司資料只供首次設定；更新工具或重啟不能覆寫既有公司設定。",
        *(key + "=" + json.dumps(value, ensure_ascii=False) for key, value in values.items()),
        "# 在平台秘密設定或主機安全環境中自行設定最少 12 字元的管理員密碼。",
        "BLUEPRINT_ADMIN_PASSWORD=",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _guide(plan: dict) -> bytes:
    tool = str(_SOURCE_ROOT / "blueprint.py")
    workspace = plan["workspace_path"]
    common = (
        "# 藍圖工作室（後台）｜安裝下一步\n\n"
        "目前只準備好私人公司資料及安裝計劃，尚未部署、建立資料庫或設定登入。"
        "company-profile.json 是你的公司資料；setup.json 是計劃，並非已連線證明。"
        "保留這些檔案在私人位置，不要上傳公開 GitHub。完整說明見公開工具的 docs/INSTALL.md。\n\n"
        "先沿用已選的本機／雲端路線，檢查現有安裝並在私人 INSTALL_PROGRESS.md 記錄進度。"
        "完整指南：docs/INSTALL_LOCAL.md 或 docs/INSTALL_CLOUD.md。"
        "GitHub 倉庫、Codex 工作環境 Publish／Republish 與正式網站部署是不同階段；"
        "環境準備好後仍要啟動並驗證真正工作台。\n\n"
        "按 docs/OPERATING_MODEL.md 分清日常資料與程式來源：藍圖提供共用工具，AI 客戶端處理工作，獨立業務 Agent 需要另行接入。"
        "已符合共同規格的 Agent 可經 API 登記及設定；新增登記本身不要求修改程式或部署。"
        "需要長期客製化時，沿用或建立公司私人程式倉庫，記錄上游版本與實際部署來源，更新時保留客製修改。"
        "直接使用原版可沿用公開正式來源；兩種方式都把公司資料、憑證及備份放在程式倉庫以外。\n\n"
        "必須沿用 library_web/ 及 assets/icons/ 的完整原版介面和功能；"
        "gui、serve 及 dist/index.html 是舊版離線工具。不要重新排版、簡化或交付示範頁。"
        "正式畫面可用後，先按 docs/NATIVE_LIFECYCLE.md 驗證完整備份下載、隔離還原、重啟保存及下一次更新路線，再驗證 AI 保存。\n\n"
    )
    if plan["mode"] == "local":
        init = _command("python3", tool, "library-init", plan["data_path"], "--username", plan["username"], "--company-file", plan["company_file"])
        connect = _command("python3", tool, "workday-connect", workspace, "--url", "http://127.0.0.1:8787", "--username-env", "BLUEPRINT_BACKEND_USERNAME", "--password-env", "BLUEPRINT_BACKEND_PASSWORD")
        start = _command("python3", tool, "library", "--data", plan["data_path"])
        body = (
            "## 本機安裝\n\n"
            "1. 確認 Python 3.10+ 可用；以下指令使用 python3 及 POSIX shell 引號。"
            "Windows 請跟 docs/INSTALL_LOCAL.md 的 PowerShell 範例，由 AI 轉換實際路徑及引號，"
            "不要只替換 Python 指令後照貼含特殊字元的路徑。"
            "初始化時在終端機安全提示輸入管理員長密碼，不要把密碼貼到聊天。已有資料庫時"
            "不要重新初始化；先核對資料路徑，按照 docs/INSTALL.md 接續使用。\n\n"
            + _code(init)
            + "\n2. 啟動後台，保持這個終端機運行：\n\n"
            + _code(start)
            + "\n3. 在同一部電腦開啟啟動輸出的實際地址，通常是 http://127.0.0.1:8787，"
            "使用瀏覽器的 HTTP Basic 登入提示，核對公司名稱、"
            "行業、簡介和目標。此模式只供這部電腦使用，並未因此成為外出可用的雲端服務。\n\n"
            "4. 在每日工作頁保存一筆真實而簡短的紀錄，停止後重新執行啟動指令，"
            "查回同一筆紀錄及公司設定。確實保留才算本機驗證完成。\n"
            + "\n5. 在安全環境設定既有後台帳密，用 HTTP 連到同一個服務；運行時不要讓另一個程序直接開資料庫：\n\n"
            + _code(connect)
            + "\n讓同一部電腦的 AI 讀取 WORKDAY.md，實際保存及讀回並在介面確認。"
            "遠端 AI 無權存取本機資料時，使用每日工作 JSON 匯入，記錄連接仍待完成。\n"
        )
    else:
        provider = plan["provider"]
        if provider == "render":
            platform = (
                "1. 登入或建立 Render 帳戶。已選公司私人客製倉庫時，從該倉庫及已核實的分支／commit 部署，保留客製內容；直接使用原版才使用以下官方部署入口：\n\n"
                f"{_RENDER_LAUNCH}\n\n"
                "核對 repo 的 render.yaml，Dockerfile 為 company/Dockerfile，持久磁碟掛載 /data，"
                "並啟用平台 HTTPS。在平台核對服務及磁碟費用，確認接受後才建立收費資源。\n\n"
            )
        elif provider == "railway":
            platform = (
                "1. 登入或建立 Railway 帳戶，在 https://railway.com/new 從 GitHub repo 部署：\n\n"
                f"{_REPOSITORY}\n\n"
                "上方是直接使用原版的來源。已選公司私人客製倉庫時，使用該倉庫及已核實的分支／commit，不能改回公開來源而遺失客製內容。"
                "在服務設定指定 company/Dockerfile；建立並掛載持久 Volume 到 /data。"
                "服務跟隨平台 PORT，未指定時為 8787；設定對外網域和 HTTPS。"
                "如非 root 的 Volume 權限有問題，按 Railway 官方說明設定 RAILWAY_RUN_UID=0。"
                "先核對平台、運行資源及 Volume 費用，"
                "確認接受後才建立收費資源。\n\n"
            )
        else:
            build = _command("docker", "build", "-f", str(_SOURCE_ROOT / "company" / "Dockerfile"), "-t", "blueprint-studio", str(_SOURCE_ROOT))
            run = _command(
                "docker", "run", "-d", "--name", "blueprint-studio", "--restart", "unless-stopped",
                "-p", "127.0.0.1:8787:8787", "--mount", "type=volume,source=company-data,target=/data",
                "--env", "BLUEPRINT_ADMIN_USERNAME", "--env", "BLUEPRINT_ADMIN_PASSWORD",
                "--env", "BLUEPRINT_COMPANY_NAME", "--env", "BLUEPRINT_COMPANY_INDUSTRY",
                "--env", "BLUEPRINT_COMPANY_DESCRIPTION", "--env", "BLUEPRINT_COMPANY_GOALS", "blueprint-studio",
            )
            platform = (
                "1. 登入自己的伺服器，確認 Docker、持久儲存及備份方案可用，先核對主機及磁碟費用。"
                "將已選的公開正式版本或公司私人客製程式放到主機，使用 company/Dockerfile，核對來源 commit 並按實際 checkout 位置調整以下路徑。先在主機安全環境設定第 2 步"
                "的環境變數，再建置及啟動：\n\n"
                + _code(build)
                + "\n" + _code("docker volume create company-data")
                + "\n" + _code(run)
                + "\n沿用 company-data volume，不刪除已有容器或資料來避開名稱衝突；有現存服務時先核對它。"
                "用 Caddy／Nginx 為真實網域設定有效 TLS，代理到 127.0.0.1:8787，保留原始 Host header。"
                "登入資料不能透過公網明文 HTTP 傳送。\n\n"
            )
        connect = _command(
            "python3", tool, "workday-connect", workspace, "--url", "https://actual",
            "--username-env", "BLUEPRINT_BACKEND_USERNAME", "--password-env", "BLUEPRINT_BACKEND_PASSWORD",
        )
        body = (
            "## 雲端部署\n\n"
            "先按 docs/INSTALL_CLOUD.md 準備所需帳戶及完整原始碼；"
            "使用 Codex 雲端時才完成相應環境接入，直接部署原版可使用公開來源。"
            "已有帳戶、倉庫或工作環境就接續使用。本計劃對應下列已選原生部署平台。"
            "如改選 Sites，按該指南的 Sites 步驟完成後台適配和完整原版驗收；"
            "目前 setup 不支援 --provider sites，不能直接套用這份原生部署設定。\n\n" + platform
            + "2. 按 deployment.env.example 在平台的環境變數表單設定管理員名稱、公司資料及"
            "BLUEPRINT_DATA_DIR=/data。使用實際文字值；dotenv 引號及跳脫字元不要原樣貼入表單。"
            "公司資料只在首次初始化使用，已有公司資料不應在重啟或更新時重設。"
            "BLUEPRINT_ADMIN_PASSWORD 只在平台秘密設定輸入，最少 12 字元；不放入聊天或公開程式碼。\n\n"
            "3. 部署完成後取得平台提供或自己設定的真實 HTTPS 網址。打開並登入，核對公司資料。"
            "目前計劃沒有聲稱任何網址已連線。\n\n"
            "4. 在每日工作頁保存一筆真實簡短紀錄，重新啟動服務後查回同一筆紀錄和公司設定，"
            "確認持久磁碟正常；有資料丟失便先修復儲存，不能當作安裝完成。\n\n"
            "5. 在你使用 AI 的裝置安全設定 BLUEPRINT_BACKEND_USERNAME 及 BLUEPRINT_BACKEND_PASSWORD"
            "環境變數，再連結私人工作室。把下列 https://actual 換成第 3 步已驗證的真實地址；"
            "這是待替換字樣，並不是已建立的服務。秘密值不要寫入指令、聊天或工作室設定檔。\n\n"
            + _code(connect)
            + "\n6. 讓你使用的 AI 讀取私人工作室的 WORKDAY.md，測試實際查詢及保存。AI 需要"
            "具備相應工具與授權才能直接執行；普通聊天可輸出整理後的 JSON，讓你手動匯入。\n"
        )
    ending = (
        "\n## 日後使用\n\n"
        "說『今日收工』時，AI 只整理目前可見或你提供的對話，保存有用成果、決定、參考及待辦。"
        "先去重、沿用穩定來源識別，再按版本檢查更新並保留可恢復歷史；不保存秘密或全部原始聊天。"
        "報告、待辦、文件、收藏、公司資料及已支援設定均經 API／MCP 保存並讀回，不為日常保存建立 Git commit 或部署 Sites。"
        "它不會自動讀取其他 AI 平台的不可見對話。沒有實際儲存成功就不能聲稱已存檔。\n\n"
        "更新公開工具時保留私人公司資料、同一個資料目錄／volume、工作室及既有工具修改。"
        "介面原始碼、按鈕邏輯、尚未支援能力或程式版本改動，經私人來源修改、測試後部署受影響的服務；GitHub 推送與上游發行不會自動完成同步或正式部署。"
        "Sites／原生後台分開判斷：相容的後台修改只部署後台；Sites Worker、工具定義或平台設定有變更才更新該入口。"
        "此計劃只準備文件；完成與否以實際登入、保存及重啟後的結果為準。\n"
    )
    return (common + body + ending).encode("utf-8")


def _existing_matches(path: Path, content: bytes) -> bool:
    _safe_path(path, "安裝計劃檔案")
    if not path.exists():
        return False
    try:
        if not path.is_file() or path.stat().st_size != len(content) or path.read_bytes() != content:
            raise LibraryError("已有自訂檔案或不同的安裝計劃，未有覆寫；請保留原有內容並選另一個私人目錄。", 409)
    except OSError as exc:
        raise LibraryError("無法核對已有安裝計劃，未有覆寫。", 409) from exc
    return True


def prepare_setup(output_dir, company, mode="cloud", provider="render", username="admin") -> dict:
    """Create private input and guidance only; equal reruns leave files untouched."""
    company = _company(company)
    if not isinstance(mode, str) or mode not in {"cloud", "local"}:
        raise LibraryError("安裝模式只可用 cloud 或 local。")
    if mode == "cloud" and (not isinstance(provider, str) or provider not in _PROVIDERS):
        raise LibraryError("雲端平台只可用 render、railway 或 server。")
    if (not isinstance(username, str) or not username.strip() or len(username) > 80
            or re.search(r"[:\x00-\x1f\x7f]", username)):
        raise LibraryError("管理員名稱需為 1–80 字，不能包含冒號或控制字元。")
    username = username.strip()
    output = _safe_path(output_dir, "私人安裝目錄")
    _outside_source(output, "私人安裝目錄")
    if output.exists() and not output.is_dir():
        raise LibraryError("私人安裝目錄必須是正常目錄。", 409)
    profile = output / "company-profile.json"
    plan = {
        "schema": "blueprint-setup", "version": 1, "mode": mode,
        "status": "awaiting_cloud_deployment" if mode == "cloud" else "awaiting_local_start",
        "company_file": str(profile), "workspace_path": str(output / _WORKSPACE_NAME), "username": username,
    }
    if mode == "cloud":
        plan["provider"] = provider
    else:
        plan["data_path"] = str(output / "data")
    files = {
        profile: _json_bytes(company), output / "setup.json": _json_bytes(plan),
        output / "START_HERE.md": _guide(plan), output / "deployment.env.example": _environment(company, username),
    }
    # Check every generated target before writing any of them. A custom guide or
    # conflicting plan cannot leave behind a partly replaced company profile.
    pending = [(path, content) for path, content in files.items() if not _existing_matches(path, content)]
    try:
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        _safe_path(output, "私人安裝目錄")
        for path, content in pending:
            _safe_path(path, "安裝計劃檔案")
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as handle:
                    try:
                        handle.write(content)
                        handle.flush()
                        os.fsync(handle.fileno())
                    except BaseException:
                        path.unlink(missing_ok=True)
                        raise
            except FileExistsError:
                # Another identical preparation is safe; a differing one must
                # not overwrite the file created between preflight and open.
                _existing_matches(path, content)
    except OSError as exc:
        raise LibraryError("無法寫入私人安裝計劃；請檢查目錄權限及剩餘空間。") from exc
    return {
        **plan, "output_path": str(output), "setup_file": str(output / "setup.json"),
        "start_here": str(output / "START_HERE.md"), "env_file": str(output / "deployment.env.example"),
    }
