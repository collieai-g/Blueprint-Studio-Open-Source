#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
private_dir=${1:-"$repo_dir/../blueprint-private"}
mkdir -p -- "$private_dir"
private_dir=$(cd -- "$private_dir" && pwd)
if [[ -e "$private_dir/.env" ]]; then
  printf 'Existing private configuration preserved: %s/.env\n' "$private_dir" >&2
  exit 1
fi
if [[ -e "$private_dir/secrets/signing.p12" || -e "$private_dir/secrets/s3.json" ]]; then
  printf 'Existing service secrets preserved. Choose a new private directory or restore its .env.\n' >&2
  exit 1
fi
command -v openssl >/dev/null || { printf 'OpenSSL is required to generate deployment secrets.\n' >&2; exit 1; }
mkdir -p -- "$private_dir/secrets" "$private_dir/backups"
chmod 700 "$private_dir" "$private_dir/secrets" "$private_dir/backups"
secret() { openssl rand -hex 32; }
postgres_password=$(secret)
setup_token=$(secret)
master_key=$(secret)
s3_access=$(openssl rand -hex 12)
s3_secret=$(secret)
documenso_db=$(secret)
documenso_auth=$(secret)
documenso_key=$(secret)
documenso_secondary=$(secret)
documenso_signing=$(secret)
documenso_webhook=$(secret)
keycloak_db=$(secret)
keycloak_admin=$(secret)
collabora_admin=$(secret)

cat > "$private_dir/.env" <<EOF
# Private deployment configuration. Keep this directory outside Git.
COMPOSE_PROJECT_NAME=blueprint-workspace
POSTGRES_PASSWORD=$postgres_password
BLUEPRINT_SETUP_TOKEN=$setup_token
BLUEPRINT_MASTER_KEY=$master_key
BLUEPRINT_PUBLIC_URL=http://127.0.0.1:8787
BLUEPRINT_PORT=8787
BLUEPRINT_LISTEN_ADDRESS=127.0.0.1
BLUEPRINT_SECRETS_DIR="$private_dir/secrets"
BLUEPRINT_S3_ACCESS_KEY=$s3_access
BLUEPRINT_S3_SECRET_KEY=$s3_secret
BLUEPRINT_S3_BUCKET=blueprint
BLUEPRINT_S3_REGION=us-east-1
DOCUMENSO_DB_PASSWORD=$documenso_db
DOCUMENSO_AUTH_SECRET=$documenso_auth
DOCUMENSO_ENCRYPTION_KEY=$documenso_key
DOCUMENSO_SECONDARY_KEY=$documenso_secondary
DOCUMENSO_SIGNING_PASSWORD=$documenso_signing
BLUEPRINT_DOCUMENSO_WEBHOOK_SECRET=$documenso_webhook
BLUEPRINT_DOCUMENSO_API_TOKEN=
BLUEPRINT_DOCUMENSO_PUBLIC_URL=http://127.0.0.1:3001
KEYCLOAK_DB_PASSWORD=$keycloak_db
KEYCLOAK_ADMIN_USER=blueprint-admin
KEYCLOAK_ADMIN_PASSWORD=$keycloak_admin
BLUEPRINT_KEYCLOAK_PUBLIC_URL=http://127.0.0.1:8081
BLUEPRINT_OIDC_ISSUER=
BLUEPRINT_OIDC_REALM=blueprint
BLUEPRINT_OIDC_CLIENT_ID=blueprint
BLUEPRINT_OIDC_CLIENT_SECRET=
COLLABORA_ADMIN_USER=blueprint-admin
COLLABORA_ADMIN_PASSWORD=$collabora_admin
BLUEPRINT_COLLABORA_PUBLIC_URL=http://127.0.0.1:9980
BLUEPRINT_COLLABORA_WOPI_ORIGIN=http://app:8787
BLUEPRINT_GOOGLE_CLIENT_ID=
BLUEPRINT_GOOGLE_CLIENT_SECRET=
BLUEPRINT_MICROSOFT_CLIENT_ID=
BLUEPRINT_MICROSOFT_CLIENT_SECRET=
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_NAME=Blueprint Studio
SMTP_FROM_ADDRESS=
EOF
cat > "$private_dir/secrets/s3.json" <<EOF
{"identities":[{"name":"blueprint","credentials":[{"accessKey":"$s3_access","secretKey":"$s3_secret"}],"actions":["Admin","Read","List","Tagging","Write"]}]}
EOF

cert_dir=$(mktemp -d "$private_dir/secrets/cert.XXXXXXXX")
trap 'rm -rf -- "$cert_dir"' EXIT
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
  -subj '/CN=Blueprint Studio document integrity/O=Self-hosted Blueprint Studio' \
  -keyout "$cert_dir/key.pem" -out "$cert_dir/cert.pem" >/dev/null 2>&1
export DOCUMENSO_SIGNING_PASSWORD=$documenso_signing
openssl pkcs12 -export -out "$private_dir/secrets/signing.p12" \
  -inkey "$cert_dir/key.pem" -in "$cert_dir/cert.pem" \
  -passout env:DOCUMENSO_SIGNING_PASSWORD >/dev/null 2>&1
unset DOCUMENSO_SIGNING_PASSWORD
chmod 600 "$private_dir/.env"
# Container processes use their own unprivileged UIDs. The parent directories
# remain 0700 on the host; the two explicitly mounted service files are readable
# by the intended container UID. The P12 itself is encrypted with a separate key.
chmod 644 "$private_dir/secrets/s3.json" "$private_dir/secrets/signing.p12"
printf 'Private configuration created in %s\n' "$private_dir"
printf 'Open that .env locally to copy BLUEPRINT_SETUP_TOKEN into the first-run setup form.\n'
printf 'No passwords or tokens have been printed. Keep the entire private directory in your backups.\n'
