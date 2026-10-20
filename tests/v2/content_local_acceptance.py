#!/usr/bin/env python3
"""Disposable Rust/PostgreSQL local-blob contract, job grant and physical GC test."""
import json
import os
import runpy
import secrets
import subprocess
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path

BINARY=os.environ.get("BLUEPRINT_TEST_BINARY","/tmp/blueprint-v2-target/debug/blueprint-backend")
DB="blueprint_local_"+uuid.uuid4().hex[:16]
DIR=Path(tempfile.mkdtemp(prefix="blueprint-local-content-"))
PASSWORD=secrets.token_urlsafe(28)
BASE="http://127.0.0.1:18790"
env={k:v for k,v in os.environ.items() if not k.startswith("BLUEPRINT_")}
env.update(BLUEPRINT_DATABASE_URL=f"postgresql://blueprint_v2@127.0.0.1:55433/{DB}",BLUEPRINT_DATA_DIR=str(DIR/"data"),BLUEPRINT_BIND="127.0.0.1:18790",BLUEPRINT_PUBLIC_URL=BASE,BLUEPRINT_ADMIN_PASSWORD=PASSWORD)
server=worker=None

def sql(command,read=False):
    result=subprocess.run(["docker","exec","blueprint-v2-postgres","psql","-U","blueprint_v2","-d",DB,"-v","ON_ERROR_STOP=1","-tAc",command],check=True,capture_output=True,text=True)
    return result.stdout.strip() if read else None

subprocess.run(["docker","exec","blueprint-v2-postgres","createdb","-U","blueprint_v2",DB],check=True,stdout=subprocess.DEVNULL)
try:
    initialized=subprocess.run([BINARY,"init","--company","Local content acceptance","--email","local-owner@example.test"],env=env,capture_output=True)
    assert initialized.returncode==0,"Disposable installation initialization failed"
    log=open(DIR/"server.log","wb");os.chmod(DIR/"server.log",0o600)
    server=subprocess.Popen([BINARY,"server"],env=env,stdout=log,stderr=subprocess.STDOUT)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(100):
        try:
            with opener.open(BASE+"/health",timeout=1) as response:
                if response.status==200:break
        except OSError:time.sleep(0.1)
    else:raise AssertionError("Disposable local app failed to start")
    os.environ.update(BLUEPRINT_TEST_BASE=BASE,BLUEPRINT_TEST_EMAIL="local-owner@example.test",BLUEPRINT_TEST_PASSWORD=PASSWORD)
    # The imported fixture creates its own users/files and never prints secrets.
    fixture=runpy.run_path(str(Path(__file__).with_name("content_acceptance.py")))
    owner=fixture["owner"]
    original=owner.upload(("local-GC-"+uuid.uuid4().hex).encode(),"gc.txt")
    copied=owner.request(f"/files/{original['id']}/copy","POST",{"name":"GC copy"})[0]
    key=original["data"]["storage_key"]
    blob=DIR/"data"/"blobs"/key[:2]/key
    assert blob.exists()
    def purge(resource):
        for _ in range(8):
            current=owner.request("/resource/"+resource)[0]
            try:
                trashed=owner.request(f"/resource/{resource}?expected_version={current['version']}","DELETE")[0]
                owner.request(f"/resource/{resource}/purge?expected_version={trashed['version']}","DELETE")
                return
            except AssertionError as error:
                if "409" not in str(error):raise
        raise AssertionError("Continuous metadata changes prevented test purge")
    purge(original["id"])
    assert blob.exists(),"Grace period must preserve original bytes"
    expired=owner.request("/uploads","POST",{"name":"expired-maintenance.txt","size":9,"mime":"text/plain"})[0]
    active=owner.request("/uploads","POST",{"name":"active-maintenance.txt","size":9,"mime":"text/plain"})[0]
    owner.request("/uploads/"+expired["id"],"PATCH",b"old",{"Upload-Offset":"0"})
    owner.request("/uploads/"+active["id"],"PATCH",b"new",{"Upload-Offset":"0"})
    expired_key=sql("SELECT data->>'upload_key' FROM resources WHERE id='"+expired["id"]+"'",True)
    active_key=sql("SELECT data->>'upload_key' FROM resources WHERE id='"+active["id"]+"'",True)
    expired_path=DIR/"data"/"resumable"/expired_key
    active_path=DIR/"data"/"resumable"/active_key
    assert expired_path.read_bytes()==b"old" and active_path.read_bytes()==b"new"
    sql("UPDATE resources SET data=jsonb_set(data,'{expires_at}',to_jsonb(now()-interval '1 minute')) WHERE id='"+expired["id"]+"'")
    fresh_finalizing=owner.request("/uploads","POST",{"name":"inflight-finalizing.txt","size":3})[0]
    stale_finalizing=owner.request("/uploads","POST",{"name":"abandoned-finalizing.txt","size":3})[0]
    for session in [fresh_finalizing,stale_finalizing]:
        owner.request("/uploads/"+session["id"],"PATCH",b"end",{"Upload-Offset":"0"})
        sql("UPDATE resources SET data=jsonb_set(jsonb_set(data,'{status}','\"finalizing\"'),'{expires_at}',to_jsonb(now()-interval '1 minute')) WHERE id='"+session["id"]+"'")
    fresh_key=sql("SELECT data->>'upload_key' FROM resources WHERE id='"+fresh_finalizing["id"]+"'",True)
    stale_key=sql("SELECT data->>'upload_key' FROM resources WHERE id='"+stale_finalizing["id"]+"'",True)
    sql("UPDATE resources SET updated_at=now()-interval '6 minutes' WHERE id='"+stale_finalizing["id"]+"'")
    worker=subprocess.Popen([BINARY,"worker"],env=env,stdout=log,stderr=subprocess.STDOUT)
    for _ in range(400):
        if sql("SELECT data->>'status' FROM resources WHERE id='"+expired["id"]+"'",True)=="expired":break
        time.sleep(0.1)
    else:raise AssertionError("Worker did not clean expired resumable staging")
    assert not expired_path.exists(),"Expired staging bytes must be removed"
    assert active_path.read_bytes()==b"new","Maintenance must preserve active upload bytes"
    assert (DIR/"data"/"resumable"/fresh_key).read_bytes()==b"end","Recent finalizing transfer must retain staging during its5minute grace"
    assert not (DIR/"data"/"resumable"/stale_key).exists(),"Abandoned finalizing staging must be removed after grace"
    assert sql("SELECT data->>'status' FROM resources WHERE id='"+stale_finalizing["id"]+"'",True)=="expired"
    assert owner.request("/uploads/"+active["id"])[0]["offset"]==3
    assert sql("SELECT COUNT(*) FROM audit_log WHERE resource_id='"+expired["id"]+"' AND action='upload.expired' AND actor_id IS NULL",True)=="1"
    assert sql("SELECT COUNT(*) FROM resource_history WHERE resource_id='"+expired["id"]+"' AND action='system.upload_expired'",True)=="1"
    print("PASS actual worker maintenance removes expired resumable staging, preserves active uploads and records system audit/history")

    def collect():
        sql("UPDATE blob_upload_pins SET expires_at=now()-interval '1 minute'; UPDATE blob_gc SET not_before=now()-interval '1 minute' WHERE status<>'removed'")
        job=owner.request("/content/gc","POST",{})[0]
        for _ in range(300):
            current=owner.request("/jobs/"+job["id"])[0]
            if current["status"]=="completed":return current["result"]
            if current["status"]=="failed":raise AssertionError(("GC job failed",current.get("error")))
            time.sleep(0.1)
        raise AssertionError("GC job did not finish")
    result=collect()
    assert result["referenced"]>=1,result
    assert blob.exists(),"A copied current/history reference must retain physical bytes"
    assert owner.request(f"/files/{copied['id']}/download")[0]
    purge(copied["id"])
    result=collect()
    assert result["removed"]>=1,result
    assert not blob.exists(),"Last-reference purge must physically remove the local blob"
    print("PASS actual local GC: 24h grace, current/history copy reference protection and physical final deletion")
    for _ in range(200):
        rows=sql("SELECT status FROM jobs WHERE kind='content.process' AND payload->>'file_id' IN (SELECT id::text FROM resources WHERE name IN ('write-only.txt','write-only-resume.txt')) ORDER BY created_at",True).splitlines()
        if len(rows)>=2 and all(value=="completed" for value in rows):break
        time.sleep(0.1)
    else:raise AssertionError(("Write-only processing grants did not complete",rows))
    print("PASS actual worker signed-grant processing for write-only parent-scoped PAT uploads")
finally:
    for process in [worker,server]:
        if process:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    subprocess.run(["docker","exec","blueprint-v2-postgres","dropdb","-U","blueprint_v2","--force",DB],check=True,stdout=subprocess.DEVNULL)
