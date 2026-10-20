//! End-to-end security and persistence checks against a disposable PostgreSQL database.
//! Run with TEST_DATABASE_URL=<dedicated local postgres> cargo test --test v2_workspace -- --ignored.
use blueprint_backend::*;
use reqwest::{Client, Method, StatusCode};
use serde_json::{Value, json};
use std::collections::HashMap;
use uuid::Uuid;

struct Fixture {
    state: AppState,
    base: String,
    client: Client,
    admin: sqlx::PgPool,
    name: String,
    _dir: tempfile::TempDir,
    server: tokio::task::JoinHandle<()>,
}
impl Fixture {
    async fn new() -> Self {
        let raw = std::env::var("TEST_DATABASE_URL")
            .expect("TEST_DATABASE_URL must target a disposable PostgreSQL server");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let name = format!("blueprint_http_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {name}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut url = url::Url::parse(&raw).unwrap();
        url.set_path(&format!("/{name}"));
        let dir = tempfile::tempdir().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let config = Config {
            database_url: url.to_string(),
            data_dir: dir.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: Some("fixture-bootstrap-token-not-a-user-secret".into()),
            services: HashMap::new(),
            secret_key: [0x45; 32],
            ui_dir: dir.path().join("ui"),
        };
        std::fs::create_dir_all(&config.data_dir).unwrap();
        let state = AppState::connect(config).await.unwrap();
        let app = app(state.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            state,
            base,
            client: Client::builder().no_proxy().build().unwrap(),
            admin,
            name,
            _dir: dir,
            server,
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
        if let Some(c) = cookie {
            req = req.header("Cookie", c);
        }
        if let Some(b) = body {
            req = req.json(&b);
        }
        req.send().await.unwrap()
    }
    async fn setup(&self) -> String {
        let r=self.request(Method::POST,"/api/v1/setup",None,Some(json!({"token":"fixture-bootstrap-token-not-a-user-secret","company_name":"Integration Test Company","email":"owner@example.test","name":"Test Owner","password":"integration-test-password-937"}))).await;
        assert_eq!(
            r.status(),
            StatusCode::OK,
            "{}",
            r.text().await.unwrap_or_default()
        );
        r.headers()
            .get("set-cookie")
            .unwrap()
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .into()
    }
    async fn login(&self, email: &str) -> String {
        let r = self
            .request(
                Method::POST,
                "/api/v1/login",
                None,
                Some(json!({"email":email,"password":"integration-test-password-937"})),
            )
            .await;
        assert_eq!(r.status(), StatusCode::OK);
        r.headers()
            .get("set-cookie")
            .unwrap()
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .into()
    }
    async fn resource(&self, cookie: &str, kind: &str, name: &str, data: Value) -> Value {
        let r = self
            .request(
                Method::POST,
                "/api/v1/resources",
                Some(cookie),
                Some(json!({"kind":kind,"name":name,"data":data})),
            )
            .await;
        let status = r.status();
        let body: Value = r.json().await.unwrap();
        assert_eq!(status, StatusCode::CREATED, "{body}");
        body
    }
    async fn cleanup(self) {
        self.server.abort();
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
async fn account_acl_versions_tokens_and_disabled_modules() {
    let f = Fixture::new().await;
    let status: Value = f
        .request(Method::GET, "/api/v1/status", None, None)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(status["initialized"], false);
    assert_eq!(
        f.request(Method::GET, "/api/v1/resources/file", None, None)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let owner = f.setup().await;
    let headers = f
        .request(Method::GET, "/api/v1/me", Some(&owner), None)
        .await;
    let me: Value = headers.json().await.unwrap();
    let owner_id = me["id"].clone();
    let csrf = f
        .client
        .post(format!("{}/api/v1/resources", f.base))
        .header("Cookie", &owner)
        .json(&json!({"kind":"folder","name":"Rejected","data":{}}))
        .send()
        .await
        .unwrap();
    assert_eq!(csrf.status(), StatusCode::FORBIDDEN);
    let cross = f
        .client
        .post(format!("{}/api/v1/resources", f.base))
        .header("Cookie", &owner)
        .header("X-Blueprint-Request", "1")
        .header("Origin", "https://untrusted.example")
        .json(&json!({"kind":"folder","name":"Rejected","data":{}}))
        .send()
        .await
        .unwrap();
    assert_eq!(cross.status(), StatusCode::FORBIDDEN);
    let member:Value=f.request(Method::POST,"/api/v1/users",Some(&owner),Some(json!({"email":"member@example.test","name":"Member","role":"member","password":"integration-test-password-937"}))).await.json().await.unwrap();
    let member_cookie = f.login("member@example.test").await;
    let folder = f
        .resource(&owner, "folder", "Private company folder", json!({}))
        .await;
    let id = folder["id"].as_str().unwrap();
    assert_eq!(
        f.request(
            Method::GET,
            &format!("/api/v1/resource/{id}"),
            Some(&member_cookie),
            None
        )
        .await
        .status(),
        StatusCode::NOT_FOUND
    );
    let changed: Value = f
        .request(
            Method::PATCH,
            &format!("/api/v1/resource/{id}"),
            Some(&owner),
            Some(json!({"name":"Renamed folder","expected_version":1})),
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(changed["version"], 2);
    assert_eq!(
        f.request(
            Method::PATCH,
            &format!("/api/v1/resource/{id}"),
            Some(&owner),
            Some(json!({"name":"Lost update","expected_version":1}))
        )
        .await
        .status(),
        StatusCode::CONFLICT
    );
    let acl = json!({format!("user:{}",member["id"].as_str().unwrap()):"viewer"});
    let changed: Value = f
        .request(
            Method::PATCH,
            &format!("/api/v1/resource/{id}"),
            Some(&owner),
            Some(json!({"acl":acl,"expected_version":2})),
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(changed["version"], 3);
    assert_eq!(
        f.request(
            Method::GET,
            &format!("/api/v1/resource/{id}"),
            Some(&member_cookie),
            None
        )
        .await
        .status(),
        StatusCode::OK
    );
    assert_eq!(
        f.request(
            Method::PATCH,
            &format!("/api/v1/resource/{id}"),
            Some(&member_cookie),
            Some(json!({"name":"Unauthorized edit","expected_version":3}))
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    let child:Value=f.request(Method::POST,"/api/v1/resources",Some(&owner),Some(json!({"kind":"note","name":"Nested private note","parent_id":id,"visibility":"company","data":{"content":"Sensitive note"}}))).await.json().await.unwrap();
    assert!(child["id"].is_string(), "{child}");
    let history: Vec<Value> = f
        .request(
            Method::GET,
            &format!("/api/v1/resource/{id}/history"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(history.len(), 3);
    let token:Value=f.request(Method::POST,"/api/v1/tokens",Some(&owner),Some(json!({"name":"Read-only folder token","scopes":["content:read"],"resource_ids":[id]}))).await.json().await.unwrap();
    let bearer = token["token"].as_str().unwrap();
    let response = f
        .client
        .get(format!("{}/api/v1/resource/{id}", f.base))
        .bearer_auth(bearer)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    let response = f
        .client
        .patch(format!("{}/api/v1/resource/{id}", f.base))
        .bearer_auth(bearer)
        .json(&json!({"name":"Token must not edit","expected_version":3}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let response = f
        .client
        .get(format!(
            "{}/api/v1/resource/{}",
            f.base,
            child["id"].as_str().unwrap()
        ))
        .bearer_auth(bearer)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let response = f
        .client
        .get(format!("{}/api/v1/users", f.base))
        .bearer_auth(bearer)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    let owner_job = repo::enqueue(
        &f.state.pool,
        &auth::actor_by_id(
            &f.state,
            Uuid::parse_str(owner_id.as_str().unwrap()).unwrap(),
        )
        .await
        .unwrap(),
        "content.private",
        json!({"private_metadata":"interactive owner's job"}),
        None,
    )
    .await
    .unwrap();
    let response = f
        .client
        .get(format!("{}/api/v1/jobs/{}", f.base, owner_job.id))
        .bearer_auth(bearer)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::NOT_FOUND);
    let actor = Actor {
        id: Uuid::parse_str(owner_id.as_str().unwrap()).unwrap(),
        email: "owner@example.test".into(),
        name: "Owner".into(),
        role: "owner".into(),
        groups: vec![],
        scopes: Some(vec!["content:read".into()]),
        resource_ids: None,
        token_id: Some(Uuid::parse_str(token["id"].as_str().unwrap()).unwrap()),
    };
    let job = repo::enqueue(
        &f.state.pool,
        &actor,
        "content.test",
        json!({}),
        Some("revocation-check"),
    )
    .await
    .unwrap();
    f.request(
        Method::DELETE,
        &format!("/api/v1/tokens/{}", token["id"].as_str().unwrap()),
        Some(&owner),
        None,
    )
    .await
    .error_for_status()
    .unwrap();
    assert!(auth::actor_for_job(&f.state, &job).await.is_err());
    let member_id = member["id"].as_str().unwrap();
    let disabled = f
        .request(
            Method::PATCH,
            &format!("/api/v1/users/{member_id}"),
            Some(&owner),
            Some(json!({"active":false})),
        )
        .await;
    assert_eq!(disabled.status(), StatusCode::OK);
    assert_eq!(
        f.request(Method::GET, "/api/v1/me", Some(&member_cookie), None)
            .await
            .status(),
        StatusCode::UNAUTHORIZED
    );
    let enabled: Value = f
        .request(
            Method::PATCH,
            &format!("/api/v1/users/{member_id}"),
            Some(&owner),
            Some(json!({"active":true,"name":"Re-enabled member"})),
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(enabled["active"], true);
    assert_eq!(enabled["name"], "Re-enabled member");
    let _ = f.login("member@example.test").await;
    let response = f
        .client
        .get(format!("{}/api/v1/resource/{id}", f.base))
        .bearer_auth(bearer)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    f.request(
        Method::PATCH,
        "/api/v1/settings",
        Some(&owner),
        Some(json!({"modules":{"notes":false}})),
    )
    .await
    .error_for_status()
    .unwrap();
    assert_eq!(
        f.request(Method::GET, "/api/v1/resources/note", Some(&owner), None)
            .await
            .status(),
        StatusCode::FORBIDDEN
    );
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "requires a dedicated disposable PostgreSQL server"]
async fn files_shares_hold_and_original_integrity() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let bytes = b"Persistent private file: UTF-8 \xe8\x97\x8d\xe5\x9c\x96\n";
    let part = reqwest::multipart::Part::bytes(bytes.to_vec())
        .file_name("company-note.txt")
        .mime_str("text/plain")
        .unwrap();
    let form = reqwest::multipart::Form::new().part("file", part);
    let response = f
        .client
        .post(format!("{}/api/v1/files/upload", f.base))
        .header("Cookie", &owner)
        .header("X-Blueprint-Request", "1")
        .multipart(form)
        .send()
        .await
        .unwrap();
    let status = response.status();
    let file: Value = response.json().await.unwrap();
    assert!(status.is_success(), "{file}");
    let id = file["id"].as_str().unwrap();
    let download = f
        .request(
            Method::GET,
            &format!("/api/v1/files/{id}/download"),
            Some(&owner),
            None,
        )
        .await;
    assert_eq!(download.status(), StatusCode::OK);
    assert_eq!(download.bytes().await.unwrap().as_ref(), bytes);
    let range = f
        .client
        .get(format!("{}/api/v1/files/{id}/download", f.base))
        .header("Cookie", &owner)
        .header("Range", "bytes=0-9")
        .send()
        .await
        .unwrap();
    assert_eq!(range.status(), StatusCode::PARTIAL_CONTENT);
    assert_eq!(range.bytes().await.unwrap().as_ref(), &bytes[..10]);
    let share: Value = f
        .request(
            Method::POST,
            "/api/v1/shares",
            Some(&owner),
            Some(json!({"resource_id":id,"access":"anyone","allow_download":true})),
        )
        .await
        .json()
        .await
        .unwrap();
    assert!(share["url"].is_string(), "{share}");
    let token = share["url"].as_str().unwrap().rsplit('/').next().unwrap();
    let public = f
        .request(
            Method::GET,
            &format!("/api/v1/public/shares/{token}/download"),
            None,
            None,
        )
        .await;
    assert_eq!(public.status(), StatusCode::OK);
    assert_eq!(public.bytes().await.unwrap().as_ref(), bytes);
    let hold = f
        .resource(
            &owner,
            "legal_hold",
            "Preserve signed evidence",
            json!({"active":true,"resource_ids":[id]}),
        )
        .await;
    let current: Value = f
        .request(
            Method::GET,
            &format!("/api/v1/resource/{id}"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    let version = current["version"].as_i64().unwrap();
    let deleted = f
        .request(
            Method::DELETE,
            &format!("/api/v1/resource/{id}?expected_version={version}"),
            Some(&owner),
            None,
        )
        .await;
    let status = deleted.status();
    let deleted: Value = deleted.json().await.unwrap();
    assert_eq!(status, StatusCode::OK, "{deleted}");
    let version = deleted["version"].as_i64().unwrap();
    assert_eq!(
        f.request(
            Method::DELETE,
            &format!("/api/v1/resource/{id}/purge?expected_version={version}"),
            Some(&owner),
            None
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    assert_eq!(
        f.request(
            Method::GET,
            &format!("/api/v1/public/shares/{token}/download"),
            None,
            None
        )
        .await
        .status(),
        StatusCode::NOT_FOUND
    );
    let restored = f
        .request(
            Method::POST,
            &format!("/api/v1/resource/{id}/restore"),
            Some(&owner),
            Some(json!({"expected_version":version})),
        )
        .await;
    assert_eq!(restored.status(), StatusCode::OK);
    let after = f
        .request(
            Method::GET,
            &format!("/api/v1/files/{id}/download"),
            Some(&owner),
            None,
        )
        .await;
    assert_eq!(after.bytes().await.unwrap().as_ref(), bytes);
    assert!(hold["id"].is_string());
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "requires a dedicated disposable PostgreSQL server"]
async fn tables_form_idempotency_calendar_and_no_fake_providers() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let table=f.resource(&owner,"table","Customers",json!({"fields":[{"id":"name","name":"Name","type":"text","required":true},{"id":"amount","name":"Amount","type":"number"}],"views":[]})).await;
    let id = table["id"].as_str().unwrap();
    let response = f
        .request(
            Method::POST,
            &format!("/api/v1/tables/{id}/records"),
            Some(&owner),
            Some(json!({"name":"Missing name","values":{"amount":12}})),
        )
        .await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let response = f
        .request(
            Method::POST,
            &format!("/api/v1/tables/{id}/records"),
            Some(&owner),
            Some(json!({"name":"Customer A","values":{"name":"Customer A","amount":12}})),
        )
        .await;
    assert!(
        response.status().is_success(),
        "{}",
        response.text().await.unwrap_or_default()
    );
    let records: Vec<Value> = f
        .request(
            Method::GET,
            &format!("/api/v1/tables/{id}/records"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(records.len(), 1);
    let form=f.resource(&owner,"form","Customer intake",json!({"fields":[{"id":"name","name":"Name","type":"text","required":true}],"table_id":id})).await;
    let form_id = form["id"].as_str().unwrap();
    let published: Value = f
        .request(
            Method::POST,
            &format!("/api/v1/forms/{form_id}/publish"),
            Some(&owner),
            Some(json!({"published":true,"expected_version":form["version"],"access":"public"})),
        )
        .await
        .json()
        .await
        .unwrap();
    let url = published["url"].as_str().unwrap();
    let query = url.split('?').nth(1).unwrap();
    let path = format!("/api/v1/public/forms/{form_id}?{query}");
    let public: Value = f
        .request(Method::GET, &path, None, None)
        .await
        .json()
        .await
        .unwrap();
    assert!(public["data"].get("publish_token_hash").is_none());
    assert!(public["data"].get("table_id").is_none());
    let key = Uuid::new_v4();
    let input = json!({"values":{"name":"Public submit"},"submission_key":key});
    let first: Value = f
        .request(Method::POST, &path, None, Some(input.clone()))
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(first["created"], true, "{first}");
    let second: Value = f
        .request(Method::POST, &path, None, Some(input))
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(first["id"], second["id"]);
    assert_eq!(second["duplicate"], true);
    // A redacted public form can be edited without sending secret publication hashes back.
    let mut form: Value = f
        .request(
            Method::GET,
            &format!("/api/v1/resource/{form_id}"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    form["data"]["description"] = json!("Updated description");
    let response = f
        .request(
            Method::PATCH,
            &format!("/api/v1/resource/{form_id}"),
            Some(&owner),
            Some(json!({"data":form["data"],"expected_version":form["version"]})),
        )
        .await;
    assert!(
        response.status().is_success(),
        "{}",
        response.text().await.unwrap_or_default()
    );
    f.request(
        Method::PATCH,
        "/api/v1/settings",
        Some(&owner),
        Some(json!({"modules":{"forms":false}})),
    )
    .await
    .error_for_status()
    .unwrap();
    assert_eq!(
        f.request(
            Method::POST,
            &path,
            None,
            Some(json!({"values":{"name":"Must not save"}}))
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    let event=f.resource(&owner,"event","Planning meeting",json!({"start":"2026-11-01T09:00:00+08:00","end":"2026-11-01T10:00:00+08:00","timezone":"Asia/Hong_Kong"})).await;
    let ics = f
        .request(Method::GET, "/api/v1/calendar/ics", Some(&owner), None)
        .await
        .text()
        .await
        .unwrap();
    assert!(ics.contains("Planning meeting"));
    assert!(ics.contains(event["id"].as_str().unwrap()));
    let connections: Value = f
        .request(
            Method::GET,
            "/api/v1/calendar/connections",
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    assert!(connections.as_array().is_some_and(|v| v.is_empty()));
    let response = f
        .request(
            Method::POST,
            "/api/v1/calendar/connections",
            Some(&owner),
            Some(json!({"provider":"google"})),
        )
        .await;
    assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    let settings: Value = f
        .request(Method::GET, "/api/v1/settings", Some(&owner), None)
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(settings["services"]["documenso"]["configured"], false);
    let generated=f.request(Method::POST,"/api/v1/documents/generate",Some(&owner),Some(json!({"name":"Generated agreement","template":"# Agreement\nCompany: {{ company }}","values":{"company":"藍圖工作室"},"format":"docx"}))).await;
    let status = generated.status();
    let generated: Value = generated.json().await.unwrap();
    assert!(status.is_success(), "{generated}");
    f.cleanup().await;
}
