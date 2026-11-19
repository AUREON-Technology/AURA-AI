# 05 — Implementation plan (estimated; to be verified during implementation)

Status: **approved ("Implement plan") and implemented locally 2026-11-19; uncommitted, not CI-run.**
WP-1 to WP-6 done (see `00` update and `04` status). **WP-7 is not done**: the checklist exists
(`07-windows-real-machine-checklist.md`) but a person has to run it on a real Windows desktop with a real
remote. The `DESKTOP_APP_STATUS.md` rows were refreshed with the local evidence only.
Original plan follows.
Sequence: daemon first, then client. A client that calls a missing route already shows "daemon out of date"
(`hostd_control_client_io.dart:151`), and the daemon is bundled with the app.

## 1. Release blockers
| WP | Scope | Layers / files (estimated) | Covers | Acceptance and tests |
|---|---|---|---|---|
| WP-1 | Fetch all remotes | `aura_hostd/lib/src/git_service.dart` (`fetch`) | F1, FR-001 | Test: bare `origin` + `upstream`, new commit on each, one fetch updates both; 0 and 1 remote still pass |
| WP-2 | 300 s remote limit | `git_service.dart` default for fetch/pull/push; `ControlServer` wiring; message names limit | F4, NFR-001, D-02 | Existing short-timeout kill test kept; add test of default value and message; client timeout ≥ daemon |
| WP-3 | `auth_failed` result + locale pin + stderr scrub | `git_service.dart` (classifier, env), `error_codes.dart` + client `hostd_error_codes.dart` (+ their parity tests), `app_en.arb`/`app_ar.arb`, `git_notifier.dart` `_applySyncFailure` | F5, F7, F8, FR-005, NFR-002, SEC-001, D-04, R-01, R-02 | Table-driven classifier tests; locale test; l10n tests (`engineer_arabic_test.dart` style); scrub test |

## 2. Required before general availability
| WP | Scope | Files (estimated) | Covers | Acceptance and tests |
|---|---|---|---|---|
| WP-4 | Publish branch | daemon: `GitService.remotes()` + `publish()` + routes (`GET /git/remotes`, `POST /git/publish`); client: `hostd_control_client{,_io,_stub}.dart`, `git_notifier.dart`, `git_panel.dart` (action + remote picker + confirmation), l10n en/ar | F3, FR-004, D-01 | Service tests (no upstream → set upstream, remote has branch, diverged, auth failure); route tests; notifier tests; widget test of the dialog |
| WP-5 | Wording | `git_service.dart` and `control_server.dart` doc comments; `gitPushTooltip` (en, ar) | F2, UX-001, D-03 | String review; l10n test |
| WP-6 | Test coverage | `aura_hostd/test` (pull diverged, pull overwrite, push rejected, route tests); `aura_client/test/desktop/git_notifier_test.dart` | F6, NFR-003 | Suites green; counts quoted |
| WP-7 | Real-machine evidence | Windows desktop, real GitHub remote and a second remote; checklist run; report committed under `docs/reports/`; refresh `DESKTOP_APP_STATUS.md:77` | F7, F9, GAP-026 | Report names machine, git version, credential setup, each scenario and its result |

## 3. After launch / optional
Wire server-side `HostGitService.fetch` or drop it. Add a `git_pull`/`git_fetch` agent tool only if wanted.

## Risks and compatibility
- New error code and routes must exist on both sides of the hostd/client contract; keep the parity tests green.
- Publish never forces. Pull stays ff-only.
- Do not run any push in tests against a real remote; use local bare repositories.

## Validation commands (to run when implementing)
```powershell
cd "E:\AURA AI\aura_hostd"; dart pub get; dart analyze --fatal-infos; dart test
cd "E:\AURA AI\aura_client"; flutter pub get; dart run build_runner build --delete-conflicting-outputs; flutter analyze; flutter test
```
