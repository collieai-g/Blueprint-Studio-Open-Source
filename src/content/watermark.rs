use super::*;
use axum::http::HeaderValue;
use base64::Engine;
const MAX_PDF: u64 = 100 * 1024 * 1024;
fn escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&#39;")
}
pub(super) fn label(actor: &Actor) -> String {
    format!(
        "{} | {} | Blueprint",
        actor.email.replace(['\r', '\n'], " "),
        Utc::now().format("%Y-%m-%d %H:%M UTC")
    )
}
async fn pdf_bytes(response: reqwest::Response) -> ApiResult<Vec<u8>> {
    if !response.status().is_success() {
        return Err(ApiError::unavailable(
            "Watermark processing service rejected the document",
        ));
    }
    let mut stream = response.bytes_stream();
    let mut bytes = Vec::new();
    while let Some(chunk) = stream.try_next().await.map_err(err)? {
        if bytes.len() as u64 + chunk.len() as u64 > MAX_PDF {
            return Err(ApiError::unavailable(
                "Watermarked preview exceeds its size limit",
            ));
        }
        bytes.extend_from_slice(&chunk);
    }
    if !bytes.starts_with(b"%PDF-") {
        return Err(ApiError::unavailable(
            "Watermark processing did not return a valid PDF",
        ));
    }
    Ok(bytes)
}
pub(super) async fn response(
    state: &AppState,
    actor: &Actor,
    r: &Resource,
    rules: &policy::PreviewRules,
) -> ApiResult<Response> {
    static LIMIT: std::sync::OnceLock<tokio::sync::Semaphore> = std::sync::OnceLock::new();
    let _permit = LIMIT
        .get_or_init(|| tokio::sync::Semaphore::new(4))
        .acquire()
        .await
        .map_err(|_| ApiError::unavailable("Preview processing is stopping"))?;
    let endpoint = state.config.services.get("gotenberg").ok_or_else(|| {
        ApiError::unavailable(
            "A document processing service must be configured for watermarked previews",
        )
    })?;
    let v = current_version(r)?;
    check_version_scan(&v)?;
    let mime = v["mime"].as_str().unwrap_or("application/octet-stream");
    let bytes = if mime == "application/pdf" {
        storage::bytes(state, version_key(&v)?, MAX_PDF).await?
    } else if let Some(p) = r
        .data
        .get("preview")
        .filter(|p| p.get("file_version") == Some(&r.data["current_version"]))
    {
        storage::bytes(
            state,
            p["storage_key"]
                .as_str()
                .ok_or_else(|| ApiError::internal("Invalid preview reference"))?,
            MAX_PDF,
        )
        .await?
    } else if mime.starts_with("text/plain")
        || matches!(
            mime,
            "image/png" | "image/jpeg" | "image/gif" | "image/webp"
        )
    {
        let source = storage::bytes(state, version_key(&v)?, 20 * 1024 * 1024).await?;
        let content = if mime.starts_with("text/plain") {
            format!("<pre>{}</pre>", escape(&String::from_utf8_lossy(&source)))
        } else {
            format!(
                "<img src=\"data:{};base64,{}\" alt=\"Document\" />",
                mime,
                base64::engine::general_purpose::STANDARD.encode(&source)
            )
        };
        let html = format!(
            "<!doctype html><html><head><meta charset=\"utf-8\"><style>body{{font:14px sans-serif}}pre{{white-space:pre-wrap;word-break:break-word}}img{{max-width:100%}}</style></head><body>{}</body></html>",
            content
        );
        let form = reqwest::multipart::Form::new().part(
            "files",
            reqwest::multipart::Part::bytes(html.into_bytes()).file_name("index.html"),
        );
        pdf_bytes(
            state
                .http
                .post(format!(
                    "{}/forms/chromium/convert/html",
                    endpoint.trim_end_matches('/')
                ))
                .multipart(form)
                .timeout(std::time::Duration::from_secs(120))
                .send()
                .await
                .map_err(|_| ApiError::unavailable("Preview conversion service is unreachable"))?,
        )
        .await?
    } else {
        return Err(ApiError::unavailable(
            "A watermarked preview is not available for this format yet",
        ));
    };
    let watermark = label(actor);
    let form = reqwest::multipart::Form::new()
        .part(
            "files",
            reqwest::multipart::Part::bytes(bytes).file_name("preview.pdf"),
        )
        .text("stampSource", "text")
        .text("stampExpression", watermark.clone())
        .text(
            "stampOptions",
            json!({"font":"Helvetica","points":"20","color":"#808080","rotation":"45","opacity":"0.25"})
                .to_string(),
        );
    let stamped = pdf_bytes(
        state
            .http
            .post(format!(
                "{}/forms/pdfengines/stamp",
                endpoint.trim_end_matches('/')
            ))
            .multipart(form)
            .timeout(std::time::Duration::from_secs(120))
            .send()
            .await
            .map_err(|_| ApiError::unavailable("Watermark service is unreachable"))?,
    )
    .await?;
    content_audit(
        state,
        actor,
        "file.watermarked_preview",
        Some(r.id),
        json!({"file_version":r.data["current_version"],"prevent_print":rules.prevent_print}),
    )
    .await?;
    let mut response = Response::new(axum::body::Body::from(stamped));
    let h = response.headers_mut();
    h.insert("content-type", HeaderValue::from_static("application/pdf"));
    h.insert(
        "content-disposition",
        HeaderValue::from_static("inline; filename=\"preview.pdf\""),
    );
    h.insert(
        "cache-control",
        HeaderValue::from_static("private, no-store"),
    );
    h.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    h.insert(
        "x-blueprint-watermark",
        HeaderValue::from_str(&watermark)
            .map_err(|_| ApiError::internal("Watermark label is invalid"))?,
    );
    if rules.prevent_print {
        h.insert(
            "x-blueprint-print-restricted",
            HeaderValue::from_static("1"),
        );
    }
    Ok(response)
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn html_source_cannot_execute_markup() {
        assert_eq!(
            escape("<script>\"&'</script>"),
            "&lt;script&gt;&quot;&amp;&#39;&lt;/script&gt;"
        );
    }
}
