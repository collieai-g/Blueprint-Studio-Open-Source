//! Deferred immutable-object collection after permanent metadata deletion.
use super::*;
use sqlx::Row;
use std::collections::BTreeSet;
const GRACE_HOURS: i64 = 24;
pub(super) fn router() -> Router<AppState> {
    Router::new().route("/content/gc", get(status).post(start))
}
fn candidates(data: &Value, result: &mut BTreeSet<(String, String)>) {
    match data {
        Value::Object(map) => {
            if let Some(key) = map
                .get("storage_key")
                .and_then(Value::as_str)
                .filter(|k| storage::valid_key(k))
            {
                let version = map
                    .get("s3_version_id")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                result.insert((key.to_owned(), version.to_owned()));
            }
            for value in map.values() {
                candidates(value, result);
            }
        }
        Value::Array(values) => {
            for value in values {
                candidates(value, result)
            }
        }
        _ => {}
    }
}
pub(super) async fn prepare(state: &AppState, actor: &Actor, r: &Resource) -> ApiResult<()> {
    if r.kind != "file" {
        return Ok(());
    }
    if !actor.can_manage(r) {
        return Err(ApiError::forbidden(
            "Cannot schedule cleanup of another account's content",
        ));
    }
    authorize_action(state, actor, r, "purge").await?;
    let mut objects = BTreeSet::new();
    candidates(&r.data, &mut objects);
    let history = sqlx::query_scalar::<_, Value>(
        "SELECT snapshot->'data' FROM resource_history WHERE resource_id=$1",
    )
    .bind(r.id)
    .fetch_all(&state.pool)
    .await
    .map_err(err)?;
    for data in history {
        candidates(&data, &mut objects);
    }
    // Top-level compatibility metadata repeats the current storage key without
    // version ID. Prefer every precise immutable version receipt when present.
    let precise = objects
        .iter()
        .filter(|(_, v)| !v.is_empty())
        .map(|(k, _)| k.clone())
        .collect::<BTreeSet<_>>();
    objects.retain(|(key, version)| !version.is_empty() || !precise.contains(key));
    let (mode, location) = storage::identity(state)?;
    let mut tx = state.pool.begin().await.map_err(err)?;
    for (key, mut version) in objects {
        if mode == "local" {
            version.clear();
        }
        sqlx::query("INSERT INTO blob_gc(id,storage_mode,storage_location,object_key,version_id,source_id,owner_id,not_before) VALUES($1,$2,$3,$4,$5,$6,$7,now()+interval '24 hours') ON CONFLICT(storage_mode,storage_location,object_key,version_id) DO UPDATE SET not_before=GREATEST(blob_gc.not_before,EXCLUDED.not_before),status='pending',last_error=NULL,updated_at=now()")
            .bind(Uuid::new_v4()).bind(&mode).bind(&location).bind(key).bind(version).bind(r.id).bind(actor.id).execute(&mut *tx).await.map_err(err)?;
    }
    tx.commit().await.map_err(err)?;
    // Schedule the first collection without relying on a user returning to the UI.
    repo::enqueue_at(
        &state.pool,
        actor,
        "content.gc",
        json!({"source_id":r.id,"actor_id":actor.id}),
        Some(&format!("gc:{}", r.id)),
        Utc::now() + Duration::hours(GRACE_HOURS),
    )
    .await?;
    Ok(())
}
async fn admin(state: &AppState, actor: &Actor) -> ApiResult<()> {
    crate::core::module_enabled(state, "files").await?;
    actor.require_scope("admin:write")?;
    if !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Only administrators can collect unreferenced content",
        ));
    }
    Ok(())
}
async fn start(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Job>> {
    admin(&state, &actor).await?;
    Ok(Json(
        repo::enqueue(
            &state.pool,
            &actor,
            "content.gc",
            json!({"actor_id":actor.id}),
            None,
        )
        .await?,
    ))
}
async fn status(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Value>> {
    admin(&state, &actor).await?;
    let rows = sqlx::query(
        "SELECT status,COUNT(*) count,MIN(not_before) next_attempt FROM blob_gc GROUP BY status",
    )
    .fetch_all(&state.pool)
    .await
    .map_err(err)?;
    Ok(Json(
        json!({"grace_hours":GRACE_HOURS,"states":rows.into_iter().map(|r|json!({"status":r.get::<String,_>("status"),"count":r.get::<i64,_>("count"),"next_attempt":r.get::<Option<DateTime<Utc>>,_>("next_attempt")})).collect::<Vec<_>>()}),
    ))
}
pub(super) async fn process(state: &AppState, job: &Job) -> ApiResult<Value> {
    let actor = auth::actor_for_job(state, job).await?;
    // A normal file owner may initiate its own eventual purge. The collector
    // itself accepts only server-created tombstones and never a payload key.
    if !actor.is_admin() {
        actor.require_scope("content:write")?;
    }
    let (mode, location) = storage::identity(state)?;
    let mut removed = 0u64;
    let mut referenced = 0u64;
    let mut blocked = 0u64;
    for _ in 0..100 {
        let mut tx = state.pool.begin().await.map_err(err)?;
        let row=sqlx::query("SELECT id,object_key,version_id FROM blob_gc WHERE storage_mode=$1 AND storage_location=$2 AND status<>'removed' AND not_before<=now() ORDER BY not_before FOR UPDATE SKIP LOCKED LIMIT 1")
            .bind(&mode).bind(&location).fetch_optional(&mut *tx).await.map_err(err)?;
        let Some(row) = row else {
            break;
        };
        let id: Uuid = row.get("id");
        let key: String = row.get("object_key");
        let version: String = row.get("version_id");
        sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,871193))")
            .bind(&key)
            .execute(&mut *tx)
            .await
            .map_err(err)?;
        let pinned:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM blob_upload_pins WHERE storage_mode=$1 AND storage_location=$2 AND object_key=$3 AND expires_at>now())").bind(&mode).bind(&location).bind(&key).fetch_one(&mut *tx).await.map_err(err)?;
        let in_use:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM resources WHERE kind='file' AND jsonb_path_exists(data,'$.**.storage_key ? (@ == $key)'::jsonpath,jsonb_build_object('key',$1::text)) UNION ALL SELECT 1 FROM resource_history WHERE snapshot->>'kind'='file' AND jsonb_path_exists(snapshot->'data','$.**.storage_key ? (@ == $key)'::jsonpath,jsonb_build_object('key',$1::text)))").bind(&key).fetch_one(&mut *tx).await.map_err(err)?;
        let (status, error) = if pinned || in_use {
            referenced += 1;
            (
                "referenced",
                Some("Object remains referenced by content/history or an active upload".to_owned()),
            )
        } else {
            match storage::delete_blob(
                state,
                &key,
                if version.is_empty() {
                    None
                } else {
                    Some(version.as_str())
                },
            )
            .await
            {
                Ok(()) => {
                    removed += 1;
                    ("removed", None)
                }
                Err(e) => {
                    blocked += 1;
                    ("blocked", Some(e.message))
                }
            }
        };
        sqlx::query("UPDATE blob_gc SET status=$2,last_error=$3,attempts=attempts+1,not_before=now()+interval '24 hours',updated_at=now() WHERE id=$1").bind(id).bind(status).bind(error).execute(&mut *tx).await.map_err(err)?;
        tx.commit().await.map_err(err)?;
        if status == "removed" {
            repo::audit(
                &state.pool,
                &actor,
                "content.blob_removed",
                None,
                json!({"gc_id":id,"storage":mode,"sha256":key,"version_id":version}),
            )
            .await?;
        }
    }
    sqlx::query("DELETE FROM blob_upload_pins WHERE expires_at<=now()")
        .execute(&state.pool)
        .await
        .map_err(err)?;
    let pending:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM blob_gc WHERE storage_mode=$1 AND storage_location=$2 AND status<>'removed')").bind(&mode).bind(&location).fetch_one(&state.pool).await.map_err(err)?;
    if pending {
        repo::enqueue_at(
            &state.pool,
            &actor,
            "content.gc",
            json!({"actor_id":actor.id}),
            Some(&format!(
                "gc-maintenance:{}",
                (Utc::now() + Duration::hours(24)).date_naive()
            )),
            Utc::now() + Duration::hours(24),
        )
        .await?;
    }
    Ok(
        json!({"removed":removed,"referenced":referenced,"blocked":blocked,"grace_hours":GRACE_HOURS}),
    )
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_content_addressed_candidates() {
        let key = "a".repeat(64);
        let mut values = BTreeSet::new();
        candidates(
            &json!({"versions":[{"storage_key":key,"s3_version_id":"v1"}],"preview":{"storage_key":"../escape"}}),
            &mut values,
        );
        assert_eq!(values.len(), 1);
        assert!(values.contains(&(key, "v1".into())));
    }
}
