use super::modification;
use crate::{Actor, ApiError, ApiResult, AppState, Resource};
use axum::{
    Extension,
    extract::{
        Path, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, header},
    response::Response,
};
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use serde_json::{Value, json};
use std::{collections::HashMap, sync::OnceLock};
use tokio::sync::{Mutex, broadcast};
use uuid::Uuid;
use yrs::{
    Any, Doc, GetString, Map, ReadTxn, StateVector, Text, Transact, Update, types::ToJson,
    updates::decoder::Decode,
};

type Channels = Mutex<HashMap<Uuid, broadcast::Sender<String>>>;
fn channels() -> &'static Channels {
    static CHANNELS: OnceLock<Channels> = OnceLock::new();
    CHANNELS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn document(resource: &Resource) -> ApiResult<Doc> {
    let client_id = u32::from_le_bytes(resource.id.as_bytes()[..4].try_into().unwrap()) as u64;
    let doc = Doc::with_client_id(client_id);
    if let Some(encoded) = resource.data.get("crdt_state").and_then(Value::as_str) {
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(encoded)
            .map_err(|_| ApiError::internal("Collaborative document state is damaged."))?;
        let update = Update::decode_v1(&bytes)
            .map_err(|_| ApiError::internal("Collaborative document state is damaged."))?;
        doc.transact_mut()
            .apply_update(update)
            .map_err(|_| ApiError::internal("Could not restore collaborative document."))?;
    } else if resource.kind == "note" {
        let text = doc.get_or_insert_text("content");
        let mut txn = doc.transact_mut();
        text.insert(
            &mut txn,
            0,
            resource
                .data
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or(""),
        );
    } else {
        let map = doc.get_or_insert_map("canvas");
        let mut txn = doc.transact_mut();
        if let Some(elements) = resource.data.get("elements") {
            if let Some(array) = elements.as_array() {
                for (index, item) in array.iter().enumerate() {
                    let id = item
                        .get("id")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                        .unwrap_or_else(|| index.to_string());
                    let value = Any::from_json(&item.to_string())
                        .map_err(|_| ApiError::bad_request("Invalid canvas element."))?;
                    map.insert(&mut txn, id, value);
                }
            } else if let Some(elements) = elements.as_object() {
                for (key, item) in elements {
                    let value = Any::from_json(&item.to_string())
                        .map_err(|_| ApiError::bad_request("Invalid canvas element."))?;
                    map.insert(&mut txn, key.clone(), value);
                }
            }
        }
    }
    Ok(doc)
}

fn state_bytes(doc: &Doc) -> Vec<u8> {
    doc.transact()
        .encode_state_as_update_v1(&StateVector::default())
}
fn encode(bytes: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(bytes)
}
fn sync_message(resource: &Resource) -> ApiResult<String> {
    let doc = document(resource)?;
    Ok(
        json!({"type":"sync","update":encode(&state_bytes(&doc)),"version":resource.version})
            .to_string(),
    )
}

fn merged(resource: &Resource, update: &[u8]) -> ApiResult<Value> {
    if update.len() > 2 * 1024 * 1024 {
        return Err(ApiError::bad_request("Collaborative update is too large."));
    }
    let doc = document(resource)?;
    let decoded =
        Update::decode_v1(update).map_err(|_| ApiError::bad_request("Invalid Yjs update."))?;
    doc.transact_mut()
        .apply_update(decoded)
        .map_err(|_| ApiError::bad_request("Yjs update could not be applied."))?;
    let mut data = resource.data.clone();
    data["crdt_state"] = json!(encode(&state_bytes(&doc)));
    if resource.kind == "note" {
        let text = doc.get_or_insert_text("content");
        let content = text.get_string(&doc.transact());
        if content.len() > 1_000_000 {
            return Err(ApiError::bad_request(
                "Collaborative notes are limited to 1 MB of text.",
            ));
        }
        data["content"] = json!(content);
    } else {
        let map = doc.get_or_insert_map("canvas");
        let value = map.to_json(&doc.transact());
        let mut output = String::new();
        value.to_json(&mut output);
        let map: Value = serde_json::from_str(&output)
            .map_err(|_| ApiError::bad_request("Canvas state must contain JSON values."))?;
        let values = map
            .as_object()
            .ok_or_else(|| ApiError::bad_request("Canvas state must contain elements."))?;
        if values.len() > 10_000 {
            return Err(ApiError::bad_request(
                "Canvas supports at most 10,000 elements.",
            ));
        }
        data["elements"] = json!(values.values().cloned().collect::<Vec<_>>());
    }
    if data.to_string().len() > 4 * 1024 * 1024 {
        return Err(ApiError::bad_request(
            "Collaborative document exceeds its saved-state limit.",
        ));
    }
    Ok(data)
}

async fn initialize(
    state: &AppState,
    actor: &Actor,
    mut resource: Resource,
) -> ApiResult<Resource> {
    if resource.data.get("crdt_state").is_some() || !actor.can_write(&resource) {
        return Ok(resource);
    }
    for _ in 0..5 {
        let doc = document(&resource)?;
        let mut data = resource.data.clone();
        data["crdt_state"] = json!(encode(&state_bytes(&doc)));
        match super::update_resource(&state, actor, resource.id, modification(&resource, data))
            .await
        {
            Ok(r) => return Ok(r),
            Err(e) if e.code == "conflict" => {
                resource = super::get_resource(&state, actor, resource.id).await?;
                if resource.data.get("crdt_state").is_some() {
                    return Ok(resource);
                }
            }
            Err(e) => return Err(e),
        }
    }
    Err(ApiError::conflict(
        "Document changed while connecting. Reconnect the editor.",
    ))
}

pub async fn upgrade(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> ApiResult<Response> {
    if let Some(origin) = headers.get(header::ORIGIN) {
        let valid = origin
            .to_str()
            .ok()
            .and_then(|s| url::Url::parse(s).ok())
            .zip(url::Url::parse(&state.config.public_url).ok())
            .is_some_and(|(a, b)| a.origin() == b.origin());
        if !valid {
            return Err(ApiError::forbidden(
                "Cross-origin collaborative connection rejected.",
            ));
        }
    }
    let resource = super::get_resource(&state, &actor, id).await?;
    if !["note", "canvas"].contains(&resource.kind.as_str()) {
        return Err(ApiError::bad_request(
            "Only notes and canvases support collaborative editing.",
        ));
    }
    if resource.deleted_at.is_some() {
        return Err(ApiError::not_found("Document is deleted."));
    }
    crate::core::ensure_module(&state, &resource.kind).await?;
    let resource = initialize(&state, &actor, resource).await?;
    let initial = sync_message(&resource)?;
    Ok(ws
        .max_message_size(3 * 1024 * 1024)
        .on_upgrade(move |socket| session(socket, state, actor, headers, resource, initial)))
}

async fn actor_for_socket(
    state: &AppState,
    headers: &HeaderMap,
    initial: &Actor,
) -> ApiResult<Actor> {
    let current = crate::auth::optional_actor(state, headers)
        .await?
        .ok_or_else(|| ApiError::unauthorized("Collaborative session expired. Sign in again."))?;
    if current.id != initial.id {
        return Err(ApiError::unauthorized("Collaborative identity changed."));
    }
    Ok(current)
}

async fn save_update(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    update: &[u8],
) -> ApiResult<Resource> {
    for _ in 0..8 {
        let resource = super::get_resource(&state, actor, id).await?;
        if !actor.can_write(&resource) {
            return Err(ApiError::forbidden("This document is read-only."));
        }
        crate::core::ensure_module(state, &resource.kind).await?;
        if resource.deleted_at.is_some() {
            return Err(ApiError::not_found("Document is deleted."));
        }
        let data = merged(&resource, update)?;
        match super::update_resource(&state, actor, id, modification(&resource, data)).await {
            Ok(r) => return Ok(r),
            Err(e) if e.code == "conflict" => continue,
            Err(e) => return Err(e),
        }
    }
    Err(ApiError::conflict(
        "The document is busy. Reconnect to merge the latest saved state.",
    ))
}

async fn session(
    socket: WebSocket,
    state: AppState,
    initial_actor: Actor,
    headers: HeaderMap,
    resource: Resource,
    initial: String,
) {
    let (sender, mut receiver) = {
        let mut map = channels().lock().await;
        let tx = map
            .entry(resource.id)
            .or_insert_with(|| broadcast::channel(256).0);
        (tx.clone(), tx.subscribe())
    };
    let (mut sink, mut stream) = socket.split();
    if sink.send(Message::Text(initial.into())).await.is_err() {
        return;
    }
    let mut last_version = resource.version;
    let mut interval = tokio::time::interval(std::time::Duration::from_secs(5));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            incoming=stream.next()=>{
                let Some(Ok(message))=incoming else{break};
                if matches!(message,Message::Close(_)){break;}
                if let Message::Ping(bytes)=message{if sink.send(Message::Pong(bytes)).await.is_err(){break;}continue;}
                let input=match message{Message::Text(text)=>serde_json::from_str::<Value>(&text).ok(),Message::Binary(bytes)=>Some(json!({"type":"update","update":encode(&bytes)})),_=>None};
                let Some(input)=input else{let _=sink.send(Message::Text(json!({"type":"error","message":"Invalid editor message."}).to_string().into())).await;continue};
                if input.get("type").and_then(Value::as_str)!=Some("update"){continue;}
                let update=input.get("update").and_then(Value::as_str).and_then(|s|base64::engine::general_purpose::STANDARD.decode(s).ok());let Some(update)=update else{continue};
                let actor=match actor_for_socket(&state,&headers,&initial_actor).await{Ok(a)=>a,Err(_)=>{let _=sink.send(Message::Close(None)).await;break;}};
                match save_update(&state,&actor,resource.id,&update).await{
                    Ok(saved)=>{last_version=saved.version;let event=json!({"type":"update","update":encode(&update),"version":saved.version,"actor_id":actor.id}).to_string();let _=sender.send(event);},
                    Err(error)=>{let _=sink.send(Message::Text(json!({"type":"error","code":error.code,"message":error.message}).to_string().into())).await;}
                }
            }
            received=receiver.recv()=>{match received{Ok(message)=>{
                let actor=match actor_for_socket(&state,&headers,&initial_actor).await{Ok(actor)=>actor,Err(_)=>{let _=sink.send(Message::Close(None)).await;break;}};
                match super::get_resource(&state,&actor,resource.id).await{Ok(current)if current.deleted_at.is_none()=>{},_=>{let _=sink.send(Message::Close(None)).await;break;}}
                if sink.send(Message::Text(message.into())).await.is_err(){break;}
            },Err(broadcast::error::RecvError::Lagged(_))=>{last_version=-1;},Err(_)=>break}},
            _=interval.tick()=>{
                let actor=match actor_for_socket(&state,&headers,&initial_actor).await{Ok(a)=>a,Err(_)=>{let _=sink.send(Message::Close(None)).await;break;}};
                match super::get_resource(&state,&actor,resource.id).await{
                    Ok(current)if current.deleted_at.is_none()=>{if crate::core::ensure_module(&state,&current.kind).await.is_err(){let _=sink.send(Message::Close(None)).await;break;}if current.version!=last_version{if let Ok(message)=sync_message(&current){if sink.send(Message::Text(message.into())).await.is_err(){break;}last_version=current.version;}}},
                    _=>{let _=sink.send(Message::Close(None)).await;break;}
                }
            }
        }
    }
    drop(receiver);
    let mut map = channels().lock().await;
    if map
        .get(&resource.id)
        .is_some_and(|tx| tx.receiver_count() == 0)
    {
        map.remove(&resource.id);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn resource() -> Resource {
        Resource {
            id: Uuid::new_v4(),
            kind: "note".into(),
            name: "Test".into(),
            parent_id: None,
            owner_id: Uuid::new_v4(),
            visibility: "private".into(),
            acl: json!({}),
            data: json!({"content":"Start"}),
            version: 1,
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
            deleted_at: None,
        }
    }
    #[test]
    fn concurrent_updates_merge_instead_of_overwriting() {
        let mut resource = resource();
        let initial = document(&resource).unwrap();
        resource.data["crdt_state"] = json!(encode(&state_bytes(&initial)));
        let left = Doc::with_client_id(100);
        left.transact_mut()
            .apply_update(Update::decode_v1(&state_bytes(&initial)).unwrap())
            .unwrap();
        let right = Doc::with_client_id(200);
        right
            .transact_mut()
            .apply_update(Update::decode_v1(&state_bytes(&initial)).unwrap())
            .unwrap();
        let l = left.get_or_insert_text("content");
        l.insert(&mut left.transact_mut(), 5, " left");
        let r = right.get_or_insert_text("content");
        r.insert(&mut right.transact_mut(), 5, " right");
        resource.data = merged(&resource, &state_bytes(&left)).unwrap();
        resource.data = merged(&resource, &state_bytes(&right)).unwrap();
        let content = resource.data["content"].as_str().unwrap();
        assert!(content.contains("left"));
        assert!(content.contains("right"));
        assert_eq!(content.matches("Start").count(), 1);
    }
    #[test]
    fn invalid_binary_is_rejected_without_changing_document() {
        assert!(merged(&resource(), &[255, 255, 255]).is_err());
    }
}
