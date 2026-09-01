# AURA AI

A production AI platform that runs on a single VPS: hybrid retrieval over your own documents,
durable memory, tool use, and a self-hosted model behind one gateway. No inference leaves your
infrastructure.

```bash
cd aura_backend
pip install -r requirements.txt
pytest          # 1284 tests, ~7 seconds, no Docker, no network, no model
```

That the test suite needs no infrastructure is a design property, not a convenience: the domain
and port layers import only the standard library, and the in-memory adapters are real
implementations of every storage port. A fitness test fails the build if that stops being true.

---

## What it does

- **Answers from your documents** — hybrid retrieval (BM25 + dense, fused with RRF), reranked,
  diversified, with a score floor that lets it **return nothing** and say so.
- **Remembers** — six memory kinds with per-kind decay, consolidation that supersedes rather than
  deletes, and provenance on every record so "forget everything you learned from that document"
  is a real operation.
- **Uses tools** — capability-gated, argument-validated, timed out, and audited on every attempt
  including refusals.
- **Runs on your hardware** — Ollama or vLLM, on your own machines. There is no hosted provider
  to fail over to: external inference APIs are rejected at construction and the build fails if one
  reappears in code, dependencies, manifests or the Flutter client. Running your own model on
  rented compute is possible but gated behind an explicit flag, because it costs you the
  works-without-internet guarantee ([split inference](docs/guides/split-inference-deployment.md)).
- **Learns on a schedule, never silently** — a nightly job rebuilds the index into a *new*
  generation and promotes it only if quality gates pass. Promoting a fine-tuned model requires a
  recorded human approval; rollback never does.

## What it refuses to do

These are deliberate, and each is tested:

- **Guess when it does not know.** Retrieval below the score floor returns nothing, and
  `is_empty_but_searched` distinguishes "we looked and found nothing" from "we could not look".
- **Let a document give it instructions.** Retrieved content travels on a `DATA` trust channel,
  fenced with a per-turn nonce. A turn whose context was quarantined cannot run a side-effecting
  tool without explicit confirmation.
- **Start in an insecure configuration.** Settings validation refuses debug mode, mock auth,
  disabled safety, or in-memory stores in production. The process does not boot.
- **Retrain itself.** No automatic path modifies the base model. `learning.finetune_auto_launch`
  exists so its absence is explicit and testable.
- **Duplicate a streamed answer.** Once a token has reached the client the router refuses to fail
  over and surfaces the error instead.

---

## Architecture

```
       HTTP  ──▶  Gateway (auth, rate limit, validation)
                      │
                      ▼
                  AI Kernel
                      │
   ┌──────────┬───────┼────────┬──────────┬─────────┬──────────┐
   ▼          ▼       ▼        ▼          ▼         ▼          ▼
 Safety   Prompts  Memory  Knowledge   Tools   Planning   Reasoning
   │          │       │        │          │         │          │
   └──────────┴───────┴────────┴──────────┴─────────┴──────────┘
                      │
                      ▼
              LLM Router  ──▶  Ollama · any OpenAI-compatible endpoint you host
```

Layering, enforced by AST-based fitness tests in `tests/architecture/`:

```
domain/  ports/          stdlib only — entities, value objects, Protocols
engines                  domain + ports only
adapters/  api/          vendor SDKs, lazily imported
container.py             the only place concrete meets abstract
```

The arrow never reverses. A layering rule that lives only in a document decays within weeks.

### The decisions worth reading

| ADR | Decision |
| --- | --- |
| [001](docs/architecture/001-hexagonal-core.md) | Hexagonal core — why the domain imports nothing |
| [002](docs/architecture/002-strangler-migration.md) | Strangler migration — `aura_core` alongside the v1/v2 app |
| [003](docs/architecture/003-provider-routing.md) | Providers declared as data; five routing policies |
| [004](docs/architecture/004-memory-taxonomy.md) | Six memory kinds, and why one store is not enough |
| [005](docs/architecture/005-hybrid-retrieval.md) | BM25 + dense with RRF, and the score floor |
| [006](docs/architecture/006-safety-pipeline.md) | Trust channels, fencing, and the tool taint rule |
| [007](docs/architecture/007-versioned-indexes.md) | Index identity, alias promotion, O(1) rollback |
| [008](docs/architecture/008-human-in-the-loop-learning.md) | Nothing reaches production without a person |
| [009](docs/architecture/009-observability-first.md) | Every degradation increments a counter |

Start with the [overview](docs/architecture/00-overview.md).

---

## Repository layout

```
aura_backend/     Python/FastAPI — the product (v3 gateway, AI kernel)
aura_client/      Flutter app (Android/iOS/macOS/web/desktop)
aura_admin/       Next.js admin dashboard
deploy/           Production compose, nginx, bootstrap/update scripts
docs/             Architecture, guides, reports, plans, and archived docs
infra/            Terraform for cloud provisioning
scripts/          Client build, Firebase rules, and local dev helpers
docker-compose.yml   Local dev stack (backend + qdrant + redis + workers)
```

Root-level `deploy_client.sh` / `.ps1` are thin wrappers around `scripts/` — either entry point works.

---

## Documentation

**Getting it running**
[Provisioning runbook](docs/guides/provisioning-runbook.md) ·
[Host migration](docs/guides/host-migration-runbook.md) ·
[Split inference](docs/guides/split-inference-deployment.md) ·
[Deployment](docs/guides/deployment-guide.md) ·
[Platform support](docs/guides/platform-support.md) ·
[Administration](docs/guides/administrator-guide.md) ·
[Maintenance](docs/guides/maintenance-guide.md) ·
[Troubleshooting](docs/guides/troubleshooting-guide.md) ·
[User guide](docs/guides/user-guide.md)

**Building against it**
[API reference](docs/guides/api-reference.md) ·
[Migration from v1/v2](docs/guides/migration-guide.md)

**Working on it**
[Developer guide](docs/guides/developer-guide.md) ·
[MLOps and fine-tuning](docs/guides/mlops-guide.md) ·
[Architecture](docs/architecture/00-overview.md)

**Evidence**
[Benchmarks](docs/reports/benchmark-report.md) ·
[Performance](docs/reports/performance-report.md) ·
[Security audit](docs/reports/security-audit.md)

**The self-hosting mission** — what the system looked like before it, what changed, and an
uninflated assessment of how close to production it actually is:
[pre-modification audit](docs/reports/AURA_AI_CURRENT_ARCHITECTURE.md) ·
[final implementation report](docs/reports/AURA_AI_FINAL_IMPLEMENTATION_REPORT.md) ·
[production readiness score](docs/reports/AURA_AI_PRODUCTION_READINESS_SCORE.md) ·
[hardware capacity report](docs/reports/AI_HARDWARE_CAPACITY_REPORT.md)

**Where it started** — the Phase 1 analysis of the system this replaces, with file:line evidence
for every finding: [architecture](docs/analysis/01-architecture-report.md) ·
[AI capability](docs/analysis/02-ai-capability-report.md) ·
[missing components](docs/analysis/03-missing-components-report.md) ·
[technical debt](docs/analysis/04-technical-debt-report.md) ·
[performance](docs/analysis/05-performance-report.md) ·
[security](docs/analysis/06-security-report.md) ·
[scalability](docs/analysis/07-scalability-report.md)

---

## Numbers

Measured on 4 vCPU / 15 GB with the in-memory stack — see the
[Benchmark Report](docs/reports/benchmark-report.md) for method and caveats.

| | |
| --- | --- |
| Pipeline overhead (p95, model time excluded) | **4.6 ms** — target is 60 ms |
| Guard pipeline over 2 KB (p50) | 0.21 ms |
| Concurrent turns before throughput falls off | 32 — the legacy pipeline caps at ~4 |
| Tests | 1284, ~7 s, no infrastructure |
| Coverage | 90% |
| External AI inference APIs | **none** — enforced by seven independent checks |
| Remote inference on rented compute | gated — refused unless `AURA_ALLOW_REMOTE_INFERENCE=true` |

## Deploying

Target: 4 vCPU, 16 GB RAM, 100 GB SSD. Roughly 30 minutes, most of it waiting for model weights.

```bash
git clone https://github.com/anasibrahimgnedy-collab/aura-ai.git /opt/aura
cd /opt/aura/deploy
cp .env.example .env && $EDITOR .env      # set AURA_DOMAIN
./scripts/bootstrap-secrets.sh
docker compose -f docker-compose.prod.yml --env-file .env up -d
```

First time on a host? Use the [Provisioning Runbook](docs/guides/provisioning-runbook.md) instead
of the four lines above — it covers the parts that only bite once, and it checks the hardware first.

Nothing in the stack has a secret default — a deployment that forgets a value fails to start
rather than running with a publicly-known password. Full instructions, including TLS and the
security checks worth running on every deployment, are in the
[Deployment Guide](docs/guides/deployment-guide.md).

## Contributing

Read the [Developer Guide](docs/guides/developer-guide.md) first — it covers the layering rules
the test suite enforces, the extension points for providers, tools, guards, and prompts, and the
conventions (typed errors, counted degradations, content-free telemetry, immutable entities,
injected I/O).

Tests assert **behaviour**, not call choreography. Nothing internal is mocked; the in-memory
adapters are the substrate, so a test exercises the same engine code production runs.
