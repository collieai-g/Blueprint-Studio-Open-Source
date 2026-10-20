#!/bin/sh
set -eu
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 \
    --command 'CREATE EXTENSION IF NOT EXISTS pgroonga;'

# Optional services own separate databases and roles. Runs only on a new volume.
if [ -n "${DOCUMENSO_DB_PASSWORD:-}" ]; then
    psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 \
        --set "service_password=$DOCUMENSO_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE documenso LOGIN PASSWORD %L', :'service_password') \gexec
CREATE DATABASE documenso OWNER documenso;
SQL
fi
if [ -n "${KEYCLOAK_DB_PASSWORD:-}" ]; then
    psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 \
        --set "service_password=$KEYCLOAK_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE keycloak LOGIN PASSWORD %L', :'service_password') \gexec
CREATE DATABASE keycloak OWNER keycloak;
SQL
fi
