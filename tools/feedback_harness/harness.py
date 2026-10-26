"""Small source-feedback runner. State and client evidence stay outside source."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "0.1.0"
HEX = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}\Z")
PRIVATE_NAMES = {"access.json", "installation.json", "install_progress.md", "operation.json",
                 "setup.json", "company-profile.json", ".env"}
PRIVATE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".zip", ".log", ".png", ".jpg", ".jpeg"}


class HarnessError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(65536):
            h.update(block)
    return h.hexdigest()


def safe_path(value):
    path = Path(value).expanduser().absolute()
    if ".." in path.parts or any(ord(c) < 32 for c in str(path)):
        raise HarnessError("路徑不可包含向上穿越或控制字元。")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise HarnessError("路徑不可包含符號連結或 junction。")
    return path.resolve()


def outside_source(value, root=ROOT):
    path = safe_path(value)
    root = safe_path(root)
    if path == root or root in path.parents or path in root.parents:
        raise HarnessError("私人狀態、證據及產物必須放在來源專案以外。")
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise HarnessError("私人狀態、證據及產物不可放在 Git checkout 內。")
    return path


def outside_all_sources(value, project_root):
    path = outside_source(value, project_root)
    return outside_source(path, ROOT)


def source_file(root, name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise HarnessError("來源清單需要正常的相對檔案路徑。")
    relative = PurePosixPath(name)
    if relative.is_absolute() or ":" in name or ".." in relative.parts or ".git" in relative.parts or name != relative.as_posix():
        raise HarnessError("來源檔案路徑不可穿越或指定 Git 內部。")
    path = safe_path(Path(root) / name)
    if Path(root).resolve() not in path.parents or not path.is_file():
        raise HarnessError("來源清單有不存在或無效的檔案。")
    return path


def load_json(path):
    try:
        path = safe_path(path)
        if Path(path).stat().st_size > 2 * 1024 * 1024:
            raise HarnessError("JSON 超過容量上限。")
        def reject_constant(_):
            raise HarnessError("JSON 不可包含 NaN 或 Infinity。")
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise HarnessError("JSON 必須是物件。")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessError("無法讀取有效 JSON。") from exc


def atomic_json(path, value):
    data = canonical(value) + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=".harness-", suffix=".tmp", dir=Path(path).parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def state_lock(directory):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with safe_path(directory / ".harness.lock").open("a+b") as stream:
        if not stream.tell():
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise HarnessError("同一私人操作正在執行；先讀回現有結果。") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def validate_config(adapter, case):
    if type(adapter.get("contract_version")) is not int or adapter["contract_version"] != 1 or type(case.get("contract_version")) is not int or case["contract_version"] != 1:
        raise HarnessError("需要 contract_version=1。")
    for value in (adapter.get("project_id"), case.get("case_id")):
        if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
            raise HarnessError("project_id 或 case_id 不正確。")
    if adapter["project_id"] != case.get("project_id"):
        raise HarnessError("案例與項目不相符。")
    revision = adapter.get("source_revision", "")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision) or case.get("source_revision") != revision:
        raise HarnessError("案例與項目必須固定相同來源 commit。")
    checks = adapter.get("checks")
    if not isinstance(checks, dict) or not checks or len(checks) > 20:
        raise HarnessError("需要明確的檢查清單。")
    for name, check in checks.items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or not isinstance(check, dict):
            raise HarnessError("檢查格式不正確。")
        argv = check.get("argv")
        if (not isinstance(argv, list) or not argv or argv[0] != "$PYTHON" or len(argv) > 80
                or any(not isinstance(arg, str) or not arg or len(arg) > 2000 or any(ord(c) < 32 for c in arg) for arg in argv)):
            raise HarnessError("最小版本只執行明確宣告的 $PYTHON argv，不執行 shell 文字。")
        if not isinstance(check.get("inputs"), list) or not check["inputs"] or len(check["inputs"]) > 1000 or any(not isinstance(item, str) for item in check["inputs"]):
            raise HarnessError("每項檢查需要明確輸入檔案清單。")
        if type(check.get("timeout_seconds")) not in {int, float} or not 0 < check["timeout_seconds"] <= 300:
            raise HarnessError("檢查時間上限須大於零且不超過 300 秒。")
    required_checks = case.get("required_checks", [])
    if not isinstance(required_checks, list) or not required_checks or any(not isinstance(item, str) for item in required_checks):
        raise HarnessError("案例需要明確的必要檢查清單。")
    for kind in required_checks:
        if kind not in checks:
            raise HarnessError("案例要求了未宣告的檢查。")
    if not isinstance(adapter.get("bundle_files"), list) or not adapter["bundle_files"] or any(not isinstance(item, str) for item in adapter["bundle_files"]):
        raise HarnessError("需要明確的分享檔案 allowlist。")
    lifecycle = adapter.get("lifecycle", {})
    if not isinstance(lifecycle, dict) or lifecycle.get("kind") not in {"reference-only", "blueprint-lifecycle-v1"}:
        raise HarnessError("未知的生命週期接入種類。")
    kinds = adapter.get("required_attestations", [])
    case_kinds = case.get("required_attestations", [])
    if not isinstance(kinds, list) or not isinstance(case_kinds, list) or any(not isinstance(item, str) for item in kinds + case_kinds):
        raise HarnessError("客戶端證據清單格式不正確。")
    if not set(case_kinds) <= set(kinds):
        raise HarnessError("項目不可省略案例要求的客戶端驗收。")
    if any(kind != "browser_form_regression" for kind in kinds):
        raise HarnessError("最小版本尚未支援這類客戶端證據。")
    if "browser_form_regression" in adapter.get("required_attestations", []) and not adapter.get("browser_fixture"):
        raise HarnessError("瀏覽器驗收需要明確的 fixture 來源。")


def readonly_lifecycle(adapter, upstream, state_dir):
    reference = adapter.get("lifecycle", {})
    if not upstream:
        return {"status": "lifecycle_reference_only", "reference": reference, "executed": False}
    source = safe_path(upstream)
    entry = source_file(source, reference.get("entrypoint", ""))
    if reference.get("kind") != "blueprint-lifecycle-v1" or reference.get("task") != "source_release":
        raise HarnessError("最小版本只接入既有 Blueprint source_release 唯讀計劃。")
    result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, timeout=15)
    if result.returncode or result.stdout.decode("ascii", "strict").strip() != adapter["source_revision"]:
        raise HarnessError("上游來源與固定 commit 不相符。")
    clean = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
                           capture_output=True, timeout=15)
    if clean.returncode or clean.stdout.strip():
        raise HarnessError("上游 checkout 有修改或未追蹤檔案；先核對固定來源。")
    request = {"contract_version": 1, "task": "source_release", "target_services": [],
               "protected_services": [], "source": {"upstream_commit": adapter["source_revision"]}, "layout_change": False}
    target = state_dir / "lifecycle-input.json"
    atomic_json(target, request)
    command = [sys.executable, "-X", "utf8", str(entry), "plan", "--input", str(target)]
    completed = subprocess.run(command, cwd=source, capture_output=True, timeout=20, env=check_environment())
    try:
        plan = json.loads(completed.stdout)
    except (ValueError, UnicodeError) as exc:
        raise HarnessError("既有唯讀生命週期計劃未返回有效結果。") from exc
    if completed.returncode or not isinstance(plan, dict) or not HEX.fullmatch(plan.get("plan_digest", "")) or plan.get("production_status") != "not_verified":
        raise HarnessError("既有生命週期計劃未通過合約核對。")
    return {"status": "referenced_existing_plan", "executed": True, "entry_sha256": file_digest(entry),
            "source_revision": adapter["source_revision"], "plan_digest": plan["plan_digest"], "plan": plan}


def plan_operation(adapter_path, case_path, state, operation, upstream=None, *, root=ROOT):
    root = safe_path(root)
    directory = outside_source(state, root)
    outside_source(directory, ROOT)
    if not IDENTIFIER.fullmatch(operation):
        raise HarnessError("operation ID 不正確。")
    adapter_file = source_file(root, adapter_path)
    case_file = source_file(root, case_path)
    adapter, case = load_json(adapter_file), load_json(case_file)
    validate_config(adapter, case)
    with state_lock(directory):
        path = directory / "operation.json"
        intent = {"operation_id": operation, "adapter": adapter, "case": case,
                  "upstream_source": str(safe_path(upstream)) if upstream else None}
        plan_digest = digest(intent)
        if path.exists():
            previous = load_json(path)
            if previous.get("plan_digest") != plan_digest or previous.get("project_root") != str(root):
                raise HarnessError("同一私人目錄已有不同計劃；不可覆寫或重新初始化。")
            return {"operation_id": operation, "plan_digest": plan_digest, "reused": True}
        lifecycle = readonly_lifecycle(adapter, upstream, directory)
        value = {"contract_version": 1, "harness_version": VERSION, "operation_id": operation,
                 "plan_digest": plan_digest, "project_root": str(root), "adapter_path": adapter_path,
                 "case_path": case_path, "adapter": adapter, "case": case,
                 "lifecycle": lifecycle, "checks": {}, "attestations": {}, "version": 1}
        atomic_json(path, value)
        return {"operation_id": operation, "plan_digest": plan_digest, "reused": False,
                "lifecycle": {"status": lifecycle["status"], "executed": lifecycle["executed"], "plan_digest": lifecycle.get("plan_digest")}}


def read_state(state):
    directory = outside_source(state)
    value = load_json(directory / "operation.json")
    root = safe_path(value.get("project_root", ""))
    outside_source(directory, root)
    if value.get("contract_version") != 1 or not isinstance(value.get("adapter"), dict) or not isinstance(value.get("case"), dict):
        raise HarnessError("私人操作狀態格式不正確。")
    validate_config(value["adapter"], value["case"])
    # Changed declarations need a new reviewed plan, rather than running stale argv.
    if load_json(source_file(root, value["adapter_path"])) != value["adapter"] or load_json(source_file(root, value["case_path"])) != value["case"]:
        raise HarnessError("項目宣告或案例已變更；先建立新的明確計劃。")
    return directory, root, value


def runtime():
    return {"implementation": platform.python_implementation(), "version": platform.python_version(),
            "executable": sys.executable, "executable_sha256": file_digest(sys.executable),
            "platform": platform.platform()}


def check_key(root, value, check_name):
    try:
        check = value["adapter"]["checks"][check_name]
    except KeyError as exc:
        raise HarnessError("沒有宣告這項檢查。") from exc
    declared = set(check["inputs"])
    if not set(value["adapter"]["bundle_files"]) <= declared:
        raise HarnessError("檢查輸入必須包含全部分享來源及文件。")
    # Test discovery can load every Python file below tests; do not silently omit dependencies.
    discovered = {p.relative_to(root).as_posix() for p in (root / "tests").rglob("*.py")}
    discovered |= {p.relative_to(root).as_posix() for p in (root / "proposals").rglob("*.py")}
    if (root / "harness.py").is_file():
        discovered.add("harness.py")
    fixture = value["adapter"].get("browser_fixture")
    if fixture:
        discovered.add(fixture)
        sibling = PurePosixPath(fixture).with_name("browser-check.mjs").as_posix()
        if (root / sibling).is_file():
            discovered.add(sibling)
    if not discovered <= declared:
        raise HarnessError("檢查輸入遺漏執行相關來源或測試檔案。")
    files = {name: file_digest(source_file(root, name)) for name in sorted(declared)}
    declarations = {value["adapter_path"]: file_digest(source_file(root, value["adapter_path"])),
                    value["case_path"]: file_digest(source_file(root, value["case_path"]))}
    engine_sha256 = file_digest(Path(__file__))
    input_tree = digest({"project_files": files, "declarations": declarations, "engine_sha256": engine_sha256})
    argv = [sys.executable if arg == "$PYTHON" else arg for arg in check["argv"]]
    current_runtime = runtime()
    key = digest({"contract_version": 1, "input_tree": input_tree, "argv": argv,
                  "runtime": current_runtime, "timeout_seconds": check["timeout_seconds"]})
    return check, {"input_tree": input_tree, "inputs": files, "declarations": declarations,
                   "engine_sha256": engine_sha256, "argv": argv,
                   "runtime": current_runtime, "validation_key": key}


def check_environment():
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({"PYTHONUTF8": "1", "PYTHONHASHSEED": "0"})
    return env


def execute(argv, cwd, timeout):
    process = subprocess.Popen(argv, cwd=cwd, env=check_environment(), shell=False,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    streams = {"stdout": {"sha": hashlib.sha256(), "size": 0}, "stderr": {"sha": hashlib.sha256(), "size": 0}}
    def drain(name, pipe):
        try:
            while block := pipe.read(65536):
                streams[name]["sha"].update(block); streams[name]["size"] += len(block)
        finally:
            pipe.close()
    threads = [threading.Thread(target=drain, args=(name, pipe), daemon=True)
               for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True; process.kill(); process.wait()
    for thread in threads:
        thread.join(timeout=3)
    incomplete = any(thread.is_alive() for thread in threads)
    return {"outcome": "timeout" if timed_out else "failed" if process.returncode or incomplete else "passed",
            "returncode": process.returncode, "output_complete": not incomplete,
            **{name: {"sha256": data["sha"].hexdigest(), "bytes": data["size"]} for name, data in streams.items()}}


def run_check(state, check_name, acknowledge_unknown=False):
    directory, root, _ = read_state(state)
    with state_lock(directory):
        _, _, value = read_state(directory)
        check, fingerprint = check_key(root, value, check_name)
        previous = value["checks"].get(check_name, {})
        if previous.get("outcome") == "running" and not acknowledge_unknown:
            raise HarnessError("上次結果不明；先核對既有程序，再使用 --acknowledge-unknown。")
        if previous.get("outcome") == "passed" and previous.get("validation_key") == fingerprint["validation_key"]:
            return {"check": check_name, "outcome": "passed", "reused": True, **fingerprint}
        pending = {"check": check_name, "outcome": "running", "execution": "runner_subprocess", **fingerprint}
        value["checks"][check_name] = pending
        value["version"] += 1; atomic_json(directory / "operation.json", value)
        started = time.monotonic()
        try:
            result = execute(fingerprint["argv"], root, check["timeout_seconds"])
        except OSError:
            result = {"outcome": "failed", "returncode": None, "reason": "process_start_failed"}
        try:
            _, after = check_key(root, value, check_name)
            if after["validation_key"] != fingerprint["validation_key"]:
                result.update(outcome="inputs_changed", reason="檢查期間來源或 runtime 改變。")
        except (HarnessError, OSError):
            result.update(outcome="inputs_changed", reason="檢查後未能核對原輸入。")
        pending.update(result, elapsed_seconds=round(time.monotonic() - started, 3))
        value["version"] += 1; atomic_json(directory / "operation.json", value)
        return {**pending, "reused": False}


def valid_check(root, value, name):
    _, key = check_key(root, value, name)
    receipt = value["checks"].get(name, {})
    return receipt.get("outcome") == "passed" and receipt.get("validation_key") == key["validation_key"], key


def evidence_valid(root, value, kind, receipt):
    if receipt.get("outcome") != "passed" or receipt.get("execution") != "external_client_attestation":
        return False
    if receipt.get("kind") != kind or receipt.get("case_id") != value["case"]["case_id"]:
        return False
    checks = receipt.get("checks", {})
    required = {"broken_origin_blocked", "fixed_login_passed", "null_origin_rejected"}
    if not isinstance(checks, dict) or set(checks) != required or any(v is not True for v in checks.values()):
        return False
    reference = receipt.get("check", "regression")
    valid, key = valid_check(root, value, reference)
    if not valid or receipt.get("validation_key") != key["validation_key"]:
        return False
    fixture = value["adapter"].get("browser_fixture")
    if not fixture or receipt.get("fixture_source_sha256") != file_digest(source_file(root, fixture)):
        return False
    try:
        for item in receipt.get("evidence_files", []):
            path = outside_all_sources(item["path"], root)
            if not path.is_file() or file_digest(path) != item["sha256"]:
                return False
        return bool(receipt.get("evidence_files"))
    except (HarnessError, OSError, KeyError, TypeError):
        return False


def attest(state, kind, evidence):
    directory, root, _ = read_state(state)
    supplied = load_json(outside_all_sources(evidence, root))
    with state_lock(directory):
        _, _, value = read_state(directory)
        if kind not in value["adapter"].get("required_attestations", []):
            raise HarnessError("沒有宣告這類客戶端證據。")
        valid, key = valid_check(root, value, "regression")
        if not valid:
            raise HarnessError("先執行目前輸入的 regression 檢查。")
        required = {"broken_origin_blocked", "fixed_login_passed", "null_origin_rejected"}
        checks = supplied.get("checks")
        files = supplied.get("evidence_files")
        allowed = {"contract_version", "kind", "case_id", "input_tree", "client", "outcome", "checks", "fixture_source_sha256", "evidence_files"}
        if (set(supplied) != allowed or type(supplied.get("contract_version")) is not int or supplied.get("contract_version") != 1 or supplied.get("kind") != kind
                or supplied.get("case_id") != value["case"]["case_id"]
                or supplied.get("outcome") not in {"passed", "failed"}
                or supplied.get("input_tree") != key["input_tree"]
                or not isinstance(supplied.get("client"), str) or not 0 < len(supplied["client"]) <= 100
                or any(ord(ch) < 32 for ch in supplied["client"])
                or not isinstance(checks, dict) or required != checks.keys() or any(type(item) is not bool for item in checks.values())
                or not isinstance(files, list) or not 0 < len(files) <= 20):
            raise HarnessError("客戶端證據不符合目前案例與輸入。")
        if supplied["outcome"] == "passed" and any(checks[name] is not True for name in required):
            raise HarnessError("通過的證據必須包含全部必要實測。")
        fixture = value["adapter"].get("browser_fixture")
        if not fixture or supplied.get("fixture_source_sha256") != file_digest(source_file(root, fixture)):
            raise HarnessError("瀏覽器 fixture 來源雜湊不相符。")
        cleaned_files = []
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"} or not isinstance(item["sha256"], str) or not HEX.fullmatch(item["sha256"]):
                raise HarnessError("證據檔案格式不正確。")
            path = outside_all_sources(item["path"], root)
            if not path.is_file() or file_digest(path) != item["sha256"]:
                raise HarnessError("證據檔案不存在或雜湊不符。")
            cleaned_files.append({"path": str(path), "sha256": item["sha256"]})
        receipt = {"kind": kind, "case_id": value["case"]["case_id"], "outcome": supplied["outcome"],
                   "client": supplied["client"], "checks": checks, "evidence_files": cleaned_files,
                   "fixture_source_sha256": supplied["fixture_source_sha256"], "input_tree": key["input_tree"],
                   "validation_key": key["validation_key"], "check": "regression",
                   "execution": "external_client_attestation", "production_status": "not_verified"}
        if value["attestations"].get(kind) == receipt:
            return {**receipt, "reused": True}
        value["attestations"][kind] = receipt
        value["version"] += 1; atomic_json(directory / "operation.json", value)
        return receipt


def status(state):
    _, root, value = read_state(state)
    checks = {name: valid_check(root, value, name)[0] for name in value["case"].get("required_checks", [])}
    attestations = {kind: evidence_valid(root, value, kind, value["attestations"].get(kind, {}))
                    for kind in value["adapter"].get("required_attestations", [])}
    ready = bool(checks) and all(checks.values()) and all(attestations.values())
    return {"operation_id": value["operation_id"], "case_id": value["case"]["case_id"],
            "plan_digest": value["plan_digest"], "local_status": "handoff_ready" if ready else "checks_or_evidence_pending",
            "checks": checks, "attestations": attestations,
            "upstream_status": "candidate_not_submitted", "deployment_status": "not_performed",
            "lifecycle_status": value["lifecycle"]["status"], "production_status": "not_verified"}


def public_file(root, name):
    path = source_file(root, name)
    if path.name.lower() in PRIVATE_NAMES or path.suffix.lower() in PRIVATE_SUFFIXES or any(part.lower() in {"private", "state", "evidence", "data", "backups", ".git"} for part in PurePosixPath(name).parts):
        raise HarnessError("分享清單包含私人或執行期檔案。")
    data = path.read_bytes()
    if len(data) > 2 * 1024 * 1024:
        raise HarnessError("分享來源檔案超過容量上限。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise HarnessError("最小分享包只接受已審閱的 UTF-8 來源文字。") from exc
    patterns = [r"[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]", r"/(?:Users|home)/[^/\s]+/",
                r"https?://[^\s/@:]+:[^\s/@]+@", r"\bsk-[A-Za-z0-9]{20,}\b"]
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
        raise HarnessError("分享來源含私人路徑或疑似憑證；請先人工清理。")
    return data


def bundle(state, output):
    directory, root, value = read_state(state)
    target = outside_all_sources(output, root)
    if target.suffix.lower() != ".zip":
        raise HarnessError("分享產物需要 .zip。")
    with state_lock(directory):
        _, root, value = read_state(directory)
        readiness = status(directory)
        if readiness["local_status"] != "handoff_ready":
            raise HarnessError("必要檢查或瀏覽器證據未通過，不建立分享包。")
        names = value["adapter"]["bundle_files"]
        if len(names) != len(set(names)) or len(names) > 1000:
            raise HarnessError("分享 allowlist 有重複或超過上限。")
        contents = {name: public_file(root, name) for name in sorted(names)}
        manifest = {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()}
        # Bind the captured archive bytes, not just the disk state before packing.
        for check_name in value["case"]["required_checks"]:
            tested = value["checks"][check_name]["inputs"]
            if any(tested.get(name) != sha for name, sha in manifest.items()):
                raise HarnessError("打包讀取的來源與已驗證輸入不同；未建立分享包。")
        if status(directory)["local_status"] != "handoff_ready":
            raise HarnessError("打包期間輸入或證據改變；未建立分享包。")
        public_status = {"contract_version": 1, "case_id": value["case"]["case_id"],
                         "source_revision": value["adapter"]["source_revision"],
                         "candidate_status": "candidate_not_submitted", "deployment_status": "not_performed",
                         "checks": "runner_passed", "browser_evidence": "external_client_attestation",
                         "private_evidence_included": False, "public_source_tree": digest(manifest)}
        contents["HANDOFF_STATUS.json"] = canonical(public_status) + b"\n"
        manifest["HANDOFF_STATUS.json"] = hashlib.sha256(contents["HANDOFF_STATUS.json"]).hexdigest()
        contents["MANIFEST.sha256"] = "".join(f"{sha}  {name}\n" for name, sha in sorted(manifest.items())).encode()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in sorted(contents.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.external_attr = 0o644 << 16; entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, data)
        data = buffer.getvalue(); sha = hashlib.sha256(data).hexdigest()
        reused = target.exists()
        if reused:
            if not target.is_file() or file_digest(target) != sha:
                raise HarnessError("目標已有不同產物；未覆寫。")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
        receipt = {"output": str(target), "sha256": sha, "size": len(data), "files": len(contents),
                   "public_source_tree": public_status["public_source_tree"], "reused": reused,
                   "upstream_status": "candidate_not_submitted", "deployment_status": "not_performed"}
        value["bundle"] = receipt; value["version"] += 1; atomic_json(directory / "operation.json", value)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description="共用實測回饋 harness；執行證據與私人實例分開。")
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("plan"); p.add_argument("--adapter", required=True); p.add_argument("--case", required=True)
    p.add_argument("--state", required=True); p.add_argument("--operation", required=True); p.add_argument("--upstream-source")
    p.add_argument("--project-root", default=str(ROOT), help="明確的項目來源根目錄；共用 engine 保持原位置。")
    p = commands.add_parser("run"); p.add_argument("--state", required=True); p.add_argument("--check", required=True)
    p.add_argument("--acknowledge-unknown", action="store_true")
    p = commands.add_parser("attest"); p.add_argument("--state", required=True); p.add_argument("--kind", required=True); p.add_argument("--evidence", required=True)
    p = commands.add_parser("status"); p.add_argument("--state", required=True)
    p = commands.add_parser("bundle"); p.add_argument("--state", required=True); p.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan_operation(args.adapter, args.case, args.state, args.operation, args.upstream_source, root=args.project_root)
        elif args.command == "run":
            result = run_check(args.state, args.check, args.acknowledge_unknown)
        elif args.command == "attest":
            result = attest(args.state, args.kind, args.evidence)
        elif args.command == "bundle":
            result = bundle(args.state, args.output)
        else:
            result = status(args.state)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if "outcome" in result and result["outcome"] != "passed" else 0
    except (HarnessError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, HarnessError) else "操作未完成；請核對檔案與本機執行環境。"
        print(json.dumps({"error": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
