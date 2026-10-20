pub mod auth;
pub mod business;
pub mod content;
pub mod core;
pub mod openapi;
pub mod repo;
pub mod workflows;

use axum::{
    Json,
    http::StatusCode,
    response::{IntoResponse, Response},
};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sqlx::{FromRow, PgPool};
use std::{collections::HashMap, path::PathBuf, sync::Arc};
use uuid::Uuid;

pub type ApiResult<T> = Result<T, ApiError>;

#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub code: String,
    pub message: String,
}
impl ApiError {
    pub fn new(status: StatusCode, code: &str, message: impl Into<String>) -> Self {
        Self {
            status,
            code: code.into(),
            message: message.into(),
        }
    }
    pub fn bad_request(m: impl Into<String>) -> Self {
        Self::new(StatusCode::BAD_REQUEST, "bad_request", m)
    }
    pub fn unauthorized(m: impl Into<String>) -> Self {
        Self::new(StatusCode::UNAUTHORIZED, "unauthorized", m)
    }
    pub fn forbidden(m: impl Into<String>) -> Self {
        Self::new(StatusCode::FORBIDDEN, "forbidden", m)
    }
    pub fn not_found(m: impl Into<String>) -> Self {
        Self::new(StatusCode::NOT_FOUND, "not_found", m)
    }
    pub fn conflict(m: impl Into<String>) -> Self {
        Self::new(StatusCode::CONFLICT, "conflict", m)
    }
    pub fn unavailable(m: impl Into<String>) -> Self {
        Self::new(StatusCode::SERVICE_UNAVAILABLE, "needs_configuration", m)
    }
    pub fn internal(m: impl Into<String>) -> Self {
        Self::new(StatusCode::INTERNAL_SERVER_ERROR, "internal", m)
    }
}
impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        (
            self.status,
            Json(json!({"error":{"code":self.code,"message":self.message}})),
        )
            .into_response()
    }
}
impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        tracing::error!(error=%e,"database operation failed");
        match e {
            sqlx::Error::RowNotFound => Self::not_found("Record not found"),
            sqlx::Error::Database(ref d) if d.is_unique_violation() => {
                Self::conflict("A matching record already exists")
            }
            _ => Self::internal("Database operation failed"),
        }
    }
}
impl From<std::io::Error> for ApiError {
    fn from(e: std::io::Error) -> Self {
        tracing::error!(error=%e,"storage operation failed");
        Self::internal("Storage operation failed")
    }
}
impl From<serde_json::Error> for ApiError {
    fn from(_: serde_json::Error) -> Self {
        Self::bad_request("Invalid JSON data")
    }
}
impl From<reqwest::Error> for ApiError {
    fn from(e: reqwest::Error) -> Self {
        tracing::warn!(timeout = e.is_timeout(), "External service request failed");
        Self::unavailable("External service unavailable")
    }
}
impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
impl std::error::Error for ApiError {}

#[derive(Clone)]
pub struct Config {
    pub database_url: String,
    pub data_dir: PathBuf,
    pub bind: String,
    pub public_url: String,
    pub setup_token: Option<String>,
    pub services: HashMap<String, String>,
    pub secret_key: [u8; 32],
    pub ui_dir: PathBuf,
}
impl Config {
    pub fn from_env() -> ApiResult<Self> {
        let data_dir = if let Ok(path) = std::env::var("BLUEPRINT_DATA_DIR") {
            if path.trim().is_empty() {
                return Err(ApiError::bad_request(
                    "BLUEPRINT_DATA_DIR must be a nonempty path",
                ));
            }
            PathBuf::from(path)
        } else if let Some(base) = std::env::var_os("LOCALAPPDATA") {
            PathBuf::from(base).join("BlueprintStudio").join("data")
        } else if let Some(base) = std::env::var_os("XDG_DATA_HOME") {
            PathBuf::from(base).join("blueprint-studio")
        } else if let Some(base) = std::env::var_os("HOME") {
            PathBuf::from(base).join(".local/share/blueprint-studio")
        } else {
            return Err(ApiError::bad_request(
                "Set BLUEPRINT_DATA_DIR to a private directory outside the source checkout",
            ));
        };
        std::fs::create_dir_all(&data_dir)?;
        let secret_key = if let Ok(key) = std::env::var("BLUEPRINT_MASTER_KEY") {
            let decoded = hex::decode(&key).map_err(|_| {
                ApiError::bad_request("BLUEPRINT_MASTER_KEY must be 64 hex characters")
            })?;
            decoded
                .try_into()
                .map_err(|_| ApiError::bad_request("BLUEPRINT_MASTER_KEY must contain 32 bytes"))?
        } else {
            let path = data_dir.join("master.key");
            if path.exists() {
                let bytes = std::fs::read(&path)?;
                bytes
                    .try_into()
                    .map_err(|_| ApiError::internal("Invalid persisted master key"))?
            } else {
                use rand::RngCore;
                use std::io::Write;
                let mut key = [0u8; 32];
                rand::rngs::OsRng.fill_bytes(&mut key);
                let mut opts = std::fs::OpenOptions::new();
                opts.write(true).create_new(true);
                #[cfg(unix)]
                {
                    use std::os::unix::fs::OpenOptionsExt;
                    opts.mode(0o600);
                }
                let mut file = opts.open(&path)?;
                file.write_all(&key)?;
                file.sync_all()?;
                key
            }
        };
        let mut services = HashMap::new();
        for name in [
            "gotenberg",
            "tika",
            "clamav",
            "documenso",
            "collabora",
            "keycloak",
            "smtp",
        ] {
            if let Ok(url) = std::env::var(format!("BLUEPRINT_{}_URL", name.to_uppercase())) {
                if !url.is_empty() {
                    services.insert(name.into(), url);
                }
            }
        }
        let bind = std::env::var("BLUEPRINT_BIND").unwrap_or_else(|_| "127.0.0.1:8788".into());
        let public_url = std::env::var("BLUEPRINT_PUBLIC_URL")
            .unwrap_or_else(|_| format!("http://{}", bind.replace("0.0.0.0", "localhost")));
        let parsed = url::Url::parse(&public_url)
            .map_err(|_| ApiError::bad_request("Invalid BLUEPRINT_PUBLIC_URL"))?;
        if !matches!(parsed.scheme(), "http" | "https")
            || parsed.host_str().is_none()
            || !parsed.username().is_empty()
            || parsed.password().is_some()
        {
            return Err(ApiError::bad_request(
                "Public URL must be an HTTP(S) origin",
            ));
        }
        Ok(Self {
            database_url: std::env::var("BLUEPRINT_DATABASE_URL").map_err(|_| {
                ApiError::unavailable(
                    "Set BLUEPRINT_DATABASE_URL to a dedicated PostgreSQL database",
                )
            })?,
            data_dir,
            bind,
            public_url: public_url.trim_end_matches('/').into(),
            setup_token: std::env::var("BLUEPRINT_SETUP_TOKEN")
                .ok()
                .filter(|x| !x.is_empty()),
            services,
            secret_key,
            ui_dir: PathBuf::from(
                std::env::var("BLUEPRINT_UI_DIR").unwrap_or_else(|_| "ui/dist".into()),
            ),
        })
    }
}

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub config: Arc<Config>,
    pub http: reqwest::Client,
}
impl AppState {
    pub async fn connect(config: Config) -> ApiResult<Self> {
        let pool = sqlx::postgres::PgPoolOptions::new()
            .max_connections(30)
            .connect(&config.database_url)
            .await?;
        sqlx::migrate!().run(&pool).await.map_err(|e| {
            tracing::error!(error=%e,"migration failed");
            ApiError::internal("Database migration failed")
        })?;
        let mut builder = reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(30))
            .redirect(reqwest::redirect::Policy::none());
        if let Some(path) = std::env::var("BLUEPRINT_CA_FILE")
            .ok()
            .or_else(|| std::env::var("SSL_CERT_FILE").ok())
        {
            let pem = std::fs::read(path)?;
            for certificate in reqwest::Certificate::from_pem_bundle(&pem)
                .map_err(|_| ApiError::bad_request("Invalid configured CA bundle"))?
            {
                builder = builder.add_root_certificate(certificate);
            }
        }
        let http = builder
            .build()
            .map_err(|_| ApiError::internal("HTTP client initialization failed"))?;
        Ok(Self {
            pool,
            config: Arc::new(config),
            http,
        })
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Actor {
    pub id: Uuid,
    pub email: String,
    pub name: String,
    pub role: String,
    pub groups: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scopes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub resource_ids: Option<Vec<Uuid>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub token_id: Option<Uuid>,
}
impl Actor {
    pub fn is_admin(&self) -> bool {
        self.resource_ids.is_none()
            && matches!(self.role.as_str(), "owner" | "admin")
            && self.scope_allows("admin:write")
    }
    pub fn scope_allows(&self, scope: &str) -> bool {
        self.scopes.as_ref().is_none_or(|v| {
            v.iter().any(|s| {
                s == "*"
                    || s == scope
                    || s == &format!("{}:*", scope.split(':').next().unwrap_or(""))
            })
        })
    }
    pub fn require_scope(&self, scope: &str) -> ApiResult<()> {
        if self.scope_allows(scope) {
            Ok(())
        } else {
            Err(ApiError::forbidden(format!("Required scope: {scope}")))
        }
    }
    fn resource_allowed(&self, r: &Resource) -> bool {
        self.resource_ids
            .as_ref()
            .is_none_or(|ids| ids.contains(&r.id))
    }
    fn acl_role<'a>(&self, r: &'a Resource) -> Option<&'a str> {
        r.acl
            .get(format!("user:{}", self.id))
            .and_then(Value::as_str)
            .or_else(|| {
                self.groups
                    .iter()
                    .find_map(|g| r.acl.get(format!("group:{g}")).and_then(Value::as_str))
            })
    }
    pub fn can_read(&self, r: &Resource) -> bool {
        (r.kind != "collection" || r.owner_id == self.id)
            && self.resource_allowed(r)
            && self.scope_allows(&format!("{}:read", scope_for(&r.kind)))
            && (self.is_admin()
                || r.owner_id == self.id
                || self
                    .acl_role(r)
                    .is_some_and(|role| role != "uploader" || r.kind == "folder")
                || (r.visibility == "company" && self.role != "guest"))
    }
    pub fn can_write(&self, r: &Resource) -> bool {
        (self.role != "viewer"
            || (r.kind == "collection" && r.owner_id == self.id && r.visibility == "private"))
            && self.resource_allowed(r)
            && self.scope_allows(&format!("{}:write", scope_for(&r.kind)))
            && (self.is_admin()
                || r.owner_id == self.id
                || self.acl_role(r).is_some_and(|role| {
                    role == "editor" || (role == "uploader" && r.kind == "folder")
                }))
    }
    pub fn can_manage(&self, r: &Resource) -> bool {
        (self.role != "viewer"
            || (r.kind == "collection" && r.owner_id == self.id && r.visibility == "private"))
            && (r.kind != "collection" || r.owner_id == self.id)
            && self.resource_allowed(r)
            && self.scope_allows(&format!("{}:write", scope_for(&r.kind)))
            && (self.is_admin() || r.owner_id == self.id)
    }
}
pub fn scope_for(kind: &str) -> &str {
    match kind {
        "file" | "folder" | "link" | "collection" | "comment" | "share" | "upload_session"
        | "note" | "canvas" | "hub" => "content",
        "workflow" | "workflow_run" | "workflow_step" => "workflows",
        "connector" | "agent" => "integrations",
        "classification" | "retention" | "legal_hold" | "archive" | "alert" | "report"
        | "group" => "admin",
        _ => "business",
    }
}

#[derive(Clone, Debug, Serialize, Deserialize, FromRow)]
pub struct Resource {
    pub id: Uuid,
    pub kind: String,
    pub name: String,
    pub parent_id: Option<Uuid>,
    pub owner_id: Uuid,
    pub visibility: String,
    pub acl: Value,
    pub data: Value,
    pub version: i64,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub deleted_at: Option<DateTime<Utc>>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NewResource {
    pub kind: String,
    pub name: String,
    #[serde(default)]
    pub parent_id: Option<Uuid>,
    #[serde(default)]
    pub visibility: Option<String>,
    #[serde(default)]
    pub acl: Option<Value>,
    #[serde(default = "empty_object")]
    pub data: Value,
}
fn empty_object() -> Value {
    json!({})
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UpdateResource {
    pub name: Option<String>,
    pub parent_id: Option<Uuid>,
    pub visibility: Option<String>,
    pub acl: Option<Value>,
    pub data: Option<Value>,
    pub expected_version: i64,
}
#[derive(Clone, Debug, Serialize, Deserialize, FromRow)]
pub struct Job {
    pub id: Uuid,
    pub owner_id: Uuid,
    pub kind: String,
    pub status: String,
    pub payload: Value,
    pub result: Value,
    pub error: Option<String>,
    pub attempts: i32,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

pub fn encrypt_secret(config: &Config, text: &str) -> ApiResult<String> {
    use aes_gcm::{
        Aes256Gcm, KeyInit,
        aead::{Aead, OsRng, rand_core::RngCore},
    };
    let cipher = Aes256Gcm::new_from_slice(&config.secret_key)
        .map_err(|_| ApiError::internal("Encryption unavailable"))?;
    let mut nonce = [0u8; 12];
    OsRng.fill_bytes(&mut nonce);
    let encrypted = cipher
        .encrypt((&nonce).into(), text.as_bytes())
        .map_err(|_| ApiError::internal("Encryption failed"))?;
    Ok(format!(
        "v1:{}:{}",
        hex::encode(nonce),
        hex::encode(encrypted)
    ))
}
pub fn decrypt_secret(config: &Config, text: &str) -> ApiResult<String> {
    use aes_gcm::{Aes256Gcm, KeyInit, aead::Aead};
    let parts: Vec<_> = text.split(':').collect();
    if parts.len() != 3 || parts[0] != "v1" {
        return Err(ApiError::internal("Invalid encrypted secret"));
    }
    let nonce =
        hex::decode(parts[1]).map_err(|_| ApiError::internal("Invalid encrypted secret"))?;
    let bytes =
        hex::decode(parts[2]).map_err(|_| ApiError::internal("Invalid encrypted secret"))?;
    if nonce.len() != 12 {
        return Err(ApiError::internal("Invalid encrypted secret"));
    }
    let cipher = Aes256Gcm::new_from_slice(&config.secret_key)
        .map_err(|_| ApiError::internal("Encryption unavailable"))?;
    let decoded = cipher
        .decrypt(aes_gcm::Nonce::from_slice(&nonce), bytes.as_slice())
        .map_err(|_| ApiError::internal("Unable to decrypt credential"))?;
    String::from_utf8(decoded).map_err(|_| ApiError::internal("Invalid decrypted credential"))
}

pub fn app(state: AppState) -> axum::Router {
    core::router(state)
}
