# syntax=docker/dockerfile:1.7
ARG NODE_IMAGE=node:24.19.0-bookworm-slim
ARG RUST_IMAGE=rust:1.99.1-slim-bookworm
ARG RUNTIME_IMAGE=postgres:17.11-bookworm

FROM ${NODE_IMAGE} AS web
WORKDIR /build/ui
COPY ui/package.json ui/package-lock.json ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -s /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --strict-ssl=true
COPY ui/ ./
RUN npm run build

FROM ${RUST_IMAGE} AS rust
WORKDIR /build
COPY Cargo.toml Cargo.lock rust-toolchain.toml ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY docs/CODEX-WORKSPACE.md ./docs/CODEX-WORKSPACE.md
RUN --mount=type=secret,id=proxy_ca \
    --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/build/target \
    if [ -s /run/secrets/proxy_ca ]; then export CARGO_HTTP_CAINFO=/run/secrets/proxy_ca; fi; \
    cargo build --release --locked && install -Dm755 target/release/blueprint-backend /out/blueprint-backend

# Supplies a matching pg_dump/pg_restore client. The database entrypoint is replaced.
FROM ${RUNTIME_IMAGE} AS runtime
RUN --mount=type=secret,id=proxy_ca \
    if [ -s /run/secrets/proxy_ca ]; then \
      cat /etc/ssl/certs/ca-certificates.crt /run/secrets/proxy_ca > /tmp/build-ca.pem; \
      printf 'Acquire::https::CaInfo "/tmp/build-ca.pem";\n' > /etc/apt/apt.conf.d/99-build-ca; \
    fi; \
    apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* /tmp/build-ca.pem /etc/apt/apt.conf.d/99-build-ca \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin blueprint \
    && mkdir -p /app/ui/dist /data /backups && chown -R blueprint:blueprint /app /data /backups
WORKDIR /app
COPY --from=rust --chown=blueprint:blueprint /out/blueprint-backend /usr/local/bin/blueprint-backend
COPY --from=web --chown=blueprint:blueprint /build/ui/dist/ /app/ui/dist/
ENV BLUEPRINT_BIND=0.0.0.0:8787 \
    BLUEPRINT_DATA_DIR=/data \
    BLUEPRINT_UI_DIR=/app/ui/dist \
    RUST_LOG=blueprint_backend=info,tower_http=info
USER blueprint
EXPOSE 8787
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail --silent http://127.0.0.1:8787/health > /dev/null || exit 1
ENTRYPOINT ["blueprint-backend"]
CMD ["server"]
