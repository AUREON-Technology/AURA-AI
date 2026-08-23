# Phase A1 — First Boot of the Production Compose File

Run 2026-08-23 against `a2a47b5`, in a disposable 4-core / 15 GB / 30 GB Linux container with
throwaway secrets. Roadmap reference: [`docs/AURA_UPGRADE_ROADMAP.md`](../AURA_UPGRADE_ROADMAP.md)
Phase A1.

## Status: A1 is de-risked, not complete

**This was not a production deployment.** No VPS, no domain, no TLS, no real credentials. A1's
exit criterion — `deploy/docker-compose.prod.yml` running on the real host — is still open.

What this run does establish is that the file had never been built or booted, and now has been.
Four defects and two environment assumptions surfaced in the first two hours, which is the
argument for doing this before Phases B–G rather than after.

## What was proven to work

| Step | Result |
| --- | --- |
| `docker compose config` resolves | Clean; only `AURA_DOMAIN` unset |
| `aura-gateway` image builds from `aura_backend/Dockerfile` | Succeeded — first build ever |
| Data tier boots (postgres 16, redis 7, qdrant) | All healthy |
| Gateway boots against the real data tier | Healthy; 10 tools, 16 prompts, 2 uvicorn workers |
| Secret hydration via `*_FILE` | Works, including composing `AURA_DATABASE_URL` from the password file |
| `alembic upgrade head` through the gateway image | Applied 0001→0003 cleanly |
| `GET /health/ready` with every dependency up | **200** |
| `POST /api/v3/chat` end-to-end on `qwen2.5:3b-instruct-q4_K_M` | **200**, correct answer, real token accounting |

The pipeline runs. Kernel, context assembly, provider routing, Postgres, Qdrant, Redis and Ollama
all work together, which had never been demonstrated.

## Finding 1 — Readiness returns 200 against an unmigrated database

**Severity: high. This is the one to fix before the real deploy.**

`docs/guides/provisioning-runbook.md` §6 states:

> Order matters. The gateway will not start cleanly against an unmigrated database.

That is false. The gateway started, reported healthy, and answered `/health/ready` with **200**
while the database contained only three tables — `aura_documents`, `aura_chunks`, `aura_memories`,
which `aura_core`'s own Postgres adapters create on startup. All eight platform tables were
absent: `users`, `tasks`, `calendar_events`, `finance_records`, `health_records`, `messages`,
`insights`, `memories`, and `alembic_version` itself.

Running the runbook's §6b step produced the missing eight (3 tables → 12).

The readiness contract checks providers, index identity, tool count and prompt count. It does not
check schema. So an operator who skips or mis-orders §6b gets a gateway that nginx will route
traffic to and that reports itself ready, which then fails on the first `/api/v3/sync` or
`/api/v3/auth` call. That is precisely the silent-degradation mode the readiness split exists to
prevent.

**Resolved.** Readiness now compares the stamped `alembic_version` against the head revision of
the migrations packaged in the image, and returns 503 with `checks.schema` when they disagree.
Because the container healthcheck polls that endpoint and nginx declares
`depends_on: gateway: condition: service_healthy`, an unmigrated schema now stops the edge from
starting rather than exposing a gateway that cannot serve. The runbook's claim was corrected to
describe what actually happens.

An unreachable database deliberately does *not* gate: that condition is transient and the chat
path still serves from Qdrant, Redis and the model, so failing readiness over a blip would turn a
partial degradation into an outage. A stale schema is operator-caused and persistent, which is
why only it is treated as fatal.

## Finding 2 — The gateway image ships 2.7 GB of CUDA libraries it cannot use

The built image is **8.88 GB**. Inside it:

| Path | Size |
| --- | --- |
| `site-packages/nvidia/*` | **2.7 GB** |
| `site-packages/torch` | 1.2 GB |
| `/root/.local` total | 5.4 GB |

`requirements.lock` pins 22 `nvidia-*` packages. They arrive as transitive dependencies of the
default (CUDA) torch wheel, which itself comes from `sentence-transformers` — the configured
embedding provider (`BAAI/bge-small-en-v1.5`). Torch is genuinely needed; the CUDA build is not,
on a CPU-only VPS.

Compiling the lockfile against `https://download.pytorch.org/whl/cpu` would drop the `nvidia-*`
set and roughly 2.7 GB. This matters more than image hygiene: four services
(`gateway`, `worker-realtime`, `worker-ingestion`, `beat`) build from this context, and with
`ollama/ollama` at 8.44 GB the stack needs well over 17 GB of images before any data.

Disk exhaustion during the worker image export is what stopped this run from booting
`worker-realtime` and `beat` — see Not Verified below.

## Finding 3 — First real latency numbers

Measured through `POST /api/v3/chat`, `use_knowledge`/`use_memory`/`use_tools` all false, so this
is close to a pipeline floor rather than a representative workload.

| Turn | Wall time | Output tokens |
| --- | --- | --- |
| 1 (cold — model load) | **20.2 s** | 2 |
| 2 (warm) | 5.3 s | 2 |
| 3 (warm) | 5.2 s | 2 |
| 4 (warm) | 5.5 s | 2 |

**~5.3 s warm to produce two tokens** from a 191-token prompt, on 3 shared CPU cores.

Set against the figure that currently circulates — p95 **4.56 ms** — the gap is about three orders
of magnitude. Both numbers are correct about different things: 4.56 ms measured pipeline overhead
with in-memory adapters and an echo provider, and this measures a real turn. The point is that the
first is not a latency budget, and nothing in the repo previously said what the second was.

Conditions worth stating: 3 CPU cores (the VPS allots 6), a shared sandbox host, cold page cache,
and a 3B Q4 model. The VPS should do better. It will not do 1000× better.

## Finding 4 — Bootstrap and compose assume an 8-core host

`docker-compose.prod.yml` sets `ollama.deploy.resources.limits.cpus: "6.0"`. Docker refuses the
container outright on any host with fewer than 6 cores:

```
Error response from daemon: range of CPUs is from 0.01 to 4.00, as there are only 4 CPUs available
```

This is correct for the intended VPS and is documented in a comment. It is recorded here because
it is a hard failure rather than a degradation, so any smaller staging box needs an override — and
because a first-time operator would reasonably read the compose file as portable.

## Not verified

Everything below was out of reach of this environment and remains untested:

- **nginx, TLS, security headers** — needs a real domain and certificate.
- **`worker-realtime`, `worker-ingestion`, `beat`** — image export ran out of disk. The gateway
  proves the shared image and its Celery imports are sound, but the worker entrypoints and the
  beat schedule have still never run.
- **prometheus, grafana, alertmanager, loki, promtail, node-exporter, backup** — not started.
- **`restore-drill.sh`** — Phase A2, still never executed.
- **Retrieval quality** — no corpus ingested; Phase A3.
- **Concurrency ceiling** — single-request only; Phase A4.

## Environment caveats

Two workarounds were needed for the sandbox and neither touched the repository:

1. The TLS-intercepting egress proxy broke `pip install` and `ollama pull`. Resolved by trusting
   the proxy CA in a locally retagged base image and in the running Ollama container. No
   Dockerfile change, and TLS verification was never disabled.
2. Ollama's 6-CPU limit was overridden to 3.0 through a separate, uncommitted override file.

Throwaway secrets generated by `bootstrap-secrets.sh` were deleted after the run. `deploy/.gitignore`
covers `secrets/`, which was verified rather than assumed.

## Recommended order for the real A1

1. ~~Fix Finding 1~~ — done; readiness is schema-aware and the runbook is corrected.
2. Rebuild the lockfile against the CPU torch index (Finding 2) before shipping 8.88 GB to a VPS
   four times over.
3. Then follow `provisioning-runbook.md` on the real host, and finish A1 by booting the services
   this run could not reach.
