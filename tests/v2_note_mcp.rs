//! Real HTTP/Yjs regression for Codex edits after a web editor initialized a note.
use base64::Engine;
use blueprint_backend::*;
use reqwest::{Client, Method, StatusCode};
use serde_json::{Value, json};
use std::collections::HashMap;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use uuid::Uuid;
use yrs::{Doc, GetString, Transact, Update, updates::decoder::Decode};

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
            .expect("TEST_DATABASE_URL must point to disposable PostgreSQL");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let name = format!("blueprint_note_mcp_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {name}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut database = url::Url::parse(&raw).unwrap();
        database.set_path(&format!("/{name}"));
        let directory = tempfile::tempdir().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let state = AppState::connect(Config {
            database_url: database.to_string(),
            data_dir: directory.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: Some("disposable-note-fixture-setup".into()),
            services: HashMap::new(),
            secret_key: [0x47; 32],
            ui_dir: directory.path().join("ui"),
        })
        .await
        .unwrap();
        let app = app(state.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            state,
            base,
            client: Client::builder().no_proxy().build().unwrap(),
            admin,
            name,
            _dir: directory,
            server,
        }
    }

    async fn request(
        &self,
        method: Method,
        path: &str,
        auth: Option<&str>,
        body: Option<Value>,
    ) -> reqwest::Response {
        let mut request = self
            .client
            .request(method, format!("{}{path}", self.base))
            .header("X-Blueprint-Request", "1");
        if let Some(auth) = auth {
            request = if auth.starts_with("bp_") {
                request.bearer_auth(auth)
            } else {
                request.header("Cookie", auth)
            };
        }
        if let Some(body) = body {
            request = request.json(&body);
        }
        request.send().await.unwrap()
    }

    async fn setup(&self) -> String {
        let response = self.request(Method::POST, "/api/v1/setup", None, Some(json!({
            "token":"disposable-note-fixture-setup", "company_name":"Note MCP test", "email":"owner@example.test",
            "name":"Test owner", "password":"disposable-note-password-4715"
        }))).await;
        assert_eq!(response.status(), StatusCode::OK);
        response.headers()["set-cookie"]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_string()
    }

    async fn get_note(&self, cookie: &str, id: &str) -> Value {
        let response = self
            .request(
                Method::GET,
                &format!("/api/v1/resource/{id}"),
                Some(cookie),
                None,
            )
            .await;
        assert_eq!(response.status(), StatusCode::OK);
        response.json().await.unwrap()
    }

    async fn mcp_update(&self, auth: &str, arguments: Value) -> Value {
        let response = self.request(Method::POST, "/api/v1/mcp", Some(auth), Some(json!({
            "jsonrpc":"2.0", "id":17, "method":"tools/call", "params":{"name":"blueprint_update", "arguments":arguments}
        }))).await;
        assert_eq!(response.status(), StatusCode::OK);
        response.json().await.unwrap()
    }

    async fn open_web_editor(&self, cookie: &str, id: &str) -> tokio::net::TcpStream {
        let parsed = url::Url::parse(&self.base).unwrap();
        let mut socket = tokio::net::TcpStream::connect(("127.0.0.1", parsed.port().unwrap()))
            .await
            .unwrap();
        socket.write_all(format!(
            "GET /api/v1/collaboration/{id}/ws HTTP/1.1\r\nHost: 127.0.0.1\r\nOrigin: {}\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nCookie: {cookie}\r\n\r\n", self.base
        ).as_bytes()).await.unwrap();
        let mut headers = Vec::new();
        let mut chunk = [0u8; 1024];
        while !headers.windows(4).any(|window| window == b"\r\n\r\n") {
            let count =
                tokio::time::timeout(std::time::Duration::from_secs(5), socket.read(&mut chunk))
                    .await
                    .unwrap()
                    .unwrap();
            assert!(count > 0);
            headers.extend_from_slice(&chunk[..count]);
        }
        assert!(headers.starts_with(b"HTTP/1.1 101"));
        socket
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
async fn mcp_note_edit_updates_real_yjs_state_and_keeps_cas_metadata_and_permissions() {
    let fixture = Fixture::new().await;
    let owner = fixture.setup().await;
    let created = fixture.request(Method::POST, "/api/v1/resources", Some(&owner), Some(json!({
        "kind":"note", "name":"Meeting answers", "data":{"content":"開始 🏗️", "tags":["project"],
        "blueprint_activity":{"activity_id":"fixture-meeting-17", "source_refs":[]}}
    }))).await;
    assert_eq!(created.status(), StatusCode::CREATED);
    let note: Value = created.json().await.unwrap();
    let id = note["id"].as_str().unwrap();
    let web_editor = fixture.open_web_editor(&owner, id).await;
    let initialized = fixture.get_note(&owner, id).await;
    assert!(initialized["data"]["crdt_state"].is_string());
    let version = initialized["version"].as_i64().unwrap();
    let content = "# 會面 🏗️\n問題：今次要決定甚麼？\n回答：確認下星期施工安排。";
    let response = fixture
        .mcp_update(
            &owner,
            json!({"id":id,"expected_version":version,"data":{"content":content}}),
        )
        .await;
    assert_eq!(response["result"]["isError"], false, "{response}");
    let saved = fixture.get_note(&owner, id).await;
    assert_eq!(saved["data"]["content"], content);
    assert_eq!(saved["data"]["tags"], json!(["project"]));
    assert_eq!(
        saved["data"]["blueprint_activity"]["activity_id"],
        "fixture-meeting-17"
    );
    assert_eq!(saved["version"], version + 1);
    assert_ne!(
        saved["data"]["crdt_state"],
        initialized["data"]["crdt_state"]
    );
    let doc = Doc::new();
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(saved["data"]["crdt_state"].as_str().unwrap())
        .unwrap();
    doc.transact_mut()
        .apply_update(Update::decode_v1(&bytes).unwrap())
        .unwrap();
    assert_eq!(
        doc.get_or_insert_text("content")
            .get_string(&doc.transact()),
        content
    );

    let stale = fixture
        .mcp_update(
            &owner,
            json!({"id":id,"expected_version":version,"data":{"content":"stale overwrite"}}),
        )
        .await;
    assert_eq!(stale["result"]["isError"], true);
    assert!(
        stale["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("changed")
    );
    let forged = fixture.mcp_update(&owner, json!({"id":id,"expected_version":version+1,"data":{"content":"forged state", "crdt_state":"arbitrary-client-state"}})).await;
    assert_eq!(forged["result"]["isError"], true);
    assert!(
        forged["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("managed")
    );

    let response = fixture
        .request(
            Method::POST,
            "/api/v1/tokens",
            Some(&owner),
            Some(json!({
                "name":"Read-only Codex fixture", "scopes":["content:read"]
            })),
        )
        .await;
    assert_eq!(response.status(), StatusCode::OK);
    let token: Value = response.json().await.unwrap();
    let readonly = fixture
        .mcp_update(
            token["token"].as_str().unwrap(),
            json!({"id":id,"expected_version":version+1,"data":{"content":"read-only overwrite"}}),
        )
        .await;
    assert_eq!(readonly["result"]["isError"], true);
    let final_note = fixture.get_note(&owner, id).await;
    assert_eq!(final_note["version"], version + 1);
    assert_eq!(final_note["data"]["content"], content);
    drop(web_editor);
    fixture.cleanup().await;
}
