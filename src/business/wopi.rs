//! Collabora's WOPI integration: authenticated launch, capability-protected file
//! operations, immutable saves and protocol locks. No editor success is simulated.

use axum::{
    Extension, Json, Router,
    body::{Body, Bytes},
    extract::{DefaultBodyLimit, Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
    routing::{get, post},
};
use base64::{
    Engine,
    engine::general_purpose::{STANDARD, URL_SAFE_NO_PAD},
};
use chrono::{Duration, Utc};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::Sha256;
use sqlx::Row;
use uuid::Uuid;

use crate::{Actor, ApiError, ApiResult, AppState, Resource, UpdateResource, auth, content, repo};

pub fn router() -> Router<AppState> {
    Router::new().route("/office/{id}/open", post(open))
}

pub fn public_router() -> Router<AppState> {
    Router::new()
        .route(
            "/wopi/files/{id}",
            get(check_file_info).post(file_operation),
        )
        .route("/wopi/files/{id}/contents", get(get_file).post(put_file))
        .layer(DefaultBodyLimit::max(max_size()))
}

fn max_size() -> usize {
    std::env::var("BLUEPRINT_OFFICE_MAX_BYTES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(64 * 1024 * 1024)
}

#[derive(Clone, Serialize, Deserialize)]
struct Claims {
    file_id: Uuid,
    user_id: Uuid,
    can_write: bool,
    initial_content_version: i64,
    expires: i64,
    nonce: Uuid,
}

#[derive(Deserialize)]
struct TokenQuery {
    access_token: String,
}

#[derive(Default, Deserialize)]
struct OpenInput {
    mode: Option<String>,
}

fn encode_claims(key: &[u8; 32], claims: &Claims) -> ApiResult<String> {
    let payload = URL_SAFE_NO_PAD.encode(serde_json::to_vec(claims)?);
    let mut mac = Hmac::<Sha256>::new_from_slice(key)
        .map_err(|_| ApiError::internal("Office token signer unavailable"))?;
    mac.update(payload.as_bytes());
    Ok(format!(
        "{payload}.{}",
        URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes())
    ))
}

fn decode_claims(key: &[u8; 32], token: &str, file_id: Uuid, now: i64) -> ApiResult<Claims> {
    if token.len() > 2048 {
        return Err(ApiError::unauthorized("Invalid Office capability"));
    }
    let (payload, signature) = token
        .split_once('.')
        .ok_or_else(|| ApiError::unauthorized("Invalid Office capability"))?;
    let signature = URL_SAFE_NO_PAD
        .decode(signature)
        .map_err(|_| ApiError::unauthorized("Invalid Office capability"))?;
    let mut mac = Hmac::<Sha256>::new_from_slice(key)
        .map_err(|_| ApiError::internal("Office token verifier unavailable"))?;
    mac.update(payload.as_bytes());
    mac.verify_slice(&signature)
        .map_err(|_| ApiError::unauthorized("Invalid Office capability signature"))?;
    let claims: Claims = serde_json::from_slice(
        &URL_SAFE_NO_PAD
            .decode(payload)
            .map_err(|_| ApiError::unauthorized("Invalid Office capability"))?,
    )
    .map_err(|_| ApiError::unauthorized("Invalid Office capability"))?;
    if claims.file_id != file_id || claims.expires <= now {
        return Err(ApiError::unauthorized(
            "Office capability expired or belongs to another file",
        ));
    }
    Ok(claims)
}

async fn mint(
    state: &AppState,
    actor: &Actor,
    file: &Resource,
    can_write: bool,
) -> ApiResult<(String, i64)> {
    let seconds = std::env::var("BLUEPRINT_WOPI_TOKEN_TTL_SECONDS")
        .ok()
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(3600)
        .clamp(60, 28800);
    let expires = Utc::now() + Duration::seconds(seconds);
    let claims = Claims {
        file_id: file.id,
        user_id: actor.id,
        can_write,
        initial_content_version: file.data["current_version"].as_i64().unwrap_or(1),
        expires: expires.timestamp(),
        nonce: Uuid::new_v4(),
    };
    let token = encode_claims(&state.config.secret_key, &claims)?;
    sqlx::query("INSERT INTO auth_challenges(token_hash,user_id,purpose,data,expires_at) VALUES($1,$2,'wopi',$3,$4)")
        .bind(auth::token_hash(&token)).bind(actor.id).bind(json!({"actor":actor,"file_id":file.id,"can_write":can_write,"nonce":claims.nonce})).bind(expires).execute(&state.pool).await?;
    Ok((token, expires.timestamp_millis()))
}

async fn authorize(
    state: &AppState,
    id: Uuid,
    token: &str,
    write: bool,
) -> ApiResult<(Actor, Resource, Claims)> {
    crate::core::ensure_module(state, "file").await?;
    let claims = decode_claims(&state.config.secret_key, token, id, Utc::now().timestamp())?;
    let stored:Option<Value>=sqlx::query_scalar("SELECT data FROM auth_challenges WHERE token_hash=$1 AND user_id=$2 AND purpose='wopi' AND expires_at>now()")
        .bind(auth::token_hash(token)).bind(claims.user_id).fetch_optional(&state.pool).await?;
    let stored =
        stored.ok_or_else(|| ApiError::unauthorized("Office capability was revoked or expired"))?;
    if stored["file_id"] != json!(id) || stored["nonce"] != json!(claims.nonce) {
        return Err(ApiError::unauthorized(
            "Office capability context did not match",
        ));
    }
    let original: Actor = serde_json::from_value(stored["actor"].clone())
        .map_err(|_| ApiError::unauthorized("Office identity is unavailable"))?;
    if original.id != claims.user_id {
        return Err(ApiError::unauthorized("Office identity did not match"));
    }
    let actor = auth::reload_actor_constraints(&state.pool, &original).await?;
    let file = repo::get(&state.pool, &actor, id).await?;
    if file.kind != "file" || file.deleted_at.is_some() {
        return Err(ApiError::not_found("File is unavailable"));
    }
    // WOPI sends original file bytes to the editor. A preview-only permission must
    // not become a route around download, malware or DLP restrictions.
    content::authorize_action(state, &actor, &file, "download").await?;
    if file.data["size"].as_u64().unwrap_or(0) > max_size() as u64 {
        return Err(ApiError::bad_request(
            "File exceeds the configured Office editing limit",
        ));
    }
    if write {
        if !claims.can_write || !actor.can_write(&file) {
            return Err(ApiError::forbidden("Office capability is read-only"));
        }
        content::authorize_action(state, &actor, &file, "update").await?;
    }
    Ok((actor, file, claims))
}

async fn open(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<OpenInput>,
) -> ApiResult<Json<Value>> {
    crate::core::ensure_module(&state, "file").await?;
    let actor = auth::reload_actor_constraints(&state.pool, &actor).await?;
    let file = repo::get(&state.pool, &actor, id).await?;
    if file.kind != "file" || file.deleted_at.is_some() {
        return Err(ApiError::not_found("File is unavailable"));
    }
    content::authorize_action(&state, &actor, &file, "download").await?;
    if file.data["size"].as_u64().unwrap_or(0) > max_size() as u64 {
        return Err(ApiError::bad_request(
            "File exceeds the configured Office editing limit",
        ));
    }
    let can_write = actor.can_write(&file)
        && content::authorize_action(&state, &actor, &file, "update")
            .await
            .is_ok();
    let requested = input
        .mode
        .as_deref()
        .unwrap_or(if can_write { "edit" } else { "view" });
    if !matches!(requested, "edit" | "view") {
        return Err(ApiError::bad_request("Office mode must be edit or view"));
    }
    if requested == "edit" && !can_write {
        return Err(ApiError::forbidden("File is read-only"));
    }
    let service = state.config.services.get("collabora").ok_or_else(|| {
        ApiError::unavailable(
            "Configure the self-hosted Collabora service to edit Office documents",
        )
    })?;
    let endpoint = format!("{}/hosting/discovery", service.trim_end_matches('/'));
    let response = state
        .http
        .get(endpoint)
        .send()
        .await
        .map_err(|_| ApiError::unavailable("Collabora discovery service is unavailable"))?;
    if !response.status().is_success()
        || response
            .content_length()
            .is_some_and(|n| n > 2 * 1024 * 1024)
    {
        return Err(ApiError::unavailable(
            "Collabora discovery could not be loaded",
        ));
    }
    let mut bytes = Vec::new();
    let mut stream = response.bytes_stream();
    use futures_util::StreamExt;
    while let Some(chunk) = stream.next().await {
        let chunk =
            chunk.map_err(|_| ApiError::unavailable("Collabora discovery could not be read"))?;
        if bytes.len() + chunk.len() > 2 * 1024 * 1024 {
            return Err(ApiError::unavailable(
                "Collabora discovery response is too large",
            ));
        }
        bytes.extend_from_slice(&chunk);
    }
    let extension = file
        .name
        .rsplit_once('.')
        .map(|(_, ext)| ext.to_ascii_lowercase())
        .ok_or_else(|| ApiError::bad_request("File has no Office extension"))?;
    let action = discovery_action(&bytes, &extension, requested)?;
    let public =
        std::env::var("BLUEPRINT_COLLABORA_PUBLIC_URL").unwrap_or_else(|_| service.clone());
    let mut action_url = rewrite_action_url(&action, &public)?;
    let wopi_src = format!("{}/api/v1/public/wopi/files/{id}", state.config.public_url);
    action_url
        .query_pairs_mut()
        .append_pair("WOPISrc", &wopi_src);
    let (token, ttl) = mint(&state, &actor, &file, requested == "edit").await?;
    repo::audit(
        &state.pool,
        &actor,
        "office.open",
        Some(id),
        json!({"mode":requested,"content_version":file.data["current_version"]}),
    )
    .await?;
    Ok(Json(
        json!({"action_url":action_url.as_str(),"launch_url":action_url.as_str(),"method":"POST","fields":{"access_token":token,"access_token_ttl":ttl.to_string()},"access_token_ttl":ttl,"wopi_src":wopi_src,"mode":requested,"filename":file.name}),
    ))
}

fn discovery_action(xml: &[u8], extension: &str, mode: &str) -> ApiResult<String> {
    use quick_xml::{Reader, events::Event};
    let mut reader = Reader::from_reader(xml);
    reader.config_mut().trim_text(true);
    loop {
        match reader.read_event() {
            Ok(Event::Start(event) | Event::Empty(event)) if event.name().as_ref() == b"action" => {
                let mut ext = None;
                let mut name = None;
                let mut url = None;
                for attribute in event.attributes() {
                    let attribute = attribute
                        .map_err(|_| ApiError::unavailable("Malformed Collabora discovery"))?;
                    let value = attribute
                        .decode_and_unescape_value(reader.decoder())
                        .map_err(|_| ApiError::unavailable("Malformed Collabora discovery"))?
                        .into_owned();
                    match attribute.key.as_ref() {
                        b"ext" => ext = Some(value),
                        b"name" => name = Some(value),
                        b"urlsrc" => url = Some(value),
                        _ => {}
                    }
                }
                if ext.as_deref() == Some(extension) && name.as_deref() == Some(mode) {
                    return url.ok_or_else(|| ApiError::unavailable("Collabora action has no URL"));
                }
            }
            Ok(Event::Eof) => break,
            Err(_) => return Err(ApiError::unavailable("Collabora discovery XML is invalid")),
            _ => {}
        }
    }
    Err(ApiError::bad_request(format!(
        "Configured Collabora service does not support {mode} for .{extension}"
    )))
}

fn rewrite_action_url(action: &str, public: &str) -> ApiResult<url::Url> {
    // Discovery may advertise a Docker hostname. Only its path/query are used;
    // the browser-facing origin comes from the deployment's explicit public URL.
    let action = url::Url::parse(action)
        .map_err(|_| ApiError::unavailable("Invalid Collabora discovery action URL"))?;
    let mut public = url::Url::parse(public)
        .map_err(|_| ApiError::unavailable("Invalid Collabora public URL"))?;
    if !matches!(public.scheme(), "http" | "https")
        || public.host_str().is_none()
        || !public.username().is_empty()
        || public.password().is_some()
    {
        return Err(ApiError::unavailable(
            "Collabora public URL must be an HTTP(S) origin",
        ));
    }
    let prefix = public.path().trim_end_matches('/').to_owned();
    public.set_path(&format!("{prefix}{}", action.path()));
    public.set_query(action.query());
    public.set_fragment(None);
    Ok(public)
}

fn content_version(file: &Resource) -> i64 {
    file.data["current_version"].as_i64().unwrap_or(1)
}
fn version(file: &Resource) -> String {
    format!(
        "{}-{}",
        content_version(file),
        file.data["sha256"].as_str().unwrap_or("unknown")
    )
}
fn header_value(headers: &HeaderMap, name: &str) -> ApiResult<String> {
    headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(str::to_owned)
        .ok_or_else(|| ApiError::bad_request(format!("{name} header is required")))
}
fn lock_id(headers: &HeaderMap, name: &str) -> ApiResult<String> {
    let value = header_value(headers, name)?;
    if value.is_empty() || value.len() > 1024 || !value.bytes().all(|b| (0x20..=0x7e).contains(&b))
    {
        return Err(ApiError::bad_request("Invalid WOPI lock identifier"));
    }
    Ok(value)
}
fn locked_conflict(current: &str, reason: &str) -> Response {
    let mut response = (StatusCode::CONFLICT, reason.to_owned()).into_response();
    if let Ok(value) = HeaderValue::from_str(current) {
        response.headers_mut().insert("X-WOPI-Lock", value);
    }
    if let Ok(value) = HeaderValue::from_str(reason) {
        response
            .headers_mut()
            .insert("X-WOPI-LockFailureReason", value);
    }
    response
}
fn no_store(mut response: Response) -> Response {
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    response
}

async fn check_file_info(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<TokenQuery>,
) -> ApiResult<Response> {
    let (actor, file, claims) = authorize(&state, id, &query.access_token, false).await?;
    let writable = claims.can_write
        && actor.can_write(&file)
        && content::authorize_action(&state, &actor, &file, "update")
            .await
            .is_ok();
    let mut info = json!({"BaseFileName":file.name,"OwnerId":file.owner_id.simple().to_string(),"Size":file.data["size"].as_u64().unwrap_or(0),"UserId":actor.id.simple().to_string(),"UserFriendlyName":actor.name,"Version":version(&file),"UserCanWrite":writable,"ReadOnly":!writable,"SupportsLocks":writable,"SupportsGetLock":true,"SupportsExtendedLockLength":true,"SupportsUpdate":writable,"SupportsRename":writable&&actor.can_manage(&file),"UserCanRename":writable&&actor.can_manage(&file),"UserCanNotWriteRelative":!writable||actor.resource_ids.is_some(),"PostMessageOrigin":url::Url::parse(&state.config.public_url).map(|u|u.origin().ascii_serialization()).unwrap_or_default(),"DisableExport":false,"DisablePrint":false});
    info["DisablePrint"] = json!(
        content::authorize_action(&state, &actor, &file, "print")
            .await
            .is_err()
    );
    if let Some(hash) = file.data["sha256"]
        .as_str()
        .and_then(|v| hex::decode(v).ok())
    {
        info["SHA256"] = json!(STANDARD.encode(hash));
    }
    Ok(no_store(Json(info).into_response()))
}

async fn get_file(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<TokenQuery>,
) -> ApiResult<Response> {
    let (actor, file, _) = authorize(&state, id, &query.access_token, false).await?;
    let bytes = content::read_file_bytes(&state, &actor, id).await?;
    let mut response = Response::new(Body::from(bytes));
    response.headers_mut().insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/octet-stream"),
    );
    if let Ok(value) = HeaderValue::from_str(&version(&file)) {
        response.headers_mut().insert("X-WOPI-ItemVersion", value);
    }
    Ok(no_store(response))
}

async fn lock_transaction(
    state: &AppState,
    id: Uuid,
) -> ApiResult<sqlx::Transaction<'_, sqlx::Postgres>> {
    let mut tx = state.pool.begin().await?;
    let key = i64::from_be_bytes(id.as_bytes()[..8].try_into().unwrap());
    sqlx::query("SELECT pg_advisory_xact_lock($1)")
        .bind(key)
        .execute(&mut *tx)
        .await?;
    Ok(tx)
}

async fn file_operation(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<TokenQuery>,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult<Response> {
    let operation = header_value(&headers, "X-WOPI-Override")?.to_ascii_uppercase();
    let write = operation != "GET_LOCK";
    let (actor, file, claims) = authorize(&state, id, &query.access_token, write).await?;
    if operation == "PUT_RELATIVE" {
        return put_relative(&state, &actor, &file, &claims, &headers, &body).await;
    }
    if operation == "RENAME_FILE" {
        return rename_file(&state, &actor, &file, &headers).await;
    }
    if !matches!(
        operation.as_str(),
        "LOCK" | "REFRESH_LOCK" | "UNLOCK" | "GET_LOCK"
    ) {
        return Err(ApiError::bad_request("Unsupported WOPI operation"));
    }
    let mut tx = lock_transaction(&state, id).await?;
    let existing=sqlx::query("SELECT lock_id,content_version FROM wopi_locks WHERE file_id=$1 AND expires_at>now() FOR UPDATE").bind(id).fetch_optional(&mut *tx).await?;
    let current = existing
        .as_ref()
        .map(|r| r.get::<String, _>("lock_id"))
        .unwrap_or_default();
    if operation == "GET_LOCK" {
        tx.commit().await?;
        let mut response = StatusCode::OK.into_response();
        response.headers_mut().insert(
            "X-WOPI-Lock",
            HeaderValue::from_str(&current)
                .map_err(|_| ApiError::internal("Invalid stored Office lock"))?,
        );
        return Ok(no_store(response));
    }
    let requested = lock_id(&headers, "X-WOPI-Lock")?;
    match operation.as_str() {
        "LOCK" => {
            let old = headers.get("X-WOPI-OldLock").and_then(|v| v.to_str().ok());
            if !current.is_empty() && current != requested && old != Some(current.as_str()) {
                return Ok(locked_conflict(
                    &current,
                    "File is locked by another editing session",
                ));
            }
            if old.is_some() && old != Some(current.as_str()) {
                return Ok(locked_conflict(&current, "Old lock did not match"));
            }
            sqlx::query("INSERT INTO wopi_locks(file_id,lock_id,content_version,expires_at) VALUES($1,$2,$3,now()+interval '30 minutes') ON CONFLICT(file_id) DO UPDATE SET lock_id=excluded.lock_id,content_version=CASE WHEN wopi_locks.expires_at>now() THEN wopi_locks.content_version ELSE excluded.content_version END,expires_at=excluded.expires_at,updated_at=now()")
                .bind(id).bind(&requested).bind(content_version(&file)).execute(&mut *tx).await?;
        }
        "REFRESH_LOCK" => {
            if current != requested || current.is_empty() {
                return Ok(locked_conflict(&current, "Lock did not match"));
            }
            sqlx::query("UPDATE wopi_locks SET expires_at=now()+interval '30 minutes',updated_at=now() WHERE file_id=$1").bind(id).execute(&mut *tx).await?;
        }
        "UNLOCK" => {
            if current != requested || current.is_empty() {
                return Ok(locked_conflict(&current, "Lock did not match"));
            }
            sqlx::query("DELETE FROM wopi_locks WHERE file_id=$1")
                .bind(id)
                .execute(&mut *tx)
                .await?;
        }
        _ => unreachable!(),
    }
    tx.commit().await?;
    repo::audit(
        &state.pool,
        &actor,
        &format!("office.{}", operation.to_ascii_lowercase()),
        Some(id),
        json!({}),
    )
    .await?;
    Ok(no_store(StatusCode::OK.into_response()))
}

async fn put_file(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<TokenQuery>,
    headers: HeaderMap,
    body: Bytes,
) -> ApiResult<Response> {
    if body.len() > max_size() {
        return Err(ApiError::bad_request(
            "Office save exceeds the configured size limit",
        ));
    }
    let (actor, _, _) = authorize(&state, id, &query.access_token, true).await?;
    let mut tx = lock_transaction(&state, id).await?;
    let lock=sqlx::query("SELECT lock_id,content_version FROM wopi_locks WHERE file_id=$1 AND expires_at>now() FOR UPDATE").bind(id).fetch_optional(&mut *tx).await?;
    let current = lock
        .as_ref()
        .map(|r| r.get::<String, _>("lock_id"))
        .unwrap_or_default();
    let supplied = headers
        .get("X-WOPI-Lock")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if current.is_empty() || current != supplied {
        return Ok(locked_conflict(
            &current,
            "A matching active Office lock is required before saving",
        ));
    }
    let file = repo::get(&state.pool, &actor, id).await?;
    let expected = lock.as_ref().unwrap().get::<i64, _>("content_version");
    if content_version(&file) != expected {
        return Ok(locked_conflict(
            &current,
            "File changed outside this editing session; reload before saving",
        ));
    }
    let saved = match content::replace_file_bytes(&state, &actor, id, &body, file.version).await {
        Ok(file) => file,
        Err(error) if error.status == StatusCode::CONFLICT => {
            return Ok(locked_conflict(
                &current,
                "File changed while saving; reload before retrying",
            ));
        }
        Err(error) => return Err(error),
    };
    sqlx::query("UPDATE wopi_locks SET content_version=$2,expires_at=now()+interval '30 minutes',updated_at=now() WHERE file_id=$1").bind(id).bind(content_version(&saved)).execute(&mut *tx).await?;
    tx.commit().await?;
    let mut response = StatusCode::OK.into_response();
    response.headers_mut().insert(
        "X-WOPI-ItemVersion",
        HeaderValue::from_str(&version(&saved))
            .map_err(|_| ApiError::internal("Invalid saved file version"))?,
    );
    Ok(no_store(response))
}

async fn put_relative(
    state: &AppState,
    actor: &Actor,
    file: &Resource,
    _claims: &Claims,
    headers: &HeaderMap,
    body: &[u8],
) -> ApiResult<Response> {
    if actor.resource_ids.is_some() {
        return Err(ApiError::forbidden(
            "A resource-limited Office capability cannot create another file",
        ));
    }
    let target = headers
        .get("X-WOPI-RelativeTarget")
        .or_else(|| headers.get("X-WOPI-SuggestedTarget"))
        .and_then(|v| v.to_str().ok())
        .ok_or_else(|| ApiError::bad_request("A relative target filename is required"))?;
    let target = if target.starts_with('.') {
        format!(
            "{}{}",
            file.name
                .rsplit_once('.')
                .map(|(stem, _)| stem)
                .unwrap_or(&file.name),
            target
        )
    } else {
        target.to_owned()
    };
    if target.is_empty() || target.len() > 255 || target.contains(['/', '\\', '\0', '\r', '\n']) {
        return Err(ApiError::bad_request("Invalid relative target filename"));
    }
    let collision:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM resources WHERE kind='file' AND parent_id IS NOT DISTINCT FROM $1 AND name=$2 AND deleted_at IS NULL)").bind(file.parent_id).bind(&target).fetch_one(&state.pool).await?;
    if collision {
        return Ok(locked_conflict(
            "",
            "A file with that name already exists; choose another filename",
        ));
    }
    let mime = file.data["mime"]
        .as_str()
        .unwrap_or("application/octet-stream");
    let saved = content::store_bytes(state, actor, &target, mime, body, file.parent_id).await?;
    let (token, _) = mint(state, actor, &saved, true).await?;
    let mut url = url::Url::parse(&format!(
        "{}/api/v1/public/wopi/files/{}",
        state.config.public_url, saved.id
    ))
    .map_err(|_| ApiError::internal("Invalid Office source URL"))?;
    url.query_pairs_mut().append_pair("access_token", &token);
    Ok(no_store(
        Json(json!({"Name":saved.name,"Url":url.as_str()})).into_response(),
    ))
}

async fn rename_file(
    state: &AppState,
    actor: &Actor,
    file: &Resource,
    headers: &HeaderMap,
) -> ApiResult<Response> {
    if !actor.can_manage(file) {
        return Err(ApiError::forbidden(
            "Only the file owner or administrator may rename it",
        ));
    }
    let mut tx = lock_transaction(state, file.id).await?;
    let current: Option<String> = sqlx::query_scalar(
        "SELECT lock_id FROM wopi_locks WHERE file_id=$1 AND expires_at>now() FOR UPDATE",
    )
    .bind(file.id)
    .fetch_optional(&mut *tx)
    .await?;
    if let Some(current) = current {
        if headers.get("X-WOPI-Lock").and_then(|v| v.to_str().ok()) != Some(current.as_str()) {
            return Ok(locked_conflict(
                &current,
                "A matching Office lock is required before renaming",
            ));
        }
    }
    let requested = header_value(headers, "X-WOPI-RequestedName")?;
    if requested.trim().is_empty()
        || requested.len() > 240
        || requested.contains(['/', '\\', '\0', '\r', '\n'])
    {
        return Err(ApiError::bad_request("Invalid filename"));
    }
    let extension = file.name.rsplit_once('.').map(|(_, ext)| ext).unwrap_or("");
    let name = if extension.is_empty() {
        requested.clone()
    } else {
        format!("{requested}.{extension}")
    };
    let updated = repo::update(
        &state.pool,
        actor,
        file.id,
        UpdateResource {
            name: Some(name),
            parent_id: None,
            visibility: None,
            acl: None,
            data: None,
            expected_version: file.version,
        },
    )
    .await?;
    tx.commit().await?;
    Ok(no_store(Json(json!({"Name":updated.name.rsplit_once('.').map(|(stem,_)|stem).unwrap_or(&updated.name)})).into_response()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use http_body_util::BodyExt;
    use tower::ServiceExt;

    #[test]
    fn signed_capability_is_bound_to_file_expiry_and_mode() {
        let key = [7; 32];
        let id = Uuid::new_v4();
        let claims = Claims {
            file_id: id,
            user_id: Uuid::new_v4(),
            can_write: false,
            initial_content_version: 1,
            expires: 2000,
            nonce: Uuid::new_v4(),
        };
        let token = encode_claims(&key, &claims).unwrap();
        assert!(!decode_claims(&key, &token, id, 1000).unwrap().can_write);
        assert!(decode_claims(&key, &token, Uuid::new_v4(), 1000).is_err());
        assert!(decode_claims(&key, &token, id, 2000).is_err());
        let mut altered = claims.clone();
        altered.can_write = true;
        let forged = format!(
            "{}.{}",
            URL_SAFE_NO_PAD.encode(serde_json::to_vec(&altered).unwrap()),
            token.split_once('.').unwrap().1
        );
        assert!(decode_claims(&key, &forged, id, 1000).is_err());
    }

    #[test]
    fn discovery_selects_exact_extension_and_mode_and_rewrites_internal_origin() {
        let xml=br#"<wopi-discovery><net-zone name="external-http"><app name="writer"><action ext="docx" name="view" urlsrc="http://collabora:9980/browser/view.html?"/><action ext="docx" name="edit" urlsrc="http://collabora:9980/browser/edit.html?lang=en&amp;"/></app></net-zone></wopi-discovery>"#;
        let action = discovery_action(xml, "docx", "edit").unwrap();
        assert_eq!(
            rewrite_action_url(&action, "https://office.example.test")
                .unwrap()
                .as_str(),
            "https://office.example.test/browser/edit.html?lang=en&"
        );
        assert!(discovery_action(xml, "unsupported", "edit").is_err());
        assert!(rewrite_action_url(&action, "https://user:password@office.example.test").is_err());
    }

    #[test]
    fn protocol_lock_conflicts_return_current_lock_without_changing_it() {
        let response = locked_conflict("active-session-lock", "Lock did not match");
        assert_eq!(response.status(), StatusCode::CONFLICT);
        assert_eq!(response.headers()["X-WOPI-Lock"], "active-session-lock");
    }

    async fn fixture() -> (AppState, Actor, Resource, tempfile::TempDir) {
        let directory = tempfile::tempdir().unwrap();
        let config = crate::Config {
            database_url: std::env::var("TEST_DATABASE_URL")
                .expect("Disposable TEST_DATABASE_URL is required"),
            data_dir: directory.path().to_owned(),
            bind: "127.0.0.1:0".into(),
            public_url: "http://localhost:8788".into(),
            setup_token: None,
            services: std::collections::HashMap::new(),
            secret_key: [19; 32],
            ui_dir: directory.path().join("ui"),
        };
        let state = AppState::connect(config).await.unwrap();
        let id = Uuid::new_v4();
        sqlx::query("INSERT INTO users(id,email,name,password_hash,role) VALUES($1,$2,'Office test','not-a-login-hash','owner')").bind(id).bind(format!("office-{id}@example.test")).execute(&state.pool).await.unwrap();
        let actor = auth::load_actor(&state.pool, id).await.unwrap();
        let doc = super::super::documents::docx_from_markdown("# Original document").unwrap();
        let file = content::store_bytes(
            &state,
            &actor,
            "office-test.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            &doc,
            None,
        )
        .await
        .unwrap();
        (state, actor, file, directory)
    }

    async fn request(
        state: &AppState,
        id: Uuid,
        token: &str,
        operation: Option<&str>,
        lock: Option<&str>,
        body: Vec<u8>,
        contents: bool,
    ) -> Response {
        let path = format!(
            "/api/v1/public/wopi/files/{}{}?access_token={token}",
            id,
            if contents { "/contents" } else { "" }
        );
        let mut request = axum::http::Request::builder()
            .method(if operation.is_some() || contents && !body.is_empty() {
                "POST"
            } else {
                "GET"
            })
            .uri(path);
        if let Some(operation) = operation {
            request = request.header("X-WOPI-Override", operation);
        }
        if let Some(lock) = lock {
            request = request.header("X-WOPI-Lock", lock);
        }
        crate::app(state.clone())
            .oneshot(request.body(Body::from(body)).unwrap())
            .await
            .unwrap()
    }

    #[tokio::test]
    #[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
    async fn wopi_lock_and_immutable_save_reject_wrong_lock_and_outside_changes() {
        let (state, actor, file, _directory) = fixture().await;
        let (token, _) = mint(&state, &actor, &file, true).await.unwrap();
        let response = request(&state, file.id, &token, None, None, vec![], false).await;
        assert_eq!(response.status(), StatusCode::OK);
        let info: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(info["UserCanWrite"], true);
        let response = request(
            &state,
            file.id,
            &token,
            Some("LOCK"),
            Some("office-lock-one"),
            vec![],
            false,
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let edited = super::super::documents::docx_from_markdown("# Edited and saved").unwrap();
        let response = request(
            &state,
            file.id,
            &token,
            Some("PUT"),
            Some("wrong-lock"),
            edited.clone(),
            true,
        )
        .await;
        assert_eq!(response.status(), StatusCode::CONFLICT);
        assert_eq!(response.headers()["X-WOPI-Lock"], "office-lock-one");
        let response = request(
            &state,
            file.id,
            &token,
            Some("PUT"),
            Some("office-lock-one"),
            edited.clone(),
            true,
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let saved = repo::raw_get(&state.pool, file.id).await.unwrap();
        assert_eq!(saved.data["versions"].as_array().unwrap().len(), 2);
        assert_eq!(
            content::read_file_bytes(&state, &actor, file.id)
                .await
                .unwrap(),
            edited
        );
        let external =
            super::super::documents::docx_from_markdown("# Uploaded outside Office").unwrap();
        content::replace_file_bytes(&state, &actor, file.id, &external, saved.version)
            .await
            .unwrap();
        let response = request(
            &state,
            file.id,
            &token,
            Some("PUT"),
            Some("office-lock-one"),
            edited,
            true,
        )
        .await;
        assert_eq!(response.status(), StatusCode::CONFLICT);
        assert_eq!(
            content::read_file_bytes(&state, &actor, file.id)
                .await
                .unwrap(),
            external
        );
        let response = request(
            &state,
            file.id,
            &token,
            Some("UNLOCK"),
            Some("office-lock-one"),
            vec![],
            false,
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
    }

    #[tokio::test]
    #[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
    async fn wopi_capability_cannot_escalate_and_rechecks_role_and_expiry() {
        let (state, actor, file, _directory) = fixture().await;
        let (read_token, _) = mint(&state, &actor, &file, false).await.unwrap();
        let response = request(
            &state,
            file.id,
            &read_token,
            Some("LOCK"),
            Some("cannot-write"),
            vec![],
            false,
        )
        .await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        let (edit_token, _) = mint(&state, &actor, &file, true).await.unwrap();
        sqlx::query("UPDATE users SET role='viewer' WHERE id=$1")
            .bind(actor.id)
            .execute(&state.pool)
            .await
            .unwrap();
        let response = request(&state, file.id, &edit_token, None, None, vec![], false).await;
        assert_eq!(response.status(), StatusCode::OK);
        let info: Value =
            serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes())
                .unwrap();
        assert_eq!(info["UserCanWrite"], false);
        let response = request(
            &state,
            file.id,
            &edit_token,
            Some("LOCK"),
            Some("revoked-write"),
            vec![],
            false,
        )
        .await;
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
        sqlx::query(
            "UPDATE auth_challenges SET expires_at=now()-interval '1 second' WHERE token_hash=$1",
        )
        .bind(auth::token_hash(&edit_token))
        .execute(&state.pool)
        .await
        .unwrap();
        let response = request(&state, file.id, &edit_token, None, None, vec![], false).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }
}
