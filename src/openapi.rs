//! Hand-written contract for the routes actually exposed by the modular server.
//! The coverage test keeps newly added routes from silently missing the contract.
use serde_json::{Map, Value, json};

fn reference(name: &str) -> Value {
    json!({"$ref":format!("#/components/schemas/{name}")})
}
fn array(name: &str) -> Value {
    json!({"type":"array","items":reference(name)})
}
fn object(properties: Value, required: &[&str]) -> Value {
    json!({"type":"object","properties":properties,"required":required})
}
fn parameter(
    name: &str,
    location: &str,
    required: bool,
    schema: Value,
    description: &str,
) -> Value {
    json!({"name":name,"in":location,"required":required,"schema":schema,"description":description})
}
fn body(schema: Value, media: &str) -> Value {
    json!({"required":true,"content":{media:{"schema":schema}}})
}
fn success(schema: Value, media: &str) -> Value {
    json!({"description":"Successful response","content":{media:{"schema":schema}}})
}

/// Does not expose configuration values, credentials or private company data.
pub fn document() -> Value {
    let mut paths = Map::new();
    // Request/response schema names are intentionally shared only where their
    // wire shape is shared. Object is an open JSON object, never a claim that an
    // optional provider is configured or that an asynchronous action succeeded.
    let routes = [
        (
            "/status",
            "get",
            "Read installation status",
            "Identity",
            "",
            "Status",
        ),
        (
            "/setup",
            "post",
            "Initialize a fresh installation using its bootstrap token",
            "Identity",
            "Setup",
            "Session",
        ),
        (
            "/login",
            "post",
            "Sign in using password and optional TOTP",
            "Identity",
            "Login",
            "Session",
        ),
        (
            "/activate",
            "post",
            "Accept an unexpired invitation and set a password",
            "Identity",
            "Activation",
            "Session",
        ),
        (
            "/me",
            "get",
            "Read current authenticated identity and constraints",
            "Identity",
            "",
            "Actor",
        ),
        (
            "/logout",
            "post",
            "Revoke the current browser session",
            "Identity",
            "",
            "Object",
        ),
        (
            "/users",
            "get",
            "List active member directory; administrators see account details",
            "Identity",
            "",
            "Objects",
        ),
        (
            "/users",
            "post",
            "Create a member or a seven-day invitation; administrator required",
            "Identity",
            "NewUser",
            "Object",
        ),
        (
            "/users/{id}",
            "patch",
            "Update member role, active state or groups; administrator required",
            "Identity",
            "UserUpdate",
            "Actor",
        ),
        (
            "/tokens",
            "get",
            "List permitted service-token metadata without token secrets",
            "Identity",
            "",
            "Objects",
        ),
        (
            "/tokens",
            "post",
            "Create a scoped service token from an interactive session; secret returned once",
            "Identity",
            "TokenInput",
            "Object",
        ),
        (
            "/tokens/{id}",
            "delete",
            "Revoke an owned token or a token managed by an administrator",
            "Identity",
            "",
            "Object",
        ),
        (
            "/auth/mfa/setup",
            "post",
            "Start TOTP enrollment from an interactive session",
            "Identity",
            "",
            "Object",
        ),
        (
            "/auth/mfa/confirm",
            "post",
            "Confirm pending TOTP enrollment",
            "Identity",
            "MfaConfirm",
            "Object",
        ),
        (
            "/auth/password",
            "post",
            "Change password and revoke existing browser sessions",
            "Identity",
            "PasswordChange",
            "Object",
        ),
        (
            "/auth/sso/start",
            "get",
            "Redirect to configured OpenID Connect provider using PKCE",
            "Identity",
            "",
            "Redirect",
        ),
        (
            "/auth/sso/callback",
            "get",
            "Validate OIDC state and code, then establish a browser session",
            "Identity",
            "",
            "Redirect",
        ),
        (
            "/resources/{kind}",
            "get",
            "List authorized non-deleted resources of one kind",
            "Resources",
            "",
            "Resources",
        ),
        (
            "/resources",
            "post",
            "Create a validated, versioned resource",
            "Resources",
            "NewResource",
            "Resource",
        ),
        (
            "/resource/{id}",
            "get",
            "Read an authorized resource with secret fields redacted",
            "Resources",
            "",
            "Resource",
        ),
        (
            "/resource/{id}",
            "patch",
            "Update a resource using its expected version",
            "Resources",
            "UpdateResource",
            "Resource",
        ),
        (
            "/resource/{id}",
            "delete",
            "Move an authorized resource to trash",
            "Resources",
            "",
            "Resource",
        ),
        (
            "/resource/{id}/history",
            "get",
            "Read authorized redacted resource history",
            "Resources",
            "",
            "Objects",
        ),
        (
            "/resource/{id}/restore",
            "post",
            "Restore a resource from trash using its expected version",
            "Resources",
            "VersionInput",
            "Resource",
        ),
        (
            "/resource/{id}/purge",
            "delete",
            "Permanently delete an empty trashed resource when governance permits",
            "Resources",
            "",
            "Object",
        ),
        (
            "/trash",
            "get",
            "List trashed resources the current actor may manage",
            "Resources",
            "",
            "Resources",
        ),
        (
            "/search",
            "get",
            "Search permitted enabled resource modules",
            "Resources",
            "",
            "Resources",
        ),
        (
            "/settings",
            "get",
            "Read company, module and service-configuration status",
            "Administration",
            "",
            "Settings",
        ),
        (
            "/settings",
            "patch",
            "Update company profile and enabled modules; administrator required",
            "Administration",
            "SettingsInput",
            "Settings",
        ),
        (
            "/audit",
            "get",
            "Read audit events; administrator required",
            "Administration",
            "",
            "Objects",
        ),
        (
            "/jobs",
            "get",
            "List owned jobs or all jobs for an administrator",
            "Jobs",
            "",
            "Jobs",
        ),
        (
            "/jobs/{id}",
            "get",
            "Read an owned or administratively managed job",
            "Jobs",
            "",
            "Job",
        ),
        (
            "/jobs/{id}/cancel",
            "post",
            "Request job cancellation; external actions may need provider acknowledgement",
            "Jobs",
            "",
            "Object",
        ),
        (
            "/dashboard",
            "get",
            "Count resources visible to the current actor",
            "Resources",
            "",
            "Object",
        ),
        (
            "/openapi.json",
            "get",
            "Read this OpenAPI contract",
            "Administration",
            "",
            "Object",
        ),
        (
            "/export",
            "get",
            "Export redacted metadata; excludes original file bytes; administrator required",
            "Administration",
            "",
            "Object",
        ),
        (
            "/files/upload",
            "post",
            "Upload an immutable original file using multipart form data",
            "Content",
            "MultipartUpload",
            "Resource",
        ),
        (
            "/files/{id}/download",
            "get",
            "Download original bytes subject to ACL, scan and content policies",
            "Content",
            "",
            "Binary",
        ),
        (
            "/files/{id}/preview",
            "get",
            "Read an available processed preview subject to content policies",
            "Content",
            "",
            "Binary",
        ),
        (
            "/files/{id}/preview-policy",
            "get",
            "Read effective preview watermark and print restrictions",
            "Content",
            "",
            "Object",
        ),
        (
            "/files/{id}/versions",
            "get",
            "List immutable versions of an authorized file",
            "Content",
            "",
            "FileVersions",
        ),
        (
            "/files/{id}/versions",
            "post",
            "Upload a new immutable file version",
            "Content",
            "VersionUpload",
            "Resource",
        ),
        (
            "/files/{id}/versions/{number}/download",
            "get",
            "Download permitted bytes of a specific immutable version",
            "Content",
            "",
            "Binary",
        ),
        (
            "/files/{id}/versions/{number}/restore",
            "post",
            "Create a new current version from a historical version",
            "Content",
            "VersionInput",
            "Resource",
        ),
        (
            "/files/{id}/copy",
            "post",
            "Atomically copy an authorized file, link or folder tree of up to 2000 items into a writable destination",
            "Content",
            "CopyInput",
            "Resource",
        ),
        (
            "/files/{id}/move",
            "post",
            "Move managed content using optimistic version and destination permission checks",
            "Content",
            "MoveInput",
            "Resource",
        ),
        (
            "/files/{id}/lock",
            "post",
            "Set or release a timed application content lock",
            "Content",
            "LockInput",
            "Resource",
        ),
        (
            "/files/{id}/governance",
            "post",
            "Set retention, legal hold or archive state; administrator required",
            "Content",
            "GovernanceInput",
            "Resource",
        ),
        (
            "/files/{id}/process",
            "post",
            "Queue file scanning, extraction and preview processing",
            "Content",
            "",
            "Job",
        ),
        (
            "/files/{id}/comments",
            "get",
            "List readable comments on a resource",
            "Content",
            "",
            "Resources",
        ),
        (
            "/files/{id}/comments",
            "post",
            "Add a persisted comment to an authorized resource",
            "Content",
            "CommentInput",
            "Resource",
        ),
        (
            "/content/capabilities",
            "get",
            "Read configured content-service capabilities",
            "Content",
            "",
            "Object",
        ),
        (
            "/content/gc",
            "get",
            "Read deferred unreferenced-content cleanup status; administrator required",
            "Content",
            "",
            "GcStatus",
        ),
        (
            "/content/gc",
            "post",
            "Queue collection of eligible unreferenced content after its 24-hour grace period; administrator required",
            "Content",
            "",
            "Job",
        ),
        (
            "/uploads",
            "post",
            "Initialize a durable offset-based upload session",
            "Content",
            "UploadInput",
            "UploadSession",
        ),
        (
            "/uploads/{id}",
            "get",
            "Read durable upload offset and expiry",
            "Content",
            "",
            "UploadSession",
        ),
        (
            "/uploads/{id}",
            "patch",
            "Append one verified chunk at the declared offset",
            "Content",
            "Binary",
            "UploadSession",
        ),
        (
            "/uploads/{id}",
            "delete",
            "Abort an unfinished upload and remove its staged bytes",
            "Content",
            "",
            "Object",
        ),
        (
            "/uploads/{id}/chunk",
            "patch",
            "Append one verified chunk at the declared offset",
            "Content",
            "Binary",
            "UploadSession",
        ),
        (
            "/uploads/{id}/complete",
            "post",
            "Finalize a complete upload idempotently into a file resource",
            "Content",
            "",
            "Resource",
        ),
        (
            "/shares",
            "get",
            "List permitted sharing-link metadata",
            "Sharing",
            "",
            "Objects",
        ),
        (
            "/shares",
            "post",
            "Create a revocable permission-constrained sharing link",
            "Sharing",
            "ShareInput",
            "Object",
        ),
        (
            "/shares/{id}",
            "delete",
            "Revoke a managed sharing link",
            "Sharing",
            "",
            "Object",
        ),
        (
            "/public/shares/{token}",
            "get",
            "Resolve an unexpired sharing link and its permitted metadata",
            "Sharing",
            "",
            "Object",
        ),
        (
            "/public/shares/{token}/download",
            "get",
            "Download a shared file when the link and current policy permit",
            "Sharing",
            "",
            "Binary",
        ),
        (
            "/public/shares/{token}/preview",
            "get",
            "Preview shared content when current policy permits",
            "Sharing",
            "",
            "Binary",
        ),
        (
            "/public/shares/{token}/items",
            "get",
            "List permitted descendants of a shared folder",
            "Sharing",
            "",
            "Resources",
        ),
        (
            "/public/shares/{token}/files/{id}/download",
            "get",
            "Download a permitted descendant of the shared folder",
            "Sharing",
            "",
            "Binary",
        ),
        (
            "/tables/{id}/records",
            "get",
            "List and normalize records using the table schema and optional saved view",
            "Business",
            "",
            "Resources",
        ),
        (
            "/tables/{id}/records",
            "post",
            "Create a schema-validated record in a writable table",
            "Business",
            "RecordInput",
            "Resource",
        ),
        (
            "/tables/{id}/views",
            "get",
            "List saved table views",
            "Business",
            "",
            "Objects",
        ),
        (
            "/tables/{id}/views",
            "post",
            "Persist a validated list, table, board, calendar or chart view",
            "Business",
            "TableView",
            "Resource",
        ),
        (
            "/forms/{id}/publish",
            "post",
            "Publish or unpublish a form and issue its access URL",
            "Business",
            "PublishForm",
            "Object",
        ),
        (
            "/forms/{id}/submit",
            "post",
            "Submit or preview an authorized form using its schema",
            "Business",
            "FormSubmission",
            "Object",
        ),
        (
            "/forms/{id}/submissions",
            "get",
            "Read submissions of a managed form",
            "Business",
            "",
            "Resources",
        ),
        (
            "/public/forms/{id}",
            "get",
            "Read a published form; company forms require a member session",
            "Business",
            "",
            "Object",
        ),
        (
            "/public/forms/{id}",
            "post",
            "Submit validated values to a published form idempotently",
            "Business",
            "FormSubmission",
            "Object",
        ),
        (
            "/calendar/ics",
            "get",
            "Export readable events as an iCalendar feed",
            "Calendar",
            "",
            "Calendar",
        ),
        (
            "/calendar/events/{id}/resolve",
            "post",
            "Resolve a calendar sync conflict using the local or remote version",
            "Calendar",
            "CalendarResolution",
            "Object",
        ),
        (
            "/calendar/connections",
            "get",
            "List owned calendar connections without OAuth tokens",
            "Calendar",
            "",
            "Resources",
        ),
        (
            "/calendar/connections",
            "post",
            "Start configured Google or Microsoft calendar OAuth",
            "Calendar",
            "CalendarConnection",
            "Object",
        ),
        (
            "/calendar/connections/{id}/sync",
            "post",
            "Queue synchronization of an owned connected calendar",
            "Calendar",
            "",
            "Object",
        ),
        (
            "/calendar/connections/{id}/disconnect",
            "post",
            "Revoke an owned calendar connection",
            "Calendar",
            "",
            "Object",
        ),
        (
            "/public/calendar/oauth/{provider}",
            "get",
            "Exchange a one-time bound OAuth state and code",
            "Calendar",
            "",
            "Redirect",
        ),
        (
            "/documents/generate",
            "post",
            "Generate a DOCX or configured-service PDF from a template and values",
            "Documents",
            "GenerateDocument",
            "Resource",
        ),
        (
            "/sign/requests",
            "get",
            "List permitted signature requests",
            "Documents",
            "",
            "Resources",
        ),
        (
            "/sign/requests",
            "post",
            "Queue a real configured Documenso envelope",
            "Documents",
            "SignInput",
            "Resource",
        ),
        (
            "/sign/requests/{id}",
            "get",
            "Read persisted provider-backed signature status",
            "Documents",
            "",
            "Resource",
        ),
        (
            "/sign/requests/{id}/refresh",
            "post",
            "Queue provider status reconciliation",
            "Documents",
            "",
            "Object",
        ),
        (
            "/sign/requests/{id}/cancel",
            "post",
            "Request cancellation at the configured signing provider",
            "Documents",
            "",
            "Object",
        ),
        (
            "/sign/requests/{id}/download",
            "get",
            "Download an actually completed provider-signed PDF",
            "Documents",
            "",
            "Binary",
        ),
        (
            "/sign/requests/{id}/evidence",
            "get",
            "Download the signing provider's actual evidence PDF",
            "Documents",
            "",
            "Binary",
        ),
        (
            "/public/sign/webhook",
            "post",
            "Authenticate Documenso notification and queue provider reconciliation",
            "Documents",
            "Object",
            "Object",
        ),
        (
            "/collaboration/{id}/ws",
            "get",
            "Upgrade to authenticated Yjs note or canvas collaboration",
            "Collaboration",
            "",
            "WebSocket",
        ),
        (
            "/office/{id}/open",
            "post",
            "Resolve configured Collabora discovery and issue a short-lived WOPI launch capability",
            "Office",
            "OfficeOpen",
            "OfficeLaunch",
        ),
        (
            "/public/wopi/files/{id}",
            "get",
            "WOPI CheckFileInfo with current user permissions",
            "Office",
            "",
            "Object",
        ),
        (
            "/public/wopi/files/{id}",
            "post",
            "WOPI locks, relative save and rename operations",
            "Office",
            "Binary",
            "Object",
        ),
        (
            "/public/wopi/files/{id}/contents",
            "get",
            "WOPI GetFile under the bound capability and current download policy",
            "Office",
            "",
            "Binary",
        ),
        (
            "/public/wopi/files/{id}/contents",
            "post",
            "WOPI PutFile as a new immutable version under a matching lock",
            "Office",
            "Binary",
            "Empty",
        ),
        (
            "/workflows/{id}/activate",
            "post",
            "Publish an immutable validated workflow definition and activate its trigger",
            "Workflows",
            "",
            "Resource",
        ),
        (
            "/workflows/{id}/deactivate",
            "post",
            "Deactivate future automatic triggers without changing pinned runs",
            "Workflows",
            "",
            "Resource",
        ),
        (
            "/workflows/{id}/run",
            "post",
            "Start an authorized pinned workflow run with optional idempotency key",
            "Workflows",
            "WorkflowRunInput",
            "Resource",
        ),
        (
            "/workflows/{id}/runs",
            "get",
            "List visible runs belonging to a readable workflow",
            "Workflows",
            "",
            "Resources",
        ),
        (
            "/runs/{id}",
            "get",
            "Read a permitted persisted workflow run and step outcomes",
            "Workflows",
            "",
            "Resource",
        ),
        (
            "/runs/{id}/approve",
            "post",
            "Persist an assigned approval decision and resume its branch",
            "Workflows",
            "ApprovalInput",
            "Resource",
        ),
        (
            "/runs/{id}/cancel",
            "post",
            "Stop local continuation and record cancellation requested for external work",
            "Workflows",
            "",
            "Resource",
        ),
        (
            "/runs/{id}/resume",
            "post",
            "Reconcile a failed or unknown step; explicit retry can repeat an external effect",
            "Workflows",
            "ResumeInput",
            "Resource",
        ),
        (
            "/agents/{id}/test",
            "post",
            "Probe configured worker connectivity using HEAD without executing a task",
            "Integrations",
            "",
            "Object",
        ),
        (
            "/connectors/{id}/test",
            "post",
            "Probe configured connector connectivity using HEAD",
            "Integrations",
            "",
            "Object",
        ),
        (
            "/agents/{id}/dispatch",
            "post",
            "Dispatch a declared task to an authorized external worker",
            "Integrations",
            "DispatchInput",
            "Resource",
        ),
        (
            "/agents/{id}/callback-secret",
            "post",
            "Rotate the worker HMAC callback secret; return it once; administrator required",
            "Integrations",
            "",
            "Object",
        ),
        (
            "/public/callbacks/{id}",
            "post",
            "Verify timestamped HMAC and persist a deduplicated worker result",
            "Integrations",
            "WorkerCallback",
            "Object",
        ),
        (
            "/mcp",
            "post",
            "MCP JSON-RPC using the same current identity, scopes, ACLs and policies",
            "Integrations",
            "JsonRpcRequest",
            "JsonRpcResponse",
        ),
        (
            "/scim/v2/ServiceProviderConfig",
            "get",
            "Read the implemented SCIM capability subset; administrator required",
            "SCIM",
            "",
            "Object",
        ),
        (
            "/scim/v2/Users",
            "get",
            "List SCIM users with supported pagination and email filter; administrator required",
            "SCIM",
            "",
            "Object",
        ),
        (
            "/scim/v2/Users",
            "post",
            "Provision a SCIM member; administrator required",
            "SCIM",
            "ScimUser",
            "Object",
        ),
        (
            "/scim/v2/Users/{id}",
            "get",
            "Read a SCIM user; administrator required",
            "SCIM",
            "",
            "Object",
        ),
        (
            "/scim/v2/Users/{id}",
            "put",
            "Replace supported displayName and active fields; administrator required",
            "SCIM",
            "ScimUser",
            "Object",
        ),
        (
            "/scim/v2/Users/{id}",
            "patch",
            "Patch displayName and active; administrator required",
            "SCIM",
            "ScimPatch",
            "Object",
        ),
        (
            "/scim/v2/Users/{id}",
            "delete",
            "Deactivate a SCIM user and revoke sessions and tokens; administrator required",
            "SCIM",
            "",
            "Empty",
        ),
        (
            "/scim/v2/Groups",
            "get",
            "List persisted SCIM groups; administrator required",
            "SCIM",
            "",
            "Object",
        ),
        (
            "/scim/v2/Groups",
            "post",
            "Create a SCIM group and validate memberships; administrator required",
            "SCIM",
            "ScimGroup",
            "Object",
        ),
        (
            "/scim/v2/Groups/{id}",
            "get",
            "Read a SCIM group; administrator required",
            "SCIM",
            "",
            "Object",
        ),
        (
            "/scim/v2/Groups/{id}",
            "patch",
            "Patch a group's displayName or members; administrator required",
            "SCIM",
            "ScimPatch",
            "Object",
        ),
        (
            "/scim/v2/Groups/{id}",
            "delete",
            "Delete a SCIM group and remove memberships; administrator required",
            "SCIM",
            "",
            "Empty",
        ),
    ];
    for (path, method, summary, tag, input, output) in routes {
        let public = path.starts_with("/public/")
            || matches!(
                path,
                "/status"
                    | "/setup"
                    | "/login"
                    | "/activate"
                    | "/auth/sso/start"
                    | "/auth/sso/callback"
            );
        let mut parameters = Vec::new();
        for segment in path.split('/') {
            if let Some(name) = segment.strip_prefix('{').and_then(|s| s.strip_suffix('}')) {
                let schema = match name {
                    "id" => json!({"type":"string","format":"uuid"}),
                    "number" => json!({"type":"integer","minimum":1}),
                    "kind" => reference("ResourceKind"),
                    "provider" => json!({"type":"string","enum":["google","microsoft"]}),
                    _ => json!({"type":"string"}),
                };
                parameters.push(parameter(name, "path", true, schema, "Route identifier"));
            }
        }
        if !matches!(method, "get" | "head")
            && !path.starts_with("/public/wopi/")
            && !path.starts_with("/public/callbacks/")
            && path != "/public/sign/webhook"
        {
            parameters.push(parameter("X-Blueprint-Request","header",false,json!({"type":"string","const":"1"}),"Required for writes without an Authorization header. Browser writes also require the configured same-origin Origin."));
        }
        let media = match output {
            "Binary" => "application/octet-stream",
            "Calendar" => "text/calendar",
            _ => "application/json",
        };
        let code = if path == "/resources" && method == "post"
            || path.starts_with("/scim/v2/") && method == "post"
        {
            "201"
        } else if output == "Empty" && method == "delete" {
            "204"
        } else if output == "Redirect" {
            if path.starts_with("/auth/sso/") {
                "302"
            } else {
                "303"
            }
        } else if output == "WebSocket" {
            "101"
        } else {
            "200"
        };
        let mut responses = json!({"400":{"$ref":"#/components/responses/BadRequest"},"401":{"$ref":"#/components/responses/Unauthorized"},"403":{"$ref":"#/components/responses/Forbidden"},"404":{"$ref":"#/components/responses/NotFound"},"409":{"$ref":"#/components/responses/Conflict"},"503":{"$ref":"#/components/responses/Unavailable"}});
        responses[code] = if matches!(output, "Empty" | "Redirect" | "WebSocket") {
            json!({"description":match output{"Redirect"=>"Redirect to the identity provider or application; Location header supplied","WebSocket"=>"WebSocket protocol upgrade","Empty"=>"Successful response with no JSON body",_=>unreachable!()}})
        } else {
            success(reference(output), media)
        };
        let mut operation = json!({"operationId":format!("{}_{}",method,path.trim_matches('/').replace(['/','{','}','-'],"_")),"tags":[tag],"summary":summary,"parameters":parameters,"responses":responses});
        if public {
            operation["security"] = json!([]);
        }
        if !input.is_empty() {
            operation["requestBody"] = body(
                reference(input),
                match input {
                    "Binary" => "application/octet-stream",
                    "MultipartUpload" | "VersionUpload" => "multipart/form-data",
                    _ => "application/json",
                },
            );
        }
        paths.entry(path.to_owned()).or_insert_with(|| json!({}))[method] = operation;
    }
    for path in ["/health", "/api/health"] {
        paths.insert(path.into(),json!({"servers":[{"url":"/"}],"get":{"operationId":format!("health_{}",path.replace('/',"_")),"tags":["Operations"],"summary":"Read server and PostgreSQL health","security":[],"responses":{"200":success(reference("Object"),"application/json"),"500":{"$ref":"#/components/responses/InternalError"}}}}));
    }
    let mut doc = json!({"openapi":"3.1.0","info":{"title":"Blueprint Studio Backend","version":env!("CARGO_PKG_VERSION"),"description":"Single-company self-hosted workspace API. Current account/token state, token scopes and resource IDs, resource and ancestor ACLs, enabled modules and content governance are enforced by the server. Browser sessions use an HttpOnly cookie; service clients use bearer tokens. Writes without an Authorization header send X-Blueprint-Request: 1. An Origin header, when present, must match the configured application origin, except WOPI also accepts the explicitly configured Collabora origin. Optional services return configuration/errors rather than simulated results. Resource data is typed by kind; arbitrary server-managed fields and credentials cannot be set through generic CRUD. Long-running actions expose persisted jobs or workflow runs."},"servers":[{"url":"/api/v1"}],"security":[{"session":[]},{"bearer":[]}],"paths":paths,"components":{"securitySchemes":{"session":{"type":"apiKey","in":"cookie","name":"blueprint_session","description":"HttpOnly browser session. CSRF header/origin checks apply to writes."},"bearer":{"type":"http","scheme":"bearer","description":"Revocable service token with explicit scopes and optional resource UUID limits."},"wopiCapability":{"type":"apiKey","in":"query","name":"access_token","description":"Short-lived signed file/user capability. Every operation rechecks current account, token, ACL and content policy."},"workerSignature":{"type":"apiKey","in":"header","name":"X-Blueprint-Signature","description":"Hex HMAC-SHA256(secret, timestamp + '.' + exact raw request body). X-Blueprint-Timestamp must be within 300 seconds."},"documensoSecret":{"type":"apiKey","in":"header","name":"X-Documenso-Secret","description":"Configured signing-provider webhook secret."}},"schemas":schemas(),"responses":error_responses()}});
    decorate(&mut doc);
    doc
}

fn error_responses() -> Value {
    let mut values = Map::new();
    for (name, description) in [
        ("BadRequest", "Invalid request or domain validation failed"),
        (
            "Unauthorized",
            "Authentication, capability or signature invalid or expired",
        ),
        (
            "Forbidden",
            "Scope, role, module, ACL or content policy denied the action",
        ),
        (
            "NotFound",
            "Resource is unavailable or not visible to this identity",
        ),
        (
            "Conflict",
            "Optimistic version, lock, state or idempotency conflict",
        ),
        (
            "Unavailable",
            "Required service is unconfigured, unreachable or returned an unverifiable result",
        ),
        ("InternalError", "Server or database operation failed"),
    ] {
        values.insert(name.into(),json!({"description":description,"content":{"application/json":{"schema":reference("Error")}}}));
    }
    Value::Object(values)
}

fn schemas() -> Value {
    let string = json!({"type":"string"});
    let boolean = json!({"type":"boolean"});
    let uuid = json!({"type":"string","format":"uuid"});
    let nullable_uuid = json!({"type":["string","null"],"format":"uuid"});
    let date = json!({"type":"string","format":"date-time"});
    let strings = json!({"type":"array","items":{"type":"string"}});
    let any = json!({"type":"object","additionalProperties":true});
    let mut schema_map = Map::new();
    schema_map.insert("Object".to_owned(), json!(any));
    schema_map.insert("Objects".to_owned(), json!(array("Object")));
    schema_map.insert("Resources".to_owned(), json!(array("Resource")));
    schema_map.insert("Jobs".to_owned(), json!(array("Job")));
    schema_map.insert(
        "Binary".to_owned(),
        json!({"type":"string","format":"binary"}),
    );
    schema_map.insert("Calendar".to_owned(), json!({"type":"string"}));
    schema_map.insert(
        "Error".to_owned(),
        json!(object(
            json!({"error":object(json!({"code":string,"message":string}),&["code","message"])}),
            &["error"]
        )),
    );
    schema_map.insert("Actor".to_owned(), json!(object(json!({"id":uuid,"email":string,"name":string,"role":{"enum":["owner","admin","member","viewer","guest"]},"groups":strings,"scopes":strings,"resource_ids":{"type":"array","items":uuid},"token_id":uuid}),&["id","email","name","role","groups"])));
    schema_map.insert("Status".to_owned(), json!(object(json!({"initialized":boolean,"company_name":string,"version":string,"setup_available":boolean,"sso_available":boolean}),&["initialized","company_name","version"])));
    schema_map.insert(
        "Session".to_owned(),
        json!(object(json!({"user":reference("Actor")}), &["user"])),
    );
    schema_map.insert("Setup".to_owned(), json!(object(json!({"token":string,"company_name":string,"email":string,"name":string,"password":{"type":"string","format":"password","writeOnly":true}}),&["token","company_name","email","name","password"])));
    schema_map.insert("Login".to_owned(), json!(object(json!({"email":string,"password":{"type":"string","format":"password","writeOnly":true},"otp":string}),&["email","password"])));
    schema_map.insert("Activation".to_owned(), json!(object(json!({"token":string,"password":{"type":"string","format":"password","writeOnly":true}}),&["token","password"])));
    schema_map.insert("NewUser".to_owned(), json!(object(json!({"name":string,"email":string,"role":{"enum":["admin","member","viewer","guest"]},"password":{"type":"string","format":"password","writeOnly":true},"groups":strings}),&["name","email","role"])));
    schema_map.insert("UserUpdate".to_owned(), json!(object(json!({"name":string,"role":{"enum":["admin","member","viewer","guest"]},"active":boolean,"groups":strings}),&[])));
    schema_map.insert("TokenInput".to_owned(), json!(object(json!({"name":string,"scopes":strings,"resource_ids":{"type":["array","null"],"items":uuid},"expires_at":{"type":["string","null"],"format":"date-time"}}),&["name","scopes"])));
    schema_map.insert(
        "MfaConfirm".to_owned(),
        json!(object(
            json!({"token":string,"code":string}),
            &["token", "code"]
        )),
    );
    schema_map.insert("PasswordChange".to_owned(), json!(object(json!({"current_password":{"type":"string","writeOnly":true},"password":{"type":"string","writeOnly":true}}),&["current_password","password"])));
    schema_map.insert(
        "ResourceKind".to_owned(),
        json!({"type":"string","enum":crate::repo::KINDS}),
    );
    schema_map.insert("Acl".to_owned(), json!({"type":"object","additionalProperties":{"type":"string","enum":["editor","viewer","uploader"]},"description":"Principal user:<uuid> or group:<uuid> to access role."}));
    schema_map.insert("Resource".to_owned(), json!(object(json!({"id":uuid,"kind":reference("ResourceKind"),"name":string,"parent_id":nullable_uuid,"owner_id":uuid,"visibility":{"enum":["private","company"]},"acl":reference("Acl"),"data":any,"version":{"type":"integer","format":"int64","minimum":1},"created_at":date,"updated_at":date,"deleted_at":{"type":["string","null"],"format":"date-time"}}),&["id","kind","name","owner_id","visibility","acl","data","version","created_at","updated_at"])));
    schema_map.insert("NewResource".to_owned(), json!(object(json!({"kind":reference("ResourceKind"),"name":string,"parent_id":nullable_uuid,"visibility":{"enum":["private","company"]},"acl":reference("Acl"),"data":any}),&["kind","name","data"])));
    schema_map.insert("UpdateResource".to_owned(), json!(object(json!({"name":string,"parent_id":nullable_uuid,"visibility":{"enum":["private","company"]},"acl":reference("Acl"),"data":any,"expected_version":{"type":"integer","format":"int64","minimum":1}}),&["expected_version"])));
    schema_map.insert(
        "VersionInput".to_owned(),
        json!(object(
            json!({"expected_version":{"type":"integer","format":"int64","minimum":1}}),
            &["expected_version"]
        )),
    );
    schema_map.insert("Settings".to_owned(), json!(object(json!({"company":any,"modules":{"type":"object","additionalProperties":boolean},"services":{"type":"object","additionalProperties":{"type":"object","properties":{"configured":boolean}}}}),&["company","modules","services"])));
    schema_map.insert(
        "SettingsInput".to_owned(),
        json!(object(
            json!({"company":any,"modules":{"type":"object","additionalProperties":boolean}}),
            &[]
        )),
    );
    schema_map.insert("Job".to_owned(), json!(object(json!({"id":uuid,"owner_id":uuid,"kind":string,"status":{"enum":["queued","running","waiting","completed","failed","cancel_requested","cancelled"]},"payload":any,"result":{},"error":{"type":["string","null"]},"attempts":{"type":"integer"},"created_at":date,"updated_at":date}),&["id","kind","status","payload","result","attempts","created_at","updated_at"])));
    schema_map.insert("MultipartUpload".to_owned(), json!(object(json!({"file":reference("Binary"),"parent_id":uuid,"visibility":{"enum":["private","company"]},"expected_version":{"type":"integer","description":"Required for uploading a new version of an existing file."}}),&["file"])));
    schema_map.insert(
        "CopyInput".to_owned(),
        json!(object(
            json!({"name":string,"parent_id":nullable_uuid}),
            &[]
        )),
    );
    schema_map.insert(
        "MoveInput".to_owned(),
        json!(object(
            json!({"parent_id":nullable_uuid,"expected_version":{"type":"integer"}}),
            &["expected_version"]
        )),
    );
    schema_map.insert(
        "LockInput".to_owned(),
        json!(object(
            json!({"locked":boolean,"minutes":{"type":"integer"},"disable_download":boolean}),
            &["locked"]
        )),
    );
    schema_map.insert("GovernanceInput".to_owned(), json!(object(json!({"expected_version":{"type":"integer"},"retain_until":date,"legal_hold":boolean,"archived":boolean}),&["expected_version"])));
    schema_map.insert(
        "CommentInput".to_owned(),
        json!(object(json!({"body":string}), &["body"])),
    );
    schema_map.insert("UploadInput".to_owned(), json!(object(json!({"name":string,"mime":string,"size":{"type":"integer","minimum":0},"parent_id":nullable_uuid,"visibility":{"enum":["private","company"]},"sha256":{"type":"string","pattern":"^[0-9a-fA-F]{64}$"}}),&["name","size"])));
    schema_map.insert("UploadSession".to_owned(), json!(object(json!({"id":uuid,"name":string,"status":{"enum":["uploading","finalizing","completed","cancelled"]},"offset":{"type":"integer"},"size":{"type":"integer"},"expires_at":date,"resource_id":uuid,"max_chunk_bytes":{"const":8388608}}),&["id","status","offset","size","max_chunk_bytes"])));
    schema_map.insert("ShareInput".to_owned(), json!(object(json!({"resource_id":uuid,"access":{"enum":["link","anyone","company","invited"]},"password":{"type":"string","writeOnly":true},"expires_at":date,"allow_download":boolean}),&["resource_id","access","allow_download"])));
    schema_map.insert(
        "RecordInput".to_owned(),
        json!(object(json!({"name":string,"values":any}), &["values"])),
    );
    schema_map.insert("TableView".to_owned(), json!(object(json!({"id":string,"name":string,"type":{"enum":["list","table","board","calendar","chart"]},"filters":{"type":"array","items":any},"sort":{"type":"array","items":any},"group_by":string}),&[])));
    schema_map.insert("PublishForm".to_owned(), json!(object(json!({"published":boolean,"expected_version":{"type":"integer"},"access":{"enum":["public","company"]},"expires_at":date}),&["published"])));
    schema_map.insert("FormSubmission".to_owned(), json!(object(json!({"values":any,"submission_key":uuid,"preview":{"type":"boolean","default":false,"description":"Only honored for authenticated form preview; public submissions force false."}}),&["values"])));
    schema_map.insert(
        "CalendarConnection".to_owned(),
        json!(object(
            json!({"provider":{"enum":["google","microsoft"]}}),
            &["provider"]
        )),
    );
    schema_map.insert("GenerateDocument".to_owned(), json!(object(json!({"name":string,"template":string,"values":any,"format":{"enum":["docx","pdf"],"default":"docx"},"parent_id":nullable_uuid}),&["name","template","values"])));
    schema_map.insert("SignInput".to_owned(), json!(object(json!({"name":string,"file_id":uuid,"recipients":{"type":"array","minItems":1,"maxItems":100,"items":any},"meta":{},"template_id":uuid}),&["name","file_id","recipients"])));
    schema_map.insert("OfficeOpen".to_owned(), json!(object(json!({"mode":{"enum":["edit","view"],"description":"Defaults to edit when current write policy allows it, otherwise view."}}),&[])));
    schema_map.insert("OfficeLaunch".to_owned(), json!(object(json!({"action_url":{"type":"string","format":"uri"},"launch_url":{"type":"string","format":"uri"},"method":{"const":"POST"},"fields":object(json!({"access_token":{"type":"string","description":"Short-lived signed capability; submit using a browser POST form."},"access_token_ttl":string}),&["access_token","access_token_ttl"]),"access_token_ttl":{"type":"integer","description":"Absolute expiry as Unix milliseconds."},"wopi_src":{"type":"string","format":"uri"},"mode":{"enum":["edit","view"]},"filename":string}),&["launch_url","method","fields","access_token_ttl","wopi_src","mode","filename"])));
    schema_map.insert(
        "WorkflowRunInput".to_owned(),
        json!(object(json!({"input":{},"idempotency_key":string}), &[])),
    );
    schema_map.insert(
        "ApprovalInput".to_owned(),
        json!(object(
            json!({"node_id":string,"approved":boolean,"comment":string}),
            &["node_id", "approved"]
        )),
    );
    schema_map.insert(
        "ResumeInput".to_owned(),
        json!(object(
            json!({"node_id":string,"outcome":{"enum":["completed","failed","retry"]},"output":{}}),
            &["node_id", "outcome"]
        )),
    );
    schema_map.insert("DispatchInput".to_owned(), json!(object(json!({"task":string,"input":{},"source_ids":{"type":"array","maxItems":100,"items":uuid},"idempotency_key":string}),&[])));
    schema_map.insert("WorkerCallback".to_owned(), json!(object(json!({"event_id":string,"run_id":uuid,"node_id":string,"status":{"enum":["progress","completed","failed","cancelled"]},"output":{},"progress":{}}),&["event_id","run_id","node_id","status"])));
    schema_map.insert("WorkflowDefinition".to_owned(), json!(object(json!({"trigger":object(json!({"type":{"enum":["manual","schedule","event"]},"cron":string,"timezone":string,"event":string,"source_id":uuid}),&["type"]),"nodes":{"type":"array","maxItems":500,"items":object(json!({"id":string,"type":{"enum":["trigger","start","set","condition","http","agent","task","approval","delay","parallel","merge","foreach","finish"]},"label":string,"config":any}),&["id","type"])},"edges":{"type":"array","items":object(json!({"source":string,"target":string,"source_handle":{"enum":["true","false"]}}),&["source","target"])}}),&["trigger","nodes","edges"])));
    schema_map.insert("IntegrationConfig".to_owned(), json!(object(json!({"endpoint":{"type":"string","format":"uri"},"enabled":boolean,"capabilities":strings,"allowed_hosts":strings,"allowed_methods":strings,"allowed_path_prefixes":strings,"allow_private_network":{"type":"boolean","default":false},"timeout_seconds":{"type":"integer","minimum":1,"maximum":300},"secret":{"type":"string","writeOnly":true},"secret_header":string,"secret_prefix":string,"callback_secret":{"type":"string","writeOnly":true},"headers":{"type":"object","additionalProperties":string}}),&[])));
    schema_map.insert("JsonRpcRequest".to_owned(), json!(object(json!({"jsonrpc":{"const":"2.0"},"id":{"type":["string","number","null"]},"method":{"enum":["initialize","ping","tools/list","tools/call"]},"params":any}),&["jsonrpc","method"])));
    schema_map.insert("JsonRpcResponse".to_owned(), json!(object(json!({"jsonrpc":{"const":"2.0"},"id":{},"result":{},"error":object(json!({"code":{"type":"integer"},"message":string}),&["code","message"])}),&["jsonrpc","id"])));
    schema_map.insert(
        "ScimUser".to_owned(),
        json!(object(
            json!({"schemas":strings,"userName":string,"displayName":string,"active":boolean}),
            &[]
        )),
    );
    schema_map.insert("ScimGroup".to_owned(), json!(object(json!({"schemas":strings,"displayName":string,"members":{"type":"array","items":object(json!({"value":uuid}),&["value"])}}),&["displayName"])));
    schema_map.insert("ScimPatch".to_owned(), json!(object(json!({"schemas":strings,"Operations":{"type":"array","items":object(json!({"op":{"enum":["add","remove","replace"]},"path":string,"value":{}}),&["op","value"])}}),&["Operations"])));
    let mut values = Value::Object(schema_map);
    values["GcStatus"] = object(
        json!({"grace_hours":{"type":"integer","const":24},"states":{"type":"array","items":object(json!({"status":{"type":"string"},"count":{"type":"integer","minimum":0},"next_attempt":{"type":["string","null"],"format":"date-time"}}),&["status","count","next_attempt"])}}),
        &["grace_hours", "states"],
    );
    values["CalendarResolution"] = object(
        json!({"resolution":{"enum":["local","remote"]},"expected_version":{"type":"integer"}}),
        &["resolution", "expected_version"],
    );
    values["VersionUpload"] = values["MultipartUpload"].clone();
    values["VersionUpload"]["required"] = json!(["file", "expected_version"]);
    values["FileVersions"] = object(
        json!({"current_version":{"type":"integer"},"versions":array("Object")}),
        &["current_version", "versions"],
    );
    values["Resource"]["description"] = json!(
        "Versioned object. Data is validated by kind. Workflow data follows WorkflowDefinition; agent and HTTP connector data follows IntegrationConfig. Credentials and provider-managed secret fields are write-only/redacted. File storage paths and secret bytes are not public contract fields."
    );
    values["NewResource"]["description"] = json!(
        "Use dedicated APIs for file upload, published workflow state, public form submissions, signature requests and calendar OAuth. Generic resource creation cannot bypass those validations."
    );
    values
}

fn add_parameter(doc: &mut Value, path: &str, method: &str, value: Value) {
    doc["paths"][path][method]["parameters"]
        .as_array_mut()
        .unwrap()
        .push(value);
}
fn decorate(doc: &mut Value) {
    let string = json!({"type":"string"});
    for path in ["/resources/{kind}", "/search", "/tables/{id}/records"] {
        add_parameter(
            doc,
            path,
            "get",
            parameter(
                "q",
                "query",
                false,
                string.clone(),
                "Name/content search query",
            ),
        );
    }
    add_parameter(
        doc,
        "/tables/{id}/records",
        "get",
        parameter(
            "view",
            "query",
            false,
            string.clone(),
            "Saved table view identifier",
        ),
    );
    for path in ["/resource/{id}", "/resource/{id}/purge"] {
        add_parameter(
            doc,
            path,
            "delete",
            parameter(
                "expected_version",
                "query",
                true,
                json!({"type":"integer","minimum":1}),
                "Current resource version; mismatch returns 409",
            ),
        );
    }
    add_parameter(
        doc,
        "/shares",
        "get",
        parameter(
            "resource_id",
            "query",
            false,
            json!({"type":"string","format":"uuid"}),
            "Limit links to one resource",
        ),
    );
    for path in [
        "/public/shares/{token}",
        "/public/shares/{token}/download",
        "/public/shares/{token}/preview",
        "/public/shares/{token}/items",
        "/public/shares/{token}/files/{id}/download",
    ] {
        add_parameter(
            doc,
            path,
            "get",
            parameter(
                "X-Share-Password",
                "header",
                false,
                string.clone(),
                "Password for a password-protected sharing link",
            ),
        );
    }
    add_parameter(
        doc,
        "/public/shares/{token}/items",
        "get",
        parameter(
            "parent_id",
            "query",
            false,
            json!({"type":"string","format":"uuid"}),
            "Readable folder inside the shared subtree",
        ),
    );
    for path in ["/uploads/{id}", "/uploads/{id}/chunk"] {
        add_parameter(
            doc,
            path,
            "patch",
            parameter(
                "Upload-Offset",
                "header",
                true,
                json!({"type":"integer","minimum":0}),
                "Exact persisted byte offset; chunks are limited to 8 MiB",
            ),
        );
        add_parameter(
            doc,
            path,
            "patch",
            parameter(
                "Upload-Checksum",
                "header",
                false,
                string.clone(),
                "Optional sha256 followed by a space and base64 digest of this chunk",
            ),
        );
    }
    for method in ["get", "post"] {
        add_parameter(
            doc,
            "/public/forms/{id}",
            method,
            parameter(
                "token",
                "query",
                false,
                string.clone(),
                "Access token from a published link form URL",
            ),
        );
    }
    for path in ["/auth/sso/callback", "/public/calendar/oauth/{provider}"] {
        for name in ["state", "code"] {
            add_parameter(
                doc,
                path,
                "get",
                parameter(
                    name,
                    "query",
                    true,
                    string.clone(),
                    "One-time OAuth/OIDC callback value",
                ),
            );
        }
    }
    for method in ["get", "post"] {
        for path in [
            "/public/wopi/files/{id}",
            "/public/wopi/files/{id}/contents",
        ] {
            doc["paths"][path][method]["security"] = json!([{"wopiCapability":[]}]);
            if method == "post" {
                add_parameter(
                    doc,
                    path,
                    method,
                    parameter(
                        "X-WOPI-Lock",
                        "header",
                        false,
                        string.clone(),
                        "Matching active lock for saves; lock operations specify their requested lock",
                    ),
                );
            }
            doc["paths"][path][method]["responses"]["409"] = json!({"description":"WOPI lock or content-version conflict; current lock returned in X-WOPI-Lock","headers":{"X-WOPI-Lock":{"schema":{"type":"string"}},"X-WOPI-LockFailureReason":{"schema":{"type":"string"}}}});
        }
    }
    add_parameter(
        doc,
        "/public/wopi/files/{id}",
        "post",
        parameter(
            "X-WOPI-Override",
            "header",
            true,
            json!({"enum":["LOCK","REFRESH_LOCK","UNLOCK","GET_LOCK","PUT_RELATIVE","RENAME_FILE"]}),
            "WOPI operation; an old lock can be replaced with X-WOPI-OldLock",
        ),
    );
    for name in [
        "X-WOPI-OldLock",
        "X-WOPI-RequestedName",
        "X-WOPI-RelativeTarget",
        "X-WOPI-SuggestedTarget",
    ] {
        add_parameter(
            doc,
            "/public/wopi/files/{id}",
            "post",
            parameter(
                name,
                "header",
                false,
                string.clone(),
                "Operation-specific WOPI header",
            ),
        );
    }
    doc["paths"]["/public/wopi/files/{id}"]["post"]["requestBody"]["required"] = json!(false);
    doc["paths"]["/public/callbacks/{id}"]["post"]["security"] = json!([{"workerSignature":[]}]);
    add_parameter(
        doc,
        "/public/callbacks/{id}",
        "post",
        parameter(
            "X-Blueprint-Timestamp",
            "header",
            true,
            string.clone(),
            "Unix seconds; accepted within 300 seconds of server time",
        ),
    );
    doc["paths"]["/public/sign/webhook"]["post"]["security"] = json!([{"documensoSecret":[]}]);
    for (name, schema) in [
        ("filter", string.clone()),
        ("startIndex", json!({"type":"integer","minimum":1})),
        ("count", json!({"type":"integer","minimum":1,"maximum":200})),
    ] {
        add_parameter(
            doc,
            "/scim/v2/Users",
            "get",
            parameter(
                name,
                "query",
                false,
                schema,
                "Supported SCIM user-list parameter; filter supports userName eq only",
            ),
        );
    }
    doc["paths"]["/mcp"]["post"]["description"] = json!(
        "Implements initialize, ping, tools/list and tools/call. Available tools: blueprint_search, blueprint_get, blueprint_create, blueprint_update, blueprint_workflow_run and blueprint_run_status. No built-in AI employee is provided. Tool authorization uses current role, bearer constraints, modules and resource/content policies. Responses are JSON-RPC JSON rather than an SSE transport."
    );
    doc["paths"]["/collaboration/{id}/ws"]["get"]["description"] = json!(
        "Uses the authenticated HTTP cookie or Authorization header for the upgrade. Notes/canvases exchange Yjs v1 updates as base64 JSON {type:'update',update:'...'} or binary frames. The server sends persisted state, accepted update/version/actor events and errors; account, token and ACL state are rechecked during the session."
    );
    for path in ["/setup", "/login", "/activate"] {
        doc["paths"][path]["post"]["responses"]["200"]["headers"] = json!({"Set-Cookie":{"schema":{"type":"string"},"description":"HttpOnly blueprint_session cookie; Secure when configured public URL uses HTTPS"}});
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_exposed_route_has_a_contract_entry() {
        let doc = document();
        // Public module routers are nested under /public; their authenticated
        // counterparts are scanned separately rather than guessing from names.
        for (source, public_marker) in [
            (include_str!("auth.rs"), false),
            (include_str!("core.rs"), false),
            (include_str!("content.rs"), true),
            (include_str!("business.rs"), true),
            (include_str!("workflows.rs"), true),
            (include_str!("business/wopi.rs"), true),
            (include_str!("content/resumable.rs"), false),
            (include_str!("content/moving.rs"), false),
            (include_str!("content/gc.rs"), false),
            (include_str!("content/copying.rs"), false),
        ] {
            let boundary = if public_marker {
                source.find("pub fn public_router()").unwrap_or(usize::MAX)
            } else {
                usize::MAX
            };
            let mut offset = 0;
            for part in source.split(".route(").skip(1) {
                let before = source[offset..].find(".route(").unwrap() + offset;
                offset = before + 7;
                let Some(path) = part
                    .trim_start()
                    .strip_prefix('"')
                    .and_then(|s| s.split('"').next())
                else {
                    continue;
                };
                let path = if before >= boundary {
                    format!("/public{path}")
                } else {
                    path.to_owned()
                };
                assert!(
                    doc["paths"].get(&path).is_some(),
                    "Missing OpenAPI path: {path}"
                );
            }
        }
    }
    #[test]
    fn every_reference_resolves_and_protocol_auth_is_explicit() {
        let doc = document();
        fn visit(value: &Value, root: &Value) {
            match value {
                Value::Object(map) => {
                    if let Some(reference) = map.get("$ref").and_then(Value::as_str) {
                        assert!(
                            root.pointer(reference.strip_prefix('#').unwrap()).is_some(),
                            "Unresolved {reference}"
                        );
                    }
                    for child in map.values() {
                        visit(child, root);
                    }
                }
                Value::Array(items) => {
                    for item in items {
                        visit(item, root);
                    }
                }
                _ => {}
            }
        }
        visit(&doc, &doc);
        assert_eq!(
            doc["paths"]["/public/wopi/files/{id}"]["get"]["security"],
            json!([{"wopiCapability":[]}])
        );
        assert_eq!(
            doc["paths"]["/public/callbacks/{id}"]["post"]["security"],
            json!([{"workerSignature":[]}])
        );
        assert_eq!(
            doc["paths"]["/resource/{id}"]["patch"]["requestBody"]["content"]["application/json"]["schema"],
            reference("UpdateResource")
        );
        assert!(doc["paths"].as_object().unwrap().len() > 85);
    }
}
