# 06 — Requirement traceability

## Status after implementation (local evidence, Windows; uncommitted, not CI-run)
| Req | Status | Evidence | Still unverified |
|---|---|---|---|
| FR-001 | **PASS (local)** | `git_service_test.dart` "fetch updates every remote when a repository has two"; route test | real remotes |
| FR-002 | **PASS (local)** | tests for diverged, overwrite (names the file), ff success, no_upstream | localized git proven only trivially (no German catalogs here) |
| FR-003 | **PASS (local)** | push success, diverged (remote untouched), no_upstream; wording test | `push.default=matching` real-machine row 16 |
| FR-004 | **PASS (local)** | 9 service tests, 3 route tests, 5 widget tests (cancel sends nothing, choice, no remote) | real remote, row 7-11 |
| FR-005 | **PASS (local)** | classifier table; real loopback HTTP 401 for fetch, pull, push and publish with no credential helper; notifier and l10n tests | **Git Credential Manager GUI, SSH** (rows 12-13) |
| NFR-001 | **PASS (local)** | default 300 s; kill with a 300 ms and a 1 s override; message "timed out after 1s" | a real 300 s wait (row 17) |
| NFR-002 | **PARTIAL** | English pinned (`LC_ALL=C`) and tested | needs a machine with localized git (row 15) |
| UX-001 | **PASS** | l10n tests (no "current branch" wording); comments corrected | — |
| SEC-001 | **PASS (local)** | URL user-info scrub tests; boundary and agent gating tests unchanged and green | row 14 |
| NFR-003 | **PASS (local)** | notifier tests: success, structured failure kept on screen, thrown error, double click | — |
Open outside these requirements: F10, F13 (see `04`).

## Original traceability (at discovery, before implementation)

Statuses: PASS, GAP, PARTIAL, UNVERIFIABLE. Nothing was executed against Dart/Flutter test suites in this
session; PASS here means code read plus, where stated, a local git experiment.

| Req | Expected | Current implementation evidence | Status | Gap | Verification today |
|---|---|---|---|---|---|
| FR-001 | Fetch all remotes, prune, tree untouched | `git_service.dart:396-414` builds `fetch --prune <remotes...>` | **GAP** | F1 (≥2 remotes fails) | Experiment: exit 128; one-remote test `git_service_test.dart:437-449` |
| FR-002 | Pull ff-only + 3 structured outcomes | `git_service.dart:420-440` | **PARTIAL** | F5 locale; `diverged`/overwrite untested | no_upstream tested (`:408`); success tested (`:422`) |
| FR-003 | Push follows git config; no force; structured outcomes | `git_service.dart:447-462` | **PARTIAL** | F6 `diverged` untested; wording F2 | Success and no_upstream tested (`:415`, `:452`) |
| FR-004 | Publish branch | none | **GAP** | F3 | none |
| FR-005 | `auth_failed` result, no prompt/store | none; generic `git_command_failed` | **GAP** | F7 | none |
| NFR-001 | 300 s limit, kill tree, message | 30 s default; kill tree implemented (`:725-736`) | **PARTIAL** | F4 | Kill tested with 300 ms override (`:472-499`) |
| NFR-002 | Locale independent | English substring matching | **GAP** | F5 | none (inferred) |
| UX-001 | Wording matches behaviour | tooltip and comments say "current branch only" | **GAP** | F2 | experiment confirms mismatch |
| SEC-001 | Human-only boundary; agent push gated; no stored creds; scrub | boundary and push gating present; scrub absent | **PARTIAL** | F8 | `command_policy_test.dart`; server push tests (`test_workspace_boundaries.py`) |
| NFR-003 | Busy guard, refresh, errors | `git_notifier.dart:206-223` | **UNVERIFIABLE** | F6 no tests | code read only |

## Tests to add (by requirement)
FR-001 two remotes · FR-002/003 diverged + overwrite + rejected push · FR-004 publish (success, diverged,
auth, no remote) · FR-005 classifier table · NFR-001 default value + message · NFR-002 locale ·
SEC-001 scrub · NFR-003 notifier · route tests for `/git/fetch|pull|push|publish|remotes`.
