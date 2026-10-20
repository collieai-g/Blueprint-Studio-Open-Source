#!/usr/bin/env python3
"""Content HTTP acceptance against an explicitly configured disposable v2 server.

BLUEPRINT_TEST_BASE defaults to loopback. Login values are read from environment,
never printed. This creates test users/content and requires an owner account.
"""
import http.cookiejar
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("BLUEPRINT_TEST_BASE", "http://127.0.0.1:8788").rstrip("/")
if urllib.parse.urlsplit(BASE).hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise SystemExit("Refusing to mutate a non-loopback server; run against a disposable local deployment")
EMAIL = os.environ.get("BLUEPRINT_TEST_EMAIL")
PASSWORD = os.environ.get("BLUEPRINT_TEST_PASSWORD")
if not EMAIL or not PASSWORD:
    raise SystemExit("Set BLUEPRINT_TEST_EMAIL and BLUEPRINT_TEST_PASSWORD for a disposable owner")
PREFIX = "content-acceptance-" + uuid.uuid4().hex[:8]


class Client:
    def __init__(self):
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(self, path, method="GET", data=None, headers=None, expected=200):
        h = {"X-Blueprint-Request": "1"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        req = urllib.request.Request(BASE + "/api/v1" + path, data=data, method=method, headers=h)
        try:
            with self.opener.open(req, timeout=45) as res:
                status, body, response_headers = res.status, res.read(), res.headers
        except urllib.error.HTTPError as exc:
            status, body, response_headers = exc.code, exc.read(), exc.headers
        if isinstance(expected, int):
            expected = {expected}
        assert status in expected, (method, path, status, body[:500])
        value = json.loads(body) if "application/json" in response_headers.get("Content-Type", "") else body
        return value, response_headers

    def login(self, email, password):
        self.request("/login", "POST", {"email": email, "password": password})

    def upload(self, content, name, parent=None, resource=None, version=None, expected=200, mime="text/plain"):
        boundary = "BlueprintAcceptance" + uuid.uuid4().hex
        fields = []
        if parent:
            fields.append(("parent_id", str(parent)))
        if version is not None:
            fields.append(("expected_version", str(version)))
        body = bytearray()
        for key, value in fields:
            body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
        body.extend(content)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        path = f"/files/{resource}/versions" if resource else "/files/upload"
        return self.request(path, "POST", bytes(body), {"Content-Type": f"multipart/form-data; boundary={boundary}"}, expected)[0]


owner, anon = Client(), Client()
owner.login(EMAIL, PASSWORD)
me = owner.request("/me")[0]
folder = owner.request("/resources", "POST", {"kind": "folder", "name": PREFIX, "visibility": "private", "data": {}}, expected={200, 201})[0]
original = "藍圖工作室\noriginal file\n".encode()
file = owner.upload(original, "公司資料.txt", folder["id"])
fid = file["id"]
read = lambda: owner.request("/resource/" + fid)[0]

body, headers = owner.request(f"/files/{fid}/download")
assert body == original
part, headers = owner.request(f"/files/{fid}/download", headers={"Range": "bytes=2-6"}, expected=206)
assert part == original[2:7] and headers["Content-Range"] == f"bytes 2-6/{len(original)}"
owner.request(f"/files/{fid}/download", headers={"Range": "bytes=999999-"}, expected={400, 416})
assert owner.request(f"/files/{fid}/preview")[0] == original
print("PASS immutable upload, UTF-8 bytes, byte ranges and native preview")

owner.upload(b"stale", "公司資料.txt", resource=fid, version=0, expected=409)
for attempt in range(5):
    current = read()
    try:
        changed = owner.upload(b"new version", "公司資料.txt", resource=fid, version=current["version"])
        break
    except AssertionError as exc:
        if "409" not in str(exc) or attempt == 4:
            raise
assert changed["data"]["current_version"] == 2
assert owner.request(f"/files/{fid}/versions/1/download")[0] == original
assert owner.request(f"/files/{fid}/download")[0] == b"new version"
versions = owner.request(f"/files/{fid}/versions")[0]
assert len(versions["versions"]) == 2
for attempt in range(5):
    current = read()
    try:
        owner.request(f"/files/{fid}/versions/1/restore", "POST", {"expected_version": current["version"]})
        break
    except AssertionError as exc:
        if "409" not in str(exc) or attempt == 4:
            raise
assert owner.request(f"/files/{fid}/download")[0] == original
current = read()
forged = dict(current["data"], storage_key="a" * 64)
owner.request(f"/resource/{fid}", "PATCH", {"expected_version": current["version"], "data": forged}, expected=400)
assert owner.request(f"/files/{fid}/download")[0] == original
print("PASS version history, stale writes, restore and forged storage metadata rejection")

share = owner.request("/shares", "POST", {"resource_id": fid, "access": "link", "password": "acceptance-password", "allow_download": False})[0]
token = share["url"].rsplit("/", 1)[1]
path = "/public/shares/" + token
anon.request(path, expected=401)
anon.request(path, headers={"X-Share-Password": "wrong"}, expected=401)
assert anon.request(path, headers={"X-Share-Password": "acceptance-password"})[0]["resource"]["id"] == fid
anon.request(path + "/download", headers={"X-Share-Password": "acceptance-password"}, expected=403)
owner.request("/shares/" + share["id"], "DELETE")
anon.request(path, headers={"X-Share-Password": "acceptance-password"}, expected=404)
print("PASS share password, download restriction and revocation")

comment = owner.request(f"/files/{fid}/comments", "POST", {"body": "交付前需要覆核"})[0]
assert any(c["id"] == comment["id"] for c in owner.request(f"/files/{fid}/comments")[0])
copy = owner.request(f"/files/{fid}/copy", "POST", {"parent_id": folder["id"], "name": "copy.txt"})[0]
assert owner.request(f"/files/{copy['id']}/download")[0] == original
assert len(copy["data"]["versions"]) == 1
print("PASS persisted comments and current-version copy")

new_email = PREFIX + "@example.invalid"
new_password = "Acceptance-temporary-42!"
user = owner.request("/users", "POST", {"name": "Content acceptance member", "email": new_email, "password": new_password, "role": "member"}, expected={200, 201})[0]
other = Client()
other.login(new_email, new_password)
other.request(f"/files/{fid}/download", expected={403, 404})
current_folder = owner.request("/resource/" + folder["id"])[0]
owner.request("/resource/" + folder["id"], "PATCH", {"expected_version": current_folder["version"], "acl": {"user:" + user["id"]: "editor"}})
current = read()
owner.request(f"/resource/{fid}", "PATCH", {"expected_version": current["version"], "acl": {"user:" + user["id"]: "editor"}})
owner.request(f"/files/{fid}/lock", "POST", {"locked": True, "minutes": 5, "disable_download": True})
other.request(f"/files/{fid}/download", expected={403, 409})
owner.request(f"/files/{fid}/lock", "POST", {"locked": False})
assert other.request(f"/files/{fid}/download")[0] == original
print("PASS direct-route ACL checks, inherited folder access and collaborative locking")

policy = owner.request("/resources", "POST", {"kind": "classification", "name": PREFIX + " restricted", "data": {"active": True, "resource_ids": [fid], "blocked_actions": ["download", "integration"]}}, expected={200, 201})[0]
owner.request(f"/files/{fid}/download", expected=403)
policy_now = owner.request("/resource/" + policy["id"])[0]
owner.request("/resource/" + policy["id"], "PATCH", {"expected_version": policy_now["version"], "data": dict(policy_now["data"], active=False)})
assert owner.request(f"/files/{fid}/download")[0] == original
print("PASS DLP enforcement on real file endpoint")

share = owner.request("/shares", "POST", {"resource_id": fid, "access": "link", "allow_download": True})[0]
token = share["url"].rsplit("/", 1)[1]
assert anon.request("/public/shares/" + token + "/download")[0] == original
current = read()
owner.request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "archived": True, "legal_hold": True})
anon.request("/public/shares/" + token + "/download", expected={403, 404})
current = read()
owner.request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "archived": False})
anon.request("/public/shares/" + token + "/download", expected=404)
current = read()
trashed = owner.request(f"/resource/{fid}?expected_version={current['version']}", "DELETE")[0]
owner.request(f"/resource/{fid}/purge?expected_version={trashed['version']}", "DELETE", expected=403)
owner.request(f"/resource/{fid}/restore", "POST", {"expected_version": trashed["version"]})
current = read()
owner.request(f"/files/{fid}/governance", "POST", {"expected_version": current["version"], "legal_hold": False})
assert owner.request(f"/files/{fid}/download")[0] == original
print("PASS archive revokes links permanently, legal hold blocks purge and restore preserves bytes")

owner.upload(b"bad filename", "../escape.txt", expected=400)
print("PASS upload path traversal rejection")
print("Content acceptance completed using disposable fixtures", PREFIX)

# Durable offsets and finalization are exercised through HTTP, including retries.
import base64
import hashlib
payload = ("resumable 藍圖\n" * 80).encode()
session = owner.request("/uploads", "POST", {"name": "resume.txt", "mime": "text/plain", "parent_id": folder["id"], "size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})[0]
sid = session["id"]
chunk = payload[:37]
checksum = "sha256 " + base64.b64encode(hashlib.sha256(chunk).digest()).decode()
progress = owner.request("/uploads/" + sid, "PATCH", chunk, {"Upload-Offset": "0", "Upload-Checksum": checksum, "Content-Type": "application/octet-stream"})[0]
assert progress["offset"] == 37
owner.request("/uploads/" + sid, "PATCH", chunk, {"Upload-Offset": "0"}, expected=409)
other.request("/uploads/" + sid, expected={403, 404})
owner.request("/uploads/" + sid + "/complete", "POST", {}, expected=409)
assert owner.request("/uploads/" + sid)[0]["offset"] == 37
owner.request("/uploads/" + sid, "PATCH", payload[37:], {"Upload-Offset": "37", "Content-Type": "application/octet-stream"})
completed = owner.request("/uploads/" + sid + "/complete", "POST", {})[0]
again = owner.request("/uploads/" + sid + "/complete", "POST", {})[0]
assert completed["id"] == again["id"] == sid
assert owner.request(f"/files/{sid}/download")[0] == payload
assert owner.request("/uploads/" + sid)[0]["status"] == "completed"
wrong = owner.request("/uploads", "POST", {"name": "checksum.txt", "parent_id": folder["id"], "size": 3, "sha256": "0" * 64})[0]["id"]
owner.request("/uploads/" + wrong, "PATCH", b"bad", {"Upload-Offset": "0"})
owner.request("/uploads/" + wrong + "/complete", "POST", {}, expected=400)
owner.request("/uploads/" + wrong, "DELETE")
owner.request("/uploads/" + wrong, expected={403, 404})
print("PASS durable chunk offsets, interrupted resume, final SHA256, idempotent completion and abort")

nested = owner.request("/resources", "POST", {"kind": "folder", "name": "nested", "parent_id": folder["id"], "data": {}}, expected={200, 201})[0]
nested_file = owner.upload(b"nested bytes", "nested.txt", nested["id"])
folder_link = owner.request("/shares", "POST", {"resource_id": folder["id"], "access": "link", "allow_download": True})[0]
folder_token = folder_link["url"].rsplit("/", 1)[1]
share_base = "/public/shares/" + folder_token
items = anon.request(share_base + "/items?parent_id=" + nested["id"])[0]
assert any(i["id"] == nested_file["id"] for i in items)
assert anon.request(share_base + "/files/" + nested_file["id"] + "/download")[0] == b"nested bytes"
outside = owner.request("/resources", "POST", {"kind": "folder", "name": PREFIX + " outside", "data": {}}, expected={200, 201})[0]
outsider = owner.upload(b"outside bytes", "outside.txt", outside["id"])
anon.request(share_base + "/items?parent_id=" + outside["id"], expected=403)
anon.request(share_base + "/files/" + outsider["id"] + "/download", expected=403)
copied_tree = owner.request(f"/files/{nested['id']}/copy", "POST", {"name": "nested copy", "parent_id": outside["id"]})[0]
all_files = owner.request("/resources/file")[0]
copy_child = next(r for r in all_files if r["parent_id"] == copied_tree["id"])
assert owner.request(f"/files/{copy_child['id']}/download")[0] == b"nested bytes"
owner.request(f"/files/{nested['id']}/copy", "POST", {"parent_id": nested["id"]}, expected=409)
current = owner.request("/resource/" + nested_file["id"])[0]
owner.request(f"/files/{nested_file['id']}/move", "POST", {"parent_id": None, "expected_version": current["version"]})
assert owner.request("/resource/" + nested_file["id"])[0]["parent_id"] is None
anon.request(share_base + "/files/" + nested_file["id"] + "/download", expected=403)
current = owner.request("/resource/" + nested_file["id"])[0]
owner.request(f"/files/{nested_file['id']}/move", "POST", {"parent_id": nested["id"], "expected_version": current["version"]})
owner.request(f"/files/{nested['id']}/move", "POST", {"parent_id": nested["id"], "expected_version": nested["version"]}, expected=409)
print("PASS nested share ancestry, outside-tree rejection, atomic folder copy and move-to-root")

# A link must stop resolving when the file itself enters trash.
file_link = owner.request("/shares", "POST", {"resource_id": outsider["id"], "access": "link", "allow_download": True})[0]
file_path = "/public/shares/" + file_link["url"].rsplit("/", 1)[1]
current = owner.request("/resource/" + outsider["id"])[0]
trashed = owner.request(f"/resource/{outsider['id']}?expected_version={current['version']}", "DELETE")[0]
anon.request(file_path, expected=404)
anon.request(file_path + "/download", expected=404)
owner.request(f"/files/{outsider['id']}/download", expected=404)
owner.request(f"/resource/{outsider['id']}/restore", "POST", {"expected_version": trashed["version"]})
print("PASS trashed source cannot leak through direct or public shared downloads")


write_token = owner.request("/tokens", "POST", {"name": PREFIX + " write only", "scopes": ["content:write"], "resource_ids": [folder["id"]]})[0]
writer = Client()
writer.token = write_token["token"]
write_only = writer.upload(b"write-only processing input", "write-only.txt", folder["id"])
writer.request(f"/files/{write_only['id']}/download", expected={403, 404})
writer.request("/resource/" + write_only["id"], expected={403, 404})
write_session = writer.request("/uploads", "POST", {"name": "write-only-resume.txt", "mime": "text/plain", "size": 3, "parent_id": folder["id"]})[0]
writer.request("/uploads/" + write_session["id"], "PATCH", b"own", {"Upload-Offset": "0"})
writer.request("/uploads/" + write_session["id"] + "/complete", "POST", {})
assert writer.request("/uploads/" + write_session["id"])[0]["status"] == "completed"
writer.request(f"/files/{write_session['id']}/download", expected={403, 404})
print("PASS write-only parent-scoped PAT uploads/finalizes without gaining file read access")

# Sharing grants the owner's ACL access, while policy exceptions belong to the
# real viewer. An anonymous visitor must never inherit the owner's group/admin.
exempt_group = owner.request("/resources", "POST", {"kind": "group", "name": PREFIX + " policy exemption", "data": {}}, expected={200, 201})[0]
original_groups = me.get("groups", [])
owner.request("/users/" + me["id"], "PATCH", {"groups": original_groups + [exempt_group["id"]]})
exemption_policy = owner.request("/resources", "POST", {"kind": "classification", "name": PREFIX + " viewer identity", "data": {"active": True, "resource_ids": [fid], "blocked_actions": ["download", "preview"], "allowed_groups": [exempt_group["id"]]}}, expected={200, 201})[0]
identity_share = owner.request("/shares", "POST", {"resource_id": fid, "access": "link", "allow_download": True})[0]
identity_path = "/public/shares/" + identity_share["url"].rsplit("/", 1)[1]
assert owner.request(f"/files/{fid}/download")[0] == original
assert owner.request(identity_path + "/download")[0] == original
anon.request(identity_path + "/download", expected=403)
anon.request(identity_path + "/preview", expected=403)
other.request(identity_path + "/download", expected=403)
policy_now = owner.request("/resource/" + exemption_policy["id"])[0]
owner.request("/resource/" + exemption_policy["id"], "PATCH", {"expected_version": policy_now["version"], "data": dict(policy_now["data"], active=False)})
assert anon.request(identity_path + "/download")[0] == original
owner.request("/users/" + me["id"], "PATCH", {"groups": original_groups})
print("PASS public sharing applies actual viewer DLP groups/admin identity; anonymous users cannot inherit owner exemptions")
