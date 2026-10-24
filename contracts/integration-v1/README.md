# Blueprint Integration Contract 1.0

This directory is the versioned HTTP contract for independent services. It is
separate from `blueprint-agent/1` and does not change the product release version.
The first implementation is the native backend only: one company per private
data directory, with independently scoped integration instances. It does not add
a model runtime, a customer-service implementation, WhatsApp access or deployment.

- `openapi.json`: OpenAPI 3.1 operations, identities, responses and error codes.
- `schemas/`: JSON Schema 2020-12 request and response definitions.
- `fixtures/`: entirely fictional shared acceptance and rejection examples.
- `SHA256SUMS`: SHA-256 of every contract file except itself, sorted by path.

Pin the repository commit, this directory's files and `SHA256SUMS` in a client
release. A moving branch name is not a contract pin. Verify the file hashes before
running shared fixtures. See [the integration guide](../../docs/INTEGRATION_GUIDE.md)
for authentication, replay, knowledge authority, schema migration and recovery.

Backward-compatible additions keep major version 1 and existing required fields,
meaning and status transitions. Changes that remove fields, change authority,
invalidate a prior accepted request or change cursor semantics require a new
contract major version and a migration path. Clients ignore unknown response
fields but must not invent unsupported request fields or action names. Primitive
capability schemas are a deliberately limited subset of JSON Schema; the backend
never downloads `$ref`, executes code or embeds remote HTML from a manifest.

HTTP success means the receiver durably stored an event or command. It is not
proof that an external action ran, that a message was sent, or that it arrived.
The simulator only demonstrates the fictional HTTP persistence/confirmation loop.
