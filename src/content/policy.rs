use crate::{Actor, ApiError, ApiResult, AppState, Resource};
use chrono::{DateTime, Utc};
use serde_json::Value;
use uuid::Uuid;

pub fn future_date(value: Option<&Value>) -> bool {
    value
        .and_then(Value::as_str)
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .is_some_and(|d| d > Utc::now())
}
pub fn blocked_action(data: &Value, action: &str, actor: &Actor) -> bool {
    let blocked = data
        .get("blocked_actions")
        .and_then(Value::as_array)
        .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(action)));
    if !blocked {
        return false;
    }
    let allowed = data
        .get("allowed_groups")
        .and_then(Value::as_array)
        .is_some_and(|g| {
            g.iter().any(|v| {
                v.as_str()
                    .is_some_and(|s| actor.groups.iter().any(|x| x == s))
            })
        });
    !allowed
}
pub(super) async fn ancestors(state: &AppState, resource: &Resource) -> ApiResult<Vec<Uuid>> {
    let mut ids = vec![resource.id];
    let mut parent = resource.parent_id;
    while let Some(id) = parent {
        if ids.contains(&id) || ids.len() > 100 {
            return Err(ApiError::conflict("Invalid resource hierarchy"));
        }
        ids.push(id);
        parent =
            sqlx::query_scalar::<_, Option<Uuid>>("SELECT parent_id FROM resources WHERE id=$1")
                .bind(id)
                .fetch_optional(&state.pool)
                .await
                .map_err(|_| ApiError::internal("Could not check resource ancestry"))?
                .flatten();
    }
    Ok(ids)
}
pub(super) fn targets(policy: &Value, ids: &[Uuid], resource: &Resource) -> bool {
    if policy.get("all_resources").and_then(Value::as_bool) == Some(true) {
        return true;
    }
    if policy
        .get("resource_id")
        .and_then(Value::as_str)
        .and_then(|s| s.parse::<Uuid>().ok())
        .is_some_and(|id| ids.contains(&id))
    {
        return true;
    }
    if policy
        .get("resource_ids")
        .and_then(Value::as_array)
        .is_some_and(|a| {
            a.iter().any(|v| {
                v.as_str()
                    .and_then(|s| s.parse::<Uuid>().ok())
                    .is_some_and(|id| ids.contains(&id))
            })
        })
    {
        return true;
    }
    policy
        .get("classification")
        .and_then(Value::as_str)
        .is_some_and(|s| resource.data.get("classification").and_then(Value::as_str) == Some(s))
}
pub(super) fn classification_targets(policy: &Resource, ids: &[Uuid], resource: &Resource) -> bool {
    targets(&policy.data, ids, resource)
        || resource
            .data
            .get("inherited_classification_policies")
            .and_then(Value::as_array)
            .is_some_and(|a| {
                a.iter()
                    .any(|v| v.as_str() == Some(policy.id.to_string().as_str()))
            })
}
pub(super) async fn inherited_classifications(
    state: &AppState,
    resource: &Resource,
) -> ApiResult<Vec<Uuid>> {
    let ids = ancestors(state, resource).await?;
    let policies = sqlx::query_as::<_, Resource>(
        "SELECT * FROM resources WHERE kind='classification' AND deleted_at IS NULL",
    )
    .fetch_all(&state.pool)
    .await
    .map_err(|_| ApiError::internal("Could not preserve classification policies"))?;
    Ok(policies
        .iter()
        .filter(|p| classification_targets(p, &ids, resource))
        .map(|p| p.id)
        .collect())
}
pub async fn authorize_action(
    state: &AppState,
    actor: &Actor,
    resource: &Resource,
    action: &str,
) -> ApiResult<()> {
    actor.require_scope(&format!(
        "{}:{}",
        crate::scope_for(&resource.kind),
        if matches!(
            action,
            "read" | "preview" | "download" | "integration" | "print"
        ) {
            "read"
        } else {
            "write"
        }
    ))?;
    if action == "print" && preview_rules(state, actor, resource).await?.prevent_print {
        return Err(ApiError::forbidden(
            "Printing is restricted by content policy",
        ));
    }
    if resource.data.get("archived").and_then(Value::as_bool) == Some(true) {
        if !actor.is_admin() || !matches!(action, "read" | "preview" | "restore") {
            return Err(ApiError::forbidden(
                "Archived content is restricted; restore it before changing it",
            ));
        }
    }
    if action == "purge"
        && (resource.data.get("legal_hold").and_then(Value::as_bool) == Some(true)
            || future_date(resource.data.get("retain_until")))
    {
        return Err(ApiError::forbidden(
            "Content is protected by retention or legal hold",
        ));
    }
    if resource.kind == "file" && matches!(action, "download" | "preview" | "integration") {
        match resource
            .data
            .pointer("/scan/status")
            .and_then(Value::as_str)
        {
            Some("infected") => {
                return Err(ApiError::forbidden(
                    "File is quarantined because malicious content was detected",
                ));
            }
            Some("clean") => {}
            _ if std::env::var("BLUEPRINT_REQUIRE_SCAN").as_deref() == Ok("1") => {
                return Err(ApiError::unavailable(
                    "File remains quarantined until scanning succeeds",
                ));
            }
            _ => {}
        }
    }
    if let Some(lock) = resource.data.get("lock") {
        let active = lock.get("locked").and_then(Value::as_bool) == Some(true)
            && (lock.get("until").is_none_or(Value::is_null) || future_date(lock.get("until")));
        let mine =
            lock.get("owner_id").and_then(Value::as_str) == Some(actor.id.to_string().as_str());
        if active
            && !mine
            && !actor.is_admin()
            && (matches!(action, "update" | "delete" | "purge")
                || (action == "download"
                    && lock.get("disable_download").and_then(Value::as_bool) == Some(true)))
        {
            return Err(ApiError::conflict("File is locked by another collaborator"));
        }
    }
    let ids = ancestors(state, resource).await?;
    let ancestor_data =
        sqlx::query_scalar::<_, Value>("SELECT data FROM resources WHERE id=ANY($1) AND id<>$2")
            .bind(&ids)
            .bind(resource.id)
            .fetch_all(&state.pool)
            .await
            .map_err(|_| ApiError::internal("Could not evaluate ancestor governance"))?;
    if ancestor_data
        .iter()
        .any(|d| d.get("archived").and_then(Value::as_bool) == Some(true))
        && (!actor.is_admin() || !matches!(action, "read" | "preview" | "restore"))
    {
        return Err(ApiError::forbidden("Content belongs to an archived folder"));
    }
    if action == "purge"
        && ancestor_data.iter().any(|d| {
            d.get("legal_hold").and_then(Value::as_bool) == Some(true)
                || future_date(d.get("retain_until"))
        })
    {
        return Err(ApiError::forbidden(
            "Content inherits retention or legal hold from its folder",
        ));
    }
    let policies=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind IN ('retention','legal_hold','archive','classification') AND deleted_at IS NULL").fetch_all(&state.pool).await.map_err(|_|ApiError::internal("Could not evaluate content policies"))?;
    for p in policies {
        if p.data.get("active").and_then(Value::as_bool) != Some(true)
            || !(if p.kind == "classification" {
                classification_targets(&p, &ids, resource)
            } else {
                targets(&p.data, &ids, resource)
            })
        {
            continue;
        }
        match p.kind.as_str() {
            "archive" if !actor.is_admin() || !matches!(action, "read" | "preview" | "restore") => {
                return Err(ApiError::forbidden("Content is in an enterprise archive"));
            }
            "legal_hold" if action == "purge" => {
                return Err(ApiError::forbidden("Content is protected by a legal hold"));
            }
            "retention" if action == "purge" && future_date(p.data.get("retain_until")) => {
                return Err(ApiError::forbidden(
                    "Content retention period has not expired",
                ));
            }
            "classification"
                if blocked_action(&p.data, action, actor)
                    || (actor.token_id.is_some()
                        && matches!(action, "read" | "preview" | "download")
                        && blocked_action(&p.data, "integration", actor)) =>
            {
                return Err(ApiError::forbidden(format!(
                    "Action is restricted by classification policy: {}",
                    p.name
                )));
            }
            _ => {}
        }
    }
    if resource.kind == "retention"
        && resource.data.get("active").and_then(Value::as_bool) == Some(true)
        && future_date(resource.data.get("retain_until"))
        && matches!(action, "delete" | "purge")
    {
        return Err(ApiError::forbidden(
            "An active unexpired retention policy cannot be removed",
        ));
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn correct_policy_target_scope() {
        let id = Uuid::new_v4();
        let r = Resource {
            id,
            kind: "file".into(),
            name: "a".into(),
            parent_id: None,
            owner_id: Uuid::new_v4(),
            visibility: "private".into(),
            acl: serde_json::json!({}),
            data: serde_json::json!({"classification":"restricted"}),
            version: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            deleted_at: None,
        };
        assert!(targets(
            &serde_json::json!({"resource_ids":[id]}),
            &[id],
            &r
        ));
        assert!(!targets(
            &serde_json::json!({"resource_ids":[Uuid::new_v4()]}),
            &[id],
            &r
        ));
        assert!(targets(
            &serde_json::json!({"classification":"restricted"}),
            &[id],
            &r
        ));
    }
    #[test]
    fn retention_dates() {
        assert!(future_date(Some(&Value::String(
            (Utc::now() + chrono::Duration::days(1)).to_rfc3339()
        ))));
        assert!(!future_date(Some(&Value::String("bad".into()))));
        assert!(!future_date(None));
    }
}

pub(super) async fn effective_governance(
    state: &AppState,
    resource: &Resource,
) -> ApiResult<(Option<String>, bool)> {
    let ids = ancestors(state, resource).await?;
    let mut direct =
        sqlx::query_scalar::<_, Value>("SELECT data FROM resources WHERE id=ANY($1) AND id<>$2")
            .bind(&ids)
            .bind(resource.id)
            .fetch_all(&state.pool)
            .await
            .map_err(|_| ApiError::internal("Could not evaluate ancestor governance"))?;
    direct.push(resource.data.clone());
    let policies=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind IN ('retention','legal_hold') AND deleted_at IS NULL AND data->>'active'='true'").fetch_all(&state.pool).await.map_err(|_|ApiError::internal("Could not evaluate governance"))?;
    let matching = policies
        .iter()
        .filter(|p| targets(&p.data, &ids, resource))
        .collect::<Vec<_>>();
    let hold = direct
        .iter()
        .any(|d| d.get("legal_hold").and_then(Value::as_bool) == Some(true))
        || matching.iter().any(|p| p.kind == "legal_hold");
    let retain = matching
        .iter()
        .filter(|p| p.kind == "retention")
        .filter_map(|p| p.data.get("retain_until").and_then(Value::as_str))
        .chain(
            direct
                .iter()
                .filter_map(|d| d.get("retain_until").and_then(Value::as_str)),
        )
        .filter_map(|s| DateTime::parse_from_rfc3339(s).ok())
        .max()
        .filter(|d| *d > Utc::now())
        .map(|d| d.to_rfc3339());
    Ok((retain, hold))
}

#[derive(Default, serde::Serialize)]
pub(super) struct PreviewRules {
    pub watermark: bool,
    pub prevent_print: bool,
}
pub(super) async fn preview_rules(
    state: &AppState,
    actor: &Actor,
    resource: &Resource,
) -> ApiResult<PreviewRules> {
    let ids = ancestors(state, resource).await?;
    let policies=sqlx::query_as::<_,Resource>("SELECT * FROM resources WHERE kind='classification' AND deleted_at IS NULL AND data->>'active'='true'").fetch_all(&state.pool).await.map_err(|_|ApiError::internal("Could not evaluate preview policies"))?;
    let mut result = PreviewRules {
        watermark: resource.data.get("watermark").and_then(Value::as_bool) == Some(true),
        prevent_print: resource.data.get("prevent_print").and_then(Value::as_bool) == Some(true),
    };
    for p in policies {
        if !classification_targets(&p, &ids, resource) {
            continue;
        }
        let exempt = p
            .data
            .get("watermark_exempt_groups")
            .and_then(Value::as_array)
            .is_some_and(|g| {
                g.iter().any(|v| {
                    v.as_str()
                        .is_some_and(|s| actor.groups.iter().any(|x| x == s))
                })
            });
        if !exempt {
            result.watermark |= p.data.get("watermark").and_then(Value::as_bool) == Some(true);
        }
        result.prevent_print |= p.data.get("prevent_print").and_then(Value::as_bool) == Some(true);
    }
    Ok(result)
}
