# Engineer agent: module readiness, executive summary

- **Mode:** DISCOVER, then PLAN (same day). The plan awaits approval.
- **Date:** 2026-11-17.
- **Repository:** `main` at `0d6c53e`; production `api.aureon-ai.tech` runs the same commit.
- **Scope (D-01):** the Desktop Engineer (paired `aura_hostd`, Windows + Linux) and the GitHub
  tools, served from Railway.
- **Who gets it (D-11):** operators (Developer, Admin, SuperAdmin) and Pro/Team customers,
  desktop included.
- **Out of scope:** server-side Docker workspaces (GAP-025) and macOS.

## Verdict: NO-SHIP

The Engineer cannot answer a single turn in production today, and four P0 and eleven P1 gaps
stand between it and real users.

1. **No model can serve it.** Every Engineer turn is classified SENSITIVE, and the only healthy
   provider (`hosted1`) is capped at `internal`. The turn ends in 503 `capability_unavailable`.
   The owner's fix (D-02) is a Claude slot raised to `sensitive`.
2. **D-02 needs consent first.** User code would go to Anthropic while the app currently says it
   never does. A one-time, server-enforced opt-in (D-03) must ship before Claude is switched on.
3. **Access may be granted to nobody.** `AURA_ENGINEER_AGENT_ROLES` is empty by default, and the
   Railway guide never sets it.
4. **The daemon has two confirmed containment defects:**
   - `task_key` can escape the granted folder;
   - revoking or untrusting a folder never reaches the connected agent.
5. **Several owner decisions (D-05…D-10c) have no implementation yet:**
   - honest trusted folders with a minimal environment;
   - no default-branch writes;
   - a per-user token cap;
   - isolated worktrees by default;
   - the IDE on Linux;
   - remote control with a desktop opt-in and per-action approval.
6. **Found in PLAN:**
   - Raising Claude to `sensitive` would also hand it every unhinted generation, unless it is
     fenced to the Engineer workload (G-26, P0).
   - Customers need a plan-based grant and Firebase desktop sign-in, both built on open PR #246
     (G-27, G-28).
7. **There is no release evidence.** Backend and daemon CI have not run since `dc87beb`, `main`
   fails one architecture test (a stale reachability doc), and no end-to-end report exists
   (GAP-026).

## What was run

- Live `/health/live`, `/health/ready` and `/api/v3/agents` on `api.aureon-ai.tech`, and on the
  old apex domain, which is now a 404.
- Backend, Python 3.11 with both lockfiles hash-checked: 22 Engineer-related test files plus
  `tests/architecture` gave **796 passed, 1 failed**. The failure is the stale
  `docs/guides/api-reachability.md` on `main`.
- Client and daemon: code read only. No Flutter or Dart SDK was run in this session; earlier
  counts are quoted from their own sources.

## What was not verified

- Railway environment values (roles, Redis, relay).
- Any real Engineer turn.
- Any real desktop or daemon run.
- Windows or Linux behaviour on real hardware.
- Anthropic's data terms (O-03).

## Reports

| File | Content |
|---|---|
| `01-as-is-system-map.md` | Layers, live state, flows, tests, evidence map |
| `02-business-interview-and-decisions.md` | D-01…D-12, technical decisions T-1…T-8, accepted consequences, deferred and open items |
| `03-requirements-and-acceptance-criteria.md` | 38 requirements with acceptance criteria and status |
| `04-gap-and-risk-register.md` | G-01…G-28 with severity and evidence |
| `05-implementation-plan.md` | Two stages. Stage 1, operators: WP0–WP10a. Stage 2, customers after #246: WP11, WP12, WP10b. Files, tests, gates and rollback for each. |

The next step is IMPLEMENT with explicit approval. The suggested first batch is independent and
inert when merged: WP0, WP6a, WP3 and WP5.
