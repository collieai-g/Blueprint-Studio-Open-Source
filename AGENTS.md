# Blueprint Studio Backend v2

## Current user-approved scope

Fresh Rust rewrite with a TypeScript web interface, informed by public Box product evidence. Each installation serves one company or individual, including departments, groups and external collaborators. The user explicitly chose a fresh start; v1 Python/SQLite, CLI and offline compatibility are not required. Never touch existing private company data. The retired v1 runtime, offline bundle, tests and deployment files remain available in Git history only.

- `src/`: Rust Axum/Tokio/SQLx modular core; one binary for server, worker and administration.
- `ui/`: React/TypeScript/Vite; Rust serves compiled assets.
- `migrations/`: forward-only PostgreSQL migrations. Company files, credentials, keys and backups stay outside Git.
- `compose.yaml`, `deploy/`, `scripts/v2/`: free self-hosted basic/document/enterprise profiles. Optional services must truthfully report missing configuration or failures.
- `docs/V2-CONTRACT.md`: shared API and module implementation contract.
- All enabled buttons must implement persisted behavior. Never fabricate AI output, scans, signatures, calendar connections or deployment success.
- External AI employees are separate services. Build their permissioned interfaces and dispatch/results, not fictional employee implementations.
- Enforce current user/token status, scopes, resource ACLs and module state across HTTP, jobs, collaboration and MCP. UI visibility is not authorization.
- Public Box SDK source is reference material under its own license; do not incorporate it into this independent open-source replacement. Keep original Blueprint branding and component implementations.
- Native synchronization and device attestation follow the web release.
- Validate with `cargo fmt --all -- --check`, `cargo check --locked --all-targets`, `cargo test --locked --all-targets`, and `npm --prefix ui run build`.
- Integration tests use only disposable databases/data. Report which external services were actually exercised; Compose validation is not runtime proof.
- Work on the rewrite branch; do not merge or deploy as a build side effect.

## Product and repository boundaries

- Public product name: Blueprint Studio Backend; Chinese name: 藍圖工作室（後台）. Keep original Blueprint branding.
- Preserve `LICENSE` and reusable artwork in `assets/icons/`; do not reintroduce a Python runtime or compatibility layer.
- New installations and disposable test data only. Never migrate, overwrite, delete or inspect private company content as a rewrite side effect.
- Tests belong in `tests/v2/`, Rust integration tests and module tests. The `v2.yml` workflow is the active verification/build workflow.
- Installation secrets, signing keys and backup exports belong in an external private directory. Never print their values in logs, tool output or reports.
- `backup`/`restore` require a maintenance window with writers stopped. External object-store and optional service data need their own version-preserving backups.
