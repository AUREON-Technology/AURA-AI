# AURA AI — Production Remediation Report

**Date:** 2026-09-27
**Scope:** remediation of the five blockers recorded in `AURA_REAL_PRODUCTION_TEST_REPORT.md`
**Repository state:** branch `claude/aura-production-readiness-c3h5jv`, based on `main` @ `71dd01b`
**Preceding audit:** `AURA_REAL_PRODUCTION_TEST_REPORT.md` (NOT PRODUCTION CERTIFIED)
**Certification after this work:** see `AURA_REAL_PRODUCTION_CERTIFICATION.md` — still **NOT PRODUCTION CERTIFIED**

---

## 1. The headline: one root cause, and it is not in the code

The previous audit recorded five blockers and suspected they were four symptoms of one condition.
That was correct, but it named the wrong condition. The actual one is:

> **GitHub Actions has not executed a single job for this repository since 2026-09-13.**
> Production has therefore been frozen on the last commit that ever deployed, `78bdc83`
> (2026-09-13 17:34 +0300), for fourteen days.

This is an **account-level entitlement problem, not a defect in this repository**. No change to
any file here can fix it. It is the first thing that must be resolved, and only the account owner
can resolve it.

### 1.1 How this was established

Not inferred. Measured, with a control.

| # | Observation | Method |
|---|---|---|
| 1 | Last successful run of *any* workflow: `Deploy VPS` #151, `2026-09-13T14:40:45Z`, head `78bdc83` | `list_workflow_runs?status=success`, 340 successes, newest is that one |
| 2 | `Backend CI` #281 on `main` @ `71dd01b`: 3 jobs created, each completing in 1–3 s, all `failure`; `docker-build` `skipped` via `needs` | `list_workflow_jobs` |
| 3 | Re-ran the failed jobs deliberately (attempt 2, `2026-09-27T17:55`). **Reproduced identically** — 3 jobs, 2–3 s each, all `failure` | `rerun_failed_jobs` |
| 4 | **`billable.UBUNTU.total_ms: 0`, and `duration_ms: 0` for every one of the 8 job runs** | `get_workflow_run_usage` |
| 5 | **No log blob exists for any job** — freshly-minted signed URLs return HTTP 404, so nothing was ever written | `get_job_logs` with `return_content` |
| 6 | Check-run `output` is empty: no title, no summary, no annotation | `get_check_run` |
| 7 | **Control:** the same failure shape affects `Client CI`, `Agent Evaluation`, `Backend CI` and both plane-deploy workflows. Not one workflow succeeds | `list_workflow_runs` across the repo |
| 8 | The repository is **private** (unauthenticated fetch → HTTP 404), so its Actions minutes are metered | `curl` |

Observations 4, 5 and 7 together are decisive. A job that is billed zero milliseconds and never
writes a log line did not run — it was rejected at assignment, before any step, before
`actions/checkout`. And because it happens to *every* workflow regardless of content, it cannot
be caused by any workflow's content.

This is the signature of exhausted GitHub Actions minutes or a reached spending limit on a
private repository. The 2026-09-13 cutover date is consistent with a monthly quota.

### 1.2 What it explains

Every remaining blocker is downstream of it:

- **BLOCK-02** (production serves no `/api/v3`): the v3 cutover merged *after* 2026-09-13 and so
  has never deployed.
- **BLOCK-03** (revision unidentifiable): the deployed build is `78bdc83`, now dated exactly.
- **BLOCK-05, the CORS finding, the missing security headers**: all properties of that
  fourteen-day-old image, and **all already correct in current `main`** — see §3.
- **BLOCK-04**: is this, plus a second genuine fault fixed in §2.1.

### 1.3 What the account owner must do

1. Open **GitHub → Settings → Billing and licensing → Plans and usage**, and check Actions
   minutes used against the included allowance, plus whether a spending limit is set to `$0`.
2. Either raise the spending limit / add a payment method, or move CI to self-hosted runners.
3. Re-run `Backend CI` on `main`. A job that produces a log at all confirms the fix.

Until step 3 produces executed jobs, **nothing in this repository can be deployed**, and no
production gate can be evidenced.

---

## 2. What was fixed in code

Everything below is committed on this branch and verified by executed tests. Nothing here is
claimed on inspection alone.

### 2.1 BLOCK-04 Cause A — `secrets` in a job-level `if:` (fixed)

A second, genuinely distinct CI fault, independent of §1 and visible as a different failure
shape: the two plane-deploy workflows fail at **0 s with zero jobs created** (a startup failure),
where `Backend CI` creates jobs that then die.

**Root cause.** Both workflows gated jobs on `secrets.RAILWAY_TOKEN != ''` / `secrets.HF_TOKEN != ''`
inside `jobs.<id>.if`. GitHub exposes only `github`, `needs`, `vars` and `inputs` there. A
`secrets` reference is therefore not a condition that evaluates false — it makes the whole file
unevaluable, so the run ends before scheduling anything and **every job in the file dies**,
including `inference-plane-deploy.yml`'s `validate`, which never mentioned a secret. Four
occurrences across two files; no other workflow did this, which is why only these two failed at
0 s. A YAML linter accepts the file: the syntax is valid, the *context* is not.

**Fix.** A `preflight` job tests the secret inside a `run:` step — where `secrets` *is* available —
and publishes a boolean output the dependent jobs gate on. The secret is only ever tested for
emptiness; it is never echoed and never written to an output.

**Verified by:** `aura_backend/tests/unit/test_workflow_job_contexts.py`, 26 tests. Includes a
**negative control**: reintroducing the bad pattern fails 3 of them; restoring it passes all 26.
The suite also refuses `env`, `steps`, `job` and `runner` in a job-level `if:` — the same trap
under other names — and asserts the preflight cannot print the secret it handles.

### 2.2 BLOCK-04, second half — a deploy that does not happen is now visible (fixed)

`workflow_run` fires on *completed*, whatever the conclusion. So when `Backend CI` failed,
`Deploy VPS` concluded **`skipped`** — a grey tick that satisfies branch protection, reads as
success on the Actions home page, and pages nobody. That is the mechanism by which a fourteen-day
production freeze went unnoticed: "did not deploy" and "deployed" rendered identically.

**Fix.** Three changes:

- `deploy-backend.yml` gains a `blocked` job that runs precisely when `Backend CI` did not
  succeed, writes a step summary, and **fails**. Red, with the reason and a link.
- Both plane-deploy workflows gain a required-deploy switch (`vars.CONTROL_PLANE_DEPLOY_REQUIRED`,
  `vars.INFERENCE_DEPLOY_REQUIRED`). While unset, a missing secret skips with a notice naming
  which settings are absent; once set, it **fails**. The switch exists because Railway and the
  HF Space are genuinely not provisioned yet — pretending otherwise would be the same dishonesty
  in the other direction.
- `inference-plane-deploy.yml`'s silent `skipped` job became a named `not-configured` job, so an
  unconfigured deploy is a legible row rather than an absence.

### 2.3 BLOCK-03 — release identity (fixed)

The previous audit could only *bound* the deployed revision, indirectly, by noticing `/health`
still advertised a `gemini` settings field that `main` had deleted. That guesswork is now
unnecessary.

| Component | Change |
|---|---|
| `aura_core/config/settings.py` | New `ReleaseSettings`: `git_sha`, `git_ref`, `build_time`, `deployment_id`, `api_version`, `app_version`, `inference_contract`. `identified` / `revision` / `to_public_dict()`. |
| `aura_core/api/v3.py` | `GET /health/version` serves it. `/health/ready` carries it too, so a 503 names its own build. |
| `aura_backend/Dockerfile` | `ARG GIT_SHA/GIT_REF/BUILD_TIME/DEPLOYMENT_ID` → `ENV AURA_*`. Declared as the **last** layers before `EXPOSE`: they change every commit, and an `ARG` invalidates everything after it. |
| `deploy/docker-compose.prod.yml`, `docker-compose.dev.yml` | `x-release-args` anchor passed to all five services that build the image, so a worker can never report a different commit than the gateway. `worker-training` keeps `AURA_TRAINING_DEPS` via a merge key. |
| `deploy/scripts/vps-update.sh` | Exports the identity from `$AFTER` — the commit just checked out, not `HEAD` read back later, since `origin/main` can move mid-deploy. |
| `.github/workflows/deploy-backend.yml` | Passes `AURA_DEPLOYMENT_ID=gha-<run_id>` so a build names the CI run that produced it. |

**Fail-closed.** `AuraSettings.validate()` now refuses to boot a staging/production process that
cannot name its source commit. This is deliberately strict and consistent with the existing
production gate (which already refuses `debug`, mock auth, and in-memory stores). A deploy that
skipped the build arg fails loudly at startup instead of shipping an anonymous image.

**The check that was missing.** `vps-update.sh` now asserts, after restarting, that
`/health/version` reports the SHA just built — and **fails the deploy** if it does not, or if the
endpoint does not answer at all. An absent endpoint is a failure, not a skip: the endpoint and the
script ship in the same commit, so if it is not answering, the new image is not running. That is
exactly the silent-stale-deploy condition, now caught.

It asks the **gateway container directly** (`compose exec`, via `python` — the runner stage purges
curl), not through nginx. The claim under test is "the new image is running", so routing it through
the edge would let an unrelated DNS or TLS fault — BLOCK-01, which is still open — fail a deploy
that actually succeeded, or let a stale-but-reachable edge answer on the container's behalf. The
edge keeps its own separate check further down. `control-plane-deploy.yml` probes the Railway
health URL instead, which on Railway *is* the application endpoint.

**Verified by:** `aura_backend/tests/unit/test_release_identity.py`, 18 tests — the settings, the
endpoint (including that it bypasses the authenticator), the production gate, the payload
allowlist, and that the identity is actually *injected* by the Dockerfile, both compose files and
the deploy script. A settings field nothing populates would be identity theatre and pass every
other test in the file.

### 2.4 DEF-12 — stale API reachability matrix (fixed)

The one failing test in the recorded baseline. `docs/guides/api-reachability.md` regenerated via
`scripts/api_reachability.py`. The diff was inspected rather than accepted blindly: line-number
drift from client changes, one newly-discovered caller for
`/api/v3/auth/desktop/pair/approve`, and the new `/health/version` row. No route became
untriaged. Counts: 74 called, 16 triaged-uncalled, 8 infra.

### 2.5 Forbidden vendor inference — traced, and it is absent (verified)

Every remaining `gemini` reference in the repository was classified:

| Location | Classification |
|---|---|
| `aura_core/config/external_inference.py` | **The denylist that enforces the ban.** The opposite of an inference path. |
| `config.py:57-59`, `aura_core/llm/router.py`, `aura_core/domain/llm.py`, `services/insights.py`, `services/sse_marker_splitter.py`, `test_agents.py` | Historical comments explaining a removal. Dead wording. |
| `aura_client/.../calendar_repository.dart` | Sample task text in seeded demo data. Not an integration. |
| `services/automation.py`, `services/agent.py` | **Stale docstrings claiming Gemini; the code calls `ollama_service`.** Corrected. |

`services/agent.py:43` already stated "Inference is `ollama_service`. No client and no key to
hold", contradicting its own method docstring 60 lines later. Both docstrings were rewritten to
describe what the code does. This matters beyond tidiness: a stale `gemini` artifact is precisely
what made the previous audit briefly suspect live hosted-vendor inference.

No hosted-vendor inference path exists. The Google Drive and Calendar integrations were left
untouched — they are legitimate non-inference workspace integrations, which the brief explicitly
warns against removing by accident.

---

## 3. Corrections to the previous audit

Required by the standing rule that accurate evidence outranks a consistent story. Three findings
in `AURA_REAL_PRODUCTION_TEST_REPORT.md` describe the **stale deployed image**, not current
`main`, and reading them as code defects would have sent this remediation after bugs that do not
exist.

| Finding as recorded | Actual state of current `main` |
|---|---|
| **BLOCK-05** — "Qdrant down while `/health` returns 200; `/health/ready`, `/health/live` all 404" | `aura_core/api/v3.py` already serves `/health/live` (checks nothing, deliberately — a liveness probe that fails on a dependency outage turns degradation into an outage) and `/health/ready` (**503 when unhealthy**). `container.health()` aggregates provider health plus a `register_readiness_check` extension point, already used to gate on the Alembic schema. **The semantics the brief asks for are already implemented.** The 404s were the v1 build. |
| **CORS `Access-Control-Allow-Origin: *`** | `aura_service.py::_apply_cors` reads an explicit origin list and, when `*` is present, **disables `allow_credentials`** and logs a warning — the correct least-privilege behaviour for a wildcard. |
| **"zero security headers"** | `deploy/nginx/templates/aura.conf.template` sets HSTS, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy` and CSP across its server blocks. |

The honest reading: these were **not fixed in this remediation because they were never broken in
the code**. They will resolve the moment a current build deploys. They remain open as
*production* findings — untestable until then — and are recorded that way in the readiness matrix
rather than as passes.

Also corrected: the prior report attributed the plane-deploy 0-job failures solely to the
`secrets`-in-`if:` fault. That fault is real and is fixed in §2.1, but the account-level outage in
§1 was running concurrently. Both were present; the distinct failure shapes (0 jobs vs. jobs
created) are what separate them.

---

## 4. Production topology has changed since the audit — and got worse

Re-probed 2026-09-27 ~18:05 UTC:

| Property | At audit (earlier 2026-09-27) | Now |
|---|---|---|
| `aureon-ai.tech` DNS | IPv4 `152.239.118.239` | **IPv6 `2a02:4780:79:5dcd::1`** (Hostinger range) |
| TLS to `:443` | Valid Let's Encrypt cert for `velvet-group.shop` | Still fails hostname verification (`ssl_verify_result=1`) |
| Application | AURA AI OS Backend v1.0, 24 legacy paths | **Django `Bad Request (400)`** — an `ALLOWED_HOSTS` rejection |
| `152.239.118.239:443` | served the v1 AURA backend | `Connection reset by peer` |
| `169.58.188.43:443` (documented VPS) | unreachable | `Connection reset by peer` |

So the previously-observed stale AURA backend is **no longer reachable at any address this audit
knows of**. `aureon-ai.tech` now points at an unrelated Django site that does not even recognise
the hostname. BLOCK-01 has not merely persisted, it has regressed: production went from *stale*
to *absent*.

`/health/version` returns 400 there, as expected — that is the Django app answering, not AURA.

---

## 5. What could not be done, and why

Stated plainly rather than folded into an average. None of these are "skipped because
inconvenient"; each is blocked by a specific missing capability.

| Area | Status | Blocker |
|---|---|---|
| BLOCK-01: DNS, SNI, TLS certificate, removing VELVET routing | **BLOCKED** | Requires registrar and VPS access. This environment has neither, and DNS/TLS changes to a live domain are outward-facing acts needing the owner's authorisation. |
| BLOCK-02: deploy current v3 to production | **BLOCKED** | Requires §1 resolved *and* host access. Merging cannot deploy while Actions executes nothing. |
| Real inference plane (GPU, CUDA, vLLM, model load, streaming, batching) | **BLOCKED** | No GPU, no provisioned Space, no reachable deployment. |
| Production auth, authorization/isolation (User A vs B, admin, IDOR, escalation) | **BLOCKED** | Mandatory gate. Needs a reachable v3 deployment; it has none. |
| Memory / RAG quality (Recall@k, MRR, nDCG, groundedness) / planning / agents / tools against production | **BLOCKED** | Same. |
| Adversarial AI safety enforcement in production | **BLOCKED** | Mandatory gate. Same. |
| Backup / restore drill with measured RPO and RTO | **BLOCKED** | Mandatory gate. Needs VPS shell plus an isolated restore target. `deploy/scripts/backup-lib.sh` logic is covered by CI's `backup-scripts` job, which proves the crypto and retention logic — **not** a real backup, and CLAUDE.md already says so. |
| Observability, latency percentiles, TTFT, throughput | **BLOCKED** | Requires live traffic against a real deployment. Any number produced here would be fabricated. |
| Chaos / restart / recovery testing | **BLOCKED** | Requires host control. |
| Desktop build, `hostd` pairing, Flutter released client, Admin dashboard | **BLOCKED** | No Flutter or Dart toolchain in this environment (CLAUDE.md states this is expected); Client CI and Host Daemon CI are the real verification, and both are inside the §1 outage. |
| Network exposure from an authoritative vantage | **NOT TESTABLE** | Unchanged from the audit: a control probe showed this container blocks non-80/443 egress, so uniform timeouts measure the vantage point, not the target. Must be re-run from the VPS or provider console. |
| Rollback validation | **BLOCKED** | Needs a production-equivalent environment. |

**No result above was converted into a PASS.** No metric in this report was estimated.

---

## 6. Verification actually performed

| Level | What |
|---|---|
| **LOCALLY TESTED** | Full `pytest` suite executed against the changes — see §7. `test_release_identity.py` (18) and `test_workflow_job_contexts.py` (26) written and run. |
| **FAILURE TESTED** | Negative control on the workflow-context tests: bad pattern reintroduced → 3 failures; restored → 26 pass. Production gate exercised by constructing an unidentified `AuraSettings` and asserting `release.git_sha` appears in the problems list. |
| **STATICALLY VERIFIED** | All ten workflow files parse; no `secrets.` remains in any job-level `if:`. Both compose files parse, with all five backend-building services carrying the release args. `bash -n` on `vps-update.sh`. |
| **PRODUCTION TESTED** | **Nothing.** There is no reachable AURA deployment to test (§4). |
| **MEASURED** | The CI outage: 0 billable ms across 8 job runs, and the 2026-09-13 last-success boundary. |
| **CERTIFIED** | Nothing. See `AURA_REAL_PRODUCTION_CERTIFICATION.md`. |

---

## 7. Test results

Recorded from executed runs, not claimed.

**Baseline before this work** (`main` @ `71dd01b`): 2776 tests collected, **1 failure** —
`tests/architecture/test_api_reachability.py::test_the_committed_matrix_is_current`.

**First run after the release-identity and workflow changes: 3 failures.** Recorded because they
are the point — each was a real consequence of a real change, caught by the suite rather than by
production:

1. `test_api_reachability.py::test_the_committed_matrix_is_current` — adding `/health/version`
   made the committed matrix stale again.
2. `test_route_auth.py::test_protected_route_rejects_every_bad_credential[GET-/health/version]` —
   the security suite enumerates routes from the app and demands every one reject bad
   credentials. `/health/version` is deliberately public.
3. `test_settings_validation.py::TestProductionGate::test_a_correctly_configured_production_deployment_starts` —
   the new production requirement meant the suite's model of "correctly configured production"
   was now incomplete.

All three were fixed at the cause, **not by weakening a check**: the matrix was regenerated;
`/health/version` was added to `test_route_auth.py`'s `PUBLIC` allowlist with its justification
recorded there; and the production-gate test now sets `AURA_GIT_SHA`, because release identity is
now genuinely part of a correctly configured production deployment.

Failure 2 is worth keeping in view — it is the repository's own architecture working. A new
unauthenticated route cannot be added without a security test forcing the author to declare it.

**Final full-suite result:** see `AURA_PRODUCTION_TEST_EVIDENCE.md` EV-20.

---

## 8. Remaining blockers

Full detail in `AURA_PRODUCTION_BLOCKERS.md`. In dependency order:

1. **BLOCK-06 (P0, new, account-level)** — GitHub Actions executes nothing. Blocks everything
   below. Only the account owner can fix it.
2. **BLOCK-01 (P0)** — `aureon-ai.tech` serves an unrelated Django application; TLS hostname
   verification fails. Regressed since the audit. Needs registrar + VPS access.
3. **BLOCK-02 (P0)** — production serves no `/api/v3`. Unblocked by 1 and 2.
4. **BLOCK-03 (P0 → resolved in code, unproven in production)** — the mechanism now exists and is
   tested; it cannot be *demonstrated* until a build carrying it deploys.
5. **BLOCK-04 (P0 → Cause A fixed, Cause B is BLOCK-06)**.
6. **BLOCK-05 (P1 → already correct in code, unproven in production)**.
7. Four mandatory gates — authorization/isolation, adversarial safety, backup/restore, agent
   containment — remain **without evidence**, and are therefore treated as blocking rather than
   passing.

---

## 9. Honest limits of this report

- Everything in §2 is verified by locally executed tests. **None of it is verified in
  production**, because there is no reachable production. A tested fix is not a proven deploy.
- The §1 diagnosis is an inference from converging measurements (zero billable time, absent logs,
  repo-wide scope, private repository, a clean date boundary). It is strongly supported, but the
  billing page itself was not readable from here, so the *specific* entitlement cause —
  exhausted minutes vs. a `$0` spending limit vs. a payment-method failure — is **UNKNOWN**. The
  remedy in §1.3 begins by looking, not by assuming.
- The fail-closed release-identity gate is a deliberate strictness increase. If a deploy path
  outside `vps-update.sh` and the two plane workflows builds this image without the build args,
  it will now refuse to start in production. That is the intended behaviour, and it is the one
  change here most likely to surprise someone.
- The nginx and CORS findings in §3 were verified by reading the templates, not by observing
  headers on a live response — that is **STATICALLY VERIFIED**, not PRODUCTION TESTED, and the
  matrix records it as such.
