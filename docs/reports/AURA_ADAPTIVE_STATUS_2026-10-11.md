# AURA Adaptive Platform (Track AP): status, full test run and fixes

**Date:** 2026-10-11 · **Base:** `main` @ `0288863` · **Scope:** Track AP (AP0–AP13) and every
suite in the repository, run before recommending anything · **Standing rule:** built ≠ tested ≠
proven; nothing here is promoted without the evidence its exit criteria name.

## Bottom line

- **The adaptive platform's core (AP1–AP8) is built and green everywhere it can be tested from
  code:** default suite, architecture fitness, legacy suite, eval gate, and 36 Postgres-backed
  adaptation tests on a real PostgreSQL 16.15. It has run live once against a real system: Flame,
  on Railway.
- **Nothing in Track AP is "proven" yet.** Backend CI works again and is green on `main`. Each
  phase's own exit criteria for AP1 and AP3–AP6 are now met. The track's binding rule still holds
  them at BUILT until M0 exits, and M0 also needs branch protection plus green Client, Host
  Daemon and Admin runs.
- **AP9 (the Console Project Factory) has not started, and it is the critical-path blocker.**
  Every adaptation endpoint is reachable only through raw API calls. The admin console and the
  Flutter client have no callers.
- **The VPS production target has not taken a deploy since at least 2026-09-27.** This is an
  infrastructure issue only the owner can fix (see P0 below). Railway deploys work.
- **This change fixes five confirmed defects and one dependency vulnerability.**
  - Defects: structured tool results cut mid-JSON, three gate correctness bugs, and
    self-approvals that were invisible.
  - Vulnerability: Next.js critical advisory in `aura_admin`.
  - It also unblocks Host Daemon CI and corrects stale evidence documents.

## 1. Test run

All on Linux, Python 3.11 (the CI pin), Flutter 3.44.6 / Dart 3.12.2 (the CI pin), Node 22,
PostgreSQL 16.15.

| Suite | Baseline `main` @ `0288863` | This branch |
| --- | --- | --- |
| Backend `pytest tests --cov=aura_core` (CI invocation) | **3342 passed**, 56 skipped, 138 deselected; coverage 83% (identical to Backend CI #347) | **3350 passed** (+8 new), 56 skipped, 0 failed; coverage 83% |
| Architecture fitness `pytest tests/architecture` | passed | passed |
| Legacy `TESTING=True python -m unittest discover` | 100 ran, OK (4 skipped) | 100 ran, OK (4 skipped) |
| Agent evaluation gate (model-free) | PASS (19 baselined metrics pass, 0 fail) | PASS (19/19) |
| Postgres-backed adaptation + service keys (`tests/load/test_adaptation_*_postgres.py`, `test_service_keys_postgres.py`, `-m load`) | **36 passed** on PostgreSQL 16.15 | **36 passed** |
| Backup crypto (`REQUIRE_AGE=1`) | 56 passed | unchanged |
| `pip-audit` (backend lock, CI method) | no known vulnerabilities | unchanged |
| Admin `npm run lint` | exit 0, but **no ESLint config exists**, so it lints nothing | unchanged |
| Admin `npm run build` | OK (16 routes) | OK |
| Admin `npm audit` | **4 (1 critical, 3 high)** | 2 (1 high, 1 moderate): both are postcss bundled inside Next |
| Client `flutter analyze` / `flutter test` | no issues / **377 passed**, 3 skipped | not touched |
| Host daemon `dart analyze --fatal-infos` / `dart test` | no issues / **195 passed, 1 failed** (debugpy not installed) | the failure passes once `debugpy` is installed; CI now installs it |

**Not runnable here:**
- Playwright e2e (needs a live dev stack).
- Docker image builds (no daemon here; Backend CI's `docker-build` job is green on #347).
- `tests/load` beyond the adaptation and service-key files (need Qdrant, Redis or Ollama).
- Anything requiring the VPS.

The baseline numbers above are what the earlier "CI pending" claims could not show. They match
Backend CI #347 exactly.

## 2. Track AP status

| Phase | Built | CI on `main` | Live evidence | Status |
| --- | --- | --- | --- | --- |
| AP0 Charter/ADRs/gaps | yes | — | — | DOCUMENTED. ADR-018…028 are still *Proposed*. |
| AP1 AUSM + Blueprint | yes | green (#347) | — | Own exit criteria met; **held at BUILT by M0** |
| AP2 Connectors + vault | yes | green | real Postgres (2026-10-11) | Needs the dev-stack run, but the dev stack is on the unreachable VPS |
| AP3 Discovery | yes | green | live Postgres, both pilots | Own exit criteria met; held by M0 |
| AP4 Twin + diff | yes | green | twin persisted and diffed on Postgres | Own exit criteria met; held by M0. Label quality is unmeasured (no GPU). |
| AP5 Planner | yes | green | cross-domain report | Own exit criteria met; held by M0 |
| AP6 Compiler | yes | green | every SQL template on Postgres 16 | Own exit criteria met; held by M0 |
| AP7 Runtime | yes | green | deployed on Railway for Flame | Needs the dev-stack deploy/rollback run with audit trail |
| AP8 Eval gate | yes | green | Flame: 0.375 coverage under a waiver (report); operator reports 0.875 at default (PR #165) | M7 (real-model eval) needed for proven |
| AP9 Console Project Factory | **no** | — | — | Not started. The MVP-1 critical path ends here. |
| AP10–AP13 | no | — | — | Not started. AP12a service keys are partly built (ADR-029). |

**Structural limits to know:**
- A single project `p0` is hard-coded (`container.py`).
- `aura_connectors` is keyed by `id` alone.
- Super Admin is the only isolation boundary.
- The gate runs synchronously inside `POST /blueprints/{v}/evaluate`, with no overall time
  budget.

## 3. Findings

Severity: **P0** = act now · **P1** = before any production claim · **P2** = quality and roadmap.

| # | Sev | Finding | Evidence | Disposition |
| --- | --- | --- | --- | --- |
| A | **P0** | VPS production has been stale since ≥ 2026-09-27. Deploy VPS #189–#213 all failed: first because Backend CI was red, and since #211 with `ssh: connect to host … port 22: Connection timed out`. | Actions logs, run 37979466093 | **Owner action** (below) |
| B | **P0** | Leaked credentials not yet rotated: the Flame read token, the `svc-flame` service key, the HF Space key and a pasted GitHub token. | `flame-link2-live.md` caveats | **Owner action** |
| C | **P0** | `aura_admin` shipped Next.js 15.5.23, which has a critical advisory (unauthenticated RCE in image optimisation, among others) plus high-severity sharp/source-map-js advisories. The admin image is built by the prod compose file. | `npm audit` | **Fixed**: lock moved to Next 15.5.27 / sharp 0.35.5. The postcss advisory needs Next 16 (breaking): recommended below. |
| D | P1 | Structured (`output_schema`) tool results larger than `max_result_chars` were validated and *then* cut mid-JSON. The model, UI views and the gate then got unparseable text. PR #164 only fixed the gate probe. GAP-055's target ("row-aware truncation") had never been built. | `tools/registry.py` | **Fixed**: lists are shortened by whole items, and the result stays schema-valid. If it can't fit, the call fails with `result_too_large`. |
| E | P1 | The gate shared harvested `*_id` values across connectors, so an id read from system A could be sent to system B's API. | `adaptation/gate.py` | **Fixed**: the harvest is keyed by connector. |
| F | P1 | Self-approvals were invisible: approvals did not record the requester. The runbook claimed a second person was required, yet the live run self-approved. | `learning/approval.py`, runbook §2.2 | **Fixed** (decision: allow, audit). `requested_by` is stored in the payload, the audit detail carries `self_approved`, and the runbook is corrected. |
| G | P1 | Gate false failures. (1) A `workflow.status` column with more than 100 states failed a correct tool as `count_mismatch`: tool cap 100 vs oracle cap 200. (2) An agent with no tools raised `StopIteration`, recorded as `gate_error`. | unit tests reproduce both | **Fixed** |
| H | P1 | Host Daemon CI would be red on its next run: the debug-console test spawns a real `python -m debugpy`, which CI never installs. | local run: 195/196, then passes with debugpy | **Fixed**: CI installs debugpy (both jobs). |
| I | P1 | Open PR #165 corrupts `flame-link2-live.md`. Backslash escapes were interpreted, so the code-formatted ``tool_contract`` became a TAB plus "ool_contract", and ``not_run`` became a line break plus "ot_run". | PR diff | **Owner action**: fix before merging |
| J | P1 | `main` is unprotected. Commits pushed straight to it include a 629-file deletion (`f20a887`, reverted by a merge) and messages that misdescribe the change (`6e5883b` "No code changes made" edits `.gitignore`). | git history | Owner action (CB-02 / M0) |
| K | P2 | Stale evidence: GAP-043 / CB-01 said CI was dead; AP reports and gaps said "CI pending"; CB-32…38 / CB-42 said "No …"; the AP7 report said "No evaluation gate yet"; the AP8 report omitted #163/#164; the runbook said AR-7 was not run. | docs | **Fixed** (GAP-043 → `proven`; the others annotated with CI #347, still `built_not_proven`) |
| L | P2 | `aura_admin` has no ESLint config, so `next lint` stops at its setup prompt and exits 0. Admin CI's lint step checks nothing. | local run | Recommended |
| M | P2 | Legacy `routing_centroids.json` / `sentiment_centroids.json` are committed (despite `.gitignore`) with 768-dim vectors. Any 384-dim embedder makes routing and sentiment fail. CI logs `shapes (384,) and (768,) not aligned`. | CI #347 log | Recommended (legacy tree) |
| N | P2 | Gate depth: HTTP tools pass on schema only (no oracle). `_permission` sends placeholder arguments to live systems. Evaluation has no time budget. Harvesting runs only one retry round. | `gate.py` | Recommended |
| O | P2 | Retrieval quality on the docs corpus is low but stable: answer pass rate 0.22, retrieval pass rate 0.61, MRR 0.43. | eval gate summary | Track under M7 |

## 4. Fixed in this change

| Fix | Files | Tests (fail before, pass after) |
| --- | --- | --- |
| Row-aware truncation of structured results | `aura_core/tools/registry.py` | 3 in `tests/unit/test_adaptation_compiler.py::TestOutputSchema` |
| Gate: per-connector harvest, status cap, empty grant | `aura_core/adaptation/gate.py` | `TestStatusOracleCap`, 2 new plus 2 updated in `TestHttpProbeSeedsAndIdentifiers` |
| Self-approval recorded and audited | `learning/approval.py`, `adaptation/{connectors,deployment}.py`, `api/admin.py` | 2 cases in `test_adaptation_connectors.py`; JSONB round trip checked on PostgreSQL 16.15 |
| Next.js critical advisory | `aura_admin/package-lock.json` | `npm audit`, `npm run build` |
| Host Daemon CI installs debugpy | `.github/workflows/hostd-ci.yml` | debug-console test passes locally with debugpy |
| Evidence corrections | gap register (+ rendered MD), backlog, roadmap table, AP7/AP8 reports, Flame runbook | `tests/architecture/test_gap_register_schema.py` |

## 5. Recommendations, in order

**P0 — owner actions (cannot be done from code):**
1. **Restore or retire the VPS target.** Check the Contabo panel and the firewall for port 22 on
   `169.58.188.43`.
   - If the host moved, update the `VPS_HOST` secret and `deploy/known_hosts`.
   - If Railway is now the real production, disable `deploy-backend.yml` deliberately rather than
     leaving it red.
   - The AP2/AP7 dev-stack evidence also depends on this host.
2. **Rotate** the four credentials listed in `flame-link2-live.md`.
3. **Fix PR #165's escaped text** before merging it.
4. **Enable branch protection on `main`** (CB-02). With CI back, this is the last M0 blocker
   besides Client/Hostd/Admin runs.
5. **Close M0** by triggering Client, Host Daemon and Admin CI on one `main` SHA and writing
   `docs/reports/m0-ci-restoration.md`. This releases AP1 and AP3–AP6 to *proven*.

**P1 — next engineering work:**
6. **AP9: Console Project Factory.** Connectors, twin, plan, blueprint, evaluate, approve, deploy
   and rollback views in `aura_admin`, through the existing generic `/api/v3` proxy. This is the
   last MVP-1 phase.
7. **A dev-stack proof for AP2 and AP7**, once the VPS is back, following
   `flame-integration-runbook.md`. Record the items the 2026-10-07 report lacked: deploy audit
   entries, the tool-call audit record, full digests and the full gate report.
8. **Upgrade `aura_admin` to Next 16** for the bundled-postcss advisory, and add an ESLint
   config so lint actually lints.
9. **Accept or amend ADR-018…028.** They are all still *Proposed*.

**P2 — quality:**
10. Add an oracle for HTTP tools (a recorded fixture or a second read path), so API-only
    blueprints are checked on values, not just shape.
11. Give gate evaluation an overall time budget, or run it as a background job.
12. Plan multi-project before AP10: `project_id` is hard-coded, and connector ids are global.
13. Legacy centroid caches: store the embedding model and dimension, and recompute on mismatch.
    Stop committing the generated files.
14. Fix the hostd PTY reader's fd reuse and the 2-fd leak per session (documented in the hostd
    report, unfixed).
