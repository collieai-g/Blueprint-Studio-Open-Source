//! Atomic, bounded copies of content trees. Blob bytes remain immutable and
//! shared; governance on each object version combines every logical reference.
use super::*;
use std::collections::HashMap;
const MAX_TREE: usize = 2000;
pub(super) async fn copy(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    name: Option<String>,
    parent_id: Option<Uuid>,
) -> ApiResult<Resource> {
    let source = repo::get(&state.pool, actor, id).await?;
    if source.deleted_at.is_some() {
        return Err(ApiError::not_found("Content is in trash"));
    }
    if !matches!(source.kind.as_str(), "file" | "folder" | "link") {
        return Err(ApiError::bad_request(
            "Only files, folders and links can be copied",
        ));
    }
    let parent = writable_parent(state, actor, parent_id).await?;
    if let Some(p) = &parent {
        if policy::ancestors(state, p).await?.contains(&id) {
            return Err(ApiError::conflict(
                "Cannot copy a folder into its own subtree",
            ));
        }
    }
    let rows=sqlx::query_as::<_,Resource>("WITH RECURSIVE tree AS (SELECT id,ARRAY[id] path,0 depth FROM resources WHERE id=$1 AND deleted_at IS NULL UNION ALL SELECT r.id,t.path||r.id,t.depth+1 FROM resources r JOIN tree t ON r.parent_id=t.id WHERE r.deleted_at IS NULL AND r.kind IN ('file','folder','link') AND NOT r.id=ANY(t.path) AND t.depth<64) SELECT r.* FROM resources r JOIN tree t ON r.id=t.id ORDER BY t.depth,r.id LIMIT 2001")
        .bind(id).fetch_all(&state.pool).await.map_err(err)?;
    if rows.len() > MAX_TREE {
        return Err(ApiError::bad_request(
            "A single tree copy is limited to 2000 items; copy smaller subfolders",
        ));
    }
    let root_name = clean_name(name.as_deref().unwrap_or(&source.name))?;
    let visibility = parent
        .as_ref()
        .map(|p| p.visibility.clone())
        .unwrap_or("private".into());
    let acl = parent
        .as_ref()
        .map(|p| p.acl.clone())
        .unwrap_or_else(|| json!({}));
    let mut prepared = Vec::new();
    let mut ids = HashMap::new();
    for original in rows {
        let r = repo::get(&state.pool, actor, original.id).await?;
        authorize_action(state, actor, &r, "copy").await?;
        let new_id = Uuid::new_v4();
        ids.insert(r.id, new_id);
        let target_parent = if r.id == id {
            parent_id
        } else {
            Some(
                *ids.get(
                    &r.parent_id
                        .ok_or_else(|| ApiError::conflict("Invalid copy hierarchy"))?,
                )
                .ok_or_else(|| ApiError::conflict("Copy hierarchy changed"))?,
            )
        };
        let mut data = if r.kind == "file" {
            let mut v = current_version(&r)?;
            check_version_scan(&v)?;
            v["number"] = json!(1);
            v["created_at"] = json!(Utc::now());
            let mut d = json!({"current_version":1,"versions":[v.clone()],"classification":r.data["classification"],"copied_from":{"resource_id":r.id,"file_version":r.data["current_version"]}});
            for key in ["mime", "size", "sha256", "storage_key", "scan"] {
                d[key] = v[key].clone();
            }
            d
        } else {
            let mut d = r.data.clone();
            if let Some(map) = d.as_object_mut() {
                for key in [
                    "lock",
                    "archived",
                    "legal_hold",
                    "retain_until",
                    "effective_governance",
                    "governance_enforcement",
                    "moved_from",
                ] {
                    map.remove(key);
                }
            }
            d["copied_from"] = json!({"resource_id":r.id});
            d
        };
        data["inherited_classification_policies"] =
            json!(policy::inherited_classifications(state, &r).await?);
        let target_name = if r.id == id {
            root_name.clone()
        } else {
            r.name.clone()
        };
        prepared.push((r, new_id, target_parent, target_name, data));
    }
    let mut tx = state.pool.begin().await.map_err(err)?;
    if let Some(p) = parent {
        let valid = sqlx::query_scalar::<_, bool>(
            "SELECT version=$2 AND deleted_at IS NULL FROM resources WHERE id=$1 FOR SHARE",
        )
        .bind(p.id)
        .bind(p.version)
        .fetch_one(&mut *tx)
        .await
        .map_err(err)?;
        if !valid {
            return Err(ApiError::conflict("Copy destination changed; reload"));
        }
    }
    let mut copied = Vec::new();
    for (source, new_id, target_parent, target_name, data) in prepared {
        let valid = sqlx::query_scalar::<_, bool>(
            "SELECT version=$2 AND deleted_at IS NULL FROM resources WHERE id=$1 FOR SHARE",
        )
        .bind(source.id)
        .bind(source.version)
        .fetch_one(&mut *tx)
        .await
        .map_err(err)?;
        if !valid {
            return Err(ApiError::conflict("Source changed while copying; retry"));
        }
        let r=sqlx::query_as::<_,Resource>("INSERT INTO resources(id,kind,name,parent_id,owner_id,visibility,acl,data,search_document) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *").bind(new_id).bind(&source.kind).bind(&target_name).bind(target_parent).bind(actor.id).bind(&visibility).bind(&acl).bind(&data).bind(format!("{} {}",target_name,data)).fetch_one(&mut *tx).await.map_err(err)?;
        sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,1,$2,$3,'copy')").bind(r.id).bind(actor.id).bind(serde_json::to_value(&r).map_err(err)?).execute(&mut *tx).await.map_err(err)?;
        sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,'content.copy',$2,$3)").bind(actor.id).bind(r.id).bind(json!({"source_id":source.id,"source_version":source.version})).execute(&mut *tx).await.map_err(err)?;
        copied.push(r);
    }
    tx.commit().await.map_err(err)?;
    let root = copied
        .first()
        .cloned()
        .ok_or_else(|| ApiError::not_found("Copy source disappeared"))?;
    for r in copied {
        if r.kind == "file" {
            if let Err(error) = queue(state, actor, &r).await {
                tracing::warn!(file_id=%r.id,error=%error,"Copied file processing must be retried");
            }
        }
    }
    Ok(root)
}
