# Executive summary

**Audit:** AURA AI full-system engineering audit · **Baseline:** `main` @ `ffbbb9fff777a7b6117236c67d517fd201ac2208` (2026-10-31) · **Mode:** read-only; nothing in the repository, its git state or any live system was modified · **Author:** lead auditor, with ten parallel pass agents.

## Overall assessment

AURA is an unusually complete self-hosted AI platform at the source level and a thinly proven one at the operational level. The architecture described in the ADRs is real and enforced: hexagonal layering is checked by AST fitness tests, the kernel is the only execution path, model-emitted tool calls never execute, providers are declared as data behind one router, and trust channels and taint gate side effects. Almost every roadmap capability has code behind it. What is missing is evidence that the system behaves that way where it runs today, and a short list of wiring gaps that leave built capabilities unreachable.

By the repository's own rule ("built and tested are not proven"), **nothing is proven on the current production target.** Production is Railway with one hosted provider; CI has not executed since 2026-10-28; `main` is unprotected and auto-deploys; the only committed end-to-end evidence predates the Railway cutover. Weighted readiness score: **2.10 / 5**. Verdict: **not production-ready**, with a credible path to ready that is mostly operational work, not rewrites.

Five measurements kept apart (details in `readiness_scorecard.md`): architectural maturity high (4/5), implementation completeness medium-high (3/5), integration completeness medium-low (2/5), operational readiness low (1/5), production readiness: no.

## What this audit could and could not verify

Executed here: 4,219 backend tests passed across unit, security, architecture, e2e, integration and deployment suites (17 failures, all traced to the local venv drifting from the lock, two untracked secret files, or Windows portability; none to a product defect); 135 legacy unittest cases with no product failures; `flutter analyze` and `dart analyze` clean; hostd 219/10 on Windows with most failures caused by a full temp volume. Not runnable here: `flutter test` (full `C:` volume), the route-auth and IDOR sweeps (FastAPI version drift), Playwright, load, certification, training, and every live check. No production endpoint, Railway setting, HF Space, VPS or GitHub Actions state was probed; those facts are quoted from `docs/status/*` and labelled. Full detail in `verification_log.md`.

## Verified capabilities (fixed in source, confirmed by code read and local tests)

- Layering, kernel-only execution, provider abstraction, pre-first-token failover, sensitivity ceilings, hash-pinned dependencies, socket-proxy `EXEC=0`, owner-scoped storage, no SQL injection path, `PostgresPool` double-checked locking.
- The October security lens is largely closed in source: JWT issuer/audience binding, production refuses to boot without a secret, demotion revokes sessions and devices, scoped pairing tokens, 60-second WebSocket re-checks, streamed body limits, hostd allowlist no longer executes repo code.
- ADR-032 single Personal-OS store for tasks, calendar, finance and health, with agent CRUD tools and per-user data keys (pending migration 0019 live).
- Sync protocol fixes, approval binding by call digest, planner-failure disclosure, graceful drain: all present in code, none CI-run since.

## Critical gaps (P1)

1. **Taint does not cross agent delegation** (FSA-001): a prompt-injected orchestrator turn can delegate to a persona whose CRUD tools then write without confirmation. One-day fix.
2. **A GCP service-account key and a backend `.env` are recoverable from git history, rotation unrecorded** (FSA-002).
3. **Production deploys with no CI gate**: Actions has no runners, `main` is unprotected, Railway auto-deploys every push, and Railway ignores `railway.json` (FSA-020, 021, 022).
4. **The VPS deploy path is dead on a changed host key** with the host's state and secret exposure unknown (FSA-023).
5. **On the live target every memory, document, tool, Engineer and image turn has no eligible provider** while the sensitive-capable Space is down; only context-free chat is served (FSA-024).
6. **Admission limits and the host-agent registry are per process** while the image runs four workers, so the desktop Engineer fails in proportion to worker count on Railway (FSA-025, 026).

Significant P2 themes: 30-day unscoped desktop token that can fetch the user's data key; proxy-keyed rate limiter on Railway; unconfined trusted-root commands in hostd; the backup container holds the key-encryption key; no alerting, backup or key escrow for the Railway datastores; the operator console is unreachable on every live target; retrieval strategies and the knowledge graph are built but unused; the prompt sees at most two 280-character chunks; fine-tuning never reaches traffic; vision has no provider anywhere; 47 routes have no consumer.

Totals: 84 consolidated findings (9 P1, 25 P2, 38 P3, 12 P4) from 117 pass-level findings; 44 features, none `VERIFIED WORKING`, 20 implemented but not fully verified, 10 partially implemented, 10 implemented but unreachable, 1 broken (CI/CD), 3 not implemented (video, push notifications, desktop packaging). Prior-finding rechecks: most October items are fixed in source; GAP-043 has regressed; several status and gap-register lines are stale in both directions (FSA-120).

## Immediate priorities (Phase 0 of `remediation_roadmap.md`)

Owner actions, no code: restore runners and protect `main`; stop Railway auto-deploy or enable wait-for-CI; link `railway.json`; rotate the historical key and `.env` values and record it; confirm or decommission the VPS; restore a sensitive-capable provider; enumerate Railway workers/beat; set the metrics token; escrow the data-encryption key; confirm migration 0019 live.

Code, each a small PR: inherit taint across delegation; short-lived desktop app tokens with a reachable revoke; one correctly keyed rate limiter plus forwarded-IP trust on Railway; destructive-verb containment in trusted hostd roots; live-voice frame size checks; per-file backup secret mounts; refuse the hash embedder in production and expose the embedder on readiness.

Then Phase 1: make every existing gate executable and required, add the regression tests the October bugs showed were missing, and produce the first real-model evaluation report. Nothing later can be proven until those two phases land.
