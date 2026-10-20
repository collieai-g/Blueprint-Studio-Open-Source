//! Real HTTP adapters against controlled provider contracts and disposable Postgres.
//! These do not claim a live Google/Microsoft/Documenso deployment was exercised.
use axum::{
    Router,
    body::Bytes,
    extract::State,
    http::{HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
};
use blueprint_backend::*;
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, HashMap},
    sync::{Arc, Mutex},
};
use uuid::Uuid;

#[derive(Clone, Default)]
struct Providers {
    events: Arc<Mutex<BTreeMap<String, Value>>>,
    calls: Arc<Mutex<Vec<(String, String, Vec<u8>)>>>,
    sign_failed: Arc<Mutex<bool>>,
    sign_status: Arc<Mutex<String>>,
}

async fn provider(
    State(mock): State<Providers>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    bytes: Bytes,
) -> Response {
    let path = uri.path();
    mock.calls
        .lock()
        .unwrap()
        .push((method.to_string(), path.into(), bytes.to_vec()));
    if path == "/oauth/token" && method == Method::POST {
        let body = String::from_utf8_lossy(&bytes);
        assert!(body.contains("client_id=mock-client"));
        assert!(body.contains("client_secret=mock-client-secret"));
        return axum::Json(json!({"access_token":"mock-access-token","refresh_token":"mock-refresh-token","expires_in":3600})).into_response();
    }
    if path.starts_with("/google/") {
        assert_eq!(
            headers.get("authorization").unwrap(),
            "Bearer mock-access-token"
        );
        let collection = "/google/calendars/primary/events";
        if path == collection && method == Method::GET {
            let events = mock
                .events
                .lock()
                .unwrap()
                .values()
                .cloned()
                .collect::<Vec<_>>();
            return axum::Json(json!({"items":events,"nextSyncToken":"mock-cursor"}))
                .into_response();
        }
        if path == collection && method == Method::POST {
            let mut body: Value = serde_json::from_slice(&bytes).unwrap();
            let id = body["id"].as_str().unwrap().to_owned();
            if mock.events.lock().unwrap().contains_key(&id) {
                return StatusCode::CONFLICT.into_response();
            }
            body["etag"] = json!("exported-1");
            mock.events.lock().unwrap().insert(id, body.clone());
            return axum::Json(body).into_response();
        }
        if let Some(id) = path.strip_prefix(&format!("{collection}/")) {
            if method == Method::GET {
                return match mock.events.lock().unwrap().get(id).cloned() {
                    Some(event) => axum::Json(event).into_response(),
                    None => StatusCode::NOT_FOUND.into_response(),
                };
            }
            if method == Method::PATCH {
                let mut events = mock.events.lock().unwrap();
                let Some(old) = events.get(id) else {
                    return StatusCode::NOT_FOUND.into_response();
                };
                if headers.get("if-match").and_then(|h| h.to_str().ok()) != old["etag"].as_str() {
                    return StatusCode::PRECONDITION_FAILED.into_response();
                }
                let mut body: Value = serde_json::from_slice(&bytes).unwrap();
                body["id"] = json!(id);
                body["etag"] = json!("exported-next");
                events.insert(id.into(), body.clone());
                return axum::Json(body).into_response();
            }
        }
    }
    if path.starts_with("/api/v2/") {
        assert_eq!(
            headers.get("authorization").unwrap(),
            "mock-documenso-api-key"
        );
        if path == "/api/v2/envelope/create" {
            if *mock.sign_failed.lock().unwrap() {
                return StatusCode::SERVICE_UNAVAILABLE.into_response();
            }
            let body = String::from_utf8_lossy(&bytes);
            assert!(body.contains("name=\"payload\""));
            assert!(body.contains("name=\"files\""));
            assert!(body.contains("%PDF-"));
            return axum::Json(json!({"id":"envelope-one","status":"DRAFT"})).into_response();
        }
        if path == "/api/v2/envelope/distribute" {
            assert_eq!(
                serde_json::from_slice::<Value>(&bytes).unwrap()["envelopeId"],
                "envelope-one"
            );
            return axum::Json(json!({"id":"envelope-one","recipients":[{"token":"private-signer-token","signingUrl":"https://provider/sign/private"}]})).into_response();
        }
        if path == "/api/v2/envelope/envelope-one" {
            return axum::Json(json!({"id":"envelope-one","status":mock.sign_status.lock().unwrap().clone(),"envelopeItems":[{"id":"item-one"}],"recipients":[{"email":"signer@example.test","token":"private-signer-token","signingUrl":"https://provider/sign/private","signingStatus":"SIGNED"}]})).into_response();
        }
        if path == "/api/v2/envelope/item/item-one/download"
            || path == "/api/v2/envelope/envelope-one/audit-log/download"
        {
            return (
                [("content-type", "application/pdf")],
                b"%PDF-1.7\ncontrolled-provider-document\n%%EOF".as_slice(),
            )
                .into_response();
        }
    }
    (
        StatusCode::NOT_FOUND,
        format!("Unexpected provider call: {method} {path}"),
    )
        .into_response()
}

struct Fixture {
    state: AppState,
    base: String,
    client: reqwest::Client,
    admin: sqlx::PgPool,
    name: String,
    _dir: tempfile::TempDir,
    server: tokio::task::JoinHandle<()>,
    provider_server: tokio::task::JoinHandle<()>,
    mock: Providers,
}
impl Fixture {
    async fn new() -> Self {
        let raw =
            std::env::var("TEST_DATABASE_URL").expect("Use a disposable PostgreSQL test server");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let name = format!("blueprint_business_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {name}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut url = url::Url::parse(&raw).unwrap();
        url.set_path(&name);
        let mock = Providers::default();
        *mock.sign_status.lock().unwrap() = "PENDING".into();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let mock_base = format!("http://{}", listener.local_addr().unwrap());
        let app = Router::new().fallback(provider).with_state(mock.clone());
        let provider_server =
            tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let dir = tempfile::tempdir().unwrap();
        let services = HashMap::from([
            ("google_client_id".into(), "mock-client".into()),
            ("google_client_secret".into(), "mock-client-secret".into()),
            (
                "google_authorize".into(),
                format!("{mock_base}/oauth/authorize"),
            ),
            ("google_token".into(), format!("{mock_base}/oauth/token")),
            ("google_calendar_api".into(), format!("{mock_base}/google")),
            ("documenso".into(), mock_base),
            (
                "documenso_api_token".into(),
                "mock-documenso-api-key".into(),
            ),
        ]);
        let config = Config {
            database_url: url.into(),
            data_dir: dir.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: Some("mock-bootstrap-token".into()),
            services,
            secret_key: [0x42; 32],
            ui_dir: dir.path().join("ui"),
        };
        std::fs::create_dir_all(&config.data_dir).unwrap();
        let state = AppState::connect(config).await.unwrap();
        let app = blueprint_backend::app(state.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            state,
            base,
            client: reqwest::Client::builder()
                .no_proxy()
                .redirect(reqwest::redirect::Policy::none())
                .build()
                .unwrap(),
            admin,
            name,
            _dir: dir,
            server,
            provider_server,
            mock,
        }
    }
    async fn request(
        &self,
        method: Method,
        path: &str,
        cookie: Option<&str>,
        body: Option<Value>,
    ) -> reqwest::Response {
        let mut req = self
            .client
            .request(method, format!("{}{path}", self.base))
            .header("X-Blueprint-Request", "1");
        if let Some(cookie) = cookie {
            req = req.header("cookie", cookie);
        }
        if let Some(body) = body {
            req = req.json(&body);
        }
        req.send().await.unwrap()
    }
    async fn setup(&self) -> (String, Actor) {
        let response=self.request(Method::POST,"/api/v1/setup",None,Some(json!({"token":"mock-bootstrap-token","company_name":"Business Test","email":"owner@example.test","name":"Owner","password":"business-test-password-987"}))).await;
        assert_eq!(response.status(), StatusCode::OK);
        let cookie = response.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_owned();
        let actor = self
            .request(Method::GET, "/api/v1/me", Some(&cookie), None)
            .await
            .json()
            .await
            .unwrap();
        (cookie, actor)
    }
    async fn resource(&self, cookie: &str, kind: &str, name: &str, data: Value) -> Resource {
        let response = self
            .request(
                Method::POST,
                "/api/v1/resources",
                Some(cookie),
                Some(json!({"kind":kind,"name":name,"data":data})),
            )
            .await;
        let status = response.status();
        let body: Value = response.json().await.unwrap();
        assert_eq!(status, StatusCode::CREATED, "{body}");
        serde_json::from_value(body).unwrap()
    }
    async fn job(&self, actor: &Actor, kind: &str, payload: Value) -> Job {
        repo::enqueue(&self.state.pool, actor, kind, payload, None)
            .await
            .unwrap()
    }
    async fn cleanup(self) {
        self.server.abort();
        self.provider_server.abort();
        self.state.pool.close().await;
        sqlx::query(&format!("DROP DATABASE {} WITH (FORCE)", self.name))
            .execute(&self.admin)
            .await
            .unwrap();
        self.admin.close().await;
    }
}

#[tokio::test]
#[ignore = "requires a dedicated disposable PostgreSQL server"]
async fn google_oauth_import_export_conflicts_and_revocation() {
    let f = Fixture::new().await;
    let (cookie, actor) = f.setup().await;
    f.mock.events.lock().unwrap().insert("remote-one".into(),json!({"id":"remote-one","etag":"remote-1","summary":"Provider meeting","start":{"dateTime":"2026-10-20T09:00:00+08:00","timeZone":"Asia/Hong_Kong"},"end":{"dateTime":"2026-10-20T10:00:00+08:00","timeZone":"Asia/Hong_Kong"}}));
    let response = f
        .request(
            Method::POST,
            "/api/v1/calendar/connections",
            Some(&cookie),
            Some(json!({"provider":"google"})),
        )
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let connected: Value = response.json().await.unwrap();
    let id = connected["connection_id"].as_str().unwrap();
    let authorization = url::Url::parse(connected["authorization_url"].as_str().unwrap()).unwrap();
    let state = authorization
        .query_pairs()
        .find(|(key, _)| key == "state")
        .unwrap()
        .1
        .into_owned();
    let path = format!("/api/v1/public/calendar/oauth/google?state={state}&code=mock-code");
    let response = f.request(Method::GET, &path, None, None).await;
    assert_eq!(response.status(), StatusCode::SEE_OTHER);
    assert_eq!(
        f.request(Method::GET, &path, None, None).await.status(),
        StatusCode::BAD_REQUEST
    );
    let listing = f
        .request(
            Method::GET,
            "/api/v1/calendar/connections",
            Some(&cookie),
            None,
        )
        .await
        .text()
        .await
        .unwrap();
    assert!(!listing.contains("mock-access-token"));
    assert!(!listing.contains("encrypted"));
    assert!(listing.contains("connected"));
    let connection = repo::raw_get(&f.state.pool, Uuid::parse_str(id).unwrap())
        .await
        .unwrap();
    assert!(!connection.data.to_string().contains("mock-refresh-token"));
    let sync = f
        .job(
            &actor,
            "business.calendar.sync",
            json!({"connection_id":id}),
        )
        .await;
    business::process_job(&f.state, &sync).await.unwrap();
    let mut events = repo::list(&f.state.pool, &actor, "event", None)
        .await
        .unwrap();
    assert_eq!(events.len(), 1);
    let event = events.pop().unwrap();
    assert_eq!(event.name, "Provider meeting");
    let local = f
        .request(
            Method::PATCH,
            &format!("/api/v1/resource/{}", event.id),
            Some(&cookie),
            Some(json!({"name":"Local edited meeting","expected_version":event.version})),
        )
        .await;
    assert_eq!(local.status(), StatusCode::OK);
    {
        let mut remotes = f.mock.events.lock().unwrap();
        let remote = remotes.get_mut("remote-one").unwrap();
        remote["summary"] = json!("Provider edited meeting");
        remote["etag"] = json!("remote-2");
    }
    business::process_job(&f.state, &sync).await.unwrap();
    let conflicted = repo::raw_get(&f.state.pool, event.id).await.unwrap();
    assert!(conflicted.data.get("sync_conflict").is_some());
    assert_eq!(conflicted.name, "Local edited meeting");
    let resolve = f
        .request(
            Method::POST,
            &format!("/api/v1/calendar/events/{}/resolve", event.id),
            Some(&cookie),
            Some(json!({"resolution":"local","expected_version":conflicted.version})),
        )
        .await;
    let status = resolve.status();
    let resolved: Value = resolve.json().await.unwrap();
    assert_eq!(status, StatusCode::OK, "{resolved}");
    let job: Job = serde_json::from_value(resolved["job"].clone()).unwrap();
    business::process_job(&f.state, &job).await.unwrap();
    assert_eq!(
        f.mock.events.lock().unwrap()["remote-one"]["summary"],
        "Local edited meeting"
    );
    let local = f
        .resource(
            &cookie,
            "event",
            "New local event",
            json!({"start":"2026-11-01T10:00:00Z","end":"2026-11-01T11:00:00Z","timezone":"UTC"}),
        )
        .await;
    let push = f
        .job(
            &actor,
            "business.calendar.push",
            json!({"connection_id":id,"event_id":local.id}),
        )
        .await;
    business::process_job(&f.state, &push).await.unwrap();
    assert!(
        f.mock
            .events
            .lock()
            .unwrap()
            .contains_key(&local.id.simple().to_string())
    );
    assert_eq!(
        f.request(
            Method::POST,
            &format!("/api/v1/calendar/connections/{id}/disconnect"),
            Some(&cookie),
            Some(json!({}))
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert!(business::process_job(&f.state, &sync).await.is_err());
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "requires a dedicated disposable PostgreSQL server"]
async fn documenso_real_job_lifecycle_evidence_and_uncertain_creation() {
    let f = Fixture::new().await;
    let (cookie, actor) = f.setup().await;
    let file = content::store_bytes(
        &f.state,
        &actor,
        "contract.pdf",
        "application/pdf",
        b"%PDF-1.7\ncontrolled-source\n%%EOF",
        None,
    )
    .await
    .unwrap();
    let input = json!({"name":"Contract signing","file_id":file.id,"recipients":[{"name":"Signer","email":"signer@example.test"}]});
    let response = f
        .request(
            Method::POST,
            "/api/v1/sign/requests",
            Some(&cookie),
            Some(input.clone()),
        )
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let request: Resource = response.json().await.unwrap();
    assert_eq!(request.data["status"], "queued");
    // Metadata processing increments the repository revision without changing
    // the immutable PDF content version selected for signing.
    let mut metadata = file.data.clone();
    metadata["processing"] = json!({"status":"completed"});
    repo::update(
        &f.state.pool,
        &actor,
        file.id,
        UpdateResource {
            name: None,
            parent_id: None,
            visibility: None,
            acl: None,
            data: Some(metadata),
            expected_version: file.version,
        },
    )
    .await
    .unwrap();
    let job = f
        .job(
            &actor,
            "business.sign.create",
            json!({"request_id":request.id}),
        )
        .await;
    let (first, second) = tokio::join!(
        business::process_job(&f.state, &job),
        business::process_job(&f.state, &job)
    );
    assert!(
        first
            .as_ref()
            .is_ok_and(|result| result["status"] == "pending")
            || second
                .as_ref()
                .is_ok_and(|result| result["status"] == "pending")
    );
    business::process_job(&f.state, &job).await.unwrap();
    assert_eq!(
        f.mock
            .calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, path, _)| path == "/api/v2/envelope/create")
            .count(),
        1
    );
    let pending = repo::raw_get(&f.state.pool, request.id).await.unwrap();
    assert!(!pending.data.to_string().contains("private-signer-token"));
    assert!(
        !pending
            .data
            .to_string()
            .contains("https://provider/sign/private")
    );
    assert_eq!(
        f.request(
            Method::GET,
            &format!("/api/v1/sign/requests/{}/download", request.id),
            Some(&cookie),
            None
        )
        .await
        .status(),
        StatusCode::CONFLICT
    );
    *f.mock.sign_status.lock().unwrap() = "COMPLETED".into();
    let refresh = f
        .job(
            &actor,
            "business.sign.refresh",
            json!({"request_id":request.id}),
        )
        .await;
    assert_eq!(
        business::process_job(&f.state, &refresh).await.unwrap()["status"],
        "completed"
    );
    for action in ["download", "evidence"] {
        let response = f
            .request(
                Method::GET,
                &format!("/api/v1/sign/requests/{}/{action}", request.id),
                Some(&cookie),
                None,
            )
            .await;
        assert_eq!(response.status(), StatusCode::OK);
        assert!(response.bytes().await.unwrap().starts_with(b"%PDF-"));
    }
    *f.mock.sign_failed.lock().unwrap() = true;
    let failed: Resource = f
        .request(
            Method::POST,
            "/api/v1/sign/requests",
            Some(&cookie),
            Some(input),
        )
        .await
        .json()
        .await
        .unwrap();
    let job = f
        .job(
            &actor,
            "business.sign.create",
            json!({"request_id":failed.id}),
        )
        .await;
    assert!(business::process_job(&f.state, &job).await.is_err());
    assert_eq!(
        repo::raw_get(&f.state.pool, failed.id).await.unwrap().data["status"],
        "needs_review"
    );
    let count = f
        .mock
        .calls
        .lock()
        .unwrap()
        .iter()
        .filter(|(_, path, _)| path == "/api/v2/envelope/create")
        .count();
    business::process_job(&f.state, &job).await.unwrap();
    assert_eq!(
        f.mock
            .calls
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, path, _)| path == "/api/v2/envelope/create")
            .count(),
        count
    );
    f.cleanup().await;
}
