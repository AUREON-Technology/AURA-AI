# Engineer end-to-end checklist (operators, then customers)

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

## Part B: customers (WP10b)

Module readiness WP10b. Run this after Part A has passed. It repeats the operator run as a
paying customer, adds the refusals a Free customer must get, and checks a downgrade
mid-session. The customer launch opens only on a committed report in which every step passed on
both machines.

### B0. Before you start

Everything in section 0, plus:

| Check | How | Expected |
|---|---|---|
| #246 is live | `curl -s https://api.aureon-ai.tech/health/ready` | `checks.schema.revision` is `0029` or later (it was `0023` on 2026-11-18) |
| Customers are switched on | Railway variables | `AURA_ENGINEER_FOR_CUSTOMERS=true`. The boot succeeded, which also proves its startup guards passed (`docs/deployment/desktop-engineer-customers.md`) |
| Browser sign-in is set up | That guide, "Desktop sign-in with Apple or email" | The web app answers at `APP_BASE_URL`; the desktop build was made with `--dart-define=APP_BASE_URL=…`; Firebase has Google, Apple and email on; `AURA_REDIS_URL` is set |
| Two customer accounts, neither an operator | Create them in the app | **Pro customer** and **Free customer**. Use addresses that are on no `*_EMAILS` list and have no Access-page role |
| Their plans | `PUT /api/v3/admin/workspaces/<id>/plan` (SuperAdmin; `docs/deployment/plans-and-limits.md`) | Pro customer's personal workspace on `pro`; Free customer left on `free`. Check each with `GET /api/v3/workspaces/<id>/plan` |

Run B1 to B4 as the **Pro customer**, on both machines.

### B1. Sign in three ways (WP12)

1. Use the browser sign-in button on the login page. The web page opens.
   - Sign in with **Google**.
   - **Expected:** the browser tab says you are signed in and can be closed. The app lands on
     the IDE, signed in.
2. Sign out, and repeat with **Apple**.
3. Sign out, and repeat with **email and password**.
   - **Expected:** each one lands on the IDE, signed in.
   - **Expected:** Settings → Desktop devices lists each sign-in as a device.
   - **Expected:** the web page signed itself out afterwards. Reloading it does not show you as
     signed in.
4. Start a browser sign-in, then wait more than a minute on the web page before finishing.
   - **Expected:** the app says the sign-in code expired, or the sign-in timed out. Starting
     again works.

### B2. The operator run, as a customer

Repeat sections 1 to 8 of Part A. Differences to check:
- **Section 1:** pairing is approved with the customer's own session.
  - **Expected:** it pairs.
  - **Expected:** the daemon stays connected for at least two minutes, which spans the
    60-second plan recheck.
- **Section 2:** the Engineer is **not** greyed out in the persona picker. Before WP11 it was
  greyed out for customers.
- **Section 7:** the phone's folder picker lists the computer's folders. Before WP11 a customer
  got 403 here. "Initialize git here" from the phone also works.
- **Section 8:** the daily cap applies to customers exactly as to operators.
- **The plan's message count.** Each Engineer turn counts one message against the month:
  `GET /api/v3/workspaces/<id>/plan` → `usage`.
  - **Expected:** a turn that failed or was refused does not count.
- **The console.** Open `https://console.aureon-ai.tech/` signed in as the customer.
  - **Expected:** refused. A customer desktop session is capped at `User` and never reaches the
    console.

### B3. Downgrade mid-session

With the Pro customer signed in on the desktop, paired, and a phone session open:

1. As SuperAdmin, set their workspace to `free` and note the time.
2. Send an Engineer message from the desktop.
   - **Expected, at once:** refused with the plan message, and a **See plans** row in the chat.
     Not a connection error.
3. Watch the IDE status bar.
   - **Expected, within about 1 minute:** the daemon disconnects. It shows *Re-pair required* or
     not connected.
4. Keep the app open.
   - **Expected, within about 15 minutes:** the session stops refreshing and the app signs out
     with *The desktop Engineer needs a Pro or Team plan. Upgrade, then sign in again.*
5. From the phone, open the Engineer tab.
   - **Expected:** the folder picker is refused, and the Engineer is greyed out.
6. Set the workspace back to `pro`, sign in on the desktop again, and confirm the daemon
   reconnects without re-pairing.

Record each time you observe. "About" is the design. A minute over is a note, not a fail; ten
minutes over is a fail.

### B4. A team member

1. Create a Team workspace, owned by an operator or a third account, on the `team` plan.
2. Invite the **Free customer**, who accepts.
3. They sign in on the desktop and pair.
   - **Expected:** both work. Sign-in and pairing look at **any** workspace on a plan that
     includes the Engineer.
4. With their **personal** (Free) workspace still active, send an Engineer message.
   - **Expected:** refused with the plan message. A turn uses the **active** workspace's plan.
5. Switch the active workspace to the team and send it again.
   - **Expected:** it runs.
   - **Record:** whether the desktop app offers a way to switch workspace at all. If a team member
     can pair but has no way on the desktop to make the team active, that is a finding.
6. Remove them from the team.
   - **Expected:** their daemon disconnects within about a minute, as in B3.

### B5. The Free customer is refused everywhere

Signed in as the **Free customer**, with no Team membership:

| Where | Expected |
|---|---|
| Desktop sign-in, every method | *The desktop Engineer needs a Pro or Team plan. Upgrade, then sign in again.* No session |
| Approving a pairing code from the phone | The same message. The code is not spent |
| Phone: Engineer persona | Greyed out |
| Phone: folder picker (`/api/v3/host/folders`) | Refused |
| An Engineer message from the phone or web | Refused with the plan message and **See plans**, before anything is sent to Anthropic: no new `llm.third_party_generation` audit row |

### B6. After the run

Section 10, plus:
1. Set the test workspaces back to their real plans.
2. Commit the report as `docs/reports/desktop-engineer-e2e-customers-<YYYY-MM-DD>.md`.
3. Only if every row passed on both machines, record the customer launch as unblocked:
   - in the module-readiness plan's progress table;
   - in `docs/status/DESKTOP_APP_STATUS.md`.

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

### Customer report template (WP10b)

```markdown
# Desktop Engineer end-to-end, customers -- <YYYY-MM-DD>

- Backend: release.git_sha <sha>, schema <rev>, AURA_ENGINEER_FOR_CUSTOMERS=true, claude1 healthy <y/n>
- Web app: <APP_BASE_URL>; desktop build: <run #N | local at <sha>>, built with APP_BASE_URL <y/n>
- Accounts: Pro customer <id>, Free customer <id>, team <id>
- Plan changes made during the run, with times: <...>

| # | Step | Windows | Ubuntu | Notes / evidence |
|---|---|---|---|---|
| B1 | Sign in: Google, Apple, email; expiry | | | |
| B2 | Operator run as Pro customer (sections 1-8, persona, phone picker, metering, console refused) | | | |
| B3 | Downgrade: turn at once, daemon ~1 min, session ~15 min, restore | | | |
| B4 | Team member entitled; removal disconnects | | | |
| B5 | Free refused at every point | | | |

## Findings
## Verdict
<"Customer launch unblocked" only if every row passed on both machines>
```
