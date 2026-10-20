#!/usr/bin/env python3
"""Disposable, authenticated HTTP metadata benchmark; Python standard library only.

Creates and drops ONLY its own randomly named database. Never accepts an existing
application database or a production base URL. Needs CREATEDB plus psql (locally
or in the selected existing PostgreSQL container), and an already-built binary.
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid


def fixture_id(kind, number):
    return str(uuid.UUID(hashlib.md5(f"bench-{kind}:{number}".encode()).hexdigest()))


def percentile(values, percent):
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)], 3)


def hardware():
    result = {"logical_host_cpus": os.cpu_count()}
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        result["cgroup_cpu_limit"] = None if quota == "max" else int(quota) / int(period)
        memory = Path("/sys/fs/cgroup/memory.max").read_text().strip()
        result["cgroup_memory_gib"] = None if memory == "max" else round(int(memory) / 2**30, 2)
    except (OSError, ValueError):
        result["cgroup_limits"] = "not available"
    return result


class Client:
    def __init__(self, port, identity, cookie):
        self.port, self.identity, self.cookie = port, identity, cookie
        self.lock = threading.Lock()
        self.connection = None

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def get(self, path):
        with self.lock:
            if self.connection is None:
                self.connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
            try:
                self.connection.request("GET", path, headers={"Cookie": f"blueprint_session={self.cookie}"})
                response = self.connection.getresponse()
                raw = response.read()
                return response.status, json.loads(raw)
            except Exception:
                self.close()
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--postgres-container", default="", help="Use psql in this existing local Docker container; otherwise use local psql")
    parser.add_argument("--pg-host", default="127.0.0.1")
    parser.add_argument("--pg-port", default=5432, type=int)
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--port", default=18790, type=int)
    parser.add_argument("--users", default=10000, type=int)
    parser.add_argument("--sessions", default=200, type=int)
    parser.add_argument("--rate", default=40.0, type=float)
    parser.add_argument("--seconds", default=60.0, type=float)
    parser.add_argument("--p95-budget-ms", default=500.0, type=float)
    parser.add_argument("--output", type=Path, help="Write aggregate JSON evidence; contains no fixture tokens")
    args = parser.parse_args()
    if not 1 <= args.sessions < args.users or args.users > 100000 or args.rate <= 0 or args.seconds < 1:
        parser.error("Require 1 <= sessions < users <= 100000, positive rate and seconds >= 1; one unmeasured fixture owner is reserved")
    binary = args.binary.resolve(strict=True)
    # Refuse to attach to another process. This runner always starts its own server.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    database = "blueprint_bench_" + uuid.uuid4().hex
    pg_env = os.environ.copy()
    pg_env.update(PGHOST=args.pg_host, PGPORT=str(args.pg_port), PGUSER=args.pg_user)

    def sql(statement, db="postgres"):
        command = ["psql", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-d", db]
        if args.postgres_container:
            # Pin the local daemon; no registry pulls or container creation occur.
            command = ["docker", "--host=unix:///var/run/docker.sock", "exec", "-i", args.postgres_container, "psql", "-X", "-q", "-U", args.pg_user, "-v", "ON_ERROR_STOP=1", "-d", db]
        result = subprocess.run(command, input=statement, text=True, capture_output=True, env=pg_env)
        if result.returncode:
            raise RuntimeError("Disposable PostgreSQL command failed: " + result.stderr[-1500:])
        return result.stdout

    cookies = [secrets.token_hex(32) for _ in range(args.sessions)]
    clients = [Client(args.port, i, token) for i, token in enumerate(cookies)]
    server = None
    created = False
    report = None
    try:
        sql(f'CREATE DATABASE "{database}";')
        created = True
        with tempfile.TemporaryDirectory(prefix="blueprint-benchmark-") as directory:
            environment = os.environ.copy()
            # Isolate this application instance from configured company services.
            for key in list(environment):
                if key.startswith("BLUEPRINT_") and key != "BLUEPRINT_CA_FILE":
                    environment.pop(key)
            authority = f"{args.pg_user}@{args.pg_host}:{args.pg_port}"
            # Optional password is inherited through libpq or supplied only to the
            # isolated child using URL escaping; never logged or written in evidence.
            if environment.get("PGPASSWORD"):
                from urllib.parse import quote
                authority = f"{quote(args.pg_user, safe='')}:{quote(environment['PGPASSWORD'], safe='')}@{args.pg_host}:{args.pg_port}"
            environment.update(BLUEPRINT_DATABASE_URL=f"postgresql://{authority}/{database}", BLUEPRINT_DATA_DIR=directory, BLUEPRINT_BIND=f"127.0.0.1:{args.port}", BLUEPRINT_PUBLIC_URL=f"http://127.0.0.1:{args.port}", RUST_LOG="warn")
            with open(Path(directory) / "server.log", "wb") as log:
                server = subprocess.Popen([str(binary), "server"], stdout=log, stderr=subprocess.STDOUT, env=environment)
                deadline = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError("Isolated server exited before health was ready")
                    try:
                        connection = http.client.HTTPConnection("127.0.0.1", args.port, timeout=1)
                        connection.request("GET", "/health")
                        response = connection.getresponse()
                        ready = response.status == 200
                        response.read()
                        connection.close()
                        if ready:
                            break
                    except OSError:
                        pass
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Isolated server health timed out")
                    time.sleep(0.1)
                session_rows = ",".join(f"('{hashlib.sha256(token.encode()).hexdigest()}','{fixture_id('user', i)}',now()+interval '1 hour')" for i, token in enumerate(cookies))
                # Fixtures are direct inserts for speed. Requests still use the real
                # cookie middleware, current account reloads, ACL and policy code.
                # No permissions, triggers, row security or auth checks are disabled.
                fixture_sql = f"""
                INSERT INTO users(id,email,name,password_hash,role,groups)
                SELECT md5('bench-user:'||i)::uuid,'bench-'||i||'@example.test','Benchmark member '||i,
                    'fixture-session-only-password-login-disabled',CASE WHEN i={args.users-1} THEN 'owner' ELSE 'member' END,
                    jsonb_build_array(md5('bench-group:'||(i%10))::uuid::text)
                FROM generate_series(0,{args.users-1}) i;
                INSERT INTO sessions(token_hash,user_id,expires_at) VALUES {session_rows};
                INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document)
                SELECT md5('bench-group:'||g)::uuid,'group','Benchmark department '||g,
                    md5('bench-user:'||({args.users-1}))::uuid,'company','{{}}',
                    jsonb_build_object('members',(SELECT jsonb_agg(jsonb_build_object('value',md5('bench-user:'||i)::uuid::text)) FROM generate_series(0,{args.users-1}) i WHERE i%10=g)),
                    'Benchmark department '||g FROM generate_series(0,9) g;
                INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document)
                SELECT md5('bench-file:'||i)::uuid,'file','Private metadata '||i,
                    md5('bench-user:'||i)::uuid,'private','{{}}',
                    '{{"mime":"text/plain","size":128,"current_version":1,"classification":"internal","scan":{{"status":"clean"}}}}',
                    'Private metadata '||i FROM generate_series(0,{args.users-1}) i;
                INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document)
                SELECT md5('bench-extra:'||i)::uuid,'file','Additional private metadata '||i,
                    md5('bench-user:'||(i/10))::uuid,'private','{{}}',
                    '{{"mime":"text/plain","size":128,"current_version":1,"classification":"internal","scan":{{"status":"clean"}}}}',
                    'Additional private metadata '||i FROM generate_series(0,{args.sessions*10-1}) i;
                INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document)
                SELECT md5('bench-department:'||i)::uuid,'file','Department metadata '||i,
                    md5('bench-user:'||({args.users-1}))::uuid,'private',
                    jsonb_build_object('group:'||md5('bench-group:'||(i/20))::uuid::text,'viewer'),
                    '{{"mime":"text/plain","size":128,"current_version":1,"classification":"internal","scan":{{"status":"clean"}}}}',
                    'Department metadata '||i FROM generate_series(0,199) i;
                ANALYZE users; ANALYZE sessions; ANALYZE resources;
                """
                sql(fixture_sql, database)
                print(f"Seeded {args.users} members and {args.sessions} browser sessions; checking HTTP isolation", file=sys.stderr, flush=True)

                def preflight(client):
                    allowed, resource = client.get(f"/api/v1/resource/{fixture_id('file', client.identity)}")
                    denied, _ = client.get(f"/api/v1/resource/{fixture_id('file', (client.identity+1)%args.users)}")
                    if allowed != 200 or resource["owner_id"] != fixture_id("user", client.identity) or denied != 404:
                        raise RuntimeError("Authenticated private-resource isolation check failed")
                    department = client.identity % 10
                    group_allowed, allowed_result = client.get(f"/api/v1/resource/{fixture_id('department', department*20)}")
                    group_denied, denied_result = client.get(f"/api/v1/resource/{fixture_id('department', ((department+1)%10)*20)}")
                    if group_allowed != 200 or group_denied != 404:
                        raise RuntimeError(f"Authenticated department-group isolation check failed for fixture {client.identity}: allowed={group_allowed} {allowed_result.get('error', {})}; denied={group_denied} {denied_result.get('error', {})}")

                with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
                    list(pool.map(preflight, clients))
                    print(f"All {args.sessions*4} HTTP isolation checks passed; offering {args.rate:g} GET/s for {args.seconds:g} seconds", file=sys.stderr, flush=True)
                    total = math.ceil(args.rate * args.seconds)
                    start = time.monotonic()

                    def request(index, scheduled):
                        client = clients[index % len(clients)]
                        # Each session exercises every route, independent of its ID.
                        choice = (index // len(clients)) % 4
                        path = ["/api/v1/me", f"/api/v1/resource/{fixture_id('file', client.identity)}", "/api/v1/resources/file", "/api/v1/resources/file?q=Department"][choice]
                        began = time.monotonic()
                        error = None
                        try:
                            status, result = client.get(path)
                            if status != 200:
                                error = f"http_{status}"
                            elif choice == 0 and result.get("id") != fixture_id("user", client.identity):
                                error = "identity_mismatch"
                            elif choice == 1 and result.get("owner_id") != fixture_id("user", client.identity):
                                error = "private_acl_leak"
                            elif choice >= 2:
                                principal = "group:" + fixture_id("group", client.identity % 10)
                                if not isinstance(result, list) or not result:
                                    error = "invalid_or_empty_list"
                                elif any(r.get("owner_id") != fixture_id("user", client.identity) and r.get("acl", {}).get(principal) != "viewer" for r in result):
                                    error = "department_acl_leak"
                        except Exception as failure:
                            error = type(failure).__name__
                        ended = time.monotonic()
                        return {"response_ms": (ended-began)*1000, "scheduled_ms": (ended-scheduled)*1000, "dispatch_lag_ms": max(0, began-scheduled)*1000, "error": error}

                    futures = []
                    for index in range(total):
                        scheduled = start + index / args.rate
                        wait = scheduled-time.monotonic()
                        if wait > 0:
                            time.sleep(wait)
                        futures.append(pool.submit(request, index, scheduled))
                    results = [future.result() for future in futures]
                    elapsed = time.monotonic()-start
                errors = {}
                for result in results:
                    if result["error"]:
                        errors[result["error"]] = errors.get(result["error"], 0) + 1
                response_times = [r["response_ms"] for r in results]
                scheduled_times = [r["scheduled_ms"] for r in results]
                with binary.open("rb") as binary_stream:
                    binary_sha256 = hashlib.file_digest(binary_stream, "sha256").hexdigest()
                report = {
                    "schema": "blueprint-metadata-benchmark-v1", "measured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "hardware": hardware(), "proposed_deployment_target": {"cpu": 8, "memory_gib": 32, "measured_on_this_target": False},
                    "binary": {"sha256": binary_sha256, "profile": "debug" if "debug" in binary.parts else "unverified", "version": subprocess.check_output([str(binary), "--version"], text=True).strip()},
                    "fixtures": {"users": args.users, "authenticated_browser_sessions": args.sessions, "metadata_files": args.users+args.sessions*10+200, "departments": 10, "private_and_group_acl_preflight_checks": args.sessions*4, "file_bytes": 0, "metadata_only": True},
                    "workload": {"offered_requests_per_second": args.rate, "seconds": args.seconds, "requests": total, "route_mix": ["GET /me", "GET /resource/:id", "GET /resources/file", "GET /resources/file?q=Department"], "max_client_threads": 32, "persistent_connections": args.sessions, "open_loop": True},
                    "results": {"elapsed_seconds": round(elapsed, 3), "completed_requests_per_second": round(total/elapsed, 3), "http_response_p50_ms": percentile(response_times, 50), "http_response_p95_ms": percentile(response_times, 95), "scheduled_to_completion_p50_ms": percentile(scheduled_times, 50), "scheduled_to_completion_p95_ms": percentile(scheduled_times, 95), "max_dispatch_lag_ms": round(max(r["dispatch_lag_ms"] for r in results), 3), "errors": sum(errors.values()), "error_rate": sum(errors.values())/total, "error_counts": errors},
                    "acceptance": {"p95_budget_ms": args.p95_budget_ms, "passed": not errors and total/elapsed >= args.rate*.95 and percentile(scheduled_times, 95) <= args.p95_budget_ms},
                    "limitations": ["Logical sessions and 200 persistent connections do not mean 200 simultaneous busy requests.", "Metadata-only reads; excludes upload, full-text scale, previews, AI workers, calendar, Office, scanning and backup load.", "Loopback HTTP, single app process and local PostgreSQL; no TLS, WAN or reverse-proxy latency.", "Fixtures are inserted directly; actual permission decisions and measured reads run through HTTP.", "This is observed capacity on the recorded host, not a promise for the proposed deployment size."]
                }
                sql("SELECT pg_size_pretty(pg_database_size(current_database()));", database)
    finally:
        for client in clients:
            client.close()
        if server and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
        if created:
            sql(f'DROP DATABASE "{database}" WITH (FORCE);')
    encoded = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end="")
    return 0 if report["acceptance"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
