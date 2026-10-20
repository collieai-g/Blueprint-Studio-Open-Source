use super::{ensure_write, modification, parse_id, provider_error};
use crate::{Actor, ApiError, ApiResult, AppState, Job, NewResource, Resource, repo};
use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Redirect, Response},
};
use chrono::{DateTime, Datelike, Duration, NaiveDate, TimeZone, Utc};
use rand::RngCore;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

fn provider_config(state: &AppState, provider: &str) -> ApiResult<(String, String)> {
    let prefix = match provider {
        "google" => "BLUEPRINT_GOOGLE",
        "microsoft" => "BLUEPRINT_MICROSOFT",
        _ => {
            return Err(ApiError::bad_request(
                "Calendar provider must be google or microsoft.",
            ));
        }
    };
    let id = state
        .config
        .services
        .get(&format!("{provider}_client_id"))
        .cloned()
        .or_else(|| std::env::var(format!("{prefix}_CLIENT_ID")).ok())
        .filter(|x| !x.is_empty())
        .ok_or_else(|| {
            ApiError::unavailable(format!(
                "Configure {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET to connect this calendar."
            ))
        })?;
    let secret = state
        .config
        .services
        .get(&format!("{provider}_client_secret"))
        .cloned()
        .or_else(|| std::env::var(format!("{prefix}_CLIENT_SECRET")).ok())
        .filter(|x| !x.is_empty())
        .ok_or_else(|| {
            ApiError::unavailable(format!(
                "Configure {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET to connect this calendar."
            ))
        })?;
    Ok((id, secret))
}
fn endpoint<'a>(state: &'a AppState, key: &str, fallback: &'a str) -> &'a str {
    state
        .config
        .services
        .get(key)
        .map(String::as_str)
        .unwrap_or(fallback)
}
fn callback_url(state: &AppState, provider: &str) -> String {
    format!(
        "{}/api/v1/public/calendar/oauth/{provider}",
        state.config.public_url.trim_end_matches('/')
    )
}
fn provider(resource: &Resource) -> ApiResult<&str> {
    resource
        .data
        .get("provider")
        .and_then(Value::as_str)
        .filter(|x| ["google", "microsoft"].contains(x))
        .ok_or_else(|| ApiError::bad_request("Invalid calendar connection provider."))
}
fn is_connection(r: &Resource) -> bool {
    r.kind == "connector" && r.data.get("type").and_then(Value::as_str) == Some("calendar")
}
fn require_connection(actor: &Actor, r: &Resource) -> ApiResult<()> {
    if !is_connection(r) {
        return Err(ApiError::not_found("Calendar connection not found."));
    }
    ensure_write(actor, r)?;
    if r.owner_id != actor.id && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Only the calendar owner can manage this connection.",
        ));
    }
    Ok(())
}
fn sanitized(r: &Resource) -> Value {
    json!({"id":r.id,"name":r.name,"provider":r.data["provider"],"status":r.data["status"],"remote_calendar_id":r.data["remote_calendar_id"],"direction":r.data["direction"],"last_sync_at":r.data["last_sync_at"],"next_sync_at":r.data["next_sync_at"],"last_error":r.data["last_error"],"version":r.version})
}

pub async fn connections(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Vec<Value>>> {
    actor.require_scope("integrations:read")?;
    Ok(Json(
        super::list_resources(&state, &actor, "connector", None)
            .await?
            .into_iter()
            .filter(|r| is_connection(r) && r.owner_id == actor.id)
            .map(|r| sanitized(&r))
            .collect(),
    ))
}

#[derive(Deserialize)]
pub struct ConnectInput {
    pub provider: String,
}
pub async fn connect(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<ConnectInput>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("integrations:write")?;
    let (client_id, _) = provider_config(&state, &input.provider)?;
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    let oauth_state = hex::encode(bytes);
    let scopes = if input.provider == "google" {
        "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly"
    } else {
        "offline_access Calendars.ReadWrite User.Read"
    };
    let authorize = if input.provider == "google" {
        endpoint(
            &state,
            "google_authorize",
            "https://accounts.google.com/o/oauth2/v2/auth",
        )
    } else {
        endpoint(
            &state,
            "microsoft_authorize",
            "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        )
    };
    let mut url = url::Url::parse(authorize)
        .map_err(|_| ApiError::internal("Invalid calendar authorization service URL."))?;
    url.query_pairs_mut()
        .append_pair("client_id", &client_id)
        .append_pair("redirect_uri", &callback_url(&state, &input.provider))
        .append_pair("response_type", "code")
        .append_pair("scope", scopes)
        .append_pair("state", &oauth_state);
    if input.provider == "google" {
        url.query_pairs_mut()
            .append_pair("access_type", "offline")
            .append_pair("prompt", "consent");
    }
    let connection=super::create_resource(&state,&actor,NewResource{kind:"connector".into(),name:format!("{} calendar",input.provider),parent_id:None,visibility:Some("private".into()),acl:None,data:json!({"type":"calendar","provider":input.provider,"owner_user_id":actor.id,"status":"authorizing","oauth_state_hash":hex::encode(Sha256::digest(oauth_state.as_bytes())),"oauth_state_expires":(Utc::now()+Duration::minutes(10)).to_rfc3339(),"remote_calendar_id":"primary","direction":"bidirectional"})}).await?;
    Ok(Json(
        json!({"connection_id":connection.id,"authorization_url":url.as_str()}),
    ))
}

#[derive(Deserialize)]
pub struct OAuthQuery {
    pub state: Option<String>,
    pub code: Option<String>,
    pub error: Option<String>,
}
pub async fn oauth_callback(
    State(state): State<AppState>,
    Path(provider_name): Path<String>,
    Query(query): Query<OAuthQuery>,
) -> ApiResult<Redirect> {
    let raw = query
        .state
        .as_deref()
        .filter(|s| s.len() == 64)
        .ok_or_else(|| ApiError::bad_request("Missing or invalid OAuth state."))?;
    let hash = hex::encode(Sha256::digest(raw.as_bytes()));
    let row:Option<(Uuid,Uuid)>=sqlx::query_as("SELECT id,owner_id FROM resources WHERE kind='connector' AND data->>'type'='calendar' AND data->>'provider'=$1 AND data->>'oauth_state_hash'=$2 AND deleted_at IS NULL LIMIT 1").bind(&provider_name).bind(&hash).fetch_optional(&state.pool).await?;
    let (id, owner) = row.ok_or_else(|| {
        ApiError::bad_request("This authorization is unavailable or has already been used.")
    })?;
    let actor = crate::auth::actor_by_id(&state, owner).await?;
    let resource = super::get_resource(&state, &actor, id).await?;
    if resource.data.get("status").and_then(Value::as_str) != Some("authorizing")
        || resource
            .data
            .get("oauth_state_expires")
            .and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .is_none_or(|x| x < Utc::now())
    {
        return Err(ApiError::bad_request(
            "Calendar authorization expired. Start a new connection.",
        ));
    }
    let mut data = resource.data.clone();
    data.as_object_mut().unwrap().remove("oauth_state_hash");
    data.as_object_mut().unwrap().remove("oauth_state_expires");
    data["status"] = json!("authorizing_exchange");
    let claimed = super::update_resource(&state, &actor, id, modification(&resource, data)).await?;
    if query.error.is_some() {
        let mut data = claimed.data.clone();
        data["status"] = json!("revoked");
        data["last_error"] = json!("Authorization was declined.");
        super::update_resource(&state, &actor, id, modification(&claimed, data)).await?;
        return Ok(Redirect::to(&format!(
            "{}/#calendar?authorization=declined",
            state.config.public_url
        )));
    }
    let code = query.code.ok_or_else(|| {
        ApiError::bad_request("The calendar provider did not return an authorization code.")
    })?;
    let (client_id, client_secret) = provider_config(&state, &provider_name)?;
    let token_url = if provider_name == "google" {
        endpoint(
            &state,
            "google_token",
            "https://oauth2.googleapis.com/token",
        )
    } else {
        endpoint(
            &state,
            "microsoft_token",
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        )
    };
    let response = state
        .http
        .post(token_url)
        .form(&[
            ("client_id", client_id),
            ("client_secret", client_secret),
            ("code", code),
            ("redirect_uri", callback_url(&state, &provider_name)),
            ("grant_type", "authorization_code".into()),
        ])
        .send()
        .await;
    let tokens = match response {
        Ok(r) if r.status().is_success() => r.json::<Value>().await.ok(),
        _ => None,
    };
    let tokens = match tokens {
        Some(tokens)
            if tokens
                .get("access_token")
                .and_then(Value::as_str)
                .is_some_and(|token| !token.is_empty()) =>
        {
            tokens
        }
        _ => {
            let mut data = claimed.data.clone();
            data["status"] = json!("error");
            data["last_error"] = json!("Token exchange failed. Start a new connection.");
            super::update_resource(&state, &actor, id, modification(&claimed, data)).await?;
            return Err(provider_error("Calendar authorization"));
        }
    };
    let mut tokens = tokens;
    tokens["expires_at"] = json!(
        (Utc::now()
            + Duration::seconds(
                tokens
                    .get("expires_in")
                    .and_then(Value::as_i64)
                    .unwrap_or(3600)
                    .clamp(1, 604800)
            ))
        .to_rfc3339()
    );
    let mut data = claimed.data.clone();
    data["tokens_encrypted"] = json!(crate::encrypt_secret(&state.config, &tokens.to_string())?);
    data["status"] = json!("connected");
    data["next_sync_at"] = json!(Utc::now().to_rfc3339());
    data["last_error"] = Value::Null;
    let saved = super::update_resource(&state, &actor, id, modification(&claimed, data)).await?;
    repo::enqueue(
        &state.pool,
        &actor,
        "business.calendar.sync",
        json!({"actor_id":owner,"connection_id":id}),
        Some(&format!("calendar-connect:{}:{}", id, saved.version)),
    )
    .await?;
    Ok(Redirect::to(&format!(
        "{}/#calendar?authorization=connected",
        state.config.public_url
    )))
}

pub async fn sync_now(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Job>> {
    actor.require_scope("integrations:write")?;
    let connection = super::get_resource(&state, &actor, id).await?;
    require_connection(&actor, &connection)?;
    if connection
        .data
        .get("tokens_encrypted")
        .and_then(Value::as_str)
        .is_none()
    {
        return Err(ApiError::conflict(
            "Authorize this calendar before synchronizing it.",
        ));
    }
    Ok(Json(
        repo::enqueue(
            &state.pool,
            &actor,
            "business.calendar.sync",
            json!({"actor_id":actor.id,"connection_id":id}),
            Some(&format!(
                "calendar-manual:{}:{}",
                id,
                Utc::now().timestamp() / 10
            )),
        )
        .await?,
    ))
}

pub async fn disconnect(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("integrations:write")?;
    let connection = super::get_resource(&state, &actor, id).await?;
    require_connection(&actor, &connection)?;
    let mut data = connection.data.clone();
    for k in [
        "tokens_encrypted",
        "oauth_state_hash",
        "oauth_state_expires",
        "sync_cursor",
    ] {
        data.as_object_mut().unwrap().remove(k);
    }
    data["status"] = json!("revoked");
    data["next_sync_at"] = Value::Null;
    let r = super::update_resource(&state, &actor, id, modification(&connection, data)).await?;
    Ok(Json(sanitized(&r)))
}

async fn access_token(
    state: &AppState,
    actor: &Actor,
    connection: &mut Resource,
) -> ApiResult<String> {
    let encrypted = connection
        .data
        .get("tokens_encrypted")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::conflict("This calendar needs authorization."))?;
    let mut tokens: Value = serde_json::from_str(&crate::decrypt_secret(&state.config, encrypted)?)
        .map_err(|_| ApiError::internal("Invalid encrypted calendar credentials."))?;
    if tokens
        .get("expires_at")
        .and_then(Value::as_str)
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .is_some_and(|t| t > Utc::now() + Duration::seconds(60))
    {
        return tokens
            .get("access_token")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .ok_or_else(|| provider_error("Calendar token"));
    }
    let refresh = tokens
        .get("refresh_token")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::conflict("Calendar access expired. Reconnect the calendar."))?
        .to_owned();
    let name = provider(connection)?.to_owned();
    let (id, secret) = provider_config(state, &name)?;
    let token_url = if name == "google" {
        endpoint(state, "google_token", "https://oauth2.googleapis.com/token")
    } else {
        endpoint(
            state,
            "microsoft_token",
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        )
    };
    let response = state
        .http
        .post(token_url)
        .form(&[
            ("client_id", id),
            ("client_secret", secret),
            ("refresh_token", refresh.clone()),
            ("grant_type", "refresh_token".into()),
        ])
        .send()
        .await?;
    if !response.status().is_success() {
        return Err(ApiError::conflict(
            "Calendar authorization is no longer valid. Reconnect the calendar.",
        ));
    }
    let refreshed: Value = response.json().await?;
    let access = refreshed
        .get("access_token")
        .and_then(Value::as_str)
        .ok_or_else(|| provider_error("Calendar token"))?
        .to_owned();
    tokens = refreshed;
    if tokens.get("refresh_token").is_none() {
        tokens["refresh_token"] = json!(refresh);
    }
    tokens["expires_at"] = json!(
        (Utc::now()
            + Duration::seconds(
                tokens
                    .get("expires_in")
                    .and_then(Value::as_i64)
                    .unwrap_or(3600)
                    .clamp(1, 604800)
            ))
        .to_rfc3339()
    );
    let mut data = connection.data.clone();
    data["tokens_encrypted"] = json!(crate::encrypt_secret(&state.config, &tokens.to_string())?);
    *connection =
        super::update_resource(&state, actor, connection.id, modification(connection, data))
            .await?;
    Ok(access)
}

pub async fn schedule_due(state: &AppState) -> ApiResult<()> {
    let rows:Vec<(Uuid,Uuid)>=sqlx::query_as("SELECT id,owner_id FROM resources WHERE kind='connector' AND data->>'type'='calendar' AND data->>'status' IN ('connected','error') AND data ? 'tokens_encrypted' AND deleted_at IS NULL AND COALESCE(data->>'next_sync_at','') <= $1 ORDER BY updated_at LIMIT 20").bind(Utc::now().to_rfc3339()).fetch_all(&state.pool).await?;
    for (id, owner) in rows {
        let Ok(actor) = crate::auth::actor_by_id(state, owner).await else {
            continue;
        };
        let Ok(r) = super::get_resource(&state, &actor, id).await else {
            continue;
        };
        let mut data = r.data.clone();
        data["next_sync_at"] = json!((Utc::now() + Duration::minutes(5)).to_rfc3339());
        if super::update_resource(&state, &actor, id, modification(&r, data))
            .await
            .is_ok()
        {
            repo::enqueue(
                &state.pool,
                &actor,
                "business.calendar.sync",
                json!({"actor_id":owner,"connection_id":id}),
                Some(&format!(
                    "calendar-periodic:{}:{}",
                    id,
                    Utc::now().timestamp() / 300
                )),
            )
            .await?;
        }
    }
    Ok(())
}

pub async fn process_sync(state: &AppState, job: &Job) -> ApiResult<Value> {
    let actor = crate::auth::actor_for_job(state, job).await?;
    let mut connection = super::get_resource(
        &state,
        &actor,
        parse_id(job.payload.get("connection_id"), "connection_id")?,
    )
    .await?;
    require_connection(&actor, &connection)?;
    if connection.data.get("status").and_then(Value::as_str) == Some("revoked") {
        return Err(ApiError::conflict("Calendar connection was revoked."));
    }
    let result = async {
        let token = access_token(state, &actor, &mut connection).await?;
        if provider(&connection)? == "google" {
            google_sync(state, &actor, &connection, &token).await
        } else {
            microsoft_sync(state, &actor, &connection, &token).await
        }
    }
    .await;
    let current = super::get_resource(&state, &actor, connection.id).await?;
    let mut data = current.data.clone();
    data["next_sync_at"] = json!((Utc::now() + Duration::minutes(5)).to_rfc3339());
    match result {
        Ok((cursor, imported)) => {
            data["status"] = json!("connected");
            data["last_error"] = Value::Null;
            data["last_sync_at"] = json!(Utc::now().to_rfc3339());
            data["sync_cursor"] = json!(cursor);
            super::update_resource(&state, &actor, current.id, modification(&current, data))
                .await?;
            Ok(json!({"connection_id":connection.id,"imported":imported}))
        }
        Err(error) => {
            data["status"] = json!("error");
            data["last_error"] = json!(
                "Synchronization failed. Check authorization and retry; local events have been preserved."
            );
            super::update_resource(&state, &actor, current.id, modification(&current, data))
                .await?;
            Err(error)
        }
    }
}

fn url_with_segment(base: &str, segments: &[&str]) -> ApiResult<url::Url> {
    let mut url =
        url::Url::parse(base).map_err(|_| ApiError::internal("Invalid calendar API URL."))?;
    {
        let mut path = url
            .path_segments_mut()
            .map_err(|_| ApiError::internal("Invalid calendar API URL."))?;
        path.pop_if_empty();
        for segment in segments {
            path.push(segment);
        }
    }
    Ok(url)
}

async fn google_sync(
    state: &AppState,
    actor: &Actor,
    connection: &Resource,
    token: &str,
) -> ApiResult<(String, usize)> {
    let base = endpoint(
        state,
        "google_calendar_api",
        "https://www.googleapis.com/calendar/v3",
    );
    let calendar = connection
        .data
        .get("remote_calendar_id")
        .and_then(Value::as_str)
        .unwrap_or("primary");
    let mut cursor = connection
        .data
        .get("sync_cursor")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let mut page: Option<String> = None;
    let mut imported = 0;
    let mut reset = false;
    for _ in 0..100 {
        let mut url = url_with_segment(base, &["calendars", calendar, "events"])?;
        url.query_pairs_mut()
            .append_pair("maxResults", "2500")
            .append_pair("showDeleted", "true")
            .append_pair("singleEvents", "false");
        if let Some(c) = &cursor {
            url.query_pairs_mut().append_pair("syncToken", c);
        }
        if let Some(p) = &page {
            url.query_pairs_mut().append_pair("pageToken", p);
        }
        let response = state.http.get(url).bearer_auth(token).send().await?;
        if response.status() == StatusCode::GONE && !reset {
            cursor = None;
            page = None;
            reset = true;
            continue;
        }
        if !response.status().is_success() {
            return Err(provider_error("Google Calendar"));
        }
        let body: Value = response.json().await?;
        for item in body
            .get("items")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if import_event(state, actor, connection, item, "google").await? {
                imported += 1;
            }
        }
        if let Some(next) = body.get("nextPageToken").and_then(Value::as_str) {
            page = Some(next.into());
            continue;
        }
        let next = body
            .get("nextSyncToken")
            .and_then(Value::as_str)
            .ok_or_else(|| provider_error("Google Calendar sync cursor"))?;
        return Ok((next.into(), imported));
    }
    Err(ApiError::unavailable(
        "Calendar sync exceeded its page limit. Narrow the connected calendar or contact the administrator.",
    ))
}

fn verified_graph_link(base: &str, link: &str) -> ApiResult<url::Url> {
    let root =
        url::Url::parse(base).map_err(|_| ApiError::internal("Invalid Microsoft Graph URL."))?;
    let target = url::Url::parse(link).map_err(|_| provider_error("Microsoft calendar cursor"))?;
    if target.origin() != root.origin()
        || !target.path().starts_with(root.path())
        || !target.username().is_empty()
        || target.password().is_some()
    {
        return Err(provider_error("Microsoft calendar cursor"));
    }
    Ok(target)
}

async fn microsoft_sync(
    state: &AppState,
    actor: &Actor,
    connection: &Resource,
    token: &str,
) -> ApiResult<(String, usize)> {
    let base = endpoint(
        state,
        "microsoft_graph_api",
        "https://graph.microsoft.com/v1.0",
    );
    let stored = connection.data.get("sync_cursor").and_then(Value::as_str);
    let mut url = if let Some(link) = stored {
        verified_graph_link(base, link)?
    } else {
        let mut u = url_with_segment(base, &["me", "calendarView", "delta"])?;
        u.query_pairs_mut()
            .append_pair(
                "startDateTime",
                &(Utc::now() - Duration::days(366)).to_rfc3339(),
            )
            .append_pair(
                "endDateTime",
                &(Utc::now() + Duration::days(732)).to_rfc3339(),
            );
        u
    };
    let mut imported = 0;
    let mut reset = false;
    for _ in 0..100 {
        let response = state
            .http
            .get(url.clone())
            .bearer_auth(token)
            .header("Prefer", "outlook.timezone=\"UTC\"")
            .send()
            .await?;
        if [StatusCode::GONE, StatusCode::BAD_REQUEST].contains(&response.status())
            && stored.is_some()
            && !reset
        {
            reset = true;
            let mut u = url_with_segment(base, &["me", "calendarView", "delta"])?;
            u.query_pairs_mut()
                .append_pair(
                    "startDateTime",
                    &(Utc::now() - Duration::days(366)).to_rfc3339(),
                )
                .append_pair(
                    "endDateTime",
                    &(Utc::now() + Duration::days(732)).to_rfc3339(),
                );
            url = u;
            continue;
        }
        if !response.status().is_success() {
            return Err(provider_error("Microsoft Calendar"));
        }
        let body: Value = response.json().await?;
        for item in body
            .get("value")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if import_event(state, actor, connection, item, "microsoft").await? {
                imported += 1;
            }
        }
        if let Some(link) = body.get("@odata.nextLink").and_then(Value::as_str) {
            url = verified_graph_link(base, link)?;
            continue;
        }
        return Ok((
            body.get("@odata.deltaLink")
                .and_then(Value::as_str)
                .ok_or_else(|| provider_error("Microsoft calendar cursor"))?
                .to_owned(),
            imported,
        ));
    }
    Err(ApiError::unavailable(
        "Calendar sync exceeded its page limit.",
    ))
}

fn event_timestamp(value: &Value, all_day: bool) -> ApiResult<String> {
    if let Some(s) = value.get("dateTime").and_then(Value::as_str) {
        if let Ok(date) = DateTime::parse_from_rfc3339(s) {
            return Ok(date.to_rfc3339());
        }
        if let Ok(date) = chrono::NaiveDateTime::parse_from_str(s, "%Y-%m-%dT%H:%M:%S%.f") {
            return Ok(Utc.from_utc_datetime(&date).to_rfc3339());
        }
    }
    if all_day {
        if let Some(s) = value.get("date").and_then(Value::as_str) {
            let date = NaiveDate::parse_from_str(s, "%Y-%m-%d")
                .map_err(|_| provider_error("Calendar event date"))?;
            return Ok(Utc
                .from_utc_datetime(&date.and_hms_opt(0, 0, 0).unwrap())
                .to_rfc3339());
        }
    }
    Err(provider_error("Calendar event timestamp"))
}

fn normalized_remote(item: &Value, name: &str) -> ApiResult<Value> {
    let all_day = if name == "google" {
        item["start"].get("date").is_some()
    } else {
        item.get("isAllDay")
            .and_then(Value::as_bool)
            .unwrap_or(false)
    };
    let mut value = json!({"start":event_timestamp(&item["start"],all_day)?,"end":event_timestamp(&item["end"],all_day)?,"timezone":item["start"].get("timeZone").and_then(Value::as_str).filter(|z|z.parse::<chrono_tz::Tz>().is_ok()).unwrap_or("UTC"),"all_day":all_day,"description":if name=="google"{item.get("description").and_then(Value::as_str).unwrap_or("")}else{item.get("bodyPreview").and_then(Value::as_str).unwrap_or("")},"location":if name=="google"{item.get("location").and_then(Value::as_str).unwrap_or("")}else{item["location"].get("displayName").and_then(Value::as_str).unwrap_or("")}});
    if name == "google" {
        if let Some(rule) = item
            .get("recurrence")
            .and_then(Value::as_array)
            .and_then(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .find_map(|s| s.strip_prefix("RRULE:"))
            })
        {
            value["rrule"] = json!(rule);
        }
    }
    super::records::validate_event(&value)?;
    Ok(value)
}

async fn import_event(
    state: &AppState,
    actor: &Actor,
    connection: &Resource,
    item: &Value,
    name: &str,
) -> ApiResult<bool> {
    let remote_id = item
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| provider_error("Calendar event identifier"))?;
    let conn = connection.id.to_string();
    let existing=sqlx::query_as::<_,Resource>("SELECT id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at FROM resources WHERE kind='event' AND owner_id=$1 AND data #>> ARRAY['external',$2,'id']=$3 LIMIT 1").bind(actor.id).bind(&conn).bind(remote_id).fetch_optional(&state.pool).await?;
    let deleted = item.get("status").and_then(Value::as_str) == Some("cancelled")
        || item.get("@removed").is_some()
        || item.get("isCancelled").and_then(Value::as_bool) == Some(true);
    let etag = item
        .get("etag")
        .or_else(|| item.get("@odata.etag"))
        .and_then(Value::as_str)
        .unwrap_or("");
    if let Some(old) = existing {
        if old.deleted_at.is_some() {
            return Ok(false);
        }
        if !actor.can_write(&old) {
            return Err(ApiError::forbidden("Calendar event is no longer writable."));
        }
        if old.data["external"][&conn]["etag"].as_str() == Some(etag) && !deleted {
            return Ok(false);
        }
        let last = old.data["external"][&conn]["last_local_version"]
            .as_i64()
            .unwrap_or(old.version);
        if old.version > last {
            let mut data = old.data.clone();
            data["sync_conflict"] = json!({"connection_id":connection.id,"remote_event":item,"detected_at":Utc::now(),"reason":"Both local and remote event changed."});
            super::update_resource(&state, actor, old.id, modification(&old, data)).await?;
            return Ok(false);
        }
        if deleted {
            repo::soft_delete(&state.pool, actor, old.id, old.version).await?;
            return Ok(true);
        }
        let normalized = normalized_remote(item, name)?;
        let mut data = old.data.clone();
        for (k, v) in normalized.as_object().unwrap() {
            data[k] = v.clone();
        }
        data["external"][&conn] =
            json!({"id":remote_id,"etag":etag,"last_local_version":old.version+1});
        data.as_object_mut().unwrap().remove("sync_conflict");
        let mut update = modification(&old, data);
        update.name = Some(
            item.get(if name == "google" {
                "summary"
            } else {
                "subject"
            })
            .and_then(Value::as_str)
            .unwrap_or("Calendar event")
            .to_owned(),
        );
        super::update_resource(&state, actor, old.id, update).await?;
        return Ok(true);
    }
    if deleted {
        return Ok(false);
    }
    let mut data = normalized_remote(item, name)?;
    data["external"] = json!({conn:{"id":remote_id,"etag":etag,"last_local_version":1}});
    data["source"] = json!("external_calendar");
    super::create_resource(
        &state,
        actor,
        NewResource {
            kind: "event".into(),
            name: item
                .get(if name == "google" {
                    "summary"
                } else {
                    "subject"
                })
                .and_then(Value::as_str)
                .filter(|s| !s.trim().is_empty())
                .unwrap_or("Calendar event")
                .into(),
            parent_id: None,
            visibility: Some("private".into()),
            acl: None,
            data,
        },
    )
    .await?;
    Ok(true)
}

pub async fn queue_exports(state: &AppState, actor: &Actor, event: &Resource) -> ApiResult<()> {
    if event.data.get("sync_enabled").and_then(Value::as_bool) == Some(false) {
        return Ok(());
    }
    let owner = crate::auth::actor_by_id(state, event.owner_id).await?;
    // Scoped service tokens need explicit integration authority before writes
    // may leave the workspace. Ordinary signed-in members have their full role.
    if !actor.scope_allows("integrations:write") {
        return Ok(());
    }
    for c in super::list_resources(&state, &owner, "connector", None)
        .await?
        .into_iter()
        .filter(|r| {
            is_connection(r)
                && r.owner_id == event.owner_id
                && r.data.get("status").and_then(Value::as_str) == Some("connected")
                && r.data.get("direction").and_then(Value::as_str) != Some("import")
                && actor.can_read(r)
        })
    {
        repo::enqueue(&state.pool,actor,"business.calendar.push",json!({"actor_id":actor.id,"connection_id":c.id,"event_id":event.id,"event_version":event.version}),Some(&format!("calendar-push:{}:{}:{}",c.id,event.id,event.version))).await?;
    }
    Ok(())
}

fn microsoft_recurrence(event: &Resource) -> ApiResult<Value> {
    let Some(rule) = event
        .data
        .get("rrule")
        .and_then(Value::as_str)
        .filter(|rule| !rule.is_empty())
    else {
        return Ok(Value::Null);
    };
    let mut parts = std::collections::BTreeMap::new();
    for part in rule.split(';') {
        let (key, value) = part
            .split_once('=')
            .ok_or_else(|| ApiError::bad_request("Invalid recurrence rule."))?;
        if ![
            "FREQ",
            "INTERVAL",
            "COUNT",
            "UNTIL",
            "BYDAY",
            "BYMONTHDAY",
            "BYMONTH",
            "BYSETPOS",
            "WKST",
        ]
        .contains(&key)
            || parts.insert(key, value).is_some()
        {
            return Err(ApiError::bad_request(
                "This recurrence rule cannot be represented by Microsoft Calendar.",
            ));
        }
    }
    let number = |key: &str, default: u32, max: u32| -> ApiResult<u32> {
        let n = parts
            .get(key)
            .map(|value| value.parse::<u32>())
            .transpose()
            .map_err(|_| ApiError::bad_request("Invalid recurrence number."))?
            .unwrap_or(default);
        if n == 0 || n > max {
            return Err(ApiError::bad_request("Recurrence number is out of range."));
        }
        Ok(n)
    };
    let start = DateTime::parse_from_rfc3339(event.data["start"].as_str().unwrap_or(""))
        .map_err(|_| ApiError::bad_request("Invalid event timestamp."))?;
    let names = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ];
    let day_name = |code: &str| -> ApiResult<&str> {
        let index = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
            .iter()
            .position(|day| *day == code)
            .ok_or_else(|| ApiError::bad_request("Unsupported recurrence weekday."))?;
        Ok(names[index])
    };
    let mut pattern = json!({"interval":number("INTERVAL",1,999)?});
    match parts.get("FREQ").copied().unwrap_or("") {
        "DAILY" => {
            if parts.contains_key("BYDAY")
                || parts.contains_key("BYMONTHDAY")
                || parts.contains_key("BYMONTH")
            {
                return Err(ApiError::bad_request(
                    "Microsoft daily recurrence does not support date filters.",
                ));
            }
            pattern["type"] = json!("daily");
        }
        "WEEKLY" => {
            pattern["type"] = json!("weekly");
            let days = parts
                .get("BYDAY")
                .map(|days| days.split(',').map(day_name).collect::<ApiResult<Vec<_>>>())
                .transpose()?
                .unwrap_or_else(|| vec![names[start.weekday().num_days_from_monday() as usize]]);
            pattern["daysOfWeek"] = json!(days);
            pattern["firstDayOfWeek"] =
                json!(day_name(parts.get("WKST").copied().unwrap_or("MO"))?);
            if parts.contains_key("BYMONTHDAY")
                || parts.contains_key("BYMONTH")
                || parts.contains_key("BYSETPOS")
            {
                return Err(ApiError::bad_request(
                    "Microsoft weekly recurrence does not support monthly date filters.",
                ));
            }
        }
        frequency @ ("MONTHLY" | "YEARLY") => {
            let yearly = frequency == "YEARLY";
            if yearly {
                pattern["month"] = json!(number("BYMONTH", start.month(), 12)?);
            } else if parts.contains_key("BYMONTH") {
                return Err(ApiError::bad_request(
                    "Microsoft monthly recurrence cannot filter months.",
                ));
            }
            if let Some(day) = parts.get("BYDAY") {
                if day.contains(',') || parts.contains_key("BYMONTHDAY") {
                    return Err(ApiError::bad_request(
                        "Microsoft relative monthly recurrence needs one weekday.",
                    ));
                }
                let (ordinal, weekday) = if day.len() > 2 {
                    day.split_at(day.len() - 2)
                } else {
                    ("", *day)
                };
                let ordinal = parts
                    .get("BYSETPOS")
                    .copied()
                    .filter(|_| ordinal.is_empty())
                    .unwrap_or(ordinal);
                let index = match ordinal {
                    "1" => "first",
                    "2" => "second",
                    "3" => "third",
                    "4" => "fourth",
                    "-1" => "last",
                    _ => {
                        return Err(ApiError::bad_request(
                            "Relative monthly recurrence needs BYSETPOS=1..4 or -1.",
                        ));
                    }
                };
                pattern["type"] = json!(if yearly {
                    "relativeYearly"
                } else {
                    "relativeMonthly"
                });
                pattern["daysOfWeek"] = json!([day_name(weekday)?]);
                pattern["index"] = json!(index);
            } else {
                if parts.contains_key("BYSETPOS") {
                    return Err(ApiError::bad_request("BYSETPOS requires a weekday."));
                }
                pattern["type"] = json!(if yearly {
                    "absoluteYearly"
                } else {
                    "absoluteMonthly"
                });
                pattern["dayOfMonth"] = json!(number("BYMONTHDAY", start.day(), 31)?);
            }
        }
        _ => {
            return Err(ApiError::bad_request(
                "Unsupported Microsoft recurrence frequency.",
            ));
        }
    }
    let mut range = json!({"type":"noEnd","startDate":start.format("%Y-%m-%d").to_string(),"recurrenceTimeZone":event.data.get("timezone").and_then(Value::as_str).unwrap_or("UTC")});
    if parts.contains_key("COUNT") && parts.contains_key("UNTIL") {
        return Err(ApiError::bad_request("Use COUNT or UNTIL, not both."));
    }
    if let Some(until) = parts.get("UNTIL") {
        let date = until
            .get(..8)
            .ok_or_else(|| ApiError::bad_request("Invalid recurrence end date."))?;
        let date = NaiveDate::parse_from_str(date, "%Y%m%d")
            .map_err(|_| ApiError::bad_request("Invalid recurrence end date."))?;
        range["type"] = json!("endDate");
        range["endDate"] = json!(date.format("%Y-%m-%d").to_string());
    }
    if parts.contains_key("COUNT") {
        range["type"] = json!("numbered");
        range["numberOfOccurrences"] = json!(number("COUNT", 1, 10000)?);
    }
    Ok(json!({"pattern":pattern,"range":range}))
}

fn remote_body(event: &Resource, name: &str) -> ApiResult<Value> {
    let all_day = event
        .data
        .get("all_day")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let start = event.data["start"].as_str().unwrap_or("");
    let end = event.data["end"].as_str().unwrap_or("");
    if name == "google" {
        let mut body = json!({"summary":event.name,"description":event.data.get("description").and_then(Value::as_str).unwrap_or(""),"location":event.data.get("location").and_then(Value::as_str).unwrap_or(""),"extendedProperties":{"private":{"blueprint_resource_id":event.id.to_string()}}});
        body["start"] = if all_day {
            json!({"date":start.get(..10).unwrap_or(start)})
        } else {
            json!({"dateTime":start,"timeZone":event.data.get("timezone").and_then(Value::as_str).unwrap_or("UTC")})
        };
        body["end"] = if all_day {
            json!({"date":end.get(..10).unwrap_or(end)})
        } else {
            json!({"dateTime":end,"timeZone":event.data.get("timezone").and_then(Value::as_str).unwrap_or("UTC")})
        };
        if let Some(rrule) = event.data.get("rrule").and_then(Value::as_str) {
            body["recurrence"] = json!([format!("RRULE:{rrule}")]);
        }
        Ok(body)
    } else {
        let zone = event
            .data
            .get("timezone")
            .and_then(Value::as_str)
            .unwrap_or("UTC");
        let parsed_zone = zone
            .parse::<chrono_tz::Tz>()
            .map_err(|_| ApiError::bad_request("Invalid event timezone."))?;
        let timestamp = |input: &str| {
            if all_day {
                format!("{}T00:00:00", input.get(..10).unwrap_or(input))
            } else {
                DateTime::parse_from_rfc3339(input)
                    .map(|t| {
                        t.with_timezone(&parsed_zone)
                            .format("%Y-%m-%dT%H:%M:%S")
                            .to_string()
                    })
                    .unwrap_or_else(|_| input.into())
            }
        };
        Ok(
            json!({"subject":event.name,"body":{"contentType":"text","content":event.data.get("description").and_then(Value::as_str).unwrap_or("")},"start":{"dateTime":timestamp(start),"timeZone":if all_day{"UTC"}else{zone}},"end":{"dateTime":timestamp(end),"timeZone":if all_day{"UTC"}else{zone}},"isAllDay":all_day,"location":{"displayName":event.data.get("location").and_then(Value::as_str).unwrap_or("")},"transactionId":event.id.to_string(),"recurrence":microsoft_recurrence(event)?}),
        )
    }
}

#[derive(Deserialize)]
pub struct ResolveInput {
    pub resolution: String,
    pub expected_version: i64,
}

pub async fn resolve_conflict(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<ResolveInput>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("business:write")?;
    actor.require_scope("integrations:write")?;
    if !["local", "remote"].contains(&input.resolution.as_str()) {
        return Err(ApiError::bad_request("resolution must be local or remote."));
    }
    let event = super::get_resource(&state, &actor, id).await?;
    if event.kind != "event" {
        return Err(ApiError::not_found("Calendar event not found."));
    }
    ensure_write(&actor, &event)?;
    if event.version != input.expected_version {
        return Err(ApiError::conflict(
            "The event changed. Reload before resolving it.",
        ));
    }
    crate::content::authorize_action(&state, &actor, &event, "update").await?;
    let connection_id = parse_id(
        event
            .data
            .get("sync_conflict")
            .and_then(|c| c.get("connection_id")),
        "sync_conflict.connection_id",
    )?;
    let mut connection = super::get_resource(&state, &actor, connection_id).await?;
    require_connection(&actor, &connection)?;
    let name = provider(&connection)?.to_owned();
    let token = access_token(&state, &actor, &mut connection).await?;
    let conn = connection_id.to_string();
    let remote_id = event.data["external"][&conn]["id"]
        .as_str()
        .ok_or_else(|| ApiError::conflict("This event has no external reference to reconcile."))?;
    let base = if name == "google" {
        endpoint(
            &state,
            "google_calendar_api",
            "https://www.googleapis.com/calendar/v3",
        )
    } else {
        endpoint(
            &state,
            "microsoft_graph_api",
            "https://graph.microsoft.com/v1.0",
        )
    };
    let calendar_id = connection
        .data
        .get("remote_calendar_id")
        .and_then(Value::as_str)
        .unwrap_or("primary");
    let url = if name == "google" {
        url_with_segment(base, &["calendars", calendar_id, "events", remote_id])?
    } else {
        url_with_segment(base, &["me", "events", remote_id])?
    };
    let response = state.http.get(url).bearer_auth(token).send().await?;
    let remote_deleted = [StatusCode::NOT_FOUND, StatusCode::GONE].contains(&response.status());
    let remote = if remote_deleted {
        Value::Null
    } else {
        if !response.status().is_success() {
            return Err(provider_error("Calendar conflict resolution"));
        }
        response.json::<Value>().await?
    };
    let remote_deleted = remote_deleted
        || remote.get("status").and_then(Value::as_str) == Some("cancelled")
        || remote.get("isCancelled").and_then(Value::as_bool) == Some(true);
    if input.resolution == "remote" && remote_deleted {
        crate::content::authorize_action(&state, &actor, &event, "delete").await?;
        repo::soft_delete(&state.pool, &actor, event.id, event.version).await?;
        return Ok(Json(
            json!({"event":null,"resolution":"remote","deleted":true}),
        ));
    }
    let mut data = event.data.clone();
    data.as_object_mut().unwrap().remove("sync_conflict");
    let mut update = modification(&event, data);
    let data = update.data.as_mut().unwrap();
    if input.resolution == "remote" {
        data.as_object_mut().unwrap().remove("rrule");
        for (key, value) in normalized_remote(&remote, &name)?.as_object().unwrap() {
            data[key] = value.clone();
        }
        update.name = Some(
            remote
                .get(if name == "google" {
                    "summary"
                } else {
                    "subject"
                })
                .and_then(Value::as_str)
                .unwrap_or("Calendar event")
                .to_owned(),
        );
    }
    if remote_deleted {
        data["external"].as_object_mut().unwrap().remove(&conn);
    } else {
        data["external"][&conn] = json!({"id":remote_id,"etag":remote.get("etag").or_else(||remote.get("@odata.etag")).and_then(Value::as_str).unwrap_or(""),"last_local_version":event.version+1});
    }
    let event = super::update_resource(&state, &actor, event.id, update).await?;
    let job = if input.resolution == "local" {
        Some(repo::enqueue(&state.pool,&actor,"business.calendar.push",json!({"connection_id":connection_id,"event_id":event.id,"event_version":event.version}),Some(&format!("calendar-resolve:{}:{}:{}",connection_id,event.id,event.version))).await?)
    } else {
        None
    };
    repo::audit(
        &state.pool,
        &actor,
        "calendar.conflict_resolved",
        Some(event.id),
        json!({"resolution":input.resolution,"connection_id":connection_id}),
    )
    .await?;
    Ok(Json(
        json!({"event":event,"resolution":input.resolution,"job":job}),
    ))
}

pub async fn process_push(state: &AppState, job: &Job) -> ApiResult<Value> {
    let actor = crate::auth::actor_for_job(state, job).await?;
    let mut connection = super::get_resource(
        &state,
        &actor,
        parse_id(job.payload.get("connection_id"), "connection_id")?,
    )
    .await?;
    require_connection(&actor, &connection)?;
    if connection.data.get("status").and_then(Value::as_str) != Some("connected") {
        return Err(ApiError::conflict("Calendar is not connected."));
    }
    let event = super::get_resource(
        &state,
        &actor,
        parse_id(job.payload.get("event_id"), "event_id")?,
    )
    .await?;
    if event.kind != "event" {
        return Err(ApiError::not_found("Calendar event not found."));
    }
    ensure_write(&actor, &event)?;
    crate::content::authorize_action(state, &actor, &event, "integration").await?;
    let conn = connection.id.to_string();
    let remote_id = event.data["external"][&conn]
        .get("id")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let etag = event.data["external"][&conn]
        .get("etag")
        .and_then(Value::as_str)
        .map(str::to_owned);
    let name = provider(&connection)?.to_owned();
    let token = access_token(state, &actor, &mut connection).await?;
    if event.data.get("sync_conflict").is_some() {
        return Err(ApiError::conflict(
            "Resolve this event's sync conflict before exporting it.",
        ));
    }
    if event.deleted_at.is_some() && remote_id.is_none() {
        return Ok(json!({"deleted":true,"remote_write":false}));
    }
    let base = if name == "google" {
        endpoint(
            state,
            "google_calendar_api",
            "https://www.googleapis.com/calendar/v3",
        )
    } else {
        endpoint(
            state,
            "microsoft_graph_api",
            "https://graph.microsoft.com/v1.0",
        )
    };
    let cal = connection
        .data
        .get("remote_calendar_id")
        .and_then(Value::as_str)
        .unwrap_or("primary");
    let url = if name == "google" {
        let mut parts = vec!["calendars", cal, "events"];
        if let Some(id) = remote_id.as_deref() {
            parts.push(id);
        }
        url_with_segment(base, &parts)?
    } else {
        let mut parts = vec!["me", "events"];
        if let Some(id) = remote_id.as_deref() {
            parts.push(id);
        }
        url_with_segment(base, &parts)?
    };
    let method = if event.deleted_at.is_some() {
        reqwest::Method::DELETE
    } else if remote_id.is_some() {
        reqwest::Method::PATCH
    } else {
        reqwest::Method::POST
    };
    let mut request = state.http.request(method, url).bearer_auth(&token);
    if let Some(etag) = etag {
        request = request.header("If-Match", etag);
    }
    if event.deleted_at.is_none() {
        request = request.json(&remote_body(&event, &name)?);
    }
    // Creation is made idempotent by provider identifiers (Google's permitted
    // base32hex ID, Microsoft's transactionId). A timeout cannot create a second
    // event on a retry.
    if name == "google" && remote_id.is_none() && event.deleted_at.is_none() {
        let mut body = remote_body(&event, &name)?;
        body["id"] = json!(event.id.simple().to_string());
        request = state
            .http
            .post(url_with_segment(base, &["calendars", cal, "events"])?)
            .bearer_auth(&token)
            .json(&body);
    }
    let response = request.send().await?;
    if response.status() == StatusCode::PRECONDITION_FAILED {
        let current = super::get_resource(&state, &actor, event.id).await?;
        let mut data = current.data.clone();
        data["sync_conflict"] = json!({"connection_id":connection.id,"reason":"Remote event changed before export.","detected_at":Utc::now()});
        super::update_resource(&state, &actor, current.id, modification(&current, data)).await?;
        return Err(ApiError::conflict(
            "Remote calendar event changed. Review both versions before retrying.",
        ));
    }
    if event.deleted_at.is_some()
        && [
            StatusCode::NO_CONTENT,
            StatusCode::NOT_FOUND,
            StatusCode::GONE,
        ]
        .contains(&response.status())
    {
        return Ok(json!({"deleted":true}));
    }
    let remote =
        if response.status() == StatusCode::CONFLICT && name == "google" && remote_id.is_none() {
            let id = event.id.simple().to_string();
            let response = state
                .http
                .get(url_with_segment(base, &["calendars", cal, "events", &id])?)
                .bearer_auth(&token)
                .send()
                .await?;
            if !response.status().is_success() {
                return Err(provider_error("Calendar export"));
            }
            response.json::<Value>().await?
        } else {
            if !response.status().is_success() {
                return Err(provider_error("Calendar export"));
            }
            response.json::<Value>().await?
        };
    let id = remote
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| provider_error("Calendar export"))?;
    let current = super::get_resource(&state, &actor, event.id).await?;
    // Keep a concurrent local edit intact. Its queued later version will export
    // again, using the remote revision recorded here.
    let mut data = current.data.clone();
    if !data.get("external").is_some_and(Value::is_object) {
        data["external"] = json!({});
    }
    data["external"][&conn] = json!({"id":id,"etag":remote.get("etag").or_else(||remote.get("@odata.etag")).and_then(Value::as_str).unwrap_or(""),"last_local_version":if current.version==event.version{current.version+1}else{event.version}});
    super::update_resource(&state, &actor, current.id, modification(&current, data)).await?;
    Ok(json!({"event_id":event.id,"remote_id":id}))
}

pub fn ics_escape(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('\r', "")
        .replace('\n', "\\n")
        .replace(';', "\\;")
        .replace(',', "\\,")
}
pub fn render_ics(events: &[Resource]) -> String {
    let mut out="BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Blueprint Studio//Company Calendar//EN\r\nCALSCALE:GREGORIAN\r\n".to_owned();
    for e in events {
        let start = e
            .data
            .get("start")
            .and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok());
        let end = e
            .data
            .get("end")
            .and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok());
        let (Some(start), Some(end)) = (start, end) else {
            continue;
        };
        out.push_str("BEGIN:VEVENT\r\n");
        out.push_str(&format!(
            "UID:{}@blueprint\r\nDTSTAMP:{}\r\n",
            e.id,
            e.updated_at.format("%Y%m%dT%H%M%SZ")
        ));
        if e.data.get("all_day").and_then(Value::as_bool) == Some(true) {
            out.push_str(&format!(
                "DTSTART;VALUE=DATE:{}\r\nDTEND;VALUE=DATE:{}\r\n",
                start.format("%Y%m%d"),
                end.format("%Y%m%d")
            ));
        } else {
            out.push_str(&format!(
                "DTSTART:{}\r\nDTEND:{}\r\n",
                start.with_timezone(&Utc).format("%Y%m%dT%H%M%SZ"),
                end.with_timezone(&Utc).format("%Y%m%dT%H%M%SZ")
            ));
        }
        out.push_str(&format!("SUMMARY:{}\r\n", ics_escape(&e.name)));
        for (key, label) in [("description", "DESCRIPTION"), ("location", "LOCATION")] {
            if let Some(s) = e.data.get(key).and_then(Value::as_str) {
                out.push_str(&format!("{label}:{}\r\n", ics_escape(s)));
            }
        }
        if let Some(rule) = e
            .data
            .get("rrule")
            .and_then(Value::as_str)
            .filter(|s| !s.contains(['\r', '\n']))
        {
            out.push_str(&format!("RRULE:{rule}\r\n"));
        }
        out.push_str("END:VEVENT\r\n");
    }
    out.push_str("END:VCALENDAR\r\n");
    fold_ics(&out)
}

fn fold_ics(text: &str) -> String {
    let mut output = String::with_capacity(text.len());
    for line in text.split_terminator("\r\n") {
        let mut octets = 0;
        for character in line.chars() {
            if octets + character.len_utf8() > 75 {
                output.push_str("\r\n ");
                octets = 1;
            }
            output.push(character);
            octets += character.len_utf8();
        }
        output.push_str("\r\n");
    }
    output
}

pub async fn export_ics(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Response> {
    let events = super::list_resources(&state, &actor, "event", None).await?;
    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("text/calendar; charset=utf-8"),
    );
    headers.insert(
        header::CONTENT_DISPOSITION,
        HeaderValue::from_static("attachment; filename=blueprint-calendar.ics"),
    );
    headers.insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, no-store"),
    );
    Ok((headers, render_ics(&events)).into_response())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn ics_values_cannot_inject_calendar_properties() {
        assert_eq!(
            ics_escape("Meeting\nATTENDEE:evil@example.org;a,b"),
            "Meeting\\nATTENDEE:evil@example.org\\;a\\,b"
        );
    }
    #[test]
    fn remote_delta_urls_cannot_change_origin() {
        assert!(
            verified_graph_link(
                "https://graph.microsoft.com/v1.0",
                "https://evil.invalid/?token=secret"
            )
            .is_err()
        );
        assert!(
            verified_graph_link(
                "https://graph.microsoft.com/v1.0",
                "https://graph.microsoft.com/v1.0/me/calendarView/delta?$deltatoken=x"
            )
            .is_ok()
        );
    }
    #[test]
    fn all_day_and_timezone_values_round_trip_to_real_timestamps() {
        assert_eq!(
            event_timestamp(&json!({"date":"2026-10-20"}), true).unwrap(),
            "2026-10-20T00:00:00+00:00"
        );
        let event=normalized_remote(&json!({"start":{"dateTime":"2026-10-20T09:00:00.0000000","timeZone":"UTC"},"end":{"dateTime":"2026-10-20T10:00:00.0000000","timeZone":"UTC"}}),"microsoft").unwrap();
        assert!(
            event["start"]
                .as_str()
                .unwrap()
                .starts_with("2026-10-20T09:00:00")
        );
    }
    #[test]
    fn outlook_recurrence_and_all_day_dates_are_preserved() {
        let mut event = Resource {
            id: Uuid::new_v4(),
            kind: "event".into(),
            name: "Meeting".into(),
            parent_id: None,
            owner_id: Uuid::new_v4(),
            visibility: "private".into(),
            acl: json!({}),
            data: json!({"start":"2026-10-20T00:00:00+08:00","end":"2026-10-21T00:00:00+08:00","all_day":true,"timezone":"Asia/Hong_Kong","rrule":"FREQ=WEEKLY;BYDAY=TU,TH;COUNT=8"}),
            version: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            deleted_at: None,
        };
        let body = remote_body(&event, "microsoft").unwrap();
        assert_eq!(body["start"]["dateTime"], "2026-10-20T00:00:00");
        assert_eq!(
            body["recurrence"]["pattern"]["daysOfWeek"],
            json!(["tuesday", "thursday"])
        );
        assert_eq!(body["recurrence"]["range"]["numberOfOccurrences"], 8);
        event.data["rrule"] = json!("FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1;UNTIL=20271231T235959Z");
        assert_eq!(
            remote_body(&event, "microsoft").unwrap()["recurrence"]["pattern"]["index"],
            "last"
        );
        event.data["rrule"] = json!("FREQ=DAILY;BYHOUR=12");
        assert!(remote_body(&event, "microsoft").is_err());
    }
    #[test]
    fn calendar_line_folding_counts_utf8_octets() {
        let line = format!("SUMMARY:{}\r\n", "藍圖工作室".repeat(20));
        let folded = fold_ics(&line);
        assert!(folded.split_terminator("\r\n").all(|line| line.len() <= 75));
        assert_eq!(folded.replace("\r\n ", ""), line);
    }
}
