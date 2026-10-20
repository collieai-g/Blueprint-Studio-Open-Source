# syntax=docker/dockerfile:1.7
FROM groonga/pgroonga:4.1.0-alpine-17
# COPY controls readability even when the host checkout uses a private umask.
COPY --chmod=0755 deploy/postgres-init.sh /docker-entrypoint-initdb.d/10-blueprint.sh
