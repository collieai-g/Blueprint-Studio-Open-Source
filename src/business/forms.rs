use super::{data_object, ensure_write, modification, parse_id, records};
use crate::{Actor, ApiError, ApiResult, AppState, NewResource, Resource, repo};
use axum::{
    Extension, Json,
    extract::{Path, Query, State},
    http::HeaderMap,
};
use base64::Engine;
use chrono::{DateTime, Utc};
use rand::RngCore;
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use uuid::Uuid;

#[derive(Deserialize)]
pub struct PublishInput {
    pub published: bool,
    pub expected_version: Option<i64>,
    pub access: Option<String>,
    pub expires_at: Option<String>,
}
#[derive(Deserialize, Default)]
pub struct PublicQuery {
    pub token: Option<String>,
}
#[derive(Deserialize)]
pub struct SubmitInput {
    pub values: Value,
    pub submission_key: Option<Uuid>,
    #[serde(default)]
    pub preview: bool,
}

pub async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    data_object(data)?;
    if kind == "form" {
        let schema = records::fields(data)?;
        if data.get("published").and_then(Value::as_bool) == Some(true)
            && data
                .get("access")
                .and_then(Value::as_str)
                .unwrap_or("public")
                == "public"
            && schema
                .iter()
                .any(|field| ["user", "relation"].contains(&field.field_type.as_str()))
        {
            return Err(ApiError::bad_request(
                "Public forms cannot expose company member or relation selectors. Unpublish this form or use company access first.",
            ));
        }
        if data
            .get("access")
            .and_then(Value::as_str)
            .is_some_and(|x| !["public", "company"].contains(&x))
        {
            return Err(ApiError::bad_request(
                "Form access must be public or company.",
            ));
        }
        if let Some(id) = data.get("table_id").filter(|v| !v.is_null()) {
            let table = super::get_resource(&state, actor, parse_id(Some(id), "table_id")?).await?;
            if table.kind != "table" {
                return Err(ApiError::bad_request(
                    "Form table_id must reference a table.",
                ));
            }
            ensure_write(actor, &table)?;
            let table_fields = records::fields(&table.data)?;
            for f in &schema {
                if !table_fields
                    .iter()
                    .any(|t| t.id == f.id && t.field_type == f.field_type)
                {
                    return Err(ApiError::bad_request(
                        "Form fields must match their linked table fields.",
                    ));
                }
            }
        }
        if let Some(rules) = data.get("conditions") {
            validate_conditions(&schema, rules)?;
        }
        if let Some(old) = previous {
            for key in ["publish_token_hash", "published", "expires_at"] {
                if old.data.get(key) != data.get(key) {
                    return Err(ApiError::bad_request(
                        "Use the form publish endpoint to change publication settings.",
                    ));
                }
            }
        } else if data.get("published").and_then(Value::as_bool) == Some(true)
            || data.get("publish_token_hash").is_some()
        {
            return Err(ApiError::bad_request(
                "Create a draft form before publishing it.",
            ));
        }
    } else if kind == "submission" {
        // Submission content is immutable; changing it would detach the audit
        // evidence from the exact input that launched a business workflow.
        if let Some(old) = previous {
            if old.data != *data {
                return Err(ApiError::bad_request(
                    "Submitted form values are immutable. Create a corrected submission instead.",
                ));
            }
        } else {
            return Err(ApiError::bad_request(
                "Use a form submission endpoint to create submissions.",
            ));
        }
    }
    Ok(())
}

fn validate_conditions(fields: &[records::Field], rules: &Value) -> ApiResult<()> {
    let rules = rules
        .as_array()
        .ok_or_else(|| ApiError::bad_request("conditions must be an array."))?;
    if rules.len() > 100 {
        return Err(ApiError::bad_request(
            "A form may contain at most 100 conditional rules.",
        ));
    }
    for rule in rules {
        let target = rule.get("target").and_then(Value::as_str).unwrap_or("");
        if !fields.iter().any(|f| f.id == target) {
            return Err(ApiError::bad_request(
                "Conditional rule references an unknown target field.",
            ));
        }
        if !["show", "hide", "enable", "disable"]
            .contains(&rule.get("action").and_then(Value::as_str).unwrap_or(""))
        {
            return Err(ApiError::bad_request("Invalid conditional rule action."));
        }
        if !["all", "any"].contains(&rule.get("mode").and_then(Value::as_str).unwrap_or("all")) {
            return Err(ApiError::bad_request("Condition mode must be all or any."));
        }
        let tests = rule
            .get("when")
            .and_then(Value::as_array)
            .ok_or_else(|| ApiError::bad_request("Condition when must be an array."))?;
        if tests.is_empty() || tests.len() > 50 {
            return Err(ApiError::bad_request(
                "A conditional rule requires 1-50 comparisons.",
            ));
        }
        for test in tests {
            let field = test.get("field").and_then(Value::as_str).unwrap_or("");
            if field == target || !fields.iter().any(|f| f.id == field) {
                return Err(ApiError::bad_request(
                    "Conditions must reference another existing field.",
                ));
            }
            if ![
                "eq",
                "ne",
                "empty",
                "not_empty",
                "contains",
                "starts_with",
                "ends_with",
                "gt",
                "gte",
                "lt",
                "lte",
            ]
            .contains(&test.get("operator").and_then(Value::as_str).unwrap_or("eq"))
            {
                return Err(ApiError::bad_request("Unsupported condition operator."));
            }
        }
    }
    Ok(())
}

fn active_values(data: &Value, values: &Value) -> ApiResult<(Vec<records::Field>, Value)> {
    let fields = records::fields(data)?;
    let mut values = values
        .as_object()
        .ok_or_else(|| ApiError::bad_request("values must be an object."))?
        .clone();
    let known = fields
        .iter()
        .map(|f| f.id.as_str())
        .collect::<std::collections::HashSet<_>>();
    if values.keys().any(|k| !known.contains(k.as_str())) {
        return Err(ApiError::bad_request(
            "values contains unknown form fields.",
        ));
    }
    let mut active = fields
        .iter()
        .map(|f| (f.id.clone(), true))
        .collect::<std::collections::HashMap<_, _>>();
    if let Some(rules) = data.get("conditions").and_then(Value::as_array) {
        validate_conditions(&fields, &json!(rules))?;
        for rule in rules {
            let tests = rule["when"].as_array().unwrap();
            let matches = if rule.get("mode").and_then(Value::as_str) == Some("any") {
                tests
                    .iter()
                    .any(|t| records::filter_matches(&Value::Object(values.clone()), t))
            } else {
                tests
                    .iter()
                    .all(|t| records::filter_matches(&Value::Object(values.clone()), t))
            };
            let enabled = match rule["action"].as_str().unwrap() {
                "show" | "enable" => matches,
                "hide" | "disable" => !matches,
                _ => true,
            };
            active.insert(rule["target"].as_str().unwrap().to_owned(), enabled);
        }
    }
    let fields = fields
        .into_iter()
        .filter(|f| active.get(&f.id) == Some(&true))
        .collect::<Vec<_>>();
    values.retain(|k, _| active.get(k) == Some(&true));
    Ok((fields, Value::Object(values)))
}

pub async fn publish(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<PublishInput>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("business:write")?;
    let form = super::get_resource(&state, &actor, id).await?;
    if form.kind != "form" {
        return Err(ApiError::not_found("Form not found."));
    }
    ensure_write(&actor, &form)?;
    if input.expected_version.is_some_and(|v| v != form.version) {
        return Err(ApiError::conflict(
            "The form changed. Reload before publishing.",
        ));
    }
    let mut data = form.data.clone();
    let access = input
        .access
        .as_deref()
        .or_else(|| data.get("access").and_then(Value::as_str))
        .unwrap_or("public")
        .to_owned();
    if !["public", "company"].contains(&access.as_str()) {
        return Err(ApiError::bad_request(
            "Form access must be public or company.",
        ));
    }
    if access == "public"
        && records::fields(&data)?
            .iter()
            .any(|f| ["relation", "user"].contains(&f.field_type.as_str()))
    {
        return Err(ApiError::bad_request(
            "Anonymous forms cannot expose company member or relation selectors. Use company-only access.",
        ));
    }
    if let Some(expiry) = &input.expires_at {
        let expiry = DateTime::parse_from_rfc3339(expiry)
            .map_err(|_| ApiError::bad_request("expires_at must be a timestamp."))?;
        if input.published && expiry <= Utc::now() {
            return Err(ApiError::bad_request(
                "Published form expiry must be in the future.",
            ));
        }
    }
    let mut token_bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut token_bytes);
    let token = hex::encode(token_bytes);
    data["published"] = json!(input.published);
    data["access"] = json!(access);
    data["expires_at"] = json!(input.expires_at);
    data["publish_token_hash"] = if input.published {
        json!(hex::encode(Sha256::digest(token.as_bytes())))
    } else {
        Value::Null
    };
    let url = if input.published {
        Some(format!(
            "{}/forms/{id}?token={token}",
            state.config.public_url.trim_end_matches('/')
        ))
    } else {
        None
    };
    data["public_url"] = json!(url);
    let mut saved = super::update_resource(&state, &actor, id, modification(&form, data)).await?;
    super::redact_resource(&mut saved);
    Ok(Json(json!({"form":saved,"url":url})))
}

async fn published_form(
    state: &AppState,
    id: Uuid,
    token: Option<&str>,
    visitor: Option<&Actor>,
) -> ApiResult<(Resource, Actor)> {
    crate::core::module_enabled(state, "forms").await?;
    let owner: Option<Uuid> = sqlx::query_scalar(
        "SELECT owner_id FROM resources WHERE id=$1 AND kind='form' AND deleted_at IS NULL",
    )
    .bind(id)
    .fetch_optional(&state.pool)
    .await
    .map_err(|_| ApiError::internal("Could not read form."))?;
    let owner = owner.ok_or_else(|| ApiError::not_found("Form is not available."))?;
    let actor = crate::auth::actor_by_id(state, owner).await?;
    let form = super::get_resource(&state, &actor, id).await?;
    if form.data.get("published").and_then(Value::as_bool) != Some(true)
        || form
            .data
            .get("expires_at")
            .and_then(Value::as_str)
            .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
            .is_some_and(|t| t < Utc::now())
    {
        return Err(ApiError::not_found("Form is not available."));
    }
    if form.data.get("access").and_then(Value::as_str) == Some("company") {
        let visitor = visitor
            .ok_or_else(|| ApiError::unauthorized("Sign in to submit this company form."))?;
        super::get_resource(&state, visitor, id).await?;
        return Ok((form, actor));
    }
    let hash = token
        .filter(|t| t.len() == 64)
        .map(|t| hex::encode(Sha256::digest(t.as_bytes())));
    if hash.as_deref() != form.data.get("publish_token_hash").and_then(Value::as_str) {
        return Err(ApiError::not_found("Form is not available."));
    }
    Ok((form, actor))
}

pub async fn public_form(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<PublicQuery>,
    headers: HeaderMap,
) -> ApiResult<Json<Value>> {
    let visitor = crate::auth::optional_actor(&state, &headers).await?;
    let (form, _) = published_form(&state, id, query.token.as_deref(), visitor.as_ref()).await?;
    let mut data = json!({});
    for key in [
        "fields",
        "description",
        "conditions",
        "submit_label",
        "expires_at",
        "access",
    ] {
        if let Some(v) = form.data.get(key) {
            data[key] = v.clone();
        }
    }
    // No membership, linked table, credentials or stored submission is exposed.
    Ok(Json(
        json!({"id":form.id,"kind":"form","name":form.name,"data":data,"version":form.version}),
    ))
}

pub async fn public_submit(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Query(query): Query<PublicQuery>,
    headers: HeaderMap,
    Json(mut input): Json<SubmitInput>,
) -> ApiResult<Json<Value>> {
    let visitor = crate::auth::optional_actor(&state, &headers).await?;
    let (form, owner) =
        published_form(&state, id, query.token.as_deref(), visitor.as_ref()).await?;
    input.preview = false;
    let result = submit(&state, &owner, &form, input, visitor.as_ref()).await?;
    if let Some(visitor) = visitor {
        repo::audit(
            &state.pool,
            &visitor,
            "form.submitted",
            result
                .get("id")
                .and_then(Value::as_str)
                .and_then(|s| Uuid::parse_str(s).ok()),
            json!({"form_id":form.id}),
        )
        .await?;
    }
    Ok(Json(result))
}

pub async fn authenticated_submit(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<SubmitInput>,
) -> ApiResult<Json<Value>> {
    actor.require_scope("business:write")?;
    let form = super::get_resource(&state, &actor, id).await?;
    if form.kind != "form" {
        return Err(ApiError::not_found("Form not found."));
    }
    if !input.preview
        && (form.data.get("published").and_then(Value::as_bool) != Some(true)
            || form
                .data
                .get("expires_at")
                .and_then(Value::as_str)
                .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
                .is_some_and(|x| x < Utc::now()))
    {
        return Err(ApiError::conflict("This form is not published."));
    }
    // A company member may submit without having edit permission on the form.
    // The owner writes the submission and linked record, while submitted_by
    // preserves the identity that actually entered the data.
    let owner = crate::auth::actor_by_id(&state, form.owner_id).await?;
    let mut result = submit(&state, &owner, &form, input, Some(&actor)).await?;
    if let Some(id) = result
        .get("id")
        .and_then(Value::as_str)
        .and_then(|s| Uuid::parse_str(s).ok())
    {
        repo::audit(
            &state.pool,
            &actor,
            "form.submitted",
            Some(id),
            json!({"form_id":form.id}),
        )
        .await?;
    }
    result["submitted_by"] = json!(actor.id);
    Ok(Json(result))
}

async fn submit(
    state: &AppState,
    owner: &Actor,
    form: &Resource,
    input: SubmitInput,
    visitor: Option<&Actor>,
) -> ApiResult<Value> {
    let anonymous = visitor.is_none();
    if input.values.to_string().len() > 28_000_000 {
        return Err(ApiError::bad_request("Form submission is too large."));
    }
    let (fields, mut values) = active_values(&form.data, &input.values)?;
    let mut attachments = Vec::new();
    for field in fields.iter().filter(|f| f.field_type == "file") {
        let Some(value) = values.get(&field.id).cloned().filter(|v| !v.is_null()) else {
            continue;
        };
        let uploads = if field.multiple {
            value.as_array().cloned().ok_or_else(|| {
                ApiError::bad_request("Multiple file fields need an array of uploads.")
            })?
        } else {
            vec![value]
        };
        let mut ids = Vec::new();
        for upload in uploads {
            if !anonymous && upload.is_string() {
                let file_id = parse_id(Some(&upload), &field.id)?;
                let file = super::get_resource(state, visitor.unwrap(), file_id).await?;
                if file.kind != "file" {
                    return Err(ApiError::bad_request(
                        "File selectors must reference uploaded files.",
                    ));
                }
                // Submitting a reference delegates its use to the form owner;
                // both identities must be able to read the selected source.
                super::get_resource(state, owner, file_id).await?;
                ids.push(upload);
                continue;
            }
            let name = upload
                .get("name")
                .and_then(Value::as_str)
                .filter(|n| !n.is_empty() && n.len() <= 255)
                .ok_or_else(|| ApiError::bad_request("Uploaded form files need a name."))?
                .to_owned();
            let mime = upload
                .get("mime")
                .and_then(Value::as_str)
                .unwrap_or("application/octet-stream")
                .to_owned();
            let raw=upload.get("content_base64").and_then(Value::as_str).ok_or_else(||ApiError::bad_request("Anonymous file fields require an actual upload, not a company file identifier."))?;
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(raw)
                .map_err(|_| ApiError::bad_request("Invalid base64 file upload."))?;
            if bytes.len() > 20 * 1024 * 1024 {
                return Err(ApiError::bad_request(
                    "Form attachments are limited to 20 MiB each.",
                ));
            }
            let placeholder = Uuid::new_v4();
            ids.push(json!(placeholder));
            attachments.push((field.id.clone(), placeholder, name, mime, bytes));
        }
        values[&field.id] = if field.multiple {
            json!(ids)
        } else {
            ids.into_iter().next().unwrap_or(Value::Null)
        };
    }
    let mut normalized = records::normalize_values(&fields, &values, true)?;
    let references = fields
        .iter()
        .filter(|field| field.field_type != "file")
        .cloned()
        .collect::<Vec<_>>();
    records::validate_relations(state, visitor.unwrap_or(owner), &references, &normalized).await?;
    records::validate_relations(state, owner, &references, &normalized).await?;
    if input.preview {
        return Ok(json!({"preview":true,"values":normalized}));
    }
    let key = input
        .submission_key
        .unwrap_or_else(Uuid::new_v4)
        .to_string();
    if let Some(existing)=sqlx::query_scalar::<_,Uuid>("SELECT id FROM resources WHERE kind='submission' AND data->>'form_id'=$1 AND data->>'submission_key'=$2 AND deleted_at IS NULL LIMIT 1").bind(form.id.to_string()).bind(&key).fetch_optional(&state.pool).await.map_err(|_|ApiError::internal("Could not check submission."))?{return Ok(json!({"id":existing,"created":false,"duplicate":true}));}
    for (field, placeholder, name, mime, bytes) in attachments {
        let file = crate::content::store_bytes(state, owner, &name, &mime, &bytes, None).await?;
        if let Some(array) = normalized.get_mut(&field).and_then(Value::as_array_mut) {
            for val in array {
                if val.as_str() == Some(&placeholder.to_string()) {
                    *val = json!(file.id);
                }
            }
        } else {
            normalized[&field] = json!(file.id);
        }
    }
    let submission=super::create_resource(&state,owner,NewResource{kind:"submission".into(),name:format!("{} · {}",form.name,Utc::now().format("%Y-%m-%d %H:%M")),parent_id:Some(form.id),visibility:Some("private".into()),acl:Some(form.acl.clone()),data:json!({"form_id":form.id,"submission_key":key,"values":normalized,"anonymous":anonymous,"status":if form.data.get("table_id").and_then(Value::as_str).is_some(){"pending"}else{"completed"}})}).await;
    let submission = match submission {
        Ok(s) => s,
        Err(err) => {
            // A concurrent duplicate may have won the unique idempotency index.
            if let Some(id)=sqlx::query_scalar::<_,Uuid>("SELECT id FROM resources WHERE kind='submission' AND data->>'form_id'=$1 AND data->>'submission_key'=$2 AND deleted_at IS NULL LIMIT 1").bind(form.id.to_string()).bind(&key).fetch_optional(&state.pool).await.ok().flatten(){return Ok(json!({"id":id,"created":false,"duplicate":true}));}
            return Err(err);
        }
    };
    if let Some(table_id) = form.data.get("table_id").and_then(Value::as_str) {
        repo::enqueue(
            &state.pool,
            owner,
            "business.form.materialize",
            json!({"actor_id":owner.id,"submission_id":submission.id,"table_id":table_id}),
            Some(&format!("submission-record:{}", submission.id)),
        )
        .await?;
    }
    repo::audit(
        &state.pool,
        owner,
        "form.submitted",
        Some(submission.id),
        json!({"form_id":form.id,"anonymous":anonymous}),
    )
    .await?;
    crate::workflows::emit_event(state, "submission.created", &submission).await?;
    Ok(
        json!({"id":submission.id,"created":true,"duplicate":false,"status":submission.data["status"]}),
    )
}

pub async fn materialize(state: &AppState, job: &crate::Job) -> ApiResult<Value> {
    let actor = crate::auth::actor_for_job(state, job).await?;
    let submission = super::get_resource(
        &state,
        &actor,
        parse_id(job.payload.get("submission_id"), "submission_id")?,
    )
    .await?;
    if submission
        .data
        .get("record_id")
        .and_then(Value::as_str)
        .is_some()
    {
        return Ok(json!({"record_id":submission.data["record_id"]}));
    }
    let table = super::get_resource(
        &state,
        &actor,
        parse_id(job.payload.get("table_id"), "table_id")?,
    )
    .await?;
    ensure_write(&actor, &table)?;
    let schema = records::fields(&table.data)?;
    let values = records::normalize_values(&schema, &submission.data["values"], true)?;
    records::validate_relations(state, &actor, &schema, &values).await?;
    let existing = super::related_resources(
        state,
        &actor,
        "record",
        "submission_id",
        submission.id,
        None,
    )
    .await?
    .into_iter()
    .next();
    let record = if let Some(r) = existing {
        r
    } else {
        super::create_resource(
            &state,
            &actor,
            NewResource {
                kind: "record".into(),
                name: submission.name.clone(),
                parent_id: Some(table.id),
                visibility: Some(table.visibility.clone()),
                acl: Some(table.acl.clone()),
                data: json!({"table_id":table.id,"values":values,"submission_id":submission.id}),
            },
        )
        .await?
    };
    let mut data = submission.data.clone();
    data["status"] = json!("completed");
    data["record_id"] = json!(record.id);
    super::update_resource(
        &state,
        &actor,
        submission.id,
        modification(&submission, data),
    )
    .await?;
    crate::workflows::emit_event(state, "record.created", &record).await?;
    Ok(json!({"record_id":record.id}))
}

pub async fn submissions(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Vec<Resource>>> {
    let form = super::get_resource(&state, &actor, id).await?;
    if form.kind != "form" {
        return Err(ApiError::not_found("Form not found."));
    }
    ensure_write(&actor, &form)?;
    Ok(Json(
        super::related_resources(&state, &actor, "submission", "form_id", id, None).await?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn conditional_fields_are_validated_on_the_server() {
        let form = json!({"fields":[{"id":"kind","name":"Kind","type":"select","options":["person","company"]},{"id":"company","name":"Company","type":"text","required":true}],"conditions":[{"target":"company","action":"show","mode":"all","when":[{"field":"kind","operator":"eq","value":"company"}]}]});
        let (f, v) = active_values(
            &form,
            &json!({"kind":"person","company":"injected hidden value"}),
        )
        .unwrap();
        assert!(records::normalize_values(&f, &v, true).is_ok());
        assert!(v.get("company").is_none());
        let (f, v) = active_values(&form, &json!({"kind":"company"})).unwrap();
        assert!(records::normalize_values(&f, &v, true).is_err());
    }
    #[test]
    fn unknown_condition_references_are_not_accepted() {
        let f = records::fields(&json!({"fields":[{"id":"a","name":"A","type":"text"}]})).unwrap();
        assert!(
            validate_conditions(
                &f,
                &json!([{"target":"a","action":"show","when":[{"field":"missing","value":1}]}])
            )
            .is_err()
        );
    }
}
