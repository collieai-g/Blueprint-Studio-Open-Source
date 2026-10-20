use crate::{ApiError, ApiResult, AppState};
use axum::{
    body::Body,
    http::{HeaderValue, StatusCode},
    response::Response,
};
use chrono::Utc;
use futures_util::{Stream, TryStreamExt};
use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::{Path, PathBuf},
};
use tokio::{
    fs::{self, File},
    io::{AsyncReadExt, AsyncSeekExt, AsyncWriteExt},
};

pub fn limit() -> u64 {
    std::env::var("BLUEPRINT_MAX_FILE_BYTES")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1024 * 1024 * 1024)
}
pub fn valid_key(key: &str) -> bool {
    key.len() == 64 && key.bytes().all(|b| b.is_ascii_hexdigit())
}
pub fn blob_path(state: &AppState, key: &str) -> ApiResult<PathBuf> {
    if !valid_key(key) {
        return Err(ApiError::bad_request("Invalid stored object key"));
    }
    Ok(state
        .config
        .data_dir
        .join("blobs")
        .join(&key[..2])
        .join(key))
}
pub struct StagingCleanup(pub PathBuf);
impl Drop for StagingCleanup {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
pub async fn staging(state: &AppState) -> ApiResult<(PathBuf, File)> {
    let dir = state.config.data_dir.join("staging");
    fs::create_dir_all(&dir).await.map_err(internal)?;
    let path = dir.join(uuid::Uuid::new_v4().to_string());
    let file = File::create(&path).await.map_err(internal)?;
    Ok((path, file))
}
fn internal(e: impl std::fmt::Display) -> ApiError {
    tracing::error!(error=%e,"Object storage operation failed");
    ApiError::internal("Object storage operation failed")
}
fn hmac(key: &[u8], data: &str) -> Vec<u8> {
    let mut m = Hmac::<Sha256>::new_from_slice(key).expect("HMAC accepts all key lengths");
    m.update(data.as_bytes());
    m.finalize().into_bytes().to_vec()
}
struct S3 {
    endpoint: String,
    bucket: String,
    access: String,
    secret: String,
    region: String,
}
fn s3() -> ApiResult<Option<S3>> {
    let endpoint = match std::env::var("BLUEPRINT_S3_ENDPOINT") {
        Ok(s) if !s.is_empty() => s,
        _ => return Ok(None),
    };
    let required = |k: &str| {
        std::env::var(k)
            .map_err(|_| ApiError::unavailable("S3 object storage credentials are not configured"))
    };
    Ok(Some(S3 {
        endpoint,
        bucket: required("BLUEPRINT_S3_BUCKET")?,
        access: required("BLUEPRINT_S3_ACCESS_KEY")?,
        secret: required("BLUEPRINT_S3_SECRET_KEY")?,
        region: std::env::var("BLUEPRINT_S3_REGION").unwrap_or("us-east-1".into()),
    }))
}
static READY: std::sync::OnceLock<tokio::sync::Mutex<std::collections::HashSet<String>>> =
    std::sync::OnceLock::new();
pub fn storage_governance() -> bool {
    is_s3() && require_object_lock()
}
pub async fn verified_object_lock() -> bool {
    if !storage_governance() {
        return false;
    }
    let Ok(Some(conf)) = s3() else {
        return false;
    };
    READY
        .get_or_init(Default::default)
        .lock()
        .await
        .contains(&format!("{}/{}", conf.endpoint, conf.bucket))
}
pub fn is_s3() -> bool {
    std::env::var("BLUEPRINT_S3_ENDPOINT").is_ok_and(|s| !s.is_empty())
}
async fn request(
    state: &AppState,
    method: reqwest::Method,
    key: &str,
    query: &str,
    hash: &str,
    headers: BTreeMap<String, String>,
    body: Option<reqwest::Body>,
) -> ApiResult<reqwest::Response> {
    let conf = s3()?.ok_or_else(|| ApiError::unavailable("S3 is not configured"))?;
    if (!key.is_empty() && !valid_key(key))
        || conf.bucket.is_empty()
        || !conf
            .bucket
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'.')
    {
        return Err(ApiError::bad_request(
            "Invalid object storage configuration",
        ));
    }
    let target = if key.is_empty() {
        format!("{}/{}", conf.endpoint.trim_end_matches('/'), conf.bucket)
    } else {
        format!(
            "{}/{}/{}",
            conf.endpoint.trim_end_matches('/'),
            conf.bucket,
            key
        )
    };
    let mut url =
        url::Url::parse(&target).map_err(|_| ApiError::unavailable("Invalid S3 endpoint"))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(ApiError::unavailable("S3 endpoint must use HTTP or HTTPS"));
    }
    url.set_query(if query.is_empty() { None } else { Some(query) });
    let now = Utc::now();
    let timestamp = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date = now.format("%Y%m%d").to_string();
    let host = match url.port() {
        Some(p) => format!("{}:{p}", url.host_str().unwrap_or("")),
        None => url.host_str().unwrap_or("").to_string(),
    };
    let mut signed = BTreeMap::from([
        ("host".to_owned(), host),
        ("x-amz-content-sha256".to_owned(), hash.to_owned()),
        ("x-amz-date".to_owned(), timestamp.clone()),
    ]);
    for (k, v) in &headers {
        if k.starts_with("x-amz-") {
            signed.insert(k.clone(), v.clone());
        }
    }
    if let Ok(token) = std::env::var("BLUEPRINT_S3_SESSION_TOKEN") {
        signed.insert("x-amz-security-token".into(), token);
    }
    let names = signed
        .keys()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .join(";");
    let canonical = signed
        .iter()
        .map(|(k, v)| format!("{k}:{}\n", v.trim()))
        .collect::<String>();
    let canonical_request = format!(
        "{}\n{}\n{}\n{}\n{}\n{}",
        method.as_str(),
        url.path(),
        query,
        canonical,
        names,
        hash
    );
    let scope = format!("{date}/{}/s3/aws4_request", conf.region);
    let to_sign = format!(
        "AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{}",
        hex::encode(Sha256::digest(canonical_request.as_bytes()))
    );
    let date_key = hmac(format!("AWS4{}", conf.secret).as_bytes(), &date);
    let region_key = hmac(&date_key, &conf.region);
    let service_key = hmac(&region_key, "s3");
    let signing_key = hmac(&service_key, "aws4_request");
    let auth = format!(
        "AWS4-HMAC-SHA256 Credential={}/{}, SignedHeaders={}, Signature={}",
        conf.access,
        scope,
        names,
        hex::encode(hmac(&signing_key, &to_sign))
    );
    let mut req = state
        .http
        .request(method, url)
        .header("authorization", auth);
    for (k, v) in signed {
        req = req.header(k, v);
    }
    for (k, v) in headers {
        if !k.starts_with("x-amz-") {
            req = req.header(k, v);
        }
    }
    if let Some(b) = body {
        req = req.body(b);
    }
    let response = req.send().await.map_err(|e| {
        tracing::warn!(error=%e,"S3 request failed");
        ApiError::unavailable("Object storage is unreachable")
    })?;
    if !response.status().is_success() {
        let status = response.status();
        return Err(if status == reqwest::StatusCode::NOT_FOUND {
            ApiError::not_found("Stored file not found")
        } else {
            ApiError::unavailable(format!("Object storage rejected the operation ({status})"))
        });
    }
    Ok(response)
}
fn file_stream(
    file: File,
    remaining: u64,
) -> impl Stream<Item = Result<Vec<u8>, std::io::Error>> + Send + 'static {
    futures_util::stream::try_unfold((file, remaining), |(mut f, left)| async move {
        if left == 0 {
            return Ok(None);
        }
        let mut buf = vec![0u8; left.min(65536) as usize];
        let n = f.read(&mut buf).await?;
        if n == 0 {
            return Ok(None);
        }
        buf.truncate(n);
        Ok(Some((buf, (f, left - n as u64))))
    })
}
pub fn identity(state: &AppState) -> ApiResult<(String, String)> {
    if let Some(conf) = s3()? {
        Ok((
            "s3".into(),
            format!("{}/{}", conf.endpoint.trim_end_matches('/'), conf.bucket),
        ))
    } else {
        Ok((
            "local".into(),
            std::fs::canonicalize(&state.config.data_dir)
                .map_err(internal)?
                .to_string_lossy()
                .into_owned(),
        ))
    }
}
async fn pin_upload(state: &AppState, key: &str) -> ApiResult<()> {
    let (mode, location) = identity(state)?;
    let mut tx = state.pool.begin().await.map_err(internal)?;
    sqlx::query("SELECT pg_advisory_xact_lock(hashtextextended($1,871193))")
        .bind(key)
        .execute(&mut *tx)
        .await
        .map_err(internal)?;
    sqlx::query("INSERT INTO blob_upload_pins(storage_mode,storage_location,object_key,expires_at) VALUES($1,$2,$3,now()+interval '24 hours') ON CONFLICT(storage_mode,storage_location,object_key) DO UPDATE SET expires_at=EXCLUDED.expires_at").bind(mode).bind(location).bind(key).execute(&mut *tx).await.map_err(internal)?;
    tx.commit().await.map_err(internal)?;
    Ok(())
}
pub async fn delete_blob(state: &AppState, key: &str, version: Option<&str>) -> ApiResult<()> {
    if !valid_key(key) {
        return Err(ApiError::bad_request("Invalid immutable object key"));
    }
    if is_s3() {
        let version = version.ok_or_else(|| {
            ApiError::unavailable(
                "Object version receipt is missing; collection requires storage review",
            )
        })?;
        let query = url::form_urlencoded::Serializer::new(String::new())
            .append_pair("versionId", version)
            .finish();
        match request(
            state,
            reqwest::Method::DELETE,
            key,
            &query,
            &hex::encode(Sha256::digest([])),
            BTreeMap::new(),
            None,
        )
        .await
        {
            Ok(_) => Ok(()),
            Err(e) if e.status == StatusCode::NOT_FOUND => Ok(()),
            Err(e) => Err(e),
        }
    } else {
        match fs::remove_file(blob_path(state, key)?).await {
            Ok(()) => Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(e) => Err(internal(e)),
        }
    }
}
pub async fn commit(
    state: &AppState,
    path: &Path,
    key: &str,
    size: u64,
    mime: &str,
) -> ApiResult<Option<String>> {
    if !valid_key(key) {
        return Err(ApiError::bad_request("Invalid content hash"));
    }
    pin_upload(state, key).await?;
    let version = if s3()?.is_some() {
        initialize(state).await?;
        let f = File::open(path).await.map_err(internal)?;
        let headers = BTreeMap::from([
            ("content-type".into(), mime.into()),
            ("content-length".into(), size.to_string()),
        ]);
        let res = request(
            state,
            reqwest::Method::PUT,
            key,
            "",
            key,
            headers,
            Some(reqwest::Body::wrap_stream(file_stream(f, size))),
        )
        .await?;
        let version = res
            .headers()
            .get("x-amz-version-id")
            .and_then(|v| v.to_str().ok())
            .map(str::to_owned);
        if require_object_lock() && version.is_none() {
            return Err(ApiError::unavailable(
                "Object storage did not return a version ID; verify bucket versioning",
            ));
        }
        version
    } else {
        let dest = blob_path(state, key)?;
        if let Some(parent) = dest.parent() {
            fs::create_dir_all(parent).await.map_err(internal)?;
        }
        if !fs::try_exists(&dest).await.map_err(internal)? {
            fs::rename(path, &dest).await.map_err(internal)?;
        }
        None
    };
    let _ = fs::remove_file(path).await;
    Ok(version)
}
pub async fn save_bytes(
    state: &AppState,
    bytes: &[u8],
    mime: &str,
) -> ApiResult<(String, Option<String>)> {
    if bytes.len() as u64 > limit() {
        return Err(ApiError::bad_request(
            "File exceeds configured upload limit",
        ));
    }
    let key = hex::encode(Sha256::digest(bytes));
    let (path, mut f) = staging(state).await?;
    let _cleanup = StagingCleanup(path.clone());
    f.write_all(bytes).await.map_err(internal)?;
    f.flush().await.map_err(internal)?;
    drop(f);
    let version = commit(state, &path, &key, bytes.len() as u64, mime).await?;
    Ok((key, version))
}
pub async fn bytes(state: &AppState, key: &str, max: u64) -> ApiResult<Vec<u8>> {
    if s3()?.is_some() {
        let res = request(
            state,
            reqwest::Method::GET,
            key,
            "",
            &hex::encode(Sha256::digest([])),
            BTreeMap::new(),
            None,
        )
        .await?;
        if res.content_length().is_some_and(|s| s > max) {
            return Err(ApiError::bad_request(
                "Stored file exceeds processing limit",
            ));
        }
        let mut stream = res.bytes_stream();
        let mut out = Vec::new();
        while let Some(chunk) = stream.try_next().await.map_err(internal)? {
            if out.len() as u64 + chunk.len() as u64 > max {
                return Err(ApiError::bad_request(
                    "Stored file exceeds processing limit",
                ));
            }
            out.extend_from_slice(&chunk);
        }
        Ok(out)
    } else {
        let path = blob_path(state, key)?;
        if fs::metadata(&path).await.map_err(internal)?.len() > max {
            return Err(ApiError::bad_request(
                "Stored file exceeds processing limit",
            ));
        }
        fs::read(path).await.map_err(internal)
    }
}
pub async fn temporary(state: &AppState, key: &str) -> ApiResult<(PathBuf, File)> {
    if s3()?.is_none() {
        let p = blob_path(state, key)?;
        let f = File::open(&p).await.map_err(internal)?;
        return Ok((p, f));
    }
    let (path, mut f) = staging(state).await?;
    let res = request(
        state,
        reqwest::Method::GET,
        key,
        "",
        &hex::encode(Sha256::digest([])),
        BTreeMap::new(),
        None,
    )
    .await?;
    let mut stream = res.bytes_stream();
    let mut size = 0u64;
    while let Some(chunk) = stream.try_next().await.map_err(internal)? {
        size += chunk.len() as u64;
        if size > limit() {
            let _ = fs::remove_file(&path).await;
            return Err(ApiError::bad_request(
                "Stored file exceeds processing limit",
            ));
        }
        f.write_all(&chunk).await.map_err(internal)?;
    }
    f.flush().await.map_err(internal)?;
    f.rewind().await.map_err(internal)?;
    Ok((path, f))
}
pub fn parse_range(header: Option<&str>, size: u64) -> ApiResult<(u64, u64, bool)> {
    let Some(value) = header else {
        return Ok((0, size, false));
    };
    let range = value
        .strip_prefix("bytes=")
        .ok_or_else(|| ApiError::bad_request("Only byte ranges are supported"))?;
    if range.contains(',') || size == 0 {
        return Err(ApiError::bad_request("Unsatisfiable byte range"));
    }
    let (a, b) = range
        .split_once('-')
        .ok_or_else(|| ApiError::bad_request("Invalid byte range"))?;
    let (start, end) = if a.is_empty() {
        let suffix = b
            .parse::<u64>()
            .map_err(|_| ApiError::bad_request("Invalid byte range"))?;
        if suffix == 0 {
            return Err(ApiError::bad_request("Unsatisfiable byte range"));
        }
        (size.saturating_sub(suffix), size - 1)
    } else {
        let start = a
            .parse::<u64>()
            .map_err(|_| ApiError::bad_request("Invalid byte range"))?;
        let end = if b.is_empty() {
            size - 1
        } else {
            b.parse::<u64>()
                .map_err(|_| ApiError::bad_request("Invalid byte range"))?
                .min(size - 1)
        };
        (start, end)
    };
    if start >= size || end < start {
        return Err(ApiError::bad_request("Unsatisfiable byte range"));
    }
    Ok((start, end - start + 1, true))
}
pub async fn download(
    state: &AppState,
    key: &str,
    size: u64,
    mime: &str,
    name: &str,
    range: Option<&str>,
    inline: bool,
) -> ApiResult<Response> {
    let (start, length, partial) = parse_range(range, size)?;
    let body = if s3()?.is_some() {
        let mut h = BTreeMap::new();
        if partial {
            h.insert(
                "range".into(),
                format!("bytes={}-{}", start, start + length - 1),
            );
        }
        let res = request(
            state,
            reqwest::Method::GET,
            key,
            "",
            &hex::encode(Sha256::digest([])),
            h,
            None,
        )
        .await?;
        Body::from_stream(res.bytes_stream())
    } else {
        let mut f = File::open(blob_path(state, key)?).await.map_err(internal)?;
        f.seek(std::io::SeekFrom::Start(start))
            .await
            .map_err(internal)?;
        Body::from_stream(file_stream(f, length))
    };
    let mut response = Response::new(body);
    *response.status_mut() = if partial {
        StatusCode::PARTIAL_CONTENT
    } else {
        StatusCode::OK
    };
    let h = response.headers_mut();
    h.insert(
        "content-type",
        HeaderValue::from_str(mime).unwrap_or(HeaderValue::from_static("application/octet-stream")),
    );
    h.insert(
        "content-length",
        HeaderValue::from_str(&length.to_string()).unwrap(),
    );
    h.insert("accept-ranges", HeaderValue::from_static("bytes"));
    h.insert(
        "x-content-type-options",
        HeaderValue::from_static("nosniff"),
    );
    h.insert(
        "cache-control",
        HeaderValue::from_static("private, no-store"),
    );
    if partial {
        h.insert(
            "content-range",
            HeaderValue::from_str(&format!("bytes {}-{}/{}", start, start + length - 1, size))
                .unwrap(),
        );
    }
    let escaped = url::form_urlencoded::byte_serialize(name.as_bytes())
        .collect::<String>()
        .replace('+', "%20");
    h.insert(
        "content-disposition",
        HeaderValue::from_str(&format!(
            "{}; filename=\"download\"; filename*=UTF-8''{}",
            if inline { "inline" } else { "attachment" },
            escaped
        ))
        .unwrap(),
    );
    if inline {
        h.insert(
            "content-security-policy",
            HeaderValue::from_static("sandbox; default-src 'none'; style-src 'unsafe-inline'"),
        );
    }
    Ok(response)
}
pub async fn object_governance(
    state: &AppState,
    key: &str,
    version: Option<&str>,
    retain_until: Option<&str>,
    hold: Option<bool>,
) -> ApiResult<()> {
    if s3()?.is_none() {
        return Ok(());
    }
    let mut q = BTreeMap::new();
    if let Some(v) = version {
        q.insert("versionId", v.to_owned());
    }
    if let Some(date) = retain_until {
        let xml = format!(
            "<Retention xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><Mode>COMPLIANCE</Mode><RetainUntilDate>{date}</RetainUntilDate></Retention>"
        );
        let mut params = q.clone();
        params.insert("retention", String::new());
        let query = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(params)
            .finish();
        let hash = hex::encode(Sha256::digest(xml.as_bytes()));
        request(
            state,
            reqwest::Method::PUT,
            key,
            &query,
            &hash,
            xml_headers(xml.as_bytes()),
            Some(xml.into()),
        )
        .await?;
    }
    if let Some(on) = hold {
        let xml = format!(
            "<LegalHold xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><Status>{}</Status></LegalHold>",
            if on { "ON" } else { "OFF" }
        );
        let mut params = q;
        params.insert("legal-hold", String::new());
        let query = url::form_urlencoded::Serializer::new(String::new())
            .extend_pairs(params)
            .finish();
        let hash = hex::encode(Sha256::digest(xml.as_bytes()));
        request(
            state,
            reqwest::Method::PUT,
            key,
            &query,
            &hash,
            xml_headers(xml.as_bytes()),
            Some(xml.into()),
        )
        .await?;
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn range_semantics() {
        assert_eq!(parse_range(Some("bytes=2-4"), 10).unwrap(), (2, 3, true));
        assert_eq!(parse_range(Some("bytes=-3"), 10).unwrap(), (7, 3, true));
        assert_eq!(parse_range(Some("bytes=5-"), 10).unwrap(), (5, 5, true));
        assert!(parse_range(Some("bytes=20-"), 10).is_err());
        assert!(parse_range(Some("bytes=1-2,4-5"), 10).is_err());
        assert!(parse_range(Some("bytes=-0"), 10).is_err());
    }
    #[test]
    fn rejects_traversal() {
        assert!(!valid_key("../access.json"));
        assert!(!valid_key(&"g".repeat(64)));
        assert!(valid_key(&"a".repeat(64)));
    }
}

pub fn require_object_lock() -> bool {
    std::env::var("BLUEPRINT_S3_REQUIRE_OBJECT_LOCK").as_deref() != Ok("0")
}
fn xml_headers(bytes: &[u8]) -> BTreeMap<String, String> {
    use base64::Engine;
    BTreeMap::from([
        ("content-type".into(), "application/xml".into()),
        (
            "content-md5".into(),
            base64::engine::general_purpose::STANDARD.encode(md5::Md5::digest(bytes)),
        ),
    ])
}
/// Initialize the configured storage without changing an existing incompatible
/// bucket or weakening its retention settings.
async fn initialize(state: &AppState) -> ApiResult<()> {
    let conf = s3()?.ok_or_else(|| ApiError::unavailable("S3 is not configured"))?;
    let identity = format!("{}/{}", conf.endpoint, conf.bucket);
    let ready = READY.get_or_init(Default::default);
    let mut initialized = ready.lock().await;
    if initialized.contains(&identity) {
        return Ok(());
    }
    let empty_hash = hex::encode(Sha256::digest([]));
    match request(
        state,
        reqwest::Method::HEAD,
        "",
        "",
        &empty_hash,
        BTreeMap::new(),
        None,
    )
    .await
    {
        Ok(_) => {}
        Err(e) if e.status == StatusCode::NOT_FOUND => {
            let xml = if conf.region != "us-east-1" {
                format!(
                    "<CreateBucketConfiguration xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><LocationConstraint>{}</LocationConstraint></CreateBucketConfiguration>",
                    conf.region
                )
            } else {
                String::new()
            };
            let mut headers = if xml.is_empty() {
                BTreeMap::new()
            } else {
                xml_headers(xml.as_bytes())
            };
            if require_object_lock() {
                headers.insert("x-amz-bucket-object-lock-enabled".into(), "true".into());
            }
            request(
                state,
                reqwest::Method::PUT,
                "",
                "",
                &hex::encode(Sha256::digest(xml.as_bytes())),
                headers,
                if xml.is_empty() {
                    None
                } else {
                    Some(xml.into())
                },
            )
            .await?;
        }
        Err(e) => return Err(e),
    }
    let versioning = "<VersioningConfiguration xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><Status>Enabled</Status></VersioningConfiguration>";
    request(
        state,
        reqwest::Method::PUT,
        "",
        "versioning=",
        &hex::encode(Sha256::digest(versioning.as_bytes())),
        xml_headers(versioning.as_bytes()),
        Some(versioning.into()),
    )
    .await?;
    if require_object_lock() {
        let response = request(
            state,
            reqwest::Method::GET,
            "",
            "object-lock=",
            &empty_hash,
            BTreeMap::new(),
            None,
        )
        .await?;
        let body = response.text().await.map_err(internal)?;
        if !body.contains("<ObjectLockEnabled>Enabled</ObjectLockEnabled>") {
            return Err(ApiError::unavailable(
                "The configured bucket does not have Object Lock enabled; create an Object Lock bucket and select it without deleting existing data",
            ));
        }
    }
    initialized.insert(identity);
    Ok(())
}

pub async fn receipt(state: &AppState, key: &str) -> ApiResult<(u64, Option<String>)> {
    pin_upload(state, key).await?;
    if is_s3() {
        let response = request(
            state,
            reqwest::Method::HEAD,
            key,
            "",
            &hex::encode(Sha256::digest([])),
            BTreeMap::new(),
            None,
        )
        .await?;
        let size = response.content_length().unwrap_or(0);
        let version = response
            .headers()
            .get("x-amz-version-id")
            .and_then(|h| h.to_str().ok())
            .map(str::to_owned);
        Ok((size, version))
    } else {
        let size = tokio::fs::metadata(blob_path(state, key)?)
            .await
            .map_err(internal)?
            .len();
        Ok((size, None))
    }
}
