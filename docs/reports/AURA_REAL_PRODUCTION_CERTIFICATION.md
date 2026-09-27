# AURA AI — Real Production Certification

**Date:** 2026-09-27
**Certifying scope:** the real AURA AI system as deployed, plus the remediation on branch
`claude/aura-production-readiness-c3h5jv`
**Companion documents:** `AURA_PRODUCTION_REMEDIATION_REPORT.md`,
`AURA_PRODUCTION_BLOCKERS.md`, `AURA_PRODUCTION_READINESS_MATRIX.md`,
`AURA_PRODUCTION_TEST_EVIDENCE.md`

---

## Verdict

# NOT PRODUCTION CERTIFIED

A real end user **cannot** use AURA safely, reliably, correctly or consistently today, because
there is no reachable AURA deployment to use. The domain the shipped client is compiled against
serves an unrelated application.

This verdict is unchanged from the preceding audit. What changed is the diagnosis, which is now
specific, measured, and largely actionable in a single afternoon by the account owner.

### Why not CONDITIONAL

`CONDITIONAL PRODUCTION CERTIFICATION` requires every security, authentication,
core-functionality and recovery gate to pass, with only genuinely non-blocking issues left.

Four mandatory gates have **no evidence at all** — authorization/isolation, adversarial AI safety
enforcement, backup/restore with measured RPO/RTO, and agent containment. Not "partial". Not
"passing with caveats". No evidence, because there is no deployment to gather it from. The brief
lists "missing evidence for a mandatory gate" as itself a blocker, and that is the correct
treatment: a gate without evidence is not a gate that passed.

---

## The single most important finding

> **GitHub Actions has executed no job for this repository since 2026-09-13.** The last
> successful run of any workflow was `Deploy VPS` #151 at `2026-09-13T14:40:45Z`, deploying
> `78bdc83`. Production has been frozen on that commit for fourteen days. Every workflow run
> since — `Backend CI`, `Client CI`, `Agent Evaluation`, both plane-deploys — fails within
> seconds having been billed **zero milliseconds** and having written **no log at all**.

This is an **account-level GitHub Actions entitlement problem**, not a defect in this repository.
It is why merging to `main` deploys nothing, why production runs a pre-v3 build, and why the
deployed revision was unidentifiable. **No code change can fix it, and nothing else can be fixed
until it is.**

The remedy is in `AURA_PRODUCTION_REMEDIATION_REPORT.md` §1.3 and begins with reading the
billing page rather than assuming which entitlement failed.

---

## Gate results

Maturity levels per the brief: CODE-PRESENT · LOCALLY TESTED · PRODUCTION TESTED ·
FAILURE TESTED · MEASURED · VERIFIED · CERTIFIED.

### Mandatory gates

| Gate | Result | Highest maturity reached | Note |
|---|---|---|---|
| No critical security flaw | **BLOCKED** | LOCALLY TESTED | Repo-side suites pass; no production surface to assess. |
| Authentication | **BLOCKED** | LOCALLY TESTED | Mandatory. No reachable deployment. |
| Authorization / isolation (A↛B, admin, IDOR, escalation) | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Safe agent / tool execution, containment | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Adversarial AI safety (injection, exfiltration, policy bypass) | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Core user journeys usable | **FAIL** | PRODUCTION TESTED | No journey reaches its third step. |
| Production API usable | **FAIL** | PRODUCTION TESTED | `aureon-ai.tech` answers with Django `400`. |
| Inference works | **BLOCKED** | CODE-PRESENT | No GPU, no provisioned Space, no reachable gateway. |
| No unrecoverable data failure | **UNKNOWN** | — | Cannot be assessed without host access. |
| Backup / restore proven | **BLOCKED** | LOCALLY TESTED | Mandatory. Crypto/retention logic is CI-covered; **no real drill**, no RPO/RTO. |
| Deployment stability | **FAIL** | MEASURED | Fourteen days, zero successful deploys. |
| No exposed secrets | **PASS** | PRODUCTION TESTED | Secrets: **NOT FOUND**. Carried forward from the audit; re-confirmed no config or secret exposure on the live surface. |
| Release identity traceable | **PARTIAL** | LOCALLY TESTED · FAILURE TESTED | Mechanism built and tested (44 tests); **unproven in production** — nothing has deployed. |

### Remediation gates

| Item | Result | Maturity |
|---|---|---|
| BLOCK-04 Cause A — `secrets` in job-level `if:` | **FIXED** | LOCALLY TESTED · FAILURE TESTED (negative control) |
| A blocked deploy fails visibly instead of concluding `skipped` | **FIXED** | LOCALLY TESTED |
| BLOCK-03 — release identity: settings, endpoint, image, compose ×2, deploy script, revision assertion | **FIXED in code** | LOCALLY TESTED · FAILURE TESTED |
| DEF-12 — stale API reachability matrix | **FIXED** | LOCALLY TESTED |
| Forbidden vendor inference absent; stale Gemini docstrings corrected | **VERIFIED** | LOCALLY TESTED (fitness function) |
| Zero unexplained test failures | **PASS** | LOCALLY TESTED |
| Health liveness/readiness semantics | **ALREADY CORRECT in code** | STATICALLY VERIFIED · LOCALLY TESTED |
| CORS least-privilege; security headers | **ALREADY CORRECT in code** | STATICALLY VERIFIED |
| BLOCK-01 — DNS / SNI / TLS / remove VELVET routing | **BLOCKED** | — |
| BLOCK-02 — deploy current v3 | **BLOCKED** | — |
| Network exposure from an authoritative vantage | **NOT TESTABLE** | — |
| Rollback validation | **BLOCKED** | — |

---

## Test evidence summary

| Suite | Result |
|---|---|
| `pytest` (aura_core) | **2820 tests: 2764 passed, 56 skipped, 0 failed** |
| Legacy `unittest` | **Ran 100 tests — OK (skipped=4)** |
| New this remediation | `test_release_identity.py` (18), `test_workflow_job_contexts.py` (26) |
| Negative control | Bad `secrets`-in-`if:` pattern reintroduced → 3 failures; restored → 26 pass |
| Workflow YAML | All 10 files parse; **no `secrets.` in any job-level `if:`** |
| Compose | Both files parse; all 5 backend-building services carry the release args |

Baseline was 2776 collected with 1 failure. 2776 + 44 new = 2820, and the 1 failure is fixed.

Three failures appeared mid-remediation and are recorded in the remediation report §7 rather than
quietly fixed — each was a real consequence of a real change, caught by the suite. All three were
resolved at the cause; **no check was weakened** to make a test pass.

---

## What a user can do today

| Journey | Result |
|---|---|
| Install → launch → register/login | **FAIL** — the release client requires HTTPS by its own policy; TLS hostname verification against `aureon-ai.tech` fails. Never reaches login. |
| First AI request, conversation | **FAIL** — blocked upstream. |
| Memory / RAG / reasoning answer | **FAIL** — blocked upstream. |
| Tool workflow with confirmation and audit | **FAIL** — blocked upstream. |
| Desktop coding workflow | **BLOCKED** — untestable; no toolchain here, and Client/Host CI are inside the outage. |
| Recovery workflow | **BLOCKED** — untestable. |

No journey reaches its third step. That is the certification in one line.

---

## Conditions for certification

In order. Each is a prerequisite for the next.

1. **Restore GitHub Actions execution.** Billing/entitlement, account owner only. Verify by a
   `Backend CI` run on `main` that produces a log.
2. **Get `Backend CI` green** with genuinely executed jobs. Its content has never been shown
   faulty — it has not been *run* since 2026-09-13.
3. **Fix BLOCK-01.** Point the canonical domain at the AURA host, issue a certificate whose SAN
   covers it, remove the VELVET/Django routing. Verify with a *verifying* client, not `-k`.
4. **Deploy current `main`.** `Deploy VPS` now asserts the live revision equals the built SHA and
   fails otherwise, so a no-op deploy can no longer report success.
5. **Confirm `/health/version`** reports the expected SHA. BLOCK-03 moves to PRODUCTION TESTED.
6. **Confirm `/health/ready`** returns 503 with Qdrant down and 200 healthy. Closes BLOCK-05.
7. **Run the four mandatory gates** against the live deployment: authorization/isolation with
   dedicated synthetic identities, adversarial safety, agent containment, and a real
   backup/restore drill into an isolated target with measured RPO and RTO.
8. **Re-run network exposure** from the VPS or provider console — this audit's vantage point
   cannot answer it, and its timeouts must not be read as a firewall.
9. **Then, and only then, re-certify.**

Steps 1–2 are hours of the owner's time. Steps 3–4 are a maintenance window. Steps 7–8 are the
real engineering, and they cannot start before 4.

---

## Certification statement

The AURA codebase is in substantially better condition than its deployment. The repository holds
a coherent hexagonal architecture with fitness functions that enforce it, 2820 passing tests, a
self-hosting invariant that is genuinely upheld, correct liveness/readiness semantics, a
least-privilege CORS posture and a full security-header set. Several findings in the preceding
audit turned out to describe a fourteen-day-old image rather than defects in that code, and this
document corrects them rather than carrying them forward.

None of that is a certification. Working code that no user can reach is not a product, and every
mandatory production gate here is either failed, blocked, or without evidence.

**Result: NOT PRODUCTION CERTIFIED.**

No missing evidence was converted into a PASS. No metric in this document was estimated. The one
inference drawn — that the CI outage is an account-level entitlement problem — is labelled as an
inference, with its converging measurements shown and its residual unknown stated.

A failed certification with accurate evidence is a successful audit.
