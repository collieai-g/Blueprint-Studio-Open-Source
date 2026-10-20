//! Job-local permissions for processing bytes a writer has just uploaded. The
//! signed grant never changes the account/PAT and cannot authorize an HTTP read.
use super::*;
use hmac::{Hmac, Mac};
fn proof(state: &AppState, data: &Value) -> ApiResult<String> {
    let mut mac = Hmac::<Sha256>::new_from_slice(&state.config.secret_key).map_err(err)?;
    mac.update(&serde_json::to_vec(data).map_err(err)?);
    Ok(hex::encode(mac.finalize().into_bytes()))
}
pub(super) fn grant(
    state: &AppState,
    actor: &Actor,
    r: &Resource,
    number: i64,
) -> ApiResult<Value> {
    let v = version_metadata(r, number)?;
    let data = json!({"file_id":r.id,"owner_id":r.owner_id,"parent_id":r.parent_id,"number":number,"sha256":v["sha256"],"token_id":actor.token_id});
    Ok(json!({"data":data,"proof":proof(state,&data)?}))
}
/// Permit parent metadata checks within a write operation. This local actor is
/// never returned or stored as the token's constraints.
pub(super) fn writer_metadata_actor(actor: &Actor) -> Actor {
    let mut local = actor.clone();
    if let Some(scopes) = local.scopes.as_mut() {
        if !scopes
            .iter()
            .any(|s| matches!(s.as_str(), "*" | "content:*" | "content:read"))
        {
            scopes.push("content:read".into());
        }
    }
    local
}
pub(super) async fn actor_and_file(
    state: &AppState,
    job: &Job,
    id: Uuid,
) -> ApiResult<(Actor, Resource)> {
    let actor = auth::actor_for_job(state, job).await?;
    actor.require_scope("content:write")?;
    if let Ok(r) = file(state, &actor, id).await {
        if !actor.can_write(&r) {
            return Err(ApiError::forbidden(
                "File processing requires current write permission",
            ));
        }
        repo::check_ancestors(&state.pool, &actor, &r, true).await?;
        return Ok((actor, r));
    }
    let grant = job
        .payload
        .get("internal_processing_grant")
        .ok_or_else(|| ApiError::forbidden("This job has no internal upload grant"))?;
    let data = grant
        .get("data")
        .ok_or_else(|| ApiError::forbidden("Invalid upload grant"))?;
    let signature = grant
        .get("proof")
        .and_then(Value::as_str)
        .and_then(|s| hex::decode(s).ok())
        .ok_or_else(|| ApiError::forbidden("Invalid upload grant"))?;
    let mut mac = Hmac::<Sha256>::new_from_slice(&state.config.secret_key).map_err(err)?;
    mac.update(&serde_json::to_vec(data).map_err(err)?);
    mac.verify_slice(&signature)
        .map_err(|_| ApiError::forbidden("Invalid upload grant signature"))?;
    let r = repo::raw_get(&state.pool, id).await?;
    let number = job.payload["number"]
        .as_i64()
        .ok_or_else(|| ApiError::bad_request("Processing version missing"))?;
    let v = version_metadata(&r, number)?;
    if r.kind != "file"
        || r.deleted_at.is_some()
        || r.owner_id != actor.id
        || job.owner_id != actor.id
        || data["file_id"] != json!(id)
        || data["owner_id"] != json!(actor.id)
        || data["parent_id"] != json!(r.parent_id)
        || data["token_id"] != json!(actor.token_id)
        || data["number"] != json!(number)
        || data["sha256"] != v["sha256"]
    {
        return Err(ApiError::forbidden(
            "Upload grant no longer matches the current owner, parent or immutable version",
        ));
    }
    if actor
        .resource_ids
        .as_ref()
        .is_some_and(|ids| !ids.contains(&id) && !r.parent_id.is_some_and(|p| ids.contains(&p)))
    {
        return Err(ApiError::forbidden(
            "Upload parent is outside the token's resource scope",
        ));
    }
    let mut local = writer_metadata_actor(&actor);
    writable_parent(state, &local, r.parent_id).await?;
    if let Some(ids) = local.resource_ids.as_mut() {
        if !ids.contains(&id) {
            ids.push(id);
        }
    }
    repo::check_ancestors(&state.pool, &local, &r, true).await?;
    if !local.can_write(&r) {
        return Err(ApiError::forbidden("Upload is no longer writable"));
    }
    Ok((local, r))
}
/// Merge scan results after slow provider requests rather than overwriting
/// concurrent locks, newer uploads or governance edits.
pub(super) async fn save_results(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    number: i64,
    data: &Value,
    scan: &Value,
) -> ApiResult<Resource> {
    for _ in 0..5 {
        let current = file(state, actor, id).await?;
        let mut merged = current.data.clone();
        let versions = merged
            .get_mut("versions")
            .and_then(Value::as_array_mut)
            .ok_or_else(|| ApiError::internal("Missing immutable versions"))?;
        let version = versions
            .iter_mut()
            .find(|v| v["number"].as_i64() == Some(number))
            .ok_or_else(|| ApiError::not_found("Processing version was removed"))?;
        version["scan"] = scan.clone();
        if current.data["current_version"].as_i64() == Some(number) {
            for key in [
                "scan",
                "extracted_text",
                "text_extraction",
                "preview",
                "classification",
                "classification_matches",
                "processing",
            ] {
                if let Some(value) = data.get(key) {
                    merged[key] = value.clone();
                }
            }
            let mut inherited = current
                .data
                .get("inherited_classification_policies")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if let Some(ids) = data
                .get("inherited_classification_policies")
                .and_then(Value::as_array)
            {
                for id in ids {
                    if !inherited.contains(id) {
                        inherited.push(id.clone());
                    }
                }
            }
            merged["inherited_classification_policies"] = json!(inherited);
        }
        let (retain, hold) = policy::effective_governance(state, &current).await?;
        merged["effective_governance"] = json!({"retain_until":retain,"legal_hold":hold});
        if let Some(value) = data.get("governance_enforcement") {
            merged["governance_enforcement"] = value.clone();
        }
        match repo::update(
            &state.pool,
            actor,
            id,
            UpdateResource {
                name: None,
                parent_id: None,
                visibility: None,
                acl: None,
                data: Some(merged),
                expected_version: current.version,
            },
        )
        .await
        {
            Ok(r) => return Ok(r),
            Err(e) if e.status == axum::http::StatusCode::CONFLICT => continue,
            Err(e) => return Err(e),
        }
    }
    Err(ApiError::unavailable(
        "Concurrent file changes prevented saving processing results; retry the job",
    ))
}
pub(super) async fn malware_alert(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    number: i64,
    scan: &Value,
) -> ApiResult<()> {
    let mut tx = state.pool.begin().await.map_err(err)?;
    let data = json!({"type":"malware","resource_id":id,"file_version":number,"scan":scan,"status":"open","severity":"high"});
    let exists:bool=sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM resources WHERE kind='alert' AND data->>'type'='malware' AND data->>'resource_id'=$1 AND data->>'file_version'=$2)").bind(id.to_string()).bind(number.to_string()).fetch_one(&mut *tx).await.map_err(err)?;
    if !exists {
        let r=sqlx::query_as::<_,Resource>("INSERT INTO resources(id,kind,name,owner_id,visibility,acl,data,search_document) VALUES($1,'alert','Malicious content quarantined',$2,'private','{}',$3,'Malicious content quarantined') RETURNING *").bind(Uuid::new_v4()).bind(actor.id).bind(data).fetch_one(&mut *tx).await.map_err(err)?;
        sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,1,$2,$3,'security_detection')").bind(r.id).bind(actor.id).bind(serde_json::to_value(&r).map_err(err)?).execute(&mut *tx).await.map_err(err)?;
        sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'security.malware',$2,$3)").bind(actor.id).bind(id).bind(json!({"alert_id":r.id,"file_version":number})).execute(&mut *tx).await.map_err(err)?;
    }
    tx.commit().await.map_err(err)?;
    Ok(())
}
