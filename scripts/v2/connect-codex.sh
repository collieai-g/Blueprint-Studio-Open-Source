#!/usr/bin/env bash
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
private_dir="$repo_dir/../blueprint-private"
server_name=blueprint
while [[ $# -gt 0 ]]; do
  case "$1" in
    --private-dir) [[ $# -ge 2 ]] || { printf 'Missing --private-dir value.\n' >&2; exit 2; }; private_dir=$2; shift 2 ;;
    --name) [[ $# -ge 2 ]] || { printf 'Missing --name value.\n' >&2; exit 2; }; server_name=$2; shift 2 ;;
    -h|--help) printf 'Usage: connect-codex.sh [--private-dir DIRECTORY] [--name blueprint]\n'; exit 0 ;;
    *) printf 'Unknown argument. Use --help.\n' >&2; exit 2 ;;
  esac
done
[[ "$server_name" =~ ^[a-zA-Z0-9_-]{1,64}$ ]] || { printf 'Use a server name containing letters, numbers, underscores or hyphens.\n' >&2; exit 2; }
command -v codex >/dev/null || { printf 'Install Codex CLI and sign in with your own account first.\n' >&2; exit 1; }
[[ -d "$private_dir" && -f "$private_dir/.env" ]] || { printf 'Blueprint is not configured. Run the installer first.\n' >&2; exit 1; }
private_dir=$(cd -- "$private_dir" && pwd)
[[ "$private_dir" != *$'\n'* && "$private_dir" != *$'\r'* ]] || { printf 'The private directory path cannot contain line breaks.\n' >&2; exit 2; }
[[ -f "$private_dir/codex-token" && -s "$private_dir/codex-token" ]] || { printf 'In Blueprint, create a scoped API token and save it in %s/codex-token. Do not paste it into chat.\n' "$private_dir" >&2; exit 1; }
wrapper="$repo_dir/scripts/v2/mcp-stdio.sh"
# Compare Codex's own normalized JSON under invocation-only overrides. No JSON
# runtime dependency, no rewriting config files, and no tokens in arguments.
toml_quote() { local value=${1//\\/\\\\}; value=${value//\"/\\\"}; printf '"%s"' "$value"; }
codex mcp list --json >/dev/null 2>&1 || { printf 'Codex MCP configuration could not be read. Existing configuration was preserved.\n' >&2; exit 1; }
if existing=$(codex mcp get "$server_name" --json 2>/dev/null); then
  expected=$(codex \
    -c "mcp_servers.$server_name.command=$(toml_quote "$wrapper")" \
    -c "mcp_servers.$server_name.args=[$(toml_quote '--private-dir'),$(toml_quote "$private_dir")]" \
    -c "mcp_servers.$server_name.enabled=true" \
    mcp get "$server_name" --json 2>/dev/null) || { printf 'A different MCP configuration already uses this name. Use --name with a new name.\n' >&2; exit 1; }
  [[ "$existing" == "$expected" ]] || { printf 'A different or disabled MCP configuration already uses this name. Existing configuration was preserved; use --name with a new name.\n' >&2; exit 1; }
  printf 'The matching Blueprint MCP configuration already exists; no changes made.\n'
else
  codex mcp add "$server_name" -- "$wrapper" --private-dir "$private_dir"
fi
printf 'Refresh MCP in Codex or open a new Codex conversation, then verify the Blueprint tools. Registration alone does not verify the running connection.\n'
