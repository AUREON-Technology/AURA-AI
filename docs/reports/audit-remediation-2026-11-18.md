# Audit remediation, 2026-11-18

Two audits were run against `main` on 2026-11-18. This report records what was done about each
finding, the evidence for it, and what is still unproven.

- **App Product Audit:** findings F1–F58.
- **Client–server integration audit:** findings INT-101–INT-130.

Before any fix, three read-only passes re-checked every finding against `main` at `4e31e61`.
Every finding still held, except F11, which was half-fixed. The fixes then ran as 12 parallel
streams (B1–B6 backend and deploy, H daemon, A console, C1–C3 client). Each stream worked in its
own worktree, with a failing test written first. The streams were merged onto
`ccr-727f386d-pmrg0l`, and the merged branch was then reviewed as a whole.

**The repository's standing rule applies:** *built* and *tested locally* are not *proven*.
Nothing here ran in CI (CB-44), on a device, on the VPS, or on Railway. The gates below ran
locally on the merged branch.

## Gates on the merged branch

| Suite | Result |
|---|---|
| Backend `pytest tests --cov=aura_core --cov-fail-under=82` (Python 3.11, hashed locks) | 6161 passed, 70 skipped, 0 failed; coverage 86.19 %. Baseline on `4e31e61` was 5778 passed. This run predates the review fixes; the re-run is recorded in the PR. |
| Backend `pytest tests/architecture` | all passed |
| Legacy `TESTING=True python -m unittest discover` | 148 ran, OK (39 skipped), same as baseline |
| Postgres 16, `pytest tests/load -m load` | 120 passed, 39 skipped, 0 failed. The skips need Docker, MySQL, MSSQL, Redis, live models or a GitHub token. |
| `flutter analyze` / `flutter test` (Flutter 3.44.6) | No issues / 1293 passed, 4 skipped, 0 failed |
| `aura_hostd`: `dart analyze --fatal-infos` / `dart test` | No issues / 546 passed, 1 failed. The failure is the `debugpy` DAP test, and the `debugpy` Python module is not installed in this environment. |
| `aura_admin`: `tsc --noEmit`, `npm run lint`, `npm run build` | clean, 0 warnings, built. Playwright ran in stream A: 38/38 against the production build. On `next dev`, the same two specs fail on `4e31e61` under load. |

## Findings

**Status values:**
- FIXED means the code changed and a test pins it. The test failed before the change, unless
  noted.
- PARTIAL and NOT REPRODUCED are explained on their row.

### Backend: metering, billing, tenancy (B1, B2, B5)

| ID | Status | What changed · test |
|---|---|---|
| F6 | FIXED | A streamed message counts once its first token is sent; it is released only if the turn fails, errors or loses its client before any token. Spec 5.3 is amended. `tests/integration/test_plan_enforcement.py` (real ASGI disconnect after the last token). |
| F7 / INT-114 | FIXED | Live voice runs plan admission and the `voice_live` rate limit on every committed utterance and every confirm, with the F6 charging rule. Turns carry the team `knowledge_owner`, `use_memory` and `origin`. The client shows the same wording as the HTTP refusal. `test_voice_live_metering.py`, `live_voice_view_model_test.dart`. |
| F8 | FIXED | Refund, expiry and store EXPIRE/REVOKE downgrade the workspace first and are idempotent; a replay finishes a half-done downgrade. Each workspace in the billing cycle is isolated. `test_billing_b2_fixes.py`. |
| F9 | FIXED | Removing, leaving or demoting a member revokes the open invites that member's role can no longer send. `accept` re-checks the inviter. `test_tenancy_b2_fixes.py`. |
| F10 | FIXED | `delete_team` erases the `workspace:<id>` corpus before the row is deleted; a failed erase keeps the team. Wired in `container.py`. |
| F34 | FIXED | Downgrading 0019 is refused while data keys exist. Migration **0030** keeps payment rows (with `workspace_id` set to NULL) when a workspace is erased. Billing holds such a payment for review instead of failing the cycle. `test_migration_data_guards_postgres.py` (Postgres). |
| F36 | FIXED | Field caps and a per-user/per-IP limit on `analyze-email` / `analyze-issue`. The client clips to the same caps. `test_automation_limits.py`, `analysis_field_caps_test.dart`. |
| F38 | FIXED | Cancel is rate-limited; the billing advisory lock has a `lock_timeout` (503). |
| F40 | FIXED | At most 500 mutations per push (413). PDF extraction runs off the event loop with a 500-page cap (413). |
| F41 | FIXED | An owner transfer forgets the saved card first, so the former owner is never charged. Trade-off: if the transfer then fails, the owner must re-add the card. |
| F42 | FIXED | Upgrades and extra seats are prorated (`proration_for`). |
| F43 | FIXED | HTTP method labels are allow-listed. A malformed `session_id` or correlation id is a 422, not a 500. |
| F57 / F58 | FIXED | `store-token` checks membership before payment state. An unsigned Fawry `cardToken` is kept only when Fawry's signed card API confirms it (path still to be confirmed in Fawry staging). |
| INT-112 / INT-118 | FIXED | Route body limits match the advertised caps (JSON documents, 25 MiB transcribe; nginx 27m for transcribe). CORS exposes `Retry-After`. |
| INT-115 | PARTIAL by design | Email and issue text stays classified sensitive (local only). On a hosted-only deployment this is now an explicit `503 no_local_provider`, not a retryable failure. Live check pending. |

### Backend: auth, governance, engine (B3, B4a, B4b, B5)

| ID | Status | What changed · test |
|---|---|---|
| F37 | FIXED | Every token exchange runs the erased-account guard. `/web/token`, `/desktop/login` and `/desktop/firebase-exchange` count as deliberate sign-ins. `test_auth_exchange_guards.py`. |
| Redis fail-open | FIXED | Issuing a token fails closed: `503 auth_state_unavailable`, retryable. Per-request access-token checks stay fail-open on the erasure read, by design. An uncaught `AuthStateUnavailable` from any route is the same 503. |
| F39 | FIXED | `nonce`, `pair/start` and `desktop/exchange` are throttled per address. |
| INT-104 | FIXED | Desktop refresh puts a spent token back and answers 503 on a database or Redis failure. The host and voice sockets close with 1011 on an infrastructure error and 4401 only on revocation. The console keeps its session on 5xx. The daemon already treated only 4401 as final, and a test now pins that. |
| INT-101 | FIXED | Rollback requires a target that was approved for production. `test_model_rollback_gate.py`. |
| INT-106 / INT-107 | FIXED | Self-approval is refused while another approver exists (wired in `aura_service.py`). Role changes, session revocation and forget-user are audited. |
| F11 | FIXED | Base confidence is kept in metadata, and an access restores it. A memory used weekly survives a year of nightly consolidation. `test_memory_decay_and_deletion.py`, Postgres. |
| F12 | FIXED | Replanned steps are numbered from the highest preserved id, and references are remapped. |
| F13 | FIXED | The semantic cache tier is keyed on a digest of everything but the last user message. Invalidation rotates a per-namespace token. |
| F14 / INT-111 | FIXED | Delete and erase work while the alias and the embedder disagree. Dense hits are checked for liveness. The Postgres list shows only the caller's documents. Deleting someone else's document is a 404, and the client treats a 404 on delete as already gone. |
| F15 / F29 | FIXED | Only 5xx, timeouts and transport errors trip the breaker. 401 and 403 fail over to another provider. Every dispatched third-party attempt is audited, after its slot is released. |
| F26 / F30 / F32 / F35 | FIXED | A streamed redaction is what gets stored. A per-turn cost ceiling (`AURA_LLM_MAX_COST_PER_TURN_USD`, off by default) is checked before every generation. The sensitive-session mark is monotonic. Context guards keep the sanitised text, strip the Unicode tag block, and cover memories. |
| F27 / F28 | FIXED | Consolidation never re-inserts a forgotten memory; forget deletes the vector first. One failing owner no longer stops nightly consolidation. |
| F31 / F33 / INT-110 | FIXED | Sync push takes row locks. A lock conflict (deadlock, serialization, lock timeout) is a retryable row error. Before this, a lock conflict was reported as "invalid value" and parked for good, which the review found. The outbox lease is renewed per entry, and abandoned claims are forgotten. A message's creation time is the client's own (clamped). All on Postgres 16. |

### Deploy (B6)

| ID | Status | What changed · test |
|---|---|---|
| F19 | FIXED, **built not proven** | New `deploy/sandbox-broker` sits between the gateway and the socket proxy. It forwards only the sandbox's Docker calls and refuses privileged, capability-adding, foreign-bind, host-namespace or wrong-image creates. Both compose files have it. `test_sandbox_broker_policy.py` (79 tests). Check 11 in `verify-engineer-workspaces` needs the VPS. |
| F46 | Secrets FIXED; Ollama PARTIAL | Dev has its own `dev_` secrets. Dev reaches prod Ollama only through `ollama-dev-gate`, which allows inference and read-only calls; pull, push, create and delete get 403. Still shared: Postgres, Redis and Qdrant passwords. |
| F47 / F48 / F49 | FIXED | The Deploy VPS `sha` is format- and ancestry-checked and passed on stdin, and scripts use `mktemp -d`. The control plane deploys `workflow_run.head_sha`. Workflow installs are pinned or hash-locked, enforced by repo hygiene. |
| F18 | FIXED (fails closed), **owner action** | The release and rollback jobs need the `release` environment. The manifest is signed with Ed25519 (`.sig` beside it). The client verifies against a pinned key before trusting the SHA-256; until the real key is pinned, no update is offered. |

### Host daemon (H) and console (A)

| ID | Status | What changed · test |
|---|---|---|
| F16 | FIXED | The untrusted tier resolves symlinks, globs and recursive reads (ADR-011 updated). |
| F17 | FIXED | Redaction never hides shell operators or what runs after a secret. |
| F44 / F45 | FIXED | Abbreviated banned git flags are refused. Agent git calls run with hooks and fsmonitor off; the Git panel does too unless the folder is trusted. |
| F53 | FIXED | `http` and `ws` backends are refused unless loopback. |
| F50 / F51 / F52 | FIXED | Console security headers with a nonce CSP, set by the app (duplicates removed from nginx). Forget-user confirms by typing the subject. The runner runs as `node`. |

### Client (C1, C2, C3)

| ID | Status | What changed · test |
|---|---|---|
| F1 | FIXED | The sync engine starts at app scope after sign-in and stops at sign-out, on desktop too. `signed_in_session_test.dart`. |
| F2 / INT-108, F-04, F21 | FIXED | Retry resends the tapped turn with its agent and workspace. It is offered only on the latest failed turn and refused while streaming. The approval card stays until the resend is accepted. A stream with no `done` frame is shown as interrupted. |
| F3, F4, F5 | FIXED | The key-loss wipe drops the pull cursor. Health days are UTC calendar dates (schema 15, re-keyed once). A 403 "not a member" heals the active workspace on the SSE and knowledge paths. |
| F20, F22 / INT-109, F24, F25 | FIXED | Approve is guarded against a double tap. Offline memory saves are queued and flushed by sync, and the user is told what happened. Google Tasks changes enter the outbox. A failed capabilities fetch is not cached. |
| F23 | NOT REPRODUCED, hardened | Leave and delete navigate before refreshing. |
| F54 / INT-117, F55, F56 | FIXED | PATCH and PUT have the deadline. Plan and status names are localized. A pull that spans a sign-out is dropped. |
| INT-102 | FIXED, **device check pending** | Live voice plays replies through `flutter_soloud`; if the engine cannot start, the page shows text-only mode. |
| INT-103, INT-105 | FIXED | macOS signs in at `/consumer/token`. The refresh retry key is persisted across restarts. |
| INT-113, INT-116 | FIXED | Sharing sends the most recent messages that fit and says what was left out. The mic sends exact 20 ms frames. |
| Desktop smoke tests | ROOT-CAUSED | 24 `pumpAndSettle` timeouts. The test read the machine's real `aura_hostd/control.json`, not anything Windows-specific. The provider is now overridden in the test. |

### Not done

- **INT-119…INT-130.** These Low items exist only in the audit's report file, which is not in the
  repository. Not routed (CB-49).
- **Not reproduced on this checkout:** the integration audit says `CLAUDE.md` claims the desktop
  pairing routes have no client caller. This checkout's `CLAUDE.md` makes no such claim, and the
  regenerated `api-reachability.md` lists callers for all three routes.

## Residual risks found in review, accepted or deferred

- **Manifest replay.** A genuine old signed manifest, including a `rollback: true` one, stays
  valid forever. Closing this needs an expiry or a version floor in the signed manifest (CB-45).
- **Sandbox broker race.** A compromised gateway could swap a workspace directory for a symlink
  between the broker's start check and the daemon's mount.
- **F45 and F16 gaps.**
  - F45: `.git/config` filters, merge drivers and `gpg.program` can still run on checkout,
    merge or commit.
  - F16: a symlink created between the check and the run.
  - F16: `wc --files0-from` and `file -f`.
- **Performance.**
  - Retrieval does one more document-store read per turn.
  - The semantic cache does one more Redis read.
  - The trusted-proxy IP logic now exists in three copies (refactor candidate).

## Owner actions

Backlog CB-45 to CB-49.
