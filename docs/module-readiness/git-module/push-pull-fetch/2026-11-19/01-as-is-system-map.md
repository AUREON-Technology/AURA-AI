# 01 — As-is system map

## Layers
| Layer | Path | Stack | Commands |
|---|---|---|---|
| Client panel | `aura_client/lib/desktop/git/git_panel.dart:166-183` | Flutter/Riverpod | `dart run build_runner build`, `flutter analyze`, `flutter test` |
| Client state | `aura_client/lib/desktop/state/git_notifier.dart:177-223` | Dart `StateNotifier` | same |
| Client transport | `aura_client/lib/core/hostd/hostd_control_client_io.dart:99-165,257-264` | `package:http`, loopback + bearer token | same |
| Daemon routes | `aura_hostd/lib/src/control_server.dart:235-240,773-804` | pure Dart | `dart pub get`, `dart analyze --fatal-infos`, `dart test` |
| Git execution | `aura_hostd/lib/src/git_service.dart:396-462,665-737` | `Process.start('git')` | same |
| Command policy (agent shell) | `aura_hostd/lib/src/command_policy.dart:530,558,762` | Dart | same |
| Server-side agent tools | `aura_backend/aura_core/tools/coding.py:780-840`, `adapters/host_git.py` | Python 3.11 / FastAPI | `pytest` |

## Human flow (Fetch / Pull / Push)
click → `GitNotifier._write` (busy guard, refresh after) → `POST /git/<op> {root}` (bearer token; root must
be a granted root) → `GitService`:
- **fetch:** `git remote`; none → `{ok:true, remotes:[]}`; else `git fetch --prune <remote>...` (30 s)
- **pull:** `git pull --ff-only` (30 s); `no_upstream` / `diverged` / `local_changes_would_be_overwritten`
  detected from English stderr → `{ok:false, reason}` → HTTP 409 with body → client returns the body
- **push:** bare `git push`, no refspec (30 s); `no_upstream` / `diverged` likewise
- every git runs with `GIT_TERMINAL_PROMPT=0`; hooks and fsmonitor off unless the folder is trusted
- timeout kills the process tree (Windows `taskkill /T`) and throws `GitCommandError` → HTTP 500
- client waits 10 min (`hostdLongRequestTimeout`), so the daemon's 30 s is the binding limit
- Boundary by design (git_service.dart:7-14, ADR-015 D5): human-only; a click is the consent; no approval
  queue; not reachable from `TaskHandler`.

## Agent flows
- **hostd `run_command`:** any `git push` spelling → approval in every folder (D-07); fetch/pull run freely
  in a trusted folder and need approval in an untrusted one.
- **Server `git_push` (Docker workspace):** `CONFIDENTIAL + side_effecting` (always confirms); plain branch
  names only; default branch refused; explicit `refs/heads/X:refs/heads/X`; token via `GIT_ASKPASS`;
  300 s. `HostGitService.fetch` (`--all --prune`) has no caller in `aura_core/tools`.

## Tests found
- `aura_hostd/test/git_service_test.dart:372-500`: no-repo, no-remote, no-upstream, one-remote fetch+pull,
  push to a local bare remote, fetch timeout kill.
- `aura_backend/tests/unit/test_workspace_tools.py`, `tests/security/test_workspace_boundaries.py`: agent push.
- `aura_hostd/test/command_policy_test.dart`: push detection.
- **None** for `/git/fetch|pull|push` routes, `GitNotifier`, or the panel.

## Evidence state
`docs/status/DESKTOP_APP_STATUS.md` (row "hostd: git and per-task worktrees") still says
"failing on Windows"; later fixes (#185, `_killTree`) are on `main` but not CI-run. The row needs a
refresh whenever this module is next verified.
