//! Runs against an explicitly supplied disposable PostgreSQL database.
//! `TEST_DATABASE_URL=... cargo test --test v2_workflows -- --ignored`

use axum::{
    Json, Router,
    body::Body,
    http::{Request, StatusCode},
    routing::post,
};
use blueprint_backend::{Actor, AppState, Config, Job, Resource, app, auth, repo, workflows};
use chrono::Utc;
use hmac::{Hmac, Mac};
use http_body_util::BodyExt;
use serde_json::{Value, json};
use sha2::Sha256;
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};
use tower::ServiceExt;
use uuid::Uuid;

struct Fixture {
    state: AppState,
    actor: Actor,
    token: String,
    _directory: tempfile::TempDir,
}

impl Fixture {
    async fn new() -> Self {
        let database_url = std::env::var("TEST_DATABASE_URL")
            .expect("TEST_DATABASE_URL must name a disposable local database");
        let directory = tempfile::tempdir().unwrap();
        let config = Config {
            database_url,
            data_dir: directory.path().into(),
            bind: "127.0.0.1:0".into(),
            public_url: "http://localhost:8788".into(),
            setup_token: None,
            services: HashMap::new(),
            secret_key: [42; 32],
            ui_dir: directory.path().join("ui"),
        };
        let state = AppState::connect(config).await.unwrap();
        let id = Uuid::new_v4();
        let email = format!("workflow-{id}@example.test");
        sqlx::query("INSERT INTO users(id,email,name,password_hash,role) VALUES($1,$2,'Workflow integration test','test-hash-not-used-for-login','owner')").bind(id).bind(&email).execute(&state.pool).await.unwrap();
        let actor = auth::load_actor(&state.pool, id).await.unwrap();
        let token = format!("test-token-{}", Uuid::new_v4());
        sqlx::query("INSERT INTO api_tokens(id,user_id,name,token_hash,scopes) VALUES($1,$2,'Workflow test',$3,'[\"*\"]'::jsonb)").bind(Uuid::new_v4()).bind(id).bind(auth::token_hash(&token)).execute(&state.pool).await.unwrap();
        Self {
            state,
            actor,
            token,
            _directory: directory,
        }
    }

    async fn request(&self, method: &str, path: &str, body: Value) -> (StatusCode, Value) {
        let request = Request::builder()
            .method(method)
            .uri(path)
            .header("Content-Type", "application/json")
            .header("X-Blueprint-Request", "1")
            .header("Authorization", format!("Bearer {}", self.token))
            .body(Body::from(serde_json::to_vec(&body).unwrap()))
            .unwrap();
        let response = app(self.state.clone()).oneshot(request).await.unwrap();
        let status = response.status();
        let bytes = response.into_body().collect().await.unwrap().to_bytes();
        (
            status,
            serde_json::from_slice(&bytes)
                .unwrap_or_else(|_| json!({"non_json":String::from_utf8_lossy(&bytes)})),
        )
    }

    async fn create(&self, kind: &str, name: &str, data: Value) -> Resource {
        let (status, result) = self
            .request(
                "POST",
                "/api/v1/resources",
                json!({"kind":kind,"name":name,"data":data}),
            )
            .await;
        assert!(status.is_success(), "{status}: {result}");
        serde_json::from_value(result).unwrap()
    }

    async fn workflow(&self, nodes: Value, edges: Value) -> Resource {
        let workflow = self
            .create(
                "workflow",
                "Integration flow",
                json!({"trigger":{"type":"manual"},"nodes":nodes,"edges":edges}),
            )
            .await;
        let (status, result) = self
            .request(
                "POST",
                &format!("/api/v1/workflows/{}/activate", workflow.id),
                json!({}),
            )
            .await;
        assert!(status.is_success(), "{status}: {result}");
        serde_json::from_value(result).unwrap()
    }

    async fn run(&self, workflow: &Resource, input: Value, key: &str) -> Resource {
        let (status, result) = self
            .request(
                "POST",
                &format!("/api/v1/workflows/{}/run", workflow.id),
                json!({"input":input,"idempotency_key":key}),
            )
            .await;
        assert!(status.is_success(), "{status}: {result}");
        serde_json::from_value(result).unwrap()
    }

    async fn tick(&self, run: &Resource) -> Value {
        let job = Job {
            id: Uuid::new_v4(),
            owner_id: self.actor.id,
            kind: "workflow.run".into(),
            status: "running".into(),
            payload: json!({"run_id":run.id,"actor_id":self.actor.id}),
            result: json!({}),
            error: None,
            attempts: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
        };
        workflows::process_job(&self.state, &job).await.unwrap()
    }
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn published_snapshot_and_idempotent_task_survive_replay() {
    let f = Fixture::new().await;
    let workflow = f
        .workflow(
            json!([
                {"id":"start","type":"trigger"},
                {"id":"task","type":"task","label":"Follow up","config":{"title":"${input.title}"}}
            ]),
            json!([{"source":"start","target":"task"}]),
        )
        .await;
    let run = f
        .run(&workflow, json!({"title":"原有發布版本"}), "one")
        .await;
    let mut changed = workflow.data.clone();
    changed["nodes"][1]["config"]["title"] = json!("未發布草稿");
    let (status, _) = f
        .request(
            "PATCH",
            &format!("/api/v1/resource/{}", workflow.id),
            json!({"data":changed,"expected_version":workflow.version}),
        )
        .await;
    assert!(status.is_success());
    assert_eq!(f.tick(&run).await["status"], "completed");
    assert_eq!(f.tick(&run).await["status"], "completed");
    let tasks = repo::list(&f.state.pool, &f.actor, "task", None)
        .await
        .unwrap();
    let created: Vec<_> = tasks
        .iter()
        .filter(|r| r.data["workflow_run_id"] == json!(run.id))
        .collect();
    assert_eq!(created.len(), 1);
    assert_eq!(created[0].name, "原有發布版本");
    let again = f
        .run(&workflow, json!({"title":"原有發布版本"}), "one")
        .await;
    assert_eq!(again.id, run.id);
    let (status, _) = f
        .request(
            "POST",
            &format!("/api/v1/workflows/{}/run", workflow.id),
            json!({"input":{"title":"different"},"idempotency_key":"one"}),
        )
        .await;
    assert_eq!(status, StatusCode::CONFLICT);
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn approval_waits_persistently_and_reject_branch_does_not_execute() {
    let f = Fixture::new().await;
    let workflow=f.workflow(json!([
        {"id":"start","type":"trigger"},
        {"id":"review","type":"approval","config":{"title":"批准操作"}},
        {"id":"task","type":"task","label":"Must not run","config":{"title":"被拒絕後不應建立"}}
    ]),json!([{"source":"start","target":"review"},{"source":"review","target":"task","source_handle":"true"}])).await;
    let run = f.run(&workflow, json!({}), "approval").await;
    assert_eq!(f.tick(&run).await["status"], "waiting");
    let persisted = repo::raw_get(&f.state.pool, run.id).await.unwrap();
    assert_eq!(persisted.data["steps"][1]["status"], "waiting");
    let (status, result) = f
        .request(
            "POST",
            &format!("/api/v1/runs/{}/approve", run.id),
            json!({"node_id":"review","approved":false,"comment":"不批准"}),
        )
        .await;
    assert!(status.is_success(), "{result}");
    assert_eq!(f.tick(&run).await["status"], "completed");
    let tasks = repo::list(&f.state.pool, &f.actor, "task", None)
        .await
        .unwrap();
    assert!(
        !tasks
            .iter()
            .any(|r| r.data["workflow_run_id"] == json!(run.id))
    );
    // A workflow-scoped token can stop its own internal execution artifact, and
    // a late approval must not revive a cancelled run.
    sqlx::query("UPDATE api_tokens SET resource_ids=$2 WHERE token_hash=$1")
        .bind(auth::token_hash(&f.token))
        .bind(json!([workflow.id]))
        .execute(&f.state.pool)
        .await
        .unwrap();
    let cancelled = f.run(&workflow, json!({}), "cancel-review").await;
    assert_eq!(f.tick(&cancelled).await["status"], "waiting");
    let (status, response) = f
        .request(
            "POST",
            &format!("/api/v1/runs/{}/cancel", cancelled.id),
            json!({}),
        )
        .await;
    assert!(status.is_success(), "{response}");
    assert_eq!(response["data"]["status"], "cancelled");
    let (status, _) = f
        .request(
            "POST",
            &format!("/api/v1/runs/{}/approve", cancelled.id),
            json!({"node_id":"review","approved":true}),
        )
        .await;
    assert_eq!(status, StatusCode::CONFLICT);
    assert_eq!(f.tick(&cancelled).await["status"], "cancelled");
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn revoked_initiating_token_stops_queued_work() {
    let f = Fixture::new().await;
    let workflow=f.workflow(json!([{"id":"start","type":"trigger"},{"id":"task","type":"task","label":"Should stop","config":{"title":"Must not execute"}}]),json!([{"source":"start","target":"task"}])).await;
    let run = f.run(&workflow, json!({}), "revoked").await;
    sqlx::query("UPDATE api_tokens SET revoked_at=now() WHERE token_hash=$1")
        .bind(auth::token_hash(&f.token))
        .execute(&f.state.pool)
        .await
        .unwrap();
    assert_eq!(f.tick(&run).await["status"], "permission_revoked");
    let tasks = repo::list(&f.state.pool, &f.actor, "task", None)
        .await
        .unwrap();
    assert!(
        !tasks
            .iter()
            .any(|r| r.data["workflow_run_id"] == json!(run.id))
    );
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn unknown_external_write_is_not_retried_automatically() {
    let f = Fixture::new().await;
    let calls = Arc::new(AtomicUsize::new(0));
    let count = calls.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/write", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new().route(
                "/write",
                post(move || {
                    let count = count.clone();
                    async move {
                        count.fetch_add(1, Ordering::SeqCst);
                        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        Json(json!({"committed":true}))
                    }
                }),
            ),
        )
        .await
        .unwrap();
    });
    let connector = f
        .create(
            "connector",
            "Test private service",
            json!({"endpoint":endpoint,"allow_private_network":true,"timeout_seconds":1,"allowed_methods":["POST"]}),
        )
        .await;
    let workflow=f.workflow(json!([{"id":"start","type":"trigger"},{"id":"write","type":"http","config":{"connector_id":connector.id,"method":"POST","body":{"operation":"one"}}}]),json!([{"source":"start","target":"write"}])).await;
    let run = f.run(&workflow, json!({}), "unknown-write").await;
    assert_eq!(f.tick(&run).await["status"], "needs_attention");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert_eq!(f.tick(&run).await["status"], "needs_attention");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    server.abort();
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn interrupted_post_requires_connector_idempotency_approval_before_replay() {
    let f = Fixture::new().await;
    let calls = Arc::new(AtomicUsize::new(0));
    let count = calls.clone();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/write", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new().route(
                "/write",
                post(move || {
                    let count = count.clone();
                    async move {
                        count.fetch_add(1, Ordering::SeqCst);
                        Json(json!({"committed":true}))
                    }
                }),
            ),
        )
        .await
        .unwrap();
    });
    let connector = f.create(
        "connector",
        "Non-idempotent write service",
        json!({"endpoint":endpoint,"allow_private_network":true,"allowed_methods":["POST"],"idempotent":false}),
    ).await;
    let workflow=f.workflow(json!([
        {"id":"start","type":"trigger"},
        {"id":"write","type":"http","config":{"connector_id":connector.id,"method":"POST","idempotent":true,"body":{"operation":"charge-once"}}}
    ]),json!([{"source":"start","target":"write"}])).await;
    let run = f.run(&workflow, json!({}), "interrupted-post").await;

    // Model a worker dying after the real provider committed and before the
    // workflow_effects receipt could be persisted. The remote effect is real;
    // only the durable crash-state fixture is inserted directly.
    let response = f
        .state
        .http
        .post(&endpoint)
        .header("Idempotency-Key", format!("blueprint:{}:write", run.id))
        .json(&json!({"operation":"charge-once"}))
        .send()
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let mut crash_state = run.data.clone();
    crash_state["status"] = json!("running");
    crash_state["pending"] = json!([]);
    crash_state["steps"] = json!([
        {"node_id":"start","status":"completed","output":{}},
        {"node_id":"write","status":"running","output":null}
    ]);
    sqlx::query("UPDATE resources SET data=$2,version=version+1 WHERE id=$1")
        .bind(run.id)
        .bind(crash_state)
        .execute(&f.state.pool)
        .await
        .unwrap();
    let receipts: i64 = sqlx::query_scalar(
        "SELECT COUNT(*) FROM workflow_effects WHERE run_id=$1 AND node_key='write'",
    )
    .bind(run.id)
    .fetch_one(&f.state.pool)
    .await
    .unwrap();
    assert_eq!(receipts, 0);

    assert_eq!(f.tick(&run).await["status"], "needs_attention");
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "a node-only flag must not replay the committed POST"
    );
    let recovered = repo::raw_get(&f.state.pool, run.id).await.unwrap();
    assert_eq!(recovered.data["steps"][1]["status"], "needs_attention");
    assert_eq!(f.tick(&run).await["status"], "needs_attention");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    server.abort();
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn signed_callback_resumes_once_and_rejects_tampering() {
    let f = Fixture::new().await;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}/worker", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        axum::serve(
            listener,
            Router::new().route(
                "/worker",
                post(|| async {
                    (
                        StatusCode::ACCEPTED,
                        Json(json!({"status":"accepted","execution_id":"remote-1"})),
                    )
                }),
            ),
        )
        .await
        .unwrap();
    });
    let secret = "test-callback-secret-not-production";
    let agent = f
        .create(
            "agent",
            "Asynchronous worker",
            json!({"endpoint":endpoint,"allow_private_network":true,"callback_secret":secret,"capabilities":["summarize"]}),
        )
        .await;
    assert!(agent.data.get("callback_secret_encrypted").is_none());
    assert_eq!(agent.data["callback_secret_configured"], true);
    let (status, result) = f
        .request(
            "POST",
            &format!("/api/v1/agents/{}/dispatch", agent.id),
            json!({"task":"summarize","input":{"title":"example"},"idempotency_key":"callback"}),
        )
        .await;
    assert!(status.is_success(), "{result}");
    let run: Resource = serde_json::from_value(result).unwrap();
    assert_eq!(f.tick(&run).await["status"], "waiting");
    let body=serde_json::to_vec(&json!({"run_id":run.id,"node_id":"worker","event_id":"callback-event-one","status":"completed","output":{"summary":"真實回報"}})).unwrap();
    let timestamp = Utc::now().timestamp().to_string();
    let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes()).unwrap();
    mac.update(timestamp.as_bytes());
    mac.update(b".");
    mac.update(&body);
    let signature = hex::encode(mac.finalize().into_bytes());
    let request = |signature: &str| {
        Request::builder()
            .method("POST")
            .uri(format!("/api/v1/public/callbacks/{}", agent.id))
            .header("Content-Type", "application/json")
            .header("X-Blueprint-Timestamp", &timestamp)
            .header("X-Blueprint-Signature", signature)
            .body(Body::from(body.clone()))
            .unwrap()
    };
    let response = app(f.state.clone()).oneshot(request("0000")).await.unwrap();
    assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    let response = app(f.state.clone())
        .oneshot(request(&signature))
        .await
        .unwrap();
    assert!(response.status().is_success());
    let response = app(f.state.clone())
        .oneshot(request(&signature))
        .await
        .unwrap();
    let result: Value =
        serde_json::from_slice(&response.into_body().collect().await.unwrap().to_bytes()).unwrap();
    assert_eq!(result["duplicate"], true);
    assert_eq!(f.tick(&run).await["status"], "completed");
    let current = repo::raw_get(&f.state.pool, run.id).await.unwrap();
    assert_eq!(
        current.data["steps"][1]["output"]["output"]["summary"],
        "真實回報"
    );
    server.abort();
}
