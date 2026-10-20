//! Security regressions. All data lives in a newly created disposable database.
use blueprint_backend::*;
use reqwest::{Client, Method, StatusCode};
use serde_json::{Value, json};
use std::collections::HashMap;
use uuid::Uuid;

const PASSWORD: &str = "security-regression-password-937";
struct Fixture {
    state: AppState,
    base: String,
    client: Client,
    admin: sqlx::PgPool,
    name: String,
    _directory: tempfile::TempDir,
    server: tokio::task::JoinHandle<()>,
}
impl Fixture {
    async fn new() -> Self {
        let raw =
            std::env::var("TEST_DATABASE_URL").expect("Use a disposable PostgreSQL test server");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let name = format!("blueprint_security_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {name}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut url = url::Url::parse(&raw).unwrap();
        url.set_path(&format!("/{name}"));
        let directory = tempfile::tempdir().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let config = Config {
            database_url: url.to_string(),
            data_dir: directory.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: Some("disposable-security-setup-token".into()),
            services: HashMap::new(),
            secret_key: [0x53; 32],
            ui_dir: directory.path().join("ui"),
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
            _directory: directory,
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
    async fn setup(&self) -> String {
        let response = self.request(Method::POST, "/api/v1/setup", None, Some(json!({
            "token":"disposable-security-setup-token", "company_name":"Security Test", "email":"owner@example.test", "name":"Owner", "password":PASSWORD
        }))).await;
        assert_eq!(response.status(), StatusCode::OK);
        response.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .into()
    }
    async fn member(&self, owner: &str, email: &str) -> (Value, String) {
        let response = self
            .request(
                Method::POST,
                "/api/v1/users",
                Some(owner),
                Some(json!({"email":email,"name":"Member","role":"member","password":PASSWORD})),
            )
            .await;
        assert_eq!(response.status(), StatusCode::OK);
        let user = response.json().await.unwrap();
        let response = self
            .request(
                Method::POST,
                "/api/v1/login",
                None,
                Some(json!({"email":email,"password":PASSWORD})),
            )
            .await;
        assert_eq!(response.status(), StatusCode::OK);
        let cookie = response.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .into();
        (user, cookie)
    }
    async fn resource(&self, cookie: &str, input: Value) -> Value {
        let response = self
            .request(Method::POST, "/api/v1/resources", Some(cookie), Some(input))
            .await;
        let status = response.status();
        let body = response.json().await.unwrap();
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
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn disabled_pending_invitation_cannot_reactivate_account() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let invite: Value = f
        .request(
            Method::POST,
            "/api/v1/users",
            Some(&owner),
            Some(json!({"email":"invited@example.test","name":"Invited","role":"member"})),
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(invite["active"], false);
    let id = invite["id"].as_str().unwrap();
    let url = url::Url::parse(invite["invite_url"].as_str().unwrap()).unwrap();
    let token = url
        .query_pairs()
        .find(|(name, _)| name == "token")
        .unwrap()
        .1
        .into_owned();
    f.request(
        Method::PATCH,
        &format!("/api/v1/users/{id}"),
        Some(&owner),
        Some(json!({"active":false})),
    )
    .await
    .error_for_status()
    .unwrap();
    let response = f
        .request(
            Method::POST,
            "/api/v1/activate",
            None,
            Some(json!({"token":token,"password":PASSWORD})),
        )
        .await;
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn failed_scim_group_patch_never_grants_membership_and_reads_current_user_membership() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let (member, _) = f.member(&owner, "member@example.test").await;
    let member_id = member["id"].as_str().unwrap();
    let response = f.request(Method::POST, "/api/v1/scim/v2/Groups", Some(&owner), Some(json!({"schemas":["urn:ietf:params:scim:schemas:core:2.0:Group"],"displayName":"Operations","members":[]}))).await;
    let status = response.status();
    let group: Value = response.json().await.unwrap();
    assert_eq!(status, StatusCode::CREATED, "{group}");
    let group_id = group["id"].as_str().unwrap();
    let response = f.request(Method::PATCH, &format!("/api/v1/scim/v2/Groups/{group_id}"), Some(&owner), Some(json!({"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"members","value":[{"value":member_id}]},{"op":"replace","path":"displayName","value":""}]}))).await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let user = auth::actor_by_id(&f.state, Uuid::parse_str(member_id).unwrap())
        .await
        .unwrap();
    assert!(!user.groups.iter().any(|id| id == group_id));
    let group: Value = f
        .request(
            Method::GET,
            &format!("/api/v1/scim/v2/Groups/{group_id}"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(group["displayName"], "Operations");
    assert_eq!(group["members"], json!([]));
    let response = f.request(Method::PATCH, &format!("/api/v1/scim/v2/Groups/{group_id}"), Some(&owner), Some(json!({"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"add","path":"members","value":[{"value":member_id}]}]}))).await;
    let status = response.status();
    let changed: Value = response.json().await.unwrap();
    assert_eq!(status, StatusCode::OK, "{changed}");
    assert_eq!(changed["members"][0]["value"], member_id);
    assert!(
        auth::actor_by_id(&f.state, Uuid::parse_str(member_id).unwrap())
            .await
            .unwrap()
            .groups
            .iter()
            .any(|id| id == group_id)
    );
    f.request(Method::PATCH, &format!("/api/v1/scim/v2/Groups/{group_id}"), Some(&owner), Some(json!({"schemas":["urn:ietf:params:scim:api:messages:2.0:PatchOp"],"Operations":[{"op":"remove","path":format!("members[value eq \"{member_id}\"]")}]}))).await.error_for_status().unwrap();
    assert!(
        !auth::actor_by_id(&f.state, Uuid::parse_str(member_id).unwrap())
            .await
            .unwrap()
            .groups
            .iter()
            .any(|id| id == group_id)
    );
    let response = f.request(Method::POST, "/api/v1/scim/v2/Groups", Some(&owner), Some(json!({"schemas":["urn:ietf:params:scim:schemas:core:2.0:Group"],"displayName":"Must roll back","members":[{"value":Uuid::new_v4()}]}))).await;
    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    let leftover: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM resources WHERE kind='group' AND name='Must roll back'",
    )
    .fetch_one(&f.state.pool)
    .await
    .unwrap();
    assert_eq!(leftover, 0);
    f.request(
        Method::PATCH,
        &format!("/api/v1/users/{member_id}"),
        Some(&owner),
        Some(json!({"groups":[group_id]})),
    )
    .await
    .error_for_status()
    .unwrap();
    let group: Value = f
        .request(
            Method::GET,
            &format!("/api/v1/scim/v2/Groups/{group_id}"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    assert_eq!(group["members"][0]["value"], member_id);
    let generic: Value = f
        .request(
            Method::GET,
            &format!("/api/v1/resource/{group_id}"),
            Some(&owner),
            None,
        )
        .await
        .json()
        .await
        .unwrap();
    f.request(
        Method::DELETE,
        &format!(
            "/api/v1/resource/{group_id}?expected_version={}",
            generic["version"]
        ),
        Some(&owner),
        None,
    )
    .await
    .error_for_status()
    .unwrap();
    assert!(
        !auth::actor_by_id(&f.state, Uuid::parse_str(member_id).unwrap())
            .await
            .unwrap()
            .groups
            .iter()
            .any(|id| id == group_id)
    );
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn disabled_files_module_blocks_specialized_upload_and_existing_download() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let form = reqwest::multipart::Form::new().part(
        "file",
        reqwest::multipart::Part::text("private test content").file_name("private.txt"),
    );
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
    f.request(
        Method::PATCH,
        "/api/v1/settings",
        Some(&owner),
        Some(json!({"modules":{"files":false}})),
    )
    .await
    .error_for_status()
    .unwrap();
    let form = reqwest::multipart::Form::new().part(
        "file",
        reqwest::multipart::Part::text("must not persist").file_name("blocked.txt"),
    );
    let response = f
        .client
        .post(format!("{}/api/v1/files/upload", f.base))
        .header("Cookie", &owner)
        .header("X-Blueprint-Request", "1")
        .multipart(form)
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::FORBIDDEN);
    assert_eq!(
        f.request(
            Method::GET,
            &format!("/api/v1/files/{id}/download"),
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
            &format!("/api/v1/resource/{id}/history"),
            Some(&owner),
            None
        )
        .await
        .status(),
        StatusCode::FORBIDDEN
    );
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn trash_requires_read_scope_and_current_ancestor_access() {
    let f = Fixture::new().await;
    let owner = f.setup().await;
    let (member, member_cookie) = f.member(&owner, "member@example.test").await;
    let member_id = member["id"].as_str().unwrap();
    let folder=f.resource(&owner,json!({"kind":"folder","name":"Restricted department","acl":{format!("user:{member_id}"):"editor"},"data":{}})).await;
    let folder_id = folder["id"].as_str().unwrap();
    let note=f.resource(&member_cookie,json!({"kind":"note","name":"Department note","parent_id":folder_id,"data":{"content":"Sensitive content"}})).await;
    let note_id = note["id"].as_str().unwrap();
    f.request(
        Method::DELETE,
        &format!("/api/v1/resource/{note_id}?expected_version=1"),
        Some(&member_cookie),
        None,
    )
    .await
    .error_for_status()
    .unwrap();
    f.request(
        Method::PATCH,
        &format!("/api/v1/resource/{folder_id}"),
        Some(&owner),
        Some(json!({"acl":{},"expected_version":1})),
    )
    .await
    .error_for_status()
    .unwrap();
    let trash: Vec<Value> = f
        .request(Method::GET, "/api/v1/trash", Some(&member_cookie), None)
        .await
        .json()
        .await
        .unwrap();
    assert!(!trash.iter().any(|r| r["id"] == note_id));
    let own=f.resource(&member_cookie,json!({"kind":"note","name":"Own private note","data":{"content":"Private own content"}})).await;
    let own_id = own["id"].as_str().unwrap();
    f.request(
        Method::DELETE,
        &format!("/api/v1/resource/{own_id}?expected_version=1"),
        Some(&member_cookie),
        None,
    )
    .await
    .error_for_status()
    .unwrap();
    let token: Value = f
        .request(
            Method::POST,
            "/api/v1/tokens",
            Some(&member_cookie),
            Some(json!({"name":"Write only","scopes":["content:write"]})),
        )
        .await
        .json()
        .await
        .unwrap();
    let response = f
        .client
        .get(format!("{}/api/v1/trash", f.base))
        .bearer_auth(token["token"].as_str().unwrap())
        .send()
        .await
        .unwrap();
    if response.status().is_success() {
        let trash: Vec<Value> = response.json().await.unwrap();
        assert!(!trash.iter().any(|r| r["id"] == own_id));
    } else {
        assert_eq!(response.status(), StatusCode::FORBIDDEN);
    }
    f.cleanup().await;
}
