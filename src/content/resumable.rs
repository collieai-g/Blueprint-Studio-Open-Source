//! Durable offset uploads. The session row becomes the final file row atomically,
//! so repeating finalization cannot create a second file.
use super::*;
use axum::{body::Bytes, extract::DefaultBodyLimit, routing::patch};
use tokio::io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt};
const CHUNK: usize = 8 * 1024 * 1024;
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/uploads", post(initialize))
        .route(
            "/uploads/{id}",
            get(status)
                .patch(append)
                .delete(abort)
                .layer(DefaultBodyLimit::max(CHUNK)),
        )
        .route(
            "/uploads/{id}/chunk",
            patch(append).layer(DefaultBodyLimit::max(CHUNK)),
        )
        .route("/uploads/{id}/complete", post(complete))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Initialize {
    name: String,
    mime: Option<String>,
    size: u64,
    parent_id: Option<Uuid>,
    visibility: Option<String>,
    sha256: Option<String>,
}
fn path(state: &AppState, key: &str) -> ApiResult<std::path::PathBuf> {
    let id =
        Uuid::parse_str(key).map_err(|_| ApiError::internal("Invalid upload session reference"))?;
    Ok(state.config.data_dir.join("resumable").join(id.to_string()))
}
fn view(r: &Resource) -> Value {
    if r.kind == "file" {
        json!({"id":r.id,"name":r.name,"status":"completed","offset":r.data["size"],"size":r.data["size"],"resource_id":r.id,"max_chunk_bytes":CHUNK})
    } else {
        json!({"id":r.id,"name":r.name,"status":r.data["status"],"offset":r.data["offset"],"size":r.data["size"],"expires_at":r.data["expires_at"],"max_chunk_bytes":CHUNK})
    }
}
async fn authorized(state: &AppState, actor: &Actor, id: Uuid) -> ApiResult<Resource> {
    crate::core::module_enabled(state, "files").await?;
    actor.require_scope("content:write")?;
    let r = repo::raw_get(&state.pool, id).await?;
    if !matches!(r.kind.as_str(), "upload_session" | "file")
        || r.deleted_at.is_some()
        || (r.kind == "file"
            && r.data.get("completed_upload").and_then(Value::as_bool) != Some(true))
    {
        return Err(ApiError::not_found("Upload session not found"));
    }
    if r.owner_id != actor.id && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Upload session belongs to another member",
        ));
    }
    writable_parent(state, actor, r.parent_id).await?;
    if r.kind == "upload_session" && !policy::future_date(r.data.get("expires_at")) {
        return Err(ApiError::conflict(
            "Upload session expired; start a new upload",
        ));
    }
    Ok(r)
}
async fn initialize(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<Initialize>,
) -> ApiResult<Json<Value>> {
    crate::core::module_enabled(&state, "files").await?;
    let parent = writable_parent(&state, &actor, input.parent_id).await?;
    let name = clean_name(&input.name)?;
    if input.size > storage::limit() {
        return Err(ApiError::bad_request(
            "File exceeds configured upload limit",
        ));
    }
    if input
        .sha256
        .as_ref()
        .is_some_and(|s| !storage::valid_key(s))
    {
        return Err(ApiError::bad_request("Invalid expected SHA256"));
    }
    if input
        .visibility
        .as_ref()
        .is_some_and(|s| !matches!(s.as_str(), "private" | "company"))
    {
        return Err(ApiError::bad_request("Invalid visibility"));
    }
    let key = Uuid::new_v4().to_string();
    let dir = state.config.data_dir.join("resumable");
    tokio::fs::create_dir_all(&dir).await.map_err(err)?;
    let file_path = path(&state, &key)?;
    let f = tokio::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&file_path)
        .await
        .map_err(err)?;
    f.sync_all().await.map_err(err)?;
    drop(f);
    let result=repo::create(&state.pool,&processing::writer_metadata_actor(&actor),NewResource{kind:"upload_session".into(),name,parent_id:input.parent_id,visibility:Some(parent.as_ref().map(|p|p.visibility.clone()).or(input.visibility).unwrap_or("private".into())),acl:parent.map(|p|p.acl),data:json!({"upload_key":key,"mime":safe_mime(input.mime.as_deref().unwrap_or("application/octet-stream")),"size":input.size,"offset":0,"status":"uploading","expected_sha256":input.sha256,"expires_at":Utc::now()+Duration::hours(24)})}).await;
    match result {
        Ok(r) => Ok(Json(view(&r))),
        Err(e) => {
            let _ = tokio::fs::remove_file(file_path).await;
            Err(e)
        }
    }
}
async fn status(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let r = authorized(&state, &actor, id).await?;
    Ok(Json(view(&r)))
}
async fn persist(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    actor: &Actor,
    r: &Resource,
    data: Value,
    kind: &str,
    action: &str,
) -> ApiResult<Resource> {
    let updated=sqlx::query_as::<_,Resource>("UPDATE resources SET kind=$2,data=$3,search_document=$4,version=version+1,updated_at=now() WHERE id=$1 AND version=$5 AND deleted_at IS NULL RETURNING *").bind(r.id).bind(kind).bind(&data).bind(format!("{} {}",r.name,data)).bind(r.version).fetch_optional(&mut **tx).await.map_err(err)?.ok_or_else(||ApiError::conflict("Upload session changed; reload status"))?;
    sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,$2,$3,$4,$5)").bind(updated.id).bind(updated.version).bind(actor.id).bind(serde_json::to_value(&updated).map_err(err)?).bind(action).execute(&mut **tx).await.map_err(err)?;
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,$2,$3,$4)")
        .bind(actor.id)
        .bind(action)
        .bind(updated.id)
        .bind(json!({"offset":data["offset"],"version":updated.version}))
        .execute(&mut **tx)
        .await
        .map_err(err)?;
    Ok(updated)
}
async fn append(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
    bytes: Bytes,
) -> ApiResult<Json<Value>> {
    let _ = authorized(&state, &actor, id).await?;
    if bytes.is_empty() || bytes.len() > CHUNK {
        return Err(ApiError::bad_request(
            "Upload chunk must contain 1–8388608 bytes",
        ));
    }
    let offset = headers
        .get("upload-offset")
        .and_then(|h| h.to_str().ok())
        .and_then(|s| s.parse::<u64>().ok())
        .ok_or_else(|| ApiError::bad_request("Upload-Offset header is required"))?;
    if let Some(checksum) = headers.get("upload-checksum").and_then(|h| h.to_str().ok()) {
        use base64::Engine;
        let expected = checksum
            .strip_prefix("sha256 ")
            .and_then(|s| base64::engine::general_purpose::STANDARD.decode(s).ok())
            .ok_or_else(|| ApiError::bad_request("Upload-Checksum must use sha256 base64"))?;
        if expected.as_slice() != Sha256::digest(&bytes).as_slice() {
            return Err(ApiError::bad_request(
                "Upload chunk checksum does not match",
            ));
        }
    }
    let mut tx = state.pool.begin().await.map_err(err)?;
    let r = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
    )
    .bind(id)
    .fetch_one(&mut *tx)
    .await
    .map_err(err)?;
    if r.kind != "upload_session"
        || r.data.get("status").and_then(Value::as_str) != Some("uploading")
    {
        return Err(ApiError::conflict("Upload does not accept more chunks"));
    }
    let received = r.data["offset"]
        .as_u64()
        .ok_or_else(|| ApiError::internal("Invalid upload offset"))?;
    let total = r.data["size"]
        .as_u64()
        .ok_or_else(|| ApiError::internal("Invalid upload size"))?;
    if received != offset {
        return Err(ApiError::conflict(format!(
            "Expected Upload-Offset: {received}"
        )));
    }
    if offset + bytes.len() as u64 > total {
        return Err(ApiError::bad_request("Chunk exceeds declared file size"));
    }
    let file_path = path(
        &state,
        r.data["upload_key"]
            .as_str()
            .ok_or_else(|| ApiError::internal("Upload staging reference missing"))?,
    )?;
    let mut f = tokio::fs::OpenOptions::new()
        .write(true)
        .open(file_path)
        .await
        .map_err(err)?;
    let actual = f.metadata().await.map_err(err)?.len();
    if actual < received {
        return Err(ApiError::conflict(
            "Staged upload data is incomplete; start a new session",
        ));
    }
    if actual > received {
        f.set_len(received).await.map_err(err)?;
    }
    f.seek(std::io::SeekFrom::Start(received))
        .await
        .map_err(err)?;
    f.write_all(&bytes).await.map_err(err)?;
    f.sync_data().await.map_err(err)?;
    let mut data = r.data.clone();
    data["offset"] = json!(offset + bytes.len() as u64);
    let updated = persist(&mut tx, &actor, &r, data, "upload_session", "upload.chunk").await?;
    tx.commit().await.map_err(err)?;
    Ok(Json(view(&updated)))
}
async fn complete(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Resource>> {
    let r = authorized(&state, &actor, id).await?;
    if r.kind == "file" {
        queue(&state, &actor, &r).await?;
        return Ok(Json(safe_completed(r)));
    }
    let mut tx = state.pool.begin().await.map_err(err)?;
    let mut r = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
    )
    .bind(id)
    .fetch_one(&mut *tx)
    .await
    .map_err(err)?;
    if r.kind == "file" {
        queue(&state, &actor, &r).await?;
        return Ok(Json(safe_completed(r)));
    }
    let total = r.data["size"]
        .as_u64()
        .ok_or_else(|| ApiError::internal("Invalid upload size"))?;
    if r.data["offset"].as_u64() != Some(total) {
        return Err(ApiError::conflict("Upload is incomplete"));
    }
    let file_path = path(
        &state,
        r.data["upload_key"]
            .as_str()
            .ok_or_else(|| ApiError::internal("Upload staging reference missing"))?,
    )?;
    let hash = if let Some(hash) = r.data.get("final_hash").and_then(Value::as_str) {
        hash.to_owned()
    } else {
        let mut f = tokio::fs::File::open(&file_path).await.map_err(err)?;
        if f.metadata().await.map_err(err)?.len() != total {
            return Err(ApiError::conflict(
                "Staged file size does not match declared size",
            ));
        }
        let mut hash = Sha256::new();
        let mut buf = vec![0u8; 65536];
        loop {
            let n = f.read(&mut buf).await.map_err(err)?;
            if n == 0 {
                break;
            }
            hash.update(&buf[..n]);
        }
        let key = hex::encode(hash.finalize());
        if r.data
            .get("expected_sha256")
            .and_then(Value::as_str)
            .is_some_and(|expected| !expected.eq_ignore_ascii_case(&key))
        {
            return Err(ApiError::bad_request(
                "Completed file SHA256 does not match",
            ));
        }
        let mut data = r.data.clone();
        data["final_hash"] = json!(key);
        data["status"] = json!("finalizing");
        r = persist(
            &mut tx,
            &actor,
            &r,
            data,
            "upload_session",
            "upload.finalizing",
        )
        .await?;
        key
    };
    let mime = r.data["mime"]
        .as_str()
        .unwrap_or("application/octet-stream")
        .to_owned();
    tx.commit().await.map_err(err)?;
    let s3_version = if tokio::fs::try_exists(&file_path).await.map_err(err)? {
        storage::commit(&state, &file_path, &hash, total, &mime).await?
    } else {
        let (size, version) = storage::receipt(&state, &hash).await?;
        if size != total {
            return Err(ApiError::conflict("Finalized object size mismatch"));
        }
        version
    };
    let actor = auth::reload_actor_constraints(&state.pool, &actor).await?;
    let _ = authorized(&state, &actor, id).await?;
    let mut tx = state.pool.begin().await.map_err(err)?;
    let latest = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE id=$1 AND deleted_at IS NULL FOR UPDATE",
    )
    .bind(id)
    .fetch_one(&mut *tx)
    .await
    .map_err(err)?;
    if latest.kind == "file" {
        return Ok(Json(safe_completed(latest)));
    }
    let version = fresh_version(1, &hash, &mime, total, s3_version);
    let data = json!({"mime":mime,"size":total,"sha256":hash,"storage_key":hash,"current_version":1,"versions":[version],"scan":{"status":"pending"},"processing":{"status":"queued"},"classification":"internal","completed_upload":true});
    let file = persist(&mut tx, &actor, &latest, data, "file", "upload.completed").await?;
    tx.commit().await.map_err(err)?;
    queue(&state, &actor, &file).await?;
    if let Err(error) = crate::workflows::emit_event(&state, "file.created", &file).await {
        tracing::warn!(error=%error,"Upload workflow event scheduling failed");
    }
    Ok(Json(file))
}
async fn abort(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let r = authorized(&state, &actor, id).await?;
    if r.kind == "file" {
        return Err(ApiError::conflict(
            "Upload is already completed; use file deletion",
        ));
    }
    let key = r.data["upload_key"]
        .as_str()
        .ok_or_else(|| ApiError::internal("Upload staging reference missing"))?;
    let mut tx = state.pool.begin().await.map_err(err)?;
    let r=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE id=$1 AND kind='upload_session' AND deleted_at IS NULL FOR UPDATE").bind(id).fetch_optional(&mut *tx).await.map_err(err)?.ok_or_else(||ApiError::conflict("Upload already changed"))?;
    let mut data = r.data.clone();
    data["status"] = json!("cancelled");
    let updated = persist(
        &mut tx,
        &actor,
        &r,
        data,
        "upload_session",
        "upload.cancelled",
    )
    .await?;
    sqlx::query("UPDATE resources SET deleted_at=now() WHERE id=$1")
        .bind(updated.id)
        .execute(&mut *tx)
        .await
        .map_err(err)?;
    tx.commit().await.map_err(err)?;
    let _ = tokio::fs::remove_file(path(&state, key)?).await;
    Ok(Json(json!({"cancelled":true})))
}

fn safe_completed(mut r: Resource) -> Resource {
    if let Some(data) = r.data.as_object_mut() {
        data.remove("extracted_text");
        data.remove("preview");
    }
    r
}

/// Maintenance only visits expired upload-session rows. Completion changes the
/// row kind to file, so immutable originals are outside this operation's scope.
pub(super) async fn cleanup_expired(state: &AppState) -> ApiResult<u64> {
    let mut tx = state.pool.begin().await.map_err(err)?;
    let rows = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE kind='upload_session' AND deleted_at IS NULL AND (data->>'status'='uploading' OR (data->>'status'='finalizing' AND updated_at<now()-interval '5 minutes')) AND (data->>'expires_at')::timestamptz<=now() ORDER BY data->>'expires_at' FOR UPDATE SKIP LOCKED LIMIT 100",
    )
    .fetch_all(&mut *tx)
    .await
    .map_err(err)?;
    let mut cleaned = 0;
    for r in rows {
        let key = r
            .data
            .get("upload_key")
            .and_then(Value::as_str)
            .ok_or_else(|| ApiError::internal("Expired upload has no staging reference"))?;
        // path() accepts only UUID keys beneath the configured resumable directory.
        let staging = path(state, key)?;
        match tokio::fs::remove_file(staging).await {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(err(error)),
        }
        let mut data = r.data.clone();
        data["status"] = json!("expired");
        data["expired_at"] = json!(Utc::now());
        data["expired_by"] = json!("system");
        let updated = sqlx::query_as::<_, Resource>(
            "UPDATE resources SET data=$2,search_document=$3,version=version+1,updated_at=now() WHERE id=$1 AND kind='upload_session' AND version=$4 RETURNING *",
        ).bind(r.id).bind(&data).bind(format!("{} {}",r.name,data)).bind(r.version)
            .fetch_one(&mut *tx).await.map_err(err)?;
        // The historical schema requires a user reference. Keep ownership in the
        // snapshot, explicitly mark system action, and audit with NULL principal.
        sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,$2,$3,$4,'system.upload_expired')")
            .bind(r.id).bind(updated.version).bind(r.owner_id)
            .bind(serde_json::to_value(&updated).map_err(err)?)
            .execute(&mut *tx).await.map_err(err)?;
        sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES(NULL,'upload.expired',$1,$2)")
            .bind(r.id).bind(json!({"system":true,"owner_id":r.owner_id,"received_bytes":data["offset"]}))
            .execute(&mut *tx).await.map_err(err)?;
        cleaned += 1;
    }
    tx.commit().await.map_err(err)?;
    Ok(cleaned)
}
