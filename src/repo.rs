use crate::*;
use sqlx::Row;

const FIELDS: &str =
    "id,kind,name,parent_id,owner_id,visibility,acl,data,version,created_at,updated_at,deleted_at";
pub const KINDS: &[&str] = &[
    "file",
    "folder",
    "link",
    "collection",
    "comment",
    "share",
    "upload_session",
    "note",
    "canvas",
    "hub",
    "table",
    "record",
    "task",
    "workday",
    "event",
    "form",
    "submission",
    "workflow",
    "workflow_run",
    "workflow_step",
    "connector",
    "agent",
    "sign_request",
    "sign_template",
    "retention",
    "legal_hold",
    "archive",
    "classification",
    "alert",
    "report",
    "notification",
    "group",
    "calendar_connection",
    "document_template",
];

pub async fn raw_get(pool: &PgPool, id: Uuid) -> ApiResult<Resource> {
    Ok(
        sqlx::query_as::<_, Resource>(&format!("SELECT {FIELDS} FROM resources WHERE id=$1"))
            .bind(id)
            .fetch_one(pool)
            .await?,
    )
}
pub async fn check_ancestors(
    pool: &PgPool,
    actor: &Actor,
    r: &Resource,
    write: bool,
) -> ApiResult<()> {
    let mut parent = r.parent_id;
    let mut seen = std::collections::HashSet::from([r.id]);
    while let Some(id) = parent {
        if !seen.insert(id) || seen.len() > 64 {
            return Err(ApiError::conflict("Invalid resource ancestry"));
        }
        let p = raw_get(pool, id).await?;
        // A token explicitly scoped to a child need not separately include its ancestors.
        let mut ancestor_actor = actor.clone();
        ancestor_actor.resource_ids = None;
        if p.deleted_at.is_some()
            || !(if write {
                ancestor_actor.can_write(&p)
            } else {
                ancestor_actor.can_read(&p)
            })
        {
            return Err(ApiError::forbidden("Parent resource access denied"));
        }
        parent = p.parent_id;
    }
    Ok(())
}
pub async fn get(pool: &PgPool, actor: &Actor, id: Uuid) -> ApiResult<Resource> {
    let r = raw_get(pool, id).await?;
    if r.deleted_at.is_some() || !actor.can_read(&r) {
        return Err(ApiError::not_found("Resource not found"));
    }
    check_ancestors(pool, actor, &r, false).await?;
    Ok(r)
}

pub async fn list(
    pool: &PgPool,
    actor: &Actor,
    kind: &str,
    query: Option<&str>,
) -> ApiResult<Vec<Resource>> {
    if !KINDS.contains(&kind) {
        return Err(ApiError::bad_request("Unknown resource kind"));
    }
    actor.require_scope(&format!("{}:read", scope_for(kind)))?;
    let q = query.unwrap_or("").trim();
    if q.chars().count() > 500 {
        return Err(ApiError::bad_request("Search query too long"));
    }
    let pgroonga: bool =
        sqlx::query_scalar("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='pgroonga')")
            .fetch_one(pool)
            .await?;
    // Filter likely-visible candidates before the batch limit. Otherwise another
    // department's newest 5000 rows can hide all of this caller's older work.
    let predicate = "kind=$1 AND deleted_at IS NULL AND (kind<>'collection' OR owner_id=$2) AND ($3 OR owner_id=$2 OR acl ? $4 OR acl ?| $5 OR (visibility='company' AND $6)) AND ($7::uuid[] IS NULL OR id=ANY($7))";
    let search = if q.is_empty() {
        ""
    } else if pgroonga {
        " AND search_document &@~ $8"
    } else {
        " AND search_document ILIKE $8 ESCAPE '\\'"
    };
    let offset_parameter = if q.is_empty() { "$8" } else { "$9" };
    let sql = format!(
        "SELECT {FIELDS} FROM resources WHERE {predicate}{search} ORDER BY updated_at DESC,id DESC LIMIT 500 OFFSET {offset_parameter}"
    );
    let groups: Vec<String> = actor
        .groups
        .iter()
        .map(|id| format!("group:{id}"))
        .collect();
    let mut offset = 0i64;
    let mut out = Vec::new();
    let mut parents = std::collections::HashMap::<Uuid, Resource>::new();
    loop {
        let mut fetch = sqlx::query_as::<_, Resource>(&sql)
            .bind(kind)
            .bind(actor.id)
            .bind(actor.is_admin())
            .bind(format!("user:{}", actor.id))
            .bind(&groups)
            .bind(actor.role != "guest")
            .bind(&actor.resource_ids);
        if !q.is_empty() {
            fetch = fetch.bind(if pgroonga {
                q.to_string()
            } else {
                format!(
                    "%{}%",
                    q.replace('\\', "\\\\")
                        .replace('%', "\\%")
                        .replace('_', "\\_")
                )
            });
        }
        let rows = fetch.bind(offset).fetch_all(pool).await?;
        let complete = rows.len() < 500;
        offset += rows.len() as i64;
        for r in rows {
            if !actor.can_read(&r) {
                continue;
            }
            let mut parent = r.parent_id;
            let mut seen = std::collections::HashSet::from([r.id]);
            let mut allowed = true;
            let mut ancestor_actor = actor.clone();
            ancestor_actor.resource_ids = None;
            while let Some(id) = parent {
                if !seen.insert(id) || seen.len() > 64 {
                    allowed = false;
                    break;
                }
                if !parents.contains_key(&id) {
                    match raw_get(pool, id).await {
                        Ok(p) => {
                            parents.insert(id, p);
                        }
                        Err(_) => {
                            allowed = false;
                            break;
                        }
                    }
                }
                let p = &parents[&id];
                if p.deleted_at.is_some() || !ancestor_actor.can_read(p) {
                    allowed = false;
                    break;
                }
                parent = p.parent_id;
            }
            if allowed {
                out.push(r);
                if out.len() == 5000 {
                    return Ok(out);
                }
            }
        }
        if complete {
            break;
        }
    }
    Ok(out)
}

pub(crate) fn validate_base(
    name: &str,
    visibility: &str,
    acl: &Value,
    data: &Value,
) -> ApiResult<()> {
    if name.trim().is_empty() || name.chars().count() > 255 {
        return Err(ApiError::bad_request("Name must contain 1–255 characters"));
    }
    if !matches!(visibility, "private" | "company") {
        return Err(ApiError::bad_request("Invalid visibility"));
    }
    let entries = acl
        .as_object()
        .ok_or_else(|| ApiError::bad_request("ACL must be an object"))?;
    if entries.len() > 500 {
        return Err(ApiError::bad_request("Too many ACL entries"));
    }
    for (principal, role) in entries {
        let (prefix, id) = principal.split_once(':').ok_or_else(|| {
            ApiError::bad_request("ACL principal must be user:<uuid> or group:<uuid>")
        })?;
        if !matches!(prefix, "user" | "group")
            || Uuid::parse_str(id).is_err()
            || !role
                .as_str()
                .is_some_and(|v| matches!(v, "viewer" | "editor" | "uploader"))
        {
            return Err(ApiError::bad_request("Invalid ACL entry"));
        }
    }
    if !data.is_object() || serde_json::to_vec(data)?.len() > 4 * 1024 * 1024 {
        return Err(ApiError::bad_request(
            "Resource data must be an object below 4 MiB",
        ));
    }
    Ok(())
}

pub async fn create(pool: &PgPool, actor: &Actor, mut new: NewResource) -> ApiResult<Resource> {
    if new.kind == "group" && new.data.get("members").is_some() {
        return Err(ApiError::bad_request(
            "Manage group membership through users or the SCIM Groups endpoint",
        ));
    }
    if !KINDS.contains(&new.kind.as_str()) {
        return Err(ApiError::bad_request("Unknown resource kind"));
    }
    actor.require_scope(&format!("{}:write", scope_for(&new.kind)))?;
    if scope_for(&new.kind) == "admin" && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Administrator access required for this resource type",
        ));
    }
    if (actor.role == "viewer" && new.kind != "collection")
        || actor.role == "guest"
            && new.parent_id.is_none()
            && !matches!(new.kind.as_str(), "comment" | "task" | "collection")
    {
        return Err(ApiError::forbidden("Your role cannot create this resource"));
    }
    if new.kind == "collection" {
        new.visibility = Some("private".into());
        new.acl = Some(json!({}));
        new.parent_id = None;
    }
    if actor.resource_ids.is_some() && new.parent_id.is_none() {
        return Err(ApiError::forbidden(
            "Resource-scoped token must create inside an authorized parent",
        ));
    }
    if let Some(parent_id) = new.parent_id {
        let parent = get(pool, actor, parent_id).await?;
        let comment = matches!(new.kind.as_str(), "comment" | "task");
        if !(if comment {
            actor.can_read(&parent)
        } else {
            actor.can_write(&parent)
        }) {
            return Err(ApiError::forbidden("Parent is read-only"));
        }
        check_ancestors(pool, actor, &parent, !comment).await?;
        if parent.deleted_at.is_some() {
            return Err(ApiError::conflict("Cannot create in deleted parent"));
        }
        // Children may be more restrictive; inherited parent checks always apply.
        if new.visibility.is_none() {
            new.visibility = Some(parent.visibility);
        }
        if new.acl.is_none() {
            new.acl = Some(parent.acl);
        }
    }
    let visibility = new.visibility.unwrap_or_else(|| "private".into());
    let acl = new.acl.unwrap_or_else(|| json!({}));
    validate_base(&new.name, &visibility, &acl, &new.data)?;
    let id = Uuid::new_v4();
    let search_document = format!("{} {}", new.name, new.data);
    let mut tx = pool.begin().await?;
    let r=sqlx::query_as::<_,Resource>(&format!("INSERT INTO resources(id,kind,name,parent_id,owner_id,visibility,acl,data,search_document) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING {FIELDS}"))
        .bind(id).bind(&new.kind).bind(new.name.trim()).bind(new.parent_id).bind(actor.id).bind(visibility).bind(acl).bind(new.data).bind(search_document).fetch_one(&mut *tx).await?;
    snapshot(&mut tx, actor, &r, "create").await?;
    tx.commit().await?;
    Ok(r)
}

pub(crate) async fn snapshot(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    actor: &Actor,
    r: &Resource,
    action: &str,
) -> ApiResult<()> {
    sqlx::query("INSERT INTO resource_history(resource_id,version,actor_id,snapshot,action) VALUES($1,$2,$3,$4,$5)").bind(r.id).bind(r.version).bind(actor.id).bind(serde_json::to_value(r)?).bind(action).execute(&mut **tx).await?;
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,$2,$3,$4)")
        .bind(actor.id)
        .bind(format!("{}.{}", r.kind, action))
        .bind(r.id)
        .bind(json!({"version":r.version}))
        .execute(&mut **tx)
        .await?;
    Ok(())
}

pub async fn update(
    pool: &PgPool,
    actor: &Actor,
    id: Uuid,
    up: UpdateResource,
) -> ApiResult<Resource> {
    let previous = get(pool, actor, id).await?;
    if previous.kind == "group" && up.data.as_ref().is_some_and(|d| d.get("members").is_some()) {
        return Err(ApiError::bad_request(
            "Manage group membership through users or the SCIM Groups endpoint",
        ));
    }
    if scope_for(&previous.kind) == "admin" && !actor.is_admin() {
        return Err(ApiError::forbidden(
            "Administrator access required for this resource type",
        ));
    }
    if previous.deleted_at.is_some() {
        return Err(ApiError::conflict("Restore the resource before editing"));
    }
    if !actor.can_edit(&previous) {
        return Err(ApiError::forbidden("Resource is read-only"));
    }
    if up.expected_version != previous.version || up.expected_version < 1 {
        return Err(ApiError::conflict(
            "Resource changed; reload the latest version",
        ));
    }
    check_ancestors(pool, actor, &previous, true).await?;
    if (up.acl.is_some() || up.visibility.is_some() || up.parent_id.is_some())
        && !actor.can_manage(&previous)
    {
        return Err(ApiError::forbidden(
            "Only the owner or administrator may change access or location",
        ));
    }
    let parent_id = up.parent_id.or(previous.parent_id);
    if parent_id == Some(id) {
        return Err(ApiError::bad_request("A resource cannot be its own parent"));
    }
    if let Some(parent) = parent_id {
        let p = get(pool, actor, parent).await?;
        if !actor.can_write(&p) || p.deleted_at.is_some() {
            return Err(ApiError::forbidden("Destination is not writable"));
        }
        let mut current = Some(parent);
        let mut depth = 0;
        while let Some(p) = current {
            if p == id || depth > 64 {
                return Err(ApiError::conflict("Moving here would create a cycle"));
            }
            current = raw_get(pool, p).await?.parent_id;
            depth += 1;
        }
    }
    let name = up.name.unwrap_or(previous.name);
    let visibility = up.visibility.unwrap_or(previous.visibility);
    let acl = up.acl.unwrap_or(previous.acl);
    let data = up.data.unwrap_or(previous.data);
    if previous.kind == "collection"
        && (visibility != "private"
            || acl.as_object().is_none_or(|m| !m.is_empty())
            || parent_id.is_some())
    {
        return Err(ApiError::bad_request(
            "Collections are private personal references; use a hub or folder for sharing",
        ));
    }
    validate_base(&name, &visibility, &acl, &data)?;
    let search_document = format!("{name} {data}");
    let mut tx = pool.begin().await?;
    let r=sqlx::query_as::<_,Resource>(&format!("UPDATE resources SET name=$2,parent_id=$3,visibility=$4,acl=$5,data=$6,search_document=$7,version=version+1,updated_at=now() WHERE id=$1 AND version=$8 AND deleted_at IS NULL RETURNING {FIELDS}"))
        .bind(id).bind(name.trim()).bind(parent_id).bind(visibility).bind(acl).bind(data).bind(search_document).bind(up.expected_version).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Resource changed; reload before saving"))?;
    snapshot(&mut tx, actor, &r, "update").await?;
    tx.commit().await?;
    Ok(r)
}

pub async fn soft_delete(
    pool: &PgPool,
    actor: &Actor,
    id: Uuid,
    version: i64,
) -> ApiResult<Resource> {
    let previous = get(pool, actor, id).await?;
    if !actor.can_manage(&previous) {
        return Err(ApiError::forbidden(
            "Only the owner or administrator may delete",
        ));
    }
    if scope_for(&previous.kind) == "admin" && !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    let mut tx = pool.begin().await?;
    let r=sqlx::query_as::<_,Resource>(&format!("UPDATE resources SET deleted_at=now(),version=version+1,updated_at=now() WHERE id=$1 AND version=$2 AND deleted_at IS NULL RETURNING {FIELDS}"))
        .bind(id).bind(version).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Resource changed or is already deleted"))?;
    if r.kind == "group" {
        sqlx::query("UPDATE users SET groups=groups-$1 WHERE groups ? $1")
            .bind(id.to_string())
            .execute(&mut *tx)
            .await?;
    }
    snapshot(&mut tx, actor, &r, "delete").await?;
    tx.commit().await?;
    Ok(r)
}
pub async fn restore(pool: &PgPool, actor: &Actor, id: Uuid, version: i64) -> ApiResult<Resource> {
    let previous = raw_get(pool, id).await?;
    if !actor.can_manage(&previous) {
        return Err(ApiError::forbidden(
            "Only the owner or administrator may restore",
        ));
    }
    if scope_for(&previous.kind) == "admin" && !actor.is_admin() {
        return Err(ApiError::forbidden("Administrator access required"));
    }
    check_ancestors(pool, actor, &previous, false).await?;
    if let Some(parent) = previous.parent_id {
        let p = raw_get(pool, parent).await?;
        if p.deleted_at.is_some() {
            return Err(ApiError::conflict("Restore the parent folder first"));
        }
    }
    let mut tx = pool.begin().await?;
    let r=sqlx::query_as::<_,Resource>(&format!("UPDATE resources SET deleted_at=NULL,version=version+1,updated_at=now() WHERE id=$1 AND version=$2 AND deleted_at IS NOT NULL RETURNING {FIELDS}"))
        .bind(id).bind(version).fetch_optional(&mut *tx).await?.ok_or_else(||ApiError::conflict("Resource changed or is not in trash"))?;
    snapshot(&mut tx, actor, &r, "restore").await?;
    tx.commit().await?;
    Ok(r)
}
pub async fn audit(
    pool: &PgPool,
    actor: &Actor,
    action: &str,
    resource_id: Option<Uuid>,
    details: Value,
) -> ApiResult<()> {
    sqlx::query("INSERT INTO audit_log(actor_id,action,resource_id,details) VALUES($1,$2,$3,$4)")
        .bind(actor.id)
        .bind(action)
        .bind(resource_id)
        .bind(details)
        .execute(pool)
        .await?;
    Ok(())
}

pub async fn enqueue(
    pool: &PgPool,
    actor: &Actor,
    kind: &str,
    payload: Value,
    idempotency_key: Option<&str>,
) -> ApiResult<Job> {
    enqueue_at(pool, actor, kind, payload, idempotency_key, Utc::now()).await
}
pub async fn enqueue_at(
    pool: &PgPool,
    actor: &Actor,
    kind: &str,
    mut payload: Value,
    idempotency_key: Option<&str>,
    available_at: DateTime<Utc>,
) -> ApiResult<Job> {
    let namespaced_key = idempotency_key.map(|key| {
        format!(
            "{}:{key}",
            actor
                .token_id
                .map(|id| format!("token:{id}"))
                .unwrap_or_else(|| "interactive".into())
        )
    });
    let idempotency_key = namespaced_key.as_deref();
    if let Some(map) = payload.as_object_mut() {
        map.entry("actor_id").or_insert(json!(actor.id));
        map.insert("actor_constraint".into(), serde_json::to_value(actor)?);
    }
    let inserted=sqlx::query_as::<_,Job>("INSERT INTO jobs(id,owner_id,kind,payload,idempotency_key,available_at) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(owner_id,kind,idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING RETURNING id,owner_id,kind,status,payload,result,error,attempts,created_at,updated_at")
        .bind(Uuid::new_v4()).bind(actor.id).bind(kind).bind(payload).bind(idempotency_key).bind(available_at).fetch_optional(pool).await?;
    match inserted{Some(job)=>Ok(job),None=>Ok(sqlx::query_as::<_,Job>("SELECT id,owner_id,kind,status,payload,result,error,attempts,created_at,updated_at FROM jobs WHERE owner_id=$1 AND kind=$2 AND idempotency_key=$3").bind(actor.id).bind(kind).bind(idempotency_key).fetch_one(pool).await?)}
}

pub async fn history(pool: &PgPool, actor: &Actor, id: Uuid) -> ApiResult<Vec<Value>> {
    get(pool, actor, id).await?;
    let rows=sqlx::query("SELECT snapshot,action,created_at,actor_id FROM resource_history WHERE resource_id=$1 ORDER BY version DESC LIMIT 500").bind(id).fetch_all(pool).await?;
    Ok(rows.into_iter().map(|r|json!({"snapshot":r.get::<Value,_>("snapshot"),"action":r.get::<String,_>("action"),"created_at":r.get::<DateTime<Utc>,_>("created_at"),"actor_id":r.get::<Uuid,_>("actor_id")})).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn actor(role: &str) -> Actor {
        Actor {
            id: Uuid::new_v4(),
            name: "Test".into(),
            email: "test@example.org".into(),
            role: role.into(),
            groups: vec![],
            scopes: None,
            resource_ids: None,
            token_id: None,
        }
    }
    fn resource(owner: Uuid) -> Resource {
        Resource {
            id: Uuid::new_v4(),
            kind: "file".into(),
            name: "private.txt".into(),
            owner_id: owner,
            parent_id: None,
            visibility: "private".into(),
            acl: json!({}),
            data: json!({}),
            version: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            deleted_at: None,
        }
    }
    #[test]
    fn scoped_owner_token_cannot_elevate() {
        let mut owner = actor("owner");
        let r = resource(owner.id);
        owner.scopes = Some(vec!["content:read".into()]);
        assert!(owner.can_read(&r));
        assert!(!owner.can_write(&r));
        assert!(!owner.is_admin());
    }
    #[test]
    fn guest_requires_explicit_acl() {
        let guest = actor("guest");
        let mut r = resource(Uuid::new_v4());
        r.visibility = "company".into();
        assert!(!guest.can_read(&r));
        r.acl = json!({format!("user:{}",guest.id):"viewer"});
        assert!(guest.can_read(&r));
        assert!(!guest.can_write(&r));
    }
    #[test]
    fn scoped_resources_do_not_leak_siblings() {
        let mut a = actor("member");
        let r = resource(a.id);
        a.resource_ids = Some(vec![Uuid::new_v4()]);
        assert!(!a.can_read(&r));
    }
    #[test]
    fn resource_scope_does_not_grant_company_administration() {
        let mut a = actor("owner");
        a.scopes = Some(vec!["admin:*".into()]);
        a.resource_ids = Some(vec![Uuid::new_v4()]);
        assert!(!a.is_admin());
    }
}
