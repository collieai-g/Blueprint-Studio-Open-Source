//! Content storage and enterprise content policies. Provider results are persisted,
//! never replaced with simulated scan, extraction or preview successes.
mod copying;
mod gc;
mod moving;
mod policy;
mod processing;
mod resumable;
mod storage;
mod watermark;
use crate::{
    Actor, ApiError, ApiResult, AppState, Job, NewResource, Resource, UpdateResource, auth, repo,
};
use argon2::{
    Argon2,
    password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
};
use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, Multipart, Path, Query, State},
    http::HeaderMap,
    response::Response,
    routing::{get, post},
};
use chrono::{DateTime, Duration, Utc};
use futures_util::TryStreamExt;
pub use policy::authorize_action;
use rand::{RngCore, rngs::OsRng};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use uuid::Uuid;

pub fn router() -> Router<AppState> {
    Router::new()
        .merge(resumable::router())
        .merge(gc::router())
        .merge(moving::router())
        .route(
            "/files/upload",
            post(upload).layer(DefaultBodyLimit::max(
                storage::limit()
                    .saturating_add(1024 * 1024)
                    .min(usize::MAX as u64) as usize,
            )),
        )
        .route("/files/{id}/download", get(download))
        .route("/files/{id}/preview", get(preview))
        .route("/files/{id}/preview-policy", get(preview_policy))
        .route(
            "/files/{id}/versions",
            get(versions)
                .post(upload_version)
                .layer(DefaultBodyLimit::max(
                    storage::limit()
                        .saturating_add(1024 * 1024)
                        .min(usize::MAX as u64) as usize,
                )),
        )
        .route(
            "/files/{id}/versions/{number}/download",
            get(download_version),
        )
        .route(
            "/files/{id}/versions/{number}/restore",
            post(restore_version),
        )
        .route("/files/{id}/copy", post(copy_file))
        .route("/files/{id}/lock", post(lock_file))
        .route("/files/{id}/governance", post(governance))
        .route("/files/{id}/process", post(reprocess))
        .route("/files/{id}/comments", get(comments).post(add_comment))
        .route("/shares", get(shares).post(create_share))
        .route("/shares/{id}", axum::routing::delete(revoke_share))
        .route("/content/capabilities", get(capabilities))
}
pub fn public_router() -> Router<AppState> {
    Router::new()
        .route("/shares/{token}", get(public_share))
        .route("/shares/{token}/download", get(public_download))
        .route("/shares/{token}/preview", get(public_preview))
        .route("/shares/{token}/items", get(public_items))
        .route(
            "/shares/{token}/files/{id}/download",
            get(public_child_download),
        )
}
fn err(e: impl std::fmt::Display) -> ApiError {
    tracing::error!(error=%e,"Content operation failed");
    ApiError::internal("Content operation failed")
}
fn safe_mime(m: &str) -> String {
    let m = m.trim().to_ascii_lowercase();
    if m.len() < 160
        && m.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/.-+;=_ ".contains(&b))
    {
        m
    } else {
        "application/octet-stream".into()
    }
}
fn clean_name(n: &str) -> ApiResult<String> {
    let n = n.trim();
    if n.is_empty() || n.len() > 255 || n.contains(['\0', '\r', '\n', '/', '\\']) {
        return Err(ApiError::bad_request(
            "File name must be 1–255 bytes without path separators",
        ));
    }
    Ok(n.to_owned())
}
fn field_uuid(v: Option<&str>) -> ApiResult<Option<Uuid>> {
    v.filter(|s| !s.is_empty())
        .map(|s| {
            s.parse()
                .map_err(|_| ApiError::bad_request("Invalid parent identifier"))
        })
        .transpose()
}
fn current_version(resource: &Resource) -> ApiResult<Value> {
    let number = resource
        .data
        .get("current_version")
        .and_then(Value::as_i64)
        .unwrap_or(1);
    version_metadata(resource, number)
}
fn version_metadata(resource: &Resource, number: i64) -> ApiResult<Value> {
    resource
        .data
        .get("versions")
        .and_then(Value::as_array)
        .and_then(|a| {
            a.iter()
                .find(|v| v.get("number").and_then(Value::as_i64) == Some(number))
        })
        .cloned()
        .ok_or_else(|| ApiError::not_found("File version not found"))
}
fn version_key(v: &Value) -> ApiResult<&str> {
    v.get("storage_key")
        .and_then(Value::as_str)
        .filter(|s| storage::valid_key(s))
        .ok_or_else(|| ApiError::internal("Invalid stored file reference"))
}
async fn file(state: &AppState, actor: &Actor, id: Uuid) -> ApiResult<Resource> {
    let r = repo::get(&state.pool, actor, id).await?;
    if r.deleted_at.is_some() {
        return Err(ApiError::not_found("File is in trash"));
    }
    if r.kind != "file" {
        return Err(ApiError::bad_request("Resource is not a file"));
    }
    Ok(r)
}
async fn writable_parent(
    state: &AppState,
    actor: &Actor,
    parent_id: Option<Uuid>,
) -> ApiResult<Option<Resource>> {
    actor.require_scope("content:write")?;
    if matches!(actor.role.as_str(), "viewer" | "guest") && parent_id.is_none() {
        return Err(ApiError::forbidden(
            "This account cannot create root content",
        ));
    }
    if let Some(id) = parent_id {
        let p = repo::get(&state.pool, &processing::writer_metadata_actor(actor), id).await?;
        if p.kind != "folder" {
            return Err(ApiError::bad_request("Destination must be a folder"));
        }
        if !actor.can_write(&p) {
            return Err(ApiError::forbidden("Destination is read-only"));
        }
        authorize_action(state, actor, &p, "update").await?;
        Ok(Some(p))
    } else {
        Ok(None)
    }
}
struct Uploaded {
    _cleanup: storage::StagingCleanup,
    path: std::path::PathBuf,
    name: String,
    mime: String,
    size: u64,
    hash: String,
    parent_id: Option<Uuid>,
    visibility: Option<String>,
    expected_version: Option<i64>,
}
async fn receive(state: &AppState, mut multipart: Multipart) -> ApiResult<Uploaded> {
    let mut saved = None;
    let mut parent_id = None;
    let mut visibility = None;
    let mut expected_version = None;
    let mut fields = 0;
    while let Some(mut field) = multipart
        .next_field()
        .await
        .map_err(|_| ApiError::bad_request("Invalid multipart upload"))?
    {
        fields += 1;
        if fields > 12 {
            return Err(ApiError::bad_request("Too many upload fields"));
        }
        let key = field.name().unwrap_or("").to_owned();
        if key == "file" {
            if saved.is_some() {
                return Err(ApiError::bad_request("Upload exactly one file per request"));
            }
            let name = clean_name(field.file_name().unwrap_or("untitled"))?;
            let mime = safe_mime(field.content_type().unwrap_or("application/octet-stream"));
            let (path, mut f) = storage::staging(state).await?;
            let cleanup = storage::StagingCleanup(path.clone());
            let mut hash = Sha256::new();
            let mut size = 0u64;
            while let Some(chunk) = field
                .chunk()
                .await
                .map_err(|_| ApiError::bad_request("Upload stream interrupted"))?
            {
                size += chunk.len() as u64;
                if size > storage::limit() {
                    let _ = tokio::fs::remove_file(&path).await;
                    return Err(ApiError::bad_request(
                        "File exceeds configured upload limit",
                    ));
                }
                f.write_all(&chunk).await.map_err(err)?;
                hash.update(&chunk);
            }
            f.flush().await.map_err(err)?;
            drop(f);
            saved = Some((
                path,
                name,
                mime,
                size,
                hex::encode(hash.finalize()),
                cleanup,
            ));
        } else {
            let mut value = Vec::new();
            while let Some(chunk) = field
                .chunk()
                .await
                .map_err(|_| ApiError::bad_request("Invalid upload field"))?
            {
                if value.len() + chunk.len() > 4096 {
                    return Err(ApiError::bad_request("Upload field too long"));
                }
                value.extend_from_slice(&chunk);
            }
            let text = String::from_utf8(value)
                .map_err(|_| ApiError::bad_request("Invalid upload text"))?;
            match key.as_str() {
                "parent_id" => parent_id = field_uuid(Some(&text))?,
                "visibility" => {
                    if !matches!(text.as_str(), "private" | "company") {
                        return Err(ApiError::bad_request("Invalid visibility"));
                    }
                    visibility = Some(text)
                }
                "expected_version" => {
                    expected_version = Some(
                        text.parse()
                            .map_err(|_| ApiError::bad_request("Invalid expected_version"))?,
                    )
                }
                _ => return Err(ApiError::bad_request("Unknown upload field")),
            }
        }
    }
    let (path, name, mime, size, hash, cleanup) =
        saved.ok_or_else(|| ApiError::bad_request("Missing file"))?;
    Ok(Uploaded {
        _cleanup: cleanup,
        path,
        name,
        mime,
        size,
        hash,
        parent_id,
        visibility,
        expected_version,
    })
}
fn fresh_version(
    number: i64,
    key: &str,
    mime: &str,
    size: u64,
    s3_version: Option<String>,
) -> Value {
    json!({"number":number,"storage_key":key,"sha256":key,"size":size,"mime":mime,"s3_version_id":s3_version,"created_at":Utc::now(),"scan":{"status":"pending"}})
}
async fn create_saved(state: &AppState, actor: &Actor, u: Uploaded) -> ApiResult<Resource> {
    let parent = writable_parent(state, actor, u.parent_id).await?;
    let s3_version = storage::commit(state, &u.path, &u.hash, u.size, &u.mime).await?;
    let v = fresh_version(1, &u.hash, &u.mime, u.size, s3_version);
    let data = json!({"mime":u.mime,"size":u.size,"sha256":u.hash,"storage_key":u.hash,"current_version":1,"versions":[v],"scan":{"status":"pending"},"processing":{"status":"queued"},"classification":"internal"});
    let r = repo::create(
        &state.pool,
        &processing::writer_metadata_actor(actor),
        NewResource {
            kind: "file".into(),
            name: u.name,
            parent_id: u.parent_id,
            visibility: Some(
                parent
                    .as_ref()
                    .map(|p| p.visibility.clone())
                    .or(u.visibility)
                    .unwrap_or("private".into()),
            ),
            acl: parent.map(|p| p.acl),
            data,
        },
    )
    .await?;
    queue(state, actor, &r).await?;
    if let Err(error) = crate::workflows::emit_event(state, "file.created", &r).await {
        tracing::warn!(error=%error,"File event scheduling failed");
    }
    Ok(r)
}
async fn queue(state: &AppState, actor: &Actor, r: &Resource) -> ApiResult<Job> {
    let number = r
        .data
        .get("current_version")
        .and_then(Value::as_i64)
        .unwrap_or(1);
    repo::enqueue(
        &state.pool,
        actor,
        "content.process",
        json!({"file_id":r.id,"number":number,"actor_id":actor.id,"internal_processing_grant":processing::grant(state,actor,r,number)?}),
        Some(&format!("content:{}:{number}", r.id)),
    )
    .await
}
async fn upload(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    multipart: Multipart,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("content:write")?;
    if actor.role == "viewer" {
        return Err(ApiError::forbidden("Viewer accounts cannot upload files"));
    }
    let u = receive(&state, multipart).await?;
    Ok(Json(create_saved(&state, &actor, u).await?))
}
pub async fn store_bytes(
    state: &AppState,
    actor: &Actor,
    name: &str,
    mime: &str,
    bytes: &[u8],
    parent_id: Option<Uuid>,
) -> ApiResult<Resource> {
    crate::core::module_enabled(state, "files").await?;
    writable_parent(state, actor, parent_id).await?;
    if bytes.len() as u64 > storage::limit() {
        return Err(ApiError::bad_request("Generated file exceeds upload limit"));
    }
    let name = clean_name(name)?;
    let (path, mut f) = storage::staging(state).await?;
    let cleanup = storage::StagingCleanup(path.clone());
    f.write_all(bytes).await.map_err(err)?;
    f.flush().await.map_err(err)?;
    drop(f);
    create_saved(
        state,
        actor,
        Uploaded {
            _cleanup: cleanup,
            path,
            name,
            mime: safe_mime(mime),
            size: bytes.len() as u64,
            hash: hex::encode(Sha256::digest(bytes)),
            parent_id,
            visibility: None,
            expected_version: None,
        },
    )
    .await
}
pub async fn read_file_bytes(state: &AppState, actor: &Actor, id: Uuid) -> ApiResult<Vec<u8>> {
    crate::core::module_enabled(state, "files").await?;
    let r = file(state, actor, id).await?;
    authorize_action(state, actor, &r, "download").await?;
    let v = current_version(&r)?;
    check_version_scan(&v)?;
    storage::bytes(state, version_key(&v)?, storage::limit()).await
}
async fn upload_version(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    multipart: Multipart,
) -> ApiResult<Json<Resource>> {
    let r = file(&state, &actor, id).await?;
    if !actor.can_write(&r) {
        return Err(ApiError::forbidden("File is read-only"));
    }
    authorize_action(&state, &actor, &r, "update").await?;
    let u = receive(&state, multipart).await?;
    if u.expected_version != Some(r.version) {
        let _ = tokio::fs::remove_file(&u.path).await;
        return Err(ApiError::conflict(
            "File changed; reload before uploading a new version",
        ));
    }
    let mut data = r.data.clone();
    let next = data
        .get("versions")
        .and_then(Value::as_array)
        .and_then(|a| {
            a.iter()
                .filter_map(|v| v.get("number").and_then(Value::as_i64))
                .max()
        })
        .unwrap_or(0)
        + 1;
    let sv = storage::commit(&state, &u.path, &u.hash, u.size, &u.mime).await?;
    let v = fresh_version(next, &u.hash, &u.mime, u.size, sv);
    data["versions"]
        .as_array_mut()
        .ok_or_else(|| ApiError::internal("Missing file version list"))?
        .push(v);
    data["mime"] = json!(u.mime);
    data["size"] = json!(u.size);
    data["sha256"] = json!(u.hash);
    data["storage_key"] = json!(u.hash);
    data["current_version"] = json!(next);
    data["scan"] = json!({"status":"pending"});
    data["processing"] = json!({"status":"queued"});
    data.as_object_mut().unwrap().remove("preview");
    data.as_object_mut().unwrap().remove("extracted_text");
    let updated = repo::update(
        &state.pool,
        &actor,
        id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(data),
            expected_version: r.version,
        },
    )
    .await?;
    queue(&state, &actor, &updated).await?;
    if let Err(error) = crate::workflows::emit_event(&state, "file.updated", &updated).await {
        tracing::warn!(error=%error,"File event scheduling failed");
    }
    Ok(Json(updated))
}
fn check_version_scan(v: &Value) -> ApiResult<()> {
    if v.pointer("/scan/status").and_then(Value::as_str) == Some("infected") {
        return Err(ApiError::forbidden("This file version is quarantined"));
    }
    if std::env::var("BLUEPRINT_REQUIRE_SCAN").as_deref() == Ok("1")
        && v.pointer("/scan/status").and_then(Value::as_str) != Some("clean")
    {
        return Err(ApiError::unavailable(
            "This file version has not passed malware scanning",
        ));
    }
    Ok(())
}
async fn content_audit(
    state: &AppState,
    actor: &Actor,
    action: &str,
    id: Option<Uuid>,
    mut details: Value,
) -> ApiResult<()> {
    if actor.id.is_nil() {
        details["viewer"] = json!(actor.name);
    }
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,$2,$3,$4)")
        .bind((!actor.id.is_nil()).then_some(actor.id))
        .bind(action)
        .bind(id)
        .bind(details)
        .execute(&state.pool)
        .await
        .map_err(err)?;
    Ok(())
}
async fn response_for(
    state: &AppState,
    actor: &Actor,
    r: &Resource,
    v: &Value,
    headers: &HeaderMap,
    inline: bool,
) -> ApiResult<Response> {
    authorize_action(state, actor, r, if inline { "preview" } else { "download" }).await?;
    check_version_scan(v)?;
    let mime = v
        .get("mime")
        .and_then(Value::as_str)
        .unwrap_or("application/octet-stream");
    let size = v.get("size").and_then(Value::as_u64).unwrap_or(0);
    content_audit(state,actor,if inline{"file.preview"}else{"file.download"},Some(r.id),json!({"file_version":v["number"],"range":headers.get("range").and_then(|v|v.to_str().ok())})).await?;
    if !inline && !actor.id.is_nil() && headers.get("range").is_none() {
        if let Err(error) = detect_download_volume(state, actor).await {
            tracing::warn!(error=%error,"Download volume monitoring failed");
        }
    }
    storage::download(
        state,
        version_key(v)?,
        size,
        mime,
        &r.name,
        headers.get("range").and_then(|v| v.to_str().ok()),
        inline,
    )
    .await
}
async fn download(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let r = file(&state, &actor, id).await?;
    response_for(&state, &actor, &r, &current_version(&r)?, &headers, false).await
}
async fn download_version(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path((id, number)): Path<(Uuid, i64)>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let r = file(&state, &actor, id).await?;
    response_for(
        &state,
        &actor,
        &r,
        &version_metadata(&r, number)?,
        &headers,
        false,
    )
    .await
}
fn native_preview(mime: &str) -> bool {
    mime == "application/pdf"
        || mime.starts_with("text/plain")
        || matches!(
            mime,
            "image/png"
                | "image/jpeg"
                | "image/gif"
                | "image/webp"
                | "image/avif"
                | "audio/mpeg"
                | "audio/ogg"
                | "audio/wav"
                | "video/mp4"
                | "video/webm"
        )
}
async fn preview(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let r = file(&state, &actor, id).await?;
    preview_resource(&state, &actor, &r, &headers).await
}
async fn preview_resource(
    state: &AppState,
    actor: &Actor,
    r: &Resource,
    headers: &HeaderMap,
) -> ApiResult<Response> {
    authorize_action(state, actor, r, "preview").await?;
    let rules = policy::preview_rules(state, actor, r).await?;
    if rules.watermark {
        return watermark::response(state, actor, r, &rules).await;
    }
    let v = current_version(r)?;
    let mime = v.get("mime").and_then(Value::as_str).unwrap_or("");
    if native_preview(mime) {
        let mut response = response_for(state, actor, r, &v, headers, true).await?;
        if rules.prevent_print {
            response.headers_mut().insert(
                "x-blueprint-print-restricted",
                axum::http::HeaderValue::from_static("1"),
            );
        }
        return Ok(response);
    }
    if let Some(p) = r
        .data
        .get("preview")
        .filter(|p| p.get("file_version") == Some(&r.data["current_version"]))
    {
        let pv = json!({"storage_key":p["storage_key"],"mime":"application/pdf","size":p["size"],"number":r.data["current_version"],"scan":r.data["scan"]});
        let mut response = response_for(state, actor, r, &pv, headers, true).await?;
        if rules.prevent_print {
            response.headers_mut().insert(
                "x-blueprint-print-restricted",
                axum::http::HeaderValue::from_static("1"),
            );
        }
        return Ok(response);
    }
    Err(ApiError::unavailable(
        "Preview is not ready; inspect processing status or configure the document processing service",
    ))
}
async fn versions(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let r = file(&state, &actor, id).await?;
    authorize_action(&state, &actor, &r, "read").await?;
    Ok(Json(
        json!({"current_version":r.data["current_version"],"versions":r.data["versions"]}),
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Expected {
    expected_version: i64,
}
async fn restore_version(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path((id, number)): Path<(Uuid, i64)>,
    Json(input): Json<Expected>,
) -> ApiResult<Json<Resource>> {
    let r = file(&state, &actor, id).await?;
    if !actor.can_write(&r) {
        return Err(ApiError::forbidden("File is read-only"));
    }
    authorize_action(&state, &actor, &r, "update").await?;
    let v = version_metadata(&r, number)?;
    if v.pointer("/scan/status").and_then(Value::as_str) == Some("infected") {
        return Err(ApiError::forbidden(
            "Cannot make a quarantined version current",
        ));
    }
    let mut data = r.data.clone();
    data["current_version"] = json!(number);
    for key in ["mime", "size", "sha256", "storage_key", "scan"] {
        data[key] = v.get(key).cloned().unwrap_or(Value::Null);
    }
    data.as_object_mut().unwrap().remove("preview");
    data.as_object_mut().unwrap().remove("extracted_text");
    let updated = repo::update(
        &state.pool,
        &actor,
        id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(data),
            expected_version: input.expected_version,
        },
    )
    .await?;
    queue(&state, &actor, &updated).await?;
    if let Err(error) = crate::workflows::emit_event(&state, "file.updated", &updated).await {
        tracing::warn!(error=%error,"File event scheduling failed");
    }
    Ok(Json(updated))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CopyInput {
    name: Option<String>,
    parent_id: Option<Uuid>,
}
async fn copy_file(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<CopyInput>,
) -> ApiResult<Json<Resource>> {
    Ok(Json(
        copying::copy(&state, &actor, id, input.name, input.parent_id).await?,
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LockInput {
    locked: bool,
    minutes: Option<i64>,
    disable_download: Option<bool>,
}
async fn lock_file(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<LockInput>,
) -> ApiResult<Json<Resource>> {
    let r = file(&state, &actor, id).await?;
    if !actor.can_write(&r) {
        return Err(ApiError::forbidden("File is read-only"));
    }
    authorize_action(&state, &actor, &r, "update").await?;
    if input.minutes.is_some_and(|m| m <= 0 || m > 525600) {
        return Err(ApiError::bad_request(
            "Lock duration must be between 1 minute and one year",
        ));
    }
    let mut data = r.data.clone();
    data["lock"] = json!({"locked":input.locked,"owner_id":actor.id,"until":if input.locked{input.minutes.map(|m|(Utc::now()+Duration::minutes(m)).to_rfc3339())}else{None},"disable_download":input.disable_download.unwrap_or(false)});
    Ok(Json(
        repo::update(
            &state.pool,
            &actor,
            id,
            UpdateResource {
                name: None,
                parent_id: None,
                visibility: None,
                acl: None,
                data: Some(data),
                expected_version: r.version,
            },
        )
        .await?,
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Governance {
    expected_version: i64,
    retain_until: Option<String>,
    legal_hold: Option<bool>,
    archived: Option<bool>,
}
async fn governance(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<Governance>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("admin:write")?;
    if !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Only administrators can change governance",
        ));
    }
    let r = repo::get(&state.pool, &actor, id).await?;
    if !matches!(r.kind.as_str(), "file" | "folder") {
        return Err(ApiError::bad_request(
            "Governance applies to files and folders",
        ));
    }
    if input.expected_version != r.version {
        return Err(ApiError::conflict(
            "File changed; reload before applying governance",
        ));
    }
    if let Some(s) = &input.retain_until {
        let until = DateTime::parse_from_rfc3339(s)
            .map_err(|_| ApiError::bad_request("Invalid retention date"))?;
        if until < Utc::now() {
            return Err(ApiError::bad_request(
                "Retention date must be in the future",
            ));
        }
        if let Some(old) = r
            .data
            .get("retain_until")
            .and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        {
            if until < old {
                return Err(ApiError::forbidden(
                    "Existing retention cannot be shortened",
                ));
            }
        }
    }
    let mut data = r.data.clone();
    if let Some(v) = input.retain_until {
        data["retain_until"] = json!(v);
    }
    if let Some(v) = input.legal_hold {
        data["legal_hold"] = json!(v);
    }
    if let Some(v) = input.archived {
        data["archived"] = json!(v);
    }
    let mut proposed = r.clone();
    proposed.data = data.clone();
    let (effective_retain, effective_hold) =
        policy::effective_governance(&state, &proposed).await?;
    data["effective_governance"] =
        json!({"retain_until":effective_retain,"legal_hold":effective_hold});
    data["governance_enforcement"] = json!(if storage::storage_governance() {
        "application_pending_storage"
    } else {
        "application"
    });
    let mut updated = repo::update(
        &state.pool,
        &actor,
        id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(data),
            expected_version: r.version,
        },
    )
    .await?;
    after_write(&state, &actor, &updated).await?;
    proposed = updated.clone();
    if let Some(versions) = r.data["versions"].as_array() {
        for v in versions {
            apply_version_governance(&state, &proposed, v).await?;
        }
    }

    if storage::storage_governance() && updated.kind == "file" {
        let mut applied = updated.data.clone();
        applied["governance_enforcement"] = json!("storage_and_application");
        updated = repo::update(
            &state.pool,
            &actor,
            id,
            UpdateResource {
                name: None,
                parent_id: None,
                visibility: None,
                acl: None,
                data: Some(applied),
                expected_version: updated.version,
            },
        )
        .await?;
    }

    Ok(Json(updated))
}
async fn reprocess(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Job>> {
    let r = file(&state, &actor, id).await?;
    if !actor.can_write(&r) {
        return Err(ApiError::forbidden("File is read-only"));
    }
    authorize_action(&state, &actor, &r, "update").await?;
    Ok(Json(
        repo::enqueue(
            &state.pool,
            &actor,
            "content.process",
            json!({"file_id":id,"number":r.data["current_version"],"actor_id":actor.id}),
            None,
        )
        .await?,
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CommentInput {
    body: String,
}
async fn comments(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Vec<Resource>>> {
    let r = repo::get(&state.pool, &actor, id).await?;
    authorize_action(&state, &actor, &r, "read").await?;
    let all = repo::list(&state.pool, &actor, "comment", None).await?;
    Ok(Json(
        all.into_iter()
            .filter(|c| c.parent_id == Some(id))
            .collect(),
    ))
}
async fn add_comment(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<CommentInput>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("content:write")?;
    let r = repo::get(&state.pool, &actor, id).await?;
    authorize_action(&state, &actor, &r, "comment").await?;
    if input.body.trim().is_empty() || input.body.len() > 100000 {
        return Err(ApiError::bad_request("Comment must contain 1–100000 bytes"));
    }
    Ok(Json(
        repo::create(
            &state.pool,
            &actor,
            NewResource {
                kind: "comment".into(),
                name: format!("Comment by {}", actor.name),
                parent_id: Some(id),
                visibility: Some(r.visibility),
                acl: Some(r.acl),
                data: json!({"body":input.body,"author_id":actor.id,"author_name":actor.name}),
            },
        )
        .await?,
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ShareInput {
    resource_id: Uuid,
    access: String,
    password: Option<String>,
    expires_at: Option<String>,
    allow_download: bool,
}
fn hash_password(password: &str) -> ApiResult<String> {
    Argon2::default()
        .hash_password(password.as_bytes(), &SaltString::generate(&mut OsRng))
        .map(|h| h.to_string())
        .map_err(err)
}
fn password_ok(password: &str, hash: &str) -> bool {
    PasswordHash::new(hash).ok().is_some_and(|h| {
        Argon2::default()
            .verify_password(password.as_bytes(), &h)
            .is_ok()
    })
}
async fn create_share(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<ShareInput>,
) -> ApiResult<Json<Value>> {
    let r = repo::get(&state.pool, &actor, input.resource_id).await?;
    if !actor.can_manage(&r) {
        return Err(ApiError::forbidden(
            "Only content managers can create sharing links",
        ));
    }
    authorize_action(&state, &actor, &r, "share").await?;
    let access = match input.access.as_str() {
        "link" | "anyone" => "anyone",
        "company" => "company",
        "invited" => "invited",
        _ => return Err(ApiError::bad_request("Invalid sharing scope")),
    };
    let expires = if let Some(e) = input.expires_at {
        let d = DateTime::parse_from_rfc3339(&e)
            .map_err(|_| ApiError::bad_request("Invalid share expiration"))?;
        if d < Utc::now() {
            return Err(ApiError::bad_request(
                "Share expiration must be in the future",
            ));
        }
        d.with_timezone(&Utc)
    } else {
        Utc::now() + Duration::days(7)
    };
    let password_hash = if let Some(p) = input.password.filter(|s| !s.is_empty()) {
        if p.len() < 8 || p.len() > 1024 {
            return Err(ApiError::bad_request(
                "Sharing password must contain 8–1024 bytes",
            ));
        }
        Some(hash_password(&p)?)
    } else {
        None
    };
    let mut token = [0u8; 32];
    OsRng.fill_bytes(&mut token);
    let token = hex::encode(token);
    let token_hash = hex::encode(Sha256::digest(token.as_bytes()));
    let share=repo::create(&state.pool,&actor,NewResource{kind:"share".into(),name:format!("Share: {}",r.name),parent_id:None,visibility:Some("private".into()),acl:None,data:json!({"resource_id":r.id,"access":access,"allow_download":input.allow_download,"expires_at":expires,"password_hash":password_hash,"token_hash":token_hash,"revoked":false})}).await?;
    repo::audit(
        &state.pool,
        &actor,
        "share.create",
        Some(r.id),
        json!({"share_id":share.id,"access":access,"allow_download":input.allow_download}),
    )
    .await?;
    Ok(Json(
        json!({"id":share.id,"resource_id":r.id,"access":access,"expires_at":expires,"allow_download":input.allow_download,"password_protected":password_hash.is_some(),"url":format!("{}/share/{}",state.config.public_url.trim_end_matches('/'),token)}),
    ))
}
#[derive(Deserialize)]
struct ShareQuery {
    resource_id: Option<Uuid>,
}
async fn shares(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Query(q): Query<ShareQuery>,
) -> ApiResult<Json<Vec<Value>>> {
    let resources = repo::list(&state.pool, &actor, "share", None).await?;
    Ok(Json(resources.into_iter().filter(|s|q.resource_id.is_none_or(|id|s.data.get("resource_id").and_then(Value::as_str)==Some(id.to_string().as_str()))).map(|s|json!({"id":s.id,"resource_id":s.data["resource_id"],"access":s.data["access"],"expires_at":s.data["expires_at"],"allow_download":s.data["allow_download"],"password_protected":s.data["password_hash"].as_str().is_some(),"revoked":s.data["revoked"],"version":s.version})).collect()))
}
async fn revoke_share(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let r = repo::get(&state.pool, &actor, id).await?;
    if r.kind != "share" || !actor.can_manage(&r) {
        return Err(ApiError::forbidden("Cannot revoke this sharing link"));
    }
    let mut data = r.data.clone();
    data["revoked"] = json!(true);
    repo::update(
        &state.pool,
        &actor,
        id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(data),
            expected_version: r.version,
        },
    )
    .await?;
    Ok(Json(json!({"revoked":true})))
}
struct ResolvedShare {
    share: Resource,
    resource: Resource,
    owner: Actor,
    viewer: Actor,
}
async fn resolve_share(
    state: &AppState,
    token: &str,
    headers: &HeaderMap,
) -> ApiResult<ResolvedShare> {
    crate::core::module_enabled(state, "files").await?;
    if token.len() != 64 || !token.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(ApiError::not_found("Sharing link not found"));
    }
    let hash = hex::encode(Sha256::digest(token.as_bytes()));
    let share=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind='share' AND data->>'token_hash'=$1 AND deleted_at IS NULL").bind(hash).fetch_optional(&state.pool).await.map_err(err)?.ok_or_else(||ApiError::not_found("Sharing link not found"))?;
    if share.data.get("revoked").and_then(Value::as_bool) == Some(true)
        || !policy::future_date(share.data.get("expires_at"))
    {
        return Err(ApiError::not_found("Sharing link is expired or revoked"));
    }
    if let Some(hash) = share.data.get("password_hash").and_then(Value::as_str) {
        let password = headers
            .get("x-share-password")
            .and_then(|h| h.to_str().ok())
            .unwrap_or("");
        if !password_ok(password, hash) {
            return Err(ApiError::unauthorized(
                "A valid sharing password is required",
            ));
        }
    }
    let owner = auth::actor_by_id(state, share.owner_id).await?;
    let rid = share
        .data
        .get("resource_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .ok_or_else(|| ApiError::internal("Invalid sharing resource reference"))?;
    let resource = repo::get(&state.pool, &owner, rid).await?;
    if resource.deleted_at.is_some() {
        return Err(ApiError::not_found("Shared content is in trash"));
    }
    if !owner.can_manage(&resource) {
        return Err(ApiError::not_found(
            "Sharing owner can no longer manage this content",
        ));
    }
    authorize_action(state, &owner, &resource, "share").await?;
    let signed_in = auth::optional_actor(state, headers).await?;
    match share.data.get("access").and_then(Value::as_str) {
        Some("anyone") => {}
        Some("company" | "invited") => {
            let viewer = signed_in
                .as_ref()
                .ok_or_else(|| ApiError::unauthorized("Sign in to open this sharing link"))?;
            if share.data["access"] == "company" && viewer.role == "guest" {
                return Err(ApiError::forbidden(
                    "This link is restricted to company members",
                ));
            }
            if share.data["access"] == "invited" {
                repo::get(&state.pool, viewer, rid).await?;
            }
        }
        _ => return Err(ApiError::forbidden("Sharing scope is invalid")),
    }
    let viewer = signed_in.unwrap_or_else(|| Actor {
        id: Uuid::nil(),
        email: format!("Shared link {}", &token[..12]),
        name: "Anonymous shared-link visitor".into(),
        role: "guest".into(),
        groups: vec![],
        scopes: Some(vec!["content:read".into()]),
        resource_ids: None,
        token_id: None,
    });
    if viewer
        .resource_ids
        .as_ref()
        .is_some_and(|ids| !ids.contains(&resource.id))
    {
        return Err(ApiError::forbidden(
            "Sharing source is outside the viewer token's resource scope",
        ));
    }
    authorize_action(state, &viewer, &resource, "read").await?;
    Ok(ResolvedShare {
        share,
        resource,
        owner,
        viewer,
    })
}

fn shared_metadata(r: &Resource) -> Value {
    let data = if r.kind == "file" {
        json!({"mime":r.data["mime"],"size":r.data["size"],"current_version":r.data["current_version"],"classification":r.data["classification"],"scan":r.data["scan"]})
    } else {
        r.data.clone()
    };
    json!({"id":r.id,"kind":r.kind,"name":r.name,"data":data,"version":r.version,"updated_at":r.updated_at})
}
async fn public_share(
    State(state): State<AppState>,
    Path(token): Path<String>,
    headers: HeaderMap,
) -> ApiResult<Json<Value>> {
    let s = resolve_share(&state, &token, &headers).await?;
    authorize_action(&state, &s.viewer, &s.resource, "read").await?;
    content_audit(
        &state,
        &s.viewer,
        "share.view",
        Some(s.resource.id),
        json!({"share_id":s.share.id}),
    )
    .await?;
    Ok(Json(
        json!({"resource":shared_metadata(&s.resource),"allow_download":s.share.data["allow_download"],"expires_at":s.share.data["expires_at"]}),
    ))
}
async fn public_download(
    State(state): State<AppState>,
    Path(token): Path<String>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let s = resolve_share(&state, &token, &headers).await?;
    if s.share.data.get("allow_download").and_then(Value::as_bool) != Some(true) {
        return Err(ApiError::forbidden(
            "Downloading is disabled for this sharing link",
        ));
    }
    if s.resource.kind != "file" {
        return Err(ApiError::bad_request(
            "This sharing link does not point to a file",
        ));
    }
    response_for(
        &state,
        &s.viewer,
        &s.resource,
        &current_version(&s.resource)?,
        &headers,
        false,
    )
    .await
}
async fn public_preview(
    State(state): State<AppState>,
    Path(token): Path<String>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let s = resolve_share(&state, &token, &headers).await?;
    if s.resource.kind != "file" {
        return Err(ApiError::bad_request(
            "This sharing link does not point to a file",
        ));
    }
    preview_resource(&state, &s.viewer, &s.resource, &headers).await
}
#[derive(Deserialize)]
struct PublicItemsQuery {
    parent_id: Option<Uuid>,
}
async fn shared_descendant(state: &AppState, s: &ResolvedShare, id: Uuid) -> ApiResult<Resource> {
    let r = repo::get(&state.pool, &s.owner, id).await?;
    if r.deleted_at.is_some() {
        return Err(ApiError::not_found("Shared content is in trash"));
    }
    if !policy::ancestors(state, &r).await?.contains(&s.resource.id) {
        return Err(ApiError::forbidden("Content is outside the shared folder"));
    }
    authorize_action(state, &s.owner, &r, "share").await?;
    if s.viewer
        .resource_ids
        .as_ref()
        .is_some_and(|ids| !ids.contains(&r.id))
    {
        return Err(ApiError::forbidden(
            "Content is outside the viewer token's resource scope",
        ));
    }
    if s.share.data["access"] == "invited" {
        repo::get(&state.pool, &s.viewer, r.id).await?;
    }
    authorize_action(state, &s.viewer, &r, "read").await?;
    Ok(r)
}
async fn public_items(
    State(state): State<AppState>,
    Path(token): Path<String>,
    Query(query): Query<PublicItemsQuery>,
    headers: HeaderMap,
) -> ApiResult<Json<Vec<Value>>> {
    let s = resolve_share(&state, &token, &headers).await?;
    if s.resource.kind != "folder" {
        return Err(ApiError::bad_request(
            "This sharing link does not point to a folder",
        ));
    }
    let parent = shared_descendant(&state, &s, query.parent_id.unwrap_or(s.resource.id)).await?;
    if parent.kind != "folder" {
        return Err(ApiError::bad_request("Browse a folder"));
    }
    let rows=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE parent_id=$1 AND kind IN ('file','folder') AND deleted_at IS NULL ORDER BY name LIMIT 1000").bind(parent.id).fetch_all(&state.pool).await.map_err(err)?;
    let mut result = Vec::new();
    for r in rows {
        if shared_descendant(&state, &s, r.id).await.is_ok()
            && authorize_action(&state, &s.viewer, &r, "read")
                .await
                .is_ok()
        {
            result.push(shared_metadata(&r));
        }
    }
    Ok(Json(result))
}
async fn public_child_download(
    State(state): State<AppState>,
    Path((token, id)): Path<(String, Uuid)>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    let s = resolve_share(&state, &token, &headers).await?;
    if s.resource.kind != "folder"
        || s.share.data.get("allow_download").and_then(Value::as_bool) != Some(true)
    {
        return Err(ApiError::forbidden(
            "Downloading is disabled for this sharing link",
        ));
    }
    let r = shared_descendant(&state, &s, id).await?;
    if r.kind != "file" {
        return Err(ApiError::bad_request("Only files can be downloaded"));
    }
    response_for(
        &state,
        &s.viewer,
        &r,
        &current_version(&r)?,
        &headers,
        false,
    )
    .await
}
async fn capabilities(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("content:read")?;
    Ok(Json(
        json!({"storage":if storage::is_s3(){"s3"}else{"local"},"object_lock":storage::verified_object_lock().await,"object_lock_required":storage::storage_governance(),"max_file_bytes":storage::limit(),"services":{"scan":state.config.services.contains_key("clamav"),"extract":state.config.services.contains_key("tika"),"office_preview":state.config.services.contains_key("gotenberg")},"scan_required":std::env::var("BLUEPRINT_REQUIRE_SCAN").as_deref()==Ok("1")}),
    ))
}

pub async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    if previous.and_then(|r| r.data.get("inherited_classification_policies"))
        != data.get("inherited_classification_policies")
    {
        return Err(ApiError::bad_request(
            "Inherited classification policies are managed by content operations",
        ));
    }
    if kind == "link" {
        let url = data
            .get("url")
            .and_then(Value::as_str)
            .and_then(|s| url::Url::parse(s).ok())
            .ok_or_else(|| ApiError::bad_request("Link URL must be absolute HTTP or HTTPS"))?;
        if !matches!(url.scheme(), "http" | "https")
            || url.host_str().is_none()
            || !url.username().is_empty()
            || url.password().is_some()
        {
            return Err(ApiError::bad_request(
                "Link URL must use HTTP or HTTPS without embedded credentials",
            ));
        }
    }
    if kind == "file" {
        let Some(old) = previous else {
            return Err(ApiError::bad_request(
                "Create files using the upload endpoint",
            ));
        };
        if !actor.is_admin() && old.data.get("classification") != data.get("classification") {
            return Err(ApiError::forbidden(
                "Only administrators can override a file classification",
            ));
        }
        for key in [
            "versions",
            "current_version",
            "storage_key",
            "sha256",
            "size",
            "mime",
            "scan",
            "processing",
            "classification_matches",
            "inherited_classification_policies",
            "text_extraction",
            "preview",
            "extracted_text",
            "retain_until",
            "legal_hold",
            "archived",
            "governance_enforcement",
            "effective_governance",
            "watermark",
            "prevent_print",
            "lock",
        ] {
            if old.data.get(key) != data.get(key) {
                return Err(ApiError::bad_request(format!(
                    "{key} is managed by a dedicated file operation"
                )));
            }
        }
    }
    if kind == "folder" {
        for key in [
            "retain_until",
            "legal_hold",
            "archived",
            "governance_enforcement",
            "effective_governance",
            "inherited_classification_policies",
            "watermark",
            "prevent_print",
        ] {
            if previous.and_then(|old| old.data.get(key)) != data.get(key) {
                return Err(ApiError::bad_request(format!(
                    "{key} is managed by the governance operation"
                )));
            }
        }
    }
    if kind == "upload_session" {
        return Err(ApiError::bad_request(
            "Manage resumable uploads through the upload session API",
        ));
    }
    if kind == "share" {
        return Err(ApiError::bad_request(
            "Manage sharing through the sharing API",
        ));
    }
    if matches!(
        kind,
        "retention" | "legal_hold" | "archive" | "classification"
    ) {
        actor.require_scope("admin:write")?;
        if !actor.is_admin() {
            return Err(ApiError::forbidden(
                "Only administrators can manage content policies",
            ));
        }
        if !data.is_object() {
            return Err(ApiError::bad_request(
                "Policy configuration must be an object",
            ));
        }
        if let Some(ids) = data.get("resource_ids") {
            let ids = ids
                .as_array()
                .ok_or_else(|| ApiError::bad_request("resource_ids must be an array"))?;
            if ids.len() > 10000 {
                return Err(ApiError::bad_request("Too many policy targets"));
            }
            for id in ids {
                let id = id
                    .as_str()
                    .and_then(|s| s.parse::<Uuid>().ok())
                    .ok_or_else(|| ApiError::bad_request("Invalid policy resource identifier"))?;
                let _ = repo::get(&state.pool, actor, id).await?;
            }
        }
        if kind == "retention" {
            if data.get("active").and_then(Value::as_bool) == Some(true)
                && !policy::future_date(data.get("retain_until"))
            {
                return Err(ApiError::bad_request(
                    "Active retention requires a future retain_until date",
                ));
            }
            if let Some(old) = previous {
                if policy::future_date(old.data.get("retain_until")) {
                    let old_date = old.data["retain_until"].as_str().unwrap_or("");
                    let new_date = data
                        .get("retain_until")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    let old_parsed = DateTime::parse_from_rfc3339(old_date).ok();
                    let new_parsed = DateTime::parse_from_rfc3339(new_date).ok();
                    let scope_changed = [
                        "resource_id",
                        "resource_ids",
                        "all_resources",
                        "classification",
                    ]
                    .iter()
                    .any(|key| old.data.get(*key) != data.get(*key));
                    if data.get("active").and_then(Value::as_bool) != Some(true)
                        || new_parsed.is_none()
                        || new_parsed < old_parsed
                        || scope_changed
                    {
                        return Err(ApiError::forbidden(
                            "An active retention policy cannot be shortened or disabled",
                        ));
                    }
                }
            }
        }
        if kind == "classification" {
            if let Some(patterns) = data.get("patterns") {
                let patterns = patterns
                    .as_array()
                    .ok_or_else(|| ApiError::bad_request("patterns must be an array"))?;
                if patterns.len() > 32 {
                    return Err(ApiError::bad_request("Too many classification patterns"));
                }
                for p in patterns {
                    let text = p.as_str().ok_or_else(|| {
                        ApiError::bad_request("Classification pattern must be text")
                    })?;
                    if text.len() > 2048 {
                        return Err(ApiError::bad_request("Classification pattern too long"));
                    }
                    regex::Regex::new(text).map_err(|_| {
                        ApiError::bad_request("Invalid classification regular expression")
                    })?;
                }
            }
        }
    }
    if kind == "comment" {
        let body = data.get("body").and_then(Value::as_str).unwrap_or("");
        if body.trim().is_empty() || body.len() > 100000 {
            return Err(ApiError::bad_request("Invalid comment body"));
        }
    }
    if kind == "collection" {
        if let Some(ids) = data.get("resource_ids").and_then(Value::as_array) {
            for id in ids {
                let id = id
                    .as_str()
                    .and_then(|s| s.parse::<Uuid>().ok())
                    .ok_or_else(|| ApiError::bad_request("Invalid collection reference"))?;
                let _ = repo::get(&state.pool, actor, id).await?;
            }
        }
    }
    Ok(())
}

async fn scan_file(state: &AppState, path: &std::path::Path) -> ApiResult<Value> {
    let Some(address) = state.config.services.get("clamav") else {
        return Ok(json!({"status":"not_configured"}));
    };
    let address = address.strip_prefix("tcp://").unwrap_or(address);
    let operation = async {
        let engine_version = clamav_version(address).await.ok();
        let mut socket = tokio::net::TcpStream::connect(address)
            .await
            .map_err(|_| ApiError::unavailable("Malware scanning service is unreachable"))?;
        socket.write_all(b"zINSTREAM\0").await.map_err(err)?;
        let mut file = tokio::fs::File::open(path).await.map_err(err)?;
        let mut buffer = vec![0u8; 65536];
        loop {
            let n = file.read(&mut buffer).await.map_err(err)?;
            if n == 0 {
                break;
            }
            socket
                .write_all(&(n as u32).to_be_bytes())
                .await
                .map_err(err)?;
            socket.write_all(&buffer[..n]).await.map_err(err)?;
        }
        socket.write_all(&0u32.to_be_bytes()).await.map_err(err)?;
        let mut response = Vec::new();
        loop {
            let mut buf = [0u8; 1024];
            let n = socket.read(&mut buf).await.map_err(err)?;
            if n == 0 {
                break;
            }
            response.extend_from_slice(&buf[..n]);
            if response.contains(&0) || response.len() > 65536 {
                break;
            }
        }
        let detail = String::from_utf8_lossy(&response)
            .trim_matches('\0')
            .trim()
            .to_owned();
        if detail.ends_with(" OK") {
            Ok(
                json!({"status":"clean","engine":"clamav","engine_version":engine_version,"checked_at":Utc::now()}),
            )
        } else if detail.ends_with(" FOUND") {
            Ok(
                json!({"status":"infected","engine":"clamav","engine_version":engine_version,"detail":detail,"checked_at":Utc::now()}),
            )
        } else {
            Err(ApiError::unavailable(
                "Malware scan did not produce a verdict",
            ))
        }
    };
    tokio::time::timeout(std::time::Duration::from_secs(180), operation)
        .await
        .map_err(|_| ApiError::unavailable("Malware scanning timed out"))?
}
fn request_file_stream(file: tokio::fs::File) -> reqwest::Body {
    reqwest::Body::wrap_stream(futures_util::stream::try_unfold(file, |mut f| async move {
        let mut b = vec![0u8; 65536];
        let n = f.read(&mut b).await?;
        if n == 0 {
            Ok::<_, std::io::Error>(None)
        } else {
            b.truncate(n);
            Ok(Some((b, f)))
        }
    }))
}
fn truncate_text(mut text: String, max: usize) -> String {
    if text.len() > max {
        let mut boundary = max;
        while !text.is_char_boundary(boundary) {
            boundary -= 1;
        }
        text.truncate(boundary);
    }
    text
}
async fn extract_text(
    state: &AppState,
    path: &std::path::Path,
    mime: &str,
    size: u64,
) -> ApiResult<Option<String>> {
    if (mime.starts_with("text/")
        || matches!(
            mime,
            "application/json" | "application/xml" | "application/javascript"
        ))
        && size <= 2_000_000
    {
        let bytes = tokio::fs::read(path).await.map_err(err)?;
        return Ok(String::from_utf8(bytes)
            .ok()
            .map(|s| truncate_text(s, 2_000_000)));
    }
    let Some(endpoint) = state.config.services.get("tika") else {
        return Ok(None);
    };
    let f = tokio::fs::File::open(path).await.map_err(err)?;
    let response = state
        .http
        .put(format!("{}/tika", endpoint.trim_end_matches('/')))
        .header("accept", "text/plain")
        .header("content-type", mime)
        .body(request_file_stream(f))
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|_| ApiError::unavailable("Text extraction service is unreachable"))?;
    if !response.status().is_success() {
        return Err(ApiError::unavailable(
            "Text extraction service rejected the file",
        ));
    }
    let mut stream = response.bytes_stream();
    let mut result = Vec::new();
    while let Some(chunk) = stream.try_next().await.map_err(err)? {
        let remaining = 2_000_000usize.saturating_sub(result.len());
        result.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
        if result.len() >= 2_000_000 {
            break;
        }
    }
    Ok(Some(truncate_text(
        String::from_utf8_lossy(&result).into_owned(),
        2_000_000,
    )))
}
async fn convert_preview(
    state: &AppState,
    path: &std::path::Path,
    name: &str,
    mime: &str,
) -> ApiResult<Option<Value>> {
    if native_preview(mime) {
        return Ok(None);
    }
    let Some(endpoint) = state.config.services.get("gotenberg") else {
        return Ok(None);
    };
    let allowed = [
        "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "csv",
    ];
    if !name
        .rsplit('.')
        .next()
        .is_some_and(|e| allowed.iter().any(|x| e.eq_ignore_ascii_case(x)))
    {
        return Ok(None);
    }
    let f = tokio::fs::File::open(path).await.map_err(err)?;
    let form = reqwest::multipart::Form::new().part(
        "files",
        reqwest::multipart::Part::stream(request_file_stream(f)).file_name(name.to_owned()),
    );
    let response = state
        .http
        .post(format!(
            "{}/forms/libreoffice/convert",
            endpoint.trim_end_matches('/')
        ))
        .multipart(form)
        .timeout(std::time::Duration::from_secs(180))
        .send()
        .await
        .map_err(|_| ApiError::unavailable("Document preview service is unreachable"))?;
    if !response.status().is_success() {
        return Err(ApiError::unavailable(
            "Document preview service could not convert this file",
        ));
    }
    let mut stream = response.bytes_stream();
    let mut result = Vec::new();
    while let Some(chunk) = stream.try_next().await.map_err(err)? {
        if result.len() + chunk.len() > 100 * 1024 * 1024 {
            return Err(ApiError::unavailable(
                "Generated preview exceeds its processing limit",
            ));
        }
        result.extend_from_slice(&chunk);
    }
    if !result.starts_with(b"%PDF-") {
        return Err(ApiError::unavailable(
            "Preview engine returned an invalid PDF",
        ));
    }
    let (key, version) = storage::save_bytes(state, &result, "application/pdf").await?;
    Ok(Some(
        json!({"storage_key":key,"s3_version_id":version,"size":result.len(),"mime":"application/pdf"}),
    ))
}
pub async fn process_job(state: &AppState, job: &Job) -> ApiResult<Value> {
    if job.kind == "content.gc" {
        return gc::process(state, job).await;
    }
    if job.kind == "content.governance" {
        return process_governance(state, job).await;
    }
    if job.kind != "content.process" {
        return Err(ApiError::bad_request("Unsupported content job"));
    }
    let id = job
        .payload
        .get("file_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .ok_or_else(|| ApiError::bad_request("Content job is missing file_id"))?;
    let _actor_id = job
        .payload
        .get("actor_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .ok_or_else(|| ApiError::bad_request("Content job is missing actor_id"))?;
    let (_, r) = processing::actor_and_file(state, job, id).await?;
    let number = job
        .payload
        .get("number")
        .and_then(Value::as_i64)
        .unwrap_or(1);
    let v = version_metadata(&r, number)?;
    let key = version_key(&v)?;
    let (path, _) = storage::temporary(state, key).await?;
    let _temporary_cleanup = storage::is_s3().then(|| storage::StagingCleanup(path.clone()));
    let mut data = r.data.clone();
    let (effective_retain, effective_hold) = policy::effective_governance(state, &r).await?;
    apply_version_governance(state, &r, &v).await?;
    data["effective_governance"] =
        json!({"retain_until":effective_retain,"legal_hold":effective_hold});
    data["governance_enforcement"] = json!(if storage::storage_governance() {
        "storage_and_application"
    } else {
        "application"
    });
    let mime = v
        .get("mime")
        .and_then(Value::as_str)
        .unwrap_or("application/octet-stream");
    let size = v.get("size").and_then(Value::as_u64).unwrap_or(0);
    let mut errors = Vec::new();
    let scan = match scan_file(state, &path).await {
        Ok(s) => s,
        Err(_) => {
            errors.push("Malware scan failed or timed out".to_owned());
            json!({"status":"error","engine":"clamav","engine_version":null,"checked_at":Utc::now()})
        }
    };
    let mut text = None;
    let mut preview_data = None;
    if scan.get("status").and_then(Value::as_str) != Some("infected")
        && scan.get("status").and_then(Value::as_str) != Some("error")
    {
        match extract_text(state, &path, mime, size).await {
            Ok(t) => text = t,
            Err(_) => errors.push("Text extraction failed".to_owned()),
        }
        match convert_preview(state, &path, &r.name, mime).await {
            Ok(p) => preview_data = p,
            Err(_) => errors.push("Preview conversion failed".to_owned()),
        }
    }
    if storage::is_s3() {
        let _ = tokio::fs::remove_file(&path).await;
    }
    if let Some(versions) = data.get_mut("versions").and_then(Value::as_array_mut) {
        if let Some(meta) = versions
            .iter_mut()
            .find(|v| v.get("number").and_then(Value::as_i64) == Some(number))
        {
            meta["scan"] = scan.clone();
        }
    }
    let is_current = data.get("current_version").and_then(Value::as_i64) == Some(number);
    if is_current {
        data["scan"] = scan.clone();
        if let Some(t) = text {
            data["extracted_text"] = json!(t);
            data["text_extraction"] = json!({"status":"completed","file_version":number});
        } else {
            data["text_extraction"] = json!({"status":"needs_configuration","file_version":number});
        }
        if let Some(mut p) = preview_data {
            p["file_version"] = json!(number);
            data["preview"] = p;
        }
        let policies=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind='classification' AND deleted_at IS NULL AND data->>'active'='true'").fetch_all(&state.pool).await.map_err(err)?;
        let content = format!(
            "{}\n{}",
            r.name,
            data.get("extracted_text")
                .and_then(Value::as_str)
                .unwrap_or("")
        );
        let mut matches = Vec::new();
        let ancestors = policy::ancestors(state, &r).await?;
        let mut inherited = policy::inherited_classifications(state, &r).await?;
        for p in policies {
            if p.data.get("resource_id").is_some() || p.data.get("resource_ids").is_some() {
                let mut scope = p.data.clone();
                if let Some(map) = scope.as_object_mut() {
                    map.remove("classification");
                }
                if !policy::targets(&scope, &ancestors, &r) {
                    continue;
                }
            }
            if let Some(patterns) = p.data.get("patterns").and_then(Value::as_array) {
                for pattern in patterns {
                    if let Some(pattern) = pattern.as_str() {
                        if regex::Regex::new(pattern).is_ok_and(|re| re.is_match(&content)) {
                            data["classification"] = p
                                .data
                                .get("classification")
                                .cloned()
                                .unwrap_or_else(|| json!(p.name));
                            inherited.push(p.id);
                            matches.push(json!({"policy_id":p.id,"policy_name":p.name}));
                            break;
                        }
                    }
                }
            }
        }
        if !matches.is_empty() {
            data["classification_matches"] = json!(matches);
        }
        inherited.sort();
        inherited.dedup();
        data["inherited_classification_policies"] = json!(inherited);
        data["processing"] = json!({"status":if errors.is_empty(){"completed"}else{"partial"},"errors":errors,"file_version":number,"completed_at":Utc::now()});
    }
    let (actor, _) = processing::actor_and_file(state, job, id).await?;
    let updated = processing::save_results(state, &actor, id, number, &data, &scan).await?;
    if scan.get("status").and_then(Value::as_str) == Some("infected") {
        processing::malware_alert(state, &actor, id, number, &scan).await?;
    }
    repo::audit(
        &state.pool,
        &actor,
        "file.processed",
        Some(id),
        json!({"file_version":number,"scan":scan,"errors":errors}),
    )
    .await?;
    Ok(
        json!({"file_id":id,"file_version":number,"scan":scan,"resource_version":updated.version,"errors":errors}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn filenames_cannot_escape_storage() {
        for name in ["../secret", "a/b", "a\\b", "bad\r\nname", ""] {
            assert!(clean_name(name).is_err());
        }
        assert_eq!(clean_name("公司資料.pdf").unwrap(), "公司資料.pdf");
    }
    #[test]
    fn no_html_or_svg_inline_execution() {
        assert!(!native_preview("text/html"));
        assert!(!native_preview("image/svg+xml"));
        assert!(native_preview("application/pdf"));
        assert!(native_preview("text/plain"));
    }
    #[test]
    fn passwords_are_verified_not_stored_plaintext() {
        let h = hash_password("example-share-password").unwrap();
        assert!(!h.contains("example-share-password"));
        assert!(password_ok("example-share-password", &h));
        assert!(!password_ok("wrong", &h));
    }
    #[test]
    fn utf8_extraction_limit_stays_valid() {
        let text = "藍圖".repeat(10);
        let limited = truncate_text(text, 10);
        assert!(limited.len() <= 10);
        assert_eq!(limited, "藍圖藍");
    }
}

/// Called after a governance resource has been persisted. App policies take
/// effect immediately; the queued operation also applies storage enforcement.
pub async fn cleanup_expired_uploads(state: &AppState) -> ApiResult<u64> {
    resumable::cleanup_expired(state).await
}

pub async fn prepare_purge(state: &AppState, actor: &Actor, r: &Resource) -> ApiResult<()> {
    gc::prepare(state, actor, r).await
}

pub async fn after_write(state: &AppState, actor: &Actor, r: &Resource) -> ApiResult<()> {
    let is_content = matches!(r.kind.as_str(), "file" | "folder");
    let active_archive = r.deleted_at.is_none()
        && ((r.kind == "archive" && r.data.get("active").and_then(Value::as_bool) == Some(true))
            || (is_content && r.data.get("archived").and_then(Value::as_bool) == Some(true)));
    let scope = if is_content {
        json!({"resource_ids":[r.id]})
    } else {
        r.data.clone()
    };
    if active_archive {
        let objects = sqlx::query_as::<_, Resource>(
            "SELECT * FROM resources WHERE kind IN ('file','folder') AND deleted_at IS NULL",
        )
        .fetch_all(&state.pool)
        .await
        .map_err(err)?;
        for object in objects {
            let ids = policy::ancestors(state, &object).await?;
            if policy::targets(&scope, &ids, &object) {
                revoke_resource_shares(state, actor, object.id).await?;
            }
        }
    }
    if matches!(r.kind.as_str(), "retention" | "legal_hold" | "archive")
        || (is_content
            && ["retain_until", "legal_hold", "archived", "moved_from"]
                .iter()
                .any(|k| r.data.get(*k).is_some()))
    {
        repo::enqueue(
            &state.pool,
            actor,
            "content.governance",
            json!({"policy_id":r.id,"actor_id":actor.id}),
            Some(&format!("governance:{}:{}", r.id, r.version)),
        )
        .await?;
    }
    Ok(())
}
async fn process_governance(state: &AppState, job: &Job) -> ApiResult<Value> {
    let _actor_id = job
        .payload
        .get("actor_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .ok_or_else(|| ApiError::bad_request("Governance job has no actor"))?;
    let actor = auth::actor_for_job(state, job).await?;
    let policy_id = job
        .payload
        .get("policy_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .ok_or_else(|| ApiError::bad_request("Governance job has no policy"))?;
    let policy = repo::raw_get(&state.pool, policy_id).await?;
    if !actor.is_admin() && !(policy.kind == "folder" && actor.can_manage(&policy)) {
        return Err(ApiError::forbidden(
            "Governance requires an active administrator or folder manager",
        ));
    }
    let scope = if matches!(policy.kind.as_str(), "file" | "folder") {
        json!({"resource_ids":[policy.id]})
    } else {
        policy.data.clone()
    };
    let objects = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE kind IN ('file','folder') ORDER BY id",
    )
    .fetch_all(&state.pool)
    .await
    .map_err(err)?;
    let policies=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind IN ('retention','legal_hold','archive') AND deleted_at IS NULL AND data->>'active'='true'").fetch_all(&state.pool).await.map_err(err)?;
    let mut applied = 0u64;
    let mut revoked = 0u64;
    for object in objects {
        let mut ids = vec![object.id];
        let mut parent = object.parent_id;
        while let Some(id) = parent {
            if ids.contains(&id) || ids.len() > 100 {
                return Err(ApiError::conflict("Invalid content hierarchy"));
            }
            ids.push(id);
            parent = sqlx::query_scalar::<_, Option<Uuid>>(
                "SELECT parent_id FROM resources WHERE id=$1",
            )
            .bind(id)
            .fetch_optional(&state.pool)
            .await
            .map_err(err)?
            .flatten();
        }
        if !policy::targets(&scope, &ids, &object) {
            continue;
        }
        let matching = policies
            .iter()
            .filter(|p| policy::targets(&p.data, &ids, &object))
            .collect::<Vec<_>>();
        let ancestor_data =
            sqlx::query_scalar::<_, Value>("SELECT data FROM resources WHERE id=ANY($1)")
                .bind(&ids)
                .fetch_all(&state.pool)
                .await
                .map_err(err)?;
        let archived = ancestor_data
            .iter()
            .any(|d| d.get("archived").and_then(Value::as_bool) == Some(true))
            || matching.iter().any(|p| p.kind == "archive");
        if archived {
            let links=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind='share' AND deleted_at IS NULL AND data->>'resource_id'=$1 AND COALESCE(data->>'revoked','false')<>'true'").bind(object.id.to_string()).fetch_all(&state.pool).await.map_err(err)?;
            for link in links {
                let mut data = link.data.clone();
                data["revoked"] = json!(true);
                data["revoked_reason"] = json!("archived");
                repo::update(
                    &state.pool,
                    &actor,
                    link.id,
                    UpdateResource {
                        name: None,
                        parent_id: None,
                        visibility: None,
                        acl: None,
                        data: Some(data),
                        expected_version: link.version,
                    },
                )
                .await?;
                revoked += 1;
            }
        }
        if object.kind == "file" {
            let (retain, hold) = policy::effective_governance(state, &object).await?;
            if let Some(versions) = object.data.get("versions").and_then(Value::as_array) {
                for v in versions {
                    apply_version_governance(state, &object, v).await?;
                }
            }
            let current = repo::raw_get(&state.pool, object.id).await?;
            if current.deleted_at.is_none() {
                let mut data = current.data.clone();
                data["effective_governance"] = json!({"retain_until":retain,"legal_hold":hold});
                data["governance_enforcement"] = json!(if storage::storage_governance() {
                    "storage_and_application"
                } else {
                    "application"
                });
                repo::update(
                    &state.pool,
                    &actor,
                    current.id,
                    UpdateResource {
                        name: None,
                        parent_id: None,
                        visibility: None,
                        acl: None,
                        data: Some(data),
                        expected_version: current.version,
                    },
                )
                .await?;
            }
        }
        applied += 1;
    }
    repo::audit(&state.pool,&actor,"governance.applied",Some(policy_id),json!({"resources":applied,"shares_revoked":revoked,"enforcement":if storage::storage_governance(){"storage_and_application"}else{"application"}})).await?;
    Ok(
        json!({"policy_id":policy_id,"resources":applied,"shares_revoked":revoked,"enforcement":if storage::storage_governance(){"storage_and_application"}else{"application"}}),
    )
}

/// A copied file may reference the same immutable object version. Storage holds
/// are the union of every logical reference, including trashed retained content.
async fn apply_version_governance(
    state: &AppState,
    resource: &Resource,
    v: &Value,
) -> ApiResult<()> {
    if !storage::storage_governance() {
        return Ok(());
    }
    static LOCK: std::sync::OnceLock<tokio::sync::Mutex<()>> = std::sync::OnceLock::new();
    let _guard = LOCK
        .get_or_init(|| tokio::sync::Mutex::new(()))
        .lock()
        .await;
    let key = version_key(v)?;
    let version = v.get("s3_version_id").and_then(Value::as_str);
    let (mut retain, mut hold) = policy::effective_governance(state, resource).await?;
    let references = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE kind='file' AND id<>$1 AND data->'versions' @> $2::jsonb",
    )
    .bind(resource.id)
    .bind(json!([{"storage_key":key}]))
    .fetch_all(&state.pool)
    .await
    .map_err(err)?;
    for other in references {
        if !other.data["versions"].as_array().is_some_and(|a| {
            a.iter().any(|x| {
                x.get("storage_key").and_then(Value::as_str) == Some(key)
                    && x.get("s3_version_id").and_then(Value::as_str) == version
            })
        }) {
            continue;
        }
        let (other_retain, other_hold) = policy::effective_governance(state, &other).await?;
        hold |= other_hold;
        if other_retain
            .as_ref()
            .and_then(|d| DateTime::parse_from_rfc3339(d).ok())
            > retain
                .as_ref()
                .and_then(|d| DateTime::parse_from_rfc3339(d).ok())
        {
            retain = other_retain;
        }
    }
    storage::object_governance(state, key, version, retain.as_deref(), Some(hold)).await
}

async fn revoke_resource_shares(state: &AppState, actor: &Actor, id: Uuid) -> ApiResult<u64> {
    let links=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind='share' AND deleted_at IS NULL AND data->>'resource_id'=$1 AND COALESCE(data->>'revoked','false')<>'true'").bind(id.to_string()).fetch_all(&state.pool).await.map_err(err)?;
    let mut count = 0;
    for link in links {
        let mut data = link.data.clone();
        data["revoked"] = json!(true);
        data["revoked_reason"] = json!("archived");
        repo::update(
            &state.pool,
            actor,
            link.id,
            UpdateResource {
                name: None,
                parent_id: None,
                visibility: None,
                acl: None,
                data: Some(data),
                expected_version: link.version,
            },
        )
        .await?;
        count += 1;
    }
    Ok(count)
}

async fn clamav_version(address: &str) -> ApiResult<String> {
    let result = async {
        let mut socket = tokio::net::TcpStream::connect(address).await.map_err(err)?;
        socket.write_all(b"zVERSION\0").await.map_err(err)?;
        let mut buf = vec![0u8; 4096];
        let n = socket.read(&mut buf).await.map_err(err)?;
        let text = String::from_utf8_lossy(&buf[..n])
            .trim_matches('\0')
            .trim()
            .to_owned();
        if text.starts_with("ClamAV ") {
            Ok(text)
        } else {
            Err(ApiError::unavailable("ClamAV version unavailable"))
        }
    };
    tokio::time::timeout(std::time::Duration::from_secs(5), result)
        .await
        .map_err(|_| ApiError::unavailable("ClamAV version timed out"))?
}
async fn detect_download_volume(state: &AppState, actor: &Actor) -> ApiResult<()> {
    let threshold = std::env::var("BLUEPRINT_DOWNLOAD_ALERT_THRESHOLD")
        .ok()
        .and_then(|v| v.parse::<i64>().ok())
        .unwrap_or(100)
        .max(1);
    let count:i64=sqlx::query_scalar("SELECT count(DISTINCT resource_id) FROM audit_log WHERE actor_id=$1 AND action='file.download' AND created_at>now()-interval '5 minutes'").bind(actor.id).fetch_one(&state.pool).await.map_err(err)?;
    if count < threshold {
        return Ok(());
    }
    let mut tx = state.pool.begin().await.map_err(err)?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,0))")
        .bind(format!("download-volume:{}", actor.id))
        .execute(&mut *tx)
        .await
        .map_err(err)?;
    let exists:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM resources WHERE kind='alert' AND data->>'rule'='download_volume' AND owner_id=$1 AND created_at>now()-interval '5 minutes')").bind(actor.id).fetch_one(&mut *tx).await.map_err(err)?;
    if exists {
        tx.commit().await.map_err(err)?;
        return Ok(());
    }
    let data = json!({"rule":"download_volume","type":"anomalous_download","status":"open","severity":"medium","actor_id":actor.id,"distinct_files":count,"threshold":threshold,"window_minutes":5,"detection_method":"fixed_threshold"});
    let resource=sqlx::query_as::<_,Resource>("INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document) VALUES($1,'alert','High download volume detected',$2,'private','{}',$3,'High download volume detected') RETURNING *").bind(Uuid::new_v4()).bind(actor.id).bind(data).fetch_one(&mut *tx).await.map_err(err)?;
    sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,1,$2,$3,'security_detection')").bind(resource.id).bind(actor.id).bind(serde_json::to_value(&resource).map_err(err)?).execute(&mut *tx).await.map_err(err)?;
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'security.download_volume',$2,$3)").bind(actor.id).bind(resource.id).bind(json!({"distinct_files":count,"threshold":threshold})).execute(&mut *tx).await.map_err(err)?;
    tx.commit().await.map_err(err)?;
    Ok(())
}

pub async fn replace_file_bytes(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    bytes: &[u8],
    expected_version: i64,
) -> ApiResult<Resource> {
    crate::core::module_enabled(state, "files").await?;
    let actor = auth::reload_actor_constraints(&state.pool, actor).await?;
    let r = file(state, &actor, id).await?;
    if !actor.can_write(&r) {
        return Err(ApiError::forbidden("File is read-only"));
    }
    authorize_action(state, &actor, &r, "update").await?;
    if expected_version != r.version {
        return Err(ApiError::conflict("File changed; reload before saving"));
    }
    if bytes.len() as u64 > storage::limit() {
        return Err(ApiError::bad_request(
            "File exceeds configured upload limit",
        ));
    }
    let mime = r
        .data
        .get("mime")
        .and_then(Value::as_str)
        .unwrap_or("application/octet-stream");
    let (key, s3_version) = storage::save_bytes(state, bytes, mime).await?;
    let mut data = r.data.clone();
    let next = data
        .get("versions")
        .and_then(Value::as_array)
        .and_then(|a| {
            a.iter()
                .filter_map(|v| v.get("number").and_then(Value::as_i64))
                .max()
        })
        .unwrap_or(0)
        + 1;
    let v = fresh_version(next, &key, mime, bytes.len() as u64, s3_version);
    data["versions"]
        .as_array_mut()
        .ok_or_else(|| ApiError::internal("Missing file version list"))?
        .push(v);
    data["size"] = json!(bytes.len());
    data["sha256"] = json!(key);
    data["storage_key"] = json!(key);
    data["current_version"] = json!(next);
    data["scan"] = json!({"status":"pending"});
    data["processing"] = json!({"status":"queued"});
    data.as_object_mut().unwrap().remove("preview");
    data.as_object_mut().unwrap().remove("extracted_text");
    let updated = repo::update(
        &state.pool,
        &actor,
        id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(data),
            expected_version,
        },
    )
    .await?;
    queue(state, &actor, &updated).await?;
    if let Err(error) = crate::workflows::emit_event(state, "file.updated", &updated).await {
        tracing::warn!(error=%error,"File event scheduling failed");
    }
    Ok(updated)
}

async fn preview_policy(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let r = file(&state, &actor, id).await?;
    authorize_action(&state, &actor, &r, "preview").await?;
    let rules = policy::preview_rules(&state, &actor, &r).await?;
    Ok(Json(
        json!({"watermark":rules.watermark,"prevent_print":rules.prevent_print,"watermark_label":if rules.watermark{Some(watermark::label(&actor))}else{None}}),
    ))
}
