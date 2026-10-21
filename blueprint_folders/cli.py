from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .core import CATEGORIES, FolderError, create_folder, init_studio, mark_folder, reindex, search_folders


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Blueprint Studio Backend｜你公司的專屬後台。")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("gui", help="開啟舊版離線資料包介面（預設）；公司後台請用 library")
    serve = commands.add_parser("serve", help="提供舊版離線資料包介面；公司後台請用 library")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-open", action="store_true")
    setup = commands.add_parser("init", help="建立工作室根目錄及 AI 管理指引")
    setup.add_argument("path")
    link = commands.add_parser("link", help="建立獨立工作室，連結共用工具以使用功能更新")
    link.add_argument("path", help="工具目錄以外嘅私人工作室路徑")
    library_init = commands.add_parser("library-init", help="初始化私人公司資料庫及管理員登入")
    library_init.add_argument("path", help="公開工具目錄以外嘅私人資料路徑")
    library_init.add_argument("--username", default="admin")
    library_init.add_argument("--company-file", help="私人公司基本資料 JSON 檔案")
    onboarding = commands.add_parser("setup", help="設定公司資料，選擇雲端（預設）或本機使用")
    onboarding.add_argument("--company-name")
    onboarding.add_argument("--industry", default="")
    onboarding.add_argument("--description", default="")
    onboarding.add_argument("--goals", default="")
    onboarding.add_argument("--mode", choices=["cloud", "local"])
    onboarding.add_argument("--provider", choices=["render", "railway", "server"])
    onboarding.add_argument("--username", default="admin")
    onboarding.add_argument("--output", help="公開工具目錄以外的私人設定目錄")
    library = commands.add_parser("library", help="啟動文件整理與網站／YouTube 收藏後台")
    library.add_argument("--data", required=True, help="已初始化嘅私人資料目錄")
    library.add_argument("--host", default="127.0.0.1")
    library.add_argument("--port", type=int, default=8787)
    library.add_argument("--no-open", action="store_true")
    connect_day = commands.add_parser("workday-connect", help="把 AI 工作室接到雲端或本機公司後台")
    connect_day.add_argument("path", help="私人工作室路徑")
    connection = connect_day.add_mutually_exclusive_group(required=True)
    connection.add_argument("--data", help="已初始化的本機公司資料目錄")
    connection.add_argument("--url", help="已部署的雲端 HTTPS 後台網址")
    connect_day.add_argument("--username-env", default="BLUEPRINT_BACKEND_USERNAME")
    connect_day.add_argument("--password-env", default="BLUEPRINT_BACKEND_PASSWORD")
    for action, help_text in (
        ("workday-save", "保存 AI 整理好的工作成果、決定、參考資料與待辦"),
        ("workday-list", "搜尋已保存的每日工作"),
        ("workday-show", "讀取一份每日工作及待辦進度"),
        ("workday-revise", "合併及修整已存工作紀錄，保留版本歷史"),
        ("workday-history", "讀取工作紀錄的歷次版本"),
    ):
        day_command = commands.add_parser(action, help=help_text)
        location = day_command.add_mutually_exclusive_group(required=True)
        location.add_argument("--workspace", help="已連接公司後台的私人工作室")
        location.add_argument("--data", help="已初始化的公司資料目錄")
        if action in ("workday-save", "workday-revise"):
            day_command.add_argument("--input", default="-", help="整理結果 JSON 檔案；- 代表標準輸入（預設）")
            if action == "workday-revise":
                day_command.add_argument("id", help="工作紀錄編號")
        elif action == "workday-list":
            day_command.add_argument("--date", default="", help="YYYY-MM-DD")
            day_command.add_argument("--query", default="")
        else:
            day_command.add_argument("id", help="工作紀錄編號")
    for action in ("create", "mark"):
        command = commands.add_parser(action, help="建立新文件夾" if action == "create" else "標記現有文件夾")
        command.add_argument("path", help="父目錄" if action == "create" else "現有文件夾")
        if action == "create":
            command.add_argument("name", help="新文件夾名稱")
        command.add_argument("--description", "--purpose", default="" if action == "create" else None, help="用途，可先留空，之後與 Codex 逐步補充")
        command.add_argument("--category", choices=["auto", *CATEGORIES], default="auto")
        command.add_argument("--tag", action="append", default=[] if action == "create" else None, help="標籤，可重複傳入")
    find = commands.add_parser("search", help="搜尋標記清單")
    find.add_argument("root")
    find.add_argument("query", nargs="?", default="")
    find.add_argument("--category", choices=list(CATEGORIES))
    rebuild = commands.add_parser("reindex", help="搬動、重新命名或直接修改標記後重建清單")
    rebuild.add_argument("root")
    info = commands.add_parser("inspect", help="讀取某文件夾身份標記")
    info.add_argument("path")
    package = commands.add_parser("pack", help="將專員文件夾封裝成 ZIP，帶回瀏覽器介面")
    package.add_argument("path")
    package.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            from blueprint_library.onboarding import prepare_setup
            interactive = sys.stdin.isatty()
            if not args.company_name:
                if not interactive:
                    raise FolderError("請先提供 --company-name，或在互動終端機執行 setup。")
                args.company_name = input("公司／創業名稱：").strip()
                args.industry = input("行業（選填）：").strip()
                args.description = input("公司簡介（選填）：").strip()
                args.goals = input("目前目標（選填）：").strip()
            mode = args.mode or ((input("使用模式 cloud / local [cloud]：").strip() or "cloud") if interactive else "cloud")
            provider = args.provider or ((input("雲端 render / railway / server [render]：").strip() or "render") if interactive and mode == "cloud" else "render")
            output = args.output or str(Path.cwd().parent / "Blueprint Studio Private")
            result = prepare_setup(output, {"name": args.company_name, "industry": args.industry,
                "description": args.description, "goals": args.goals}, mode, provider, args.username)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command and args.command.startswith("workday-"):
            from blueprint_library.commands import run_workday_command
            result = run_workday_command(args)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "library-init":
            from blueprint_library.server import initialize_library
            password = os.environ.get("BLUEPRINT_ADMIN_PASSWORD")
            if password is None:
                password = getpass.getpass("設定管理員密碼（最少 12 字元）：")
                if password != getpass.getpass("再次輸入密碼："):
                    raise FolderError("兩次密碼不一致，未有初始化。")
            profile = None
            if args.company_file:
                from blueprint_library.onboarding import load_company_file
                profile = load_company_file(args.company_file)
            result = initialize_library(args.path, args.username, password, company_profile=profile)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "library":
            from blueprint_library.server import serve_library
            serve_library(args.data, host=args.host, port=args.port, open_browser=not args.no_open)
            return 0
        if args.command in (None, "gui"):
            page = Path(__file__).resolve().parents[1] / "dist" / "index.html"
            if not page.exists():
                raise FolderError("缺少瀏覽器版，請先執行 python tools/build_web.py。")
            if not webbrowser.open(page.as_uri()):
                print("請用瀏覽器開啟：" + str(page))
            return 0
        if args.command == "serve":
            from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
            from functools import partial
            page = Path(__file__).resolve().parents[1] / "dist"
            handler = partial(SimpleHTTPRequestHandler, directory=str(page))
            server = ThreadingHTTPServer((args.host, args.port), handler)
            url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{server.server_port}/"
            print("Blueprint Studio Backend：" + url, flush=True)
            if not args.no_open:
                webbrowser.open(url)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        if args.command == "init":
            result = init_studio(args.path)
        elif args.command == "link":
            from .workspaces import link_workspace
            result = link_workspace(args.path)
        elif args.command == "create":
            result = create_folder(args.path, args.name, args.description, args.category, args.tag)
        elif args.command == "mark":
            result = mark_folder(args.path, args.description, args.category, args.tag)
        elif args.command == "search":
            result = search_folders(args.root, args.query, args.category)
        elif args.command == "reindex":
            result = reindex(args.root)
        elif args.command == "inspect":
            from .core import _path, _read_json
            folder = _path(args.path)
            if (folder / ".blueprint").is_symlink():
                raise FolderError("身份檔目錄不能係符號連結。")
            result = _read_json(folder / ".blueprint" / "folder.json")
        elif args.command == "pack":
            from .packages import pack_folder
            result = pack_folder(args.path, args.output)
        else:
            parser.error("未知指令")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, EOFError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
