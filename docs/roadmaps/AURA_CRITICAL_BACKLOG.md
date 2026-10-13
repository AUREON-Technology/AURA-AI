# AURA Critical Backlog

**Baseline:** `5db6692` (2026-09-25). Only **actionable, unresolved** items, ordered by execution
priority. Each links to its phase in [AURA_MASTER_UPGRADE_ROADMAP.md](AURA_MASTER_UPGRADE_ROADMAP.md)
and evidence in [AURA_FULL_SYSTEM_REVIEW.md](../reports/AURA_FULL_SYSTEM_REVIEW.md). When an item
closes, record the evidence in `docs/reports/` and update `gap_register.yaml`; remove the row here.

Severity: **Critical** (blocks everything / live exposure) · **High** (blocks a production claim) ·
**Medium** · **Low**.

## Tier 1 — do first (M0/M1/M2)

---
**ID:** CB-01 · **Severity:** Critical · **Area:** CI/CD
- **Claim:** "Actions dead since 2026-09-22" (GAP-043).
- **Actual state:** Every job on `main` has died in 2–5 s with no steps since **2026-09-13** (Backend CI #222 onward); PR runs too.
- **Evidence:** GitHub API — Backend CI #220 last success (`78bdc83`); job `108130701162` 2 s; Host Daemon CI jobs 2–4 s on 2026-09-25.
- **Impact:** nothing merged in 12 days is CI-verified; deploy is gated on CI, so nothing deploys.
- **Root cause:** consistent with an account-level Actions/billing block (no runner assigned); not a workflow defect for Backend CI.
- **Required action:** repo owner restores Actions (GitHub Settings → Billing/Actions); rerun Backend CI on `main`.
- **Verification:** linked green run with every job executing steps.
- **Roadmap phase:** M0 · **Blocking:** everything.
- **Update 2026-10-11:** runners are back. Backend CI on `main` executes its steps from #306
  (2026-10-01) and is green from #332 (2026-10-07); #347 ran every job green. Verification met
  for Backend CI (GAP-043 `proven`). Still open under M0: branch protection (CB-02), and Client,
  Host Daemon and Admin runs (not triggered since 2026-09-29). Deploy VPS now fires but times out
  on SSH to the VPS (runs #211–#213): see `docs/reports/AURA_ADAPTIVE_STATUS_2026-10-11.md`.

---
**ID:** CB-02 · **Severity:** Critical · **Area:** Governance
- **Claim:** protection pending (GAP-005).
- **Actual state:** `main` unprotected; 4 direct pushes on 2026-09-25 (`61caac0`, `35f0371`, `d3e2918`, `8e26156`).
- **Evidence:** `git log --first-parent --no-merges origin/main --since=2026-09-16`.
- **Impact:** unreviewed code (incl. security-relevant path handling) reaches the deploy branch.
- **Root cause:** no branch protection; workflow habit.
- **Required action:** after CB-01, enable protection with required checks (Backend `test`/`pip-audit`/`docker-build`, Client `analyze-and-test`/`build-android`/`build-linux`, Hostd, Admin).
- **Verification:** API/settings record in `docs/reports/`; a failing PR shown blocked.
- **Roadmap phase:** M0 · **Blocking:** yes.

---
**ID:** CB-03 · **Severity:** Critical · **Area:** Deployment / Security
- **Claim:** security fixes "fixed" (GAP-048, GAP-004).
- **Actual state:** not deployed — last successful Deploy VPS #151 at `78bdc83` (2026-09-13); #152–#175 skipped. Live VPS SHA: UNKNOWN.
- **Evidence:** GitHub API.
- **Impact:** cross-user session overwrite, refresh-token double spend, WS reauth as another user, cross-user ingestion versioning, SSRF gaps likely live.
- **Root cause:** CB-01.
- **Required action:** record live SHA (`git -C /opt/aura-stack rev-parse HEAD`); after CB-01/CB-02 and Tier-1 fixes, deploy `main` (M2 procedure incl. DB restore point and age key precondition).
- **Verification:** live SHA == certified SHA; HTTP-level security tests against live gateway.
- **Roadmap phase:** M2 · **Blocking:** yes.

---
**ID:** CB-04 · **Severity:** High · **Area:** Tools / Safety (SEC-01)
- **Claim:** "a turn whose context was quarantined cannot run a side-effecting tool without explicit confirmation" (ADR-006, CLAUDE.md).
- **Actual state:** confirmation is not bound to the approved call. Client sends tool names + positional call ids; server accepts either; resume re-plans from scratch.
- **Evidence:** `aura_core/execution/engine.py:480-484`; `aura_client/lib/features/chat/presentation/chat_view_model.dart:139-147`; `.../data/chat_repository.dart:253-282`; `aura_core/kernel/kernel.py:1150`.
- **Impact:** an approval for one action can authorize a different one after re-planning (injection-steerable).
- **Root cause:** stateless confirmation design.
- **Required action:** server-stored pending approvals keyed by argument digest; opaque approval tokens; execute the stored step; drop name-level confirmation.
- **Verification:** test — approve `delete_task(A)`, force re-plan to `delete_task(B)` → refused.
- **Roadmap phase:** M1 · **Blocking:** side-effecting autonomy, desktop AI coding (M8.5a).

---
**ID:** CB-05 · **Severity:** High · **Area:** Desktop Engineer (SEC-02)
- **Claim:** `workspace_local` effects are "confined to an ephemeral workspace destroyed on expiry" (ADR-014; `tests/architecture/test_workspace_local_tools.py`).
- **Actual state:** the exemption applies unchanged when tools run on the user's real machine via `aura_hostd`; trusted roots run any non-denylisted command; writes to `.git/hooks` allowed.
- **Evidence:** `aura_core/tools/policy.py:114-115`; `kernel.py:988-1015`; `aura_hostd/lib/src/command_policy.dart` (trusted → allow).
- **Impact:** injected content can execute commands as the user without confirmation.
- **Root cause:** exemption is a static tool property, not a routing decision.
- **Required action:** routing-aware exemption; taint ⇒ confirmation on host routing; hostd write denylist for `.git/hooks/**`, `.git/config`; amend ADR-014.
- **Verification:** tainted desktop-routed `run_command` → `needs_confirmation`; fitness test updated.
- **Roadmap phase:** M1 · **Blocking:** desktop GA.

---
**ID:** CB-06 · **Severity:** High · **Area:** Secrets (SEC-03)
- **Claim:** desktop OAuth client secret "NOT hardcoded … Empty by default" (`config.dart` comment; upgrade roadmap J5a).
- **Actual state:** hardcoded `defaultValue: 'GOCSPX-…'`, in git since `cad6e7a` (2026-09-12).
- **Evidence:** `aura_client/lib/core/config/config.dart:45-48`.
- **Impact:** OAuth client impersonation; policy violation (no secrets in client code). Google treats installed-app secrets as non-confidential, which limits but does not remove impact.
- **Root cause:** direct push without review/secret scan.
- **Required action:** rotate in Google Cloud; remove default; secret-pattern test over `aura_client/`, `aura_hostd/`.
- **Verification:** `git grep -n GOCSPX-` empty; login fails closed without `--dart-define`.
- **Roadmap phase:** M1 · **Blocking:** desktop release.

---
**ID:** CB-07 · **Severity:** High · **Area:** CI config (SEC-15)
- **Actual state:** `control-plane-deploy.yml` (lines 40-42, 77-79) and `inference-plane-deploy.yml` (61, 108) use `secrets.*` in job-level `if:`; both show as file-named runs failing in 0 s on every push.
- **Evidence:** runs 36156221775, 36156223086.
- **Impact:** Railway/HF deploys can never run; red noise on every push.
- **Required action:** gate inside a step (`if [ -z "$TOKEN" ]`) or use `vars`; add `actionlint`.
- **Verification:** workflows show by `name:` and reach job evaluation.
- **Roadmap phase:** M0 · **Blocking:** Railway/HF target only.

---
**ID:** CB-08 · **Severity:** High · **Area:** Secrets layout (GAP-046, GAP-047)
- **Actual state:** backup container mounts all of `deploy/secrets` incl. `data_encryption_keys`; dev stack shares prod keys.
- **Required action:** per-file mounts; dev key file; parity test in both compose files (same commit).
- **Verification:** fitness test; one backup cycle on VPS.
- **Roadmap phase:** M1 → M2 · **Blocking:** encryption-at-rest claim.

---
**ID:** CB-09 · **Severity:** High · **Area:** Deploy isolation (GAP-027)
- **Actual state:** dev stack uses prod Ollama (single slot) over `aura_edge`.
- **Required action:** separate dev inference or quota; parity test.
- **Roadmap phase:** M1 · **Blocking:** production latency integrity.

---
**ID:** CB-10 · **Severity:** Medium · **Area:** Auth config (SEC-10)
- **Actual state:** no startup refusal without `JWT_SECRET` in production (`services/jwt_auth.py:54-67`); per-process random secret across 4 workers.
- **Required action:** refuse in staging/production; require ≥32 bytes.
- **Verification:** settings test.
- **Roadmap phase:** M1.

---
**ID:** CB-11 · **Severity:** Medium · **Area:** Tenant isolation (SEC-09)
- **Actual state:** Qdrant treats missing `owner_id` as global (`qdrant_store.py:408-410`).
- **Required action:** explicit global sentinel; reject writes without owner.
- **Roadmap phase:** M1.
- **Update 2026-10-12:** fixed in code. `GLOBAL_OWNER` is the only global owner; both vector adapters
  refuse owner-less points; an owner-less point matches no read
  (`tests/unit/test_vector_owner_isolation.py`, in-memory and in-process Qdrant). The isolation
  probe on a real Qdrant server (AP10 exit) is still to run.

---
**ID:** CB-12 · **Severity:** Medium · **Area:** HTTP limits (SEC-08, SEC-11)
- **Actual state:** legacy limiter keys on `request.client.host`; payload limit trusts `Content-Length`; nginx 20 MB vs gateway 5 MB.
- **Required action:** one client-IP resolver; streamed size limits; per-route consistency.
- **Roadmap phase:** M1.

---
**ID:** CB-13 · **Severity:** Medium · **Area:** Dead/unreviewed code (GAP-008, GAP-010, GAP-038)
- **Actual state:** `engineering2`, `proactive`, `continuous_intelligence`, `security/{authenticator,policy,rate_limiter,secret_manager}` have zero importers; `aura_hostd/build/aura_hostd.exe` + logs tracked; root strays; legacy ingest tasks registered.
- **Required action:** delete/untrack via reviewed PR; guard test.
- **Roadmap phase:** M1.

---
**ID:** CB-14 · **Severity:** Medium · **Area:** Kernel (silent fallback)
- **Actual state:** `_plan_and_execute` swallows all exceptions and answers directly (`kernel.py:1171-1173`).
- **Required action:** typed degraded outcome + metric + alert.
- **Roadmap phase:** M1.

## Tier 2 — production foundation (M3/M4/M5/M6)

| ID | Sev | Area | Actual state (evidence) | Required action | Verification | Phase |
| --- | --- | --- | --- | --- | --- | --- |
| CB-15 | High | Observability | No HTTP request metrics; buckets stop at 60 s/12 s (`observability/metrics.py:45-46`) | Route histogram + status counter; widen buckets; outbox/queue gauges | Grafana p95 matches load p95 | M3 |
| CB-16 | High | Inference decision | Rented HF Space as sole provider (`docs/deployment/control-plane.md:75-76`) vs GAP-034 | ADR-017; never a sole remote provider | ADR merged | M4 |
| CB-17 | High | Inference capacity | `NUM_PARALLEL=1`, CPU, p95 70.5 s (A4) | Pinned GPU pool + failover | Cutover + failure reports | M4 |
| CB-18 | High | Supply chain | LLM by tag; embedder/STT no revision; HF Space `vllm:latest`; licenses unrecorded (GAP-030/031/042) | Pin + startup check + license table | Test + MODEL_REGISTRY | M4 |
| CB-19 | High | Capacity | No load matrix (GAP-041); Phase-3 numbers are echo artifacts | 1→100 matrix + soak on prod topology | Report | M5 |
| CB-20 | High | Schema | 26 runtime-DDL tables outside Alembic; readiness checks Alembic only | Alembic baseline; forbid runtime DDL; downgrade tests | Fitness test | M6 |
| CB-21 | High | DR | No offsite restore drill; no RPO/RTO (GAP-028) | Drill onto fresh host | Report | M6 |
| CB-22 | Medium | Data residency | Railway/Qdrant Cloud plaintext chunks (SEC-12); chunks outside encryption ADR | Encryption ADR covers chunks & third parties | ADR | M6 |

## Tier 3 — quality & product (M7–M12, M14)

| ID | Sev | Area | Actual state | Required action | Phase |
| --- | --- | --- | --- | --- | --- |
| CB-23 | High | Evaluation | All gated quality from scripted providers/hash embedder (GAP-021) | EV-1..EV-9 real-model baselines, gated | M7 |
| CB-24 | Medium | Guards | Leak/groundedness guard precision unknown (GAP-022) | Labelled FP suite; tune | M7 |
| CB-25 | High | Desktop | Desktop + hostd not compiled/tested in CI since 09-13; `build-windows` non-gating | M8.0 re-baseline; make Windows gating | M8 |
| CB-26 | Medium | Desktop | PTY Windows-only; no bundled/signed hostd; no restart-on-crash; no installer/updates | M8.3, M8.8, M8.10 | M8 |
| CB-27 | Medium | Engineer | Workspace VPS verification never run (GAP-025) | Run script; commit report | M9 |
| CB-28 | Medium | Personal OS | Two stores; agent-created tasks invisible in app | Store decision ADR + implementation | M11 |
| CB-29 | Medium | Voice | English-only STT while Arabic TTS ships (GAP-020) | Multilingual ASR + WER | M12 |
| CB-30 | Medium | Client | Release APK on floating Flutter (GAP-044); 16 uncalled routes; admin CI not on PRs (GAP-007) | Pin; wire/declare; PR-trigger admin | M14 |
| CB-31 | Low | Docs | CLAUDE.md ADR range/SYNCABLE_MODELS/admin; `rag/agentic.py:119` "no OCR"; duplicate Phase J; gap register dates (GAP-039) | Doc corrections | M0 |

## Track AP — Adaptive AI Platform (AP0–AP13, parallel track)

Plan: [AURA_ADAPTIVE_PLATFORM_ROADMAP.md](AURA_ADAPTIVE_PLATFORM_ROADMAP.md). None of these may be
reported beyond BUILT until M0 restores CI evidence.

| ID | Sev | Area | Actual state | Required action | Phase |
| --- | --- | --- | --- | --- | --- |
| CB-32 | Medium | Governance | Track doc, ADR-018…028 (still Proposed) and GAP-049…062 merged | Merge track doc, ADR-018…028, GAP-049…062 | AP0 |
| CB-33 | Medium | Domain model | Built; CI green on main (#347); held at BUILT by M0 (GAP-049) | AUSM + Blueprint + digest | AP1 |
| CB-34 | High | Connectors / secrets | Built; CI green + real Postgres; dev-stack run pending (GAP-050) | Read-only ports, `secret_ref` vault, connector approval; after SEC-01..03 | AP2 |
| CB-35 | Medium | Discovery | Built with HR/e-commerce pilots; CI green + live Postgres; held by M0 (GAP-051, GAP-052) | Deterministic discovery, Project Twin + diff, HR/e-commerce pilots | AP3–AP4 |
| CB-36 | High | Generated access | Built; row-aware truncation of structured results added 2026-10-11 (GAP-054, GAP-055) | `QueryTemplate` builder + `test_no_freeform_sql`; `output_schema` | AP6 |
| CB-37 | High | Runtime seam | Built; deployed live on Railway for Flame; dev-stack run pending (GAP-056) | `ActiveBlueprint` swap, digest-bound approval, rollback | AP7 |
| CB-38 | Medium | Evaluation | Built; Flame run needed a coverage waiver (0.375), operator reports 0.875 at default after #163/#164 (PR #165); M7 for proven (GAP-057) | Generated suites incl. injection; gate deploy | AP8 |
| CB-39 | Medium | Console | No Project Factory UI (GAP-058) | `/console/adaptation` + Playwright | AP9 |
| CB-40 | High | Isolation | CB-11 fixed in code (2026-10-12); isolation probe on a real Qdrant still to run (GAP-059) | Fix CB-11 first; isolation probe | AP10 |
| CB-41 | Medium | Personal OS | Personal AURA hardcoded outside any blueprint (GAP-062) | Project 0 parity → shadow → retire coupling | AP13 |
| CB-42 | High | First integration | Flame AR-1…AR-8 built; Link 2 live run on Railway 2026-10-07 (`flame-link2-live.md`), Link 1 not run (GAP-063) | Run `docs/guides/flame-integration-runbook.md` on the dev stack; write the Link 1 / Link 2 live reports | AP7/AP12a |

## Not in this backlog (deliberately)

Repository split, Kubernetes/microservices, video understanding, macOS desktop certification,
inline AI completions before GPU TTFT, real LoRA on the production base, CRM/social publishing —
see the master roadmap §20 and the review §40.10.
