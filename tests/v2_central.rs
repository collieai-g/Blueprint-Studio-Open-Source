//! Real HTTP and isolated PostgreSQL checks for the external intelligence client.
use blueprint_backend::*;
use reqwest::{Client, Method, StatusCode};
use serde_json::{Value, json};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
};
use uuid::Uuid;

struct Fixture {
    state: AppState,
    base: String,
    client: Client,
    owner: Actor,
    token: String,
    admin: sqlx::PgPool,
    database: String,
    _directory: tempfile::TempDir,
    server: tokio::task::JoinHandle<()>,
}

impl Fixture {
    async fn new() -> Self {
        let raw = std::env::var("TEST_DATABASE_URL").expect("Disposable PostgreSQL required");
        let admin = sqlx::PgPool::connect(&raw).await.unwrap();
        let database = format!("blueprint_central_{}", Uuid::new_v4().simple());
        sqlx::query(&format!("CREATE DATABASE {database}"))
            .execute(&admin)
            .await
            .unwrap();
        let mut url = url::Url::parse(&raw).unwrap();
        url.set_path(&format!("/{database}"));
        let directory = tempfile::tempdir().unwrap();
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let config = Config {
            database_url: url.to_string(),
            data_dir: directory.path().join("data"),
            bind: "127.0.0.1:0".into(),
            public_url: base.clone(),
            setup_token: None,
            services: HashMap::new(),
            secret_key: [0x72; 32],
            ui_dir: directory.path().join("ui"),
        };
        let state = AppState::connect(config).await.unwrap();
        let owner = auth::initialize(
            &state,
            "Central Test Company",
            "owner@example.test",
            "Owner",
            "central-disposable-test-password-824",
        )
        .await
        .unwrap();
        let token = Self::make_token(&state, owner.id, json!(["*"]), None).await;
        let app = app(state.clone());
        let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
        Self {
            state,
            base,
            client: Client::builder().no_proxy().build().unwrap(),
            owner,
            token,
            admin,
            database,
            _directory: directory,
            server,
        }
    }
    async fn make_token(
        state: &AppState,
        user_id: Uuid,
        scopes: Value,
        resource_ids: Option<Value>,
    ) -> String {
        let token = format!("central-test-{}", Uuid::new_v4());
        sqlx::query("INSERT INTO api_tokens(id,user_id,name,token_hash,scopes,resource_ids) VALUES($1,$2,'Central test',$3,$4,$5)")
            .bind(Uuid::new_v4()).bind(user_id).bind(auth::token_hash(&token)).bind(scopes).bind(resource_ids).execute(&state.pool).await.unwrap();
        token
    }
    async fn member(&self) -> String {
        let id = Uuid::new_v4();
        sqlx::query("INSERT INTO users(id,email,name,password_hash,role) VALUES($1,$2,'Member','unused-test-hash','member')").bind(id).bind(format!("member-{id}@example.test")).execute(&self.state.pool).await.unwrap();
        Self::make_token(
            &self.state,
            id,
            json!(["content:read", "business:read", "integrations:read"]),
            None,
        )
        .await
    }
    async fn request(
        &self,
        method: Method,
        path: &str,
        token: &str,
        body: Value,
    ) -> (StatusCode, Value) {
        let response = self
            .client
            .request(method, format!("{}{path}", self.base))
            .bearer_auth(token)
            .header("X-Blueprint-Request", "1")
            .json(&body)
            .send()
            .await
            .unwrap();
        let status = response.status();
        (status, response.json().await.unwrap())
    }
    async fn create(&self, kind: &str, name: &str, data: Value, visibility: &str) -> Value {
        let (status, result) = self
            .request(
                Method::POST,
                "/api/v1/resources",
                &self.token,
                json!({"kind":kind,"name":name,"data":data,"visibility":visibility}),
            )
            .await;
        assert_eq!(status, StatusCode::CREATED, "{result}");
        result
    }
    async fn rpc(&self, token: &str, name: &str, args: Value) -> Value {
        let (status,result)=self.request(Method::POST,"/api/v1/mcp",token,json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":name,"arguments":args}})).await;
        assert_eq!(status, StatusCode::OK, "{result}");
        result
    }
    async fn call(&self, token: &str, name: &str, args: Value) -> Value {
        let result = self.rpc(token, name, args).await;
        assert_eq!(result["result"]["isError"], false, "{result}");
        serde_json::from_str(result["result"]["content"][0]["text"].as_str().unwrap()).unwrap()
    }
    async fn denied(&self, token: &str, name: &str, args: Value) {
        let result = self.rpc(token, name, args).await;
        assert_eq!(result["result"]["isError"], true, "{result}");
    }
    async fn cleanup(self) {
        self.server.abort();
        self.state.pool.close().await;
        sqlx::query(&format!("DROP DATABASE {} WITH (FORCE)", self.database))
            .execute(&self.admin)
            .await
            .unwrap();
        self.admin.close().await;
    }
}

fn contains_id(resources: &Value, id: &Value) -> bool {
    resources
        .as_array()
        .unwrap()
        .iter()
        .any(|resource| &resource["id"] == id)
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn context_preserves_ambiguity_and_expands_only_real_authorized_relations() {
    let f = Fixture::new().await;
    let (status, _) = f
        .request(
            Method::PATCH,
            "/api/v1/settings",
            &f.token,
            json!({"company":{"name":"Context Company","timezone":null}}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    let initial = f
        .call(&f.token, "blueprint_workspace_info", json!({}))
        .await;
    assert_eq!(initial["timezone"], "UTC");
    assert!(initial["playbook"].as_str().unwrap().contains("Codex"));
    let (status, _) = f
        .request(
            Method::PATCH,
            "/api/v1/settings",
            &f.token,
            json!({"company":{"name":"Context Company","timezone":"Asia/Hong_Kong"}}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    let info = f
        .call(&f.token, "blueprint_workspace_info", json!({}))
        .await;
    assert_eq!(info["timezone"], "Asia/Hong_Kong");
    assert!(info["local_time"].as_str().unwrap().ends_with("+08:00"));
    let projects = f
        .create(
            "table",
            "項目",
            json!({"fields":[{"id":"summary","name":"進度","type":"text"}]}),
            "private",
        )
        .await;
    let project = f
        .create(
            "record",
            "海旁裝修項目",
            json!({"table_id":projects["id"],"values":{"summary":"等待施工報價"}}),
            "private",
        )
        .await;
    let decoy = f
        .create(
            "note",
            "Not a typed relation",
            json!({"content":"Unrelated note"}),
            "private",
        )
        .await;
    let contacts=f.create("table","聯絡人",json!({"fields":[{"id":"project","name":"負責項目","type":"relation","relation_table":projects["id"]},{"id":"untyped","name":"普通文字","type":"text"}]}),"private").await;
    let first=f.create("record","黃老細",json!({"table_id":contacts["id"],"values":{"project":project["id"],"untyped":decoy["id"]}}),"private").await;
    let second = f
        .create(
            "record",
            "黃老細",
            json!({"table_id":contacts["id"],"values":{}}),
            "private",
        )
        .await;
    let result = f
        .call(
            &f.token,
            "blueprint_context",
            json!({"queries":["黃老細","黃老細"],"limit":20}),
        )
        .await;
    assert_eq!(result["queries"], json!(["黃老細"]));
    assert!(contains_id(&result["matches"], &first["id"]));
    assert!(contains_id(&result["matches"], &second["id"]));
    assert!(contains_id(&result["related"], &project["id"]));
    assert!(!contains_id(&result["related"], &decoy["id"]));
    assert_eq!(result["relations"][0]["field_id"], "project");
    assert_eq!(result["relations"][0]["source_id"], first["id"]);
    assert_eq!(result["timezone"], "Asia/Hong_Kong");
    let cited = result["matches"]
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["id"] == first["id"])
        .unwrap();
    assert_eq!(cited["version"], 1);
    assert!(cited["url"].as_str().unwrap().ends_with("/#tables"));
    let limited = f
        .call(
            &f.token,
            "blueprint_context",
            json!({"queries":["黃老細"],"limit":1}),
        )
        .await;
    assert_eq!(limited["matches"].as_array().unwrap().len(), 1);
    assert_eq!(limited["truncated"], true);
    let constrained = Fixture::make_token(
        &f.state,
        f.owner.id,
        json!(["business:read"]),
        Some(json!([first["id"], contacts["id"]])),
    )
    .await;
    let result = f
        .call(
            &constrained,
            "blueprint_context",
            json!({"source_ids":[first["id"]]}),
        )
        .await;
    assert!(result["related"].as_array().unwrap().is_empty());
    assert!(result["relations"].as_array().unwrap().is_empty());
    f.denied(
        &constrained,
        "blueprint_context",
        json!({"source_ids":[first["id"],project["id"]]}),
    )
    .await;
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn context_finds_incoming_typed_project_links_without_exposing_private_projects() {
    let f = Fixture::new().await;
    let contacts = f
        .create("table", "People", json!({"fields":[]}), "company")
        .await;
    let contact = f
        .create(
            "record",
            "黃老細",
            json!({"table_id":contacts["id"],"values":{}}),
            "company",
        )
        .await;
    let projects = f.create("table", "Projects", json!({"fields":[
        {"id":"contact","name":"裝修承辦人","type":"relation","relation_table":contacts["id"]},
        {"id":"plain_text","name":"普通文字","type":"text"}
    ]}), "company").await;
    let visible = f
        .create(
            "record",
            "Harbour renovation",
            json!({"table_id":projects["id"],"values":{"contact":contact["id"]}}),
            "company",
        )
        .await;
    let hidden = f
        .create(
            "record",
            "Private unreleased renovation",
            json!({"table_id":projects["id"],"values":{"contact":contact["id"]}}),
            "private",
        )
        .await;
    let decoy = f
        .create(
            "record",
            "UUID in text does not prove a relation",
            json!({"table_id":projects["id"],"values":{"plain_text":contact["id"]}}),
            "company",
        )
        .await;
    let member = f.member().await;
    let result = f
        .call(&member, "blueprint_context", json!({"queries":["黃老細"]}))
        .await;
    assert_eq!(result["matches"].as_array().unwrap().len(), 1);
    assert!(contains_id(&result["matches"], &contact["id"]));
    assert!(contains_id(&result["related"], &visible["id"]));
    assert!(!contains_id(&result["related"], &hidden["id"]));
    assert!(!contains_id(&result["related"], &decoy["id"]));
    assert!(!result.to_string().contains("Private unreleased"));
    assert_eq!(result["relations"].as_array().unwrap().len(), 1);
    assert_eq!(result["relations"][0]["source_id"], visible["id"]);
    assert_eq!(result["relations"][0]["target_id"], contact["id"]);
    assert_eq!(result["relations"][0]["field_id"], "contact");
    assert_eq!(result["relations"][0]["table_version"], 1);
    let limited = f
        .call(
            &member,
            "blueprint_context",
            json!({"source_ids":[contact["id"]],"limit":1}),
        )
        .await;
    assert!(limited["related"].as_array().unwrap().is_empty());
    assert_eq!(limited["truncated"], true);
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn context_rechecks_acl_modules_scopes_and_integration_dlp_and_redacts_secrets() {
    let f = Fixture::new().await;
    let member = f.member().await;
    let private = f
        .create(
            "note",
            "Wong private",
            json!({"content":"private customer history"}),
            "private",
        )
        .await;
    let public=f.create("note","Wong shared",json!({"content":"shared customer history","nested":{"api_key":"test-never-return-this","safe":"evidence"}}),"company").await;
    let blocked = f
        .create(
            "note",
            "Wong blocked",
            json!({"content":"DLP restricted"}),
            "company",
        )
        .await;
    f.create(
        "classification",
        "No integrations",
        json!({"active":true,"resource_ids":[blocked["id"]],"blocked_actions":["integration"]}),
        "private",
    )
    .await;
    let result = f
        .call(&member, "blueprint_context", json!({"queries":["Wong"]}))
        .await;
    assert!(contains_id(&result["matches"], &public["id"]));
    assert!(!contains_id(&result["matches"], &private["id"]));
    assert!(!contains_id(&result["matches"], &blocked["id"]));
    assert!(!result.to_string().contains("test-never-return-this"));
    assert!(result.to_string().contains("evidence"));
    let private_error = f
        .rpc(
            &member,
            "blueprint_context",
            json!({"source_ids":[private["id"]]}),
        )
        .await;
    let blocked_error = f
        .rpc(
            &member,
            "blueprint_context",
            json!({"source_ids":[blocked["id"]]}),
        )
        .await;
    assert_eq!(private_error["result"], blocked_error["result"]);
    let no_scope = Fixture::make_token(&f.state, f.owner.id, json!(["business:read"]), None).await;
    let result = f
        .call(&no_scope, "blueprint_context", json!({"queries":["Wong"]}))
        .await;
    assert!(result["matches"].as_array().unwrap().is_empty());
    let (status, _) = f
        .request(
            Method::PATCH,
            "/api/v1/settings",
            &f.token,
            json!({"modules":{"notes":false}}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    let result = f
        .call(&member, "blueprint_context", json!({"queries":["Wong"]}))
        .await;
    assert!(result["matches"].as_array().unwrap().is_empty());
    f.denied(
        &member,
        "blueprint_context",
        json!({"source_ids":[public["id"]]}),
    )
    .await;
    f.denied(
        &f.token,
        "blueprint_context",
        json!({"queries":["Wong"],"limit":51}),
    )
    .await;
    f.cleanup().await;
}

#[tokio::test]
#[ignore = "Requires disposable PostgreSQL via TEST_DATABASE_URL"]
async fn mcp_dispatch_uses_durable_jobs_and_current_token_isolation() {
    let f = Fixture::new().await;
    let calls = Arc::new(AtomicUsize::new(0));
    let worker_calls = calls.clone();
    let endpoint=axum::Router::new().route("/worker",axum::routing::post(move|axum::Json(input):axum::Json<Value>|{
        let calls=worker_calls.clone();async move { calls.fetch_add(1,Ordering::SeqCst);assert_eq!(input["task"],"prepare_meeting");axum::Json(json!({"output":{"brief":"Controlled worker response","source_count":input["sources"].as_array().unwrap().len()}})) }
    }));
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}/worker", listener.local_addr().unwrap());
    let worker_server = tokio::spawn(async move { axum::serve(listener, endpoint).await.unwrap() });
    let source = f
        .create(
            "note",
            "Meeting facts",
            json!({"content":"Known project facts"}),
            "private",
        )
        .await;
    let agent=f.create("agent","Controlled protocol test worker",json!({"endpoint":url,"allow_private_network":true,"enabled":true,"capabilities":["prepare_meeting"]}),"private").await;
    let token_b = Fixture::make_token(&f.state, f.owner.id, json!(["*"]), None).await;
    let args = json!({"agent_id":agent["id"],"task":"prepare_meeting","input":{"goal":"Review quote"},"source_ids":[source["id"]],"idempotency_key":"one-meeting"});
    let first = f
        .call(&f.token, "blueprint_agent_dispatch", args.clone())
        .await;
    assert_eq!(first["status"], "queued");
    assert_eq!(first["job_status"], "queued");
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    let duplicate = f
        .call(&f.token, "blueprint_agent_dispatch", args.clone())
        .await;
    assert_eq!(first["run_id"], duplicate["run_id"]);
    assert_eq!(first["job_id"], duplicate["job_id"]);
    let other = f
        .call(&token_b, "blueprint_agent_dispatch", args.clone())
        .await;
    assert_ne!(first["run_id"], other["run_id"]);
    f.denied(
        &f.token,
        "blueprint_job_status",
        json!({"job_id":other["job_id"]}),
    )
    .await;
    let queued = f
        .call(
            &f.token,
            "blueprint_job_status",
            json!({"job_id":first["job_id"]}),
        )
        .await;
    assert_eq!(queued["status"], "queued");
    let mut changed = args.clone();
    changed["input"] = json!({"goal":"Different request"});
    f.denied(&f.token, "blueprint_agent_dispatch", changed)
        .await;
    let mut running = tokio::process::Command::new(env!("CARGO_BIN_EXE_blueprint-backend"))
        .arg("worker")
        .env("BLUEPRINT_DATABASE_URL", &f.state.config.database_url)
        .env("BLUEPRINT_DATA_DIR", &f.state.config.data_dir)
        .env(
            "BLUEPRINT_MASTER_KEY",
            hex::encode(f.state.config.secret_key),
        )
        .env("BLUEPRINT_PUBLIC_URL", &f.base)
        .env("BLUEPRINT_UI_DIR", &f.state.config.ui_dir)
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut completed = false;
    for _ in 0..100 {
        let result = f
            .call(
                &f.token,
                "blueprint_job_status",
                json!({"job_id":first["job_id"]}),
            )
            .await;
        if result["status"] == "completed" {
            completed = true;
            break;
        }
        assert_ne!(result["status"], "failed", "{result}");
        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    }
    assert!(completed, "Actual worker did not finish queued job");
    let result = f
        .call(
            &f.token,
            "blueprint_run_status",
            json!({"run_id":first["run_id"]}),
        )
        .await;
    assert_eq!(result["data"]["status"], "completed");
    assert!(result.to_string().contains("Controlled worker response"));
    running.kill().await.unwrap();
    worker_server.abort();
    let (status, _) = f
        .request(
            Method::PATCH,
            "/api/v1/settings",
            &f.token,
            json!({"modules":{"workflows":false}}),
        )
        .await;
    assert_eq!(status, StatusCode::OK);
    let mut disabled = args;
    disabled["idempotency_key"] = json!("disabled-module");
    f.denied(&f.token, "blueprint_agent_dispatch", disabled)
        .await;
    f.cleanup().await;
}
