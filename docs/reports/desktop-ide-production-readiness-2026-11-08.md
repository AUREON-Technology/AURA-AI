# Desktop IDE — production-readiness assessment (2026-11-08)

**Scope:** the Desktop IDE (`aura_client/lib/desktop/`, the `/ide` routes), the daemon it depends on
(`aura_hostd/`), and the backend half of the Engineer agent's host connection
(`aura_core/adapters/host_agent.py`). Assessed at `772f9ef`, re-checked after merging `main` at
`6bc04b6` (Phase 0 fixes FSA-001…031, including FSA-004 and FSA-006, which touch the desktop). The
whole-system view at `772f9ef` is [`AURA_PRODUCTION_READINESS_2026-11-07.md`](AURA_PRODUCTION_READINESS_2026-11-07.md);
this report goes deeper on the desktop only.

**Verdict: not production-ready.** The IDE is fit for internal dogfooding on Windows by someone who
builds it from source and points it at a hand-built `aura_hostd`. It is not fit for any user who
expects to install it. M8 (Desktop IDE Productization) has not passed any of its sub-milestone exit
criteria: M8.0, the re-baseline, still needs green Windows CI, and no desktop suite has run in CI
since `dc87beb`.

Evidence labels follow [`docs/status/README.md`](../status/README.md): PROVEN / TESTED / BUILT /
OPEN. "Local" means run in this session on Linux (Flutter 3.44.6 / Dart 3.12.2, the CI pins), not in
CI.

## What was checked, and what came back

| Check | Result |
| --- | --- |
| CI state | **Runners are back; the desktop suites have not run.** Repo Hygiene and Admin CI executed on 2026-11-06/07, so CB-44's runner block has lifted. Client CI and Host Daemon CI have had no trigger since 2026-10-31 (path filters), and their latest runs (#170–#174, #62–#66) still show the no-runner signature. Nothing merged since `dc87beb` has been CI-run on desktop, including all of PR #185's Windows and hostd fixes (which *are* on `main`: `bbc028a`, `130b58f`, `4b1968e`, `e512b4e`). |
| `aura_client`: `flutter analyze` | Local: no issues. |
| `aura_client`: `flutter test` | Local: **538 passed, 3 skipped, 0 failed** before this change; **544 passed, 4 skipped, 0 failed** with the new shell smoke test (6 cases plus 1 skipped known-defect test). After merging `main` at `6bc04b6`: **549 passed, 4 skipped, 0 failed**; analyze still clean. |
| `aura_hostd`: `dart analyze --fatal-infos` | Local: no issues. |
| `aura_hostd`: `dart test` (Linux) | Local: **232 passed** (231 + the debugpy case once `debugpy` is installed, as CI does); **258 passed** after merging `main` at `6bc04b6` (FSA-006 adds command-policy cases). The run **exits in 16–20 s**: the `PosixPtySession` isolate hang that held Linux CI to its 15-minute timeout no longer reproduces at `4b1968e`. |
| `aura_hostd` on Windows | Not runnable here. The last CI run that executed (on `dc87beb`) had 5 failures; the fixes have never run on Windows. |
| Does the IDE shell render? (new) | Local: **yes.** `test/desktop/desktop_shell_smoke_test.dart` pumps the real `DesktopShell` with the platform forced to Windows at 1920×1080, 1280×800, 1024×700 and 800×600 (wide, wide edge, standard, compact), LTR and RTL. It clicks all five activity-bar destinations back to back with hit-test misses made fatal. No exceptions, no overflow. No earlier test pumped the shell; individual parts only. |
| Defect found by that check (**fixed**) | Below the wide breakpoint the AI panel floats, and its scrim (`desktop_shell.dart`, `_ShellBody`) was `Positioned.fill`, covering the activity bar too. The next activity-bar click only closed the panel, so reaching e.g. Control Center took two clicks. Severity: low. Fixed in this PR. The scrim now starts after the activity bar. The activity bar also closes a floating AI panel before opening a primary panel, which is otherwise drawn underneath it, and closes whatever floats before showing the bottom panel. So moving the scrim alone was not enough. 14 new cases fail on the old code and pass on the fix; the shell test file has 20 cases in all. Full client suite after the fix: 563 passed, 3 skipped. |
| Client test coverage of IDE modules | 7 of 14 modules under `lib/desktop/` have **no** test that imports them: `agent` (255 lines), `control_center` (541), `debug` (658), `git` (684), `host` (403), `output` (215), `terminal` (186): about 2,900 lines, including the Agent Review merge/discard panel. Their hostd counterparts are tested; the client halves are not. |

## Blockers (must be fixed before any external user)

1. **No CI run on the desktop code since `dc87beb`.** Runners are assigned again, but Client and
   Host Daemon CI have not been triggered since. Desktop code has not been verified on Windows by
   CI since 2026-09-11, and hostd's Windows job has never been green. No claim about the Windows
   build, the only platform the IDE runs on, can be stronger than BUILT until those jobs run.
   *Exit:* Client CI `build-windows` and Host Daemon CI `analyze-and-test-windows` green on
   `main`, then made gating (M8.0).

2. **The Engineer agent reaches the daemon only by luck on a multi-worker backend** (code-read).
   `HostAgentRegistry` (`aura_core/adapters/host_agent.py:156`) is an in-process dict. The backend
   defaults to `--workers ${AURA_WORKERS:-4}` (`aura_backend/Dockerfile:153`, `deploy/docker-compose.prod.yml:403`). The daemon's WebSocket lives on one worker, and a chat
   request lands on any of them. So with four workers, about three out of four Engineer turns
   would see "no desktop connected" while the IDE shows the daemon as connected. The status file
   lists this under F-09 as a security remainder. It is first a functional blocker for the IDE's
   AI half. Railway's `AURA_WORKERS` is a dashboard setting and was not checked.
   *Exit:* a shared registry/routing layer (e.g. Redis pub/sub keyed by user), with a test that
   runs two workers.
   **Update (2026-11-08): built, not proven live.** The host-agent relay
   (`aura_core/adapters/host_agent_relay.py`, ADR-011 amendment) lets any worker reach the
   session over Redis pub/sub, with signed messages. Two-worker tests pass on the in-memory bus
   and against a real Redis 7.0. It is on whenever `AURA_REDIS_URL` is set. Whether Railway has
   Redis configured has not been checked, and without it this blocker still holds there.

3. **Not installable.** No installer, no code signing, no updater, and `aura_hostd` is not bundled.
   The user has to type the path to a daemon binary they built themselves
   (`core/hostd/hostd_supervisor_io.dart:25-28`). (CB-26, M8.10.)

4. **No crash recovery.** `HostdSupervisor` is 60 lines that start the daemon and log its exit
   code. Nothing restarts it, and nothing recovers unsaved buffers (CB-26, M8.8).

5. **Never proven end to end (GAP-026, M8.13).** No recorded session on a real machine of
   sign in → pair daemon → open repo → agent edit → Agent Review → merge → push. Desktop login has
   one recorded live attempt, and it failed (`client_secret is missing`). The fix (`34f0fc4`) is
   unproven.


Fixed on `main` since this assessment began, but not proven: **the 30-day desktop session token**
(the F-09 remainder). FSA-004 (`ba17ad4`) has desktop sign-in return a 15-minute access token plus
a refresh token bound to the device. Revoking the device drops its refresh tokens, and Settings ›
Desktop devices lists and revokes devices. The 30-day form survives only for the daemon's
`host_agent`-scoped token. It is TESTED in its authoring session (11 backend, 5 client cases), not
in CI, and has not been seen live.

## Should fix before a beta

- Tests for the seven untested IDE modules, starting with `control_center` (merge/discard acts on
  the user's real repository) and `git` (push).
- macOS: the client has no network entitlement. `Release.entitlements` has the sandbox and audio
  input only, and `DebugProfile.entitlements` has `network.server` but not `network.client`
  (code-read). A sandboxed macOS build cannot reach the backend. Either add it or declare macOS
  unsupported. The roadmap already defers macOS (M8.12).
- `/ide` renders `SizedBox.shrink()` on Linux and macOS (`core/routing/router.dart:420`). The IDE is
  Windows-only by design (ADR-015), but a Linux user who deep-links there sees a blank window.
  Redirect instead.
- Stale `aura_hostd/README.md` (still says there is no PTY and no pairing).

## What is in good shape

- **Static health:** client and daemon both analyze clean at the CI's strictness (`--fatal-infos`
  for hostd; info-level lints fail the client gate).
- **hostd on Linux:** git (including real fetch/pull/push against a second repo and a timeout that
  kills a dead remote), per-task worktrees, approval queue, consent tiers, the DAP debug tunnel,
  and containment are all exercised by 232 passing tests, and the suite now exits cleanly.
- **Human writes stay in the workspace:** `WorkspaceFileService.rename/delete` resolve symlinks
  through `requireContainedClient` before acting. A link that points out of the root is refused
  rather than followed. hostd stays the authoritative boundary, per ADR-015 §3.
- **Agent Review** (D9): discard asks for confirmation, merge disables its own buttons while in
  flight, and a conflict is reported rather than auto-resolved (code-read).
- **The shell renders** at all three breakpoints (four window sizes) in both text directions without layout errors
  (new test). This is the first executed evidence for F-36's RTL work on the IDE shell.

## M8 sub-milestones against their exit criteria

| | Exit criterion (roadmap) | State |
| --- | --- | --- |
| M8.0 | Client + hostd CI green on Linux and Windows; `build-windows` gating | OPEN: CI not running |
| M8.1 | Golden tests; layout survives restart (Windows runner) | BUILT; layout persistence unit-tested; no goldens |
| M8.2 | Integration tests; 10k-file repo opens under target | BUILT; no integration test, no measurement |
| M8.3 | PTY tests on Windows + Linux; no orphans after kill | Linux PTY TESTED locally; Windows unverified |
| M8.4 | Real-git tests incl. conflict path | hostd half TESTED locally; client panel untested |
| M8.5a | EV-11 subset; approval-token E2E | OPEN |
| M8.5b | Gated on M4 TTFT p95 < 500 ms | OPEN (gate not met) |
| M8.6 | Test explorer; DAP for ≥ 2 languages | Python DAP only |
| M8.7 | Security UX review; SEC-02 regressions | BUILT; no review recorded |
| M8.8 | Kill-daemon / kill-app recovery tests | OPEN |
| M8.9 | Measured cold start, 100k-file repo, typing latency, screen reader | OPEN; nothing measured |
| M8.10 | Signed installer; update + rollback | OPEN |
| M8.11 | Opt-in content-free telemetry | OPEN |
| M8.12 | Windows + Linux certification report | OPEN |
| M8.13 | Recorded golden-path run | OPEN |

## Suggested order

1. Run Client CI and Host Daemon CI on `main` (runners are back; the jobs just need a trigger),
   then make the Windows jobs gating. Everything else is unverifiable on Windows until they run.
2. Shared host-agent registry (blocker 2): built on 2026-11-08 (see blocker 2). What remains is
   confirming `AURA_REDIS_URL` on Railway and observing a paired daemon there.
3. M8.13 golden path on one real Windows machine. This is the smallest piece of PROVEN evidence
   and will surface whatever the unit suites cannot.
4. Bundle and sign hostd, then add a restart-on-crash supervisor (M8.8, M8.10).
5. Fill the client test gaps, and fix the macOS items.

## Not checked

Anything on a real Windows or macOS machine; desktop sign-in against the live backend; Railway's
`AURA_WORKERS` value; rendering fidelity, fonts, screen-reader output and any performance figure
(the test host is headless).
