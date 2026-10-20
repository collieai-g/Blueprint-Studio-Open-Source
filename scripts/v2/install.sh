#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  cat <<'HELP'
Usage: ./scripts/v2/install.sh [--private-dir PATH] [--profile basic|documents|enterprise]

Starts this checkout using Docker. The default is a new basic installation with
private configuration beside this program, in ../blueprint-private. Reruns reuse
its configuration, project identity, profile and persistent volumes.
No system packages are installed and no existing company data is deleted.
HELP
}
fail() { printf 'Installation stopped: %s\n' "$*" >&2; exit 1; }
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
private_dir="$repo_dir/../blueprint-private"
profile=basic
profile_explicit=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --private-dir) [[ $# -ge 2 && -n "$2" ]] || fail '--private-dir needs a path.'; private_dir=$2; shift 2 ;;
    --profile) [[ $# -ge 2 ]] || fail '--profile needs basic, documents or enterprise.'; profile=$2; profile_explicit=true; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) fail "Unknown option: $1. Use --help." ;;
  esac
done
case "$profile" in basic|documents|enterprise) ;; *) fail 'Profile must be basic, documents or enterprise.' ;; esac
[[ -f "$repo_dir/compose.yaml" && -f "$repo_dir/Dockerfile" ]] || fail 'Run this installer from a complete Blueprint source download.'
command -v docker >/dev/null 2>&1 || fail 'Install Docker Desktop (macOS/Windows) or Docker Engine with Compose and Buildx (Linux), then run this command again.'
docker_os=$(docker info --format '{{.OSType}}' 2>/dev/null) || fail 'Docker is not running or is unavailable to this account. Start Docker Desktop/Engine, then run this command again.'
[[ "$docker_os" == linux ]] || fail 'Docker must use Linux containers. Switch Docker Desktop to Linux containers, then retry.'
compose_version=$(docker compose version --short 2>/dev/null) || fail 'Docker Compose v2 is required. Update Docker Desktop or install the Compose plugin.'
[[ "$compose_version" =~ ^v?([0-9]+)\. ]] || fail 'Could not identify the Docker Compose version.'
[[ ${BASH_REMATCH[1]} -ge 2 ]] || fail 'Docker Compose v2 or newer is required.'
docker buildx version >/dev/null 2>&1 || fail 'Docker Buildx is required. Update Docker Desktop or install the Buildx plugin.'
[[ ! -e "$private_dir" || -d "$private_dir" ]] || fail 'The private configuration path is an existing file. Choose a directory.'
mkdir -p -- "$private_dir"
private_dir=$(cd -- "$private_dir" && pwd -P)
case "$private_dir/" in "$repo_dir/"*) fail 'Choose a private directory outside the Blueprint program folder.' ;; esac
marker="$private_dir/.blueprint-install-profile"
if [[ -f "$marker" ]]; then
  saved_profile=$(tr -d '\r\n' < "$marker")
  case "$saved_profile" in basic|documents|enterprise) ;; *) fail 'The saved installation profile is invalid. Inspect .blueprint-install-profile locally; no configuration was replaced.' ;; esac
  if [[ "$profile_explicit" == true && "$profile" != "$saved_profile" ]]; then
    fail "This installation uses $saved_profile. Changing profiles needs a separate reviewed upgrade; its configuration and data were preserved."
  fi
  profile=$saved_profile
elif [[ -f "$private_dir/.env" && "$profile_explicit" != true ]]; then
  fail 'Existing configuration has no saved installer profile. Rerun with --profile basic, documents or enterprise matching the existing installation. No configuration was replaced.'
fi
if [[ -f "$private_dir/.env" ]]; then
  printf 'Reusing the existing private configuration and company volumes.\n'
else
  command -v openssl >/dev/null 2>&1 || fail 'OpenSSL is required to create private configuration. Install it, then retry.'
  printf 'Creating private configuration outside the program folder.\n'
  bash "$repo_dir/scripts/v2/configure.sh" "$private_dir"
  # Only a newly generated .env is changed. Different private directories must
  # never accidentally share the default Compose project and company volumes.
  project_suffix=$(printf '%s' "$private_dir" | openssl dgst -sha256 | awk '{print substr($NF,1,12)}')
  project_name="blueprint-$project_suffix"
  temporary_env=$(mktemp "$private_dir/.env.project.XXXXXXXX")
  trap '[[ -z "${temporary_env:-}" ]] || rm -f -- "$temporary_env"' EXIT
  awk -v project="$project_name" '/^COMPOSE_PROJECT_NAME=/{print "COMPOSE_PROJECT_NAME=" project;next}{print}' "$private_dir/.env" > "$temporary_env"
  chmod 600 "$temporary_env"
  mv -- "$temporary_env" "$private_dir/.env"
  temporary_env=
fi
printf '%s\n' "$profile" > "$marker"
chmod 600 "$marker"

run_stack() (
  # Respect the private file, not another development project's exported app
  # variables. This changes only the child process; Docker contexts and global
  # environment/configuration remain untouched.
  for variable in $(compgen -e); do
    case "$variable" in
      BLUEPRINT_*|POSTGRES_PASSWORD|DOCUMENSO_*|KEYCLOAK_*|COLLABORA_*|SMTP_*|RUST_LOG|COMPOSE_*) unset "$variable" ;;
    esac
  done
  bash "$repo_dir/scripts/v2/stack.sh" "$private_dir" "$profile" "$@"
)
read_public_url() {
  local value
  value=$(awk '/^BLUEPRINT_PUBLIC_URL=/{sub(/^[^=]*=/,"");sub(/\r$/,"");print;exit}' "$private_dir/.env")
  case "$value" in \"*\") value=${value#\"}; value=${value%\"} ;; \'*\') value=${value#\'}; value=${value%\'} ;; esac
  printf '%s' "${value:-http://127.0.0.1:8787}"
}
printf 'Starting the %s installation. The first download and build can take several minutes.\n' "$profile"
run_stack up -d --build || fail 'Docker could not start the installation. Your private configuration and persistent volumes were kept; rerun the same command after resolving the Docker error.'
health=$(run_stack exec -T app curl --fail --silent --max-time 10 http://127.0.0.1:8787/health 2>/dev/null) || fail 'The app is not healthy yet. Your configuration and volumes were kept; check Docker Desktop and rerun this command.'
[[ "$health" == *'"status":"ok"'* ]] || fail 'The app did not report a healthy database connection.'
running=$(run_stack ps --status running --services 2>/dev/null) || fail 'Could not check the installation containers.'
for service in app postgres worker; do
  printf '%s\n' "$running" | awk -v wanted="$service" '$0==wanted{found=1}END{exit !found}' || fail "The $service service is not running. Your configuration and volumes were kept."
done
status=$(run_stack exec -T app curl --fail --silent --max-time 10 http://127.0.0.1:8787/api/v1/status 2>/dev/null) || fail 'The app started but its first-run status is unavailable.'
[[ "$status" == *'"initialized":true'* || "$status" == *'"initialized":false'* ]] || fail 'The app returned an invalid first-run response.'
public_url=$(read_public_url)
case "$public_url" in http://*|https://*) ;; *) fail 'Inspect BLUEPRINT_PUBLIC_URL locally; it must be an HTTP(S) browser address.' ;; esac
[[ "$public_url" != *'@'* && "$public_url" != *'?'* && "$public_url" != *'#'* && "$public_url" != *$'\n'* ]] || fail 'Inspect BLUEPRINT_PUBLIC_URL locally; use an address without credentials, query or fragment.'
printf '\nBlueprint is ready. Open: %s\n' "$public_url"
if [[ "$status" == *'"initialized":true'* ]]; then
  printf 'Sign in to your existing company. Existing configuration and data were reused.\n'
else
  printf 'Complete company setup in your browser. Open %s/.env locally and copy BLUEPRINT_SETUP_TOKEN into the setup page.\n' "$private_dir"
fi
printf 'No passwords, setup codes or API tokens have been printed.\n'
if [[ "$profile" != basic ]]; then
  printf 'Optional services still need their accounts, credentials and connection checks in the admin settings.\n'
fi
