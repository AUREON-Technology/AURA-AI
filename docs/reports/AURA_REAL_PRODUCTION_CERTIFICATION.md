# AURA AI — Real Production Certification

**Date:** 2026-09-27
**Certifying scope:** the real AURA AI system as deployed, plus the remediation on branch
`claude/aura-production-readiness-c3h5jv`
**Companion documents:** `AURA_PRODUCTION_REMEDIATION_REPORT.md`,
`AURA_PRODUCTION_BLOCKERS.md`, `AURA_PRODUCTION_READINESS_MATRIX.md`,
`AURA_PRODUCTION_TEST_EVIDENCE.md`

---

> ## Addendum — 2026-09-27, revised after a second deployment target was disclosed
>
> The operator supplied `https://aura-ai-production-03a4.up.railway.app`. It serves a **working
> AURA v3 gateway over valid TLS** (EV-22). This audit had not found it: nothing in the
> repository, DNS, or the deployment docs names that hostname.
>
> **Every "BLOCKED — no reachable deployment" result below was true of `aureon-ai.tech` and false
> of the product.** That is a material error in the earlier reports, and the corrected results are
> in the revised gate table. Four gates moved to PASS on live evidence:
> authentication enforcement (72/82 endpoints 401, public set matching the repo's own allowlist),
> the liveness/readiness contract, no active insecure bypasses (all five `aura_insecure_mode`
> gauges 0.0), and CORS refusing an untrusted origin. The v1 locked-route invariant holds:
> **zero legacy paths**, where `aureon-ai.tech` served all 14.
>
> **The verdict does not change, and the reason is much narrower than before.** The platform is up;
> the model is not. `hfspace` — the only configured provider — fails its probe with `ReadTimeout`,
> and `aura_dependency_up{provider:hfspace}` is `0.0`. With no reachable LLM, no user can get an
> answer, so the core journey still fails at its third step. The database is also two migrations
> behind (`expected 0008, revision 0006`) despite `railway.json` declaring a gating
> `alembic upgrade head`.
>
> Two defects found here were **fixed on this branch**, because both were architectural rather than
> stale: Railway has no nginx, and the header policy and `/metrics` refusal lived only in the nginx
> template. The app now carries both, and nginx strips the duplicates (DEF-21, DEF-22).
>
> One correction to the correction: EV-19 argued the "zero security headers" finding was a
> stale-image artifact. **That was wrong for this target.** It is a genuine gap wherever the app is
> the edge. CORS and the health contract, however, were correctly identified as stale-image
> artifacts, and EV-22 confirms both behave properly in production.

> ## Addendum 2 — 2026-09-27 21:28, after an outage I caused and fixed
>
> A change in PR #128 — a **fail-closed** release-identity gate — took the Railway deployment down
> from 19:52 to 21:27. Railway builds the Dockerfile through its own git integration and never
> passes `--build-arg GIT_SHA`, so `validate()` raised at import and `uvicorn` could not load the
> app. PR #130 fixed it; the service recovered ~90 s after that merge. Full account in EV-23.
>
> **Two gates closed as a result, both PRODUCTION TESTED:**
>
> - **BLOCK-03 — CLOSED.** `/health/version` reports `git_sha a517cb689458...`, which **is** the
>   merge commit of PR #130. "Which build is production running?" is now a one-request question
>   with a provably current answer. Populated entirely by the platform fallback
>   (`RAILWAY_GIT_COMMIT_SHA`) with no build arg wired — the original build-arg-only design could
>   never have identified this target.
> - **DEF-21 — FIXED.** All five security headers now present on a target with no nginx in the
>   path, where an hour earlier there were none.
>
> **The verdict still does not change.** `/health/ready` correctly returns 503: inference is still
> down (`hfspace` `ReadTimeout`) and the schema is still two migrations behind. A user still cannot
> get an answer.
>
> **One finding worth more than the fix:** the release-identity gate should never have been
> fail-closed. Every other production check in `validate()` prevents an outcome *worse* than not
> serving. An unidentified build is a diagnosability problem, and a feature whose purpose is to
> make incidents easier to diagnose must never be able to cause one. That reasoning error, not the
> code, was the defect.

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
| No critical security flaw | **PARTIAL** | **PRODUCTION TESTED** | No bypass active, no secret exposure, CORS sound. Two real gaps found and fixed (DEF-21 headers, DEF-22 open `/metrics`). Authorization/isolation still unevidenced. |
| Authentication | **PASS** | **PRODUCTION TESTED** | 72/82 endpoints 401 unauthenticated; every non-gated path matches the repo's own `PUBLIC`/`IDENTITY_EXCHANGE` sets (EV-22). |
| Authorization / isolation (A↛B, admin, IDOR, escalation) | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Safe agent / tool execution, containment | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Adversarial AI safety (injection, exfiltration, policy bypass) | **BLOCKED** | LOCALLY TESTED | Mandatory. **No evidence.** |
| Core user journeys usable | **FAIL** | PRODUCTION TESTED | Still fails at step three, now for one narrow reason: **inference is down** (DEF-19), so no answer can be produced. |
| Production API usable | **PASS (Railway)** / **FAIL (`aureon-ai.tech`)** | PRODUCTION TESTED | Railway: 80 `/api/v3` routes, valid TLS, zero legacy paths. `aureon-ai.tech`: Django `400`. |
| Inference works | **FAIL** | **PRODUCTION TESTED** | `hfspace` probe `ReadTimeout` 5.16 s; `aura_dependency_up` `0.0`. Measured, not assumed (DEF-19). |
| No unrecoverable data failure | **UNKNOWN** | — | Cannot be assessed without host access. |
| Backup / restore proven | **BLOCKED** | LOCALLY TESTED | Mandatory. Crypto/retention logic is CI-covered; **no real drill**, no RPO/RTO. Needs host/DB access neither target grants this audit. |
| Deployment stability | **FAIL** | MEASURED | Fourteen days, zero successful GitHub-driven deploys (BLOCK-06). Railway deploys via its own git integration, which is why a v3 build is live there while the VPS is frozen — and why nobody noticed. |
| No exposed secrets | **PASS** | PRODUCTION TESTED | Secrets: **NOT FOUND**. Carried forward from the audit; re-confirmed no config or secret exposure on the live surface. |
| Release identity traceable | **PASS** | **PRODUCTION TESTED** | `/health/version` reports `a517cb689458`, which is the merge commit of PR #130 — provably current, via the platform fallback with no build arg (EV-23). BLOCK-03 closed. |

### Remediation gates

| Item | Result | Maturity |
|---|---|---|
| BLOCK-04 Cause A — `secrets` in job-level `if:` | **FIXED — confirmed in CI** | LOCALLY TESTED · FAILURE TESTED (negative control) · **PRODUCTION TESTED** (EV-21: jobs 0 → 4, run reports its `name:` again) |
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
| Install → launch → register/login | **FAIL as shipped** — the client is compiled against `aureon-ai.tech`, whose TLS hostname verification fails. Against the Railway host it would reach login: TLS is valid and the auth endpoints answer. A rebuild with `BACKEND_URL` pointed there is the difference. |
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
3. **Bring up the Inference Plane (DEF-19).** The highest-value single action, and independent of
   everything else: the Control Plane is healthy and waiting for a provider. Check the Hugging Face
   Space is running and that the Control Plane's `openai_compatible` base URL and key reach it.
   `/health/ready` flips to 200 when it does.
4. **Apply the migrations (DEF-20).** `alembic upgrade head` against the Railway database; the
   schema is at `0006`, code expects `0008`. Then find out why `railway.json`'s gating
   `preDeployCommand` did not stop the release.
5. **Fix BLOCK-01**, or retire it. `aureon-ai.tech` points at an unrelated Django site. Either
   point it at the AURA host with a matching certificate, or — given Railway already serves a
   correct v3 gateway — make the Railway hostname canonical and rebuild the client against it.
   Verify with a *verifying* client, not `-k`.
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
