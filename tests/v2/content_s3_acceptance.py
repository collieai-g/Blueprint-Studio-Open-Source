#!/usr/bin/env python3
"""Real Rust HTTP/storage smoke against disposable local PostgreSQL + SeaweedFS.

Requires an already-built BLUEPRINT_TEST_BINARY and S3 credential selector file
BLUEPRINT_TEST_S3_CREDENTIAL_FILE (Seaweed identities[0].credentials[0]). No secret
values are printed. Creates/removes its own app DB; never runs Docker pulls.
"""
import datetime
import hashlib
import hmac
import http.cookiejar
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BINARY = os.environ.get("BLUEPRINT_TEST_BINARY", "/tmp/blueprint-v2-target/debug/blueprint-backend")
CREDENTIAL_FILE = os.environ.get("BLUEPRINT_TEST_S3_CREDENTIAL_FILE", "/tmp/blueprint-v2-deploy-check/secrets/s3.json")
S3 = os.environ.get("BLUEPRINT_TEST_S3_ENDPOINT", "http://127.0.0.1:18333").rstrip("/")
BASE = "http://127.0.0.1:18789"
assert urllib.parse.urlsplit(S3).hostname in {"127.0.0.1", "localhost", "::1"}, "Disposable loopback S3 only"
credential = json.loads(Path(CREDENTIAL_FILE).read_text())["identities"][0]["credentials"][0]
ACCESS, SECRET = credential["accessKey"], credential["secretKey"]
DB = "blueprint_content_" + uuid.uuid4().hex[:16]
BUCKET = "blueprint-rust-" + uuid.uuid4().hex[:16]
DIR = Path(tempfile.mkdtemp(prefix="blueprint-rust-s3-"))
PASSWORD = secrets.token_urlsafe(28)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def request(path, method="GET", data=None, headers=None, expected=200):
    h = {"X-Blueprint-Request": "1"}
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(BASE + "/api/v1" + path, method=method, data=data, headers=h)
    try:
        with opener.open(req, timeout=45) as response:
            status, body, mime = response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        status, body, mime = error.code, error.read(), error.headers.get("Content-Type", "")
    assert status == expected, (method, path, status, body[:300])
    return json.loads(body) if "application/json" in mime else body


def upload(content, resource=None, version=None):
    boundary = "RustS3" + uuid.uuid4().hex
    body = bytearray()
    if version is not None:
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="expected_version"\r\n\r\n{version}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="rust-s3.txt"\r\nContent-Type: text/plain\r\n\r\n'.encode())
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return request(f"/files/{resource}/versions" if resource else "/files/upload", "POST", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def s3(method, key, query):
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    path = "/" + BUCKET + "/" + key
    canonical_query = "&".join(urllib.parse.quote(k, safe="-_.~") + "=" + urllib.parse.quote(v, safe="-_.~") for k, v in sorted(query.items()))
    host = urllib.parse.urlsplit(S3).netloc
    digest = hashlib.sha256(b"").hexdigest()
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{digest}\nx-amz-date:{timestamp}\n"
    names = "host;x-amz-content-sha256;x-amz-date"
    canonical = f"{method}\n{path}\n{canonical_query}\n{canonical_headers}\n{names}\n{digest}"
    scope = f"{day}/us-east-1/s3/aws4_request"
    to_sign = f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n" + hashlib.sha256(canonical.encode()).hexdigest()
    signing = ("AWS4" + SECRET).encode()
    for part in [day, "us-east-1", "s3", "aws4_request"]:
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = f"AWS4-HMAC-SHA256 Credential={ACCESS}/{scope}, SignedHeaders={names}, Signature={signature}"
    req = urllib.request.Request(S3 + path + "?" + canonical_query, method=method, headers={"Host": host, "x-amz-content-sha256": digest, "x-amz-date": timestamp, "Authorization": authorization})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


env = os.environ.copy()
for key in [k for k in env if k.startswith("BLUEPRINT_")]:
    env.pop(key)
env.update(BLUEPRINT_DATABASE_URL=f"postgresql://blueprint_v2@127.0.0.1:55433/{DB}", BLUEPRINT_DATA_DIR=str(DIR / "data"), BLUEPRINT_BIND="127.0.0.1:18789", BLUEPRINT_PUBLIC_URL=BASE, BLUEPRINT_ADMIN_PASSWORD=PASSWORD, BLUEPRINT_S3_ENDPOINT=S3, BLUEPRINT_S3_BUCKET=BUCKET, BLUEPRINT_S3_REGION="us-east-1", BLUEPRINT_S3_ACCESS_KEY=ACCESS, BLUEPRINT_S3_SECRET_KEY=SECRET, BLUEPRINT_S3_REQUIRE_OBJECT_LOCK="1")
server = None
worker = None
subprocess.run(["docker", "exec", "blueprint-v2-postgres", "createdb", "-U", "blueprint_v2", DB], check=True, stdout=subprocess.DEVNULL)
try:
    initialized = subprocess.run([BINARY, "init", "--company", "Rust S3 acceptance", "--email", "s3-owner@example.test"], env=env, capture_output=True)
    assert initialized.returncode == 0, "Disposable app initialization failed"
    log = open(DIR / "server.log", "wb")
    os.chmod(DIR / "server.log", 0o600)
    server = subprocess.Popen([BINARY, "server"], env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + "/health", timeout=1) as response:
                if response.status == 200:
                    break
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    else:
        raise AssertionError("Disposable Rust HTTP server failed to start")
    request("/login", "POST", {"email": "s3-owner@example.test", "password": PASSWORD})
    first = upload("immutable Seaweed 藍圖\n".encode())
    fid, original = first["id"], "immutable Seaweed 藍圖\n".encode()
    assert request(f"/files/{fid}/download") == original
    assert request(f"/files/{fid}/download", headers={"Range": "bytes=1-5"}, expected=206) == original[1:6]
    v = first["data"]["versions"][0]
    assert v["s3_version_id"], "Rust upload must persist real provider version ID"
    assert s3("GET", v["storage_key"], {"versionId": v["s3_version_id"]}) == (200, original)
    changed = upload(b"second immutable version", fid, first["version"])
    assert request(f"/files/{fid}/versions/1/download") == original
    assert request(f"/files/{fid}/download") == b"second immutable version"
    copied = request(f"/files/{fid}/copy", "POST", {"name": "shared physical version"})
    current = request("/resource/" + fid)
    held = request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "legal_hold": True})
    v2 = held["data"]["versions"][1]
    status, xml = s3("GET", v2["storage_key"], {"legal-hold": "", "versionId": v2["s3_version_id"]})
    assert status == 200 and b"ON" in xml, "Application must apply actual object-version legal hold"
    copied = request("/resource/" + copied["id"])
    request(f"/files/{copied['id']}/governance", "POST", {"expected_version": copied["version"], "legal_hold": False})
    status, xml = s3("GET", v2["storage_key"], {"legal-hold": "", "versionId": v2["s3_version_id"]})
    assert status == 200 and b"ON" in xml, "Releasing a copy must preserve the original reference's hold"
    assert s3("DELETE", v2["storage_key"], {"versionId": v2["s3_version_id"]})[0] == 403, "Storage must reject held version deletion"
    current = request("/resource/" + fid)
    request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "legal_hold": False})
    status, xml = s3("GET", v2["storage_key"], {"legal-hold": "", "versionId": v2["s3_version_id"]})
    assert status == 200 and b"OFF" in xml
    current = request("/resource/" + fid)
    retain = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
    request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "retain_until": retain})
    status, xml = s3("GET", v2["storage_key"], {"retention": "", "versionId": v2["s3_version_id"]})
    assert status == 200 and b"COMPLIANCE" in xml
    assert s3("DELETE", v2["storage_key"], {"versionId": v2["s3_version_id"]})[0] == 403
    print("PASS real Rust S3: Object Lock bucket creation, immutable versions, range reads, shared-reference hold union/release and compliance retention")
    # Run the broader content contract against this isolated real S3 deployment.
    acceptance_env=os.environ.copy()
    acceptance_env.update(BLUEPRINT_TEST_BASE=BASE,BLUEPRINT_TEST_EMAIL="s3-owner@example.test",BLUEPRINT_TEST_PASSWORD=PASSWORD)
    subprocess.run([sys.executable,str(Path(__file__).with_name("content_acceptance.py"))],env=acceptance_env,check=True)
    # Exercise real physical collection with a reference held by an ordinary copy.
    gc_file=upload(("GC-"+uuid.uuid4().hex).encode())
    gc_copy=request(f"/files/{gc_file['id']}/copy","POST",{"name":"GC shared reference"})
    gc_version=gc_file["data"]["versions"][0]
    def psql(command):
        subprocess.run(["docker","exec","blueprint-v2-postgres","psql","-U","blueprint_v2","-d",DB,"-v","ON_ERROR_STOP=1","-c",command],check=True,stdout=subprocess.DEVNULL)
    def purge(resource):
        current=request("/resource/"+resource)
        trashed=request(f"/resource/{resource}?expected_version={current['version']}","DELETE")
        request(f"/resource/{resource}/purge?expected_version={trashed['version']}","DELETE")
    def collect():
        psql("UPDATE blob_upload_pins SET expires_at=now()-interval '1 minute'; UPDATE blob_gc SET not_before=now()-interval '1 minute' WHERE status<>'removed'")
        job=request("/content/gc","POST",{})
        for _ in range(200):
            status=request("/jobs/"+job["id"])
            if status["status"]=="completed":
                return status["result"]
            if status["status"]=="failed":
                raise AssertionError(("GC job failed",status.get("error")))
            time.sleep(0.1)
        raise AssertionError("GC job did not complete")
    purge(gc_file["id"])
    worker=subprocess.Popen([BINARY,"worker"],env=env,stdout=log,stderr=subprocess.STDOUT)
    first_gc=collect()
    assert first_gc["referenced"]>=1,first_gc
    assert s3("GET",gc_version["storage_key"],{"versionId":gc_version["s3_version_id"]})[0]==200
    assert request(f"/files/{gc_copy['id']}/download")
    purge(gc_copy["id"])
    final_gc=collect()
    assert final_gc["removed"]>=1,final_gc
    assert s3("GET",gc_version["storage_key"],{"versionId":gc_version["s3_version_id"]})[0]==404
    print("PASS real deferred S3 GC: live copy/history protects bytes, final reference removal deletes exact object version")

finally:
    if worker:
        worker.terminate()
        try:
            worker.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
    if server:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
    subprocess.run(["docker", "exec", "blueprint-v2-postgres", "dropdb", "-U", "blueprint_v2", "--force", DB], check=True, stdout=subprocess.DEVNULL)
