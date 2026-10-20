#!/usr/bin/env bash
# Codex launches this command. stdout is reserved for MCP JSON-RPC only.
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
private_dir="$repo_dir/../blueprint-private"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --private-dir) [[ $# -ge 2 ]] || { printf 'Missing --private-dir value.\n' >&2; exit 2; }; private_dir=$2; shift 2 ;;
    -h|--help) printf 'Usage: mcp-stdio.sh [--private-dir DIRECTORY]\n' >&2; exit 0 ;;
    *) printf 'Unknown argument. Use --private-dir DIRECTORY.\n' >&2; exit 2 ;;
  esac
done
[[ -d "$private_dir" && -f "$private_dir/.env" ]] || { printf 'Blueprint is not configured. Run the installer first.\n' >&2; exit 1; }
private_dir=$(cd -- "$private_dir" && pwd)
token_file="$private_dir/codex-token"
[[ -f "$token_file" && -s "$token_file" ]] || { printf 'Save a scoped Blueprint API token in the private codex-token file, then retry.\n' >&2; exit 1; }
BLUEPRINT_MCP_TOKEN=$(< "$token_file")
[[ -n "$BLUEPRINT_MCP_TOKEN" && "$BLUEPRINT_MCP_TOKEN" != *[[:space:]]* ]] || { printf 'The private codex-token file must contain one nonempty token.\n' >&2; exit 1; }
export BLUEPRINT_MCP_TOKEN
profile=basic
if [[ -f "$private_dir/.blueprint-install-profile" ]]; then profile=$(< "$private_dir/.blueprint-install-profile"); fi
case "$profile" in basic|documents|enterprise) ;; *) printf 'Invalid saved installation profile.\n' >&2; exit 1 ;; esac
command -v docker >/dev/null || { printf 'Docker is required for the Blueprint MCP connection.\n' >&2; exit 1; }
# A Codex terminal may have another installation's Compose/application variables
# exported. The selected private .env is authoritative; clean only this child.
run_mcp() (
  while IFS= read -r variable; do
    case "$variable" in
      BLUEPRINT_MCP_TOKEN) ;;
      BLUEPRINT_*|COMPOSE_*|POSTGRES_PASSWORD|DOCUMENSO_*|KEYCLOAK_*|COLLABORA_*|SMTP_*|RUST_LOG) unset "$variable" ;;
    esac
  done < <(compgen -e)
  exec "$repo_dir/scripts/v2/stack.sh" "$private_dir" "$profile" exec -T -e BLUEPRINT_MCP_TOKEN app blueprint-backend mcp-stdio --url http://127.0.0.1:8787 --token-env BLUEPRINT_MCP_TOKEN
)
run_mcp
