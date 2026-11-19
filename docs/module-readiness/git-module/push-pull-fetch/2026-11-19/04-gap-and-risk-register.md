# 04 — Gap and risk register

Evidence from code reading and local git 2.53 experiments (2026-11-19). Nothing here was observed live.

| ID | Sev | Requirement | Finding | Evidence | Fix direction | Verify by |
|---|---|---|---|---|---|---|
| F1 | **P1** | FR-001 | Fetch fails for any repo with ≥2 remotes: `fetch --prune origin upstream` treats the second name as a refspec. | `git_service.dart:408`; experiment: `fatal: couldn't find remote ref upstream`, exit 128; `--all --prune` exit 0; `host_git.py:217` already uses `--all`; only test has one remote (`git_service_test.dart:439`). | Use `git fetch --all --prune`; keep the no-remote no-op. | Two-remote test |
| F3 | P2 | FR-004 | No way to publish a new branch from the panel; Push returns `no_upstream`. | `git_service.dart:451-455`; panel creates branches (`git_panel.dart:193`); git message confirmed in experiment. | Per D-01: Publish branch (route + UI). | Publish tests |
| F4 | P2 | NFR-001 | 30 s kill; client's 10 min wait is unreachable. | `git_service.dart:115`; `control_server.dart:70` (no override); `hostd_control_client_io.dart:84,257-264`. | 300 s per D-02. | Timeout test |
| F5 | P2 | NFR-002, FR-002 | Result detection matches English stderr; the daemon pins no language (`host_git.py:112` does). | `git_service.dart:424-436,451-457,744`. Inferred; not run on a localized git. | REC-01 | Locale test |
| F6 | P2 | NFR-003 | Test gaps: no assertion of `diverged` (pull/push) or pull-overwrite; no `/git/*` route tests; no `GitNotifier`/panel tests; hostd Windows git suite not CI-run since `dc87beb`. | grep of `aura_hostd/test`, `aura_client/test`; status doc | Add tests; run on Windows CI. | Suites green |
| F2 | P2→wording | FR-003, UX-001 | Push comments/tooltip promise "current branch only", but bare push follows git config. | `git_service.dart:442-446`; `control_server.dart:749-752`; `app_en.arb:3882`; experiment: `push.default=matching` pushed `other` too. | D-03: fix wording; risk AR-01 accepted. | String/comment review |
| F7 | P3→P2 | FR-005 | No `auth_failed` result; GCM GUI and ssh prompts are not stopped by `GIT_TERMINAL_PROMPT=0`; the hang is bounded only by the timeout. | `git_service.dart:744`. Unverified on real Windows. | D-04 + real-machine check (consider `GCM_INTERACTIVE=never`, `GIT_SSH_COMMAND` with `BatchMode=yes` — to evaluate). | Windows run |
| F8 | P3 | SEC-001 | Raw git stderr is returned to the client; a remote URL with embedded credentials could appear. | `git_service.dart:437,459`; REC-02 | Scrub URL user-info. | Scrub test |
| F9 | P3 | — | Status doc row for hostd git on Windows is stale. | `DESKTOP_APP_STATUS.md:77` | Refresh after verification. | Doc review |

## Found during implementation (2026-11-19)
| ID | Sev | Finding | Evidence | Status |
|---|---|---|---|---|
| F10 | P3 | The panel's sync-button row (three icon buttons + Merge + Branch) overflows at narrow widths. At the real default sidebar width of 280 px (`desktop_application_state.dart:427`) it overflows by far more than the 7 px seen at 396 px, so the Merge or Branch buttons may be cut off. Existing, not caused by this work. | Widget test at 420 px: "RenderFlex overflowed by 7.1 pixels on the right" | **OPEN**: not fixed (outside push/pull/fetch); checklist row 18 records the real-machine view |
| F11 | **P2** | **Every structured failure message the Git panel sets was wiped before it could be shown.** `_write` ran the operation, then `refresh()`, which replaces the whole state and clears `error`. So "no upstream", "diverged", "would be overwritten", "nothing staged" and the new `auth_failed` / publish messages never reached the person. | `git_notifier.dart` `_write`; the regression test failed without the fix (`Actual: <null>`) | **FIXED** (`_write` keeps the operation's error); test `each structured failure … stays on screen` |
| F12 | P3 | `GitPanel` wrapped a `ListTile` (History) in a coloured `Container`, which hides its ink and trips a Flutter debug assertion. | Widget test output | **FIXED**: `Material(color: …)` |
| F13 | info | `aura_hostd/test/error_codes_test.dart` ("control_server.dart writes every error body through _fail") fails on a Windows checkout with `core.autocrlf=true`, because it matches `\n` literals against CRLF text. It checks the pairing code, which this work did not touch. | `tr -cd '\r'` shows CRLF in the working tree and LF in the HEAD blobs; the failing assertion is at `error_codes_test.dart:166` | **OPEN**: pre-existing, not fixed here. Linux CI reads LF. |

## Status of the earlier findings after implementation (local evidence only)
F1 fixed · F2 wording fixed (D-03) · F3 fixed (Publish branch) · F4 fixed (300 s) · F5 fixed (English pinned
for remote operations, `LC_ALL=C`) · F6 tests added · F7 `auth_failed` added, **real-machine behaviour of
Git Credential Manager still unverified** · F8 URL user-info scrubbed · F9 status-doc row updated, see below.

## Accepted risk
AR-01 (D-03): Push may publish more than the checked-out branch under non-default `push.default` or a
configured `remote.*.push`. Owner: user.

## Residual (known, not new)
A trusted folder is a full shell (D-06). `invokesGitPush` catches every plain spelling but not a push
built through variable indirection (command_policy.dart:755-761). Out of scope here.

## Not investigated
Live remotes (GitHub), Git Credential Manager behaviour, SSH remotes, submodules, LFS, very large repositories,
macOS/Linux desktop, phone/web.
