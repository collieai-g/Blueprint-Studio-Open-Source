use crate::*;
use axum::{
    Extension, Json, Router,
    extract::{DefaultBodyLimit, Path, Query, State},
    http::StatusCode,
    middleware,
    routing::{delete, get, post},
};
use sqlx::Row;
use tower_http::{services::ServeDir, trace::TraceLayer};

pub fn default_modules() -> Value {
    json!({"files":true,"collections":true,"knowledge":true,"notes":true,"canvas":true,"databases":true,"tasks":true,"workdays":true,"calendar":true,"forms":true,"workflows":true,"integrations":true,"sign":true,"documents":true,"security":true,"governance":true})
}
pub fn module_for(kind: &str) -> &str {
    match kind {
        "file" | "folder" | "link" | "comment" | "share" | "upload_session" => "files",
        "collection" => "collections",
        "note" => "notes",
        "canvas" => "canvas",
        "hub" => "knowledge",
        "table" | "record" => "databases",
        "task" => "tasks",
        "workday" => "workdays",
        "event" | "calendar_connection" => "calendar",
        "form" | "submission" => "forms",
        "workflow" | "workflow_run" | "workflow_step" => "workflows",
        "connector" | "agent" => "integrations",
        "sign_request" | "sign_template" => "sign",
        "document_template" => "documents",
        "classification" | "alert" => "security",
        "retention" | "legal_hold" | "archive" | "report" => "governance",
        _ => "identity",
    }
}
pub async fn module_enabled(state: &AppState, module: &str) -> ApiResult<()> {
    if module == "identity" {
        return Ok(());
    }
    let value: Value =
        sqlx::query_scalar("SELECT modules FROM company_settings WHERE singleton=true")
            .fetch_one(&state.pool)
            .await?;
    if value[module].as_bool() == Some(false) {
        Err(ApiError::forbidden(format!(
            "The {module} module is disabled"
        )))
    } else {
        Ok(())
    }
}
pub async fn ensure_module(state: &AppState, kind: &str) -> ApiResult<()> {
    module_enabled(state, module_for(kind)).await
}
async fn module_guard(
    State(state): State<AppState>,
    req: axum::extract::Request,
    next: middleware::Next,
) -> axum::response::Response {
    use axum::response::IntoResponse;
    let route = req
        .extensions()
        .get::<axum::extract::OriginalUri>()
        .map(|u| u.0.path())
        .unwrap_or_else(|| req.uri().path());
    let segment = route
        .trim_start_matches("/api/v1/")
        .trim_start_matches("public/")
        .trim_start_matches('/')
        .split('/')
        .next()
        .unwrap_or("");
    let module = match segment {
        "files" | "shares" | "uploads" | "office" => Some("files"),
        "tables" => Some("databases"),
        "forms" => Some("forms"),
        "calendar" => Some("calendar"),
        "documents" => Some("documents"),
        "sign" if !route.ends_with("/webhook") => Some("sign"),
        "workflows" | "runs" => Some("workflows"),
        "agents" | "mcp" | "connectors" => Some("integrations"),
        _ => None,
    };
    if let Some(module) = module {
        if let Err(e) = module_enabled(&state, module).await {
            return e.into_response();
        }
    }
    next.run(req).await
}
pub fn router(state: AppState) -> Router {
    let protected = Router::new()
        .merge(auth::router())
        .merge(content::router())
        .merge(business::router())
        .merge(workflows::router())
        .route("/resources/{kind}", get(list_resources))
        .route("/resources", post(create_resource))
        .route(
            "/resource/{id}",
            get(get_resource)
                .patch(update_resource)
                .delete(delete_resource),
        )
        .route("/resource/{id}/history", get(history))
        .route("/resource/{id}/restore", post(restore_resource))
        .route("/resource/{id}/purge", delete(purge_resource))
        .route("/trash", get(trash))
        .route("/search", get(search))
        .route("/settings", get(settings).patch(update_settings))
        .route("/audit", get(audit))
        .route("/jobs", get(jobs))
        .route("/jobs/{id}", get(job))
        .route("/jobs/{id}/cancel", post(cancel_job))
        .route("/dashboard", get(dashboard))
        .route("/openapi.json", get(openapi))
        .route("/export", get(export))
        .route("/scim/v2/ServiceProviderConfig", get(scim_config))
        .route("/scim/v2/Users", get(scim_users).post(scim_create))
        .route(
            "/scim/v2/Users/{id}",
            get(scim_user)
                .patch(scim_update)
                .put(scim_replace)
                .delete(scim_delete),
        )
        .route("/scim/v2/Groups", get(scim_groups).post(scim_group_create))
        .route(
            "/scim/v2/Groups/{id}",
            get(scim_group)
                .patch(scim_group_update)
                .delete(scim_group_delete),
        )
        .route_layer(middleware::from_fn_with_state(state.clone(), module_guard))
        .route_layer(middleware::from_fn_with_state(
            state.clone(),
            auth::require_auth,
        ));
    let public = content::public_router()
        .merge(business::public_router())
        .merge(workflows::public_router())
        .layer(middleware::from_fn_with_state(state.clone(), module_guard));
    let api = protected
        .merge(auth::public_router())
        .nest("/public", public);
    let ui = ServeDir::new(&state.config.ui_dir).not_found_service(
        tower_http::services::ServeFile::new(state.config.ui_dir.join("index.html")),
    );
    Router::new().nest("/api/v1",api).route("/health",get(health)).route("/api/health",get(health))
        .fallback_service(ui).layer(DefaultBodyLimit::max(8*1024*1024)).layer(middleware::from_fn(security_headers)).layer(middleware::from_fn_with_state(state.clone(),auth::csrf)).layer(TraceLayer::new_for_http().make_span_with(|request:&axum::http::Request<axum::body::Body>|tracing::info_span!("http_request",method=%request.method(),path=%request.uri().path()))).with_state(state)
}
async fn security_headers(
    req: axum::extract::Request,
    next: middleware::Next,
) -> axum::response::Response {
    let private_api = req.uri().path().starts_with("/api/");
    let mut response = next.run(req).await;
    let headers = response.headers_mut();
    headers
        .entry("x-content-type-options")
        .or_insert(axum::http::HeaderValue::from_static("nosniff"));
    headers
        .entry("referrer-policy")
        .or_insert(axum::http::HeaderValue::from_static("no-referrer"));
    if private_api {
        headers.insert(
            "cache-control",
            axum::http::HeaderValue::from_static("no-store"),
        );
    }
    response
}
async fn health(State(state): State<AppState>) -> ApiResult<Json<Value>> {
    sqlx::query("SELECT 1").execute(&state.pool).await?;
    Ok(Json(
        json!({"status":"ok","database":"ready","version":env!("CARGO_PKG_VERSION")}),
    ))
}
#[derive(Deserialize)]
struct SearchQuery {
    q: Option<String>,
}
fn redact(mut r: Resource) -> Resource {
    workflows::redact_resource(&mut r);
    business::redact_resource(&mut r);
    redact_values(&mut r.data);
    r
}
fn redact_values(value: &mut Value) {
    match value {
        Value::Object(o) => {
            let keys: Vec<_> = o
                .keys()
                .filter(|k| {
                    k.ends_with("_encrypted")
                        || matches!(
                            k.as_str(),
                            "access_token"
                                | "refresh_token"
                                | "client_secret"
                                | "password"
                                | "password_hash"
                                | "token_hash"
                                | "api_key"
                                | "callback_secret"
                        )
                })
                .cloned()
                .collect();
            for k in keys {
                o.remove(&k);
            }
            for v in o.values_mut() {
                redact_values(v);
            }
        }
        Value::Array(a) => {
            for v in a {
                redact_values(v)
            }
        }
        _ => {}
    }
}
async fn list_resources(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(kind): Path<String>,
    Query(query): Query<SearchQuery>,
) -> ApiResult<Json<Vec<Resource>>> {
    module_enabled(&state, module_for(&kind)).await?;
    let rows = repo::list(&state.pool, &actor, &kind, query.q.as_deref()).await?;
    let mut result = Vec::new();
    for r in rows {
        if content::authorize_action(&state, &actor, &r, "read")
            .await
            .is_ok()
        {
            result.push(redact(r));
        }
    }
    Ok(Json(result))
}
async fn get_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Resource>> {
    let r = repo::get(&state.pool, &actor, id).await?;
    module_enabled(&state, module_for(&r.kind)).await?;
    content::authorize_action(&state, &actor, &r, "read").await?;
    Ok(Json(redact(r)))
}
async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    old: Option<&Resource>,
) -> ApiResult<()> {
    content::validate(state, actor, kind, data, old).await?;
    business::validate(state, actor, kind, data, old).await?;
    workflows::validate(state, actor, kind, data, old).await?;
    Ok(())
}
async fn changed(state: &AppState, actor: &Actor, r: &Resource, event: &str) {
    if let Err(e) = content::after_write(state, actor, r).await {
        tracing::warn!(resource_id=%r.id,error=%e,"post-change content policy scheduling failed");
    }
    if let Err(e) = business::after_change(state, actor, r).await {
        tracing::warn!(resource_id=%r.id,error=%e,"post-change calendar scheduling failed");
    }
    if !matches!(
        r.kind.as_str(),
        "workflow_run" | "workflow_step" | "notification" | "alert" | "report"
    ) {
        if let Err(e) = workflows::emit_event(state, event, r).await {
            tracing::warn!(resource_id=%r.id,error=%e,"post-change workflow scheduling failed");
        }
    }
}
async fn create_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(mut input): Json<NewResource>,
) -> ApiResult<(StatusCode, Json<Resource>)> {
    if input.kind == "record" {
        let table_id = input.data["table_id"]
            .as_str()
            .and_then(|v| v.parse::<Uuid>().ok())
            .ok_or_else(|| ApiError::bad_request("Record table_id is required"))?;
        if input.parent_id.is_some_and(|id| id != table_id) {
            return Err(ApiError::bad_request("A record must belong to its table"));
        }
        input.parent_id = Some(table_id);
    }
    module_enabled(&state, module_for(&input.kind)).await?;
    if let Some(parent) = input.parent_id {
        let parent = repo::get(&state.pool, &actor, parent).await?;
        content::authorize_action(
            &state,
            &actor,
            &parent,
            if matches!(input.kind.as_str(), "comment" | "task") {
                "comment"
            } else {
                "update"
            },
        )
        .await?;
    }
    business::prepare_data(&state, &actor, &input.kind, &mut input.data, None).await?;
    workflows::prepare_data(&state, &input.kind, &mut input.data, None).await?;
    validate(&state, &actor, &input.kind, &input.data, None).await?;
    let r = repo::create(&state.pool, &actor, input).await?;
    changed(&state, &actor, &r, &format!("{}.created", r.kind)).await;
    Ok((StatusCode::CREATED, Json(redact(r))))
}
async fn update_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(mut input): Json<UpdateResource>,
) -> ApiResult<Json<Resource>> {
    let old = repo::get(&state.pool, &actor, id).await?;
    module_enabled(&state, module_for(&old.kind)).await?;
    content::authorize_action(&state, &actor, &old, "update").await?;
    if matches!(old.kind.as_str(), "file" | "folder" | "link")
        && input
            .parent_id
            .is_some_and(|parent| Some(parent) != old.parent_id)
    {
        return Err(ApiError::bad_request(
            "Use the file move endpoint so inherited governance is preserved",
        ));
    }
    if old.kind == "record"
        && (input.parent_id.is_some_and(|p| Some(p) != old.parent_id)
            || input
                .data
                .as_ref()
                .is_some_and(|d| d["table_id"] != old.data["table_id"]))
    {
        return Err(ApiError::bad_request(
            "A record cannot be reassigned to another table",
        ));
    }
    if let Some(data) = &mut input.data {
        business::prepare_data(&state, &actor, &old.kind, data, Some(&old)).await?;
        workflows::prepare_data(&state, &old.kind, data, Some(&old)).await?;
        validate(&state, &actor, &old.kind, data, Some(&old)).await?;
    }
    let r = repo::update(&state.pool, &actor, id, input).await?;
    let event =
        if r.kind == "task" && r.data["status"] == "completed" && old.data["status"] != "completed"
        {
            "task.completed".into()
        } else {
            format!("{}.updated", r.kind)
        };
    changed(&state, &actor, &r, &event).await;
    Ok(Json(redact(r)))
}
#[derive(Deserialize)]
struct VersionQuery {
    expected_version: i64,
}
async fn delete_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Query(q): Query<VersionQuery>,
) -> ApiResult<Json<Resource>> {
    let old = repo::get(&state.pool, &actor, id).await?;
    module_enabled(&state, module_for(&old.kind)).await?;
    content::authorize_action(&state, &actor, &old, "delete").await?;
    let r = repo::soft_delete(&state.pool, &actor, id, q.expected_version).await?;
    changed(&state, &actor, &r, &format!("{}.deleted", r.kind)).await;
    Ok(Json(redact(r)))
}
#[derive(Deserialize)]
struct VersionInput {
    expected_version: i64,
}
async fn restore_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(q): Json<VersionInput>,
) -> ApiResult<Json<Resource>> {
    let old = repo::raw_get(&state.pool, id).await?;
    if !actor.can_manage(&old) {
        return Err(ApiError::forbidden("Cannot restore this resource"));
    }
    module_enabled(&state, module_for(&old.kind)).await?;
    content::authorize_action(&state, &actor, &old, "restore").await?;
    let r = repo::restore(&state.pool, &actor, id, q.expected_version).await?;
    Ok(Json(redact(r)))
}
async fn purge_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Query(q): Query<VersionQuery>,
) -> ApiResult<Json<Value>> {
    let old = repo::raw_get(&state.pool, id).await?;
    if !actor.can_manage(&old) {
        return Err(ApiError::forbidden(
            "Cannot permanently delete this resource",
        ));
    }
    module_enabled(&state, module_for(&old.kind)).await?;
    if scope_for(&old.kind) == "admin" && !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    if old.deleted_at.is_none() {
        return Err(ApiError::conflict("Move the resource to trash first"));
    }
    content::authorize_action(&state, &actor, &old, "purge").await?;
    let children: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM resources WHERE parent_id=$1")
        .bind(id)
        .fetch_one(&state.pool)
        .await?;
    if children > 0 {
        return Err(ApiError::conflict(
            "Empty the folder before permanent deletion",
        ));
    }
    content::prepare_purge(&state, &actor, &old).await?;
    let mut tx = state.pool.begin().await?;
    let locked:Option<i64>=sqlx::query_scalar("SELECT version FROM resources WHERE id=$1 AND version=$2 AND deleted_at IS NOT NULL FOR UPDATE").bind(id).bind(q.expected_version).fetch_optional(&mut *tx).await?;
    if locked.is_none() {
        return Err(ApiError::conflict("Resource changed"));
    }
    sqlx::query("DELETE FROM resource_history WHERE resource_id=$1")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    sqlx::query("DELETE FROM resources WHERE id=$1")
        .bind(id)
        .execute(&mut *tx)
        .await?;
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'resource.purge',$2,$3)").bind(actor.id).bind(id).bind(json!({"kind":old.kind,"version":q.expected_version})).execute(&mut *tx).await?;
    tx.commit().await?;
    Ok(Json(json!({"ok":true})))
}
async fn history(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Vec<Value>>> {
    let r = repo::get(&state.pool, &actor, id).await?;
    ensure_module(&state, &r.kind).await?;
    content::authorize_action(&state, &actor, &r, "read").await?;
    let mut h = repo::history(&state.pool, &actor, id).await?;
    for entry in &mut h {
        if let Ok(r) = serde_json::from_value::<Resource>(entry["snapshot"].clone()) {
            entry["snapshot"] = serde_json::to_value(redact(r))?;
        }
        redact_values(entry);
    }
    Ok(Json(h))
}
async fn trash(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Vec<Resource>>> {
    let rows=sqlx::query_as::<_,Resource>("SELECT id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at FROM resources WHERE deleted_at IS NOT NULL AND (owner_id=$1 OR $2) ORDER BY deleted_at DESC").bind(actor.id).bind(actor.is_admin()).fetch_all(&state.pool).await?;
    let mut visible = Vec::new();
    for r in rows {
        if !actor.can_read(&r)
            || !actor.can_manage(&r)
            || ensure_module(&state, &r.kind).await.is_err()
        {
            continue;
        }
        // A trashed ancestor is expected here, but its ACL still applies.
        let mut current = r.parent_id;
        let mut seen = std::collections::HashSet::from([r.id]);
        let mut allowed = true;
        let mut ancestor_actor = actor.clone();
        ancestor_actor.resource_ids = None;
        while let Some(id) = current {
            if !seen.insert(id) || seen.len() > 64 {
                allowed = false;
                break;
            }
            let parent = repo::raw_get(&state.pool, id).await?;
            if !ancestor_actor.can_read(&parent) {
                allowed = false;
                break;
            }
            current = parent.parent_id;
        }
        if allowed
            && content::authorize_action(&state, &actor, &r, "read")
                .await
                .is_ok()
        {
            visible.push(redact(r));
            if visible.len() == 1000 {
                break;
            }
        }
    }
    Ok(Json(visible))
}
async fn search(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Query(q): Query<SearchQuery>,
) -> ApiResult<Json<Vec<Resource>>> {
    let term = q.q.unwrap_or_default();
    if term.trim().is_empty() {
        return Ok(Json(vec![]));
    }
    let mut all = Vec::new();
    for kind in repo::KINDS {
        if actor.scope_allows(&format!("{}:read", scope_for(kind)))
            && module_enabled(&state, module_for(kind)).await.is_ok()
        {
            for r in repo::list(&state.pool, &actor, kind, Some(&term)).await? {
                if content::authorize_action(&state, &actor, &r, "read")
                    .await
                    .is_ok()
                {
                    all.push(redact(r));
                }
            }
        }
    }
    all.sort_by_key(|r| std::cmp::Reverse(r.updated_at));
    all.truncate(500);
    Ok(Json(all))
}

async fn settings(
    State(state): State<AppState>,
    Extension(_actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    let row = sqlx::query("SELECT company,modules FROM company_settings WHERE singleton=true")
        .fetch_one(&state.pool)
        .await?;
    let mut services = serde_json::Map::new();
    for name in [
        "gotenberg",
        "tika",
        "clamav",
        "documenso",
        "collabora",
        "keycloak",
        "smtp",
    ] {
        services.insert(
            name.into(),
            json!({"configured":state.config.services.contains_key(name)}),
        );
    }
    services.insert(
        "s3".into(),
        json!({"configured":std::env::var("BLUEPRINT_S3_ENDPOINT").is_ok_and(|s|!s.is_empty())}),
    );
    services.insert("google_calendar".into(),json!({"configured":std::env::var("BLUEPRINT_GOOGLE_CLIENT_ID").is_ok_and(|s|!s.is_empty())&&std::env::var("BLUEPRINT_GOOGLE_CLIENT_SECRET").is_ok_and(|s|!s.is_empty())}));
    services.insert("microsoft_calendar".into(),json!({"configured":std::env::var("BLUEPRINT_MICROSOFT_CLIENT_ID").is_ok_and(|s|!s.is_empty())&&std::env::var("BLUEPRINT_MICROSOFT_CLIENT_SECRET").is_ok_and(|s|!s.is_empty())}));
    Ok(Json(
        json!({"company":row.get::<Value,_>("company"),"modules":row.get::<Value,_>("modules"),"services":services}),
    ))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SettingsInput {
    company: Option<Value>,
    modules: Option<Value>,
}
async fn update_settings(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<SettingsInput>,
) -> ApiResult<Json<Value>> {
    if !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    if let Some(company) = &input.company {
        if !company.is_object()
            || company["name"]
                .as_str()
                .is_none_or(|s| s.trim().is_empty() || s.len() > 255)
        {
            return Err(ApiError::bad_request("Company name is required"));
        }
        if let Some(tz) = company["timezone"].as_str() {
            tz.parse::<chrono_tz::Tz>()
                .map_err(|_| ApiError::bad_request("Unknown timezone"))?;
        }
    }
    if let Some(modules) = &input.modules {
        let map = modules
            .as_object()
            .ok_or_else(|| ApiError::bad_request("Modules must be an object"))?;
        let allowed = default_modules();
        for (name, enabled) in map {
            if allowed.get(name).is_none() || !enabled.is_boolean() {
                return Err(ApiError::bad_request(
                    "Unknown module or invalid enabled value",
                ));
            }
        }
    }
    sqlx::query("UPDATE company_settings SET company=CASE WHEN $1::jsonb IS NULL THEN company ELSE company||$1 END,modules=CASE WHEN $2::jsonb IS NULL THEN modules ELSE modules||$2 END,updated_at=now() WHERE singleton=true").bind(input.company).bind(input.modules).execute(&state.pool).await?;
    repo::audit(&state.pool, &actor, "settings.update", None, json!({})).await?;
    settings(State(state), Extension(actor)).await
}
async fn audit(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    if !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    let rows=sqlx::query("SELECT a.id,a.actor_id,u.name AS actor_name,a.action,a.resource_id,a.details,a.created_at FROM audit_log a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 1000").fetch_all(&state.pool).await?;
    Ok(Json(Value::Array(rows.into_iter().map(|r|json!({"id":r.get::<i64,_>("id"),"actor_id":r.get::<Option<Uuid>,_>("actor_id"),"actor_name":r.get::<Option<String>,_>("actor_name"),"action":r.get::<String,_>("action"),"resource_id":r.get::<Option<Uuid>,_>("resource_id"),"details":r.get::<Value,_>("details"),"created_at":r.get::<DateTime<Utc>,_>("created_at")})).collect())))
}
async fn jobs(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Vec<Job>>> {
    let rows=sqlx::query_as::<_,Job>("SELECT id,owner_id,kind,status,payload,result,error,attempts,created_at,updated_at FROM jobs WHERE (owner_id=$1 OR $2) AND ($3::text IS NULL OR payload->'actor_constraint'->>'token_id'=$3) ORDER BY created_at DESC LIMIT 500").bind(actor.id).bind(actor.is_admin()).bind(actor.token_id.map(|id|id.to_string())).fetch_all(&state.pool).await?;
    Ok(Json(
        rows.into_iter()
            .map(|mut j| {
                redact_values(&mut j.payload);
                redact_values(&mut j.result);
                j
            })
            .collect(),
    ))
}
async fn job(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Job>> {
    let mut j=sqlx::query_as::<_,Job>("SELECT id,owner_id,kind,status,payload,result,error,attempts,created_at,updated_at FROM jobs WHERE id=$1 AND (owner_id=$2 OR $3) AND ($4::text IS NULL OR payload->'actor_constraint'->>'token_id'=$4)").bind(id).bind(actor.id).bind(actor.is_admin()).bind(actor.token_id.map(|id|id.to_string())).fetch_one(&state.pool).await?;
    redact_values(&mut j.payload);
    redact_values(&mut j.result);
    Ok(Json(j))
}
async fn cancel_job(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let result=sqlx::query("UPDATE jobs SET status=CASE WHEN status='queued' THEN 'cancelled' ELSE 'cancel_requested' END,updated_at=now() WHERE id=$1 AND (owner_id=$2 OR $3) AND ($4::text IS NULL OR payload->'actor_constraint'->>'token_id'=$4) AND status IN('queued','running','waiting')").bind(id).bind(actor.id).bind(actor.is_admin()).bind(actor.token_id.map(|id|id.to_string())).execute(&state.pool).await?;
    if result.rows_affected() == 0 {
        return Err(ApiError::conflict("Job cannot be cancelled"));
    }
    repo::audit(
        &state.pool,
        &actor,
        "job.cancel_request",
        None,
        json!({"job_id":id}),
    )
    .await?;
    Ok(Json(
        json!({"accepted":true,"note":"A running external action may require its provider to acknowledge cancellation"}),
    ))
}
async fn dashboard(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    let mut counts = serde_json::Map::new();
    for kind in [
        "file",
        "folder",
        "task",
        "event",
        "table",
        "workflow",
        "workflow_run",
        "agent",
    ] {
        let count = if actor.scope_allows(&format!("{}:read", scope_for(kind)))
            && ensure_module(&state, kind).await.is_ok()
        {
            repo::list(&state.pool, &actor, kind, None).await?.len()
        } else {
            0
        };
        counts.insert(kind.into(), json!(count));
    }
    Ok(Json(json!({"counts":counts})))
}
async fn export(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    if !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    let mut resources = Vec::new();
    for kind in repo::KINDS {
        if ensure_module(&state, kind).await.is_err() {
            continue;
        }
        resources.extend(
            repo::list(&state.pool, &actor, kind, None)
                .await?
                .into_iter()
                .map(redact),
        );
    }
    repo::audit(
        &state.pool,
        &actor,
        "export.metadata",
        None,
        json!({"count":resources.len()}),
    )
    .await?;
    Ok(Json(
        json!({"schema":"blueprint-workspace-export-v2","created_at":Utc::now(),"resources":resources,"includes_original_files":false,"note":"Use the backup command for a full local-data backup; external storage requires its own version-preserving backup"}),
    ))
}

async fn openapi() -> Json<Value> {
    Json(crate::openapi::document())
}

fn admin(actor: &Actor) -> ApiResult<()> {
    if actor.is_admin() {
        Ok(())
    } else {
        Err(ApiError::forbidden("Administrator access required"))
    }
}
fn scim_user_value(r: &sqlx::postgres::PgRow) -> Value {
    json!({"schemas":["urn:ietf:params:scim:schemas:core:2.0:User"],"id":r.get::<Uuid,_>("id"),"userName":r.get::<String,_>("email"),"displayName":r.get::<String,_>("name"),"active":r.get::<bool,_>("active"),"emails":[{"value":r.get::<String,_>("email"),"primary":true}],"meta":{"resourceType":"User"}})
}
async fn scim_config(Extension(actor): Extension<Actor>) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    Ok(Json(
        json!({"schemas":["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],"patch":{"supported":true},"bulk":{"supported":false},"filter":{"supported":true,"maxResults":200},"changePassword":{"supported":false},"sort":{"supported":false},"etag":{"supported":false},"authenticationSchemes":[{"type":"oauthbearertoken","name":"Blueprint scoped service token","primary":true}]}),
    ))
}
#[derive(Deserialize)]
struct ScimQuery {
    filter: Option<String>,
    #[serde(rename = "startIndex")]
    start_index: Option<usize>,
    count: Option<usize>,
}
async fn scim_users(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Query(q): Query<ScimQuery>,
) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    let filter = if let Some(f) = q.filter {
        let name = f
            .strip_prefix("userName eq \"")
            .and_then(|v| v.strip_suffix('"'))
            .ok_or_else(|| ApiError::bad_request("Supported SCIM filter: userName eq \"email\""))?;
        Some(name.to_lowercase())
    } else {
        None
    };
    let rows=sqlx::query("SELECT id,email,name,active FROM users WHERE ($1::text IS NULL OR lower(email)=$1) ORDER BY created_at").bind(filter).fetch_all(&state.pool).await?;
    let total = rows.len();
    let start = q.start_index.unwrap_or(1).max(1);
    let items: Vec<_> = rows
        .iter()
        .skip(start - 1)
        .take(q.count.unwrap_or(100).min(200))
        .map(scim_user_value)
        .collect();
    Ok(Json(
        json!({"schemas":["urn:ietf:params:scim:api:messages:2.0:ListResponse"],"totalResults":total,"startIndex":start,"itemsPerPage":items.len(),"Resources":items}),
    ))
}
async fn scim_user(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    let r = sqlx::query("SELECT id,email,name,active FROM users WHERE id=$1")
        .bind(id)
        .fetch_one(&state.pool)
        .await?;
    Ok(Json(scim_user_value(&r)))
}
async fn scim_create(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(data): Json<Value>,
) -> ApiResult<(StatusCode, Json<Value>)> {
    admin(&actor)?;
    let email = data["userName"]
        .as_str()
        .ok_or_else(|| ApiError::bad_request("userName required"))?;
    if !email.contains('@') {
        return Err(ApiError::bad_request("userName must be an email"));
    }
    let name = data["displayName"].as_str().unwrap_or(email);
    let id = Uuid::new_v4();
    let hash = auth::password_hash(&auth::random_token())?;
    sqlx::query("INSERT INTO users(id,email,name,password_hash,role,active) VALUES($1,$2,$3,$4,'member',$5)").bind(id).bind(email.to_lowercase()).bind(name).bind(hash).bind(data["active"].as_bool().unwrap_or(true)).execute(&state.pool).await?;
    repo::audit(
        &state.pool,
        &actor,
        "scim.user_create",
        None,
        json!({"user_id":id}),
    )
    .await?;
    let Json(user) = scim_user(State(state), Extension(actor), Path(id)).await?;
    Ok((StatusCode::CREATED, Json(user)))
}
async fn scim_apply(state: &AppState, actor: &Actor, id: Uuid, data: Value) -> ApiResult<()> {
    admin(actor)?;
    let old = sqlx::query("SELECT role FROM users WHERE id=$1")
        .bind(id)
        .fetch_one(&state.pool)
        .await?;
    if old.get::<String, _>("role") == "owner" {
        return Err(ApiError::forbidden(
            "SCIM cannot change the installation owner",
        ));
    }
    let mut tx = state.pool.begin().await?;
    sqlx::query("UPDATE users SET name=COALESCE($2,name),active=COALESCE($3,active),updated_at=now() WHERE id=$1").bind(id).bind(data["displayName"].as_str()).bind(data["active"].as_bool()).execute(&mut *tx).await?;
    if data["active"] == false {
        sqlx::query("DELETE FROM sessions WHERE user_id=$1")
            .bind(id)
            .execute(&mut *tx)
            .await?;
        sqlx::query("UPDATE api_tokens SET revoked_at=now() WHERE user_id=$1")
            .bind(id)
            .execute(&mut *tx)
            .await?;
        sqlx::query("DELETE FROM auth_challenges WHERE user_id=$1")
            .bind(id)
            .execute(&mut *tx)
            .await?;
    }
    tx.commit().await?;
    repo::audit(
        &state.pool,
        actor,
        "scim.user_update",
        None,
        json!({"user_id":id}),
    )
    .await
}
async fn scim_replace(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(data): Json<Value>,
) -> ApiResult<Json<Value>> {
    scim_apply(&state, &actor, id, data).await?;
    scim_user(State(state), Extension(actor), Path(id)).await
}
async fn scim_update(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(data): Json<Value>,
) -> ApiResult<Json<Value>> {
    let mut change = json!({});
    for op in data["Operations"]
        .as_array()
        .ok_or_else(|| ApiError::bad_request("Operations required"))?
    {
        match op["path"].as_str() {
            Some("active") => change["active"] = op["value"].clone(),
            Some("displayName") => change["displayName"] = op["value"].clone(),
            None => {
                if let Some(map) = op["value"].as_object() {
                    for (k, v) in map {
                        change[k] = v.clone();
                    }
                }
            }
            _ => return Err(ApiError::bad_request("Unsupported SCIM patch path")),
        }
    }
    scim_apply(&state, &actor, id, change).await?;
    scim_user(State(state), Extension(actor), Path(id)).await
}
async fn scim_delete(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<StatusCode> {
    scim_apply(&state, &actor, id, json!({"active":false})).await?;
    Ok(StatusCode::NO_CONTENT)
}
const SCIM_GROUP_SCHEMA: &str = "urn:ietf:params:scim:schemas:core:2.0:Group";
const SCIM_PATCH_SCHEMA: &str = "urn:ietf:params:scim:api:messages:2.0:PatchOp";
fn scim_schema(data: &Value, expected: &str) -> ApiResult<()> {
    let schemas = data["schemas"]
        .as_array()
        .ok_or_else(|| ApiError::bad_request("SCIM schemas array is required"))?;
    if schemas.len() != 1 || schemas[0].as_str() != Some(expected) {
        return Err(ApiError::bad_request("Unsupported SCIM schema"));
    }
    Ok(())
}
async fn group_members(pool: &PgPool, id: Uuid) -> ApiResult<Vec<Value>> {
    let rows = sqlx::query("SELECT id,name FROM users WHERE groups ? $1 ORDER BY id")
        .bind(id.to_string())
        .fetch_all(pool)
        .await?;
    Ok(rows.into_iter().map(|row|json!({"value":row.get::<Uuid,_>("id").to_string(),"display":row.get::<String,_>("name")})).collect())
}
async fn scim_group_value(state: &AppState, r: &Resource) -> ApiResult<Value> {
    Ok(
        json!({"schemas":[SCIM_GROUP_SCHEMA],"id":r.id,"displayName":r.name,"members":group_members(&state.pool,r.id).await?,"meta":{"resourceType":"Group"}}),
    )
}
async fn scim_groups(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    let rows = repo::list(&state.pool, &actor, "group", None).await?;
    let mut groups = Vec::new();
    for row in rows {
        groups.push(scim_group_value(&state, &row).await?);
    }
    Ok(Json(
        json!({"schemas":["urn:ietf:params:scim:api:messages:2.0:ListResponse"],"totalResults":groups.len(),"startIndex":1,"itemsPerPage":groups.len(),"Resources":groups}),
    ))
}
async fn scim_group(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    let r = repo::get(&state.pool, &actor, id).await?;
    if r.kind != "group" || r.deleted_at.is_some() {
        return Err(ApiError::not_found("Group not found"));
    }
    Ok(Json(scim_group_value(&state, &r).await?))
}
fn member_ids(value: &Value) -> ApiResult<Vec<Uuid>> {
    let items = value
        .as_array()
        .ok_or_else(|| ApiError::bad_request("members must be an array"))?;
    if items.len() > 10000 {
        return Err(ApiError::bad_request(
            "A group may contain at most 10000 members",
        ));
    }
    let mut ids = Vec::new();
    for item in items {
        let uid = item["value"]
            .as_str()
            .and_then(|v| Uuid::parse_str(v).ok())
            .ok_or_else(|| ApiError::bad_request("Group members require a user UUID value"))?;
        if !ids.contains(&uid) {
            ids.push(uid);
        }
    }
    Ok(ids)
}
async fn write_group_members(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    id: Uuid,
    ids: &[Uuid],
) -> ApiResult<()> {
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users WHERE id=ANY($1)")
        .bind(ids)
        .fetch_one(&mut **tx)
        .await?;
    if count != ids.len() as i64 {
        return Err(ApiError::bad_request("A group member does not exist"));
    }
    sqlx::query("UPDATE users SET groups=groups-$1,updated_at=now() WHERE groups ? $1")
        .bind(id.to_string())
        .execute(&mut **tx)
        .await?;
    if !ids.is_empty() {
        sqlx::query("UPDATE users SET groups=groups||jsonb_build_array($2::text),updated_at=now() WHERE id=ANY($1) AND NOT groups ? $2").bind(ids).bind(id.to_string()).execute(&mut **tx).await?;
    }
    Ok(())
}
async fn locked_group(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    actor: &Actor,
    id: Uuid,
) -> ApiResult<Resource> {
    let r=sqlx::query_as::<_,Resource>("SELECT id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at FROM resources WHERE id=$1 AND kind='group' AND deleted_at IS NULL FOR UPDATE").bind(id).fetch_optional(&mut **tx).await?.ok_or_else(||ApiError::not_found("Group not found"))?;
    if !actor.can_manage(&r) {
        return Err(ApiError::forbidden("Group management is not allowed"));
    }
    Ok(r)
}
async fn membership_audit(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    actor: &Actor,
    id: Uuid,
    ids: &[Uuid],
) -> ApiResult<()> {
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'group.membership',$2,$3)").bind(actor.id).bind(id).bind(json!({"member_ids":ids})).execute(&mut **tx).await?;
    Ok(())
}
async fn scim_group_create(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(data): Json<Value>,
) -> ApiResult<(StatusCode, Json<Value>)> {
    admin(&actor)?;
    scim_schema(&data, SCIM_GROUP_SCHEMA)?;
    let name = data["displayName"]
        .as_str()
        .ok_or_else(|| ApiError::bad_request("displayName is required"))?
        .trim();
    let members = member_ids(data.get("members").unwrap_or(&json!([])))?;
    repo::validate_base(name, "company", &json!({}), &json!({}))?;
    if actor.resource_ids.is_some() {
        return Err(ApiError::forbidden(
            "A resource-scoped token cannot create a new group",
        ));
    }
    let id = Uuid::new_v4();
    let mut tx = state.pool.begin().await?;
    let r=sqlx::query_as::<_,Resource>("INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document) VALUES($1,'group',$2,$3,'company','{}','{}',$2) RETURNING id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at").bind(id).bind(name).bind(actor.id).fetch_one(&mut *tx).await?;
    write_group_members(&mut tx, id, &members).await?;
    repo::snapshot(&mut tx, &actor, &r, "scim_create").await?;
    membership_audit(&mut tx, &actor, id, &members).await?;
    tx.commit().await?;
    Ok((
        StatusCode::CREATED,
        Json(scim_group_value(&state, &r).await?),
    ))
}
fn apply_members(action: &str, current: &mut Vec<Uuid>, incoming: Vec<Uuid>) -> ApiResult<()> {
    match action {
        "replace" => *current = incoming,
        "add" => {
            for id in incoming {
                if !current.contains(&id) {
                    current.push(id);
                }
            }
        }
        "remove" => current.retain(|id| !incoming.contains(id)),
        _ => return Err(ApiError::bad_request("Unsupported group patch operation")),
    }
    Ok(())
}
async fn scim_group_update(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(data): Json<Value>,
) -> ApiResult<Json<Value>> {
    admin(&actor)?;
    scim_schema(&data, SCIM_PATCH_SCHEMA)?;
    let operations = data["Operations"]
        .as_array()
        .ok_or_else(|| ApiError::bad_request("Operations array is required"))?;
    if operations.is_empty() || operations.len() > 1000 {
        return Err(ApiError::bad_request(
            "Operations must contain 1–1000 entries",
        ));
    }
    let mut tx = state.pool.begin().await?;
    let previous = locked_group(&mut tx, &actor, id).await?;
    repo::check_ancestors(&state.pool, &actor, &previous, true).await?;
    let mut members: Vec<Uuid> =
        sqlx::query_scalar("SELECT id FROM users WHERE groups ? $1 ORDER BY id")
            .bind(id.to_string())
            .fetch_all(&mut *tx)
            .await?;
    let mut name = previous.name.clone();
    for op in operations {
        let action = op["op"]
            .as_str()
            .ok_or_else(|| ApiError::bad_request("SCIM op is required"))?
            .to_lowercase();
        if !matches!(action.as_str(), "add" | "replace" | "remove") {
            return Err(ApiError::bad_request("Unsupported group patch operation"));
        }
        match op["path"].as_str() {
            Some("displayName") => {
                if action == "remove" {
                    return Err(ApiError::bad_request("displayName is required"));
                }
                name = op["value"]
                    .as_str()
                    .ok_or_else(|| ApiError::bad_request("displayName must be a string"))?
                    .trim()
                    .into();
            }
            Some("members") => {
                if action == "remove" && op.get("value").is_none() {
                    members.clear();
                } else {
                    apply_members(&action, &mut members, member_ids(&op["value"])?)?;
                }
            }
            Some(path) if path.starts_with("members[value eq \"") && path.ends_with("\"]") => {
                if action != "remove" {
                    return Err(ApiError::bad_request(
                        "Filtered members supports remove only",
                    ));
                }
                let raw = path
                    .strip_prefix("members[value eq \"")
                    .and_then(|v| v.strip_suffix("\"]"))
                    .unwrap();
                let uid = Uuid::parse_str(raw)
                    .map_err(|_| ApiError::bad_request("Invalid member filter"))?;
                members.retain(|id| *id != uid);
            }
            None if action != "remove" => {
                let value = op["value"]
                    .as_object()
                    .ok_or_else(|| ApiError::bad_request("SCIM value must be an object"))?;
                for (key, value) in value {
                    match key.as_str() {
                        "displayName" => {
                            name = value
                                .as_str()
                                .ok_or_else(|| {
                                    ApiError::bad_request("displayName must be a string")
                                })?
                                .trim()
                                .into()
                        }
                        "members" => apply_members(&action, &mut members, member_ids(value)?)?,
                        _ => return Err(ApiError::bad_request("Unsupported group attribute")),
                    }
                }
            }
            _ => return Err(ApiError::bad_request("Unsupported group patch path")),
        }
    }
    if members.len() > 10000 {
        return Err(ApiError::bad_request(
            "A group may contain at most 10000 members",
        ));
    }
    repo::validate_base(&name, &previous.visibility, &previous.acl, &previous.data)?;
    write_group_members(&mut tx, id, &members).await?;
    let r=sqlx::query_as::<_,Resource>("UPDATE resources SET name=$2,search_document=$2,version=version+1,updated_at=now() WHERE id=$1 RETURNING id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at").bind(id).bind(name).fetch_one(&mut *tx).await?;
    repo::snapshot(&mut tx, &actor, &r, "scim_update").await?;
    membership_audit(&mut tx, &actor, id, &members).await?;
    tx.commit().await?;
    Ok(Json(scim_group_value(&state, &r).await?))
}
async fn scim_group_delete(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<StatusCode> {
    admin(&actor)?;
    let mut tx = state.pool.begin().await?;
    let previous = locked_group(&mut tx, &actor, id).await?;
    repo::check_ancestors(&state.pool, &actor, &previous, true).await?;
    write_group_members(&mut tx, id, &[]).await?;
    let r=sqlx::query_as::<_,Resource>("UPDATE resources SET deleted_at=now(),version=version+1,updated_at=now() WHERE id=$1 RETURNING id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at").bind(id).fetch_one(&mut *tx).await?;
    repo::snapshot(&mut tx, &actor, &r, "scim_delete").await?;
    membership_audit(&mut tx, &actor, id, &[]).await?;
    tx.commit().await?;
    Ok(StatusCode::NO_CONTENT)
}
