# Metadata capacity benchmark

`scripts/v2/bench.py` measures actual authenticated HTTP requests against an
isolated server process. It creates a randomly named `blueprint_bench_<uuid>`
database, lets the binary apply its normal migrations, and drops that database
after stopping its server. It never accepts an existing application database or
an arbitrary application URL. Use a dedicated development PostgreSQL instance
with `CREATEDB` permission. No production data or credentials are required.

Build the desired binary first. A release build is appropriate for deployment
sizing; the first development baseline below records its actual build profile.

```sh
cargo build --locked --release
python3 scripts/v2/bench.py \
  --binary target/release/blueprint-backend \
  --pg-host 127.0.0.1 --pg-port 5432 --pg-user blueprint_v2 \
  --output /tmp/blueprint-metadata-benchmark.json
```

If `psql` is available only inside an existing local PostgreSQL container, add
`--postgres-container <container-name>`. The app still connects to the configured
host/port. This option uses the local Docker daemon and never pulls an image.
The local `psql` variant inherits ordinary libpq credential settings. Keep output
JSON outside Git if adding private environment details to it.

The default workload has 10,000 synthetic active members, 200 authenticated cookie
sessions with persistent connections, 12,200 file-metadata rows, and 10 department
groups. Each active member has private content and group-readable content.
Fixtures are inserted directly to keep setup bounded; no permission checks,
triggers or authentication middleware are disabled. Their declared scan state is
synthetic fixture metadata. No real file bytes or malware scans are exercised.

Before measurement, all 200 identities pass actual HTTP checks for an allowed and
a denied private object, and an allowed and a denied department object: 800 checks
in total. Every measured list response is also checked for unauthorized objects.
The open-loop workload offers 40 requests/second for 60 seconds, rotating each
identity through `/me`, a private resource read, file-metadata listing and filtered
department listing. Results include response p50/p95, scheduled-to-completion
p50/p95, dispatch delay, completed throughput and error rate. Scheduled latency
includes client queue delay so a slow server cannot hide behind the load generator.

The default acceptance gate requires zero errors or ACL leaks, at least 95% of
offered throughput, and scheduled p95 at most 500 ms. Change the rate, duration or
latency budget explicitly for other scenarios. A failed gate produces a nonzero
exit status and retains truthful aggregate results.

Two hundred sessions means 200 identities and persistent connections, with a
maximum of 32 busy load-generator threads. It does not represent 200 simultaneously
busy requests. This test excludes uploads, scanning, OCR, preview conversion,
full-text indexing, Office sessions, background-worker throughput, external AI,
calendar synchronization, TLS, WAN and backup traffic. Measure those separately
before making production sizing commitments. The proposed 8 CPU / 32 GiB deployment
target has not been validated merely by running this smaller development host.

## Observed development baseline, 20 October 2026

The [aggregate JSON evidence](V2-PERFORMANCE-2026-10-20.json) records a real
60-second run against `blueprint-backend 2.0.0-alpha.1`, debug profile, on a
container limited to **4 CPU and 16 GiB RAM**. PostgreSQL ran locally in the
existing development container. The measured binary SHA-256 is recorded in the
evidence. No 8 CPU / 32 GiB machine was tested.

| Measurement | Observed result |
| --- | ---: |
| Active account fixtures / browser sessions | 10,000 / 200 |
| File-metadata fixtures / department groups | 12,200 / 10 |
| Private and group HTTP isolation preflight | 800 passed |
| Offered traffic / measured requests | 40 GET/s / 2,400 |
| Completed traffic | 39.994 GET/s |
| HTTP response p50 / p95 | 27.394 / 77.584 ms |
| Scheduled-to-completion p50 / p95 | 28.051 / 78.088 ms |
| Maximum load-generator dispatch delay | 49.470 ms |
| HTTP, validation or ACL errors | 0; error rate 0% |

The default benchmark gate passed. Its server and randomly generated database
were removed afterward. This result supports the recorded metadata-read scenario
only; the exclusions above still apply.

The exact command in this environment was:

```sh
python3 scripts/v2/bench.py \
  --binary /tmp/blueprint-v2-target/debug/blueprint-backend \
  --postgres-container blueprint-v2-postgres \
  --pg-user blueprint_v2 --pg-port 55433 \
  --seconds 60 --output /tmp/blueprint-benchmark-2026-10-20.json
```
