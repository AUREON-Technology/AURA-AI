# 02 — Interview and decision log

| ID | Question | Answer | Source | Affects |
|---|---|---|---|---|
| D-01 | A branch created in the panel has no upstream and Push dead-ends ("no upstream set"). Offer a way to publish it? | **Yes: add a "Publish branch" action** (`git push --set-upstream <remote> <branch>`; confirmation names remote and branch; user picks when several remotes) | USER (accepted recommendation A) | FR-004, new route, client UI, l10n, tests |
| D-02 | How long may fetch/pull/push run before being treated as hung? | **300 seconds** (message names the limit) | USER (accepted recommendation A) | NFR-001 |
| D-03 | Should Push send exactly the current branch, or follow the user's git config? | **Follow git config** (like the terminal); fix tooltip and comments | USER (chose B over recommendation A) | FR-003, UX-001, AR-01 |
| D-04 | Which sign-in setups must the panel support? | **Existing git credentials only**; classify auth failures as `auth_failed` with a clear message; never prompt or store | USER (accepted recommendation A) | FR-005 |

## Established, not asked
- **Human push without a confirmation dialog** is deliberate (ADR-015 D5: a click is the consent).
  Source: `git_service.dart:7-14`.
- **Sign-off needs real-machine evidence.** Project rule: "don't mark something done without the evidence its
  exit criteria name"; GAP-026 needs a committed real-machine report.

## Agent recommendations, pending approval at plan stage (not user decisions)
- **R-01:** pin git's message language for the daemon's git processes (`LC_MESSAGES`/`LANGUAGE`, not
  `LC_ALL=C`, to avoid path-encoding side effects) so result detection does not depend on locale (F5).
- **R-02:** scrub URL user-info from git stderr before showing it to the user (defence in depth).

## Accepted risk
- **AR-01 (from D-03):** with `push.default=matching` or a configured `remote.*.push`, one Push click can
  publish more than the checked-out branch (reproduced). Owner: user. Mitigation: wording states that Push
  follows git config. Review: when the module is next verified.

## Deferred
None.
