use super::*;
pub fn router() -> Router<AppState> {
    Router::new().route("/files/{id}/move", post(move_resource))
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Move {
    parent_id: Option<Uuid>,
    expected_version: i64,
}
async fn move_resource(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<Move>,
) -> ApiResult<Json<Resource>> {
    crate::core::module_enabled(&state, "files").await?;
    let r = repo::get(&state.pool, &actor, id).await?;
    if !matches!(r.kind.as_str(), "file" | "folder" | "link") {
        return Err(ApiError::bad_request(
            "Only files, folders and links can be moved",
        ));
    }
    if !actor.can_manage(&r) {
        return Err(ApiError::forbidden("Only a manager can move this content"));
    }
    authorize_action(&state, &actor, &r, "update").await?;
    let destination = writable_parent(&state, &actor, input.parent_id).await?;
    repo::check_ancestors(&state.pool, &actor, &r, true).await?;
    let mut parent = input.parent_id;
    let mut depth = 0;
    while let Some(pid) = parent {
        if pid == id || depth > 100 {
            return Err(ApiError::conflict("Cannot move a folder inside itself"));
        }
        parent = repo::get(&state.pool, &actor, pid).await?.parent_id;
        depth += 1;
    }
    let (retain, hold) = policy::effective_governance(&state, &r).await?;
    let mut data = r.data.clone();
    if let Some(date) = retain {
        data["retain_until"] = json!(date);
    }
    if hold {
        data["legal_hold"] = json!(true);
    }
    data["moved_from"] = json!(r.parent_id);
    data["inherited_classification_policies"] =
        json!(policy::inherited_classifications(&state, &r).await?);
    let mut tx = state.pool.begin().await.map_err(err)?;
    let moved=sqlx::query_as::<_,Resource>("UPDATE resources SET parent_id=$2,data=$3,search_document=$4,visibility=$6,acl=$7,version=version+1,updated_at=now() WHERE id=$1 AND version=$5 AND deleted_at IS NULL RETURNING *").bind(id).bind(input.parent_id).bind(&data).bind(format!("{} {}",r.name,data)).bind(input.expected_version).bind(destination.as_ref().map(|p|p.visibility.as_str()).unwrap_or("private")).bind(destination.as_ref().map(|p|p.acl.clone()).unwrap_or_else(||json!({}))).fetch_optional(&mut *tx).await.map_err(err)?.ok_or_else(||ApiError::conflict("Resource changed; reload before moving"))?;
    sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,$2,$3,$4,'move')").bind(id).bind(moved.version).bind(actor.id).bind(serde_json::to_value(&moved).map_err(err)?).execute(&mut *tx).await.map_err(err)?;
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'content.move',$2,$3)").bind(actor.id).bind(id).bind(json!({"from":r.parent_id,"to":input.parent_id,"version":moved.version})).execute(&mut *tx).await.map_err(err)?;
    tx.commit().await.map_err(err)?;
    after_write(&state, &actor, &moved).await?;
    if let Err(error) = crate::workflows::emit_event(&state, "file.moved", &moved).await {
        tracing::warn!(error=%error,"Move event scheduling failed");
    }
    Ok(Json(moved))
}
