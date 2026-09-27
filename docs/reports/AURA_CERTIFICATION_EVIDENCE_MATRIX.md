# AURA AI — Certification Evidence Matrix

**Updated:** 2026-09-27 22:20 UTC
**Live revision under test:** `0832665f4280` (Railway Control Plane)
**Repository revision:** `main` @ `0832665f`

> ## OVERALL VERDICT: NOT PRODUCTION CERTIFIED
>
> Four mandatory gates have no evidence. Per the closure rule, certification is refused while any
> mandatory gate lacks evidence — no combination of the gates that *do* pass changes that.

This file is the single canonical answer to "what is proven, and by what?". It is
**machine-checked**: `aura_backend/tests/architecture/test_certification_gate.py` parses the table
below on every default `pytest` run and fails if a gate claims PASS without citing evidence, if a
mandatory gate is missing, or if the verdict above disagrees with the rows. The intent is that the
refusal cannot be forgotten, softened, or quietly edited into a pass — the rule is enforced by the
suite rather than by whoever writes the summary.

## How to read the columns

- **Mandatory** — `yes` means the brief's §36 list treats its absence as itself a blocker.
- **Maturity** — the highest level actually reached: `CODE-PRESENT`, `LOCALLY TESTED`,
  `FAILURE TESTED`, `MEASURED`, `PRODUCTION TESTED`, `CERTIFIED`.
- **Evidence** — a specific artifact. `—` means none, and `—` on a mandatory gate is what refuses
  certification. An instrument that exists but has never been run is **not** evidence.
- **Verdict** — `PASS`, `PARTIAL`, `FAIL`, `BLOCKED`, `NOT APPLICABLE`, `UNKNOWN`.

<!-- CERTIFICATION-MATRIX-BEGIN -->

| Gate | Mandatory | Maturity | Evidence | Verdict |
| --- | --- | --- | --- | --- |
| Authentication enforced | yes | PRODUCTION TESTED | EV-22 (72/82 endpoints 401; public set matches repo allowlist) | PASS |
| Authorization / isolation | yes | LOCALLY TESTED | — | BLOCKED |
| Adversarial AI safety | yes | LOCALLY TESTED | — | BLOCKED |
| Agent / tool containment | yes | LOCALLY TESTED | — | BLOCKED |
| Backup / restore with RPO+RTO | yes | LOCALLY TESTED | — | BLOCKED |
| No critical security flaw | yes | PRODUCTION TESTED | EV-22, EV-23 (no bypass active; headers fixed; CORS sound) | PARTIAL |
| No exposed secrets | yes | PRODUCTION TESTED | EV-09 (Secrets: NOT FOUND) | PASS |
| Production API usable | yes | PRODUCTION TESTED | EV-22 (80 /api/v3 routes, valid TLS, zero legacy paths) | PASS |
| Core user journeys usable | yes | PRODUCTION TESTED | EV-22 (no journey reached step three) | UNKNOWN |
| Inference works | yes | PRODUCTION TESTED | EV-24 (hfspace healthy=True; no completed turn observed) | PARTIAL |
| No unrecoverable data failure | yes | CODE-PRESENT | — | UNKNOWN |
| Deployment stability | yes | MEASURED | EV-17 (zero executed CI jobs since 2026-09-13) | FAIL |
| Database schema integrity | yes | PRODUCTION TESTED | EV-23 (revision 0006, code expects 0008) | FAIL |
| Release identity traceable | no | PRODUCTION TESTED | EV-23 (/health/version = merge commit of #130) | PASS |
| Liveness / readiness contract | no | PRODUCTION TESTED | EV-22 (503 on unhealthy dependency) | PASS |
| Security response headers | no | PRODUCTION TESTED | EV-23 (all five present on Railway) | PASS |
| CORS least-privilege | no | PRODUCTION TESTED | EV-22 (hostile origin not granted) | PASS |
| v1 locked routes absent | no | PRODUCTION TESTED | EV-22 (zero legacy paths) | PASS |
| No insecure-mode bypass active | no | PRODUCTION TESTED | EV-22 (all five aura_insecure_mode gauges 0.0) | PASS |
| Self-hosted-only inference invariant | no | LOCALLY TESTED | test_no_external_inference.py (repo-wide fitness test) | PASS |
| Metrics endpoint not public | no | PRODUCTION TESTED | EV-23 (200 unauthenticated; gate opt-in, token unset) | FAIL |
| Network exposure (non-web ports) | no | CODE-PRESENT | — | NOT APPLICABLE |
| Rollback validated | no | CODE-PRESENT | — | BLOCKED |
| Desktop / hostd / Flutter clients | no | CODE-PRESENT | — | BLOCKED |
| RAG retrieval quality | no | CODE-PRESENT | EV-22 (hash-embed-v1 active, nothing indexed) | BLOCKED |
| Observability under real traffic | no | CODE-PRESENT | — | BLOCKED |
| Performance percentiles / TTFT | no | CODE-PRESENT | — | BLOCKED |

<!-- CERTIFICATION-MATRIX-END -->

## The four mandatory gates without evidence

Each has an instrument now. **An instrument is not evidence**, and the distinction is the whole
point of this file.

| Gate | Instrument | What it needs to become evidence |
|---|---|---|
| Authorization / isolation | `tests/certification/test_authorization_isolation.py` | Two synthetic access tokens. One operator run. |
| Adversarial AI safety | `tests/certification/test_adversarial_safety.py` | The same two tokens. |
| Agent / tool containment | `tests/certification/test_agent_containment.py` (boundary only) | The boundary part needs tokens. The **escape** invariants need a host with `AURA_SANDBOX_ENABLED` and a reachable Docker daemon — structurally impossible on Railway, so `deploy/scripts/verify-engineer-workspaces.sh` on the VPS. |
| Backup / restore | `deploy/scripts/restore-drill.sh` | VPS shell plus an isolated restore target. No token fixes this. |
| Core user journeys (A/B/C/E) | `tests/certification/test_e2e_user_journeys.py` | The same two tokens. Journey D (desktop coding) is NOT APPLICABLE on any reachable target. |

Three are one command away. Two need the VPS, which is a different blocker.

Note what the verdict column still says for each: the rows above have instruments and remain
BLOCKED or UNKNOWN. `test_certification_gate.py` rejects a suite path offered as evidence for
a PASS, so building these could not move a verdict even by accident -- only a run can.

## Owner-side actions, in dependency order

1. **GitHub Actions billing** — nothing merged has been CI-validated since 2026-09-13. Everything
   in `main` today rests on local runs alone.
2. **Railway → service → Settings → Root Directory** — `aura_backend/railway.json` is almost
   certainly never read, which is why `preDeployCommand` has not migrated the database and
   `healthcheckPath` has been inert. Today a schema-breaking change can deploy silently.
3. **Apply the migrations** — `alembic upgrade head`; the schema is at `0006` and the code expects
   `0008`.
4. **`AURA_METRICS_TOKEN`** on the Railway service — `/metrics` is world-readable, which the
   repository's own nginx config refuses in both server blocks.
5. **Two synthetic tokens** (`aura-cert-user-a`, `aura-cert-user-b`, role `User`) — turns two
   mandatory gates from instruments into evidence.

## Standing rules this file encodes

- A skipped test is not a pass. The certification suites skip without credentials and say the gate
  is BLOCKED.
- An instrument that has never run is not evidence.
- "Works locally" is not "works in production", and the Maturity column keeps them apart.
- Absence of evidence on a mandatory gate refuses certification regardless of how many other gates
  pass — enforced by `test_certification_gate.py`, not by good intentions.
