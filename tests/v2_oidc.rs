//! OIDC protocol tests use a local HTTP provider and a newly generated test-only RSA key.
//! No external identity account or private user content is accessed.
use axum::{
    Json, Router,
    extract::{Form, State},
    http::HeaderMap,
    routing::{get, post},
};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use blueprint_backend::*;
use jsonwebtoken::{Algorithm, EncodingKey, Header};
use reqwest::{Client, Method, StatusCode};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    process::{Command, Stdio},
    sync::{Arc, Mutex},
};
use uuid::Uuid;

const ISSUER: &str = "https://identity.example.test/realms/blueprint";
const CLIENT: &str = "blueprint-oidc-test";
const PASSWORD: &str = "oidc-disposable-test-password-937";
#[derive(Clone, Copy, Debug)]
enum Failure {
    None,
    Issuer,
    Audience,
    Expired,
    Nonce,
    MissingSubject,
    UserinfoSubject,
    UnverifiedEmail,
    Signature,
    AuthorizedParty,
}
struct Controls {
    failure: Failure,
    email: String,
    subject: String,
    nonce: String,
    challenge: String,
    bad_discovery: bool,
    foreign_endpoint: bool,
    calls: Vec<&'static str>,
    pkce_verified: bool,
}
struct Provider {
    controls: Mutex<Controls>,
    key: EncodingKey,
    jwks: Value,
    redirect_uri: Mutex<String>,
}
fn record(provider: &Provider, operation: &'static str) {
    provider.controls.lock().unwrap().calls.push(operation);
}
async fn discovery(State(provider): State<Arc<Provider>>) -> Json<Value> {
    record(&provider, "discovery");
    let control = provider.controls.lock().unwrap();
    Json(
        json!({"issuer":if control.bad_discovery {"https://foreign.example.test/realms/blueprint"}else{ISSUER},
        "authorization_endpoint":format!("{ISSUER}/protocol/openid-connect/auth"),
        "token_endpoint":if control.foreign_endpoint {"https://foreign.example.test/token".into()}else{format!("{ISSUER}/protocol/openid-connect/token")},
        "userinfo_endpoint":format!("{ISSUER}/protocol/openid-connect/userinfo"),
        "jwks_uri":format!("{ISSUER}/protocol/openid-connect/certs")}),
    )
}
async fn certs(State(provider): State<Arc<Provider>>) -> Json<Value> {
    record(&provider, "certs");
    Json(provider.jwks.clone())
}
async fn token(
    State(provider): State<Arc<Provider>>,
    Form(form): Form<HashMap<String, String>>,
) -> (StatusCode, Json<Value>) {
    record(&provider, "token");
    let mut control = provider.controls.lock().unwrap();
    let verifier = form.get("code_verifier").map(String::as_str).unwrap_or("");
    let valid = form.get("grant_type").map(String::as_str) == Some("authorization_code")
        && form.get("client_id").map(String::as_str) == Some(CLIENT)
        && form.get("client_secret").map(String::as_str)
            == Some("disposable-provider-client-secret")
        && form.get("code").map(String::as_str) == Some("test-authorization-code")
        && form.get("redirect_uri") == Some(&*provider.redirect_uri.lock().unwrap())
        && verifier.len() >= 43
        && URL_SAFE_NO_PAD.encode(Sha256::digest(verifier.as_bytes())) == control.challenge;
    if !valid {
        return (
            StatusCode::BAD_REQUEST,
            Json(json!({"error":"invalid_grant"})),
        );
    }
    control.pkce_verified = true;
    let mut claims = json!({"iss":ISSUER,"aud":CLIENT,"sub":control.subject,"nonce":control.nonce,
        "exp":chrono::Utc::now().timestamp()+300,"iat":chrono::Utc::now().timestamp(),"azp":CLIENT});
    match control.failure {
        Failure::Issuer => claims["iss"] = json!("https://foreign.example.test/realms/blueprint"),
        Failure::Audience => claims["aud"] = json!("another-client"),
        Failure::Expired => claims["exp"] = json!(chrono::Utc::now().timestamp() - 3600),
        Failure::Nonce => claims["nonce"] = json!("different-flow-nonce"),
        Failure::MissingSubject => {
            claims.as_object_mut().unwrap().remove("sub");
        }
        Failure::AuthorizedParty => claims["azp"] = json!("another-client"),
        _ => {}
    }
    let mut header = Header::new(Algorithm::RS256);
    header.kid = Some("runtime-test-key".into());
    let mut signed = jsonwebtoken::encode(&header, &claims, &provider.key).unwrap();
    if matches!(control.failure, Failure::Signature) {
        let start = signed.rfind('.').unwrap() + 1;
        let replacement = if &signed[start..start + 1] == "A" {
            "B"
        } else {
            "A"
        };
        signed.replace_range(start..start + 1, replacement);
    }
    (
        StatusCode::OK,
        Json(
            json!({"access_token":"disposable-provider-access-token","id_token":signed,"token_type":"Bearer","expires_in":300}),
        ),
    )
}
async fn userinfo(
    State(provider): State<Arc<Provider>>,
    headers: HeaderMap,
) -> (StatusCode, Json<Value>) {
    record(&provider, "userinfo");
    if headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        != Some("Bearer disposable-provider-access-token")
    {
        return (
            StatusCode::UNAUTHORIZED,
            Json(json!({"error":"invalid_token"})),
        );
    }
    let control = provider.controls.lock().unwrap();
    (
        StatusCode::OK,
        Json(
            json!({"sub":if matches!(control.failure,Failure::UserinfoSubject){"another-subject"}else{&control.subject},"email":control.email,"email_verified":!matches!(control.failure,Failure::UnverifiedEmail)}),
        ),
    )
}
struct Environment(Vec<(&'static str, Option<String>)>);
impl Environment {
    fn set() -> Self {
        let values = [
            ("BLUEPRINT_OIDC_ISSUER", ISSUER),
            ("BLUEPRINT_OIDC_REALM", "blueprint"),
            ("BLUEPRINT_OIDC_CLIENT_ID", CLIENT),
            (
                "BLUEPRINT_OIDC_CLIENT_SECRET",
                "disposable-provider-client-secret",
            ),
        ];
        let previous = values
            .iter()
            .map(|(key, _)| (*key, std::env::var(key).ok()))
            .collect();
        // This test binary has one test, and CI serializes integration tests.
        for (key, value) in values {
            unsafe {
                std::env::set_var(key, value);
            }
        }
        Self(previous)
    }
}
impl Drop for Environment {
    fn drop(&mut self) {
        for (key, value) in &self.0 {
            unsafe {
                if let Some(value) = value {
                    std::env::set_var(key, value);
                } else {
                    std::env::remove_var(key);
                }
            }
        }
    }
}
struct Fixture {
    state: AppState,
    base: String,
    client: Client,
    admin: sqlx::PgPool,
    database: String,
    provider: Arc<Provider>,
    _directory: tempfile::TempDir,
    server: tokio::task::JoinHandle<()>,
    provider_server: tokio::task::JoinHandle<()>,
    _environment: Environment,
}
impl Fixture {
    async fn new() -> Self {
        let environment = Environment::set();
        let directory = tempfile::tempdir().unwrap();
        let private_key = directory.path().join("test-only-rsa.pem");
        let result = Command::new("openssl")
            .args([
                "genpkey",
                "-algorithm",
                "RSA",
                "-pkeyopt",
                "rsa_keygen_bits:2048",
                "-pkeyopt",
                "rsa_keygen_pubexp:65537",
                "-out",
            ])
            .arg(&private_key)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .unwrap();
        assert!(result.success());
        let modulus = Command::new("openssl")
            .args(["rsa", "-in"])
            .arg(&private_key)
            .args(["-modulus", "-noout"])
            .output()
            .unwrap();
        assert!(modulus.status.success());
        let modulus = String::from_utf8(modulus.stdout).unwrap();
        let n = URL_SAFE_NO_PAD
            .encode(hex::decode(modulus.trim().strip_prefix("Modulus=").unwrap()).unwrap());
        let provider = Arc::new(Provider {
            controls: Mutex::new(Controls {
                failure: Failure::None,
                email: "member@example.test".into(),
                subject: "stable-subject-one".into(),
                nonce: String::new(),
                challenge: String::new(),
                bad_discovery: false,
                foreign_endpoint: false,
                calls: vec![],
                pkce_verified: false,
            }),
            key: EncodingKey::from_rsa_pem(&std::fs::read(private_key).unwrap()).unwrap(),
            jwks: json!({"keys":[{"kty":"RSA","kid":"runtime-test-key","use":"sig","alg":"RS256","n":n,"e":"AQAB"}]}),
            redirect_uri: Mutex::new(String::new()),
        });
        let provider_listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let internal = format!("http://{}", provider_listener.local_addr().unwrap());
        let routes = Router::new()
            .route(
                "/realms/blueprint/.well-known/openid-configuration",
                get(discovery),
            )
            .route(
                "/realms/blueprint/protocol/openid-connect/token",
                post(token),
            )
            .route(
                "/realms/blueprint/protocol/openid-connect/certs",
                get(certs),
            )
            .route(
                "/realms/blueprint/protocol/openid-connect/userinfo",
                get(userinfo),
            )
            .with_state(provider.clone());
        let provider_server =
            tokio::spawn(async move { axum::serve(provider_listener, routes).await.unwrap() });
        let raw =
            std::env::var("TEST_DATABASE_URL").expect("Use a disposable PostgreSQL test server");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let database = format!("blueprint_oidc_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {database}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut database_url = url::Url::parse(&raw).unwrap();
        database_url.set_path(&format!("/{database}"));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        *provider.redirect_uri.lock().unwrap() = format!("{base}/api/v1/auth/sso/callback");
        let config = Config {
            database_url: database_url.to_string(),
            data_dir: directory.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: Some("disposable-oidc-setup-token".into()),
            services: HashMap::from([("keycloak".into(), internal)]),
            secret_key: [0x64; 32],
            ui_dir: directory.path().join("ui"),
        };
        std::fs::create_dir_all(&config.data_dir).unwrap();
        let mut state = AppState::connect(config).await.unwrap();
        state.http = Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .build()
            .unwrap();
        let app = app(state.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            state,
            base,
            client: Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
            admin,
            database,
            provider,
            _directory: directory,
            server,
            provider_server,
            _environment: environment,
        }
    }
    async fn request(
        &self,
        method: Method,
        path: &str,
        cookie: Option<&str>,
        body: Option<Value>,
    ) -> reqwest::Response {
        let mut request = self
            .client
            .request(method, format!("{}{path}", self.base))
            .header("X-Blueprint-Request", "1");
        if let Some(cookie) = cookie {
            request = request.header("Cookie", cookie);
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        request.send().await.unwrap()
    }
    async fn start(&self) -> (String, String) {
        let response = self
            .request(Method::GET, "/api/v1/auth/sso/start", None, None)
            .await;
        let status = response.status();
        assert_eq!(
            status,
            StatusCode::FOUND,
            "{}",
            response.text().await.unwrap_or_default()
        );
        let location = url::Url::parse(response.headers()["location"].to_str().unwrap()).unwrap();
        assert_eq!(
            location.origin().ascii_serialization(),
            "https://identity.example.test"
        );
        assert_eq!(
            location.path(),
            "/realms/blueprint/protocol/openid-connect/auth"
        );
        let query: HashMap<_, _> = location
            .query_pairs()
            .map(|(key, value)| (key.into_owned(), value.into_owned()))
            .collect();
        assert_eq!(query["client_id"], CLIENT);
        assert_eq!(query["code_challenge_method"], "S256");
        assert_eq!(query["nonce"], query["state"]);
        assert_ne!(query["code_challenge"], query["nonce"]);
        let state = query["state"].clone();
        let cookie = response.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_string();
        {
            let mut control = self.provider.controls.lock().unwrap();
            control.nonce = query["nonce"].clone();
            control.challenge = query["code_challenge"].clone();
            control.pkce_verified = false;
        }
        (state, cookie)
    }
    async fn callback(&self, state: &str, cookie: Option<&str>) -> reqwest::Response {
        self.request(
            Method::GET,
            &format!("/api/v1/auth/sso/callback?state={state}&code=test-authorization-code"),
            cookie,
            None,
        )
        .await
    }
    async fn cleanup(self) {
        self.server.abort();
        self.provider_server.abort();
        self.state.pool.close().await;
        sqlx::query(&format!("DROP DATABASE {} WITH (FORCE)", self.database))
            .execute(&self.admin)
            .await
            .unwrap();
        self.admin.close().await;
    }
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL and OpenSSL via TEST_DATABASE_URL"]
async fn oidc_public_internal_pkce_signed_claims_binding_and_account_revocation() {
    let f = Fixture::new().await;
    let setup=f.request(Method::POST,"/api/v1/setup",None,Some(json!({"token":"disposable-oidc-setup-token","company_name":"OIDC Test","email":"owner@example.test","name":"Owner","password":PASSWORD}))).await;
    assert_eq!(setup.status(), StatusCode::OK);
    let owner = setup.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let member:Value=f.request(Method::POST,"/api/v1/users",Some(&owner),Some(json!({"name":"Preprovisioned Member","email":"member@example.test","role":"member","password":PASSWORD}))).await.json().await.unwrap();
    let member_id = member["id"].as_str().unwrap();
    for failure in [
        Failure::Issuer,
        Failure::Audience,
        Failure::Expired,
        Failure::Nonce,
        Failure::MissingSubject,
        Failure::UserinfoSubject,
        Failure::UnverifiedEmail,
        Failure::Signature,
        Failure::AuthorizedParty,
    ] {
        f.provider.controls.lock().unwrap().failure = failure;
        let (state, cookie) = f.start().await;
        let response = f.callback(&state, Some(&cookie)).await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "{failure:?}");
        assert!(response.headers().get("set-cookie").is_none());
    }
    f.provider.controls.lock().unwrap().failure = Failure::None;
    f.provider.controls.lock().unwrap().email = "not-provisioned@example.test".into();
    let (state, cookie) = f.start().await;
    assert_eq!(
        f.callback(&state, Some(&cookie)).await.status(),
        StatusCode::FORBIDDEN
    );
    f.provider.controls.lock().unwrap().email = "member@example.test".into();
    let (state, cookie) = f.start().await;
    let before = f
        .provider
        .controls
        .lock()
        .unwrap()
        .calls
        .iter()
        .filter(|call| **call == "token")
        .count();
    assert_eq!(
        f.callback(&state, None).await.status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        f.callback("wrong-state", Some(&cookie)).await.status(),
        StatusCode::UNAUTHORIZED
    );
    assert_eq!(
        f.provider
            .controls
            .lock()
            .unwrap()
            .calls
            .iter()
            .filter(|call| **call == "token")
            .count(),
        before
    );
    let response = f.callback(&state, Some(&cookie)).await;
    let status = response.status();
    assert_eq!(
        status,
        StatusCode::FOUND,
        "{}",
        response.text().await.unwrap_or_default()
    );
    let session = response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    assert!(f.provider.controls.lock().unwrap().pkce_verified);
    let me: Value = f
        .request(Method::GET, "/api/v1/me", Some(&session), None)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(me["id"], member_id);
    assert_eq!(
        f.callback(&state, Some(&cookie)).await.status(),
        StatusCode::UNAUTHORIZED
    );
    let binding: Uuid = sqlx::query_scalar(
        "SELECT user_id FROM external_identities WHERE issuer=$1 AND subject='stable-subject-one'",
    )
    .bind(ISSUER)
    .fetch_one(&f.state.pool)
    .await
    .unwrap();
    assert_eq!(binding.to_string(), member_id);
    f.provider.controls.lock().unwrap().subject = "different-subject-same-email".into();
    let (state, cookie) = f.start().await;
    let response = f.callback(&state, Some(&cookie)).await;
    assert!(response.status().is_client_error());
    assert!(response.headers().get("set-cookie").is_none());
    f.provider.controls.lock().unwrap().subject = "stable-subject-one".into();
    // Once bound, an identity provider email change cannot switch this subject to the owner.
    f.provider.controls.lock().unwrap().email = "owner@example.test".into();
    let (state, cookie) = f.start().await;
    let response = f.callback(&state, Some(&cookie)).await;
    assert_eq!(response.status(), StatusCode::FOUND);
    let session2 = response.headers()["set-cookie"]
        .to_str()
        .unwrap()
        .split(';')
        .next()
        .unwrap()
        .to_string();
    let me: Value = f
        .request(Method::GET, "/api/v1/me", Some(&session2), None)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(me["id"], member_id);
    f.request(
        Method::PATCH,
        &format!("/api/v1/users/{member_id}"),
        Some(&owner),
        Some(json!({"active":false})),
    )
    .await
    .error_for_status()
    .unwrap();
    assert_eq!(
        f.request(Method::GET, "/api/v1/me", Some(&session), None)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let (state, cookie) = f.start().await;
    assert_eq!(
        f.callback(&state, Some(&cookie)).await.status(),
        StatusCode::UNAUTHORIZED
    );
    {
        let mut control = f.provider.controls.lock().unwrap();
        control.bad_discovery = true;
    }
    assert_eq!(
        f.request(Method::GET, "/api/v1/auth/sso/start", None, None)
            .await
            .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    {
        let mut control = f.provider.controls.lock().unwrap();
        control.bad_discovery = false;
        control.foreign_endpoint = true;
    }
    assert_eq!(
        f.request(Method::GET, "/api/v1/auth/sso/start", None, None)
            .await
            .status(),
        StatusCode::SERVICE_UNAVAILABLE
    );
    {
        let control = f.provider.controls.lock().unwrap();
        for operation in ["discovery", "token", "certs", "userinfo"] {
            assert!(
                control.calls.contains(&operation),
                "Missing internal provider request: {operation}"
            );
        }
    }
    f.cleanup().await;
}
