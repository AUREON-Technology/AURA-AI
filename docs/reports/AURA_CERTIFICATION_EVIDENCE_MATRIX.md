# AURA AI — Certification Evidence Matrix

**Updated:** 2026-09-28 10:55 UTC
**Live revision under test:** `e8ed25a86006` (Railway Control Plane)
**Repository revision:** `main` @ `8ae96ef`

> ## OVERALL VERDICT: NOT PRODUCTION CERTIFIED
>
> Four mandatory gates have no evidence, one fails on observed behaviour (EV-27), and one that
> was recorded as passing has been corrected down to PARTIAL (EV-28 — the earlier samples measured
> a warm endpoint). Per the closure rule, certification is refused while any mandatory gate lacks
> evidence; a gate that fails on evidence is a separate refusal again. No combination of the gates
> that *do* pass changes either.
>
> The correction is the point of keeping this file. Two gates have now moved *down* on new
> evidence — an audit where every revision improves the score is measuring the auditor, not the
> system.

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
| Core user journeys usable | yes | PRODUCTION TESTED | EV-27 (Engineer summarised a file it never read) | FAIL |
| Inference works | yes | MEASURED | EV-28 (works warm; 0/20 while asleep, wakes in ~5.5min; cold-start turn untested) | PARTIAL |
| No unrecoverable data failure | yes | CODE-PRESENT | — | UNKNOWN |
| Deployment stability | yes | MEASURED | EV-17 (zero executed CI jobs since 2026-09-13) | FAIL |
| Database schema integrity | yes | PRODUCTION TESTED | EV-25, EV-26 (schema healthy at revision 0008, across two deploys) | PASS |
| Release identity traceable | no | PRODUCTION TESTED | EV-23, EV-26 (six consecutive deploys, each = its merge commit) | PASS |
| Liveness / readiness contract | no | PRODUCTION TESTED | EV-22 (503 on unhealthy dependency) | PASS |
| Security response headers | no | PRODUCTION TESTED | EV-23 (all five present on Railway) | PASS |
| CORS least-privilege | no | PRODUCTION TESTED | EV-22 (hostile origin not granted) | PASS |
| v1 locked routes absent | no | PRODUCTION TESTED | EV-22 (zero legacy paths) | PASS |
| No insecure-mode bypass active | no | PRODUCTION TESTED | EV-22 (all five aura_insecure_mode gauges 0.0) | PASS |
| Self-hosted-only inference invariant | no | LOCALLY TESTED | test_no_external_inference.py (repo-wide fitness test) | PASS |
| Metrics endpoint not public | no | PRODUCTION TESTED | EV-23, EV-26 (still 200 unauthenticated; gate opt-in, token unset) | FAIL |
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

Two are one command away. Two need the VPS, which is a different blocker.

## Owner-side actions, in dependency order

### Still outstanding

1. **GitHub Actions billing** (BLOCK-06) — nothing merged has been CI-validated since 2026-09-13.
   Everything in `main` today rests on local runs alone. This is the largest remaining risk in the
   repository, and it is the one item on this list that no amount of agent work can substitute for:
   a suite that passes locally is not a suite that gates a merge.
2. **Two synthetic tokens** (`aura-cert-user-a`, `aura-cert-user-b`, role `User`) — the highest-value
   item, because it turns **three** mandatory gates from instruments into evidence in one operator
   command:
   `cd aura_backend && AURA_CERT_BASE_URL=<prod> pytest tests/certification -m cert`.
3. **`AURA_METRICS_TOKEN`** on the Railway service (DEF-22) — `/metrics` is still world-readable
   (re-confirmed in EV-26), which the repository's own nginx config refuses in both server blocks.
   The gate ships opt-in, so an unset token means no gate at all.
4. **VPS shell** — the only route to the remaining two mandatory gates: `restore-drill.sh` for
   backup/restore, and `verify-engineer-workspaces.sh` for the containment escape invariants.
   Neither is reachable from the Railway target by construction.

### Closed, with the evidence that closed them

| Action | Evidence | Note |
| --- | --- | --- |
| **Railway Root Directory → `aura_backend`** | EV-25 | The diagnosis predicted that `railway.json` was unread, so `preDeployCommand` had never run. Correcting the Root Directory advanced the schema on the next deploy, which the competing explanation (the command running and *failing*) would not have produced. |
| **Apply the migrations** | EV-25, EV-26 | Schema healthy at `0008` against a code expectation of `0008`, and still `0008` after a further deploy — so the fix is structural, not a one-off manual run. |

## Standing rules this file encodes

- A skipped test is not a pass. The certification suites skip without credentials and say the gate
  is BLOCKED.
- An instrument that has never run is not evidence.
- "Works locally" is not "works in production", and the Maturity column keeps them apart.
- Absence of evidence on a mandatory gate refuses certification regardless of how many other gates
  pass — enforced by `test_certification_gate.py`, not by good intentions.
