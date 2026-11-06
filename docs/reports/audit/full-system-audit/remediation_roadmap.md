# Remediation roadmap (planning artifact — nothing here was executed)

**Audit:** AURA AI full-system engineering audit · **Baseline:** `main` @ `ffbbb9f` · **Date:** 2026-10-31 · **Author:** lead auditor.

This roadmap is ordered by dependency and severity, not by subsystem. It preserves the existing architecture (hexagonal layering, AIKernel as the only execution path, ProviderRouter as the only inference path, ADR-003/030 self-hosting rules, confirmation gating, Alembic + existing stores) and the canonical `docs/roadmaps/AURA_MASTER_UPGRADE_ROADMAP.md` phase names (M0–M15) where they map. Finding IDs are from `gap_analysis.md`. Effort: S ≤ 1 day, M days, L weeks. **No phase is complete until its evidence-based acceptance criteria are met and the evidence is committed under `docs/reports/`.** Rollback for every code change is "revert the PR"; migrations and infrastructure items name their own rollback.

Dependency spine: **Phase 0 (owner actions on CI, Railway, VPS, secrets) unblocks everything**, because until runners run and `main` is protected no later phase can be *proven*, and until a sensitive-capable provider is healthy most user-facing features cannot be exercised live.

---

## Phase 0 — Critical safety and correctness (M0/M1/M2)

**Objectives:** stop unverified code reaching production; remove the two P1 security defects; make the live target able to serve sensitive turns; close the cheapest P2 security gaps.

**Findings addressed:** FSA-001, FSA-002, FSA-003, FSA-004, FSA-005, FSA-006, FSA-007, FSA-008, FSA-009, FSA-017, FSA-020, FSA-021, FSA-022, FSA-023, FSA-024, FSA-026 (short-term half), FSA-027, FSA-030, FSA-031, FSA-032.

**Prerequisites:** owner access to GitHub (billing, branch protection), Railway dashboard, Google Cloud (key rotation), VPS console.

**Proposed changes (owner, no code):**
1. Restore GitHub Actions runners; re-run Backend, Client, Host Daemon, Admin, Repo Hygiene on `main` (FSA-021).
2. Protect `main` with required checks; disable Railway GitHub auto-deploy or enable "Wait for CI"; set `vars.CONTROL_PLANE_DEPLOY_REQUIRED=true` (FSA-020).
3. Link `aura_backend/railway.json` as the service config file; snapshot the dashboard into `docs/reports/` (FSA-022).
4. Confirm the VPS host-key fingerprint out of band; decide keep/decommission; if keep, re-pin by PR; if decommission, rotate every secret shared with Railway (FSA-023).
5. Rotate the historical GCP service-account key and every value of the historical `.env`; delete the local `deploy/.env.bak-*` and the local `credentials.json`; record the rotation date (FSA-002, FSA-003).
6. Restore `hfspace` on an always-on flavor or provision a second sensitive-capable node; do not raise `hosted1` to `sensitive` without an ADR-030 §4 decision (FSA-024).
7. Enumerate Railway services; create `worker-realtime`, `worker-ingestion`, `beat` if absent (FSA-027). Set `AURA_WORKERS=1` on Railway until FSA-026's long fix lands.
8. Set `AURA_METRICS_TOKEN`, confirm `/metrics` → 401 (FSA-009). Seal `AURA_DATA_ENCRYPTION_KEYS` offline (FSA-030). Verify `/health/ready` shows `schema.revision 0019` after the next deploy, then run `scripts/move_personal_os_records.py --dry-run` (FSA-032).

**Proposed changes (code, each its own PR):**
- `TurnRequest.inherited_taint` set by `delegate_to_agent`, OR'd into child taint (FSA-001).
- Desktop app login issues a short access + device-bound refresh pair; 30-day form only for the scoped daemon token; `device_name` on pairing; device list + revoke in the app (FSA-004).
- Remove `RateLimitMiddleware` (or key it on `extract_client_ip`) and add `--forwarded-allow-ips` to the Dockerfile CMD, pinned by `test_deploy_robustness.py` (FSA-005).
- hostd: argument containment for destructive verbs in trusted roots; backend sends `cwd` = active root (FSA-006).
- Live voice: frame byte validation + utterance byte ceiling (FSA-007).
- Backup container: per-file secret mounts, parity in both compose files, fitness test (FSA-008).
- `AuraSettings.validate()` refuses `hash-embed-v1` in staging/production; readiness reports the embedder; `control-plane.md` lists `AURA_EMBEDDING_*` (FSA-031).
- Move `remember_fact` out of `_READ_TOOLS` (FSA-017).
- Scope `test_no_committed_client_secrets.py` to `git ls-files`; desktop build scripts refuse a non-empty client secret (FSA-003).

**Acceptance criteria:** a green run of every workflow on `main` with steps executed; a PR shown blocked by a required check; a Railway deploy whose trigger is the workflow; rotation record committed; `tests/unit/test_delegation.py` taint case green; a live RAG turn and a live tool turn succeed (recorded in `docs/reports/`); `/health/ready` reports sensitive-capable provider and embedder; `/metrics` returns 401 without the token.
**Required tests:** named per item above plus `tests/security` re-run on a Python 3.11 venv built from the lock.
**Rollback:** owner settings are reversible in their dashboards; code PRs revert cleanly; no migration in this phase.
**Effort:** owner S–M; code 6 × S + 2 × M. **Definition of completion:** all acceptance evidence committed and `docs/status/*` updated with the verifying commit.

---

## Phase 1 — Verification and regression protection (M0/M3)

**Objectives:** make every existing gate executable and gating; add the regression tests the October bugs showed were missing; produce the first real-model evidence.

**Findings addressed:** FSA-080, FSA-081, FSA-082, FSA-083, FSA-084, FSA-085, FSA-048, FSA-063, FSA-113, FSA-120, G-03, G-04, G-05, G-06, G-07, G-08, G-09, G-10.

**Prerequisites:** Phase 0 items 1–3.

**Proposed changes:**
- Sanctioned local path = Python 3.11 venv from `requirements.lock`; `conftest` warns when `fastapi.__version__` differs from the lock (FSA-080, G-07). Generate a hashed `requirements-dev.lock` and use it in all three workflows (G-06).
- Backend CI installs `espeak-ng` and `tesseract-ocr` so engine-backed voice/OCR tests run; implement `tests/load/test_live_voice_roundtrip.py` against the built image (FSA-081).
- A scheduled/dispatch job runs `evaluation/runner.py` and `evaluation/vision_eval.py` against the dev stack with the production embedder and the deployed model; commit the first report; keep the deterministic gate as the merge gate (FSA-082, G-02, G-09).
- Fix 8.3-path canonicalisation in client and hostd tests, then remove `continue-on-error` from the Windows jobs (G-03, FSA-063); tag `posix_pty_session_test.dart` with its own timeout (G-04).
- Add coverage reporting for `api/`, `services/`, `infrastructure/`; move the four testcontainers legacy tests into the CI `postgres` job (G-05).
- Fix `control-plane-deploy.yml` health/version derivation with a unit test (FSA-048).
- Reachability generator excludes test dirs, requires an API base, records the verb (FSA-083).
- Railway-targetable `verify_hosted_inference.py` run with Redis and real fallback; include check 10; commit (FSA-084).
- Green hostd CI on Linux and Windows; record the GAP-026 real-machine session and run `verify-engineer-workspaces.sh` on a host with the sandbox profile (FSA-085).
- Regression tests for the bug classes that slipped: approval replay (`approve A → re-plan B` refused), sync cursor skip, memory id mismatch, tainted delegation, host-routed approval after server timeout (FSA-041 precursor), `write_files` in Engineer declarations, Celery task→queue consumption.
- Delete `deploy/vps_known_hosts.tmp`; refuse `*.tmp` in the artefact guard (FSA-113). Correct the documentation lines listed under FSA-120; add a "verified against commit" line to each status doc.

**Acceptance criteria:** every workflow green and required; `tests/architecture` and `tests/security` pass in CI (not just locally); first real-model evaluation report in `docs/reports/`; Windows jobs gating; `docs/guides/api-reachability.md` regenerated without the false positives.
**Rollback:** CI-only changes; revert the PR. **Effort:** M overall (several S PRs, two M). **Definition of completion:** a dated CI run id for each suite recorded in `testing_and_evaluation.md`'s successor.

---

## Phase 2 — Core reliability (M4/M5 prerequisites)

**Objectives:** make concurrency, persistence, queueing and failover behave on the real topology.

**Findings addressed:** FSA-025, FSA-026 (long half), FSA-041, FSA-046, FSA-010, FSA-011, FSA-012, FSA-013, FSA-015, FSA-016, FSA-018, FSA-059, FSA-066, FSA-073, FSA-090, FSA-091, FSA-092, FSA-093, FSA-094, FSA-100, FSA-110, FSA-111, FSA-112.

**Prerequisites:** Phase 0; Phase 1 gates running.

**Proposed changes:**
- Redis-backed cross-process admission/quota controller behind the existing `AdmissionController` interface (FSA-025); host-agent routing via Redis pub/sub or a dedicated single process per ADR-011 (FSA-026).
- Host-routed tool cancellation: cancel frame on server timeout, daemon marks the approval cancelled; Engineer context deadline ≥ longest host-routed tool (FSA-041).
- Client sends `Idempotency-Key` on every side-effecting write; pinned by tests (FSA-046).
- asyncpg `command_timeout`/`max_inactive_connection_lifetime`; computed connection budget and explicit `max_connections` (FSA-073).
- `upgrade head && downgrade -1 && upgrade head` in the CI postgres job; migration-review checklist item (FSA-090).
- Security hygiene batch: prod CORS default (FSA-010), streamed `fetch_url` with byte budget (FSA-011), nginx-only forwarded IPs in both compose files (FSA-012), hostd `control.json` 0600 (FSA-013), uid-indexed session sets + refresh-family revocation (FSA-015), audit/telemetry retention purge + pseudonymised erasure (FSA-016), vector payload content off when Qdrant is off-host (FSA-018), live-voice language allow-list (FSA-059), `forget_source` payload key (FSA-066), `needs_reencryption` counter and a recorded `reencrypt_fields.py` run (FSA-094).
- Provider hygiene: boot check and documented Groq example for `SUPPORTS_JSON_MODE` (FSA-092); map Ollama `done_reason` (FSA-093); `*_MODEL_REVISION` settings folded into index identity and telemetry, Whisper baked or warmed (FSA-110); digest pins for the HF Space image and nginx/postgres/redis/tinyproxy, required `VLLM_MODEL_REVISION` (FSA-111); remove or wire `TOOLS_MAX_CALLS_PER_TURN` (FSA-112); `_drain_finetune_queue` reuses the worker container, `ApprovalService.get()` (FSA-091); compose parity on sandbox network, attestation default, limits (FSA-100).

**Acceptance criteria:** `tests/load` run on the live topology with a committed concurrency matrix (GAP-041 exit); an Engineer turn succeeds from every gateway worker; a retried client write is deduplicated (test); downgrade path exercised in CI; two-source-IP limiter check live.
**Rollback:** each item independent; the admission controller keeps the in-process implementation selectable by setting. **Effort:** L overall (two M items, many S). **Definition of completion:** load report + the per-item tests green in CI.

---

## Phase 3 — Integration completeness (M6/M8)

**Objectives:** connect what is built but unreachable; decide the fate of every uncalled route.

**Findings addressed:** FSA-040, FSA-042, FSA-043, FSA-044, FSA-045, FSA-047, FSA-057.

**Prerequisites:** Phase 0 (console hosting decision, runners).

**Proposed changes:**
- Client sets `desktop_task_isolation` from the IDE chat; Agent Review panel populated; status doc corrected until then (FSA-040).
- Host the console (Railway service for `aura_admin` or the VPS once deploys work); wire service-keys, job polling, scorecard, refresh/latest; extend `mock-gateway.mjs`; AP9 stays on Track AP (FSA-042).
- Remove or 410 `/api/v3/learning/*` and `build_lora_dataset_task`; route `ingest_pdf_task` through `KnowledgeEngine.ingest`; lazy legacy imports in `tasks.py`; test that every registered task's queue is consumed (FSA-043).
- `/pair/approve` checks `engineer_agent`; device list + revoke in the host panel; 4401 surfaced as "pair again" (FSA-044).
- Owner decision table for the 47 uncalled routes: wire / API-only (owner, date) / remove; update CB-30 (FSA-045).
- Wire `fetch_url`/file/weather builders from settings or drop them from the grant (FSA-047); add `write_files` and an `mcp_*` policy to the Engineer grant with a grant-coverage test (FSA-057).

**Acceptance criteria:** console login succeeds on the live target; `GET /api/v3/tools`, `/agents/runs`, `/knowledge/graph` each have a consumer or a dated API-only note; an isolated desktop task shows in Agent Review; the Engineer model's declarations include `write_files`.
**Rollback:** revert; the console hosting change is a deploy-config change reversible in the platform. **Effort:** M. **Definition of completion:** regenerated reachability matrix with ≤ the agreed API-only set uncalled.

---

## Phase 4 — AI platform completeness (M7/M9/M10/M11/M12)

**Objectives:** make the AI features do what their ADRs say, or relabel them.

**Findings addressed:** FSA-050, FSA-051, FSA-052, FSA-053, FSA-054, FSA-055, FSA-056, FSA-058, FSA-060, FSA-064, FSA-065, FSA-067.

**Prerequisites:** Phase 0 provider fix; Phase 1 real-model evaluation job (to measure the changes).

**Proposed changes:**
- Pass `knowledge=self.retrieval` (the facade) to `KernelDependencies` and `/knowledge/search` with a fitness test, or relabel strategies/graph as not-in-path in ADR-005 and GAP-023 (FSA-050).
- Per-provider/pool prompt budget; raise `top_k`/`prompt_chunk_max_chars` defaults for non-CPU providers; answer suite over the rendered prompt (FSA-051).
- `RecordKind.PROJECT/GOAL` in the synced store with generated CRUD tools and decrypted titles; integration test on real Postgres (FSA-052).
- Wire `RetrievalEvaluator` into the refresh pipeline; lifecycle test that a recall drop blocks promotion (FSA-053); real generations or documented incremental refresh (FSA-054).
- Fine-tuning decision: (a) merge → GGUF → `create_model` → canary behind `RegressionGate` with datasets in Postgres/object storage, or (b) relabel as dataset preparation; in both cases refuse promotion of non-servable artefacts (FSA-055).
- Vision: pinned VLM pool on an always-on Space; `mode` (`vlm`/`ocr_only`) in capabilities; vision eval report (FSA-056, G-09).
- Multilingual Whisper with a WER report; `tesseract-ocr-ara`; `ocr_lang` validated at build (FSA-058).
- Orchestrator `allowed_tools` excludes capability-gated tools or the comment is corrected (FSA-060); consolidation/extraction decisions documented (FSA-064, FSA-065); delegation targets resolved at call time or documented (FSA-067).

**Acceptance criteria:** a real-embedder retrieval report shows the prompt-budget change's effect; `AURA_KNOWLEDGE_RETRIEVAL_STRATEGY` changes retrieval in a test; agent can list/create projects and goals; a recall drop blocks a refresh in a test; GAP-017's exit report (or the relabel) committed; a vision eval report with a pinned model.
**Rollback:** every change behind existing settings; facade wiring reverts to `KnowledgeEngine`. **Effort:** L (fine-tuning option (a) is its own L; vision depends on GPU hosting). **Definition of completion:** the ADRs named match the code and the reports exist.

---

## Phase 5 — Performance and scaling (M5)

**Objectives:** measure before scaling.

**Findings addressed:** FSA-070, FSA-072, FSA-074.

**Prerequisites:** Phases 0 and 2 (cross-process limits, live provider).

**Proposed changes:** run `tests/load` and `tests/benchmarks` on the live Railway + hosted topology and on the VPS if retained; record TTFT, generation time, retrieval, tool and total latency separately with model, concurrency, prompt/output sizes and warm/cold state; take the 200k-chunk lexical measurement; build the Postgres FTS adapter only if the ceiling is approached; document the corpus ceiling.
**Acceptance criteria:** a committed load matrix replacing `phase-a4-load-test.md` as the reference; GAP-011/041 closed with evidence. **Rollback:** none (measurement). **Effort:** M. **Definition of completion:** capacity doc updated with measured numbers and conditions.

---

## Phase 6 — Product and operational readiness (M8.10/M13/M14/M15)

**Objectives:** operate the live target like production; ship the clients.

**Findings addressed:** FSA-028, FSA-029, FSA-014, FSA-049, FSA-061, FSA-062, FSA-114.

**Prerequisites:** Phase 0.

**Proposed changes:** Prometheus + Alertmanager for Railway using the existing configs, critical alerts routed to a human, one live scrape recorded (FSA-028); `backup.sh` as a Railway cron with an age recipient and S3 target, then `restore-drill.sh` against that output with a report and measured RPO/RTO (FSA-029); hostd supervised restart, re-pair prompt on 4401, bundled daemon, installer/signing/updater (FSA-061); macOS network entitlement or drop macOS (FSA-062); dead dev preset removed and a readiness probe before switching (FSA-049); hostd `/status` liveness-only (FSA-014); prune stale worktrees and strays (FSA-114); one Android device report and an iOS decision.
**Acceptance criteria:** an alert fired and acknowledged in a drill; a restore drill report against Railway data; a signed desktop installer that bundles hostd; `docs/status/*` rows moved from BUILT to PROVEN with CI run ids and report paths.
**Rollback:** infrastructure additions are additive. **Effort:** L. **Definition of completion:** `gap_register.yaml` entries GAP-028/036/037/026 at `proven`.

---

## Sequencing summary

| Phase | Blocks | Blocked by | Owner-only items |
| --- | --- | --- | --- |
| 0 | everything | — | runners, branch protection, Railway config, VPS decision, key rotation, provider hosting, workers, tokens, escrow |
| 1 | 2, 3, 4 (proof) | 0 | none beyond CI minutes |
| 2 | 5 | 0, 1 | none |
| 3 | 4 (console for AI ops) | 0 | console hosting decision |
| 4 | — | 0, 1, 3 | fine-tuning option (a) vs (b); vision GPU hosting |
| 5 | — | 0, 2 | none |
| 6 | — | 0 | alert routing, backup target, signing certificates |

Quick wins that do not displace Phase 0 (all S, no owner dependency): FSA-001, FSA-007, FSA-017, FSA-057, FSA-047, FSA-066, FSA-093, FSA-112, FSA-113, FSA-120.
