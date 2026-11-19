# 07 — Real-machine verification checklist (WP-7)

**Status: NOT RUN.** This is the procedure, not a result. Nothing below has been observed on a real
Windows desktop against a real remote; until the table at the end is filled in and committed, the
module stays *built, not proven* (GAP-026), whatever the local test counts say.

## Set-up
- A Windows 11 desktop with the AURA desktop build under test (name its commit) and its bundled
  `aura_hostd`; signed in; Desktop IDE enabled.
- A throwaway GitHub repository you own, cloned into a granted folder, with a **second remote**
  (a fork or a second repository) added as `upstream`. Use a personal access token or Git Credential
  Manager sign-in you can revoke afterwards. Never use a repository that matters.
- Record: Windows build, `git --version`, Git Credential Manager version, how git signs in (GCM /
  SSH agent / token), the AURA commit.

## Scenarios (record result, wall-clock time, and the exact message shown)
| # | Scenario | Expected | Covers |
|---|---|---|---|
| 1 | Fetch with one remote; with `origin` + `upstream` | Both remotes' new commits appear; no error | FR-001, F1 |
| 2 | Pull when behind | Fast-forwards; no merge commit | FR-002 |
| 3 | Pull when diverged | "diverged" message; nothing changed | FR-002 |
| 4 | Pull with an untracked file the remote also has | Message names the file; file untouched | FR-002 |
| 5 | Push a tracked branch that is ahead | Remote has the commit | FR-003 |
| 6 | Push after the remote moved on | "diverged" message; remote untouched | FR-003 |
| 7 | Create a branch in the panel, commit, **Publish branch** (one remote) | Dialog names remote and branch; after confirming, upstream shows and the button disappears | FR-004 |
| 8 | Publish with two remotes | A choice is offered, `origin` pre-selected; the chosen remote receives it | FR-004 |
| 9 | Publish then Cancel | Nothing sent | FR-004 |
| 10 | Publish in a repo with no remote; on a detached HEAD; before the first commit | Each says why; nothing sent | FR-004 |
| 11 | Publish a branch name the remote already has with other commits | "different branch with this name"; remote untouched | FR-004 |
| 12 | Sign out of the remote (revoke the token / clear the credential), then Fetch, Pull, Push, Publish | "Signing in to the remote failed…" message; **how long it took and whether any dialog appeared** | FR-005, F7 |
| 13 | SSH remote with no key loaded | `auth_failed` message, no hang | FR-005 |
| 14 | Remote URL with a token in it (`https://user:token@…`) and a failing fetch | The message shows `***@`, never the token | SEC-001 |
| 15 | Git in a non-English language (set `LANGUAGE`/`LC_ALL`, or a German/French Windows display language) and repeat 3, 4, 6 | Same structured messages | NFR-002, F5 |
| 16 | `git config push.default matching`, with another branch also ahead, then Push | Record whether the other branch was pushed (accepted risk AR-01); the tooltip reads "Push (follows your git configuration)" | D-03 |
| 17 | A large push or pull over a slow link, if one is available | Completes within 300 s, or fails with a message that says "timed out after 300s" | NFR-001 |
| 18 | Panel at the real default sidebar width (280 px) | Record whether the Merge / Branch buttons are cut off (F10) | F10 |

## Result table (fill in, then commit this file or a report under `docs/reports/`)
| # | Date | Result (pass / fail / not run) | Time | Message shown / notes |
|---|---|---|---|---|

Sign-off needs every row run, with failures filed as findings. A row marked "not run" keeps its
requirement `UNVERIFIABLE` in `06`.
