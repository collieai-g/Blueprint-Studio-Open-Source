# Shared feedback harness

Build a small, executable feedback loop: reproduce, fix, regress, collect evidence,
and prepare a sanitized upstream handoff. This is reusable source, not an instance.

- Keep all instance bindings, observations, screenshots, execution receipts, raw
  outputs, databases, backups and credentials outside this project and Git.
- Never operate the running workbench, change its source, bind to its ports, use
  its credentials, or read its database as part of harness regression checks.
  Use synthetic data and independently allocated loopback ports.
- Separate an observed client compatibility failure from a bug introduced by a
  local workaround. Do not claim all clients lack a capability from one failure.
- Use the existing Blueprint lifecycle plan as a referenced contract. Do not
  implement a parallel backup, deployment, permission or company-data engine.
- A check runner may execute only declared argv, shell=False, with bounded time.
  Persist return codes and output digests; never persist subprocess output or
  passwords. Environment values and browser cookies are never exported.
- Hash explicit inputs and the actual runtime. Reuse a passed check only when
  the full validation key matches. Unknown outcomes require inspection.
- Browser evidence supplied by another client is an attestation, distinct from
  a runner-executed check. Historical receipts do not prove a new live session.
- Public bundles use an explicit allowlist of reviewed generic source and
  synthetic fixtures. Never include private evidence, instance names, local user
  paths, credentials or deployment state. Fail closed on unsafe bundle inputs.
- No automatic GitHub mutation, PR creation, merge, release or deployment. The
  bundle is a reviewable handoff, with public and instance statuses separate.
- Use clear Traditional Chinese in user-facing documentation. Keep the harness
  dependency-free on Python 3.12; browser regression uses documented CUA APIs.

Verification: python -X utf8 -m unittest discover -s tests -v.
