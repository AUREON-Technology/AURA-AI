# 03 — Requirements and acceptance criteria

Actor for all: a signed-in desktop user working in a granted workspace root. "Daemon" = `aura_hostd`.
Source column: D-xx = user decision, EXIST = existing documented behaviour, REC = agent recommendation.

| ID | Requirement (MUST unless stated) | Acceptance criteria | Source | Pri |
|---|---|---|---|---|
| FR-001 | **Fetch** MUST update the remote-tracking refs of **every** configured remote with pruning, and never touch the working tree. A repo with no remote is a no-op, not an error. | With 0, 1 and 2 remotes: result `ok:true`; every remote's new commit appears under `remotes/<name>/...`; working tree unchanged. | EXIST | P0 |
| FR-002 | **Pull** MUST be fast-forward-only (no merge commit, no rebase). Expected outcomes are structured results: `no_upstream`, `diverged`, `local_changes_would_be_overwritten` (with files). | Each of the three returns `ok:false` + reason (HTTP 409 + body), including under a non-English git locale; success returns `ok:true`. | EXIST | P0 |
| FR-003 | **Push** MUST run the user's configured `git push` (no refspec, never `--force`), as the terminal would. Outcomes `no_upstream` and `diverged` are structured. | Push of a tracked branch lands on the remote; a non-fast-forward returns `diverged`; no force flag ever appears in the argv. | D-03 | P0 |
| FR-004 | **Publish branch.** When the current branch has no upstream, the panel MUST offer "Publish branch". It runs `git push --set-upstream <remote> <branch>`. Confirmation names the remote and branch. With several remotes the user picks one; with none the action says there is no remote. Never `--force`. | After publish: upstream is set, ahead/behind appear, remote has the branch. Cancel does nothing. Remote list comes from the daemon. Rejected or auth failure surfaces as a structured result. | D-01 | P1 |
| FR-005 | **Sign-in failure.** The daemon MUST NOT prompt or store credentials (existing git credentials only). A fetch/pull/push/publish authentication failure MUST return `ok:false, reason:"auth_failed"` and the panel shows: sign in to this remote in your terminal or credential manager, then retry. Message in English and Egyptian Arabic. | Simulated 401/403, "Authentication failed", "could not read Username", "Permission denied (publickey)", "Host key verification failed" each map to `auth_failed`; the panel shows the localized message; no credential is written anywhere. | D-04 | P1 |
| NFR-001 | **Time limit.** fetch, pull, push and publish MUST be allowed **300 s**; on expiry git and its child processes are killed, the busy state is released, and the message names the limit. Local git steps keep their own limits. | Test with an injected short timeout proves kill + message; default in `ControlServer`'s `GitService` is 300 s; client waiting time is not shorter than the daemon's. | D-02 | P1 |
| NFR-002 | **Locale independence.** Result detection MUST NOT depend on the OS or git locale. | Test runs git with a non-English `LANGUAGE`/`LC_MESSAGES` and still gets `diverged`/`no_upstream`. (REC-01 approach: pin message language.) | REC | P2 |
| UX-001 | Panel wording MUST match behaviour: the Push tooltip and the code comments say Push follows the user's git configuration (not "only the current branch"). en + ar. | Strings updated in both `.arb` files; comments at `git_service.dart` push doc and `control_server.dart` remote-sync doc corrected. | D-03 | P2 |
| SEC-001 | Human-only boundary stays: no route reachable from `TaskHandler`; agent `git push` stays approval-gated in every folder; no credential is stored or logged; surfaced stderr has URL user-info scrubbed. | Existing `command_policy` push tests still pass; a test shows `user:pass@` in a stderr sample is masked. | EXIST + REC | P1 |
| NFR-003 | Concurrency/UX: the panel shows busy during an operation, ignores a second click, and refreshes status afterwards. | `GitNotifier` tests for success, structured failure, thrown error, double-click. | EXIST | P2 |

## State transitions (Publish branch)
`no upstream` → (click Publish) → `choose remote` (only if >1) → `confirm` → `pushing` → `tracking`
Failure edges from `pushing`: `auth_failed`, `diverged` (remote already has a different branch of that name),
`timeout`, `error`. Every failure returns to `no upstream` with the message shown.

## Permission matrix
Granted-root signed-in desktop user: fetch, pull, push, publish (click is the consent; ADR-015 D5).
Engineer agent: no access to these routes; `git push` only through approval (hostd) or confirmation (server tool).
Phone/web remote tasks: not offered these routes.
