use super::{ensure_write, modification, parse_id, provider_error};
use crate::{Actor, ApiError, ApiResult, AppState, Job, NewResource, Resource, repo};
use axum::{
    Extension, Json,
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, header},
    response::{IntoResponse, Response},
};
use chrono::{Duration, Utc};
use hmac::{Hmac, Mac};
use serde::Deserialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{Cursor, Write};
use uuid::Uuid;
use zip::{ZipWriter, write::SimpleFileOptions};

#[derive(Deserialize)]
pub struct GenerateInput {
    pub name: String,
    pub template: String,
    pub values: Value,
    #[serde(default = "default_format")]
    pub format: String,
    pub parent_id: Option<Uuid>,
}
fn default_format() -> String {
    "docx".into()
}

fn public_provider_state(mut value: Value) -> Value {
    fn remove_secrets(value: &mut Value) {
        match value {
            Value::Object(object) => {
                for key in ["token", "signingUrl", "signingUrls", "apiKey", "directLink"] {
                    object.remove(key);
                }
                for child in object.values_mut() {
                    remove_secrets(child);
                }
            }
            Value::Array(array) => {
                for child in array {
                    remove_secrets(child);
                }
            }
            _ => {}
        }
    }
    remove_secrets(&mut value);
    value
}

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}
fn paragraph(text: &str, style: Option<&str>) -> String {
    format!(
        "<w:p>{}<w:r><w:t xml:space=\"preserve\">{}</w:t></w:r></w:p>",
        style
            .map(|s| format!("<w:pPr><w:pStyle w:val=\"{s}\"/></w:pPr>"))
            .unwrap_or_default(),
        xml_escape(text)
    )
}

pub fn render_template(template: &str, values: &Value) -> ApiResult<String> {
    if template.len() > 1_000_000 || !values.is_object() || values.to_string().len() > 2_000_000 {
        return Err(ApiError::bad_request(
            "Template and input exceed the document generation limits.",
        ));
    }
    // No loader or arbitrary callbacks are installed. Bound collections before
    // rendering; reject unbounded constructors and recursive template macros.
    if regex::Regex::new(r"\b(range|cycler|joiner|namespace)\s*\(")
        .unwrap()
        .is_match(template)
        || template.contains("{% macro")
        || template.contains("recursive")
        || template.contains("{% include")
        || template.contains("{% import")
        || template.contains("{% extends")
    {
        return Err(ApiError::bad_request(
            "Document templates cannot load files or create unbounded loops.",
        ));
    }
    fn bounded(v: &Value, depth: usize) -> bool {
        if depth > 12 {
            return false;
        }
        match v {
            Value::Array(a) => a.len() <= 1000 && a.iter().all(|v| bounded(v, depth + 1)),
            Value::Object(o) => o.len() <= 500 && o.values().all(|v| bounded(v, depth + 1)),
            _ => true,
        }
    }
    if !bounded(values, 0) {
        return Err(ApiError::bad_request(
            "Document input is too deeply nested or contains too many rows.",
        ));
    }
    let mut env = minijinja::Environment::new();
    env.set_fuel(Some(100_000));
    env.set_undefined_behavior(minijinja::UndefinedBehavior::Strict);
    env.add_template("document", template)
        .map_err(|_| ApiError::bad_request("Template syntax is invalid."))?;
    let rendered = env
        .get_template("document")
        .unwrap()
        .render(values)
        .map_err(|_| {
            ApiError::bad_request("Template values are missing or incompatible with the template.")
        })?;
    if rendered.len() > 8_000_000 {
        return Err(ApiError::bad_request("Generated document is too large."));
    }
    Ok(rendered)
}

pub fn docx_from_markdown(text: &str) -> ApiResult<Vec<u8>> {
    docx_with_images(text, &[])
}

struct DocumentImage {
    source: Uuid,
    bytes: Vec<u8>,
    extension: &'static str,
    width: u32,
    height: u32,
}

fn image_size(bytes: &[u8]) -> ApiResult<(&'static str, u32, u32)> {
    if bytes.starts_with(b"\x89PNG\r\n\x1a\n") && bytes.len() >= 24 {
        let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
        let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
        if width > 0 && height > 0 && (width as u64) * (height as u64) <= 40_000_000 {
            return Ok(("png", width, height));
        }
    }
    if bytes.starts_with(&[0xff, 0xd8]) {
        let mut offset = 2;
        while offset + 9 < bytes.len().min(128 * 1024) {
            if bytes[offset] != 0xff {
                offset += 1;
                continue;
            }
            let marker = bytes[offset + 1];
            if marker == 0xda || marker == 0xd9 {
                break;
            }
            if marker == 0x00 || marker == 0xd8 || (0xd0..=0xd7).contains(&marker) {
                offset += 2;
                continue;
            }
            let len = u16::from_be_bytes([bytes[offset + 2], bytes[offset + 3]]) as usize;
            if len < 2 || offset + 2 + len > bytes.len() {
                break;
            }
            if [0xc0, 0xc1, 0xc2].contains(&marker) {
                let height = u16::from_be_bytes([bytes[offset + 5], bytes[offset + 6]]) as u32;
                let width = u16::from_be_bytes([bytes[offset + 7], bytes[offset + 8]]) as u32;
                if width > 0 && height > 0 && (width as u64) * (height as u64) <= 40_000_000 {
                    return Ok(("jpeg", width, height));
                }
                break;
            }
            offset += len + 2;
        }
    }
    Err(ApiError::bad_request(
        "Document images must be valid PNG or JPEG files below 40 megapixels.",
    ))
}

async fn document_images(
    state: &AppState,
    actor: &Actor,
    text: &str,
) -> ApiResult<Vec<DocumentImage>> {
    let pattern = regex::Regex::new(r"!\[[^\]]*\]\(file:([0-9a-fA-F-]{36})\)").unwrap();
    let mut images = Vec::new();
    for capture in pattern.captures_iter(text) {
        let id = Uuid::parse_str(&capture[1])
            .map_err(|_| ApiError::bad_request("Invalid document image reference."))?;
        if images.iter().any(|i: &DocumentImage| i.source == id) {
            continue;
        }
        if images.len() >= 10 {
            return Err(ApiError::bad_request(
                "A generated document may contain at most ten images.",
            ));
        }
        let source = super::get_resource(state, actor, id).await?;
        crate::content::authorize_action(state, actor, &source, "integration").await?;
        let bytes = crate::content::read_file_bytes(state, actor, id).await?;
        if bytes.len() > 10 * 1024 * 1024 {
            return Err(ApiError::bad_request(
                "Document images are limited to 10 MiB each.",
            ));
        }
        let (extension, width, height) = image_size(&bytes)?;
        images.push(DocumentImage {
            source: id,
            bytes,
            extension,
            width,
            height,
        });
    }
    Ok(images)
}

fn image_paragraph(image: &DocumentImage, index: usize) -> String {
    let max_width = 5_486_400f64;
    let width = (image.width as f64 * 9525.0).min(max_width);
    let height = width * image.height as f64 / image.width as f64;
    let (cx, cy) = (width as u64, height.min(8_229_600.0) as u64);
    let drawing_id = index + 1;
    format!(
        "<w:p><w:r><w:drawing><wp:inline distT=\"0\" distB=\"0\" distL=\"0\" distR=\"0\"><wp:extent cx=\"{cx}\" cy=\"{cy}\"/><wp:docPr id=\"{drawing_id}\" name=\"Image {drawing_id}\"/><a:graphic><a:graphicData uri=\"http://schemas.openxmlformats.org/drawingml/2006/picture\"><pic:pic><pic:nvPicPr><pic:cNvPr id=\"0\" name=\"image{index}.{}\"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed=\"rIdImage{index}\"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"{cx}\" cy=\"{cy}\"/></a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>",
        image.extension
    )
}

fn docx_with_images(text: &str, images: &[DocumentImage]) -> ApiResult<Vec<u8>> {
    let mut body = String::new();
    let lines = text.lines().collect::<Vec<_>>();
    let mut idx = 0;
    while idx < lines.len() {
        let line = lines[idx];
        let picture = regex::Regex::new(r"^\s*!\[[^\]]*\]\(file:([0-9a-fA-F-]{36})\)\s*$").unwrap();
        if let Some(capture) = picture.captures(line) {
            let source = Uuid::parse_str(&capture[1])
                .map_err(|_| ApiError::bad_request("Invalid document image."))?;
            let (index, image) = images
                .iter()
                .enumerate()
                .find(|(_, i)| i.source == source)
                .ok_or_else(|| ApiError::bad_request("Document image was not loaded."))?;
            body.push_str(&image_paragraph(image, index));
            idx += 1;
            continue;
        }
        if line.trim_start().starts_with('|') && line.trim_end().ends_with('|') {
            body.push_str("<w:tbl><w:tblPr><w:tblBorders><w:top w:val=\"single\" w:sz=\"4\"/><w:left w:val=\"single\" w:sz=\"4\"/><w:bottom w:val=\"single\" w:sz=\"4\"/><w:right w:val=\"single\" w:sz=\"4\"/><w:insideH w:val=\"single\" w:sz=\"4\"/><w:insideV w:val=\"single\" w:sz=\"4\"/></w:tblBorders></w:tblPr>");
            while idx < lines.len()
                && lines[idx].trim_start().starts_with('|')
                && lines[idx].trim_end().ends_with('|')
            {
                let cells = lines[idx]
                    .trim()
                    .trim_matches('|')
                    .split('|')
                    .map(str::trim)
                    .collect::<Vec<_>>();
                if !cells
                    .iter()
                    .all(|c| c.chars().all(|x| matches!(x, '-' | ':' | ' ')))
                {
                    body.push_str("<w:tr>");
                    for cell in cells {
                        body.push_str(
                            "<w:tc><w:tcPr><w:tcW w:w=\"2400\" w:type=\"dxa\"/></w:tcPr>",
                        );
                        body.push_str(&paragraph(cell, None));
                        body.push_str("</w:tc>");
                    }
                    body.push_str("</w:tr>");
                }
                idx += 1;
            }
            body.push_str("</w:tbl>");
            continue;
        }
        if let Some(s) = line.strip_prefix("### ") {
            body.push_str(&paragraph(s, Some("Heading3")));
        } else if let Some(s) = line.strip_prefix("## ") {
            body.push_str(&paragraph(s, Some("Heading2")));
        } else if let Some(s) = line.strip_prefix("# ") {
            body.push_str(&paragraph(s, Some("Heading1")));
        } else {
            body.push_str(&paragraph(line, None));
        }
        idx += 1;
    }
    body.push_str("<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/><w:pgMar w:top=\"1134\" w:right=\"1134\" w:bottom=\"1134\" w:left=\"1134\"/></w:sectPr>");
    let document = format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?><w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" xmlns:wp=\"http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing\" xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" xmlns:pic=\"http://schemas.openxmlformats.org/drawingml/2006/picture\"><w:body>{body}</w:body></w:document>"
    );
    let mut writer = ZipWriter::new(Cursor::new(Vec::new()));
    let options = SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);
    let content_types = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Default Extension=\"png\" ContentType=\"image/png\"/><Default Extension=\"jpeg\" ContentType=\"image/jpeg\"/><Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/><Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/></Types>";
    let mut relationships="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>".to_owned();
    for (index, image) in images.iter().enumerate() {
        relationships.push_str(&format!("<Relationship Id=\"rIdImage{index}\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image\" Target=\"media/image{index}.{}\"/>",image.extension));
    }
    relationships.push_str("</Relationships>");
    for (name, content) in [
        ("[Content_Types].xml", content_types),
        (
            "_rels/.rels",
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument\" Target=\"word/document.xml\"/></Relationships>",
        ),
        ("word/_rels/document.xml.rels", relationships.as_str()),
        (
            "word/styles.xml",
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?><w:styles xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii=\"Calibri\" w:hAnsi=\"Calibri\" w:eastAsia=\"Noto Sans CJK TC\"/><w:sz w:val=\"22\"/></w:rPr></w:rPrDefault></w:docDefaults><w:style w:type=\"paragraph\" w:styleId=\"Heading1\"><w:name w:val=\"heading 1\"/><w:rPr><w:b/><w:sz w:val=\"36\"/></w:rPr></w:style><w:style w:type=\"paragraph\" w:styleId=\"Heading2\"><w:name w:val=\"heading 2\"/><w:rPr><w:b/><w:sz w:val=\"30\"/></w:rPr></w:style><w:style w:type=\"paragraph\" w:styleId=\"Heading3\"><w:name w:val=\"heading 3\"/><w:rPr><w:b/><w:sz w:val=\"26\"/></w:rPr></w:style></w:styles>",
        ),
        ("word/document.xml", document.as_str()),
    ] {
        writer
            .start_file(name, options)
            .map_err(|_| ApiError::internal("Could not create document package."))?;
        writer
            .write_all(content.as_bytes())
            .map_err(|_| ApiError::internal("Could not write document package."))?;
    }
    for (index, image) in images.iter().enumerate() {
        writer
            .start_file(
                format!("word/media/image{index}.{}", image.extension),
                options,
            )
            .map_err(|_| ApiError::internal("Could not create document image."))?;
        writer
            .write_all(&image.bytes)
            .map_err(|_| ApiError::internal("Could not write document image."))?;
    }
    Ok(writer
        .finish()
        .map_err(|_| ApiError::internal("Could not finish document package."))?
        .into_inner())
}

pub async fn generate(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(input): Json<GenerateInput>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("business:write")?;
    actor.require_scope("content:write")?;
    if !["docx", "pdf"].contains(&input.format.as_str()) {
        return Err(ApiError::bad_request(
            "Document format must be docx or pdf.",
        ));
    }
    if input.name.trim().is_empty() || input.name.len() > 240 {
        return Err(ApiError::bad_request(
            "Document name must contain 1-240 bytes.",
        ));
    }
    if let Some(parent) = input.parent_id {
        let r = super::get_resource(&state, &actor, parent).await?;
        ensure_write(&actor, &r)?;
    }
    let rendered = render_template(&input.template, &input.values)?;
    let images = document_images(&state, &actor, &rendered).await?;
    let docx = if images.is_empty() {
        docx_from_markdown(&rendered)?
    } else {
        docx_with_images(&rendered, &images)?
    };
    let (bytes, mime) = if input.format == "pdf" {
        let url=state.config.services.get("gotenberg").ok_or_else(||ApiError::unavailable("Configure Gotenberg to generate PDF documents. DOCX generation works without an external service."))?;
        let form = reqwest::multipart::Form::new().part(
            "files",
            reqwest::multipart::Part::bytes(docx)
                .file_name("document.docx")
                .mime_str("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                .map_err(|_| ApiError::internal("Invalid document MIME type."))?,
        );
        let response = state
            .http
            .post(format!(
                "{}/forms/libreoffice/convert",
                url.trim_end_matches('/')
            ))
            .multipart(form)
            .timeout(std::time::Duration::from_secs(90))
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(provider_error("PDF conversion"));
        }
        let bytes = response.bytes().await?;
        if bytes.len() > 100 * 1024 * 1024 || !bytes.starts_with(b"%PDF-") {
            return Err(provider_error("PDF conversion output"));
        }
        (bytes.to_vec(), "application/pdf")
    } else {
        (
            docx,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    };
    let name = if input.name.ends_with(&format!(".{}", input.format)) {
        input.name.clone()
    } else {
        format!("{}.{}", input.name, input.format)
    };
    let file =
        crate::content::store_bytes(&state, &actor, &name, mime, &bytes, input.parent_id).await?;
    repo::audit(&state.pool,&actor,"document.generated",Some(file.id),json!({"format":input.format,"template_sha256":hex::encode(Sha256::digest(input.template.as_bytes()))})).await?;
    crate::workflows::emit_event(&state, "file.created", &file).await?;
    Ok(Json(file))
}

fn service(state: &AppState) -> ApiResult<(String, String)> {
    let base=state.config.services.get("documenso").cloned().ok_or_else(||ApiError::unavailable("Configure the self-hosted Documenso service and API token to use electronic signing."))?;
    let token = state
        .config
        .services
        .get("documenso_api_token")
        .cloned()
        .or_else(|| std::env::var("BLUEPRINT_DOCUMENSO_API_TOKEN").ok())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| {
            ApiError::unavailable(
                "Configure BLUEPRINT_DOCUMENSO_API_TOKEN using a Community Edition API token.",
            )
        })?;
    Ok((format!("{}/api/v2", base.trim_end_matches('/')), token))
}

#[derive(Deserialize)]
pub struct SignInput {
    pub name: String,
    pub file_id: Uuid,
    pub recipients: Vec<Value>,
    #[serde(default)]
    pub meta: Value,
    pub template_id: Option<Uuid>,
}

pub fn normalized_recipients(input: &[Value]) -> ApiResult<Vec<Value>> {
    if input.is_empty() || input.len() > 100 {
        return Err(ApiError::bad_request(
            "A signature request needs 1-100 recipients.",
        ));
    }
    let mut output = Vec::new();
    let mut signer_index = 0;
    for r in input {
        let email = r
            .get("email")
            .and_then(Value::as_str)
            .filter(|s| s.len() <= 320 && s.contains('@') && !s.contains(['\r', '\n', ' ']))
            .ok_or_else(|| ApiError::bad_request("Recipients need a valid email address."))?;
        let name = r
            .get("name")
            .and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty() && s.len() <= 255)
            .ok_or_else(|| ApiError::bad_request("Recipients need a name."))?;
        let role = r.get("role").and_then(Value::as_str).unwrap_or("SIGNER");
        if !["SIGNER", "APPROVER", "CC", "VIEWER"].contains(&role) {
            return Err(ApiError::bad_request("Unsupported recipient role."));
        }
        let fields = if let Some(fields) = r.get("fields") {
            let fields = fields
                .as_array()
                .ok_or_else(|| ApiError::bad_request("Recipient fields must be an array."))?;
            if fields.len() > 200 {
                return Err(ApiError::bad_request("Too many signature fields."));
            }
            for f in fields {
                if ![
                    "SIGNATURE",
                    "INITIALS",
                    "NAME",
                    "EMAIL",
                    "DATE",
                    "TEXT",
                    "NUMBER",
                    "CHECKBOX",
                    "RADIO",
                    "DROPDOWN",
                ]
                .contains(&f.get("type").and_then(Value::as_str).unwrap_or(""))
                {
                    return Err(ApiError::bad_request("Unsupported signature field type."));
                }
                let page = f.get("page").and_then(Value::as_u64).unwrap_or(0);
                if page == 0 || page > 10000 {
                    return Err(ApiError::bad_request(
                        "Signature field page must be positive.",
                    ));
                }
                for key in ["positionX", "positionY", "width", "height"] {
                    if !f
                        .get(key)
                        .and_then(Value::as_f64)
                        .is_some_and(|x| x.is_finite() && (0.0..=100.0).contains(&x))
                    {
                        return Err(ApiError::bad_request(
                            "Signature field positions must be percentages between 0 and 100.",
                        ));
                    }
                }
                if f["positionX"].as_f64().unwrap() + f["width"].as_f64().unwrap() > 100.0
                    || f["positionY"].as_f64().unwrap() + f["height"].as_f64().unwrap() > 100.0
                {
                    return Err(ApiError::bad_request(
                        "Signature fields must fit inside their PDF page.",
                    ));
                }
            }
            fields.clone()
        } else if role == "SIGNER" {
            if signer_index >= 8 {
                return Err(ApiError::bad_request(
                    "For more than eight signers, provide explicit PDF field positions.",
                ));
            }
            let x = 10.0 + (signer_index % 2) as f64 * 45.0;
            let y = 60.0 + (signer_index / 2) as f64 * 8.0;
            signer_index += 1;
            vec![
                json!({"identifier":0,"type":"SIGNATURE","page":1,"positionX":x,"positionY":y,"width":35.0,"height":6.0}),
            ]
        } else {
            vec![]
        };
        let mut recipient = json!({"name":name,"email":email,"role":role,"fields":fields});
        if let Some(order) = r.get("signingOrder").and_then(Value::as_u64) {
            if order == 0 || order > 100 {
                return Err(ApiError::bad_request(
                    "Signing order must be between 1 and 100.",
                ));
            }
            recipient["signingOrder"] = json!(order);
        }
        output.push(recipient);
    }
    Ok(output)
}

pub async fn validate(
    state: &AppState,
    actor: &Actor,
    kind: &str,
    data: &Value,
    previous: Option<&Resource>,
) -> ApiResult<()> {
    super::data_object(data)?;
    if kind == "sign_request" {
        if previous.is_none() || previous.is_some_and(|p| p.data != *data) {
            return Err(ApiError::bad_request(
                "Signature requests are managed through signing endpoints so provider and audit states cannot be forged.",
            ));
        }
    }
    if kind == "sign_template" {
        let file =
            super::get_resource(&state, actor, parse_id(data.get("file_id"), "file_id")?).await?;
        if file.kind != "file" {
            return Err(ApiError::bad_request(
                "Signature templates need an uploaded PDF.",
            ));
        }
        if let Some(recipients) = data.get("recipients") {
            normalized_recipients(
                recipients
                    .as_array()
                    .ok_or_else(|| ApiError::bad_request("recipients must be an array."))?,
            )?;
        }
    }
    Ok(())
}

pub async fn list_sign_requests(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
) -> ApiResult<Json<Vec<Resource>>> {
    Ok(Json(
        super::list_resources(&state, &actor, "sign_request", None).await?,
    ))
}

pub async fn create_sign_request(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Json(mut input): Json<SignInput>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("business:write")?;
    actor.require_scope("business:sign")?;
    service(&state)?;
    if let Some(template_id) = input.template_id {
        let template = super::get_resource(&state, &actor, template_id).await?;
        if template.kind != "sign_template" {
            return Err(ApiError::bad_request(
                "template_id must reference a signature template.",
            ));
        }
        if parse_id(template.data.get("file_id"), "template.file_id")? != input.file_id {
            return Err(ApiError::bad_request(
                "The request file must match the selected signature template.",
            ));
        }
        if input.recipients.is_empty() {
            input.recipients = template
                .data
                .get("recipients")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
        }
        if !input.meta.is_object() {
            input.meta = template.data.get("meta").cloned().unwrap_or(json!({}));
        }
    }
    let file = super::get_resource(&state, &actor, input.file_id).await?;
    if file.kind != "file" {
        return Err(ApiError::bad_request(
            "file_id must reference an uploaded PDF.",
        ));
    }
    ensure_write(&actor, &file)?;
    crate::content::authorize_action(&state, &actor, &file, "integration").await?;
    let recipients = normalized_recipients(&input.recipients)?;
    if input.name.trim().is_empty() {
        return Err(ApiError::bad_request("Signature request needs a name."));
    }
    let r=super::create_resource(&state,&actor,NewResource{kind:"sign_request".into(),name:input.name,parent_id:Some(file.id),visibility:Some("private".into()),acl:Some(json!({})),data:json!({"file_id":file.id,"file_version":file.data.get("current_version").and_then(Value::as_i64).unwrap_or(file.version),"file_resource_version":file.version,"file_sha256":file.data.get("sha256"),"recipients":recipients,"meta":input.meta,"template_id":input.template_id,"status":"queued","provider":"documenso"})}).await?;
    repo::enqueue(
        &state.pool,
        &actor,
        "business.sign.create",
        json!({"actor_id":actor.id,"request_id":r.id}),
        Some(&format!("sign-create:{}", r.id)),
    )
    .await?;
    Ok(Json(r))
}

pub async fn get_sign_request(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Resource>> {
    let r = super::get_resource(&state, &actor, id).await?;
    if r.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    Ok(Json(r))
}
pub async fn refresh_sign_request(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Job>> {
    service(&state)?;
    let r = super::get_resource(&state, &actor, id).await?;
    if r.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    ensure_write(&actor, &r)?;
    Ok(Json(
        repo::enqueue(
            &state.pool,
            &actor,
            "business.sign.refresh",
            json!({"actor_id":actor.id,"request_id":r.id}),
            Some(&format!(
                "sign-refresh:{}:{}",
                r.id,
                Utc::now().timestamp() / 10
            )),
        )
        .await?,
    ))
}

async fn set_status(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    status: &str,
    error: Option<&str>,
) -> ApiResult<Resource> {
    let r = super::get_resource(&state, actor, id).await?;
    let mut data = r.data.clone();
    data["status"] = json!(status);
    data["last_error"] = json!(error);
    super::update_resource(&state, actor, id, modification(&r, data)).await
}

pub async fn process_sign_create(state: &AppState, job: &Job) -> ApiResult<Value> {
    let actor = crate::auth::actor_for_job(state, job).await?;
    actor.require_scope("business:sign")?;
    let id = parse_id(job.payload.get("request_id"), "request_id")?;
    let request = super::get_resource(&state, &actor, id).await?;
    if request.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    ensure_write(&actor, &request)?;
    if request.data.get("status").and_then(Value::as_str) != Some("queued") {
        return Ok(json!({"request_id":id,"status":request.data["status"],"replayed":true}));
    }
    let (base, token) = service(state)?;
    let file_id = parse_id(request.data.get("file_id"), "file_id")?;
    let file = super::get_resource(&state, &actor, file_id).await?;
    crate::content::authorize_action(state, &actor, &file, "integration").await?;
    if file
        .data
        .get("current_version")
        .and_then(Value::as_i64)
        .unwrap_or(file.version)
        != request
            .data
            .get("file_version")
            .and_then(Value::as_i64)
            .unwrap_or(file.version)
    {
        set_status(
            state,
            &actor,
            id,
            "needs_review",
            Some("Source file changed after the signature request was created."),
        )
        .await?;
        return Err(ApiError::conflict(
            "Source document changed. Create a request for the desired file version.",
        ));
    }
    let bytes = crate::content::read_file_bytes(state, &actor, file_id).await?;
    if request
        .data
        .get("file_sha256")
        .and_then(Value::as_str)
        .is_some_and(|hash| hash != hex::encode(Sha256::digest(&bytes)))
    {
        set_status(state,&actor,id,"needs_review",Some("Source PDF changed while being read. No document was sent to the signing provider.")).await?;
        return Err(ApiError::conflict(
            "Source PDF changed. Create a request for the desired content version.",
        ));
    }
    if !bytes.starts_with(b"%PDF-") || bytes.len() > 25 * 1024 * 1024 {
        set_status(
            state,
            &actor,
            id,
            "failed",
            Some("Signing requires a PDF below 25 MiB."),
        )
        .await?;
        return Err(ApiError::bad_request(
            "Signing requires a PDF below 25 MiB. Generate or upload a PDF first.",
        ));
    }
    // Claim the exact queued revision we read. Reloading before this update
    // would let two workers both advance an already claimed request and send it.
    let mut claiming = request.data.clone();
    claiming["status"] = json!("creating");
    claiming["last_error"] = Value::Null;
    let request =
        super::update_resource(state, &actor, id, modification(&request, claiming)).await?;
    let payload = json!({"type":"DOCUMENT","title":request.name,"externalId":id.to_string(),"recipients":request.data["recipients"],"meta":request.data.get("meta").filter(|v|v.is_object()).cloned().unwrap_or(json!({}))});
    let multipart = reqwest::multipart::Form::new()
        .text("payload", payload.to_string())
        .part(
            "files",
            reqwest::multipart::Part::bytes(bytes)
                .file_name("document.pdf")
                .mime_str("application/pdf")
                .unwrap(),
        );
    let response = state
        .http
        .post(format!("{base}/envelope/create"))
        .header(header::AUTHORIZATION, &token)
        .multipart(multipart)
        .send()
        .await;
    let body = match response {
        Ok(r) if r.status().is_success() => match r.json::<Value>().await {
            Ok(body) if body.get("id").and_then(Value::as_str).is_some() => body,
            _ => {
                set_status(state,&actor,id,"needs_review",Some("Provider creation response was invalid. Inspect Documenso using this request's externalId before creating another request.")).await?;
                return Err(provider_error("Signing provider response"));
            }
        },
        _ => {
            set_status(state,&actor,id,"needs_review",Some("Provider creation could not be confirmed. Refresh or inspect Documenso before creating another request.")).await?;
            return Err(provider_error("Signing provider"));
        }
    };
    let provider_id = body
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| provider_error("Signing provider identifier"))?;
    let current = super::get_resource(&state, &actor, id).await?;
    let mut data = current.data.clone();
    data["provider_id"] = json!(provider_id);
    data["status"] = json!("dispatching");
    super::update_resource(&state, &actor, id, modification(&current, data)).await?;
    let response = state
        .http
        .post(format!("{base}/envelope/distribute"))
        .header(header::AUTHORIZATION, &token)
        .json(&json!({"envelopeId":provider_id}))
        .send()
        .await;
    let distribution = match response {
        Ok(r) if r.status().is_success() => r
            .json::<Value>()
            .await
            .map_err(|_| provider_error("Signing distribution response"))?,
        _ => {
            set_status(state,&actor,id,"needs_review",Some("Sending could not be confirmed. Refresh to reconcile provider status; the platform will not blindly send again.")).await?;
            return Err(provider_error("Signing distribution"));
        }
    };
    let current = super::get_resource(&state, &actor, id).await?;
    let mut data = current.data.clone();
    data["status"] = json!("pending");
    data["delivery"] = public_provider_state(distribution);
    data["last_error"] = Value::Null;
    data["next_poll_at"] = json!((Utc::now() + Duration::minutes(2)).to_rfc3339());
    let saved = super::update_resource(&state, &actor, id, modification(&current, data)).await?;
    crate::workflows::emit_event(state, "sign.sent", &saved).await?;
    Ok(json!({"request_id":id,"provider_id":provider_id,"status":"pending"}))
}

pub async fn process_sign_refresh(state: &AppState, job: &Job) -> ApiResult<Value> {
    let actor = crate::auth::actor_for_job(state, job).await?;
    let id = parse_id(job.payload.get("request_id"), "request_id")?;
    let request = super::get_resource(&state, &actor, id).await?;
    if request.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    ensure_write(&actor, &request)?;
    let provider_id=request.data.get("provider_id").and_then(Value::as_str).ok_or_else(||ApiError::conflict("Provider creation was not confirmed. Inspect the signing service using this request's externalId before creating another request."))?;
    let (base, token) = service(state)?;
    let response = state
        .http
        .get(format!("{base}/envelope/{provider_id}"))
        .header(header::AUTHORIZATION, &token)
        .send()
        .await?;
    if !response.status().is_success() {
        return Err(provider_error("Signing status"));
    }
    let body: Value = response.json().await?;
    let status = body
        .get("status")
        .and_then(Value::as_str)
        .filter(|s| ["DRAFT", "PENDING", "COMPLETED", "REJECTED", "CANCELLED"].contains(s))
        .ok_or_else(|| provider_error("Signing status"))?
        .to_ascii_lowercase();
    let mut data = request.data.clone();
    data["status"] = json!(status);
    data["provider_state"] = public_provider_state(body.clone());
    data["last_error"] = Value::Null;
    data["last_checked_at"] = json!(Utc::now());
    data["next_poll_at"] = json!((Utc::now() + Duration::minutes(2)).to_rfc3339());
    let saved = super::update_resource(&state, &actor, id, modification(&request, data)).await?;
    if status == "completed"
        && request.data.get("status").and_then(Value::as_str) != Some("completed")
    {
        crate::workflows::emit_event(state, "sign.completed", &saved).await?;
    }
    if status == "rejected"
        && request.data.get("status").and_then(Value::as_str) != Some("rejected")
    {
        crate::workflows::emit_event(state, "sign.rejected", &saved).await?;
    }
    Ok(json!({"request_id":id,"status":status}))
}

pub async fn cancel_sign_request(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Json<Resource>> {
    actor.require_scope("business:sign")?;
    let request = super::get_resource(&state, &actor, id).await?;
    ensure_write(&actor, &request)?;
    if request.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    let provider_id = request
        .data
        .get("provider_id")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::conflict("This request has no confirmed provider document."))?;
    let (base, token) = service(&state)?;
    let response = state
        .http
        .post(format!("{base}/envelope/cancel"))
        .header(header::AUTHORIZATION, token)
        .json(&json!({"envelopeId":provider_id,"reason":"Cancelled from Blueprint Studio"}))
        .send()
        .await?;
    if !response.status().is_success() {
        return Err(provider_error("Signature cancellation"));
    }
    Ok(Json(
        set_status(&state, &actor, id, "cancelled", None).await?,
    ))
}

fn constant_secret_equal(expected: &str, supplied: &str) -> bool {
    let mut mac =
        Hmac::<Sha256>::new_from_slice(expected.as_bytes()).expect("HMAC accepts arbitrary keys");
    mac.update(supplied.as_bytes());
    let expected_mac = {
        let mut h = Hmac::<Sha256>::new_from_slice(expected.as_bytes()).unwrap();
        h.update(expected.as_bytes());
        h.finalize().into_bytes()
    };
    mac.verify_slice(&expected_mac).is_ok()
}

pub async fn sign_webhook(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> ApiResult<Json<Value>> {
    let expected = std::env::var("BLUEPRINT_DOCUMENSO_WEBHOOK_SECRET")
        .ok()
        .filter(|x| !x.is_empty())
        .ok_or_else(|| ApiError::unavailable("Configure the Documenso webhook secret."))?;
    let supplied = headers
        .get("x-documenso-secret")
        .and_then(|x| x.to_str().ok())
        .unwrap_or("");
    if !constant_secret_equal(&expected, supplied) {
        return Err(ApiError::unauthorized("Invalid webhook authentication."));
    }
    let payload = body
        .get("payload")
        .or_else(|| body.get("data"))
        .unwrap_or(&body);
    let provider_id = payload
        .get("id")
        .and_then(Value::as_str)
        .ok_or_else(|| ApiError::bad_request("Webhook is missing its envelope identifier."))?;
    let row:Option<(Uuid,Uuid)>=sqlx::query_as("SELECT id,owner_id FROM resources WHERE kind='sign_request' AND data->>'provider_id'=$1 AND deleted_at IS NULL LIMIT 1").bind(provider_id).fetch_optional(&state.pool).await?;
    if let Some(id_owner) = row {
        let actor = crate::auth::actor_by_id(&state, id_owner.1).await?;
        let key = format!(
            "sign-webhook:{}",
            hex::encode(Sha256::digest(body.to_string().as_bytes()))
        );
        repo::enqueue(
            &state.pool,
            &actor,
            "business.sign.refresh",
            json!({"actor_id":actor.id,"request_id":id_owner.0}),
            Some(&key),
        )
        .await?;
    }
    Ok(Json(json!({"accepted":true})))
}

pub async fn schedule_due(state: &AppState) -> ApiResult<()> {
    let stale:Vec<(Uuid,Uuid)>=sqlx::query_as("SELECT id,owner_id FROM resources WHERE kind='sign_request' AND data->>'status'='creating' AND updated_at < now()-interval '5 minutes' AND deleted_at IS NULL ORDER BY updated_at LIMIT 20").fetch_all(&state.pool).await?;
    for (id, owner) in stale {
        let Ok(actor) = crate::auth::actor_by_id(state, owner).await else {
            continue;
        };
        set_status(state,&actor,id,"needs_review",Some("Document creation was interrupted before a provider identifier was confirmed. Inspect Documenso using this request's externalId before creating another request.")).await?;
    }
    let rows:Vec<(Uuid,Uuid)>=sqlx::query_as("SELECT id,owner_id FROM resources WHERE kind='sign_request' AND data->>'status' IN ('pending','dispatching') AND data ? 'provider_id' AND COALESCE(data->>'next_poll_at','')<=$1 AND deleted_at IS NULL ORDER BY updated_at LIMIT 20").bind(Utc::now().to_rfc3339()).fetch_all(&state.pool).await?;
    for (id, owner) in rows {
        let Ok(actor) = crate::auth::actor_by_id(state, owner).await else {
            continue;
        };
        let Ok(request) = super::get_resource(&state, &actor, id).await else {
            continue;
        };
        let mut data = request.data.clone();
        data["next_poll_at"] = json!((Utc::now() + Duration::minutes(2)).to_rfc3339());
        if super::update_resource(&state, &actor, id, modification(&request, data))
            .await
            .is_ok()
        {
            repo::enqueue(
                &state.pool,
                &actor,
                "business.sign.refresh",
                json!({"actor_id":owner,"request_id":id}),
                Some(&format!(
                    "sign-periodic:{}:{}",
                    id,
                    Utc::now().timestamp() / 120
                )),
            )
            .await?;
        }
    }
    Ok(())
}

async fn provider_pdf(
    state: &AppState,
    actor: &Actor,
    id: Uuid,
    evidence: bool,
) -> ApiResult<Response> {
    let request = super::get_resource(&state, actor, id).await?;
    if request.kind != "sign_request" {
        return Err(ApiError::not_found("Signature request not found."));
    }
    if request.data.get("status").and_then(Value::as_str) != Some("completed") {
        return Err(ApiError::conflict(
            "Only completed signature requests have a final signed PDF and evidence.",
        ));
    }
    let provider_id = request
        .data
        .get("provider_id")
        .and_then(Value::as_str)
        .ok_or_else(|| provider_error("Signature document"))?;
    let (base, token) = service(state)?;
    let path = if evidence {
        format!("{base}/envelope/{provider_id}/audit-log/download")
    } else {
        let response = state
            .http
            .get(format!("{base}/envelope/{provider_id}"))
            .header(header::AUTHORIZATION, &token)
            .send()
            .await?;
        if !response.status().is_success() {
            return Err(provider_error("Signature document"));
        }
        let body: Value = response.json().await?;
        let item = body
            .get("envelopeItems")
            .and_then(Value::as_array)
            .and_then(|a| a.first())
            .and_then(|i| i.get("id"))
            .and_then(Value::as_str)
            .ok_or_else(|| provider_error("Signed document item"))?;
        format!("{base}/envelope/item/{item}/download?version=signed")
    };
    let response = state
        .http
        .get(path)
        .header(header::AUTHORIZATION, token)
        .send()
        .await?;
    if !response.status().is_success() {
        return Err(provider_error("Signature PDF download"));
    }
    let bytes = response.bytes().await?;
    if !bytes.starts_with(b"%PDF-") || bytes.len() > 100 * 1024 * 1024 {
        return Err(provider_error("Signature PDF response"));
    }
    repo::audit(
        &state.pool,
        actor,
        if evidence {
            "sign.evidence_downloaded"
        } else {
            "sign.signed_pdf_downloaded"
        },
        Some(id),
        json!({"sha256":hex::encode(Sha256::digest(&bytes))}),
    )
    .await?;
    let mut headers = HeaderMap::new();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/pdf"),
    );
    headers.insert(
        header::CONTENT_DISPOSITION,
        HeaderValue::from_static(if evidence {
            "attachment; filename=signature-evidence.pdf"
        } else {
            "attachment; filename=signed-document.pdf"
        }),
    );
    headers.insert(
        header::CACHE_CONTROL,
        HeaderValue::from_static("private, no-store"),
    );
    Ok((headers, bytes).into_response())
}
pub async fn download_signed(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Response> {
    provider_pdf(&state, &actor, id, false).await
}
pub async fn download_evidence(
    State(state): State<AppState>,
    Extension(actor): Extension<Actor>,
    Path(id): Path<Uuid>,
) -> ApiResult<Response> {
    provider_pdf(&state, &actor, id, true).await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn generated_docx_is_a_real_xml_package_with_tables_and_escaped_content() {
        let text=render_template("# {{ company }}\n{% if amount > 0 %}| Item | Amount |\n|---|---|\n| Service | {{ amount }} |{% endif %}",&json!({"company":"藍圖 & Studio","amount":100})).unwrap();
        let bytes = docx_from_markdown(&text).unwrap();
        let mut zip = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
        assert!(zip.by_name("[Content_Types].xml").is_ok());
        let mut document = String::new();
        std::io::Read::read_to_string(
            &mut zip.by_name("word/document.xml").unwrap(),
            &mut document,
        )
        .unwrap();
        assert!(document.contains("藍圖 &amp; Studio"));
        assert!(document.contains("<w:tbl>"));
        assert!(document.contains("100"));
    }
    #[test]
    fn template_rejects_missing_inputs_and_unbounded_constructors() {
        assert!(render_template("{{ missing }}", &json!({})).is_err());
        assert!(
            render_template("{% for i in range(999999999) %}x{% endfor %}", &json!({})).is_err()
        );
        assert!(render_template("{% include 'secret' %}", &json!({})).is_err());
    }
    #[test]
    fn signer_fields_have_real_bounded_pdf_positions() {
        let r = normalized_recipients(&[json!({"email":"a@example.org","name":"A"})]).unwrap();
        assert_eq!(r[0]["fields"][0]["type"], "SIGNATURE");
        assert!(normalized_recipients(&[json!({"email":"a@example.org","name":"A","fields":[{"type":"SIGNATURE","page":1,"positionX":90,"positionY":90,"width":30,"height":5}]})]).is_err());
    }
    #[test]
    fn callback_secret_must_match() {
        assert!(constant_secret_equal("test-secret", "test-secret"));
        assert!(!constant_secret_equal("test-secret", "test-secreu"));
        assert!(!constant_secret_equal("test-secret", ""));
    }
    #[test]
    fn embedded_images_have_media_relationships_and_drawing_markup() {
        let source = Uuid::new_v4();
        let mut png = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR".to_vec();
        png.extend(1u32.to_be_bytes());
        png.extend(1u32.to_be_bytes());
        assert_eq!(image_size(&png).unwrap(), ("png", 1, 1));
        let image = DocumentImage {
            source,
            bytes: png.clone(),
            extension: "png",
            width: 1,
            height: 1,
        };
        let bytes =
            docx_with_images(&format!("# Company logo\n![Logo](file:{source})"), &[image]).unwrap();
        let mut package = zip::ZipArchive::new(Cursor::new(bytes)).unwrap();
        let mut media = Vec::new();
        std::io::Read::read_to_end(
            &mut package.by_name("word/media/image0.png").unwrap(),
            &mut media,
        )
        .unwrap();
        assert_eq!(media, png);
        for (path, expected) in [
            ("word/document.xml", "rIdImage0"),
            ("word/_rels/document.xml.rels", "media/image0.png"),
        ] {
            let mut text = String::new();
            std::io::Read::read_to_string(&mut package.by_name(path).unwrap(), &mut text).unwrap();
            assert!(text.contains(expected));
        }
    }
    #[test]
    fn provider_reconciliation_never_exposes_signer_bearer_links() {
        let state = public_provider_state(
            json!({"status":"PENDING","recipients":[{"email":"signer@example.org","token":"private","signingUrl":"https://sign/private","signingStatus":"NOT_SIGNED"}]}),
        );
        assert!(state["recipients"][0].get("token").is_none());
        assert!(state["recipients"][0].get("signingUrl").is_none());
        assert_eq!(state["recipients"][0]["signingStatus"], "NOT_SIGNED");
    }
}
