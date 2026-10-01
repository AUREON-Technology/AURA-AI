# AURA Roadmap Reconciliation

**Baseline:** `5db6692` (2026-09-25). Maps every phase of every prior roadmap onto the single phase
system of [AURA_MASTER_UPGRADE_ROADMAP.md](AURA_MASTER_UPGRADE_ROADMAP.md) (`M0`–`M15`). Evidence
references (SEC-xx, §n) point into [AURA_FULL_SYSTEM_REVIEW.md](../reports/AURA_FULL_SYSTEM_REVIEW.md).

Decision vocabulary: **KEEP** (carried as-is into an M-phase) · **MERGE** (combined with overlapping
work) · **REPLACE** (goal kept, definition rewritten) · **REMOVE** (not doing) · **DEFER** (valid,
not before a named precondition) · **DONE-HIST** (completed work; history stays in the old ledger) ·
**STALE** (claim contradicted by the tree).

## 1. Why a new phase system

| Problem | Evidence |
| --- | --- |
| Four overlapping ID schemes: A–K; 0–26; DPL/IDE/Platform; FULL's own A–R | three roadmap files |
| Letter collision: FULL "Phase A" = desktop audit, upgrade-roadmap "Phase A" = evidence; FULL "Phase J" = desktop certification, upgrade-roadmap has **two** "Phase J" (workspaces, desktop engineer) | `AURA_UPGRADE_ROADMAP.md:495,634`; FULL §53 |
| Conflicting priority (desktop-first vs correctness-first) | FULL §53 vs MASTER Phase 1 |
| Conflicting architecture decisions (repo split; rented inference) | FULL DPL-1/3 vs `docs/deployment/architecture.md` §1, MASTER rule 5 |
| Work recorded as progressing while the phase-0 precondition (CI runs) never held | MASTER Phase 0 exit vs GitHub API (review §28) |

New IDs `M0`–`M15` are chosen to collide with nothing (gap severities use `P0`–`P3`).

## 2. `docs/AURA_UPGRADE_ROADMAP.md` (A–K)

| Old phase | Actual current state (re-derived) | New phase(s) | Decision | Reason / evidence |
| --- | --- | --- | --- | --- |
| A1 deploy prod compose | EXECUTED on VPS (Aug); **stale** — last automated deploy 2026-09-13 `78bdc83` | M2 | DONE-HIST + REPLACE | "1 and counting" is not a current state; M2 re-establishes it |
| A2 restore drill | EXECUTED once in a sandbox, not prod; offsite/`restore.sh` never run | M6 | MERGE | GAP-028 |
| A3 retrieval/answer corpus | TESTED (hash embedder, scripted provider); per-strategy open | M7 (EV-1/2), M10 | REPLACE | real-model/production-embedder requirement |
| A4 real-data-tier load test | MEASURED once (n=20, sandbox CPU) | M5 | DONE-HIST + MERGE | reference number retained with conditions |
| A5 pip-audit | TESTED in CI **when CI ran** | M0 | KEEP | depends on CI restoration |
| B1–B3 legacy seams | DONE (main.py removed, ADR-002 update) | — | DONE-HIST | legacy tests pass (100 OK here) |
| B2 follow-ups (memory 100 cap, offline outbox, client encryption of memory) | Open | M10 (cap), M11 | MERGE | named follow-ups never scheduled |
| C1 agent registry | TESTED | — | DONE-HIST | review §8 |
| C2/C4 personal-OS CRUD | TESTED; **server-visible only** | M11 | MERGE | two-store fork (review §21) |
| C3/C5 agent evaluator | TESTED (echo) | M7 (EV-6) | REPLACE | echo ≠ agent success |
| D1 projects/goals | Backend TESTED; client modules now **exist** (roadmap "deferred" is STALE); compile unverified | M8.0/M14 (compile), M11 | MERGE | review §21 |
| D2 goals in context | TESTED | — | DONE-HIST | |
| D3 prioritisation tools | TESTED | — | DONE-HIST | |
| E1 durable outbox | TESTED (+ real PG claim) | M11 (crash test), M3 (outbox gauge) | MERGE | Z3 no-loss under worker crash unproven |
| E2 triggers/briefing | TESTED | M11 | MERGE | notification safety missing |
| E3 insight citations | TESTED; generation deferred | M11 (after M7) | DEFER | needs real-model eval |
| F1 OCR | EXECUTED end to end | — ; vision → M12 | DONE-HIST | |
| F2 STT/TTS | EXECUTED end to end | M12 (Arabic ASR, WER) | MERGE | GAP-020 |
| F3 client voice/PDF wiring | IMPLEMENTED, **not compiled in CI since 09-13** | M8.0/M14 | MERGE | |
| G1 telemetry + scorecard | TESTED (+ real PG claim) | M3 | MERGE | HTTP metrics missing |
| G2 multi-model routing | MEASURED (two Ollama models) | M4 | MERGE | GPU pool needed |
| G3 PostgresPool lock | TESTED | — | DONE-HIST | GAP-001 closed_stale |
| G4 DNS-rebinding guard | TESTED; TOCTOU disclosed | M1 (IP pinning optional), M15 | KEEP | residual |
| H live voice | SCAFFOLDED/IMPLEMENTED | M12 | MERGE | device/load gates open |
| I transcript NLP | SCAFFOLDED | M12 | MERGE | |
| J (workspaces, ADR-013/014) | BUILT, sandbox off in prod | M9; exemption rule amended in M1 (SEC-02) | REPLACE | **duplicate ID "J"** |
| J (desktop engineer, ADR-011) J1–J5c | IMPLEMENTED; never CI-built since 09-13 | M8, M9 | MERGE | **duplicate ID "J"** |
| K desktop IDE D0–D10 | D0 done; D1–D9 IMPLEMENTED (code-read); D10 not started | M8.0–M8.13; D10 → REMOVE for now | MERGE | D10 "Personal AI OS Desktop" has no spec/exit criteria |
| §4 scope decisions (no CRM, no social posting, no k8s, no base-model training) | Valid | "Do not build yet" list | KEEP | |
| §5 scorecard | Mixed current/stale | review §29/§38 | REPLACE | |
| §6 production program waves 1–2 | BUILT, not deployed, CI never ran on them | M1–M7 | MERGE | |

## 3. `docs/production/MASTER_PRODUCTION_ROADMAP.md` (0–26)

| Old | Current state | New | Decision | Reason |
| --- | --- | --- | --- | --- |
| 0 Baseline freeze & gap ledger | Register exists & schema-tested; exit ("CI runs on main") **not met** | M0 | REPLACE | CI restoration becomes the whole phase |
| 1 Correctness & security blockers | Partly built (SecretManager fixed, trainer honesty); GAP-005/043/008/010/027 open | M0 + M1 | MERGE | adds SEC-01/02/03 |
| 2 GPU inference plane | Config built; no GPU host | M4 | MERGE | + ADR-017 gate |
| 3 50+ user load matrix | Harness built, self-validated with echo only | M5 | KEEP | |
| 4 RAG 2.0 | Open | M10 | KEEP | |
| 5 Memory 2.0 | Encryption built not deployed; scopes partly exist | M6 (encryption), M10 | MERGE | |
| 6 Personal OS | Open | M11 | KEEP | store decision made explicit |
| 7 Agent platform 2.0 | Built (ADR-016), echo-evaluated | M11 | MERGE | |
| 8 Multimodal | Not implemented | M12 | KEEP | |
| 9 Voice 2.0 | Open | M12 | MERGE | |
| 10 NLP | Scaffolded | M12 | MERGE | |
| 11 Knowledge graph | Built, unmeasured | M10 | MERGE | |
| 12 Proactivity 2.0 | One trigger; dead `proactive` pkg | M1 (delete pkg), M11 | MERGE | |
| 13 Learning/MLOps | Mechanism executed on tiny model | M13 | KEEP (deferrable) | |
| 14 Evaluation 2.0 | Scripted gates only | M7 | KEEP | |
| 15 Observability 2.0 | Partial | M3 | KEEP | moved **before** inference/load (measurement precedes tuning) |
| 16 Flutter completion | In progress | M14 | KEEP | |
| 17 Admin control plane | Open | M14 | MERGE | |
| 18 Desktop engineer | Built/unproven | M8, M9 | MERGE | |
| 19 Deployment hardening | Partly (vllm pinned in compose) | M1 (isolation), M4 (pinning), M15 (parity) | MERGE | |
| 20 HA/DR | Open | M6 | KEEP | |
| 21 Security hardening | Partly | M1 + M15 | MERGE | |
| 22 Model supply chain | Open | M4 | MERGE | pinning is an inference-cutover precondition |
| 23 Production readiness testing | Not started | M15 | MERGE | |
| 24 Canary | Not started | M15 | MERGE | |
| 25 SLOs in force | Not started | M15 | MERGE | |
| 26 Continuous improvement | Not started | M15 (tail) | MERGE | |
| "What should NOT be done" | Valid | Master §2 principles + review §40.10 | KEEP | |
| Definition of production-ready | Valid | Master §21 | KEEP + extended | |

## 4. `docs/plans/AURA_FULL_UPGRADE_ROADMAP.md`

### 4.1 Deployment track (DPL)

| Old | Current state | New | Decision | Reason |
| --- | --- | --- | --- | --- |
| DPL-0 baseline audit | Effectively delivered by `docs/deployment/architecture.md` §2 (ownership map) | — | DONE-HIST | |
| DPL-1 repository split | Rejected with reasons in `docs/deployment/architecture.md` §1 | — | **REMOVE** | no architectural benefit; history rewrite; out of granted scope; logical split already gives independent deploys |
| DPL-2 inference API contract | Partial: OpenAI-compatible surface + `tests/deployment/test_inference_contract.py` pins defaults | M4.2 | REPLACE | contract = documented OpenAI subset, not a bespoke `/v1/vision`/`/v1/audio` API that nothing calls yet |
| DPL-3 HF inference plane | BUILT (Space source); deploy workflow invalid (SEC-15); never proven; hosting policy unresolved | M4 (behind ADR-017) | REPLACE | D-1 |
| DPL-4 Railway control plane | BUILT (`railway.json`, workflow invalid); never deployed by CI | M4/M6 option under ADR-017 | DEFER | optional target; VPS remains production until ADR-017 says otherwise |
| DPL-5 service auth | Bearer key; no rotation; metrics public | M4.4 | KEEP | |
| DPL-6 ProviderRouter integration | DONE (openai_compatible) | — | DONE-HIST | but "sole provider" config rejected (M4.5) |
| DPL-7 independent CI/CD | Workflows exist, 2 invalid, CI dead | M0 | MERGE | |
| DPL-8 independent versioning | DOCUMENTED ONLY | M4.2 | MERGE | versioning = contract version + pinned revisions |
| DPL-9 failure testing | Not started | M4 failure tests | KEEP | full list carried |
| DPL-10 deployment observability | Partial | M3 + M4 metrics | MERGE | |
| DPL-11 rollback | DOCUMENTED ONLY; runtime DDL makes image rollback unsafe | M2 rollback + M6 schema authority | REPLACE | |

### 4.2 Desktop track (IDE)

| Old | Current state (Phase-0 audit + review) | New | Decision |
| --- | --- | --- | --- |
| IDE-0 baseline audit | DONE (code-read); not executed | M8.0 (execute it) | REPLACE |
| IDE-1 design system | Partial (`AuraIdeDensity`) | M8.1 | MERGE |
| IDE-2 shell | Implemented; resize/persist fixed, unverified | M8.1 | MERGE |
| IDE-3 explorer | Implemented; context menu #108 unverified | M8.2 | MERGE |
| IDE-4 editor | Partial | M8.2 | MERGE |
| IDE-5 command/navigation | Partial | M8.2 | MERGE |
| IDE-6 PTY | Windows-only ConPTY | M8.3 | KEEP |
| IDE-7 git/diff | Implemented; remote ops via direct push, unverified | M8.4 | KEEP |
| IDE-8 AI coding | Agent panel real; depends on SEC-01 tokens | M8.5a | KEEP |
| IDE-9 inline intelligence | Not implemented; **infeasible on CPU inference** | M8.5b | **DEFER** (gated on M4 TTFT) |
| IDE-10 problems/tests/debug | Problems + Python DAP real; no test explorer | M8.6 | KEEP |
| IDE-11 agent workspace | Worktrees real, narrow | M8.7 | MERGE |
| IDE-12 approval & security UX | hostd queue real; SEC-02 server-side gap | M1 + M8.7 | MERGE |
| IDE-13 host management | Implemented; no restart/bundle | M8.8 | MERGE |
| IDE-14 personalization | Not started | M8.9 | MERGE (low priority) |
| IDE-15 accessibility | Minimal | M8.9 | MERGE |
| IDE-16 performance | Unmeasured | M8.9 | MERGE |
| IDE-17 large repos | Unmeasured (5k-file cap) | M8.9 | MERGE |
| IDE-18 crash recovery | Partial session restore | M8.8 | MERGE |
| IDE-19 packaging/updates | Not implemented | M8.10 | KEEP |
| IDE-20 cross-platform cert | Windows non-gating, Linux gating, macOS none | M8.12 (macOS deferred) | MERGE |
| IDE-21 desktop observability | Not implemented | M8.11 | KEEP |
| IDE-22 security cert | Not started; junction test added #109 unverified | M8.12 | MERGE |
| IDE-23 AI UX evaluation | Not started | M7 EV-11 + M8.13 | MERGE |
| IDE-24 golden-path cert | Not started | M8.13 | KEEP |
| FULL §32 desktop priority P0–P18 | Ordering mostly sound inside desktop | M8 sub-order | KEEP (reordered: security before AI coding; inline after M4) |

### 4.3 Platform track

| Old | New | Decision | Note |
| --- | --- | --- | --- |
| Platform-1 immediate correctness/security | M1 | MERGE | "PostgreSQL pool race" item is **STALE** (GAP-001) |
| Platform-2 production inference plane | M4 | MERGE | 9 pools listed → start with **one** chat/coder pool; add pools on measured need |
| Platform-3 scalability | M5 | MERGE | |
| Platform-4 RAG 2.0 | M10 | MERGE | |
| Platform-5 memory 2.0 | M6 + M10 | MERGE | |
| Platform-6 Personal OS completion | M11 | MERGE | |
| Platform-7 agent platform | M11 | MERGE | |
| Platform-8 multimodal | M12 | MERGE | |
| Platform-9 voice 2.0 | M12 | MERGE | |
| Platform-10 NLP | M12 | MERGE | |
| Platform-11 knowledge graph | M10 | MERGE | |
| Platform-12 proactivity | M11 | MERGE | |
| Platform-13 real MLOps | M13 | MERGE (deferrable) | |
| Platform-14 evaluation | M7 | MERGE | |
| Platform-15 observability | M3 | MERGE | |
| Platform-16 Flutter completion | M14 | MERGE | |
| Platform-17 admin | M14 | MERGE | |
| Platform-18 HA/DR | M6 | MERGE | |
| Platform-19 model supply chain | M4 | MERGE | |
| Platform-20 canary | M15 | MERGE | |

### 4.4 FULL §53 master sequence (A–R) and §54 DoD

| Old | New | Decision | Reason |
| --- | --- | --- | --- |
| A–J desktop-first before any platform work | M0 → M1 → (M8 ‖ M3–M7) | **REPLACE** | desktop code cannot be compiled/tested until CI runs (M0); SEC-02 is desktop's worst exposure (M1) |
| K two-repository split | — | **REMOVE** | see DPL-1 |
| L HF inference deployment | M4 | REPLACE | ADR-017 first; never a sole provider |
| M Railway control-plane deployment | M4/M6 option | DEFER | optional target |
| N secure integration | M4.4 | MERGE | |
| O load/performance testing | M5 | KEEP (moved earlier than RAG/agents) | |
| P RAG/memory/agent/multimodal | M10–M12 | MERGE | |
| Q MLOps + evaluation gates | M7 (gates, early) + M13 (MLOps, late) | **SPLIT** | evaluation must precede feature upgrades, not follow them |
| R canary | M15 | MERGE | |
| §54 DoD "independent GitHub repositories" | — | REMOVE | |
| §54 DoD "Control Plane on Railway / Inference on HF" | Master §21 "ADR-017 in force" | REPLACE | hosting is a decision, not a definition of done |
| §55 completion rule ladder | Master §2 ladder (superset) | KEEP | |

## 5. Gap register → M-phase

| Gaps | Phase | Note |
| --- | --- | --- |
| 001, 002, 003 | — | closed_stale (confirmed) |
| 004, 048 | M2 (deploy) | built_not_proven → proven only once live |
| 005, 043, 045, 007 | M0 | **043 date corrected to 2026-09-13** |
| 006, 021, 022 | M7 | |
| 008, 009, 010, 018, 027, 046, 047 | M1 | 009/018 need only deployment + CI |
| 011, 012, 030, 031, 034, 035, 042 | M4 | **012/032 partly stale** (prod vllm pinned) |
| 041 | M5 | |
| 013, 044 | M14 (+M8.0) | 013 partly stale (projects/goals modules exist) |
| 014 | M1 (verify) | code + test present (review §9) — status can move to proven once CI green |
| 015 | M2 | client header uncompiled |
| 016, 028, 029 | M6 | |
| 017 | M13 | |
| 019, 020 | M12 | |
| 023 | M10 | |
| 024 | M11 | **partly stale** (finance/health/engineer scopes exist) |
| 025, 026 | M9 | |
| 032, 033 | M4 / M0 | |
| 036, 037 | M3 | |
| 038 | M1 | dead legacy tasks |
| 039 | M0 (doc fix) | CLAUDE.md additionally stale on ADR range (015/016) |
| 040 | M0 | |
| 049–062 | AP0–AP13 | Track AP ([AURA_ADAPTIVE_PLATFORM_ROADMAP.md](AURA_ADAPTIVE_PLATFORM_ROADMAP.md)), added 2026-10-01; parallel to M-phases and gated by M0/M1/CB-11/M4/M7/M14 |
| **New (to be added to the register):** SEC-01, SEC-02, SEC-03, SEC-08, SEC-09, SEC-10, SEC-11, SEC-12, SEC-15, dual schema authority, two Personal-OS stores, silent planning fallback | M1 / M4 / M6 / M11 | from this review |

## 6. Summary of changes

| Category | Items |
| --- | --- |
| **Kept** | Evidence standing rule; ADR-002/003/006/007/008/009/011/013/015/016 decisions; scope exclusions (CRM, social posting, k8s, base-model training); gap register as machine-checked ledger; desktop sub-order; DPL-9 failure list; production Definition of Done |
| **Merged** | 27 production phases + 20 Platform phases + DPL + IDE + A–K follow-ups into 16 M-phases |
| **Replaced** | "Phase 0 baseline" → "M0 CI restoration"; A3/C3 scripted gates → real-model EV suite; DPL-2 bespoke API → OpenAI-subset contract; FULL desktop-first order → security-first then parallel |
| **Removed** | Repository split (DPL-1, FULL K, DoD item); D10 "Personal AI OS Desktop" (no spec); PostgresPool race item (closed) |
| **Deferred** | Railway as primary target (until ADR-017); inline AI (until GPU TTFT); video; macOS desktop cert; real LoRA (M13); model-generated insights (after M7) |
| **Newly added** | M2 production reconciliation; ADR-017 inference hosting; SEC-01 argument-bound approvals; SEC-02 routing-aware exemption (ADR-014 amendment); SEC-03 secret rotation; single schema authority; Personal-OS store decision; evaluation requirements EV-1..EV-12; silent-fallback telemetry; hostd `.git/hooks` write denylist |

## 7. Contradictions resolved

| # | Contradiction | Resolution |
| --- | --- | --- |
| C1 | HF Space primary (deployment docs, FULL) vs no rented inference without ADR (MASTER, GAP-034) | ADR-017 gate in M4; production default stays VPS/CPU until merged |
| C2 | Repo split required vs rejected | Removed |
| C3 | Desktop-first vs correctness-first | M0/M1 first, then parallel lanes |
| C4 | CI dead since 09-22 (register) vs 09-13 (API) | 09-13 (evidence precedence: CI results > docs) |
| C5 | "Production deployments: 1 and counting" | Production is stale at `78bdc83`; M2 |
| C6 | Roadmap §1 "agents are prompts" still listed open after Phase C | Stale; review §8 gives the accurate split (1 autonomous, 7 tool-scoped) |
| C7 | A4 "3 cores" vs "4-core/15 GB" | Both retained as reported; confidence marked low (review §29) |
| C8 | CLAUDE.md: ADRs 001–014, SYNCABLE_MODELS in client, admin "lightly used" | Doc fixes in M0 (GAP-039) |
| C9 | D1 "Flutter deferred" vs modules exist | Modules exist; compile unverified (M8.0) |
| C10 | GAP-024 "all shared" vs scoped personas | Partly stale |
| C11 | `config.dart` comment "not hardcoded" vs hardcoded secret | SEC-03 |
| C12 | Desktop audit "VERIFIED" vs roadmap "not proven" | Audit's VERIFIED = code-read; ladder state IMPLEMENTED until CI executes |
| C13 | Adaptive platform proposal letters ("Phase A…") vs historical A–K IDs | New track uses the **AP** prefix (AP0–AP13) so no ID collides with `docs/AURA_UPGRADE_ROADMAP.md` |
