#!/usr/bin/env python3
"""Exercise an already-built production image using disposable Docker resources.

No Rust/Node compilation runs here. Only the app's loopback HTTP port is
published. Generated credentials stay in private temporary env files and memory.
"""
import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid


class RuntimeCheck:
    def __init__(self, image, directory):
        self.image = image
        self.directory = Path(directory)
        self.prefix = "blueprint-runtime-" + uuid.uuid4().hex[:12]
        self.network = self.prefix + "-network"
        self.postgres = self.prefix + "-postgres"
        self.app = self.prefix + "-app"
        self.worker = self.prefix + "-worker"
        self.backup = self.prefix + "-backup"
        self.containers = []
        self.volumes = [self.prefix + "-pg", self.prefix + "-data", self.prefix + "-backups"]
        self.redactions = []
        self.checks = []
        self.base = ""
        self.port_reservation = None
        self.opener = self.client()

    @staticmethod
    def client():
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )

    def clean(self, text):
        for secret in self.redactions:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def docker(self, *arguments, required=True):
        result = subprocess.run(
            ["docker", *arguments], text=True, capture_output=True, timeout=180
        )
        if required and result.returncode:
            raise RuntimeError(self.clean("Docker operation failed: " + result.stderr[-3000:]))
        return result

    def env_file(self, name, values):
        path = self.directory / name
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
            for key, value in values.items():
                output.write(key + "=" + value + "\n")
        return str(path)

    def passed(self, name):
        self.checks.append(name)
        print("PASS " + name, flush=True)

    def request(self, path, method="GET", data=None, headers=None, expected=200):
        request_headers = {"X-Blueprint-Request": "1", "Origin": self.base}
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data).encode()
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = urllib.request.Request(
            self.base + path, method=method, data=data, headers=request_headers
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                status = response.status
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
                cookies = response.headers.get_all("Set-Cookie", [])
                for cookie in cookies:
                    self.redactions.append(cookie.split(";", 1)[0].split("=", 1)[-1])
        except urllib.error.HTTPError as error:
            raise RuntimeError(self.clean(
                f"HTTP {method} {path}: {error.code}: {error.read(2000).decode(errors='replace')}"
            )) from None
        if status != expected:
            raise RuntimeError(f"HTTP {method} {path}: expected {expected}, received {status}")
        return json.loads(body) if "application/json" in content_type else body

    def wait_postgres(self):
        for _ in range(120):
            result = self.docker("exec", self.postgres, "pg_isready", "-h", "127.0.0.1", "-U", "blueprint", "-d", "blueprint", required=False)
            if result.returncode == 0:
                return
            time.sleep(0.5)
        raise RuntimeError("Disposable PostgreSQL did not become ready")

    def wait_app(self):
        for _ in range(120):
            try:
                if self.request("/health")["status"] == "ok":
                    return
            except (OSError, urllib.error.URLError, RuntimeError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Production app did not become healthy")

    def run(self):
        user = json.loads(self.docker("image", "inspect", "--format", "{{json .Config.User}}", self.image).stdout)
        if user in ("", "0", "root", "0:0", "root:root"):
            raise RuntimeError("Production image must declare a non-root default user")
        self.port_reservation = socket.socket()
        self.port_reservation.bind(("127.0.0.1", 0))
        port = self.port_reservation.getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"
        database_password, setup_token, master_key, password = [secrets.token_hex(32) for _ in range(4)]
        self.redactions.extend([database_password, setup_token, master_key, password])
        pg_env = self.env_file("postgres.env", {
            "POSTGRES_USER": "blueprint", "POSTGRES_DB": "blueprint", "POSTGRES_PASSWORD": database_password,
        })
        app_env = self.env_file("app.env", {
            "BLUEPRINT_DATABASE_URL": f"postgresql://blueprint:{database_password}@{self.postgres}:5432/blueprint",
            "BLUEPRINT_DATA_DIR": "/data", "BLUEPRINT_UI_DIR": "/app/ui/dist",
            "BLUEPRINT_BIND": "0.0.0.0:8787", "BLUEPRINT_PUBLIC_URL": self.base,
            "BLUEPRINT_SETUP_TOKEN": setup_token, "BLUEPRINT_MASTER_KEY": master_key,
            "RUST_LOG": "warn",
        })
        self.docker("network", "create", self.network)
        for volume in self.volumes:
            self.docker("volume", "create", volume)
        self.containers.append(self.postgres)
        self.docker("run", "-d", "--name", self.postgres, "--network", self.network,
                    "--env-file", pg_env, "-v", self.volumes[0] + ":/var/lib/postgresql/data",
                    "postgres:17.11-bookworm")
        self.wait_postgres()
        shared = ["--network", self.network, "--env-file", app_env, "-v", self.volumes[1] + ":/data"]
        self.containers.append(self.app)
        self.port_reservation.close()
        self.docker("run", "-d", "--name", self.app, *shared,
                    "-v", self.volumes[2] + ":/backups", "-p", f"127.0.0.1:{port}:8787", self.image, "server")
        self.wait_app()
        self.docker("exec", self.app, "curl", "--fail", "--silent", "http://127.0.0.1:8787/health")
        if self.docker("exec", self.app, "id", "-u").stdout.strip() == "0":
            raise RuntimeError("Production app runs as root")
        ports = json.loads(self.docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", self.app).stdout)
        if any(binding["HostIp"] != "127.0.0.1" for bindings in ports.values() if bindings for binding in bindings):
            raise RuntimeError("App published a non-loopback host port")
        self.passed("production image starts healthy as a non-root user on loopback")
        html = self.request("/")
        if b"<html" not in html.lower() or b"/assets/" not in html:
            raise RuntimeError("Compiled production UI was not served")
        self.passed("compiled web UI is served from the production image")
        if self.request("/api/v1/status")["initialized"]:
            raise RuntimeError("Disposable installation was unexpectedly initialized")
        self.request("/api/v1/setup", "POST", {
            "token": setup_token, "company_name": "Disposable Container Company",
            "email": "owner@example.test", "name": "Container Owner", "password": password,
        })
        status = self.request("/api/v1/status")
        if not status["initialized"] or status["company_name"] != "Disposable Container Company":
            raise RuntimeError("First-run setup did not persist")
        self.passed("first-run setup creates an authenticated company owner")
        original = "Blueprint container worker and persistence validation 藍圖\n".encode()
        boundary = "BlueprintRuntime" + uuid.uuid4().hex
        multipart = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="container-smoke.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode()
            + original + f"\r\n--{boundary}--\r\n".encode()
        )
        file = self.request("/api/v1/files/upload", "POST", multipart, {"Content-Type": "multipart/form-data; boundary=" + boundary})
        file_id = file["id"]
        if self.request(f"/api/v1/files/{file_id}/download") != original:
            raise RuntimeError("Uploaded original bytes differ")
        self.passed("actual UTF-8 file upload and original download match")
        queued = [job for job in self.request("/api/v1/jobs")
                  if job["kind"] == "content.process" and job["payload"].get("file_id") == file_id]
        if not queued or queued[0]["status"] != "queued":
            raise RuntimeError("Upload did not create a persisted job before starting the worker")
        self.containers.append(self.worker)
        self.docker("run", "-d", "--name", self.worker, "--no-healthcheck", *shared, self.image, "worker")
        if self.docker("exec", self.worker, "id", "-u").stdout.strip() == "0":
            raise RuntimeError("Production worker runs as root")
        for _ in range(120):
            jobs = self.request("/api/v1/jobs")
            matches = [job for job in jobs if job["kind"] == "content.process" and job["payload"].get("file_id") == file_id]
            if matches and matches[0]["status"] == "failed":
                raise RuntimeError("Actual worker file-processing job failed")
            if matches and matches[0]["status"] == "completed":
                processed = self.request("/api/v1/resource/" + file_id)
                if processed["data"].get("extracted_text") != original.decode():
                    raise RuntimeError("Worker did not persist actual text extraction")
                if processed["data"]["scan"]["status"] == "clean":
                    raise RuntimeError("Unconfigured antivirus was incorrectly reported as clean")
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Actual worker did not complete its persisted job")
        self.passed("separate non-root worker completes actual text processing without faking antivirus")
        self.docker("stop", "-t", "30", self.worker, self.app)
        self.docker("stop", "-t", "30", self.postgres)
        self.docker("start", self.postgres)
        self.wait_postgres()
        self.docker("start", self.app)
        self.wait_app()
        self.docker("start", self.worker)
        self.opener = self.client()
        self.request("/api/v1/login", "POST", {"email": "owner@example.test", "password": password})
        if self.request(f"/api/v1/files/{file_id}/download") != original:
            raise RuntimeError("Original bytes did not survive app/database restart")
        if self.request("/api/v1/resource/" + file_id)["data"].get("extracted_text") != original.decode():
            raise RuntimeError("Worker metadata did not survive restart")
        self.passed("PostgreSQL and app restart preserve login, original hash and worker results")
        self.docker("stop", "-t", "30", self.worker, self.app)
        self.containers.append(self.backup)
        self.docker("run", "--rm", "--name", self.backup, *shared,
                    "-v", self.volumes[2] + ":/backups", self.image, "backup", "/backups/runtime-smoke")
        self.docker("run", "--rm", "--name", self.backup, "--entrypoint", "sh",
                    "-v", self.volumes[2] + ":/backups", self.image, "-c",
                    "test -s /backups/runtime-smoke/database.dump && test -s /backups/runtime-smoke/manifest.json && test -s /backups/runtime-smoke/data/master.key")
        self.passed("production image contains pg_dump and writes a private core backup as non-root")
        print(json.dumps({"schema": "blueprint-container-smoke-v1", "image": self.image,
                          "checks": self.checks, "original_sha256": hashlib.sha256(original).hexdigest(), "passed": True}, indent=2))

    def diagnostics(self):
        for container in self.containers:
            result = self.docker("logs", "--tail", "60", container, required=False)
            if result.returncode == 0:
                print(self.clean(f"Diagnostic {container}:\n{result.stdout}{result.stderr}"), file=sys.stderr)

    def cleanup(self):
        if self.port_reservation is not None:
            self.port_reservation.close()
        for container in reversed(self.containers):
            self.docker("rm", "-f", "-v", container, required=False)
        for volume in self.volumes:
            self.docker("volume", "rm", volume, required=False)
        self.docker("network", "rm", self.network, required=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="blueprint-backend:ci")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="blueprint-runtime-private-") as directory:
        check = RuntimeCheck(args.image, directory)
        try:
            check.run()
        except Exception as error:
            print(check.clean(str(error)), file=sys.stderr)
            check.diagnostics()
            return 1
        finally:
            check.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
