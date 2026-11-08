# Desktop IDE — production-readiness assessment (2026-11-08)

**Scope:** the Desktop IDE (`aura_client/lib/desktop/`, the `/ide` routes), the daemon it depends on
(`aura_hostd/`), and the backend half of the Engineer agent's host connection
(`aura_core/adapters/host_agent.py`). `main` at `772f9ef`.

**Verdict: not production-ready.** The IDE is fit for internal dogfooding on Windows by someone who
builds it from source and points it at a hand-built `aura_hostd`. It is not fit for any user who
expects to install it. M8 (Desktop IDE Productization) has not passed any of its sub-milestone exit
criteria: M8.0, the re-baseline, still needs green Windows CI, and CI has not run since 2026-10-28.

Evidence labels follow [`docs/status/README.md`](../status/README.md): PROVEN / TESTED / BUILT /
OPEN. "Local" means run in this session on Linux (Flutter 3.44.6 / Dart 3.12.2, the CI pins), not in
CI.

## What was checked, and what came back

| Check | Result |
| --- | --- |
| CI state | **Not executing.** Latest Client CI (#170–#174) and Host Daemon CI (#62–#66) jobs all end in 2–4 s with no log (CB-44). Nothing merged since `dc87beb` has been CI-run, including all of PR #185's Windows and hostd fixes (which *are* on `main`: `bbc028a`, `130b58f`, `4b1968e`, `e512b4e`). |
| `aura_client`: `flutter analyze` | Local: no issues. |
| `aura_client`: `flutter test` | Local: **538 passed, 3 skipped, 0 failed** before this change; **544 passed, 4 skipped, 0 failed** with the new shell smoke test (6 cases plus 1 skipped known-defect test). |
| `aura_hostd`: `dart analyze --fatal-infos` | Local: no issues. |
| `aura_hostd`: `dart test` (Linux) | Local: **232 passed** (231 + the debugpy case once `debugpy` is installed, as CI does). The run **exits in 20 s**: the `PosixPtySession` isolate hang that held Linux CI to its 15-minute timeout no longer reproduces at `4b1968e`. |
| `aura_hostd` on Windows | Not runnable here. The last CI run that executed (on `dc87beb`) had 5 failures; the fixes have never run on Windows. |
| Does the IDE shell render? (new) | Local: **yes.** `test/desktop/desktop_shell_smoke_test.dart` pumps the real `DesktopShell` with the platform forced to Windows at 1920×1080, 1280×800 and 1024×700, LTR and RTL. It opens all five activity-bar destinations with hit-test misses made fatal. 6 passed, no exceptions, no overflow. No earlier test pumped the shell; individual parts only. |
| Defect found by that check | At 1024 px the AI panel floats, and its scrim (`desktop_shell.dart`, `_ShellBody`, `Positioned.fill`) covers the activity bar too. The next activity-bar click only closes the panel, so reaching e.g. Control Center takes two clicks. The scrim's own comment expects the activity bar to stay usable. Recorded as a skipped test in the same file, confirmed to fail when un-skipped. Severity: low. |
| Client test coverage of IDE modules | 7 of 14 modules under `lib/desktop/` have **no** test that imports them: `agent` (255 lines), `control_center` (541), `debug` (658), `git` (684), `host` (403), `output` (215), `terminal` (186): about 2,900 lines, including the Agent Review merge/discard panel. Their hostd counterparts are tested; the client halves are not. |

## Blockers (must be fixed before any external user)

1. **CI does not run (CB-44).** Desktop code has not been verified on Windows by CI since
   2026-09-11, and hostd's Windows job has never been green. No claim about the Windows build,
   the only platform the IDE runs on, can be stronger than BUILT until runners return.
   *Exit:* Client CI `build-windows` and Host Daemon CI `analyze-and-test-windows` green on
   `main`, then made gating (M8.0).

2. **The Engineer agent reaches the daemon only by luck on a multi-worker backend** (code-read).
   `HostAgentRegistry` (`aura_core/adapters/host_agent.py:156`) is an in-process dict. The backend
   defaults to `--workers ${AURA_WORKERS:-4}` (`aura_backend/Dockerfile:147`,
   `deploy/docker-compose.prod.yml:403`). The daemon's WebSocket lives on one worker, and a chat
   request lands on any of them. So with four workers, about three out of four Engineer turns
   would see "no desktop connected" while the IDE shows the daemon as connected. The status file
   lists this under F-09 as a security remainder. It is first a functional blocker for the IDE's
   AI half. Railway's `AURA_WORKERS` is a dashboard setting and was not checked.
   *Exit:* a shared registry/routing layer (e.g. Redis pub/sub keyed by user), with a test that
   runs two workers.

3. **Not installable.** No installer, no code signing, no updater, and `aura_hostd` is not bundled.
   The user has to type the path to a daemon binary they built themselves
   (`core/hostd/hostd_supervisor_io.dart:25-28`). (CB-26, M8.10.)

4. **No crash recovery.** `HostdSupervisor` is 60 lines that start the daemon and log its exit
   code. Nothing restarts it, and nothing recovers unsaved buffers (CB-26, M8.8).

5. **Never proven end to end (GAP-026, M8.13).** No recorded session on a real machine of
   sign in → pair daemon → open repo → agent edit → Agent Review → merge → push. Desktop login has
   one recorded live attempt, and it failed (`client_secret is missing`). The fix (`34f0fc4`) is
   unproven.

6. **The desktop sign-in token is still a full 30-day session** (F-09 remainder, per
   `DESKTOP_APP_STATUS.md`; not re-checked here). A desktop app with local file and terminal
   access is the highest-value token holder in the system.

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
- The scrim defect above. A one-line fix (`PositionedDirectional(start: activityBarWidth, …)`)
  plus un-skipping the test.
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
- **The shell renders** at three window sizes in both text directions without layout errors
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

1. Restore CI (CB-44). Everything else is unverifiable on Windows until it runs.
2. Shared host-agent registry (blocker 2). It is the only blocker that breaks the product even
   for a dogfooding user on a correctly built machine.
3. M8.13 golden path on one real Windows machine. This is the smallest piece of PROVEN evidence
   and will surface whatever the unit suites cannot.
4. Bundle and sign hostd, then add a restart-on-crash supervisor (M8.8, M8.10).
5. Fill the client test gaps, and fix the scrim and macOS items.

## Not checked

Anything on a real Windows or macOS machine; desktop sign-in against the live backend; Railway's
`AURA_WORKERS` value; rendering fidelity, fonts, screen-reader output and any performance figure
(the test host is headless).
