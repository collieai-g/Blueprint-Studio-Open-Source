//! Company records, calendars, forms, documents, signing and collaborative content.
//!
//! This module stores business state in the same versioned resource repository as
//! the rest of the workspace. External services are optional, explicit adapters;
//! absence or failure is never represented as a successful operation.

mod calendar;
mod collaboration;
mod documents;
mod forms;
mod records;
mod wopi;

pub(crate) use collaboration::update_note_from_mcp;

use crate::{Actor, ApiError, ApiResult, AppState, Job, Resource, repo};
use axum::{
    Router,
    extract::DefaultBodyLimit,
    routing::{get, post},
};
use serde_json::{Value, json};

pub fn router() -> Router<AppState> {
    Router::new()
        .merge(wopi::router())
        .route(
            "/tables/{id}/records",
            get(records::list_records).post(records::create_record),
        )
        .route(
            "/tables/{id}/views",
            get(records::list_views).post(records::save_view),
        )
        .route("/forms/{id}/publish", post(forms::publish))
        .route(
            "/forms/{id}/submit",
            post(forms::authenticated_submit).layer(DefaultBodyLimit::max(32 * 1024 * 1024)),
        )
        .route("/forms/{id}/submissions", get(forms::submissions))
        .route("/calendar/ics", get(calendar::export_ics))
        .route(
            "/calendar/events/{id}/resolve",
            post(calendar::resolve_conflict),
        )
        .route(
            "/calendar/connections",
            get(calendar::connections).post(calendar::connect),
        )
        .route("/calendar/connections/{id}/sync", post(calendar::sync_now))
        .route(
            "/calendar/connections/{id}/disconnect",
            post(calendar::disconnect),
        )
        .route("/documents/generate", post(documents::generate))
        .route(
            "/sign/requests",
            get(documents::list_sign_requests).post(documents::create_sign_request),
        )
        .route("/sign/requests/{id}", get(documents::get_sign_request))
        .route(
            "/sign/requests/{id}/refresh",
            post(documents::refresh_sign_request),
        )
        .route(
            "/sign/requests/{id}/cancel",
            post(documents::cancel_sign_request),
        )
        .route(
            "/sign/requests/{id}/download",
            get(documents::download_signed),
        )
        .route(
            "/sign/requests/{id}/evidence",
            get(documents::download_evidence),
        )
        .route("/collaboration/{id}/ws", get(collaboration::upgrade))
}

pub fn public_router() -> Router<AppState> {
    Router::new()
        .merge(wopi::public_router())
        .route(
            "/forms/{id}",
            get(forms::public_form)
                .post(forms::public_submit)
                .layer(DefaultBodyLimit::max(32 * 1024 * 1024)),
        )
        .route("/calendar/oauth/{provider}", get(calendar::oauth_callback))
        .route("/sign/webhook", post(documents::sign_webhook))
}

/// Preserve server-managed state omitted from redacted editor responses, and
/// compute derived values before the shared validation/persistence boundary.
pub async fn prepare_data(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &mut Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    data_object(data)?;
    if let Some(old) = previous {
        let keys: &[&str] = match kind {
            "form" => &[
                "publish_token_hash",
                "published",
                "expires_at",
                "public_url",
            ],
            "event" => &["external", "source", "sync_conflict"],
            "note" | "canvas" => &["crdt_state"],
            "connector" if old.data.get("type").and_then(Value::as_str) == Some("calendar") => &[
                "type",
                "tokens_encrypted",
                "oauth_state_hash",
                "oauth_state_expires",
                "provider",
                "owner_user_id",
                "sync_cursor",
                "last_sync_at",
                "next_sync_at",
                "status",
                "last_error",
            ],
            _ => &[],
        };
        for key in keys {
            if let Some(existing) = old.data.get(key) {
                if let Some(proposed) = data.get(key) {
                    if proposed != existing {
                        return Err(ApiError::bad_request(format!(
                            "{key} is managed by its dedicated endpoint."
                        )));
                    }
                } else {
                    data[*key] = existing.clone();
                }
            } else if data.get(key).is_some_and(|value| !value.is_null()) {
                return Err(ApiError::bad_request(format!(
                    "{key} is managed by its dedicated endpoint."
                )));
            }
        }
    } else {
        let keys: &[&str] = match kind {
            "event" => &["external", "source", "sync_conflict"],
            "note" | "canvas" => &["crdt_state"],
            _ => &[],
        };
        if keys
            .iter()
            .any(|key| data.get(key).is_some_and(|value| !value.is_null()))
        {
            return Err(ApiError::bad_request(
                "Provider and collaboration state is managed by its dedicated endpoint.",
            ));
        }
    }
    if kind == "record" {
        let table = crate::repo::get(
            &state.pool,
            actor,
            parse_id(data.get("table_id"), "table_id")?,
        )
        .await?;
        if table.kind != "table" {
            return Err(ApiError::bad_request("table_id must reference a table."));
        }
        ensure_write(actor, &table)?;
        data["values"] = records::normalize_values(
            &records::fields(&table.data)?,
            data.get("values").unwrap_or(&json!({})),
            true,
        )?;
    }
    if kind == "workday" {
        records::normalize_workday(data, previous)?;
    }
    Ok(())
}

pub async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    match kind {
        "table" | "record" | "task" | "workday" | "event" | "note" | "canvas" | "hub" => {
            records::validate(state, actor, kind, data, previous).await
        }
        "form" | "submission" => forms::validate(state, actor, kind, data, previous).await,
        "sign_request" | "sign_template" => {
            documents::validate(state, actor, kind, data, previous).await
        }
        "connector" if data.get("type").and_then(Value::as_str) == Some("calendar") => {
            if let Some(old) = previous {
                for key in [
                    "tokens_encrypted",
                    "oauth_state_hash",
                    "oauth_state_expires",
                    "provider",
                    "owner_user_id",
                    "sync_cursor",
                ] {
                    if old.data.get(key) != data.get(key) {
                        return Err(ApiError::bad_request(
                            "Calendar credentials and sync state are managed by the calendar connection endpoints.",
                        ));
                    }
                }
            } else {
                return Err(ApiError::bad_request(
                    "Use the calendar connection endpoint to authorize a calendar.",
                ));
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

pub async fn process_job(state: &AppState, job: &Job) -> ApiResult<Value> {
    match job.kind.as_str() {
        "business.calendar.sync" => calendar::process_sync(state, job).await,
        "business.calendar.push" => calendar::process_push(state, job).await,
        "business.form.materialize" => forms::materialize(state, job).await,
        "business.sign.create" => documents::process_sign_create(state, job).await,
        "business.sign.refresh" => documents::process_sign_refresh(state, job).await,
        _ => Err(ApiError::bad_request("Unsupported business job type.")),
    }
}

/// Called after ordinary API resource mutations. Import adapters use the low-level
/// repository so their own synchronizations do not recursively trigger exports.
pub async fn after_change(state: &AppState, actor: &Actor, resource: &Resource) -> ApiResult<()> {
    if resource.kind == "event" {
        calendar::queue_exports(state, actor, resource).await?;
    }
    Ok(())
}

/// The worker invokes this bounded scan periodically to recover scheduled syncs
/// after restarts. Due times are advanced before queueing to prevent queue floods.
pub async fn schedule_due_jobs(state: &AppState) -> ApiResult<()> {
    if crate::core::module_enabled(state, "calendar").await.is_ok() {
        calendar::schedule_due(state).await?;
    }
    if crate::core::module_enabled(state, "sign").await.is_ok() {
        documents::schedule_due(state).await?;
    }
    Ok(())
}

pub fn redact_resource(resource: &mut Resource) {
    if let Some(data) = resource.data.as_object_mut() {
        for key in [
            "tokens_encrypted",
            "oauth_state_hash",
            "oauth_state_expires",
            "publish_token_hash",
            "webhook_secret",
        ] {
            data.remove(key);
        }
    }
}

fn ensure_write(actor: &Actor, resource: &Resource) -> ApiResult<()> {
    if actor.can_write(resource) {
        Ok(())
    } else {
        Err(ApiError::forbidden(
            "You do not have permission to change this resource.",
        ))
    }
}

fn parse_id(value: Option<&Value>, field: &str) -> ApiResult<uuid::Uuid> {
    value
        .and_then(Value::as_str)
        .and_then(|s| uuid::Uuid::parse_str(s).ok())
        .ok_or_else(|| ApiError::bad_request(&format!("{field} must be a UUID.")))
}

fn data_object(value: &Value) -> ApiResult<&serde_json::Map<String, Value>> {
    value
        .as_object()
        .ok_or_else(|| ApiError::bad_request("Resource data must be an object."))
}

fn modification(resource: &Resource, data: Value) -> crate::UpdateResource {
    crate::UpdateResource {
        name: None,
        parent_id: None,
        visibility: None,
        acl: None,
        data: Some(data),
        expected_version: resource.version,
    }
}

fn provider_error(label: &str) -> ApiError {
    ApiError::unavailable(&format!(
        "{label} could not complete the request. Check the integration settings and job details."
    ))
}

async fn get_resource(state: &AppState, actor: &Actor, id: uuid::Uuid) -> ApiResult<Resource> {
    let resource = repo::get(&state.pool, actor, id).await?;
    crate::core::ensure_module(state, &resource.kind).await?;
    crate::content::authorize_action(state, actor, &resource, "read").await?;
    Ok(resource)
}

async fn list_resources(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    query: Option<&str>,
) -> ApiResult<Vec<Resource>> {
    crate::core::ensure_module(state, kind).await?;
    let rows = repo::list(&state.pool, actor, kind, query).await?;
    let mut result = Vec::new();
    for row in rows {
        if crate::content::authorize_action(state, actor, &row, "read")
            .await
            .is_ok()
        {
            result.push(row);
        }
    }
    Ok(result)
}

/// Filter in PostgreSQL before applying the per-query bound. A busy unrelated
/// table must not hide records or submissions from the requested parent.
async fn related_resources(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    field: &str,
    id: uuid::Uuid,
    query: Option<&str>,
) -> ApiResult<Vec<Resource>> {
    crate::core::ensure_module(state, kind).await?;
    actor.require_scope(&format!("{}:read", crate::scope_for(kind)))?;
    let query = query.unwrap_or("").trim();
    if query.chars().count() > 500 {
        return Err(ApiError::bad_request("Search query is too long."));
    }
    let pattern = format!(
        "%{}%",
        query
            .replace('\\', "\\\\")
            .replace('%', "\\%")
            .replace('_', "\\_")
    );
    let rows = sqlx::query_as::<_, Resource>(
        "SELECT id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at \
         FROM resources WHERE kind=$1 AND data->>$2=$3 AND deleted_at IS NULL \
         AND ($4='' OR search_document ILIKE $5 ESCAPE '\\') ORDER BY updated_at DESC LIMIT 5000",
    )
    .bind(kind).bind(field).bind(id.to_string()).bind(query).bind(pattern)
    .fetch_all(&state.pool).await?;
    let mut visible = Vec::new();
    for resource in rows {
        if actor.can_read(&resource)
            && repo::check_ancestors(&state.pool, actor, &resource, false)
                .await
                .is_ok()
            && crate::content::authorize_action(state, actor, &resource, "read")
                .await
                .is_ok()
        {
            visible.push(resource);
        }
    }
    Ok(visible)
}

async fn create_resource(
    state: &AppState,
    actor: &Actor,
    input: crate::NewResource,
) -> ApiResult<Resource> {
    crate::core::ensure_module(state, &input.kind).await?;
    if let Some(parent_id) = input.parent_id {
        let parent = get_resource(state, actor, parent_id).await?;
        crate::content::authorize_action(state, actor, &parent, "update").await?;
    }
    repo::create(&state.pool, actor, input).await
}

async fn update_resource(
    state: &AppState,
    actor: &Actor,
    id: uuid::Uuid,
    input: crate::UpdateResource,
) -> ApiResult<Resource> {
    let resource = get_resource(state, actor, id).await?;
    crate::content::authorize_action(state, actor, &resource, "update").await?;
    repo::update(&state.pool, actor, id, input).await
}
