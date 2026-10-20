//! A stateless stdio transport for clients whose durable configuration uses a
//! local command. Authentication stays in a private file or process environment.

use reqwest::{Client, Url, header};
use serde_json::{Value, json};
use std::{io, path::Path, time::Duration};
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt, BufReader};

const MAX_LINE: usize = 1024 * 1024;
const MAX_RESPONSE: usize = 8 * 1024 * 1024;
const MAX_TOKEN: usize = 16 * 1024;

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}

fn endpoint(input: &str) -> io::Result<Url> {
    let mut url = Url::parse(input).map_err(|_| invalid("Invalid Blueprint MCP URL"))?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err(invalid(
            "MCP URLs cannot contain credentials, queries or fragments",
        ));
    }
    let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        return Err(invalid(
            "Use HTTPS, or HTTP on localhost, for the MCP connection",
        ));
    }
    match url.path().trim_end_matches('/') {
        "" => url.set_path("/api/v1/mcp"),
        "/api/v1/mcp" => url.set_path("/api/v1/mcp"),
        _ => {
            return Err(invalid(
                "Use the Blueprint origin or its /api/v1/mcp endpoint",
            ));
        }
    }
    Ok(url)
}

async fn token(token_env: Option<&str>, token_file: Option<&Path>) -> io::Result<String> {
    if token_env.is_some() && token_file.is_some() {
        return Err(invalid(
            "Choose a token file or environment variable, not both",
        ));
    }
    let value = if let Some(path) = token_file {
        let metadata = tokio::fs::metadata(path)
            .await
            .map_err(|_| invalid("Cannot read the private MCP token file"))?;
        if !metadata.is_file() || metadata.len() > MAX_TOKEN as u64 {
            return Err(invalid("Invalid private MCP token file"));
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o077 != 0 {
                return Err(invalid("The MCP token file must be private (chmod 600)"));
            }
        }
        tokio::fs::read_to_string(path)
            .await
            .map_err(|_| invalid("Cannot read the private MCP token file"))?
    } else {
        let name = token_env.unwrap_or("BLUEPRINT_MCP_TOKEN");
        if name.is_empty()
            || !name.chars().enumerate().all(|(index, ch)| {
                ch == '_' || ch.is_ascii_alphabetic() || index > 0 && ch.is_ascii_digit()
            })
        {
            return Err(invalid("Invalid MCP token environment-variable name"));
        }
        std::env::var(name).map_err(|_| {
            invalid("Set the MCP token environment variable or provide --token-file")
        })?
    };
    let value = value.trim_end_matches(['\r', '\n']);
    if value.is_empty()
        || value.len() > MAX_TOKEN
        || value
            .bytes()
            .any(|byte| byte.is_ascii_whitespace() || byte.is_ascii_control())
    {
        return Err(invalid("The MCP token must be a single nonempty token"));
    }
    Ok(value.to_owned())
}

async fn read_line<R: AsyncBufRead + Unpin>(reader: &mut R) -> io::Result<Option<Vec<u8>>> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            return Ok((!line.is_empty()).then_some(line));
        }
        let consumed = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(available.len(), |position| position + 1);
        if line.len() + consumed > MAX_LINE {
            return Err(invalid("MCP input exceeds the one MiB line limit"));
        }
        let complete = available[consumed - 1] == b'\n';
        line.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if complete {
            return Ok(Some(line));
        }
    }
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc":"2.0","id":id,"error":{"code":code,"message":message}})
}

async fn forward(
    client: &Client,
    url: &Url,
    request: &Value,
) -> Result<Option<Value>, &'static str> {
    let mut response = client
        .post(url.clone())
        .json(request)
        .send()
        .await
        .map_err(|_| "Blueprint MCP is unreachable or the request timed out")?;
    match response.status().as_u16() {
        401 | 403 => return Err("Blueprint rejected the MCP identity or its permissions"),
        300..=399 => return Err("Blueprint MCP redirects are not followed"),
        400..=599 => return Err("Blueprint MCP returned an unsuccessful HTTP status"),
        _ => {}
    }
    if !request
        .as_object()
        .is_some_and(|object| object.contains_key("id"))
    {
        return Ok(None);
    }
    if response
        .content_length()
        .is_some_and(|length| length > MAX_RESPONSE as u64)
    {
        return Err("Blueprint MCP response exceeds the eight MiB limit");
    }
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .map_err(|_| "Blueprint MCP response could not be read")?
    {
        if body.len() + chunk.len() > MAX_RESPONSE {
            return Err("Blueprint MCP response exceeds the eight MiB limit");
        }
        body.extend_from_slice(&chunk);
    }
    let value: Value = serde_json::from_slice(&body)
        .map_err(|_| "Blueprint MCP returned an invalid JSON response")?;
    if value.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
        || value.get("id") != request.get("id")
        || value.get("result").is_some() == value.get("error").is_some()
    {
        return Err("Blueprint MCP returned an invalid JSON-RPC response");
    }
    Ok(Some(value))
}

async fn serve<R: AsyncBufRead + Unpin, W: AsyncWrite + Unpin>(
    client: Client,
    url: Url,
    mut input: R,
    mut output: W,
) -> io::Result<()> {
    while let Some(line) = read_line(&mut input).await? {
        if line.iter().all(u8::is_ascii_whitespace) {
            continue;
        }
        let result = match serde_json::from_slice::<Value>(&line) {
            Err(_) => Some(rpc_error(Value::Null, -32700, "Invalid JSON")),
            Ok(request) => {
                let id = request.get("id").cloned().unwrap_or(Value::Null);
                let notification = request.as_object().is_some_and(|object| {
                    !object.contains_key("id") && object.get("method").is_some_and(Value::is_string)
                });
                if request.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
                    || !request.get("method").is_some_and(Value::is_string)
                    || !(id.is_null() || id.is_string() || id.is_number())
                {
                    Some(rpc_error(Value::Null, -32600, "Invalid JSON-RPC request"))
                } else {
                    match forward(&client, &url, &request).await {
                        Ok(value) => value,
                        Err(message) if notification => {
                            eprintln!("{message}");
                            None
                        }
                        Err(message) => Some(rpc_error(id, -32000, message)),
                    }
                }
            }
        };
        if let Some(value) = result {
            let mut body = serde_json::to_vec(&value)
                .map_err(|_| io::Error::other("Could not encode MCP response"))?;
            body.push(b'\n');
            output.write_all(&body).await?;
            output.flush().await?;
        }
    }
    Ok(())
}

pub async fn run(url: &str, token_env: Option<&str>, token_file: Option<&Path>) -> io::Result<()> {
    let url = endpoint(url)?;
    let token = token(token_env, token_file).await?;
    let mut auth = header::HeaderValue::from_str(&format!("Bearer {token}"))
        .map_err(|_| invalid("Invalid MCP token"))?;
    auth.set_sensitive(true);
    let mut headers = header::HeaderMap::new();
    headers.insert(header::AUTHORIZATION, auth);
    headers.insert(
        header::ACCEPT,
        header::HeaderValue::from_static("application/json"),
    );
    let client = Client::builder()
        .default_headers(headers)
        .redirect(reqwest::redirect::Policy::none())
        .connect_timeout(Duration::from_secs(10))
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|_| io::Error::other("Could not initialize the MCP HTTP client"))?;
    serve(
        client,
        url,
        BufReader::new(tokio::io::stdin()),
        tokio::io::stdout(),
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{Json, Router, routing::post};
    use tokio::io::AsyncReadExt;

    #[test]
    fn endpoint_rejects_plaintext_remote_and_embedded_credentials() {
        for value in [
            "http://example.com",
            "https://user:secret@example.com",
            "https://example.com/?token=secret",
            "file:///tmp/mcp",
            "https://example.com/wrong",
        ] {
            assert!(endpoint(value).is_err());
        }
        assert_eq!(
            endpoint("http://127.0.0.1:8787/").unwrap().path(),
            "/api/v1/mcp"
        );
        assert!(endpoint("https://company.example/api/v1/mcp").is_ok());
    }

    #[tokio::test]
    async fn bounded_lines_handle_eof_and_reject_oversized_input() {
        assert_eq!(
            read_line(&mut BufReader::new(&b"{}"[..])).await.unwrap(),
            Some(b"{}".to_vec())
        );
        let oversized = vec![b'a'; MAX_LINE + 1];
        assert!(
            read_line(&mut BufReader::new(oversized.as_slice()))
                .await
                .is_err()
        );
    }

    #[tokio::test]
    async fn stdio_preserves_rpc_ids_errors_and_silences_notifications() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(
            axum::serve(
                listener,
                Router::new().route(
                    "/api/v1/mcp",
                    post(|Json(request): Json<Value>| async move {
                        let id = request.get("id").cloned().unwrap_or(Value::Null);
                        Json(if request["method"] == "unknown" {
                            rpc_error(id, -32601, "Method not found")
                        } else {
                            json!({"jsonrpc":"2.0","id":id,"result":{"ok":true}})
                        })
                    }),
                ),
            )
            .into_future(),
        );
        let input = b"{\"jsonrpc\":\"2.0\",\"id\":\"one\",\"method\":\"initialize\"}\n{\"jsonrpc\":\"2.0\",\"method\":\"notifications/initialized\"}\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"unknown\"}\ninvalid\n";
        let (writer, mut reader) = tokio::io::duplex(4096);
        serve(
            Client::new(),
            endpoint(&format!("http://{address}")).unwrap(),
            BufReader::new(&input[..]),
            writer,
        )
        .await
        .unwrap();
        let mut output = String::new();
        reader.read_to_string(&mut output).await.unwrap();
        let values: Vec<Value> = output
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        assert_eq!(values.len(), 3);
        assert_eq!(values[0]["id"], "one");
        assert_eq!(values[1]["id"], 2);
        assert_eq!(values[1]["error"]["code"], -32601);
        assert_eq!(values[2]["error"]["code"], -32700);
        server.abort();
    }

    #[tokio::test]
    async fn failed_http_never_echoes_server_response_secrets() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(
            axum::serve(
                listener,
                Router::new().route(
                    "/api/v1/mcp",
                    post(|| async {
                        (
                            axum::http::StatusCode::UNAUTHORIZED,
                            "secret-server-body-do-not-log",
                        )
                    }),
                ),
            )
            .into_future(),
        );
        let input = b"{\"jsonrpc\":\"2.0\",\"id\":9,\"method\":\"ping\"}\n";
        let (writer, mut reader) = tokio::io::duplex(4096);
        serve(
            Client::new(),
            endpoint(&format!("http://{address}")).unwrap(),
            BufReader::new(&input[..]),
            writer,
        )
        .await
        .unwrap();
        let mut output = String::new();
        reader.read_to_string(&mut output).await.unwrap();
        assert!(!output.contains("secret-server-body"));
        assert_eq!(serde_json::from_str::<Value>(&output).unwrap()["id"], 9);
        server.abort();
    }
}
