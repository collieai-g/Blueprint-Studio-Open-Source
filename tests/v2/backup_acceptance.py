#!/usr/bin/env python3
"""Exercise the actual Rust backup/restore CLI with two disposable databases.

Requires a local PostgreSQL administrator, pg_dump/pg_restore matching the server,
and a built blueprint-backend. No running company database is modified. Each run
creates randomly named databases and temporary private storage, then removes both.
Use --result-file to save sanitized, portable JSON evidence outside test storage.
"""
import argparse
import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def request(self, path, method="GET", data=None, headers=None, expected=200):
        request_headers = {"X-Blueprint-Request": "1"}
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base + "/api/v1" + path,
            method=method, data=data, headers=request_headers,
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                status, body, content_type = response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            status, body, content_type = error.code, error.read(), error.headers.get("Content-Type", "")
        allowed = {expected} if isinstance(expected, int) else set(expected)
        assert status in allowed, f"{method} {path}: HTTP {status}, expected {sorted(allowed)}"
        return json.loads(body) if "application/json" in content_type else body

    def login(self, email, password, otp=None, expected=200):
        data = {"email": email, "password": password}
        if otp is not None:
            data["otp"] = otp
        return self.request("/login", "POST", data, expected=expected)

    def upload(self, name, content, parent=None, resource=None, version=None):
        boundary = "BlueprintBackupTest" + uuid.uuid4().hex
        body = bytearray()
        for key, value in (("parent_id", parent), ("expected_version", version)):
            if value is not None:
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: text/plain\r\n\r\n'.encode())
        body.extend(content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        path = f"/files/{resource}/versions" if resource else "/files/upload"
        return self.request(path, "POST", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}, expected={200, 201})


def totp(secret):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return f"{value % 1_000_000:06d}"


def stop(process):
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=Path(os.environ.get("BLUEPRINT_TEST_BINARY", "/tmp/blueprint-v2-target/debug/blueprint-backend")))
    parser.add_argument("--database-url", default=os.environ.get("TEST_DATABASE_URL", "postgresql://blueprint_v2@127.0.0.1:55433/blueprint_v2_dev"))
    parser.add_argument("--postgres-container", default="blueprint-v2-postgres", help="Fallback psql transport when psql is not on PATH")
    parser.add_argument("--source-port", type=int, default=18791)
    parser.add_argument("--target-port", type=int, default=18792)
    parser.add_argument("--result-file", type=Path)
    return parser.parse_args()


def run(args):
    database = urllib.parse.urlsplit(args.database_url)
    if database.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Only an explicitly disposable loopback PostgreSQL server is allowed")
    if not args.binary.is_file():
        raise RuntimeError("Build the Rust CLI before running backup acceptance")
    for port in (args.source_port, args.target_port):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    username = urllib.parse.unquote(database.username or "postgres")
    database_name = urllib.parse.unquote(database.path.lstrip("/") or "postgres")
    pg_environment = os.environ.copy()
    pg_environment.update(PGHOST=database.hostname, PGPORT=str(database.port or 5432), PGUSER=username, PGDATABASE=database_name)
    if database.password:
        pg_environment["PGPASSWORD"] = urllib.parse.unquote(database.password)
    if os.environ.get("BLUEPRINT_TEST_PSQL_COMMAND"):
        psql = shlex.split(os.environ["BLUEPRINT_TEST_PSQL_COMMAND"])
    elif shutil.which("psql"):
        psql = ["psql"]
    else:
        psql = ["docker", "exec", "-i", args.postgres_container, "psql", "-U", username, "-d", database_name]

    def sql(statement):
        result = subprocess.run(psql + ["-X", "-v", "ON_ERROR_STOP=1", "-At", "-c", statement], env=pg_environment, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("PostgreSQL acceptance setup/query failed")
        return result.stdout.strip()

    suffix = uuid.uuid4().hex[:16]
    source_db, target_db = f"blueprint_backup_source_{suffix}", f"blueprint_backup_target_{suffix}"
    created, processes, logs = [], [], []
    evidence = {
        "schema": "blueprint-backup-acceptance-v1",
        "executed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
        "postgresql": sql("SHOW server_version"),
        "external_services": "none",
        "checks": {},
    }

    def checked(name):
        evidence["checks"][name] = True
        print("PASS " + name, flush=True)

    with tempfile.TemporaryDirectory(prefix="blueprint-backup-acceptance-") as temporary:
        root = Path(temporary)
        clean_environment = {key: value for key, value in os.environ.items() if not key.startswith("BLUEPRINT_")}
        # Optional adapters, mail and externally provided keys cannot affect this test.
        clean_environment["RUST_LOG"] = "blueprint_backend=warn,tower_http=warn"
        key = secrets.token_hex(32)
        setup_token = secrets.token_urlsafe(32)
        owner_email, member_email = "restore-owner@example.invalid", "restore-member@example.invalid"
        password = secrets.token_urlsafe(30)

        def environment(name, label, port, master_key=None):
            connection = urllib.parse.urlunsplit(database._replace(path="/" + name))
            values = dict(clean_environment, BLUEPRINT_DATABASE_URL=connection, BLUEPRINT_DATA_DIR=str(root / label / "data"), BLUEPRINT_BIND=f"127.0.0.1:{port}", BLUEPRINT_PUBLIC_URL=f"http://127.0.0.1:{port}", BLUEPRINT_SETUP_TOKEN=setup_token, BLUEPRINT_UI_DIR=str(root / "empty-ui"))
            if master_key:
                values["BLUEPRINT_MASTER_KEY"] = master_key
            return values

        source_env = environment(source_db, "source", args.source_port, key)
        target_env = environment(target_db, "target", args.target_port)

        def launch(env, label, port):
            handle = (root / f"{label}.log").open("wb")
            logs.append(handle)
            process = subprocess.Popen([str(args.binary), "server"], env=env, stdout=handle, stderr=subprocess.STDOUT)
            processes.append(process)
            client = Client(f"http://127.0.0.1:{port}")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"{label} server exited before readiness")
                try:
                    status = client.request("/status")
                    return process, client, status
                except (OSError, urllib.error.URLError):
                    time.sleep(0.1)
            raise RuntimeError(f"{label} server readiness timed out")

        def command(env, action, path, expected_error=None):
            result = subprocess.run([str(args.binary), action, str(path)], env=env, capture_output=True, text=True, timeout=90)
            if expected_error:
                assert result.returncode != 0 and expected_error.lower() in result.stderr.lower(), f"CLI {action} did not refuse {expected_error}"
            else:
                assert result.returncode == 0, f"CLI {action} failed (exit {result.returncode})"
            return result

        try:
            for name in (source_db, target_db):
                sql(f"CREATE DATABASE {name}")
                created.append(name)
            source_process, owner, status = launch(source_env, "source", args.source_port)
            assert status["initialized"] is False
            owner.request("/setup", "POST", {"token": setup_token, "company_name": "備份還原測試公司", "email": owner_email, "name": "Private owner identity", "password": password})
            identity = owner.request("/me")
            member = owner.request("/users", "POST", {"email": member_email, "name": "Private member identity", "role": "member", "password": password}, expected={200, 201})
            folder = owner.request("/resources", "POST", {"kind": "folder", "name": "Private originals", "visibility": "private", "data": {}}, expected=201)
            original, current = "藍圖工作室\n原稿 v1\n".encode(), "藍圖工作室\n修訂 v2\n".encode()
            file = owner.upload("company-original.txt", original, parent=folder["id"])
            file = owner.upload("company-original.txt", current, resource=file["id"], version=file["version"])
            assert file["data"]["current_version"] == 2
            note = owner.request("/resources", "POST", {"kind": "note", "name": "Private operating decisions", "visibility": "private", "data": {"content": "Private decision revision one"}}, expected=201)
            note = owner.request("/resource/" + note["id"], "PATCH", {"expected_version": note["version"], "data": {"content": "Private decision revision two — 已覆核"}})
            shared = owner.request("/resources", "POST", {"kind": "note", "name": "Explicitly shared decision", "visibility": "private", "acl": {"user:" + member["id"]: "viewer"}, "data": {"content": "A viewer can read this decision"}}, expected=201)
            challenge = owner.request("/auth/mfa/setup", "POST", {})
            superseded_challenge = owner.request("/auth/mfa/setup", "POST", {})
            history = owner.request("/resource/" + note["id"] + "/history")
            assert len(history) == 2
            checked("source_original_versions_private_identities_notes_and_mfa_challenge_persisted")

            # No worker is started; stopping this sole server stops every writer.
            stop(source_process)
            assert source_process.poll() is not None
            snapshot = root / "snapshot"
            command(source_env, "backup", snapshot)
            manifest = json.loads((snapshot / "manifest.json").read_text())
            assert manifest["schema"] == "blueprint-backup-v2" and manifest["storage"] == "local"
            assert manifest["requires_stopped_writers"] is True
            assert manifest["database_sha256"] == hashlib.sha256((snapshot / "database.dump").read_bytes()).hexdigest()
            assert (snapshot / "data" / "master.key").read_bytes() == bytes.fromhex(key)
            evidence["backup_format"] = manifest["schema"]
            evidence["backup_database_sha256"] = manifest["database_sha256"]
            checked("actual_pg_dump_snapshot_contains_database_objects_and_active_master_key")
            command(source_env, "backup", snapshot, "Backup path already exists")
            checked("backup_refuses_overwriting_existing_snapshot")

            corrupted = root / "corrupted"
            shutil.copytree(snapshot, corrupted)
            with (corrupted / "database.dump").open("ab") as handle:
                handle.write(b"intentional acceptance checksum corruption")
            command(target_env, "restore", corrupted, "Backup checksum mismatch")
            assert sql(f"SELECT COUNT(*) FROM pg_database WHERE datname='{target_db}'") == "1"
            checked("restore_rejects_database_checksum_mismatch")
            command(target_env, "restore", snapshot)
            assert (root / "target" / "data" / "master.key").read_bytes() == bytes.fromhex(key)
            checked("actual_pg_restore_into_fresh_database_and_storage")

            target_process, restored, status = launch(target_env, "restored", args.target_port)
            assert status["initialized"] is True and status["company_name"] == "備份還原測試公司"
            restored.login(owner_email, password)
            assert restored.request("/me")["id"] == identity["id"]
            assert restored.request(f"/files/{file['id']}/download") == current
            assert restored.request(f"/files/{file['id']}/versions/1/download") == original
            versions = restored.request(f"/files/{file['id']}/versions")
            assert len(versions["versions"]) == 2
            assert restored.request("/resource/" + note["id"])["data"]["content"] == note["data"]["content"]
            assert restored.request("/resource/" + note["id"] + "/history") == history
            evidence["original_sha256"] = hashlib.sha256(original).hexdigest()
            evidence["current_sha256"] = hashlib.sha256(current).hexdigest()
            evidence["restored_file_versions"] = len(versions["versions"])
            checked("restored_login_identity_company_original_hashes_versions_note_and_history")

            member_client = Client(restored.base)
            member_client.login(member_email, password)
            assert member_client.request("/me")["id"] == member["id"]
            for path in (f"/files/{file['id']}/download", "/resource/" + note["id"]):
                member_client.request(path, expected={403, 404})
            assert member_client.request("/resource/" + shared["id"])["data"]["content"] == shared["data"]["content"]
            checked("restored_private_denials_and_explicit_viewer_acl")

            assert restored.request("/auth/mfa/confirm", "POST", {"token": challenge["token"], "code": totp(challenge["secret"])})["enabled"] is True
            restored.request("/auth/mfa/setup", "POST", {}, expected=409)
            restored.request("/auth/mfa/confirm", "POST", {"token": superseded_challenge["token"], "code": totp(superseded_challenge["secret"])}, expected=409)
            fresh_login = Client(restored.base)
            denial = fresh_login.login(owner_email, password, expected=401)
            assert denial["error"]["code"] == "mfa_required"
            fresh_login.login(owner_email, password, otp=totp(challenge["secret"]))
            assert fresh_login.request("/me")["id"] == identity["id"]
            checked("restored_encrypted_mfa_challenge_decrypts_and_totp_login_is_enforced")

            stop(target_process)
            command(target_env, "restore", snapshot, "fresh empty installation")
            checked("restore_refuses_populated_installation")
            evidence["passed"] = True
            return evidence
        finally:
            for process in processes:
                stop(process)
            for handle in logs:
                handle.close()
            for name in reversed(created):
                sql(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def main():
    args = options()
    result = run(args)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.result_file:
        args.result_file.parent.mkdir(parents=True, exist_ok=True)
        args.result_file.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
