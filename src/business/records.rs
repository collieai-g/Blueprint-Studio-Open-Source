use super::{data_object, ensure_write, modification, parse_id};
use crate::{Actor, ApiError, ApiResult, AppState, NewResource, Resource};
use axum::{
    Extension, Json,
    extract::{Path, Query, State},
};
use chrono::{DateTime, NaiveDate, Utc};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::collections::{HashMap, HashSet};
use uuid::Uuid;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Field {
    pub id: String,
    pub name: String,
    #[serde(rename = "type", alias = "kind")]
    pub field_type: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub options: Vec<String>,
    #[serde(default)]
    pub relation_table: Option<Uuid>,
    #[serde(default)]
    pub multiple: bool,
    #[serde(default)]
    pub formula: Option<String>,
    #[serde(default)]
    pub pattern: Option<String>,
    #[serde(default)]
    pub min: Option<f64>,
    #[serde(default)]
    pub max: Option<f64>,
}

pub fn fields(data: &Value) -> ApiResult<Vec<Field>> {
    let array = data
        .get("fields")
        .and_then(Value::as_array)
        .ok_or_else(|| ApiError::bad_request("fields must be an array."))?;
    if array.len() > 200 {
        return Err(ApiError::bad_request(
            "A table or form may contain at most 200 fields.",
        ));
    }
    let fields: Vec<Field> = serde_json::from_value(Value::Array(array.clone()))
        .map_err(|_| ApiError::bad_request("Each field needs an id, name and supported type."))?;
    let identifier = regex::Regex::new(r"^[A-Za-z][A-Za-z0-9_]{0,63}$").unwrap();
    let mut seen = HashSet::new();
    for field in &fields {
        if !identifier.is_match(&field.id) || !seen.insert(&field.id) {
            return Err(ApiError::bad_request(
                "Field ids must be unique identifiers, for example customer_name.",
            ));
        }
        if field.name.trim().is_empty() || field.name.len() > 256 {
            return Err(ApiError::bad_request(
                "Field names must contain between 1 and 256 bytes.",
            ));
        }
        if ![
            "text",
            "long_text",
            "number",
            "currency",
            "checkbox",
            "date",
            "datetime",
            "select",
            "multiselect",
            "email",
            "url",
            "user",
            "file",
            "relation",
            "formula",
        ]
        .contains(&field.field_type.as_str())
        {
            return Err(ApiError::bad_request(&format!(
                "Unsupported field type: {}",
                field.field_type
            )));
        }
        if ["select", "multiselect"].contains(&field.field_type.as_str())
            && (field.options.is_empty()
                || field.options.len() > 200
                || field.options.iter().any(|x| x.is_empty() || x.len() > 256)
                || field.options.iter().collect::<HashSet<_>>().len() != field.options.len())
        {
            return Err(ApiError::bad_request(
                "Choice fields need between 1 and 200 unique, nonempty options.",
            ));
        }
        if field.field_type == "relation" && field.relation_table.is_none() {
            return Err(ApiError::bad_request(
                "A relation field needs relation_table.",
            ));
        }
        if let Some(pattern) = &field.pattern {
            if pattern.len() > 512 || regex::Regex::new(pattern).is_err() {
                return Err(ApiError::bad_request("Invalid field validation pattern."));
            }
        }
        if field.min.zip(field.max).is_some_and(|(min, max)| min > max) {
            return Err(ApiError::bad_request(
                "A field minimum cannot exceed its maximum.",
            ));
        }
        if field.field_type == "formula" {
            validate_formula(field.formula.as_deref().unwrap_or(""))?;
        }
    }
    check_formula_cycles(&fields)?;
    Ok(fields)
}

fn validate_formula(source: &str) -> ApiResult<()> {
    // Expressions are deliberately small and cannot call functions, load
    // templates, access attributes or construct comprehensions.
    if source.is_empty()
        || source.len() > 512
        || source.contains(['[', ']', '{', '}', '|', ';', '\\'])
        || source.contains("__")
        || regex::Regex::new(r"[A-Za-z_]\w*\s*\(")
            .unwrap()
            .is_match(source)
        || regex::Regex::new(r"[A-Za-z_]\w*\s*\.")
            .unwrap()
            .is_match(source)
    {
        return Err(ApiError::bad_request(
            "Formula must be a bounded arithmetic or comparison expression using field identifiers.",
        ));
    }
    minijinja::Environment::new()
        .compile_expression(source)
        .map_err(|_| ApiError::bad_request("Formula syntax is invalid."))?;
    Ok(())
}

fn check_formula_cycles(fields: &[Field]) -> ApiResult<()> {
    let names: HashSet<&str> = fields.iter().map(|f| f.id.as_str()).collect();
    let environment = minijinja::Environment::new();
    let mut deps: HashMap<&str, Vec<&str>> = HashMap::new();
    for f in fields.iter().filter(|f| f.field_type == "formula") {
        let source = f.formula.as_deref().unwrap_or("");
        let expression = environment
            .compile_expression(source)
            .map_err(|_| ApiError::bad_request("Invalid formula."))?;
        let variables = expression.undeclared_variables(true);
        if variables.iter().any(|v| !names.contains(v.as_str())) {
            return Err(ApiError::bad_request(
                "Formula references an unknown field.",
            ));
        }
        deps.insert(
            f.id.as_str(),
            fields
                .iter()
                .filter(|field| variables.contains(&field.id))
                .map(|field| field.id.as_str())
                .collect(),
        );
    }
    fn visit<'a>(
        node: &'a str,
        deps: &HashMap<&'a str, Vec<&'a str>>,
        visiting: &mut HashSet<&'a str>,
        done: &mut HashSet<&'a str>,
    ) -> bool {
        if done.contains(node) {
            return true;
        }
        if !visiting.insert(node) {
            return false;
        }
        if let Some(next) = deps.get(node) {
            for n in next {
                if deps.contains_key(n) && !visit(n, deps, visiting, done) {
                    return false;
                }
            }
        }
        visiting.remove(node);
        done.insert(node);
        true
    }
    let (mut visiting, mut done) = (HashSet::new(), HashSet::new());
    for name in deps.keys() {
        if !visit(name, &deps, &mut visiting, &mut done) {
            return Err(ApiError::bad_request(
                "Formula fields cannot contain circular references.",
            ));
        }
    }
    Ok(())
}

fn missing(value: &Value) -> bool {
    value.is_null()
        || value.as_str().is_some_and(|s| s.trim().is_empty())
        || value.as_array().is_some_and(Vec::is_empty)
}

pub fn normalize_workday(data: &mut Value, previous: Option<&Resource>) -> ApiResult<()> {
    let mut all_ids = HashSet::new();
    for key in ["outcomes", "decisions", "references", "todos"] {
        let Some(input) = data.get(key) else { continue };
        let input = input
            .as_array()
            .ok_or_else(|| ApiError::bad_request("Workday sections must be arrays."))?;
        if input.len() > 1000 {
            return Err(ApiError::bad_request(
                "Workday sections are limited to 1000 entries.",
            ));
        }
        let old = previous
            .and_then(|r| r.data.get(key))
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut entries = Vec::new();
        for item in input {
            let text = item
                .as_str()
                .or_else(|| item.get("text").and_then(Value::as_str))
                .or_else(|| item.get("title").and_then(Value::as_str))
                .filter(|s| !s.trim().is_empty() && s.len() <= 200_000)
                .ok_or_else(|| ApiError::bad_request("Workday entries need nonempty text."))?;
            let prior = item
                .get("id")
                .and_then(Value::as_str)
                .and_then(|id| {
                    old.iter()
                        .find(|o| o.get("id").and_then(Value::as_str) == Some(id))
                })
                .or_else(|| {
                    old.iter()
                        .find(|o| o.get("text").and_then(Value::as_str) == Some(text))
                });
            let id = prior
                .and_then(|p| p.get("id"))
                .and_then(Value::as_str)
                .and_then(|s| Uuid::parse_str(s).ok())
                .or_else(|| {
                    item.get("id")
                        .and_then(Value::as_str)
                        .and_then(|s| Uuid::parse_str(s).ok())
                })
                .unwrap_or_else(Uuid::new_v4);
            if !all_ids.insert(id) {
                return Err(ApiError::bad_request("Workday entry IDs must be unique."));
            }
            let mut entry = if item.is_object() {
                item.clone()
            } else {
                json!({})
            };
            entry["id"] = json!(id);
            entry["text"] = json!(text);
            if key == "todos" {
                entry["completed"] = json!(
                    item.get("completed")
                        .and_then(Value::as_bool)
                        .or_else(|| prior
                            .and_then(|p| p.get("completed"))
                            .and_then(Value::as_bool))
                        .unwrap_or(false)
                );
            }
            if let Some(url) = entry.get("url").and_then(Value::as_str) {
                if !url::Url::parse(url).is_ok_and(|u| ["http", "https"].contains(&u.scheme())) {
                    return Err(ApiError::bad_request(
                        "Workday reference URLs must use HTTP or HTTPS.",
                    ));
                }
            }
            entries.push(entry);
        }
        data[key] = json!(entries);
    }
    Ok(())
}

pub fn normalize_values(fields: &[Field], values: &Value, required: bool) -> ApiResult<Value> {
    let input = values
        .as_object()
        .ok_or_else(|| ApiError::bad_request("values must be an object."))?;
    let allowed: HashSet<&str> = fields.iter().map(|f| f.id.as_str()).collect();
    if input.keys().any(|k| !allowed.contains(k.as_str())) {
        return Err(ApiError::bad_request("values contains unknown fields."));
    }
    let mut output = input.clone();
    for field in fields {
        let value = input.get(&field.id).unwrap_or(&Value::Null);
        if field.field_type == "formula" {
            output.remove(&field.id);
            continue;
        }
        if missing(value) {
            if required && field.required {
                return Err(ApiError::bad_request(&format!(
                    "{} is required.",
                    field.name
                )));
            }
            continue;
        }
        let valid = match field.field_type.as_str() {
            "text" | "long_text" => value.as_str().is_some_and(|s| s.len() <= 200_000),
            "email" => value.as_str().is_some_and(|s| {
                s.len() <= 320
                    && s.contains('@')
                    && !s.contains(['\r', '\n', ' '])
                    && s.split('@').count() == 2
            }),
            "url" => value
                .as_str()
                .and_then(|s| url::Url::parse(s).ok())
                .is_some_and(|u| {
                    ["http", "https"].contains(&u.scheme())
                        && u.username().is_empty()
                        && u.password().is_none()
                }),
            "number" | "currency" => value.as_f64().is_some_and(|n| {
                n.is_finite()
                    && field.min.is_none_or(|m| n >= m)
                    && field.max.is_none_or(|m| n <= m)
            }),
            "checkbox" => value.is_boolean(),
            "date" => value
                .as_str()
                .is_some_and(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").is_ok()),
            "datetime" => value
                .as_str()
                .is_some_and(|s| DateTime::parse_from_rfc3339(s).is_ok()),
            "select" => value
                .as_str()
                .is_some_and(|s| field.options.iter().any(|x| x == s)),
            "multiselect" => value.as_array().is_some_and(|a| {
                a.len() <= field.options.len()
                    && a.iter().all(|v| {
                        v.as_str()
                            .is_some_and(|s| field.options.iter().any(|o| o == s))
                    })
                    && a.iter().map(Value::to_string).collect::<HashSet<_>>().len() == a.len()
            }),
            "user" | "file" | "relation" => {
                if field.multiple {
                    value.as_array().is_some_and(|a| {
                        a.len() <= 100
                            && a.iter()
                                .all(|v| v.as_str().is_some_and(|s| Uuid::parse_str(s).is_ok()))
                    })
                } else {
                    value.as_str().is_some_and(|s| Uuid::parse_str(s).is_ok())
                }
            }
            _ => false,
        };
        if !valid {
            return Err(ApiError::bad_request(&format!(
                "{} has an invalid value for {}.",
                field.name, field.field_type
            )));
        }
        if let (Some(pattern), Some(text)) = (&field.pattern, value.as_str()) {
            if !regex::Regex::new(pattern).unwrap().is_match(text) {
                return Err(ApiError::bad_request(&format!(
                    "{} does not match its validation rule.",
                    field.name
                )));
            }
        }
    }
    let mut env = minijinja::Environment::new();
    env.set_fuel(Some(10_000));
    env.set_undefined_behavior(minijinja::UndefinedBehavior::Strict);
    let mut pending: Vec<&Field> = fields
        .iter()
        .filter(|f| f.field_type == "formula")
        .collect();
    while !pending.is_empty() {
        let mut progressed = false;
        for idx in (0..pending.len()).rev() {
            let field = pending[idx];
            let source = field.formula.as_deref().unwrap_or("");
            let expr = env
                .compile_expression(source)
                .map_err(|_| ApiError::bad_request("Invalid formula."))?;
            let dependencies = expr.undeclared_variables(true);
            if pending.iter().any(|f| dependencies.contains(&f.id)) {
                continue;
            }
            let value = expr
                .eval(minijinja::Value::from_serialize(&output))
                .map_err(|_| {
                    ApiError::bad_request(&format!(
                        "Cannot calculate {}. Check referenced numeric values.",
                        field.name
                    ))
                })?;
            let value = serde_json::to_value(value)
                .map_err(|_| ApiError::bad_request("Formula produced an unsupported value."))?;
            if value.as_f64().is_some_and(|n| !n.is_finite()) {
                return Err(ApiError::bad_request("Formula result must be finite."));
            }
            output.insert(field.id.clone(), value);
            pending.remove(idx);
            progressed = true;
        }
        if !progressed {
            return Err(ApiError::bad_request(
                "Formula fields cannot contain circular references.",
            ));
        }
    }
    Ok(Value::Object(output))
}

pub(super) async fn validate_relations(
    state: &AppState,
    actor: &Actor,
    fields: &[Field],
    values: &Value,
) -> ApiResult<()> {
    for field in fields
        .iter()
        .filter(|f| ["relation", "file", "user"].contains(&f.field_type.as_str()))
    {
        let Some(value) = values.get(&field.id) else {
            continue;
        };
        if missing(value) {
            continue;
        }
        let vals: Vec<&Value> = if field.multiple {
            value.as_array().unwrap().iter().collect()
        } else {
            vec![value]
        };
        for id in vals {
            let id = parse_id(Some(id), &field.id)?;
            if field.field_type == "user" {
                let exists: bool = sqlx::query_scalar(
                    "SELECT EXISTS(SELECT 1 FROM users WHERE id=$1 AND active=true)",
                )
                .bind(id)
                .fetch_one(&state.pool)
                .await
                .map_err(|_| ApiError::internal("Could not validate member."))?;
                if !exists {
                    return Err(ApiError::bad_request("A selected member is unavailable."));
                }
            } else {
                let resource = super::get_resource(&state, actor, id).await?;
                if field.field_type == "file" && resource.kind != "file" {
                    return Err(ApiError::bad_request(
                        "File fields must reference uploaded files.",
                    ));
                }
                if field.field_type == "relation"
                    && (resource.kind != "record"
                        || resource.data.get("table_id").and_then(Value::as_str)
                            != field
                                .relation_table
                                .as_ref()
                                .map(Uuid::to_string)
                                .as_deref())
                {
                    return Err(ApiError::bad_request(
                        "A relation must reference a record in the selected table.",
                    ));
                }
            }
        }
    }
    Ok(())
}

pub async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    data_object(data)?;
    match kind {
        "table" => {
            let schema = fields(data)?;
            for f in schema.iter().filter(|f| f.field_type == "relation") {
                let related = super::get_resource(&state, actor, f.relation_table.unwrap()).await?;
                if related.kind != "table" {
                    return Err(ApiError::bad_request(
                        "relation_table must reference a table.",
                    ));
                }
            }
            if let Some(old) = previous {
                if old.data.get("fields") != data.get("fields") {
                    for record in
                        super::related_resources(state, actor, "record", "table_id", old.id, None)
                            .await?
                    {
                        normalize_values(
                            &schema,
                            record.data.get("values").unwrap_or(&json!({})),
                            true,
                        )?;
                    }
                }
            }
            if let Some(views) = data.get("views") {
                if !views.is_array() || views.as_array().unwrap().len() > 100 {
                    return Err(ApiError::bad_request(
                        "views must contain at most 100 saved views.",
                    ));
                }
            }
        }
        "record" => {
            let table =
                super::get_resource(&state, actor, parse_id(data.get("table_id"), "table_id")?)
                    .await?;
            if table.kind != "table" {
                return Err(ApiError::bad_request("table_id must reference a table."));
            }
            ensure_write(actor, &table)?;
            if previous.is_some_and(|old| old.data.get("table_id") != data.get("table_id")) {
                return Err(ApiError::bad_request(
                    "Records cannot be moved between tables by changing table_id.",
                ));
            }
            let schema = fields(&table.data)?;
            let vals = normalize_values(&schema, data.get("values").unwrap_or(&json!({})), true)?;
            validate_relations(state, actor, &schema, &vals).await?;
            // The dedicated table endpoint computes formulas. Reject forged
            // formula values when records are submitted through generic CRUD.
            for field in schema.iter().filter(|f| f.field_type == "formula") {
                if data.get("values").and_then(|v| v.get(&field.id)) != vals.get(&field.id) {
                    return Err(ApiError::bad_request(
                        "Formula values must be server-computed; use the table record endpoint.",
                    ));
                }
            }
        }
        "event" => validate_event(data)?,
        "task" => {
            if data.get("status").and_then(Value::as_str).is_some_and(|s| {
                ![
                    "open",
                    "in_progress",
                    "completed",
                    "approved",
                    "rejected",
                    "cancelled",
                ]
                .contains(&s)
            }) {
                return Err(ApiError::bad_request("Invalid task status."));
            }
            if let Some(s) = data
                .get("due_at")
                .filter(|v| !v.is_null())
                .and_then(Value::as_str)
            {
                DateTime::parse_from_rfc3339(s)
                    .map_err(|_| ApiError::bad_request("due_at must be a timestamp."))?;
            }
            if let Some(a) = data.get("assignees") {
                if !a.is_array()
                    || a.as_array().unwrap().len() > 100
                    || a.as_array()
                        .unwrap()
                        .iter()
                        .any(|v| v.as_str().is_none_or(|s| Uuid::parse_str(s).is_err()))
                {
                    return Err(ApiError::bad_request(
                        "assignees must contain member UUIDs.",
                    ));
                }
            }
        }
        "workday" => {
            let date = data
                .get("date")
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request("Workday needs a date."))?;
            NaiveDate::parse_from_str(date, "%Y-%m-%d")
                .map_err(|_| ApiError::bad_request("date must use YYYY-MM-DD."))?;
            for key in ["outcomes", "decisions", "references", "todos"] {
                if data
                    .get(key)
                    .is_some_and(|v| !v.is_array() || v.as_array().is_some_and(|a| a.len() > 1000))
                {
                    return Err(ApiError::bad_request(
                        "Workday sections must be arrays of at most 1000 items.",
                    ));
                }
            }
        }
        "note" => {
            if data
                .get("content")
                .is_some_and(|v| v.as_str().is_none_or(|s| s.len() > 2_000_000))
            {
                return Err(ApiError::bad_request("Note content is limited to 2 MB."));
            }
            if let Some(old) = previous {
                if old.data.get("crdt_state").is_some()
                    && data.get("content") != old.data.get("content")
                    && data.get("crdt_state") == old.data.get("crdt_state")
                {
                    return Err(ApiError::conflict(
                        "This note is collaboratively edited. Reconnect the editor before changing its content.",
                    ));
                }
            }
        }
        "canvas" => {
            if data
                .get("elements")
                .is_some_and(|v| !v.is_array() && !v.is_object())
            {
                return Err(ApiError::bad_request(
                    "Canvas elements must be an array or map.",
                ));
            }
            if previous.is_some_and(|old| {
                old.data.get("crdt_state").is_some()
                    && data.get("elements") != old.data.get("elements")
                    && data.get("crdt_state") == old.data.get("crdt_state")
            }) {
                return Err(ApiError::conflict(
                    "This canvas is collaboratively edited. Reconnect the editor before changing its elements.",
                ));
            }
        }
        "hub" => {
            if let Some(ids) = data.get("resource_ids") {
                let ids = ids
                    .as_array()
                    .ok_or_else(|| ApiError::bad_request("Hub resource_ids must be an array."))?;
                if ids.len() > 1000 {
                    return Err(ApiError::bad_request(
                        "A hub may contain at most 1000 selected resources.",
                    ));
                }
                for id in ids {
                    super::get_resource(&state, actor, parse_id(Some(id), "resource_id")?).await?;
                }
            }
        }
        _ => {}
    }
    Ok(())
}

pub fn validate_event(data: &Value) -> ApiResult<()> {
    let parse = |key: &str| -> ApiResult<DateTime<Utc>> {
        DateTime::parse_from_rfc3339(
            data.get(key)
                .and_then(Value::as_str)
                .ok_or_else(|| ApiError::bad_request(&format!("{key} is required.")))?,
        )
        .map(|x| x.with_timezone(&Utc))
        .map_err(|_| ApiError::bad_request(&format!("{key} must be a timestamp.")))
    };
    if parse("end")? <= parse("start")? {
        return Err(ApiError::bad_request("Event end must be after its start."));
    }
    if let Some(zone) = data.get("timezone").and_then(Value::as_str) {
        zone.parse::<chrono_tz::Tz>()
            .map_err(|_| ApiError::bad_request("timezone must be an IANA timezone."))?;
    }
    if let Some(rule) = data
        .get("rrule")
        .filter(|v| !v.is_null())
        .and_then(Value::as_str)
    {
        if rule.len() > 1024
            || rule.contains(['\r', '\n'])
            || !rule.starts_with("FREQ=")
            || !["DAILY", "WEEKLY", "MONTHLY", "YEARLY"]
                .iter()
                .any(|f| rule.starts_with(&format!("FREQ={f}")))
        {
            return Err(ApiError::bad_request(
                "rrule must be a bounded RFC5545 recurrence rule.",
            ));
        }
    }
    Ok(())
}

#[derive(Deserialize, Default)]
pub struct ListQuery {
    pub q: Option<String>,
    pub view: Option<String>,
}
pub async fn list_records(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Query(query): Query<ListQuery>,
) -> ApiResult<Json<Vec<Resource>>> {
    let table = super::get_resource(&state, &actor, id).await?;
    if table.kind != "table" {
        return Err(ApiError::not_found("Table not found."));
    }
    let schema = fields(&table.data)?;
    let mut records =
        super::related_resources(&state, &actor, "record", "table_id", id, query.q.as_deref())
            .await?;
    for record in &mut records {
        record.data["values"] = normalize_values(
            &schema,
            record.data.get("values").unwrap_or(&json!({})),
            false,
        )?;
    }
    if let Some(view_id) = query.view {
        if let Some(view) = table
            .data
            .get("views")
            .and_then(Value::as_array)
            .and_then(|vs| {
                vs.iter()
                    .find(|v| v.get("id").and_then(Value::as_str) == Some(&view_id))
            })
        {
            if let Some(filters) = view.get("filters").and_then(Value::as_array) {
                records.retain(|r| {
                    filters
                        .iter()
                        .all(|f| filter_matches(r.data.get("values").unwrap_or(&Value::Null), f))
                });
            }
            if let Some(sort) = view.get("sort").and_then(Value::as_str) {
                records.sort_by(|a, b| {
                    compare_values(&a.data["values"][sort], &b.data["values"][sort])
                });
                if view.get("descending").and_then(Value::as_bool) == Some(true) {
                    records.reverse();
                }
            }
        }
    }
    Ok(Json(records))
}

fn compare_values(left: &Value, right: &Value) -> std::cmp::Ordering {
    match (left, right) {
        (Value::Null, Value::Null) => std::cmp::Ordering::Equal,
        (Value::Null, _) => std::cmp::Ordering::Less,
        (_, Value::Null) => std::cmp::Ordering::Greater,
        (Value::Number(a), Value::Number(b)) => a
            .as_f64()
            .partial_cmp(&b.as_f64())
            .unwrap_or(std::cmp::Ordering::Equal),
        (Value::Bool(a), Value::Bool(b)) => a.cmp(b),
        (Value::String(a), Value::String(b)) => a.to_lowercase().cmp(&b.to_lowercase()),
        _ => left.to_string().cmp(&right.to_string()),
    }
}

pub fn filter_matches(values: &Value, filter: &Value) -> bool {
    let key = filter.get("field").and_then(Value::as_str).unwrap_or("");
    let left = values.get(key).unwrap_or(&Value::Null);
    let right = filter.get("value").unwrap_or(&Value::Null);
    match filter
        .get("operator")
        .and_then(Value::as_str)
        .unwrap_or("eq")
    {
        "eq" => left == right,
        "ne" => left != right,
        "empty" => missing(left),
        "not_empty" => !missing(left),
        "contains" => {
            left.as_str()
                .zip(right.as_str())
                .is_some_and(|(l, r)| l.to_lowercase().contains(&r.to_lowercase()))
                || left.as_array().is_some_and(|a| a.contains(right))
        }
        "starts_with" => left
            .as_str()
            .zip(right.as_str())
            .is_some_and(|(l, r)| l.starts_with(r)),
        "ends_with" => left
            .as_str()
            .zip(right.as_str())
            .is_some_and(|(l, r)| l.ends_with(r)),
        "gt" => left
            .as_f64()
            .zip(right.as_f64())
            .is_some_and(|(l, r)| l > r),
        "gte" => left
            .as_f64()
            .zip(right.as_f64())
            .is_some_and(|(l, r)| l >= r),
        "lt" => left
            .as_f64()
            .zip(right.as_f64())
            .is_some_and(|(l, r)| l < r),
        "lte" => left
            .as_f64()
            .zip(right.as_f64())
            .is_some_and(|(l, r)| l <= r),
        _ => false,
    }
}

#[derive(Deserialize)]
pub struct RecordInput {
    pub name: Option<String>,
    pub values: Value,
}
pub async fn create_record(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(input): Json<RecordInput>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("business:write")?;
    let table = super::get_resource(&state, &actor, id).await?;
    if table.kind != "table" {
        return Err(ApiError::not_found("Table not found."));
    }
    ensure_write(&actor, &table)?;
    let schema = fields(&table.data)?;
    let values = normalize_values(&schema, &input.values, true)?;
    validate_relations(&state, &actor, &schema, &values).await?;
    let record = super::create_resource(
        &state,
        &actor,
        NewResource {
            kind: "record".into(),
            name: input
                .name
                .filter(|s| !s.trim().is_empty())
                .unwrap_or_else(|| format!("{} record", table.name)),
            parent_id: Some(id),
            visibility: Some(table.visibility.clone()),
            acl: Some(table.acl.clone()),
            data: json!({"table_id":id,"values":values}),
        },
    )
    .await?;
    Ok(Json(record))
}

pub async fn list_views(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Value>> {
    let table = super::get_resource(&state, &actor, id).await?;
    if table.kind != "table" {
        return Err(ApiError::not_found("Table not found."));
    }
    Ok(Json(table.data.get("views").cloned().unwrap_or(json!([]))))
}
pub async fn save_view(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    Json(mut input): Json<Value>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("business:write")?;
    let table = super::get_resource(&state, &actor, id).await?;
    ensure_write(&actor, &table)?;
    if table.kind != "table" {
        return Err(ApiError::not_found("Table not found."));
    }
    let schema = fields(&table.data)?;
    let view_type = input.get("type").and_then(Value::as_str).unwrap_or("list");
    if !["list", "table", "board", "calendar", "chart"].contains(&view_type) {
        return Err(ApiError::bad_request("Unsupported view type."));
    }
    if let Some(filters) = input.get("filters") {
        for filter in filters
            .as_array()
            .ok_or_else(|| ApiError::bad_request("filters must be an array."))?
        {
            let f = filter.get("field").and_then(Value::as_str).unwrap_or("");
            if !schema.iter().any(|s| s.id == f) {
                return Err(ApiError::bad_request("View references an unknown field."));
            }
        }
    }
    if !input.is_object() {
        return Err(ApiError::bad_request("View must be an object."));
    }
    let view_id = input
        .get("id")
        .and_then(Value::as_str)
        .map(str::to_owned)
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    input["id"] = json!(view_id);
    let mut data = table.data.clone();
    let mut views = data
        .get("views")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    views.retain(|v| v.get("id").and_then(Value::as_str) != Some(&view_id));
    if views.len() >= 100 {
        return Err(ApiError::bad_request(
            "At most 100 saved views are allowed.",
        ));
    }
    views.push(input);
    data["views"] = json!(views);
    Ok(Json(
        super::update_resource(&state, &actor, id, modification(&table, data)).await?,
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn schema_rejects_cycles_and_duplicate_ids() {
        assert!(fields(&json!({"fields":[{"id":"x","name":"x","type":"formula","formula":"y + 1"},{"id":"y","name":"y","type":"formula","formula":"x + 1"}]})).is_err());
        assert!(fields(&json!({"fields":[{"id":"x","name":"x","type":"text"},{"id":"x","name":"x","type":"number"}]})).is_err());
    }
    #[test]
    fn expressions_compute_server_values_and_reject_calls() {
        let f=fields(&json!({"fields":[{"id":"qty","name":"Quantity","type":"number","required":true},{"id":"price","name":"Price","type":"currency"},{"id":"total","name":"Total","type":"formula","formula":"qty * price"}]})).unwrap();
        assert_eq!(
            normalize_values(&f, &json!({"qty":3,"price":12.5,"total":999}), true).unwrap()["total"],
            json!(37.5)
        );
        assert!(validate_formula("range(99999999)").is_err());
        assert!(normalize_values(&f, &json!({"price":1}), true).is_err());
    }
    #[test]
    fn invalid_choice_unknown_field_and_bad_relation_fail() {
        let f=fields(&json!({"fields":[{"id":"status","name":"Status","type":"select","options":["open","done"]},{"id":"client","name":"Client","type":"relation","relation_table":Uuid::new_v4()}]})).unwrap();
        assert!(normalize_values(&f, &json!({"status":"admin"}), true).is_err());
        assert!(normalize_values(&f, &json!({"extra":1}), true).is_err());
        assert!(normalize_values(&f, &json!({"client":"not-an-id"}), true).is_err());
    }
    #[test]
    fn event_uses_real_timestamps_and_iana_timezones() {
        assert!(validate_event(&json!({"start":"2026-10-20T10:00:00+08:00","end":"2026-10-20T11:00:00+08:00","timezone":"Asia/Hong_Kong","rrule":"FREQ=WEEKLY;COUNT=3"})).is_ok());
        assert!(
            validate_event(&json!({"start":"2026-10-20T11:00:00Z","end":"2026-10-20T10:00:00Z"}))
                .is_err()
        );
        assert!(validate_event(&json!({"start":"2026-10-20T10:00:00Z","end":"2026-10-20T11:00:00Z","timezone":"Not/Zone"})).is_err());
    }
    #[test]
    fn numeric_views_sort_numbers_instead_of_serialized_text() {
        assert_eq!(
            compare_values(&json!(2), &json!(10)),
            std::cmp::Ordering::Less
        );
        assert_eq!(
            compare_values(&Value::Null, &json!(0)),
            std::cmp::Ordering::Less
        );
    }
    #[test]
    fn workday_revisions_preserve_checked_todo_identity() {
        let id = Uuid::new_v4();
        let old = Resource {
            id: Uuid::new_v4(),
            kind: "workday".into(),
            name: "Day".into(),
            parent_id: None,
            owner_id: Uuid::new_v4(),
            visibility: "private".into(),
            acl: json!({}),
            data: json!({"date":"2026-10-20","todos":[{"id":id,"text":"Send quote","completed":true}]}),
            version: 1,
            created_at: Utc::now(),
            updated_at: Utc::now(),
            deleted_at: None,
        };
        let mut data = json!({"date":"2026-10-20","todos":["Send quote","Review contract"]});
        normalize_workday(&mut data, Some(&old)).unwrap();
        assert_eq!(data["todos"][0]["id"], json!(id));
        assert_eq!(data["todos"][0]["completed"], true);
        assert_eq!(data["todos"][1]["completed"], false);
    }
}
