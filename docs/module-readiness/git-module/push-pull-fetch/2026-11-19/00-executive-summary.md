# Git module — Push / Pull / Fetch: executive summary

## Update after implementation (2026-11-19, later the same day)
**Verdict: READY FOR REAL-MACHINE VALIDATION. Not production-ready, not proven.** The P1 and all P2 findings
below are implemented and pass locally on Windows; the work is **uncommitted** and **not CI-run**. Sign-off
still needs the real-machine run in `07-windows-real-machine-checklist.md` (GAP-026).

Evidence (local, Windows, git 2.53, Dart 3.12.2):
- `aura_hostd`: `dart analyze --fatal-infos` clean. `dart test`: 535 passed, 1 failed. The failure is
  `error_codes_test` (F13), which matches `\n` against a CRLF checkout and checks code this work did not touch.
  New: 18 `git_service` tests (30 before, 48 now), 6 route tests.
- `aura_client`: `flutter analyze` clean; `flutter test` 1319 passed, 5 skipped. New: 15 notifier tests,
  5 panel tests (real widget).
- Not run: anything against a real remote, Git Credential Manager, SSH, a localized git, the real 280 px
  sidebar, a real 300 s wait, CI.

What changed: fetch uses `--all` (F1); remote operations get 300 s, English messages and masked URL
credentials (F4, F5, F8); a refused sign-in is `auth_failed` (F7); "Publish branch" (D-01, new routes
`GET /git/remotes`, `POST /git/publish`); Push wording follows git config (D-03). Two extra defects found
and fixed on the way: panel messages were wiped by the refresh after every operation (F11), and a
`ListTile` under a `ColoredBox` (F12). **Open:** F10 (button row overflow at narrow width), F13 (CRLF test).
Deviation from R-01: `LC_ALL=C` is used for remote operations (with `core.quotepath=false`), not
`LC_MESSAGES`/`LANGUAGE`, because an `LC_ALL` already set on the machine would override those.

*The original discovery summary follows unchanged.*

**Date:** 2026-11-19 · **Mode:** DISCOVER (read-only; no application code changed) · **Branch:** `main`, clean at start

## Scope
The commands a person runs from the Desktop IDE Git panel: **Fetch, Pull, Push** (and, by decision
D-01, a new **Publish branch**). Path: `git_panel.dart` → `GitNotifier` → `HostdControlClient` →
loopback `aura_hostd` `/git/{fetch,pull,push}` → `GitService` → real `git`.
Also reviewed for context: the Engineer agent's server-side `git_push` tool (`coding.py`,
`host_git.py`) and the agent's `git push` through hostd `run_command` (`command_policy.dart`).
Not covered: live behaviour, mobile/web (no Git UI), the web console.

## Readiness: **NO-SHIP** (provisional, from code and local git experiments only)
- **P1:** Fetch fails on any repository with two or more remotes (F1). Reproduced with git 2.53.
- Four P2 gaps (timeout, locale-dependent result detection, no publish path for a new branch, test gaps),
  one P3, and one accepted risk (AR-01).
- Nothing is proven on a real Windows desktop against a real remote (GAP-026 is `built_not_proven`).
  Hostd Windows git tests have not run in CI since `dc87beb`.
- Not run in this session: any Dart or Flutter test or analyzer. Git behaviour was tested only in a
  throwaway local repository. Dart and Flutter exist at `E:/Flutter/flutter/bin` for the verify phase.

## Decisions taken (see 02)
D-01 add "Publish branch" · D-02 300 s limit for fetch/pull/push · D-03 Push follows the user's git config ·
D-04 use existing git credentials, add a distinct `auth_failed` result.

## What is good
Agent `git_push` is confirmation-gated, refuses the default branch and any refspec/option spelling, keeps
the token out of argv and `.git/config`, and is tested with real git. Pull is fast-forward-only. Hooks are
off in untrusted folders. A hung network git is killed with its child process tree on Windows.

## Reports in this folder
01 as-is map · 02 decisions · 03 requirements · 04 gaps and risks · 05 plan · 06 traceability.
