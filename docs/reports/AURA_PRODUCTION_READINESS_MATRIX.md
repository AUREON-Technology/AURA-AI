# AURA AI — Production Readiness Matrix

**Assessed:** 2026-09-27 (UTC) · **Repo revision:** `71dd01b` (`main`)
**Production target:** `aureon-ai.tech` → A `152.239.118.239` / AAAA `2a02:4780:79:5dcd::1`
**Overall:** **NOT PRODUCTION CERTIFIED** — see `AURA_PRODUCTION_BLOCKERS.md`

## How to read this table

The columns are deliberately not interchangeable, because conflating them is how a system like this
comes to be described as ready when it is not:

- **Implemented** — the capability exists in the repository at `71dd01b`.
- **Tested** — covered by the repo's own automated suites (in-memory substrate, no infrastructure).
- **Production Tested** — exercised against the **live deployment** during this audit.
- **Failure Tested** — its failure path was deliberately exercised against the live deployment.
- **Verified** — the observed live behavior matched the documented/intended behavior.
- **Ready** — safe for a real end user today.

A capability can be fully Implemented and Tested and still be **NOT READY**, and most of this table
is exactly that. The dominant reason is not defective code: it is that **the artifact running in
production is a pre-v3 build** (BLOCK-02/03) reached over an **HTTPS endpoint that serves another
site's certificate** (BLOCK-01). Both are deployment facts, not code facts. They make almost
everything below untestable in production regardless of its quality in the repo.

Legend: `Y` yes · `N` no · `P` partial · `—` not applicable · `BLOCKED` prevented by a dependency ·
`N/T` not tested

---

## Core platform

| Capability | Implemented | Tested | Production Tested | Failure Tested | Verified | Ready |
| --- | --- | --- | --- | --- | --- | --- |
| Ingress / nginx | Y | Y | **P** (`:80` only) | N | **N** | **NOT READY** |
| TLS / HTTPS | Y | Y | **Y** | — | **N** (foreign cert) | **NOT READY** |
| v3 Gateway (`/api/v3/*`) | Y | Y | **Y** | — | **N** (absent; all 404) | **NOT READY** |
| Legacy v1/v2 surface removal | Y | Y | **Y** | — | **N** (14 routes live) | **NOT READY** |
| Health / readiness contract | Y | Y | **Y** | P | **N** (200 while degraded) | **NOT READY** |
| Build provenance at runtime | N | — | Y | — | **N** (unidentifiable) | **NOT READY** |
| PostgreSQL connectivity | Y | Y | **Y** | N | **Y** (reachable) | READY W/ LIMITATION |
| Redis connectivity | Y | Y | **Y** | N | **Y** (reachable) | READY W/ LIMITATION |
| Qdrant connectivity | Y | Y | **Y** | — | **N** (client `None`) | **NOT READY** |
| Celery worker / Beat / outbox | Y | Y | **N** | N | N/T | **NOT TESTED** |
| Object/file storage | Y | Y | N | N | N/T | **NOT TESTED** |

## Security & access control

| Capability | Implemented | Tested | Production Tested | Failure Tested | Verified | Ready |
| --- | --- | --- | --- | --- | --- | --- |
| Unauthenticated rejection | Y | Y | **Y** (22/24 endpoints) | Y | **Y** | **READY** |
| Device attestation gate | Y | Y | **Y** | Y | **Y** (403 enforced) | **READY** |
| Invalid refresh-token rejection | Y | Y | **Y** | Y | **Y** (401) | **READY** |
| Token revocation semantics | Y | Y | **Y** | Y | **P** (200 on invalid) | READY W/ LIMITATION |
| Secret / config file exposure | Y | Y | **Y** | Y | **Y** (none found) | **READY** |
| Security response headers | Y | Y | **Y** | — | **N** (none present) | **NOT READY** |
| CORS policy | Y | Y | **Y** | Y | **N** (`*`, any origin) | **NOT READY** |
| Rate limiting | Y | Y | **P** | P | **P** (none on `/health`) | **NOT TESTED** |
| Authorization / RBAC / IDOR | Y | Y | **N** | N | N/T | **BLOCKED** |
| Cross-user data isolation | Y | Y | **N** | N | N/T | **BLOCKED** |
| Admin endpoint protection | Y | Y | **N** | N | N/T | **BLOCKED** |
| Prompt-injection enforcement | Y | Y | **N** | N | N/T | **BLOCKED** |
| Internal datastore exposure | Y | Y | **N** | — | N/T | **NOT TESTABLE** |
| Dependency CVE gating | Y | Y | **N** | — | **N** (`pip-audit` job failing) | **NOT READY** |

## AI capabilities

| Capability | Implemented | Tested | Production Tested | Failure Tested | Verified | Ready |
| --- | --- | --- | --- | --- | --- | --- |
| AIKernel execution path | Y | Y | **N** | N | N/T | **BLOCKED** |
| Real LLM inference | Y | Y | **N** | N | N/T | **BLOCKED** |
| Self-hosted-only invariant | Y | **Y** (repo) | **N** | — | **N** in prod | **BLOCKED** |
| ProviderRouter / failover | Y | Y | **N** | N | N/T | **BLOCKED** |
| Memory (6 kinds) | Y | Y | **N** | N | N/T | **BLOCKED** |
| RAG / hybrid retrieval | Y | Y | **N** | N | **N** (Qdrant down) | **NOT READY** |
| Retrieval quality (Recall@k/MRR/nDCG) | Y | P | **N** | — | N/T | **NOT TESTED** |
| Planning / reasoning | Y | Y | **N** | N | N/T | **BLOCKED** |
| Agents / personas | Y | Y | **N** | N | N/T | **BLOCKED** |
| Tools + confirmation gating | Y | Y | **N** | N | N/T | **BLOCKED** |
| Trust channels / taint | Y | Y | **N** | N | N/T | **BLOCKED** |
| Streaming / TTFT | Y | Y | **N** | N | N/T | **BLOCKED** |

## Product surfaces

| Capability | Implemented | Tested | Production Tested | Failure Tested | Verified | Ready |
| --- | --- | --- | --- | --- | --- | --- |
| Flutter client ↔ production | Y | Y (CI) | **Y** | — | **N** (TLS + 404) | **NOT READY** |
| Personal OS CRUD | P | Y | **N** | N | N/T | **BLOCKED** |
| Voice (`/api/v3/voice/*`) | Y | Y | **Y** | — | **N** (404, WS 404) | **NOT READY** |
| Desktop Engineer app | P | Y | **N** | N | N/T | **NOT TESTED** |
| Host daemon (`aura_hostd`) | P | Y | **N** | N | N/T | **NOT TESTED** |
| Engineer workspaces (ADR-013/14) | Y | Y | **N** | N | N/T | **NOT TESTED** |
| Admin console | P | P | **N** | N | N/T | **NOT TESTED** |
| Sync (`/api/v3/sync`) | Y | Y | **Y** | — | **N** (404) | **NOT READY** |

## Operations

| Capability | Implemented | Tested | Production Tested | Failure Tested | Verified | Ready |
| --- | --- | --- | --- | --- | --- | --- |
| CI gating (Backend CI) | Y | — | **Y** | — | **N** (fails at startup) | **NOT READY** |
| Deploy pipeline (VPS) | Y | — | **Y** | — | **N** (skipped) | **NOT READY** |
| Second target (Railway + HF) | Y | — | **Y** | — | **N** (not deployed) | **NOT READY** |
| Dev/staging environment | Y | — | **Y** | — | **N** (`dev.` NXDOMAIN) | **NOT READY** |
| Compose isolation parity | Y | Y | **N** | — | **Y** (static: parity holds) | READY W/ LIMITATION |
| Sandbox exec surface pinning | Y | Y | **N** | — | **Y** (static) | READY W/ LIMITATION |
| Backups | Y | Y | **N** | N | N/T | **BLOCKED** |
| Restore / DR drill | Y | P | **N** | N | N/T | **BLOCKED** |
| Metrics / logs / traces | Y | Y | **N** | N | **N** (`/metrics` absent) | **NOT TESTED** |
| Alerting | Y | Y | **N** | N | N/T | **NOT TESTED** |
| Rollback procedure | Y | P | **N** | N | N/T | **BLOCKED** |
| Migration state | Y | Y | **N** | N | N/T | **BLOCKED** |
| Performance (real stack) | — | P | **P** (`/health` only) | N | P | **NOT TESTED** |

---

## Tally

| Verdict | Count |
| --- | --- |
| **READY** | 4 |
| READY WITH KNOWN LIMITATION | 5 |
| **NOT READY** | 20 |
| NOT TESTED | 11 |
| BLOCKED | 16 |
| NOT TESTABLE (this environment) | 1 |

**4 of 57 capabilities are production-ready.** All four are negative controls — the system
correctly refusing unauthenticated access and not leaking configuration. Nothing that delivers
user-facing value is verified in production.

The honest summary: this is a substantially built and well-tested codebase attached to a broken
deployment. The repo's own `docs/production/gap_register.yaml` (31 open, 13 built-not-proven) and
`AURA_AI_PRODUCTION_READINESS_SCORE.md` ("No dimension can score above 8, because this system has
never served a real user") already said as much. This audit's contribution is the live evidence for
*why* — and it is worse than "unproven": the deployed artifact is not the audited one, and the path
to change that is itself broken.

---

*Generated by [Claude Code](https://claude.ai/code)*
