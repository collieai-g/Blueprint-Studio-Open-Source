use crate::*;
use argon2::{
    Argon2, PasswordHasher, PasswordVerifier,
    password_hash::{PasswordHash, SaltString, rand_core::OsRng},
};
use axum::{
    Extension, Router,
    extract::{Path, Query, Request, State},
    http::{HeaderMap, header},
    middleware::Next,
    response::{IntoResponse, Response},
    routing::{delete, get, patch, post},
};
use rand::{Rng, distributions::Alphanumeric};
use sha2::{Digest, Sha256};
use sqlx::Row;

pub fn random_token() -> String {
    rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(48)
        .map(char::from)
        .collect()
}
pub fn token_hash(token: &str) -> String {
    hex::encode(Sha256::digest(token.as_bytes()))
}
pub fn password_hash(password: &str) -> ApiResult<String> {
    if password.chars().count() < 12 || password.len() > 1024 {
        return Err(ApiError::bad_request(
            "Password must contain at least 12 characters and at most 1024 bytes",
        ));
    }
    Argon2::default()
        .hash_password(password.as_bytes(), &SaltString::generate(&mut OsRng))
        .map(|v| v.to_string())
        .map_err(|_| ApiError::internal("Password hashing failed"))
}
fn verify_password(hash: &str, password: &str) -> bool {
    PasswordHash::new(hash).is_ok_and(|hash| {
        Argon2::default()
            .verify_password(password.as_bytes(), &hash)
            .is_ok()
    })
}
static PASSWORD_WORK: std::sync::LazyLock<std::sync::Arc<tokio::sync::Semaphore>> =
    std::sync::LazyLock::new(|| std::sync::Arc::new(tokio::sync::Semaphore::new(4)));
static DUMMY_HASH: std::sync::LazyLock<String> = std::sync::LazyLock::new(|| {
    password_hash("nonexistent-account-password").expect("valid dummy password")
});
fn password_permit() -> ApiResult<tokio::sync::OwnedSemaphorePermit> {
    PASSWORD_WORK.clone().try_acquire_owned().map_err(|_| {
        ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            "Password service is busy; try again shortly",
        )
    })
}
async fn hash_password_async(password: String) -> ApiResult<String> {
    let permit = password_permit()?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        password_hash(&password)
    })
    .await
    .map_err(|_| ApiError::internal("Password service failed"))?
}
async fn verify_password_async(hash: Option<String>, password: String) -> ApiResult<bool> {
    let permit = password_permit()?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        verify_password(hash.as_deref().unwrap_or(&DUMMY_HASH), &password)
    })
    .await
    .map_err(|_| ApiError::internal("Password service failed"))
}

pub async fn load_actor(pool: &PgPool, id: Uuid) -> ApiResult<Actor> {
    let row =
        sqlx::query("SELECT id,email,name,role,groups FROM users WHERE id=$1 AND active=true")
            .bind(id)
            .fetch_optional(pool)
            .await?
            .ok_or_else(|| ApiError::unauthorized("Account is unavailable"))?;
    let raw: Vec<String> = serde_json::from_value(row.get("groups")).unwrap_or_default();
    let valid: Vec<Uuid> = raw.iter().filter_map(|g| Uuid::parse_str(g).ok()).collect();
    let groups = if valid.is_empty() {
        Vec::new()
    } else {
        sqlx::query_scalar::<_, Uuid>(
            "SELECT id FROM resources WHERE kind='group' AND deleted_at IS NULL AND id=ANY($1)",
        )
        .bind(&valid)
        .fetch_all(pool)
        .await?
        .into_iter()
        .map(|id| id.to_string())
        .collect()
    };
    Ok(Actor {
        id: row.get("id"),
        email: row.get("email"),
        name: row.get("name"),
        role: row.get("role"),
        groups,
        scopes: None,
        resource_ids: None,
        token_id: None,
    })
}
pub async fn actor_by_id(state: &AppState, id: Uuid) -> ApiResult<Actor> {
    load_actor(&state.pool, id).await
}
pub async fn actor_for_job(state: &AppState, job: &Job) -> ApiResult<Actor> {
    if let Some(snapshot) = job.payload.get("actor_constraint") {
        let original: Actor = serde_json::from_value(snapshot.clone())?;
        if original.id != job.owner_id {
            return Err(ApiError::forbidden("Job actor mismatch"));
        }
        reload_actor_constraints(&state.pool, &original).await
    } else {
        actor_by_id(state, job.owner_id).await
    }
}
pub async fn reload_actor_constraints(pool: &PgPool, original: &Actor) -> ApiResult<Actor> {
    let mut actor = load_actor(pool, original.id).await?;
    actor.scopes = original.scopes.clone();
    actor.resource_ids = original.resource_ids.clone();
    actor.token_id = original.token_id;
    if let Some(id) = original.token_id {
        let row=sqlx::query("SELECT scopes,resource_ids FROM api_tokens WHERE id=$1 AND user_id=$2 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())").bind(id).bind(original.id).fetch_optional(pool).await?.ok_or_else(||ApiError::unauthorized("Service token has been revoked or expired"))?;
        let current: Vec<String> = serde_json::from_value(row.get("scopes"))?;
        // A token cannot gain scopes mid-run. Existing scopes must still be allowed.
        let checker = Actor {
            scopes: Some(current),
            ..actor.clone()
        };
        if actor
            .scopes
            .as_ref()
            .is_some_and(|scopes| scopes.iter().any(|scope| !checker.scope_allows(scope)))
        {
            return Err(ApiError::forbidden(
                "Service permissions changed; start a new run",
            ));
        }
        let ids: Option<Value> = row.get("resource_ids");
        if let Some(ids) = ids {
            let now: Vec<Uuid> = serde_json::from_value(ids)?;
            actor.resource_ids = Some(match actor.resource_ids {
                Some(old) => old.into_iter().filter(|id| now.contains(id)).collect(),
                None => now,
            });
        }
    }
    Ok(actor)
}

pub async fn optional_actor(state: &AppState, headers: &HeaderMap) -> ApiResult<Option<Actor>> {
    if let Some(auth) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
    {
        let token = auth
            .strip_prefix("Bearer ")
            .ok_or_else(|| ApiError::unauthorized("Bearer token required"))?;
        let row=sqlx::query("UPDATE api_tokens SET last_used_at=now() WHERE token_hash=$1 AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now()) RETURNING id,user_id,scopes,resource_ids").bind(token_hash(token)).fetch_optional(&state.pool).await?.ok_or_else(||ApiError::unauthorized("Invalid or revoked service token"))?;
        let mut actor = load_actor(&state.pool, row.get("user_id")).await?;
        actor.scopes = Some(serde_json::from_value(row.get("scopes"))?);
        actor.resource_ids = row
            .get::<Option<Value>, _>("resource_ids")
            .map(serde_json::from_value)
            .transpose()?;
        actor.token_id = Some(row.get("id"));
        return Ok(Some(actor));
    }
    let cookie = headers
        .get(header::COOKIE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| {
            v.split(';')
                .map(str::trim)
                .find_map(|part| part.strip_prefix("blueprint_session="))
        });
    if let Some(token) = cookie {
        let id: Option<Uuid> = sqlx::query_scalar(
            "SELECT user_id FROM sessions WHERE token_hash=$1 AND expires_at>now()",
        )
        .bind(token_hash(token))
        .fetch_optional(&state.pool)
        .await?;
        if let Some(id) = id {
            return Ok(Some(load_actor(&state.pool, id).await?));
        }
    }
    Ok(None)
}
pub async fn require_auth(State(state): State<AppState>, mut req: Request, next: Next) -> Response {
    match optional_actor(&state, req.headers()).await {
        Ok(Some(actor)) => {
            req.extensions_mut().insert(actor);
            next.run(req).await
        }
        Ok(None) => ApiError::unauthorized("Sign in to continue").into_response(),
        Err(error) => error.into_response(),
    }
}
pub async fn csrf(State(state): State<AppState>, req: Request, next: Next) -> Response {
    if !matches!(
        *req.method(),
        axum::http::Method::GET | axum::http::Method::HEAD | axum::http::Method::OPTIONS
    ) {
        let path = req.uri().path();
        let signed_callback = path.starts_with("/api/v1/public/callbacks/")
            || path.starts_with("/api/v1/public/sign/webhook")
            || path.starts_with("/api/v1/public/wopi/");
        if !signed_callback
            && req
                .headers()
                .get("x-blueprint-request")
                .and_then(|x| x.to_str().ok())
                != Some("1")
            && req.headers().get(header::AUTHORIZATION).is_none()
        {
            return ApiError::forbidden("Missing request verification header").into_response();
        }
        if let Some(origin) = req.headers().get(header::ORIGIN) {
            let parsed_origin = origin.to_str().ok().and_then(|o| url::Url::parse(o).ok());
            let valid = parsed_origin
                .as_ref()
                .zip(url::Url::parse(&state.config.public_url).ok())
                .is_some_and(|(a, b)| a.origin() == b.origin())
                || (path.starts_with("/api/v1/public/wopi/")
                    && parsed_origin
                        .as_ref()
                        .zip(
                            std::env::var("BLUEPRINT_COLLABORA_PUBLIC_URL")
                                .ok()
                                .and_then(|v| url::Url::parse(&v).ok()),
                        )
                        .is_some_and(|(a, b)| a.origin() == b.origin()));
            if !valid {
                return ApiError::forbidden("Cross-origin write rejected").into_response();
            }
        }
    }
    next.run(req).await
}

pub fn public_router() -> Router<AppState> {
    Router::new()
        .route("/status", get(status))
        .route("/setup", post(setup))
        .route("/login", post(login))
        .route("/activate", post(activate))
        .route("/auth/sso/start", get(sso_start))
        .route("/auth/sso/callback", get(sso_callback))
}
pub fn router() -> Router<AppState> {
    Router::new()
        .route("/me", get(me))
        .route("/logout", post(logout))
        .route("/users", get(users).post(create_user))
        .route("/users/{id}", patch(update_user))
        .route("/tokens", get(tokens).post(create_token))
        .route("/tokens/{id}", delete(revoke_token))
        .route("/auth/mfa/setup", post(mfa_setup))
        .route("/auth/mfa/confirm", post(mfa_confirm))
        .route("/auth/password", post(change_password))
}
async fn status(State(state): State<AppState>) -> ApiResult<Json<Value>> {
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users")
        .fetch_one(&state.pool)
        .await?;
    let company: Value =
        sqlx::query_scalar("SELECT company FROM company_settings WHERE singleton=true")
            .fetch_one(&state.pool)
            .await?;
    Ok(Json(
        json!({"initialized":count>0,"company_name":company["name"].as_str().unwrap_or("藍圖工作室"),"version":env!("CARGO_PKG_VERSION"),"setup_available":state.config.setup_token.is_some(),"sso_available":std::env::var("BLUEPRINT_OIDC_CLIENT_ID").is_ok()&&state.config.services.contains_key("keycloak")}),
    ))
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Setup {
    token: String,
    company_name: String,
    email: String,
    name: String,
    password: String,
}
async fn setup(State(state): State<AppState>, Json(input): Json<Setup>) -> ApiResult<Response> {
    let expected = state.config.setup_token.as_deref().ok_or_else(|| {
        ApiError::unavailable("Set BLUEPRINT_SETUP_TOKEN or use the init command")
    })?;
    if token_hash(expected) != token_hash(&input.token) {
        return Err(ApiError::forbidden("Invalid setup token"));
    }
    let actor = initialize(
        &state,
        &input.company_name,
        &input.email,
        &input.name,
        &input.password,
    )
    .await?;
    session_response(&state, actor).await
}
pub async fn initialize(
    state: &AppState,
    company: &str,
    email: &str,
    name: &str,
    password: &str,
) -> ApiResult<Actor> {
    validate_email(email)?;
    if company.trim().is_empty() || name.trim().is_empty() {
        return Err(ApiError::bad_request(
            "Company and administrator name are required",
        ));
    }
    let hash = hash_password_async(password.to_string()).await?;
    let mut tx = state.pool.begin().await?;
    sqlx::query("SELECT pg_advisory_xact_lock(7867340902)")
        .execute(&mut *tx)
        .await?;
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users")
        .fetch_one(&mut *tx)
        .await?;
    if count > 0 {
        return Err(ApiError::conflict(
            "This installation is already initialized",
        ));
    }
    let id = Uuid::new_v4();
    sqlx::query("INSERT INTO users(id,email,name,password_hash,role) VALUES($1,$2,$3,$4,'owner')")
        .bind(id)
        .bind(email.trim().to_lowercase())
        .bind(name.trim())
        .bind(hash)
        .execute(&mut *tx)
        .await?;
    sqlx::query(
        "UPDATE company_settings SET company=$1,modules=$2,updated_at=now() WHERE singleton=true",
    )
    .bind(json!({"name":company.trim(),"locale":"zh-HK","timezone":"Asia/Hong_Kong"}))
    .bind(crate::core::default_modules())
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    let actor = load_actor(&state.pool, id).await?;
    repo::audit(
        &state.pool,
        &actor,
        "installation.initialize",
        None,
        json!({}),
    )
    .await?;
    Ok(actor)
}
fn validate_email(email: &str) -> ApiResult<()> {
    if email.len() > 254 || !email.contains('@') || email.chars().any(char::is_whitespace) {
        Err(ApiError::bad_request("A valid email address is required"))
    } else {
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Login {
    email: String,
    password: String,
    #[serde(default)]
    otp: Option<String>,
}
async fn login(State(state): State<AppState>, Json(input): Json<Login>) -> ApiResult<Response> {
    if input.email.len() > 254 || input.password.len() > 1024 {
        return Err(ApiError::unauthorized("Invalid email or password"));
    }
    let key = token_hash(&input.email.trim().to_lowercase());
    let attempts:i32=sqlx::query_scalar("INSERT INTO login_attempts(key,attempts) VALUES($1,1) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN login_attempts.window_start<now()-interval '15 minutes' THEN 1 ELSE login_attempts.attempts+1 END,window_start=CASE WHEN login_attempts.window_start<now()-interval '15 minutes' THEN now() ELSE login_attempts.window_start END RETURNING attempts").bind(&key).fetch_one(&state.pool).await?;
    if attempts > 15 {
        return Err(ApiError::new(
            StatusCode::TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many sign-in attempts; try again in 15 minutes",
        ));
    }
    let user = sqlx::query(
        "SELECT id,password_hash,totp_secret FROM users WHERE lower(email)=$1 AND active=true",
    )
    .bind(input.email.trim().to_lowercase())
    .fetch_optional(&state.pool)
    .await?;
    // Run a password hash even for unknown users to avoid a cheap enumeration path.
    let valid = verify_password_async(
        user.as_ref()
            .map(|row| row.get::<String, _>("password_hash")),
        input.password,
    )
    .await?;
    if !valid {
        return Err(ApiError::unauthorized("Invalid email or password"));
    }
    let user = user.unwrap();
    let id: Uuid = user.get("id");
    if let Some(encrypted) = user.get::<Option<String>, _>("totp_secret") {
        let secret = decrypt_secret(&state.config, &encrypted)?;
        if !totp(&secret, &input.email)?
            .check_current(input.otp.as_deref().unwrap_or(""))
            .unwrap_or(false)
        {
            return Err(ApiError::new(
                StatusCode::UNAUTHORIZED,
                "mfa_required",
                "A valid authentication code is required",
            ));
        }
    }
    sqlx::query("DELETE FROM login_attempts WHERE key=$1")
        .bind(key)
        .execute(&state.pool)
        .await?;
    let actor = load_actor(&state.pool, id).await?;
    repo::audit(&state.pool, &actor, "auth.login", None, json!({})).await?;
    session_response(&state, actor).await
}
async fn session_response(state: &AppState, actor: Actor) -> ApiResult<Response> {
    let token = random_token();
    sqlx::query("INSERT INTO sessions(token_hash,user_id,expires_at) VALUES($1,$2,now()+interval '12 hours')").bind(token_hash(&token)).bind(actor.id).execute(&state.pool).await?;
    let secure = if state.config.public_url.starts_with("https://") {
        "; Secure"
    } else {
        ""
    };
    let cookie =
        format!("blueprint_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200{secure}");
    Ok(([(header::SET_COOKIE, cookie)], Json(json!({"user":actor}))).into_response())
}
async fn me(Extension(actor): Extension<Actor>) -> Json<Actor> {
    Json(actor)
}
async fn logout(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    headers: HeaderMap,
) -> ApiResult<Response> {
    if let Some(token) = headers
        .get(header::COOKIE)
        .and_then(|v| v.to_str().ok())
        .and_then(|c| {
            c.split(';')
                .map(str::trim)
                .find_map(|p| p.strip_prefix("blueprint_session="))
        })
    {
        sqlx::query("DELETE FROM sessions WHERE token_hash=$1")
            .bind(token_hash(token))
            .execute(&state.pool)
            .await?;
    }
    repo::audit(&state.pool, &actor, "auth.logout", None, json!({})).await?;
    Ok((
        [(
            header::SET_COOKIE,
            "blueprint_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )],
        Json(json!({"ok":true})),
    )
        .into_response())
}

async fn users(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() {
        actor.require_scope("admin:read")?;
    }
    let admin = actor.is_admin();
    let rows=sqlx::query("SELECT id,email,name,role,groups,active,totp_secret IS NOT NULL AS mfa_enabled,created_at FROM users ORDER BY created_at").fetch_all(&state.pool).await?;
    Ok(Json(Value::Array(rows.into_iter().filter(|r|admin||r.get::<bool,_>("active")).map(|r|{let mut item=json!({"id":r.get::<Uuid,_>("id"),"name":r.get::<String,_>("name"),"role":r.get::<String,_>("role"),"groups":r.get::<Value,_>("groups"),"active":r.get::<bool,_>("active"),"created_at":r.get::<DateTime<Utc>,_>("created_at")});if admin||r.get::<Uuid,_>("id")==actor.id{item["email"]=json!(r.get::<String,_>("email"));item["mfa_enabled"]=json!(r.get::<bool,_>("mfa_enabled"));}item}).collect())))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NewUser {
    name: String,
    email: String,
    role: String,
    password: Option<String>,
    #[serde(default)]
    groups: Vec<String>,
}
async fn create_user(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<NewUser>,
) -> ApiResult<Json<Value>> {
    if !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    validate_email(&input.email)?;
    valid_role(&input.role)?;
    if input.role == "owner" {
        return Err(ApiError::bad_request(
            "Only the initial account may be company owner",
        ));
    }
    if input.name.trim().is_empty() {
        return Err(ApiError::bad_request("Name is required"));
    }
    validate_groups(&state.pool, &input.groups).await?;
    let id = Uuid::new_v4();
    let invite = input.password.is_none();
    let hash = hash_password_async(input.password.unwrap_or_else(random_token)).await?;
    sqlx::query("INSERT INTO users(id,email,name,password_hash,role,groups,active) VALUES($1,$2,$3,$4,$5,$6,$7)").bind(id).bind(input.email.trim().to_lowercase()).bind(&input.name).bind(hash).bind(&input.role).bind(json!(input.groups)).bind(!invite).execute(&state.pool).await?;
    let mut response =
        json!({"id":id,"name":input.name,"email":input.email,"role":input.role,"active":!invite});
    if invite {
        let token = random_token();
        sqlx::query("INSERT INTO auth_challenges(token_hash,user_id,purpose,expires_at) VALUES($1,$2,'invite',now()+interval '7 days')").bind(token_hash(&token)).bind(id).execute(&state.pool).await?;
        response["invite_url"] = json!(format!(
            "{}/activate?token={token}",
            state.config.public_url
        ));
    }
    repo::audit(
        &state.pool,
        &actor,
        "user.create",
        None,
        json!({"user_id":id,"role":input.role}),
    )
    .await?;
    Ok(Json(response))
}
#[derive(Deserialize)]
struct Activate {
    token: String,
    password: String,
}
async fn activate(
    State(state): State<AppState>,
    Json(input): Json<Activate>,
) -> ApiResult<Response> {
    let valid:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM auth_challenges WHERE token_hash=$1 AND purpose='invite' AND expires_at>now())").bind(token_hash(&input.token)).fetch_one(&state.pool).await?;
    if !valid {
        return Err(ApiError::unauthorized("Invitation is invalid or expired"));
    }
    let hash = hash_password_async(input.password).await?;
    let mut tx = state.pool.begin().await?;
    let id:Uuid=sqlx::query_scalar("DELETE FROM auth_challenges WHERE token_hash=$1 AND purpose='invite' AND expires_at>now() RETURNING user_id").bind(token_hash(&input.token)).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::unauthorized("Invitation is invalid or expired"))?;
    sqlx::query("UPDATE users SET password_hash=$2,active=true,updated_at=now() WHERE id=$1")
        .bind(id)
        .bind(hash)
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    session_response(&state, load_actor(&state.pool, id).await?).await
}
fn valid_role(role: &str) -> ApiResult<()> {
    if matches!(role, "owner" | "admin" | "member" | "viewer" | "guest") {
        Ok(())
    } else {
        Err(ApiError::bad_request("Invalid role"))
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UserUpdate {
    name: Option<String>,
    role: Option<String>,
    active: Option<bool>,
    groups: Option<Vec<String>>,
}
async fn update_user(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<UserUpdate>,
) -> ApiResult<Json<Value>> {
    if !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    let old_role: String = sqlx::query_scalar("SELECT role FROM users WHERE id=$1")
        .bind(id)
        .fetch_optional(&state.pool)
        .await?
        .ok_or_else(|| ApiError::not_found("User not found"))?;
    if old_role == "owner"
        && (input.active == Some(false) || input.role.as_deref().is_some_and(|r| r != "owner"))
    {
        return Err(ApiError::forbidden(
            "The installation owner cannot be disabled or demoted",
        ));
    }
    if input.role.as_deref() == Some("owner") && old_role != "owner" {
        return Err(ApiError::forbidden("Owner role cannot be assigned here"));
    }
    if let Some(groups) = &input.groups {
        validate_groups(&state.pool, groups).await?;
    }
    if let Some(role) = &input.role {
        valid_role(role)?;
    }
    if input.name.as_deref().is_some_and(|n| n.trim().is_empty()) {
        return Err(ApiError::bad_request("Name cannot be empty"));
    }
    let mut tx = state.pool.begin().await?;
    sqlx::query("UPDATE users SET name=COALESCE($2,name),role=COALESCE($3,role),active=COALESCE($4,active),groups=COALESCE($5,groups),updated_at=now() WHERE id=$1").bind(id).bind(input.name).bind(input.role).bind(input.active).bind(input.groups.map(|g|json!(g))).execute(&mut *tx).await?;
    if input.active == Some(false) {
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
        &actor,
        "user.update",
        None,
        json!({"user_id":id}),
    )
    .await?;
    let row = sqlx::query("SELECT id,email,name,role,groups,active FROM users WHERE id=$1")
        .bind(id)
        .fetch_one(&state.pool)
        .await?;
    Ok(Json(
        json!({"id":row.get::<Uuid,_>("id"),"email":row.get::<String,_>("email"),"name":row.get::<String,_>("name"),"role":row.get::<String,_>("role"),"groups":row.get::<Value,_>("groups"),"active":row.get::<bool,_>("active")}),
    ))
}

async fn validate_groups(pool: &PgPool, groups: &[String]) -> ApiResult<()> {
    if groups.len() > 100 {
        return Err(ApiError::bad_request(
            "An account may belong to at most 100 groups",
        ));
    }
    for value in groups {
        let id =
            Uuid::parse_str(value).map_err(|_| ApiError::bad_request("Group IDs must be UUIDs"))?;
        let exists:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM resources WHERE id=$1 AND kind='group' AND deleted_at IS NULL)").bind(id).fetch_one(pool).await?;
        if !exists {
            return Err(ApiError::bad_request("Group does not exist"));
        }
    }
    Ok(())
}

async fn tokens(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Interactive session or administrator token required",
        ));
    }
    actor.require_scope("integrations:read")?;
    let rows=sqlx::query("SELECT id,user_id,name,scopes,resource_ids,expires_at,revoked_at,last_used_at,created_at FROM api_tokens WHERE user_id=$1 OR $2 ORDER BY created_at DESC").bind(actor.id).bind(actor.is_admin()).fetch_all(&state.pool).await?;
    Ok(Json(Value::Array(rows.into_iter().map(|r|json!({"id":r.get::<Uuid,_>("id"),"user_id":r.get::<Uuid,_>("user_id"),"name":r.get::<String,_>("name"),"scopes":r.get::<Value,_>("scopes"),"resource_ids":r.get::<Option<Value>,_>("resource_ids"),"expires_at":r.get::<Option<DateTime<Utc>>,_>("expires_at"),"revoked_at":r.get::<Option<DateTime<Utc>>,_>("revoked_at"),"last_used_at":r.get::<Option<DateTime<Utc>>,_>("last_used_at"),"created_at":r.get::<DateTime<Utc>,_>("created_at")})).collect())))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TokenInput {
    name: String,
    scopes: Vec<String>,
    resource_ids: Option<Vec<Uuid>>,
    expires_at: Option<DateTime<Utc>>,
}
async fn create_token(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<TokenInput>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("integrations:write")?;
    if actor.token_id.is_some() {
        return Err(ApiError::forbidden(
            "Use an interactive session to create tokens",
        ));
    }
    if input.name.trim().is_empty() || input.scopes.is_empty() || input.scopes.len() > 30 {
        return Err(ApiError::bad_request(
            "Token name and explicit scopes are required",
        ));
    }
    for scope in &input.scopes {
        let namespace = scope.split(':').next().unwrap_or("");
        if !matches!(
            namespace,
            "content" | "business" | "workflows" | "integrations" | "admin"
        ) || !scope.contains(':')
        {
            return Err(ApiError::bad_request("Invalid token scope"));
        }
        if scope.starts_with("admin:") && !actor.is_admin() {
            return Err(ApiError::forbidden(
                "Administrator scope requires administrator role",
            ));
        }
    }
    if let Some(ids) = &input.resource_ids {
        if input.scopes.iter().any(|s| s.starts_with("admin:")) {
            return Err(ApiError::bad_request(
                "Administrator scopes are global and cannot be combined with resource IDs",
            ));
        }
        for id in ids {
            let r = repo::get(&state.pool, &actor, *id).await?;
            if !actor.can_manage(&r) {
                return Err(ApiError::forbidden(
                    "Only managed resources may be delegated",
                ));
            }
        }
    }
    if input.expires_at.is_some_and(|d| d <= Utc::now()) {
        return Err(ApiError::bad_request("Expiry must be in the future"));
    }
    let token = format!("bp_{}", random_token());
    let id = Uuid::new_v4();
    sqlx::query("INSERT INTO api_tokens(id,user_id,name,token_hash,scopes,resource_ids,expires_at) VALUES($1,$2,$3,$4,$5,$6,$7)").bind(id).bind(actor.id).bind(&input.name).bind(token_hash(&token)).bind(json!(input.scopes)).bind(input.resource_ids.map(|i|json!(i))).bind(input.expires_at).execute(&state.pool).await?;
    repo::audit(
        &state.pool,
        &actor,
        "token.create",
        None,
        json!({"token_id":id}),
    )
    .await?;
    Ok(Json(
        json!({"id":id,"name":input.name,"token":token,"scopes":input.scopes}),
    ))
}
async fn revoke_token(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Interactive session or administrator token required",
        ));
    }
    actor.require_scope("integrations:write")?;
    let result =
        sqlx::query("UPDATE api_tokens SET revoked_at=now() WHERE id=$1 AND (user_id=$2 OR $3)")
            .bind(id)
            .bind(actor.id)
            .bind(actor.is_admin())
            .execute(&state.pool)
            .await?;
    if result.rows_affected() == 0 {
        return Err(ApiError::not_found("Token not found"));
    }
    repo::audit(
        &state.pool,
        &actor,
        "token.revoke",
        None,
        json!({"token_id":id}),
    )
    .await?;
    Ok(Json(json!({"ok":true})))
}

fn totp(secret: &str, email: &str) -> ApiResult<totp_rs::TOTP> {
    let bytes = totp_rs::Secret::Encoded(secret.into())
        .to_bytes()
        .map_err(|_| ApiError::internal("Invalid MFA secret"))?;
    totp_rs::TOTP::new(
        totp_rs::Algorithm::SHA1,
        6,
        1,
        30,
        bytes,
        Some("Blueprint Studio".into()),
        email.into(),
    )
    .map_err(|_| ApiError::internal("Invalid MFA configuration"))
}
async fn mfa_setup(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() {
        return Err(ApiError::forbidden("Interactive session required"));
    }
    let enabled: bool = sqlx::query_scalar("SELECT totp_secret IS NOT NULL FROM users WHERE id=$1")
        .bind(actor.id)
        .fetch_one(&state.pool)
        .await?;
    if enabled {
        return Err(ApiError::conflict("MFA is already enabled"));
    }
    let secret = totp_rs::Secret::generate_secret().to_encoded().to_string();
    let token = random_token();
    sqlx::query("INSERT INTO auth_challenges(token_hash,user_id,purpose,data,expires_at) VALUES($1,$2,'mfa',$3,now()+interval '10 minutes')").bind(token_hash(&token)).bind(actor.id).bind(json!({"secret":encrypt_secret(&state.config,&secret)?})).execute(&state.pool).await?;
    Ok(Json(
        json!({"token":token,"secret":secret,"uri":totp(&secret,&actor.email)?.get_url()}),
    ))
}
#[derive(Deserialize)]
struct MfaConfirm {
    token: String,
    code: String,
}
async fn mfa_confirm(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<MfaConfirm>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() {
        return Err(ApiError::forbidden("Interactive session required"));
    }
    let mut tx = state.pool.begin().await?;
    let data:Value=sqlx::query_scalar("SELECT data FROM auth_challenges WHERE token_hash=$1 AND user_id=$2 AND purpose='mfa' AND expires_at>now() FOR UPDATE").bind(token_hash(&input.token)).bind(actor.id).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::unauthorized("MFA setup expired"))?;
    let encrypted = data["secret"]
        .as_str()
        .ok_or_else(|| ApiError::internal("Invalid MFA setup"))?;
    let secret = decrypt_secret(&state.config, encrypted)?;
    if !totp(&secret, &actor.email)?
        .check_current(&input.code)
        .unwrap_or(false)
    {
        return Err(ApiError::bad_request("Invalid authentication code"));
    }
    let enabled =
        sqlx::query("UPDATE users SET totp_secret=$2 WHERE id=$1 AND totp_secret IS NULL")
            .bind(actor.id)
            .bind(encrypted)
            .execute(&mut *tx)
            .await?;
    if enabled.rows_affected() != 1 {
        return Err(ApiError::conflict("MFA is already enabled"));
    }
    sqlx::query("DELETE FROM auth_challenges WHERE token_hash=$1")
        .bind(token_hash(&input.token))
        .execute(&mut *tx)
        .await?;
    tx.commit().await?;
    repo::audit(&state.pool, &actor, "mfa.enable", None, json!({})).await?;
    Ok(Json(json!({"enabled":true})))
}
#[derive(Deserialize)]
struct PasswordChange {
    current_password: String,
    password: String,
}
async fn change_password(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<PasswordChange>,
) -> ApiResult<Json<Value>> {
    if actor.token_id.is_some() {
        return Err(ApiError::forbidden("Interactive session required"));
    }
    let old: String = sqlx::query_scalar("SELECT password_hash FROM users WHERE id=$1")
        .bind(actor.id)
        .fetch_one(&state.pool)
        .await?;
    if !verify_password_async(Some(old), input.current_password).await? {
        return Err(ApiError::unauthorized("Current password is incorrect"));
    }
    let hash = hash_password_async(input.password).await?;
    sqlx::query("UPDATE users SET password_hash=$2 WHERE id=$1")
        .bind(actor.id)
        .bind(hash)
        .execute(&state.pool)
        .await?;
    sqlx::query("DELETE FROM sessions WHERE user_id=$1")
        .bind(actor.id)
        .execute(&state.pool)
        .await?;
    repo::audit(&state.pool, &actor, "auth.password_change", None, json!({})).await?;
    Ok(Json(json!({"ok":true,"sign_in_required":true})))
}

fn oidc_issuer(state: &AppState) -> ApiResult<String> {
    let internal = state
        .config
        .services
        .get("keycloak")
        .ok_or_else(|| ApiError::unavailable("Configure the enterprise identity provider"))?;
    let realm = std::env::var("BLUEPRINT_OIDC_REALM").unwrap_or_else(|_| "blueprint".into());
    if realm.is_empty()
        || !realm
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
    {
        return Err(ApiError::unavailable("Invalid identity realm"));
    }
    Ok(std::env::var("BLUEPRINT_OIDC_ISSUER")
        .ok()
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| {
            if internal.contains("/realms/") {
                internal.clone()
            } else {
                format!("{}/realms/{realm}", internal.trim_end_matches('/'))
            }
        })
        .trim_end_matches('/')
        .into())
}
fn oidc_internal_endpoint(state: &AppState, issuer: &str, endpoint: &str) -> ApiResult<String> {
    let base =
        url::Url::parse(issuer).map_err(|_| ApiError::unavailable("Invalid identity issuer"))?;
    let public = url::Url::parse(endpoint)
        .map_err(|_| ApiError::unavailable("Invalid identity endpoint"))?;
    if public.origin() != base.origin()
        || !public
            .path()
            .starts_with(&format!("{}/", base.path().trim_end_matches('/')))
        || public.fragment().is_some()
        || !public.username().is_empty()
        || public.password().is_some()
    {
        return Err(ApiError::unavailable(
            "Identity endpoint is outside the configured issuer",
        ));
    }
    let internal = state
        .config
        .services
        .get("keycloak")
        .ok_or_else(|| ApiError::unavailable("Identity provider not configured"))?;
    let mut target = url::Url::parse(internal)
        .map_err(|_| ApiError::unavailable("Invalid internal identity URL"))?;
    let suffix = public
        .path()
        .strip_prefix(base.path().trim_end_matches('/'))
        .unwrap_or("");
    let realm_path = if target.path().contains("/realms/") {
        target.path().trim_end_matches('/').to_string()
    } else {
        let realm = base.path().rsplit('/').next().unwrap_or("blueprint");
        format!("{}/realms/{realm}", target.path().trim_end_matches('/'))
    };
    target.set_path(&format!("{realm_path}{suffix}"));
    target.set_query(public.query());
    target.set_fragment(None);
    Ok(target.to_string())
}
async fn oidc_discovery(state: &AppState) -> ApiResult<(String, Value)> {
    let issuer = oidc_issuer(state)?;
    let endpoint = oidc_internal_endpoint(
        state,
        &issuer,
        &format!("{issuer}/.well-known/openid-configuration"),
    )?;
    let discovery: Value = state
        .http
        .get(endpoint)
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    if discovery["issuer"].as_str() != Some(&issuer) {
        return Err(ApiError::unavailable("Identity issuer mismatch"));
    }
    for key in [
        "authorization_endpoint",
        "token_endpoint",
        "userinfo_endpoint",
        "jwks_uri",
    ] {
        oidc_internal_endpoint(
            state,
            &issuer,
            discovery[key]
                .as_str()
                .ok_or_else(|| ApiError::unavailable("Identity discovery is incomplete"))?,
        )?;
    }
    Ok((issuer, discovery))
}
async fn sso_start(State(state): State<AppState>) -> ApiResult<Response> {
    let (_, discovery) = oidc_discovery(&state).await?;
    let client = std::env::var("BLUEPRINT_OIDC_CLIENT_ID")
        .map_err(|_| ApiError::unavailable("Configure BLUEPRINT_OIDC_CLIENT_ID"))?;
    let endpoint = discovery["authorization_endpoint"]
        .as_str()
        .ok_or_else(|| ApiError::unavailable("Identity discovery is incomplete"))?;
    let nonce = random_token();
    let verifier = random_token();
    use base64::Engine;
    let challenge = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .encode(Sha256::digest(verifier.as_bytes()));
    sqlx::query("INSERT INTO auth_challenges(token_hash,purpose,data,expires_at) VALUES($1,'oidc',$2,now()+interval '10 minutes')").bind(token_hash(&nonce)).bind(json!({"verifier":encrypt_secret(&state.config,&verifier)?,"nonce":nonce})).execute(&state.pool).await?;
    let mut url = url::Url::parse(endpoint)
        .map_err(|_| ApiError::unavailable("Invalid identity endpoint"))?;
    url.query_pairs_mut().extend_pairs([
        ("client_id", client.as_str()),
        ("response_type", "code"),
        ("scope", "openid email profile"),
        (
            "redirect_uri",
            &format!("{}/api/v1/auth/sso/callback", state.config.public_url),
        ),
        ("state", &nonce),
        ("nonce", &nonce),
        ("code_challenge", &challenge),
        ("code_challenge_method", "S256"),
    ]);
    let secure = if state.config.public_url.starts_with("https") {
        "; Secure"
    } else {
        ""
    };
    Ok((StatusCode::FOUND,[(header::LOCATION,url.to_string()),(header::SET_COOKIE,format!("blueprint_sso={nonce}; Path=/api/v1/auth/sso; HttpOnly; SameSite=Lax; Max-Age=600{secure}"))]).into_response())
}
#[derive(Deserialize)]
struct SsoQuery {
    state: String,
    code: String,
}
async fn sso_callback(
    State(state): State<AppState>,
    headers: HeaderMap,
    Query(input): Query<SsoQuery>,
) -> ApiResult<Response> {
    let cookie = headers
        .get(header::COOKIE)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| {
            v.split(';')
                .map(str::trim)
                .find_map(|v| v.strip_prefix("blueprint_sso="))
        })
        .ok_or_else(|| ApiError::unauthorized("Identity state cookie missing"))?;
    if token_hash(cookie) != token_hash(&input.state) {
        return Err(ApiError::unauthorized("Identity state mismatch"));
    }
    let data:Value=sqlx::query_scalar("DELETE FROM auth_challenges WHERE token_hash=$1 AND purpose='oidc' AND expires_at>now() RETURNING data").bind(token_hash(&input.state)).fetch_optional(&state.pool).await?.ok_or_else(||ApiError::unauthorized("Identity request expired"))?;
    let (issuer, discovery) = oidc_discovery(&state).await?;
    let client = std::env::var("BLUEPRINT_OIDC_CLIENT_ID")
        .map_err(|_| ApiError::unavailable("Identity client missing"))?;
    let secret = std::env::var("BLUEPRINT_OIDC_CLIENT_SECRET").unwrap_or_default();
    let verifier = decrypt_secret(
        &state.config,
        data["verifier"]
            .as_str()
            .ok_or_else(|| ApiError::internal("Missing identity verifier"))?,
    )?;
    let token_endpoint = oidc_internal_endpoint(
        &state,
        &issuer,
        discovery["token_endpoint"]
            .as_str()
            .ok_or_else(|| ApiError::unavailable("Identity endpoint missing"))?,
    )?;
    let tokens: Value = state
        .http
        .post(token_endpoint)
        .form(&[
            ("grant_type", "authorization_code"),
            ("client_id", client.as_str()),
            ("client_secret", secret.as_str()),
            ("code", input.code.as_str()),
            (
                "redirect_uri",
                &format!("{}/api/v1/auth/sso/callback", state.config.public_url),
            ),
            ("code_verifier", verifier.as_str()),
        ])
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let id_token = tokens["id_token"]
        .as_str()
        .ok_or_else(|| ApiError::unauthorized("Identity token missing"))?;
    let header = jsonwebtoken::decode_header(id_token)
        .map_err(|_| ApiError::unauthorized("Invalid identity token"))?;
    if header.alg != jsonwebtoken::Algorithm::RS256 {
        return Err(ApiError::unauthorized("Identity token must use RS256"));
    }
    let jwks: jsonwebtoken::jwk::JwkSet = state
        .http
        .get(oidc_internal_endpoint(
            &state,
            &issuer,
            discovery["jwks_uri"].as_str().unwrap(),
        )?)
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let key = jwks
        .find(
            header
                .kid
                .as_deref()
                .ok_or_else(|| ApiError::unauthorized("Identity signing key missing"))?,
        )
        .ok_or_else(|| ApiError::unauthorized("Unknown identity signing key"))?;
    let key = jsonwebtoken::DecodingKey::from_jwk(key)
        .map_err(|_| ApiError::unauthorized("Invalid identity signing key"))?;
    let mut validation = jsonwebtoken::Validation::new(jsonwebtoken::Algorithm::RS256);
    validation.set_issuer(&[&issuer]);
    validation.set_audience(&[&client]);
    validation.set_required_spec_claims(&["exp", "iss", "aud", "sub"]);
    let claims = jsonwebtoken::decode::<Value>(id_token, &key, &validation)
        .map_err(|_| ApiError::unauthorized("Identity token verification failed"))?
        .claims;
    if claims["nonce"] != data["nonce"] || claims["sub"].as_str().is_none_or(str::is_empty) {
        return Err(ApiError::unauthorized(
            "Identity token nonce or subject mismatch",
        ));
    }
    if claims["azp"].as_str().is_some_and(|azp| azp != client) {
        return Err(ApiError::unauthorized(
            "Identity token authorized party mismatch",
        ));
    }
    let endpoint = oidc_internal_endpoint(
        &state,
        &issuer,
        discovery["userinfo_endpoint"]
            .as_str()
            .ok_or_else(|| ApiError::unavailable("Identity userinfo missing"))?,
    )?;
    let user: Value = state
        .http
        .get(endpoint)
        .bearer_auth(
            tokens["access_token"]
                .as_str()
                .ok_or_else(|| ApiError::unauthorized("Identity token missing"))?,
        )
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    if user["email_verified"] != true || user["sub"] != claims["sub"] {
        return Err(ApiError::unauthorized(
            "Identity provider must verify the email address and subject",
        ));
    }
    let email = user["email"]
        .as_str()
        .ok_or_else(|| ApiError::unauthorized("Identity email missing"))?
        .to_lowercase();
    let subject = claims["sub"].as_str().unwrap();
    let mut tx = state.pool.begin().await?;
    let id = if let Some(id) = sqlx::query_scalar::<_, Uuid>(
        "SELECT user_id FROM external_identities WHERE issuer=$1 AND subject=$2",
    )
    .bind(&issuer)
    .bind(subject)
    .fetch_optional(&mut *tx)
    .await?
    {
        id
    } else {
        let id: Uuid =
            sqlx::query_scalar("SELECT id FROM users WHERE lower(email)=$1 AND active=true")
                .bind(email)
                .fetch_optional(&mut *tx)
                .await?
                .ok_or_else(|| {
                    ApiError::forbidden("Ask an administrator to invite this account")
                })?;
        sqlx::query("INSERT INTO external_identities(issuer,subject,user_id) VALUES($1,$2,$3)")
            .bind(&issuer)
            .bind(subject)
            .bind(id)
            .execute(&mut *tx)
            .await?;
        id
    };
    tx.commit().await?;
    let actor = load_actor(&state.pool, id).await?;
    repo::audit(&state.pool, &actor, "auth.sso_login", None, json!({})).await?;
    let mut response = session_response(&state, actor).await?;
    *response.status_mut() = StatusCode::FOUND;
    response
        .headers_mut()
        .insert(header::LOCATION, header::HeaderValue::from_static("/"));
    Ok(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn password_roundtrip_and_rejection() {
        let hashed = password_hash("a-strong-test-password").unwrap();
        assert!(verify_password(&hashed, "a-strong-test-password"));
        assert!(!verify_password(&hashed, "wrong-password"));
        assert!(password_hash("short").is_err());
    }
    #[test]
    fn tokens_have_entropy() {
        assert_ne!(random_token(), random_token());
        assert_eq!(token_hash("secret").len(), 64);
    }
}
