#!/usr/bin/env bash
set -euo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
private_dir=${1:-"$repo_dir/../blueprint-private"}
profile=${2:-basic}
if [[ $# -ge 2 ]]; then shift 2; elif [[ $# -eq 1 ]]; then shift; fi
if [[ ! -f "$private_dir/.env" ]]; then
  printf 'Run scripts/v2/configure.sh first. Missing %s/.env\n' "$private_dir" >&2
  exit 1
fi
private_dir=$(cd -- "$private_dir" && pwd)
compose=(docker compose --project-directory "$repo_dir" --env-file "$private_dir/.env" -f "$repo_dir/compose.yaml")
case "$profile" in
  basic) ;;
  documents) compose+=(-f "$repo_dir/deploy/compose.documents.yaml" --profile documents) ;;
  enterprise) compose+=(-f "$repo_dir/deploy/compose.documents.yaml" -f "$repo_dir/deploy/compose.enterprise.yaml" --profile enterprise) ;;
  *) printf 'Unknown profile: %s. Use basic, documents or enterprise.\n' "$profile" >&2; exit 2 ;;
esac
if [[ -n "${CODEX_PROXY_CERT:-}" ]]; then
  [[ -f "$CODEX_PROXY_CERT" ]] || { printf 'Configured build CA does not exist.\n' >&2; exit 1; }
  compose+=(-f "$repo_dir/deploy/compose.proxy-ca.yaml")
fi
if [[ $# -eq 0 ]]; then set -- up -d --build; fi
exec "${compose[@]}" "$@"
