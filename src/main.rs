use blueprint_backend::*;
use chrono::Utc;
use clap::{Parser, Subcommand};
use serde_json::json;
use std::path::{Path, PathBuf};
use uuid::Uuid;

#[derive(Parser)]
#[command(version, about = "Blueprint Studio self-hosted company workspace")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
}
#[derive(Subcommand)]
enum Command {
    Server,
    Worker,
    Init {
        #[arg(long)]
        company: String,
        #[arg(long)]
        email: String,
        #[arg(long, default_value = "Administrator")]
        name: String,
        #[arg(long)]
        password_file: Option<PathBuf>,
    },
    Doctor,
    Backup {
        path: PathBuf,
    },
    Restore {
        path: PathBuf,
    },
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "blueprint_backend=info,tower_http=warn".into()),
        )
        .init();
    let cli = Cli::parse();
    let config = Config::from_env()?;
    let state = AppState::connect(config).await?;
    match cli.command.unwrap_or(Command::Server) {
        Command::Server => {
            let listener = tokio::net::TcpListener::bind(&state.config.bind).await?;
            tracing::info!(bind=%state.config.bind,"Blueprint server ready");
            axum::serve(listener, app(state))
                .with_graceful_shutdown(shutdown())
                .await?;
        }
        Command::Worker => worker(state).await?,
        Command::Init {
            company,
            email,
            name,
            password_file,
        } => {
            let password = if let Some(path) = password_file {
                std::fs::read_to_string(path)?.trim_end().to_string()
            } else {
                std::env::var("BLUEPRINT_ADMIN_PASSWORD").map_err(|_|ApiError::bad_request("Supply --password-file or BLUEPRINT_ADMIN_PASSWORD; passwords are never accepted on the command line"))?
            };
            let actor = auth::initialize(&state, &company, &email, &name, &password).await?;
            println!("Installation initialized for {}", actor.email);
        }
        Command::Doctor => {
            let mut services = serde_json::Map::new();
            let mut failed = false;
            for (name, url) in &state.config.services {
                if name == "clamav" {
                    let addr = url.strip_prefix("tcp://").unwrap_or(url);
                    let ready = tokio::time::timeout(
                        std::time::Duration::from_secs(3),
                        tokio::net::TcpStream::connect(addr),
                    )
                    .await
                    .is_ok_and(|r| r.is_ok());
                    if !ready {
                        failed = true;
                    }
                    services.insert(name.clone(), json!({"configured":true,"reachable":ready}));
                } else if name != "smtp" {
                    let path = match name.as_str() {
                        "gotenberg" => "/health",
                        "tika" => "/version",
                        "collabora" => "/hosting/discovery",
                        _ => "",
                    };
                    let ready = state
                        .http
                        .get(format!("{}{path}", url.trim_end_matches('/')))
                        .send()
                        .await
                        .is_ok_and(|r| r.status().is_success() || r.status().as_u16() == 401);
                    if !ready {
                        failed = true;
                    }
                    services.insert(name.clone(), json!({"configured":true,"reachable":ready}));
                }
            }
            let pgroonga: bool = sqlx::query_scalar(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgroonga')",
            )
            .fetch_one(&state.pool)
            .await?;
            println!(
                "{}",
                serde_json::to_string_pretty(
                    &json!({"database":"ready","search":if pgroonga{"pgroonga"}else{"postgresql-fallback"},"services":services,"version":env!("CARGO_PKG_VERSION")})
                )?
            );
            if failed {
                return Err("One or more configured services are unavailable".into());
            }
        }
        Command::Backup { path } => backup(&state, &path).await?,
        Command::Restore { path } => restore(&state, &path).await?,
    }
    Ok(())
}
async fn shutdown() {
    let _ = tokio::signal::ctrl_c().await;
}

async fn worker(state: AppState) -> ApiResult<()> {
    let worker_id = Uuid::new_v4();
    let concurrency = std::env::var("BLUEPRINT_WORKER_CONCURRENCY")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(20)
        .clamp(1, 100);
    let mut tasks = tokio::task::JoinSet::new();
    tracing::info!(%worker_id,concurrency,"Background worker ready");
    let mut last_schedule = std::time::Instant::now() - std::time::Duration::from_secs(60);
    loop {
        if last_schedule.elapsed() >= std::time::Duration::from_secs(30) {
            if let Err(error) = content::cleanup_expired_uploads(&state).await {
                tracing::warn!(error=%error,"Unable to clean expired upload staging");
            }
            if let Err(e) = business::schedule_due_jobs(&state).await {
                tracing::warn!(error=%e,"Unable to enqueue calendar sync");
            }
            last_schedule = std::time::Instant::now();
        }
        // Recover only explicitly safe work after a lost lease. Non-idempotent external writes need review.
        sqlx::query("UPDATE jobs SET status='failed',error='Worker lease expired during a non-idempotent operation; inspect the provider before retrying',lease_owner=NULL,lease_until=NULL,updated_at=now() WHERE status='running' AND lease_until<now() AND kind NOT LIKE 'content.%' AND kind NOT LIKE 'workflow.%' AND COALESCE((payload->>'retry_safe')::boolean,false)=false").execute(&state.pool).await?;
        while tasks.len() < concurrency {
            let row=sqlx::query_as::<_,Job>("UPDATE jobs SET status='running',attempts=attempts+1,lease_owner=$1,lease_until=now()+interval '60 seconds',updated_at=now() WHERE id=(SELECT id FROM jobs WHERE (status='queued' AND available_at<=now()) OR (status='running' AND lease_until<now() AND (kind LIKE 'content.%' OR kind LIKE 'workflow.%' OR payload->>'retry_safe'='true')) ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id,owner_id,kind,status,payload,result,error,attempts,created_at,updated_at")
                .bind(worker_id).fetch_optional(&state.pool).await?;
            let Some(job) = row else {
                break;
            };
            let cloned = state.clone();
            tasks.spawn(async move { run_job(cloned, worker_id, job).await });
        }
        tokio::select! {_ = tokio::signal::ctrl_c()=>{break;},Some(result)=tasks.join_next(),if !tasks.is_empty()=>{if let Err(e)=result{tracing::error!(error=%e,"Job task crashed");}},_=tokio::time::sleep(std::time::Duration::from_millis(500))=>{}}
    }
    while let Some(result) = tasks.join_next().await {
        if let Err(e) = result {
            tracing::error!(error=%e,"Job task crashed during shutdown");
        }
    }
    Ok(())
}
async fn run_job(state: AppState, worker_id: Uuid, job: Job) {
    let pool = state.pool.clone();
    let id = job.id;
    let heartbeat = tokio::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(15)).await;
            let _=sqlx::query("UPDATE jobs SET lease_until=now()+interval '60 seconds' WHERE id=$1 AND lease_owner=$2 AND status IN('running','cancel_requested')").bind(id).bind(worker_id).execute(&pool).await;
        }
    });
    let result = if job.kind.starts_with("content.") {
        content::process_job(&state, &job).await
    } else if job.kind.starts_with("business.") {
        business::process_job(&state, &job).await
    } else if job.kind.starts_with("workflow.") {
        workflows::process_job(&state, &job).await
    } else {
        Err(ApiError::bad_request("Unknown background job kind"))
    };
    heartbeat.abort();
    let settled = match result {
        Ok(result) => {
            let status = if result["status"] == "cancelled" {
                "cancelled"
            } else {
                "completed"
            };
            sqlx::query("UPDATE jobs SET status=$3,result=$4,error=NULL,lease_until=NULL,lease_owner=NULL,updated_at=now() WHERE id=$1 AND lease_owner=$2").bind(job.id).bind(worker_id).bind(status).bind(result).execute(&state.pool).await
        }
        Err(error) => {
            let safe = job.kind.starts_with("content.") || job.payload["retry_safe"] == true;
            let retry = safe && error.status.as_u16() >= 500 && job.attempts < 3;
            let status = if retry { "queued" } else { "failed" };
            sqlx::query("UPDATE jobs SET status=$3,error=$4,available_at=now()+make_interval(secs=>$5),lease_until=NULL,lease_owner=NULL,updated_at=now() WHERE id=$1 AND lease_owner=$2").bind(job.id).bind(worker_id).bind(status).bind(error.message).bind((2i32.pow(job.attempts.min(8) as u32)*5) as f64).execute(&state.pool).await
        }
    };
    if let Err(e) = settled {
        tracing::error!(job_id=%job.id,error=%e,"Unable to settle job; lease recovery will apply");
    }
}

fn pg_command(program: &str, state: &AppState) -> ApiResult<tokio::process::Command> {
    pg_connection_command(program, &state.config.database_url)
}
fn pg_component(value: &str) -> ApiResult<String> {
    percent_encoding::percent_decode_str(value)
        .decode_utf8()
        .map(|value| value.into_owned())
        .map_err(|_| ApiError::bad_request("Database URL contains an invalid UTF-8 component"))
}
fn pg_database_name(database_url: &str) -> ApiResult<String> {
    let parsed =
        url::Url::parse(database_url).map_err(|_| ApiError::bad_request("Invalid database URL"))?;
    pg_component(parsed.path().trim_start_matches('/'))
}
fn pg_connection_command(program: &str, database_url: &str) -> ApiResult<tokio::process::Command> {
    let parsed =
        url::Url::parse(database_url).map_err(|_| ApiError::bad_request("Invalid database URL"))?;
    let mut cmd = tokio::process::Command::new(program);
    cmd.env("PGHOST", parsed.host_str().unwrap_or("localhost"))
        .env("PGPORT", parsed.port().unwrap_or(5432).to_string())
        .env("PGUSER", pg_component(parsed.username())?)
        .env("PGDATABASE", pg_database_name(database_url)?);
    if let Some(password) = parsed.password() {
        cmd.env("PGPASSWORD", pg_component(password)?);
    }
    for (key, value) in parsed.query_pairs() {
        match key.as_ref() {
            "sslmode" => {
                cmd.env("PGSSLMODE", value.as_ref());
            }
            "sslrootcert" => {
                cmd.env("PGSSLROOTCERT", value.as_ref());
            }
            _ => {}
        }
    }
    Ok(cmd)
}
fn secure_dir(path: &Path) -> ApiResult<()> {
    std::fs::create_dir(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700))?;
    }
    Ok(())
}
fn copy_tree(source: &Path, dest: &Path) -> ApiResult<()> {
    std::fs::create_dir_all(dest)?;
    for entry in std::fs::read_dir(source)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        if ty.is_symlink() {
            return Err(ApiError::bad_request(
                "Backup data directory must not contain symlinks",
            ));
        }
        let output = dest.join(entry.file_name());
        if ty.is_dir() {
            copy_tree(&entry.path(), &output)?;
        } else {
            std::fs::copy(entry.path(), output)?;
        }
    }
    Ok(())
}
async fn backup(state: &AppState, path: &Path) -> ApiResult<()> {
    if std::env::var("BLUEPRINT_S3_ENDPOINT").is_ok_and(|value| !value.trim().is_empty()) {
        return Err(ApiError::bad_request(
            "External S3 requires a version-preserving object-store backup in addition to the database and keys; follow docs/V2-OPERATIONS.md instead of producing a local-only backup",
        ));
    }
    if path.exists() {
        return Err(ApiError::conflict("Backup path already exists"));
    }
    let source = std::fs::canonicalize(&state.config.data_dir)?;
    let parent = path.parent().unwrap_or(Path::new("."));
    let parent = std::fs::canonicalize(parent)?;
    if parent.starts_with(&source) {
        return Err(ApiError::bad_request(
            "Backup must be stored outside the live data directory",
        ));
    }
    secure_dir(path)?;
    let result = pg_command("pg_dump", state)?
        .args(["--format=custom", "--no-owner", "--no-acl", "--file"])
        .arg(path.join("database.dump"))
        .output()
        .await?;
    if !result.status.success() {
        return Err(ApiError::internal(
            "pg_dump failed; no complete backup was produced",
        ));
    }
    copy_tree(&state.config.data_dir, &path.join("data"))?;
    std::fs::write(path.join("data/master.key"), state.config.secret_key)?;
    use sha2::{Digest, Sha256};
    let hash = hex::encode(Sha256::digest(std::fs::read(path.join("database.dump"))?));
    std::fs::write(
        path.join("manifest.json"),
        serde_json::to_vec_pretty(
            &json!({"schema":"blueprint-backup-v2","version":env!("CARGO_PKG_VERSION"),"created_at":Utc::now(),"database_sha256":hash,"storage":"local","requires_stopped_writers":true}),
        )?,
    )?;
    println!(
        "Local backup created. Keep its master key private; verify a restore before relying on it."
    );
    Ok(())
}
async fn restore(state: &AppState, path: &Path) -> ApiResult<()> {
    let count: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM users")
        .fetch_one(&state.pool)
        .await?;
    if count > 0 {
        return Err(ApiError::conflict(
            "Restore is allowed only into a fresh empty installation",
        ));
    }
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(path.join("manifest.json"))?)?;
    if manifest["schema"] != "blueprint-backup-v2" || manifest["storage"] != "local" {
        return Err(ApiError::bad_request("Unsupported backup format"));
    }
    use sha2::{Digest, Sha256};
    let hash = hex::encode(Sha256::digest(std::fs::read(path.join("database.dump"))?));
    if manifest["database_sha256"] != hash {
        return Err(ApiError::bad_request("Backup checksum mismatch"));
    }
    let result = pg_command("pg_restore", state)?
        .args([
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-acl",
            "--dbname",
        ])
        .arg(pg_database_name(&state.config.database_url)?)
        .arg(path.join("database.dump"))
        .output()
        .await?;
    if !result.status.success() {
        return Err(ApiError::internal(
            "Database restore failed; inspect the fresh installation before starting it",
        ));
    }
    copy_tree(&path.join("data"), &state.config.data_dir)?;
    println!(
        "Restored local database and files. Configure BLUEPRINT_MASTER_KEY to match the restored key or remove that environment override before restart."
    );
    Ok(())
}

#[cfg(test)]
mod pg_url_tests {
    use super::*;
    #[test]
    fn backup_client_decodes_url_credentials_and_database_without_form_plus_rules() {
        let command=pg_connection_command("pg_dump","postgresql://user%40example:p%40ss%2Bword%3A%25@localhost:5433/company%20records+2026?sslmode=require").unwrap();
        let variables: std::collections::HashMap<_, _> = command
            .as_std()
            .get_envs()
            .map(|(key, value)| {
                (
                    key.to_string_lossy().into_owned(),
                    value.unwrap().to_string_lossy().into_owned(),
                )
            })
            .collect();
        assert_eq!(variables["PGUSER"], "user@example");
        assert_eq!(variables["PGPASSWORD"], "p@ss+word:%");
        assert_eq!(variables["PGDATABASE"], "company records+2026");
        assert_eq!(variables["PGPORT"], "5433");
        assert_eq!(variables["PGSSLMODE"], "require");
    }
}
