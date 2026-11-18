# Engineer end-to-end checklist (operators)

Module readiness WP10a (NFR-04). Its result is the evidence that stage 1 of the Engineer works for
real people on real machines. A green `pytest`, `flutter test` or `dart test` cannot stand in for
it: none of them pairs a daemon, reaches Anthropic or opens a pull request on GitHub.

Run every section twice, once on **Windows 11** and once on **Ubuntu 24.04 desktop**, against
`https://api.aureon-ai.tech`, signed in as a **Developer** (not Admin or SuperAdmin; the point is
the narrowest role that should work). Section 7 needs a phone too.

Record each step as **pass**, **fail** (with what you saw instead) or **not run** (with why), in a
copy of the report template at the end. A step you could not run is not a pass.

## 0. Before you start

| Check | How | Expected |
|---|---|---|
| WP6 is live | `curl -s https://api.aureon-ai.tech/health/ready` | `checks.schema.revision` is `0023` or later; `providers.claude1.healthy` is `true` |
| No unfenced Claude slot | Railway → gateway → Deploy logs, latest boot | No `serves every workload`, no `host-agent relay off` |
| The tester is a Developer | Console → Users, or ask an admin | Role `Developer`, nothing higher |
| A throwaway repository | GitHub | A repo you own with a default branch (`main`), a README, and nothing you would mind losing. The GitHub App is installed on it |
| A build of this commit | See below | The app and `aura_hostd` from the same commit as production (`/health/ready` → `release.git_sha`) |

**Getting a build.** No `desktop-v*` release has been tagged yet (G-13, the owner's call). Until
one is, either run *Desktop Package* by hand (Actions → Desktop Package → Run workflow) and take
the `aura-desktop-windows-x64` / `aura-desktop-linux-x64` artifacts, or build locally:

```bash
cd aura_hostd && dart pub get && dart compile exe bin/aura_hostd.dart -o aura_hostd
```

```bash
cd aura_client && flutter pub get && flutter build linux --release --dart-define=BACKEND_URL=https://api.aureon-ai.tech
```

On Windows use `flutter build windows` and `aura_hostd.exe`. If the build is local, write down
the commit and say so in the report: a local build proves the code, not the shipped package.

## 1. Pair the computer

1. Start the app and sign in with Google.
   - **Expected:** it lands on the IDE (`/ide`).
2. Open the Host panel, then **Start**.
   - **Expected:** status *Running, not paired*. If it says the executable path is missing, set
     it in Full Settings.
3. **Pair**.
   - **Expected:** *Approving with your signed-in AURA session...*, then *Paired.*
   - **Expected:** the status bar reads *Host Connected*.
4. Quit and reopen the app.
   - **Expected:** still *Running, paired*, with no second pairing.

## 2. Consent

1. **Settings → Engineer → Sharing code with Anthropic.**
   - **Expected:** *Sharing code with Anthropic: off*.
   - If it is on from an earlier run, **Withdraw** it first.
2. Send the Engineer one message, for example "list the files in this folder".
   - **Expected:** the sheet *The Engineer uses Claude, by Anthropic* opens before anything runs.
3. Choose **Not now**.
   - **Expected:** the turn does not run. You see *The Engineer didn't run this: it needs your
     agreement…*
   - In the Console audit (`GET /api/v3/admin/audit?action=llm.third_party_generation`) there is
     **no** new row for this user.
4. Send again and choose **Agree and continue**.
   - **Expected:** the turn runs.
   - **Expected:** the setting reads *Sharing code with Anthropic: on since {date}*.
   - **Expected:** a new `llm.third_party_generation` audit row names provider `claude1`.
5. Withdraw, send one more message.
   - **Expected:** refused again, with nothing sent.
6. Agree again for the rest of the run.

## 3. Isolated work, approval, merge

Grant your clone of the throwaway repository as a workspace root, and leave it **untrusted**.

1. Ask: "add a line 'hello from the Engineer' to README.md".
   - **Expected:** the Engineer works in its own worktree, not your checkout. `git worktree list`
     in the clone shows a second worktree, and your own `README.md` is unchanged.
   - **Expected:** the write waits: *Pending Approvals* shows *1 file*.
2. Approve it in the IDE.
   - **Expected:** the change lands in the task's worktree.
3. Ask it to run `git status`.
   - **Expected:** it runs without asking, because it is read-only.
4. Ask it to run `npm install` (or any write command).
   - **Expected:** *This workspace isn't trusted, so this command is waiting for your approval.*
5. Deny that one.
   - **Expected:** it does not run, and the Engineer says so.
6. Merge the task branch from the control centre, using **Merge**.
   - **Expected:** your checkout now has the README line.
   - **Expected:** the *Merged* dialog offers to discard the worktree. Accept it.
7. Trust the root and ask it to run `git push`.
   - **Expected:** *Any git push by the agent waits for your approval.*, even though the root is
     trusted. Deny it.
8. In a folder that is **not** a git repository, grant it and ask for any change.
   - **Expected:** *This folder isn't a git repository yet…*, with an **Initialize git here**
     option.
   - Use it, then repeat step 1 in that folder.
   - **Expected:** it now works on its own copy.

## 4. GitHub: branch, one commit, draft PR

Settings → Engineer → **Connect GitHub**, then authorise on the throwaway repository.

1. Ask: "on <repo>, create branch `e2e-<date>`, add `docs/e2e.md` and `docs/e2e2.md`, and open a
   draft PR".
   - **Expected, on GitHub:** a new branch; **one** commit containing both files; a **draft** PR
     against `main`.
   - **Expected, in Engineer Task History:** *Created branch*, *Committed files* and *Opened
     draft PR*, each with a working link.

## 5. Default-branch refusal

1. Ask: "commit a change to README.md directly on `main` of <repo>".
   - **Expected:** refused. The reply says it is refusing to push to `main`, the default branch,
     and suggests a feature branch.
   - **Expected:** `main` on GitHub has no new commit.

## 6. Folder revoked while connected

1. Start a task in a granted folder. While the daemon is still connected, go to **Settings →
   Engineer → Workspace roots → Revoke** that folder.
2. Ask for any read or write in it.
   - **Expected:** *This folder isn't one of your workspaces anymore.*
   - **Expected:** nothing under that folder changes after the revoke.

## 7. Remote control from a phone

On the phone, sign in to the same account. The Engineer tab shows the desktop bar.

1. With remote control **off** on the computer (the default):
   - **Expected:** the phone shows *Remote control is off on your computer — turn it on in AURA
     on the computer*. A request from the phone does nothing on the computer.
2. On the computer, turn on **Allow control from my phone and the web**, and confirm **Allow**.
3. From the phone, ask for a file change in a granted folder.
   - **Expected:** the approval appears **on the phone** (D-10c), not only on the computer.
   - **Expected:** nothing changes on disk until you approve it on the phone. After approval it
     lands.
4. From the phone, ask for a read-only command.
   - **Expected:** *Commands sent from your phone or the web wait for your approval there
     first.* It runs only once approved on the phone.
5. Turn remote control off again on the computer, then repeat step 3 from the phone.
   - **Expected:** refused with the *Remote control is off* message.
6. Quit the desktop app, then make the same request from the phone.
   - **Expected:** *Your computer isn't connected*.

## 8. The daily cap

`USER_TPD` is 400,000 tokens, too many to spend by hand. Lower it for this step only.

1. Railway: set `AURA_PROVIDER_CLAUDE1_USER_TPD=3000` and wait for the redeploy.
2. Send Engineer messages until refused.
   - **Expected:** *You've reached today's Engineer limit. It resets at {time}*, where the time
     is the next 00:00 UTC in your local time.
   - **Expected:** an ordinary chat turn still works.
3. Set `USER_TPD` back to its real value and redeploy.
   - Record both changes in the report, with times.

## 9. Anthropic unavailable

1. Railway: remove `claude1` from `AURA_LLM_PROVIDERS` and wait for the redeploy.
2. Send the Engineer a message.
   - **Expected (current code):** the turn ends with *This needs a feature that isn't available
     yet*.
   - **Expected:** nothing is sent anywhere else. The audit has no `llm.third_party_generation`
     row for the turn, and no other provider answered it.
   - **Judge this message.** It is accurate but generic. If an operator would read it as "broken"
     rather than "the Engineer is off", record it as a finding against FR-09.
3. Put `claude1` back and confirm one Engineer turn works again.

## 10. After the run

1. Close the draft PR and delete the `e2e-*` branches.
2. Withdraw consent if the tester should not keep it.
3. Commit the report as `docs/reports/desktop-engineer-e2e-operators-<YYYY-MM-DD>.md`.
4. Only if every step passed on both machines:
   - mark GAP-026 in `docs/production/gap_register.yaml`;
   - update `docs/status/DESKTOP_APP_STATUS.md`;
   - update ADR-011's status line.

   A run with any fail or not-run is still committed, but those three keep saying "not proven".

## Report template

```markdown
# Desktop Engineer end-to-end, operators -- <YYYY-MM-DD>

- Tester / role: <name>, Developer
- Backend: https://api.aureon-ai.tech, release.git_sha <sha>, schema <rev>, claude1 healthy <y/n>
- Build: <Desktop Package run #N artifacts | local build at <sha>>
- Machines: Windows 11 <build>; Ubuntu 24.04 <kernel>; phone <model/OS>
- Railway changes made during the run: <USER_TPD 3000 at hh:mm, restored at hh:mm; claude1
  removed hh:mm, restored hh:mm>

| # | Step | Windows | Ubuntu | Notes / evidence (screenshot, PR link, audit row id) |
|---|---|---|---|---|
| 1 | Pair, survive restart | | | |
| 2 | Consent: refuse, agree, withdraw | | | |
| 3 | Isolated write, approval, merge, push held, init git | | | |
| 4 | Branch, one commit, draft PR | | | |
| 5 | Default-branch refusal | | | |
| 6 | Revoke while connected | | | |
| 7 | Remote control from phone | | | |
| 8 | Daily cap message | | | |
| 9 | Anthropic unavailable | | | |

## Findings
<each fail, with what was expected and what happened>

## Verdict
<"Proven for operators on Windows and Ubuntu" only if every row passed on both; otherwise what
remains>
```
